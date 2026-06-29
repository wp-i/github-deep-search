from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from github_deep_search import web


def test_web_index_exposes_real_search_trust_contract() -> None:
    client = TestClient(web.app)
    response = client.get("/")
    styles = client.get("/static/styles.css")
    script = client.get("/static/app.js")

    assert response.status_code == 200
    assert styles.status_code == 200
    assert script.status_code == 200

    html = response.text
    assert "从一个产品想法" in html
    assert "无内置 Demo 结果" in html
    assert "无假仓库排行" in html
    assert "无静态业务词表" in html
    assert 'id="keyStatus"' in html
    assert 'id="query"' in html
    assert 'id="demo"' not in html
    assert 'data-example=' not in html
    assert 'id="progressFill"' in html
    assert 'id="copyMarkdown"' in html
    assert 'id="downloadJson"' in html
    assert "/static/styles.css?v=20260630-2" in html
    assert "/static/app.js?v=20260630-2" in html

    assert "radial-gradient" not in styles.text
    assert "border-radius: 8px" in styles.text
    assert "letter-spacing: 0" in styles.text
    assert 'mode: "detailed"' in script.text
    assert 'budget: "continue"' in script.text
    assert "loadStatus" in script.text
    assert "demo" not in script.text.lower()
    assert "复制 Markdown" in script.text
    assert "error.message" in script.text


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
        "recommendedKeys": ["GITHUB_TOKEN", "LLM_API_KEY"],
    }


def test_deep_web_request_reuses_default_report_as_baseline(monkeypatch) -> None:
    calls: list[tuple[str, str, str, object | None]] = []

    async def fake_deep_search(query: str, mode: str, budget: str, baseline=None):
        report = SimpleNamespace(query=query, marker=f"{mode}/{budget}")
        calls.append((query, mode, budget, baseline))
        return report

    monkeypatch.setattr(web, "deep_search", fake_deep_search)
    monkeypatch.setattr(
        web,
        "report_to_dict",
        lambda report, include_html=False: {"reportHtml": f"<p>{report.marker}</p>"},
    )
    web._baseline_reports.clear()
    client = TestClient(web.app)
    payload = {"query": "find an accessible diagram editor", "mode": "light", "budget": "standard"}

    assert client.post("/api/search", json=payload).status_code == 200
    payload["budget"] = "high"
    assert client.post("/api/search", json=payload).status_code == 200

    assert calls[0][3] is None
    assert calls[1][3] is not None
    assert calls[1][3].marker == "light/standard"


def test_web_request_defaults_to_expanded_complete_research(monkeypatch) -> None:
    calls: list[tuple[str, str, str]] = []

    async def fake_deep_search(query: str, mode: str, budget: str, baseline=None):
        calls.append((query, mode, budget))
        return SimpleNamespace(query=query, marker="complete")

    monkeypatch.setattr(web, "deep_search", fake_deep_search)
    monkeypatch.setattr(web, "report_to_dict", lambda report, include_html=False: {"reportHtml": "<p>ok</p>"})
    client = TestClient(web.app)

    response = client.post("/api/search", json={"query": "find a useful project"})

    assert response.status_code == 200
    assert calls == [("find a useful project", "detailed", "continue")]
