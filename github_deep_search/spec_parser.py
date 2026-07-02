from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import replace
from typing import Any

from github_deep_search.models import SearchSpec
from github_deep_search.providers.llm import LLMClient


SPEC_GROUPS = ["literal_keywords", "domains", "actions", "objects", "outputs", "interfaces"]


class SearchSpecParser:
    async def parse(self, query: str, llm: LLMClient | None) -> SearchSpec:
        if llm:
            explicit_clauses = self._explicit_requirement_clauses(query)
            system_prompt = "You parse product-search requirements into a strict search specification. Return JSON only."
            user_prompt = (
                    "Return JSON with keys: intent, literal_keywords, domains, actions, objects, outputs, "
                    "interfaces, must_have, nice_to_have, negative_filters, search_queries, "
                    "repo_search_queries, code_search_queries, topic_search_queries, issue_search_queries, "
                    "web_search_queries, evidence_aliases.\n"
                    "Rules:\n"
                    "- Preserve exact named entities and important phrases from the user in literal_keywords.\n"
                    "- Do not replace a specific named entity with a broad category.\n"
                    "- Preserve the user's product abstraction level and the relationships between actors, actions, "
                    "objects, constraints, and outputs. Never replace it with a broader or downstream product.\n"
                    "- Split the requirement into core outcomes and extension/implementation guesses. must_have "
                    "contains only the user's core functional outcomes and hard constraints. nice_to_have contains "
                    "optional wants, uncertain phrasing, implementation guesses, credentials, providers, or runtime "
                    "assumptions unless the user explicitly says they are mandatory.\n"
                    "- Search and scoring must be anchored on must_have first. Extension or implementation details "
                    "must never become the primary project identity.\n"
                    "- Derive query wording only from the current requirement and its structured interpretation. "
                    "Do not use canned examples, domain vocabularies, known repositories, translated word packs, "
                    "or phrases remembered from other requests.\n"
                    "- Make query angles meaningfully distinct: vary the product identity, a core capability, an "
                    "interface or platform constraint, and an expected artifact or outcome when those are present.\n"
                    "- Prefer concise terms that repository authors and issue reporters are likely to use. Do not "
                    "invent capabilities or adjacent product categories merely to increase query count.\n"
                    "- evidence_aliases must be an object keyed by each must_have item. Each value is a short list of "
                    "concrete README/source/path phrases that would prove that exact must_have. Do not include broad "
                    "generic words that could match unrelated projects.\n"
                    "- evidence_aliases should use phrases project authors are likely to write in README files or "
                    "source paths. Reuse planned repo/code query phrases as evidence aliases when they would prove "
                    "the exact must_have item.\n"
                    "- Evidence aliases must also be derived only from the current must-have text and the current "
                    "single-pass interpretation. Do not add hard-coded cross-language mappings or translation "
                    "fallback terms.\n"
                    "- Every must_have item must have a matching evidence_aliases key. The key text must exactly match "
                    "the corresponding must_have string.\n"
                    "- Keep must_have items non-overlapping. If one item already contains another item as the same "
                    "capability, keep only the more specific item.\n"
                    "- For non-English requirements, repository and issue queries must preserve important words from "
                    "the user's original text. Do not force a second-language query solely for coverage.\n"
                    "- repo_search_queries target GitHub repository name, description, topics, and README discovery.\n"
                    "- code_search_queries target concrete source/path/config evidence and should use phrases likely to appear in code, manifests, config, tests, or filenames.\n"
                    "- topic_search_queries target GitHub topic discovery. Use concise GitHub topic slugs without a topic: prefix.\n"
                    "- issue_search_queries target GitHub issues where maintainers/users discuss the requested product capabilities, platforms, bugs, or integrations.\n"
                    "- web_search_queries target broad web discovery of GitHub repositories and comparison pages.\n"
                    "- search_queries is a backward-compatible union of the planned query channels.\n"
                    "- All query fields must be concise search phrases, not full sentences.\n"
                    "- Do not include final recommendations or project analysis.\n"
                    "- The requirement anchors listed below are mandatory. Preserve the product identity and every "
                    "requested capability or constraint in the structured fields, must_have list, aliases, and search "
                    "angles. Do not reduce the request to one convenient sub-feature.\n"
                    f"Mandatory anchors: {explicit_clauses}\n"
                    f"Requirement:\n{query}"
            )
            for attempt in range(2):
                prompt = user_prompt
                if attempt:
                    prompt += (
                        "\nThe previous plan failed grounding validation. Re-plan from this requirement only. "
                        "Ensure the original-language repository and issue queries contain important terms copied "
                        "from the requirement, while keeping every channel semantically consistent. "
                        f"Every one of these anchors must remain represented: {explicit_clauses}."
                    )
                data = await llm.json_chat(system_prompt, prompt)
                spec = self._from_llm_data(query, data)
                if spec and self._valid(spec):
                    return spec
                if spec and attempt:
                    anchored = self._with_anchor_queries(spec, explicit_clauses)
                    if self._valid(anchored):
                        return anchored
            return self._literal_only_spec(query)
        return self._literal_only_spec(query)

    def _with_anchor_queries(self, spec: SearchSpec, anchors: list[str]) -> SearchSpec:
        anchor_queries = [anchor for anchor in anchors if anchor.strip()]
        if not anchor_queries:
            return spec
        repo_queries = self._merge_lists(anchor_queries, spec.repo_search_queries, limit=10)
        issue_queries = self._merge_lists(anchor_queries, spec.issue_search_queries or spec.repo_search_queries, limit=10)
        web_queries = self._merge_lists([f"site:github.com {item}" for item in anchor_queries[:3]], spec.web_search_queries, limit=10)
        search_queries = self._merge_lists(
            spec.search_queries,
            repo_queries,
            spec.code_search_queries,
            spec.topic_search_queries,
            issue_queries,
            web_queries,
            limit=20,
        )
        return replace(
            spec,
            search_queries=search_queries,
            repo_search_queries=repo_queries,
            issue_search_queries=issue_queries,
            web_search_queries=web_queries,
        )

    def _from_llm_data(self, query: str, data: dict[str, Any] | None) -> SearchSpec | None:
        if not isinstance(data, dict):
            return None
        search_queries = self._list(data.get("search_queries"), limit=10)
        evidence_aliases = self._evidence_aliases(data.get("evidence_aliases"))
        repo_queries = self._list(data.get("repo_search_queries"), limit=10) or search_queries
        code_queries = self._list(data.get("code_search_queries"), limit=10) or self._queries_from_aliases(evidence_aliases)
        topic_queries = self._topic_queries(
            self._list(data.get("topic_search_queries"), limit=10)
            or self._merge_lists(
                self._list(data.get("domains")),
                self._list(data.get("objects")),
                self._list(data.get("interfaces")),
                self._list(data.get("literal_keywords")),
                limit=10,
            )
        )
        issue_queries = self._list(data.get("issue_search_queries"), limit=10) or repo_queries[:6]
        web_queries = self._list(data.get("web_search_queries"), limit=10) or search_queries[:4]
        union_queries = self._merge_lists(
            search_queries,
            repo_queries,
            code_queries,
            topic_queries,
            issue_queries,
            web_queries,
            limit=20,
        )
        raw_must_have = self._non_redundant_features(
            self._list(data.get("core_must_have"))
            or self._list(data.get("core_requirements"))
            or self._list(data.get("must_have"))
            or self._list(data.get("must_have_features"))
        )
        raw_nice_to_have = self._non_redundant_features(
            self._list(data.get("nice_to_have"))
            or self._list(data.get("nice_to_have_features"))
            or self._list(data.get("implementation_assumptions"))
            or self._list(data.get("extension_requirements"))
        )
        must_have, demoted = self._split_core_and_extension_features(query, raw_must_have)
        nice_to_have = self._non_redundant_features([*raw_nice_to_have, *demoted])
        return SearchSpec(
            raw=query,
            intent=str(data.get("intent") or query[:120]),
            literal_keywords=self._list(data.get("literal_keywords")),
            domains=self._list(data.get("domains")),
            actions=self._list(data.get("actions")),
            objects=self._list(data.get("objects")),
            outputs=self._list(data.get("outputs")),
            interfaces=self._list(data.get("interfaces")),
            must_have=must_have,
            nice_to_have=nice_to_have,
            negative_filters=self._list(data.get("negative_filters")),
            search_queries=union_queries,
            repo_search_queries=repo_queries,
            code_search_queries=code_queries,
            topic_search_queries=topic_queries,
            issue_search_queries=issue_queries,
            web_search_queries=web_queries,
            evidence_aliases=evidence_aliases,
        )

    def _split_core_and_extension_features(self, query: str, features: list[str]) -> tuple[list[str], list[str]]:
        core: list[str] = []
        extension: list[str] = []
        for feature in features:
            if self._is_uncertain_or_assumptive_feature(query, feature):
                extension.append(feature)
            else:
                core.append(feature)
        return self._non_redundant_features(core), self._non_redundant_features(extension)

    def _is_uncertain_or_assumptive_feature(self, query: str, feature: str) -> bool:
        feature_text = str(feature or "").strip()
        if not feature_text:
            return False
        lowered_feature = feature_text.lower()
        lowered_query = str(query or "").lower()
        hard_markers = ["必须", "必需", "一定要", "required", "must", "mandatory"]
        uncertainty_markers = [
            "可能",
            "也许",
            "大概",
            "猜想",
            "估计",
            "不确定",
            "是否需要",
            "maybe",
            "might",
            "may need",
            "perhaps",
            "probably",
            "optional",
            "if needed",
        ]
        if any(marker in lowered_feature for marker in hard_markers):
            return False
        if any(marker in lowered_feature for marker in uncertainty_markers):
            return True
        feature_index = lowered_query.find(lowered_feature)
        if feature_index < 0:
            compact_feature = re.sub(r"\s+", "", lowered_feature)
            compact_query = re.sub(r"\s+", "", lowered_query)
            feature_index = compact_query.find(compact_feature)
            if feature_index < 0:
                return False
            before = compact_query[max(0, feature_index - 18) : feature_index]
            feature_local = compact_query[feature_index : feature_index + len(compact_feature)]
        else:
            before = lowered_query[max(0, feature_index - 36) : feature_index]
            feature_local = lowered_query[feature_index : feature_index + len(lowered_feature)]
        local = before + feature_local
        if any(marker in local for marker in hard_markers):
            return False
        return any(marker in local for marker in uncertainty_markers)

    def _literal_only_spec(self, query: str) -> SearchSpec:
        literal = self._literal_terms(query)
        clauses = self._explicit_requirement_clauses(query)
        if len(clauses) == 1 and clauses[0].strip().lower() == str(query or "").strip().lower() and not re.search(
            r"[\u4e00-\u9fff]", query or ""
        ):
            clauses = []
        features = self._non_redundant_features(clauses) or literal[:8]
        literal_keywords = self._merge_lists(clauses, literal, limit=16)
        repo_queries = self._literal_queries(literal_keywords, query, features)
        code_queries = self._literal_code_queries(literal_keywords)
        topic_queries = self._topic_queries(literal_keywords)
        issue_queries = repo_queries[:6]
        web_queries = [f"site:github.com {item}" for item in repo_queries[:3]]
        evidence_aliases = {item: self._literal_aliases(item) for item in features}
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
            search_queries=list(OrderedDict.fromkeys([*repo_queries, *code_queries, *topic_queries, *issue_queries, *web_queries])),
            repo_search_queries=repo_queries,
            code_search_queries=code_queries,
            topic_search_queries=topic_queries,
            issue_search_queries=issue_queries,
            web_search_queries=web_queries,
            evidence_aliases=evidence_aliases,
        )

    def _valid(self, spec: SearchSpec) -> bool:
        if not spec.search_queries:
            return False
        if not spec.repo_search_queries:
            return False
        if not spec.code_search_queries:
            return False
        if not spec.topic_search_queries:
            return False
        if not spec.issue_search_queries:
            return False
        if spec.must_have and not self._evidence_aliases_cover_must_have(spec):
            return False
        if not spec.literal_keywords and not any([spec.domains, spec.actions, spec.objects, spec.outputs]):
            return False
        raw_signals = self._signals(spec.raw)
        if not raw_signals:
            return True
        grounded_queries = " ".join([*spec.repo_search_queries[:3], *spec.issue_search_queries[:3]])
        if not raw_signals.intersection(self._signals(grounded_queries)):
            return False
        planned = " ".join(
            [
                spec.intent,
                " ".join(spec.literal_keywords),
                " ".join(spec.domains),
                " ".join(spec.actions),
                " ".join(spec.objects),
                " ".join(spec.outputs),
                " ".join(spec.interfaces),
                " ".join(spec.search_queries),
                " ".join(spec.repo_search_queries),
                " ".join(spec.code_search_queries),
                " ".join(spec.topic_search_queries),
                " ".join(spec.issue_search_queries),
                " ".join(spec.web_search_queries),
            ]
        )
        overlap = raw_signals & self._signals(planned)
        if len(overlap) < min(3, max(1, len(raw_signals) // 5)):
            return False
        structured = " ".join(
            [
                spec.intent,
                *spec.domains,
                *spec.actions,
                *spec.objects,
                *spec.outputs,
                *spec.interfaces,
                *spec.must_have,
                *spec.nice_to_have,
            ]
        )
        structured_signals = self._anchor_signals(structured)
        discovery_signals = self._anchor_signals(
            " ".join([*spec.repo_search_queries, *spec.issue_search_queries])
        )
        for clause in self._explicit_requirement_clauses(spec.raw):
            clause_signals = self._anchor_signals(clause)
            required_overlap = min(2, len(clause_signals))
            if clause_signals and len(clause_signals.intersection(structured_signals)) < required_overlap:
                return False
            if self._is_uncertain_or_assumptive_feature(spec.raw, clause):
                continue
            if clause_signals and len(clause_signals.intersection(discovery_signals)) < required_overlap:
                return False
        return True

    def _anchor_signals(self, text: str) -> set[str]:
        signals = set(re.findall(r"[a-z][a-z0-9_.-]{2,}", (text or "").lower()))
        for run in re.findall(r"[\u4e00-\u9fff]{2,}", text or ""):
            signals.update(run[index : index + 2] for index in range(len(run) - 1))
        weak = {
            "一个",
            "一种",
            "一款",
            "需要",
            "支持",
            "系统",
            "功能",
            "能力",
            "数据",
            "可以",
            "进行",
        }
        return {item for item in signals if item not in weak}

    def _explicit_requirement_clauses(self, query: str) -> list[str]:
        """Extract user-written product/capability anchors without domain assumptions."""
        text = re.sub(r"\s+", " ", str(query or "")).strip()
        if not text:
            return []
        text = re.sub(r"\bwith\b", ",", text, flags=re.IGNORECASE)
        text = re.sub(r"(?<=。)(?=最后|最好|尤其|必须|有个|也有)", "，", text)
        parts = re.split(r"[：:，,、；;。！？!?\n]", text)
        clauses: list[str] = []
        for part in parts:
            cleaned = part.strip(" .。")
            cleaned = re.sub(
                r"^(?:我(?:想|需要|要)|我们(?:想|需要|要)|寻找|找|做)(?:做|找)?(?:一个|一款|一种)?",
                "",
                cleaned,
            )
            cleaned = re.sub(
                r"^(?:i\s+(?:need|want)|we\s+(?:need|want)|looking\s+for)(?:\s+an?|\s+the)?\s+",
                "",
                cleaned,
                flags=re.IGNORECASE,
            )
            cleaned = re.sub(
                r"^(?:可以|可|支持|具备|并|并且|并能|而且|且能|以及|必须有|必须|尤其是|最后|最好支持|最好|有个|也有|and)\s*",
                "",
                cleaned,
                flags=re.IGNORECASE,
            )
            cleaned = re.sub(r"^通过关键词进行", "", cleaned)
            cleaned = re.sub(r"^生成一份", "生成", cleaned)
            cleaned = re.sub(r"^记录相关关键词下的", "关键词", cleaned)
            cleaned = re.sub(r"可以通过(.+?)直接运行$", r"可通过\1运行", cleaned)
            cleaned = re.sub(r"pdf", "PDF", cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(r"mcp", "MCP", cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(r"top\s*10", "Top 10", cleaned, flags=re.IGNORECASE)
            if len(cleaned) >= 2:
                clauses.append(cleaned)
        return list(OrderedDict.fromkeys(clauses))[:10]

    def _evidence_aliases_cover_must_have(self, spec: SearchSpec) -> bool:
        alias_keys = {self._norm_key(key) for key, values in spec.evidence_aliases.items() if values}
        return all(self._norm_key(feature) in alias_keys for feature in spec.must_have)

    def _norm_key(self, value: str) -> str:
        return re.sub(r"\s+", " ", str(value).strip().lower())

    def _list(self, value: object, limit: int = 16) -> list[str]:
        if not isinstance(value, list):
            return []
        items: list[str] = []
        for item in value:
            text = str(item).strip()
            if text:
                items.append(text)
        return list(OrderedDict.fromkeys(items))[:limit]

    def _non_redundant_features(self, items: list[str]) -> list[str]:
        normalized = [re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", item.lower()) for item in items]
        kept: list[str] = []
        for index, item in enumerate(items):
            current = normalized[index]
            if not current:
                continue
            contained_by_more_specific = any(
                index != other_index
                and current != other
                and current in other
                for other_index, other in enumerate(normalized)
            )
            if not contained_by_more_specific:
                kept.append(item)
        return kept

    def _evidence_aliases(self, value: object) -> dict[str, list[str]]:
        if not isinstance(value, dict):
            return {}
        aliases: dict[str, list[str]] = {}
        for key, raw_items in value.items():
            feature = str(key).strip()
            if not feature:
                continue
            items = self._list(raw_items, limit=12)
            if items:
                aliases[feature] = items
        return aliases

    def _queries_from_aliases(self, aliases: dict[str, list[str]]) -> list[str]:
        items: list[str] = []
        for values in aliases.values():
            items.extend(values[:3])
        return list(OrderedDict.fromkeys(item for item in items if item))[:10]

    def _merge_lists(self, *lists: list[str], limit: int) -> list[str]:
        merged: list[str] = []
        for values in lists:
            merged.extend(values)
        return list(OrderedDict.fromkeys(item for item in merged if item))[:limit]

    def _topic_queries(self, values: list[str]) -> list[str]:
        topics: list[str] = []
        for value in values:
            text = re.sub(r"\s+", "-", str(value).strip().lower())
            text = re.sub(r"[^a-z0-9_.\-\u4e00-\u9fff]", "", text)
            text = text.strip(".-_")
            if text:
                topics.append(text[:50])
        return list(OrderedDict.fromkeys(topics))[:10]

    def _literal_aliases(self, text: str) -> list[str]:
        exact = str(text).strip()
        if not exact:
            return []
        parts = self._literal_terms(exact)
        return list(OrderedDict.fromkeys([exact, *parts]))[:8]

    def _literal_terms(self, text: str) -> list[str]:
        chunks = re.findall(r"[A-Za-z][A-Za-z0-9_.-]{2,}|[\u4e00-\u9fff]{2,}", text.lower())
        return list(OrderedDict.fromkeys(chunks))[:10]

    def _literal_queries(self, literal: list[str], query: str, features: list[str]) -> list[str]:
        if literal:
            joined = " ".join(literal[:6])
            anchors = [item for item in features if len(self._signals(item)) >= 2]
            return list(OrderedDict.fromkeys([*anchors[:4], joined, f"{joined} github", f"{joined} open source"]))
        return [query]

    def _literal_code_queries(self, literal: list[str]) -> list[str]:
        return list(OrderedDict.fromkeys(item for item in literal if item))[:6]

    def _signals(self, text: str) -> set[str]:
        lowered = (text or "").lower()
        signals = set(re.findall(r"[a-z][a-z0-9_.-]{2,}", lowered))
        for run in re.findall(r"[\u4e00-\u9fff]{2,}", lowered):
            if len(run) <= 4:
                signals.add(run)
                continue
            signals.update(run[index : index + 2] for index in range(len(run) - 1))
            signals.update(run[index : index + 3] for index in range(len(run) - 2))
        return signals
