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
            explicit_clauses = self._planning_anchors(query)
            system_prompt = "You parse product-search requirements into a strict search specification. Return JSON only."
            user_prompt = (
                    "Return JSON with keys: intent, literal_keywords, domains, actions, objects, outputs, "
                    "interfaces, must_have, nice_to_have, negative_filters, search_queries, "
                    "repo_search_queries, code_search_queries, topic_search_queries, issue_search_queries, "
                    "web_search_queries, evidence_aliases, evidence_components.\n"
                    "Rules:\n"
                    "- Preserve exact named entities and important phrases from the user in literal_keywords.\n"
                    "- Do not replace a specific named entity with a broad category.\n"
                    "- Preserve the user's product abstraction level and the relationships between actors, actions, "
                    "objects, constraints, and outputs. Never replace it with a broader or downstream product.\n"
                    "- Split the requirement into core outcomes and extension/implementation guesses. must_have "
                    "contains only the user's core functional outcomes and hard constraints. nice_to_have contains "
                    "optional wants, uncertain phrasing, implementation guesses, credentials, providers, or runtime "
                    "assumptions unless the user explicitly says they are mandatory.\n"
                    "- Each must_have item must be an independently verifiable repository capability or complete "
                    "workflow outcome. Do not split a helper input, framing sentence, or dependent setup step into "
                    "a standalone core feature unless repository evidence could prove it independently. For workflow "
                    "requests, keep the action, object, platform/context, and expected output together when they only "
                    "make sense as one capability.\n"
                    "- For numbered or manual-operation workflows, do not copy the numbered steps verbatim into "
                    "must_have unless a step is itself the repository capability. Infer the repository-searchable "
                    "automation capability or workflow outcome that would satisfy the steps, and keep the steps "
                    "represented through actions, objects, outputs, interfaces, queries, and evidence aliases.\n"
                    "- For multi-step workflows, domains/actions/objects/outputs/interfaces must describe the "
                    "interpreted workflow. Leaving all of those groups empty means the plan is invalid.\n"
                    "- Treat feasibility comments, ease-of-implementation comments, suggested implementation routes, "
                    "and phrases the user marks as uncertain as nice_to_have or implementation assumptions. They must "
                    "not become the core product identity or the first search angle.\n"
                    "- If the user asks whether an open-source tool exists, that availability constraint belongs in "
                    "nice_to_have or search planning. The core capability still needs to describe what the tool does.\n"
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
                    "- evidence_components must be an object keyed by every must_have item. Each feature value is an "
                    "object whose keys are current-request component labels and whose values are short lists of "
                    "literal repository phrases that prove that component. The components must collectively cover "
                    "the action, object, context/interface, named specifics, and output that are necessary for that "
                    "feature. Keep separately required specifics in separate components so a shared broad phrase "
                    "cannot prove the whole feature. Derive every component and phrase from this request only.\n"
                    "- evidence_components are proof requirements, not extra search suggestions. A repository must "
                    "support every component locally before the corresponding must_have can be confirmed.\n"
                    "- evidence_aliases must not contain extra core capability keys that are missing from must_have. "
                    "If a capability needs aliases, it belongs in must_have or nice_to_have explicitly.\n"
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
                    "angles. Do not reduce the request to one convenient sub-feature. If an anchor is uncertain or "
                    "assumptive, preserve it as a secondary/nice-to-have detail instead of a primary search anchor.\n"
                    f"Mandatory anchors: {explicit_clauses}\n"
                    f"Requirement:\n{query}"
            )
            for attempt in range(3):
                prompt = user_prompt
                if attempt:
                    prompt += (
                        "\nThe previous plan failed grounding validation. Re-plan from this requirement only. "
                        "Ensure the original-language repository and issue queries contain important terms copied "
                        "from the requirement, while keeping every channel semantically consistent. "
                        "Do not copy framing fragments as standalone capabilities. If the request is a workflow, "
                        "express the repository-searchable capability as a complete outcome with its action, object, "
                        "context, and output, then populate domains/actions/objects/outputs/interfaces from the same "
                        "current request. If the previous plan copied numbered/manual steps verbatim, replace that "
                        "with the interpreted automation or workflow capability while preserving the step objects and "
                        "outputs in the structured fields and search queries. "
                        f"Every one of these anchors must remain represented: {explicit_clauses}."
                    )
                data = await llm.json_chat(system_prompt, prompt)
                spec = self._from_llm_data(query, data)
                if spec and self._valid(spec, require_components=True):
                    return spec
                if spec and attempt:
                    anchored = self._with_anchor_queries(spec, explicit_clauses)
                    if self._valid(anchored, require_components=True):
                        return anchored
            recovery_data = await llm.json_chat(
                "Return JSON only.",
                self._recovery_prompt(query, explicit_clauses),
            )
            recovery_spec = self._from_llm_data(query, recovery_data)
            if recovery_spec and self._valid(recovery_spec, require_components=True):
                return recovery_spec
            return self._literal_only_spec(query)
        return self._literal_only_spec(query)

    @staticmethod
    def _recovery_prompt(query: str, anchors: list[str]) -> str:
        return (
            "The previous requirement plan was empty or structurally invalid. Re-plan only from the current "
            "requirement below. Return JSON with intent, literal_keywords, domains, actions, objects, outputs, "
            "interfaces, must_have, nice_to_have, search_queries, repo_search_queries, code_search_queries, "
            "topic_search_queries, issue_search_queries, web_search_queries, evidence_aliases, and "
            "evidence_components. Every must_have must be a complete repository-searchable capability, have an "
            "exact evidence_aliases key, and have non-empty evidence_components. Keep uncertain context in "
            "nice_to_have. Do not return empty lists for the core capability, planned repository queries, or "
            "evidence fields. Do not use examples, product word lists, translation tables, known repositories, "
            "or fixed phrases from other requests.\n"
            f"Requirement anchors: {anchors}\n"
            f"Requirement: {query}"
        )

    def _with_anchor_queries(self, spec: SearchSpec, anchors: list[str]) -> SearchSpec:
        anchor_queries = [anchor for anchor in anchors if anchor.strip()]
        if not anchor_queries:
            return spec
        combined_queries = self._combined_anchor_queries(anchor_queries)
        focused_queries = self._merge_lists(anchor_queries, combined_queries, limit=10)
        repo_queries = self._merge_lists(focused_queries, spec.repo_search_queries, limit=10)
        issue_queries = self._merge_lists(focused_queries, spec.issue_search_queries or spec.repo_search_queries, limit=10)
        web_queries = self._merge_lists([f"site:github.com {item}" for item in focused_queries[:3]], spec.web_search_queries, limit=10)
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

    def _combined_anchor_queries(self, anchors: list[str]) -> list[str]:
        combined: list[str] = []
        for index, left in enumerate(anchors[:5]):
            for right in anchors[index + 1 : index + 4]:
                if self._same_query_language(left, right):
                    combined.append(f"{left} {right}")
        return list(OrderedDict.fromkeys(combined))[:6]

    @staticmethod
    def _same_query_language(left: str, right: str) -> bool:
        return bool(re.search(r"[^\x00-\x7f]", left)) == bool(re.search(r"[^\x00-\x7f]", right))

    def _from_llm_data(self, query: str, data: dict[str, Any] | None) -> SearchSpec | None:
        if not isinstance(data, dict):
            return None
        search_queries = self._list(data.get("search_queries"), limit=10)
        evidence_aliases = self._evidence_aliases(data.get("evidence_aliases"))
        evidence_components = self._evidence_components(data.get("evidence_components"))
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
        raw_must_have, evidence_aliases = self._align_numbered_step_features(query, raw_must_have, evidence_aliases)
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
            evidence_components=evidence_components,
        )

    def _split_core_and_extension_features(self, query: str, features: list[str]) -> tuple[list[str], list[str]]:
        core: list[str] = []
        steps = self._numbered_requirement_clauses(self._structured_requirement_body(query))
        for feature in features:
            if self._outside_structured_requirement_body(query, feature):
                if steps and self._numbered_step_is_represented(steps, feature):
                    core.append(feature)
                continue
            core.append(feature)
        return self._non_redundant_features(core), []

    def _align_numbered_step_features(
        self,
        query: str,
        features: list[str],
        evidence_aliases: dict[str, list[str]],
    ) -> tuple[list[str], dict[str, list[str]]]:
        steps = self._numbered_requirement_clauses(self._structured_requirement_body(query))
        if not steps:
            return features, evidence_aliases
        candidate_features = self._non_redundant_features(
            [
                *features,
                *[
                    key
                    for key in evidence_aliases
                    if key not in features and not self._outside_structured_requirement_body(query, key)
                ],
            ]
        )
        if candidate_features and self._numbered_steps_are_represented(steps, candidate_features) and not self._has_split_step_fragments(
            steps, candidate_features
        ):
            return self._ensure_aliases_for_features(candidate_features, evidence_aliases)

        aligned_features: list[str] = []
        aligned_aliases: dict[str, list[str]] = {}
        for step in steps:
            normalized_step = re.sub(r"\s+", "", step.casefold())
            contained = [
                feature
                for feature in candidate_features
                if self._normalized_contains(normalized_step, feature)
            ]
            exact = [
                feature
                for feature in contained
                if re.sub(r"\s+", "", feature.casefold()) == normalized_step
            ]
            if exact:
                feature = exact[0]
            elif len(contained) >= 2:
                feature = step
            elif contained:
                feature = contained[0]
            else:
                feature = step
            aligned_features.append(feature)
            aliases = [feature]
            if feature == step:
                aliases.append(step)
            for key, values in evidence_aliases.items():
                normalized_key = re.sub(r"\s+", "", str(key).casefold())
                if normalized_key and (
                    normalized_key == re.sub(r"\s+", "", feature.casefold())
                    or normalized_key in normalized_step
                    or self._normalized_contains(normalized_key, step)
                ):
                    aliases.append(key)
                    aliases.extend(values)
            aligned_aliases[feature] = list(OrderedDict.fromkeys(item for item in aliases if item))[:12]
        return self._non_redundant_features(aligned_features), aligned_aliases

    def _numbered_steps_are_represented(self, steps: list[str], features: list[str]) -> bool:
        feature_signals = [self._anchor_signals(feature) for feature in features]
        for step in steps:
            signals = self._anchor_signals(step)
            if not signals:
                continue
            required = min(2, max(1, len(signals) // 4))
            if not any(len(signals.intersection(candidate)) >= required for candidate in feature_signals):
                return False
        return True

    def _numbered_step_is_represented(self, steps: list[str], feature: str) -> bool:
        feature_signals = self._anchor_signals(feature)
        if not feature_signals:
            return False
        for step in steps:
            signals = self._anchor_signals(step)
            if not signals:
                continue
            required = min(2, max(1, len(signals) // 4))
            if len(signals.intersection(feature_signals)) >= required:
                return True
        return False

    def _has_split_step_fragments(self, steps: list[str], features: list[str]) -> bool:
        for step in steps:
            normalized_step = re.sub(r"\s+", "", step.casefold())
            exact = False
            contained = 0
            for feature in features:
                normalized_feature = re.sub(r"\s+", "", feature.casefold())
                if not normalized_feature:
                    continue
                if normalized_feature == normalized_step:
                    exact = True
                    break
                if normalized_feature in normalized_step:
                    contained += 1
            if not exact and contained >= 2:
                return True
        return False

    @staticmethod
    def _normalized_contains(container: str, value: str) -> bool:
        normalized = re.sub(r"\s+", "", str(value or "").casefold())
        return bool(normalized and normalized in container)

    def _ensure_aliases_for_features(
        self,
        features: list[str],
        evidence_aliases: dict[str, list[str]],
    ) -> tuple[list[str], dict[str, list[str]]]:
        aliases: dict[str, list[str]] = {}
        for feature in features:
            values = [feature]
            for key, key_values in evidence_aliases.items():
                if self._norm_key(key) == self._norm_key(feature):
                    values.extend(key_values)
            aliases[feature] = list(OrderedDict.fromkeys(item for item in values if item))[:12]
        return features, aliases

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
        evidence_components = {
            item: {"literal_requirement": self._literal_aliases(item)}
            for item in features
        }
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
            evidence_components=evidence_components,
        )

    def _valid(self, spec: SearchSpec, *, require_components: bool = False) -> bool:
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
        if not spec.must_have:
            return False
        if not self._evidence_aliases_match_must_have(spec):
            return False
        if require_components and not self._evidence_components_match_must_have(spec):
            return False
        if self._splits_numbered_requirement_steps(spec.raw, spec.must_have):
            return False
        if not spec.literal_keywords and not any([spec.domains, spec.actions, spec.objects, spec.outputs]):
            return False
        if self._requires_structured_interpretation(spec.raw) and not any(
            [spec.domains, spec.actions, spec.objects, spec.outputs, spec.interfaces]
        ):
            return False
        raw_signals = self._signals(spec.raw)
        if not raw_signals:
            return True
        interpreted_plan = self._has_complete_interpreted_plan(spec) and self._uses_different_script(spec)
        grounded_queries = " ".join([*spec.repo_search_queries[:3], *spec.issue_search_queries[:3]])
        if not raw_signals.intersection(self._signals(grounded_queries)) and not interpreted_plan:
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
        if len(overlap) < min(3, max(1, len(raw_signals) // 5)) and not interpreted_plan:
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
        primary_signals = self._anchor_signals(
            " ".join(
                [
                    spec.intent,
                    *spec.domains,
                    *spec.actions,
                    *spec.objects,
                    *spec.outputs,
                    *spec.interfaces,
                    *spec.must_have,
                ]
            )
        )
        secondary_signals = self._anchor_signals(" ".join(spec.nice_to_have))
        discovery_signals = self._anchor_signals(
            " ".join([*spec.repo_search_queries, *spec.issue_search_queries])
        )
        for clause in self._planning_anchors(spec.raw):
            clause_signals = self._anchor_signals(clause)
            required_overlap = min(8, max(2, len(clause_signals) // 4))
            if (
                clause_signals
                and len(clause_signals.intersection(structured_signals)) < required_overlap
                and not interpreted_plan
            ):
                return False
            secondary_overlap = len(clause_signals.intersection(secondary_signals))
            primary_overlap = len(clause_signals.intersection(primary_signals))
            if clause_signals and secondary_overlap >= required_overlap and primary_overlap < required_overlap:
                continue
            combined_primary_secondary = clause_signals.intersection(primary_signals | secondary_signals)
            if secondary_overlap and primary_overlap >= 2 and len(combined_primary_secondary) >= required_overlap:
                continue
            if (
                clause_signals
                and len(clause_signals.intersection(discovery_signals)) < required_overlap
                and not interpreted_plan
            ):
                return False
        return True

    def _has_complete_interpreted_plan(self, spec: SearchSpec) -> bool:
        return bool(
            spec.must_have
            and self._evidence_aliases_match_must_have(spec)
            and self._evidence_components_match_must_have(spec)
            and any([spec.domains, spec.actions, spec.objects, spec.outputs, spec.interfaces])
        )

    @staticmethod
    def _uses_different_script(spec: SearchSpec) -> bool:
        raw_has_non_ascii = bool(re.search(r"[^\x00-\x7f]", spec.raw))
        plan = " ".join(
            [spec.intent, *spec.domains, *spec.actions, *spec.objects, *spec.outputs, *spec.interfaces, *spec.must_have]
        )
        return raw_has_non_ascii != bool(re.search(r"[^\x00-\x7f]", plan))

    def _evidence_components_match_must_have(self, spec: SearchSpec) -> bool:
        expected = {self._norm_key(item) for item in spec.must_have if self._norm_key(item)}
        actual = {self._norm_key(item) for item in spec.evidence_components if self._norm_key(item)}
        if not expected or actual != expected:
            return False
        for feature, groups in spec.evidence_components.items():
            if not isinstance(groups, dict) or not groups:
                return False
            for label, aliases in groups.items():
                if not str(label).strip() or not aliases:
                    return False
                if not all(str(alias).strip() for alias in aliases):
                    return False
        return True


    def _requires_structured_interpretation(self, query: str) -> bool:
        clauses = self._explicit_requirement_clauses(query)
        return len(clauses) >= 2 and len(self._anchor_signals(query)) >= 6

    def _planning_anchors(self, query: str) -> list[str]:
        text = re.sub(r"\s+", " ", str(query or "")).strip()
        if not text:
            return []
        text = self._structured_requirement_body(text)
        numbered = self._numbered_requirement_clauses(text)
        if numbered:
            return numbered
        return [text] if len(text) >= 2 else []

    def _anchor_signals(self, text: str) -> set[str]:
        signals = set(re.findall(r"[a-z][a-z0-9_.-]{2,}", (text or "").lower()))
        for run in re.findall(r"[\u4e00-\u9fff]{2,}", text or ""):
            signals.update(run[index : index + 2] for index in range(len(run) - 1))
        return signals

    def _explicit_requirement_clauses(self, query: str) -> list[str]:
        """Extract user-written product/capability anchors without domain assumptions."""
        text = re.sub(r"\s+", " ", str(query or "")).strip()
        if not text:
            return []
        text = self._structured_requirement_body(text)
        numbered = self._numbered_requirement_clauses(text)
        if numbered:
            return numbered
        parts = re.split(r"[：:，,、；;。！？!?\n]", text)
        clauses: list[str] = []
        for part in parts:
            cleaned = re.sub(r"^\d+[.)、)]\s*", "", part.strip(" .。"))
            cleaned = cleaned.strip(" .。:：")
            if len(cleaned) >= 2:
                clauses.append(cleaned)
        return list(OrderedDict.fromkeys(clauses))[:10]

    def _numbered_requirement_clauses(self, text: str) -> list[str]:
        markers = list(re.finditer(r"(?:^|[\s，,；;。:：])\d+[)、.)]\s*", text))
        if not markers:
            return []
        clauses: list[str] = []
        for index, marker in enumerate(markers):
            start = marker.end()
            end = markers[index + 1].start() if index + 1 < len(markers) else len(text)
            clause = text[start:end].strip(" \t\r\n，,；;。")
            if len(clause) >= 2:
                clauses.append(clause)
        return list(OrderedDict.fromkeys(clauses))[:10]

    @staticmethod
    def _structured_requirement_body(text: str) -> str:
        value = str(text or "").strip()
        match = re.search(r"[：:]", value)
        if not match:
            return value
        before = value[: match.start()].strip()
        after = value[match.end() :].strip()
        if not before or not after:
            return value
        has_list_body = bool(re.search(r"(?:^|[\s，,；;。])\d+[)、.)]", after))
        return after if has_list_body else value

    def _outside_structured_requirement_body(self, query: str, feature: str) -> bool:
        body = self._structured_requirement_body(query)
        if body == str(query or "").strip():
            return False
        normalized_feature = re.sub(r"\s+", "", str(feature or "").casefold())
        if not normalized_feature:
            return True
        normalized_body = re.sub(r"\s+", "", body.casefold())
        return normalized_feature not in normalized_body

    def _evidence_aliases_cover_must_have(self, spec: SearchSpec) -> bool:
        alias_keys = {self._norm_key(key) for key, values in spec.evidence_aliases.items() if values}
        return all(self._norm_key(feature) in alias_keys for feature in spec.must_have)

    def _evidence_aliases_match_must_have(self, spec: SearchSpec) -> bool:
        feature_keys = {self._norm_key(feature) for feature in spec.must_have}
        alias_keys = {self._norm_key(key) for key, values in spec.evidence_aliases.items() if values}
        return bool(feature_keys) and feature_keys == alias_keys

    def _splits_numbered_requirement_steps(self, query: str, features: list[str]) -> bool:
        steps = self._numbered_requirement_clauses(self._structured_requirement_body(query))
        if not steps:
            return False
        step_signals = [(step, self._anchor_signals(step)) for step in steps]
        for feature in features:
            normalized_feature = re.sub(r"\s+", "", str(feature or "").casefold())
            feature_signals = self._anchor_signals(feature)
            if not normalized_feature or not feature_signals:
                continue
            for step, signals in step_signals:
                normalized_step = re.sub(r"\s+", "", step.casefold())
                if normalized_feature == normalized_step:
                    break
                if normalized_feature in normalized_step:
                    return True
        return False

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

    def _evidence_components(self, value: object) -> dict[str, dict[str, list[str]]]:
        if not isinstance(value, dict):
            return {}
        components: dict[str, dict[str, list[str]]] = {}
        for raw_feature, raw_groups in value.items():
            feature = str(raw_feature).strip()
            if not feature or not isinstance(raw_groups, dict):
                continue
            groups: dict[str, list[str]] = {}
            for raw_label, raw_aliases in raw_groups.items():
                label = str(raw_label).strip()
                aliases = self._list(raw_aliases, limit=12)
                if label and aliases:
                    groups[label] = aliases
            if groups:
                components[feature] = groups
        return components

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
        if re.search(r"[，,；;。！？!?]", exact):
            return [exact]
        parts = self._literal_terms(exact)
        return list(OrderedDict.fromkeys([exact, *parts]))[:8]

    def _literal_terms(self, text: str) -> list[str]:
        chunks = re.findall(r"[A-Za-z][A-Za-z0-9_.-]{2,}|[\u4e00-\u9fff]{2,}", text.lower())
        return list(OrderedDict.fromkeys(chunks))[:10]

    def _literal_queries(self, literal: list[str], query: str, features: list[str]) -> list[str]:
        if literal:
            joined = " ".join(literal[:6])
            anchors = [item for item in features if len(self._signals(item)) >= 2]
            return list(OrderedDict.fromkeys([*anchors[:4], joined]))
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
