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
