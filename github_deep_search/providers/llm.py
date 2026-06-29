from __future__ import annotations

import json
from typing import Any

import httpx

from github_deep_search.models import BudgetUsage
from github_deep_search.utils import estimate_tokens


class LLMClient:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        usage: BudgetUsage,
        timeout: float = 45.0,
        thinking: str | None = None,
        reasoning_effort: str | None = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.usage = usage
        self.thinking = thinking
        self.reasoning_effort = reasoning_effort
        self.client = httpx.AsyncClient(timeout=timeout)

    async def close(self) -> None:
        await self.client.aclose()

    async def chat(self, system: str, user: str, temperature: float = 0.2) -> str:
        self.usage.llm_input_tokens += estimate_tokens(system) + estimate_tokens(user)
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
        }
        if self.thinking:
            payload["thinking"] = {"type": self.thinking}
        if self.reasoning_effort:
            payload["reasoning_effort"] = self.reasoning_effort
        try:
            response = await self.client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
            content = data["choices"][0]["message"]["content"]
            self.usage.llm_output_tokens += estimate_tokens(content)
            return content
        except Exception as exc:
            self.usage.warnings.append(f"LLM request failed: {exc}")
            return ""

    async def json_chat(self, system: str, user: str) -> dict[str, Any] | None:
        content = await self.chat(system, user)
        if not content:
            return None
        cleaned = content.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            if cleaned.lower().startswith("json"):
                cleaned = cleaned[4:].strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            start = cleaned.find("{")
            end = cleaned.rfind("}")
            if start >= 0 and end > start:
                try:
                    return json.loads(cleaned[start : end + 1])
                except json.JSONDecodeError:
                    pass
        self.usage.warnings.append("LLM did not return valid JSON; using heuristic fallback.")
        return None
