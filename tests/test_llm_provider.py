from __future__ import annotations

import asyncio
import json

import httpx

from github_deep_search.models import Usage
from github_deep_search.providers.llm import LLMClient


def test_chat_uses_openai_compatible_endpoint_and_real_usage() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["authorization"] = request.headers["Authorization"]
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 3},
            },
        )

    async def run() -> None:
        usage = Usage()
        client = LLMClient("test-key", "https://provider.example/v1", "model", usage)
        await client.client.aclose()
        client.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            result = await client.chat("system", "user", operation="baseline")
        finally:
            await client.close()

        assert result == "ok"
        assert usage.llm_input_tokens == 12
        assert usage.llm_output_tokens == 3
        assert usage.llm_total_tokens == 15

    asyncio.run(run())
    assert captured["path"] == "/v1/chat/completions"
    assert captured["authorization"] == "Bearer test-key"
    assert captured["payload"] == {
        "model": "model",
        "messages": [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "user"},
        ],
        "temperature": 0.2,
    }


def test_json_chat_extracts_fenced_json_without_semantic_retry() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "json: {\"value\": 1}"}}]},
        )

    async def run() -> None:
        usage = Usage()
        client = LLMClient("test-key", "https://provider.example/v1", "model", usage)
        await client.client.aclose()
        client.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            result = await client.json_chat("system", "user")
        finally:
            await client.close()
        assert result == {"value": 1}

    asyncio.run(run())
    assert calls == 1


def test_invalid_json_fails_without_literal_fallback() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "not json"}}]},
        )

    async def run() -> None:
        usage = Usage()
        client = LLMClient("test-key", "https://provider.example/v1", "model", usage)
        await client.client.aclose()
        client.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            result = await client.json_chat("system", "user")
        finally:
            await client.close()
        assert result is None
        assert usage.provider_events[-1].kind == "invalid_response"
        assert all("literal" not in warning.lower() for warning in usage.warnings)

    asyncio.run(run())


def test_provider_error_redacts_api_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="rejected test-secret")

    async def run() -> None:
        usage = Usage()
        client = LLMClient("test-secret", "https://provider.example/v1", "model", usage)
        await client.client.aclose()
        client.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            result = await client.chat("system", "user")
        finally:
            await client.close()

        assert result == ""
        assert client.last_failure is not None
        assert "test-secret" not in "\n".join(usage.warnings)
        assert "[redacted]" in "\n".join(usage.warnings)

    asyncio.run(run())
