from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

import pytest

from github_deep_search.config import Settings
from github_deep_search.models import (
    InputCoverage,
    ParsedRequirement,
    ReportLanguage,
    RunRequest,
    SearchQueryPair,
    ValidatedInput,
    VerificationRequirement,
)
from github_deep_search.pipeline import PipelineContext, PipelineFailure
from github_deep_search.providers.llm import LLMProviderError
from github_deep_search.stages.parse import ParseStage, _input_units


REALISTIC_PARSE_CASES = (
    (
        "  我想找一个可以在本地部署的知识库工具，支持 Markdown 导入、全文搜索和 Docker Compose，最好提供 REST API。  ",
        "zh",
        {
            "complete_requirement": "寻找可以本地部署的知识库工具，支持 Markdown 导入、全文搜索和 Docker Compose，并优先考虑提供 REST API 的项目。",
            "core_goal": "寻找可本地部署的开源知识库工具",
            "reasonable_interpretations": ["完整的知识库应用，而不是只提供单一搜索算法的底层库"],
            "functional_requirements": ["管理知识库内容", "导入 Markdown 内容", "提供全文搜索", "支持 Docker Compose 部署"],
            "constraints": ["可以在本地或自有环境部署"],
            "preferences": ["提供 REST API"],
            "exclusions": [],
            "github_language_qualifier": None,
            "search_query_pairs": [
                {
                    "purpose": "高召回寻找知识库应用",
                    "zh": "知识库 软件",
                    "en": "knowledge base software",
                },
                {
                    "purpose": "寻找支持内容导入和搜索的知识库应用",
                    "zh": "知识库 Markdown 全文搜索",
                    "en": "knowledge base Markdown full text search",
                },
                {
                    "purpose": "寻找便于本地部署的知识库应用",
                    "zh": "自托管 知识库 Docker Compose",
                    "en": "self hosted knowledge base Docker Compose",
                },
            ],
            "evidence_targets": [
                "README 或文档明确说明 Markdown 导入方式",
                "源码或文档证明存在全文搜索",
                "仓库包含可用的 Docker Compose 配置",
                "文档说明是否提供 REST API",
            ],
            "suggested_repositories": [],
        },
    ),
    (
        "I need a desktop transcription app for macOS and Windows that works fully offline and can export timestamped subtitles.",
        "en",
        {
            "complete_requirement": "Find a desktop transcription application for macOS and Windows that runs fully offline and exports subtitles with timestamps.",
            "core_goal": "Find a cross-platform offline desktop transcription application",
            "reasonable_interpretations": ["A user-facing desktop application rather than only a speech-to-text SDK"],
            "functional_requirements": ["Transcribe audio locally", "Export timestamped subtitle files"],
            "constraints": ["Support both macOS and Windows", "Work fully offline"],
            "preferences": [],
            "exclusions": ["Mandatory cloud transcription services"],
            "github_language_qualifier": None,
            "search_query_pairs": [
                {
                    "purpose": "Recall desktop transcription applications",
                    "zh": "桌面 音频转录 应用",
                    "en": "desktop audio transcription app",
                },
                {
                    "purpose": "Find transcription applications that work offline",
                    "zh": "离线 音频转录",
                    "en": "offline audio transcription",
                },
                {
                    "purpose": "Find transcription tools with subtitle export",
                    "zh": "音频转录 时间戳 字幕导出",
                    "en": "audio transcription timestamped subtitle export",
                },
            ],
            "evidence_targets": [
                "Release or build documentation supports macOS and Windows",
                "Documentation confirms transcription can run without a network service",
                "Documentation or source confirms timestamped subtitle export",
            ],
            "suggested_repositories": [],
        },
    ),
    (
        "我需要一个团队使用的自托管看板，必须支持权限管理、附件和审计日志，不依赖第三方云服务。",
        "zh",
        {
            "complete_requirement": "寻找供团队使用的自托管看板，必须具备权限管理、附件和审计日志，并且运行时不依赖第三方云服务。",
            "core_goal": "寻找具备团队治理能力的自托管看板应用",
            "reasonable_interpretations": ["看板指可供多人协作管理任务或工作流的应用"],
            "functional_requirements": ["团队看板协作", "权限管理", "附件管理", "审计日志"],
            "constraints": ["支持自托管", "必须支持权限管理、附件和审计日志"],
            "preferences": [],
            "exclusions": ["运行依赖第三方云服务"],
            "github_language_qualifier": None,
            "search_query_pairs": [
                {
                    "purpose": "高召回寻找团队看板应用",
                    "zh": "团队 看板 软件",
                    "en": "team kanban software",
                },
                {
                    "purpose": "寻找可以自托管的看板",
                    "zh": "自托管 看板",
                    "en": "self hosted kanban",
                },
                {
                    "purpose": "寻找具有权限和审计能力的看板",
                    "zh": "看板 权限 审计日志",
                    "en": "kanban permissions audit log",
                },
            ],
            "evidence_targets": [
                "部署文档证明可以自托管",
                "文档或源码证明存在权限控制",
                "文档或源码证明支持附件",
                "文档或源码证明记录审计日志",
                "配置和架构证明核心运行不强制依赖第三方云服务",
            ],
            "suggested_repositories": [],
        },
    ),
    (
        "Find a Python library for validating large JSON documents with streaming input, custom error messages, and no mandatory database.",
        "en",
        {
            "complete_requirement": "Find a Python library that validates large JSON documents from streaming input, supports custom error messages, and does not require a database.",
            "core_goal": "Find a Python library for streaming validation of large JSON documents",
            "reasonable_interpretations": ["Validation should avoid loading the complete JSON document into memory before processing"],
            "functional_requirements": ["Validate JSON documents", "Process streaming input", "Support custom error messages"],
            "constraints": ["Be usable as a Python library"],
            "preferences": [],
            "exclusions": ["Mandatory database dependency"],
            "github_language_qualifier": "language:Python",
            "search_query_pairs": [
                {
                    "purpose": "Recall Python JSON validation libraries",
                    "zh": "JSON 验证库",
                    "en": "JSON validation library",
                },
                {
                    "purpose": "Find streaming JSON parsers as partial-match foundations",
                    "zh": "流式 JSON 解析器",
                    "en": "streaming JSON parser",
                },
                {
                    "purpose": "Find standard JSON Schema validators",
                    "zh": "JSON Schema 验证器",
                    "en": "JSON Schema validator",
                },
            ],
            "evidence_targets": [
                "Package metadata and examples confirm Python library usage",
                "Source or documentation confirms incremental or streaming JSON processing",
                "API documentation confirms custom validation error messages",
                "Dependencies and setup confirm no mandatory database",
            ],
            "suggested_repositories": [],
        },
    ),
    (
        "想找一个 React component library，用于构建可访问的数据表格，需要虚拟滚动、键盘导航和 server-side pagination。",
        "zh",
        {
            "complete_requirement": "寻找用于构建可访问数据表格的 React 组件库，需要支持虚拟滚动、键盘导航和服务端分页。",
            "core_goal": "寻找支持大型可访问数据表格的 React 组件库",
            "reasonable_interpretations": ["组件库应提供可组合的数据表格能力，而不是仅提供一个成品页面"],
            "functional_requirements": ["构建数据表格", "虚拟滚动", "键盘导航", "服务端分页"],
            "constraints": ["适用于 React", "支持无障碍交互"],
            "preferences": [],
            "exclusions": [],
            "github_language_qualifier": None,
            "search_query_pairs": [
                {
                    "purpose": "高召回寻找 React 数据表格组件",
                    "zh": "React 数据表格 组件",
                    "en": "React data table component",
                },
                {
                    "purpose": "寻找支持无障碍交互的 React 表格",
                    "zh": "React 表格 无障碍 键盘导航",
                    "en": "React table accessibility keyboard navigation",
                },
                {
                    "purpose": "寻找支持虚拟滚动的 React 表格",
                    "zh": "React 表格 虚拟滚动",
                    "en": "React table virtual scrolling",
                },
            ],
            "evidence_targets": [
                "包和示例证明组件用于 React",
                "文档或源码证明支持虚拟滚动",
                "无障碍文档或交互代码证明支持键盘导航",
                "API 文档证明支持服务端分页",
            ],
            "suggested_repositories": [],
        },
    ),
)


