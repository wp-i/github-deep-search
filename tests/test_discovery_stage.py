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
    StageName,
)
from github_deep_search.pipeline import PipelineContext, PipelineFailure
from github_deep_search.providers.github import (
    GitHubAuthenticationError,
    GitHubAuthorizationError,
    GitHubProviderError,
    GitHubQueryError,
    GitHubRateLimitError,
    GitHubRequestLimitError,
)
from github_deep_search.stages.discovery import DiscoveryStage


REALISTIC_DISCOVERY_CASES = (
    (
        "我想找一个可以在本地部署的知识库工具，支持 Markdown 导入、全文搜索和 Docker Compose，最好提供 REST API。",
        "知识库 软件",
        "knowledge base software",
        "知识库 Markdown 全文搜索",
        "knowledge base Markdown full text search",
    ),
    (
        "I need a desktop transcription app for macOS and Windows that works fully offline and can export timestamped subtitles.",
        "桌面 音频转录 应用",
        "desktop audio transcription app",
        "离线 音频转录",
        "offline audio transcription",
    ),
    (
        "我需要一个团队使用的自托管看板，必须支持权限管理、附件和审计日志，不依赖第三方云服务。",
        "团队 看板 软件",
        "team kanban software",
        "自托管 看板",
        "self hosted kanban",
    ),
    (
        "Find a Python library for validating large JSON documents with streaming input, custom error messages, and no mandatory database.",
        "Python JSON 验证库",
        "Python JSON validation library",
        "Python 流式 JSON 解析器",
        "Python streaming JSON parser",
    ),
    (
        "想找一个 React component library，用于构建可访问的数据表格，需要虚拟滚动、键盘导航和 server-side pagination。",
        "React 数据表格 组件",
        "React data table component",
        "React 表格 无障碍 键盘导航",
        "React table accessibility keyboard navigation",
    ),
)


class RecordingControl:
    def __init__(self) -> None:
        self.warnings: list[tuple[str, StageName | None]] = []

    async def warning(self, message: str, *, stage: StageName | None = None) -> None:
        self.warnings.append((message, stage))


class FakeGitHubClient:
    def __init__(self) -> None:
        self.authentication_error: GitHubProviderError | None = None
        self.search_results: dict[str, list[CandidateRepository] | GitHubProviderError] = {}
        self.page_search_results: dict[
            tuple[str, int],
            list[CandidateRepository] | GitHubProviderError,
        ] = {}
        self.repository_results: dict[str, CandidateRepository | None | GitHubProviderError] = {}
        self.search_calls: list[tuple[str, int]] = []
        self.search_pages: list[int] = []
        self.repository_calls: list[tuple[str, str, str]] = []
        self.authentication_calls = 0
        self.closed = False

    async def validate_authentication(self) -> None:
        self.authentication_calls += 1
        if self.authentication_error is not None:
            raise self.authentication_error

    async def search_repositories(
        self,
        query: str,
        per_page: int = 10,
        page: int = 1,
    ) -> list[CandidateRepository]:
        self.search_calls.append((query, per_page))
        self.search_pages.append(page)
        outcome = self.page_search_results.get((query, page), self.search_results.get(query))
        if isinstance(outcome, GitHubProviderError):
            raise outcome
        if outcome is not None:
            return outcome
        index = len(self.search_calls)
        return [repository("search", f"project-{index}", found_by=f"github:{query}")]

    async def get_repository(
        self,
        owner: str,
        name: str,
        found_by: str = "github:url",
    ) -> CandidateRepository | None:
        self.repository_calls.append((owner, name, found_by))
        outcome = self.repository_results.get(f"{owner}/{name}".lower())
        if isinstance(outcome, GitHubProviderError):
            raise outcome
        if outcome is None:
            return None
        outcome.found_by = [found_by]
        return outcome

    async def close(self) -> None:
        self.closed = True


def settings() -> Settings:
    return Settings(
        github_token="github-token",
        llm_api_key="llm-key",
        llm_base_url="https://provider.example/v1",
        llm_model="model",
        llm_thinking=None,
        llm_reasoning_effort=None,
        max_github_requests=200,
        max_candidates=80,
        max_evidence_repositories=12,
        run_timeout_seconds=600,
    )


def repository(
    owner: str,
    name: str,
    *,
    private: bool = False,
    found_by: str = "github:test",
) -> CandidateRepository:
    return CandidateRepository(
        owner=owner,
        name=name,
        url=f"https://github.com/{owner}/{name}",
        is_private=private,
        found_by=[found_by],
    )


