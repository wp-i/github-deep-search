from __future__ import annotations

from dataclasses import replace

import pytest

from github_deep_search.config import Settings
from github_deep_search.models import (
    CandidateRepository,
    DiscoveryResult,
    ParsedRequirement,
    RunRequest,
    SearchQueryPair,
)
from github_deep_search.pipeline import PipelineContext, PipelineFailure
from github_deep_search.providers.github import (
    GitHubAuthenticationError,
    GitHubAuthorizationError,
    GitHubProviderError,
    GitHubRateLimitError,
    GitHubRequestLimitError,
)
from github_deep_search.stages.evidence import EvidenceStage


class RecordingControl:
    def __init__(self) -> None:
        self.supplemental_iterations: list[int] = []

    async def supplemental_discovery(self, *, iteration: int) -> None:
        self.supplemental_iterations.append(iteration)


class FakeEvidenceClient:
    def __init__(self) -> None:
        self.authentication_error: GitHubProviderError | None = None
        self.repository_results: dict[
            str,
            CandidateRepository | None | GitHubProviderError,
        ] = {}
        self.trees: dict[str, list[str] | GitHubProviderError] = {}
        self.readmes: dict[str, str | GitHubProviderError] = {}
        self.files: dict[tuple[str, str], str | GitHubProviderError] = {}
        self.releases: dict[str, str | None | GitHubProviderError] = {}
        self.repository_calls: list[str] = []
        self.tree_calls: list[str] = []
        self.readme_calls: list[str] = []
        self.file_calls: list[tuple[str, str]] = []
        self.release_calls: list[str] = []
        self.authentication_calls = 0
        self.closed = False

    async def validate_authentication(self) -> None:
        self.authentication_calls += 1
        if self.authentication_error is not None:
            raise self.authentication_error

    async def get_repository(
        self,
        owner: str,
        name: str,
        found_by: str = "github:url",
    ) -> CandidateRepository | None:
        key = f"{owner}/{name}".casefold()
        self.repository_calls.append(key)
        outcome = self.repository_results[key]
        if isinstance(outcome, GitHubProviderError):
            raise outcome
        if outcome is None:
            return None
        return replace(outcome, found_by=[found_by])

    async def fetch_tree_paths(
        self,
        repo: CandidateRepository,
        limit: int = 1200,
    ) -> list[str]:
        del limit
        key = repo.full_name.casefold()
        self.tree_calls.append(key)
        outcome = self.trees.get(key, ["README.md", "src/main.py"])
        if isinstance(outcome, GitHubProviderError):
            raise outcome
        return outcome

    async def fetch_readme(self, repo: CandidateRepository) -> str:
        key = repo.full_name.casefold()
        self.readme_calls.append(key)
        outcome = self.readmes.get(key, "Streaming validation project documentation")
        if isinstance(outcome, GitHubProviderError):
            raise outcome
        return outcome

    async def fetch_file_text(
        self,
        repo: CandidateRepository,
        path: str,
        max_chars: int = 10000,
    ) -> str:
        del max_chars
        key = repo.full_name.casefold()
        self.file_calls.append((key, path))
        outcome = self.files.get((key, path), f"Implementation evidence from {path}")
        if isinstance(outcome, GitHubProviderError):
            raise outcome
        return outcome

    async def fetch_latest_release_at(self, repo: CandidateRepository) -> str | None:
        key = repo.full_name.casefold()
        self.release_calls.append(key)
        outcome = self.releases.get(key)
        if isinstance(outcome, GitHubProviderError):
            raise outcome
        return outcome

    async def close(self) -> None:
        self.closed = True


class FakeSupplementalDiscovery:
    def __init__(self, candidates: tuple[CandidateRepository, ...]) -> None:
        self.candidates = candidates
        self.calls: list[int] = []

    async def supplement(self, context: PipelineContext, *, page: int = 2) -> int:
        self.calls.append(page)
        assert context.discovery_result is not None
        current = context.discovery_result
        context.discovery_result = replace(
            current,
            candidates=(*current.candidates, *self.candidates),
        )
        return len(self.candidates)


def settings(*, evidence_limit: int = 12) -> Settings:
    return Settings(
        github_token="github-token",
        llm_api_key="llm-key",
        llm_base_url="https://provider.example/v1",
        llm_model="model",
        llm_thinking=None,
        llm_reasoning_effort=None,
        max_github_requests=200,
        max_candidates=80,
        max_evidence_repositories=evidence_limit,
        run_timeout_seconds=600,
    )


def repository(
    owner: str,
    name: str,
    *,
    private: bool = False,
    size_kb: int = 25,
    fork: bool = False,
    parent: str | None = None,
    mirror_url: str | None = None,
) -> CandidateRepository:
    return CandidateRepository(
        owner=owner,
        name=name,
        url=f"https://github.com/{owner}/{name}",
        description="Candidate description",
        default_branch="main",
        is_private=private,
        is_fork=fork,
        parent_full_name=parent,
        mirror_url=mirror_url,
        size_kb=size_kb,
        found_by=["github:streaming validation"],
    )


