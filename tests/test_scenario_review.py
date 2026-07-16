from __future__ import annotations

from dataclasses import asdict
import importlib.util
from pathlib import Path

import pytest

from github_deep_search.adversarial_review import AgentReview, ReviewFinding
from github_deep_search.adversarial_review import _system_prompt


def _review_module():
    path = Path("scripts/review_scenario_run.py")
    spec = importlib.util.spec_from_file_location("review_scenario_run", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _project(repo: str, confidence: str, reference: bool) -> dict:
    return {
        "repo": repo,
        "url": f"https://github.com/{repo}",
        "confidenceLevel": confidence,
        "isReferenceCandidate": reference,
        "coreConfirmed": confidence == "reliable",
        "publicSummary": f"Summary for {repo}",
        "verifiedCapabilities": ["Verified operation"],
        "capabilityEvidence": [
            {
                "kind": "readme",
                "locator": "README",
                "excerpt": "Verified operation is supported.",
            }
        ],
        "capabilityCitationsReviewed": True,
        "score": 70,
        "stars": 1,
        "lastPushedAt": "2026-01-01T00:00:00Z",
    }


def test_decision_check_validates_structure_without_product_rules() -> None:
    review = _review_module()
    report = {
        "decisionBrief": {
            "level": "adjacent",
            "headline": "A decision",
            "confirmedFeatures": [],
            "gaps": [],
            "unconfirmedFeatures": ["current requirement"],
            "nextStep": "Inspect the evidence.",
        },
        "topProjects": [_project("demo/lead", "lead", True)],
        "reportMarkdown": "# Report\n\nhttps://github.com/demo/lead",
    }

    result = review.decision_check(report)

    assert result["status"] == "pass"
    assert result["score"] == result["maxScore"] == 8


def test_decision_check_rejects_missing_project_content_and_uniform_scores() -> None:
    review = _review_module()
    first = _project("demo/first", "lead", True)
    second = {**_project("demo/second", "lead", True), "publicSummary": ""}
    report = {
        "decisionBrief": {
            "level": "adjacent",
            "headline": "A decision",
            "unconfirmedFeatures": ["current requirement"],
            "nextStep": "Inspect the evidence.",
        },
        "topProjects": [first, second],
        "reportMarkdown": "\n".join(["# Report", first["url"], second["url"]]),
    }

    result = review.decision_check(report)

    assert result["status"] == "needs_review"
    assert result["checks"]["project_content_complete"] is False
    assert result["checks"]["score_diversity_or_single_result"] is False


def test_decision_check_rejects_an_untraceable_reviewed_capability() -> None:
    review = _review_module()
    project = _project("demo/project", "lead", True)
    project["capabilityEvidence"] = []
    report = {
        "decisionBrief": {
            "level": "adjacent",
            "headline": "A decision",
            "unconfirmedFeatures": ["current requirement"],
            "nextStep": "Inspect the evidence.",
        },
        "topProjects": [project],
        "reportMarkdown": f"# Report\n\n{project['url']}",
    }

    result = review.decision_check(report)

    assert result["status"] == "needs_review"
    assert result["checks"]["project_content_complete"] is False


def test_consistency_check_rejects_disjoint_count_and_score_drift() -> None:
    review = _review_module()
    previous_project = {**_project("demo/previous", "lead", True), "score": 26}
    current_projects = [
        {**_project(f"demo/current-{index}", "lead", True), "score": score}
        for index, score in enumerate((31, 29, 29), start=1)
    ]
    common = {
        "requirement": {"raw": "Find a filtering tool"},
        "raw": {"search_completeness": "complete"},
        "runTrace": {"status": "completed"},
    }

    result = review.consistency_check(
        {**common, "topProjects": current_projects},
        {**common, "topProjects": [previous_project]},
    )

    assert result["status"] == "needs_review"
    assert result["checks"]["nonempty_results_overlap"] is False
    assert result["checks"]["result_count_stable"] is False
    assert result["checks"]["score_ranges_not_disjoint_when_results_are_disjoint"] is False


def test_consistency_check_rejects_a_regenerated_search_plan() -> None:
    review = _review_module()
    common = {
        "raw": {"search_completeness": "complete"},
        "runTrace": {"status": "completed"},
        "topProjects": [_project("demo/shared", "lead", True)],
    }
    previous = {
        **common,
        "requirement": {"raw": "Find a filtering tool", "repoSearchQueries": ["stable plan"]},
    }
    current = {
        **common,
        "requirement": {"raw": "Find a filtering tool", "repoSearchQueries": ["different plan"]},
    }

    result = review.consistency_check(current, previous)

    assert result["status"] == "needs_review"
    assert result["checks"]["same_request"] is True
    assert result["checks"]["same_search_plan"] is False


def test_url_contract_and_selection_cover_reliable_plus_top_adjacent() -> None:
    review = _review_module()
    reliable = _project("demo/reliable", "reliable", False)
    adjacent = _project("demo/adjacent", "lead", True)
    extra = _project("demo/extra", "reference", True)

    selected = review.selected_link_targets({"topProjects": [reliable, adjacent, extra]})

    assert [item["repo"] for item in selected] == ["demo/reliable", "demo/adjacent"]
    assert review.repository_url_contract(reliable)["valid"] is True
    assert review.repository_url_contract({**reliable, "url": "https://github.com/demo/other"})["valid"] is False


def test_navigation_classification_separates_identity_and_environment_failures() -> None:
    review = _review_module()
    contract = review.repository_url_contract(_project("demo/project", "lead", True))

    assert review.classify_navigation(
        contract,
        contract["url"],
        contract["url"],
        200,
        "Project",
    ) == ("passed", "identity_and_reachability")
    assert review.classify_navigation(
        contract,
        contract["url"],
        contract["url"],
        None,
        "",
        "TimeoutError",
    ) == ("environment_unverified", "network")


def test_adversarial_summary_counts_only_verifiable_referenced_findings() -> None:
    review = _review_module()
    reviews = [
        AgentReview(
            role="evidence",
            verdict="concern",
            summary="Review",
            findings=[
                ReviewFinding("P1", "analysis", "claim", ["topProjects[0]"], "counter", True),
                ReviewFinding("P0", "analysis", "unsupported", [], "counter", False),
            ],
        )
    ]
    adversarial = {
        "status": review.adversarial_status(reviews),
        "reviews": [asdict(reviews[0])],
    }
    triage = review.triage_template(adversarial)

    summary = review.aggregate_summary(
        {"status": "pass", "score": 5, "maxScore": 5},
        {"status": "pass", "checks": {}},
        adversarial,
        {"status": "passed", "results": [{}]},
        triage,
        {"status": "pass", "reviewer": "reviewer", "verdict": "pass", "scoresComplete": True},
    )

    assert summary["status"] == "human_triage_required"
    assert summary["adversarialReview"]["severityCounts"] == {"P0": 0, "P1": 1, "P2": 0, "P3": 0}

    triage["findings"][0]["disposition"] = "accepted"
    triage["findings"][0]["reviewer"] = "independent-reviewer"
    triage["findings"][0]["rationale"] = "Confirmed by the cited artifact."
    accepted = review.aggregate_summary(
        {"status": "pass", "score": 5, "maxScore": 5},
        {"status": "pass", "checks": {}},
        adversarial,
        {"status": "passed", "results": [{}]},
        triage,
        {"status": "pass", "reviewer": "reviewer", "verdict": "pass", "scoresComplete": True},
    )
    assert accepted["status"] == "action_required"

    triage["findings"][0]["disposition"] = "rejected"
    triage["status"] = "completed"
    rejected = review.aggregate_summary(
        {"status": "pass", "score": 5, "maxScore": 5},
        {"status": "pass", "checks": {}},
        adversarial,
        {"status": "passed", "results": [{}]},
        triage,
        {"status": "pass", "reviewer": "reviewer", "verdict": "pass", "scoresComplete": True},
    )
    assert rejected["status"] == "pass"


def test_review_artifact_secret_scan_rejects_credential_shapes() -> None:
    review = _review_module()

    review.assert_review_artifact_secret_free({"title": "asterisk-next-to-price"})
    with pytest.raises(ValueError, match="secret-shaped"):
        review.assert_review_artifact_secret_free({"finding": "sk-" + "a" * 24})


def test_review_artifacts_are_immutable(tmp_path: Path) -> None:
    review = _review_module()
    (tmp_path / "finding-triage.json").write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="immutable"):
        review.ensure_review_outputs_absent(tmp_path)