def _classified_payload(payload: dict[str, Any], raw_input: str) -> dict[str, Any]:
    result = deepcopy(payload)
    functions = result.pop("functional_requirements")
    result["base_function"] = {
        "text": functions[0],
        "checks": ["基础能力核验"],
        "check_facets": ["capability"],
        "source_unit_ids": ["U01"],
    }
    result["classified_requirements"] = [
        {
            "kind": kind,
            "facet": {
                "constraint": "other_condition",
                "preference": "preference",
                "exclusion": "exclusion",
            }[kind],
            "text": text,
            "checks": ["待编号核验项"],
            "check_facets": [
                {
                    "constraint": "other_condition",
                    "preference": "preference",
                    "exclusion": "exclusion",
                }[kind]
            ],
            "source_unit_ids": ["U01"],
        }
        for field, kind in (
            ("constraints", "constraint"),
            ("preferences", "preference"),
            ("exclusions", "exclusion"),
        )
        for text in result.pop(field)
    ]
    result["classified_requirements"][:0] = [
        {
            "kind": "function",
            "facet": "capability",
            "text": text,
            "checks": ["待编号核验项"],
            "check_facets": ["capability"],
            "source_unit_ids": ["U01"],
        }
        for text in functions[1:]
    ]
    for index, item in enumerate(result["classified_requirements"], start=1):
        item["checks"] = [f"核验项-{index}"]
    function_checks = [
        result["base_function"]["checks"][0],
        *(
            check
            for item in result["classified_requirements"]
            if item["kind"] == "function"
            for check in item["checks"]
        ),
    ]
    for index, pair in enumerate(result["search_query_pairs"]):
        for language in ("zh", "en"):
            words = pair.pop(language).split()
            pair[f"{language}_terms"] = [
                " ".join(words[offset : offset + 3])
                for offset in range(0, len(words), 3)
            ]
        pair["mapped_requirement_check"] = (
            function_checks[index] if index < len(function_checks) else function_checks[0]
        )
    units = _input_units(raw_input)
    result["context_unit_ids"] = [unit.id for unit in units[1:]]
    return result