def parsed_requirement(
    zh: str,
    en: str,
    *,
    suggestions: tuple[str, ...] = (),
    secondary: tuple[str, str] | None = None,
) -> ParsedRequirement:
    secondary_zh, secondary_en = secondary or (zh, en)
    return ParsedRequirement(
        complete_requirement="Complete requirement",
        core_goal="Find a relevant public GitHub project",
        reasonable_interpretations=("A project matching the complete requirement",),
        functional_requirements=("Required capability",),
        constraints=(),
        preferences=(),
        exclusions=(),
        search_query_pairs=(
            SearchQueryPair(purpose="primary discovery", zh=zh, en=en),
            SearchQueryPair(
                purpose="alternative discovery",
                zh=secondary_zh,
                en=secondary_en,
            ),
        ),
        evidence_targets=("Repository evidence for the required capability",),
        suggested_repositories=suggestions,
    )


def context_for(
    raw_input: str,
    parsed: ParsedRequirement,
    *,
    configured: Settings | None = None,
) -> tuple[PipelineContext, RecordingControl]:
    control = RecordingControl()
    context = PipelineContext(
        run_id="run-id",
        request=RunRequest(raw_input=raw_input),
        settings=configured or settings(),
        parsed_requirement=parsed,
        control=control,  # type: ignore[arg-type]
    )
    return context, control


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raw_input", "zh", "en", "secondary_zh", "secondary_en"),
    REALISTIC_DISCOVERY_CASES,
)
async def test_discovery_executes_bilingual_queries_for_five_realistic_plans(
    raw_input: str,
    zh: str,
    en: str,
    secondary_zh: str,
    secondary_en: str,
) -> None:
    client = FakeGitHubClient()
    context, _ = context_for(
        raw_input,
        parsed_requirement(zh, en, secondary=(secondary_zh, secondary_en)),
    )

    await DiscoveryStage(client=client).execute(context)

    assert context.request.raw_input == raw_input
    assert [query for query, _ in client.search_calls] == [zh, en, secondary_zh, secondary_en]
    assert context.discovery_result is not None
    assert context.discovery_result.successful_queries == (zh, en, secondary_zh, secondary_en)
    assert context.discovery_result.failed_queries == ()
    assert all(not candidate.is_private for candidate in context.discovery_result.candidates)


@pytest.mark.asyncio
async def test_discovery_revalidates_suggestions_and_merges_case_insensitive_provenance() -> None:
    client = FakeGitHubClient()
    client.repository_results = {
        "owner/project": repository("Owner", "Project"),
        "legacy/project": repository("Owner", "Project"),
        "private/hidden": repository("private", "hidden", private=True),
    }
    client.search_results = {
        "中文查询": [repository("owner", "project", found_by="github:中文查询")],
        "english query": [repository("another", "tool", found_by="github:english query")],
    }
    parsed = parsed_requirement(
        "中文查询",
        "english query",
        suggestions=("Owner/Project", "legacy/project", "missing/repo", "private/hidden"),
    )
    context, _ = context_for("Find a project", parsed)

    await DiscoveryStage(client=client).execute(context)

    assert context.discovery_result is not None
    assert [candidate.full_name for candidate in context.discovery_result.candidates] == [
        "Owner/Project",
        "another/tool",
    ]
    assert context.discovery_result.candidates[0].found_by == [
        "llm_suggestion:Owner/Project",
        "llm_suggestion:legacy/project",
        "github:中文查询",
    ]
    assert context.discovery_result.verified_suggestions == ("Owner/Project",)
    assert len(client.repository_calls) == 4


@pytest.mark.asyncio
async def test_one_invalid_query_warns_when_another_query_completes() -> None:
    client = FakeGitHubClient()
    client.search_results = {
        "bad query": GitHubQueryError("invalid", retryable=False),
        "good query": [repository("public", "project")],
    }
    context, control = context_for(
        "Find a project",
        parsed_requirement("bad query", "good query"),
    )

    await DiscoveryStage(client=client).execute(context)

    assert context.discovery_result is not None
    assert context.discovery_result.successful_queries == ("good query",)
    assert context.discovery_result.failed_queries == ("bad query",)
    assert len(control.warnings) == 1
    assert control.warnings[0][1] == "discovery"


@pytest.mark.asyncio
async def test_all_invalid_queries_fail_instead_of_using_partial_suggestions() -> None:
    client = FakeGitHubClient()
    failure = GitHubQueryError("invalid", retryable=False)
    client.search_results = {"bad zh": failure, "bad en": failure}
    client.repository_results = {"owner/project": repository("owner", "project")}
    context, _ = context_for(
        "Find a project",
        parsed_requirement("bad zh", "bad en", suggestions=("owner/project",)),
    )

    with pytest.raises(PipelineFailure) as caught:
        await DiscoveryStage(client=client).execute(context)

    assert caught.value.code == "github_queries_failed"
    assert context.discovery_result is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider_error", "expected_code"),
    [
        (GitHubAuthenticationError("auth", retryable=False), "github_authentication_failed"),
        (GitHubAuthorizationError("forbidden", retryable=False), "github_authorization_failed"),
        (GitHubRateLimitError("rate", retryable=True), "github_rate_limited"),
        (GitHubRequestLimitError("budget", retryable=False), "github_request_limit"),
        (GitHubProviderError("network", retryable=True), "github_discovery_failed"),
    ],
)
async def test_provider_wide_failures_stop_discovery(
    provider_error: GitHubProviderError,
    expected_code: str,
) -> None:
    client = FakeGitHubClient()
    client.authentication_error = provider_error
    context, _ = context_for(
        "Find a project",
        parsed_requirement("中文查询", "english query"),
    )

    with pytest.raises(PipelineFailure) as caught:
        await DiscoveryStage(client=client).execute(context)

    assert caught.value.code == expected_code
    assert client.search_calls == []


