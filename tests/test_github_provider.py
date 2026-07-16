from __future__ import annotations

import asyncio

import httpx
import pytest

from github_deep_search.config import Settings
from github_deep_search.engine import DeepSearchEngine
from github_deep_search.models import BudgetUsage
from github_deep_search.providers.github import (
    GitHubAuthenticationError,
    GitHubClient,
    GitHubProviderError,
    GitHubRateLimitError,
)
from github_deep_search.run_trace import SearchRunFailed


async def _client_with_transport(handler) -> tuple[GitHubClient, BudgetUsage]:
    usage = BudgetUsage()
    client = GitHubClient("authenticated-test-token", usage)
    await client.client.aclose()
    client.client = httpx.AsyncClient(
        base_url="https://api.github.com",
        transport=httpx.MockTransport(handler),
    )
    return client, usage


def test_github_client_rejects_missing_authentication() -> None:
    with pytest.raises(GitHubAuthenticationError, match="anonymous fallback is disabled"):
        GitHubClient("", BudgetUsage())


def test_engine_fails_before_llm_work_when_github_token_is_missing() -> None:
    settings = Settings(
        github_token=None,
        tavily_api_key=None,
        llm_api_key="configured-test-llm",
        llm_base_url="https://provider.example/v1",
        llm_model="model",
        llm_thinking=None,
        llm_reasoning_effort=None,
        max_github_requests=20,
        max_tavily_credits=0,
        max_candidates=10,
        max_deep_analyze_repos=3,
        task_deadline_seconds=30,
        llm_input_usd_per_1m=0.0,
        llm_output_usd_per_1m=0.0,
        tavily_usd_per_credit=0.0,
    )

    with pytest.raises(SearchRunFailed) as captured:
        asyncio.run(DeepSearchEngine(settings).run("current request"))

    artifact = captured.value.artifact
    assert artifact.failure.stage == "discovery"
    assert artifact.failure.retryable is False
    assert artifact.usage.llm_input_tokens == 0
    assert artifact.run_trace.stages[0].status == "not_started"
    assert artifact.run_trace.stages[1].status == "failed"


def test_github_client_retries_one_transient_server_error_without_failure_event() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(502, request=request)
        return httpx.Response(200, request=request, json={"ok": True})

    async def run() -> tuple[dict[str, object] | None, BudgetUsage]:
        client, usage = await _client_with_transport(handler)
        try:
            return await client._get("/transient"), usage
        finally:
            await client.close()

    result, usage = asyncio.run(run())

    assert result == {"ok": True}
    assert calls == 2
    assert usage.github_requests == 2
    assert usage.provider_events == []


@pytest.mark.parametrize(
    ("status", "headers", "error_type", "retryable", "event_kind"),
    [
        (401, {}, GitHubAuthenticationError, False, "authentication"),
        (403, {}, GitHubProviderError, False, "authorization"),
        (403, {"x-ratelimit-remaining": "0"}, GitHubRateLimitError, True, "rate_limit"),
        (403, {"retry-after": "61"}, GitHubRateLimitError, True, "rate_limit"),
        (429, {"retry-after": "61"}, GitHubRateLimitError, True, "rate_limit"),
    ],
)
def test_github_auth_and_rate_failures_are_not_converted_to_empty_results(
    status: int,
    headers: dict[str, str],
    error_type: type[GitHubProviderError],
    retryable: bool,
    event_kind: str,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, headers=headers, request=request, json={"message": "provider failure"})

    async def run() -> tuple[GitHubProviderError, BudgetUsage]:
        client, usage = await _client_with_transport(handler)
        try:
            with pytest.raises(error_type) as captured:
                await client._get("/search/repositories", q="current request")
            return captured.value, usage
        finally:
            await client.close()

    error, usage = asyncio.run(run())

    assert error.retryable is retryable
    assert usage.provider_events[-1].kind == event_kind
    assert "anonymous fallback is disabled" in str(error)


def test_github_client_waits_for_a_short_authenticated_rate_reset(monkeypatch) -> None:
    calls = 0
    waits: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                403,
                headers={"retry-after": "0.25", "x-ratelimit-remaining": "0"},
                request=request,
            )
        return httpx.Response(200, request=request, json={"ok": True})

    async def fake_sleep(delay: float) -> None:
        waits.append(delay)

    monkeypatch.setattr("github_deep_search.providers.github.asyncio.sleep", fake_sleep)

    async def run() -> tuple[dict[str, object] | None, BudgetUsage]:
        client, usage = await _client_with_transport(handler)
        try:
            return await client._get("/search/code", q="current request"), usage
        finally:
            await client.close()

    result, usage = asyncio.run(run())

    assert result == {"ok": True}
    assert calls == 2
    assert waits == [0.25]
    assert usage.provider_events == []
    assert any("retrying after 0.2s" in warning for warning in usage.warnings)
