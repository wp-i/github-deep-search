from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Protocol, Sequence

from github_deep_search.config import Settings
from github_deep_search.models import (
    AnalysisResult,
    Usage,
    DiscoveryResult,
    EvidenceResult,
    FinalReport,
    ParsedRequirement,
    STAGE_NAMES,
    ReportLanguage,
    RunRequest,
    StageName,
    ValidatedInput,
)


class PipelineFailure(Exception):
    def __init__(self, code: str, public_message: str) -> None:
        super().__init__(public_message)
        self.code = code
        self.public_message = public_message


@dataclass
class PipelineContext:
    run_id: str
    request: RunRequest
    settings: Settings
    cancellation_event: asyncio.Event | None = None
    validated_input: ValidatedInput | None = None
    parsed_requirement: ParsedRequirement | None = None
    discovery_result: DiscoveryResult | None = None
    evidence_result: EvidenceResult | None = None
    analysis_result: AnalysisResult | None = None
    final_report: FinalReport | None = None
    usage: Usage = field(default_factory=Usage)
    control: PipelineControl | None = None

    def raise_if_cancelled(self) -> None:
        if self.cancellation_event is not None and self.cancellation_event.is_set():
            raise asyncio.CancelledError

    async def warning(self, message: str, *, stage: StageName | None = None) -> None:
        if self.control is None:
            raise RuntimeError("pipeline control is not available")
        await self.control.warning(message, stage=stage)

    async def supplemental_discovery(self, *, iteration: int) -> None:
        if self.control is None:
            raise RuntimeError("pipeline control is not available")
        await self.control.supplemental_discovery(iteration=iteration)


class PipelineControl(Protocol):
    async def stage_started(self, stage: StageName) -> None: ...

    async def stage_completed(self, stage: StageName) -> None: ...

    async def set_report_language(self, language: ReportLanguage) -> None: ...

    async def warning(self, message: str, *, stage: StageName | None = None) -> None: ...

    async def supplemental_discovery(self, *, iteration: int) -> None: ...


class PipelineStage(Protocol):
    name: StageName

    async def execute(self, context: PipelineContext) -> None: ...


class PipelineRunner(Protocol):
    async def run(self, context: PipelineContext, control: PipelineControl) -> None: ...

    async def aclose(self) -> None: ...


class Pipeline:
    def __init__(self, stages: Sequence[PipelineStage]) -> None:
        stage_names = tuple(stage.name for stage in stages)
        if stage_names != STAGE_NAMES:
            raise ValueError(f"pipeline stages must be ordered as {STAGE_NAMES!r}")
        self._stages = tuple(stages)
        self._closed_stage_indexes: set[int] = set()

    async def run(self, context: PipelineContext, control: PipelineControl) -> None:
        context.control = control
        for stage in self._stages:
            context.raise_if_cancelled()
            await control.stage_started(stage.name)
            await stage.execute(context)
            if stage.name == "input" and context.validated_input is not None:
                await control.set_report_language(context.validated_input.report_language)
            await control.stage_completed(stage.name)

    async def aclose(self) -> None:
        first_error: Exception | None = None
        for index in reversed(range(len(self._stages))):
            if index in self._closed_stage_indexes:
                continue
            stage = self._stages[index]
            close = getattr(stage, "aclose", None)
            if close is None:
                self._closed_stage_indexes.add(index)
                continue
            try:
                await close()
            except Exception as exc:
                if first_error is None:
                    first_error = exc
            else:
                self._closed_stage_indexes.add(index)
        if first_error is not None:
            raise first_error


def build_pipeline(settings: Settings) -> Pipeline:
    del settings
    from github_deep_search.stages.analysis import AnalysisStage
    from github_deep_search.stages.discovery import DiscoveryStage
    from github_deep_search.stages.evidence import EvidenceStage
    from github_deep_search.stages.input import InputStage
    from github_deep_search.stages.parse import ParseStage
    from github_deep_search.stages.report import ReportStage

    discovery = DiscoveryStage()
    return Pipeline(
        (
            InputStage(),
            ParseStage(),
            discovery,
            EvidenceStage(supplemental_discovery=discovery),
            AnalysisStage(),
            ReportStage(),
        )
    )
