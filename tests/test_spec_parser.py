from __future__ import annotations

import asyncio

import pytest

from github_deep_search.spec_parser import SearchSpecParser


def test_spec_parser_accepts_evidence_aliases_from_llm_data() -> None:
    parser = SearchSpecParser()
    spec = parser._from_llm_data(
        "need tasks with deadlines",
        {
            "intent": "Find task app",
            "literal_keywords": ["task app"],
            "domains": ["task management"],
            "actions": ["set deadline"],
            "objects": ["deadline"],
            "outputs": ["task list"],
            "interfaces": ["desktop"],
            "must_have": ["deadline"],
            "search_queries": ["task deadline app"],
            "repo_search_queries": ["desktop task app deadline"],
            "code_search_queries": ["due date field"],
            "topic_search_queries": ["task-management"],
            "issue_search_queries": ["task deadline issue"],
            "web_search_queries": ["site:github.com task deadline app"],
            "evidence_aliases": {"deadline": ["deadline", "due date"]},
        },
    )

    assert spec is not None
    requirement = spec.to_requirement()
    assert requirement.evidence_aliases == {"deadline": ["deadline", "due date"]}
    assert requirement.repo_search_queries == ["desktop task app deadline"]
    assert requirement.code_search_queries == ["due date field"]
    assert requirement.topic_search_queries == ["task-management"]
    assert requirement.issue_search_queries == ["task deadline issue"]
    assert requirement.web_search_queries == ["site:github.com task deadline app"]


def test_spec_parser_removes_contained_duplicate_features_without_domain_rules() -> None:
    parser = SearchSpecParser()

    assert parser._non_redundant_features(
        ["offline classification", "mobile offline classification", "bird call recording", "species result"]
    ) == ["mobile offline classification", "bird call recording", "species result"]


def test_literal_fallback_evidence_aliases_use_only_literal_terms() -> None:
    parser = SearchSpecParser()
    spec = parser._literal_only_spec("deadline widget")

    assert spec.evidence_aliases["deadline"] == ["deadline"]
    assert spec.evidence_aliases["widget"] == ["widget"]
    assert spec.repo_search_queries
    assert spec.code_search_queries
    assert spec.topic_search_queries
    assert spec.issue_search_queries
    assert spec.web_search_queries


def test_literal_fallback_preserves_user_phrasing_without_static_cleanup() -> None:
    parser = SearchSpecParser()
    spec = parser._literal_only_spec("我想做一个windows10/11的谷歌浏览器插件，可以总结网页内容，并把摘要同步直接放置在桌面。")

    assert spec.must_have == [
        "我想做一个windows10/11的谷歌浏览器插件",
        "可以总结网页内容",
        "并把摘要同步直接放置在桌面",
    ]
    assert spec.evidence_aliases["我想做一个windows10/11的谷歌浏览器插件"][0] == "我想做一个windows10/11的谷歌浏览器插件"
    assert spec.evidence_aliases["可以总结网页内容"] == ["可以总结网页内容"]
    assert "chrome extension" not in str(spec.evidence_aliases)
    assert "browser extension" not in str(spec.evidence_aliases)
    assert "desktop" not in str(spec.evidence_aliases)
    assert spec.interfaces == []


def test_literal_fallback_uses_clause_anchors_before_raw_terms() -> None:
    parser = SearchSpecParser()
    spec = parser._literal_only_spec("找一个开源 Python 终端 UI 库，支持表格、进度条、Markdown 渲染和富文本样式。")

    assert spec.repo_search_queries[:2] == ["找一个开源 Python 终端 UI 库", "Markdown 渲染和富文本样式"]


