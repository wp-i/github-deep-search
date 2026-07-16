from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import secrets
import sys
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from github_deep_search.config import Settings, get_settings
from github_deep_search.engine import deep_search
from github_deep_search.models import Requirement
from github_deep_search.run_trace import SearchRunFailed
from github_deep_search.serializers import diagnostic_report_to_dict, failure_artifact_to_dict


SCENARIO_FIELDS = {
    "case_id",
    "raw_request",
    "language",
    "scenario_family",
    "core_outcome_direction",
    "optional_constraints",
    "risk_level",
    "evaluation_date",
    "reviewer",
    "source",
    "redaction",
}
REQUIRED_TEXT_FIELDS = SCENARIO_FIELDS - {"optional_constraints"}
RISK_LEVELS = {"low", "medium", "high"}
SAFE_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9-]{1,79}$")
SECRET_PATTERNS = (
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._-]{20,}\b", re.IGNORECASE),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one explicit V1 scenario card against configured providers")
    parser.add_argument("scenario", type=Path, help="Path to a versioned JSON scenario card")
    parser.add_argument("--release-id", required=True, help="URL-safe release evaluation identifier")
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("docs/evaluations"),
        help="Root directory for generated evaluation artifacts",
    )
    parser.add_argument("--run-id", help="Optional URL-safe run identifier; defaults to UTC timestamp plus nonce")
    parser.add_argument(
        "--fixed-plan-from",
        type=Path,
        help="Qualified first-run directory whose audited search plan must be reused",
    )
    return parser.parse_args()


