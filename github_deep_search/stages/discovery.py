from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol

from github_deep_search.models import CandidateRepository, DiscoveryResult, ParsedRequirement
from github_deep_search.pipeline import PipelineContext, PipelineFailure
from github_deep_search.providers.github import (
    GitHubAuthenticationError,
    GitHubAuthorizationError,
    GitHubClient,
    GitHubProviderError,
    GitHubQueryError,
    GitHubRateLimitError,
    GitHubRequestLimitError,
)


class DiscoveryClient(Protocol):
    async def validate_authentication(self) -> None: ...

    async def search_repositories(
        self,
        query: str,
        per_page: int = 10,
        page: int = 1,
    ) -> list[CandidateRepository]: ...

    async def get_repository(
        self,
        owner: str,
        name: str,
        found_by: str = "github:url",
    ) -> CandidateRepository | None: ...

    async def close(self) -> None: ...


class DiscoveryStage:
    name = "discovery"

    def __init__(self, client: DiscoveryClient | None = None) -> None:
        self._client = client

    async def execute(self, context: PipelineContext) -> None:
        parsed = context.parsed_requirement
        if parsed is None:
            raise PipelineFailure(
                "discovery_prerequisite_missing",
                "The parsed requirement is unavailable for GitHub discovery.",
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

        suggestion_candidates: list[CandidateRepository] = []
        suggestion_indexes: dict[str, int] = {}
        verified_suggestions: list[str] = []
        await self._verify_suggestions(
            context,
            client,
            parsed,
            suggestion_candidates,
            suggestion_indexes,
            verified_suggestions,
        )

        successful_queries: list[str] = []
        failed_queries: list[str] = []
        query_candidate_batches: list[list[CandidateRepository]] = []
        seen_queries: set[str] = set()
        per_page = min(10, context.settings.max_candidates)
        for purpose, language, query in _planned_queries(parsed):
            if query in seen_queries:
                continue
            seen_queries.add(query)
            context.raise_if_cancelled()
            try:
                repositories = await client.search_repositories(
                    query,
                    per_page=per_page,
                    page=1,
                )
            except GitHubQueryError:
                failed_queries.append(query)
                await context.warning(
                    f"GitHub rejected the planned {language} query for {purpose}; "
                    "discovery continued with the other planned queries.",
                    stage="discovery",
                )
                continue
            except GitHubProviderError as exc:
                raise _pipeline_failure(exc) from None

            successful_queries.append(query)
            query_candidate_batches.append(repositories)

        if not successful_queries:
            raise PipelineFailure(
                "github_queries_failed",
                "GitHub rejected every planned repository query.",
            )
        candidates: list[CandidateRepository] = []
        candidate_indexes: dict[str, int] = {}
        evidence_window = min(
            context.settings.max_candidates,
            context.settings.max_evidence_repositories,
        )
        query_reserve = min(len(parsed.search_query_pairs), evidence_window)
        suggestion_prefix_count = min(
            len(suggestion_candidates),
            max(0, evidence_window - query_reserve),
        )
        for repository in suggestion_candidates[:suggestion_prefix_count]:
            _merge_candidate(
                candidates,
                candidate_indexes,
                repository,
                limit=context.settings.max_candidates,
            )

        seeded_queries = 0
        for repository in _candidate_batch_items(query_candidate_batches):
            if _merge_candidate(
                candidates,
                candidate_indexes,
                repository,
                limit=context.settings.max_candidates,
            ):
                seeded_queries += 1
                if seeded_queries >= query_reserve:
                    break

        _merge_candidate_batches(
            candidates,
            candidate_indexes,
            [
                suggestion_candidates[suggestion_prefix_count:],
                *query_candidate_batches,
            ],
            limit=context.settings.max_candidates,
        )
        if not candidates:
            raise PipelineFailure(
                "no_discovery_candidates",
                "GitHub discovery found no public repository candidates.",
            )

        context.discovery_result = DiscoveryResult(
            candidates=tuple(candidates),
            successful_queries=tuple(successful_queries),
            failed_queries=tuple(failed_queries),
            verified_suggestions=tuple(verified_suggestions),
        )

    async def supplement(self, context: PipelineContext, *, page: int = 2) -> int:
        parsed = context.parsed_requirement
        discovery = context.discovery_result
        client = self._client
        if parsed is None or discovery is None or client is None:
            raise PipelineFailure(
                "supplemental_discovery_prerequisite_missing",
                "The original discovery state is unavailable for supplemental discovery.",
            )

        candidates = list(discovery.candidates)
        candidate_indexes = {
            repository.full_name.casefold(): index
            for index, repository in enumerate(candidates)
        }
        successful_queries = list(discovery.successful_queries)
        failed_queries = list(discovery.failed_queries)
        candidate_batches: list[list[CandidateRepository]] = []
        seen_queries: set[str] = set()
        per_page = min(10, context.settings.max_candidates)
        for purpose, language, query in _planned_queries(parsed):
            if query in seen_queries:
                continue
            seen_queries.add(query)
            context.raise_if_cancelled()
            try:
                repositories = await client.search_repositories(
                    query,
                    per_page=per_page,
                    page=max(2, page),
                )
            except GitHubQueryError:
                if query not in failed_queries:
                    failed_queries.append(query)
                await context.warning(
                    f"GitHub rejected the supplemental {language} query for {purpose}; "
                    "discovery continued with the other planned queries.",
                    stage="discovery",
                )
                continue
            except GitHubProviderError as exc:
                raise _pipeline_failure(exc) from None

            if query not in successful_queries:
                successful_queries.append(query)
            candidate_batches.append(repositories)

        added = _merge_candidate_batches(
            candidates,
            candidate_indexes,
            candidate_batches,
            limit=context.settings.max_candidates,
        )

        context.discovery_result = DiscoveryResult(
            candidates=tuple(candidates),
            successful_queries=tuple(successful_queries),
            failed_queries=tuple(failed_queries),
            verified_suggestions=discovery.verified_suggestions,
        )
        return added

    async def _verify_suggestions(
        self,
        context: PipelineContext,
        client: DiscoveryClient,
        parsed: ParsedRequirement,
        candidates: list[CandidateRepository],
        candidate_indexes: dict[str, int],
        verified_suggestions: list[str],
    ) -> None:
        verified_suggestion_keys: set[str] = set()
        for full_name in parsed.suggested_repositories:
            context.raise_if_cancelled()
            owner, name = full_name.split("/", 1)
            try:
                repository = await client.get_repository(
                    owner,
                    name,
                    found_by=f"llm_suggestion:{full_name}",
                )
            except GitHubProviderError as exc:
                raise _pipeline_failure(exc) from None
            if (
                repository is None
                or repository.is_private
                or not repository.owner
                or not repository.name
            ):
                continue
            canonical_key = repository.full_name.casefold()
            if canonical_key not in verified_suggestion_keys:
                verified_suggestion_keys.add(canonical_key)
                verified_suggestions.append(repository.full_name)
            _merge_candidate(
                candidates,
                candidate_indexes,
                repository,
                limit=context.settings.max_candidates,
            )

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.close()


def _planned_queries(parsed: ParsedRequirement) -> Iterator[tuple[str, str, str]]:
    for pair in parsed.search_query_pairs:
        yield pair.purpose, "Chinese", pair.zh
        yield pair.purpose, "English", pair.en


def _merge_candidate(
    candidates: list[CandidateRepository],
    candidate_indexes: dict[str, int],
    repository: CandidateRepository,
    *,
    limit: int,
) -> bool:
    if repository.is_private or not repository.owner or not repository.name:
        return False
    key = repository.full_name.casefold()
    existing_index = candidate_indexes.get(key)
    if existing_index is not None:
        existing = candidates[existing_index]
        for source in repository.found_by:
            if source not in existing.found_by:
                existing.found_by.append(source)
        return False
    if len(candidates) >= limit:
        return False
    repository.found_by = list(dict.fromkeys(repository.found_by))
    candidate_indexes[key] = len(candidates)
    candidates.append(repository)
    return True


def _merge_candidate_batches(
    candidates: list[CandidateRepository],
    candidate_indexes: dict[str, int],
    batches: list[list[CandidateRepository]],
    *,
    limit: int,
) -> int:
    added = 0
    for repository in _candidate_batch_items(batches):
        if _merge_candidate(
            candidates,
            candidate_indexes,
            repository,
            limit=limit,
        ):
            added += 1
    return added


def _candidate_batch_items(
    batches: list[list[CandidateRepository]],
) -> Iterator[CandidateRepository]:
    longest_batch = max((len(batch) for batch in batches), default=0)
    for item_index in range(longest_batch):
        for batch in batches:
            if item_index >= len(batch):
                continue
            yield batch[item_index]


def _pipeline_failure(error: GitHubProviderError) -> PipelineFailure:
    if isinstance(error, GitHubAuthenticationError):
        return PipelineFailure(
            "github_authentication_failed",
            "GitHub rejected the configured authentication for discovery.",
        )
    if isinstance(error, GitHubAuthorizationError):
        return PipelineFailure(
            "github_authorization_failed",
            "GitHub rejected the configured repository access for discovery.",
        )
    if isinstance(error, GitHubRateLimitError):
        return PipelineFailure(
            "github_rate_limited",
            "GitHub rate limits prevented repository discovery.",
        )
    if isinstance(error, GitHubRequestLimitError):
        return PipelineFailure(
            "github_request_limit",
            "The configured GitHub request budget was exhausted during discovery.",
        )
    return PipelineFailure(
        "github_discovery_failed",
        "GitHub could not complete authenticated repository discovery.",
    )
