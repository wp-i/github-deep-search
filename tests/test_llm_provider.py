from __future__ import annotations

import asyncio
import json

import httpx

from github_deep_search.models import BudgetUsage
from github_deep_search.providers.llm import LLMClient


def test_json_chat_uses_deterministic_sampling() -> None:
    observed: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed.update(json.loads(request.content))
        return httpx.Response(
            200,
            request=request,
            json={
                "choices": [{"message": {"content": '{"ok":true}'}}],
                "usage": {"prompt_tokens": 4, "completion_tokens": 2},
            },
        )

    async def run() -> dict[str, object] | None:
        client = LLMClient("test-key", "https://provider.example", "model", BudgetUsage())
        await client.client.aclose()
        client.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            return await client.json_chat("system", "user")
        finally:
            await client.close()

    result = asyncio.run(run())

    assert result == {"ok": True}
    assert observed["temperature"] == 0.0
