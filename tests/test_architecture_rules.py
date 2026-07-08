from __future__ import annotations

import re
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
        "generic_features = {",
        "_awesome_list_penalty",
        "list_markers",
        "stop = {",
    ]
    for marker in forbidden_markers:
        assert marker not in sources


def test_rule_gate_forbids_static_language_patch_tables() -> None:
    agents = Path("AGENTS.md").read_text(encoding="utf-8")

    required_rule_text = [
        "fixed phrase",
        "wording pattern",
        "language marker",
        "Static natural-language cleanup regexes",
        "in any language",
        "Static stopword, weak-word, generic-word, catalog-word, report-artifact-word",
        "fixed word or regex",
        "Test-only static phrases",
        "sample-specific ranking behavior",
    ]
    for marker in required_rule_text:
        assert marker in agents

    contributing = Path("CONTRIBUTING.md").read_text(encoding="utf-8")
    evidence_gating = Path("docs/evidence-gating.md").read_text(encoding="utf-8")
    assert "The same rule applies to tests" in contributing
    assert "fixture, golden report, or assertion" in evidence_gating
    assert "static report rewrites" in evidence_gating


def test_parser_runtime_has_no_static_natural_language_filters() -> None:
    parser_source = Path("github_deep_search/spec_parser.py").read_text(encoding="utf-8")

    forbidden_markers = [
        "_strip_requirement_preamble",
        "_is_filler_requirement_clause",
        "_is_uncertain_or_assumptive_feature",
        "hard_markers",
        "uncertainty_markers",
        "causal_markers",
        "implementation_markers",
        "replacements = {",
    ]
    for marker in forbidden_markers:
        assert marker not in parser_source
    assert re.search(r"[\u4e00-\u9fff]", parser_source) is None


def test_runtime_pipeline_has_no_static_semantic_patch_tables() -> None:
    runtime_sources = "\n".join(
        Path(path).read_text(encoding="utf-8")
        for path in [
            "github_deep_search/engine.py",
            "github_deep_search/spec_parser.py",
        ]
    )

    forbidden_markers = [
        "negative_before",
        "negative_after",
        "strong_markers",
        "restricted_markers",
        "does not support",
        "not supported",
        "not available",
        "awesome list",
        "curated list",
        "newsletter",
        "browser extension",
        "chrome extension",
        "open source technical research analyst",
        "open source\", max_results",
        "replacements = {",
    ]
    for marker in forbidden_markers:
        assert marker not in runtime_sources


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
        "_known_literal_aliases",
        "_literal_concepts",
        "chrome " + "extension",
        "browser " + "extension",
        "manifest_" + "version",
        "谷歌" + "浏览器",
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
    assert not Path("ui").exists()
    assert not Path("docs/UI_REDESIGN_HANDOFF.md").exists()
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
    assert "不内置 Demo 报告" in readme
    assert "不内置假仓库、假排行或 seeded result data" in readme
    assert "测试夹具不会被 Web、CLI、MCP server 或搜索引擎运行时加载" in readme


def test_readme_shows_real_run_preview_and_cost_boundary() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "用一句产品想法，真实搜索 GitHub" in readme
    assert "https://img.shields.io/github/stars/wp-i/github-deep-search?style=social" in readme
    assert "https://github.com/wp-i/github-deep-search/actions/workflows/ci.yml/badge.svg" in readme
    assert "No%20Demo%20Data-Real%20Search" in readme
    assert "## 它解决什么" in readme
    assert "## 15 秒看懂" in readme
    assert "浏览器插件，可以总结网页内容，并把摘要同步到 Notion" in readme
    assert "Top 项目" in readme
    assert "## 一分钟跑起来" in readme
    assert "python scripts/start_web.py" in readme
    assert "## 真实运行效果" in readme
    assert "默认打开页面" in readme
    assert "搜索后结果页" in readme
    assert "docs/assets/web-workbench-20260702.png" in readme
    assert "docs/assets/web-result-20260702.png" in readme
    assert "docs/assets/web-workbench.jpg" not in readme
    assert "报告消耗" in readme
    assert "## API Key 与消耗" in readme
    assert "没有 key 可以打开界面，但不会得到可信的真实调研报告。" in readme
    assert "`GITHUB_TOKEN` | 基本必需" in readme
    assert "`LLM_API_KEY` | 必需" in readme
    assert "GitHub 请求上限" in readme
    assert "典型 LLM tokens" in readme
    assert "## 信任边界" in readme
    assert "不内置 Demo 报告" in readme
    assert "不使用静态产品同义词表" in readme
    assert Path("docs/assets/web-workbench-20260702.png").exists()
    assert Path("docs/assets/web-result-20260702.png").exists()
    assert "LLM_INPUT_USD_PER_1M" in readme
    assert Path("docs/assets/real-search-ready.png").exists()
    assert Path("docs/assets/real-run-report-cropped.png").exists()
    assert Path("docs/REAL_RUNS.md").exists()


def test_readme_uses_two_primary_web_screenshots() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")

    primary_screenshots = [
        "docs/assets/web-workbench-20260702.png",
        "docs/assets/web-result-20260702.png",
    ]

    for screenshot in primary_screenshots:
        assert readme.count(screenshot) == 1
        assert Path(screenshot).exists()

    assert readme.index(primary_screenshots[0]) < readme.index(primary_screenshots[1])
    assert "默认打开页面" in readme
    assert "搜索后结果页" in readme