def test_parser_repairs_llm_channel_queries_with_chinese_anchors() -> None:
    parser = SearchSpecParser()
    query = "开源 Python 终端 UI 库，表格、进度条、Markdown 渲染和富文本样式。"
    spec = parser._from_llm_data(
        query,
        {
            "intent": "寻找开源 Python 终端 UI 库",
            "literal_keywords": ["开源 Python 终端 UI 库", "表格", "进度条", "Markdown 渲染", "富文本样式"],
            "domains": ["terminal", "cli", "tui"],
            "actions": ["render", "display", "support"],
            "objects": ["table", "progress bar", "Markdown", "rich text"],
            "outputs": ["terminal UI"],
            "interfaces": ["Python API"],
            "must_have": ["开源 Python 终端 UI 库", "表格", "进度条", "Markdown 渲染和富文本样式"],
            "search_queries": ["Python terminal UI table progress Markdown"],
            "repo_search_queries": ["Python terminal UI table progress Markdown"],
            "code_search_queries": ["table progress markdown rich text"],
            "topic_search_queries": ["python-tui", "python-terminal-ui"],
            "issue_search_queries": ["Python terminal UI table progress Markdown"],
            "evidence_aliases": {
                "开源 Python 终端 UI 库": ["open source Python terminal UI library", "terminal UI library", "TUI"],
                "表格": ["table"],
                "进度条": ["progress bar"],
                "Markdown 渲染和富文本样式": ["Markdown", "rich text"],
            },
        },
    )

    assert spec is not None
    assert parser._valid(spec) is False

    repaired = parser._with_anchor_queries(spec, parser._explicit_requirement_clauses(query))

    assert parser._valid(repaired) is True
    assert repaired.repo_search_queries[0] == "开源 Python 终端 UI 库"


def test_spec_parser_rejects_missing_must_have_evidence_aliases() -> None:
    parser = SearchSpecParser()
    spec = parser._from_llm_data(
        "need tasks with deadlines",
        {
            "intent": "Find task app",
            "literal_keywords": ["task app"],
            "domains": ["task management"],
            "actions": ["set deadline"],
            "objects": ["deadline"],
            "outputs": ["task list"],
            "interfaces": ["desktop"],
            "must_have": ["deadline", "completion toggle"],
            "search_queries": ["task deadline app"],
            "repo_search_queries": ["task deadline app"],
            "code_search_queries": ["deadline"],
            "topic_search_queries": ["task-management"],
            "issue_search_queries": ["deadline issue"],
            "evidence_aliases": {"deadline": ["deadline", "due date"]},
        },
    )

    assert spec is not None
    assert parser._valid(spec) is False


def test_spec_parser_rejects_ungrounded_channel_queries_even_when_intent_repeats_input() -> None:
    parser = SearchSpecParser()
    spec = parser._from_llm_data(
        "Need an offline bird call classifier",
        {
            "intent": "Need an offline bird call classifier",
            "literal_keywords": ["offline bird call classifier"],
            "domains": ["audio classification"],
            "actions": ["classify"],
            "objects": ["bird calls"],
            "outputs": ["species"],
            "interfaces": ["mobile"],
            "must_have": ["offline classification"],
            "search_queries": ["invoice approval workflow"],
            "repo_search_queries": ["invoice approval workflow"],
            "code_search_queries": ["invoice status"],
            "topic_search_queries": ["accounting"],
            "issue_search_queries": ["invoice approval issue"],
            "evidence_aliases": {"offline classification": ["offline classification"]},
        },
    )

    assert spec is not None
    assert parser._valid(spec) is False


def test_spec_parser_replans_once_after_ungrounded_llm_output() -> None:
    class SequencedLLM:
        def __init__(self) -> None:
            self.calls = 0

        async def json_chat(self, system: str, prompt: str) -> dict[str, object]:
            self.calls += 1
            repo_query = "invoice approval workflow" if self.calls == 1 else "offline bird call classifier"
            return {
                "intent": "Find an offline bird call classifier",
                "literal_keywords": ["offline bird call classifier"],
                "domains": ["audio classification"],
                "actions": ["classify"],
                "objects": ["bird calls"],
                "outputs": ["species"],
                "interfaces": ["mobile"],
                "must_have": ["offline classification"],
                "search_queries": [repo_query],
                "repo_search_queries": [repo_query],
                "code_search_queries": ["offline classification"],
                "topic_search_queries": ["bird-audio"],
                "issue_search_queries": [repo_query],
                "evidence_aliases": {"offline classification": ["offline classification"]},
            }

    llm = SequencedLLM()
    spec = asyncio.run(SearchSpecParser().parse("Need an offline bird call classifier", llm))  # type: ignore[arg-type]

    assert llm.calls == 2
    assert spec.repo_search_queries == ["offline bird call classifier"]


