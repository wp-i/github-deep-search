from __future__ import annotations

from github_deep_search.engine import DeepSearchEngine
from github_deep_search.models import BudgetUsage, CandidateRepository, EvidenceCoverage, ProjectAnalysis, Requirement


def test_evidence_coverage_tracks_sources_per_must_have() -> None:
    engine = DeepSearchEngine()
    requirement = Requirement(
        raw="Find an extension that summarizes web pages and syncs to Notion.",
        intent="Find browser extension",
        must_have_features=["summarize web pages", "sync to Notion", "export PDF"],
        nice_to_have_features=[],
        target_platforms=["browser extension"],
        search_queries=["web page summary Notion browser extension"],
        evidence_aliases={
            "summarize web pages": ["summarize", "summary"],
            "sync to Notion": ["notion", "syncToNotion"],
            "export PDF": ["pdf export"],
        },
    )
    repo = CandidateRepository(
        owner="demo",
        name="extension",
        url="https://github.com/demo/extension",
        readme="Browser extension that can summarize web pages and save notes.",
        file_paths=["src/notion_sync.ts", "src/content_script.ts", "README.md"],
        key_files={
            "src/notion_sync.ts": "async function syncToNotion(summary) { return notion.pages.create(summary); }"
        },
    )

    coverage = engine._build_evidence_coverage(repo, requirement)

    by_feature = {item.feature: item for item in coverage}
    assert by_feature["summarize web pages"].covered is True
    assert by_feature["summarize web pages"].readme_evidence
    assert by_feature["sync to Notion"].covered is True
    assert by_feature["sync to Notion"].source_evidence
    assert by_feature["sync to Notion"].path_evidence
    assert by_feature["export PDF"].covered is False
    assert by_feature["export PDF"].status == "unknown"
    assert by_feature["export PDF"].unknown_reason
    assert not by_feature["export PDF"].missing_reason


def test_evidence_gate_keeps_unconfirmed_feature_out_of_missing() -> None:
    engine = DeepSearchEngine()
    usage = BudgetUsage()
    requirement = Requirement(
        raw="Find an extension that summarizes web pages and exports PDF.",
        intent="Find browser extension",
        must_have_features=["summarize web pages", "export PDF"],
        nice_to_have_features=[],
        target_platforms=["browser extension"],
        search_queries=["web page summary PDF extension"],
        evidence_aliases={
            "summarize web pages": ["summarize", "summary"],
            "export PDF": ["pdf export"],
        },
    )
    repo = CandidateRepository(
        owner="demo",
        name="extension",
        url="https://github.com/demo/extension",
        readme="Browser extension that can summarize web pages.",
    )
    analysis = ProjectAnalysis(
        repo=repo,
        match_score=90,
        recommendation="Looks usable",
        directly_usable=True,
        covered_features=["summarize web pages", "export PDF"],
        missing_features=[],
        required_changes=[],
        risks=[],
        evidence=[],
    )

    gated, stats = engine._apply_evidence_gate(requirement, [analysis], usage)

    assert gated[0].match_score <= 90
    assert gated[0].directly_usable is False
    assert "export PDF" not in gated[0].missing_features
    assert "export PDF" in gated[0].unknown_features
    assert "export PDF" not in gated[0].covered_features
    assert stats["penalized_count"] == 0
    assert stats["unknown_feature_count"] == 1
    assert not usage.warnings


def test_clear_readme_evidence_can_produce_high_match_score() -> None:
    engine = DeepSearchEngine()
    usage = BudgetUsage()
    requirement = Requirement(
        raw="Need autonomous planning and testing.",
        intent="Find autonomous developer",
        must_have_features=["autonomous planning", "automated testing"],
        nice_to_have_features=[],
        target_platforms=[],
        search_queries=["autonomous planning automated testing"],
        evidence_aliases={
            "autonomous planning": ["autonomous planning"],
            "automated testing": ["automated testing"],
        },
    )
    repo = CandidateRepository(
        owner="demo",
        name="claims-only",
        url="https://github.com/demo/claims-only",
        readme="Includes autonomous planning and automated testing.",
    )
    analysis = ProjectAnalysis(
        repo=repo,
        match_score=92,
        recommendation="Strong match",
        directly_usable=True,
        covered_features=[],
        missing_features=[],
        required_changes=[],
        risks=[],
        evidence=[],
    )

    gated, stats = engine._apply_evidence_gate(requirement, [analysis], usage)

    assert gated[0].match_score >= 90
    assert stats["score_capped_count"] == 0


def test_semantic_evidence_cannot_accumulate_across_unrelated_sections() -> None:
    engine = DeepSearchEngine()

    assert engine._semantic_alias_match(
        "offline bird call classification with species label export",
        "Runs offline.\nRecords bird calls.\nExports files.\nShows a species label.",
    ) is False


def test_covered_features_are_derived_from_evidence_coverage() -> None:
    engine = DeepSearchEngine()
    usage = BudgetUsage()
    requirement = Requirement(
        raw="需要添加任务和截止日期",
        intent="Find task app",
        must_have_features=["add task with deadline"],
        nice_to_have_features=[],
        target_platforms=[],
        search_queries=["task deadline app"],
        evidence_aliases={"add task with deadline": ["deadline field"]},
    )
    repo = CandidateRepository(owner="demo", name="partial", url="https://github.com/demo/partial")
    analysis = ProjectAnalysis(
        repo=repo,
        match_score=70,
        recommendation="LLM thinks it may fit",
        directly_usable=True,
        covered_features=["添加任务与截止日期"],
        missing_features=[],
        required_changes=[],
        risks=[],
        evidence=[],
        evidence_coverage=engine._build_evidence_coverage(repo, requirement),
    )

    gated, _ = engine._apply_evidence_gate(requirement, [analysis], usage)

    assert gated[0].covered_features == []
    assert gated[0].missing_features == []
    assert "add task with deadline" in gated[0].unknown_features
    assert gated[0].directly_usable is False


def test_explicit_negative_statement_is_the_only_missing_case() -> None:
    engine = DeepSearchEngine()
    requirement = Requirement(
        raw="Need PDF export",
        intent="Find export tool",
        must_have_features=["PDF export"],
        nice_to_have_features=[],
        target_platforms=[],
        search_queries=["PDF export"],
        evidence_aliases={"PDF export": ["PDF export"]},
    )
    repo = CandidateRepository(
        owner="demo",
        name="no-pdf",
        url="https://github.com/demo/no-pdf",
        readme="PDF export is not supported.",
    )

    coverage = engine._build_evidence_coverage(repo, requirement)

    assert coverage[0].status == "missing"
    assert coverage[0].missing_reason