REALISTIC_PARSE_CASES = tuple(
    (raw_input, language, _classified_payload(payload, raw_input))
    for raw_input, language, payload in REALISTIC_PARSE_CASES
)


_UNSET = object()


def _audit_payload(response: dict[str, Any] | None) -> dict[str, Any] | None:
    if response is None:
        return None
    base = response["base_function"]
    requirements = [base]
    seen = {("function", base["text"].strip())}
    for item in response["classified_requirements"]:
        key = (item["kind"], item["text"].strip())
        if key in seen:
            continue
        seen.add(key)
        requirements.append(item)
    return {
        "base_requirement": base["text"],
        "audited_requirements": [
            {
                "id": f"V{index:03d}",
                "capability_checks": [
                    check
                    for check, facet in zip(
                        item["checks"], item["check_facets"], strict=True
                    )
                    if facet == "capability"
                ],
                "condition_checks": [
                    {"check": check, "facet": facet}
                    for check, facet in zip(
                        item["checks"], item["check_facets"], strict=True
                    )
                    if facet != "capability"
                ],
            }
            for index, item in enumerate(requirements, start=1)
        ],
        "search_query_pairs": deepcopy(response["search_query_pairs"]),
    }


class FakeParseClient:
    def __init__(
        self,
        response: dict[str, Any] | None,
        *,
        failure: LLMProviderError | None = None,
        audit_response: dict[str, Any] | None | object = _UNSET,
    ) -> None:
        self.response = response
        self.audit_response = audit_response
        self.last_failure = failure
        self.calls: list[dict[str, str]] = []
        self.closed = False

    async def json_chat(
        self,
        system: str,
        user: str,
        *,
        operation: str = "chat",
    ) -> dict[str, Any] | None:
        self.calls.append({"system": system, "user": user, "operation": operation})
        if operation == "audit_requirement_checks":
            if self.audit_response is _UNSET:
                return deepcopy(_audit_payload(self.response))
            return deepcopy(self.audit_response)  # type: ignore[arg-type]
        return deepcopy(self.response)

    async def close(self) -> None:
        self.closed = True


class RecordingControl:
    def __init__(self) -> None:
        self.warnings: list[tuple[str, str | None]] = []

    async def warning(self, message: str, *, stage=None) -> None:
        self.warnings.append((message, stage))


def test_input_units_expose_each_clause_of_a_long_realistic_request() -> None:
    units = _input_units(
        "我们要迁移 Markdown 文档和附件；需要中文全文搜索，并提供 API。"
    )

    assert [unit.text for unit in units] == [
        "我们要迁移 Markdown 文档",
        "附件",
        "需要中文全文搜索",
        "并提供 API",
    ]


def configured_settings() -> Settings:
    return Settings(
        github_token="github-token",
        llm_api_key="llm-key",
        llm_base_url="https://provider.example/v1",
        llm_model="model",
        llm_thinking=None,
        llm_reasoning_effort=None,
        max_github_requests=200,
        max_candidates=80,
        max_evidence_repositories=12,
        run_timeout_seconds=600,
    )


def context_for(raw_input: str, report_language: ReportLanguage) -> PipelineContext:
    return PipelineContext(
        run_id="run-id",
        request=RunRequest(raw_input=raw_input),
        settings=configured_settings(),
        validated_input=ValidatedInput(raw_input=raw_input, report_language=report_language),
    )


