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


def test_literal_fallback_extracts_chinese_product_capabilities() -> None:
    parser = SearchSpecParser()
    spec = parser._literal_only_spec("我想做一个windows10/11的谷歌浏览器插件，可以总结网页内容，并把摘要同步直接放置在桌面。")

    assert "我想做一个" not in spec.must_have
    assert spec.must_have == [
        "windows10/11的谷歌浏览器插件",
        "总结网页内容",
        "把摘要同步直接放置在桌面",
    ]
    assert spec.evidence_aliases["windows10/11的谷歌浏览器插件"] == [
        "windows10/11的谷歌浏览器插件",
        "windows10",
        "的谷歌浏览器插件",
    ]
    assert spec.evidence_aliases["总结网页内容"] == ["总结网页内容"]
    assert "chrome extension" not in str(spec.evidence_aliases)
    assert "browser extension" not in str(spec.evidence_aliases)
    assert "desktop" not in str(spec.evidence_aliases)
    assert spec.interfaces == []


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

    assert len(llm.prompts) == 2
    assert spec.must_have
    assert not any(("Translate requirement " + "phrases") in prompt for prompt in llm.prompts)
    assert not any(("literal " + "English") in prompt for prompt in llm.prompts)


def test_uncertain_implementation_guess_is_demoted_from_must_have() -> None:
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
            "must_have": ["核心优化工具", "外部服务 API key"],
            "search_queries": ["核心优化工具"],
            "repo_search_queries": ["核心优化工具"],
            "code_search_queries": ["核心优化工具"],
            "topic_search_queries": ["核心优化工具"],
            "issue_search_queries": ["核心优化工具"],
            "evidence_aliases": {
                "核心优化工具": ["核心优化工具"],
                "外部服务 API key": ["external service api key"],
            },
        },
    )

    assert spec is not None
    assert spec.must_have == ["核心优化工具"]
    assert "外部服务 API key" in spec.nice_to_have
    assert parser._valid(spec) is True


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


def test_parser_turns_long_vibe_coding_request_into_short_capabilities() -> None:
    parser = SearchSpecParser()
    query = (
        "我想通过关键词进行星河平台相关热门内容和热门评论查询，必须有相关的截图，尤其是评论截图。"
        "最后生成一份pdf报告，记录相关关键词下的热度top10。最好支持多端运行，有个网页版，"
        "也有mcp可以通过bridgeBox直接运行"
    )

    assert parser._explicit_requirement_clauses(query) == [
        "星河平台相关热门内容和热门评论查询",
        "相关的截图",
        "评论截图",
        "生成PDF报告",
        "关键词热度Top 10",
        "多端运行",
        "网页版",
        "MCP可通过bridgeBox运行",
    ]