def test_paperless_style_candidate_is_not_crushed_without_source_files() -> None:
    engine = DeepSearchEngine()
    requirement = Requirement(
        raw="self-hosted document manager with OCR, search, tags, Docker and local data",
        intent="Find document manager",
        must_have_features=[
            "self-hosted",
            "OCR",
            "full-text search",
            "automatic tagging",
            "Docker deployment",
            "local data storage",
        ],
        nice_to_have_features=[],
        target_platforms=[],
        search_queries=["self hosted OCR document manager"],
        evidence_aliases={
            "self-hosted": ["self-hosted", "local server"],
            "OCR": ["OCR"],
            "full-text search": ["searchable", "full-text search"],
            "automatic tagging": ["automatic matching", "automatic tagging"],
            "Docker deployment": ["Docker Compose"],
            "local data storage": ["local storage", "local server"],
        },
    )
    repo = CandidateRepository(
        owner="paperless-ngx",
        name="paperless-ngx",
        url="https://github.com/paperless-ngx/paperless-ngx",
        readme=(
            "A self-hosted document manager that uses OCR to create a searchable archive. "
            "Run it on a local server with Docker Compose. It supports local storage and automatic matching."
        ),
    )
    analysis = ProjectAnalysis(
        repo=repo,
        match_score=92,
        recommendation="Strong match",
        directly_usable=True,
        covered_features=[],
        missing_features=[],
        required_changes=[],
        risks=[],
        evidence=[],
    )

    gated, _ = engine._apply_evidence_gate(requirement, [analysis], BudgetUsage())

    assert gated[0].match_score >= 60
    assert gated[0].missing_features == []
    assert len(gated[0].covered_features) == 6


def test_n8n_style_candidate_stays_visible_with_license_difference() -> None:
    engine = DeepSearchEngine()
    requirement = Requirement(
        raw="open-source workflow automation with visual editor, webhooks, schedules, Docker, PostgreSQL, integrations",
        intent="Find workflow automation",
        must_have_features=[
            "open source",
            "visual node editor",
            "webhooks",
            "scheduled workflows",
            "Docker deployment",
            "PostgreSQL support",
            "reusable integrations",
        ],
        nice_to_have_features=[],
        target_platforms=[],
        search_queries=["open source workflow automation"],
        evidence_aliases={
            "open source": ["open source"],
            "visual node editor": ["visual node editor"],
            "webhooks": ["webhooks"],
            "scheduled workflows": ["scheduled workflows"],
            "Docker deployment": ["Docker"],
            "PostgreSQL support": ["PostgreSQL"],
            "reusable integrations": ["integrations"],
        },
    )
    repo = CandidateRepository(
        owner="n8n-io",
        name="n8n",
        url="https://github.com/n8n-io/n8n",
        license="NOASSERTION",
        readme=(
            "Workflow automation with a visual node editor, webhooks, scheduled workflows, Docker, "
            "PostgreSQL and reusable integrations."
        ),
        key_files={"LICENSE.md": "Sustainable Use License"},
    )
    analysis = ProjectAnalysis(
        repo=repo,
        match_score=90,
        recommendation="Strong functional match",
        directly_usable=True,
        covered_features=[],
        missing_features=[],
        required_changes=[],
        risks=[],
        evidence=[],
    )

    gated, _ = engine._apply_evidence_gate(requirement, [analysis], BudgetUsage())

    assert gated[0].match_score >= 70
    assert gated[0].suitability_score < gated[0].functional_score
    assert "开源许可带有额外使用限制" in gated[0].different_features
    assert gated[0].missing_features == []


def test_secondary_features_cannot_outvote_core_but_remain_adjacent_reference() -> None:
    engine = DeepSearchEngine()
    requirement = Requirement(
        raw="ClipBox search with screenshots PDF web and connector",
        intent="Find ClipBox research tool",
        must_have_features=[
            "ClipBox media and comment search",
            "comment screenshots",
            "PDF report",
            "Top 10 ranking",
            "multi-platform",
            "web version",
            "MCP through BridgeBox",
        ],
        nice_to_have_features=[],
        target_platforms=[],
        search_queries=["ClipBox comment search"],
        evidence_aliases={
            "ClipBox media and comment search": ["ClipBox comment search"],
            "comment screenshots": ["comment screenshot"],
            "PDF report": ["PDF report"],
            "Top 10 ranking": ["Top 10"],
            "multi-platform": ["multi-platform"],
            "web version": ["web app"],
            "MCP through BridgeBox": ["BridgeBox MCP"],
        },
    )
    repo = CandidateRepository(
        owner="demo",
        name="peripheral-only",
        url="https://github.com/demo/peripheral-only",
        readme="comment screenshot PDF report Top 10 multi-platform web app BridgeBox MCP",
    )
    analysis = ProjectAnalysis(
        repo=repo,
        match_score=90,
        recommendation="Looks related",
        directly_usable=True,
        covered_features=[],
        missing_features=[],
        required_changes=[],
        risks=[],
        evidence=[],
    )

    gated, stats = engine._apply_evidence_gate(requirement, [analysis], BudgetUsage())
    selected = engine._with_reference_candidates([], gated, BudgetUsage())

    assert 0 < gated[0].match_score < 50
    assert gated[0].core_feature == "ClipBox media and comment search"
    assert gated[0].core_confirmed is False
    assert stats["core_requirement_unconfirmed_count"] == 1
    assert len(selected) == 1
    assert selected[0].confidence_level == "lead"
    assert selected[0].is_reference_candidate is True


def test_confirmed_difference_removes_feature_from_confirmed_list() -> None:
    engine = DeepSearchEngine()
    requirement = Requirement(
        raw="search and PDF report",
        intent="Find a research tool",
        must_have_features=["keyword search", "生成PDF报告"],
        nice_to_have_features=[],
        target_platforms=[],
        search_queries=["keyword search PDF report"],
        evidence_aliases={
            "keyword search": ["keyword search"],
            "生成PDF报告": ["PDF report"],
        },
    )
    repo = CandidateRepository(
        owner="demo",
        name="html-report",
        url="https://github.com/demo/html-report",
        readme="keyword search PDF report",
    )
    analysis = ProjectAnalysis(
        repo=repo,
        match_score=70,
        recommendation="partial",
        directly_usable=False,
        covered_features=[],
        missing_features=[],
        required_changes=[],
        risks=[],
        evidence=[],
        different_features=["生成的是交互式HTML报告，而非PDF报告"],
    )

    gated, _ = engine._apply_evidence_gate(requirement, [analysis], BudgetUsage())

    assert "生成PDF报告" not in gated[0].covered_features
    assert "生成PDF报告" in gated[0].different_features


