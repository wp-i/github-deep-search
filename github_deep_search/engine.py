from __future__ import annotations

import asyncio
import json
import math
import re
import time
from collections import OrderedDict
from difflib import SequenceMatcher
from typing import Sequence

from github_deep_search.config import Settings, get_settings
from github_deep_search.models import (
    BudgetUsage,
    CandidateRepository,
    EvidenceCoverage,
    ProjectAnalysis,
    Requirement,
    SearchReport,
)
from github_deep_search.providers.github import GitHubClient
from github_deep_search.providers.llm import LLMClient
from github_deep_search.providers.tavily import TavilyClient
from github_deep_search.spec_parser import SearchSpecParser
from github_deep_search.utils import compact_text, extract_github_repos, keyword_bag, normalize_repo_url


class DeepSearchEngine:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._readme_cache: dict[str, str] = {}
        self._tree_cache: dict[str, list[str]] = {}
        self._file_cache: dict[tuple[str, str], str] = {}

    async def run(
        self,
        query: str,
    ) -> SearchReport:
        started = time.perf_counter()
        usage = BudgetUsage()
        request_limit = self.settings.max_github_requests
        github = GitHubClient(self.settings.github_token, usage, request_limit=request_limit)
        tavily = TavilyClient(self.settings.tavily_api_key, usage) if self.settings.tavily_api_key else None
        llm = (
            LLMClient(
                self.settings.llm_api_key,
                self.settings.llm_base_url,
                self.settings.llm_model,
                usage,
                thinking=self.settings.llm_thinking,
                reasoning_effort=self.settings.llm_reasoning_effort,
            )
            if self.settings.llm_api_key
            else None
        )
        try:
            spec = await SearchSpecParser().parse(query, llm)
            requirement = spec.to_requirement()
            candidates = await self._collect_candidates(requirement, github, tavily, usage)
            discovery_requests = usage.github_requests
            ranked = self._rank_candidates(requirement, candidates)
            await self._hydrate_readmes(ranked, github, usage)
            readme_requests = usage.github_requests - discovery_requests
            reranked = self._rank_candidates(requirement, ranked)
            deep_pool_limit = self._deep_pool_limit()
            deep_repos = reranked[:deep_pool_limit]
            await self._hydrate_source_evidence(deep_repos, github, usage, requirement)
            source_requests = usage.github_requests - discovery_requests - readme_requests
            deep_repos = self._rerank_by_evidence(deep_repos, requirement)
            analyses = await self._analyze_top_projects(requirement, deep_repos, llm)
            analyses, evidence_gate_stats = self._apply_evidence_gate(requirement, analyses, usage)
            low_confidence_analyses = [item for item in analyses if item.match_score < 50]
            reliable_analyses = [item for item in analyses if item.match_score >= 50]
            low_confidence = [item.repo.full_name for item in low_confidence_analyses]
            analyses = self._with_reference_candidates(reliable_analyses, low_confidence_analyses, usage, requirement)
            if len(analyses) < self.settings.max_deep_analyze_repos and not analyses:
                analyses.extend(
                    self._fallback_low_similarity_leads(
                        requirement,
                        reranked,
                        usage,
                        slots=self.settings.max_deep_analyze_repos - len(analyses),
                        excluded_projects={item.repo.full_name.lower() for item in analyses},
                        excluded_families={self._project_family_key(item) for item in analyses},
                    )
                )
            analyses = sorted(
                analyses,
                key=lambda item: item.match_score,
                reverse=True,
            )[
                : self.settings.max_deep_analyze_repos
            ]
            returned_reference_names = {item.repo.full_name for item in analyses if item.is_reference_candidate}
            low_confidence_not_returned = [
                item.repo.full_name for item in low_confidence_analyses if item.repo.full_name not in returned_reference_names
            ]
            if low_confidence_not_returned:
                usage.warnings.append(
                    "Low-confidence candidates below 50/100 not returned: " + ", ".join(low_confidence_not_returned[:5])
                )
            if len(analyses) < self.settings.max_deep_analyze_repos and len(candidates) > len(analyses):
                usage.warnings.append(
                    f"Returned {len(analyses)} project(s) because remaining candidates did not pass confidence filtering."
                )
            opportunity = await self._analyze_opportunity(requirement, analyses, llm)
            usage.elapsed_ms = int((time.perf_counter() - started) * 1000)
            usage.estimated_usd = self._estimate_usd(usage)
            self._mark_cost_completeness(usage)
            if not usage.estimated_usd_complete:
                usage.warnings.append("LLM unit price is not configured; estimated cost excludes or undercounts LLM spend.")
            search_completeness = self._search_completeness(usage, request_limit)
            report_markdown = self._write_report(
                query,
                requirement,
                analyses,
                opportunity,
                usage,
                search_completeness,
            )
            summary = self._write_summary(analyses, opportunity)
            return SearchReport(
                query=query,
                requirement=requirement,
                top_projects=analyses,
                opportunity=opportunity,
                summary=summary,
                report_markdown=report_markdown,
                usage=usage,
                raw={
                    "candidate_count": len(candidates),
                    "ranked_count": len(ranked),
                    "deep_pool_count": len(deep_repos),
                    "top_projects_returned": len(analyses),
                    "reliable_top_projects_count": len([item for item in analyses if not item.is_reference_candidate]),
                    "reference_candidate_count": len(
                        [item for item in analyses if item.is_reference_candidate and item.confidence_level != "lead"]
                    ),
                    "low_similarity_lead_count": len([item for item in analyses if item.confidence_level == "lead"]),
                    "github_request_limit_reached": usage.github_requests >= request_limit,
                    "github_request_limit": request_limit,
                    "search_completeness": search_completeness["level"],
                    "search_completeness_reasons": search_completeness["reasons"],
                    "source_mix": self._source_mix(candidates),
                    "cache_stats": {
                        "readmes": len(self._readme_cache),
                        "trees": len(self._tree_cache),
                        "files": len(self._file_cache),
                    },
                    "request_stages": {
                        "discovery": discovery_requests,
                        "readme": readme_requests,
                        "source": source_requests,
                    },
                    "planned_query_counts": {
                        "repo": len(requirement.repo_search_queries),
                        "code": len(requirement.code_search_queries),
                        "topic": len(requirement.topic_search_queries),
                        "issue": len(requirement.issue_search_queries),
                        "web": len(requirement.web_search_queries),
                    },
                    "planned_repo_queries_used": self._planned_repo_search_queries(requirement),
                    "top_ranked_candidates": [
                        {
                            "repo": item.full_name,
                            "score": round(item.raw_score, 1),
                            "core_signal": item.core_signal_score,
                            "found_by": item.found_by[:4],
                        }
                        for item in ranked[:15]
                    ],
                    "deep_pool_candidates": [
                        {
                            "repo": item.full_name,
                            "score": round(item.raw_score, 1),
                            "core_signal": item.core_signal_score,
                        }
                        for item in deep_repos
                    ],
                    "core_requirement": self._core_requirement_feature(requirement),
                    "evidence_gate": evidence_gate_stats,
                    "low_confidence_filtered_count": len(low_confidence_not_returned),
                    "low_confidence_candidate_count": len(low_confidence),
                },
            )
        finally:
            await github.close()
            if tavily:
                await tavily.close()
            if llm:
                await llm.close()

    def _budgeted_github_limit(self) -> int:
        return self.settings.max_github_requests

    def _budgeted_candidate_limit(self) -> int:
        return self.settings.max_candidates

    def _deep_pool_limit(self) -> int:
        return 20

    def _evidence_request_reserve(self) -> int:
        """Requests discovery cannot borrow: README plus focused checks for final candidates."""
        result_count = self.settings.max_deep_analyze_repos
        readme_count = self._deep_pool_limit() + 2
        files_per_repo = 2
        return readme_count + result_count * (1 + files_per_repo)

    def _merge_queries(self, queries: list[str], limit: int = 6) -> list[str]:
        return list(OrderedDict.fromkeys(q.strip() for q in queries if q.strip()))[:limit]

    async def _collect_candidates(
        self,
        requirement: Requirement,
        github: GitHubClient,
        tavily: TavilyClient | None,
        usage: BudgetUsage,
    ) -> list[CandidateRepository]:
        repos: OrderedDict[str, CandidateRepository] = OrderedDict()
        candidate_limit = self._budgeted_candidate_limit()
        repo_queries = self._planned_repo_search_queries(requirement)
        code_queries = self._planned_code_search_queries(requirement)
        topic_queries = self._planned_topic_search_queries(requirement)
        issue_queries = self._interleave_multilingual_queries(
            self._planned_issue_search_queries(requirement)
        )
        queries_per_wave = 6
        request_limit = self._budgeted_github_limit()
        evidence_reserve = self._evidence_request_reserve()
        search_request_limit = max(8, request_limit - evidence_reserve)
        repo_per_page = 20
        code_per_page = 10
        topic_per_page = 20
        issue_per_page = 20

        for wave_index in range(2):
            await self._collect_github_wave(
                repos,
                github,
                usage,
                repo_queries,
                code_queries,
                topic_queries,
                issue_queries,
                wave_index=wave_index,
                queries_per_wave=queries_per_wave,
                repo_per_page=repo_per_page,
                code_per_page=code_per_page,
                topic_per_page=topic_per_page,
                issue_per_page=issue_per_page,
                request_limit=search_request_limit,
                candidate_limit=candidate_limit,
            )
            if len(repos) >= candidate_limit:
                break

        if len(repos) < self.settings.max_deep_analyze_repos and self._has_github_wave_queries(
            repo_queries,
            code_queries,
            topic_queries,
            issue_queries,
            wave_index=2,
            queries_per_wave=queries_per_wave,
        ):
            usage.warnings.append("Fewer than Top 3 candidate repositories after two GitHub waves; running a third wave.")
            await self._collect_github_wave(
                repos,
                github,
                usage,
                repo_queries,
                code_queries,
                topic_queries,
                issue_queries,
                wave_index=2,
                queries_per_wave=queries_per_wave,
                repo_per_page=repo_per_page,
                code_per_page=code_per_page,
                topic_per_page=topic_per_page,
                issue_per_page=issue_per_page,
                request_limit=search_request_limit,
                candidate_limit=candidate_limit,
            )

        if tavily:
            planned_web_queries = requirement.web_search_queries or requirement.search_queries
            web_limit = 4
            web_queries = planned_web_queries[:web_limit]
            for search_query in web_queries:
                if usage.tavily_credits >= self.settings.max_tavily_credits:
                    usage.warnings.append("Tavily budget reached during cross-validation.")
                    break
                results = await tavily.search(f"site:github.com {search_query}", max_results=5)
                repo_pairs: list[tuple[str, str]] = []
                for item in results:
                    url_pair = normalize_repo_url(str(item.get("url") or ""))
                    if url_pair:
                        repo_pairs.append(url_pair)
                    repo_pairs.extend(extract_github_repos(str(item.get("content") or "")))
                for owner, name in repo_pairs[:5]:
                    key = f"{owner.lower()}/{name.lower()}"
                    if key in repos:
                        repos[key].found_by.append(f"tavily:{search_query}")
                        continue
                    if usage.github_requests >= search_request_limit:
                        break
                    repo = await github.get_repository(owner, name, found_by=f"tavily:{search_query}")
                    if repo:
                        self._merge_repo(repos, repo)
                        if len(repos) >= candidate_limit:
                            break
        return list(repos.values())

    async def _collect_github_wave(
        self,
        repos: OrderedDict[str, CandidateRepository],
        github: GitHubClient,
        usage: BudgetUsage,
        repo_queries: list[str],
        code_queries: list[str],
        topic_queries: list[str],
        issue_queries: list[str],
        wave_index: int,
        queries_per_wave: int,
        repo_per_page: int,
        code_per_page: int,
        topic_per_page: int,
        issue_per_page: int,
        request_limit: int,
        candidate_limit: int,
    ) -> None:
        start = wave_index * queries_per_wave
        end = start + queries_per_wave
        repo_batch = repo_queries[start:end]
        code_batch = code_queries[start:end]
        topic_batch = topic_queries[start:end]
        issue_batch = issue_queries[start:end]
        if not any([repo_batch, code_batch, topic_batch, issue_batch]):
            return
        # Run channels in order so each one sees the latest usage count. Parallel
        # checks can all pass at once and consume the requests reserved for evidence.
        results = []
        results.append(
            await self._search_repo_candidates(
                github, usage, repo_batch, per_page=repo_per_page, request_limit=request_limit
            )
        )
        results.append(
            await self._search_topic_candidates(
                github, usage, topic_batch, per_page=topic_per_page, request_limit=request_limit
            )
        )
        results.append(
            await self._search_code_candidates(
                github, usage, code_batch, per_page=code_per_page, request_limit=request_limit
            )
        )
        results.append(
            await self._search_issue_candidates(
                github, usage, issue_batch, per_page=issue_per_page, request_limit=request_limit
            )
        )
        for repo in [item for batch in results for item in batch]:
            self._merge_repo(repos, repo)
            if len(repos) >= candidate_limit:
                break

    def _has_github_wave_queries(
        self,
        repo_queries: list[str],
        code_queries: list[str],
        topic_queries: list[str],
        issue_queries: list[str],
        wave_index: int,
        queries_per_wave: int,
    ) -> bool:
        start = wave_index * queries_per_wave
        return any(
            len(queries) > start
            for queries in [repo_queries, code_queries, topic_queries, issue_queries]
        )

    async def _search_repo_candidates(
        self,
        github: GitHubClient,
        usage: BudgetUsage,
        queries: list[str],
        per_page: int,
        request_limit: int,
    ) -> list[CandidateRepository]:
        candidates: list[CandidateRepository] = []
        for search_query in queries:
            if usage.github_requests >= request_limit:
                usage.warnings.append("GitHub request budget reached during repository search.")
                break
            gh_query = self._to_github_repo_query(search_query)
            candidates.extend(await github.search_repositories(gh_query, per_page=per_page))
        return candidates

    async def _search_code_candidates(
        self,
        github: GitHubClient,
        usage: BudgetUsage,
        queries: list[str],
        per_page: int,
        request_limit: int,
    ) -> list[CandidateRepository]:
        candidates: list[CandidateRepository] = []
        seen: set[str] = set()
        for search_query in queries:
            if usage.github_requests >= request_limit:
                usage.warnings.append("GitHub request budget reached during code search.")
                break
            for owner, name, path in await github.search_code_repositories(search_query, per_page=per_page):
                key = f"{owner.lower()}/{name.lower()}"
                if key in seen:
                    continue
                seen.add(key)
                if usage.github_requests >= request_limit:
                    break
                found_by = f"github_code:{search_query}"
                if path:
                    found_by += f":{path}"
                repo = await github.get_repository(owner, name, found_by=found_by)
                if repo:
                    candidates.append(repo)
        return candidates

    async def _search_topic_candidates(
        self,
        github: GitHubClient,
        usage: BudgetUsage,
        topics: list[str],
        per_page: int,
        request_limit: int,
    ) -> list[CandidateRepository]:
        candidates: list[CandidateRepository] = []
        for topic in topics:
            if usage.github_requests >= request_limit:
                usage.warnings.append("GitHub request budget reached during topic search.")
                break
            topic_query = self._to_github_topic_query(topic)
            if topic_query:
                candidates.extend(await github.search_topic_repositories(topic_query, per_page=per_page))
        return candidates

    async def _search_issue_candidates(
        self,
        github: GitHubClient,
        usage: BudgetUsage,
        queries: list[str],
        per_page: int,
        request_limit: int,
    ) -> list[CandidateRepository]:
        candidates: list[CandidateRepository] = []
        seen: set[str] = set()
        for search_query in queries:
            if usage.github_requests >= request_limit:
                usage.warnings.append("GitHub request budget reached during issue search.")
                break
            for owner, name in await github.search_issue_repositories(search_query, per_page=per_page):
                key = f"{owner.lower()}/{name.lower()}"
                if key in seen:
                    continue
                seen.add(key)
                if usage.github_requests >= request_limit:
                    break
                repo = await github.get_repository(owner, name, found_by=f"github_issue:{search_query}")
                if repo:
                    candidates.append(repo)
        return candidates

    def _planned_code_search_queries(self, requirement: Requirement) -> list[str]:
        limit = 5
        queries: list[str] = []
        core_feature = self._core_requirement_feature(requirement)
        core_aliases = (
            sorted(self._feature_aliases(core_feature, requirement), key=self._query_specificity_key)
            if core_feature
            else []
        )
        planned = [*core_aliases, *requirement.code_search_queries]
        for phrase in self._merge_queries(planned, limit=limit):
            token = self._github_search_token(phrase)
            if token:
                queries.append(f"{token} in:file,path")
        return self._merge_queries(queries, limit=limit)

    def _planned_repo_search_queries(
        self,
        requirement: Requirement,
    ) -> list[str]:
        """Put narrow core-capability angles before secondary output/interface queries."""
        limit = 10
        core_feature = self._core_requirement_feature(requirement)
        concepts = requirement.feature_concepts or {}
        domains = [str(item).strip() for item in concepts.get("domains", []) if str(item).strip()]
        targets = [
            str(item).strip()
            for item in [*concepts.get("objects", []), *concepts.get("actions", [])]
            if str(item).strip()
        ]
        core_queries: list[str] = []
        for domain in domains[:4]:
            for target in targets[:6]:
                if self._same_query_language(domain, target):
                    core_queries.append(f"{domain} {target}")
        if core_feature:
            core_aliases = self._feature_aliases(core_feature, requirement)
            aliases = [
                alias
                for alias in sorted(core_aliases, key=self._query_specificity_key)
                if self._searchable_repo_phrase(alias, core_feature)
            ]
            aliases.extend(self._combined_core_alias_queries(core_aliases))
            core_queries.extend(aliases)
        core_queries.extend(domains[:4])
        channel_queries = self._merge_queries(
            [*requirement.repo_search_queries, *requirement.search_queries],
            limit=20,
        )
        planned = [
            query
            for query in [*channel_queries, *core_queries]
            if self._searchable_repo_phrase(query, core_feature or "", allow_single_signal=query in channel_queries)
        ]
        if not planned:
            planned = [*channel_queries, *core_queries]
        planned = sorted(planned, key=lambda query: self._repo_query_priority_key(query, requirement, core_feature))
        return self._merge_queries(planned, limit=limit)

    def _query_specificity_key(self, query: str) -> tuple[int, int, int, str]:
        signals = self._semantic_signals(query)
        words = re.findall(r"[A-Za-z0-9_.-]{2,}|[\u4e00-\u9fff]{2,}", str(query))
        return (-len(signals), -len(words), -len(str(query)), str(query).lower())

    def _repo_query_priority_key(
        self,
        query: str,
        requirement: Requirement,
        core_feature: str | None,
    ) -> tuple[int, int, int, int, int, str]:
        core_aliases = self._feature_aliases(core_feature, requirement) if core_feature else set()
        core_match = bool(core_feature and self._matching_terms(query, core_aliases))
        secondary_aliases = set()
        for feature in requirement.must_have_features:
            if core_feature and self._same_feature_key(feature, core_feature):
                continue
            secondary_aliases.update(self._feature_aliases(feature, requirement))
        secondary_match = bool(secondary_aliases and self._matching_terms(query, secondary_aliases))
        specificity = self._query_specificity_key(query)
        return (
            0 if core_match else 1,
            1 if secondary_match and not core_match else 0,
            specificity[0],
            specificity[1],
            specificity[2],
            specificity[3],
        )

    def _searchable_repo_phrase(self, phrase: str, core_feature: str, allow_single_signal: bool = False) -> bool:
        signals = self._semantic_signals(phrase)
        meaningful = signals - self._generic_search_signals()
        if self._same_feature_key(phrase, core_feature):
            return len(meaningful) >= 2
        return len(meaningful) >= (1 if allow_single_signal else 2)

    def _combined_core_alias_queries(self, aliases: set[str]) -> list[str]:
        single_signal_aliases: list[str] = []
        multi_signal_aliases: list[str] = []
        for alias in aliases:
            signals = self._semantic_signals(alias) - self._generic_search_signals()
            if len(signals) == 1 and re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{1,}", alias.strip()):
                single_signal_aliases.append(alias.strip())
            elif len(signals) >= 1:
                multi_signal_aliases.append(alias.strip())
        combined: list[str] = []
        for left_index, left in enumerate(sorted(single_signal_aliases, key=str.lower)):
            for right in sorted(single_signal_aliases, key=str.lower)[left_index + 1 :]:
                combined.append(f"{left} {right}")
        for single in sorted(single_signal_aliases, key=str.lower):
            for phrase in sorted(multi_signal_aliases, key=self._query_specificity_key):
                if single.lower() in phrase.lower():
                    continue
                combined.append(f"{single} {phrase}")
        return self._merge_queries(combined, limit=8)

    @staticmethod
    def _generic_search_signals() -> set[str]:
        return set()

    def _topic_query_variants(self, phrases: list[str]) -> list[str]:
        variants: list[str] = []
        generic = self._generic_search_signals()
        for phrase in phrases:
            tokens = re.findall(r"[A-Za-z0-9_.-]{2,}|[\u4e00-\u9fff]{2,}", str(phrase).lower())
            tokens = [token for token in tokens if token not in generic]
            if not tokens:
                continue
            variants.append("-".join(tokens[:4]))
            for size in (3, 2):
                for index in range(0, max(0, len(tokens) - size + 1)):
                    variants.append("-".join(tokens[index : index + size]))
            variants.extend(token for token in tokens if len(token) >= 3 or re.search(r"[\u4e00-\u9fff]", token))
        return self._merge_queries(variants, limit=20)

    def _planned_topic_search_queries(
        self,
        requirement: Requirement,
    ) -> list[str]:
        limit = 8
        concepts = requirement.feature_concepts or {}
        domains = [str(item).strip() for item in concepts.get("domains", []) if str(item).strip()]
        alias_phrases = [
            str(value).strip()
            for values in (requirement.evidence_aliases or {}).values()
            for value in values
            if str(value).strip()
        ]
        concept_phrases = [
            str(value).strip()
            for group in ("literal_keywords", "domains", "objects", "outputs", "interfaces")
            for value in concepts.get(group, [])
            if str(value).strip()
        ]
        variant_groups = [
            [query]
            for query in requirement.topic_search_queries
            if str(query).strip()
        ]
        variant_groups.extend(
            self._topic_query_variants([phrase])
            for phrase in [*alias_phrases, *concept_phrases]
            if phrase
        )
        planned: list[str] = []
        for index in range(max((len(group) for group in variant_groups), default=0)):
            for group in variant_groups:
                if index < len(group):
                    planned.append(group[index])
        planned.extend(domains)
        return self._merge_queries(planned, limit=limit)

    @staticmethod
    def _same_query_language(left: str, right: str) -> bool:
        return bool(re.search(r"[^\x00-\x7f]", left)) == bool(re.search(r"[^\x00-\x7f]", right))

    def _interleave_multilingual_queries(self, queries: list[str]) -> list[str]:
        original_language = [item for item in queries if re.search(r"[^\x00-\x7f]", item)]
        ascii_queries = [item for item in queries if not re.search(r"[^\x00-\x7f]", item)]
        if not original_language or not ascii_queries:
            return list(queries)
        interleaved: list[str] = []
        for index in range(max(len(original_language), len(ascii_queries))):
            if index < len(original_language):
                interleaved.append(original_language[index])
            if index < len(ascii_queries):
                interleaved.append(ascii_queries[index])
        return interleaved

    def _planned_issue_search_queries(
        self,
        requirement: Requirement,
    ) -> list[str]:
        limit = 5
        return self._merge_queries(requirement.issue_search_queries or requirement.repo_search_queries, limit=limit)

    def _github_search_token(self, phrase: str) -> str:
        clean = re.sub(r"\s+", " ", str(phrase).strip())
        if not clean:
            return ""
        if any(char.isspace() for char in clean):
            return f'"{clean[:80]}"'
        return clean[:80]

    def _to_github_repo_query(self, query: str) -> str:
        words = re.findall(r"[A-Za-z0-9_.-]{2,}|[\u4e00-\u9fff]{2,}", query)
        if not words:
            return query
        # GitHub joins plain words as AND conditions. Long product sentences
        # become so restrictive that the canonical project disappears.
        clean = " ".join(words[:3])
        return f"{clean} in:name,description,readme"

    def _to_github_topic_query(self, topic: str) -> str:
        clean = re.sub(r"\s+", "-", str(topic).strip().lower())
        clean = re.sub(r"[^a-z0-9_.\-\u4e00-\u9fff]", "", clean).strip(".-_")
        return clean[:50]

    def _merge_repo(self, repos: OrderedDict[str, CandidateRepository], repo: CandidateRepository) -> None:
        if not repo.owner or not repo.name:
            return
        key = repo.full_name.lower()
        if key in repos:
            for source in repo.found_by:
                if source not in repos[key].found_by:
                    repos[key].found_by.append(source)
            return
        repos[key] = repo

    def _source_mix(self, candidates: list[CandidateRepository]) -> dict[str, int]:
        mix = {"github_repo": 0, "github_code": 0, "github_topic": 0, "github_issue": 0, "tavily": 0, "other": 0}
        for repo in candidates:
            sources = repo.found_by or []
            counted = False
            if any(source.startswith("github:") for source in sources):
                mix["github_repo"] += 1
                counted = True
            if any(source.startswith("github_code:") for source in sources):
                mix["github_code"] += 1
                counted = True
            if any(source.startswith("github_topic:") for source in sources):
                mix["github_topic"] += 1
                counted = True
            if any(source.startswith("github_issue:") for source in sources):
                mix["github_issue"] += 1
                counted = True
            if any(source.startswith("tavily:") for source in sources):
                mix["tavily"] += 1
                counted = True
            if not counted:
                mix["other"] += 1
        return mix

    def _rank_candidates(self, requirement: Requirement, candidates: list[CandidateRepository]) -> list[CandidateRepository]:
        query_words = keyword_bag(
            " ".join(
                [
                    requirement.raw,
                    requirement.intent,
                    " ".join(requirement.must_have_features),
                    " ".join(requirement.target_platforms),
                ]
            )
        )
        query_words.update(self._requirement_aliases(requirement))
        alias_terms = self._requirement_aliases(requirement)
        concept_groups = self._requirement_concept_groups(requirement)
        domain_aliases = {item.lower() for item in (requirement.feature_concepts or {}).get("domains", [])}
        desired_languages = self._requirement_language_constraints(requirement, candidates)
        for repo in candidates:
            strong_haystack = " ".join([repo.name, repo.description, repo.language or "", " ".join(repo.topics)])
            weak_haystack = repo.readme[:12000]
            combined_haystack = f"{strong_haystack} {weak_haystack}".lower()
            strong_words = keyword_bag(strong_haystack)
            weak_words = keyword_bag(weak_haystack)
            strong_alias_hits = {term for term in alias_terms if term in strong_haystack.lower()}
            weak_alias_hits = {term for term in alias_terms if term in weak_haystack.lower()}
            strong_overlap = len(query_words & strong_words) + len(strong_alias_hits)
            weak_overlap = len(query_words & weak_words) + len(weak_alias_hits - strong_alias_hits)
            strong_coverage = strong_overlap / max(1, len(query_words))
            weak_coverage = weak_overlap / max(1, len(query_words))
            token_coverage = strong_coverage * 0.75 + weak_coverage * 0.25
            semantic_coverage, covered_groups = self._semantic_coverage(concept_groups, strong_haystack, weak_haystack)
            popularity = min(10, math.log10(repo.stars + 1) * 3.5)
            cross_source = min(8, max(0, len(repo.found_by) - 1) * 4)
            source_quality = self._source_quality_score(repo)
            freshness = 5 if repo.last_pushed_at and repo.last_pushed_at[:4] >= "2024" else 0
            readme_bonus = 8 if repo.readme else 0
            domain_bonus = 0
            if domain_aliases:
                if any(alias in strong_haystack.lower() for alias in domain_aliases):
                    domain_bonus = 20
                elif any(alias in combined_haystack for alias in domain_aliases):
                    domain_bonus = 8
                else:
                    domain_bonus = -60
            language_bonus = 0
            if desired_languages:
                repo_language = str(repo.language or "").strip().lower()
                if repo_language in desired_languages:
                    language_bonus = 28
                elif repo_language:
                    language_bonus = -45
                else:
                    language_bonus = -12
            specificity_bonus = min(12, max(0, covered_groups - 2) * 4)
            repo.core_signal_score = self._core_direction_score(requirement, repo)
            core_bonus = repo.core_signal_score * 18
            repo.raw_score = (
                semantic_coverage * 70
                + token_coverage * 15
                + popularity
                + cross_source
                + source_quality
                + freshness
                + readme_bonus
                + domain_bonus
                + language_bonus
                + specificity_bonus
                + core_bonus
            )
            if self._is_catalog_repository(repo):
                repo.raw_score = min(repo.raw_score, 15)
        return sorted(candidates, key=lambda item: item.raw_score, reverse=True)

    def _requirement_language_constraints(
        self,
        requirement: Requirement,
        candidates: list[CandidateRepository],
    ) -> set[str]:
        candidate_languages = {str(repo.language or "").strip().lower() for repo in candidates if repo.language}
        if not candidate_languages:
            return set()
        concepts = requirement.feature_concepts or {}
        text = " ".join(
            [
                requirement.raw,
                requirement.intent,
                " ".join(requirement.target_platforms),
                " ".join(concepts.get("interfaces", [])),
                " ".join(concepts.get("literal_keywords", [])),
            ]
        ).lower()
        tokens = set(re.findall(r"[a-z][a-z0-9_.+-]{1,}", text))
        return {language for language in candidate_languages if language in tokens}

    def _core_direction_score(self, requirement: Requirement, repo: CandidateRepository) -> float:
        core_feature = self._core_requirement_feature(requirement)
        if not core_feature:
            return 0.0
        core_aliases = self._feature_aliases(core_feature, requirement)
        domain_aliases = self._clean_feature_aliases(
            {str(item) for item in (requirement.feature_concepts or {}).get("domains", [])}
        )
        strong = " ".join([repo.name, repo.description, " ".join(repo.topics)])
        weak = self._readme_capability_text(repo.readme)
        core_strong = bool(self._matching_terms(strong, core_aliases))
        core_weak = bool(self._matching_terms(weak, core_aliases))
        domain_strong = bool(domain_aliases and self._matching_terms(strong, domain_aliases))
        domain_weak = bool(domain_aliases and self._matching_terms(weak, domain_aliases))
        if domain_aliases and domain_strong and core_strong:
            return 3.0
        if domain_aliases and domain_strong:
            return 2.5
        if domain_aliases and domain_weak and (core_strong or core_weak):
            return 2.0
        if domain_aliases and domain_weak:
            return 1.0
        if domain_aliases and core_strong:
            return 2.0
        if domain_aliases and core_weak:
            return 1.0
        if not domain_aliases and core_strong:
            return 3.0
        if not domain_aliases and core_weak:
            return 2.0
        return 0.0

    def _is_catalog_repository(self, repo: CandidateRepository) -> bool:
        readme = str(repo.readme or "")
        if len(readme) >= 20_000:
            github_links = len(re.findall(r"https?://github\.com/[^)\s]+", readme, flags=re.IGNORECASE))
            external_links = len(re.findall(r"https?://", readme, flags=re.IGNORECASE))
            if github_links >= 50 and external_links >= github_links:
                return True
        return False

    def _source_quality_score(self, repo: CandidateRepository) -> float:
        sources = repo.found_by or []
        score = 0.0
        if any(source.startswith("github:") for source in sources):
            score += 14
        if any(source.startswith("github_topic:") for source in sources):
            score += 6
        if any(source.startswith("github_issue:") for source in sources):
            score += 4
        if any(source.startswith("github_code:") for source in sources):
            score += 2
        if any(source.startswith("tavily:") for source in sources):
            score += 5
        return min(score, 18)

    def _requirement_concept_groups(self, requirement: Requirement) -> dict[str, set[str]]:
        concepts = requirement.feature_concepts or {}
        groups: dict[str, set[str]] = {}
        for group, values in concepts.items():
            terms = {str(value).strip().lower() for value in values if str(value).strip()}
            if terms:
                groups[group] = terms
        return groups

    def _semantic_coverage(
        self,
        concept_groups: dict[str, set[str]],
        strong_haystack: str,
        weak_haystack: str,
    ) -> tuple[float, int]:
        if not concept_groups:
            return 0.0, 0
        group_weights = {
            "domains": 1.6,
            "actions": 1.25,
            "objects": 1.35,
            "outputs": 1.1,
            "interfaces": 0.7,
        }
        strong = strong_haystack.lower()
        weak = weak_haystack.lower()
        total = 0.0
        score = 0.0
        covered = 0
        for group, aliases in concept_groups.items():
            weight = group_weights.get(group, 1.0)
            total += weight
            if any(alias in strong for alias in aliases):
                score += weight
                covered += 1
            elif any(alias in weak for alias in aliases):
                score += weight * 0.68
                covered += 1
        return score / max(total, 1.0), covered

    async def _hydrate_readmes(
        self,
        ranked: list[CandidateRepository],
        github: GitHubClient,
        usage: BudgetUsage,
    ) -> None:
        limit = self._deep_pool_limit() + 2
        request_limit = self._budgeted_github_limit()
        source_reserve = self.settings.max_deep_analyze_repos * 3
        for repo in ranked[:limit]:
            if usage.github_requests >= request_limit - source_reserve:
                usage.warnings.append("GitHub request budget reached before fetching all READMEs.")
                break
            await self._fetch_readme_into(repo, github)

    async def _fetch_readme_into(self, repo: CandidateRepository, github: GitHubClient) -> None:
        key = repo.full_name.lower()
        if key not in self._readme_cache:
            self._readme_cache[key] = await github.fetch_readme(repo)
        repo.readme = self._readme_cache[key]

    async def _hydrate_source_evidence(
        self,
        repos: list[CandidateRepository],
        github: GitHubClient,
        usage: BudgetUsage,
        requirement: Requirement,
    ) -> None:
        repo_limit = self.settings.max_deep_analyze_repos
        max_files = 2
        for repo in repos[:repo_limit]:
            if usage.github_requests >= self._budgeted_github_limit():
                usage.warnings.append("GitHub request budget reached before source evidence checks.")
                break
            await self._fetch_source_evidence_into(repo, github, requirement, max_files=max_files)

    async def _fetch_source_evidence_into(
        self,
        repo: CandidateRepository,
        github: GitHubClient,
        requirement: Requirement,
        max_files: int | None = None,
    ) -> None:
        repo_key = repo.full_name.lower()
        if repo_key not in self._tree_cache:
            self._tree_cache[repo_key] = await github.fetch_tree_paths(repo)
        repo.file_paths = self._tree_cache[repo_key]
        selected_paths = self._select_key_paths(repo.file_paths, requirement)
        if max_files is not None:
            selected_paths = selected_paths[:max_files]
        for path in selected_paths:
            cache_key = (repo_key, path)
            if cache_key not in self._file_cache:
                self._file_cache[cache_key] = await github.fetch_file_text(repo, path, max_chars=9000)
            repo.key_files[path] = self._file_cache[cache_key]
        repo.evidence_coverage = self._build_evidence_coverage(repo, requirement)
        repo.source_evidence = self._build_source_evidence(repo, requirement)

    def _select_key_paths(self, paths: list[str], requirement: Requirement) -> list[str]:
        aliases = self._requirement_aliases(requirement)
        max_files = 8
        weighted: list[tuple[int, str]] = []
        config_names = {
            "package.json",
            "pyproject.toml",
            "requirements.txt",
            "setup.py",
            "manifest.json",
            "dockerfile",
            "go.mod",
            "pom.xml",
            "license",
            "license.md",
            "license.txt",
        }
        binary_ext = {
            ".png",
            ".jpg",
            ".jpeg",
            ".gif",
            ".webp",
            ".ico",
            ".pdf",
            ".zip",
            ".gz",
            ".mp4",
            ".mov",
            ".woff",
            ".ttf",
        }
        source_ext = {
            ".py",
            ".js",
            ".ts",
            ".tsx",
            ".jsx",
            ".go",
            ".rs",
            ".java",
            ".kt",
            ".php",
            ".rb",
            ".cs",
            ".json",
            ".yaml",
            ".yml",
            ".md",
        }
        for path in paths[:1200]:
            lowered = path.lower()
            if any(lowered.endswith(ext) for ext in binary_ext):
                continue
            suffix = "." + lowered.rsplit(".", 1)[-1] if "." in lowered else ""
            basename = lowered.rsplit("/", 1)[-1]
            score = 0
            if basename in config_names:
                score += 80
            if any(part in lowered for part in ["/src/", "/lib/", "/app/", "/packages/", "/extension/"]):
                score += 20
            if suffix in source_ext:
                score += 10
            for alias in aliases:
                if alias in lowered:
                    score += 35
            if score:
                weighted.append((score, path))
        weighted.sort(key=lambda item: (-item[0], len(item[1])))
        selected: list[str] = []
        for _, path in weighted:
            if path not in selected:
                selected.append(path)
            if len(selected) >= max_files:
                break
        return selected

    def _build_source_evidence(self, repo: CandidateRepository, requirement: Requirement) -> list[str]:
        aliases = self._requirement_aliases(requirement)
        evidence: list[str] = []
        if repo.file_paths:
            evidence.append(f"已读取文件树：{len(repo.file_paths)} 个文件路径")
        if repo.key_files:
            evidence.append(f"已抽检关键文件：{', '.join(repo.key_files.keys())}")

        path_hits = [path for path in repo.file_paths if self._contains_alias(path, aliases)]
        if path_hits:
            evidence.append(f"路径命中需求关键词：{', '.join(path_hits[:5])}")

        content_hits: list[str] = []
        for path, text in repo.key_files.items():
            hits = sorted({alias for alias in aliases if alias in text.lower()})
            if hits:
                content_hits.append(f"{path} 包含 {', '.join(hits[:6])}")
        if content_hits:
            evidence.append("源码/配置命中：" + "；".join(content_hits[:4]))
        elif repo.key_files:
            evidence.append("关键文件未命中主要需求关键词，源码强证据不足")
        elif repo.file_paths:
            evidence.append("仅完成文件树检查，未抽到可验证实现的关键源码")

        readme_hits = sorted({alias for alias in aliases if alias in repo.readme.lower()})
        if readme_hits:
            evidence.append(f"README 命中：{', '.join(readme_hits[:8])}")
        if not evidence:
            evidence.append("未找到 README 之外的强证据，需要人工复核仓库实现")
        return evidence[:6]

    def _build_evidence_coverage(self, repo: CandidateRepository, requirement: Requirement) -> list[EvidenceCoverage]:
        coverage: list[EvidenceCoverage] = []
        is_catalog = self._is_catalog_repository(repo)
        capability_readme = self._readme_capability_text(repo.readme)
        for feature in self._evidence_gate_features(requirement):
            aliases = self._feature_aliases(feature, requirement)
            if not aliases:
                continue
            public_description = " ".join([repo.description, " ".join(repo.topics)])
            readme_hits = [] if is_catalog else self._matching_feature_terms(feature, capability_readme, aliases)
            description_hits = [] if is_catalog else self._matching_feature_terms(feature, public_description, aliases)
            path_evidence: list[str] = []
            for path in ([] if is_catalog else repo.file_paths):
                if not self._path_can_prove_capability(path):
                    continue
                hits = self._matching_feature_terms(feature, path, aliases)
                if hits:
                    path_evidence.append(f"{path} ({', '.join(hits[:3])})")
                if len(path_evidence) >= 5:
                    break
            source_evidence: list[str] = []
            for path, text in ({} if is_catalog else repo.key_files).items():
                evidence_text = self._readme_capability_text(text) if path.lower().endswith((".md", ".mdx")) else text
                hits = self._matching_feature_terms(feature, evidence_text, aliases)
                if hits:
                    source_evidence.append(f"{path} ({', '.join(hits[:3])})")
                if len(source_evidence) >= 5:
                    break
            explicit_missing = "" if is_catalog else self._explicit_missing_reason(repo.readme, aliases)
            covered = bool(
                path_evidence
                or source_evidence
                or description_hits
                or (readme_hits and not explicit_missing)
            )
            core_feature = self._core_requirement_feature(requirement)
            compositional_groups = sum(
                bool((requirement.feature_concepts or {}).get(group))
                for group in ("domains", "actions", "objects")
            )
            if (
                feature == core_feature
                and compositional_groups >= 2
                and self._core_feature_requires_compositional_evidence(requirement, feature)
            ):
                core_aligned = self._core_evidence_is_compositional(requirement, repo)
                if core_aligned and not covered and not explicit_missing:
                    covered = True
                    readme_hits = ["核心平台、操作和对象在同一说明中得到确认"]
                elif not core_aligned:
                    covered = False
                    path_evidence = []
                    source_evidence = []
                    description_hits = []
                    readme_hits = []
            if covered and not self._named_entities_are_all_present(feature, repo, aliases):
                covered = False
                path_evidence = []
                source_evidence = []
                description_hits = []
                readme_hits = []
            if path_evidence or source_evidence or description_hits:
                explicit_missing = ""
            status = "supported" if covered else ("missing" if explicit_missing else "unknown")
            coverage.append(
                EvidenceCoverage(
                    feature=feature,
                    covered=covered,
                    status=status,
                    readme_evidence=(
                        [f"公开说明 ({', '.join(list(dict.fromkeys([*description_hits, *readme_hits]))[:6])})"]
                        if description_hits or readme_hits
                        else []
                    ),
                    source_evidence=source_evidence,
                    path_evidence=path_evidence,
                    missing_reason=explicit_missing,
                    unknown_reason="" if covered or explicit_missing else "公开说明中暂未确认",
                )
            )
        return coverage

    def _core_evidence_is_compositional(self, requirement: Requirement, repo: CandidateRepository) -> bool:
        concepts = requirement.feature_concepts or {}
        groups = {
            group: self._clean_feature_aliases({str(item) for item in concepts.get(group, [])})
            for group in ("domains", "actions", "objects")
        }
        required_groups = [group for group, aliases in groups.items() if aliases]
        if len(required_groups) < 2:
            return False
        core_feature = self._core_requirement_feature(requirement) or ""
        core_signals = self._semantic_signals(core_feature)
        domain_aliases = groups.get("domains", set())
        text = "\n".join(
            [
                " ".join([repo.name, repo.description, " ".join(repo.topics)]),
                self._readme_capability_text(repo.readme),
                *[
                    self._readme_capability_text(content)
                    if path.lower().endswith((".md", ".mdx"))
                    else content
                    for path, content in repo.key_files.items()
                ],
            ]
        )
        for chunk in re.split(r"[\r\n。！？!?；;]+", text):
            chunk = chunk.strip()
            if not chunk:
                continue
            chunk_signals = self._semantic_signals(chunk)
            overlap = core_signals.intersection(chunk_signals)
            overlap_threshold = min(3, len(core_signals))
            hits = sum(bool(self._matching_terms(chunk, groups[group])) for group in required_groups)
            if hits == len(required_groups):
                return len(overlap) >= overlap_threshold
            domain_hit = bool(domain_aliases and self._matching_terms(chunk, domain_aliases))
            action_hit = bool(groups.get("actions") and self._matching_terms(chunk, groups["actions"]))
            if domain_hit:
                if len(overlap) >= 3:
                    return True
                if action_hit and len(overlap) >= 2:
                    return True
        return False

    def _core_feature_requires_compositional_evidence(self, requirement: Requirement, feature: str) -> bool:
        concepts = requirement.feature_concepts or {}
        matched: set[str] = set()
        for group in ("domains", "actions", "objects"):
            aliases = self._clean_feature_aliases({str(item) for item in concepts.get(group, [])})
            if aliases and self._matching_terms(feature, aliases):
                matched.add(group)
        return len(matched) >= 2 and bool(matched.intersection({"actions", "objects"}))

    def _named_entities_are_all_present(
        self,
        feature: str,
        repo: CandidateRepository,
        aliases: set[str] | None = None,
    ) -> bool:
        entities = self._named_entity_tokens(feature)
        if len(entities) < 2:
            return True
        text = " ".join(
            [
                repo.name,
                repo.description,
                " ".join(repo.topics),
                self._readme_capability_text(repo.readme),
                *repo.file_paths,
                *repo.key_files.values(),
            ]
        ).lower()
        for entity in entities:
            if self._literal_alias_present(entity, text):
                continue
            if any(entity in alias and self._literal_alias_present(alias, text) for alias in aliases or set()):
                continue
            return False
        return True

    @staticmethod
    def _named_entity_tokens(feature: str) -> set[str]:
        return {
            token.lower()
            for token in re.findall(r"[A-Za-z][A-Za-z0-9_.-]{2,}", feature)
            if any(character.isupper() or character.isdigit() for character in token)
        }

    @staticmethod
    def _readme_capability_text(text: str) -> str:
        """Remove presentation-only Markdown that must not prove product capabilities."""
        kept: list[str] = []
        for line in str(text or "").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("#"):
                continue
            if re.search(r"!\[[^\]]*\]\([^)]*\)", stripped):
                continue
            if re.search(r"<\s*(?:img|picture|source)\b", stripped, flags=re.IGNORECASE):
                continue
            kept.append(stripped)
        return "\n".join(kept)

    @staticmethod
    def _path_can_prove_capability(path: str) -> bool:
        lowered = str(path or "").replace("\\", "/").lower()
        if lowered.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico")):
            return False
        return True

    def _explicit_missing_reason(self, text: str, aliases: set[str]) -> str:
        """Only classify a feature as missing when the project says so explicitly."""
        return ""

    def _rerank_by_evidence(
        self,
        repos: list[CandidateRepository],
        requirement: Requirement,
    ) -> list[CandidateRepository]:
        for repo in repos:
            if not repo.evidence_coverage:
                repo.evidence_coverage = self._build_evidence_coverage(repo, requirement)
            repo.evidence_score = self._evidence_score(repo.evidence_coverage, requirement)
            repo.raw_score = repo.raw_score * 0.65 + repo.evidence_score * 35
        return sorted(repos, key=lambda item: item.raw_score, reverse=True)

    @staticmethod
    def _evidence_strength(item: EvidenceCoverage) -> float:
        """Continuous weight by evidence source so source > path > readme > claim only."""
        if item.status == "supported" or item.covered:
            if item.source_evidence:
                return 1.0
            if item.path_evidence:
                return 0.95
            if item.readme_evidence:
                return 0.90
            return 0.70
        if item.status == "different":
            return 0.45
        return 0.0

    def _evidence_score(
        self,
        coverage: list[EvidenceCoverage],
        requirement: Requirement | None = None,
    ) -> float:
        if not coverage:
            return 0.0
        core_feature = self._core_requirement_feature(requirement) if requirement else None
        weights = [4.0 if item.feature == core_feature else 1.0 for item in coverage]
        total_weight = sum(weights)
        covered = sum(
            weight
            for item, weight in zip(coverage, weights)
            if item.status == "supported" or item.covered
        )
        strong = sum(weight for item, weight in zip(coverage, weights) if item.source_evidence)
        medium = sum(
            weight
            for item, weight in zip(coverage, weights)
            if item.path_evidence and not item.source_evidence
        )
        weak = sum(
            weight
            for item, weight in zip(coverage, weights)
            if item.readme_evidence and not item.source_evidence and not item.path_evidence
        )
        return min(
            1.0,
            (covered / total_weight) * 0.7
            + (strong / total_weight) * 0.2
            + ((medium + weak) / total_weight) * 0.1,
        )

    def _core_requirement_feature(self, requirement: Requirement | None) -> str | None:
        if not requirement or not requirement.must_have_features:
            return None
        candidate_features = requirement.must_have_features
        concepts = requirement.feature_concepts or {}
        context = " ".join(
            [
                requirement.intent,
                *[str(item) for item in concepts.get("domains", [])],
                *[str(item) for item in concepts.get("actions", [])],
                *[str(item) for item in concepts.get("objects", [])],
            ]
        )
        context_signals = self._semantic_signals(context)
        context_signals.update(
            token[:-1] if token.endswith("s") and len(token) > 4 else token
            for token in list(context_signals)
        )
        if not context_signals:
            return candidate_features[0]
        domain_aliases = self._clean_feature_aliases(
            {
                str(item)
                for group in ("domains", "interfaces")
                for item in concepts.get(group, [])
            }
        )
        object_aliases = self._clean_feature_aliases({str(item) for item in concepts.get("objects", [])})
        action_aliases = self._clean_feature_aliases({str(item) for item in concepts.get("actions", [])})
        output_aliases = self._clean_feature_aliases({str(item) for item in concepts.get("outputs", [])})
        scored_features: list[tuple[int, int, int, int, int, int, int, str]] = []
        for index, feature in enumerate(candidate_features):
            feature_signals = self._semantic_signals(feature)
            feature_signals.update(
                token[:-1] if token.endswith("s") and len(token) > 4 else token
                for token in list(feature_signals)
            )
            overlap = len(feature_signals.intersection(context_signals))
            domain_hits = len(self._matching_terms(feature, domain_aliases)) if domain_aliases else 0
            object_hits = len(self._matching_terms(feature, object_aliases)) if object_aliases else 0
            action_hits = len(self._matching_terms(feature, action_aliases)) if action_aliases else 0
            output_hits = len(self._matching_terms(feature, output_aliases)) if output_aliases else 0
            primary_hits = domain_hits + object_hits
            output_only_penalty = 1 if output_hits and not primary_hits else 0
            scored_features.append(
                (
                    domain_hits,
                    object_hits,
                    action_hits,
                    overlap,
                    -output_only_penalty,
                    -index,
                    len(feature_signals),
                    feature,
                )
            )
        best = max(scored_features)
        return best[7] if best[0] > 0 or best[1] > 0 or best[3] > 0 else candidate_features[0]

    def _evidence_gate_features(self, requirement: Requirement) -> list[str]:
        features: list[str] = []
        for feature in requirement.must_have_features:
            normalized = re.sub(r"\s+", " ", feature.strip().lower())
            if not normalized:
                continue
            if normalized in {item.lower() for item in features}:
                continue
            features.append(feature)
        return features

    def _feature_aliases(self, feature: str, requirement: Requirement | None = None) -> set[str]:
        aliases = {feature}
        if requirement:
            aliases.update(self._aliases_from_requirement(feature, requirement))
        return self._clean_feature_aliases(aliases)

    def _aliases_from_requirement(self, feature: str, requirement: Requirement) -> set[str]:
        aliases: set[str] = set()
        for key, values in (requirement.evidence_aliases or {}).items():
            if self._same_feature_key(feature, key):
                aliases.update(values)
        return aliases

    def _same_feature_key(self, left: str, right: str) -> bool:
        left_norm = re.sub(r"\s+", " ", str(left).strip().lower())
        right_norm = re.sub(r"\s+", " ", str(right).strip().lower())
        return bool(left_norm and right_norm and left_norm == right_norm)

    def _clean_feature_aliases(self, aliases: set[str]) -> set[str]:
        cleaned: set[str] = set()
        for alias in aliases:
            text = str(alias).strip().lower()
            if not text:
                continue
            if len(text) < 2:
                continue
            cleaned.add(text)
        return cleaned

    def _matching_terms(self, text: str, aliases: set[str]) -> list[str]:
        lowered = (text or "").lower()
        exact = {alias for alias in aliases if self._literal_alias_present(alias, lowered)}
        semantic = {
            alias
            for alias in aliases
            if alias not in exact and self._semantic_alias_match(alias, lowered)
        }
        return sorted(exact | semantic, key=lambda item: (len(item), item), reverse=True)

    def _matching_feature_terms(self, feature: str, text: str, aliases: set[str]) -> list[str]:
        hits = self._matching_terms(text, aliases)
        if not hits:
            return []
        feature_signals = self._semantic_signals(feature)
        compact_feature = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", str(feature or "").lower())
        simple_feature = len(compact_feature) <= 8
        lowered_text = (text or "").lower()
        trusted_exact_hits = [
            alias
            for alias in hits
            if self._literal_alias_present(alias, lowered_text)
            and self._semantic_signals(alias)
            and not self._semantic_signals(alias).issubset(self._generic_search_signals())
        ]
        if trusted_exact_hits and self._different_script(feature, trusted_exact_hits[0]):
            named_entities = self._named_entity_tokens(feature)
            combined_exact_signals = {
                signal
                for alias in trusted_exact_hits
                for signal in self._semantic_signals(alias)
            }
            has_broad_combined_signal = len(combined_exact_signals) >= 3
            if (named_entities and len(combined_exact_signals) >= 2 and named_entities.issubset(combined_exact_signals)) or (
                len(trusted_exact_hits) >= 2 and has_broad_combined_signal
            ) or (
                simple_feature and has_broad_combined_signal
            ):
                return trusted_exact_hits
        evidence_signals = self._semantic_signals(text)
        named_entities = self._named_entity_tokens(feature)
        if len(feature_signals) <= 2:
            if any(self._same_feature_key(feature, alias) for alias in hits):
                return hits
            exact_alias_signals = {
                signal
                for alias in hits
                if self._literal_alias_present(alias, lowered_text)
                for signal in self._semantic_signals(alias)
            }
            if (
                named_entities
                and len(exact_alias_signals | evidence_signals) >= 2
                and named_entities.issubset(exact_alias_signals | evidence_signals)
            ):
                return hits
            if any(
                self._literal_alias_present(alias, lowered_text)
                and self._semantic_signals(alias)
                and not self._semantic_signals(alias).issubset(feature_signals)
                for alias in hits
            ):
                return hits
            combined_hit_signals = {
                signal
                for alias in hits
                for signal in self._semantic_signals(alias)
            }
            if (
                hits
                and any(self._different_script(feature, alias) for alias in hits)
                and simple_feature
                and len(combined_hit_signals) >= 2
            ):
                return hits
            if feature_signals and feature_signals.issubset(evidence_signals):
                return hits
            return []
        overlap = feature_signals.intersection(evidence_signals)
        required_overlap = min(3, max(2, len(feature_signals) // 3))
        combined_alias_signals = {
            signal
            for alias in hits
            for signal in self._semantic_signals(alias)
        }
        if (
            len(named_entities) >= 2
            and len(combined_alias_signals | evidence_signals) >= 2
            and named_entities.issubset(combined_alias_signals | evidence_signals)
        ):
            return hits
        if len(hits) >= 2 and len(combined_alias_signals) >= 2 and len(overlap) >= 2:
            return hits
        if hits and any(self._different_script(feature, alias) for alias in hits):
            named_entities = self._named_entity_tokens(feature)
            if (
                named_entities
                and len(combined_alias_signals) >= 2
                and named_entities.issubset(combined_alias_signals)
            ) or (
                len(hits) >= 2 and len(combined_alias_signals) >= 3
            ) or (
                simple_feature and len(combined_alias_signals) >= 2
            ):
                return hits
        supported: list[str] = []
        for alias in hits:
            alias_signals = self._semantic_signals(alias)
            exact_hit = self._literal_alias_present(alias, lowered_text)
            if self._different_script(feature, alias):
                named_entities = self._named_entity_tokens(feature)
                if not (
                    named_entities
                    and len(alias_signals) >= 2
                    and named_entities.issubset(alias_signals)
                ):
                    continue
            if exact_hit and (self._same_feature_key(feature, alias) or len(alias_signals) >= 2):
                supported.append(alias)
            elif exact_hit and alias_signals and not alias_signals.issubset(feature_signals):
                supported.append(alias)
            elif len(overlap) >= required_overlap:
                supported.append(alias)
        return supported

    @staticmethod
    def _different_script(left: str, right: str) -> bool:
        return bool(re.search(r"[^\x00-\x7f]", left or "")) != bool(re.search(r"[^\x00-\x7f]", right or ""))

    @staticmethod
    def _literal_alias_present(alias: str, lowered_text: str) -> bool:
        if re.fullmatch(r"[a-z0-9_.-]+", alias) and len(alias) <= 4:
            plural = "s?" if not alias.endswith("s") else ""
            return bool(re.search(rf"(?<![a-z0-9]){re.escape(alias)}{plural}(?![a-z0-9])", lowered_text))
        return alias in lowered_text

    def _contains_alias(self, text: str, aliases: set[str]) -> bool:
        lowered = text.lower()
        return any(
            self._literal_alias_present(alias, lowered) or self._semantic_alias_match(alias, lowered)
            for alias in aliases
        )

    def _semantic_alias_match(self, alias: str, text: str) -> bool:
        alias_signals = self._semantic_signals(alias)
        if len(alias_signals) < 2:
            return False
        # Evidence must occur in one local statement. Comparing a feature with an
        # entire large README lets unrelated words from distant sections add up
        # into a false match.
        for chunk in re.split(r"[\r\n。！？!?；;]+", text):
            chunk = chunk.strip()
            if not chunk:
                continue
            windows = [chunk]
            if len(chunk) > 280:
                windows = [chunk[start : start + 220] for start in range(0, len(chunk), 160)]
            for window in windows:
                text_signals = self._semantic_signals(window)
                text_signals.update(token[:-1] for token in list(text_signals) if token.endswith("s") and len(token) > 4)
                if not text_signals:
                    continue
                overlap = alias_signals & text_signals
                coverage = len(overlap) / max(1, len(alias_signals))
                if (
                    len(overlap) >= 2
                    and (coverage >= 0.45 or (len(overlap) >= 4 and coverage >= 0.3))
                ) or (len(overlap) >= 1 and len(alias_signals) <= 2 and coverage >= 0.5):
                    return True
        return False

    @staticmethod
    def _evidence_score_cap(coverage: list[EvidenceCoverage]) -> int:
        """Keep wholly unconfirmed projects cautious without treating unknown as absent."""
        if not coverage:
            return 100
        supported = sum(1 for item in coverage if item.status == "supported" or item.covered)
        explicit = sum(1 for item in coverage if item.status in {"different", "missing"})
        return 74 if supported == 0 and explicit == 0 else 100

    @staticmethod
    def _score_reason(analysis: ProjectAnalysis) -> str:
        coverage = analysis.evidence_coverage
        if not coverage:
            return "项目公开内容较少，目前只能作为弱线索。"

        def brief(items: list[str], limit: int = 3) -> str:
            shown = "、".join(items[:limit])
            return f"{shown}等 {len(items)} 项" if len(items) > limit else shown

        parts: list[str] = []
        if analysis.covered_features and analysis.confidence_level != "lead":
            prefix = "仅确认" if len(analysis.covered_features) <= 2 else "已确认"
            parts.append(f"{prefix}{brief(analysis.covered_features)}")
        elif analysis.core_feature and not analysis.core_confirmed:
            parts.append("公开证据只支持较弱相邻关系")
        else:
            parts.append("项目公开内容较少")
        if analysis.missing_features:
            parts.append(f"明确缺少{brief(analysis.missing_features)}")
        if analysis.different_features:
            parts.append("项目定位或使用方式也有差异")
        return "；".join(parts) + "。"

    def _semantic_signals(self, text: str) -> set[str]:
        lowered = (text or "").lower()
        signals = set(re.findall(r"[a-z][a-z0-9_.-]{2,}", lowered))
        for run in re.findall(r"[\u4e00-\u9fff]{2,}", lowered):
            if len(run) <= 4:
                signals.add(run)
            signals.update(run[index : index + 2] for index in range(len(run) - 1))
            signals.update(run[index : index + 3] for index in range(len(run) - 2))
        return {item for item in signals if len(item) >= 2}

    def _requirement_aliases(self, requirement: Requirement) -> set[str]:
        seed = " ".join(
            [
                requirement.raw,
                requirement.intent,
                " ".join(requirement.must_have_features),
                " ".join(requirement.nice_to_have_features),
                " ".join(requirement.target_platforms),
            ]
        ).lower()
        aliases = set(keyword_bag(seed))
        for key, values in (requirement.evidence_aliases or {}).items():
            key_text = str(key).strip().lower()
            if key_text:
                aliases.add(key_text)
            for value in values:
                text = str(value).strip().lower()
                if text:
                    aliases.add(text)
        for values in (requirement.feature_concepts or {}).values():
            for value in values:
                text = str(value).strip().lower()
                if text:
                    aliases.add(text)
        return aliases

    async def _analyze_top_projects(
        self,
        requirement: Requirement,
        repos: list[CandidateRepository],
        llm: LLMClient | None,
    ) -> list[ProjectAnalysis]:
        if llm and repos:
            payload = {
                "requirement": {
                    "raw": requirement.raw,
                    "must_have_features": requirement.must_have_features,
                    "target_platforms": requirement.target_platforms,
                    "feature_concepts": requirement.feature_concepts,
                },
                "repositories": [
                    {
                        "full_name": repo.full_name,
                        "url": repo.url,
                        "description": repo.description,
                        "language": repo.language,
                        "topics": repo.topics,
                        "source_evidence": repo.source_evidence,
                        "evidence_coverage": [
                            {
                                "feature": item.feature,
                                "covered": item.covered,
                                "readme_evidence": item.readme_evidence,
                                "source_evidence": item.source_evidence,
                                "path_evidence": item.path_evidence,
                                "missing_reason": item.missing_reason,
                            }
                            for item in repo.evidence_coverage
                        ],
                        "sampled_file_paths": repo.file_paths[:80],
                        "key_file_excerpts": {
                            path: compact_text(text, 2500) for path, text in repo.key_files.items() if text
                        },
                        "readme_excerpt": compact_text(repo.readme, 9000),
                    }
                    for repo in repos
                ],
            }
            data = await llm.json_chat(
                (
                    "You are a technical repository research analyst. Return JSON only. "
                    "Use the same language as the user's requirement for every natural-language field. "
                    "If the requirement is Chinese, all recommendations, risks, changes, and evidence summaries must be Chinese."
                ),
                (
                    "For each repository, compare it to the requirement. Return JSON: "
                    '{"projects":[{"repo":"owner/name","match_score":0-100,"recommendation":"...",'
                    '"directly_usable":true/false,"covered_features":[],"different_features":[],'
                    '"missing_features":[],"unknown_features":[],'
                    '"required_changes":[],"risks":[],"evidence":[]}]}.\n'
                    "Use only the supplied repository material and evidence coverage. "
                    "If the supplied material does not prove a requested capability, keep it unknown.\n"
                    "Do not use popularity metadata, stars, license, or language as the main recommendation. Focus on "
                    "functional fit, evidence coverage, missing capabilities, and the next verification step.\n"
                    "Write all user-facing fields for a reader with no software-development experience. Avoid "
                    "acronyms, architecture labels, implementation jargon, raw program errors, and internal research "
                    "process. Do not repeat missing_features inside recommendation or required_changes.\n"
                    "Score by semantic feature coverage, not by exact keyword overlap. Compare domain, action, object, "
                    "output, and interface concepts. For each covered feature, evidence should cite README/source/path "
                    "signals from the payload. Penalize projects that match the domain but miss the user's main action/object/output.\n"
                    "A clear statement in the project's own README is valid support. Source or file-path evidence "
                    "increases confidence but is not required for a high functional match. Use missing_features only "
                    "when the supplied project material explicitly says a capability is unavailable. Put capabilities "
                    "that were not checked or not mentioned in unknown_features, never in missing_features. Put a "
                    "different scope, workflow, or license constraint in different_features. Every difference must "
                    "contrast a requested capability with the project's actual alternative; do not list unrelated "
                    "extra capabilities as differences.\n"
                    f"{json.dumps(payload, ensure_ascii=False)}"
                ),
            )
            if data and isinstance(data.get("projects"), list):
                by_name = {repo.full_name.lower(): repo for repo in repos}
                analyses: list[ProjectAnalysis] = []
                for item in data["projects"]:
                    repo = by_name.get(str(item.get("repo", "")).lower())
                    if not repo:
                        continue
                    evidence = [str(x) for x in item.get("evidence", [])]
                    for source_item in repo.source_evidence:
                        if source_item not in evidence:
                            evidence.append(source_item)
                    analyses.append(
                        ProjectAnalysis(
                            repo=repo,
                            match_score=int(item.get("match_score") or min(100, repo.raw_score)),
                            recommendation=str(item.get("recommendation") or "需要人工复核"),
                            directly_usable=bool(item.get("directly_usable")),
                            covered_features=[str(x) for x in item.get("covered_features", [])][:8],
                            different_features=[str(x) for x in item.get("different_features", [])][:8],
                            unknown_features=[str(x) for x in item.get("unknown_features", [])][:8],
                            missing_features=[str(x) for x in item.get("missing_features", [])][:8],
                            required_changes=[str(x) for x in item.get("required_changes", [])][:8],
                            risks=[str(x) for x in item.get("risks", [])][:8],
                            evidence=evidence[:6],
                            evidence_coverage=repo.evidence_coverage,
                        )
                    )
                if analyses:
                    analyzed_names = {item.repo.full_name.lower() for item in analyses}
                    analyses.extend(
                        self._heuristic_analysis(requirement, repo)
                        for repo in repos
                        if repo.full_name.lower() not in analyzed_names
                    )
                    return sorted(analyses, key=lambda item: item.match_score, reverse=True)
        return [self._heuristic_analysis(requirement, repo) for repo in repos]

    def _heuristic_analysis(self, requirement: Requirement, repo: CandidateRepository) -> ProjectAnalysis:
        features = requirement.must_have_features or list(keyword_bag(requirement.raw))[:6]
        text = " ".join([repo.name, repo.description, " ".join(repo.topics), repo.readme[:12000]]).lower()
        covered = [feature for feature in features if any(part.lower() in text for part in keyword_bag(feature))]
        missing = [feature for feature in features if feature not in covered]
        score = int(max(0, min(100, repo.raw_score)))
        recommendation = "可直接评估使用" if score >= 80 and len(missing) <= 1 else "适合作为参考或二次开发"
        if score < 45:
            recommendation = "相关度偏低，仅建议参考"
        risks = []
        if not repo.readme:
            risks.append("README 未获取成功，证据不足")
        if repo.last_pushed_at and repo.last_pushed_at[:4] < "2023":
            risks.append("最近维护时间较早")
        if not repo.license:
            risks.append("许可证信息不明确")
        evidence = []
        if repo.description:
            evidence.append(f"描述：{repo.description}")
        if repo.topics:
            evidence.append(f"Topics：{', '.join(repo.topics[:8])}")
        evidence.extend(repo.source_evidence[:4])
        return ProjectAnalysis(
            repo=repo,
            match_score=score,
            recommendation=recommendation,
            directly_usable=score >= 85 and len(missing) == 0,
            covered_features=covered[:8],
            missing_features=missing[:8],
            required_changes=self._fallback_changes(missing),
            risks=risks,
            evidence=evidence,
            evidence_coverage=repo.evidence_coverage,
        )

    def _apply_evidence_gate(
        self,
        requirement: Requirement,
        analyses: list[ProjectAnalysis],
        usage: BudgetUsage,
    ) -> tuple[list[ProjectAnalysis], dict[str, int]]:
        gated_features = self._evidence_gate_features(requirement)
        stats = {
            "gated_feature_count": len(gated_features),
            "penalized_count": 0,
            "score_capped_count": 0,
            "fully_covered_count": 0,
            "unknown_feature_count": 0,
            "explicit_missing_count": 0,
            "core_requirement_unconfirmed_count": 0,
            "repeated_analysis_text_count": 0,
            "ungated_generic_must_have_count": len(requirement.must_have_features) - len(gated_features),
        }
        if not gated_features:
            return analyses, stats
        gated: list[ProjectAnalysis] = []
        penalized: list[str] = []
        for analysis in analyses:
            coverage = analysis.evidence_coverage or analysis.repo.evidence_coverage
            if not coverage:
                coverage = self._build_evidence_coverage(analysis.repo, requirement)
            analysis.evidence_coverage = coverage
            reported_differences = list(analysis.different_features)
            for item in coverage:
                if item.covered and item.status == "unknown":
                    item.status = "supported"
                if self._feature_has_confirmed_absence(item.feature, reported_differences):
                    item.status = "missing"
                    item.covered = False
                    item.missing_reason = "核对结果明确说明该能力不提供"
                elif item.status == "supported" and self._feature_has_confirmed_difference(
                    item.feature, reported_differences
                ):
                    item.status = "different"
                    item.covered = False
            if self._reported_difference_overlaps_primary(requirement, reported_differences):
                supported_count = sum(1 for item in coverage if item.status == "supported" or item.covered)
                if supported_count < len(coverage):
                    for item in coverage:
                        if item.status == "supported" or item.covered:
                            item.status = "different"
                            item.covered = False
                            if not item.difference_reason:
                                item.difference_reason = "reported scope difference overlaps the requested capability"
            evidence_covered = [item.feature for item in coverage if item.status == "supported"]
            explicit_missing = [item.feature for item in coverage if item.status == "missing"]
            unknown = [item.feature for item in coverage if item.status == "unknown"]
            evidence_differences = [item.feature for item in coverage if item.status == "different"]
            constraint_differences, constraint_unknown = self._constraint_findings(requirement, analysis.repo)
            analysis.covered_features = evidence_covered[:8]
            remaining_differences = [
                difference
                for difference in reported_differences
                if not any(
                    self._feature_has_confirmed_absence(feature, [difference])
                    for feature in explicit_missing
                )
            ]
            analysis.different_features = list(
                dict.fromkeys([*remaining_differences, *evidence_differences, *constraint_differences])
            )[:8]
            analysis.unknown_features = list(dict.fromkeys([*unknown, *constraint_unknown]))[:8]
            # A model guess is never enough to call a feature absent. Only an
            # explicit statement in the project's own material enters this list.
            analysis.missing_features = explicit_missing[:8]
            stats["unknown_feature_count"] += len(unknown)
            stats["explicit_missing_count"] += len(explicit_missing)

            core_feature = self._core_requirement_feature(requirement)
            weights = [4.0 if item.feature == core_feature else 1.0 for item in coverage]
            total_weight = max(1.0, sum(weights))
            evidence_fit = round(
                100
                * sum(
                    weight * self._evidence_strength(item)
                    for item, weight in zip(coverage, weights)
                )
                / total_weight
            )
            original_score = max(0, min(100, int(analysis.match_score)))
            functional_score = round(original_score * 0.25 + evidence_fit * 0.75)
            score_cap = self._evidence_score_cap(coverage)
            if functional_score > score_cap:
                functional_score = score_cap
                stats["score_capped_count"] += 1
            core_coverage = next(
                (item for item in coverage if item.feature == core_feature),
                None,
            )
            core_supported = bool(
                core_coverage
                and (core_coverage.status == "supported" or core_coverage.covered)
            )
            confirmed_weight = sum(
                weight * self._evidence_strength(item)
                for item, weight in zip(coverage, weights)
            )
            confirmed_cap = round(20 + 80 * confirmed_weight / total_weight)
            if functional_score > confirmed_cap:
                functional_score = confirmed_cap
                stats["score_capped_count"] += 1
            is_catalog = self._is_catalog_repository(analysis.repo)
            analysis.core_feature = core_feature or ""
            analysis.core_confirmed = core_supported
            analysis.is_catalog = is_catalog
            if is_catalog:
                functional_score = 0
            elif core_feature and not core_supported:
                # Keep relative ordering for adjacent projects, but make sure
                # peripheral features can never promote them to reliable results.
                # Projects that still carry a domain signal are penalized less
                # harshly than completely generic projects.
                has_domain_signal = analysis.repo.core_signal_score >= 2.0
                penalty_factor = 0.75 if has_domain_signal else 0.60
                functional_score = min(49, max(1, round(functional_score * penalty_factor)))
                supported_non_core = sum(
                    1
                    for item in coverage
                    if item.feature != core_feature and (item.status == "supported" or item.covered)
                )
                if supported_non_core <= 1 and analysis.repo.core_signal_score <= 0:
                    functional_score = min(functional_score, 19)
                stats["core_requirement_unconfirmed_count"] += 1
            analysis.functional_score = functional_score
            analysis.match_score = functional_score

            suitability_penalty = (
                len(explicit_missing) * 10
                + len(analysis.different_features) * 5
                + len(constraint_differences) * 10
            )
            analysis.suitability_score = max(0, functional_score - suitability_penalty)
            analysis.score_reason = self._score_reason(analysis)
            analysis.evidence = self._coverage_evidence_summary(analysis)
            if unknown or analysis.different_features:
                analysis.directly_usable = False

            if explicit_missing:
                stats["penalized_count"] += 1
                analysis.directly_usable = False
                missing_note = "、".join(explicit_missing[:5])
                risk = f"项目明确不包含：{missing_note}"
                if risk not in analysis.risks:
                    analysis.risks.append(risk)
                change = f"如必须具备「{missing_note}」，需要补充开发"
                if change not in analysis.required_changes:
                    analysis.required_changes.append(change)
                penalized.append(analysis.repo.full_name)
            if not unknown and not explicit_missing and not evidence_differences:
                stats["fully_covered_count"] += 1
            gated.append(analysis)
        stats["repeated_analysis_text_count"] = self._degrade_repeated_analysis_text(gated, usage)
        if penalized:
            usage.warnings.append("Candidates with explicitly absent requirements: " + ", ".join(penalized[:5]))
        return sorted(gated, key=lambda item: item.match_score, reverse=True), stats

    def _coverage_evidence_summary(self, analysis: ProjectAnalysis) -> list[str]:
        """Expose only evidence derived from repository material, not model prose."""
        summary: list[str] = []
        for item in analysis.evidence_coverage:
            if item.status == "supported" or item.covered:
                sources = [*item.source_evidence[:2], *item.path_evidence[:2], *item.readme_evidence[:2]]
                if sources:
                    summary.append(f"{item.feature}: {'; '.join(sources[:3])}")
                else:
                    summary.append(f"{item.feature}: public repository material supports this item")
            elif (
                item.status == "missing"
                and item.missing_reason
                and not item.missing_reason.startswith("核对结果")
            ):
                summary.append(f"{item.feature}: {item.missing_reason}")
            elif item.status == "different" and item.difference_reason:
                summary.append(f"{item.feature}: {item.difference_reason}")
            if len(summary) >= 6:
                break
        return summary

    def _degrade_repeated_analysis_text(
        self,
        analyses: list[ProjectAnalysis],
        usage: BudgetUsage,
    ) -> int:
        grouped: dict[tuple[str, str], list[ProjectAnalysis]] = {}
        for analysis in analyses:
            if analysis.core_confirmed and analysis.match_score >= 50:
                continue
            reason_key = re.sub(r"\s+", "", analysis.score_reason.casefold())
            covered_key = "|".join(re.sub(r"\s+", "", item.casefold()) for item in analysis.covered_features)
            if not reason_key and not covered_key:
                continue
            grouped.setdefault((reason_key, covered_key), []).append(analysis)

        repeated = 0
        affected: list[str] = []
        for items in grouped.values():
            if len(items) < 2:
                continue
            for analysis in items:
                signal = self._public_candidate_signal(analysis.repo)
                if signal:
                    analysis.score_reason = (
                        f"{analysis.score_reason.rstrip('。') if analysis.score_reason else '公开证据较弱'}；"
                        f"该项目可核对线索：{signal}。"
                    )
                else:
                    analysis.score_reason = "公开证据不足以形成独立匹配理由，仅能作为低可信线索。"
                    analysis.match_score = min(analysis.match_score, 19)
                    analysis.functional_score = min(analysis.functional_score or analysis.match_score, analysis.match_score)
                    analysis.suitability_score = min(analysis.suitability_score or analysis.match_score, analysis.match_score)
                analysis.directly_usable = False
                repeated += 1
                affected.append(analysis.repo.full_name)
        if affected:
            usage.warnings.append(
                "Repeated low-confidence analysis text was made project-specific or downgraded: "
                + ", ".join(affected[:5])
            )
        return repeated

    @staticmethod
    def _public_candidate_signal(repo: CandidateRepository) -> str:
        if repo.description:
            return compact_text(repo.description, 120)
        if repo.topics:
            return "topics: " + ", ".join(repo.topics[:5])
        if repo.found_by:
            return ", ".join(repo.found_by[:2])
        return repo.full_name

    def _feature_has_confirmed_difference(self, feature: str, differences: list[str]) -> bool:
        """Prevent one capability from appearing as both confirmed and different."""
        feature_text = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", feature.lower())
        if not feature_text:
            return False
        feature_signals = self._semantic_signals(feature)
        for difference in differences:
            difference_text = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", difference.lower())
            if not difference_text:
                continue
            match_size = SequenceMatcher(None, feature_text, difference_text).find_longest_match().size
            if match_size >= 3 and match_size / max(3, min(len(feature_text), 8)) >= 0.45:
                return True
            difference_signals = self._semantic_signals(difference)
            if feature_signals and difference_signals:
                overlap = feature_signals.intersection(difference_signals)
                required_overlap = min(4, max(2, len(feature_signals) // 5))
                if len(overlap) >= required_overlap:
                    return True
        return False

    def _reported_difference_overlaps_primary(
        self,
        requirement: Requirement,
        differences: list[str],
    ) -> bool:
        if not differences:
            return False
        concepts = requirement.feature_concepts or {}
        primary_text = " ".join(
            [
                requirement.intent,
                *requirement.must_have_features,
                *[
                    str(item)
                    for group in ("domains", "actions", "objects", "interfaces")
                    for item in concepts.get(group, [])
                ],
            ]
        )
        primary_signals = self._semantic_signals(primary_text)
        if not primary_signals:
            return False
        difference_signals = self._semantic_signals(" ".join(differences))
        return len(primary_signals.intersection(difference_signals)) >= 2

    @classmethod
    def _feature_has_confirmed_absence(cls, feature: str, differences: list[str]) -> bool:
        return False

    def _constraint_findings(
        self, requirement: Requirement, repo: CandidateRepository
    ) -> tuple[list[str], list[str]]:
        return [], []

    def _with_reference_candidates(
        self,
        reliable: list[ProjectAnalysis],
        low_confidence: list[ProjectAnalysis],
        usage: BudgetUsage,
        requirement: Requirement | None = None,
    ) -> list[ProjectAnalysis]:
        max_results = self.settings.max_deep_analyze_repos
        selected: list[ProjectAnalysis] = []
        used_families: set[str] = set()
        for item in sorted(reliable, key=lambda candidate: candidate.match_score, reverse=True):
            family = self._project_family_key(item)
            if family in used_families or self._is_catalog_candidate(item):
                continue
            selected.append(item)
            used_families.add(family)
            if len(selected) >= max_results:
                break
        slots = max(0, max_results - len(selected))
        remaining = [item for item in low_confidence if not self._is_catalog_candidate(item)]

        # If a selected reliable candidate fails core confirmation, allow a
        # core-confirmed reference candidate from the low-confidence pool to
        # displace the weakest unconfirmed reliable result, as long as the
        # replacement scores higher and does not duplicate a family already used.
        if selected and remaining:
            unconfirmed_selected = [
                item for item in selected if item.core_feature and not item.core_confirmed
            ]
            core_confirmed_alternatives = [
                item
                for item in remaining
                if item.core_confirmed
                and item.match_score >= 35
                and self._is_usable_reference_candidate(item, requirement)
            ]
            if unconfirmed_selected and core_confirmed_alternatives:
                core_confirmed_alternatives = sorted(
                    core_confirmed_alternatives, key=lambda candidate: candidate.match_score, reverse=True
                )
                selected_by_family = {self._project_family_key(item): item for item in selected}
                for target in sorted(unconfirmed_selected, key=lambda candidate: candidate.match_score):
                    if not core_confirmed_alternatives:
                        break
                    replacement = core_confirmed_alternatives[0]
                    replacement_family = self._project_family_key(replacement)
                    if replacement_family in selected_by_family and selected_by_family[replacement_family] is not target:
                        continue
                    if replacement.match_score > target.match_score:
                        selected_by_family.pop(self._project_family_key(target), None)
                        selected_by_family[replacement_family] = replacement
                        self._mark_reference_candidate(replacement)
                        selected.remove(target)
                        selected.append(replacement)
                        core_confirmed_alternatives.pop(0)
                        remaining.remove(replacement)

        if slots <= 0 or not remaining:
            return selected
        minimum_reference_score = 35 if selected else 20
        eligible_references = [
            item
            for item in remaining
            if self._is_evidence_backed_reference_candidate(
                item,
                minimum_score=minimum_reference_score,
                require_core_confirmed=bool(selected),
            )
            and self._is_usable_reference_candidate(item, requirement)
        ]
        references: list[ProjectAnalysis] = []
        for item in sorted(eligible_references, key=lambda candidate: candidate.match_score, reverse=True):
            family = self._project_family_key(item)
            if family in used_families:
                continue
            references.append(item)
            used_families.add(family)
            if len(references) >= slots:
                break
        for item in references:
            self._mark_reference_candidate(item)
        selected.extend(references)
        slots = max(0, max_results - len(selected))

        reference_names = {item.repo.full_name for item in references}
        adjacent_pool = [
            item
            for item in remaining
            if not selected
            and item.repo.full_name not in reference_names
            and (item.match_score >= 15 or self._has_multi_feature_core_evidence(item, requirement))
            and self._has_meaningful_adjacent_value(item, requirement)
            and (self._is_usable_reference_candidate(item, requirement) or item.repo.core_signal_score >= 2.0)
        ]
        adjacent: list[ProjectAnalysis] = []
        for item in sorted(
            adjacent_pool,
            key=lambda candidate: (candidate.repo.core_signal_score, candidate.match_score),
            reverse=True,
        ):
            family = self._project_family_key(item)
            if family in used_families:
                continue
            adjacent.append(item)
            used_families.add(family)
            if len(adjacent) >= slots:
                break
        for item in adjacent:
            self._ensure_positive_lead_score(item, requirement)
            self._mark_low_similarity_lead(item)
        selected.extend(adjacent)

        rejected_count = len(low_confidence) - len(references) - len(adjacent)
        if rejected_count:
            usage.warnings.append(
                f"Excluded {rejected_count} low-confidence candidate(s) with no meaningful project evidence."
            )
        if references:
            usage.warnings.append(
                "Added evidence-backed reference candidates because reliable Top projects were insufficient: "
                + ", ".join(item.repo.full_name for item in references)
            )
        if adjacent:
            usage.warnings.append(
                "Added relatively closest adjacent projects because stronger matches were insufficient: "
                + ", ".join(item.repo.full_name for item in adjacent)
            )
        return selected

    @staticmethod
    def _is_evidence_backed_reference_candidate(
        analysis: ProjectAnalysis,
        minimum_score: int,
        require_core_confirmed: bool,
    ) -> bool:
        if require_core_confirmed:
            return analysis.core_confirmed and analysis.match_score >= 20
        return analysis.match_score >= minimum_score and (analysis.core_confirmed or not analysis.core_feature)

    @staticmethod
    def _supported_evidence_count(coverage: Sequence[EvidenceCoverage]) -> int:
        return sum(1 for item in coverage if item.status == "supported" or item.covered)

    def _has_multi_feature_core_evidence(
        self,
        analysis: ProjectAnalysis,
        requirement: Requirement | None,
    ) -> bool:
        core_feature = self._core_requirement_feature(requirement) if requirement else analysis.core_feature
        if not core_feature:
            return False
        has_core = any(
            item.feature == core_feature and (item.status == "supported" or item.covered)
            for item in analysis.evidence_coverage
        )
        return has_core and self._supported_evidence_count(analysis.evidence_coverage) >= 2

    def _fallback_low_similarity_leads(
        self,
        requirement: Requirement,
        repos: list[CandidateRepository],
        usage: BudgetUsage,
        slots: int | None = None,
        excluded_projects: set[str] | None = None,
        excluded_families: set[str] | None = None,
    ) -> list[ProjectAnalysis]:
        leads: list[ProjectAnalysis] = []
        target = slots if slots is not None else self.settings.max_deep_analyze_repos
        excluded_projects = set(excluded_projects or set())
        used_families = set(excluded_families or set())
        for repo in repos:
            if len(leads) >= target:
                break
            if repo.core_signal_score <= 0:
                repo.core_signal_score = self._core_direction_score(requirement, repo)
            family = re.sub(r"[^a-z0-9]+", "", repo.name.lower()) or repo.full_name.lower()
            if (
                repo.full_name.lower() in excluded_projects
                or family in used_families
                or self._is_catalog_repository(repo)
            ):
                continue
            adjacent_signal = self._repo_has_adjacent_signal(requirement, repo)
            core_feature = self._core_requirement_feature(requirement) or (
                requirement.must_have_features[0] if requirement.must_have_features else "核心需求"
            )
            coverage = repo.evidence_coverage or self._build_evidence_coverage(repo, requirement)
            covered_features = self._supported_adjacent_features(requirement, coverage)
            core_supported = any(
                item.feature == core_feature and (item.status == "supported" or item.covered)
                for item in coverage
            )
            core_related_features = self._core_related_adjacent_features(requirement, covered_features)
            core_aligned = self._core_evidence_is_compositional(requirement, repo)
            discovery_evidence = self._has_planned_discovery_evidence(repo)
            has_adjacent_evidence = bool(core_related_features or core_supported or discovery_evidence)
            if not has_adjacent_evidence:
                continue
            if repo.raw_score < 15 and not adjacent_signal and not has_adjacent_evidence:
                continue
            if repo.core_signal_score <= 0 and not adjacent_signal and not has_adjacent_evidence:
                continue
            if covered_features and not core_related_features and not core_aligned:
                continue
            if not covered_features and not core_supported and not core_aligned and not discovery_evidence:
                continue
            if repo.core_signal_score <= 0 and not covered_features and not core_supported and not discovery_evidence:
                continue
            unknown_features = [
                feature
                for feature in list(dict.fromkeys(requirement.must_have_features))
                if feature not in covered_features
            ][:8]
            score = self._low_similarity_lead_score(
                repo,
                coverage,
                covered_features,
                core_supported=core_supported,
                core_aligned=core_aligned,
                adjacent_signal=adjacent_signal,
            )
            if discovery_evidence:
                score = max(score, 20)
            if not covered_features and not core_supported and repo.core_signal_score < 2.0:
                continue
            if not covered_features and not core_supported and score < 20 and not discovery_evidence:
                continue
            analysis = ProjectAnalysis(
                repo=repo,
                match_score=score,
                recommendation="相邻方向，重点能力尚未核对",
                directly_usable=False,
                covered_features=covered_features[:8],
                missing_features=[],
                required_changes=[],
                risks=[],
                evidence=[],
                unknown_features=unknown_features,
                functional_score=score,
                suitability_score=score,
                core_feature=core_feature,
                core_confirmed=False,
                evidence_coverage=coverage
                or [EvidenceCoverage(feature=feature, covered=False, status="unknown") for feature in unknown_features],
            )
            self._mark_low_similarity_lead(analysis)
            signal = self._public_candidate_signal(repo)
            analysis.score_reason = (
                f"公开线索「{signal}」与「{core_feature}」方向相近，"
                "公开证据只支持较弱相邻关系，因此只作为相邻参考。"
            )
            leads.append(analysis)
            used_families.add(family)
        if leads:
            usage.warnings.append(
                "Added fallback low-similarity leads from ranked candidates: "
                + ", ".join(item.repo.full_name for item in leads)
            )
        return leads

    def _low_similarity_lead_score(
        self,
        repo: CandidateRepository,
        coverage: list[EvidenceCoverage],
        covered_features: list[str],
        *,
        core_supported: bool,
        core_aligned: bool,
        adjacent_signal: bool,
    ) -> int:
        supported = sum(1 for item in coverage if item.status == "supported" or item.covered)
        explicit = sum(1 for item in coverage if item.status in {"different", "missing"})
        evidence_bonus = min(14, supported * 4 + explicit * 2)
        core_bonus = 0
        if core_supported:
            core_bonus = 10
        elif core_aligned:
            core_bonus = 6
        elif repo.core_signal_score > 0:
            core_bonus = min(6, round(repo.core_signal_score * 2))
        source_bonus = min(4, max(0, len(repo.found_by) - 1) * 2)
        raw_bonus = min(5, max(0, int(repo.raw_score or 0)) // 20)
        score = 8 + evidence_bonus + core_bonus + source_bonus + raw_bonus
        if adjacent_signal or covered_features:
            score = max(score, 15)
        return max(1, min(39, score))

    def _has_planned_discovery_evidence(self, repo: CandidateRepository) -> bool:
        if repo.core_signal_score < 2.0:
            return False
        sources = [str(source) for source in repo.found_by or []]
        for source in sources:
            signal_count = self._discovery_source_signal_count(source)
            if source.startswith(("github_topic:", "github_code:")) and signal_count >= 2:
                return True
            if source.startswith("github:") and repo.core_signal_score >= 2.5 and signal_count >= 3:
                return True
            if source.startswith("tavily:") and repo.core_signal_score >= 2.5 and signal_count >= 3:
                return True
        return False

    def _discovery_source_signal_count(self, source: str) -> int:
        payload = str(source or "")
        for prefix in ("github_topic:", "github_code:", "github_issue:", "github:", "tavily:"):
            if payload.startswith(prefix):
                payload = payload[len(prefix) :]
                break
        payload = payload.split(" in:", 1)[0]
        payload = re.sub(r"[-_/]+", " ", payload)
        return len(self._semantic_signals(payload))

    def _analysis_has_adjacent_signal(
        self,
        analysis: ProjectAnalysis,
        requirement: Requirement | None,
    ) -> bool:
        if not requirement:
            return False
        return bool(
            analysis.repo.core_signal_score > 0
            or self._repo_has_adjacent_signal(requirement, analysis.repo)
        )

    def _repo_has_adjacent_signal(self, requirement: Requirement, repo: CandidateRepository) -> bool:
        if self._is_catalog_repository(repo):
            return False
        coverage = repo.evidence_coverage or self._build_evidence_coverage(repo, requirement)
        groups = requirement.feature_concepts or {}
        public_text = " ".join(
            [
                repo.name,
                repo.description,
                " ".join(repo.topics),
                self._readme_capability_text(repo.readme),
                " ".join(repo.file_paths[:80]),
            ]
        )
        domain_aliases = self._clean_feature_aliases({str(item) for item in groups.get("domains", [])})
        domain_hit = bool(domain_aliases and self._matching_terms(public_text, domain_aliases))
        if domain_aliases and not domain_hit:
            return False
        if domain_aliases and self._supported_adjacent_features(requirement, coverage):
            return True
        group_hits: dict[str, bool] = {}
        for group in ("actions", "objects", "outputs", "interfaces"):
            aliases = self._clean_feature_aliases({str(item) for item in groups.get(group, [])})
            group_hits[group] = bool(aliases and self._matching_terms(public_text, aliases))
        if domain_aliases:
            return any(group_hits.values())
        substantive_hits = sum(1 for group in ("actions", "objects") if group_hits.get(group))
        total_hits = sum(1 for hit in group_hits.values() if hit)
        return substantive_hits >= 1 and total_hits >= 2

    def _supported_adjacent_features(
        self,
        requirement: Requirement,
        coverage: list[EvidenceCoverage],
    ) -> list[str]:
        core_feature = self._core_requirement_feature(requirement)
        return [
            item.feature
            for item in coverage
            if item.feature != core_feature and (item.status == "supported" or item.covered)
        ]

    def _core_related_adjacent_features(
        self,
        requirement: Requirement,
        features: list[str],
    ) -> list[str]:
        concepts = requirement.feature_concepts or {}
        primary_aliases = self._clean_feature_aliases(
            {
                str(item)
                for group in ("domains", "actions", "objects")
                for item in concepts.get(group, [])
            }
        )
        secondary_aliases = self._clean_feature_aliases(
            {
                str(item)
                for group in ("outputs", "interfaces")
                for item in concepts.get(group, [])
            }
        )
        if not primary_aliases:
            return [
                feature
                for feature in features
                if not self._matching_terms(feature, secondary_aliases)
            ]
        return [
            feature
            for feature in features
            if not self._matching_terms(feature, secondary_aliases)
            and self._matching_terms(feature, primary_aliases)
        ]

    @staticmethod
    def _requires_compositional_core(requirement: Requirement) -> bool:
        concepts = requirement.feature_concepts or {}
        return (
            sum(bool(concepts.get(group)) for group in ("domains", "actions", "objects"))
            >= 2
        )

    def _ensure_positive_lead_score(
        self,
        analysis: ProjectAnalysis,
        requirement: Requirement | None,
    ) -> None:
        if analysis.match_score > 0:
            return
        adjacent_features = (
            self._supported_adjacent_features(requirement, analysis.evidence_coverage)
            if requirement
            else []
        )
        analysis.match_score = 15 if adjacent_features else 1
        analysis.functional_score = analysis.match_score
        analysis.suitability_score = max(analysis.suitability_score, analysis.match_score)

    def _is_usable_reference_candidate(
        self,
        analysis: ProjectAnalysis,
        requirement: Requirement | None = None,
    ) -> bool:
        if self._is_catalog_candidate(analysis):
            return False
        core_feature = self._core_requirement_feature(requirement) if requirement else analysis.core_feature
        if (
            core_feature
            and not analysis.core_confirmed
            and self._feature_has_confirmed_difference(core_feature, analysis.different_features)
        ):
            return False
        return any(
            item.covered
            and (
                item.feature == core_feature
                or (
                    requirement is not None
                    and bool(self._core_related_adjacent_features(requirement, [item.feature]))
                )
                or (
                    requirement is None
                )
            )
            for item in analysis.evidence_coverage
        )

    def _has_meaningful_adjacent_value(
        self,
        analysis: ProjectAnalysis,
        requirement: Requirement | None,
    ) -> bool:
        if analysis.core_confirmed:
            return True
        if not requirement:
            return self._is_usable_reference_candidate(analysis)
        core_feature = self._core_requirement_feature(requirement) or analysis.core_feature
        if core_feature and self._feature_has_confirmed_difference(core_feature, analysis.different_features):
            return False
        if analysis.repo.core_signal_score <= 0:
            analysis.repo.core_signal_score = self._core_direction_score(requirement, analysis.repo)
        supported = self._supported_adjacent_features(requirement, analysis.evidence_coverage)
        if self._has_planned_discovery_evidence(analysis.repo):
            return analysis.match_score >= 15
        if supported:
            if self._core_related_adjacent_features(requirement, supported):
                return True
            if self._requires_compositional_core(requirement):
                return (
                    analysis.repo.core_signal_score >= 2.0
                    and self._core_evidence_is_compositional(requirement, analysis.repo)
                )
            return analysis.repo.core_signal_score >= 2.0
        if analysis.repo.core_signal_score < 2.0 or analysis.match_score < 20:
            return False
        if self._requires_compositional_core(requirement):
            return self._core_evidence_is_compositional(requirement, analysis.repo)
        return True

    def _is_catalog_candidate(self, analysis: ProjectAnalysis) -> bool:
        return analysis.is_catalog or self._is_catalog_repository(analysis.repo)

    @staticmethod
    def _project_family_key(analysis: ProjectAnalysis) -> str:
        """Treat same-named mirrors/forks as one result family in the concise Top 3."""
        return re.sub(r"[^a-z0-9]+", "", analysis.repo.name.lower()) or analysis.repo.full_name.lower()

    def _mark_low_similarity_lead(self, analysis: ProjectAnalysis) -> None:
        covered = [
            item.feature
            for item in analysis.evidence_coverage
            if (item.status == "supported" or item.covered)
            and (analysis.core_confirmed or item.feature != analysis.core_feature)
        ][:5]
        missing = [
            item.feature for item in analysis.evidence_coverage if item.status == "missing"
        ][:5]
        unknown = [
            item.feature for item in analysis.evidence_coverage if item.status == "unknown" and not item.covered
        ][:5]
        if analysis.evidence_coverage:
            analysis.covered_features = covered[:8]
            analysis.missing_features = missing
            analysis.unknown_features = list(dict.fromkeys([*analysis.unknown_features, *unknown]))[:8]
        finding_text = "、".join(missing or unknown) if (missing or unknown) else "核心能力匹配较少"
        reason = f"低相似线索：{finding_text}"
        analysis.is_reference_candidate = True
        analysis.confidence_level = "lead"
        analysis.reference_reason = reason
        analysis.directly_usable = False
        if reason not in analysis.risks:
            analysis.risks.append(reason)
        if "仅作为搜索线索，不建议作为候选项目采用" not in analysis.required_changes:
            analysis.required_changes.append("仅作为搜索线索，不建议作为候选项目采用")

    def _mark_reference_candidate(self, analysis: ProjectAnalysis) -> None:
        missing = [
            item.feature for item in analysis.evidence_coverage if item.status == "missing"
        ][:5]
        unknown = [
            item.feature for item in analysis.evidence_coverage if item.status == "unknown" and not item.covered
        ][:5]
        if analysis.evidence_coverage:
            analysis.missing_features = missing
            analysis.unknown_features = list(dict.fromkeys([*analysis.unknown_features, *unknown]))[:8]
        if missing:
            reason = f"参考项目：明确缺少 {'、'.join(missing)}"
        elif analysis.unknown_features:
            reason = f"参考项目：{'、'.join(analysis.unknown_features[:3])}尚待确认"
        else:
            reason = "参考项目：只覆盖部分需求"
        analysis.is_reference_candidate = True
        analysis.confidence_level = "reference"
        analysis.reference_reason = reason
        analysis.directly_usable = False
        if reason not in analysis.risks:
            analysis.risks.append(reason)
        if "作为参考候选评估，不建议直接采用" not in analysis.required_changes:
            analysis.required_changes.append("作为参考候选评估，不建议直接采用")

    def _fallback_changes(self, missing: list[str]) -> list[str]:
        if not missing:
            return ["先本地运行并验证主要流程，再决定是否集成"]
        return [f"补齐功能：{item}" for item in missing[:5]]

    async def _analyze_opportunity(
        self,
        requirement: Requirement,
        analyses: list[ProjectAnalysis],
        llm: LLMClient | None,
    ) -> str:
        if not analyses:
            core = self._core_requirement_feature(requirement) or (
                requirement.must_have_features[0] if requirement.must_have_features else "核心需求"
            )
            return (
                f"本次没有找到可直接采用且能完整确认「{self._plain_user_text(core)}」的项目。"
                "如果候选发现阶段仍有可复核仓库，报告会优先补充核心功能相近或相关方向；"
                "本轮为空通常表示公开检索阶段没有拿到足够可核对的仓库证据。"
            )
        if all(item.is_reference_candidate for item in analyses):
            best = analyses[0]
            unresolved = list(dict.fromkeys([*best.missing_features, *best.unknown_features]))
            focus = self._plain_user_text("、".join(unresolved[:4])) if unresolved else "完整使用流程"
            reusable = (
                self._plain_user_text("、".join(best.covered_features[:3]))
                if best.covered_features
                else "现有项目的基础框架"
            )
            return (
                "本次已经核对候选项目的公开说明和重点内容，"
                f"仍未确认有项目同时具备「{focus}」。"
                f"可借鉴 {best.repo.full_name} 的「{reusable}」；未覆盖的核心部分需要另找方案或单独实现。"
            )
        best_analysis = analyses[0]
        covered = self._plain_user_text("、".join(best_analysis.covered_features[:4]))
        unknown = self._plain_user_text("、".join(best_analysis.unknown_features[:5]))
        missing = self._plain_user_text("、".join(best_analysis.missing_features[:4]))
        differences = self._plain_user_text("、".join(best_analysis.different_features[:3]))
        parts = [
            f"本次已确认 {best_analysis.repo.full_name} 具备「{covered}」。"
            if covered
            else f"本次尚未确认 {best_analysis.repo.full_name} 覆盖核心能力。"
        ]
        if unknown:
            parts.append(f"项目公开内容尚未确认「{unknown}」。")
        if missing:
            parts.append(f"项目明确不提供「{missing}」，这些部分需要补充实现。")
        if differences:
            parts.append(f"同时还存在「{differences}」等使用差异。")
        if unknown:
            parts.append("下一步应先继续核对这些未确认能力；确认确实没有后，再决定补充开发。")
        elif not missing:
            parts.append("下一步可直接试用主要流程，再决定是否采用。")
        return "".join(parts)

    @staticmethod
    def _plain_user_text(text: str) -> str:
        return str(text or "")

    def _sentence(self, text: str) -> str:
        stripped = str(text or "").strip()
        if not stripped:
            return ""
        return stripped if stripped.endswith(("。", "！", "？", ".", "!", "?")) else f"{stripped}。"

    def _write_summary(self, analyses: list[ProjectAnalysis], opportunity: str) -> str:
        if not analyses:
            return "本次未找到可直接采用的项目；若候选发现阶段有可复核仓库，应优先补充核心功能相近或相关方向。"
        best = analyses[0]
        if all(item.confidence_level == "lead" for item in analyses):
            return (
                f"已整理 {len(analyses)} 个相邻方向，最接近的是 {best.repo.full_name}（{best.match_score}/100），"
                "目前更适合用来找灵感，不适合直接采用。"
            )
        if all(item.is_reference_candidate for item in analyses):
            confirmed = (
                self._plain_user_text("、".join(best.covered_features[:2]))
                if best.covered_features
                else "部分相邻线索"
            )
            return (
                f"没有找到可直接使用的项目。最接近的 {best.repo.full_name} 为 {best.match_score}/100，"
                f"可作为「{confirmed}」方向的参考线索。"
            )
        if best.is_reference_candidate:
            return (
                f"可靠候选不足，已补充参考项目；最接近的是 {best.repo.full_name}"
                f"（关联度 {best.match_score}/100）。"
            )
        summary = f"最相关项目是 {best.repo.full_name}（关联度 {best.match_score}/100）。"
        summary += "下面只列出有公开证据支撑的符合、明确差异和明确缺失。"
        return summary

    def _write_report(
        self,
        query: str,
        requirement: Requirement,
        analyses: list[ProjectAnalysis],
        opportunity: str,
        usage: BudgetUsage,
        search_completeness: dict[str, object] | None = None,
    ) -> str:
        lines: list[str] = []
        lines.append("# 调研结论")
        lines.append("")
        lines.append("## 一句话判断")
        lines.append(self._write_summary(analyses, opportunity))
        lines.append("")
        lines.append("## 已整理的线索")
        if not analyses:
            lines.append("- 未形成可核对项目清单；这不是理想结果，应优先检查检索词、平台别名和相邻项目兜底是否生效。")
            self._append_empty_result_context(lines, requirement)
        for project_index, analysis in enumerate(analyses, start=1):
            lines.append("")
            self._append_project_report(lines, project_index, analysis)
        lines.append("")
        lines.append("## 本次消耗")
        lines.append(self._format_token_usage(usage))
        return "\n".join(lines)

    @staticmethod
    def _format_token_usage(usage: BudgetUsage) -> str:
        total = usage.llm_input_tokens + usage.llm_output_tokens
        label = "LLM Token（估算）" if usage.llm_token_estimated and total else "LLM Token"
        return f"- {label}：输入 {usage.llm_input_tokens}，输出 {usage.llm_output_tokens}，合计 {total}。"

    def _append_empty_result_context(self, lines: list[str], requirement: Requirement) -> None:
        channels: list[str] = []
        if requirement.repo_search_queries or requirement.search_queries:
            channels.append("项目名称、简介和说明")
        if requirement.code_search_queries:
            channels.append("代码和文件路径线索")
        if requirement.topic_search_queries:
            channels.append("主题标签")
        if requirement.issue_search_queries:
            channels.append("相关讨论和问题")
        if requirement.web_search_queries:
            channels.append("网页交叉发现")
        if not channels:
            channels.append("项目公开说明")
        core = self._plain_user_text(
            self._core_requirement_feature(requirement)
            or (requirement.must_have_features[0] if requirement.must_have_features else "核心需求")
        )
        lines.append("")
        lines.append("### 本次为什么没有列出项目")
        lines.append(f"- 已查找方向：{'、'.join(channels)}。")
        lines.append(
            f"- 筛选原因：没有候选能用公开证据完整确认「{core}」。按核心规则，这种情况下应继续展示核心功能相近或相关方向，而不是输出空报告。"
        )
        if core:
            lines.append(f"- 关键缺口：未确认核心能力「{core}」。")

    def _append_project_report(
        self,
        lines: list[str],
        index: int,
        analysis: ProjectAnalysis,
    ) -> None:
        repo = analysis.repo
        label = ""
        if analysis.confidence_level == "lead":
            label = "（相邻参考）"
        elif analysis.is_reference_candidate:
            label = "（参考项目）"
        metadata = self._format_repo_title_metadata(repo)
        lines.append(f"### {index}. {repo.full_name}{label}{metadata}")
        lines.append(f"- 关联度：{analysis.match_score}/100")
        lines.append(
            f"- 得分原因：{self._plain_user_text(analysis.score_reason or self._score_reason(analysis))}"
        )
        if analysis.covered_features and analysis.confidence_level != "lead":
            lines.append(f"- 符合部分：{self._plain_user_text('、'.join(analysis.covered_features[:5]))}")
        differences: list[str] = []
        for item in analysis.different_features:
            differences.extend(
                self._plain_user_text(part).strip(" 。；;")
                for part in re.split(r"[；;。]", item)
                if part.strip(" 。；;")
            )
            if len(differences) >= 3:
                break
        differences = differences[:3]
        if differences:
            lines.append(f"- 差异部分：{'；'.join(differences)}")
        if analysis.missing_features:
            lines.append(f"- 缺失部分：{self._plain_user_text('、'.join(analysis.missing_features[:4]))}")
        lines.append(f"- 地址：{repo.url}")

    @staticmethod
    def _format_repo_title_metadata(repo: CandidateRepository) -> str:
        updated = str(repo.last_pushed_at or "").strip()
        if "T" in updated:
            updated = updated.split("T", 1)[0]
        elif len(updated) > 10:
            updated = updated[:10]
        updated = updated or "未知"
        return f" · ★ {repo.stars} · 更新 {updated}"

    def _search_completeness(self, usage: BudgetUsage, request_limit: int) -> dict[str, object]:
        reasons: list[str] = []
        if usage.github_requests >= request_limit:
            reasons.append("GitHub request limit reached")
        if usage.warnings and any("rate limit" in warning.lower() for warning in usage.warnings):
            reasons.append("GitHub rate limit warning")
        return {"level": "limited" if reasons else "complete", "reasons": reasons}

    def _mark_cost_completeness(self, usage: BudgetUsage) -> None:
        missing: list[str] = []
        if usage.llm_input_tokens > 0 and self.settings.llm_input_usd_per_1m <= 0:
            missing.append("llm_input_usd_per_1m")
        if usage.llm_output_tokens > 0 and self.settings.llm_output_usd_per_1m <= 0:
            missing.append("llm_output_usd_per_1m")
        if usage.tavily_credits > 0 and self.settings.tavily_usd_per_credit <= 0:
            missing.append("tavily_usd_per_credit")
        usage.missing_price_components = missing
        usage.estimated_usd_complete = not missing

    def _estimate_usd(self, usage: BudgetUsage) -> float:
        llm_cost = (
            usage.llm_input_tokens / 1_000_000 * self.settings.llm_input_usd_per_1m
            + usage.llm_output_tokens / 1_000_000 * self.settings.llm_output_usd_per_1m
        )
        tavily_cost = usage.tavily_credits * self.settings.tavily_usd_per_credit
        return round(llm_cost + tavily_cost, 4)


async def deep_search(
    query: str,
) -> SearchReport:
    return await DeepSearchEngine().run(query)
