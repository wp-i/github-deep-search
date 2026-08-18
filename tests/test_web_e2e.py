from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest


playwright = pytest.importorskip("playwright.sync_api")


pytestmark = pytest.mark.e2e


REALISTIC_USER_INPUTS = (
    "我想在自己的 Windows 电脑上运行一个《崩坏：星穹铁道》日常自动化工具，主要是减少每天重复点击的时间。它至少应覆盖每日委托、派遣和消耗开拓力，能够识别当前游戏状态并在异常时停止；最好有可视化配置界面、运行日志和定时启动。账号或截图数据必须只保存在本机，我不接受要求把凭据上传到第三方云端的服务，也不希望使用会修改游戏客户端文件的方案。",
    "我们是一个大约十人的小团队，准备把多年积累的 Markdown 文档和附件迁移到公司内网中的知识库。希望找到能够用 Docker Compose 自托管的完整应用，支持批量导入 Markdown、中文全文搜索、按用户或空间控制访问权限，并提供 API 方便以后接入内部机器人。界面易用和备份迁移简单属于加分项；不能把核心索引或文档内容依赖在外部 SaaS 上，也不要只有聊天问答界面而缺少常规文档浏览与编辑能力的项目。",
    "I record long interviews and workshops on both macOS and Windows, often while travelling without reliable internet. I need a desktop transcription application that can run fully offline after installation, accept common audio and video files, preserve timestamps, and export SRT or VTT subtitles. Speaker separation and optional GPU acceleration would be useful, but it must still have a CPU path. I do not want a hosted API wrapper or a tool that uploads recordings to a vendor cloud, and I would prefer something a non-developer can operate without assembling a Python pipeline manually.",
    "I am building a Python ingestion service that receives JSON documents ranging from a few megabytes to several gigabytes. I need a maintained library that can validate incrementally from a stream instead of loading the entire document into memory, express nested schemas, and return actionable errors that include the failing path; custom error messages or extension hooks are highly desirable. It must be usable as a normal library without a mandatory database or hosted service. Standards compatibility is useful, but dependable streaming behaviour and clear integration examples matter more than popularity.",
    "我们正在做一个 React 后台管理界面，需要选择可复用的数据表格 component library。真实数据可能超过十万行，因此必须支持虚拟滚动，以及 server-side pagination、sorting 和 filtering；键盘导航、焦点管理和 screen reader 语义同样重要。希望它提供完整的类型声明、可控制的状态和自定义单元格渲染，样式最好能自行接管。我们不接受核心功能只能购买商业许可证后才能使用的方案，也不想要绑定某个后端服务或只能展示静态数据的组件。",
)


@pytest.fixture(scope="module")
def browser():
    with playwright.sync_playwright() as runtime:
        browser = runtime.chromium.launch(headless=True)
        yield browser
        browser.close()