def test_explicit_absence_moves_feature_to_missing_not_difference() -> None:
    engine = DeepSearchEngine()
    requirement = Requirement(
        raw="report with screenshots",
        intent="Create report",
        must_have_features=["report", "评论截图"],
        nice_to_have_features=[],
        target_platforms=[],
        search_queries=["report screenshots"],
        evidence_aliases={"report": ["report"], "评论截图": ["评论截图"]},
    )
    repo = CandidateRepository(
        owner="demo",
        name="text-report",
        url="https://github.com/demo/text-report",
        readme="report 评论截图",
    )
    analysis = ProjectAnalysis(
        repo=repo,
        match_score=60,
        recommendation="partial",
        directly_usable=False,
        covered_features=[],
        missing_features=[],
        required_changes=[],
        risks=[],
        evidence=[],
        different_features=["它只生成文本报告，不包含评论截图"],
    )

    gated, _ = engine._apply_evidence_gate(requirement, [analysis], BudgetUsage())

    assert "评论截图" in gated[0].missing_features
    assert "评论截图" not in gated[0].covered_features
    assert "它只生成文本报告，不包含评论截图" not in gated[0].different_features


def test_documentation_images_do_not_prove_screenshot_capability() -> None:
    engine = DeepSearchEngine()
    requirement = Requirement(
        raw="must capture comment screenshots",
        intent="Capture comments",
        must_have_features=["评论截图"],
        nice_to_have_features=[],
        target_platforms=[],
        search_queries=["comment screenshots"],
        evidence_aliases={"评论截图": ["截图", "screenshot"]},
    )
    repo = CandidateRepository(
        owner="demo",
        name="docs-only",
        url="https://github.com/demo/docs-only",
        readme="# 程序截图\n![screenshot](docs/screenshots/main.png)",
        file_paths=["docs/screenshots/main.png"],
    )

    coverage = engine._build_evidence_coverage(repo, requirement)

    assert coverage[0].status == "unknown"
    assert coverage[0].covered is False


def test_two_confirmed_features_cannot_score_near_full_match() -> None:
    engine = DeepSearchEngine()
    features = ["core search", "web"] + [f"feature {index}" for index in range(6)]
    requirement = Requirement(
        raw="core search with web and six other features",
        intent="core search",
        must_have_features=features,
        nice_to_have_features=[],
        target_platforms=[],
        search_queries=["core search"],
        evidence_aliases={feature: [feature] for feature in features},
    )
    repo = CandidateRepository(
        owner="demo",
        name="partial",
        url="https://github.com/demo/partial",
        readme="core search web",
    )
    analysis = ProjectAnalysis(
        repo=repo,
        match_score=100,
        recommendation="partial",
        directly_usable=False,
        covered_features=[],
        missing_features=[],
        required_changes=[],
        risks=[],
        evidence=[],
    )

    gated, _ = engine._apply_evidence_gate(requirement, [analysis], BudgetUsage())

    assert gated[0].core_confirmed is True
    assert gated[0].match_score <= 60


def test_short_ascii_alias_requires_token_boundary() -> None:
    engine = DeepSearchEngine()

    assert engine._matching_terms("supports an MCP server", {"mcp"}) == ["mcp"]
    assert engine._matching_terms("internal tmcpvalue only", {"mcp"}) == []


def test_core_feature_requires_domain_action_and_object_in_one_statement() -> None:
    engine = DeepSearchEngine()
    requirement = Requirement(
        raw="ClipBox keyword search posts and comments",
        intent="Query ClipBox posts and comments",
        must_have_features=["ClipBox posts and comments keyword search"],
        nice_to_have_features=[],
        target_platforms=[],
        search_queries=["ClipBox posts comments search"],
        feature_concepts={
            "domains": ["ClipBox"],
            "actions": ["keyword search", "search"],
            "objects": ["posts", "comments"],
        },
        evidence_aliases={"ClipBox posts and comments keyword search": ["ClipBox comments search"]},
    )
    scattered = CandidateRepository(
        owner="demo",
        name="notes",
        url="https://github.com/demo/notes",
        readme="Supports ClipBox.\nEach note stores popular comments.\nSearch your notes.",
    )
    direct = CandidateRepository(
        owner="demo",
        name="search",
        url="https://github.com/demo/search",
        readme="Supports ClipBox posts and comments keyword search.",
    )
    paraphrased_direct = CandidateRepository(
        owner="demo",
        name="collector",
        url="https://github.com/demo/collector",
        readme="ClipBox posts, comments, search, and trending data collector.",
    )

    scattered_coverage = engine._build_evidence_coverage(scattered, requirement)
    direct_coverage = engine._build_evidence_coverage(direct, requirement)
    paraphrased_coverage = engine._build_evidence_coverage(paraphrased_direct, requirement)

    assert scattered_coverage[0].status == "unknown"
    assert direct_coverage[0].status == "supported"
    assert paraphrased_coverage[0].status == "supported"


def test_multi_entity_integration_requires_every_named_entity() -> None:
    engine = DeepSearchEngine()
    requirement = Requirement(
        raw="MCP through BridgeBox",
        intent="Integration",
        must_have_features=["MCP可通过BridgeBox运行"],
        nice_to_have_features=[],
        target_platforms=[],
        search_queries=["MCP BridgeBox"],
        evidence_aliases={"MCP可通过BridgeBox运行": ["MCP", "BridgeBox", "API"]},
    )
    api_only = CandidateRepository(
        owner="demo",
        name="api",
        url="https://github.com/demo/api",
        readme="Provides a public API.",
    )
    integrated = CandidateRepository(
        owner="demo",
        name="integration",
        url="https://github.com/demo/integration",
        readme="Runs as an MCP server through BridgeBox.",
    )

    api_coverage = engine._build_evidence_coverage(api_only, requirement)
    integrated_coverage = engine._build_evidence_coverage(integrated, requirement)

    assert api_coverage[0].status == "unknown"
    assert integrated_coverage[0].status == "supported"


def test_core_query_accepts_search_and_hot_list_wording() -> None:
    engine = DeepSearchEngine()
    requirement = Requirement(
        raw="星河平台热门帖子查询",
        intent="星河平台热门帖子查询",
        must_have_features=["星河平台热门帖子查询"],
        nice_to_have_features=[],
        target_platforms=[],
        search_queries=["星河平台 热门帖子 查询"],
        feature_concepts={
            "domains": ["星河平台", "StarRiver"],
            "actions": ["查询", "搜索", "query", "search"],
            "objects": ["热门帖子", "hot posts"],
        },
        evidence_aliases={"星河平台热门帖子查询": ["星河平台热门帖子", "starriver hot post query"]},
    )
    repo = CandidateRepository(
        owner="demo",
        name="collector",
        url="https://github.com/demo/collector",
        readme="星河平台帖子、评论、搜索和热榜数据采集工具。",
    )

    coverage = engine._build_evidence_coverage(repo, requirement)

    assert coverage[0].status == "supported"


