from __future__ import annotations

import asyncio
import json
import math
import re
import time
from collections import OrderedDict
from difflib import SequenceMatcher
from typing import Literal, Sequence

from github_deep_search.config import Settings, get_settings
from github_deep_search.run_trace import (
    RunTraceRecorder,
    SearchRunFailed,
    build_failure_artifact,
    classify_failure,
)
from github_deep_search.models import (
    AdjacentEvidence,
    BudgetUsage,
    CandidateRepository,
    EvidenceCoverage,
    EvidenceReference,
    ProjectAnalysis,
    ProviderEvent,
    Requirement,
    RunFailure,
    SearchReport,
)
from github_deep_search.public_report import build_public_project_view
from github_deep_search.providers.github import GitHubClient, GitHubProviderError
from github_deep_search.providers.llm import LLMClient
from github_deep_search.providers.tavily import TavilyClient
from github_deep_search.spec_parser import QUERY_CHANNEL_LIMITS, SearchSpecParser
from github_deep_search.utils import compact_text, extract_github_repos, keyword_bag, normalize_repo_url


class DeepSearchEngine:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._readme_cache: dict[str, str] = {}
        self._tree_cache: dict[str, list[str]] = {}
        self._file_cache: dict[tuple[str, str], str] = {}
        self._repository_metadata_cache: dict[str, CandidateRepository] = {}

    async def run(
        self,
        query: str,
        *,
        fixed_requirement: Requirement | None = None,
    ) -> SearchReport:
        started = time.perf_counter()
        usage = BudgetUsage()
        request_limit = self.settings.max_github_requests
        trace = RunTraceRecorder()
        github: GitHubClient | None = None
        tavily: TavilyClient | None = None
        llm: LLMClient | None = None
        provider_event_index = 0
        try:
            if not self.settings.github_token:
                failure = RunFailure(
                    kind="provider",
                    stage="discovery",
                    exception_type="GitHubAuthenticationError",
                    message=(
                        "GitHub authentication is required. Configure GITHUB_TOKEN; "
                        "anonymous fallback is disabled."
                    ),
                    retryable=False,
                )
                trace.fail(failure)
                raise SearchRunFailed(
                    build_failure_artifact(query, usage, trace.build(), failure)
                )
            github = GitHubClient(
                self.settings.github_token,
                usage,
                request_limit=request_limit,
            )
            await github.validate_authentication()
            tavily = (
                TavilyClient(self.settings.tavily_api_key, usage)
                if self.settings.tavily_api_key
                else None
            )
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
            trace.begin("parse", {"query": 1})
            provider_event_index = len(usage.provider_events)
            if fixed_requirement is not None:
                if fixed_requirement.raw.strip() != query.strip():
                    raise ValueError("Fixed search plan does not match the current request")
                requirement = fixed_requirement
            else:
                spec = await SearchSpecParser().parse(query, llm)
                requirement = spec.to_requirement()
            self._finish_trace_stage(
                trace,
                {
                    "must_have": len(requirement.must_have_features),
                    "nice_to_have": len(requirement.nice_to_have_features),
                    "planned_queries": len(requirement.search_queries),
                    "fixed_plan": int(fixed_requirement is not None),
                },
                self._stage_provider_events(usage, provider_event_index, "parse"),
            )
            trace.begin("discovery", {"planned_queries": len(requirement.search_queries)})
            provider_event_index = len(usage.provider_events)
            candidates = await self._collect_candidates(requirement, github, tavily, usage)
            discovery_requests = usage.github_requests
            ranked = self._rank_candidates(requirement, candidates)
            readme_pool = self._evidence_hydration_pool(ranked)
            self._finish_trace_stage(
                trace,
                {"candidates": len(candidates), "requests": usage.github_requests},
                self._stage_provider_events(usage, provider_event_index, "discovery"),
            )
            trace.begin("evidence", {"deep_pool": len(readme_pool)})
            provider_event_index = len(usage.provider_events)
            await self._hydrate_repository_metadata(readme_pool, github)
            metadata_requests = usage.github_requests - discovery_requests
            await self._hydrate_readmes(readme_pool, github, usage)
            readme_requests = usage.github_requests - discovery_requests - metadata_requests
            reranked = self._rank_candidates(requirement, ranked)
            deep_pool_limit = self._deep_pool_limit()
            readme_pool_names = {repo.full_name.lower() for repo in readme_pool}
            verified_reranked = [
                repo for repo in reranked if repo.full_name.lower() in readme_pool_names
            ]
            deep_repos = self._evidence_hydration_pool(verified_reranked, deep_pool_limit)
            await self._hydrate_source_evidence(deep_repos, github, usage, requirement)
            source_requests = (
                usage.github_requests
                - discovery_requests
                - metadata_requests
                - readme_requests
            )
            deep_repos = self._rerank_by_evidence(deep_repos, requirement)
            self._finish_trace_stage(
                trace,
                {"coverage_items": sum(len(item.evidence_coverage) for item in deep_repos)},
                self._stage_provider_events(usage, provider_event_index, "evidence"),
            )
            trace.begin("analysis", {"deep_pool": len(deep_repos)})
            provider_event_index = len(usage.provider_events)
            analyses = await self._analyze_top_projects(requirement, deep_repos, llm)
            analyses, evidence_gate_stats = self._apply_evidence_gate(requirement, analyses, usage)
            analyzed_count = len(analyses)
            analyses = self._select_report_projects(requirement, analyses, usage)
            if len(analyses) < self.settings.max_deep_analyze_repos and len(candidates) > len(analyses):
                usage.warnings.append(
                    f"Returned {len(analyses)} project(s) because remaining candidates did not pass evidence tiering."
                )
            opportunity = ""
            self._finish_trace_stage(
                trace,
                {"analyses": len(analyses)},
                self._stage_provider_events(usage, provider_event_index, "analysis"),
            )
            trace.begin("report_delivery", {"projects": len(analyses)})
            provider_event_index = len(usage.provider_events)
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
            summary = self._write_summary(requirement, analyses)
            self._finish_trace_stage(
                trace,
                {"markdown": int(bool(report_markdown.strip()))},
                self._stage_provider_events(usage, provider_event_index, "report_delivery"),
            )
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
                        "metadata": metadata_requests,
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
                        self._candidate_trace_item(item, include_found_by=True)
                        for item in ranked[:15]
                    ],
                    "deep_pool_candidates": [
                        self._candidate_trace_item(item)
                        for item in deep_repos
                    ],
                    "score_semantics": {
                        "discovery_score": "Unbounded pre-analysis retrieval score used only to prioritize evidence collection.",
                        "topProjects.score": (
                            "Evidence-derived 0-100 relevance: verified requirement/component coverage weighted by "
                            "source strength, plus current-SearchSpec concept coverage; model self-scores and fixed "
                            "fallback floors are excluded."
                        ),
                    },
                    "core_requirement": self._core_requirement_feature(requirement),
                    "evidence_gate": evidence_gate_stats,
                    "low_confidence_filtered_count": max(0, analyzed_count - len(analyses)),
                    "low_confidence_candidate_count": analyzed_count,
                },
                run_trace=trace.build(),
            )
        except SearchRunFailed:
            raise
        except Exception as exc:
            stage = (
                "discovery"
                if isinstance(exc, GitHubProviderError) and not trace.active_name
                else trace.active_name or trace.next_name
            )
            self._stage_provider_events(usage, provider_event_index, stage)
            usage.elapsed_ms = int((time.perf_counter() - started) * 1000)
            failure = classify_failure(stage, exc)
            trace.fail(failure)
            raise SearchRunFailed(
                build_failure_artifact(query, usage, trace.build(), failure)
            ) from exc
        finally:
            if github:
                await github.close()
            if tavily:
                await tavily.close()
            if llm:
                await llm.close()

    @staticmethod
    def _stage_provider_events(
        usage: BudgetUsage,
        start: int,
        stage: str,
    ) -> list[ProviderEvent]:
        events = usage.provider_events[start:]
        for event in events:
            if not event.stage:
                event.stage = stage
        return events

    @staticmethod
    def _finish_trace_stage(
        trace: RunTraceRecorder,
        outputs: dict[str, int],
        provider_events: list[ProviderEvent],
    ) -> None:
        if not provider_events:
            trace.complete(outputs)
            return
        notes = list(
            dict.fromkeys(
                f"{event.provider}:{event.outcome}:{event.kind}"
                for event in provider_events
            )
        )
        trace.partial(outputs, notes)

    def _budgeted_github_limit(self) -> int:
        return self.settings.max_github_requests

    @staticmethod
    def _candidate_trace_item(
        repo: CandidateRepository,
        *,
        include_found_by: bool = False,
    ) -> dict[str, object]:
        item: dict[str, object] = {
            "repo": repo.full_name,
            "discovery_score": round(repo.raw_score, 1),
            "score_basis": "pre_analysis_retrieval",
            "core_signal": repo.core_signal_score,
        }
        if include_found_by:
            item["found_by"] = repo.found_by[:4]
        return item

    def _budgeted_candidate_limit(self) -> int:
        return self.settings.max_candidates

    def _deep_pool_limit(self) -> int:
        return 20

    def _evidence_hydration_pool(
        self,
        ranked: list[CandidateRepository],
        limit: int | None = None,
    ) -> list[CandidateRepository]:
        """Reserve strongest candidates, then cover executed discovery angles.

        Ranking before README retrieval is necessarily based on sparse metadata.  A
        single broad query must therefore not consume the entire evidence budget and
        prevent the other SearchSpec-planned discovery angles from being checked. At
        the same time, source diversity must not displace every globally strong
        candidate. This only chooses verification work; it is not capability evidence
        or a score boost.
        """
        target = limit if limit is not None else self._deep_pool_limit() + 2
        if target <= 0:
            return []
        selected: list[CandidateRepository] = []
        selected_names: set[str] = set()
        source_counts: dict[str, int] = {}

        def select(repo: CandidateRepository) -> None:
            selected.append(repo)
            selected_names.add(repo.full_name.lower())
            for source in {str(value).strip() for value in repo.found_by if str(value).strip()}:
                source_counts[source] = source_counts.get(source, 0) + 1

        strongest_count = min(len(ranked), max(1, target // 2))
        globally_strongest = sorted(
            ranked,
            key=lambda repo: (repo.raw_score, repo.core_signal_score),
            reverse=True,
        )
        for repo in globally_strongest[:strongest_count]:
            select(repo)
        if len(selected) >= target:
            return selected

        # Two rounds protect against a broad first result from a useful angle
        # masking its next-best candidate, while still leaving capacity for the
        # strongest overall metadata matches.
        for per_source_limit in (1, 2):
            for repo in ranked:
                sources = {str(source).strip() for source in repo.found_by if str(source).strip()}
                if (
                    repo.full_name.lower() in selected_names
                    or not sources
                    or not any(source_counts.get(source, 0) < per_source_limit for source in sources)
                ):
                    continue
                select(repo)
                if len(selected) >= target:
                    return selected

        for repo in ranked:
            if repo.full_name.lower() in selected_names:
                continue
            select(repo)
            if len(selected) >= target:
                break
        return selected

    def _evidence_request_reserve(self) -> int:
        """Requests discovery cannot borrow: README plus focused checks for final candidates."""
        result_count = self.settings.max_deep_analyze_repos
        readme_count = self._deep_pool_limit() + 2
        files_per_repo = 2
        metadata_count = readme_count
        return metadata_count + readme_count + result_count * (1 + files_per_repo)

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
        issue_queries = self._planned_issue_search_queries(requirement)
        queries_per_wave = 2
        request_limit = self._budgeted_github_limit()
        evidence_reserve = self._evidence_request_reserve()
        search_request_limit = max(8, request_limit - evidence_reserve)
        repo_per_page = 20
        code_per_page = 10
        topic_per_page = 20
        issue_per_page = 20

        max_github_queries = max(
            len(repo_queries), len(code_queries), len(topic_queries), len(issue_queries)
        )
        wave_count = math.ceil(max_github_queries / queries_per_wave)
        for wave_index in range(wave_count):
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
            )

        if tavily:
            planned_web_queries = requirement.web_search_queries or requirement.search_queries
            web_limit = QUERY_CHANNEL_LIMITS["web_search_queries"]
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
                    repo = CandidateRepository(
                        owner=owner,
                        name=name,
                        url=f"https://github.com/{owner}/{name}",
                        found_by=[f"tavily:{search_query}"],
                    )
                    self._merge_repo(repos, repo)
        return self._rank_candidates(requirement, list(repos.values()))[:candidate_limit]

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
        # Merge channel results round-robin. A broad repository query must not
        # consume the full candidate budget before code, topic, or issue
        # evidence gets a chance to contribute.
        for index in range(max((len(batch) for batch in results), default=0)):
            for batch in results:
                if index >= len(batch):
                    continue
                self._merge_repo(repos, batch[index])

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
            results = await github.search_repositories(gh_query, per_page=per_page)
            candidates.extend(results)
            for repo in results:
                self._cache_repository_metadata(repo)
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
                repo = CandidateRepository(
                    owner=owner,
                    name=name,
                    url=f"https://github.com/{owner}/{name}",
                    found_by=[found_by],
                    file_paths=[path] if path else [],
                )
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
                results = await github.search_topic_repositories(topic_query, per_page=per_page)
                candidates.extend(results)
                for repo in results:
                    self._cache_repository_metadata(repo)
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
                repo = CandidateRepository(
                    owner=owner,
                    name=name,
                    url=f"https://github.com/{owner}/{name}",
                    found_by=[f"github_issue:{search_query}"],
                )
                candidates.append(repo)
        return candidates

    def _planned_code_search_queries(self, requirement: Requirement) -> list[str]:
        limit = QUERY_CHANNEL_LIMITS["code_search_queries"]
        queries: list[str] = []
        for phrase in self._merge_queries(requirement.code_search_queries, limit=limit):
            token = self._github_search_token(phrase)
            if token:
                queries.append(f"{token} in:file,path")
        return self._merge_queries(queries, limit=limit)

    def _planned_repo_search_queries(
        self,
        requirement: Requirement,
    ) -> list[str]:
        limit = QUERY_CHANNEL_LIMITS["repo_search_queries"]
        return self._merge_queries(requirement.repo_search_queries, limit=limit)

    def _planned_topic_search_queries(
        self,
        requirement: Requirement,
    ) -> list[str]:
        limit = QUERY_CHANNEL_LIMITS["topic_search_queries"]
        return self._merge_queries(requirement.topic_search_queries, limit=limit)

    def _planned_issue_search_queries(
        self,
        requirement: Requirement,
    ) -> list[str]:
        limit = QUERY_CHANNEL_LIMITS["issue_search_queries"]
        return self._merge_queries(requirement.issue_search_queries, limit=limit)

    def _github_search_token(self, phrase: str) -> str:
        clean = re.sub(r"\s+", " ", str(phrase).strip())
        if not clean:
            return ""
        if any(char.isspace() for char in clean):
            return f'"{clean[:80]}"'
        return clean[:80]

    def _to_github_repo_query(self, query: str) -> str:
        clean = re.sub(r"\s+", " ", str(query or "")).strip()[:180]
        if not clean:
            return ""
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
            self._merge_repository_metadata(repos[key], repo)
            return
        repos[key] = repo
        if key in self._repository_metadata_cache:
            self._merge_repository_metadata(repo, self._repository_metadata_cache[key])

    def _cache_repository_metadata(self, repo: CandidateRepository) -> CandidateRepository:
        key = repo.full_name.lower()
        existing = self._repository_metadata_cache.get(key)
        if existing is None:
            self._repository_metadata_cache[key] = repo
            return repo
        for source in repo.found_by:
            if source not in existing.found_by:
                existing.found_by.append(source)
        return existing

    async def _get_repository_cached(
        self,
        github: GitHubClient,
        owner: str,
        name: str,
        *,
        found_by: str,
    ) -> CandidateRepository | None:
        key = f"{owner.lower()}/{name.lower()}"
        cached = self._repository_metadata_cache.get(key)
        if cached is not None:
            if found_by not in cached.found_by:
                cached.found_by.append(found_by)
            return cached
        repo = await github.get_repository(owner, name, found_by=found_by)
        return self._cache_repository_metadata(repo) if repo else None

    async def _hydrate_repository_metadata(
        self,
        repos: Sequence[CandidateRepository],
        github: GitHubClient,
    ) -> None:
        for repo in repos:
            full = await self._get_repository_cached(
                github,
                repo.owner,
                repo.name,
                found_by=repo.found_by[0] if repo.found_by else "github:evidence_pool",
            )
            if full is None:
                continue
            self._merge_repository_metadata(repo, full)
            self._repository_metadata_cache[repo.full_name.lower()] = repo

    @staticmethod
    def _merge_repository_metadata(
        target: CandidateRepository,
        source: CandidateRepository,
    ) -> None:
        for found_by in source.found_by:
            if found_by not in target.found_by:
                target.found_by.append(found_by)
        for path in source.file_paths:
            if path not in target.file_paths:
                target.file_paths.append(path)
        target.url = source.url or target.url
        target.description = source.description or target.description
        target.stars = max(target.stars, source.stars)
        target.forks = max(target.forks, source.forks)
        target.language = source.language or target.language
        target.topics = list(OrderedDict.fromkeys([*target.topics, *source.topics]))
        target.last_pushed_at = source.last_pushed_at or target.last_pushed_at
        target.license = source.license or target.license
        target.default_branch = source.default_branch or target.default_branch

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
        domain_aliases = self._requirement_domain_aliases(requirement)
        desired_languages = self._requirement_language_constraints(requirement, candidates)
        component_counts: dict[str, int] = {}
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
            component_counts[repo.full_name.lower()] = sum(
                len(item.component_evidence)
                for item in self._build_evidence_coverage(repo, requirement)
            )
        return sorted(
            candidates,
            key=lambda item: (component_counts.get(item.full_name.lower(), 0), item.raw_score),
            reverse=True,
        )

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
        domain_aliases = self._requirement_domain_aliases(requirement)
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
        external_links = len(re.findall(r"https?://", readme, flags=re.IGNORECASE))
        if len(readme) >= 80_000 and external_links >= 50:
            return True
        if len(readme) >= 20_000:
            github_links = len(re.findall(r"https?://github\.com/[^)\s]+", readme, flags=re.IGNORECASE))
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
            component_groups = self._feature_evidence_components(feature, requirement)
            if component_groups:
                coverage.append(
                    self._build_component_evidence_coverage(
                        repo,
                        requirement,
                        feature,
                        component_groups,
                        is_catalog=is_catalog,
                        capability_readme=capability_readme,
                    )
                )
                continue
            aliases = self._feature_aliases(feature, requirement)
            if not aliases:
                continue
            public_description = " ".join([repo.description, " ".join(repo.topics)])
            readme_hits = [] if is_catalog else self._matching_feature_terms(feature, capability_readme, aliases)
            description_hits = [] if is_catalog else self._matching_feature_terms(feature, public_description, aliases)
            path_evidence: list[str] = []
            path_hits: list[tuple[str, list[str]]] = []
            for path in ([] if is_catalog else repo.file_paths):
                if not self._path_can_prove_capability(path):
                    continue
                hits = self._matching_feature_terms(feature, path, aliases)
                if hits:
                    path_evidence.append(f"{path} ({', '.join(hits[:3])})")
                    path_hits.append((path, hits))
                if len(path_evidence) >= 5:
                    break
            source_evidence: list[str] = []
            source_hits: list[tuple[str, str, list[str]]] = []
            for path, text in ({} if is_catalog else repo.key_files).items():
                evidence_text = self._readme_capability_text(text) if path.lower().endswith((".md", ".mdx")) else text
                hits = self._matching_feature_terms(feature, evidence_text, aliases)
                if hits:
                    source_evidence.append(f"{path} ({', '.join(hits[:3])})")
                    source_hits.append((path, text, hits))
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
            evidence_references: list[EvidenceReference] = []
            if description_hits:
                evidence_references.append(
                    self._evidence_reference_from_text(
                        "repository_metadata",
                        "description/topics",
                        public_description,
                        description_hits,
                    )
                )
            if readme_hits and readme_hits != ["核心平台、操作和对象在同一说明中得到确认"]:
                evidence_references.append(
                    self._evidence_reference_from_text("readme", "README", repo.readme, readme_hits)
                )
            evidence_references.extend(
                self._evidence_reference_from_text("path", path, path, hits)
                for path, hits in path_hits
            )
            evidence_references.extend(
                self._evidence_reference_from_text("source", path, text, hits)
                for path, text, hits in source_hits
            )
            if not evidence_references and repo.readme:
                # Unknown remains unknown. This only shows a reviewer which local
                # material was examined when no current-request alias was found.
                evidence_references.append(
                    self._evidence_reference_from_text("readme", "README", repo.readme, [])
                )
            if not evidence_references:
                # A candidate identity is an audit observation, not capability
                # proof. Empty aliases keep that distinction explicit when
                # collection produced no README, path, or source material.
                evidence_references.append(
                    self._evidence_reference_from_text(
                        "repository_metadata",
                        "repository identity",
                        "\n".join(item for item in [repo.full_name, repo.description] if item),
                        [],
                    )
                )
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
                    evidence_references=evidence_references[:8],
                )
            )
        return coverage

    def _feature_evidence_components(
        self,
        feature: str,
        requirement: Requirement,
    ) -> dict[str, list[str]]:
        for key, groups in (requirement.evidence_components or {}).items():
            if not self._same_feature_key(feature, key) or not isinstance(groups, dict):
                continue
            return {
                str(label).strip(): [
                    str(alias).strip()
                    for alias in aliases
                    if str(alias).strip()
                ]
                for label, aliases in groups.items()
                if str(label).strip() and isinstance(aliases, list)
            }
        return {}

    def _build_component_evidence_coverage(
        self,
        repo: CandidateRepository,
        requirement: Requirement,
        feature: str,
        component_groups: dict[str, list[str]],
        *,
        is_catalog: bool,
        capability_readme: str,
    ) -> EvidenceCoverage:
        component_evidence: dict[str, list[str]] = {
            label: [] for label in component_groups
        }
        full_readme_evidence: list[str] = []
        full_source_evidence: list[str] = []
        full_path_evidence: list[str] = []
        full_references: list[EvidenceReference] = []
        if not is_catalog:
            materials: list[tuple[str, str, str]] = [
                ("repository_metadata", "仓库简介", repo.description),
                ("readme", "README", capability_readme),
            ]
            materials.extend(("path", path, path) for path in repo.file_paths if self._path_can_prove_capability(path))
            materials.extend(
                (
                    "source",
                    path,
                    self._readme_capability_text(text) if path.lower().endswith((".md", ".mdx")) else text,
                )
                for path, text in repo.key_files.items()
                if text
            )
            for source_type, source_name, text in materials:
                if not text:
                    continue
                for window in self._local_evidence_windows(text):
                    matched_labels = [
                        label
                        for label, aliases in component_groups.items()
                        if any(
                            self._literal_alias_present(
                                str(alias).strip().lower(),
                                window.lower(),
                            )
                            for alias in aliases
                        )
                    ]
                    if not matched_labels:
                        continue
                    snippet = compact_text(window, 320)
                    rendered = f"{source_name}: {snippet}"
                    for label in matched_labels:
                        if rendered not in component_evidence[label]:
                            component_evidence[label].append(rendered)
                    if len(matched_labels) != len(component_groups):
                        continue
                    if source_type == "source" and rendered not in full_source_evidence:
                        full_source_evidence.append(rendered)
                    elif source_type == "path" and rendered not in full_path_evidence:
                        full_path_evidence.append(rendered)
                    elif rendered not in full_readme_evidence:
                        full_readme_evidence.append(rendered)
                    reference = self._evidence_reference_from_text(
                        source_type,
                        source_name,
                        text,
                        [
                            alias
                            for aliases in component_groups.values()
                            for alias in aliases
                            if self._literal_alias_present(alias.lower(), window.lower())
                        ],
                    )
                    if reference not in full_references:
                        full_references.append(reference)

        component_evidence = {
            label: snippets[:4]
            for label, snippets in component_evidence.items()
            if snippets
        }
        covered = bool(full_readme_evidence or full_source_evidence or full_path_evidence)
        if not full_references and repo.readme:
            full_references.append(
                self._evidence_reference_from_text("readme", "README", repo.readme, [])
            )
        if not full_references:
            full_references.append(
                self._evidence_reference_from_text(
                    "repository_metadata",
                    "repository identity",
                    "\n".join(item for item in [repo.full_name, repo.description] if item),
                    [],
                )
            )
        return EvidenceCoverage(
            feature=feature,
            covered=covered,
            status="supported" if covered else "unknown",
            readme_evidence=full_readme_evidence[:5],
            source_evidence=full_source_evidence[:5],
            path_evidence=full_path_evidence[:5],
            unknown_reason="" if covered else "公开说明中暂未确认全部必需证据组件",
            component_evidence=component_evidence,
            required_component_count=len(component_groups),
            evidence_references=full_references[:8],
        )

    @staticmethod
    def _evidence_reference_from_text(
        kind: Literal["repository_metadata", "readme", "path", "source"],
        locator: str,
        text: str,
        aliases: Sequence[str],
    ) -> EvidenceReference:
        """Attach current-request aliases to their nearest repository-local text."""
        normalized_aliases = list(OrderedDict.fromkeys(alias for alias in aliases if alias))
        lines = str(text or "").splitlines()
        for index, line in enumerate(lines):
            line_aliases = [
                alias
                for alias in normalized_aliases
                if DeepSearchEngine._literal_alias_present(alias.lower(), line.lower())
            ]
            if line_aliases:
                excerpt = "\n".join(lines[max(0, index - 1) : index + 2])
                return EvidenceReference(
                    kind=kind,
                    locator=locator,
                    excerpt=compact_text(excerpt, 320),
                    matched_aliases=line_aliases,
                    line_start=index + 1,
                    line_end=index + 1,
                )
        return EvidenceReference(
            kind=kind,
            locator=locator,
            excerpt=compact_text(text, 320),
            matched_aliases=normalized_aliases,
        )

    @staticmethod
    def _local_evidence_windows(text: str) -> list[str]:
        statements = [
            item.strip()
            for item in re.split(r"[\r\n。！？!?；;]+", str(text or ""))
            if item.strip()
        ]
        windows: list[str] = []
        for index, statement in enumerate(statements):
            if len(statement) > 420:
                windows.extend(
                    statement[start : start + 360]
                    for start in range(0, len(statement), 260)
                )
                continue
            windows.append(statement)
            if index + 1 < len(statements) and len(statements[index + 1]) <= 420:
                windows.append(f"{statement} {statements[index + 1]}")
        return list(OrderedDict.fromkeys(windows))

    def _core_evidence_is_compositional(
        self,
        requirement: Requirement,
        repo: CandidateRepository,
        *,
        require_all_groups: bool = False,
    ) -> bool:
        concepts = requirement.feature_concepts or {}
        group_names = ["domains", "actions", "objects"]
        if require_all_groups and concepts.get("interfaces"):
            group_names.append("interfaces")
        groups = {
            group: self._clean_feature_aliases({str(item) for item in concepts.get(group, [])})
            for group in group_names
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
                if require_all_groups:
                    return True
                return len(overlap) >= overlap_threshold
            if require_all_groups:
                continue
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
        if item.required_component_count > 0 and item.component_evidence:
            component_ratio = min(
                1.0,
                len(item.component_evidence) / item.required_component_count,
            )
            reference_quality = max(
                (
                    1.0
                    if reference.kind == "source"
                    else 0.95
                    if reference.kind == "path"
                    else 0.90
                    if reference.kind in {"readme", "repository_metadata"}
                    else 0.70
                    for reference in item.evidence_references
                ),
                default=0.70,
            )
            return component_ratio * reference_quality
        return 0.0

    def _verified_match_score(
        self,
        requirement: Requirement,
        repo: CandidateRepository,
        coverage: Sequence[EvidenceCoverage],
        adjacent_evidence: AdjacentEvidence | None,
    ) -> int:
        core_feature = self._core_requirement_feature(requirement)
        weights = [
            2.0 if core_feature and self._same_feature_key(item.feature, core_feature) else 1.0
            for item in coverage
        ]
        total_weight = sum(weights)
        evidence_fit = (
            sum(
                weight * self._requirement_evidence_strength(requirement, item)
                for item, weight in zip(coverage, weights)
            )
            / total_weight
            if total_weight
            else 0.0
        )
        concept_groups = self._requirement_concept_groups(requirement)
        concept_fit, _ = self._semantic_coverage(
            concept_groups,
            " ".join([repo.name, repo.description, " ".join(repo.topics)]),
            " ".join(
                [
                    self._readme_capability_text(repo.readme),
                    " ".join(repo.file_paths[:80]),
                    " ".join(repo.key_files.values()),
                ]
            ),
        )
        score = (
            evidence_fit * 80 + concept_fit * 20
            if evidence_fit > 0 and concept_groups
            else adjacent_evidence.relevance_score
            if adjacent_evidence is not None
            else evidence_fit * 100
        )
        return max(0, min(100, round(score)))

    def _build_adjacent_evidence(
        self,
        requirement: Requirement,
        repo: CandidateRepository,
    ) -> AdjacentEvidence | None:
        candidates = self._adjacent_evidence_candidates(requirement, repo, limit=1)
        return candidates[0] if candidates else None

    def _adjacent_evidence_candidates(
        self,
        requirement: Requirement,
        repo: CandidateRepository,
        *,
        limit: int = 3,
    ) -> list[AdjacentEvidence]:
        if self._is_catalog_repository(repo):
            return []
        groups = self._adjacent_concept_groups(requirement)
        if not groups or "actions" not in groups:
            return []
        materials: list[
            tuple[Literal["repository_metadata", "readme", "source"], str, str]
        ] = [
            (
                "repository_metadata",
                "description",
                repo.description.strip(),
            ),
            ("readme", "README", self._readme_capability_text(repo.readme)),
        ]
        materials.extend(
            (
                "source",
                path,
                self._readme_capability_text(text)
                if path.lower().endswith((".md", ".mdx"))
                else text,
            )
            for path, text in repo.key_files.items()
        )
        candidates: list[AdjacentEvidence] = []
        for kind, locator, material in materials:
            if not material:
                continue
            for window in self._local_evidence_windows(material):
                if self._window_is_external_reference_list(window):
                    continue
                group_matches = {
                    name: self._literal_matching_terms(window, aliases)
                    for name, aliases in groups.items()
                }
                if not group_matches.get("actions"):
                    continue
                if "domains" in groups and not group_matches.get("domains"):
                    continue
                if "objects" in groups and not group_matches.get("objects"):
                    continue
                matches = [
                    term
                    for terms in group_matches.values()
                    for term in terms
                ]
                candidates.append(
                    AdjacentEvidence(
                        reference=EvidenceReference(
                            kind=kind,
                            locator=locator,
                            excerpt=compact_text(window, 320),
                            matched_aliases=list(OrderedDict.fromkeys(matches)),
                        ),
                        group_matches=group_matches,
                        relevance_score=self._adjacent_relevance_score(group_matches, kind),
                        capability=self._adjacent_capability(requirement, group_matches),
                    )
                )
        ordered = sorted(
            candidates,
            key=lambda item: (
                item.relevance_score,
                sum(len(matches) for matches in item.group_matches.values()),
                -len(item.reference.excerpt),
            ),
            reverse=True,
        )
        selected: list[AdjacentEvidence] = []
        seen: set[tuple[str, str]] = set()
        selected_content: list[tuple[str, str]] = []
        for item in ordered:
            key = (item.reference.locator, item.reference.excerpt.casefold())
            if key in seen:
                continue
            content_key = re.sub(
                r"[^a-z0-9\u4e00-\u9fff]+",
                "",
                item.reference.excerpt.casefold(),
            )
            if content_key and any(
                item.reference.locator == locator
                and (
                    content_key in existing
                    or existing in content_key
                    or SequenceMatcher(None, content_key, existing).ratio() >= 0.65
                )
                for locator, existing in selected_content
            ):
                continue
            selected.append(item)
            seen.add(key)
            if content_key:
                selected_content.append((item.reference.locator, content_key))
            if len(selected) >= limit:
                break
        return selected

    def _literal_matching_terms(self, text: str, aliases: set[str]) -> list[str]:
        lowered = str(text or "").lower()
        return sorted(
            (alias for alias in aliases if self._literal_alias_present(alias, lowered)),
            key=lambda item: (len(item), item),
            reverse=True,
        )

    @staticmethod
    def _window_is_external_reference_list(window: str) -> bool:
        text = str(window or "")
        links = {
            target
            for target in re.findall(r"\[[^\]]+\]\((https?://[^)]+)\)", text)
        }
        return len(links) >= 2 or bool(
            links and re.match(r"^\s*[-*+]\s*\[", text)
        )

    @staticmethod
    def _adjacent_relevance_score(
        group_matches: dict[str, list[str]],
        source_kind: str,
    ) -> int:
        group_weights = {"domains": 1.2, "actions": 1.4, "objects": 1.4}
        weighted_strength = 0.0
        total_weight = 0.0
        for name, weight in group_weights.items():
            matches = group_matches.get(name, [])
            if not matches:
                continue
            # Passing the local domain/action/object gate establishes adjacency.
            # Additional current-request matches increase confidence smoothly
            # without requiring a product-specific keyword or a fixed score floor.
            depth = len(matches) / (len(matches) + 1)
            weighted_strength += weight * (0.6 + 0.4 * depth)
            total_weight += weight
        if not total_weight:
            return 0
        source_strength = {
            "source": 1.0,
            "readme": 0.92,
            "repository_metadata": 0.82,
        }.get(source_kind, 0.70)
        return max(
            0,
            min(40, round(40 * source_strength * weighted_strength / total_weight)),
        )

    def _adjacent_capability(
        self,
        requirement: Requirement,
        group_matches: dict[str, list[str]],
    ) -> str:
        concepts = requirement.feature_concepts or {}
        selected = {
            name: self._preferred_report_term(
                matches,
                requirement.report_language,
                [str(value) for value in concepts.get(name, [])],
            )
            for name, matches in group_matches.items()
        }
        domain = selected.get("domains", "")
        action = selected.get("actions", "")
        object_ = selected.get("objects", "")
        if not action or not object_:
            return ""
        if requirement.report_language == "en":
            return f"{action} {object_}" + (f" for {domain}" if domain else "")
        action_object = (
            f"{action}{object_}"
            if re.search(r"[\u4e00-\u9fff]", action + object_)
            else f"{action} {object_}"
        )
        return f"{domain}：{action_object}" if domain else action_object

    def _preferred_report_term(
        self,
        terms: Sequence[str],
        language: str,
        original_terms: Sequence[str],
    ) -> str:
        original_by_key = {
            str(term).strip().casefold(): str(term).strip()
            for term in original_terms
            if str(term).strip()
        }
        values = list(
            OrderedDict.fromkeys(
                original_by_key.get(str(term).strip().casefold(), str(term).strip())
                for term in terms
                if str(term).strip()
            )
        )
        if not values:
            return ""
        language_matches = [
            term
            for term in values
            if bool(re.search(r"[\u4e00-\u9fff]", term)) == (language == "zh")
        ]
        pool = language_matches or values
        return max(pool, key=lambda term: (len(self._semantic_signals(term)), len(term), term))

    def _adjacent_concept_groups(self, requirement: Requirement) -> dict[str, set[str]]:
        concepts = requirement.feature_concepts or {}
        core_feature = self._core_requirement_feature(requirement) or ""
        core_parts = [core_feature]
        core_components: dict[str, list[str]] = {}
        for feature, aliases in (requirement.evidence_aliases or {}).items():
            if self._same_feature_key(feature, core_feature):
                core_parts.extend(str(alias) for alias in aliases)
        for feature, components in (requirement.evidence_components or {}).items():
            if not self._same_feature_key(feature, core_feature):
                continue
            core_components = components
            for label, aliases in components.items():
                core_parts.append(str(label))
                core_parts.extend(str(alias) for alias in aliases)
        core_context = " ".join(core_parts)

        def current_request_aliases(group: str) -> set[str]:
            values = self._clean_feature_aliases(
                {str(item) for item in concepts.get(group, [])}
            )
            core_values = {
                value
                for value in values
                if self._matching_terms(core_context, {value})
            }
            expanded = set(core_values)
            for label, phrases in core_components.items():
                phrase_aliases = self._clean_feature_aliases(
                    {str(phrase) for phrase in phrases}
                )
                component_text = " ".join([str(label), *phrase_aliases])
                if core_values and self._matching_terms(component_text, core_values):
                    expanded.update(phrase_aliases)
            return expanded

        groups = {
            group: current_request_aliases(group)
            for group in ("domains", "actions", "objects")
        }
        return {name: aliases for name, aliases in groups.items() if aliases}

    def _requirement_evidence_strength(
        self,
        requirement: Requirement,
        item: EvidenceCoverage,
    ) -> float:
        if item.status == "supported" or item.covered or not item.component_evidence:
            return self._evidence_strength(item)
        component_groups = self._feature_evidence_components(item.feature, requirement)
        if not component_groups:
            return self._evidence_strength(item)
        concept_weights = {
            "domains": 1.6,
            "actions": 1.25,
            "objects": 1.35,
            "outputs": 1.1,
            "interfaces": 0.7,
        }
        concepts = requirement.feature_concepts or {}
        weights: dict[str, float] = {}
        for label, aliases in component_groups.items():
            component_text = " ".join([label, *aliases])
            weight = sum(
                concept_weights[group]
                for group in concept_weights
                if concepts.get(group)
                and self._matching_terms(
                    component_text,
                    self._clean_feature_aliases({str(value) for value in concepts[group]}),
                )
            )
            weights[label] = weight or 1.0
        total = sum(weights.values())
        matched = sum(weights.get(label, 0.0) for label in item.component_evidence)
        if total <= 0 or matched <= 0:
            return 0.0
        references = item.evidence_references
        source_quality = max(
            (
                1.0
                if reference.kind == "source"
                else 0.95
                if reference.kind == "path"
                else 0.90
                if reference.kind in {"readme", "repository_metadata"}
                else 0.70
                for reference in references
            ),
            default=0.70,
        )
        return min(1.0, matched / total) * source_quality

    def _evidence_score(
        self,
        coverage: list[EvidenceCoverage],
        requirement: Requirement | None = None,
    ) -> float:
        if not coverage:
            return 0.0
        weights = [1.0 for _ in coverage]
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
        for feature in [*requirement.must_have_features, *requirement.nice_to_have_features]:
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
        left_norm = self._normalized_feature_key(left)
        right_norm = self._normalized_feature_key(right)
        return bool(left_norm and right_norm and left_norm == right_norm)

    @staticmethod
    def _normalized_feature_key(value: str) -> str:
        return re.sub(r"\s+", " ", str(value).strip().casefold())

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
        lowered_text = (text or "").lower()
        exact = [
            alias
            for alias in self._matching_terms(text, aliases)
            if self._literal_alias_present(alias, lowered_text)
        ]
        if not exact:
            return []
        combined = {
            signal for alias in exact for signal in self._semantic_signals(alias)
        }
        named_entities = self._named_entity_tokens(feature)
        if named_entities and named_entities.issubset(combined):
            return exact
        if any(self._same_feature_key(feature, alias) for alias in exact):
            return exact
        if len(exact) >= 2 and len(combined) >= 2:
            return exact
        feature_signals = self._semantic_signals(feature)
        return [
            alias
            for alias in exact
            if len(self._semantic_signals(alias)) >= 2
            and len(feature_signals) <= max(3, len(self._semantic_signals(alias)) * 2)
        ]

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

    def _requirement_domain_aliases(self, requirement: Requirement) -> set[str]:
        return self._clean_feature_aliases(
            {str(item) for item in (requirement.feature_concepts or {}).get("domains", [])}
        )

    async def _analyze_top_projects(
        self,
        requirement: Requirement,
        repos: list[CandidateRepository],
        llm: LLMClient | None,
    ) -> list[ProjectAnalysis]:
        batch_size = 5
        if llm and len(repos) > batch_size:
            batches = [repos[index : index + batch_size] for index in range(0, len(repos), batch_size)]
            analyzed_batches = await asyncio.gather(
                *(self._analyze_top_projects(requirement, batch, llm) for batch in batches)
            )
            return sorted(
                [analysis for batch in analyzed_batches for analysis in batch],
                key=lambda item: item.match_score,
                reverse=True,
            )
        if llm and repos:
            payload = {
                "requirement": {
                    "raw": requirement.raw,
                    "must_have_features": requirement.must_have_features,
                    "nice_to_have_features": requirement.nice_to_have_features,
                    "target_platforms": requirement.target_platforms,
                    "feature_concepts": requirement.feature_concepts,
                    "evidence_components": requirement.evidence_components,
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
                                "component_evidence": item.component_evidence,
                                "required_component_count": item.required_component_count,
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
                    '"required_changes":[],"risks":[],"evidence":[],"component_citations":[],'
                    '"difference_citations":[]}]}.\n'
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
                    "extra capabilities as differences. component_citations contains feature, component, locator, "
                    "and excerpt copied exactly from supplied README or key-file content. One local excerpt must "
                    "support every component of a compound feature before it is covered. Still emit a citation for "
                    "each individually supported component when the other components remain unknown. Every "
                    "different_features finding must have a difference_citations entry containing the exact feature, "
                    "affected component when applicable, finding, locator, and a local excerpt copied exactly from "
                    "supplied README or key-file content.\n"
                    f"{json.dumps(payload, ensure_ascii=False)}"
                ),
                operation="repository_analysis",
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
                    coverage = self._apply_verified_component_citations(
                        repo, requirement, repo.evidence_coverage, item.get("component_citations")
                    )
                    reported_differences = [str(x) for x in item.get("different_features", [])][:8]
                    coverage, verified_differences = self._apply_verified_difference_citations(
                        repo,
                        requirement,
                        coverage,
                        reported_differences,
                        item.get("difference_citations"),
                    )
                    analyses.append(
                        ProjectAnalysis(
                            repo=repo,
                            match_score=int(item.get("match_score") or min(100, repo.raw_score)),
                            recommendation=str(item.get("recommendation") or "需要人工复核"),
                            directly_usable=bool(item.get("directly_usable")),
                            covered_features=[str(x) for x in item.get("covered_features", [])][:8],
                            different_features=verified_differences,
                            unknown_features=[str(x) for x in item.get("unknown_features", [])][:8],
                            missing_features=[str(x) for x in item.get("missing_features", [])][:8],
                            required_changes=[str(x) for x in item.get("required_changes", [])][:8],
                            risks=[str(x) for x in item.get("risks", [])][:8],
                            evidence=evidence[:6],
                            evidence_coverage=coverage,
                        )
                    )
                if analyses:
                    focused_capabilities = await self._review_adjacent_capabilities(
                        requirement,
                        [analysis.repo for analysis in analyses],
                        llm,
                    )
                    for analysis in analyses:
                        reviewed = focused_capabilities.get(analysis.repo.full_name.lower())
                        if reviewed is None:
                            continue
                        analysis.verified_capabilities, analysis.capability_evidence = reviewed
                        analysis.capability_citations_reviewed = True
                    analyzed_names = {item.repo.full_name.lower() for item in analyses}
                    analyses.extend(
                        self._heuristic_analysis(requirement, repo)
                        for repo in repos
                        if repo.full_name.lower() not in analyzed_names
                    )
                    return sorted(analyses, key=lambda item: item.match_score, reverse=True)
        return [self._heuristic_analysis(requirement, repo) for repo in repos]

    async def _review_adjacent_capabilities(
        self,
        requirement: Requirement,
        repos: Sequence[CandidateRepository],
        llm: LLMClient,
    ) -> dict[str, tuple[list[str], list[EvidenceReference]]]:
        """Produce adjacent public capabilities from bounded repository-local excerpts."""
        candidates: list[dict[str, object]] = []
        by_id: dict[str, tuple[str, AdjacentEvidence]] = {}
        repo_keys: set[str] = set()
        for repo in repos:
            adjacent_items = self._adjacent_evidence_candidates(requirement, repo)
            if not adjacent_items:
                continue
            key = repo.full_name.lower()
            repo_keys.add(key)
            for index, adjacent in enumerate(adjacent_items):
                candidate_id = f"{repo.full_name}#{index}"
                by_id[candidate_id.casefold()] = (key, adjacent)
                candidates.append(
                    {
                        "id": candidate_id,
                        "repo": repo.full_name,
                        "locator": adjacent.reference.locator,
                        "excerpt": adjacent.reference.excerpt,
                    }
                )
        if not candidates:
            return {}
        reviewed_items: list[dict[str, object]] = []
        pending = candidates
        batch_size = 10
        for _attempt in range(2):
            missing: list[dict[str, object]] = []
            for offset in range(0, len(pending), batch_size):
                batch = pending[offset : offset + batch_size]
                batch_ids = {str(item["id"]).casefold() for item in batch}
                data = await llm.json_chat(
                    "You are a repository-evidence verifier. Return JSON only.",
                    (
                        "For every supplied evidence item, decide whether its excerpt describes a runtime capability of that "
                        "repository itself. Return "
                        '{"evidence":[{"id":"exact input id","supported":true/false,'
                        '"capabilities":["exact phrase from excerpt"]}]}. '
                        "A supported capability must describe behavior users can execute or configure in this repository. "
                        "Reject release notes about search phrases, tags, keywords, metadata, examples, planned work, and "
                        "external project lists. When supported is true, return up to three relevant capabilities and include "
                        "all relevant runtime behaviors stated in the excerpt. Each capability must be a concise exact contiguous "
                        "phrase copied from excerpt (maximum 120 characters). Prefer the most specific phrase that preserves a "
                        "concrete object, condition, example, scope, or threshold relevant to the request; do not reduce it to a "
                        "broad action when the same excerpt supplies that useful detail. If the excerpt states a limitation or unavailable context for a capability, "
                        "the exact phrase must retain that limitation instead of shortening it to an unconditional capability. "
                        "Include exactly one result for every input evidence id.\n"
                        + json.dumps(
                            {"requirement": requirement.raw, "evidence": batch},
                            ensure_ascii=False,
                        )
                    ),
                    operation="adjacent_evidence_review",
                )
                returned_ids: set[str] = set()
                if data and isinstance(data.get("evidence"), list):
                    for item in data["evidence"]:
                        if not isinstance(item, dict):
                            continue
                        candidate_id = str(item.get("id") or "").strip().casefold()
                        if candidate_id in batch_ids and candidate_id not in returned_ids:
                            reviewed_items.append(item)
                            returned_ids.add(candidate_id)
                missing.extend(
                    item
                    for item in batch
                    if str(item["id"]).casefold() not in returned_ids
                )
            pending = missing
            if not pending:
                break
        accumulated: dict[str, tuple[list[str], list[EvidenceReference]]] = {
            key: ([], []) for key in repo_keys
        }
        for item in reviewed_items:
            candidate_id = str(item.get("id") or "").strip().casefold()
            matched = by_id.get(candidate_id)
            if matched is None or not bool(item.get("supported")):
                continue
            key, adjacent = matched
            excerpt = adjacent.reference.excerpt
            raw_capabilities = item.get("capabilities")
            if not isinstance(raw_capabilities, list):
                continue
            capabilities = list(
                OrderedDict.fromkeys(
                    capability
                    for value in raw_capabilities
                    if (capability := str(value or "").strip())
                    and len(capability) <= 120
                    and capability.casefold() in excerpt.casefold()
                )
            )[:3]
            if not capabilities:
                continue
            public_capabilities = capabilities
            capability_items, evidence_items = accumulated[key]
            existing_keys = {
                re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", capability.casefold())
                for capability in capability_items
            }
            new_capabilities = [
                capability
                for capability in public_capabilities
                if (
                    normalized := re.sub(
                        r"[^a-z0-9\u4e00-\u9fff]+",
                        "",
                        capability.casefold(),
                    )
                )
                and normalized not in existing_keys
            ]
            if not new_capabilities:
                continue
            capability_items.extend(new_capabilities)
            reference = EvidenceReference(
                kind=adjacent.reference.kind,
                locator=adjacent.reference.locator,
                excerpt=excerpt,
            )
            if reference not in evidence_items:
                evidence_items.append(reference)
        return {
            key: (capabilities[:5], evidence[:5])
            for key, (capabilities, evidence) in accumulated.items()
        }

    def _apply_verified_component_citations(
        self,
        repo: CandidateRepository,
        requirement: Requirement,
        coverage: list[EvidenceCoverage],
        citations: object,
    ) -> list[EvidenceCoverage]:
        if not isinstance(citations, list):
            return coverage
        materials = {"README": repo.readme, **repo.key_files}
        verified: dict[str, dict[str, dict[str, EvidenceReference]]] = {}
        for citation in citations:
            if not isinstance(citation, dict):
                continue
            feature = str(citation.get("feature") or "").strip()
            component = str(citation.get("component") or "").strip()
            locator = str(citation.get("locator") or "").strip()
            excerpt = str(citation.get("excerpt") or "").strip()
            groups = requirement.evidence_components.get(feature, {})
            component_aliases = {
                str(alias).strip().lower()
                for alias in groups.get(component, [])
                if str(alias).strip()
            }
            if (
                component not in groups
                or not excerpt
                or excerpt not in materials.get(locator, "")
                or not any(
                    self._literal_alias_present(alias, excerpt.lower())
                    for alias in component_aliases
                )
            ):
                continue
            kind = "readme" if locator == "README" else "source"
            key = f"{locator}\0{excerpt}"
            verified.setdefault(feature, {}).setdefault(component, {})[key] = EvidenceReference(
                kind=kind, locator=locator, excerpt=excerpt
            )
        for item in coverage:
            groups = requirement.evidence_components.get(item.feature, {})
            cited = verified.get(item.feature, {})
            if not groups or not cited:
                continue
            for component, entries in cited.items():
                for reference in entries.values():
                    rendered = f"{reference.locator}: {compact_text(reference.excerpt, 320)}"
                    component_items = item.component_evidence.setdefault(component, [])
                    if rendered not in component_items:
                        component_items.append(rendered)
                    if reference not in item.evidence_references:
                        item.evidence_references.append(reference)
            if set(cited) != set(groups):
                continue
            shared = set.intersection(*(set(entries) for entries in cited.values()))
            if not shared:
                continue
            reference = cited[next(iter(cited))][next(iter(shared))]
            rendered = f"{reference.locator}: {compact_text(reference.excerpt, 320)}"
            if reference.kind == "readme":
                item.readme_evidence.append(rendered)
            else:
                item.source_evidence.append(rendered)
            item.covered = True
            item.status = "supported"
            item.unknown_reason = ""
        return coverage

    def _apply_verified_difference_citations(
        self,
        repo: CandidateRepository,
        requirement: Requirement,
        coverage: list[EvidenceCoverage],
        reported_differences: list[str],
        citations: object,
    ) -> tuple[list[EvidenceCoverage], list[str]]:
        """Keep semantic differences only when the cited repository text is exact and local."""
        if not isinstance(citations, list) or not reported_differences:
            return coverage, []
        materials = {"README": repo.readme, **repo.key_files}
        coverage_by_feature = {
            self._normalized_feature_key(item.feature): item for item in coverage
        }
        verified: list[str] = []
        for citation in citations:
            if not isinstance(citation, dict):
                continue
            feature = str(citation.get("feature") or "").strip()
            component = str(citation.get("component") or "").strip()
            finding = str(citation.get("finding") or "").strip()
            locator = str(citation.get("locator") or "").strip()
            excerpt = str(citation.get("excerpt") or "").strip()
            item = coverage_by_feature.get(self._normalized_feature_key(feature))
            groups = requirement.evidence_components.get(item.feature, {}) if item else {}
            if (
                item is None
                or finding not in reported_differences
                or not excerpt
                or excerpt not in materials.get(locator, "")
                or (component and component not in groups)
                or not self._feature_has_confirmed_difference(feature, [finding])
            ):
                continue
            if component:
                item.component_evidence.pop(component, None)
            item.covered = False
            item.status = "different"
            item.difference_reason = finding
            reference = EvidenceReference(
                kind="readme" if locator == "README" else "source",
                locator=locator,
                excerpt=excerpt,
            )
            if reference not in item.evidence_references:
                item.evidence_references.append(reference)
            if finding not in verified:
                verified.append(finding)
        return coverage, verified[:8]

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
            required_changes=self._default_required_changes(missing),
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
            "unverified_model_difference_count": 0,
            "ungated_generic_must_have_count": max(
                0,
                len(requirement.must_have_features)
                - sum(
                    1
                    for feature in requirement.must_have_features
                    if any(self._same_feature_key(feature, gated) for gated in gated_features)
                ),
            ),
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
            evidence_covered = [item.feature for item in coverage if item.status == "supported"]
            explicit_missing = [item.feature for item in coverage if item.status == "missing"]
            unknown = [item.feature for item in coverage if item.status == "unknown"]
            evidence_differences = [
                item.difference_reason or item.feature
                for item in coverage
                if item.status == "different"
            ]
            stats["unverified_model_difference_count"] += len(
                [finding for finding in reported_differences if finding not in evidence_differences]
            )
            analysis.covered_features = list(OrderedDict.fromkeys(evidence_covered))[:8]
            analysis.different_features = list(dict.fromkeys(evidence_differences))[:8]
            analysis.unknown_features = list(dict.fromkeys(unknown))[:8]
            # A model guess is never enough to call a feature absent. Only an
            # explicit statement in the project's own material enters this list.
            analysis.missing_features = explicit_missing[:8]
            stats["unknown_feature_count"] += len(unknown)
            stats["explicit_missing_count"] += len(explicit_missing)

            core_feature = self._core_requirement_feature(requirement)
            adjacent_evidence = self._build_adjacent_evidence(requirement, analysis.repo)
            analysis.adjacent_evidence = adjacent_evidence
            functional_score = self._verified_match_score(
                requirement,
                analysis.repo,
                coverage,
                adjacent_evidence,
            )
            required_keys = {
                self._normalized_feature_key(feature)
                for feature in requirement.must_have_features
            }
            required_coverage = [
                item
                for item in coverage
                if self._normalized_feature_key(item.feature) in required_keys
            ]
            core_supported = bool(required_keys) and len(required_coverage) == len(required_keys) and all(
                item.status == "supported" or item.covered
                for item in required_coverage
            )
            if (
                core_supported
                and not requirement.evidence_components
                and self._requires_compositional_core(requirement)
            ):
                core_supported = self._core_evidence_is_compositional(
                    requirement,
                    analysis.repo,
                    require_all_groups=True,
                )
            is_catalog = self._is_catalog_repository(analysis.repo)
            analysis.core_feature = core_feature or ""
            analysis.core_confirmed = core_supported
            analysis.is_catalog = is_catalog
            if is_catalog:
                functional_score = 0
            elif core_feature and not core_supported:
                functional_score = min(49, functional_score)
                stats["core_requirement_unconfirmed_count"] += 1
            analysis.functional_score = functional_score
            analysis.match_score = functional_score

            suitability_penalty = (
                len(explicit_missing) * 10
                + len(analysis.different_features) * 5
            )
            analysis.suitability_score = max(0, functional_score - suitability_penalty)
            analysis.score_reason = self._score_reason(analysis)
            analysis.evidence = self._coverage_evidence_summary(analysis)
            # Broad model prose is not evidence. Rebuild negative guidance only
            # from verified missing/different states below and during tiering.
            analysis.risks = []
            analysis.required_changes = []
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
        if stats["unverified_model_difference_count"]:
            usage.warnings.append(
                "Discarded unverified model-reported differences: "
                + str(stats["unverified_model_difference_count"])
            )
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

    def _select_report_projects(
        self,
        requirement: Requirement,
        analyses: list[ProjectAnalysis],
        usage: BudgetUsage,
    ) -> list[ProjectAnalysis]:
        ranked: list[tuple[tuple[int, int, int, int], ProjectAnalysis]] = []
        for analysis in analyses:
            if self._is_catalog_candidate(analysis):
                continue
            adjacent_evidence = analysis.adjacent_evidence
            if analysis.capability_citations_reviewed:
                (
                    analysis.verified_capabilities,
                    analysis.capability_evidence,
                ) = self._relevant_capability_citations(requirement, analysis)
            else:
                analysis.verified_capabilities = self._relevant_verified_capabilities(
                    requirement,
                    analysis.verified_capabilities,
                    adjacent_evidence.reference.excerpt if adjacent_evidence is not None else "",
                )
            component_count = sum(
                len(item.component_evidence)
                for item in analysis.evidence_coverage
                if item.status not in {"different", "missing"}
            )
            supported_count = self._supported_evidence_count(analysis.evidence_coverage)
            if (
                adjacent_evidence is not None
                and not analysis.core_confirmed
                and analysis.capability_citations_reviewed
                and not analysis.verified_capabilities
            ):
                continue
            if analysis.core_confirmed:
                tier = 3
                analysis.confidence_level = "reliable"
                analysis.is_reference_candidate = False
            elif component_count and adjacent_evidence is not None:
                tier = 2
                self._mark_reference_candidate(analysis)
            elif adjacent_evidence is not None:
                tier = 1
                self._mark_low_similarity_lead(analysis)
            else:
                continue
            if adjacent_evidence is not None and not analysis.core_confirmed:
                self._apply_adjacent_public_evidence(analysis, adjacent_evidence)
            ranked.append(
                (
                    (analysis.match_score, tier, component_count, supported_count),
                    analysis,
                )
            )

        selected: list[ProjectAnalysis] = []
        families: set[str] = set()
        for _, analysis in sorted(ranked, key=lambda item: item[0], reverse=True):
            family = self._project_family_key(analysis)
            if family in families:
                continue
            selected.append(analysis)
            families.add(family)
            if len(selected) >= self.settings.max_deep_analyze_repos:
                break
        if selected:
            usage.warnings.append(
                "Selected report projects by verified tier: "
                + ", ".join(item.repo.full_name for item in selected)
            )
        return selected

    @staticmethod
    def _supported_evidence_count(coverage: Sequence[EvidenceCoverage]) -> int:
        return sum(1 for item in coverage if item.status == "supported" or item.covered)

    def _relevant_verified_capabilities(
        self,
        requirement: Requirement,
        capabilities: Sequence[str],
        evidence_context: str = "",
    ) -> list[str]:
        groups = self._adjacent_concept_groups(requirement)
        actions = groups.get("actions", set())
        context = {
            alias
            for group in ("domains", "objects")
            for alias in groups.get(group, set())
        }
        if not actions:
            return []
        return [
            capability
            for capability in capabilities
            if self._literal_matching_terms(capability, actions)
            and (
                not context
                or self._literal_matching_terms(
                    " ".join([capability, evidence_context]),
                    context,
                )
            )
        ][:5]

    def _relevant_capability_citations(
        self,
        requirement: Requirement,
        analysis: ProjectAnalysis,
    ) -> tuple[list[str], list[EvidenceReference]]:
        capabilities: list[str] = []
        evidence: list[EvidenceReference] = []
        for capability in analysis.verified_capabilities:
            matching_references = [
                reference
                for reference in analysis.capability_evidence
                if capability.casefold() in reference.excerpt.casefold()
            ]
            relevant_references = [
                reference
                for reference in matching_references
                if self._relevant_verified_capabilities(
                    requirement,
                    [capability],
                    reference.excerpt,
                )
            ]
            if not relevant_references:
                continue
            if capability.casefold() not in {item.casefold() for item in capabilities}:
                capabilities.append(capability)
            for reference in relevant_references:
                if reference not in evidence:
                    evidence.append(reference)
        return capabilities[:5], evidence[:5]

    @staticmethod
    def _apply_adjacent_public_evidence(
        analysis: ProjectAnalysis,
        adjacent_evidence: AdjacentEvidence,
    ) -> None:
        capability = adjacent_evidence.capability
        if (
            not analysis.capability_citations_reviewed
            and capability
            and capability not in analysis.verified_capabilities
        ):
            analysis.verified_capabilities.append(capability)
        evidence = (
            f"{adjacent_evidence.reference.locator}: "
            f"{adjacent_evidence.reference.excerpt}"
        )
        if adjacent_evidence.reference.excerpt and evidence not in analysis.evidence:
            analysis.evidence.append(evidence)

    @staticmethod
    def _requires_compositional_core(requirement: Requirement) -> bool:
        concepts = requirement.feature_concepts or {}
        return (
            sum(bool(concepts.get(group)) for group in ("domains", "actions", "objects"))
            >= 2
        )

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
        analysis.recommendation = reason
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
        analysis.recommendation = reason
        analysis.directly_usable = False
        if reason not in analysis.risks:
            analysis.risks.append(reason)
        if "作为参考候选评估，不建议直接采用" not in analysis.required_changes:
            analysis.required_changes.append("作为参考候选评估，不建议直接采用")

    def _default_required_changes(self, missing: list[str]) -> list[str]:
        if not missing:
            return ["先本地运行并验证主要流程，再决定是否集成"]
        return [f"补齐功能：{item}" for item in missing[:5]]

    @staticmethod
    def _plain_user_text(text: str) -> str:
        return str(text or "")

    def _write_summary(self, requirement: Requirement, analyses: list[ProjectAnalysis]) -> str:
        language = requirement.report_language
        if not analyses:
            return (
                "本次未找到有公开证据支持的可用或相邻项目。"
                if language == "zh"
                else "No usable or adjacent project with public supporting evidence was found."
            )
        best = analyses[0]
        score = build_public_project_view(best, language).relevance
        if best.directly_usable and best.core_confirmed:
            return (
                f"找到了可优先评估的项目：{best.repo.full_name}（相关度 {score}%）。"
                if language == "zh"
                else f"A directly usable candidate is available: {best.repo.full_name} ({score}% relevant)."
            )
        return (
            f"没有确认可直接使用的项目；保留了 {len(analyses)} 个低置信度相邻线索供参考。"
            if language == "zh"
            else f"No directly usable project was confirmed; {len(analyses)} low-confidence adjacent lead(s) remain for reference."
        )

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
        language = requirement.report_language
        lines.append("# 调研结论" if language == "zh" else "# Research conclusion")
        lines.append("")
        lines.append("## 一句话判断" if language == "zh" else "## Summary")
        lines.append(self._write_summary(requirement, analyses))
        lines.append("")
        lines.append("## 候选项目" if language == "zh" else "## Candidate projects")
        if not analyses:
            self._append_empty_result_context(lines, requirement)
        for project_index, analysis in enumerate(analyses, start=1):
            lines.append("")
            self._append_project_report(lines, project_index, analysis, language)
        lines.append("")
        lines.append("## 本次消耗" if language == "zh" else "## Usage")
        lines.append(self._format_token_usage(usage, language))
        return "\n".join(lines)

    @staticmethod
    def _format_token_usage(usage: BudgetUsage, language: str = "zh") -> str:
        total = usage.llm_input_tokens + usage.llm_output_tokens
        if language == "en":
            label = "LLM tokens (estimated)" if usage.llm_token_estimated and total else "LLM tokens"
            return f"- {label}: {usage.llm_input_tokens} input, {usage.llm_output_tokens} output, {total} total."
        label = "LLM Token（估算）" if usage.llm_token_estimated and total else "LLM Token"
        return f"- {label}：输入 {usage.llm_input_tokens}，输出 {usage.llm_output_tokens}，合计 {total}。"

    def _append_empty_result_context(self, lines: list[str], requirement: Requirement) -> None:
        if requirement.report_language == "en":
            lines.append("- The configured repository, code, topic, issue, and web discovery channels were searched.")
            lines.append("- No candidate retained enough local evidence to be useful.")
            return
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
        lines.append(f"- 已查找方向：{'、'.join(channels)}。")
        lines.append(
            f"- 未列出项目：没有候选能用公开证据确认「{core}」。"
        )

    def _append_project_report(
        self,
        lines: list[str],
        index: int,
        analysis: ProjectAnalysis,
        language: str,
    ) -> None:
        repo = analysis.repo
        metadata = self._format_repo_title_metadata(repo)
        lines.append(f"### {index}. [{repo.full_name}]({repo.url}){metadata}")
        public = build_public_project_view(analysis, language)
        if language == "en":
            lines.append(f"- Relevance: {public.relevance}%")
            if public.summary:
                lines.append(f"- Overview: {self._plain_user_text(public.summary)}")
            if public.verified_capabilities:
                lines.append(f"- Verified capabilities: {'; '.join(public.verified_capabilities)}")
            if not analysis.core_confirmed:
                lines.append("- Scope: these are adjacent verified capabilities, not confirmation of the complete core requirement.")
            return
        lines.append(f"- 相关度：{public.relevance}%")
        if public.summary:
            lines.append(f"- 简介：{self._plain_user_text(public.summary)}")
        if public.verified_capabilities:
            lines.append(f"- 已确认能力：{'；'.join(public.verified_capabilities)}")
        if not analysis.core_confirmed:
            lines.append("- 适用范围：以上只确认相邻能力，不表示完整满足核心需求。")

    @staticmethod
    def _format_repo_title_metadata(repo: CandidateRepository) -> str:
        updated = str(repo.last_pushed_at or "").strip()
        if "T" in updated:
            updated = updated.split("T", 1)[0]
        elif len(updated) > 10:
            updated = updated[:10]
        updated = updated or "未知"
        return f" · ★ {repo.stars} · {updated}"

    def _search_completeness(self, usage: BudgetUsage, request_limit: int) -> dict[str, object]:
        reasons: list[str] = []
        if usage.github_requests >= request_limit:
            reasons.append("GitHub request limit reached")
        for event in usage.provider_events:
            if event.provider not in {"github", "tavily"}:
                continue
            reason = f"{event.provider} {event.kind} during {event.stage or 'search'}"
            if reason not in reasons:
                reasons.append(reason)
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
    *,
    fixed_requirement: Requirement | None = None,
) -> SearchReport:
    return await DeepSearchEngine().run(query, fixed_requirement=fixed_requirement)
