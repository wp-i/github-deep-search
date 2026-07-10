from __future__ import annotations

from dataclasses import asdict
import importlib.util
from pathlib import Path

import pytest

from github_deep_search.adversarial_review import AgentReview, ReviewFinding


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
        "reportMarkdown": "# Report",
    }

    result = review.decision_check(report)

    assert result["status"] == "pass"
    assert result["score"] == result["maxScore"] == 5


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
        adversarial,
        {"status": "passed", "results": [{}]},
        triage,
    )

    assert summary["status"] == "human_triage_required"
    assert summary["adversarialReview"]["severityCounts"] == {"P0": 0, "P1": 1, "P2": 0, "P3": 0}

    triage["findings"][0]["disposition"] = "accepted"
    triage["findings"][0]["reviewer"] = "independent-reviewer"
    triage["findings"][0]["rationale"] = "Confirmed by the cited artifact."
    accepted = review.aggregate_summary(
        {"status": "pass", "score": 5, "maxScore": 5},
        adversarial,
        {"status": "passed", "results": [{}]},
        triage,
    )
    assert accepted["status"] == "action_required"


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