def test_project_catalog_mentions_are_not_project_capabilities() -> None:
    engine = DeepSearchEngine()
    requirement = Requirement(
        raw="ClipBox search with screenshots and PDF",
        intent="Find ClipBox tool",
        must_have_features=["ClipBox search", "screenshots", "PDF report", "web version", "MCP"],
        nice_to_have_features=[],
        target_platforms=[],
        search_queries=["ClipBox search"],
        evidence_aliases={
            "ClipBox search": ["ClipBox search"],
            "screenshots": ["screenshot"],
            "PDF report": ["PDF report"],
            "web version": ["web app"],
            "MCP": ["MCP"],
        },
    )
    repo = CandidateRepository(
        owner="1c7",
        name="chinese-independent-developer",
        url="https://github.com/1c7/chinese-independent-developer",
        description="中国独立开发者项目列表 -- 分享大家都在做什么",
        readme=(
            "## 中国独立开发者项目列表\n聚合所有中国独立开发者的项目\n"
            "收录项目：ClipBox search, screenshot, PDF report, web app, MCP"
        ),
    )

    coverage = engine._build_evidence_coverage(repo, requirement)
    engine._rank_candidates(requirement, [repo])
    analysis = ProjectAnalysis(
        repo=repo,
        match_score=40,
        recommendation="Catalog only",
        directly_usable=False,
        covered_features=[],
        missing_features=[],
        required_changes=[],
        risks=[],
        evidence=[],
        evidence_coverage=coverage,
    )
    gated, _ = engine._apply_evidence_gate(requirement, [analysis], BudgetUsage())
    selected = engine._with_reference_candidates([], gated, BudgetUsage())

    assert engine._is_catalog_repository(repo) is True
    assert repo.raw_score <= 15
    assert all(item.status == "unknown" for item in coverage)
    assert all(not item.covered for item in coverage)
    assert gated[0].match_score == 0
    assert gated[0].is_catalog is True
    assert selected == []


def test_project_news_lists_are_catalog_repositories() -> None:
    engine = DeepSearchEngine()
    repo = CandidateRepository(
        owner="GitHubDaily",
        name="GitHubDaily",
        url="https://github.com/GitHubDaily/GitHubDaily",
        description="A list cool, interesting projects of GitHub",
    )

    assert engine._is_catalog_repository(repo) is True


def test_evidence_gate_does_not_special_case_open_source_constraint() -> None:
    engine = DeepSearchEngine()
    requirement = Requirement(
        raw="Find an open source browser extension.",
        intent="Find browser extension",
        must_have_features=["open source", "browser extension", "开源"],
        nice_to_have_features=[],
        target_platforms=["browser extension"],
        search_queries=["open source browser extension"],
    )

    assert engine._evidence_gate_features(requirement) == ["open source", "browser extension", "开源"]


def test_evidence_coverage_uses_semantic_concepts_for_bilingual_features() -> None:
    engine = DeepSearchEngine()
    requirement = Requirement(
        raw="找一个浏览器插件",
        intent="Find browser extension",
        must_have_features=["浏览器插件"],
        nice_to_have_features=[],
        target_platforms=["browser extension"],
        search_queries=["browser extension"],
        feature_concepts={"domains": ["browser extension", "浏览器插件"]},
        evidence_aliases={"浏览器插件": ["browser extension", "manifest.json"]},
    )
    repo = CandidateRepository(
        owner="demo",
        name="extension",
        url="https://github.com/demo/extension",
        readme="A browser extension for saving web pages.",
        file_paths=["src/extension/manifest.json"],
        key_files={"src/extension/manifest.json": '{"manifest_version": 3}'},
    )

    coverage = engine._build_evidence_coverage(repo, requirement)

    assert coverage[0].feature == "浏览器插件"
    assert coverage[0].covered is True


def test_deadline_requires_deadline_specific_evidence() -> None:
    engine = DeepSearchEngine()
    requirement = Requirement(
        raw="Need tasks with deadlines.",
        intent="Find task app",
        must_have_features=["deadline"],
        nice_to_have_features=[],
        target_platforms=["desktop"],
        search_queries=["task deadline app"],
        feature_concepts={
            "actions": ["add task", "set deadline", "mark completion"],
            "objects": ["task", "deadline", "completion status"],
        },
        evidence_aliases={"deadline": ["deadline", "due date", "截止日期"]},
    )
    repo = CandidateRepository(
        owner="demo",
        name="taskbar",
        url="https://github.com/demo/taskbar",
        readme="Change the taskbar position and set window size.",
        file_paths=["src/TaskbarSettings.cs"],
        key_files={"src/TaskbarSettings.cs": "public void SetTaskbarSize() {}"},
    )

    coverage = engine._build_evidence_coverage(repo, requirement)

    assert coverage[0].feature == "deadline"
    assert coverage[0].covered is False


def test_completion_toggle_requires_completion_specific_evidence() -> None:
    engine = DeepSearchEngine()
    requirement = Requirement(
        raw="Need tasks with completion toggle.",
        intent="Find task app",
        must_have_features=["completion toggle"],
        nice_to_have_features=[],
        target_platforms=["desktop"],
        search_queries=["task completion toggle app"],
        feature_concepts={
            "actions": ["add task", "set deadline", "mark completion"],
            "objects": ["task", "deadline", "completion status"],
        },
        evidence_aliases={"completion toggle": ["completed", "checkbox", "mark complete"]},
    )
    repo = CandidateRepository(
        owner="demo",
        name="notes",
        url="https://github.com/demo/notes",
        readme="A sticky note desktop window with task text.",
        file_paths=["src/TaskWindow.cs"],
        key_files={"src/TaskWindow.cs": "public void SetWindowTopmost() {}"},
    )

    coverage = engine._build_evidence_coverage(repo, requirement)

    assert coverage[0].feature == "completion toggle"
    assert coverage[0].covered is False


def test_summarization_requires_summary_specific_evidence() -> None:
    engine = DeepSearchEngine()
    requirement = Requirement(
        raw="Need web page summarization.",
        intent="Find summarizer",
        must_have_features=["summarize web pages"],
        nice_to_have_features=[],
        target_platforms=["browser extension"],
        search_queries=["summarize web pages"],
        evidence_aliases={"summarize web pages": ["summarize", "summary", "摘要"]},
    )
    repo = CandidateRepository(
        owner="demo",
        name="clipper",
        url="https://github.com/demo/clipper",
        readme="Save web page content to notes.",
        file_paths=["src/web_page_content.ts"],
        key_files={"src/web_page_content.ts": "export function saveWebPageContent() {}"},
    )

    coverage = engine._build_evidence_coverage(repo, requirement)

    assert coverage[0].feature == "summarize web pages"
    assert coverage[0].covered is False


