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
@pytest.mark.parametrize("viewport", [{"width": 1440, "height": 900}, {"width": 390, "height": 844}])
def test_rendered_first_run_flow_is_usable(viewport: dict[str, int]) -> None:
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
                    "repo": "sample/accessibility-tool",
                    "url": "https://github.com/sample/accessibility-tool",
                    "score": 78,
                    "recommendation": "Useful as a focused implementation reference.",
                    "isReferenceCandidate": False,
                    "confidenceLevel": "reliable",
                    "referenceReason": "",
                }
            ],
            "reportMarkdown": (
                "# Research report\n\n"
                "## Summary\n\nFound one strong reference project.\n\n"
                "### 1. sample/accessibility-tool\n\n"
                "- Address: https://github.com/sample/accessibility-tool\n"
                "- Verdict: suitable reference.\n"
            ),
            "reportHtml": (
                "<h1>Research report</h1><h2>Summary</h2><p>Found one strong reference project.</p>"
                "<h3>1. sample/accessibility-tool</h3>"
                "<ul><li><a href='https://github.com/sample/accessibility-tool'>打开 GitHub 仓库</a></li>"
                "<li>Verdict: suitable reference.</li></ul>"
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
            page = browser.new_page(viewport=viewport)
            page.route(
                "**/api/search",
                lambda route: route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(payload),
                ),
            )
            page.goto(url)

            assert "从一个产品想法" in page.locator(".intro h1").inner_text()
            assert page.locator("#keyStatus").is_visible()
            assert page.locator(".query-panel").is_visible()
            assert page.locator("#demo").count() == 0

            page.locator("#query").fill(
                "Find a real open-source browser extension that summarizes web pages and syncs notes."
            )
            assert "browser extension" in page.locator("#query").input_value()
            page.locator("#run").click()
            page.locator("#results.active").wait_for()

            report_text = page.locator("#report").inner_text()
            assert page.locator("#report h1").inner_text() == "Research report"
            assert "sample/accessibility-tool" in report_text
            assert page.locator("#copyMarkdown").is_visible()
            assert page.locator("#downloadJson").is_visible()
            assert page.locator("#progressFill").get_attribute("style") == "width: 100%;"
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth") is True

            visual_contract = page.evaluate(
                """() => {
                    const panel = getComputedStyle(document.querySelector('.query-panel'));
                    const report = getComputedStyle(document.querySelector('#report'));
                    const rect = (selector) => document.querySelector(selector).getBoundingClientRect();
                    return {
                        panelRadius: parseFloat(panel.borderRadius),
                        reportRadius: parseFloat(report.borderRadius),
                        reportFontSize: parseFloat(report.fontSize),
                        query: rect('.query-panel').width,
                        runPanel: rect('.run-panel').width,
                        runButton: rect('#run').width,
                        queryInner: rect('.query-panel').width - 36,
                        viewportWidth: document.documentElement.clientWidth
                    };
                }"""
            )
            assert visual_contract["panelRadius"] <= 8
            assert visual_contract["reportRadius"] <= 8
            assert visual_contract["reportFontSize"] >= 16
            if viewport["width"] > 820:
                assert visual_contract["query"] > visual_contract["runPanel"]
            else:
                assert visual_contract["runButton"] >= visual_contract["queryInner"] * 0.98
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=10)
