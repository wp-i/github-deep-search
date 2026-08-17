from __future__ import annotations

from fastapi.testclient import TestClient

from github_deep_search import __version__
from github_deep_search import web
from github_deep_search.config import Settings


def settings(*, github: bool, llm: bool) -> Settings:
    return Settings(
        github_token="github-token" if github else None,
        llm_api_key="llm-key" if llm else None,
        llm_base_url="https://provider.example/v1",
        llm_model="model",
        llm_thinking=None,
        llm_reasoning_effort=None,
        max_github_requests=200,
        max_candidates=80,
        max_evidence_repositories=12,
        run_timeout_seconds=600,
    )


def test_status_exposes_only_baseline_state(monkeypatch) -> None:
    monkeypatch.setattr(web, "get_settings", lambda: settings(github=True, llm=False))
    response = TestClient(web.app).get("/api/status")

    assert response.status_code == 200
    assert response.json() == {
        "version": __version__,
        "hasGithubToken": True,
        "hasLlmKey": False,
        "searchAvailable": False,
        "hasActiveRun": False,
    }


def test_search_is_explicitly_unavailable() -> None:
    response = TestClient(web.app).post("/api/runs", json={"query": "sample request"})

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "development_baseline",
            "message": "The six-stage search pipeline is being rebuilt and is not available yet.",
        }
    }
    assert "report" not in response.text.lower()


def test_query_contract_keeps_2000_character_limit() -> None:
    response = TestClient(web.app).post("/api/runs", json={"query": "x" * 2001})
    assert response.status_code == 422


def test_home_page_disables_search_and_has_no_fake_progress() -> None:
    response = TestClient(web.app).get("/")

    assert response.status_code == 200
    assert "搜索功能重建中" in response.text
    assert "disabled" in response.text
    assert "setInterval" not in response.text
