from __future__ import annotations

import asyncio
import importlib.util
import json
import subprocess
import sys

import pytest

from github_deep_search.models import (
    BudgetUsage,
    CandidateRepository,
    EvidenceCoverage,
    ProjectAnalysis,
    Requirement,
    SearchReport,
)
from github_deep_search.serializers import report_to_dict
from github_deep_search.utils import simple_markdown_to_html


def fake_report() -> SearchReport:
    repo = CandidateRepository(
        owner="demo",
        name="project",
        url="https://github.com/demo/project",
        stars=12,
        last_pushed_at="2026-01-15T08:30:00Z",
        language="Python",
        license="MIT",
        found_by=["github:test"],
    )
    analysis = ProjectAnalysis(
        repo=repo,
        match_score=82,
        recommendation="可作为基础项目二次开发",
        directly_usable=False,
        covered_features=["搜索"],
        missing_features=["截图"],
        required_changes=["补充截图能力"],
        risks=["接口可能变化"],
        evidence=["README 提到搜索能力"],
        evidence_coverage=[
            EvidenceCoverage(
                feature="搜索",
                covered=True,
                readme_evidence=["README (搜索)"],
            )
        ],
    )
    requirement = Requirement(
        raw="测试需求",
        intent="寻找测试项目",
        must_have_features=["搜索", "截图"],
        nice_to_have_features=["报告"],
        target_platforms=["CLI"],
        search_queries=["test search"],
        repo_search_queries=["test repo search"],
        code_search_queries=["test code search"],
        topic_search_queries=["test-topic"],
        issue_search_queries=["test issue search"],
        web_search_queries=["test web search"],
        feature_concepts={"actions": ["search"], "outputs": ["screenshot"]},
        evidence_aliases={"搜索": ["search"], "截图": ["screenshot"]},
    )
    usage = BudgetUsage(
        github_requests=2,
        github_search_requests=1,
        github_code_search_requests=1,
        github_topic_search_requests=1,
        github_issue_search_requests=1,
        llm_input_tokens=1234,
        llm_output_tokens=567,
        tavily_credits=0,
        estimated_usd_complete=False,
        missing_price_components=["llm_input_usd_per_1m"],
    )
    return SearchReport(
        query="测试需求",
        requirement=requirement,
        top_projects=[analysis],
        opportunity="存在一定机会",
        summary="demo/project 相关度 82",
        report_markdown="# report",
        usage=usage,
        raw={
            "candidate_count": 1,
            "ranked_count": 1,
            "deep_pool_count": 1,
            "search_completeness": "complete",
            "search_completeness_reasons": [],
            "cache_stats": {"readmes": 1, "trees": 1, "files": 1},
        },
    )


def test_report_to_dict_contains_full_project_fields() -> None:
    data = report_to_dict(fake_report())
    project = data["topProjects"][0]
    for key in [
        "repo",
        "url",
        "score",
        "recommendation",
        "directlyUsable",
        "isReferenceCandidate",
        "confidenceLevel",
        "referenceReason",
        "coveredFeatures",
        "missingFeatures",
        "requiredChanges",
        "risks",
        "evidence",
        "evidenceCoverage",
        "foundBy",
    ]:
        assert key in project
    assert project["evidenceCoverage"][0]["covered"] is True
    assert project["stars"] == 12
    assert project["lastPushedAt"] == "2026-01-15T08:30:00Z"
    assert data["raw"]["candidate_count"] == 1
    assert data["usage"]["llmTokenEstimated"] is False
    assert data["usage"]["estimatedUsdComplete"] is False
    assert data["usage"]["missingPriceComponents"] == ["llm_input_usd_per_1m"]
    assert data["requirement"]["evidenceAliases"] == {"搜索": ["search"], "截图": ["screenshot"]}
    assert data["requirement"]["repoSearchQueries"] == ["test repo search"]
    assert data["requirement"]["codeSearchQueries"] == ["test code search"]
    assert data["requirement"]["topicSearchQueries"] == ["test-topic"]
    assert data["requirement"]["issueSearchQueries"] == ["test issue search"]
    assert data["requirement"]["webSearchQueries"] == ["test web search"]
    assert data["usage"]["githubCodeSearchRequests"] == 1
    assert data["usage"]["githubTopicSearchRequests"] == 1
    assert data["usage"]["githubIssueSearchRequests"] == 1
    assert data["raw"]["search_completeness"] == "complete"


def test_report_serializer_adds_project_metadata_to_rendered_html() -> None:
    report = fake_report()
    report.report_markdown = "\n".join(
        [
            "# 调研结论",
            "## 已整理的线索",
            "### 1. demo/project",
            "- 关联度：82/100",
        ]
    )

    data = report_to_dict(report, include_html=True)

    assert "### 1. demo/project · ★ 12 · 更新 2026-01-15" in data["reportMarkdown"]
    assert "<h3>1. demo/project · ★ 12 · 更新 2026-01-15</h3>" in data["reportHtml"]