@pytest.mark.asyncio
async def test_candidate_limit_does_not_skip_planned_queries() -> None:
    client = FakeGitHubClient()
    client.search_results = {
        "中文查询": [repository("first", "project")],
        "english query": [repository("second", "project")],
    }
    limited_settings = replace(settings(), max_candidates=1)
    context, _ = context_for(
        "Find a project",
        parsed_requirement("中文查询", "english query"),
        configured=limited_settings,
    )

    await DiscoveryStage(client=client).execute(context)

    assert [query for query, _ in client.search_calls] == ["中文查询", "english query"]
    assert context.discovery_result is not None
    assert [candidate.full_name for candidate in context.discovery_result.candidates] == [
        "first/project"
    ]


@pytest.mark.asyncio
async def test_candidate_limit_is_shared_fairly_across_suggestions_and_queries() -> None:
    client = FakeGitHubClient()
    client.repository_results = {
        "suggested/one": repository("suggested", "one"),
        "suggested/two": repository("suggested", "two"),
    }
    client.search_results = {
        "中文查询": [
            repository("zh", "one"),
            repository("zh", "two"),
            repository("zh", "three"),
        ],
        "english query": [
            repository("en", "one"),
            repository("en", "two"),
            repository("en", "three"),
        ],
    }
    limited_settings = replace(settings(), max_candidates=5)
    context, _ = context_for(
        "Find a project",
        parsed_requirement(
            "中文查询",
            "english query",
            suggestions=("suggested/one", "suggested/two"),
        ),
        configured=limited_settings,
    )

    await DiscoveryStage(client=client).execute(context)

    assert context.discovery_result is not None
    assert [candidate.full_name for candidate in context.discovery_result.candidates] == [
        "suggested/one",
        "zh/one",
        "en/one",
        "suggested/two",
        "zh/two",
    ]


@pytest.mark.asyncio
async def test_discovery_fails_when_no_public_candidates_exist() -> None:
    client = FakeGitHubClient()
    client.search_results = {
        "中文查询": [repository("private", "one", private=True)],
        "english query": [],
    }
    context, _ = context_for(
        "Find a project",
        parsed_requirement("中文查询", "english query"),
    )

    with pytest.raises(PipelineFailure) as caught:
        await DiscoveryStage(client=client).execute(context)

    assert caught.value.code == "no_discovery_candidates"


@pytest.mark.asyncio
async def test_discovery_requires_parse_output_before_github_calls() -> None:
    client = FakeGitHubClient()
    context = PipelineContext(
        run_id="run-id",
        request=RunRequest(raw_input="Find a project"),
        settings=settings(),
    )

    with pytest.raises(PipelineFailure) as caught:
        await DiscoveryStage(client=client).execute(context)

    assert caught.value.code == "discovery_prerequisite_missing"
    assert client.authentication_calls == 0


@pytest.mark.asyncio
async def test_discovery_stage_closes_its_github_client() -> None:
    client = FakeGitHubClient()
    stage = DiscoveryStage(client=client)

    await stage.aclose()

    assert client.closed is True


@pytest.mark.asyncio
async def test_supplemental_discovery_uses_original_queries_and_next_page_once() -> None:
    client = FakeGitHubClient()
    client.page_search_results = {
        ("中文查询", 2): [repository("next", "one")],
        ("english query", 2): [
            repository("existing", "project"),
            repository("next", "two"),
        ],
    }
    parsed = parsed_requirement("中文查询", "english query")
    context, _ = context_for("Find a project", parsed)
    context.discovery_result = DiscoveryResult(
        candidates=(repository("existing", "project"),),
        successful_queries=("中文查询", "english query"),
        failed_queries=(),
        verified_suggestions=(),
    )
    stage = DiscoveryStage(client=client)

    added = await stage.supplement(context, page=2)

    assert added == 2
    assert client.search_pages == [2, 2]
    assert [query for query, _ in client.search_calls] == ["中文查询", "english query"]
    assert context.discovery_result is not None
    assert [candidate.full_name for candidate in context.discovery_result.candidates] == [
        "existing/project",
        "next/one",
        "next/two",
    ]
