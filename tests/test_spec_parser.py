from __future__ import annotations

import asyncio
from copy import deepcopy

import pytest

from github_deep_search.spec_parser import QUERY_CHANNEL_LIMITS, SearchSpecParser
from github_deep_search.providers.llm import LLMProviderError


REQUEST = "Find software that classifies sensor readings; exporting a dashboard is optional."
CORE = "classify sensor readings"


def _valid_roles() -> dict[str, object]:
    return {
        "requirement_items": [
            {
                "kind": "capability",
                "statement": CORE,
                "scope": "",
                "action": "classify",
                "primary_object": "sensor readings",
            },
            {"kind": "preference", "statement": "export a dashboard"},
        ],
    }


def _roles_for_plan(plan: dict[str, object]) -> dict[str, object]:
    core = str(plan["core_requirement"])
    return {
        "requirement_items": [
            {
                "kind": "capability",
                "statement": core,
                "scope": "",
                "action": "represent",
                "primary_object": core,
            },
            *[
                {"kind": "preference", "statement": item}
                for item in plan["nice_to_have"]
            ],
        ],
    }


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
        "core_requirement": CORE,
        "hard_constraints": [],
        "nice_to_have": ["export a dashboard"],
        "negative_filters": [],
        **channels,
        "evidence_aliases": {
            CORE: ["sensor reading classification"],
            "export a dashboard": ["dashboard export"],
        },
        "evidence_components": {
            CORE: {
                "classify sensor readings": ["sensor reading classification"]
            },
            "export a dashboard": {
                "export a dashboard": ["dashboard export"]
            },
        },
    }


def _plan_with_named_domain() -> tuple[dict[str, object], str]:
    plan = _valid_plan()
    feature = "Aster sensor classifier: classify Aster sensor readings"
    plan["domains"] = ["Aster"]
    plan["core_requirement"] = feature
    plan["evidence_aliases"] = {
        feature: ["Aster sensor reading classification"],
        "export a dashboard": ["dashboard export"],
    }
    plan["evidence_components"] = {
        feature: {
            "classify sensor readings": ["sensor reading classification"],
        },
        "export a dashboard": {
            "export a dashboard": ["dashboard export"],
        },
    }
    return plan, feature


class _QueuedLLM:
    def __init__(self, *responses: dict[str, object] | None) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []

    async def json_chat(self, _system: str, prompt: str) -> dict[str, object] | None:
        self.prompts.append(prompt)
        return self.responses.pop(0)


class _FailingProviderLLM:
    def __init__(self) -> None:
        self.calls = 0
        self.last_failure: LLMProviderError | None = None

    async def json_chat(self, _system: str, _prompt: str) -> None:
        self.calls += 1
        self.last_failure = LLMProviderError(
            "The configured LLM provider rejected the request (HTTP 402).",
            retryable=False,
            status_code=402,
        )
        return None


def test_parser_accepts_one_complete_plan_without_downstream_stages() -> None:
    llm = _QueuedLLM(_valid_roles(), _valid_plan())

    spec = asyncio.run(SearchSpecParser().parse(REQUEST, llm))  # type: ignore[arg-type]

    assert len(llm.prompts) == 2
    assert "requirement roles only" in llm.prompts[0]
    assert "Fixed requirement roles" in llm.prompts[1]
    assert spec.must_have == [CORE]
    assert spec.nice_to_have == ["export a dashboard"]
    assert spec.report_language == "en"
    assert set(spec.evidence_components) == {
        CORE,
        "export a dashboard",
    }


def test_role_stage_projects_exactly_one_core_and_no_hard_constraints() -> None:
    roles = SearchSpecParser()._roles_from_llm_data(_valid_roles())

    assert roles is not None
    assert roles.as_dict() == {
        "core_requirement": CORE,
        "hard_constraints": [],
        "nice_to_have": ["export a dashboard"],
    }


