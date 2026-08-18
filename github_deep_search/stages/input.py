from __future__ import annotations

import re
from urllib.parse import urlsplit

from github_deep_search.models import ValidatedInput
from github_deep_search.pipeline import PipelineContext, PipelineFailure


_CHINESE_CHARACTER = re.compile(
    r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\U00020000-\U0002fa1f\U00030000-\U000323af]"
)


class InputStage:
    name = "input"

    async def execute(self, context: PipelineContext) -> None:
        raw_input = context.request.raw_input
        if not raw_input or not raw_input.strip() or len(raw_input) > 2000:
            raise PipelineFailure(
                "invalid_input",
                "The request must contain 1 to 2000 characters of meaningful text.",
            )

        settings = context.settings
        if not settings.has_github:
            raise PipelineFailure(
                "github_token_missing",
                "GITHUB_TOKEN must be configured before starting a run.",
            )
        if not settings.has_llm:
            raise PipelineFailure(
                "llm_api_key_missing",
                "LLM_API_KEY must be configured before starting a run.",
            )

        parsed_base_url = urlsplit(settings.llm_base_url)
        if parsed_base_url.scheme not in {"http", "https"} or not parsed_base_url.netloc:
            raise PipelineFailure(
                "llm_base_url_invalid",
                "LLM_BASE_URL must be a valid HTTP or HTTPS URL.",
            )
        if not settings.llm_model.strip():
            raise PipelineFailure(
                "llm_model_missing",
                "LLM_MODEL must be configured before starting a run.",
            )

        report_language = "zh" if _CHINESE_CHARACTER.search(raw_input) else "en"
        context.validated_input = ValidatedInput(
            raw_input=raw_input,
            report_language=report_language,
        )