def test_compound_requirement_is_not_proven_by_weak_platform_fragments() -> None:
    engine = DeepSearchEngine()
    usage = BudgetUsage()
    requirement = Requirement(
        raw="我想做一个windows10/11的谷歌浏览器插件，可以总结网页内容，并把摘要同步直接放置在桌面。",
        intent="网页摘要桌面同步工具",
        must_have_features=[
            "windows10/11的谷歌浏览器插件",
            "总结网页内容",
            "把摘要同步直接放置在桌面",
        ],
        nice_to_have_features=[],
        target_platforms=["windows10/11", "谷歌浏览器"],
        search_queries=["windows10/11 谷歌浏览器插件 总结网页内容 摘要 同步 桌面"],
        evidence_aliases={
            "windows10/11的谷歌浏览器插件": ["windows10/11", "谷歌浏览器插件"],
            "总结网页内容": ["网页", "摘要"],
            "把摘要同步直接放置在桌面": ["摘要", "同步", "桌面"],
        },
    )
    repo = CandidateRepository(
        owner="demo",
        name="desktop-player",
        url="https://github.com/demo/desktop-player",
        description="一个小而快并且功能强大的 Windows 动态桌面软件",
        readme="支持视频和网页动画播放，支持 Windows10/11 系统。支持 URL 和网页文件。",
    )
    analysis = ProjectAnalysis(
        repo=repo,
        match_score=100,
        recommendation="模型误判为强匹配",
        directly_usable=True,
        covered_features=[],
        missing_features=[],
        required_changes=[],
        risks=[],
        evidence=[],
    )

    coverage = engine._build_evidence_coverage(repo, requirement)
    gated, _ = engine._apply_evidence_gate(requirement, [analysis], usage)

    assert all(item.status == "unknown" for item in coverage)
    assert all(not item.covered for item in coverage)
    assert gated[0].match_score <= 19
    assert gated[0].directly_usable is False
    assert gated[0].confidence_level == "reliable"


def test_engine_does_not_invent_business_aliases_without_search_spec() -> None:
    engine = DeepSearchEngine()
    requirement = Requirement(
        raw="Need tasks with deadlines.",
        intent="Find task app",
        must_have_features=["deadline"],
        nice_to_have_features=[],
        target_platforms=["desktop"],
        search_queries=["task deadline app"],
    )
    repo = CandidateRepository(
        owner="demo",
        name="task",
        url="https://github.com/demo/task",
        readme="Tasks can have a due date.",
    )

    coverage = engine._build_evidence_coverage(repo, requirement)

    assert coverage[0].covered is False


def test_reference_candidates_fill_when_reliable_results_are_insufficient() -> None:
    engine = DeepSearchEngine()
    usage = BudgetUsage()
    repo = CandidateRepository(owner="demo", name="partial", url="https://github.com/demo/partial")
    analysis = ProjectAnalysis(
        repo=repo,
        match_score=42,
        recommendation="Has sticky notes but lacks deadlines.",
        directly_usable=False,
        covered_features=["sticky notes"],
        missing_features=["deadline"],
        required_changes=[],
        risks=[],
        evidence=[],
        evidence_coverage=[EvidenceCoverage(feature="sticky notes", covered=True, readme_evidence=["README"])],
    )

    selected = engine._with_reference_candidates([], [analysis], usage)

    assert len(selected) == 1
    assert selected[0].is_reference_candidate is True
    assert selected[0].confidence_level == "reference"
    assert selected[0].directly_usable is False
    assert "参考项目" in selected[0].reference_reason
    assert "低置信参考候选" not in selected[0].recommendation
    assert usage.warnings


def test_reference_label_does_not_turn_unknown_into_missing() -> None:
    engine = DeepSearchEngine()
    usage = BudgetUsage()
    repo = CandidateRepository(owner="demo", name="partial", url="https://github.com/demo/partial")
    analysis = ProjectAnalysis(
        repo=repo,
        match_score=42,
        recommendation="Partial",
        directly_usable=False,
        covered_features=["search"],
        missing_features=[],
        required_changes=[],
        risks=[],
        evidence=[],
        unknown_features=["automatic tagging"],
        evidence_coverage=[
            EvidenceCoverage(feature="search", covered=True, status="supported", readme_evidence=["README"]),
            EvidenceCoverage(feature="automatic tagging", covered=False, status="unknown"),
        ],
    )

    selected = engine._with_reference_candidates([], [analysis], usage)

    assert selected[0].missing_features == []
    assert selected[0].unknown_features == ["automatic tagging"]


def test_reference_candidates_exclude_zero_score_without_evidence_coverage() -> None:
    engine = DeepSearchEngine()
    usage = BudgetUsage()
    repo = CandidateRepository(owner="demo", name="unrelated", url="https://github.com/demo/unrelated")
    analysis = ProjectAnalysis(
        repo=repo,
        match_score=0,
        recommendation="Unrelated.",
        directly_usable=False,
        covered_features=[],
        missing_features=["feature"],
        required_changes=[],
        risks=[],
        evidence=[],
        evidence_coverage=[EvidenceCoverage(feature="feature", covered=False)],
    )

    selected = engine._with_reference_candidates([], [analysis], usage)

    assert selected == []
    assert any("Excluded 1 low-confidence candidate" in warning for warning in usage.warnings)


def test_reference_candidates_do_not_use_positive_score_as_unsupported_filler() -> None:
    engine = DeepSearchEngine()
    usage = BudgetUsage()
    repo = CandidateRepository(owner="demo", name="weak", url="https://github.com/demo/weak")
    analysis = ProjectAnalysis(
        repo=repo,
        match_score=32,
        recommendation="Weak textual match.",
        directly_usable=False,
        covered_features=[],
        missing_features=["feature"],
        required_changes=[],
        risks=[],
        evidence=[],
        evidence_coverage=[EvidenceCoverage(feature="feature", covered=False)],
    )

    selected = engine._with_reference_candidates([], [analysis], usage)

    assert selected == []


