from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from github_deep_search.config import Settings
from github_deep_search.models import STAGE_NAMES, RunEvent, StageName
from github_deep_search.pipeline import Pipeline, PipelineContext
from github_deep_search.run_manager import RunConflictError, RunManager, RunNotFoundError
from tests.fakes import fake_final_report


def settings(*, timeout: int = 600) -> Settings:
    return Settings(
        github_token="github-token",
        llm_api_key="llm-key",
        llm_base_url="https://provider.example/v1",
        llm_model="model",
        llm_thinking=None,
        llm_reasoning_effort=None,
        max_github_requests=200,
        max_candidates=80,
        max_evidence_repositories=12,
        run_timeout_seconds=timeout,
    )


@dataclass
class ControlledStage:
    name: StageName
    started: asyncio.Event | None = None
    release: asyncio.Event | None = None
    fail: bool = False
    closed: bool = False
    close_release: asyncio.Event | None = None
    close_started: asyncio.Event | None = None

    async def execute(self, context: PipelineContext) -> None:
        context.raise_if_cancelled()
        if self.started is not None:
            self.started.set()
        if self.release is not None:
            await self.release.wait()
        if self.fail:
            raise RuntimeError("secret github-token should never be public")
        if self.name == "report":
            language = context.validated_input.report_language if context.validated_input else "en"
            context.final_report = fake_final_report(language)

    async def aclose(self) -> None:
        if self.close_started is not None:
            self.close_started.set()
        if self.close_release is not None:
            await self.close_release.wait()
        self.closed = True


def pipeline_with(*, blocked_stage: StageName | None = None, fail_stage: StageName | None = None):
    started = asyncio.Event() if blocked_stage else None
    release = asyncio.Event() if blocked_stage else None
    stages = [
        ControlledStage(
            name=name,
            started=started if name == blocked_stage else None,
            release=release if name == blocked_stage else None,
            fail=name == fail_stage,
        )
        for name in STAGE_NAMES
    ]
    return Pipeline(stages), started, release


async def wait_for_terminal(manager: RunManager, run_id: str):
    for _ in range(200):
        snapshot = await manager.get(run_id)
        if snapshot.status != "running":
            return snapshot
        await asyncio.sleep(0.01)
    raise AssertionError("run did not reach a terminal state")


@pytest.mark.asyncio
async def test_run_manager_records_real_six_stage_progress_and_replayable_events() -> None:
    manager = RunManager()
    pipeline, _, _ = pipeline_with()

    created = await manager.start("sample request", settings(), pipeline)
    completed = await wait_for_terminal(manager, created.id)
    events = [event async for event in manager.iter_events(created.id, after=0, heartbeat_seconds=0.01) if event]

    assert completed.status == "completed"
    assert [stage.name for stage in completed.stages] == list(STAGE_NAMES)
    assert all(stage.status == "completed" for stage in completed.stages)
    assert [event.type for event in events] == [
        *(event for _ in STAGE_NAMES for event in ("stage_started", "stage_completed")),
        "run_completed",
    ]
    assert [event.sequence for event in events] == list(range(1, 14))

    replay = [event async for event in manager.iter_events(created.id, after=10) if event]
    assert [event.sequence for event in replay] == [11, 12, 13]


@pytest.mark.asyncio
async def test_run_manager_rejects_a_second_active_run_and_replaces_terminal_memory() -> None:
    manager = RunManager()
    first_pipeline, started, release = pipeline_with(blocked_stage="parse")
    first = await manager.start("first request", settings(), first_pipeline)
    assert started is not None and release is not None
    await started.wait()

    second_stages = [ControlledStage(name=name) for name in STAGE_NAMES]
    second_pipeline = Pipeline(second_stages)
    with pytest.raises(RunConflictError):
        await manager.start("second request", settings(), second_pipeline)

    assert all(stage.closed for stage in second_stages)

    release.set()
    await wait_for_terminal(manager, first.id)
    replacement_pipeline, _, _ = pipeline_with()
    second = await manager.start("second request", settings(), replacement_pipeline)

    with pytest.raises(RunNotFoundError):
        await manager.get(first.id)
    assert (await manager.get(second.id)).request.raw_input == "second request"
    await wait_for_terminal(manager, second.id)


