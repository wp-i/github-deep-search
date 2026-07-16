from __future__ import annotations

import json
import re
from collections import OrderedDict
from typing import Any

from github_deep_search.models import SearchSpec
from github_deep_search.providers.llm import LLMClient


QUERY_CHANNEL_LIMITS = {
    "repo_search_queries": 10,
    "code_search_queries": 5,
    "topic_search_queries": 8,
    "issue_search_queries": 5,
    "web_search_queries": 4,
}


class SearchSpecParser:
    """Create one current-request search plan and validate its structure.

    Product meaning belongs to the model-generated SearchSpec. Deterministic code
    only normalizes fields, validates exact coverage, and retries the same owning
    stage when the returned structure is invalid.
    """

    async def parse(self, query: str, llm: LLMClient | None) -> SearchSpec:
        if not llm:
            return self._literal_only_spec(query)

        errors: list[str] = []
        for _attempt in range(3):
            data = await llm.json_chat(
                "You parse the current request into one repository-search specification. Return JSON only.",
                self._plan_prompt(query, errors),
            )
            spec = self._from_llm_data(query, data)
            errors = self._validation_errors(spec)
            if spec is not None and not errors:
                return spec
        detail = "; ".join(errors) or "response was not a valid SearchSpec object"
        raise ValueError(f"SearchSpec generation failed structural validation: {detail}")

    @staticmethod
    def _plan_prompt(query: str, validation_errors: list[str]) -> str:
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
            "interfaces, must_have, nice_to_have, negative_filters, repo_search_queries, code_search_queries, "
            "topic_search_queries, issue_search_queries, web_search_queries, evidence_aliases, and "
            "evidence_components.\n"
            "Interpret only the current request. Keep the smallest usable core outcome and explicit hard constraints "
            "in must_have. Put preferences, refinements, thresholds described as optional, supplemental signals, and "
            "implementation suggestions in nice_to_have. A dependent workflow must remain one complete capability; "
            "do not turn framing text or one manual step into a standalone must-have. Preserve every requested outcome "
            "or refinement in must_have or nice_to_have.\n"
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
            "evidence_aliases and evidence_components must have exactly the combined must_have and nice_to_have strings "
            "as keys. Each evidence_alias value is a non-empty array of concrete phrases that could prove that entire "
            "feature in repository material. Each evidence_components value is a non-empty object whose labels are "
            "current-request proof components and whose values are non-empty phrase arrays. Components must collectively "
            "cover every named action, object, domain/interface, relationship, output, and constraint required by the "
            "feature. Each phrase must be one contiguous repository-author expression that can independently prove its "
            "named component in one metadata, README, path, or source location. Do not use search queries, tag bundles, "
            "joined synonyms, or fragments that require evidence from another location.\n"
            "Use this structural shape: "
            '{"intent":"...","literal_keywords":[],"domains":[],"actions":[],"objects":[],"outputs":[],'
            '"interfaces":[],"must_have":[],"nice_to_have":[],"negative_filters":[],'
            '"repo_search_queries":[],"code_search_queries":[],"topic_search_queries":[],'
            '"issue_search_queries":[],"web_search_queries":[],"evidence_aliases":{"<feature>":["<phrase>"]},'
            '"evidence_components":{"<feature>":{"<component>":["<phrase>"]}}}.\n'
            f"Current request:\n{query}"
        )

    def _from_llm_data(
        self,
        query: str,
        data: dict[str, Any] | None,
    ) -> SearchSpec | None:
        if not isinstance(data, dict):
            return None
        must_have = self._non_redundant_features(
            self._list(data.get("must_have"))
            or self._list(data.get("core_must_have"))
            or self._list(data.get("core_requirements"))
        )
        nice_to_have = self._non_redundant_features(
            self._list(data.get("nice_to_have"))
            or self._list(data.get("implementation_assumptions"))
            or self._list(data.get("extension_requirements"))
        )
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

    def _literal_only_spec(self, query: str) -> SearchSpec:
        literal = self._literal_terms(query)
        clauses = self._explicit_requirement_clauses(query)
        features = self._non_redundant_features(clauses) or literal[:8] or [query.strip()]
        literal_keywords = self._merge_lists(clauses, literal, limit=16)
        repo_queries = self._literal_queries(literal_keywords, query, features)
        code_queries = self._literal_code_queries(literal_keywords)
        topic_queries = self._topic_queries(literal_keywords)
        issue_queries = repo_queries[:5]
        web_queries = [f"site:github.com {item}" for item in repo_queries[:4]]
        evidence_aliases = {feature: self._literal_aliases(feature) for feature in features}
        return SearchSpec(
            raw=query,
            intent=query[:120],
            literal_keywords=literal_keywords,
            domains=[],
            actions=[],
            objects=[],
            outputs=[],
            interfaces=[],
            must_have=features,
            nice_to_have=[],
            negative_filters=[],
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
            evidence_aliases=evidence_aliases,
            evidence_components={
                feature: {feature: self._literal_aliases(feature)}
                for feature in features
            },
        )

    @staticmethod
    def _request_language(query: str) -> str:
        return "zh" if re.search(r"[\u4e00-\u9fff]", str(query or "")) else "en"

    def _explicit_requirement_clauses(self, query: str) -> list[str]:
        text = re.sub(r"\s+", " ", str(query or "")).strip()
        if not text:
            return []
        numbered = self._numbered_requirement_clauses(text)
        if numbered:
            return numbered
        parts = re.split(r"[,，、;；。！？?\n]", text)
        clauses = [
            re.sub(r"^\d+[.)、]\s*", "", part.strip(" .。；;，,"))
            for part in parts
        ]
        return list(OrderedDict.fromkeys(item for item in clauses if len(item) >= 2))[:10]

    @staticmethod
    def _numbered_requirement_clauses(text: str) -> list[str]:
        markers = list(re.finditer(r"(?:^|[\s，,；;。])\d+[.)、]\s*", text))
        clauses: list[str] = []
        for index, marker in enumerate(markers):
            start = marker.end()
            end = markers[index + 1].start() if index + 1 < len(markers) else len(text)
            clause = text[start:end].strip(" \t\r\n，,；;。")
            if len(clause) >= 2:
                clauses.append(clause)
        return list(OrderedDict.fromkeys(clauses))[:10]

    @staticmethod
    def _norm_key(value: str) -> str:
        return re.sub(r"\s+", " ", str(value).strip().casefold())

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
    def _topic_queries(values: list[str]) -> list[str]:
        topics: list[str] = []
        for value in values:
            text = re.sub(r"\s+", "-", str(value).strip().casefold())
            text = re.sub(r"[^a-z0-9_.\-\u4e00-\u9fff]", "", text).strip(".-_")
            if text:
                topics.append(text[:50])
        return list(OrderedDict.fromkeys(topics))[:8]

    def _literal_aliases(self, text: str) -> list[str]:
        exact = str(text).strip()
        if not exact:
            return []
        return list(OrderedDict.fromkeys([exact, *self._literal_terms(exact)]))[:8]

    @staticmethod
    def _literal_terms(text: str) -> list[str]:
        chunks = re.findall(
            r"[A-Za-z][A-Za-z0-9_.-]{2,}|[\u4e00-\u9fff]{2,}",
            text.casefold(),
        )
        return list(OrderedDict.fromkeys(chunks))[:10]

    def _literal_queries(
        self,
        literal: list[str],
        query: str,
        features: list[str],
    ) -> list[str]:
        if literal:
            return list(OrderedDict.fromkeys([*features[:4], " ".join(literal[:6])]))[:10]
        return [query]

    @staticmethod
    def _literal_code_queries(literal: list[str]) -> list[str]:
        return list(OrderedDict.fromkeys(item for item in literal if item))[:5]

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
