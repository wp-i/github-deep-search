from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from github_deep_search import web
from github_deep_search.models import BudgetUsage, RunFailure
from github_deep_search.run_trace import RunTraceRecorder, SearchRunFailed, build_failure_artifact


def test_web_index_exposes_real_search_trust_contract() -> None:
    client = TestClient(web.app)
    response = client.get("/")
    styles = client.get("/static/styles.css")
    script = client.get("/static/app.js")

    assert response.status_code == 200
    assert styles.status_code == 200
    assert script.status_code == 200

    html = response.text
    assert "开源项目调研工作台" in html
    assert "实时检索 GitHub" in html
    assert "基于 GitHub、README 与源码证据检索" in html
    assert "Real GitHub search" in html
    assert "No demo data" in html
    assert "等待启动调研分析" in html
    assert 'id="profile"' not in html
    assert 'id="keyStatus"' in html
    assert 'id="query"' in html
    assert 'id="demo"' not in html
    assert 'data-example=' not in html
    assert 'id="progressFill"' in html
    assert 'id="copyMarkdown"' in html
    assert 'id="downloadJson"' in html
    assert 'class="run-rail"' in html
    assert 'class="right-column"' in html
    assert 'content="width=1180"' in html
    assert "/static/styles.css?v=20260716-01" in html
    assert "/static/app.js?v=20260716-01" in html

    assert "radial-gradient" not in styles.text
    assert "border-radius: 8px" in styles.text
    assert "letter-spacing: 0" in styles.text
    assert ".run-rail {\n  display: flex;" in styles.text
    assert "margin-top: auto;" in styles.text
    assert "loadStatus" in script.text
    assert "demo" not in script.text.lower()
    assert "复制 Markdown" in script.text
    assert "Research in progress" in script.text
    assert "report-loading" in script.text
    assert "error.message" in script.text
    assert "renderDecisionBrief" not in script.text
    assert "renderEvidenceReferences" not in script.text
    assert "escapeHtml" in script.text
    assert ".decision-brief" not in styles.text
    assert ".evidence-references" not in styles.text
    assert "--report-accent: #1d4ed8" in styles.text
    assert ".report h3 + ul" in styles.text


def test_status_reports_key_presence(monkeypatch) -> None:
    monkeypatch.setattr(
        web,
        "get_settings",
        lambda: SimpleNamespace(github_token="ghp_example", has_llm=True, has_tavily=False),
    )

    client = TestClient(web.app)
    response = client.get("/api/status")

    assert response.status_code == 200
    assert response.json() == {
        "hasGithubToken": True,
        "hasLlmKey": True,
        "hasTavilyKey": False,
        "requiredKeys": ["GITHUB_TOKEN", "LLM_API_KEY"],
    }


def test_web_search_calls_deep_search_with_query(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_deep_search(query: str):
        calls.append(query)
        return SimpleNamespace(query=query)

    monkeypatch.setattr(web, "deep_search", fake_deep_search)
    monkeypatch.setattr(web, "report_to_dict", lambda report, include_html=False: {"reportHtml": "<p>ok</p>"})
    client = TestClient(web.app)

    response = client.post("/api/search", json={"query": "find an accessible diagram editor"})

    assert response.status_code == 200
    assert calls == ["find an accessible diagram editor"]


def test_web_search_returns_serialized_failure_trace(monkeypatch) -> None:
    trace = RunTraceRecorder()
    trace.begin("parse", {"query": 1})
    failure = RunFailure(
        kind="provider",
        stage="parse",
        exception_type="TimeoutError",
        message="A configured provider failed during parse.",
        retryable=True,
    )
    trace.fail(failure)
    artifact = build_failure_artifact("current request", BudgetUsage(), trace.build(), failure)

    async def failed_search(query: str):
        raise SearchRunFailed(artifact)

    monkeypatch.setattr(web, "deep_search", failed_search)
    response = TestClient(web.app).post("/api/search", json={"query": "current request"})

    assert response.status_code == 502
    assert response.json()["failureArtifact"]["runTrace"]["status"] == "failed"
    assert response.json()["failureArtifact"]["runTrace"]["stages"][1]["status"] == "not_started"
