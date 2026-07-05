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
    assert "/static/styles.css?v=20260703-01" in html
    assert "/static/app.js?v=20260703-01" in html

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
