from __future__ import annotations

from pathlib import Path


def test_engine_does_not_define_sample_specific_alias_tables() -> None:
    engine_source = Path("github_deep_search/engine.py").read_text(encoding="utf-8")

    forbidden_markers = [
        "_precise_builtin_aliases",
        "builtin: list[tuple",
        "dueDate",
        "isCompleted",
        "CornerRadius",
        "content_scripts",
        "notion api",
    ]
    for marker in forbidden_markers:
        assert marker not in engine_source


def test_search_pipeline_does_not_use_hardcoded_business_word_lists() -> None:
    sources = "\n".join(
        Path(path).read_text(encoding="utf-8")
        for path in [
            "github_deep_search/engine.py",
            "github_deep_search/spec_parser.py",
            "github_deep_search/utils.py",
        ]
    )

    forbidden_markers = [
        "_generic_evidence_gate_terms",
        "_generic_feature_alias_terms",
        "_awesome_list_penalty",
        "list_markers",
        "stop = {",
    ]
    for marker in forbidden_markers:
        assert marker not in sources


def test_search_pipeline_has_no_configured_query_expansion_path() -> None:
    parser_source = Path("github_deep_search/spec_parser.py").read_text(encoding="utf-8")

    assert not Path("config/search_expansions.json").exists()
    assert "configured_query_expansions" not in parser_source
    assert "search_expansions" not in parser_source


def test_search_pipeline_has_no_translation_fallback_or_domain_word_pack() -> None:
    sources = "\n".join(
        Path(path).read_text(encoding="utf-8")
        for path in [
            "github_deep_search/engine.py",
            "github_deep_search/spec_parser.py",
        ]
    )

    forbidden_markers = [
        "_grounded_" + "fallback_spec",
        "Translate requirement " + "phrases",
        "literal " + "English",
        "\"trans" + "lations\"",
        "English " + "angles",
        "route " + "optimizer",
        "waypoint " + "optimization",
        "travelling " + "salesman",
        "vehicle " + "routing",
    ]
    for marker in forbidden_markers:
        assert marker not in sources


def test_runtime_has_no_bundled_demo_or_seeded_results() -> None:
    runtime_sources = "\n".join(
        Path(path).read_text(encoding="utf-8")
        for path in [
            "github_deep_search/web.py",
            "github_deep_search/static/index.html",
            "github_deep_search/static/app.js",
        ]
    )
    readme = Path("README.md").read_text(encoding="utf-8")

    assert not Path("github_deep_search/demo.py").exists()
    assert not Path("docs/assets/demo-report.svg").exists()
    forbidden_markers = [
        "demo_report",
        "demo: true",
        "demoAvailable",
        'id="demo"',
        "data-example=",
        "bundled sample report",
        "seeded result data.",
    ]
    for marker in forbidden_markers:
        assert marker not in runtime_sources
    assert "不内置 Demo 报告。" in readme
    assert "不内置假仓库、假排行或 seeded result data。" in readme
    assert "测试夹具不会被 Web、CLI、MCP server 或搜索引擎运行时加载。" in readme


def test_readme_shows_real_run_preview_and_cost_boundary() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "输入一句产品想法，真实搜索 GitHub" in readme
    assert "## 15 秒看懂" in readme
    assert "浏览器插件，可以总结网页内容，并把摘要同步到 Notion" in readme
    assert "GitHub Deep Search 给出的结果" in readme
    assert "## 真实运行结果" in readme
    assert "docs/assets/real-run-highlight.png" in readme
    assert "docs/assets/real-run-report-cropped.png" in readme
    assert "报告记录消耗" in readme
    assert "## 必须配置 API Key" in readme
    assert "没有 key 可以打开界面，但不会得到可信的真实调研报告。" in readme
    assert "`GITHUB_TOKEN`：真实使用基本必需。" in readme
    assert "`LLM_API_KEY`：必需。" in readme
    assert "## 预期消耗" in readme
    assert "GitHub 请求上限" in readme
    assert "典型 LLM tokens" in readme
    assert Path("docs/assets/real-run-highlight.png").exists()
    assert "LLM_INPUT_USD_PER_1M" in readme
    assert Path("docs/assets/real-search-ready.png").exists()
    assert Path("docs/assets/real-run-report-cropped.png").exists()
    assert Path("docs/REAL_RUNS.md").exists()
