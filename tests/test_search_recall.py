from __future__ import annotations

import asyncio

import pytest

from github_deep_search.engine import DeepSearchEngine
from github_deep_search.models import (
    BudgetUsage,
    CandidateRepository,
    EvidenceCoverage,
    ProjectAnalysis,
    Requirement,
    SearchReport,
)


def test_evidence_budget_is_actually_used_after_discovery() -> None:
    engine = DeepSearchEngine()
    usage = BudgetUsage(github_requests=26)
    requirement = Requirement(
        raw="need search",
        intent="find search tool",
        must_have_features=["search"],
        nice_to_have_features=[],
        target_platforms=[],
        search_queries=["search tool"],
        evidence_aliases={"search": ["search"]},
    )

    class FakeGithub:
        async def fetch_readme(self, repo):
            usage.github_requests += 1
            return "search"

        async def fetch_tree_paths(self, repo):
            usage.github_requests += 1
            return ["src/search.py"]

        async def fetch_file_text(self, repo, path, max_chars=9000):
            usage.github_requests += 1
            return "def search(): pass"

    repos = [
        CandidateRepository(owner="demo", name=f"repo-{index}", url=f"https://github.com/demo/repo-{index}")
        for index in range(10)
    ]

    asyncio.run(engine._hydrate_readmes(repos, FakeGithub(), usage))
    readme_count = len(engine._readme_cache)
    asyncio.run(engine._hydrate_source_evidence(repos, FakeGithub(), usage, requirement))

    assert readme_count == 10
    assert len(engine._tree_cache) == 3
    assert len(engine._file_cache) == 3
    assert usage.github_requests == 42


def test_search_budget_leaves_room_for_evidence() -> None:
    engine = DeepSearchEngine()
    request_limit = engine._budgeted_github_limit()
    evidence_reserve = engine._evidence_request_reserve()
    search_limit = max(8, request_limit - evidence_reserve)

    assert request_limit == 200
    assert evidence_reserve > 0
    assert search_limit > 0
    assert search_limit + evidence_reserve <= request_limit


def test_reasonable_query_returns_at_least_one_adjacent_result() -> None:
    """A well-formed query with domain, action and object should never return zero adjacent leads."""
    engine = DeepSearchEngine()
    usage = BudgetUsage()
    requirement = Requirement(
        raw="Need a browser extension that filters unwanted videos on a video platform",
        intent="Find video filtering browser extension",
        must_have_features=["browser extension filters unwanted videos on a video platform"],
        nice_to_have_features=[],
        target_platforms=[],
        search_queries=["browser extension filters unwanted videos"],
        feature_concepts={
            "domains": ["video platform"],
            "actions": ["filters"],
            "objects": ["unwanted videos"],
            "interfaces": ["browser extension"],
        },
        evidence_aliases={
            "browser extension filters unwanted videos on a video platform": [
                "browser extension filters unwanted videos",
            ],
        },
    )
    repo = CandidateRepository(
        owner="demo",
        name="video-filter-extension",
        url="https://github.com/demo/video-filter-extension",
        description="Browser extension for a video platform that filters unwanted videos.",
        raw_score=24,
        found_by=["github:video platform", "github_code:browser extension"],
    )
    repo.evidence_coverage = engine._build_evidence_coverage(repo, requirement)

    leads = engine._fallback_low_similarity_leads(requirement, [repo], usage)

    assert len(leads) >= 1
    assert leads[0].confidence_level == "lead"


def test_deep_pool_grows_with_available_candidates() -> None:
    engine = DeepSearchEngine()
    assert engine._deep_pool_limit() == 20


def test_multilingual_queries_are_interleaved_and_repo_query_preserves_request_terms() -> None:
    engine = DeepSearchEngine()

    planned = engine._interleave_multilingual_queries(
        ["文档管理 OCR", "全文搜索 标签", "本地保存", "document management OCR", "document search tags"]
    )

    assert planned[:4] == ["文档管理 OCR", "document management OCR", "全文搜索 标签", "document search tags"]
    assert engine._to_github_repo_query("document management system OCR full-text search Docker") == (
        "document management system in:name,description,readme"
    )


