from __future__ import annotations

import json
import re
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from github_deep_search.models import SearchSpec
from github_deep_search.providers.llm import LLMClient, LLMProviderError


QUERY_CHANNEL_LIMITS = {
    "repo_search_queries": 10,
    "code_search_queries": 5,
    "topic_search_queries": 8,
    "issue_search_queries": 5,
    "web_search_queries": 4,
}


@dataclass(frozen=True)
class _RequirementRoles:
    core_requirement: str
    nice_to_have: tuple[str, ...]

    @property
    def hard_constraints(self) -> tuple[str, ...]:
        return ()

    def as_dict(self) -> dict[str, object]:
        return {
            "core_requirement": self.core_requirement,
            "hard_constraints": [],
            "nice_to_have": list(self.nice_to_have),
        }


class SearchSpecParser:
    """Create one current-request search plan and validate its structure.

    Product meaning belongs to the model-generated SearchSpec. Deterministic code
    only normalizes fields, validates exact coverage, and retries the same owning
    stage when the returned structure is invalid.
    """

    async def parse(self, query: str, llm: LLMClient | None) -> SearchSpec:
        if not llm:
            raise ValueError(
                "SearchSpec parsing requires a configured LLM; use an audited fixed "
                "requirement plan when running without one."
            )

        role_data = await llm.json_chat(
            "You assign mutually exclusive requirement roles for the current request. Return JSON only.",
            self._role_prompt(query),
        )
        roles = self._roles_from_llm_data(role_data)
        role_errors = self._role_validation_errors(roles)
        if roles is None or role_errors:
            provider_failure = getattr(llm, "last_failure", None)
            if isinstance(provider_failure, LLMProviderError):
                raise provider_failure
            detail = "; ".join(role_errors) or "response was not a valid requirement-role object"
            raise ValueError(f"SearchSpec role generation failed structural validation: {detail}")

        errors: list[str] = []
        for _attempt in range(3):
            data = await llm.json_chat(
                "You expand fixed requirement roles into one repository-search specification. Return JSON only.",
                self._plan_prompt(query, roles, errors),
            )
            spec = self._from_llm_data(query, data)
            errors = [
                *self._validation_errors(spec),
                *self._role_consistency_errors(spec, roles),
            ]
            if spec is not None and not errors:
                return spec
        provider_failure = getattr(llm, "last_failure", None)
        if isinstance(provider_failure, LLMProviderError):
            raise provider_failure
        detail = "; ".join(errors) or "response was not a valid SearchSpec object"
        raise ValueError(f"SearchSpec generation failed structural validation: {detail}")

    @staticmethod
    def _role_prompt(query: str) -> str:
        return (
            "Return JSON with exactly one field: requirement_items. "
            "This step decides requirement roles only; do not generate queries, keywords, evidence phrases, "
            "repositories, or recommendations. requirement_items is one non-empty ordered array containing every desired "
            "detail exactly once. The first item is the sole core and must be a capability object with exactly kind, "
            "statement, scope, action, and primary_object, where kind is capability. Every later independently useful "
            "repository action/object pair uses the same capability shape. scope is one concise explicitly named subject "
            "entity, business or content domain, or product platform, or an empty string when none is named. An input or "
            "output container, file or media format, machine, runtime, deployment place, or delivery environment is a "
            "preference, unless transforming that format is itself the request's primary job. "
            "action contains one operation. primary_object contains one primary object. statement is one concise, "
            "self-contained, repository-searchable expression of that same scope, action, and object; it must not add or "
            "repeat a capability or property. Create a separate array entry for every independently useful requested "
            "action/object pair, even when entries share a scope or action; never combine them in one entry. Every desired "
            "adoption, operating, deployment, language, input/output, integration, delivery, quality, or convenience "
            "property is a preference object with exactly kind and statement, where kind is preference. Order capability "
            "items by: an explicitly stated main, core, or primary capability; "
            "otherwise the requested tool or workflow's overall purpose; otherwise the first capability in the user's "
            "ordering. The parser will use only the first item as core and rank every later item regardless of kind. "
            "All non-core content is ranking-only and never a hard constraint. Do not emit the same requested detail as "
            "both a capability and a preference, and do not duplicate or paraphrase one detail in multiple items. "
            "Ignore pure problem narration or current limitations that do not ask a repository to provide behavior. "
            "Use this shape: "
            '{"requirement_items":[{"kind":"capability","statement":"...","scope":"...",'
            '"action":"...","primary_object":"..."},{"kind":"preference","statement":"..."}]}.\n'
            f"Current request:\n{query}"
        )

    @staticmethod
    def _plan_prompt(
        query: str,
        roles: _RequirementRoles,
        validation_errors: list[str],
    ) -> str:
        retry_note = (
            "The previous response failed these domain-neutral structural checks: "
            + json.dumps(validation_errors, ensure_ascii=False)
            + ". Regenerate the complete unchanged-request plan and correct every listed error.\n"
            if validation_errors
            else ""
        )
        return (
            retry_note
            + "Return JSON with exactly these fields: intent, literal_keywords, domains, actions, objects, outputs, "
            "interfaces, core_requirement, hard_constraints, nice_to_have, negative_filters, repo_search_queries, "
            "code_search_queries, topic_search_queries, issue_search_queries, web_search_queries, evidence_aliases, "
            "and evidence_components.\n"
            "The requirement-role decision below is fixed. Copy core_requirement, hard_constraints, and nice_to_have "
            "exactly, including wording and order. Do not merge, split, add, remove, or reclassify any role while "
            "generating the rest of the plan. Fixed requirement roles:\n"
            f"{json.dumps(roles.as_dict(), ensure_ascii=False)}\n"
            "domains, actions, objects, outputs, and interfaces describe the same interpreted request. For Chinese or "
            "English input, derive both Chinese and English repository-author wording from this request and include "
            "both languages in repo_search_queries. Do not use a static translation table, fixed vocabulary, known "
            "repository, or recommendation.\n"
            "Return exactly the following number of distinct queries per channel: "
            f"{json.dumps(QUERY_CHANNEL_LIMITS)}. Repository queries target names, descriptions, topics, and README "
            "text. Code queries are concise phrases likely to occur in files, manifests, configuration, tests, or "
            "source. Topic queries are plausible short GitHub topics. Issue queries describe the requested capability "
            "or problem. Web queries support broad GitHub discovery. Every query must remain grounded in the current "
            "request; do not invent adjacent product categories merely to fill a count.\n"
            "evidence_aliases and evidence_components must have exactly core_requirement plus every hard_constraints "
            "and nice_to_have string as keys. Each evidence_alias value is a non-empty array of concrete phrases that "
            "could prove that entire feature in repository material. Each evidence_components value is a non-empty "
            "object whose labels are current-request proof components and whose values are non-empty phrase arrays. "
            "Components must collectively cover every named action, object, domain/interface, relationship, output, "
            "and constraint required by the feature. Each phrase must be one contiguous repository-author expression "
            "that can independently prove its named component in one metadata, README, path, or source location. Do "
            "not use search queries, tag bundles, joined synonyms, or fragments that require evidence from another "
            "location.\n"
            "Use this structural shape: "
            '{"intent":"...","literal_keywords":[],"domains":[],"actions":[],"objects":[],"outputs":[],'
            '"interfaces":[],"core_requirement":"...","hard_constraints":[],"nice_to_have":[],"negative_filters":[],'
            '"repo_search_queries":[],"code_search_queries":[],"topic_search_queries":[],'
            '"issue_search_queries":[],"web_search_queries":[],"evidence_aliases":{"<feature>":["<phrase>"]},'
            '"evidence_components":{"<feature>":{"<component>":["<phrase>"]}}}.\n'
            f"Current request:\n{query}"
        )

    def _roles_from_llm_data(
        self,
        data: dict[str, Any] | None,
    ) -> _RequirementRoles | None:
        expected_fields = {"requirement_items"}
        if not isinstance(data, dict) or set(data) != expected_fields:
            return None
        items = data.get("requirement_items")
        if not isinstance(items, list) or not items:
            return None
        statements: list[str] = []
        for index, item in enumerate(items[:16]):
            if not isinstance(item, dict):
                return None
            kind = item.get("kind")
            if kind == "capability":
                if (
                    set(item) != {"kind", "statement", "scope", "action", "primary_object"}
                    or not all(isinstance(value, str) for value in item.values())
                    or not item["action"].strip()
                    or not item["primary_object"].strip()
                ):
                    return None
            elif kind == "preference":
                if set(item) != {"kind", "statement"} or not isinstance(item.get("statement"), str):
                    return None
            else:
                return None
            statement = item["statement"].strip()
            if not statement or (index == 0 and kind != "capability"):
                return None
            statements.append(statement)
        return _RequirementRoles(
            core_requirement=statements[0],
            nice_to_have=tuple(
                self._non_redundant_features(statements[1:])
            ),
        )

    def _role_validation_errors(self, roles: _RequirementRoles | None) -> list[str]:
        if roles is None:
            return [
                "response must contain one non-empty discriminated requirement-item array starting with a capability"
            ]
        errors: list[str] = []
        if not roles.core_requirement:
            errors.append("core_requirement is empty")
        values = [roles.core_requirement, *roles.nice_to_have]
        normalized = [self._norm_key(value) for value in values]
        if len(normalized) != len(set(normalized)):
            errors.append("requirement roles contain an exact normalized duplicate")
        return errors

    def _role_consistency_errors(
        self,
        spec: SearchSpec | None,
        roles: _RequirementRoles,
    ) -> list[str]:
        if spec is None:
            return []
        expected_must = [roles.core_requirement]
        errors: list[str] = []
        if spec.must_have != expected_must:
            errors.append("plan must copy the fixed core_requirement and hard_constraints exactly")
        if spec.nice_to_have != list(roles.nice_to_have):
            errors.append("plan must copy the fixed nice_to_have list exactly")
        return errors

    def _from_llm_data(
        self,
        query: str,
        data: dict[str, Any] | None,
    ) -> SearchSpec | None:
        if not isinstance(data, dict):
            return None
        raw_core = data.get("core_requirement")
        core_requirement = raw_core.strip() if isinstance(raw_core, str) else ""
        hard_constraints = self._list(data.get("hard_constraints"), limit=15)
        must_have = (
            self._merge_lists([core_requirement], hard_constraints, limit=16)
            if core_requirement
            else []
        )
        nice_to_have = self._non_redundant_features(self._list(data.get("nice_to_have")))
        features = [*must_have, *nice_to_have]
        repo_queries = self._list(
            data.get("repo_search_queries"),
            limit=QUERY_CHANNEL_LIMITS["repo_search_queries"],
        )
        code_queries = self._list(
            data.get("code_search_queries"),
            limit=QUERY_CHANNEL_LIMITS["code_search_queries"],
        )
        topic_queries = self._list(
            data.get("topic_search_queries"),
            limit=QUERY_CHANNEL_LIMITS["topic_search_queries"],
        )
        issue_queries = self._list(
            data.get("issue_search_queries"),
            limit=QUERY_CHANNEL_LIMITS["issue_search_queries"],
        )
        web_queries = self._list(
            data.get("web_search_queries"),
            limit=QUERY_CHANNEL_LIMITS["web_search_queries"],
        )
        return SearchSpec(
            raw=query,
            intent=str(data.get("intent") or query[:120]).strip(),
            literal_keywords=self._list(data.get("literal_keywords")),
            domains=self._list(data.get("domains")),
            actions=self._list(data.get("actions")),
            objects=self._list(data.get("objects")),
            outputs=self._list(data.get("outputs")),
            interfaces=self._list(data.get("interfaces")),
            must_have=must_have,
            nice_to_have=nice_to_have,
            negative_filters=self._list(data.get("negative_filters")),
            search_queries=self._merge_lists(
                repo_queries,
                code_queries,
                topic_queries,
                issue_queries,
                web_queries,
                limit=20,
            ),
            report_language=self._request_language(query),
            repo_search_queries=repo_queries,
            code_search_queries=code_queries,
            topic_search_queries=topic_queries,
            issue_search_queries=issue_queries,
            web_search_queries=web_queries,
            evidence_aliases=self._retain_feature_keys(
                self._evidence_aliases(data.get("evidence_aliases")),
                features,
            ),
            evidence_components=self._retain_feature_keys(
                self._evidence_components(data.get("evidence_components")),
                features,
            ),
        )

    def _validation_errors(self, spec: SearchSpec | None) -> list[str]:
        if spec is None:
            return ["response is not a JSON object with the SearchSpec fields"]
        errors: list[str] = []
        if not spec.must_have:
            errors.append("must_have is empty")
        if not any(
            [
                spec.literal_keywords,
                spec.domains,
                spec.actions,
                spec.objects,
                spec.outputs,
                spec.interfaces,
            ]
        ):
            errors.append("structured request fields are empty")
        for channel, expected in QUERY_CHANNEL_LIMITS.items():
            actual = len(getattr(spec, channel))
            if actual != expected:
                errors.append(f"{channel} must contain exactly {expected} distinct queries; got {actual}")
        if not self._bilingual_repository_queries(spec):
            errors.append("repo_search_queries must include current-request Chinese and English searches")

        expected_keys = {
            self._norm_key(feature)
            for feature in [*spec.must_have, *spec.nice_to_have]
            if self._norm_key(feature)
        }
        alias_keys = {
            self._norm_key(feature)
            for feature, phrases in spec.evidence_aliases.items()
            if phrases
        }
        component_keys = {
            self._norm_key(feature)
            for feature, groups in spec.evidence_components.items()
            if groups
        }
        if not expected_keys or alias_keys != expected_keys:
            errors.append("evidence_aliases keys must exactly equal must_have plus nice_to_have")
        if component_keys != expected_keys:
            errors.append("evidence_components keys must exactly equal must_have plus nice_to_have")
        if any(
            not groups
            or any(not str(label).strip() or not phrases for label, phrases in groups.items())
            for groups in spec.evidence_components.values()
        ):
            errors.append("every evidence component label must have a non-empty phrase array")
        for feature, groups in spec.evidence_components.items():
            feature_key = self._coverage_key(feature)
            component_keys = [
                self._coverage_key(text)
                for text in [
                    *groups,
                    *(
                        phrase
                        for phrases in groups.values()
                        for phrase in phrases
                    ),
                ]
            ]
            for value in spec.domains:
                value_key = self._coverage_key(value)
                if (
                    value_key
                    and value_key in feature_key
                    and not any(value_key in component_key for component_key in component_keys)
                ):
                    errors.append(
                        f"evidence_components[{feature}] does not cover named domain: {value}"
                    )
        plan_evidence_keys = [
            self._coverage_key(text)
            for feature, groups in spec.evidence_components.items()
            for text in [
                feature,
                *groups,
                *(phrase for phrases in groups.values() for phrase in phrases),
            ]
        ]
        for value in spec.objects:
            value_key = self._coverage_key(value)
            if value_key and not any(value_key in key for key in plan_evidence_keys):
                errors.append(
                    f"requirement features and evidence_components do not cover named object: {value}"
                )

        raw_signals = self._signals(spec.raw)
        planned_signals = self._signals(
            " ".join(
                [
                    spec.intent,
                    *spec.literal_keywords,
                    *spec.domains,
                    *spec.actions,
                    *spec.objects,
                    *spec.outputs,
                    *spec.interfaces,
                    *spec.must_have,
                    *spec.nice_to_have,
                    *spec.repo_search_queries,
                    *spec.issue_search_queries,
                ]
            )
        )
        if raw_signals and not raw_signals.intersection(planned_signals):
            errors.append("the plan is not grounded in the current request")
        return list(OrderedDict.fromkeys(errors))

    @staticmethod
    def _bilingual_repository_queries(spec: SearchSpec) -> bool:
        return any(re.search(r"[\u4e00-\u9fff]", query) for query in spec.repo_search_queries) and any(
            re.search(r"[A-Za-z]{2,}", query) for query in spec.repo_search_queries
        )

    @staticmethod
    def _request_language(query: str) -> str:
        return "zh" if re.search(r"[\u4e00-\u9fff]", str(query or "")) else "en"

    @staticmethod
    def _norm_key(value: str) -> str:
        return re.sub(r"\s+", " ", str(value).strip().casefold())

    @staticmethod
    def _coverage_key(value: str) -> str:
        return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", str(value).casefold())

    @staticmethod
    def _list(value: object, limit: int = 16) -> list[str]:
        if not isinstance(value, list):
            return []
        items = [str(item).strip() for item in value if str(item).strip()]
        return list(OrderedDict.fromkeys(items))[:limit]

    def _non_redundant_features(self, items: list[str]) -> list[str]:
        normalized = [
            re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", item.casefold())
            for item in items
        ]
        return [
            item
            for index, item in enumerate(items)
            if normalized[index]
            and not any(
                index != other
                and normalized[index] != candidate
                and normalized[index] in candidate
                for other, candidate in enumerate(normalized)
            )
        ]

    def _evidence_aliases(self, value: object) -> dict[str, list[str]]:
        if not isinstance(value, dict):
            return {}
        result: dict[str, list[str]] = {}
        for key, phrases in value.items():
            parsed = self._phrase_list(phrases, limit=12)
            if str(key).strip() and parsed:
                result[str(key).strip()] = parsed
        return result

    def _evidence_components(self, value: object) -> dict[str, dict[str, list[str]]]:
        if not isinstance(value, dict):
            return {}
        result: dict[str, dict[str, list[str]]] = {}
        for feature, raw_groups in value.items():
            if not str(feature).strip() or not isinstance(raw_groups, dict):
                continue
            groups: dict[str, list[str]] = {}
            for label, phrases in raw_groups.items():
                parsed = self._phrase_list(phrases, limit=12)
                if str(label).strip() and parsed:
                    groups[str(label).strip()] = parsed
            if groups:
                result[str(feature).strip()] = groups
        return result

    def _phrase_list(self, value: object, limit: int) -> list[str]:
        if isinstance(value, str):
            return [value.strip()] if value.strip() else []
        return self._list(value, limit=limit)

    def _retain_feature_keys(
        self,
        value: dict[str, Any],
        features: list[str],
    ) -> dict[str, Any]:
        by_key = {self._norm_key(key): (key, item) for key, item in value.items()}
        retained: dict[str, Any] = {}
        for feature in features:
            matched = by_key.get(self._norm_key(feature))
            if matched is not None:
                retained[feature] = matched[1]
        return retained

    @staticmethod
    def _merge_lists(*lists: list[str], limit: int) -> list[str]:
        merged: list[str] = []
        for values in lists:
            merged.extend(values)
        return list(OrderedDict.fromkeys(item for item in merged if item))[:limit]

    @staticmethod
    def _signals(text: str) -> set[str]:
        lowered = (text or "").casefold()
        signals = set(re.findall(r"[a-z][a-z0-9_.-]{2,}", lowered))
        for run in re.findall(r"[\u4e00-\u9fff]{2,}", lowered):
            if len(run) <= 4:
                signals.add(run)
            else:
                signals.update(run[index : index + 2] for index in range(len(run) - 1))
                signals.update(run[index : index + 3] for index in range(len(run) - 2))
        return signals