def test_reviewer_prompt_preserves_unknown_and_adjacent_evidence_semantics() -> None:
    prompt = _system_prompt("evidence")

    assert "adjacent lead may be retained" in prompt
    assert "Unknown means" in prompt
    assert "narrower than the user's core requirement" in prompt
    assert "README or description is valid evidence" in prompt
    assert "retain the limitation" in prompt


def test_blind_review_must_have_reviewer_verdict_and_complete_scores(tmp_path: Path) -> None:
    review = _review_module()
    pending = """# Independent Review
- Reviewer: pending-independent-review
## Blind review scores
| Dimension | Score (0-2) | Evidence |
| --- | --- | --- |
| Requirement understanding |  |  |
## Qualification verdict
- Verdict: pending (pass / fail)
"""
    (tmp_path / "review.md").write_text(pending, encoding="utf-8")

    assert review.blind_review_check(tmp_path)["status"] == "needs_review"

    complete = pending.replace("pending-independent-review", "independent-reviewer")
    complete = complete.replace("| Requirement understanding |  |", "| Requirement understanding | 2 |")
    complete = complete.replace("Verdict: pending (pass / fail)", "Verdict: pass")
    (tmp_path / "review.md").write_text(complete, encoding="utf-8")

    assert review.blind_review_check(tmp_path)["status"] == "pass"
