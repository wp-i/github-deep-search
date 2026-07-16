from __future__ import annotations

import asyncio
from copy import deepcopy

import pytest

from github_deep_search.spec_parser import QUERY_CHANNEL_LIMITS, SearchSpecParser


REQUEST = "Find software that classifies sensor readings; exporting a dashboard is optional."


def _valid_plan() -> dict[str, object]:
    channels = {
        name: [f"sensor classification {name} {index}" for index in range(count)]
        for name, count in QUERY_CHANNEL_LIMITS.items()
    }
    channels["repo_search_queries"][0] = "传感器读数分类"
    return {
        "intent": "Find software that classifies sensor readings",
        "literal_keywords": ["sensor readings", "dashboard export"],
        "domains": ["sensor analysis"],
        "actions": ["classify", "export"],
        "objects": ["sensor readings", "dashboard"],
        "outputs": ["classification", "dashboard"],
        "interfaces": [],
        "must_have": ["classify sensor readings"],
        "nice_to_have": ["export a dashboard"],
        "negative_filters": [],
        **channels,
        "evidence_aliases": {
            "classify sensor readings": ["sensor reading classification"],
            "export a dashboard": ["dashboard export"],
        },
        "evidence_components": {
            "classify sensor readings": {
                "classify sensor readings": ["sensor reading classification"]
            },
            "export a dashboard": {
                "export a dashboard": ["dashboard export"]
            },
        },
    }


class _QueuedLLM:
    def __init__(self, *responses: dict[str, object] | None) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []

    async def json_chat(self, _system: str, prompt: str) -> dict[str, object] | None:
        self.prompts.append(prompt)
        return self.responses.pop(0)


def test_parser_accepts_one_complete_plan_without_downstream_stages() -> None:
    llm = _QueuedLLM(_valid_plan())

    spec = asyncio.run(SearchSpecParser().parse(REQUEST, llm))  # type: ignore[arg-type]

    assert len(llm.prompts) == 1
    assert spec.must_have == ["classify sensor readings"]
    assert spec.nice_to_have == ["export a dashboard"]
    assert spec.report_language == "en"
    assert set(spec.evidence_components) == {
        "classify sensor readings",
        "export a dashboard",
    }


def test_parser_retries_the_same_complete_plan_stage() -> None:
    invalid = _valid_plan()
    invalid["repo_search_queries"] = ["sensor classifier"]
    llm = _QueuedLLM(invalid, _valid_plan())

    spec = asyncio.run(SearchSpecParser().parse(REQUEST, llm))  # type: ignore[arg-type]

    assert spec.repo_search_queries[0] == "传感器读数分类"
    assert len(llm.prompts) == 2
    assert "repo_search_queries must contain exactly 10" in llm.prompts[1]
    assert "Regenerate the complete unchanged-request plan" in llm.prompts[1]


def test_parser_fails_after_three_invalid_complete_plans() -> None:
    invalid = _valid_plan()
    invalid["evidence_aliases"] = {}
    llm = _QueuedLLM(deepcopy(invalid), deepcopy(invalid), deepcopy(invalid))

    with pytest.raises(ValueError, match="evidence_aliases keys"):
        asyncio.run(SearchSpecParser().parse(REQUEST, llm))  # type: ignore[arg-type]

    assert len(llm.prompts) == 3


@pytest.mark.parametrize("channel,expected", QUERY_CHANNEL_LIMITS.items())
def test_query_channels_require_exact_capacity(channel: str, expected: int) -> None:
    plan = _valid_plan()
    plan[channel] = list(plan[channel])[: expected - 1]  # type: ignore[index]
    spec = SearchSpecParser()._from_llm_data(REQUEST, plan)

    errors = SearchSpecParser()._validation_errors(spec)

    assert f"{channel} must contain exactly {expected} distinct queries" in errors[0]


def test_repository_queries_must_include_chinese_and_english() -> None:
    plan = _valid_plan()
    plan["repo_search_queries"] = [
        f"sensor classification repository {index}" for index in range(10)
    ]
    spec = SearchSpecParser()._from_llm_data(REQUEST, plan)

    assert (
        "repo_search_queries must include current-request Chinese and English searches"
        in SearchSpecParser()._validation_errors(spec)
    )


def test_every_feature_requires_aliases_and_components() -> None:
    plan = _valid_plan()
    plan["evidence_aliases"] = {
        "classify sensor readings": ["sensor reading classification"]
    }
    plan["evidence_components"] = {
        "classify sensor readings": {
            "classify sensor readings": ["sensor reading classification"]
        }
    }
    spec = SearchSpecParser()._from_llm_data(REQUEST, plan)

    errors = SearchSpecParser()._validation_errors(spec)

    assert "evidence_aliases keys must exactly equal must_have plus nice_to_have" in errors
    assert "evidence_components keys must exactly equal must_have plus nice_to_have" in errors


def test_unrelated_plan_is_rejected_as_ungrounded() -> None:
    plan = _valid_plan()
    plan.update(
        {
            "intent": "Manage invoices",
            "literal_keywords": ["invoice"],
            "domains": ["accounting"],
            "actions": ["reconcile"],
            "objects": ["invoices"],
            "outputs": ["ledger"],
            "must_have": ["reconcile invoices"],
            "nice_to_have": [],
            "evidence_aliases": {"reconcile invoices": ["invoice reconciliation"]},
            "evidence_components": {
                "reconcile invoices": {
                    "reconcile invoices": ["invoice reconciliation"]
                }
            },
            "repo_search_queries": [
                "发票核对",
                *[f"invoice reconciliation repository {index}" for index in range(9)],
            ],
            "issue_search_queries": [
                f"invoice reconciliation issue {index}" for index in range(5)
            ],
        }
    )
    spec = SearchSpecParser()._from_llm_data(REQUEST, plan)

    assert "the plan is not grounded in the current request" in SearchSpecParser()._validation_errors(spec)


def test_numbered_workflow_is_preserved_as_structural_anchors() -> None:
    clauses = SearchSpecParser()._explicit_requirement_clauses(
        "1. collect readings 2. classify anomalies 3. export results"
    )

    assert clauses == ["collect readings", "classify anomalies", "export results"]


def test_no_llm_path_preserves_request_and_language() -> None:
    parser = SearchSpecParser()

    spec = asyncio.run(parser.parse("查找可以分析传感器读数的项目", None))

    assert spec.raw == "查找可以分析传感器读数的项目"
    assert spec.report_language == "zh"
    assert spec.must_have
    assert set(spec.evidence_aliases) == set(spec.must_have)


def test_prompt_contains_no_fixed_translation_or_repository_examples() -> None:
    prompt = SearchSpecParser._plan_prompt(REQUEST, [])

    assert REQUEST in prompt
    assert "static translation table" in prompt
    assert "known repository" in prompt
    assert "owner/" not in prompt