def test_spec_parser_backfills_code_queries_from_evidence_aliases() -> None:
    parser = SearchSpecParser()
    spec = parser._from_llm_data(
        "need tasks with deadlines",
        {
            "intent": "Find task app",
            "literal_keywords": ["task app"],
            "domains": ["task management"],
            "actions": ["set deadline"],
            "objects": ["deadline"],
            "outputs": ["task list"],
            "interfaces": ["desktop"],
            "must_have": ["deadline"],
            "search_queries": ["task deadline app"],
            "repo_search_queries": ["task deadline app"],
            "evidence_aliases": {"deadline": ["deadline", "due date"]},
        },
    )

    assert spec is not None
    assert spec.code_search_queries == ["deadline", "due date"]


@pytest.mark.parametrize(
    ("query", "first_repo_query", "topic"),
    [
        ("寻找适合阳台种植的开源灌溉控制器", "阳台 灌溉 控制器", "irrigation-controller"),
        ("Need a screen-reader friendly diagram editor", "accessible diagram editor", "accessibility"),
        ("寻找离线鸟鸣识别移动应用", "离线 鸟鸣识别 移动应用", "birdsong-recognition"),
    ],
)
def test_spec_parser_preserves_llm_channel_plans_across_domains(
    query: str,
    first_repo_query: str,
    topic: str,
) -> None:
    parser = SearchSpecParser()
    spec = parser._from_llm_data(
        query,
        {
            "intent": query,
            "literal_keywords": [first_repo_query],
            "domains": [topic],
            "actions": ["discover"],
            "objects": ["application"],
            "outputs": ["result"],
            "interfaces": ["user interface"],
            "must_have": ["core capability"],
            "search_queries": [first_repo_query],
            "repo_search_queries": [first_repo_query, f"{first_repo_query} open source"],
            "code_search_queries": ["core capability implementation"],
            "topic_search_queries": [topic],
            "issue_search_queries": [f"{first_repo_query} issue"],
            "web_search_queries": [f"site:github.com {first_repo_query}"],
            "evidence_aliases": {"core capability": ["core capability"]},
        },
    )

    assert spec is not None
    assert spec.repo_search_queries[0] == first_repo_query
    assert spec.topic_search_queries == [topic]


def test_parser_prompt_requires_current_requirement_grounding() -> None:
    class CapturingLLM:
        def __init__(self) -> None:
            self.prompts: list[str] = []

        async def json_chat(self, system: str, prompt: str) -> None:
            self.prompts.append(prompt)
            return None

    llm = CapturingLLM()
    asyncio.run(SearchSpecParser().parse("跨平台色彩对比度检查器", llm))  # type: ignore[arg-type]

    assert any("current requirement" in prompt for prompt in llm.prompts)
    assert any("canned examples" in prompt for prompt in llm.prompts)
    assert any("core outcomes" in prompt for prompt in llm.prompts)
    assert any("Extension or implementation details" in prompt for prompt in llm.prompts)
    assert not any(("English " + "angles") in prompt for prompt in llm.prompts)


def test_invalid_llm_plan_falls_back_to_literal_spec_without_translation_pass() -> None:
    class InvalidLLM:
        def __init__(self) -> None:
            self.prompts: list[str] = []

        async def json_chat(self, system: str, prompt: str) -> None:
            self.prompts.append(f"{system}\n{prompt}")
            return None

    llm = InvalidLLM()
    spec = asyncio.run(SearchSpecParser().parse("跨平台色彩对比度检查器", llm))  # type: ignore[arg-type]

    assert len(llm.prompts) == 3
    assert spec.must_have
    assert not any(("Translate requirement " + "phrases") in prompt for prompt in llm.prompts)
    assert not any(("literal " + "English") in prompt for prompt in llm.prompts)


def test_parser_trusts_llm_core_and_optional_split_without_marker_tables() -> None:
    parser = SearchSpecParser()
    query = "需要一个核心优化工具，可能需要用户输入外部服务 API key 作为能跑起来的前提?"
    spec = parser._from_llm_data(
        query,
        {
            "intent": "核心优化工具",
            "literal_keywords": ["核心优化工具", "外部服务 API key"],
            "domains": ["核心优化工具"],
            "actions": ["优化"],
            "objects": ["工作流"],
            "outputs": ["优化结果"],
            "interfaces": [],
            "must_have": ["核心优化工具"],
            "nice_to_have": ["外部服务 API key"],
            "search_queries": ["核心优化工具"],
            "repo_search_queries": ["核心优化工具"],
            "code_search_queries": ["核心优化工具"],
            "topic_search_queries": ["核心优化工具"],
            "issue_search_queries": ["核心优化工具"],
            "evidence_aliases": {
                "核心优化工具": ["核心优化工具"],
            },
        },
    )

    assert spec is not None
    assert spec.must_have == ["核心优化工具"]
    assert "外部服务 API key" in spec.nice_to_have
    assert parser._valid(spec) is True


