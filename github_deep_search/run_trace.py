from __future__ import annotations

from dataclasses import asdict
from typing import Any

from github_deep_search.models import (
    BudgetUsage,
    RunFailure,
    RunTrace,
    SearchFailureArtifact,
    StageOutcome,
)
from github_deep_search.providers.github import GitHubProviderError


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
        stages = {stage.name: stage for stage in self._stages}
        if name in stages:
            previous = stages[name]
            stages[name] = StageOutcome(
                name=name,
                status="failed",
                inputs=previous.inputs,
                outputs=previous.outputs,
                notes=[*previous.notes, failure.message],
                failure=failure,
            )
        else:
            stages[name] = StageOutcome(
                name=name,
                status="failed",
                inputs=dict(inputs),
                notes=[failure.message],
                failure=failure,
            )
        self._active_name = ""
        self._active_inputs = {}
        self._stages = [
            stages.get(stage_name, StageOutcome(stage_name, "not_started"))
            for stage_name in STAGE_NAMES
        ]

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


def classify_failure(stage: str, exc: Exception) -> RunFailure:
    exception_type = type(exc).__name__
    module = type(exc).__module__
    if stage == "report_delivery":
        kind = "report_delivery"
        message = "Report delivery failed."
        retryable = False
    elif stage == "parse" and isinstance(exc, (TypeError, ValueError)):
        kind = "invalid_request"
        message = str(exc).strip() or "The request could not be parsed."
        retryable = False
    elif isinstance(exc, GitHubProviderError):
        kind = "provider"
        message = str(exc)
        retryable = exc.retryable
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
