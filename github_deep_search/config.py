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


def _positive_int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


@dataclass(frozen=True)
class Settings:
    github_token: str | None
    llm_api_key: str | None
    llm_base_url: str
    llm_model: str
    llm_thinking: str | None
    llm_reasoning_effort: str | None
    max_github_requests: int
    max_candidates: int
    max_evidence_repositories: int
    run_timeout_seconds: int

    @property
    def has_github(self) -> bool:
        return bool(self.github_token and self.github_token.strip())

    @property
    def has_llm(self) -> bool:
        return bool(self.llm_api_key and self.llm_api_key.strip())


def get_settings() -> Settings:
    _load_env()
    return Settings(
        github_token=os.getenv("GITHUB_TOKEN") or None,
        llm_api_key=os.getenv("LLM_API_KEY") or None,
        llm_base_url=(os.getenv("LLM_BASE_URL") or "https://api.openai.com/v1").rstrip("/"),
        llm_model=os.getenv("LLM_MODEL") or "gpt-5-mini",
        llm_thinking=os.getenv("LLM_THINKING") or None,
        llm_reasoning_effort=os.getenv("LLM_REASONING_EFFORT") or None,
        max_github_requests=_positive_int_env("MAX_GITHUB_REQUESTS", 200),
        max_candidates=_positive_int_env("MAX_CANDIDATES", 80),
        max_evidence_repositories=_positive_int_env("MAX_EVIDENCE_REPOSITORIES", 12),
        run_timeout_seconds=_positive_int_env("RUN_TIMEOUT_SECONDS", 600),
    )
