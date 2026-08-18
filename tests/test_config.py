from __future__ import annotations

from github_deep_search import config


def test_settings_use_only_confirmed_provider_and_runtime_fields(monkeypatch) -> None:
    monkeypatch.setattr(config, "_load_env", lambda: None)
    monkeypatch.setenv("GITHUB_TOKEN", "github-token")
    monkeypatch.setenv("LLM_API_KEY", "llm-key")
    monkeypatch.setenv("LLM_BASE_URL", "https://provider.example/v1/")
    monkeypatch.setenv("LLM_MODEL", "model")
    monkeypatch.setenv("RUN_TIMEOUT_SECONDS", "900")

    settings = config.get_settings()

    assert settings.has_github is True
    assert settings.has_llm is True
    assert settings.llm_base_url == "https://provider.example/v1"
    assert settings.run_timeout_seconds == 900
    assert not hasattr(settings, "tavily_api_key")
    assert not hasattr(settings, "task_deadline_seconds")


def test_invalid_positive_limits_use_contract_defaults(monkeypatch) -> None:
    monkeypatch.setattr(config, "_load_env", lambda: None)
    monkeypatch.setenv("RUN_TIMEOUT_SECONDS", "0")
    monkeypatch.setenv("MAX_CANDIDATES", "not-an-integer")

    settings = config.get_settings()

    assert settings.run_timeout_seconds == 600
    assert settings.max_candidates == 80


def test_whitespace_only_credentials_are_not_configured() -> None:
    configured = config.Settings(
        github_token="  ",
        llm_api_key="\t",
        llm_base_url="https://provider.example/v1",
        llm_model="model",
        llm_thinking=None,
        llm_reasoning_effort=None,
        max_github_requests=200,
        max_candidates=80,
        max_evidence_repositories=12,
        run_timeout_seconds=600,
    )

    assert configured.has_github is False
    assert configured.has_llm is False
