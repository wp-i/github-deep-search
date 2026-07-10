from __future__ import annotations

from dataclasses import asdict
from typing import Any

from github_deep_search.models import (
    BudgetUsage,
    RunFailure,
    RunTrace,
    SearchFailureArtifact,
    SearchReport,
    StageOutcome,
)


STAGE_NAMES = ("parse", "discovery", "evidence", "analysis", "report_delivery")


class SearchRunFailed(RuntimeError):
    def __init__(self, artifact: SearchFailureArtifact) -> None:
        super().__init__(artifact.failure.message)
        self.artifact = artifact


class RunTraceRecorder:
    def __init__(self) -> None:
        self._stages: list[StageOutcome] = []
        self._active_name = ""
        self._active_inputs: dict[str, int] = {}

    @property
    def active_name(self) -> str:
        return self._active_name

    @property
    def next_name(self) -> str:
        if self._active_name:
            return self._active_name
        if len(self._stages) < len(STAGE_NAMES):
            return STAGE_NAMES[len(self._stages)]
        return STAGE_NAMES[-1]

    def begin(self, name: str, inputs: dict[str, int]) -> None:
        if name not in STAGE_NAMES:
            raise ValueError(f"Unknown run stage: {name}")
        if self._active_name:
            raise RuntimeError(f"Run stage {self._active_name} is still active")
        expected = STAGE_NAMES[len(self._stages)]
        if name != expected:
            raise RuntimeError(f"Expected run stage {expected}, got {name}")
        self._active_name = name
        self._active_inputs = dict(inputs)

    def complete(self, outputs: dict[str, int], notes: list[str] | None = None) -> None:
        self._finish("completed", outputs, notes or [])

    def partial(self, outputs: dict[str, int], notes: list[str]) -> None:
        self._finish("partial", outputs, notes)

    def fail(self, failure: RunFailure) -> None:
        name = self._active_name or failure.stage
        inputs = self._active_inputs if self._active_name else {}
        completed_names = {stage.name for stage in self._stages}
        if name in completed_names:
            index = next(index for index, stage in enumerate(self._stages) if stage.name == name)
            previous = self._stages[index]
            self._stages[index] = StageOutcome(
                name=name,
                status="failed",
                inputs=previous.inputs,
                outputs=previous.outputs,
                notes=[*previous.notes, failure.message],
                failure=failure,
            )
        else:
            self._stages.append(
                StageOutcome(
                    name=name,
                    status="failed",
                    inputs=dict(inputs),
                    notes=[failure.message],
                    failure=failure,
                )
            )
        self._active_name = ""
        self._active_inputs = {}
        recorded_names = {stage.name for stage in self._stages}
        for stage_name in STAGE_NAMES:
            if stage_name not in recorded_names:
                self._stages.append(StageOutcome(stage_name, "not_started"))

    def _finish(self, status: str, outputs: dict[str, int], notes: list[str]) -> None:
        if not self._active_name:
            raise RuntimeError("No active run stage")
        self._stages.append(
            StageOutcome(
                name=self._active_name,
                status=status,  # type: ignore[arg-type]
                inputs=dict(self._active_inputs),
                outputs=dict(outputs),
                notes=list(notes),
            )
        )
        self._active_name = ""
        self._active_inputs = {}

    def build(self) -> RunTrace:
        failure = next((item.failure for item in self._stages if item.failure), None)
        if failure:
            status = "failed"
        elif all(item.status == "completed" for item in self._stages):
            status = "completed"
        else:
            status = "partial"
        return RunTrace("1", status, list(self._stages), failure)


