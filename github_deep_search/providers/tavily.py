from __future__ import annotations

from typing import Any

import httpx

from github_deep_search.models import BudgetUsage, ProviderEvent


class TavilyClient:
    def __init__(self, api_key: str, usage: BudgetUsage, timeout: float = 20.0) -> None:
        self.api_key = api_key
        self.usage = usage
        self.client = httpx.AsyncClient(base_url="https://api.tavily.com", timeout=timeout)

    async def close(self) -> None:
        await self.client.aclose()

    async def search(self, query: str, max_results: int = 5) -> list[dict[str, Any]]:
        if self.usage.tavily_credits >= 4:
            self.usage.warnings.append("Tavily budget reached; skipped extra web search.")
            self.usage.provider_events.append(
                ProviderEvent("tavily", "search", "limited", "budget_limit")
            )
            return []
        payload = {
            "api_key": self.api_key,
            "query": query,
            "search_depth": "basic",
            "max_results": max_results,
            "include_answer": False,
            "include_raw_content": False,
            "include_images": False,
            "include_usage": True,
        }
        try:
            response = await self.client.post("/search", json=payload)
            response.raise_for_status()
            data = response.json()
            usage = data.get("usage") or {}
            credits = usage.get("credits") or usage.get("total_credits") or 1
            try:
                self.usage.tavily_credits += int(credits)
            except (TypeError, ValueError):
                self.usage.tavily_credits += 1
            return list(data.get("results") or [])
        except httpx.HTTPError as exc:
            self.usage.warnings.append(f"Tavily search failed: {exc}")
            self.usage.provider_events.append(
                ProviderEvent("tavily", "search", "failed", type(exc).__name__)
            )
            return []
