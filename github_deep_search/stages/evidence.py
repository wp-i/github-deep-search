from __future__ import annotations

import re
from typing import Protocol
from urllib.parse import quote

from github_deep_search.models import (
    CandidateRepository,
    EvidenceMaterial,
    EvidenceRepository,
    EvidenceResult,
    ParsedRequirement,
    RejectedEvidenceCandidate,
    RepositoryRelationKind,
)
from github_deep_search.pipeline import PipelineContext, PipelineFailure
from github_deep_search.providers.github import (
    GitHubAuthenticationError,
    GitHubAuthorizationError,
    GitHubClient,
    GitHubProviderError,
    GitHubRateLimitError,
    GitHubRequestLimitError,
)


_MIN_EVIDENCE_REPOSITORIES = 3
_TREE_FETCH_LIMIT = 1200
_TREE_RESULT_LIMIT = 240
_FILE_RESULT_LIMIT = 3
_README_EXCERPT_LIMIT = 8000
_FILE_EXCERPT_LIMIT = 3000
_TERM_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_+.-]{2,}")
_TEXT_SUFFIXES = {
    ".c",
    ".cc",
    ".cfg",
    ".conf",
    ".cpp",
    ".cs",
    ".css",
    ".go",
    ".h",
    ".hpp",
    ".html",
    ".ini",
    ".java",
    ".js",
    ".json",
    ".jsx",
    ".kt",
    ".md",
    ".php",
    ".properties",
    ".py",
    ".rb",
    ".rs",
    ".sh",
    ".sql",
    ".swift",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".vue",
    ".xml",
    ".yaml",
    ".yml",
}
_TEXT_FILENAMES = {
    "dockerfile",
    "gemfile",
    "makefile",
    "procfile",
    "requirements",
}


class EvidenceClient(Protocol):
    async def validate_authentication(self) -> None: ...

    async def get_repository(
        self,
        owner: str,
        name: str,
        found_by: str = "github:url",
    ) -> CandidateRepository | None: ...

    async def fetch_readme(self, repo: CandidateRepository) -> str: ...

    async def fetch_tree_paths(
        self,
        repo: CandidateRepository,
        limit: int = 1200,
    ) -> list[str]: ...

    async def fetch_file_text(
        self,
        repo: CandidateRepository,
        path: str,
        max_chars: int = 10000,
    ) -> str: ...

    async def fetch_latest_release_at(self, repo: CandidateRepository) -> str | None: ...

    async def close(self) -> None: ...


class SupplementalDiscovery(Protocol):
    async def supplement(self, context: PipelineContext, *, page: int = 2) -> int: ...