def test_generic_qualifier_does_not_become_core_requirement() -> None:
    engine = DeepSearchEngine()
    requirement = Requirement(
        raw="我想找一个开源工具，可以监控多个 GitHub 仓库的 issue 热点、评论趋势和高频问题。",
        intent="GitHub issue 热点监控工具",
        must_have_features=[
            "开源工具",
            "监控多个 GitHub 仓库的 issue 热点",
            "评论趋势和高频问题",
        ],
        nice_to_have_features=[],
        target_platforms=[],
        search_queries=["GitHub issue 热点监控 评论趋势"],
        feature_concepts={
            "domains": ["GitHub"],
            "actions": ["监控", "分析"],
            "objects": ["issue 热点", "评论趋势", "高频问题"],
        },
    )

    assert engine._core_requirement_feature(requirement) != "开源工具"
    assert engine._core_requirement_feature(requirement) == "监控多个 GitHub 仓库的 issue 热点"


def test_generic_qualifier_only_reference_is_rejected() -> None:
    engine = DeepSearchEngine()
    usage = BudgetUsage()
    requirement = Requirement(
        raw="我想找一个开源工具，可以监控多个 GitHub 仓库的 issue 热点、评论趋势和高频问题。",
        intent="GitHub issue 热点监控工具",
        must_have_features=[
            "开源工具",
            "监控多个 GitHub 仓库的 issue 热点",
            "评论趋势和高频问题",
        ],
        nice_to_have_features=[],
        target_platforms=[],
        search_queries=["GitHub issue 热点监控 评论趋势"],
    )
    analysis = ProjectAnalysis(
        repo=CandidateRepository(owner="demo", name="generic-open-source", url="https://github.com/demo/generic"),
        match_score=44,
        recommendation="Only generic qualifier.",
        directly_usable=False,
        covered_features=["开源工具"],
        missing_features=[],
        required_changes=[],
        risks=[],
        evidence=[],
        core_feature="监控多个 GitHub 仓库的 issue 热点",
        core_confirmed=False,
        evidence_coverage=[
            EvidenceCoverage(feature="开源工具", covered=True, status="supported", readme_evidence=["README"]),
            EvidenceCoverage(feature="监控多个 GitHub 仓库的 issue 热点", covered=False, status="unknown"),
            EvidenceCoverage(feature="评论趋势和高频问题", covered=False, status="unknown"),
        ],
    )

    selected = engine._with_reference_candidates([], [analysis], usage, requirement)

    assert selected == []


def test_very_low_score_with_evidence_is_retained_as_adjacent_reference() -> None:
    engine = DeepSearchEngine()
    usage = BudgetUsage()
    repo = CandidateRepository(owner="demo", name="partial", url="https://github.com/demo/partial")
    analysis = ProjectAnalysis(
        repo=repo,
        match_score=19,
        recommendation="Partial evidence.",
        directly_usable=False,
        covered_features=["feature"],
        missing_features=[],
        required_changes=[],
        risks=[],
        evidence=["README mentions feature."],
        evidence_coverage=[EvidenceCoverage(feature="feature", covered=True, readme_evidence=["README (feature)"])],
    )

    selected = engine._with_reference_candidates([], [analysis], usage)

    assert len(selected) == 1
    assert selected[0].confidence_level == "lead"


def test_low_score_output_only_evidence_is_rejected_when_core_direction_is_missing() -> None:
    engine = DeepSearchEngine()
    usage = BudgetUsage()
    requirement = Requirement(
        raw="Need social keyword search with screenshots and HTML report",
        intent="Find social research tool",
        must_have_features=["social keyword search", "screenshots", "HTML report"],
        nice_to_have_features=[],
        target_platforms=["web"],
        search_queries=["social keyword search screenshots report"],
        evidence_aliases={
            "social keyword search": ["social keyword search"],
            "screenshots": ["screenshots"],
            "HTML report": ["HTML report"],
        },
    )
    repo = CandidateRepository(
        owner="demo",
        name="reporter",
        url="https://github.com/demo/reporter",
        raw_score=5,
        readme="A web app that captures screenshots and generates an HTML report.",
    )
    analysis = ProjectAnalysis(
        repo=repo,
        match_score=12,
        recommendation="Adjacent evidence.",
        directly_usable=False,
        covered_features=[],
        missing_features=[],
        required_changes=[],
        risks=[],
        evidence=[],
        core_feature="social keyword search",
        core_confirmed=False,
        evidence_coverage=engine._build_evidence_coverage(repo, requirement),
    )

    selected = engine._with_reference_candidates([], [analysis], usage, requirement)

    assert selected == []


def test_zero_score_adjacent_lead_gets_positive_display_score() -> None:
    engine = DeepSearchEngine()
    usage = BudgetUsage()
    requirement = Requirement(
        raw="Need platform keyword search with screenshots and web report",
        intent="Find platform research tool",
        must_have_features=["platform keyword search", "screenshots", "web report"],
        nice_to_have_features=[],
        target_platforms=["web"],
        search_queries=["platform keyword search screenshots report"],
        feature_concepts={
            "domains": ["platform"],
            "actions": ["keyword search"],
            "outputs": ["screenshots", "web report"],
            "interfaces": ["web"],
        },
        evidence_aliases={
            "platform keyword search": ["platform keyword search"],
            "screenshots": ["screenshots"],
            "web report": ["web report"],
        },
    )
    repo = CandidateRepository(
        owner="demo",
        name="zero-adjacent",
        url="https://github.com/demo/zero-adjacent",
        raw_score=1,
        readme="A platform keyword search dashboard that captures screenshots and generates a web report.",
    )
    analysis = ProjectAnalysis(
        repo=repo,
        match_score=0,
        recommendation="Adjacent evidence.",
        directly_usable=False,
        covered_features=[],
        missing_features=[],
        required_changes=[],
        risks=[],
        evidence=[],
        core_feature="platform keyword search",
        core_confirmed=False,
        evidence_coverage=engine._build_evidence_coverage(repo, requirement),
    )

    selected = engine._with_reference_candidates([], [analysis], usage, requirement)

    assert len(selected) == 1
    assert selected[0].confidence_level == "lead"
    assert selected[0].match_score > 0
    assert "screenshots" in selected[0].covered_features


def test_result_tiers_fill_in_reliable_reference_adjacent_order() -> None:
    engine = DeepSearchEngine()
    usage = BudgetUsage()

    def project(name: str, score: int, *, core_confirmed: bool, core_feature: str = "core") -> ProjectAnalysis:
        repo = CandidateRepository(
            owner="demo",
            name=name,
            url=f"https://github.com/demo/{name}",
            raw_score=score,
        )
        return ProjectAnalysis(
            repo=repo,
            match_score=score,
            recommendation="candidate",
            directly_usable=score >= 50,
            covered_features=["supporting feature"],
            missing_features=[],
            required_changes=[],
            risks=[],
            evidence=[],
            core_feature=core_feature,
            core_confirmed=core_confirmed,
            evidence_coverage=[
                EvidenceCoverage(feature="supporting feature", covered=True, status="supported")
            ],
        )

    reliable = project("reliable", 70, core_confirmed=True)
    partial = project("partial", 42, core_confirmed=True)
    adjacent = project("adjacent", 39, core_confirmed=False)

    selected = engine._with_reference_candidates([reliable], [adjacent, partial], usage)

    assert [item.repo.name for item in selected] == ["reliable", "partial"]
    assert selected[1].confidence_level == "reference"


