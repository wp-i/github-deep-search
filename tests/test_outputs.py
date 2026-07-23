from __future__ import annotations

import importlib.util
import json
import subprocess
import sys

import pytest

from github_deep_search.engine import DeepSearchEngine
from github_deep_search.models import (
    BudgetUsage,
    CandidateRepository,
    EvidenceCoverage,
    ProjectAnalysis,
    Requirement,
    RunTrace,
    SearchReport,
    StageOutcome,
)
from github_deep_search.serializers import diagnostic_report_to_dict, report_to_dict
from github_deep_search.utils import simple_markdown_to_html


def fake_report(*, language: str = "zh") -> SearchReport:
    repo = CandidateRepository(
        owner="demo",
        name="project",
        url="https://github.com/demo/project",
        description="A concise repository summary.",
        stars=12,
        last_pushed_at="2026-01-15T08:30:00Z",
        language="Python",
        license="MIT",
        found_by=["github:test"],
    )
    analysis = ProjectAnalysis(
        repo=repo,
        match_score=82,
        recommendation="Internal recommendation",
        directly_usable=False,
        covered_features=["repository search"],
        missing_features=["screenshot export"],
        required_changes=["Add screenshot export"],
        risks=["Internal risk"],
        evidence=["README supports repository search"],
        evidence_coverage=[
            EvidenceCoverage(
                feature="repository search",
                covered=True,
                status="supported",
                readme_evidence=["README (repository search)"],
            )
        ],
        verified_capabilities=[
            "搜索 GitHub 仓库" if language == "zh" else "Search GitHub repositories"
        ],
    )
    requirement = Requirement(
        raw="测试需求" if language == "zh" else "Test request",
        intent="寻找项目" if language == "zh" else "Find a project",
        must_have_features=["repository search"],
        nice_to_have_features=[],
        target_platforms=["CLI"],
        search_queries=["repository search", "仓库 搜索"],
        report_language=language,  # type: ignore[arg-type]
        repo_search_queries=["repository search", "仓库 搜索"],
        evidence_aliases={"repository search": ["repository search"]},
    )
    usage = BudgetUsage(
        github_requests=2,
        llm_input_tokens=1234,
        llm_output_tokens=567,
        estimated_usd_complete=False,
        missing_price_components=["llm_input_usd_per_1m"],
    )
    engine = DeepSearchEngine()
    markdown = engine._write_report(
        requirement.raw,
        requirement,
        [analysis],
        "",
        usage,
    )
    return SearchReport(
        query=requirement.raw,
        requirement=requirement,
        top_projects=[analysis],
        opportunity="Internal opportunity",
        summary=engine._write_summary(requirement, [analysis]),
        report_markdown=markdown,
        usage=usage,
        raw={"candidate_count": 1, "search_completeness": "complete"},
        run_trace=RunTrace(
            "1",
            "completed",
            [
                StageOutcome(name, "completed")
                for name in ("parse", "discovery", "evidence", "analysis", "report_delivery")
            ],
        ),
    )


def test_user_json_is_concise_and_keeps_requested_public_fields() -> None:
    data = report_to_dict(fake_report())
    project = data["topProjects"][0]

    assert set(data) == {"summary", "reportMarkdown", "topProjects", "usage"}
    assert set(project) == {
        "repo",
        "url",
        "relevance",
        "summary",
        "covered",
        "stars",
        "lastPushedAt",
    }
    assert project["relevance"] == 82
    assert project["summary"] == "A concise repository summary."
    assert project["covered"] == ["搜索 GitHub 仓库", "repository search"]
    assert project["stars"] == 12
    assert project["lastPushedAt"] == "2026-01-15T08:30:00Z"
    assert set(data["usage"]) == {
        "llmInputTokens",
        "llmOutputTokens",
        "llmTokenEstimated",
    }
    assert "requirement" not in data
    assert "raw" not in data
    assert "missingFeatures" not in project
    assert "evidenceCoverage" not in project


def test_english_public_summary_uses_the_repository_description() -> None:
    project = report_to_dict(fake_report(language="en"))["topProjects"][0]

    assert project["summary"] == "A concise repository summary."
    assert project["summary"] not in project["covered"]


