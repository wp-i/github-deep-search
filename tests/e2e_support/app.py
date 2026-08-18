from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass

from github_deep_search.config import Settings
from github_deep_search.models import STAGE_NAMES, StageName
from github_deep_search.pipeline import Pipeline, PipelineContext, PipelineFailure
from github_deep_search.stages.input import InputStage
from github_deep_search.web import create_app
from tests.fakes import fake_final_report


MODE = os.environ.get("GITHUB_DEEP_SEARCH_E2E_MODE", "complete")
RUN_NUMBER = 0


@dataclass
class ControlledStage:
    name: StageName
    behavior: str

    async def execute(self, context: PipelineContext) -> None:
        context.raise_if_cancelled()
        if self.name == "discovery":
            await context.supplemental_discovery(iteration=1)
            await context.warning("One repository query was skipped safely.", stage="discovery")
        if self.behavior == "hold" and self.name == "discovery":
            await asyncio.Event().wait()
        if self.behavior == "timeout" and self.name == "parse":
            await asyncio.Event().wait()
        if self.behavior == "fail" and self.name == "evidence":
            raise PipelineFailure("controlled_failure", "The controlled evidence stage failed safely.")
        if self.name == "report":
            language = context.validated_input.report_language if context.validated_input else "en"
            context.final_report = fake_final_report(language)
        await asyncio.sleep(0.18)


def settings() -> Settings:
    missing_credentials = MODE == "missing_credentials"
    return Settings(
        github_token=None if missing_credentials else "e2e-placeholder",
        llm_api_key="e2e-placeholder",
        llm_base_url="https://provider.invalid/v1",
        llm_model="e2e-model",
        llm_thinking=None,
        llm_reasoning_effort=None,
        max_github_requests=1,
        max_candidates=3,
        max_evidence_repositories=3,
        run_timeout_seconds=1 if MODE == "timeout" else 600,
    )


def pipeline_factory(configured: Settings) -> Pipeline:
    del configured
    global RUN_NUMBER
    RUN_NUMBER += 1
    behavior = "fail" if MODE == "fail_once" and RUN_NUMBER == 1 else MODE
    stages = [
        InputStage() if name == "input" else ControlledStage(name, behavior)
        for name in STAGE_NAMES
    ]
    return Pipeline(stages)


app = create_app(pipeline_factory=pipeline_factory, settings_provider=settings)
