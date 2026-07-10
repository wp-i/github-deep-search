from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from typing import Any
from urllib.parse import urlparse


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from github_deep_search.adversarial_review import (
    REVIEW_ROLES,
    AgentReview,
    reviews_to_dict,
    run_adversarial_reviews,
)
from github_deep_search.config import get_settings
from github_deep_search.models import BudgetUsage
from github_deep_search.providers.llm import LLMClient
from github_deep_search.utils import simple_markdown_to_html


DEFAULT_ROLES = ("user", "semantic", "evidence")
REVIEW_ARTIFACT_NAMES = (
    "decision-check.json",
    "adversarial-review.json",
    "link-review.json",
    "finding-triage.json",
    "review-summary.json",
)
SECRET_PATTERNS = (
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._-]{20,}\b", re.IGNORECASE),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Review one retained live-scenario artifact")
    parser.add_argument("run_dir", type=Path, help="Scenario run directory containing report.json")
    parser.add_argument(
        "--roles",
        default=",".join(DEFAULT_ROLES),
        help=f"Comma-separated adversarial roles: {', '.join(REVIEW_ROLES)}",
    )
    parser.add_argument("--browser-timeout-ms", type=int, default=30000)
    return parser.parse_args()


def load_report(run_dir: Path) -> dict[str, Any]:
    path = run_dir / "report.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read scenario report: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("Scenario report must be a JSON object")
    return data


def parse_roles(value: str) -> list[str]:
    roles = list(dict.fromkeys(item.strip() for item in value.split(",") if item.strip()))
    if not roles:
        raise ValueError("At least one adversarial role is required")
    unknown = [role for role in roles if role not in REVIEW_ROLES]
    if unknown:
        raise ValueError(f"Unknown adversarial roles: {', '.join(unknown)}")
    if "architecture" in roles:
        raise ValueError("Architecture review requires explicit source context and is not part of scenario review")
    return roles


def decision_check(report: dict[str, Any]) -> dict[str, Any]:
    brief = report.get("decisionBrief") if isinstance(report.get("decisionBrief"), dict) else {}
    projects = report.get("topProjects") if isinstance(report.get("topProjects"), list) else []
    best = projects[0] if projects and isinstance(projects[0], dict) else {}
    core_unconfirmed = bool(best) and best.get("coreConfirmed") is not True
    confirmed = brief.get("confirmedFeatures") if isinstance(brief.get("confirmedFeatures"), list) else []
    gaps = brief.get("gaps") if isinstance(brief.get("gaps"), list) else []
    unconfirmed = brief.get("unconfirmedFeatures") if isinstance(brief.get("unconfirmedFeatures"), list) else []
    checks = {
        "decision_present": bool(brief.get("level") and brief.get("headline")),
        "evidence_boundary_present": bool(confirmed or gaps or unconfirmed or not projects),
        "unconfirmed_core_is_visible": bool(not core_unconfirmed or gaps or unconfirmed),
        "next_action_present": bool(str(brief.get("nextStep") or "").strip()),
        "readable_report_present": bool(str(report.get("reportMarkdown") or "").strip()),
    }
    return {
        "schemaVersion": "1",
        "kind": "structural-30-second-decision-check",
        "status": "pass" if all(checks.values()) else "needs_review",
        "score": sum(checks.values()),
        "maxScore": len(checks),
        "checks": checks,
        "note": "This structural check does not replace independent human scoring.",
    }


def repository_url_contract(project: dict[str, Any]) -> dict[str, Any]:
    repo = str(project.get("repo") or "").strip()
    url = str(project.get("url") or "").strip()
    repo_parts = [part for part in repo.split("/") if part]
    parsed = urlparse(url)
    path_parts = [part for part in parsed.path.split("/") if part]
    valid = (
        len(repo_parts) == 2
        and parsed.scheme.lower() == "https"
        and parsed.netloc.lower() == "github.com"
        and len(path_parts) == 2
        and [part.casefold() for part in path_parts] == [part.casefold() for part in repo_parts]
        and not parsed.query
        and not parsed.fragment
    )
    return {
        "repo": repo,
        "url": url,
        "valid": valid,
        "reason": "identity_match" if valid else "url_identity_mismatch",
    }


def selected_link_targets(report: dict[str, Any]) -> list[dict[str, Any]]:
    projects = [item for item in report.get("topProjects", []) if isinstance(item, dict)]
    reliable = [
        item
        for item in projects
        if item.get("confidenceLevel") == "reliable" and item.get("isReferenceCandidate") is not True
    ]
    adjacent = next(
        (
            item
            for item in projects
            if item.get("isReferenceCandidate") is True
            or item.get("confidenceLevel") in {"reference", "lead"}
        ),
        None,
    )
    selected = [*reliable, *([adjacent] if adjacent else [])]
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in selected:
        repo = str(item.get("repo") or "").casefold()
        if repo and repo not in seen:
            seen.add(repo)
            unique.append(item)
    return unique