class EvidenceStage:
    name = "evidence"

    def __init__(
        self,
        client: EvidenceClient | None = None,
        supplemental_discovery: SupplementalDiscovery | None = None,
    ) -> None:
        self._client = client
        self._supplemental_discovery = supplemental_discovery

    async def execute(self, context: PipelineContext) -> None:
        parsed = context.parsed_requirement
        discovery = context.discovery_result
        if parsed is None or discovery is None:
            raise PipelineFailure(
                "evidence_prerequisite_missing",
                "The parsed requirement and discovery candidates are unavailable for evidence collection.",
            )

        client = self._client
        if client is None:
            try:
                client = GitHubClient(
                    context.settings.github_token or "",
                    context.usage,
                    request_limit=context.settings.max_github_requests,
                )
            except GitHubProviderError as exc:
                raise _pipeline_failure(exc) from None
            self._client = client

        try:
            await client.validate_authentication()
        except GitHubProviderError as exc:
            raise _pipeline_failure(exc) from None

        repositories: list[EvidenceRepository] = []
        rejected: list[RejectedEvidenceCandidate] = []
        inspected = 0
        candidate_index = 0
        supplemental_count = 0
        terms = _evidence_terms(parsed)

        while inspected < context.settings.max_evidence_repositories:
            candidates = context.discovery_result.candidates
            if candidate_index >= len(candidates):
                if (
                    len(repositories) < _MIN_EVIDENCE_REPOSITORIES
                    and supplemental_count == 0
                    and self._supplemental_discovery is not None
                ):
                    supplemental_count = 1
                    await context.supplemental_discovery(iteration=1)
                    await self._supplemental_discovery.supplement(context, page=2)
                    continue
                break

            candidate = candidates[candidate_index]
            candidate_index += 1
            inspected += 1
            context.raise_if_cancelled()
            try:
                evidence, rejection = await self._inspect_candidate(client, candidate, terms)
            except GitHubProviderError as exc:
                raise _pipeline_failure(exc) from None
            if evidence is not None:
                repositories.append(evidence)
            elif rejection is not None:
                rejected.append(rejection)

        if len(repositories) < _MIN_EVIDENCE_REPOSITORIES:
            raise PipelineFailure(
                "insufficient_evidence_repositories",
                "Fewer than three public repositories provided readable GitHub evidence.",
            )

        context.evidence_result = EvidenceResult(
            repositories=tuple(repositories),
            rejected_candidates=tuple(rejected),
            inspected_count=inspected,
            supplemental_discovery_count=supplemental_count,
        )

    async def _inspect_candidate(
        self,
        client: EvidenceClient,
        candidate: CandidateRepository,
        terms: tuple[str, ...],
    ) -> tuple[EvidenceRepository | None, RejectedEvidenceCandidate | None]:
        refreshed = await client.get_repository(
            candidate.owner,
            candidate.name,
            found_by=f"evidence:{candidate.full_name}",
        )
        if refreshed is None:
            return None, RejectedEvidenceCandidate(candidate.full_name, "not_found")
        if refreshed.is_private:
            return None, RejectedEvidenceCandidate(refreshed.full_name, "private")

        refreshed.found_by = list(dict.fromkeys([*candidate.found_by, *refreshed.found_by]))
        tree_paths = await client.fetch_tree_paths(refreshed, limit=_TREE_FETCH_LIMIT)
        readme = await client.fetch_readme(refreshed)
        if not tree_paths and not readme.strip():
            reason = "empty" if refreshed.size_kb == 0 else "no_material"
            return None, RejectedEvidenceCandidate(refreshed.full_name, reason)

        refreshed.latest_release_at = await client.fetch_latest_release_at(refreshed)
        materials: list[EvidenceMaterial] = []
        readme_path = _readme_path(tree_paths)
        if readme.strip():
            materials.append(
                EvidenceMaterial(
                    kind="readme",
                    path=readme_path,
                    url=_file_url(refreshed, readme_path),
                    excerpt=_excerpt(readme, terms, _README_EXCERPT_LIMIT),
                )
            )

        for path in _select_file_paths(tree_paths, terms, exclude=readme_path):
            text = await client.fetch_file_text(refreshed, path, max_chars=12000)
            if not text.strip():
                continue
            materials.append(
                EvidenceMaterial(
                    kind="file",
                    path=path,
                    url=_file_url(refreshed, path),
                    excerpt=_excerpt(text, terms, _FILE_EXCERPT_LIMIT),
                )
            )

        relation_kind, relation_key = _repository_relation(refreshed)
        return (
            EvidenceRepository(
                repository=refreshed,
                materials=tuple(materials),
                tree_paths=tuple(tree_paths[:_TREE_RESULT_LIMIT]),
                relation_kind=relation_kind,
                relation_key=relation_key,
            ),
            None,
        )

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.close()


def _evidence_terms(parsed: ParsedRequirement) -> tuple[str, ...]:
    sources = [pair.en for pair in parsed.search_query_pairs]
    sources.extend(parsed.evidence_targets)
    terms: list[str] = []
    seen: set[str] = set()
    for source in sources:
        for match in _TERM_PATTERN.findall(source):
            term = match.casefold().strip(".-")
            if not term or term in seen:
                continue
            seen.add(term)
            terms.append(term)
    return tuple(terms[:48])


