from __future__ import annotations

import json
from typing import Any

import httpx

from github_deep_search.models import BudgetUsage, ProviderEvent
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

    async def chat(
        self,
        system: str,
        user: str,
        temperature: float = 0.2,
        *,
        operation: str = "chat",
    ) -> str:
        estimated_input_tokens = estimate_tokens(system) + estimate_tokens(user)
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
            usage_data = data.get("usage") if isinstance(data, dict) else None
            prompt_tokens = self._usage_int(usage_data, "prompt_tokens")
            completion_tokens = self._usage_int(usage_data, "completion_tokens")
            if prompt_tokens is None or completion_tokens is None:
                self.usage.llm_input_tokens += estimated_input_tokens
                self.usage.llm_output_tokens += estimate_tokens(content)
                self.usage.llm_token_estimated = True
            else:
                self.usage.llm_input_tokens += prompt_tokens
                self.usage.llm_output_tokens += completion_tokens
            return content
        except Exception as exc:
            self.usage.llm_input_tokens += estimated_input_tokens
            self.usage.llm_token_estimated = True
            if isinstance(exc, httpx.HTTPStatusError):
                response_text = " ".join(exc.response.text.split()).strip()
                if self.api_key:
                    response_text = response_text.replace(self.api_key, "[redacted]")
                if len(response_text) > 1200:
                    response_text = f"{response_text[:1200]}...[truncated]"
                detail = (
                    f"status={exc.response.status_code}; operation={operation}; model={self.model}; "
                    f"input_chars={len(system) + len(user)}; "
                    f"estimated_input_tokens={estimated_input_tokens}; "
                    f"response={response_text or '[empty]'}"
                )
            else:
                detail = str(exc).strip() or repr(exc)
            self.usage.warnings.append(
                f"LLM request failed ({type(exc).__name__}): {detail}"
            )
            self.usage.provider_events.append(
                ProviderEvent("llm", operation, "failed", type(exc).__name__)
            )
            return ""

    @staticmethod
    def _usage_int(usage_data: object, key: str) -> int | None:
        if not isinstance(usage_data, dict):
            return None
        value = usage_data.get(key)
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        return None

    async def json_chat(
        self,
        system: str,
        user: str,
        *,
        operation: str = "chat",
    ) -> dict[str, Any] | None:
        # Structured planning and evidence decisions are contracts, not creative
        # prose. Use deterministic sampling so repeated runs do not start from
        # materially different search plans merely because of model temperature.
        content = await self.chat(system, user, temperature=0.0, operation=operation)
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
        self.usage.warnings.append("LLM did not return valid JSON; using the literal request plan.")
        self.usage.provider_events.append(
            ProviderEvent("llm", "json_chat", "failed", "invalid_response")
        )
        return None