def parsed_requirement() -> ParsedRequirement:
    return ParsedRequirement(
        complete_requirement="Find streaming JSON validation software",
        core_goal="Validate large JSON streams",
        reasonable_interpretations=("A library that processes incremental input",),
        functional_requirements=("Validate JSON", "Accept streaming input"),
        constraints=(),
        preferences=(),
        exclusions=(),
        search_query_pairs=(
            SearchQueryPair(
                purpose="primary",
                zh="流式 JSON 验证",
                en="streaming JSON validation",
            ),
        ),
        evidence_targets=("Source evidence for incremental streaming validation",),
        suggested_repositories=(),
    )


def context_for(
    candidates: tuple[CandidateRepository, ...],
    *,
    configured: Settings | None = None,
) -> tuple[PipelineContext, RecordingControl]:
    control = RecordingControl()
    context = PipelineContext(
        run_id="run-id",
        request=RunRequest(raw_input="Find streaming JSON validation software"),
        settings=configured or settings(),
        parsed_requirement=parsed_requirement(),
        discovery_result=DiscoveryResult(
            candidates=candidates,
            successful_queries=("streaming JSON validation",),
            failed_queries=(),
            verified_suggestions=(),
        ),
        control=control,  # type: ignore[arg-type]
    )
    return context, control


def configure_repository(client: FakeEvidenceClient, candidate: CandidateRepository) -> None:
    client.repository_results[candidate.full_name.casefold()] = candidate


@pytest.mark.asyncio
async def test_evidence_collects_traceable_material_and_stable_relations() -> None:
    original = repository("alpha", "validator")
    fork = repository(
        "beta",
        "validator-fork",
        fork=True,
        parent="alpha/validator",
    )
    mirror = repository(
        "gamma",
        "validator-mirror",
        mirror_url="https://mirror.example/validator.git",
    )
    client = FakeEvidenceClient()
    for candidate in (original, fork, mirror):
        configure_repository(client, candidate)
    client.trees["alpha/validator"] = [
        "README.md",
        "docs/streaming-validation.md",
        "src/engine.py",
        "config/settings.yaml",
        "assets/logo.png",
    ]
    client.readmes["alpha/validator"] = "header\n" + ("streaming validation\n" * 900)
    client.releases["alpha/validator"] = "2026-08-01T00:00:00Z"
    context, _ = context_for((original, fork, mirror))

    await EvidenceStage(client=client).execute(context)

    assert context.evidence_result is not None
    assert context.evidence_result.inspected_count == 3
    assert context.evidence_result.supplemental_discovery_count == 0
    evidence = context.evidence_result.repositories
    assert [item.relation_kind for item in evidence] == ["original", "fork", "mirror"]
    assert [item.relation_key for item in evidence] == [
        "alpha/validator",
        "alpha/validator",
        "https://mirror.example/validator.git",
    ]
    assert evidence[0].repository.latest_release_at == "2026-08-01T00:00:00Z"
    assert evidence[0].repository.found_by == [
        "github:streaming validation",
        "evidence:alpha/validator",
    ]
    assert evidence[0].materials[0].path == "README.md"
    assert evidence[0].materials[0].url.endswith("/blob/main/README.md")
    assert len(evidence[0].materials[0].excerpt) <= 8000
    assert evidence[0].materials[1].path == "docs/streaming-validation.md"
    assert len([item for item in evidence[0].materials if item.kind == "file"]) == 3
    assert len(evidence[0].tree_paths) == 5


@pytest.mark.asyncio
async def test_candidate_level_absence_is_rejected_and_later_candidates_continue() -> None:
    missing = repository("bad", "missing")
    private = repository("bad", "private")
    empty = repository("bad", "empty", size_kb=0)
    no_material = repository("bad", "no-material", size_kb=10)
    usable = tuple(repository("good", f"project-{index}") for index in range(3))
    client = FakeEvidenceClient()
    client.repository_results[missing.full_name.casefold()] = None
    client.repository_results[private.full_name.casefold()] = replace(private, is_private=True)
    for candidate in (empty, no_material, *usable):
        configure_repository(client, candidate)
    for candidate in (empty, no_material):
        key = candidate.full_name.casefold()
        client.trees[key] = []
        client.readmes[key] = ""
    context, _ = context_for((missing, private, empty, no_material, *usable))

    await EvidenceStage(client=client).execute(context)

    assert context.evidence_result is not None
    assert [item.reason for item in context.evidence_result.rejected_candidates] == [
        "not_found",
        "private",
        "empty",
        "no_material",
    ]
    assert len(context.evidence_result.repositories) == 3
    assert context.evidence_result.inspected_count == 7


