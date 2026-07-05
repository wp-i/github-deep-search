from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _load_env() -> None:
    cwd_env = Path.cwd() / ".env"
    user_keys_env = Path.cwd() / "config" / "user_keys.env"
    if cwd_env.exists():
        load_dotenv(cwd_env)
    else:
        load_dotenv()
    if user_keys_env.exists():
        load_dotenv(user_keys_env, override=True)


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return float(value)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    github_token: str | None
    tavily_api_key: str | None
    llm_api_key: str | None
    llm_base_url: str
    llm_model: str
    llm_thinking: str | None
    llm_reasoning_effort: str | None
    max_github_requests: int
    max_tavily_credits: int
    max_candidates: int
    max_deep_analyze_repos: int
    task_deadline_seconds: int
    llm_input_usd_per_1m: float
    llm_output_usd_per_1m: float
    tavily_usd_per_credit: float

    @property
    def has_llm(self) -> bool:
        return bool(self.llm_api_key)

    @property
    def has_tavily(self) -> bool:
        return bool(self.tavily_api_key)


def get_settings() -> Settings:
    _load_env()
    return Settings(
        github_token=os.getenv("GITHUB_TOKEN") or None,
        tavily_api_key=os.getenv("TAVILY_API_KEY") or None,
        llm_api_key=os.getenv("LLM_API_KEY") or None,
        llm_base_url=(os.getenv("LLM_BASE_URL") or "https://api.openai.com/v1").rstrip("/"),
        llm_model=os.getenv("LLM_MODEL") or "gpt-5-mini",
        llm_thinking=os.getenv("LLM_THINKING") or None,
        llm_reasoning_effort=os.getenv("LLM_REASONING_EFFORT") or None,
        max_github_requests=_int_env("MAX_GITHUB_REQUESTS", 200),
        max_tavily_credits=_int_env("MAX_TAVILY_CREDITS", 4),
        max_candidates=_int_env("MAX_CANDIDATES", 80),
        max_deep_analyze_repos=_int_env("MAX_DEEP_ANALYZE_REPOS", 3),
        task_deadline_seconds=_int_env("TASK_DEADLINE_SECONDS", 70),
        llm_input_usd_per_1m=_float_env("LLM_INPUT_USD_PER_1M", 0.0),
        llm_output_usd_per_1m=_float_env("LLM_OUTPUT_USD_PER_1M", 0.0),
        tavily_usd_per_credit=_float_env("TAVILY_USD_PER_CREDIT", 0.008),
    )