def test_role_stage_rejects_a_malformed_shape_before_plan_generation() -> None:
    llm = _QueuedLLM(
        {
            **_valid_roles(),
            "repo_search_queries": ["must not be generated here"],
        }
    )

    with pytest.raises(ValueError, match="role generation failed structural validation"):
        asyncio.run(SearchSpecParser().parse(REQUEST, llm))  # type: ignore[arg-type]

    assert len(llm.prompts) == 1


def test_role_stage_rejects_the_superseded_five_slot_shape() -> None:
    llm = _QueuedLLM(
        {
            "primary_product_form": "sensor-reading classifier",
            "primary_user_job": "classify sensor readings",
            "adoption_constraints": [],
            "environment_preferences": [],
            "experience_preferences": ["export a dashboard"],
        }
    )

    with pytest.raises(ValueError, match="role generation failed structural validation"):
        asyncio.run(SearchSpecParser().parse(REQUEST, llm))  # type: ignore[arg-type]

    assert len(llm.prompts) == 1


def test_role_stage_rejects_an_exact_cross_role_duplicate() -> None:
    duplicate = _valid_roles()
    duplicate["requirement_items"] = [
        *duplicate["requirement_items"],  # type: ignore[list-item]
        {"kind": "preference", "statement": CORE},
    ]
    llm = _QueuedLLM(duplicate)

    with pytest.raises(ValueError, match="exact normalized duplicate"):
        asyncio.run(SearchSpecParser().parse(REQUEST, llm))  # type: ignore[arg-type]


def test_role_slots_project_to_canonical_roles_without_semantic_reclassification() -> None:
    data = _valid_roles()
    data["requirement_items"] = [
        data["requirement_items"][0],  # type: ignore[index]
        {
            "kind": "capability",
            "statement": "export a dashboard",
            "scope": "",
            "action": "export",
            "primary_object": "a dashboard",
        },
        {"kind": "preference", "statement": "available under a permissive license"},
        {"kind": "preference", "statement": "available in a portable runtime"},
    ]
    parser = SearchSpecParser()

    roles = parser._roles_from_llm_data(data)

    assert roles is not None
    assert roles.core_requirement == CORE
    assert roles.hard_constraints == ()
    assert roles.nice_to_have == (
        "export a dashboard",
        "available under a permissive license",
        "available in a portable runtime",
    )


def test_complete_plan_retries_if_it_reclassifies_the_frozen_roles() -> None:
    reclassified = _valid_plan()
    reclassified["core_requirement"] = "export a dashboard"
    reclassified["nice_to_have"] = [CORE]
    llm = _QueuedLLM(_valid_roles(), reclassified, _valid_plan())

    spec = asyncio.run(SearchSpecParser().parse(REQUEST, llm))  # type: ignore[arg-type]

    assert spec.must_have == [CORE]
    assert spec.nice_to_have == ["export a dashboard"]
    assert len(llm.prompts) == 3
    assert "plan must copy the fixed core_requirement" in llm.prompts[2]
    assert "plan must copy the fixed nice_to_have list exactly" in llm.prompts[2]


def test_complete_plan_retries_if_it_adds_a_positive_hard_constraint() -> None:
    invalid = _valid_plan()
    added = "available under a permissive license"
    invalid["hard_constraints"] = [added]
    invalid["evidence_aliases"][added] = ["permissive license"]  # type: ignore[index]
    invalid["evidence_components"][added] = {  # type: ignore[index]
        "license": ["permissive license"]
    }
    llm = _QueuedLLM(_valid_roles(), invalid, _valid_plan())

    spec = asyncio.run(SearchSpecParser().parse(REQUEST, llm))  # type: ignore[arg-type]

    assert spec.must_have == [CORE]
    assert len(llm.prompts) == 3
    assert "plan must copy the fixed core_requirement and hard_constraints exactly" in llm.prompts[2]