def test_reliable_result_is_filled_with_core_confirmed_references_for_top3() -> None:
    engine = DeepSearchEngine()
    usage = BudgetUsage()

    def project(name: str, score: int, *, core_confirmed: bool) -> ProjectAnalysis:
        repo = CandidateRepository(
            owner="demo",
            name=name,
            url=f"https://github.com/demo/{name}",
            raw_score=score,
        )
        return ProjectAnalysis(
            repo=repo,
            match_score=score,
            recommendation="candidate",
            directly_usable=score >= 50,
            covered_features=["core"],
            missing_features=[],
            required_changes=[],
            risks=[],
            evidence=[],
            core_feature="core",
            core_confirmed=core_confirmed,
            evidence_coverage=[
                EvidenceCoverage(feature="core", covered=core_confirmed, status="supported" if core_confirmed else "unknown")
            ],
        )

    reliable = project("reliable", 100, core_confirmed=True)
    reference = project("reference", 31, core_confirmed=True)
    lower_reference = project("lower-reference", 21, core_confirmed=True)
    noisy = project("noisy", 45, core_confirmed=False)

    selected = engine._with_reference_candidates([reliable], [noisy, reference, lower_reference], usage)

    assert [item.repo.name for item in selected] == ["reliable", "reference", "lower-reference"]
    assert selected[1].confidence_level == "reference"
    assert selected[2].confidence_level == "reference"


def test_repo_search_uses_core_evidence_aliases_without_domain_word_pack() -> None:
    engine = DeepSearchEngine()
    requirement = Requirement(
        raw="需要一个中文描述的核心排程能力工具",
        intent="核心排程能力工具",
        must_have_features=["核心排程能力"],
        nice_to_have_features=[],
        target_platforms=[],
        search_queries=["核心排程能力"],
        evidence_aliases={
            "核心排程能力": ["constraint solver", "task scheduler"],
        },
    )

    queries = engine._planned_repo_search_queries(requirement, "light", "standard")

    assert "constraint solver" in queries
    assert "task scheduler" in queries


def test_peripheral_only_candidate_is_capped_below_core_capability_match() -> None:
    engine = DeepSearchEngine()
    requirement = Requirement(
        raw="需要一个核心优化工作流工具，可以输入外部服务 API key。",
        intent="核心优化工作流工具",
        must_have_features=["核心优化工作流", "外部服务 API key", "模糊输入"],
        nice_to_have_features=[],
        target_platforms=[],
        search_queries=["核心优化工作流"],
        evidence_aliases={
            "核心优化工作流": ["optimization workflow", "workflow optimizer"],
            "外部服务 API key": ["external service api key"],
            "模糊输入": ["fuzzy input"],
        },
    )
    core_repo = CandidateRepository(
        owner="demo",
        name="workflow-optimizer",
        url="https://github.com/demo/workflow-optimizer",
        description="Workflow optimizer for optimization workflow planning.",
        stars=8,
        readme="An optimization workflow tool with a workflow optimizer.",
    )
    api_key_repo = CandidateRepository(
        owner="demo",
        name="service-dashboard",
        url="https://github.com/demo/service-dashboard",
        description="Dashboard with external service API key configuration.",
        stars=20,
        readme="Configure external service api key for dashboard analytics.",
    )

    ranked = engine._rank_candidates(requirement, [api_key_repo, core_repo])
    analyses = [
        ProjectAnalysis(
            repo=repo,
            match_score=int(repo.raw_score),
            recommendation="candidate",
            directly_usable=True,
            covered_features=[],
            missing_features=[],
            required_changes=[],
            risks=[],
            evidence=[],
            evidence_coverage=engine._build_evidence_coverage(repo, requirement),
        )
        for repo in ranked
    ]
    gated, _ = engine._apply_evidence_gate(requirement, analyses, BudgetUsage())

    by_name = {item.repo.full_name: item for item in gated}
    assert by_name["demo/workflow-optimizer"].match_score > by_name["demo/service-dashboard"].match_score
    assert by_name["demo/service-dashboard"].match_score <= 19


def test_same_named_forks_do_not_fill_multiple_top_slots() -> None:
    engine = DeepSearchEngine()
    usage = BudgetUsage()

    def adjacent(owner: str, name: str, score: int) -> ProjectAnalysis:
        repo = CandidateRepository(
            owner=owner,
            name=name,
            url=f"https://github.com/{owner}/{name}",
            raw_score=score,
        )
        return ProjectAnalysis(
            repo=repo,
            match_score=score,
            recommendation="adjacent",
            directly_usable=False,
            covered_features=["PDF"],
            missing_features=[],
            required_changes=[],
            risks=[],
            evidence=[],
            core_feature="ClipBox search",
            core_confirmed=False,
            evidence_coverage=[EvidenceCoverage(feature="PDF", covered=True, status="supported")],
        )

    selected = engine._with_reference_candidates(
        [],
        [
            adjacent("one", "TrendRadar", 30),
            adjacent("two", "TrendRadar", 29),
            adjacent("three", "OtherTool", 25),
        ],
        usage,
    )

    assert [item.repo.full_name for item in selected] == ["one/TrendRadar", "three/OtherTool"]


def test_fallback_low_similarity_leads_from_ranked_candidates() -> None:
    engine = DeepSearchEngine()
    usage = BudgetUsage()
    requirement = Requirement(
        raw="need project idea generator",
        intent="Find project idea generator",
        must_have_features=["project idea generation"],
        nice_to_have_features=[],
        target_platforms=[],
        search_queries=["project idea generator"],
        evidence_aliases={"project idea generation": ["project idea"]},
    )
    repo = CandidateRepository(
        owner="demo",
        name="idea-tool",
        url="https://github.com/demo/idea-tool",
        description="AI project idea generation",
        raw_score=21,
    )

    leads = engine._fallback_low_similarity_leads(requirement, [repo], usage)

    assert len(leads) == 1
    assert leads[0].confidence_level == "lead"
    assert leads[0].is_reference_candidate is True
    assert "低相似线索" in leads[0].reference_reason
    assert leads[0].covered_features == []
    assert leads[0].missing_features == []
    assert leads[0].unknown_features == ["project idea generation"]
    assert "公开证据只支持较弱相邻关系" in leads[0].score_reason