@pytest.mark.asyncio
async def test_run_manager_closes_all_pipeline_stage_resources() -> None:
    manager = RunManager()
    stages = [ControlledStage(name=name) for name in STAGE_NAMES]
    pipeline = Pipeline(stages)

    created = await manager.start("sample request", settings(), pipeline)
    await wait_for_terminal(manager, created.id)

    assert all(stage.closed for stage in stages)


@pytest.mark.asyncio
async def test_global_timeout_includes_pipeline_resource_closing() -> None:
    manager = RunManager()
    close_release = asyncio.Event()
    stages = [ControlledStage(name=name) for name in STAGE_NAMES]
    stages[-1].close_release = close_release
    pipeline = Pipeline(stages)

    created = await manager.start("sample request", settings(timeout=1), pipeline)
    timed_out = await wait_for_terminal(manager, created.id)

    assert timed_out.status == "failed"
    assert timed_out.error is not None
    assert timed_out.error.code == "run_timeout"
    assert timed_out.report is None
    assert await manager.get_active() is None


@pytest.mark.asyncio
async def test_cancellation_does_not_wait_indefinitely_for_resource_closing() -> None:
    manager = RunManager()
    close_started = asyncio.Event()
    close_release = asyncio.Event()
    stages = [ControlledStage(name=name) for name in STAGE_NAMES]
    stages[-1].close_started = close_started
    stages[-1].close_release = close_release
    pipeline = Pipeline(stages)
    created = await manager.start("sample request", settings(), pipeline)
    await close_started.wait()

    cancelled = await asyncio.wait_for(manager.cancel(created.id), timeout=2.0)

    assert cancelled.status == "cancelled"
    assert await manager.get_active() is None


@pytest.mark.asyncio
async def test_run_manager_cancels_the_active_stage_and_keeps_future_stages_not_started() -> None:
    manager = RunManager()
    pipeline, started, _ = pipeline_with(blocked_stage="discovery")
    created = await manager.start("sample request", settings(), pipeline)
    assert started is not None
    await started.wait()

    cancelled = await manager.cancel(created.id)

    assert cancelled.status == "cancelled"
    assert cancelled.stages[2].status == "cancelled"
    assert all(stage.status == "not_started" for stage in cancelled.stages[3:])
    assert cancelled.error is not None
    assert cancelled.error.code == "run_cancelled"
    assert await manager.get_active() is None


@pytest.mark.asyncio
async def test_run_manager_times_out_and_does_not_expose_exception_details() -> None:
    timeout_manager = RunManager()
    timeout_pipeline, started, _ = pipeline_with(blocked_stage="input")
    timed = await timeout_manager.start("sample request", settings(timeout=1), timeout_pipeline)
    assert started is not None
    await started.wait()
    timed_out = await wait_for_terminal(timeout_manager, timed.id)

    assert timed_out.status == "failed"
    assert timed_out.error is not None
    assert timed_out.error.code == "run_timeout"
    assert timed_out.stages[0].status == "failed"

    failed_manager = RunManager()
    failed_pipeline, _, _ = pipeline_with(fail_stage="evidence")
    failed = await failed_manager.start("sample request", settings(), failed_pipeline)
    unexpected = await wait_for_terminal(failed_manager, failed.id)

    assert unexpected.status == "failed"
    assert unexpected.error is not None
    assert unexpected.error.code == "unexpected_error"
    assert "github-token" not in unexpected.error.message


@pytest.mark.asyncio
async def test_warning_and_supplemental_discovery_events_are_user_visible() -> None:
    manager = RunManager()
    pipeline, started, release = pipeline_with(blocked_stage="discovery")
    created = await manager.start("sample request", settings(), pipeline)
    assert started is not None and release is not None
    await started.wait()

    await manager.warning(created.id, "one query was invalid", stage="discovery")
    await manager.supplemental_discovery(created.id, iteration=1)
    release.set()
    completed = await wait_for_terminal(manager, created.id)

    events: list[RunEvent] = [
        event async for event in manager.iter_events(created.id, after=0) if event
    ]
    assert completed.warnings == ("one query was invalid",)
    assert completed.supplemental_discovery_iteration == 1
    assert any(event.type == "warning" and event.message == "one query was invalid" for event in events)
    assert any(event.type == "supplemental_discovery" and event.iteration == 1 for event in events)