def test_chinese_terminal_ui_query_does_not_drop_request_terms_by_static_word_list() -> None:
    engine = DeepSearchEngine()
    requirement = Requirement(
        raw="找一个开源 Python 终端 UI 库，支持表格、进度条、Markdown 渲染和富文本样式。",
        intent="寻找开源 Python 终端 UI 库",
        must_have_features=["开源 Python 终端 UI 库", "表格", "进度条", "Markdown 渲染和富文本样式"],
        nice_to_have_features=[],
        target_platforms=["Python API"],
        search_queries=["Python terminal UI table progress Markdown"],
        repo_search_queries=["Python terminal UI table progress Markdown"],
        evidence_aliases={
            "开源 Python 终端 UI 库": ["open source Python terminal UI library", "terminal UI library", "TUI", "Python"],
            "表格": ["table"],
            "进度条": ["progress bar"],
            "Markdown 渲染和富文本样式": ["Markdown", "rich text"],
        },
    )

    planned = engine._planned_repo_search_queries(requirement)

    assert any("terminal ui library" in item.lower() for item in planned)
    assert any("python tui" in item.lower() for item in planned)
    assert "python" not in planned
    assert engine._to_github_repo_query("开源 Python 终端 UI 库") == "开源 Python 终端 in:name,description,readme"
    assert "terminal ui library" in engine._requirement_aliases(requirement)
    topics = engine._planned_topic_search_queries(requirement)
    assert "progress-bar" in topics
    assert "tui" in engine._topic_query_variants(list(requirement.evidence_aliases.values())[0])


def test_chinese_terminal_ui_identity_becomes_core_requirement() -> None:
    engine = DeepSearchEngine()
    requirement = Requirement(
        raw="找一个开源 Python 终端 UI 库，支持表格、进度条、Markdown 渲染和富文本样式。",
        intent="寻找一个开源的 Python 终端 UI 库，支持表格、进度条、Markdown 渲染和富文本样式",
        must_have_features=["开源 Python 终端 UI 库", "支持表格", "支持进度条", "支持 Markdown 渲染", "支持富文本样式"],
        nice_to_have_features=[],
        target_platforms=["Python API"],
        search_queries=["Python terminal UI table progress Markdown"],
        feature_concepts={
            "literal_keywords": ["开源 Python 终端 UI 库", "表格", "进度条", "Markdown 渲染", "富文本样式"],
            "domains": ["开源", "Python", "终端 UI", "Python 库"],
            "actions": ["支持", "渲染", "显示"],
            "objects": ["表格", "进度条", "Markdown", "富文本"],
            "outputs": ["终端 UI"],
            "interfaces": ["Python API"],
        },
        evidence_aliases={
            "开源 Python 终端 UI 库": ["open source Python TUI library", "Python terminal UI library"],
            "支持表格": ["table widget"],
            "支持进度条": ["progress bar"],
            "支持 Markdown 渲染": ["Markdown render"],
            "支持富文本样式": ["rich text style"],
        },
    )

    assert engine._core_requirement_feature(requirement) == "开源 Python 终端 UI 库"


def test_core_alias_counts_when_domain_concepts_use_another_language() -> None:
    engine = DeepSearchEngine()
    requirement = Requirement(
        raw="找一个开源 Python 终端 UI 库",
        intent="寻找开源 Python 终端 UI 库",
        must_have_features=["开源 Python 终端 UI 库"],
        nice_to_have_features=[],
        target_platforms=[],
        search_queries=["Python terminal UI library"],
        feature_concepts={"domains": ["终端 UI", "Python 库"]},
        evidence_aliases={"开源 Python 终端 UI 库": ["TUI", "Python terminal UI library"]},
    )
    repo = CandidateRepository(
        owner="demo",
        name="tui-lib",
        url="https://github.com/demo/tui-lib",
        description="A Python TUI library for terminal applications.",
        topics=["python", "tui"],
    )

    assert engine._core_direction_score(requirement, repo) > 0


def test_ranking_prefers_requested_repository_language_from_current_requirement() -> None:
    engine = DeepSearchEngine()
    requirement = Requirement(
        raw="Need a Python terminal UI library",
        intent="Find Python terminal UI library",
        must_have_features=["Python terminal UI library"],
        nice_to_have_features=[],
        target_platforms=["Python API"],
        search_queries=["Python terminal UI library"],
        feature_concepts={"interfaces": ["Python API"], "domains": ["terminal UI"]},
        evidence_aliases={"Python terminal UI library": ["Python terminal UI library", "TUI"]},
    )
    python_repo = CandidateRepository(
        owner="demo",
        name="python-ui",
        url="https://github.com/demo/python-ui",
        description="Terminal UI library for Python applications.",
        language="Python",
        topics=["terminal-ui"],
        readme="Terminal UI library for Python applications.",
    )
    other_repo = CandidateRepository(
        owner="demo",
        name="other-ui",
        url="https://github.com/demo/other-ui",
        description="Terminal UI library for command line applications.",
        language="Go",
        topics=["terminal-ui"],
        readme="Terminal UI library for command line applications.",
    )

    ranked = engine._rank_candidates(requirement, [other_repo, python_repo])

    assert ranked[0].full_name == "demo/python-ui"