@pytest.mark.asyncio
async def test_evidence_requests_one_next_page_from_discovery_when_needed() -> None:
    initial = (repository("first", "one"), repository("first", "two"))
    added = repository("next", "three")
    client = FakeEvidenceClient()
    for candidate in (*initial, added):
        configure_repository(client, candidate)
    supplemental = FakeSupplementalDiscovery((added,))
    context, control = context_for(initial)

    await EvidenceStage(
        client=client,
        supplemental_discovery=supplemental,
    ).execute(context)

    assert context.evidence_result is not None
    assert context.evidence_result.supplemental_discovery_count == 1
    assert supplemental.calls == [2]
    assert control.supplemental_iterations == [1]
    assert [item.repository.full_name for item in context.evidence_result.repositories] == [
        "first/one",
        "first/two",
        "next/three",
    ]


@pytest.mark.asyncio
async def test_insufficient_evidence_stops_after_one_supplemental_attempt() -> None:
    initial = (repository("first", "one"), repository("first", "two"))
    client = FakeEvidenceClient()
    for candidate in initial:
        configure_repository(client, candidate)
    supplemental = FakeSupplementalDiscovery(())
    context, control = context_for(initial)

    with pytest.raises(PipelineFailure) as caught:
        await EvidenceStage(
            client=client,
            supplemental_discovery=supplemental,
        ).execute(context)

    assert caught.value.code == "insufficient_evidence_repositories"
    assert supplemental.calls == [2]
    assert control.supplemental_iterations == [1]
    assert context.evidence_result is None


@pytest.mark.asyncio
async def test_evidence_candidate_limit_is_a_hard_inspection_bound() -> None:
    candidates = tuple(repository("limit", f"project-{index}") for index in range(4))
    client = FakeEvidenceClient()
    for candidate in candidates:
        configure_repository(client, candidate)
    client.trees[candidates[0].full_name.casefold()] = []
    client.readmes[candidates[0].full_name.casefold()] = ""
    context, _ = context_for(candidates, configured=settings(evidence_limit=3))

    with pytest.raises(PipelineFailure) as caught:
        await EvidenceStage(client=client).execute(context)

    assert caught.value.code == "insufficient_evidence_repositories"
    assert len(client.repository_calls) == 3
    assert candidates[3].full_name.casefold() not in client.repository_calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider_error", "expected_code"),
    [
        (
            GitHubAuthenticationError("auth", retryable=False),
            "github_evidence_authentication_failed",
        ),
        (
            GitHubAuthorizationError("forbidden", retryable=False),
            "github_evidence_authorization_failed",
        ),
        (GitHubRateLimitError("rate", retryable=True), "github_evidence_rate_limited"),
        (
            GitHubRequestLimitError("budget", retryable=False),
            "github_evidence_request_limit",
        ),
        (GitHubProviderError("network", retryable=True), "github_evidence_failed"),
    ],
)
async def test_provider_wide_failure_stops_before_candidate_calls(
    provider_error: GitHubProviderError,
    expected_code: str,
) -> None:
    candidate = repository("sample", "project")
    client = FakeEvidenceClient()
    configure_repository(client, candidate)
    client.authentication_error = provider_error
    context, _ = context_for((candidate,))

    with pytest.raises(PipelineFailure) as caught:
        await EvidenceStage(client=client).execute(context)

    assert caught.value.code == expected_code
    assert client.repository_calls == []
    assert context.evidence_result is None


@pytest.mark.asyncio
async def test_provider_failure_during_material_collection_stops_immediately() -> None:
    candidates = tuple(repository("sample", f"project-{index}") for index in range(3))
    client = FakeEvidenceClient()
    for candidate in candidates:
        configure_repository(client, candidate)
    client.trees[candidates[0].full_name.casefold()] = GitHubProviderError(
        "network",
        retryable=True,
    )
    context, _ = context_for(candidates)

    with pytest.raises(PipelineFailure) as caught:
        await EvidenceStage(client=client).execute(context)

    assert caught.value.code == "github_evidence_failed"
    assert client.repository_calls == [candidates[0].full_name.casefold()]


@pytest.mark.asyncio
async def test_evidence_requires_discovery_output_and_closes_client() -> None:
    client = FakeEvidenceClient()
    context = PipelineContext(
        run_id="run-id",
        request=RunRequest(raw_input="Find a project"),
        settings=settings(),
        parsed_requirement=parsed_requirement(),
    )
    stage = EvidenceStage(client=client)

    with pytest.raises(PipelineFailure) as caught:
        await stage.execute(context)
    await stage.aclose()

    assert caught.value.code == "evidence_prerequisite_missing"
    assert client.authentication_calls == 0
    assert client.closed is True