def _readme_path(paths: list[str]) -> str:
    readmes = [
        path
        for path in paths
        if path.rsplit("/", 1)[-1].casefold().startswith("readme")
    ]
    if not readmes:
        return "README"
    return min(readmes, key=lambda path: (path.count("/"), len(path), path.casefold()))


def _select_file_paths(
    paths: list[str],
    terms: tuple[str, ...],
    *,
    exclude: str,
) -> tuple[str, ...]:
    candidates: list[tuple[int, int, int, str]] = []
    excluded = exclude.casefold()
    for path in paths:
        lowered = path.casefold()
        if lowered == excluded or not _is_text_path(path):
            continue
        score = sum(1 for term in terms if term in lowered)
        candidates.append((-score, path.count("/"), len(path), path))
    candidates.sort(key=lambda item: (item[0], item[1], item[2], item[3].casefold()))
    return tuple(item[3] for item in candidates[:_FILE_RESULT_LIMIT])


def _is_text_path(path: str) -> bool:
    filename = path.rsplit("/", 1)[-1].casefold()
    if filename in _TEXT_FILENAMES:
        return True
    dot_index = filename.rfind(".")
    return dot_index >= 0 and filename[dot_index:] in _TEXT_SUFFIXES


def _excerpt(text: str, terms: tuple[str, ...], limit: int) -> str:
    compact = text.replace("\x00", "").strip()
    if len(compact) <= limit:
        return compact

    lowered = compact.casefold()
    positions = sorted(
        {
            position
            for term in terms
            if (position := lowered.find(term)) >= 0
        }
    )
    if not positions:
        return compact[:limit].rstrip()

    chunks = [compact[: min(1200, limit // 3)].rstrip()]
    remaining = limit - len(chunks[0])
    for position in positions:
        if remaining <= 80:
            break
        window = min(900, remaining - 4)
        start = max(0, position - window // 3)
        chunk = compact[start : start + window].strip()
        if not chunk or chunk in chunks:
            continue
        chunks.append(chunk)
        remaining -= len(chunk) + 4
    return "\n\n…\n\n".join(chunks)[:limit].rstrip()


def _file_url(repository: CandidateRepository, path: str) -> str:
    branch = quote(repository.default_branch or "main", safe="")
    encoded_path = quote(path, safe="/")
    return f"{repository.url.rstrip('/')}/blob/{branch}/{encoded_path}"


def _repository_relation(
    repository: CandidateRepository,
) -> tuple[RepositoryRelationKind, str]:
    if repository.mirror_url:
        return "mirror", repository.mirror_url.rstrip("/").casefold()
    if repository.is_fork:
        return "fork", (repository.parent_full_name or repository.full_name).casefold()
    return "original", repository.full_name.casefold()


def _pipeline_failure(error: GitHubProviderError) -> PipelineFailure:
    if isinstance(error, GitHubAuthenticationError):
        return PipelineFailure(
            "github_evidence_authentication_failed",
            "GitHub rejected the configured authentication during evidence collection.",
        )
    if isinstance(error, GitHubAuthorizationError):
        return PipelineFailure(
            "github_evidence_authorization_failed",
            "GitHub rejected repository access during evidence collection.",
        )
    if isinstance(error, GitHubRateLimitError):
        return PipelineFailure(
            "github_evidence_rate_limited",
            "GitHub rate limits prevented complete evidence collection.",
        )
    if isinstance(error, GitHubRequestLimitError):
        return PipelineFailure(
            "github_evidence_request_limit",
            "The configured GitHub request budget was exhausted during evidence collection.",
        )
    return PipelineFailure(
        "github_evidence_failed",
        "GitHub could not complete authenticated evidence collection.",
    )
