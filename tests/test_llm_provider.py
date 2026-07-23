from __future__ import annotations

import asyncio
import json

import httpx

from github_deep_search.models import BudgetUsage, ProviderEvent
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


def test_http_error_retains_bounded_sanitized_provider_detail() -> None:
    usage = BudgetUsage()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            request=request,
            json={"error": {"message": f"invalid request test-key {'x' * 1400}"}},
        )

    async def run() -> dict[str, object] | None:
        client = LLMClient("test-key", "https://provider.example", "model", usage)
        await client.client.aclose()
        client.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            return await client.json_chat(
                "system",
                "user",
                operation="repository_analysis",
            )
        finally:
            await client.close()

    result = asyncio.run(run())

    assert result is None
    assert len(usage.warnings) == 1
    warning = usage.warnings[0]
    assert "status=400" in warning
    assert "operation=repository_analysis" in warning
    assert "model=model" in warning
    assert "input_chars=10" in warning
    assert "estimated_input_tokens=3" in warning
    assert 'response={"error":{"message":"invalid request [redacted]' in warning
    assert "...[truncated]" in warning
    assert "test-key" not in warning
    assert usage.provider_events == [
        ProviderEvent("llm", "repository_analysis", "failed", "HTTPStatusError")
    ]