@contextmanager
def running_app(mode: str) -> Iterator[str]:
    port = _available_port()
    environment = os.environ.copy()
    environment["GITHUB_DEEP_SEARCH_E2E_MODE"] = mode
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "tests.e2e_support.app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=os.fspath(_root()),
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        _wait_until_ready(process, base_url)
        yield base_url
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def test_user_can_complete_a_run_and_recover_terminal_state_after_refresh(browser) -> None:
    with running_app("complete") as base_url:
        page = browser.new_page()
        page.goto(base_url)

        query = REALISTIC_USER_INPUTS[0]
        query_input = page.get_by_label("需求描述", exact=True)
        start_button = page.get_by_role("button", name="开始搜索")
        playwright.expect(query_input).to_be_enabled()
        playwright.expect(start_button).to_be_enabled()
        assert query_input.get_attribute("maxlength") == "2000"
        start_button.click()
        playwright.expect(page.locator("#search-notice")).to_have_text(
            "请输入有意义的需求描述。"
        )
        playwright.expect(page.locator("#run-panel")).to_be_hidden()
        query_input.fill(query)
        start_button.click()

        playwright.expect(page.locator('[data-status="in_progress"]')).to_have_count(1)
        playwright.expect(page.locator("#run-panel")).to_have_attribute("aria-busy", "true")
        playwright.expect(page.locator("#report")).to_be_hidden()
        run_panel_top = page.locator("#run-panel").evaluate(
            "element => element.getBoundingClientRect().top"
        )
        assert 0 <= run_panel_top < page.evaluate("window.innerHeight")
        animation_name = page.locator('[data-status="in_progress"]').evaluate(
            "element => getComputedStyle(element, '::after').animationName"
        )
        assert animation_name != "none"
        playwright.expect(page.get_by_text("补充发现 · 第 1 次", exact=True)).to_be_visible()
        playwright.expect(page.locator("#run-warnings")).to_contain_text("query was skipped")
        playwright.expect(page.locator("#run-state")).to_have_text("已完成", timeout=10_000)
        playwright.expect(page.locator("#run-panel")).to_have_attribute("aria-busy", "false")
        assert query_input.input_value() == query
        assert page.locator('[data-status="completed"]').count() == 6
        playwright.expect(page.locator("#report")).to_be_visible()
        assert page.locator(".result-card").count() == 3
        assert page.locator(".result-card").nth(0).get_by_text("90/100", exact=True).count() == 1
        playwright.expect(
            page.locator(".result-card").nth(0).get_by_text(
                "Controlled core capability",
                exact=True,
            )
        ).to_be_visible()
        playwright.expect(
            page.locator(".result-card").nth(0).get_by_text(
                "Controlled deployment constraint",
                exact=True,
            )
        ).to_be_visible()
        assert page.locator('.result-card[data-rank="1"] a', has_text="README.md").get_attribute(
            "href"
        ) == "https://github.com/example/project-1/blob/main/README.md"
        playwright.expect(
            page.locator('.result-card[data-rank="1"] .evidence-quote')
        ).to_have_text("Controlled evidence excerpt.")
        playwright.expect(page.locator("#report-usage")).to_contain_text("150")

        page.context.grant_permissions(
            ["clipboard-read", "clipboard-write"],
            origin=base_url,
        )
        page.get_by_role("button", name="复制 Markdown").click()
        playwright.expect(page.locator("#report-action-status")).to_have_text("Markdown 已复制。")
        copied = page.evaluate("navigator.clipboard.readText()")
        assert copied.replace("\r\n", "\n") == "# Controlled report\n"

        with page.expect_download() as download_info:
            page.get_by_role("button", name="下载 Markdown").click()
        download = download_info.value
        assert download.suggested_filename == "github-deep-search-report.md"
        assert Path(download.path()).read_text(encoding="utf-8") == "# Controlled report\n"

        page.reload()
        playwright.expect(page.locator("#run-state")).to_have_text("已完成")
        assert page.locator('[data-status="completed"]').count() == 6
        assert page.locator(".result-card").count() == 3
        assert page.get_by_label("需求描述", exact=True).input_value() == query
        playwright.expect(page.get_by_role("button", name="开始搜索")).to_be_enabled()

        page.set_viewport_size({"width": 375, "height": 812})
        layout = page.evaluate(
            "() => ({ scrollWidth: document.documentElement.scrollWidth, width: window.innerWidth })"
        )
        assert layout["scrollWidth"] == layout["width"]
        page.close()


def test_user_refreshes_an_active_run_then_cancels_it(browser) -> None:
    with running_app("hold") as base_url:
        page = browser.new_page()
        page.goto(base_url)
        query = REALISTIC_USER_INPUTS[1]
        page.get_by_label("需求描述", exact=True).fill(query)
        page.get_by_role("button", name="开始搜索").click()
        discovery = page.locator('[data-stage="discovery"]')
        playwright.expect(discovery).to_have_attribute("data-status", "in_progress")

        page.reload()
        playwright.expect(discovery).to_have_attribute("data-status", "in_progress")
        assert page.get_by_label("需求描述", exact=True).input_value() == query
        playwright.expect(page.get_by_role("button", name="取消任务")).to_be_visible()
        page.get_by_role("button", name="取消任务").click()

        playwright.expect(page.locator("#run-state")).to_have_text("已取消")
        playwright.expect(discovery).to_have_attribute("data-status", "cancelled")
        playwright.expect(page.get_by_role("button", name="开始搜索")).to_be_enabled()
        playwright.expect(page.locator("#report")).to_be_hidden()
        page.close()