def expected_requirement(payload: dict[str, Any], raw_input: str) -> ParsedRequirement:
    language_qualifier = payload["github_language_qualifier"]

    def query(value: str) -> str:
        if language_qualifier is None:
            return value
        return f"{value} {language_qualifier}"

    by_kind = {
        kind: tuple(
            item["text"]
            for item in payload["classified_requirements"]
            if item["kind"] == kind
        )
        for kind in ("function", "constraint", "preference", "exclusion")
    }
    verification_requirements = (
        VerificationRequirement(
            kind="function",
            requirement=payload["base_function"]["text"],
            checks=tuple(payload["base_function"]["checks"]),
            facet="capability",
            source_unit_ids=tuple(payload["base_function"]["source_unit_ids"]),
            check_facets=tuple(payload["base_function"]["check_facets"]),
        ),
        *(
            VerificationRequirement(
                kind=item["kind"],
                requirement=item["text"],
                checks=tuple(item["checks"]),
                facet=item["facet"],
                source_unit_ids=tuple(item["source_unit_ids"]),
                check_facets=tuple(item["check_facets"]),
            )
            for item in payload["classified_requirements"]
        ),
    )
    units = _input_units(raw_input)
    checks_by_unit = {unit.id: [] for unit in units}
    for requirement in verification_requirements:
        for unit_id in requirement.source_unit_ids:
            checks_by_unit[unit_id].extend(requirement.checks)
    context_unit_ids = set(payload["context_unit_ids"])
    return ParsedRequirement(
        complete_requirement=payload["complete_requirement"],
        core_goal=payload["core_goal"],
        reasonable_interpretations=tuple(payload["reasonable_interpretations"]),
        functional_requirements=(
            payload["base_function"]["text"],
            *by_kind["function"],
        ),
        constraints=by_kind["constraint"],
        preferences=by_kind["preference"],
        exclusions=by_kind["exclusion"],
        search_query_pairs=tuple(
            SearchQueryPair(
                purpose=item["purpose"],
                zh=query(" ".join(item["zh_terms"])),
                en=query(" ".join(item["en_terms"])),
            )
            for item in payload["search_query_pairs"]
        ),
        evidence_targets=tuple(
            check
            for requirement in verification_requirements
            for check in requirement.checks
        ),
        suggested_repositories=tuple(payload["suggested_repositories"]),
        github_language_qualifier=language_qualifier,
        verification_requirements=verification_requirements,
        input_coverage=tuple(
            InputCoverage(
                unit_id=unit.id,
                text=unit.text,
                disposition=("context" if unit.id in context_unit_ids else "requirement"),
                requirement_checks=tuple(checks_by_unit[unit.id]),
                reason=(
                    "Marked as context by the requirement parser."
                    if unit.id in context_unit_ids
                    else "Mapped to requirement checks."
                ),
            )
            for unit in units
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(("raw_input", "report_language", "payload"), REALISTIC_PARSE_CASES)
async def test_parse_stage_structures_five_realistic_user_inputs(
    raw_input: str,
    report_language: ReportLanguage,
    payload: dict[str, Any],
) -> None:
    client = FakeParseClient(payload)
    stage = ParseStage(client=client)
    context = context_for(raw_input, report_language)

    await stage.execute(context)

    assert context.request.raw_input == raw_input
    assert context.validated_input is not None
    assert context.validated_input.raw_input == raw_input
    assert context.parsed_requirement == expected_requirement(payload, raw_input)
    assert len(client.calls) == 2
    assert client.calls[0]["operation"] == "parse_requirements"
    assert client.calls[1]["operation"] == "audit_requirement_checks"
    submitted = json.loads(client.calls[0]["user"])
    assert submitted == {
        "raw_input": raw_input,
        "report_language": report_language,
        "input_units": [
            {"id": unit.id, "text": unit.text} for unit in _input_units(raw_input)
        ],
    }


@pytest.mark.asyncio
async def test_parse_prompt_requires_independent_recall_queries_without_negative_terms() -> None:
    payload = deepcopy(REALISTIC_PARSE_CASES[3][2])
    client = FakeParseClient(payload)

    await ParseStage(client=client).execute(
        context_for(REALISTIC_PARSE_CASES[3][0], "en")
    )

    prompt = " ".join(client.calls[0]["system"].split())
    assert "parallel OR recall paths" in prompt
    assert "progressively narrowed AND refinements" in prompt
    assert "one authoritative list" in prompt
    assert "broad artifact and core project capability" in prompt
    assert "exactly one atomic evidence check" in prompt
    assert "Re-read raw_input clause by clause" in prompt
    assert "omit the base capability" in prompt
    assert "Do not put exclusions" in prompt
    assert "required-absent" in prompt
    assert "language:<label>" in prompt
    assert "must occur verbatim in raw_input" in prompt
    assert "Do not infer an implementation language" in prompt
    assert "provide up to 8 likely owner/name leads" in prompt
    assert "FINAL VALIDATION BEFORE RETURNING JSON" in prompt
    assert "Mandatory capabilities occur once as function" in prompt
    assert "positive inverse" in prompt
    assert "Every atomic requirement appears in evidence_targets" in prompt
    assert "source ids and context ids must be disjoint" in prompt
    assert "mapped_requirement_check contains one exact base or function check" in prompt
    assert "checks and check_facets have equal length" in prompt
    assert "zh_terms and en_terms each contain one or two terms" in prompt
    audit_prompt = " ".join(client.calls[1]["system"].split())
    assert "atomize the evidence checks" in audit_prompt
    assert "Do not search, recommend, add, remove, merge, reclassify" in audit_prompt
    assert "every scope, natural_language, scale, format" in audit_prompt
    assert "self-contained relationship" in audit_prompt
    assert "Remove every modifier that narrows the operation" in audit_prompt
    assert "condition_checks cannot be empty" in audit_prompt
    assert "unchanged qualified function text" in audit_prompt
    assert "return 2 to 4 final parallel search query pairs" in audit_prompt
    assert "capability_checks" in audit_prompt
    assert "capability is forbidden in condition_checks" in audit_prompt
    assert "A function condition must not use preference or exclusion" in audit_prompt
    assert "Every preference condition uses preference" in audit_prompt
    assert "every exclusion condition uses exclusion" in audit_prompt
    assert "base_requirement may rewrite only" in audit_prompt
    audit_request = json.loads(client.calls[1]["user"])
    assert audit_request["initial_search_query_pairs"] == payload["search_query_pairs"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload.pop("core_goal"),
        lambda payload: payload.update(complete_requirement="  "),
        lambda payload: payload.update(reasonable_interpretations=[]),
        lambda payload: payload.update(classified_requirements="not-an-array"),
        lambda payload: payload.update(base_function={"text": "  ", "checks": ["x"]}),
        lambda payload: payload.update(base_function={"text": "Capability", "checks": []}),
        lambda payload: payload.update(
            base_function={
                "text": "Capability",
                "checks": ["check one", "check two"],
                "source_unit_ids": ["U01"],
            }
        ),
        lambda payload: payload.update(base_function={"text": "Capability"}),
        lambda payload: payload.update(
            classified_requirements=[
                {"kind": "unknown", "text": "Capability", "checks": ["Capability"]}
            ]
        ),
        lambda payload: payload.update(
            classified_requirements=[
                {"kind": "function", "text": "Capability", "checks": []}
            ]
        ),
        lambda payload: payload.update(
            classified_requirements=[
                {
                    "kind": "function",
                    "text": "Capability",
                    "checks": ["check", "check"],
                }
            ]
        ),
        lambda payload: payload.update(
            classified_requirements=[
                {
                    "kind": "function",
                    "text": "Capability one",
                    "checks": ["shared check"],
                },
                {
                    "kind": "constraint",
                    "text": "Condition two",
                    "checks": ["shared check"],
                },
            ]
        ),
        lambda payload: payload.update(github_language_qualifier=123),
        lambda payload: payload.update(github_language_qualifier="Python"),
        lambda payload: payload.update(search_query_pairs=[{"purpose": "goal", "zh": "查询"}]),
        lambda payload: payload["search_query_pairs"][0].update(
            mapped_requirement_check=payload["classified_requirements"][0]["checks"][0]
        ),
        lambda payload: payload["search_query_pairs"][0].update(
            mapped_requirement_check=["one", "two"]
        ),
        lambda payload: payload.update(search_query_pairs=payload["search_query_pairs"][:1]),
        lambda payload: payload.update(
            search_query_pairs=[
                {**payload["search_query_pairs"][0], "purpose": f"purpose-{index}"}
                for index in range(5)
            ]
        ),
        lambda payload: payload.update(evidence_targets=[]),
        lambda payload: payload.update(suggested_repositories=["not-a-full-name"]),
        lambda payload: payload.update(
            suggested_repositories=[f"owner/repository-{index}" for index in range(9)]
        ),
        lambda payload: payload.update(unexpected="field"),
    ],
)
async def test_parse_stage_rejects_invalid_structures_without_retry(mutate) -> None:
    payload = deepcopy(REALISTIC_PARSE_CASES[0][2])
    mutate(payload)
    client = FakeParseClient(payload)
    stage = ParseStage(client=client)
    pipeline_context = context_for("Build a local knowledge tool", "en")

    with pytest.raises(PipelineFailure) as caught:
        await stage.execute(pipeline_context)

    assert caught.value.code == "invalid_parse_output"
    assert len(client.calls) == 1
    assert pipeline_context.usage.warnings[0].startswith("Parse output validation failed: ")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload["context_unit_ids"].pop(),
        lambda payload: payload["context_unit_ids"].__setitem__(0, "unknown"),
        lambda payload: payload["context_unit_ids"].append(
            payload["context_unit_ids"][0]
        ),
        lambda payload: payload["base_function"].update(source_unit_ids=["U02"]),
    ],
)
async def test_parse_stage_rejects_incomplete_or_inconsistent_input_coverage(
    mutate,
) -> None:
    raw_input, _, original = REALISTIC_PARSE_CASES[0]
    payload = deepcopy(original)
    mutate(payload)
    client = FakeParseClient(payload)
    pipeline_context = context_for(raw_input, "zh")

    with pytest.raises(PipelineFailure) as caught:
        await ParseStage(client=client).execute(pipeline_context)

    assert caught.value.code == "invalid_parse_output"
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_parse_stage_preserves_multiple_atomic_checks_and_facets() -> None:
    raw_input, _, original = REALISTIC_PARSE_CASES[0]
    payload = deepcopy(original)
    item = payload["classified_requirements"][0]
    old_check = item["checks"][0]
    item["checks"] = ["导入文档", "支持 Markdown 格式"]
    item["check_facets"] = ["capability", "format"]
    for pair in payload["search_query_pairs"]:
        if pair["mapped_requirement_check"] == old_check:
            pair["mapped_requirement_check"] = item["checks"][0]
    pipeline_context = context_for(raw_input, "zh")

    await ParseStage(client=FakeParseClient(payload)).execute(pipeline_context)

    assert pipeline_context.parsed_requirement is not None
    requirement = pipeline_context.parsed_requirement.verification_requirements[1]
    assert requirement.checks == ("导入文档", "支持 Markdown 格式")
    assert requirement.check_facets == ("capability", "format")