def test_evidence_gate_accepts_current_aliases_and_plural_readme_terms() -> None:
    engine = DeepSearchEngine()
    requirement = Requirement(
        raw="找一个开源 Python 终端 UI 库，支持表格、进度条、Markdown 渲染和富文本样式。",
        intent="寻找开源 Python 终端 UI 库",
        must_have_features=["开源 Python 终端 UI 库", "支持表格", "支持进度条", "支持 Markdown 渲染", "支持富文本样式"],
        nice_to_have_features=[],
        target_platforms=["Python API"],
        search_queries=["Python terminal UI table progress Markdown"],
        feature_concepts={
            "domains": ["终端 UI", "Python 库"],
            "actions": ["渲染", "显示", "支持"],
            "objects": ["表格", "进度条", "Markdown", "富文本"],
            "interfaces": ["Python API", "终端"],
        },
        evidence_aliases={
            "开源 Python 终端 UI 库": ["Python TUI library", "Python terminal UI library"],
            "支持表格": ["table widget"],
            "支持进度条": ["progress bar"],
            "支持 Markdown 渲染": ["markdown"],
            "支持富文本样式": ["rich text"],
        },
    )
    repo = CandidateRepository(
        owner="demo",
        name="terminal-ui-kit",
        url="https://github.com/demo/terminal-ui-kit",
        description="A Python library for rich text and beautiful formatting in the terminal.",
        language="Python",
        topics=["python", "tui", "tables", "progress-bar", "markdown"],
        license="MIT",
        readme="Render pretty tables, progress bars, markdown, and rich text in terminal applications.",
    )

    coverage = {item.feature: item for item in engine._build_evidence_coverage(repo, requirement)}

    assert coverage["开源 Python 终端 UI 库"].status == "supported"
    assert coverage["支持表格"].status == "supported"


def test_core_capability_queries_run_before_secondary_output_queries() -> None:
    engine = DeepSearchEngine()
    requirement = Requirement(
        raw="星河平台关键词评论查询并生成 PDF",
        intent="查询星河平台内容和评论",
        must_have_features=["星河平台内容和评论查询", "生成 PDF"],
        nice_to_have_features=[],
        target_platforms=[],
        search_queries=["星河平台 PDF"],
        repo_search_queries=["PDF report MCP"],
        feature_concepts={
            "domains": ["星河平台", "StarRiver"],
            "actions": ["查询", "search"],
            "objects": ["评论", "comments"],
        },
        evidence_aliases={
            "星河平台内容和评论查询": ["StarRiver comments search"],
            "生成 PDF": ["PDF report"],
        },
    )

    planned = engine._planned_repo_search_queries(requirement)

    assert "星河平台 评论" in planned
    assert "StarRiver comments" in planned
    assert planned.index("StarRiver comments") < planned.index("PDF report MCP")


def test_analysis_keeps_unreturned_repositories_for_evidence_gate() -> None:
    engine = DeepSearchEngine()
    requirement = Requirement(
        raw="need search",
        intent="find search tool",
        must_have_features=["search"],
        nice_to_have_features=[],
        target_platforms=[],
        search_queries=["search"],
        evidence_aliases={"search": ["search"]},
    )
    repos = [
        CandidateRepository(owner="demo", name=name, url=f"https://github.com/demo/{name}", readme="search")
        for name in ["one", "two", "three"]
    ]

    class PartialLLM:
        async def json_chat(self, system: str, prompt: str) -> dict[str, object]:
            return {
                "projects": [
                    {
                        "repo": "demo/one",
                        "match_score": 80,
                        "recommendation": "match",
                        "directly_usable": True,
                        "covered_features": ["search"],
                        "different_features": [],
                        "missing_features": [],
                        "unknown_features": [],
                        "required_changes": [],
                        "risks": [],
                        "evidence": [],
                    }
                ]
            }

    analyses = asyncio.run(engine._analyze_top_projects(requirement, repos, PartialLLM()))

    assert {item.repo.full_name for item in analyses} == {"demo/one", "demo/two", "demo/three"}