def test_anchor_queries_are_derived_from_extracted_clauses_without_phrase_filtering() -> None:
    parser = SearchSpecParser()
    query = (
        "我想做一个浏览器插件，用来过滤平台里的广告类视频，"
        "web 端因为平台 API 数据可以直接抓取，"
        "通过 AI 进行解析应该很容易实现，手机端我不确定"
    )
    anchors = parser._explicit_requirement_clauses(query)
    spec = parser._from_llm_data(
        query,
        {
            "intent": "过滤平台广告视频的浏览器插件",
            "literal_keywords": ["浏览器插件", "平台", "广告类视频", "API 数据", "AI 解析", "手机端"],
            "domains": ["浏览器插件", "平台视频"],
            "actions": ["过滤"],
            "objects": ["广告类视频"],
            "outputs": [],
            "interfaces": ["浏览器插件", "手机端"],
            "must_have": ["浏览器插件", "过滤平台里的广告类视频"],
            "nice_to_have": [
                "web 端因为平台 API 数据可以直接抓取",
                "通过 AI 进行解析应该很容易实现",
                "手机端我不确定",
            ],
            "search_queries": ["浏览器插件 过滤 广告类视频"],
            "repo_search_queries": ["浏览器插件 过滤 广告类视频"],
            "code_search_queries": ["浏览器插件 过滤"],
            "topic_search_queries": ["browser-extension"],
            "issue_search_queries": ["浏览器插件 过滤 广告类视频"],
            "evidence_aliases": {
                "浏览器插件": ["浏览器插件", "browser extension"],
                "过滤平台里的广告类视频": ["过滤 广告类视频"],
                "web 端因为平台 API 数据可以直接抓取": ["API 数据抓取"],
                "通过 AI 进行解析应该很容易实现": ["AI 解析"],
                "手机端我不确定": ["手机端"],
            },
        },
    )

    assert spec is not None
    assert spec.must_have == ["浏览器插件", "过滤平台里的广告类视频"]
    assert "web 端因为平台 API 数据可以直接抓取" in spec.nice_to_have
    assert "通过 AI 进行解析应该很容易实现" in spec.nice_to_have
    assert "手机端我不确定" in spec.nice_to_have

    anchored = parser._with_anchor_queries(spec, anchors)

    assert any("浏览器插件" in query and "过滤平台里的广告类视频" in query for query in anchored.repo_search_queries)
    assert any("我想做一个浏览器插件" in query for query in anchored.repo_search_queries)


def test_parser_rejects_plan_that_collapses_product_to_one_subfeature() -> None:
    parser = SearchSpecParser()
    query = (
        "我需要一个可自托管的开源文档管理系统：支持扫描件 OCR、全文搜索、"
        "自动标签和分类、Docker 部署，并能在本地保存数据。"
    )
    spec = parser._from_llm_data(
        query,
        {
            "intent": "部署 OCR 服务",
            "literal_keywords": ["OCR", "Docker"],
            "domains": ["文字识别"],
            "actions": ["识别"],
            "objects": ["扫描件"],
            "outputs": ["文字"],
            "interfaces": ["服务器"],
            "must_have": ["OCR", "Docker 部署"],
            "search_queries": ["OCR Docker"],
            "repo_search_queries": ["OCR Docker 服务", "OCR Docker 部署", "扫描件 OCR Docker"],
            "code_search_queries": ["OCR", "Dockerfile"],
            "topic_search_queries": ["ocr"],
            "issue_search_queries": ["OCR Docker 问题", "扫描件 OCR", "Docker 部署"],
            "evidence_aliases": {"OCR": ["OCR"], "Docker 部署": ["Dockerfile"]},
        },
    )

    assert spec is not None
    assert parser._valid(spec) is False
    assert "文档管理系统" in parser._explicit_requirement_clauses(query)[0]
    assert "全文搜索" in parser._explicit_requirement_clauses(query)
    assert "自动标签和分类" in parser._explicit_requirement_clauses(query)


