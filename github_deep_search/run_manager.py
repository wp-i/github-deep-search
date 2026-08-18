from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import AsyncIterator
from uuid import uuid4

from github_deep_search.config import Settings
from github_deep_search.models import (
    FinalReport,
    STAGE_NAMES,
    ReportLanguage,
    RunError,
    RunEvent,
    RunEventType,
    RunRequest,
    RunSnapshot,
    RunStatus,
    StageName,
    StageProgress,
)
from github_deep_search.pipeline import (
    PipelineContext,
    PipelineControl,
    PipelineFailure,
    PipelineRunner,
)


_CANCEL_CLOSE_GRACE_SECONDS = 1.0


class RunConflictError(Exception):
    pass


class RunNotFoundError(Exception):
    pass


class RunNotActiveError(Exception):
    pass


@dataclass
class _RunRecord:
    id: str
    request: RunRequest
    status: RunStatus
    stages: dict[StageName, StageProgress]
    created_at: datetime
    updated_at: datetime
    report_language: ReportLanguage | None = None
    warnings: list[str] = field(default_factory=list)
    supplemental_discovery_iteration: int = 0
    error: RunError | None = None
    report: FinalReport | None = None
    events: list[RunEvent] = field(default_factory=list)
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    cancellation_event: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task[None] | None = None


class _ManagerControl(PipelineControl):
    def __init__(self, manager: RunManager, run_id: str) -> None:
        self._manager = manager
        self._run_id = run_id

    async def stage_started(self, stage: StageName) -> None:
        await self._manager._stage_started(self._run_id, stage)

    async def stage_completed(self, stage: StageName) -> None:
        await self._manager._stage_completed(self._run_id, stage)

    async def set_report_language(self, language: ReportLanguage) -> None:
        await self._manager._set_report_language(self._run_id, language)

    async def warning(self, message: str, *, stage: StageName | None = None) -> None:
        await self._manager.warning(self._run_id, message, stage=stage)

    async def supplemental_discovery(self, *, iteration: int) -> None:
        await self._manager.supplemental_discovery(self._run_id, iteration=iteration)