@pytest.mark.asyncio
async def test_parse_audit_keeps_language_qualified_search_as_two_atomic_checks() -> None:
    raw_input, _, original = REALISTIC_PARSE_CASES[0]
    payload = deepcopy(original)
    search_item = next(
        item
        for item in payload["classified_requirements"]
        if item["kind"] == "function" and item["text"] == "提供全文搜索"
    )
    old_check = search_item["checks"][0]
    search_item["text"] = "支持中文全文搜索"
    search_item["checks"] = ["支持中文全文搜索"]
    for pair in payload["search_query_pairs"]:
        if pair["mapped_requirement_check"] == old_check:
            pair["mapped_requirement_check"] = "支持中文全文搜索"
    audit = _audit_payload(payload)
    assert audit is not None
    requirement_id = f"V{payload['classified_requirements'].index(search_item) + 2:03d}"
    audited_item = next(
        item
        for item in audit["audited_requirements"]
        if item["id"] == requirement_id
    )
    audited_item["capability_checks"] = ["提供全文搜索"]
    audited_item["condition_checks"] = [
        {
            "check": "全文搜索支持中文内容",
            "facet": "natural_language",
        }
    ]
    for pair in audit["search_query_pairs"]:
        if pair["mapped_requirement_check"] in {old_check, "支持中文全文搜索"}:
            pair["mapped_requirement_check"] = "提供全文搜索"

    context = context_for(raw_input, "zh")
    await ParseStage(
        client=FakeParseClient(payload, audit_response=audit)
    ).execute(context)

    assert context.parsed_requirement is not None
    requirement = next(
        item
        for item in context.parsed_requirement.verification_requirements
        if item.requirement == "支持中文全文搜索"
    )
    assert requirement.checks == ("提供全文搜索", "全文搜索支持中文内容")
    assert requirement.check_facets == ("capability", "natural_language")