class FakeGitHub:
    def __init__(self) -> None:
        self.repo_per_pages: list[int] = []
        self.code_per_pages: list[int] = []
        self.topic_per_pages: list[int] = []
        self.issue_per_pages: list[int] = []

    async def search_repositories(self, query: str, per_page: int = 10) -> list[CandidateRepository]:
        self.repo_per_pages.append(per_page)
        return [
            CandidateRepository(
                owner="demo",
                name=f"repo-hit-{index}",
                url=f"https://github.com/demo/repo-hit-{index}",
                found_by=[f"github:{query}"],
            )
            for index in range(12)
        ]

    async def search_code_repositories(self, query: str, per_page: int = 10) -> list[tuple[str, str, str]]:
        self.code_per_pages.append(per_page)
        return [("demo", "code-hit", "src/evidence.ts")]

    async def search_topic_repositories(self, topic: str, per_page: int = 10) -> list[CandidateRepository]:
        self.topic_per_pages.append(per_page)
        return [
            CandidateRepository(
                owner="demo",
                name="topic-hit",
                url="https://github.com/demo/topic-hit",
                found_by=[f"github_topic:{topic}"],
            )
        ]

    async def search_issue_repositories(self, query: str, per_page: int = 10) -> list[tuple[str, str]]:
        self.issue_per_pages.append(per_page)
        return [("demo", "issue-hit")]

    async def get_repository(self, owner: str, name: str, found_by: str = "github:url") -> CandidateRepository:
        return CandidateRepository(
            owner=owner,
            name=name,
            url=f"https://github.com/{owner}/{name}",
            found_by=[found_by],
        )


class CountingGitHub:
    def __init__(self) -> None:
        self.readme_calls = 0
        self.tree_calls = 0
        self.file_calls = 0

    async def fetch_readme(self, repo: CandidateRepository) -> str:
        self.readme_calls += 1
        return "feature evidence"

    async def fetch_tree_paths(self, repo: CandidateRepository) -> list[str]:
        self.tree_calls += 1
        return ["src/feature.py"]

    async def fetch_file_text(self, repo: CandidateRepository, path: str, max_chars: int = 10000) -> str:
        self.file_calls += 1
        return "feature evidence"


class WaveGitHub:
    def __init__(self, hits_per_repo_query: int) -> None:
        self.hits_per_repo_query = hits_per_repo_query
        self.repo_queries: list[str] = []
        self.code_queries: list[str] = []
        self.topic_queries: list[str] = []
        self.issue_queries: list[str] = []

    async def search_repositories(self, query: str, per_page: int = 10) -> list[CandidateRepository]:
        self.repo_queries.append(query)
        return [
            CandidateRepository(
                owner="demo",
                name=f"{query.replace(' ', '-')}-{index}",
                url=f"https://github.com/demo/{query.replace(' ', '-')}-{index}",
                found_by=[f"github:{query}"],
            )
            for index in range(self.hits_per_repo_query)
        ]

    async def search_code_repositories(self, query: str, per_page: int = 10) -> list[tuple[str, str, str]]:
        self.code_queries.append(query)
        return []

    async def search_topic_repositories(self, topic: str, per_page: int = 10) -> list[CandidateRepository]:
        self.topic_queries.append(topic)
        return []

    async def search_issue_repositories(self, query: str, per_page: int = 10) -> list[tuple[str, str]]:
        self.issue_queries.append(query)
        return []


def test_collect_candidates_uses_all_default_github_search_channels() -> None:
    engine = DeepSearchEngine()
    usage = BudgetUsage()
    requirement = Requirement(
        raw="need project",
        intent="need project",
        must_have_features=["feature"],
        nice_to_have_features=[],
        target_platforms=[],
        search_queries=["feature project"],
        repo_search_queries=["feature repo"],
        code_search_queries=["feature evidence"],
        topic_search_queries=["feature-topic"],
        issue_search_queries=["feature issue"],
        web_search_queries=["feature web"],
        evidence_aliases={"feature": ["feature evidence"]},
    )

    github = FakeGitHub()
    candidates = asyncio.run(engine._collect_candidates(requirement, github, None, usage))

    by_name = {repo.full_name: repo for repo in candidates}
    assert "demo/repo-hit-0" in by_name
    assert "demo/code-hit" in by_name
    assert "demo/topic-hit" in by_name
    assert "demo/issue-hit" in by_name
    assert any(source.startswith("github_code:") for source in by_name["demo/code-hit"].found_by)
    assert any(source.startswith("github_topic:") for source in by_name["demo/topic-hit"].found_by)
    assert any(source.startswith("github_issue:") for source in by_name["demo/issue-hit"].found_by)
    assert github.repo_per_pages
    assert all(value == 20 for value in github.repo_per_pages)
    assert github.code_per_pages
    assert all(value == 10 for value in github.code_per_pages)
    assert github.topic_per_pages
    assert all(value == 20 for value in github.topic_per_pages)
    assert github.issue_per_pages == [20]


