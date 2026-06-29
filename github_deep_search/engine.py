from __future__ import annotations

import asyncio
import copy
import json
import math
import re
import time
from collections import OrderedDict
from difflib import SequenceMatcher

from github_deep_search.config import Settings, get_settings
from github_deep_search.models import (
    BudgetUsage,
    CandidateRepository,
    EvidenceCoverage,
    Mode,
    ProjectAnalysis,
    Requirement,
    SearchBudget,
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
        mode: Mode = "detailed",
        budget: SearchBudget = "continue",
        baseline: SearchReport | None = None,
    ) -> SearchReport:
        started = time.perf_counter()
        usage = BudgetUsage()
        request_limit = self._budgeted_github_limit(budget)
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
            baseline = self._valid_baseline(query, mode, budget, baseline)
            if baseline:
                requirement = copy.deepcopy(baseline.requirement)
            else:
                spec = await SearchSpecParser().parse(query, llm)
                requirement = spec.to_requirement()
            candidates = await self._collect_candidates(requirement, github, tavily, usage, mode, budget)
            discovery_requests = usage.github_requests
            if baseline:
                candidates = self._include_baseline_candidates(candidates, baseline)
            ranked = self._rank_candidates(requirement, candidates)
            await self._hydrate_readmes(ranked, github, usage, mode, budget)
            readme_requests = usage.github_requests - discovery_requests
            reranked = self._rank_candidates(requirement, ranked)
            deep_pool_limit = self._deep_pool_limit(mode, budget)
            deep_repos = reranked[:deep_pool_limit]
            await self._hydrate_source_evidence(deep_repos, github, usage, requirement, mode, budget)
            source_requests = usage.github_requests - discovery_requests - readme_requests
            deep_repos = self._rerank_by_evidence(deep_repos, requirement)
            analyses = await self._analyze_top_projects(requirement, deep_repos, llm)
            analyses, evidence_gate_stats = self._apply_evidence_gate(requirement, analyses, usage)
            low_confidence_analyses = [item for item in analyses if item.match_score < 50]
            reliable_analyses = [item for item in analyses if item.match_score >= 50]
            low_confidence = [item.repo.full_name for item in low_confidence_analyses]
            analyses = self._with_reference_candidates(reliable_analyses, low_confidence_analyses, usage, requirement)
            if baseline:
                analyses = self._preserve_baseline_results(analyses, baseline)
            if len(analyses) < self.settings.max_deep_analyze_repos:
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
                mode,
                search_completeness,
                budget,
            )
            summary = self._write_summary(analyses, opportunity)
            return SearchReport(
                query=query,
                mode=mode,
                budget=budget,
                requirement=requirement,
                top_projects=analyses,
                opportunity=opportunity,
                summary=summary,
                report_markdown=report_markdown,
                usage=usage,
                raw={
                    "candidate_count": len(candidates),
                    "budget": budget,
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
                    "planned_repo_queries_used": self._planned_repo_search_queries(requirement, mode, budget),
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
                    "baseline_reused": bool(baseline),
                },
            )
        finally:
            await github.close()
            if tavily:
                await tavily.close()
            if llm:
                await llm.close()

    def _budget_multiplier(self, budget: SearchBudget) -> float:
        if budget == "high":
            return 1.8
        if budget == "continue":
            return 2.3
        return 1.0

    def _valid_baseline(
        self,
        query: str,
        mode: Mode,
        budget: SearchBudget,
        baseline: SearchReport | None,
    ) -> SearchReport | None:
        if (mode != "detailed" and budget == "standard") or baseline is None:
            return None
        normalized_query = re.sub(r"\s+", " ", query).strip().casefold()
        normalized_baseline = re.sub(r"\s+", " ", baseline.query).strip().casefold()
        if normalized_query != normalized_baseline:
            return None
        return baseline

    def _budgeted_github_limit(self, budget: SearchBudget) -> int:
        return max(1, int(self.settings.max_github_requests * self._budget_multiplier(budget)))

    def _budgeted_candidate_limit(self, budget: SearchBudget) -> int:
        return max(1, int(self.settings.max_candidates * self._budget_multiplier(budget)))

    def _deep_pool_limit(self, mode: Mode, budget: SearchBudget = "standard") -> int:
        if mode == "light":
            base = max(self.settings.max_deep_analyze_repos * 2, 5)
        else:
            base = max(self.settings.max_deep_analyze_repos * 3, 8)
        return min(12, max(base, int(base * self._budget_multiplier(budget))))

    def _evidence_request_reserve(self, mode: Mode, budget: SearchBudget) -> int:
        """Requests discovery cannot borrow: README plus focused checks for final candidates."""
        result_count = self.settings.max_deep_analyze_repos
        readme_count = self._deep_pool_limit(mode, budget) + 2
        files_per_repo = 1 if mode == "light" else 2
        return readme_count + result_count * (1 + files_per_repo)

    def _merge_queries(self, queries: list[str], limit: int = 6) -> list[str]:
        return list(OrderedDict.fromkeys(q.strip() for q in queries if q.strip()))[:limit]

    async def _collect_candidates(
        self,
        requirement: Requirement,
        github: GitHubClient,
        tavily: TavilyClient | None,
        usage: BudgetUsage,
        mode: Mode,
        budget: SearchBudget,
    ) -> list[CandidateRepository]:
        repos: OrderedDict[str, CandidateRepository] = OrderedDict()
        candidate_limit = self._budgeted_candidate_limit(budget)
        repo_queries = self._planned_repo_search_queries(requirement, mode, budget)
        code_queries = self._planned_code_search_queries(requirement, mode, budget)
        topic_queries = self._planned_topic_search_queries(requirement, mode, budget)
        issue_queries = self._interleave_multilingual_queries(
            self._planned_issue_search_queries(requirement, mode, budget)
        )
        queries_per_wave = max(3, int(3 * self._budget_multiplier(budget)))
        request_limit = self._budgeted_github_limit(budget)
        evidence_reserve = self._evidence_request_reserve(mode, budget)
        search_request_limit = max(8, request_limit - evidence_reserve)
        repo_per_page = 12
        code_per_page = 5
        topic_per_page = 10
        issue_per_page = 10

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
            web_limit = int((2 if mode == "light" else 4) * self._budget_multiplier(budget))
            web_queries = planned_web_queries[:web_limit]
            for search_query in web_queries:
                if usage.tavily_credits >= self.settings.max_tavily_credits:
                    usage.warnings.append("Tavily budget reached during cross-validation.")
                    break
                results = await tavily.search(f"site:github.com {search_query} open source", max_results=5)
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

    def _planned_code_search_queries(self, requirement: Requirement, mode: Mode, budget: SearchBudget = "standard") -> list[str]:
        limit = int((3 if mode == "light" else 5) * self._budget_multiplier(budget))
        queries: list[str] = []
        core_feature = self._core_requirement_feature(requirement)
        core_aliases = sorted(self._feature_aliases(core_feature, requirement)) if core_feature else []
        planned = [*core_aliases, *requirement.code_search_queries]
        for phrase in self._merge_queries(planned, limit=limit):
            token = self._github_search_token(phrase)
            if token:
                queries.append(f"{token} in:file,path")
        return self._merge_queries(queries, limit=limit)

    def _planned_repo_search_queries(
        self,
        requirement: Requirement,
        mode: Mode,
        budget: SearchBudget = "standard",
    ) -> list[str]:
        """Put narrow core-capability angles before secondary output/interface queries."""
        limit = max(10, int((7 if mode == "light" else 10) * self._budget_multiplier(budget)))
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
            aliases = sorted(
                self._feature_aliases(core_feature, requirement),
                key=lambda item: (len(item.split()), len(item)),
            )
            core_queries.extend(aliases)
        core_queries.extend(domains[:4])
        planned = [
            *core_queries,
            *(requirement.repo_search_queries or requirement.search_queries),
        ]
        return self._merge_queries(self._interleave_multilingual_queries(planned), limit=limit)

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

    def _planned_topic_search_queries(
        self,
        requirement: Requirement,
        mode: Mode,
        budget: SearchBudget = "standard",
    ) -> list[str]:
        limit = int((3 if mode == "light" else 5) * self._budget_multiplier(budget))
        concepts = requirement.feature_concepts or {}
        domains = [str(item).strip() for item in concepts.get("domains", []) if str(item).strip()]
        return self._merge_queries([*domains, *requirement.topic_search_queries], limit=limit)

    def _planned_issue_search_queries(
        self,
        requirement: Requirement,
        mode: Mode,
        budget: SearchBudget = "standard",
    ) -> list[str]:
        limit = int((3 if mode == "light" else 5) * self._budget_multiplier(budget))
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
        clean = " ".join(words[:2])
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

    def _include_baseline_candidates(
        self,
        candidates: list[CandidateRepository],
        baseline: SearchReport,
    ) -> list[CandidateRepository]:
        merged: OrderedDict[str, CandidateRepository] = OrderedDict(
            (repo.full_name.lower(), repo) for repo in candidates if repo.owner and repo.name
        )
        for analysis in baseline.top_projects:
            seed = copy.deepcopy(analysis.repo)
            key = seed.full_name.lower()
            if key in merged:
                current = merged[key]
                for source in seed.found_by:
                    if source not in current.found_by:
                        current.found_by.append(source)
                current.readme = current.readme or seed.readme
                current.file_paths = current.file_paths or seed.file_paths
                current.key_files = current.key_files or seed.key_files
                current.source_evidence = current.source_evidence or seed.source_evidence
                current.evidence_coverage = current.evidence_coverage or seed.evidence_coverage
                seed = current
            else:
                merged[key] = seed
            if seed.readme:
                self._readme_cache[key] = seed.readme
            if seed.file_paths:
                self._tree_cache[key] = list(seed.file_paths)
            for path, text in seed.key_files.items():
                self._file_cache[(key, path)] = text
        return list(merged.values())

    def _preserve_baseline_results(
        self,
        current: list[ProjectAnalysis],
        baseline: SearchReport,
    ) -> list[ProjectAnalysis]:
        by_name = {item.repo.full_name.lower(): item for item in current}
        pinned: list[str] = []
        for previous in baseline.top_projects:
            key = previous.repo.full_name.lower()
            if not previous.is_reference_candidate and previous.match_score >= 50:
                pinned.append(key)
            if key in by_name:
                item = by_name[key]
                item.match_score = max(item.match_score, previous.match_score)
                item.covered_features = list(dict.fromkeys([*item.covered_features, *previous.covered_features]))[:8]
                item.missing_features = [
                    feature for feature in item.missing_features if feature not in item.covered_features
                ][:8]
                if not previous.is_reference_candidate and previous.match_score >= 50:
                    item.is_reference_candidate = False
                    item.confidence_level = "reliable"
                    item.reference_reason = ""
                if previous.directly_usable:
                    item.directly_usable = True
                continue
            by_name[key] = copy.deepcopy(previous)

        ordered = sorted(by_name.values(), key=lambda item: item.match_score, reverse=True)
        if any(not item.is_reference_candidate and item.match_score >= 50 for item in ordered):
            ordered = [
                item
                for item in ordered
                if not item.is_reference_candidate or item.match_score >= 35
            ]
        selected = ordered[: self.settings.max_deep_analyze_repos]
        selected_names = {item.repo.full_name.lower() for item in selected}
        for key in pinned:
            if key in selected_names or key not in by_name:
                continue
            replace_index = next(
                (
                    index
                    for index in range(len(selected) - 1, -1, -1)
                    if selected[index].repo.full_name.lower() not in pinned
                ),
                None,
            )
            if replace_index is None:
                continue
            selected_names.discard(selected[replace_index].repo.full_name.lower())
            selected[replace_index] = by_name[key]
            selected_names.add(key)
        return sorted(selected, key=lambda item: item.match_score, reverse=True)

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
                + specificity_bonus
                + core_bonus
            )
            if self._is_catalog_repository(repo):
                repo.raw_score = min(repo.raw_score, 15)
        return sorted(candidates, key=lambda item: item.raw_score, reverse=True)

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
        if not domain_aliases and core_strong:
            return 3.0
        if not domain_aliases and core_weak:
            return 2.0
        return 0.0

    def _is_catalog_repository(self, repo: CandidateRepository) -> bool:
        name = repo.name.lower()
        public_text = f"{repo.description} {repo.readme[:2500]}".lower()
        if name.startswith("awesome-") or name.endswith("-awesome"):
            return True
        if re.search(r"(?:^|[-_])(?:book|intro|tutorial|course|lecture|handbook|guide)(?:$|[-_])", name):
            return True
        strong_markers = {
            "curated list",
            "project list",
            "projects list",
            "directory of projects",
            "collection of projects",
            "list of projects",
            "list cool",
            "interesting projects",
            "awesome list",
            "newsletter",
            "course materials",
            "lecture notes",
            "textbook",
            "study guide",
            "learning notes",
            "项目列表",
            "项目合集",
            "资源列表",
            "软件目录",
            "产品目录",
            "聚合所有",
            "课程资料",
            "课程笔记",
            "学习笔记",
            "教程",
            "教材",
            "讲义",
        }
        if any(marker in public_text for marker in strong_markers):
            return True
        if re.search(r"\blist\b.{0,80}\bprojects?\b|\bprojects?\b.{0,80}\blist\b", public_text):
            return True
        if re.search(r"\b(?:daily|weekly|monthly)\b", name) and re.search(r"\bprojects?\b", public_text):
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
        mode: Mode,
        budget: SearchBudget,
    ) -> None:
        limit = self._deep_pool_limit(mode, budget) + 2
        request_limit = self._budgeted_github_limit(budget)
        source_reserve = self.settings.max_deep_analyze_repos * (2 if mode == "light" else 3)
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
        mode: Mode,
        budget: SearchBudget,
    ) -> None:
        repo_limit = self.settings.max_deep_analyze_repos
        max_files = 1 if mode == "light" else 2
        for repo in repos[:repo_limit]:
            if usage.github_requests >= self._budgeted_github_limit(budget):
                usage.warnings.append("GitHub request budget reached before source evidence checks.")
                break
            await self._fetch_source_evidence_into(repo, github, requirement, mode, max_files=max_files)

    async def _fetch_source_evidence_into(
        self,
        repo: CandidateRepository,
        github: GitHubClient,
        requirement: Requirement,
        mode: Mode,
        max_files: int | None = None,
    ) -> None:
        repo_key = repo.full_name.lower()
        if repo_key not in self._tree_cache:
            self._tree_cache[repo_key] = await github.fetch_tree_paths(repo)
        repo.file_paths = self._tree_cache[repo_key]
        selected_paths = self._select_key_paths(repo.file_paths, requirement, mode)
        if max_files is not None:
            selected_paths = selected_paths[:max_files]
        for path in selected_paths:
            cache_key = (repo_key, path)
            if cache_key not in self._file_cache:
                self._file_cache[cache_key] = await github.fetch_file_text(repo, path, max_chars=9000)
            repo.key_files[path] = self._file_cache[cache_key]
        repo.evidence_coverage = self._build_evidence_coverage(repo, requirement)
        repo.source_evidence = self._build_source_evidence(repo, requirement)

    def _select_key_paths(self, paths: list[str], requirement: Requirement, mode: Mode) -> list[str]:
        aliases = self._requirement_aliases(requirement)
        max_files = 5 if mode == "light" else 8
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
            readme_hits = [] if is_catalog else self._matching_terms(capability_readme, aliases)
            public_description = " ".join([repo.description, " ".join(repo.topics)])
            description_hits = [] if is_catalog else self._matching_terms(public_description, aliases)
            path_evidence: list[str] = []
            for path in ([] if is_catalog else repo.file_paths):
                if not self._path_can_prove_capability(path):
                    continue
                hits = self._matching_terms(path, aliases)
                if hits:
                    path_evidence.append(f"{path} ({', '.join(hits[:3])})")
                if len(path_evidence) >= 5:
                    break
            source_evidence: list[str] = []
            for path, text in ({} if is_catalog else repo.key_files).items():
                evidence_text = self._readme_capability_text(text) if path.lower().endswith((".md", ".mdx")) else text
                hits = self._matching_terms(evidence_text, aliases)
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
            if feature == core_feature and compositional_groups >= 2:
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
            if covered and not self._named_entities_are_all_present(feature, repo):
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

    def _named_entities_are_all_present(self, feature: str, repo: CandidateRepository) -> bool:
        entities = {
            token.lower()
            for token in re.findall(r"[A-Za-z][A-Za-z0-9_.-]{2,}", feature)
            if any(character.isupper() or character.isdigit() for character in token)
        }
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
        return all(self._literal_alias_present(entity, text) for entity in entities)

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
        parts = {part for part in lowered.split("/") if part}
        if parts.intersection({"screenshots", "screenshot", "images", "image", "assets"}):
            return False
        return True

    def _explicit_missing_reason(self, text: str, aliases: set[str]) -> str:
        """Only classify a feature as missing when the project says so explicitly."""
        lowered = str(text or "").lower()
        if not lowered:
            return ""
        negative_before = r"(?:does not support|doesn't support|not supported|without|unavailable|不支持|不提供|不包含|无法)"
        negative_after = r"(?:is not supported|is unavailable|not available|不受支持|不可用)"
        for alias in sorted(aliases, key=len, reverse=True):
            escaped = re.escape(alias.lower())
            if re.search(rf"{negative_before}.{{0,48}}{escaped}", lowered, flags=re.DOTALL):
                return f"项目说明明确表示不提供「{alias}」"
            if re.search(rf"{escaped}.{{0,32}}{negative_after}", lowered, flags=re.DOTALL):
                return f"项目说明明确表示不提供「{alias}」"
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
        candidate_features = [
            feature
            for feature in requirement.must_have_features
            if not self._is_generic_qualifier_feature(feature)
        ] or requirement.must_have_features
        focused_features = [
            feature
            for feature in candidate_features
            if not self._is_broad_tool_label(feature) and not self._is_output_artifact_feature(feature)
        ]
        if focused_features:
            candidate_features = focused_features
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
        primary_aliases = self._clean_feature_aliases(
            {
                str(item)
                for group in ("domains", "actions", "objects")
                for item in concepts.get(group, [])
            }
        )
        output_aliases = self._clean_feature_aliases({str(item) for item in concepts.get("outputs", [])})
        scored_features: list[tuple[int, int, int, int, int, str]] = []
        for feature in candidate_features:
            feature_signals = self._semantic_signals(feature)
            feature_signals.update(
                token[:-1] if token.endswith("s") and len(token) > 4 else token
                for token in list(feature_signals)
            )
            overlap = len(feature_signals.intersection(context_signals))
            primary_hits = len(self._matching_terms(feature, primary_aliases)) if primary_aliases else 0
            output_hits = len(self._matching_terms(feature, output_aliases)) if output_aliases else 0
            output_only_penalty = 1 if output_hits and not primary_hits else 0
            scored_features.append(
                (primary_hits, overlap, -output_only_penalty, len(feature_signals), len(feature), feature)
            )
        best = max(scored_features)
        return best[5] if best[0] > 0 or best[1] > 0 else candidate_features[0]

    @staticmethod
    def _is_generic_qualifier_feature(feature: str) -> bool:
        normalized = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", str(feature or "").lower())
        generic_features = {
            "opensource",
            "opensourcetool",
            "opensourceproject",
            "open",
            "source",
            "tool",
            "tools",
            "project",
            "projects",
            "software",
            "app",
            "application",
            "repo",
            "repository",
            "开源",
            "开源工具",
            "开源项目",
            "工具",
            "项目",
            "软件",
            "应用",
        }
        return normalized in generic_features

    @staticmethod
    def _is_broad_tool_label(feature: str) -> bool:
        text = re.sub(r"\s+", "", str(feature or "").lower())
        if not text:
            return False
        if re.search(r"(?:工具|项目|软件|应用)$", text) and len(text) <= 12:
            return True
        return bool(re.fullmatch(r"[a-z0-9_.-]*(?:tool|project|software|app|application)s?", text))

    @staticmethod
    def _is_output_artifact_feature(feature: str) -> bool:
        text = str(feature or "").strip().lower()
        if not text:
            return False
        action_markers = [
            "监控",
            "查询",
            "搜索",
            "采集",
            "分析",
            "测试",
            "执行",
            "summarize",
            "sync",
            "monitor",
            "search",
            "query",
            "collect",
            "test",
            "run",
        ]
        if any(marker in text for marker in action_markers):
            return False
        output_markers = [
            "报告",
            "仪表盘",
            "截图",
            "可视化",
            "网页",
            "markdown",
            "pdf",
            "report",
            "dashboard",
            "screenshot",
            "visualization",
        ]
        return any(marker in text for marker in output_markers)

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

    @staticmethod
    def _literal_alias_present(alias: str, lowered_text: str) -> bool:
        if re.fullmatch(r"[a-z0-9_.-]+", alias) and len(alias) <= 4:
            return bool(re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", lowered_text))
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
                if not text_signals:
                    continue
                overlap = alias_signals & text_signals
                coverage = len(overlap) / max(1, len(alias_signals))
                if len(overlap) >= 2 and (coverage >= 0.45 or (len(overlap) >= 4 and coverage >= 0.3)):
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
        if analysis.core_feature and not analysis.core_confirmed:
            parts.append(f"核心能力「{analysis.core_feature}」尚未确认")
        if analysis.covered_features:
            prefix = "仅确认" if len(analysis.covered_features) <= 2 else "已确认"
            parts.append(f"{prefix}{brief(analysis.covered_features)}")
        elif not analysis.core_feature:
            parts.append("尚未确认核心能力")
        if analysis.missing_features:
            parts.append(f"明确缺少{brief(analysis.missing_features)}")
        if analysis.unknown_features:
            parts.append(f"{brief(analysis.unknown_features)}仍未确认")
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
        weak = {
            "github",
            "project",
            "projects",
            "repo",
            "repos",
            "repository",
            "repositories",
            "software",
            "system",
            "tool",
            "tools",
            "app",
            "apps",
            "multiple",
            "across",
            "several",
            "various",
            "many",
            "自动",
            "多个",
            "多個",
            "不同",
            "若干",
            "仓库",
            "倉庫",
            "项目",
            "软件",
            "系统",
            "工具",
            "功能",
            "支持",
            "当前",
            "是否",
            "类似",
        }
        return {item for item in signals if item not in weak and len(item) >= 2}

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
                    "You are an open source technical research analyst. Return JSON only. "
                    "Use the same language as the user's requirement for every natural-language field. "
                    "If the requirement is Chinese, all recommendations, risks, changes, and evidence summaries must be Chinese."
                ),
                (
                    "For each repository, compare it to the requirement. Return JSON: "
                    '{"projects":[{"repo":"owner/name","match_score":0-100,"recommendation":"...",'
                    '"directly_usable":true/false,"covered_features":[],"different_features":[],'
                    '"missing_features":[],"unknown_features":[],'
                    '"required_changes":[],"risks":[],"evidence":[]}]}.\n'
                    "Prefer practical software projects over curated lists, newsletters, directories, or awesome lists. "
                    "If a candidate is only a list/directory/newsletter, give it a low score and mark it as reference-only.\n"
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
            evidence_covered = [item.feature for item in coverage if item.status == "supported"]
            explicit_missing = [item.feature for item in coverage if item.status == "missing"]
            unknown = [item.feature for item in coverage if item.status == "unknown"]
            evidence_differences = [item.feature for item in coverage if item.status == "different"]
            constraint_differences, constraint_unknown = self._constraint_findings(requirement, analysis.repo)
            if constraint_differences:
                unknown = [
                    feature
                    for feature in unknown
                    if not any(term in feature.lower() for term in ["open source", "open-source", "opensource", "开源"])
                ]
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
                    weight
                    * (
                        1.0
                        if item.status == "supported"
                        else 0.45
                        if item.status == "different"
                        else 0.0
                    )
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
                weight
                * (
                    1.0
                    if item.status == "supported"
                    else 0.45
                    if item.status == "different"
                    else 0.0
                )
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
                functional_score = min(49, max(1, round(functional_score * 0.60)))
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
        if penalized:
            usage.warnings.append("Candidates with explicitly absent requirements: " + ", ".join(penalized[:5]))
        return sorted(gated, key=lambda item: item.match_score, reverse=True), stats

    @staticmethod
    def _feature_has_confirmed_difference(feature: str, differences: list[str]) -> bool:
        """Prevent one capability from appearing as both confirmed and different."""
        feature_text = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", feature.lower())
        if not feature_text:
            return False
        for difference in differences:
            difference_text = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", difference.lower())
            if not difference_text:
                continue
            match_size = SequenceMatcher(None, feature_text, difference_text).find_longest_match().size
            if match_size >= 3 and match_size / max(3, min(len(feature_text), 8)) >= 0.45:
                return True
        return False

    @classmethod
    def _feature_has_confirmed_absence(cls, feature: str, differences: list[str]) -> bool:
        negative = re.compile(
            r"(?:does not|doesn't|not available|not supported|without|no\s+|"
            r"不支持|不提供|不包含|没有|无(?:法|此|该)?|缺少)"
        )
        return any(
            negative.search(str(difference).lower())
            and cls._feature_has_confirmed_difference(feature, [difference])
            for difference in differences
        )

    def _constraint_findings(
        self, requirement: Requirement, repo: CandidateRepository
    ) -> tuple[list[str], list[str]]:
        raw = f"{requirement.raw} {' '.join(requirement.must_have_features)}".lower()
        if not any(term in raw for term in ["open source", "open-source", "opensource", "开源"]):
            return [], []

        approved = {
            "MIT",
            "APACHE-2.0",
            "GPL-2.0",
            "GPL-3.0",
            "AGPL-3.0",
            "LGPL-2.1",
            "LGPL-3.0",
            "BSD-2-CLAUSE",
            "BSD-3-CLAUSE",
            "MPL-2.0",
            "ISC",
            "UNLICENSE",
        }
        license_id = str(repo.license or "").upper()
        license_text = repo.readme.lower() + " " + " ".join(
            text.lower() for path, text in repo.key_files.items() if "license" in path.lower()
        )
        restricted_markers = [
            "sustainable use license",
            "business source license",
            "server side public license",
            "fair-code license",
            "fair code license",
        ]
        if any(marker in license_text for marker in restricted_markers) or license_id in {
            "BUSL-1.1",
            "SSPL-1.0",
            "ELASTIC-2.0",
        }:
            return ["开源许可带有额外使用限制"], []
        if license_id in approved:
            return [], []
        return [], ["开源许可是否符合预期"]

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
        if slots <= 0 or not low_confidence:
            return selected
        remaining = [item for item in low_confidence if not self._is_catalog_candidate(item)]
        minimum_reference_score = 35 if selected else 20
        eligible_references = [
            item
            for item in remaining
            if item.match_score >= minimum_reference_score
            and (item.core_confirmed or not item.core_feature)
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
            if item.repo.full_name not in reference_names
            and (
                item.match_score >= 15
                or self._analysis_has_adjacent_signal(item, requirement)
            )
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
            if repo.raw_score < 15 and not adjacent_signal:
                continue
            if repo.core_signal_score <= 0 and not adjacent_signal:
                continue
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
            if covered_features and not core_related_features and not core_aligned:
                continue
            if not covered_features and not core_supported and not core_aligned:
                continue
            if repo.core_signal_score <= 0 and not covered_features and not core_supported:
                continue
            unknown_features = [
                feature
                for feature in list(dict.fromkeys(requirement.must_have_features))
                if feature not in covered_features
            ][:8]
            score = max(1, min(29, int(repo.raw_score or 1)))
            if adjacent_signal:
                score = max(score, 15)
            if not covered_features and not core_supported and repo.core_signal_score < 2.0:
                continue
            if not covered_features and not core_supported and score < 20:
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
            analysis.score_reason = (
                f"项目名称和公开简介与「{core_feature}」方向相近，"
                "但重点能力尚未确认，因此只作为相邻参考。"
            )
            leads.append(analysis)
            used_families.add(family)
        if leads:
            usage.warnings.append(
                "Added fallback low-similarity leads from ranked candidates: "
                + ", ".join(item.repo.full_name for item in leads)
            )
        return leads

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
            and not self._is_generic_qualifier_feature(item.feature)
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
                if not self._is_output_artifact_feature(feature)
                and not self._is_broad_tool_label(feature)
                and not self._is_generic_qualifier_feature(feature)
                and not self._matching_terms(feature, secondary_aliases)
            ]
        return [
            feature
            for feature in features
            if not self._is_output_artifact_feature(feature)
            and not self._is_broad_tool_label(feature)
            and not self._is_generic_qualifier_feature(feature)
            and not self._matching_terms(feature, secondary_aliases)
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
                    and not self._is_generic_qualifier_feature(item.feature)
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
        if analysis.repo.core_signal_score <= 0:
            analysis.repo.core_signal_score = self._core_direction_score(requirement, analysis.repo)
        supported = self._supported_adjacent_features(requirement, analysis.evidence_coverage)
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
            and not self._is_generic_qualifier_feature(item.feature)
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
                f"本次没有找到公开内容能够确认「{self._plain_user_text(core)}」的项目。"
                "同时也没有留下可核对的相邻项目线索，建议换一组更具体的关键词继续查找。"
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
        plain = str(text or "")
        replacements = {
            "项目为元仓库，实际代码在": "这个地址只是项目入口，实际内容在",
            "元仓库": "项目入口",
            "实际代码": "实际内容",
            "MCP 服务器": "配套工具",
            "MCP服务器": "配套工具",
            "MCP 服务": "配套工具",
            "数据查询接口": "查询能力",
            "README": "项目说明",
            "源码": "项目内容",
            "API": "连接方式",
            "Skill": "扩展功能",
            "工作流": "操作流程",
            "LLM": "AI",
            "Agent": "智能助手",
            "RAG": "知识检索",
            "外部数据库（如 MySQL）": "额外的数据存储服务",
        }
        for source, target in replacements.items():
            plain = plain.replace(source, target)
        return plain

    def _sentence(self, text: str) -> str:
        stripped = str(text or "").strip()
        if not stripped:
            return ""
        return stripped if stripped.endswith(("。", "！", "？", ".", "!", "?")) else f"{stripped}。"

    def _write_summary(self, analyses: list[ProjectAnalysis], opportunity: str) -> str:
        if not analyses:
            return "本次未找到足够相关的项目线索，建议缩小需求范围后再查一次。"
        best = analyses[0]
        if all(item.confidence_level == "lead" for item in analyses):
            return (
                f"已整理 {len(analyses)} 个相邻方向，最接近的是 {best.repo.full_name}（{best.match_score}/100），"
                "目前更适合用来找灵感，不适合直接采用。"
            )
        if all(item.is_reference_candidate for item in analyses):
            unresolved = list(dict.fromkeys([*best.missing_features, *best.unknown_features]))
            confirmed = (
                self._plain_user_text("、".join(best.covered_features[:2]))
                if best.covered_features
                else "少量外围能力"
            )
            unresolved_text = (
                self._plain_user_text("、".join(unresolved[:4])) if unresolved else "完整使用流程"
            )
            return (
                f"没有找到可直接使用的项目。最接近的 {best.repo.full_name} 为 {best.match_score}/100，"
                f"目前只确认「{confirmed}」；「{unresolved_text}」等关键能力仍未确认。"
            )
        if best.is_reference_candidate:
            return (
                f"可靠候选不足，已补充参考项目；最接近的是 {best.repo.full_name}"
                f"（关联度 {best.match_score}/100）。"
            )
        summary = f"最相关项目是 {best.repo.full_name}（关联度 {best.match_score}/100）。"
        summary += "下面按符合、差异和缺失三方面列出判断。"
        return summary

    def _write_report(
        self,
        query: str,
        requirement: Requirement,
        analyses: list[ProjectAnalysis],
        opportunity: str,
        usage: BudgetUsage,
        mode: Mode,
        search_completeness: dict[str, object] | None = None,
        budget: SearchBudget = "standard",
    ) -> str:
        lines: list[str] = []
        lines.append("# 调研结论")
        lines.append("")
        lines.append("## 一句话判断")
        lines.append(self._write_summary(analyses, opportunity))
        lines.append("")
        lines.append("## 已整理的线索")
        if not analyses:
            lines.append("- 暂无。")
            self._append_empty_result_context(lines, requirement)
        for project_index, analysis in enumerate(analyses, start=1):
            lines.append("")
            self._append_project_report(lines, project_index, analysis, mode)
        lines.append("")
        lines.append("## 下一步")
        lines.append(opportunity)
        lines.append("")
        lines.append("## 本次消耗")
        lines.append(self._format_token_usage(usage))
        if search_completeness:
            level = str(search_completeness.get("level") or "complete")
            if level != "complete":
                lines.append("")
                if budget == "standard":
                    range_message = "当前模式的搜索范围已用满，结论可能不完整；可切换深度模式继续查找。"
                elif budget == "high":
                    range_message = (
                        "本次深度调研已达到搜索范围上限，结论可能仍不完整；"
                        "可缩小目标用户或核心功能后重新调研。"
                    )
                else:
                    range_message = (
                        "本次已使用最大搜索范围，结论仍可能不完整；"
                        "建议缩小需求范围后重新调研。"
                    )
                lines.append(range_message)
        return "\n".join(lines)

    @staticmethod
    def _format_token_usage(usage: BudgetUsage) -> str:
        total = usage.llm_input_tokens + usage.llm_output_tokens
        return f"- Token：输入 {usage.llm_input_tokens}，输出 {usage.llm_output_tokens}，合计 {total}。"

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
        must_have = "、".join(self._plain_user_text(item) for item in requirement.must_have_features[:5])
        lines.append("")
        lines.append("### 本次为什么没有列出项目")
        lines.append(f"- 已查找方向：{'、'.join(channels)}。")
        lines.append(
            f"- 筛选原因：没有候选能用公开证据确认「{core}」；只有截图、报告、网页界面等外围能力的项目不会作为相邻项目展示。"
        )
        if must_have:
            lines.append(f"- 关键缺口：{must_have}。")
        lines.append(
            "- 下一步建议：保留目标平台和核心动作重新查找，例如把平台名、数据对象、查询/采集动作和报告形式分别组合成更短的关键词。"
        )

    def _append_project_report(
        self,
        lines: list[str],
        index: int,
        analysis: ProjectAnalysis,
        mode: Mode,
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
        lines.append(
            f"- 符合部分：{self._plain_user_text('、'.join(analysis.covered_features[:5])) if analysis.covered_features else '暂未确认'}"
        )
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
        if analysis.unknown_features and len(differences) < 3:
            unknown = self._plain_user_text("、".join(analysis.unknown_features[: 3 - len(differences)]))
            differences.append(f"尚未确认：{unknown}")
        lines.append(f"- 差异部分：{'；'.join(differences) if differences else '未发现明显差异'}")
        lines.append(
            f"- 缺失部分：{self._plain_user_text('、'.join(analysis.missing_features[:4])) if analysis.missing_features else '未发现明确缺失'}"
        )
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
    mode: Mode = "detailed",
    budget: SearchBudget = "continue",
    baseline: SearchReport | None = None,
) -> SearchReport:
    return await DeepSearchEngine().run(query, mode, budget, baseline=baseline)