def test_parser_keeps_long_request_clauses_without_term_normalization() -> None:
    parser = SearchSpecParser()
    query = (
        "我想通过关键词进行星河平台相关热门内容和热门评论查询，必须有相关的截图，尤其是评论截图。"
        "最后生成一份pdf报告，记录相关关键词下的热度top10。最好支持多端运行，有个网页版，"
        "也有mcp可以通过bridgeBox直接运行"
    )

    assert parser._explicit_requirement_clauses(query) == [
        "我想通过关键词进行星河平台相关热门内容和热门评论查询",
        "必须有相关的截图",
        "尤其是评论截图",
        "最后生成一份pdf报告",
        "记录相关关键词下的热度top10",
        "最好支持多端运行",
        "有个网页版",
        "也有mcp可以通过bridgeBox直接运行",
    ]


def test_parser_uses_numbered_body_after_structural_intro() -> None:
    parser = SearchSpecParser()
    query = (
        "调研输入：1、搜索对应关键词，找到对应商品；"
        "2、点击付款，页面显示实付页面价格明细，统计全通路优惠明细；"
        "3、抓取到各频道的价格"
    )

    clauses = parser._explicit_requirement_clauses(query)

    assert "调研输入" not in clauses
    assert "搜索对应关键词，找到对应商品" in clauses
    assert any("实付页面价格明细" in clause for clause in clauses)
    assert any("抓取到各频道的价格" in clause for clause in clauses)


def test_literal_aliases_keep_compound_clause_intact() -> None:
    parser = SearchSpecParser()

    assert parser._literal_aliases("搜索对应关键词，找到对应商品") == ["搜索对应关键词，找到对应商品"]


def test_llm_must_have_from_structural_intro_is_not_kept_as_core_feature() -> None:
    parser = SearchSpecParser()
    spec = parser._from_llm_data(
        "调研输入：1、搜索商品并抓取价格明细；2、输出优惠明细",
        {
            "intent": "抓取商品价格明细",
            "literal_keywords": ["搜索商品", "价格明细"],
            "domains": ["商品"],
            "actions": ["搜索", "抓取"],
            "objects": ["价格明细"],
            "outputs": ["优惠明细"],
            "interfaces": [],
            "must_have": [
                "调研输入",
                "搜索商品并抓取价格明细",
                "输出优惠明细",
            ],
            "search_queries": ["搜索商品 价格明细"],
            "repo_search_queries": ["搜索商品 价格明细"],
            "code_search_queries": ["price detail"],
            "topic_search_queries": ["price"],
            "issue_search_queries": ["搜索商品 价格明细"],
            "evidence_aliases": {
                "调研输入": ["调研输入"],
                "搜索商品并抓取价格明细": ["搜索商品", "价格明细"],
                "输出优惠明细": ["优惠明细"],
            },
        },
    )

    assert spec is not None
    assert spec.must_have == ["搜索商品并抓取价格明细", "输出优惠明细"]


def test_numbered_step_repair_promotes_hidden_alias_key_to_step() -> None:
    parser = SearchSpecParser()
    spec = parser._from_llm_data(
        "调研输入：1、搜索商品；2、抓取价格；3、输出优惠明细",
        {
            "intent": "搜索商品并抓取价格",
            "literal_keywords": ["搜索商品", "抓取价格", "输出优惠明细"],
            "domains": ["商品"],
            "actions": ["搜索", "抓取", "输出"],
            "objects": ["价格", "优惠明细"],
            "outputs": ["优惠明细"],
            "interfaces": [],
            "must_have": ["搜索商品", "抓取价格"],
            "nice_to_have": [],
            "search_queries": ["搜索商品 抓取价格 输出优惠明细"],
            "repo_search_queries": ["搜索商品 抓取价格 输出优惠明细"],
            "code_search_queries": ["搜索商品 抓取价格"],
            "topic_search_queries": ["商品价格"],
            "issue_search_queries": ["搜索商品 抓取价格 输出优惠明细"],
            "evidence_aliases": {
                "搜索商品": ["搜索商品"],
                "抓取价格": ["抓取价格"],
                "输出优惠明细": ["优惠明细"],
            },
        },
    )

    assert spec is not None
    assert spec.must_have == ["搜索商品", "抓取价格", "输出优惠明细"]
    assert set(spec.evidence_aliases) == set(spec.must_have)
    assert parser._valid(spec) is True