@pytest.mark.asyncio
async def test_parse_audit_replaces_only_checks_and_rebuilds_evidence_targets() -> None:
    raw_input, _, original = REALISTIC_PARSE_CASES[0]
    payload = deepcopy(original)
    audit = _audit_payload(payload)
    assert audit is not None
    audit["audited_requirements"][1]["capability_checks"] = ["执行每日任务"]
    audit["audited_requirements"][1]["condition_checks"] = [
        {"check": "覆盖指定任务范围", "facet": "scope"}
    ]
    for pair in audit["search_query_pairs"]:
        if pair["mapped_requirement_check"] == payload["classified_requirements"][0]["checks"][0]:
            pair["mapped_requirement_check"] = "执行每日任务"
    client = FakeParseClient(payload, audit_response=audit)
    pipeline_context = context_for(raw_input, "zh")

    await ParseStage(client=client).execute(pipeline_context)

    assert pipeline_context.parsed_requirement is not None
    requirement = pipeline_context.parsed_requirement.verification_requirements[1]
    assert requirement.requirement == payload["classified_requirements"][0]["text"]
    assert requirement.kind == payload["classified_requirements"][0]["kind"]
    assert requirement.source_unit_ids == tuple(
        payload["classified_requirements"][0]["source_unit_ids"]
    )
    assert requirement.checks == ("执行每日任务", "覆盖指定任务范围")
    assert pipeline_context.parsed_requirement.evidence_targets == tuple(
        check
        for item in audit["audited_requirements"]
        for check in (
            *item["capability_checks"],
            *(condition["check"] for condition in item["condition_checks"]),
        )
    )
    assert [call["operation"] for call in client.calls] == [
        "parse_requirements",
        "audit_requirement_checks",
    ]


@pytest.mark.asyncio
async def test_parse_audit_corrects_initial_query_mapping_without_a_third_call() -> None:
    raw_input, _, original = REALISTIC_PARSE_CASES[0]
    payload = deepcopy(original)
    original_function_mapping = payload["search_query_pairs"][1][
        "mapped_requirement_check"
    ]
    constraint_check = next(
        item["checks"][0]
        for item in payload["classified_requirements"]
        if item["kind"] == "constraint"
    )
    payload["search_query_pairs"][1]["mapped_requirement_check"] = constraint_check
    audit = _audit_payload(payload)
    assert audit is not None
    audit["search_query_pairs"][1][
        "mapped_requirement_check"
    ] = original_function_mapping
    client = FakeParseClient(payload, audit_response=audit)
    pipeline_context = context_for(raw_input, "zh")

    await ParseStage(client=client).execute(pipeline_context)

    assert pipeline_context.parsed_requirement is not None
    assert len(pipeline_context.parsed_requirement.search_query_pairs) >= 2
    assert [call["operation"] for call in client.calls] == [
        "parse_requirements",
        "audit_requirement_checks",
    ]


