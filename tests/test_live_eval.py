from __future__ import annotations

import os
import asyncio

import pytest

from github_deep_search.engine import deep_search


@pytest.mark.live
@pytest.mark.skipif(os.getenv("RUN_LIVE_EVAL") != "1", reason="set RUN_LIVE_EVAL=1 to run live evals")
def test_live_light_eval_returns_structured_projects() -> None:
    report = asyncio.run(
        deep_search(
            "Find an open source browser extension that summarizes web pages and syncs notes to Notion.",
            "light",
        )
    )

    assert report.raw["candidate_count"] >= 0
    assert report.raw["top_projects_returned"] == len(report.top_projects)
    assert "evidence_gate" in report.raw
    if report.top_projects:
        for project in report.top_projects:
            assert project.repo.full_name
            assert project.evidence_coverage
            assert all(item.feature for item in project.evidence_coverage)
    else:
        assert report.raw["top_projects_returned"] == 0
        assert report.usage.warnings
        explanation = f"{report.summary}\n{report.report_markdown}\n{' '.join(report.usage.warnings)}"
        assert "未找到可靠" in explanation or "Returned 0 project" in explanation


# ---------------------------------------------------------------------------
# Real end-to-end pipeline trace tests
# ---------------------------------------------------------------------------


def _verify_pipeline_stages(report, scenario_name: str) -> dict:
    """Verify all 6 pipeline stages and return a report dict."""
    issues = []

    # Stage 1+2: Parser + Search Queries
    if not report.requirement.intent:
        issues.append(f"[{scenario_name}] Stage 1-2: intent is empty")
    if not report.requirement.must_have_features:
        issues.append(f"[{scenario_name}] Stage 1-2: must_have_features is empty")
    if not report.requirement.search_queries:
        issues.append(f"[{scenario_name}] Stage 1-2: search_queries is empty")

    query_counts = report.raw.get("planned_query_counts", {})
    total_queries = sum(query_counts.values())
    if total_queries < 4:
        issues.append(f"[{scenario_name}] Stage 2: only {total_queries} queries, expected >= 4")

    # Stage 3: Candidate collection
    candidate_count = report.raw.get("candidate_count", 0)
    if candidate_count < 3:
        issues.append(f"[{scenario_name}] Stage 3: only {candidate_count} candidates, expected >= 3")

    source_mix = report.raw.get("source_mix", {})
    if len(source_mix) < 2:
        issues.append(f"[{scenario_name}] Stage 3: candidates from only {len(source_mix)} source types")

    # Stage 4+5: README + Evidence collection
    deep_pool_count = report.raw.get("deep_pool_count", 0)
    if deep_pool_count < 3:
        issues.append(f"[{scenario_name}] Stage 4-5: only {deep_pool_count} in deep pool, expected >= 3")

    # Stage 6: LLM analysis
    top_projects_returned = report.raw.get("top_projects_returned", 0)
    if top_projects_returned == 0:
        issues.append(f"[{scenario_name}] Stage 6: no projects returned")

    reliable_count = report.raw.get("reliable_top_projects_count", 0)
    reference_count = report.raw.get("reference_candidate_count", 0)
    if reliable_count == 0 and reference_count == 0:
        issues.append(f"[{scenario_name}] Stage 6: no reliable or reference candidates")

    # Check for score diversity
    if report.top_projects and len(report.top_projects) > 1:
        scores = [p.match_score for p in report.top_projects]
        if len(set(scores)) == 1:
            issues.append(f"[{scenario_name}] Stage 6: all {len(scores)} projects have identical score {scores[0]}")

    # Evidence coverage check
    if report.top_projects:
        has_source_evidence = any(
            any(cov.source_evidence for cov in p.evidence_coverage)
            for p in report.top_projects
        )
        has_path_evidence = any(
            any(cov.path_evidence for cov in p.evidence_coverage)
            for p in report.top_projects
        )
        if not has_source_evidence and not has_path_evidence:
            issues.append(f"[{scenario_name}] Stage 5: no source or path evidence in any project")

    return {
        "candidate_count": candidate_count,
        "deep_pool_count": deep_pool_count,
        "top_projects_returned": top_projects_returned,
        "reliable_count": reliable_count,
        "reference_count": reference_count,
        "issues": issues,
    }