def rendered_href(report_markdown: str, url: str) -> str | None:
    html = simple_markdown_to_html(report_markdown)
    double = f'href="{url}"'
    single = f"href='{url}'"
    if double in html or single in html:
        return url
    return None


def classify_navigation(
    contract: dict[str, Any],
    rendered: str | None,
    final_url: str,
    http_status: int | None,
    title: str,
    error: str = "",
) -> tuple[str, str]:
    if not contract["valid"]:
        return "failed", "address_identity"
    if rendered is None:
        return "failed", "rendered_link_missing"
    if error:
        return "environment_unverified", "network"
    if http_status in {401, 403} or "sign in" in title.casefold():
        return "environment_unverified", "authentication"
    if http_status == 429:
        return "environment_unverified", "rate_limit"
    if http_status == 404:
        return "failed", "not_found"
    final_contract = repository_url_contract({"repo": contract["repo"], "url": final_url})
    if not final_contract["valid"]:
        return "failed", "redirect_identity"
    if http_status is None or http_status >= 400:
        return "environment_unverified", "http"
    return "passed", "identity_and_reachability"


async def browser_link_review(report: dict[str, Any], timeout_ms: int) -> dict[str, Any]:
    targets = selected_link_targets(report)
    results: list[dict[str, Any]] = []
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return {
            "schemaVersion": "1",
            "status": "environment_unverified",
            "reason": "playwright_not_installed",
            "results": [],
        }
    async with async_playwright() as browser_api:
        try:
            browser = await browser_api.chromium.launch(headless=True)
        except Exception as exc:
            return {
                "schemaVersion": "1",
                "status": "environment_unverified",
                "reason": f"browser_launch:{type(exc).__name__}",
                "results": [],
            }
        try:
            context = await browser.new_context()
            report_page = await context.new_page()
            await report_page.set_content(simple_markdown_to_html(str(report.get("reportMarkdown") or "")))
            for project in targets:
                contract = repository_url_contract(project)
                href = rendered_href(str(report.get("reportMarkdown") or ""), contract["url"])
                final_url = ""
                title = ""
                status_code: int | None = None
                error = ""
                if contract["valid"] and href:
                    page = await context.new_page()
                    try:
                        response = await page.goto(href, wait_until="domcontentloaded", timeout=timeout_ms)
                        final_url = page.url
                        title = await page.title()
                        status_code = response.status if response else None
                    except Exception as exc:
                        error = type(exc).__name__
                        final_url = page.url
                    finally:
                        await page.close()
                status, reason = classify_navigation(
                    contract,
                    href,
                    final_url,
                    status_code,
                    title,
                    error,
                )
                results.append(
                    {
                        "repo": contract["repo"],
                        "initialUrl": contract["url"],
                        "renderedHref": href,
                        "finalUrl": final_url,
                        "httpStatus": status_code,
                        "pageTitle": title,
                        "status": status,
                        "reason": reason,
                        "checkedAt": datetime.now(timezone.utc).isoformat(),
                    }
                )
        finally:
            await browser.close()
    statuses = {item["status"] for item in results}
    overall = "failed" if "failed" in statuses else "environment_unverified" if "environment_unverified" in statuses else "passed"
    return {"schemaVersion": "1", "status": overall, "results": results}


async def adversarial_review(report: dict[str, Any], roles: list[str]) -> dict[str, Any]:
    settings = get_settings()
    if not settings.llm_api_key:
        return {
            "schemaVersion": "1",
            "roles": roles,
            "status": "inconclusive",
            "reviews": [],
            "usage": {},
            "reason": "LLM_API_KEY is not configured",
        }
    usage = BudgetUsage()
    reviewer = LLMClient(
        settings.llm_api_key,
        settings.llm_base_url,
        settings.llm_model,
        usage,
        thinking=settings.llm_thinking,
        reasoning_effort=settings.llm_reasoning_effort,
    )
    try:
        reviews = await run_adversarial_reviews(reviewer, report, roles)
    finally:
        await reviewer.close()
    return {
        "schemaVersion": "1",
        "roles": roles,
        "status": adversarial_status(reviews),
        "reviews": reviews_to_dict(reviews),
        "usage": {
            "llmInputTokens": usage.llm_input_tokens,
            "llmOutputTokens": usage.llm_output_tokens,
            "llmTokenEstimated": usage.llm_token_estimated,
            "warnings": usage.warnings,
        },
    }


