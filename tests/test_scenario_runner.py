from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from github_deep_search.config import Settings


def _runner_module():
    path = Path("scripts/run_live_scenario.py")
    spec = importlib.util.spec_from_file_location("run_live_scenario", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _scenario() -> dict[str, object]:
    return {
        "case_id": "neutral-case",
        "raw_request": "A current user request",
        "language": "en",
        "scenario_family": "single-capability",
        "core_outcome_direction": "A human review anchor",
        "optional_constraints": ["An optional constraint"],
        "risk_level": "medium",
        "evaluation_date": "2026-07-10",
        "reviewer": "pending-independent-review",
        "source": "Manually authored",
        "redaction": "No sensitive data included.",
    }


def _settings() -> Settings:
    return Settings(
        github_token="configured-github-secret",
        tavily_api_key="configured-tavily-secret",
        llm_api_key="configured-llm-secret",
        llm_base_url="https://provider.example/v1",
        llm_model="model",
        llm_thinking=None,
        llm_reasoning_effort=None,
        max_github_requests=10,
        max_tavily_credits=2,
        max_candidates=8,
        max_deep_analyze_repos=2,
        task_deadline_seconds=30,
        llm_input_usd_per_1m=0.0,
        llm_output_usd_per_1m=0.0,
        tavily_usd_per_credit=0.0,
    )


def test_scenario_card_requires_exact_structural_schema(tmp_path: Path) -> None:
    runner = _runner_module()
    path = tmp_path / "scenario.json"
    path.write_text(json.dumps(_scenario()), encoding="utf-8")

    loaded = runner.load_scenario(path)

    assert loaded["case_id"] == "neutral-case"


def test_scenario_card_rejects_unrecognized_fields(tmp_path: Path) -> None:
    runner = _runner_module()
    card = _scenario()
    card["unexpected"] = "not schema data"
    path = tmp_path / "scenario.json"
    path.write_text(json.dumps(card), encoding="utf-8")

    with pytest.raises(ValueError, match="fields must match"):
        runner.load_scenario(path)


def test_default_run_id_is_portable_and_schema_safe() -> None:
    runner = _runner_module()

    assert runner.SAFE_IDENTIFIER.fullmatch(runner.default_run_id())


def test_live_scenario_rejects_anonymous_github_configuration() -> None:
    runner = _runner_module()
    settings = _settings()
    anonymous = Settings(**{**settings.__dict__, "github_token": None})

    with pytest.raises(ValueError, match="anonymous GitHub API fallback is disabled"):
        runner.require_authenticated_github(anonymous)


def test_scenario_artifacts_have_required_files_and_no_credentials(tmp_path: Path) -> None:
    runner = _runner_module()
    output = tmp_path / "release" / "neutral-case" / "run-1"
    payload = {
        "reportMarkdown": "# Report\n",
        "topProjects": [],
        "usage": {"providerEvents": []},
        "runTrace": {"status": "completed", "stages": []},
    }

    runner.write_artifacts(
        output,
        _scenario(),
        runner.configuration_record(_settings()),
        payload,
        _settings(),
    )

    assert {path.name for path in output.iterdir()} == {
        "request.json",
        "trace.json",
        "report.json",
        "report.md",
        "review.md",
    }
    assert "Trace status: completed" in (output / "review.md").read_text(encoding="utf-8")
    assert "Verdict: pending (pass / fail)" in (output / "review.md").read_text(encoding="utf-8")
    for path in output.iterdir():
        assert "configured-llm-secret" not in path.read_text(encoding="utf-8")


def test_fixed_plan_loader_requires_qualified_matching_artifacts(tmp_path: Path) -> None:
    runner = _runner_module()
    run_dir = tmp_path / "qualified-run"
    run_dir.mkdir()
    configuration = runner.configuration_record(_settings())
    requirement = {
        "raw": _scenario()["raw_request"],
        "reportLanguage": "en",
        "intent": "Find a current project",
        "mustHaveFeatures": ["current capability"],
        "niceToHaveFeatures": [],
        "targetPlatforms": ["browser"],
        "searchQueries": ["current project"],
        "repoSearchQueries": ["current project"],
        "codeSearchQueries": ["current capability"],
        "topicSearchQueries": ["current-project"],
        "issueSearchQueries": ["current capability issue"],
        "webSearchQueries": ["current project GitHub"],
        "featureConcepts": {"actions": ["find"], "objects": ["project"]},
        "evidenceAliases": {"current capability": ["current capability"]},
        "evidenceComponents": {
            "current capability": {"A current user request": ["current capability"]}
        },
    }
    (run_dir / "report.json").write_text(
        json.dumps(
            {
                "requirement": requirement,
                "runTrace": {"status": "completed"},
                "raw": {"search_completeness": "complete"},
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "request.json").write_text(
        json.dumps({"configuration": configuration}), encoding="utf-8"
    )
    (run_dir / "review-summary.json").write_text(
        json.dumps({"status": "pass"}), encoding="utf-8"
    )

    fixed = runner.load_fixed_requirement(run_dir, _scenario(), configuration)

    assert fixed.raw == _scenario()["raw_request"]
    assert fixed.repo_search_queries == ["current project"]


def test_fixed_plan_loader_rejects_an_unqualified_source(tmp_path: Path) -> None:
    runner = _runner_module()
    run_dir = tmp_path / "failed-run"
    run_dir.mkdir()
    for name, value in {
        "report.json": {"requirement": {}, "runTrace": {"status": "completed"}, "raw": {"search_completeness": "complete"}},
        "request.json": {"configuration": runner.configuration_record(_settings())},
        "review-summary.json": {"status": "action_required"},
    }.items():
        (run_dir / name).write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(ValueError, match="qualified first run"):
        runner.load_fixed_requirement(
            run_dir,
            _scenario(),
            runner.configuration_record(_settings()),
        )


def test_secret_scan_rejects_long_credential_shape_but_not_path_text() -> None:
    runner = _runner_module()

    runner.assert_secret_free("asterisk-next-to-every-price.md", _settings())
    with pytest.raises(ValueError, match="secret-shaped"):
        runner.assert_secret_free("sk-" + "a" * 24, _settings())


def test_runtime_package_does_not_reference_evaluation_assets() -> None:
    runtime_sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in Path("github_deep_search").rglob("*.py")
    )

    assert "docs/evaluations" not in runtime_sources
