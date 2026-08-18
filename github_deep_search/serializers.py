from __future__ import annotations

from datetime import datetime
from typing import Any

from github_deep_search.models import (
    FinalReport,
    ReportEvidence,
    ReportProject,
    ReportRequirement,
    RunError,
    RunEvent,
    RunSnapshot,
    StageProgress,
)


def serialize_run(snapshot: RunSnapshot) -> dict[str, Any]:
    payload = {
        "id": snapshot.id,
        "query": snapshot.request.raw_input,
        "status": snapshot.status,
        "reportLanguage": snapshot.report_language,
        "createdAt": _serialize_datetime(snapshot.created_at),
        "updatedAt": _serialize_datetime(snapshot.updated_at),
        "stages": [_serialize_stage(stage) for stage in snapshot.stages],
        "warnings": list(snapshot.warnings),
        "supplementalDiscoveryIteration": snapshot.supplemental_discovery_iteration,
        "error": _serialize_error(snapshot.error) if snapshot.error else None,
        "lastEventId": snapshot.last_event_sequence,
    }
    if snapshot.report is not None:
        payload["report"] = _serialize_report(snapshot.report)
    return payload


def serialize_event(event: RunEvent) -> dict[str, Any]:
    return {
        "id": event.sequence,
        "type": event.type,
        "at": _serialize_datetime(event.occurred_at),
        "stage": event.stage,
        "iteration": event.iteration,
        "message": event.message,
    }


def _serialize_stage(stage: StageProgress) -> dict[str, Any]:
    return {
        "name": stage.name,
        "status": stage.status,
        "startedAt": _serialize_datetime(stage.started_at) if stage.started_at else None,
        "finishedAt": _serialize_datetime(stage.finished_at) if stage.finished_at else None,
    }


def _serialize_error(error: RunError) -> dict[str, Any]:
    return {
        "code": error.code,
        "message": error.message,
        "stage": error.stage,
    }


def _serialize_report(report: FinalReport) -> dict[str, Any]:
    return {
        "language": report.language,
        "projects": [_serialize_project(project) for project in report.projects],
        "markdown": report.markdown,
        "usage": {
            "llmInputTokens": report.usage.llm_input_tokens,
            "llmOutputTokens": report.usage.llm_output_tokens,
            "llmTotalTokens": report.usage.llm_total_tokens,
        },
    }


def _serialize_project(project: ReportProject) -> dict[str, Any]:
    return {
        "repository": project.repository,
        "url": project.url,
        "score": project.score,
        "relevanceReason": project.relevance_reason,
        "confirmed": [_serialize_requirement(item) for item in project.confirmed],
        "gaps": [_serialize_requirement(item) for item in project.gaps],
        "facts": {
            "lastPushedAt": project.last_pushed_at,
            "archived": project.is_archived,
            "license": project.license,
            "latestReleaseAt": project.latest_release_at,
        },
        "risks": list(project.risks),
    }


def _serialize_requirement(requirement: ReportRequirement) -> dict[str, Any]:
    return {
        "id": requirement.requirement_id,
        "kind": requirement.kind,
        "requirement": requirement.requirement,
        "status": requirement.status,
        "explanation": requirement.explanation,
        "evidence": [_serialize_evidence(item) for item in requirement.evidence],
    }


def _serialize_evidence(evidence: ReportEvidence) -> dict[str, Any]:
    return {
        "id": evidence.evidence_id,
        "label": evidence.label,
        "url": evidence.url,
        "quote": evidence.quote,
    }


def _serialize_datetime(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")
