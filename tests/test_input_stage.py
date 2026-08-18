from __future__ import annotations

import pytest

from github_deep_search.config import Settings
from github_deep_search.models import RunRequest
from github_deep_search.pipeline import PipelineContext, PipelineFailure
from github_deep_search.stages.input import InputStage


def settings(
    *,
    github_token: str | None = "github-token",
    llm_api_key: str | None = "llm-key",
    llm_base_url: str = "https://provider.example/v1",
    llm_model: str = "model",
) -> Settings:
    return Settings(
        github_token=github_token,
        llm_api_key=llm_api_key,
        llm_base_url=llm_base_url,
        llm_model=llm_model,
        llm_thinking=None,
        llm_reasoning_effort=None,
        max_github_requests=200,
        max_candidates=80,
        max_evidence_repositories=12,
        run_timeout_seconds=600,
    )


async def execute(raw_input: str, configured: Settings | None = None) -> PipelineContext:
    context = PipelineContext(
        run_id="run-id",
        request=RunRequest(raw_input=raw_input),
        settings=configured or settings(),
    )
    await InputStage().execute(context)
    return context


@pytest.mark.asyncio
async def test_input_stage_preserves_raw_input_and_detects_report_language() -> None:
    mixed = "  Build 一个 MCP client  "
    mixed_context = await execute(mixed)
    english_context = await execute("Build an MCP client")

    assert mixed_context.validated_input is not None
    assert mixed_context.validated_input.raw_input == mixed
    assert mixed_context.validated_input.report_language == "zh"
    assert english_context.validated_input is not None
    assert english_context.validated_input.raw_input == "Build an MCP client"
    assert english_context.validated_input.report_language == "en"

    extension_context = await execute("Build \U00020000 client")
    assert extension_context.validated_input is not None
    assert extension_context.validated_input.report_language == "zh"


@pytest.mark.asyncio
@pytest.mark.parametrize("raw_input", ["", "   ", "x" * 2001])
async def test_input_stage_rejects_invalid_input_without_rewriting_it(raw_input: str) -> None:
    with pytest.raises(PipelineFailure) as caught:
        await execute(raw_input)

    assert caught.value.code == "invalid_input"
    if raw_input:
        assert raw_input not in caught.value.public_message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("configured", "code"),
    [
        (settings(github_token=None), "github_token_missing"),
        (settings(github_token="  "), "github_token_missing"),
        (settings(llm_api_key=None), "llm_api_key_missing"),
        (settings(llm_api_key="\t"), "llm_api_key_missing"),
        (settings(llm_base_url="not-a-url"), "llm_base_url_invalid"),
        (settings(llm_model=" "), "llm_model_missing"),
    ],
)
async def test_input_stage_rejects_missing_or_invalid_provider_configuration(
    configured: Settings,
    code: str,
) -> None:
    with pytest.raises(PipelineFailure) as caught:
        await execute("sample request", configured)

    assert caught.value.code == code
    assert "github-token" not in caught.value.public_message
    assert "llm-key" not in caught.value.public_message
