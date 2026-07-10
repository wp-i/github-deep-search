from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol


REVIEW_ROLES = {
    "user": "Judge whether the report gives a user a decision, the strongest evidence, the core gap, and a next action.",
    "semantic": "Compare the original request, structured requirement, and executed plan. Look for omitted core intent or unsupported hardening of unknown details.",
    "evidence": "Challenge every supported capability. Require a local evidence reference and flag claims that exceed the supplied evidence.",
    "reliability": "Inspect trace completeness, warnings, costs, and failure classification. Look for a partial run presented as complete.",
    "architecture": "Review the supplied source context for contract leakage, runtime access to evaluation assets, compensating branches, or boundary violations.",
}


class JsonReviewer(Protocol):
    async def json_chat(self, system: str, user: str) -> dict[str, Any] | None: ...


@dataclass(frozen=True)
class ReviewFinding:
    severity: str
    stage: str
    claim: str
    evidence_refs: list[str]
    counterexample: str
    verifiable: bool


@dataclass(frozen=True)
class AgentReview:
    role: str
    verdict: str
    summary: str
    findings: list[ReviewFinding] = field(default_factory=list)
    raw_available: bool = False


async def run_adversarial_reviews(
    reviewer: JsonReviewer,
    report: dict[str, Any],
    roles: list[str],
    source_context: str = "",
) -> list[AgentReview]:
    reviews: list[AgentReview] = []
    for role in roles:
        if role not in REVIEW_ROLES:
            raise ValueError(f"Unknown review role: {role}")
        data = await reviewer.json_chat(
            _system_prompt(role),
            _user_prompt(role, report, source_context),
        )
        reviews.append(_review_from_data(role, data))
    return reviews


def reviews_to_dict(reviews: list[AgentReview]) -> list[dict[str, Any]]:
    return [asdict(review) for review in reviews]


def _system_prompt(role: str) -> str:
    return (
        "You are an independent adversarial reviewer of a GitHub research report. "
        f"Your role: {REVIEW_ROLES[role]} "
        "Do not recommend repositories, invent product knowledge, or propose keyword lists, synonyms, "
        "translation fallbacks, query-specific branches, or test fixtures. "
        "Return strict JSON with verdict (pass, concern, or inconclusive), summary, and findings. "
        "Each finding must contain severity (P0-P3), stage, claim, evidence_refs, counterexample, and verifiable. "
        "Set verifiable to false when the supplied artifacts cannot support the finding."
    )


def _user_prompt(role: str, report: dict[str, Any], source_context: str) -> str:
    artifact: dict[str, Any] = {
        "summary": report.get("summary"),
        "decisionBrief": report.get("decisionBrief"),
        "requirement": report.get("requirement"),
        "topProjects": report.get("topProjects"),
        "usage": report.get("usage"),
        "raw": report.get("raw"),
        "runTrace": report.get("runTrace"),
    }
    if role == "architecture":
        artifact["sourceContext"] = source_context
    return json.dumps(artifact, ensure_ascii=False, indent=2)


def _review_from_data(role: str, data: dict[str, Any] | None) -> AgentReview:
    if not isinstance(data, dict):
        return AgentReview(
            role=role,
            verdict="inconclusive",
            summary="The configured review agent did not return a structured review.",
        )
    findings_data = data.get("findings")
    finding_items = findings_data if isinstance(findings_data, list) else []
    findings = [
        _finding_from_data(item)
        for item in finding_items
        if isinstance(item, dict)
    ]
    return AgentReview(
        role=role,
        verdict=_text(data.get("verdict"), "inconclusive"),
        summary=_text(data.get("summary"), "No review summary was returned."),
        findings=findings,
        raw_available=True,
    )


def _finding_from_data(data: dict[str, Any]) -> ReviewFinding:
    evidence_refs = _texts(data.get("evidence_refs"))
    return ReviewFinding(
        severity=_text(data.get("severity"), "P3"),
        stage=_text(data.get("stage"), "unknown"),
        claim=_text(data.get("claim")),
        evidence_refs=evidence_refs,
        counterexample=_text(data.get("counterexample")),
        verifiable=bool(data.get("verifiable")) and bool(evidence_refs),
    )


def _text(value: object, default: str = "") -> str:
    return value.strip() if isinstance(value, str) and value.strip() else default


def _texts(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [_text(item) for item in value if _text(item)]
