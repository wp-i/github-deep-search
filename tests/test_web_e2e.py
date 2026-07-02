from __future__ import annotations

import json
import socket
import subprocess
import sys
import time

import httpx
import pytest


playwright = pytest.importorskip("playwright.sync_api")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_server(url: str) -> None:
    for _ in range(50):
        try:
            if httpx.get(url, timeout=0.5).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    raise AssertionError("Web server did not start")


@pytest.mark.e2e
def test_rendered_desktop_first_run_flow_is_usable() -> None:
    port = _free_port()
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "github_deep_search.web:app", "--host", "127.0.0.1", "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        url = f"http://127.0.0.1:{port}"
        _wait_for_server(url)
        payload = {
            "topProjects": [
                {
                    "repo": "Textualize/rich",
                    "url": "https://github.com/Textualize/rich",
                    "score": 78,
                    "recommendation": "适合作为终端 UI 与富文本输出能力的优先候选。",
                    "isReferenceCandidate": False,
                    "confidenceLevel": "reliable",
                    "referenceReason": "",
                }
            ],
            "reportMarkdown": (
                "# 调研结论\n\n"
                "## 一句话判断\n\nTextualize/rich 是最相关的候选项目。\n\n"
                "### 1. Textualize/rich\n\n"
                "- 地址：https://github.com/Textualize/rich\n"
                "- 判断：可作为终端 UI 能力的优先候选。\n"
            ),
            "reportHtml": (
                "<h1>调研结论</h1><h2>一句话判断</h2><p>Textualize/rich 是最相关的候选项目。</p>"
                "<h3>1. Textualize/rich</h3>"
                "<ul><li><a href='https://github.com/Textualize/rich'>打开 GitHub 仓库</a></li>"
                "<li>判断：可作为终端 UI 能力的优先候选。</li></ul>"
            ),
            "usage": {"elapsedMs": 1200},
            "raw": {
                "candidate_count": 8,
                "top_projects_returned": 1,
                "search_completeness": "complete",
            },
        }
        with playwright.sync_playwright() as browser_api:
            try:
                browser = browser_api.chromium.launch(headless=True)
            except playwright.Error as exc:
                pytest.skip(f"Playwright Chromium is not installed: {exc}")
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.route(
                "**/api/search",
                lambda route: route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(payload),
                ),
            )
            page.goto(url)

            assert page.locator("#hero-title").inner_text() == "开源项目调研工作台"
            assert page.locator("#keyStatus").is_visible()
            assert page.locator(".query-panel").is_visible()
            assert page.locator(".run-rail").is_visible()
            assert page.locator(".right-column").is_visible()
            assert page.locator("#emptyState").is_visible()
            assert page.locator("#demo").count() == 0

            page.locator("#query").fill(
                "找一个开源 Python 终端 UI 库，支持表格、进度条、Markdown 渲染和富文本样式。"
            )
            assert "终端 UI" in page.locator("#query").input_value()
            page.locator("#run").click()
            page.locator("#results.active").wait_for()

            report_text = page.locator("#report").inner_text()
            assert page.locator("#report h1").inner_text() == "调研结论"
            assert "Textualize/rich" in report_text
            assert "优先候选" in report_text
            assert page.locator("#copyMarkdown").is_visible()
            assert page.locator("#downloadJson").is_visible()
            assert page.locator("#emptyState").is_visible() is False
            assert page.locator("#progressFill").get_attribute("style") == "width: 100%;"
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth") is True

            visual_contract = page.evaluate(
                """() => {
                    const panel = getComputedStyle(document.querySelector('.query-panel'));
                    const report = getComputedStyle(document.querySelector('#report'));
                    const rect = (selector) => document.querySelector(selector).getBoundingClientRect();
                    return {
                        panelRadius: parseFloat(panel.borderRadius),
                        reportFontSize: parseFloat(report.fontSize),
                        shell: rect('.workspace-shell').width,
                        leftColumn: rect('.left-column').width,
                        rightColumn: rect('.right-column').width,
                        query: rect('.query-panel').width,
                        textarea: rect('#query').width,
                        rail: rect('.run-rail').width,
                        stage: rect('.stage').width,
                        runButton: rect('#run').width,
                        viewportWidth: document.documentElement.clientWidth
                    };
                }"""
            )
            assert visual_contract["panelRadius"] == 16
            assert visual_contract["reportFontSize"] >= 16
            assert visual_contract["stage"] == visual_contract["viewportWidth"] - 96
            assert visual_contract["shell"] == visual_contract["viewportWidth"] - 96
            assert 560 <= visual_contract["leftColumn"] <= 570
            assert 730 <= visual_contract["rightColumn"] <= 740
            assert visual_contract["query"] == visual_contract["leftColumn"]
            assert visual_contract["textarea"] == visual_contract["leftColumn"] - 50
            assert visual_contract["rail"] == visual_contract["leftColumn"]
            assert visual_contract["runButton"] == visual_contract["textarea"]
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=10)
