from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest

from github_deep_search.adversarial_review import run_adversarial_reviews
from github_deep_search.decision_brief import build_decision_brief, format_decision_brief
from github_deep_search.engine import DeepSearchEngine
from github_deep_search.models import (
    BudgetUsage,
    CandidateRepository,
    EvidenceCoverage,
    ProjectAnalysis,
    ProviderEvent,
    Requirement,
    RunFailure,
    SearchReport,
)
from github_deep_search.run_trace import (
    RunTraceRecorder,
    SearchRunFailed,
    build_failure_artifact,
    build_run_trace,
)
from github_deep_search.serializers import failure_artifact_to_dict, report_to_dict
from github_deep_search.spec_parser import SearchSpecParser


def _report() -> SearchReport:
    requirement = Requirement(
        raw="Current user request",
        intent="Current outcome",
        must_have_features=["core capability"],
        nice_to_have_features=["extension"],
        target_platforms=[],
        search_queries=["current query"],
        repo_search_queries=["current query"],
        evidence_aliases={"core capability": ["current evidence"]},
    )
    analysis = ProjectAnalysis(
        repo=CandidateRepository("demo", "project", "https://github.com/demo/project"),
        match_score=72,
        recommendation="Evidence-backed candidate",
        directly_usable=False,
        covered_features=["core capability"],
        missing_features=["extension"],
        required_changes=[],
        risks=[],
        evidence=["README (current evidence)"],
        core_confirmed=True,
        evidence_coverage=[
            EvidenceCoverage(feature="core capability", covered=True, status="supported"),
            EvidenceCoverage(feature="extension", covered=False, status="unknown"),
        ],
    )
    return SearchReport(
        query=requirement.raw,
        requirement=requirement,
        top_projects=[analysis],
        opportunity="",
        summary="Evidence-backed candidate found.",
        report_markdown="# Report",
        usage=BudgetUsage(),
        raw={
            "candidate_count": 2,
            "deep_pool_count": 1,
            "reliable_top_projects_count": 1,
            "reference_candidate_count": 0,
            "low_similarity_lead_count": 0,
            "planned_query_counts": {"repo": 1, "code": 1},
            "request_stages": {"discovery": 2, "readme": 1, "source": 1},
            "source_mix": {"github_repo": 2},
            "evidence_gate": {"coverage_items": 2},
            "search_completeness": "complete",
            "search_completeness_reasons": [],
        },
    )


def test_decision_brief_uses_evidence_states_without_business_rules() -> None:
    brief = build_decision_brief(_report().requirement, _report().top_projects)

    assert brief.level == "verified"
    assert brief.confirmed_features == ["core capability"]
    assert brief.unconfirmed_features == ["extension"]
    assert "demo/project" in brief.next_step
    assert any(line.startswith("- 下一步：") for line in format_decision_brief(brief))


def test_serialized_report_exposes_decision_brief_and_stage_trace() -> None:
    data = report_to_dict(_report())

    assert data["decisionBrief"]["level"] == "verified"
    assert data["decisionBrief"]["confirmedFeatures"] == ["core capability"]
    assert data["runTrace"]["schema_version"] == "1"
    assert [stage["name"] for stage in data["runTrace"]["stages"]] == [
        "parse",
        "discovery",
        "evidence",
        "analysis",
        "report_delivery",
    ]


def test_run_trace_marks_limited_discovery_without_deleting_the_result() -> None:
    report = _report()
    report.raw["search_completeness"] = "limited"
    report.raw["search_completeness_reasons"] = ["request limit reached"]

    trace = build_run_trace(report)

    discovery = next(stage for stage in trace.stages if stage.name == "discovery")
    assert trace.status == "partial"
    assert discovery.status == "partial"
    assert discovery.notes == ["request limit reached"]


def test_engine_failure_preserves_failed_and_not_started_stages(monkeypatch) -> None:
    async def fail_parse(self, query, llm):
        raise RuntimeError("internal details must not be exported")

    monkeypatch.setattr(SearchSpecParser, "parse", fail_parse)

    with pytest.raises(SearchRunFailed) as raised:
        asyncio.run(DeepSearchEngine().run("current request"))

    artifact = failure_artifact_to_dict(raised.value.artifact)
    assert artifact["status"] == "failed"
    assert artifact["failure"] == {
        "kind": "execution",
        "stage": "parse",
        "exceptionType": "RuntimeError",
        "message": "The search run failed during parse.",
        "retryable": False,
    }
    assert [(stage["name"], stage["status"]) for stage in artifact["runTrace"]["stages"]] == [
        ("parse", "failed"),
        ("discovery", "not_started"),
        ("evidence", "not_started"),
        ("analysis", "not_started"),
        ("report_delivery", "not_started"),
    ]
    assert "internal details" not in artifact["errorReportMarkdown"]