def test_fallback_low_similarity_leads_accept_domain_adjacent_feature_evidence() -> None:
    engine = DeepSearchEngine()
    usage = BudgetUsage()
    requirement = Requirement(
        raw="Need short video keyword search with screenshots and web report",
        intent="Find short video research tool",
        must_have_features=["short video keyword search", "screenshots", "web report"],
        nice_to_have_features=[],
        target_platforms=["web"],
        search_queries=["short video keyword search screenshots report"],
        feature_concepts={
            "domains": ["short video"],
            "actions": ["keyword search"],
            "objects": ["short video"],
            "outputs": ["screenshots", "web report"],
            "interfaces": ["web"],
        },
        evidence_aliases={
            "short video keyword search": ["short video keyword search"],
            "screenshots": ["screenshots"],
            "web report": ["web report"],
        },
    )
    repo = CandidateRepository(
        owner="demo",
        name="snapshot-report",
        url="https://github.com/demo/snapshot-report",
        description="Short video keyword search dashboards capture screenshots and publish a web report.",
        raw_score=6,
    )
    repo.evidence_coverage = engine._build_evidence_coverage(repo, requirement)

    leads = engine._fallback_low_similarity_leads(requirement, [repo], usage)

    assert len(leads) == 1
    assert leads[0].confidence_level == "lead"
    assert leads[0].match_score > 15
    assert leads[0].core_confirmed is False
    assert "short video keyword search" in leads[0].unknown_features
    assert "screenshots" in leads[0].covered_features
    assert "web report" in leads[0].covered_features


def test_fallback_low_similarity_leads_have_evidence_sensitive_scores() -> None:
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
                "filters unwanted videos on a video platform",
            ],
        },
    )
    weak = CandidateRepository(
        owner="demo",
        name="video-helper",
        url="https://github.com/demo/video-helper",
        description="Browser extension for a video platform that filters videos.",
        raw_score=24,
        found_by=["github:video platform"],
    )
    stronger = CandidateRepository(
        owner="demo",
        name="video-filter-extension",
        url="https://github.com/demo/video-filter-extension",
        description="Browser extension for a video platform that filters unwanted videos.",
        raw_score=24,
        found_by=["github:video platform", "github_code:browser extension"],
    )
    for repo in (weak, stronger):
        repo.evidence_coverage = engine._build_evidence_coverage(repo, requirement)

    leads = engine._fallback_low_similarity_leads(requirement, [weak, stronger], usage)

    assert len(leads) == 2
    scores = {item.repo.full_name: item.match_score for item in leads}
    assert scores["demo/video-filter-extension"] > scores["demo/video-helper"]
    assert len(set(scores.values())) == 2
    assert all(score < 50 for score in scores.values())


def test_fallback_low_similarity_leads_reject_generic_outputs_for_domain_request() -> None:
    engine = DeepSearchEngine()
    usage = BudgetUsage()
    requirement = Requirement(
        raw="Need short video keyword search with screenshots and web report",
        intent="Find short video research tool",
        must_have_features=["short video keyword search", "screenshots", "web report"],
        nice_to_have_features=[],
        target_platforms=["web"],
        search_queries=["short video keyword search screenshots report"],
        feature_concepts={
            "domains": ["short video"],
            "actions": ["keyword search"],
            "objects": ["short video"],
            "outputs": ["screenshots", "web report"],
            "interfaces": ["web"],
        },
        evidence_aliases={
            "short video keyword search": ["short video keyword search"],
            "screenshots": ["screenshots"],
            "web report": ["web report"],
        },
    )
    repo = CandidateRepository(
        owner="demo",
        name="generic-snapshot-report",
        url="https://github.com/demo/generic-snapshot-report",
        description="Capture screenshots and publish a web report.",
        raw_score=6,
    )
    repo.evidence_coverage = engine._build_evidence_coverage(repo, requirement)

    leads = engine._fallback_low_similarity_leads(requirement, [repo], usage)

    assert leads == []


def test_fallback_low_similarity_leads_reject_weak_core_signal_without_feature_evidence() -> None:
    engine = DeepSearchEngine()
    usage = BudgetUsage()
    requirement = Requirement(
        raw="Find a tool that monitors issue hotspots across multiple GitHub repositories and generates a dashboard",
        intent="Find GitHub issue hotspot monitoring tool",
        must_have_features=[
            "monitor issue hotspots across multiple GitHub repositories",
            "dashboard",
            "open source tool",
        ],
        nice_to_have_features=[],
        target_platforms=["web"],
        search_queries=["GitHub issue hotspot monitoring dashboard"],
        feature_concepts={
            "actions": ["monitor"],
            "objects": ["GitHub repositories", "issues", "hotspots"],
            "outputs": ["dashboard"],
            "interfaces": ["web"],
        },
        evidence_aliases={
            "monitor issue hotspots across multiple GitHub repositories": [
                "GitHub issue hotspot monitoring",
                "monitor multiple GitHub repositories",
            ],
            "dashboard": ["dashboard"],
            "open source tool": ["open source tool"],
        },
    )
    repo = CandidateRepository(
        owner="demo",
        name="generic-dashboard-tool",
        url="https://github.com/demo/generic-dashboard-tool",
        description="Open source dashboard and report tool for project analytics.",
        raw_score=29,
        core_signal_score=1.0,
    )
    repo.evidence_coverage = engine._build_evidence_coverage(repo, requirement)

    leads = engine._fallback_low_similarity_leads(requirement, [repo], usage)

    assert leads == []


def test_fallback_low_similarity_leads_reject_shared_platform_wrong_core_object() -> None:
    engine = DeepSearchEngine()
    usage = BudgetUsage()
    requirement = Requirement(
        raw="Find a tool that monitors issue hotspots across multiple GitHub repositories",
        intent="Find GitHub issue hotspot monitoring tool",
        must_have_features=["monitor issue hotspots across multiple GitHub repositories"],
        nice_to_have_features=[],
        target_platforms=[],
        search_queries=["GitHub issue hotspot monitoring"],
        feature_concepts={
            "actions": ["monitor"],
            "objects": ["GitHub repositories", "issues", "hotspots"],
        },
        evidence_aliases={
            "monitor issue hotspots across multiple GitHub repositories": [
                "monitor issue hotspots across multiple GitHub repositories"
            ],
        },
    )
    repo = CandidateRepository(
        owner="demo",
        name="github-cve-monitor",
        url="https://github.com/demo/github-cve-monitor",
        description="Realtime monitor github repositories for CVE and security tool updates.",
        raw_score=29,
    )
    repo.evidence_coverage = engine._build_evidence_coverage(repo, requirement)

    leads = engine._fallback_low_similarity_leads(requirement, [repo], usage)

    assert repo.core_signal_score < 2.0
    assert leads == []