@pytest.mark.asyncio
async def test_parse_audit_can_narrow_only_the_base_requirement_text() -> None:
    raw_input, _, original = REALISTIC_PARSE_CASES[0]
    payload = deepcopy(original)
    payload["base_function"]["text"] = "带限定的游戏自动化工具"
    audit = _audit_payload(payload)
    assert audit is not None
    audit["base_requirement"] = "游戏自动化工具"
    client = FakeParseClient(payload, audit_response=audit)
    pipeline_context = context_for(raw_input, "zh")

    await ParseStage(client=client).execute(pipeline_context)

    assert pipeline_context.parsed_requirement is not None
    assert pipeline_context.parsed_requirement.functional_requirements[0] == (
        "游戏自动化工具"
    )
    assert pipeline_context.parsed_requirement.verification_requirements[1].requirement == (
        payload["classified_requirements"][0]["text"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["missing", "unknown", "duplicate_check"])
async def test_parse_stage_rejects_invalid_requirement_audit_without_retry(
    mutation: str,
) -> None:
    raw_input, _, original = REALISTIC_PARSE_CASES[0]
    payload = deepcopy(original)
    audit = _audit_payload(payload)
    assert audit is not None
    if mutation == "missing":
        audit["audited_requirements"].pop()
    elif mutation == "unknown":
        audit["audited_requirements"][0]["id"] = "unknown"
    else:
        audit["audited_requirements"][1]["capability_checks"] = deepcopy(
            audit["audited_requirements"][0]["capability_checks"]
        )
        audit["audited_requirements"][1]["condition_checks"] = []
    client = FakeParseClient(payload, audit_response=audit)
    pipeline_context = context_for(raw_input, "zh")

    with pytest.raises(PipelineFailure) as caught:
        await ParseStage(client=client).execute(pipeline_context)

    assert caught.value.code == "invalid_parse_output"
    assert len(client.calls) == 2
    assert pipeline_context.usage.warnings[0].startswith(
        "Parse audit validation failed:"
    )


@pytest.mark.asyncio
async def test_parse_stage_distinguishes_audit_provider_failure_from_invalid_output() -> None:
    raw_input, _, original = REALISTIC_PARSE_CASES[0]
    failure_client = FakeParseClient(
        deepcopy(original),
        failure=LLMProviderError("provider unavailable", retryable=True),
        audit_response=None,
    )
    invalid_client = FakeParseClient(deepcopy(original), audit_response=None)

    with pytest.raises(PipelineFailure) as failed:
        await ParseStage(client=failure_client).execute(context_for(raw_input, "zh"))
    with pytest.raises(PipelineFailure) as invalid:
        await ParseStage(client=invalid_client).execute(context_for(raw_input, "zh"))

    assert failed.value.code == "llm_parse_audit_failed"
    assert invalid.value.code == "invalid_parse_output"
    assert len(failure_client.calls) == 2
    assert len(invalid_client.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["function_without_capability", "constraint_capability"])
async def test_parse_stage_rejects_audit_capability_boundary_violations(
    mutation: str,
) -> None:
    raw_input, _, original = REALISTIC_PARSE_CASES[0]
    payload = deepcopy(original)
    audit = _audit_payload(payload)
    assert audit is not None
    if mutation == "function_without_capability":
        audit["audited_requirements"][0]["capability_checks"] = []
        audit["audited_requirements"][0]["condition_checks"] = [
            {"check": "错误限定", "facet": "scope"}
        ]
    else:
        constraint_offset = next(
            index
            for index, item in enumerate(payload["classified_requirements"], start=1)
            if item["kind"] == "constraint"
        )
        audit["audited_requirements"][constraint_offset][
            "capability_checks"
        ] = ["错误能力"]

    with pytest.raises(PipelineFailure) as caught:
        await ParseStage(
            client=FakeParseClient(payload, audit_response=audit)
        ).execute(context_for(raw_input, "zh"))

    assert caught.value.code == "invalid_parse_output"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case_index", "kind", "invalid_facet"),
    [
        (0, "function", "preference"),
        (0, "constraint", "exclusion"),
        (0, "preference", "scope"),
        (1, "exclusion", "runtime"),
    ],
)
async def test_parse_stage_rejects_audit_condition_facets_from_another_kind(
    case_index: int,
    kind: str,
    invalid_facet: str,
) -> None:
    raw_input, report_language, original = REALISTIC_PARSE_CASES[case_index]
    payload = deepcopy(original)
    audit = _audit_payload(payload)
    assert audit is not None
    requirement_offset = next(
        index
        for index, item in enumerate(payload["classified_requirements"], start=1)
        if item["kind"] == kind
    )
    audit_item = audit["audited_requirements"][requirement_offset]
    audit_item["condition_checks"] = [
        {"check": f"invalid {kind} condition", "facet": invalid_facet}
    ]

    with pytest.raises(PipelineFailure) as caught:
        await ParseStage(
            client=FakeParseClient(payload, audit_response=audit)
        ).execute(context_for(raw_input, report_language))

    assert caught.value.code == "invalid_parse_output"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload["classified_requirements"][0].update(
            checks=["能力一", "限定一"], check_facets=["capability"]
        ),
        lambda payload: payload["classified_requirements"][0].update(
            check_facets=["format"]
        ),
    ],
)
async def test_parse_stage_rejects_misaligned_check_facets(mutate) -> None:
    raw_input, _, original = REALISTIC_PARSE_CASES[0]
    payload = deepcopy(original)
    mutate(payload)
    pipeline_context = context_for(raw_input, "zh")

    with pytest.raises(PipelineFailure) as caught:
        await ParseStage(client=FakeParseClient(payload)).execute(pipeline_context)

    assert caught.value.code == "invalid_parse_output"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload["search_query_pairs"][0].update(
            zh_terms=["一", "二", "三"]
        ),
        lambda payload: payload["search_query_pairs"][0].update(
            en_terms=["one two three four"]
        ),
        lambda payload: payload["search_query_pairs"][0].update(
            mapped_requirement_check=payload["classified_requirements"][0]["checks"][0]
        ),
        lambda payload: payload["search_query_pairs"][1].update(
            mapped_requirement_check=next(
                item["checks"][0]
                for item in payload["classified_requirements"]
                if item["kind"] == "constraint"
            )
        ),
    ],
)
async def test_parse_stage_rejects_invalid_query_mapping_or_term_budget(mutate) -> None:
    raw_input, _, original = REALISTIC_PARSE_CASES[0]
    payload = deepcopy(original)
    mutate(payload)
    pipeline_context = context_for(raw_input, "zh")

    with pytest.raises(PipelineFailure) as caught:
        await ParseStage(client=FakeParseClient(payload)).execute(pipeline_context)

    assert caught.value.code == "invalid_parse_output"