def test_spec_validation_rejects_missing_must_have() -> None:
    parser = SearchSpecParser()
    spec = parser._from_llm_data(
        "搜索商品并输出价格明细",
        {
            "intent": "搜索商品并输出价格明细",
            "literal_keywords": ["搜索商品", "价格明细"],
            "domains": ["商品"],
            "actions": ["搜索", "输出"],
            "objects": ["价格明细"],
            "outputs": ["价格明细"],
            "interfaces": [],
            "must_have": [],
            "nice_to_have": [],
            "search_queries": ["搜索商品 价格明细"],
            "repo_search_queries": ["搜索商品 价格明细"],
            "code_search_queries": ["搜索商品 价格明细"],
            "topic_search_queries": ["商品价格"],
            "issue_search_queries": ["搜索商品 价格明细"],
            "evidence_aliases": {},
        },
    )

    assert spec is not None
    assert parser._valid(spec) is False


def test_parser_repairs_split_numbered_step_fragments() -> None:
    parser = SearchSpecParser()
    query = "调研输入：1、搜索对应关键词，找到对应商品；2、点击付款并统计价格明细"
    spec = parser._from_llm_data(
        query,
        {
            "intent": "自动化商品价格统计",
            "literal_keywords": ["搜索对应关键词", "找到对应商品", "点击付款", "价格明细"],
            "domains": ["商品"],
            "actions": ["搜索", "点击", "统计"],
            "objects": ["商品", "价格明细"],
            "outputs": ["价格明细"],
            "interfaces": [],
            "must_have": ["搜索对应关键词", "找到对应商品", "点击付款并统计价格明细"],
            "nice_to_have": [],
            "search_queries": ["搜索对应关键词 找到对应商品 价格明细"],
            "repo_search_queries": ["搜索对应关键词 找到对应商品 价格明细"],
            "code_search_queries": ["价格明细"],
            "topic_search_queries": ["商品价格"],
            "issue_search_queries": ["搜索对应关键词 找到对应商品 价格明细"],
            "evidence_aliases": {
                "搜索对应关键词": ["搜索对应关键词"],
                "找到对应商品": ["找到对应商品"],
                "点击付款并统计价格明细": ["点击付款", "价格明细"],
            },
        },
    )

    assert spec is not None
    assert spec.must_have == ["搜索对应关键词，找到对应商品", "点击付款并统计价格明细"]
    assert set(spec.evidence_aliases) == set(spec.must_have)
    assert "搜索对应关键词" in spec.evidence_aliases["搜索对应关键词，找到对应商品"]
    assert parser._valid(spec) is True


def test_numbered_workflow_preserves_llm_interpreted_capability() -> None:
    parser = SearchSpecParser()
    interpreted = "automate item search, inspect checkout price details, and collect channel prices"
    spec = parser._from_llm_data(
        (
            "Research input: 1) search matching keywords and find the matching item; "
            "2) open checkout and inspect payable price details; "
            "3) collect prices from multiple channels"
        ),
        {
            "intent": "find an automation tool for item price detail collection",
            "literal_keywords": ["item search", "checkout price details", "channel prices"],
            "domains": ["commerce automation"],
            "actions": ["search", "inspect", "collect"],
            "objects": ["items", "checkout price details", "channel prices"],
            "outputs": ["price detail report"],
            "interfaces": ["browser"],
            "must_have": [interpreted],
            "nice_to_have": [],
            "search_queries": ["commerce automation item search checkout price channel prices"],
            "repo_search_queries": ["commerce automation item search checkout price collect channel prices"],
            "code_search_queries": ["checkout price details channel prices"],
            "topic_search_queries": ["commerce-automation"],
            "issue_search_queries": ["checkout price details collect channel prices"],
            "evidence_aliases": {
                interpreted: [
                    "checkout price details",
                    "collect channel prices",
                    "item search automation",
                ]
            },
        },
    )

    assert spec is not None
    assert spec.must_have == [interpreted]
    assert set(spec.evidence_aliases) == {interpreted}
    assert parser._valid(spec) is True