def test_rendered_report_links_repository_urls() -> None:
    html = simple_markdown_to_html("- 地址：https://github.com/demo/project")

    assert '<a href="https://github.com/demo/project"' in html
    assert "打开 GitHub 仓库" in html
    assert ">https://github.com/demo/project<" not in html


def test_cli_json_uses_full_serializer(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from github_deep_search import __main__ as cli

    async def fake_deep_search(query: str) -> SearchReport:
        return fake_report()

    monkeypatch.setattr(cli, "deep_search", fake_deep_search)
    monkeypatch.setattr(sys, "argv", ["github_deep_search", "测试需求", "--format", "json"])
    cli.main()
    output = json.loads(capsys.readouterr().out)
    assert output["topProjects"][0]["evidence"] == ["README 提到搜索能力"]
    assert output["topProjects"][0]["requiredChanges"] == ["补充截图能力"]


def test_mcp_help_exits_without_running_server() -> None:
    if importlib.util.find_spec("mcp") is None:
        pytest.skip("mcp package is not installed")
    result = subprocess.run(
        [sys.executable, "-m", "github_deep_search.mcp_server", "--help"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0
    assert "GitHub Deep Search MCP server" in result.stdout


def test_report_uses_same_three_dimensions_for_reference_candidates() -> None:
    from github_deep_search.engine import DeepSearchEngine

    report = fake_report()
    reference = report.top_projects[0]
    reference.is_reference_candidate = True
    reference.confidence_level = "reference"
    reference.reference_reason = "参考项目：主要缺口"
    report.top_projects = [reference]
    markdown = DeepSearchEngine()._write_report(
        report.query,
        report.requirement,
        report.top_projects,
        report.opportunity,
        report.usage,
        {"level": "complete", "reasons": []},
    )

    assert "## 已整理的线索" in markdown
    assert "### 1. demo/project（参考项目） · ★ 12 · 更新 2026-01-15" in markdown
    assert "- 符合部分：" in markdown
    assert "- 缺失部分：" in markdown
    assert "- 差异部分：" not in markdown
    assert "未发现明显差异" not in markdown


def test_report_uses_continuous_project_numbering_across_groups() -> None:
    from copy import deepcopy

    from github_deep_search.engine import DeepSearchEngine

    report = fake_report()
    reliable = report.top_projects[0]
    reference = deepcopy(reliable)
    reference.repo = CandidateRepository(owner="demo", name="reference", url="https://github.com/demo/reference")
    reference.match_score = 40
    reference.is_reference_candidate = True
    reference.confidence_level = "reference"
    markdown = DeepSearchEngine()._write_report(
        report.query,
        report.requirement,
        [reliable, reference],
        report.opportunity,
        report.usage,
        {"level": "complete", "reasons": []},
    )

    assert "### 1. demo/project" in markdown
    assert "### 2. demo/reference" in markdown


def test_report_does_not_expose_internal_runtime_warnings() -> None:
    from github_deep_search.engine import DeepSearchEngine

    report = fake_report()
    report.usage.github_requests = 40
    report.usage.github_search_requests = 4
    report.usage.warnings = [
        "GitHub remaining quota is low: 9",
        "Evidence gate penalized candidates missing must-have evidence: demo/project(-24)",
        "Excluded 3 low-confidence candidate(s) with score <= 0 and no evidence-backed coverage.",
    ]

    markdown = DeepSearchEngine()._write_report(
        report.query,
        report.requirement,
        report.top_projects,
        report.opportunity,
        report.usage,
        {"level": "limited", "reasons": ["GitHub request limit reached"]},
    )

    assert "GitHub remaining quota" not in markdown
    assert "Evidence gate penalized" not in markdown
    assert "Excluded 3 low-confidence" not in markdown
    assert "搜索完整性：limited" not in markdown
    assert "GitHub 请求：" not in markdown
    assert "## 下一步" not in markdown
    assert "## 本次消耗" in markdown
    assert "Token：输入 1234，输出 567，合计 1801" in markdown
    assert "## 本次调研" not in markdown
    assert "耗时：" not in markdown
    assert "置信级别" not in markdown
    assert "许可证" not in markdown
    assert "stars：" not in markdown
    assert "must_have" not in markdown


def test_report_does_not_show_user_invisible_search_range_controls() -> None:
    from github_deep_search.engine import DeepSearchEngine

    report = fake_report()
    markdown = DeepSearchEngine()._write_report(
        report.query,
        report.requirement,
        report.top_projects,
        report.opportunity,
        report.usage,
        {"level": "limited", "reasons": ["GitHub request limit reached"]},
    )

    assert "深度调研已达到搜索范围上限" not in markdown
    assert "搜索范围" not in markdown
    assert "缩小需求范围" not in markdown
    assert "切换深度模式" not in markdown
    assert "得分原因：" in markdown


def test_report_marks_estimated_llm_tokens() -> None:
    from github_deep_search.engine import DeepSearchEngine

    report = fake_report()
    report.usage.llm_token_estimated = True
    markdown = DeepSearchEngine()._write_report(
        report.query,
        report.requirement,
        report.top_projects,
        report.opportunity,
        report.usage,
        {"level": "complete", "reasons": []},
    )

    assert "LLM Token（估算）：输入 1234，输出 567，合计 1801" in markdown


def test_no_candidate_report_states_the_gap_once() -> None:
    from github_deep_search.engine import DeepSearchEngine

    report = fake_report()
    engine = DeepSearchEngine()
    opportunity = asyncio.run(engine._analyze_opportunity(report.requirement, [], None))
    markdown = engine._write_report(
        report.query,
        report.requirement,
        [],
        opportunity,
        report.usage,
        {"level": "complete", "reasons": []},
    )

    assert markdown.count("未找到") == 1
    assert "- 暂无。" not in markdown
    assert "未列出项目：没有候选能用公开证据确认" in markdown
    assert "SearchSpec" not in markdown
    assert "应优先补充核心功能相近或相关方向" not in markdown


def test_light_report_avoids_repeated_reference_reason_and_opportunity() -> None:
    from github_deep_search.engine import DeepSearchEngine

    report = fake_report()
    reference = report.top_projects[0]
    reference.is_reference_candidate = True
    reference.confidence_level = "reference"
    reference.reference_reason = "参考项目：缺少截图"
    markdown = DeepSearchEngine()._write_report(
        report.query,
        report.requirement,
        [reference],
        report.opportunity,
        report.usage,
        {"level": "complete", "reasons": []},
    )

    assert report.opportunity not in markdown
    assert reference.reference_reason not in markdown
    assert reference.recommendation not in markdown
    assert "- 下一步：" not in markdown
    assert markdown.count("截图") <= 3


def test_light_report_centralizes_shared_reference_findings() -> None:
    from copy import deepcopy

    from github_deep_search.engine import DeepSearchEngine

    report = fake_report()
    first = report.top_projects[0]
    first.is_reference_candidate = True
    first.confidence_level = "reference"
    first.repo.description = "第一个项目简介"
    second = deepcopy(first)
    second.repo = CandidateRepository(
        owner="demo",
        name="second",
        url="https://github.com/demo/second",
        description="第二个项目简介",
    )

    markdown = DeepSearchEngine()._write_report(
        report.query,
        report.requirement,
        [first, second],
        report.opportunity,
        report.usage,
        {"level": "complete", "reasons": []},
    )

    assert "评分方式：" not in markdown
    assert markdown.count("- 符合部分：") == 2
    assert markdown.count("- 差异部分：") == 0
    assert markdown.count("- 缺失部分：") == 2
    assert "README" not in markdown
    assert "源码" not in markdown


def test_detailed_report_does_not_expose_parser_concept_groups() -> None:
    from github_deep_search.engine import DeepSearchEngine

    report = fake_report()
    markdown = DeepSearchEngine()._write_report(
        report.query,
        report.requirement,
        report.top_projects,
        report.opportunity,
        report.usage,
        {"level": "complete", "reasons": []},
    )

    assert "语义概念组" not in markdown
    assert "literal_keywords" not in markdown


def test_report_groups_low_similarity_leads_separately() -> None:
    from github_deep_search.engine import DeepSearchEngine

    report = fake_report()
    lead = report.top_projects[0]
    lead.is_reference_candidate = True
    lead.confidence_level = "lead"
    lead.reference_reason = "低相似线索：缺少核心证据"
    lead.covered_features = ["弱相邻片段"]
    report.top_projects = [lead]

    markdown = DeepSearchEngine()._write_report(
        report.query,
        report.requirement,
        report.top_projects,
        report.opportunity,
        report.usage,
        {"level": "complete", "reasons": []},
    )

    assert "相邻方向" in markdown
    assert "### 1. demo/project" in markdown
    assert "- 符合部分：" not in markdown
    assert "弱相邻片段" not in markdown


def test_three_project_report_is_plain_and_quick_to_scan() -> None:
    from copy import deepcopy

    from github_deep_search.engine import DeepSearchEngine

    report = fake_report()
    projects = []
    for index in range(3):
        project = deepcopy(report.top_projects[0])
        project.repo = CandidateRepository(
            owner="demo",
            name=f"project-{index}",
            url=f"https://github.com/demo/project-{index}",
        )
        project.unknown_features = ["自动分类"]
        project.different_features = ["使用范围不同"]
        projects.append(project)

    markdown = DeepSearchEngine()._write_report(
        report.query,
        report.requirement,
        projects,
        report.opportunity,
        report.usage,
        {"level": "complete", "reasons": []},
    )

    assert len(markdown) < 1800
    assert markdown.count("- 符合部分：") == 3
    assert markdown.count("- 差异部分：") == 3
    assert markdown.count("- 缺失部分：") == 3
    for technical_term in ["README", "源码", "路径证据", "请求预算", "must_have"]:
        assert technical_term not in markdown


def test_report_preserves_evidence_text_without_static_rephrase_table() -> None:
    from github_deep_search.engine import DeepSearchEngine

    report = fake_report()
    project = report.top_projects[0]
    project.different_features = ["项目为元仓库，实际代码在 core；MCP 服务器使用 API 和 RAG"]
    markdown = DeepSearchEngine()._write_report(
        report.query,
        report.requirement,
        [project],
        report.opportunity,
        report.usage,
        {"level": "complete", "reasons": []},
    )

    assert "项目为元仓库，实际代码在 core；MCP 服务器使用 API 和 RAG" in markdown
    assert "项目入口" not in markdown
    assert "配套工具" not in markdown


def test_low_match_report_explains_score_without_repeating_prompt() -> None:
    from github_deep_search.engine import DeepSearchEngine

    report = fake_report()
    project = report.top_projects[0]
    project.repo = CandidateRepository(
        owner="demo",
        name="clipbox-helper",
        url="https://github.com/demo/clipbox-helper",
    )
    project.match_score = 37
    project.is_reference_candidate = True
    project.confidence_level = "reference"
    project.covered_features = ["MCP 可通过 BridgeBox 运行"]
    project.different_features = ["通过其他平台间接获取内容；需要用户自行组合操作流程"]
    project.missing_features = []
    project.unknown_features = [
        "ClipBox 热门内容和热门评论查询",
        "截图",
        "评论截图",
        "生成 PDF 报告",
        "关键词热度 Top 10",
        "多端运行",
        "网页版",
    ]
    engine = DeepSearchEngine()
    project.score_reason = engine._score_reason(project)
    opportunity = asyncio.run(engine._analyze_opportunity(report.requirement, [project], None))
    markdown = engine._write_report(
        report.query,
        report.requirement,
        [project],
        opportunity,
        report.usage,
        {"level": "complete", "reasons": []},
    )

    assert "评分方式：" not in markdown
    assert "考虑限制后的适用度" not in markdown
    assert "得分原因：仅确认MCP 可通过 BridgeBox 运行" in markdown
    assert "仍未确认" not in markdown
    assert "本次已经核对候选项目的公开说明和重点内容" not in markdown
    assert "用户试用" not in markdown


def test_report_omits_empty_difference_and_missing_rows() -> None:
    from github_deep_search.engine import DeepSearchEngine

    report = fake_report()
    project = report.top_projects[0]
    project.different_features = []
    project.unknown_features = []
    project.missing_features = []

    markdown = DeepSearchEngine()._write_report(
        report.query,
        report.requirement,
        [project],
        report.opportunity,
        report.usage,
        {"level": "complete", "reasons": []},
    )

    assert "- 符合部分：" in markdown
    assert "- 差异部分：" not in markdown
    assert "- 缺失部分：" not in markdown
    assert "未发现明显差异" not in markdown
    assert "未发现明确缺失" not in markdown


def test_report_omits_unconfirmed_rows_and_unknown_feature_text() -> None:
    from github_deep_search.engine import DeepSearchEngine

    report = fake_report()
    project = report.top_projects[0]
    project.covered_features = []
    project.unknown_features = ["评论截图", "PDF 报告"]
    project.different_features = []
    project.missing_features = []
    project.score_reason = ""

    markdown = DeepSearchEngine()._write_report(
        report.query,
        report.requirement,
        [project],
        report.opportunity,
        report.usage,
        {"level": "complete", "reasons": []},
    )

    assert "- 符合部分：" not in markdown
    assert "- 差异部分：" not in markdown
    assert "暂未确认" not in markdown
    assert "尚未确认" not in markdown
    assert "评论截图" not in markdown
    assert "PDF 报告" not in markdown


def test_next_step_does_not_turn_unknown_into_missing() -> None:
    from github_deep_search.engine import DeepSearchEngine

    report = fake_report()
    project = report.top_projects[0]
    project.is_reference_candidate = False
    project.covered_features = ["ClipBox 热门内容和评论查询"]
    project.unknown_features = ["评论截图", "PDF 报告"]
    project.missing_features = []
    engine = DeepSearchEngine()

    opportunity = asyncio.run(engine._analyze_opportunity(report.requirement, [project], None))

    assert "尚未确认「评论截图、PDF 报告」" in opportunity
    assert "缺少评论截图" not in opportunity
    assert "确认确实没有后，再决定补充开发" in opportunity