class RunManager:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._records: dict[str, _RunRecord] = {}
        self._active_id: str | None = None

    async def start(
        self,
        raw_input: str,
        settings: Settings,
        pipeline: PipelineRunner,
    ) -> RunSnapshot:
        async with self._lock:
            conflict = self._active_id is not None
            if not conflict:
                snapshot = self._start_locked(raw_input, settings, pipeline)
        if conflict:
            try:
                await _close_pipeline(pipeline)
            except Exception:
                pass
            raise RunConflictError("another run is already active")
        return snapshot

    def _start_locked(
        self,
        raw_input: str,
        settings: Settings,
        pipeline: PipelineRunner,
    ) -> RunSnapshot:
        self._records.clear()
        now = _utc_now()
        run_id = uuid4().hex
        record = _RunRecord(
            id=run_id,
            request=RunRequest(raw_input=raw_input),
            status="running",
            stages={name: StageProgress(name=name) for name in STAGE_NAMES},
            created_at=now,
            updated_at=now,
        )
        self._records[run_id] = record
        self._active_id = run_id
        context = PipelineContext(
            run_id=run_id,
            request=record.request,
            settings=settings,
            cancellation_event=record.cancellation_event,
        )
        record.task = asyncio.create_task(
            self._execute(record, pipeline, context),
            name=f"github-deep-search-{run_id}",
        )
        return _snapshot(record)

    async def get(self, run_id: str) -> RunSnapshot:
        async with self._lock:
            return _snapshot(self._record(run_id))

    async def get_active(self) -> RunSnapshot | None:
        async with self._lock:
            if self._active_id is None:
                return None
            return _snapshot(self._record(self._active_id))

    async def cancel(self, run_id: str) -> RunSnapshot:
        task: asyncio.Task[None] | None
        async with self._lock:
            record = self._record(run_id)
            if record.status != "running":
                raise RunNotActiveError("the run is not active")
            record.cancellation_event.set()
            self._finish_locked(
                record,
                status="cancelled",
                event_type="run_cancelled",
                error=RunError(
                    code="run_cancelled",
                    message="The run was cancelled by the user.",
                    stage=_current_stage(record),
                ),
            )
            task = record.task

        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        return await self.get(run_id)

    async def warning(self, run_id: str, message: str, *, stage: StageName | None = None) -> None:
        async with self._lock:
            record = self._running_record(run_id)
            record.warnings.append(message)
            self._append_event_locked(record, "warning", stage=stage, message=message)

    async def supplemental_discovery(self, run_id: str, *, iteration: int) -> None:
        if iteration < 1:
            raise ValueError("supplemental discovery iteration must be positive")
        async with self._lock:
            record = self._running_record(run_id)
            record.supplemental_discovery_iteration = max(
                record.supplemental_discovery_iteration,
                iteration,
            )
            self._append_event_locked(
                record,
                "supplemental_discovery",
                stage="discovery",
                iteration=iteration,
            )

    async def iter_events(
        self,
        run_id: str,
        *,
        after: int = 0,
        heartbeat_seconds: float = 15.0,
    ) -> AsyncIterator[RunEvent | None]:
        if after < 0:
            raise ValueError("event sequence must not be negative")
        async with self._lock:
            record = self._record(run_id)

        next_sequence = after + 1
        while True:
            async with self._lock:
                pending = [event for event in record.events if event.sequence >= next_sequence]
                terminal = record.status != "running"
                changed = record.changed

            if pending:
                for event in pending:
                    next_sequence = event.sequence + 1
                    yield event
                continue
            if terminal:
                return

            try:
                await asyncio.wait_for(changed.wait(), timeout=heartbeat_seconds)
            except TimeoutError:
                yield None

    async def close(self) -> None:
        async with self._lock:
            active_id = self._active_id
        if active_id is not None:
            await self.cancel(active_id)

    async def _execute(
        self,
        record: _RunRecord,
        pipeline: PipelineRunner,
        context: PipelineContext,
    ) -> None:
        control = _ManagerControl(self, record.id)
        failure: tuple[str, str] | None = None
        cancelled = False
        pipeline_closed = False
        try:
            async with asyncio.timeout(context.settings.run_timeout_seconds):
                try:
                    await pipeline.run(context, control)
                except PipelineFailure as exc:
                    failure = (exc.code, exc.public_message)
                except Exception:
                    failure = (
                        "unexpected_error",
                        "The run failed unexpectedly.",
                    )

                if failure is None and context.final_report is None:
                    failure = (
                        "incomplete_pipeline",
                        "The pipeline ended without producing a final report.",
                    )

                try:
                    await _close_pipeline(pipeline)
                    pipeline_closed = True
                except Exception:
                    if failure is None:
                        failure = (
                            "resource_close_failed",
                            "The run could not close its resources safely.",
                        )
        except TimeoutError:
            failure = (
                "run_timeout",
                f"The run exceeded the {context.settings.run_timeout_seconds}-second timeout.",
            )
        except asyncio.CancelledError:
            cancelled = True

        if cancelled and not pipeline_closed:
            try:
                async with asyncio.timeout(_CANCEL_CLOSE_GRACE_SECONDS):
                    await _close_pipeline(pipeline)
            except Exception:
                pass

        if cancelled:
            await self._cancel_if_running(record.id)
        elif failure is not None:
            await self._fail(record.id, *failure)
        else:
            try:
                if context.final_report is None:
                    raise RuntimeError("final report is unavailable")
                await self._complete(record.id, context.final_report)
            except Exception:
                await self._fail(
                    record.id,
                    "incomplete_pipeline",
                    "The pipeline ended before all six stages completed.",
                )

    async def _stage_started(self, run_id: str, stage: StageName) -> None:
        async with self._lock:
            record = self._running_record(run_id)
            progress = record.stages[stage]
            stage_index = STAGE_NAMES.index(stage)
            if progress.status != "not_started" or any(
                record.stages[previous].status != "completed"
                for previous in STAGE_NAMES[:stage_index]
            ):
                raise RuntimeError(f"invalid transition when starting stage {stage}")
            now = _utc_now()
            record.stages[stage] = replace(progress, status="in_progress", started_at=now)
            self._append_event_locked(record, "stage_started", stage=stage, occurred_at=now)

    async def _stage_completed(self, run_id: str, stage: StageName) -> None:
        async with self._lock:
            record = self._running_record(run_id)
            progress = record.stages[stage]
            if progress.status != "in_progress":
                raise RuntimeError(f"invalid transition when completing stage {stage}")
            now = _utc_now()
            record.stages[stage] = replace(progress, status="completed", finished_at=now)
            self._append_event_locked(record, "stage_completed", stage=stage, occurred_at=now)

    async def _set_report_language(self, run_id: str, language: ReportLanguage) -> None:
        async with self._lock:
            record = self._running_record(run_id)
            record.report_language = language
            self._touch_locked(record)

    async def _complete(self, run_id: str, report: FinalReport) -> None:
        async with self._lock:
            record = self._running_record(run_id)
            if any(stage.status != "completed" for stage in record.stages.values()):
                raise RuntimeError("pipeline returned before all stages completed")
            record.report = report
            self._finish_locked(record, status="completed", event_type="run_completed")

    async def _fail(self, run_id: str, code: str, message: str) -> None:
        async with self._lock:
            record = self._record(run_id)
            if record.status != "running":
                return
            self._finish_locked(
                record,
                status="failed",
                event_type="run_failed",
                error=RunError(code=code, message=message, stage=_current_stage(record)),
            )

    async def _cancel_if_running(self, run_id: str) -> None:
        async with self._lock:
            record = self._record(run_id)
            if record.status != "running":
                return
            self._finish_locked(
                record,
                status="cancelled",
                event_type="run_cancelled",
                error=RunError(
                    code="run_cancelled",
                    message="The run was cancelled by the user.",
                    stage=_current_stage(record),
                ),
            )

    def _finish_locked(
        self,
        record: _RunRecord,
        *,
        status: RunStatus,
        event_type: RunEventType,
        error: RunError | None = None,
    ) -> None:
        current_stage = _current_stage(record)
        if current_stage is not None:
            progress = record.stages[current_stage]
            terminal_stage_status = "cancelled" if status == "cancelled" else "failed"
            record.stages[current_stage] = replace(
                progress,
                status=terminal_stage_status,
                finished_at=_utc_now(),
            )
        record.status = status
        record.error = error
        self._active_id = None
        self._append_event_locked(
            record,
            event_type,
            stage=current_stage,
            message=error.message if error else None,
        )

    def _append_event_locked(
        self,
        record: _RunRecord,
        event_type: RunEventType,
        *,
        stage: StageName | None = None,
        iteration: int | None = None,
        message: str | None = None,
        occurred_at: datetime | None = None,
    ) -> None:
        event_time = occurred_at or _utc_now()
        record.events.append(
            RunEvent(
                sequence=len(record.events) + 1,
                type=event_type,
                occurred_at=event_time,
                stage=stage,
                iteration=iteration,
                message=message,
            )
        )
        record.updated_at = event_time
        self._notify_locked(record)

    def _touch_locked(self, record: _RunRecord) -> None:
        record.updated_at = _utc_now()
        self._notify_locked(record)

    @staticmethod
    def _notify_locked(record: _RunRecord) -> None:
        changed = record.changed
        record.changed = asyncio.Event()
        changed.set()

    def _record(self, run_id: str) -> _RunRecord:
        try:
            return self._records[run_id]
        except KeyError as exc:
            raise RunNotFoundError(run_id) from exc

    def _running_record(self, run_id: str) -> _RunRecord:
        record = self._record(run_id)
        if record.status != "running":
            raise RunNotActiveError(run_id)
        return record


def _current_stage(record: _RunRecord) -> StageName | None:
    return next(
        (name for name in STAGE_NAMES if record.stages[name].status == "in_progress"),
        None,
    )


def _snapshot(record: _RunRecord) -> RunSnapshot:
    return RunSnapshot(
        id=record.id,
        request=record.request,
        status=record.status,
        stages=tuple(record.stages[name] for name in STAGE_NAMES),
        created_at=record.created_at,
        updated_at=record.updated_at,
        report_language=record.report_language,
        warnings=tuple(record.warnings),
        supplemental_discovery_iteration=record.supplemental_discovery_iteration,
        error=record.error,
        last_event_sequence=len(record.events),
        report=record.report,
    )


def _utc_now() -> datetime:
    return datetime.now(UTC)


async def _close_pipeline(pipeline: PipelineRunner) -> None:
    await pipeline.aclose()
