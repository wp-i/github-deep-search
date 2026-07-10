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
    for path in output.iterdir():
        assert "configured-llm-secret" not in path.read_text(encoding="utf-8")


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