@pytest.mark.asyncio
async def test_parse_ignores_and_warns_for_language_qualifier_not_explicit_in_input() -> None:
    payload = deepcopy(REALISTIC_PARSE_CASES[4][2])
    payload["github_language_qualifier"] = "language:TypeScript"
    client = FakeParseClient(payload)
    control = RecordingControl()
    context = context_for(REALISTIC_PARSE_CASES[4][0], "zh")
    context.control = control  # type: ignore[assignment]

    await ParseStage(client=client).execute(context)

    assert context.parsed_requirement is not None
    assert context.parsed_requirement.github_language_qualifier is None
    assert all(
        "language:" not in query
        for pair in context.parsed_requirement.search_query_pairs
        for query in (pair.zh, pair.en)
    )
    assert control.warnings == [
        (
            "An inferred GitHub language filter was ignored because the language "
            "was not explicit in the user input.",
            "parse",
        )
    ]
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_parse_stage_trims_and_removes_only_exact_duplicates() -> None:
    payload = deepcopy(REALISTIC_PARSE_CASES[0][2])
    payload["base_function"] = {
        "text": "base capability",
        "checks": ["base capability"],
        "check_facets": ["capability"],
        "source_unit_ids": ["U01"],
    }
    payload["classified_requirements"] = [
        {
            "kind": "function",
            "facet": "capability",
            "text": "  capability  ",
            "checks": ["check one"],
            "check_facets": ["capability"],
            "source_unit_ids": ["U01"],
        },
        {
            "kind": "function",
            "facet": "capability",
            "text": "capability",
            "checks": ["check one"],
            "check_facets": ["capability"],
            "source_unit_ids": ["U01"],
        },
        {
            "kind": "function",
            "facet": "capability",
            "text": "Capability",
            "checks": ["check two"],
            "check_facets": ["capability"],
            "source_unit_ids": ["U01"],
        },
    ]
    payload["context_unit_ids"] = []
    query = {
        "purpose": " purpose ",
        "zh_terms": [" 查询 "],
        "en_terms": [" query "],
        "mapped_requirement_check": "base capability",
    }
    payload["search_query_pairs"] = [
        query,
        deepcopy(query),
        {
            "purpose": "secondary",
            "zh_terms": ["次要查询"],
            "en_terms": ["secondary query"],
            "mapped_requirement_check": "check one",
        },
    ]
    payload["suggested_repositories"] = [
        "owner/repository",
        "owner/repository",
        "LingDong-/wax",
    ]
    client = FakeParseClient(payload)
    context = context_for("Find a project", "en")

    await ParseStage(client=client).execute(context)

    assert context.parsed_requirement is not None
    assert context.parsed_requirement.functional_requirements == (
        "base capability",
        "capability",
        "Capability",
    )
    assert context.parsed_requirement.search_query_pairs == (
        SearchQueryPair(purpose="purpose", zh="查询", en="query"),
        SearchQueryPair(purpose="secondary", zh="次要查询", en="secondary query"),
    )
    assert context.parsed_requirement.suggested_repositories == (
        "owner/repository",
        "LingDong-/wax",
    )


@pytest.mark.asyncio
async def test_parse_stage_rejects_the_same_requirement_in_multiple_categories() -> None:
    payload = deepcopy(REALISTIC_PARSE_CASES[0][2])
    duplicated = payload["classified_requirements"][0]["text"]
    payload["classified_requirements"].append(
        {
            "kind": "exclusion",
            "facet": "exclusion",
            "text": duplicated,
            "checks": [duplicated],
            "check_facets": ["exclusion"],
            "source_unit_ids": ["U01"],
        }
    )
    client = FakeParseClient(payload)

    with pytest.raises(PipelineFailure) as caught:
        await ParseStage(client=client).execute(context_for("Find a project", "en"))

    assert caught.value.code == "invalid_parse_output"
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_parse_stage_distinguishes_provider_failure_from_invalid_json() -> None:
    provider_client = FakeParseClient(
        None,
        failure=LLMProviderError("provider unavailable", retryable=True),
    )
    invalid_json_client = FakeParseClient(None)

    with pytest.raises(PipelineFailure) as provider_failure:
        await ParseStage(client=provider_client).execute(context_for("Find a project", "en"))
    with pytest.raises(PipelineFailure) as invalid_output:
        await ParseStage(client=invalid_json_client).execute(context_for("Find a project", "en"))

    assert provider_failure.value.code == "llm_parse_failed"
    assert invalid_output.value.code == "invalid_parse_output"
    assert len(provider_client.calls) == 1
    assert len(invalid_json_client.calls) == 1


@pytest.mark.asyncio
async def test_parse_stage_requires_validated_input_before_calling_llm() -> None:
    client = FakeParseClient(REALISTIC_PARSE_CASES[0][2])
    context = PipelineContext(
        run_id="run-id",
        request=RunRequest(raw_input="Find a project"),
        settings=configured_settings(),
    )

    with pytest.raises(PipelineFailure) as caught:
        await ParseStage(client=client).execute(context)

    assert caught.value.code == "parse_prerequisite_missing"
    assert client.calls == []


@pytest.mark.asyncio
async def test_parse_stage_closes_its_llm_client() -> None:
    client = FakeParseClient(REALISTIC_PARSE_CASES[0][2])
    stage = ParseStage(client=client)

    await stage.aclose()

    assert client.closed is True