def test_public_summary_rejects_a_description_identical_to_the_repository_name() -> None:
    report = fake_report(language="en")
    report.top_projects[0].repo.name = "placeholder"
    report.top_projects[0].repo.description = "placeholder"

    project = report_to_dict(report)["topProjects"][0]

    assert project["summary"] == (
        "Public project materials confirm: "
        "Search GitHub repositories; repository search."
    )


def test_public_summary_remains_distinct_when_description_is_the_capability_evidence() -> None:
    report = fake_report()
    report.top_projects[0].verified_capabilities = [
        report.top_projects[0].repo.description.rstrip(".")
    ]

    project = report_to_dict(report)["topProjects"][0]

    assert project["summary"].endswith("（主要语言：Python）")
    assert project["summary"] != project["covered"][0]


def test_diagnostic_serializer_retains_trace_and_evidence_for_evaluation() -> None:
    data = diagnostic_report_to_dict(fake_report())

    assert data["requirement"]["repoSearchQueries"] == ["repository search", "仓库 搜索"]
    assert data["topProjects"][0]["evidenceCoverage"][0]["status"] == "supported"
    assert data["topProjects"][0]["publicSummary"] == "A concise repository summary."
    assert data["topProjects"][0]["capabilityEvidence"] == []
    assert data["raw"]["candidate_count"] == 1
    assert data["runTrace"]["schema_version"] == "1"


def test_diagnostic_serializer_rejects_a_missing_engine_trace() -> None:
    report = fake_report()
    report.run_trace = None

    with pytest.raises(ValueError, match="engine-produced run trace"):
        diagnostic_report_to_dict(report)


def test_user_json_can_include_rendered_html_without_expanding_the_schema() -> None:
    data = report_to_dict(fake_report(), include_html=True)

    assert set(data) == {"summary", "reportMarkdown", "reportHtml", "topProjects", "usage"}
    assert '<a href="https://github.com/demo/project"' in data["reportHtml"]


def test_report_is_concise_and_keeps_stars_update_date_and_tokens() -> None:
    markdown = fake_report().report_markdown

    assert "[demo/project](https://github.com/demo/project)" in markdown
    assert "相关度：82%" in markdown
    assert "★ 12" in markdown
    assert "2026-01-15" in markdown
    assert "LLM Token" in markdown
    assert "简介：" in markdown
    assert "已确认能力：搜索 GitHub 仓库；repository search" in markdown
    assert "未覆盖" not in markdown
    assert "得分原因" not in markdown
    assert "证据" not in markdown


def test_english_report_follows_the_input_language() -> None:
    markdown = fake_report(language="en").report_markdown

    assert "# Research conclusion" in markdown
    assert "## Candidate projects" in markdown
    assert "Relevance: 82%" in markdown
    assert "Overview: A concise repository summary." in markdown
    assert "Verified capabilities: Search GitHub repositories; repository search" in markdown
    assert "LLM tokens" in markdown
    assert "相关度" not in markdown


def test_low_confidence_summary_does_not_invent_a_best_adjacent_lead() -> None:
    report = fake_report(language="en")
    report.top_projects[0].core_confirmed = False
    report.top_projects[0].directly_usable = False
    report.top_projects[0].confidence_level = "lead"

    summary = DeepSearchEngine()._write_summary(report.requirement, report.top_projects)

    assert "low-confidence adjacent lead" in summary
    assert report.top_projects[0].repo.full_name not in summary


def test_cli_json_uses_the_concise_user_serializer(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from github_deep_search import __main__ as cli

    async def fake_deep_search(query: str) -> SearchReport:
        return fake_report()

    monkeypatch.setattr(cli, "deep_search", fake_deep_search)
    monkeypatch.setattr(sys, "argv", ["github_deep_search", "测试需求", "--format", "json"])
    cli.main()
    output = json.loads(capsys.readouterr().out)

    assert output["topProjects"][0]["relevance"] == 82
    assert "evidence" not in output["topProjects"][0]
    assert "requiredChanges" not in output["topProjects"][0]


def test_plain_repository_url_is_linked_in_rendered_markdown() -> None:
    html = simple_markdown_to_html("- URL: https://github.com/demo/project")

    assert '<a href="https://github.com/demo/project"' in html


def test_estimated_token_label_is_visible() -> None:
    report = fake_report()
    report.usage.llm_token_estimated = True

    markdown = DeepSearchEngine()._write_report(
        report.query,
        report.requirement,
        report.top_projects,
        "",
        report.usage,
    )

    assert "LLM Token（估算）" in markdown


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