def test_structured_provider_event_marks_its_actual_stage_partial() -> None:
    trace = RunTraceRecorder()
    trace.begin("parse", {"query": 1})
    event = ProviderEvent("llm", "json_chat", "failed", "invalid_response")

    DeepSearchEngine._finish_trace_stage(trace, {"planned_queries": 1}, [event])

    built = trace.build()
    assert built.status == "partial"
    assert built.stages[0].status == "partial"
    assert built.stages[0].notes == ["llm:failed:invalid_response"]


def test_failure_artifact_is_readable_and_serializable() -> None:
    trace = RunTraceRecorder()
    trace.begin("parse", {"query": 1})
    trace.complete({"planned_queries": 2})
    trace.begin("discovery", {"planned_queries": 2})
    failure = RunFailure(
        kind="provider",
        stage="discovery",
        exception_type="TimeoutError",
        message="A configured provider failed during discovery.",
        retryable=True,
    )
    trace.fail(failure)

    data = failure_artifact_to_dict(
        build_failure_artifact("current request", BudgetUsage(), trace.build(), failure)
    )

    assert data["runTrace"]["status"] == "failed"
    assert data["errorReportMarkdown"].startswith("# Search run failed")
    assert data["failure"]["retryable"] is True


def test_reference_tiering_replaces_unverified_model_recommendation() -> None:
    analysis = _report().top_projects[0]
    analysis.core_confirmed = False
    analysis.core_feature = "core capability"
    analysis.recommendation = "Unverified capability claim"
    analysis.evidence_coverage[0].covered = False
    analysis.evidence_coverage[0].status = "unknown"

    DeepSearchEngine()._mark_low_similarity_lead(analysis)

    assert analysis.is_reference_candidate is True
    assert analysis.recommendation == analysis.reference_reason
    assert analysis.recommendation != "Unverified capability claim"


def test_candidate_trace_distinguishes_discovery_score_from_public_score() -> None:
    repo = CandidateRepository(
        "demo",
        "project",
        "https://github.com/demo/project",
        raw_score=120.3,
        core_signal_score=2.5,
        found_by=["github:current-query"],
    )

    item = DeepSearchEngine._candidate_trace_item(repo, include_found_by=True)

    assert item == {
        "repo": "demo/project",
        "discovery_score": 120.3,
        "score_basis": "pre_analysis_retrieval",
        "core_signal": 2.5,
        "found_by": ["github:current-query"],
    }
    assert "score" not in item


class _FakeReviewer:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def json_chat(self, system: str, user: str) -> dict:
        self.calls.append((system, user))
        return {
            "verdict": "concern",
            "summary": "A trace field needs review.",
            "findings": [
                {
                    "severity": "P2",
                    "stage": "analysis",
                    "claim": "Check the evidence boundary.",
                    "evidence_refs": ["topProjects[0].evidenceCoverage[0]"],
                    "counterexample": "The supplied evidence is incomplete.",
                    "verifiable": True,
                }
            ],
        }


class _MalformedFindingsReviewer:
    async def json_chat(self, system: str, user: str) -> dict:
        return {
            "verdict": "inconclusive",
            "summary": "No structured findings were returned.",
            "findings": {"claim": "wrong shape"},
        }


def test_adversarial_review_requires_evidence_references_for_verifiable_findings() -> None:
    reviewer = _FakeReviewer()
    reviews = asyncio.run(
        run_adversarial_reviews(
            reviewer,
            report_to_dict(_report()),
            ["user", "evidence"],
        )
    )

    assert [review.role for review in reviews] == ["user", "evidence"]
    assert all(review.findings[0].verifiable for review in reviews)
    assert len(reviewer.calls) == 2


def test_adversarial_review_treats_non_list_findings_as_empty() -> None:
    reviews = asyncio.run(
        run_adversarial_reviews(
            _MalformedFindingsReviewer(),
            report_to_dict(_report()),
            ["reliability"],
        )
    )

    assert reviews[0].verdict == "inconclusive"
    assert reviews[0].findings == []


def test_adversarial_review_script_help_runs_without_api_key() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/run_adversarial_review.py", "--help"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0
    assert "adversarial reviews" in result.stdout


def test_adversarial_review_accepts_utf8_bom_report_artifacts(tmp_path: Path) -> None:
    script_path = Path("scripts/run_adversarial_review.py")
    spec = importlib.util.spec_from_file_location("adversarial_review_script", script_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    report_path = tmp_path / "report.json"
    report_path.write_text('{"summary": "report"}', encoding="utf-8-sig")

    assert module._load_json(report_path) == {"summary": "report"}