def load_scenario(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read scenario card: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("Scenario card must be a JSON object")
    keys = set(data)
    if keys != SCENARIO_FIELDS:
        missing = sorted(SCENARIO_FIELDS - keys)
        extra = sorted(keys - SCENARIO_FIELDS)
        raise ValueError(f"Scenario card fields must match the schema; missing={missing}, extra={extra}")
    for field in REQUIRED_TEXT_FIELDS:
        if not isinstance(data[field], str) or not data[field].strip():
            raise ValueError(f"Scenario card field {field} must be non-empty text")
    if not isinstance(data["optional_constraints"], list) or not all(
        isinstance(item, str) and item.strip() for item in data["optional_constraints"]
    ):
        raise ValueError("Scenario card optional_constraints must be a list of non-empty text")
    if data["risk_level"] not in RISK_LEVELS:
        raise ValueError(f"Scenario card risk_level must be one of {sorted(RISK_LEVELS)}")
    if not SAFE_IDENTIFIER.fullmatch(data["case_id"]):
        raise ValueError("Scenario card case_id must be a URL-safe lowercase identifier")
    return data


def configuration_record(settings: Settings) -> dict[str, Any]:
    public = {
        "github_configured": bool(settings.github_token),
        "llm_configured": bool(settings.llm_api_key),
        "tavily_configured": bool(settings.tavily_api_key),
        "llm_base_url": settings.llm_base_url,
        "llm_model": settings.llm_model,
        "llm_thinking": settings.llm_thinking,
        "llm_reasoning_effort": settings.llm_reasoning_effort,
        "max_github_requests": settings.max_github_requests,
        "max_tavily_credits": settings.max_tavily_credits,
        "max_candidates": settings.max_candidates,
        "max_deep_analyze_repos": settings.max_deep_analyze_repos,
    }
    encoded = json.dumps(public, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return {"fingerprint": hashlib.sha256(encoded).hexdigest(), "public": public}


def require_authenticated_github(settings: Settings) -> None:
    if not settings.github_token:
        raise ValueError(
            "GITHUB_TOKEN is required for live scenarios; anonymous GitHub API fallback is disabled"
        )


def load_fixed_requirement(
    run_dir: Path,
    scenario: dict[str, Any],
    configuration: dict[str, Any],
) -> Requirement:
    try:
        report = json.loads((run_dir / "report.json").read_text(encoding="utf-8-sig"))
        request = json.loads((run_dir / "request.json").read_text(encoding="utf-8-sig"))
        review = json.loads((run_dir / "review-summary.json").read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read fixed-plan artifacts: {exc}") from exc
    if not all(isinstance(item, dict) for item in (report, request, review)):
        raise ValueError("Fixed-plan artifacts must be JSON objects")
    requirement_data = report.get("requirement")
    trace = report.get("runTrace")
    raw = report.get("raw")
    if not isinstance(requirement_data, dict):
        raise ValueError("Fixed-plan report does not contain a diagnostic requirement")
    if not isinstance(trace, dict) or trace.get("status") != "completed":
        raise ValueError("Fixed-plan run must have a completed trace")
    if not isinstance(raw, dict) or raw.get("search_completeness") != "complete":
        raise ValueError("Fixed-plan run must have complete search coverage")
    if review.get("status") != "pass":
        raise ValueError("Fixed-plan source must be a qualified first run")
    if str(requirement_data.get("raw") or "").strip() != str(scenario["raw_request"]).strip():
        raise ValueError("Fixed-plan request does not match the current scenario")
    source_configuration = request.get("configuration")
    if (
        not isinstance(source_configuration, dict)
        or source_configuration.get("fingerprint") != configuration.get("fingerprint")
    ):
        raise ValueError("Fixed-plan configuration fingerprint does not match the current run")
    return requirement_from_diagnostic(requirement_data)


def requirement_from_diagnostic(data: dict[str, Any]) -> Requirement:
    requirement = Requirement(
        raw=_required_text(data, "raw"),
        intent=_required_text(data, "intent"),
        must_have_features=_text_list(data.get("mustHaveFeatures")),
        nice_to_have_features=_text_list(data.get("niceToHaveFeatures")),
        target_platforms=_text_list(data.get("targetPlatforms")),
        search_queries=_text_list(data.get("searchQueries")),
        report_language="en" if data.get("reportLanguage") == "en" else "zh",
        repo_search_queries=_text_list(data.get("repoSearchQueries")),
        code_search_queries=_text_list(data.get("codeSearchQueries")),
        topic_search_queries=_text_list(data.get("topicSearchQueries")),
        issue_search_queries=_text_list(data.get("issueSearchQueries")),
        web_search_queries=_text_list(data.get("webSearchQueries")),
        feature_concepts=_text_map(data.get("featureConcepts")),
        evidence_aliases=_text_map(data.get("evidenceAliases")),
        evidence_components=_nested_text_map(data.get("evidenceComponents")),
    )
    if not requirement.must_have_features or not requirement.repo_search_queries:
        raise ValueError("Fixed-plan requirement is missing required features or repository queries")
    return requirement


def _required_text(data: dict[str, Any], key: str) -> str:
    value = str(data.get(key) or "").strip()
    if not value:
        raise ValueError(f"Fixed-plan requirement field {key} is empty")
    return value


def _text_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _text_map(value: object) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        return {}
    return {
        str(key): _text_list(items)
        for key, items in value.items()
        if str(key).strip() and _text_list(items)
    }


def _nested_text_map(value: object) -> dict[str, dict[str, list[str]]]:
    if not isinstance(value, dict):
        return {}
    return {
        str(key): _text_map(items)
        for key, items in value.items()
        if str(key).strip() and _text_map(items)
    }


def default_run_id() -> str:
    now = datetime.now(timezone.utc)
    stamp = f"{now:%Y%m%d}t{now:%H%M%S}z"
    return f"{stamp}-{secrets.token_hex(4)}"


def validate_identifier(value: str, field: str) -> str:
    if not SAFE_IDENTIFIER.fullmatch(value):
        raise ValueError(f"{field} must be a URL-safe lowercase identifier")
    return value


def review_markdown(case_id: str, run_id: str, payload: dict[str, Any]) -> str:
    trace = payload.get("runTrace") if isinstance(payload.get("runTrace"), dict) else {}
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    projects = payload.get("topProjects") if isinstance(payload.get("topProjects"), list) else []
    report = payload.get("reportMarkdown") or payload.get("errorReportMarkdown")
    review_date = datetime.now(timezone.utc).date().isoformat()
    return "\n".join(
        [
            "# Independent Review",
            "",
            "## Scope",
            "",
            f"- Case ID: {case_id}",
            f"- Run ID: {run_id}",
            f"- Review date: {review_date}",
            "- Reviewer: pending-independent-review",
            "",
            "## Machine-readable facts",
            "",
            f"- Trace status: {trace.get('status', 'unknown')}",
            f"- Returned projects: {len(projects)}",
            f"- Provider events: {len(usage.get('providerEvents', []))}",
            f"- Readable report: {bool(isinstance(report, str) and report.strip())}",
            "",
            "## Blind review scores",
            "",
            "| Dimension | Score (0-2) | Evidence / explanation |",
            "| --- | --- | --- |",
            "| Requirement understanding |  |  |",
            "| Candidate relevance |  |  |",
            "| Project summary and verified capabilities |  |  |",
            "| Evidence credibility |  |  |",
            "| Tier consistency |  |  |",
            "| Independent-run consistency | N/A for first run |  |",
            "| Failure actionability |  |  |",
            "",
            "## Qualification verdict",
            "",
            "- Verdict: pending (pass / fail)",
            "- A blank score, pending verdict, or unresolved finding means this report is not qualified.",
            "",
            "## Findings",
            "",
            "Record only findings that cite a report field, trace field, repository evidence, or link-review artifact. Include the earliest stage and whether the finding is reproducible.",
            "",
        ]
    )


def assert_secret_free(value: object, settings: Settings) -> None:
    rendered = json.dumps(value, ensure_ascii=False, sort_keys=True) if not isinstance(value, str) else value
    secrets_to_check = [settings.github_token, settings.tavily_api_key, settings.llm_api_key]
    if any(secret and secret in rendered for secret in secrets_to_check):
        raise ValueError("Evaluation artifact would contain a configured credential")
    if any(pattern.search(rendered) for pattern in SECRET_PATTERNS):
        raise ValueError("Evaluation artifact would contain a secret-shaped value")


def write_artifacts(
    output_dir: Path,
    scenario: dict[str, Any],
    configuration: dict[str, Any],
    payload: dict[str, Any],
    settings: Settings,
) -> None:
    trace = payload.get("runTrace") if isinstance(payload.get("runTrace"), dict) else {}
    report_markdown = payload.get("reportMarkdown") or payload.get("errorReportMarkdown") or "# Empty report\n"
    request = {
        "case_id": scenario["case_id"],
        "raw_request": scenario["raw_request"],
        "language": scenario["language"],
        "scenario_family": scenario["scenario_family"],
        "configuration": configuration,
    }
    for value in (request, trace, payload, report_markdown):
        assert_secret_free(value, settings)
    output_dir.mkdir(parents=True, exist_ok=False)
    (output_dir / "request.json").write_text(json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "trace.json").write_text(json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "report.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "report.md").write_text(report_markdown, encoding="utf-8")
    (output_dir / "review.md").write_text(
        review_markdown(scenario["case_id"], output_dir.name, payload), encoding="utf-8"
    )


async def main_async() -> None:
    args = parse_args()
    scenario = load_scenario(args.scenario)
    release_id = validate_identifier(args.release_id, "release_id")
    run_id = validate_identifier(args.run_id or default_run_id(), "run_id")
    settings = get_settings()
    require_authenticated_github(settings)
    configuration = configuration_record(settings)
    fixed_requirement = (
        load_fixed_requirement(args.fixed_plan_from, scenario, configuration)
        if args.fixed_plan_from
        else None
    )
    try:
        report = await deep_search(
            scenario["raw_request"],
            fixed_requirement=fixed_requirement,
        )
        payload = diagnostic_report_to_dict(report)
    except SearchRunFailed as exc:
        payload = failure_artifact_to_dict(exc.artifact)
    output_dir = args.output_root / release_id / scenario["case_id"] / run_id
    write_artifacts(output_dir, scenario, configuration, payload, settings)
    print(output_dir)


def main() -> None:
    try:
        asyncio.run(main_async())
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