def adversarial_status(reviews: list[AgentReview]) -> str:
    findings = [finding for review in reviews for finding in review.findings if finding.verifiable]
    if any(finding.severity in {"P0", "P1"} for finding in findings):
        return "triage_required"
    if findings or any(review.verdict == "concern" for review in reviews):
        return "needs_review"
    if reviews and all(review.verdict == "pass" for review in reviews):
        return "pass"
    return "inconclusive"


def triage_template(adversarial: dict[str, Any]) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    for review in adversarial.get("reviews", []):
        if not isinstance(review, dict):
            continue
        role = str(review.get("role") or "unknown")
        for index, finding in enumerate(review.get("findings", []), start=1):
            if not isinstance(finding, dict) or not finding.get("verifiable") or not finding.get("evidence_refs"):
                continue
            findings.append(
                {
                    "id": f"{role}-{index}",
                    "role": role,
                    "severity": finding.get("severity"),
                    "stage": finding.get("stage"),
                    "claim": finding.get("claim"),
                    "evidenceRefs": finding.get("evidence_refs"),
                    "disposition": "pending",
                    "reviewer": "pending-independent-review",
                    "rationale": "",
                }
            )
    return {
        "schemaVersion": "1",
        "status": "pending" if findings else "not_required",
        "findings": findings,
        "note": "Agent severity is a triage suggestion. Only accepted findings enter the defect closure loop.",
    }


def aggregate_summary(
    decision: dict[str, Any],
    adversarial: dict[str, Any],
    links: dict[str, Any],
    triage: dict[str, Any],
) -> dict[str, Any]:
    findings = [
        finding
        for review in adversarial.get("reviews", [])
        if isinstance(review, dict)
        for finding in review.get("findings", [])
        if isinstance(finding, dict) and finding.get("verifiable") and finding.get("evidence_refs")
    ]
    severity_counts = {
        severity: sum(1 for finding in findings if finding.get("severity") == severity)
        for severity in ("P0", "P1", "P2", "P3")
    }
    accepted = [
        finding
        for finding in triage.get("findings", [])
        if isinstance(finding, dict) and finding.get("disposition") == "accepted"
    ]
    accepted_high = any(finding.get("severity") in {"P0", "P1"} for finding in accepted)
    pending = any(
        isinstance(finding, dict) and finding.get("disposition") == "pending"
        for finding in triage.get("findings", [])
    )
    return {
        "schemaVersion": "1",
        "decisionCheck": {"status": decision["status"], "score": decision["score"], "maxScore": decision["maxScore"]},
        "adversarialReview": {
            "status": adversarial.get("status"),
            "verifiableFindingCount": len(findings),
            "severityCounts": severity_counts,
        },
        "linkReview": {
            "status": links.get("status"),
            "reviewedCount": len(links.get("results", [])),
        },
        "findingTriage": {
            "status": triage.get("status"),
            "pendingCount": sum(
                1 for finding in triage.get("findings", []) if finding.get("disposition") == "pending"
            ),
            "acceptedCount": len(accepted),
        },
        "status": (
            "action_required"
            if accepted_high or links.get("status") == "failed"
            else "human_triage_required"
            if pending
            else "review_pending"
        ),
        "note": "Independent human scoring remains required before release acceptance.",
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def ensure_review_outputs_absent(run_dir: Path) -> None:
    existing = [name for name in REVIEW_ARTIFACT_NAMES if (run_dir / name).exists()]
    if existing:
        raise ValueError(
            "Review artifacts are immutable; use an unreviewed run directory. Existing: "
            + ", ".join(existing)
        )


def assert_review_artifact_secret_free(payload: object) -> None:
    settings = get_settings()
    rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    configured = [settings.github_token, settings.tavily_api_key, settings.llm_api_key]
    if any(secret and secret in rendered for secret in configured):
        raise ValueError("Review artifact would contain a configured credential")
    if any(pattern.search(rendered) for pattern in SECRET_PATTERNS):
        raise ValueError("Review artifact would contain a secret-shaped value")


async def main_async() -> None:
    args = parse_args()
    ensure_review_outputs_absent(args.run_dir)
    report = load_report(args.run_dir)
    roles = parse_roles(args.roles)
    decision = decision_check(report)
    adversarial = await adversarial_review(report, roles)
    links = await browser_link_review(report, args.browser_timeout_ms)
    triage_path = args.run_dir / "finding-triage.json"
    triage = triage_template(adversarial)
    summary = aggregate_summary(decision, adversarial, links, triage)
    for payload in (decision, adversarial, links, triage, summary):
        assert_review_artifact_secret_free(payload)
    write_json(args.run_dir / "decision-check.json", decision)
    write_json(args.run_dir / "adversarial-review.json", adversarial)
    write_json(args.run_dir / "link-review.json", links)
    write_json(triage_path, triage)
    write_json(args.run_dir / "review-summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def main() -> None:
    try:
        asyncio.run(main_async())
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