def test_parser_rejects_missing_core_and_legacy_requirement_fields() -> None:
    plan = _valid_plan()
    plan.pop("core_requirement")
    plan["must_have"] = ["classify sensor readings"]
    plan["core_requirements"] = ["classify sensor readings"]
    plan["hard_constraints"] = ["available under a permissive license"]

    spec = SearchSpecParser()._from_llm_data(REQUEST, plan)

    assert spec is not None
    assert "must_have is empty" in SearchSpecParser()._validation_errors(spec)


def test_parser_retries_the_same_complete_plan_stage() -> None:
    invalid = _valid_plan()
    invalid["repo_search_queries"] = ["sensor classifier"]
    llm = _QueuedLLM(_valid_roles(), invalid, _valid_plan())

    spec = asyncio.run(SearchSpecParser().parse(REQUEST, llm))  # type: ignore[arg-type]

    assert spec.repo_search_queries[0] == "传感器读数分类"
    assert len(llm.prompts) == 3
    assert "repo_search_queries must contain exactly 10" in llm.prompts[2]
    assert "Regenerate the complete unchanged-request plan" in llm.prompts[2]


def test_parser_fails_after_three_invalid_complete_plans() -> None:
    invalid = _valid_plan()
    invalid["evidence_aliases"] = {}
    llm = _QueuedLLM(
        _valid_roles(),
        deepcopy(invalid),
        deepcopy(invalid),
        deepcopy(invalid),
    )

    with pytest.raises(ValueError, match="evidence_aliases keys"):
        asyncio.run(SearchSpecParser().parse(REQUEST, llm))  # type: ignore[arg-type]

    assert len(llm.prompts) == 4


def test_parser_preserves_provider_failure_from_required_role_stage() -> None:
    llm = _FailingProviderLLM()

    with pytest.raises(LLMProviderError, match="HTTP 402") as raised:
        asyncio.run(SearchSpecParser().parse(REQUEST, llm))  # type: ignore[arg-type]

    assert raised.value.retryable is False
    assert llm.calls == 1


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
        CORE: ["sensor reading classification"]
    }
    plan["evidence_components"] = {
        CORE: {
            "classify sensor readings": ["sensor reading classification"]
        }
    }
    spec = SearchSpecParser()._from_llm_data(REQUEST, plan)

    errors = SearchSpecParser()._validation_errors(spec)

    assert "evidence_aliases keys must exactly equal must_have plus nice_to_have" in errors
    assert "evidence_components keys must exactly equal must_have plus nice_to_have" in errors


def test_components_cover_named_domains_used_by_the_feature() -> None:
    plan, feature = _plan_with_named_domain()
    plan["evidence_components"][feature].update(  # type: ignore[index]
        {"As": ["prefix"], "ter": ["suffix"]}
    )
    parser = SearchSpecParser()

    incomplete = parser._from_llm_data(REQUEST, plan)
    errors = parser._validation_errors(incomplete)

    assert (
        f"evidence_components[{feature}] does not cover named domain: Aster"
        in errors
    )

    plan["evidence_components"][feature]["Aster source"] = ["Aster"]  # type: ignore[index]
    complete = parser._from_llm_data(REQUEST, plan)

    assert not any(
        "does not cover named domain" in error
        for error in parser._validation_errors(complete)
    )


def test_parser_retries_when_components_omit_a_named_domain() -> None:
    invalid, feature = _plan_with_named_domain()
    valid = deepcopy(invalid)
    valid["evidence_components"][feature]["Aster source"] = [  # type: ignore[index]
        "Aster"
    ]
    llm = _QueuedLLM(_roles_for_plan(valid), invalid, valid)

    spec = asyncio.run(SearchSpecParser().parse(REQUEST, llm))  # type: ignore[arg-type]

    assert spec.domains == ["Aster"]
    assert len(llm.prompts) == 3
    assert "does not cover named domain: Aster" in llm.prompts[2]