def build_run_trace(report: SearchReport) -> RunTrace:
    raw = report.raw
    planned = _mapping(raw.get("planned_query_counts"))
    request_stages = _mapping(raw.get("request_stages"))
    source_mix = _mapping(raw.get("source_mix"))
    evidence_gate = _mapping(raw.get("evidence_gate"))
    completeness = str(raw.get("search_completeness") or "complete")
    completeness_reasons = _strings(raw.get("search_completeness_reasons"))
    coverage_count = sum(len(item.evidence_coverage) for item in report.top_projects)

    stages = [
        StageOutcome(
            name="parse",
            status="completed",
            inputs={"query": 1},
            outputs={
                "must_have": len(report.requirement.must_have_features),
                "nice_to_have": len(report.requirement.nice_to_have_features),
                "planned_queries": sum(_integer(value) for value in planned.values()),
            },
        ),
        StageOutcome(
            name="discovery",
            status="partial" if completeness == "limited" else "completed",
            inputs={"planned_queries": sum(_integer(value) for value in planned.values())},
            outputs={
                "candidates": _integer(raw.get("candidate_count")),
                "sources": sum(1 for value in source_mix.values() if _integer(value) > 0),
                "requests": _integer(request_stages.get("discovery")),
            },
            notes=completeness_reasons,
        ),
        StageOutcome(
            name="evidence",
            status="completed",
            inputs={"deep_pool": _integer(raw.get("deep_pool_count"))},
            outputs={
                "coverage_items": coverage_count,
                "requests": _integer(request_stages.get("readme"))
                + _integer(request_stages.get("source")),
            },
        ),
        StageOutcome(
            name="analysis",
            status="completed",
            inputs={"deep_pool": _integer(raw.get("deep_pool_count"))},
            outputs={
                "returned_projects": len(report.top_projects),
                "reliable": _integer(raw.get("reliable_top_projects_count")),
                "reference": _integer(raw.get("reference_candidate_count")),
                "leads": _integer(raw.get("low_similarity_lead_count")),
                "evidence_gate_items": _integer(evidence_gate.get("coverage_items")),
            },
        ),
        StageOutcome(
            name="report_delivery",
            status="completed" if report.report_markdown.strip() else "failed",
            inputs={"projects": len(report.top_projects)},
            outputs={"markdown": int(bool(report.report_markdown.strip()))},
        ),
    ]
    status = "completed" if all(stage.status == "completed" for stage in stages) else "partial"
    return RunTrace(schema_version="1", status=status, stages=stages)


def classify_failure(stage: str, exc: Exception) -> RunFailure:
    exception_type = type(exc).__name__
    module = type(exc).__module__
    if stage == "report_delivery":
        kind = "report_delivery"
        message = "Report delivery failed."
        retryable = False
    elif stage == "parse" and isinstance(exc, (TypeError, ValueError)):
        kind = "invalid_request"
        message = "The request could not be parsed."
        retryable = False
    elif isinstance(exc, (ConnectionError, TimeoutError)) or module.startswith("httpx"):
        kind = "provider"
        message = f"A configured provider failed during {stage}."
        retryable = True
    else:
        kind = "execution"
        message = f"The search run failed during {stage}."
        retryable = False
    return RunFailure(kind, stage, exception_type, message, retryable)


def build_failure_artifact(
    query: str,
    usage: BudgetUsage,
    trace: RunTrace,
    failure: RunFailure,
) -> SearchFailureArtifact:
    completed = [stage.name for stage in trace.stages if stage.status == "completed"]
    skipped = [stage.name for stage in trace.stages if stage.status == "not_started"]
    lines = [
        "# Search run failed",
        "",
        f"- Failed stage: {failure.stage}",
        f"- Failure kind: {failure.kind}",
        f"- Reason: {failure.message}",
        f"- Retryable: {'yes' if failure.retryable else 'no'}",
        f"- Completed stages: {', '.join(completed) if completed else 'none'}",
        f"- Not started: {', '.join(skipped) if skipped else 'none'}",
    ]
    return SearchFailureArtifact("1", query, "\n".join(lines), usage, trace, failure)


def run_trace_to_dict(trace: RunTrace) -> dict[str, Any]:
    return asdict(trace)


def _mapping(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _strings(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _integer(value: object) -> int:
    return int(value) if isinstance(value, (int, float)) else 0