def test_collect_candidates_runs_third_wave_only_when_two_waves_do_not_fill_top3() -> None:
    engine = DeepSearchEngine()
    requirement = Requirement(
        raw="need project",
        intent="need project",
        must_have_features=["feature"],
        nice_to_have_features=[],
        target_platforms=[],
        search_queries=["feature project"],
        repo_search_queries=["repo first", "repo second", "repo third", "repo fourth", "repo fifth", "repo sixth"],
        code_search_queries=["code first", "code second", "code third", "code fourth", "code fifth", "code sixth"],
        topic_search_queries=["topic-first", "topic-second", "topic-third", "topic-fourth", "topic-fifth", "topic-sixth"],
        issue_search_queries=["issue first", "issue second", "issue third", "issue fourth", "issue fifth", "issue sixth"],
        evidence_aliases={"feature": ["feature evidence"]},
    )

    enough_after_two = WaveGitHub(hits_per_repo_query=1)
    asyncio.run(engine._collect_candidates(requirement, enough_after_two, None, BudgetUsage()))
    # Unified budget path: 6 repo queries plus 1 union query and 1 derived alias gives 8 repo queries in two waves.
    # Unified limits: code=5, topic=8, issue=5.
    assert len(enough_after_two.repo_queries) == 8
    assert len(enough_after_two.code_queries) == 5
    assert len(enough_after_two.topic_queries) == 8
    assert len(enough_after_two.issue_queries) == 5

    not_enough_after_two = WaveGitHub(hits_per_repo_query=0)
    asyncio.run(engine._collect_candidates(requirement, not_enough_after_two, None, BudgetUsage()))
    assert len(not_enough_after_two.repo_queries) >= 7
    assert len(not_enough_after_two.code_queries) == 5
    assert len(not_enough_after_two.topic_queries) == 8
    assert len(not_enough_after_two.issue_queries) == 5


def test_unified_budget_limits() -> None:
    engine = DeepSearchEngine()

    # Unified execution path: no budget distinction.
    assert engine._budgeted_candidate_limit() == engine.settings.max_candidates
    assert engine._budgeted_github_limit() == engine.settings.max_github_requests


def test_request_limit_and_completeness() -> None:
    engine = DeepSearchEngine()
    active_limit = engine._budgeted_github_limit()
    assert active_limit == engine.settings.max_github_requests

    below_limit = BudgetUsage(github_requests=active_limit - 1)
    at_limit = BudgetUsage(github_requests=active_limit)
    assert engine._search_completeness(below_limit, active_limit)["level"] == "complete"
    assert engine._search_completeness(at_limit, active_limit)["level"] == "limited"


def test_evidence_rerank_prefers_structured_source_coverage() -> None:
    engine = DeepSearchEngine()
    requirement = Requirement(
        raw="need project",
        intent="need project",
        must_have_features=["feature"],
        nice_to_have_features=[],
        target_platforms=[],
        search_queries=["feature project"],
        repo_search_queries=["feature repo"],
        code_search_queries=["feature evidence"],
        web_search_queries=["feature web"],
        evidence_aliases={"feature": ["feature evidence"]},
    )
    weak = CandidateRepository(owner="demo", name="weak", url="https://github.com/demo/weak", raw_score=50)
    weak.evidence_coverage = [EvidenceCoverage(feature="feature", covered=True, readme_evidence=["README"])]
    strong = CandidateRepository(owner="demo", name="strong", url="https://github.com/demo/strong", raw_score=45)
    strong.evidence_coverage = [
        EvidenceCoverage(feature="feature", covered=True, source_evidence=["src/feature.ts"])
    ]

    reranked = engine._rerank_by_evidence([weak, strong], requirement)

    assert reranked[0].full_name == "demo/strong"


def test_repo_evidence_cache_avoids_duplicate_fetches() -> None:
    engine = DeepSearchEngine()
    github = CountingGitHub()
    repo = CandidateRepository(owner="demo", name="cached", url="https://github.com/demo/cached")
    requirement = Requirement(
        raw="need feature",
        intent="need feature",
        must_have_features=["feature"],
        nice_to_have_features=[],
        target_platforms=[],
        search_queries=["feature"],
        evidence_aliases={"feature": ["feature evidence"]},
    )

    asyncio.run(engine._fetch_readme_into(repo, github))
    asyncio.run(engine._fetch_readme_into(repo, github))
    asyncio.run(engine._fetch_source_evidence_into(repo, github, requirement))
    asyncio.run(engine._fetch_source_evidence_into(repo, github, requirement))

    assert github.readme_calls == 1
    assert github.tree_calls == 1
    assert github.file_calls == 1