def test_parser_retries_when_features_and_components_omit_a_generated_object() -> None:
    invalid = _valid_plan()
    invalid["objects"] = ["sensor readings", "control surface"]
    valid = deepcopy(invalid)
    expanded_core = "sensor control classifier: classify sensor readings through a control surface"
    valid["core_requirement"] = expanded_core
    valid["evidence_aliases"] = {
        **valid["evidence_aliases"],  # type: ignore[dict-item]
        expanded_core: [
            "control surface sensor reading classification"
        ],
    }
    valid["evidence_aliases"].pop(CORE)  # type: ignore[union-attr]
    valid["evidence_components"] = {
        **valid["evidence_components"],  # type: ignore[dict-item]
        expanded_core: {
            "sensor readings": ["sensor reading classification"],
            "control surface": ["control surface"],
        },
    }
    valid["evidence_components"].pop(CORE)  # type: ignore[union-attr]
    llm = _QueuedLLM(_roles_for_plan(valid), invalid, valid)

    spec = asyncio.run(SearchSpecParser().parse(REQUEST, llm))  # type: ignore[arg-type]

    assert spec.objects == ["sensor readings", "control surface"]
    assert len(llm.prompts) == 3
    assert "do not cover named object: control surface" in llm.prompts[2]


def test_component_validation_allows_decomposed_interface_values() -> None:
    plan = _valid_plan()
    feature = "classify sensor readings in a browser dashboard"
    plan["core_requirement"] = feature
    plan["interfaces"] = ["browser dashboard"]
    plan["evidence_aliases"] = {
        feature: ["browser sensor classification dashboard"],
        "export a dashboard": ["dashboard export"],
    }
    plan["evidence_components"] = {
        feature: {
            "classification": ["sensor reading classification"],
            "browser interface": ["browser interface"],
            "dashboard result": ["dashboard result"],
        },
        "export a dashboard": {
            "export a dashboard": ["dashboard export"],
        },
    }

    spec = SearchSpecParser()._from_llm_data(REQUEST, plan)

    assert not any(
        "does not cover named domain" in error
        for error in SearchSpecParser()._validation_errors(spec)
    )


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
            "core_requirement": "reconcile invoices",
            "hard_constraints": [],
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


def test_no_llm_path_rejects_an_unqualified_semantic_plan() -> None:
    parser = SearchSpecParser()

    with pytest.raises(ValueError, match="requires a configured LLM"):
        asyncio.run(parser.parse("A current user request", None))


def test_prompt_contains_no_fixed_translation_or_repository_examples() -> None:
    parser = SearchSpecParser()
    roles = parser._roles_from_llm_data(_valid_roles())

    assert roles is not None
    role_prompt = parser._role_prompt(REQUEST)
    plan_prompt = parser._plan_prompt(REQUEST, roles, [])

    assert REQUEST in role_prompt
    assert "requirement roles only" in role_prompt
    assert "requirement_items" in role_prompt
    assert "one non-empty ordered array" in role_prompt
    assert "exactly kind, statement, scope, action, and primary_object" in role_prompt
    assert "self-contained, repository-searchable expression" in role_prompt
    assert "must not add or repeat a capability or property" in role_prompt
    assert "Create a separate array entry" in role_prompt
    assert "use only the first item as core" in role_prompt
    assert "All non-core content is ranking-only" in role_prompt
    assert "both a capability and a preference" in role_prompt
    assert "subject entity, business or content domain, or product platform" in role_prompt
    assert "file or media format" in role_prompt
    assert "unless transforming that format is itself the request's primary job" in role_prompt
    assert "containing every desired detail exactly once" in role_prompt
    assert "Ignore pure problem narration" in role_prompt
    assert "background_context" not in role_prompt
    assert "primary_product_form" not in role_prompt
    assert "adoption_constraints" not in role_prompt
    assert "queries, keywords, evidence phrases" in role_prompt
    assert "Fixed requirement roles" in plan_prompt
    assert f'"core_requirement": "{CORE}"' in plan_prompt
    assert '"nice_to_have": ["export a dashboard"]' in plan_prompt
    assert "primary_product_form" not in plan_prompt
    assert '"must_have"' not in plan_prompt
    assert "static translation table" in plan_prompt
    assert "known repository" in plan_prompt
    assert "owner/" not in plan_prompt