def test_user_sees_safe_failure_and_can_start_again(browser) -> None:
    with running_app("fail") as base_url:
        page = browser.new_page()
        page.goto(base_url)
        query = REALISTIC_USER_INPUTS[2]
        page.get_by_label("需求描述", exact=True).fill(query)
        page.get_by_role("button", name="开始搜索").click()

        playwright.expect(page.locator("#run-state")).to_have_text("失败", timeout=10_000)
        playwright.expect(page.locator("#run-error")).to_have_text(
            "The controlled evidence stage failed safely."
        )
        playwright.expect(page.locator('[data-stage="evidence"]')).to_have_attribute(
            "data-status", "failed"
        )
        playwright.expect(page.get_by_role("button", name="开始搜索")).to_be_enabled()
        body_text = page.locator("body").inner_text()
        assert "e2e-placeholder" not in body_text
        assert "Authorization" not in body_text
        assert page.get_by_label("需求描述", exact=True).input_value() == query
        page.close()


def test_user_can_retry_the_same_realistic_input_after_failure(browser) -> None:
    with running_app("fail_once") as base_url:
        page = browser.new_page()
        page.goto(base_url)
        query = REALISTIC_USER_INPUTS[3]
        query_input = page.get_by_label("需求描述", exact=True)
        query_input.fill(query)
        page.get_by_role("button", name="开始搜索").click()

        playwright.expect(page.locator("#run-state")).to_have_text("失败", timeout=10_000)
        assert query_input.input_value() == query

        page.get_by_role("button", name="开始搜索").click()
        playwright.expect(page.locator("#run-state")).to_have_text("已完成", timeout=10_000)
        assert query_input.input_value() == query
        page.close()


def test_user_sees_timeout_as_a_terminal_failure(browser) -> None:
    with running_app("timeout") as base_url:
        page = browser.new_page()
        page.goto(base_url)
        query = REALISTIC_USER_INPUTS[4]
        page.get_by_label("需求描述", exact=True).fill(query)
        page.get_by_role("button", name="开始搜索").click()

        playwright.expect(page.locator("#run-state")).to_have_text("失败", timeout=5_000)
        playwright.expect(page.locator("#run-error")).to_contain_text("1-second timeout")
        playwright.expect(page.get_by_role("button", name="开始搜索")).to_be_enabled()
        assert page.get_by_label("需求描述", exact=True).input_value() == query
        page.close()


def test_user_is_told_to_configure_missing_credentials(browser) -> None:
    with running_app("missing_credentials") as base_url:
        page = browser.new_page()
        page.goto(base_url)

        playwright.expect(page.get_by_role("button", name="请先配置凭据")).to_be_disabled()
        playwright.expect(page.locator("#github-status")).to_have_text("未配置")
        playwright.expect(page.locator("#search-notice")).to_contain_text(
            "GITHUB_TOKEN 和 LLM_API_KEY"
        )
        page.close()


def _available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        return int(server.getsockname()[1])


def _wait_until_ready(process: subprocess.Popen[str], base_url: str) -> None:
    for _ in range(100):
        if process.poll() is not None:
            output = process.stdout.read() if process.stdout else ""
            raise AssertionError(f"test Web app stopped before startup:\n{output}")
        try:
            response = httpx.get(f"{base_url}/api/status", timeout=0.2)
            if response.status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.05)
    raise AssertionError("test Web app did not start")


def _root() -> Path:
    return Path(__file__).resolve().parents[1]
