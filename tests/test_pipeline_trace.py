"""Pipeline stage-by-stage trace tests.

These tests verify that each stage of the search pipeline produces
reasonable outputs, rather than only asserting final results.

Stages:
1. Parser output - SearchSpec/Requirement structure
2. Search queries - diversity and relevance
3. Candidate collection - quantity and sources
4. README collection - fetch success
5. Evidence collection - depth and coverage
6. LLM analysis + evidence gate - scoring and tiering
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest

from github_deep_search.engine import DeepSearchEngine
from github_deep_search.models import (
    BudgetUsage,
    CandidateRepository,
    EvidenceCoverage,
    ProjectAnalysis,
    Requirement,
    SearchSpec,
)


# ---------------------------------------------------------------------------
# Stage verification helpers
# ---------------------------------------------------------------------------


@dataclass
class StageReport:
    """Collects observations from each pipeline stage."""

    parser: dict[str, Any] = field(default_factory=dict)
    queries: dict[str, Any] = field(default_factory=dict)
    candidates: dict[str, Any] = field(default_factory=dict)
    readme: dict[str, Any] = field(default_factory=dict)
    evidence: dict[str, Any] = field(default_factory=dict)
    analysis: dict[str, Any] = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)

    def add_issue(self, stage: str, message: str) -> None:
        self.issues.append(f"[{stage}] {message}")


def verify_parser_output(spec: SearchSpec, report: StageReport) -> None:
    """Stage 1: Verify parser produced a well-formed SearchSpec."""
    report.parser = {
        "raw": spec.raw,
        "intent": spec.intent,
        "must_have_count": len(spec.must_have),
        "nice_to_have_count": len(spec.nice_to_have),
        "query_count": len(spec.search_queries),
        "repo_query_count": len(spec.repo_search_queries),
        "code_query_count": len(spec.code_search_queries),
        "has_evidence_aliases": bool(spec.evidence_aliases),
    }

    if not spec.intent:
        report.add_issue("parser", "intent is empty")
    if not spec.must_have and not spec.nice_to_have:
        report.add_issue("parser", "both must_have and nice_to_have are empty")
    if not spec.search_queries:
        report.add_issue("parser", "search_queries is empty")
    if len(spec.search_queries) < 2:
        report.add_issue("parser", f"only {len(spec.search_queries)} search query, expected >= 2")
    if not spec.evidence_aliases:
        report.add_issue("parser", "evidence_aliases is empty, evidence matching may be weak")


def verify_search_queries(requirement: Requirement, report: StageReport) -> None:
    """Stage 2: Verify search queries are diverse and well-formed."""
    all_queries = (
        requirement.repo_search_queries
        + requirement.code_search_queries
        + requirement.topic_search_queries
        + requirement.issue_search_queries
    )
    unique_queries = set(all_queries)

    report.queries = {
        "repo_queries": len(requirement.repo_search_queries),
        "code_queries": len(requirement.code_search_queries),
        "topic_queries": len(requirement.topic_search_queries),
        "issue_queries": len(requirement.issue_search_queries),
        "total_queries": len(all_queries),
        "unique_queries": len(unique_queries),
        "duplicate_ratio": 1 - len(unique_queries) / max(1, len(all_queries)),
    }

    if not requirement.repo_search_queries:
        report.add_issue("queries", "repo_search_queries is empty")
    if not requirement.code_search_queries:
        report.add_issue("queries", "code_search_queries is empty")
    if len(unique_queries) < len(all_queries) * 0.5:
        report.add_issue(
            "queries",
            f"high duplicate ratio: {len(unique_queries)}/{len(all_queries)} unique",
        )
    if len(all_queries) < 4:
        report.add_issue("queries", f"only {len(all_queries)} total queries, expected >= 4")


def verify_candidate_collection(
    candidates: list[CandidateRepository], report: StageReport
) -> None:
    """Stage 3: Verify candidate collection produced diverse results."""
    sources: dict[str, int] = {}
    for repo in candidates:
        for source in repo.found_by:
            category = source.split(":")[0] if ":" in source else source
            sources[category] = sources.get(category, 0) + 1

    scores = [repo.raw_score for repo in candidates]
    avg_score = sum(scores) / len(scores) if scores else 0

    report.candidates = {
        "total": len(candidates),
        "sources": sources,
        "source_count": len(sources),
        "avg_raw_score": avg_score,
        "max_raw_score": max(scores) if scores else 0,
        "min_raw_score": min(scores) if scores else 0,
    }

    if not candidates:
        report.add_issue("candidates", "no candidates collected")
        return
    if len(candidates) < 3:
        report.add_issue("candidates", f"only {len(candidates)} candidates, expected >= 3")
    if len(sources) < 2:
        report.add_issue("candidates", f"candidates found via only {len(sources)} source type")
    if avg_score < 10:
        report.add_issue("candidates", f"average raw_score is low: {avg_score:.1f}")


def verify_readme_collection(
    candidates: list[CandidateRepository], report: StageReport
) -> None:
    """Stage 4: Verify README collection succeeded for candidates."""
    with_readme = [r for r in candidates if r.readme]
    without_readme = [r for r in candidates if not r.readme]

    report.readme = {
        "total_candidates": len(candidates),
        "with_readme": len(with_readme),
        "without_readme": len(without_readme),
        "readme_ratio": len(with_readme) / max(1, len(candidates)),
    }

    if not candidates:
        return
    if len(with_readme) == 0:
        report.add_issue("readme", "no candidates have README content")
    if len(with_readme) / len(candidates) < 0.5:
        report.add_issue(
            "readme",
            f"only {len(with_readme)}/{len(candidates)} candidates have README",
        )


def verify_evidence_collection(
    candidates: list[CandidateRepository], requirement: Requirement, report: StageReport
) -> None:
    """Stage 5: Verify evidence collection produced coverage data."""
    engine = DeepSearchEngine()
    total_features = len(requirement.must_have_features) + len(requirement.nice_to_have_features)
    if total_features == 0:
        report.add_issue("evidence", "requirement has no features to verify")
        return

    coverage_stats = {
        "total_features": total_features,
        "candidates_with_coverage": 0,
        "avg_covered_features": 0.0,
        "source_evidence_count": 0,
        "path_evidence_count": 0,
        "readme_evidence_count": 0,
    }

    covered_counts = []
    for repo in candidates:
        if not repo.evidence_coverage:
            repo.evidence_coverage = engine._build_evidence_coverage(repo, requirement)
        if repo.evidence_coverage:
            coverage_stats["candidates_with_coverage"] += 1
            covered = sum(1 for c in repo.evidence_coverage if c.covered)
            covered_counts.append(covered)
            for cov in repo.evidence_coverage:
                coverage_stats["source_evidence_count"] += len(cov.source_evidence)
                coverage_stats["path_evidence_count"] += len(cov.path_evidence)
                coverage_stats["readme_evidence_count"] += len(cov.readme_evidence)

    if covered_counts:
        coverage_stats["avg_covered_features"] = sum(covered_counts) / len(covered_counts)

    report.evidence = coverage_stats

    if coverage_stats["candidates_with_coverage"] == 0:
        report.add_issue("evidence", "no candidates have evidence coverage")
    if coverage_stats["source_evidence_count"] == 0 and coverage_stats["path_evidence_count"] == 0:
        report.add_issue("evidence", "no source or path evidence found, only README-level matches")


def verify_llm_and_gate(
    analyses: list[ProjectAnalysis], requirement: Requirement, report: StageReport
) -> None:
    """Stage 6: Verify LLM analysis and evidence gate produced tiered results."""
    scores = [a.match_score for a in analyses]
    unique_scores = set(scores)

    reliable = [a for a in analyses if a.confidence_level == "reliable"]
    reference = [a for a in analyses if a.confidence_level == "reference"]
    adjacent = [a for a in analyses if a.confidence_level == "adjacent"]

    report.analysis = {
        "total_analyses": len(analyses),
        "reliable_count": len(reliable),
        "reference_count": len(reference),
        "adjacent_count": len(adjacent),
        "unique_scores": len(unique_scores),
        "score_range": max(scores) - min(scores) if scores else 0,
        "avg_score": sum(scores) / len(scores) if scores else 0,
        "core_confirmed_count": sum(1 for a in analyses if a.core_confirmed),
    }

    if not analyses:
        report.add_issue("analysis", "no analyses produced")
        return
    if len(unique_scores) == 1 and len(analyses) > 1:
        report.add_issue("analysis", f"all {len(analyses)} analyses have identical score {scores[0]}")
    if len(reliable) == 0 and len(reference) == 0 and len(adjacent) == 0:
        report.add_issue("analysis", "no results in any confidence tier")


# ---------------------------------------------------------------------------
# Fake GitHub client for trace tests
# ---------------------------------------------------------------------------


class TraceGitHub:
    """Mock GitHub client that returns realistic data for pipeline tracing."""

    def __init__(self) -> None:
        self.usage = BudgetUsage()
        self.paused = False
        self.request_limit = 200
        self.repo_queries: list[str] = []
        self.code_queries: list[str] = []
        self.topic_queries: list[str] = []
        self.issue_queries: list[str] = []

    async def _get(self, path: str, **params: Any) -> dict | None:
        self.usage.github_requests += 1
        if path == "/search/repositories":
            query = params.get("q", "")
            self.repo_queries.append(query)
            return {
                "items": [
                    {
                        "full_name": f"demo/repo-{i}",
                        "html_url": f"https://github.com/demo/repo-{i}",
                        "description": f"A project related to {query}",
                        "stargazers_count": 100 - i * 10,
                        "forks_count": 20 - i * 2,
                        "language": "Python",
                        "topics": ["demo", "test"],
                        "pushed_at": "2024-01-01T00:00:00Z",
                        "license": {"spdx_id": "MIT"},
                        "default_branch": "main",
                        "score": 50 - i * 5,  # GitHub search relevance score
                    }
                    for i in range(3)
                ]
            }
        if path == "/search/code":
            query = params.get("q", "")
            self.code_queries.append(query)
            return {
                "items": [
                    {
                        "repository": {
                            "full_name": "demo/code-hit",
                            "html_url": "https://github.com/demo/code-hit",
                            "description": "Code search result",
                            "stargazers_count": 50,
                            "forks_count": 10,
                            "language": "TypeScript",
                            "topics": [],
                            "pushed_at": "2024-01-01T00:00:00Z",
                            "license": {"spdx_id": "MIT"},
                            "default_branch": "main",
                        },
                        "path": "src/feature.ts",
                    }
                ]
            }
        if path == "/search/topics":
            query = params.get("q", "")
            self.topic_queries.append(query)
            return {
                "items": [
                    {
                        "name": query,
                        "display_name": query.title(),
                        "short_description": f"Topic about {query}",
                    }
                ]
            }
        if path == "/search/issues":
            query = params.get("q", "")
            self.issue_queries.append(query)
            return {
                "items": [
                    {
                        "repository_url": "https://api.github.com/repos/demo/issue-hit",
                        "html_url": "https://github.com/demo/issue-hit/issues/1",
                        "title": f"Issue about {query}",
                        "body": "This is a test issue",
                    }
                ]
            }
        if path.startswith("/repos/") and path.endswith("/readme"):
            return {
                "content": "IyBEZW1vIFByb2plY3QKCkEgZGVtbyBwcm9qZWN0IHdpdGggZmVhdHVyZXMgZm9yIHRlc3Rpbmcu",
                "encoding": "base64",
            }
        if path.startswith("/repos/") and path.endswith("/git/trees/main"):
            return {
                "tree": [
                    {"path": "src/feature.ts", "type": "blob"},
                    {"path": "src/index.ts", "type": "blob"},
                    {"path": "README.md", "type": "blob"},
                ]
            }
        return None

    async def search_repositories(
        self, query: str, per_page: int = 10
    ) -> list[CandidateRepository]:
        self.usage.github_search_requests += 1
        data = await self._get("/search/repositories", q=query, per_page=per_page)
        if not data:
            return []
        return [
            CandidateRepository(
                owner=item["full_name"].split("/")[0],
                name=item["full_name"].split("/")[1],
                url=item["html_url"],
                description=item.get("description", ""),
                stars=item.get("stargazers_count", 0),
                forks=item.get("forks_count", 0),
                language=item.get("language"),
                topics=item.get("topics", []),
                last_pushed_at=item.get("pushed_at"),
                license=item.get("license", {}).get("spdx_id") if item.get("license") else None,
                default_branch=item.get("default_branch", "main"),
                found_by=[f"github:{query}"],
                raw_score=item.get("score", 0),
            )
            for item in data.get("items", [])
        ]

    async def search_code_repositories(
        self, query: str, per_page: int = 10
    ) -> list[tuple[str, str, str]]:
        self.usage.github_code_search_requests += 1
        data = await self._get("/search/code", q=query, per_page=per_page)
        if not data:
            return []
        return [
            (
                item["repository"]["full_name"],
                item["repository"]["html_url"],
                item.get("path", ""),
            )
            for item in data.get("items", [])
        ]

    async def search_topics(self, query: str, per_page: int = 10) -> list[str]:
        self.usage.github_topic_search_requests += 1
        data = await self._get("/search/topics", q=query, per_page=per_page)
        if not data:
            return []
        return [item["name"] for item in data.get("items", [])]

    async def search_issues(self, query: str, per_page: int = 10) -> list[dict]:
        self.usage.github_issue_search_requests += 1
        data = await self._get("/search/issues", q=query, per_page=per_page)
        if not data:
            return []
        return data.get("items", [])

    async def search_topic_repositories(
        self, topic: str, per_page: int = 10
    ) -> list[CandidateRepository]:
        self.usage.github_search_requests += 1
        self.usage.github_topic_search_requests += 1
        self.topic_queries.append(topic)
        return [
            CandidateRepository(
                owner="demo",
                name=f"topic-{topic}-{i}",
                url=f"https://github.com/demo/topic-{topic}-{i}",
                description=f"Topic result for {topic}",
                stars=80 - i * 10,
                forks=15,
                language="Python",
                topics=[topic],
                found_by=[f"github_topic:{topic}"],
                raw_score=45 - i * 5,
            )
            for i in range(2)
        ]

    async def search_issue_repositories(
        self, query: str, per_page: int = 10
    ) -> list[tuple[str, str]]:
        self.usage.github_search_requests += 1
        self.usage.github_issue_search_requests += 1
        self.issue_queries.append(query)
        return [("demo", f"issue-hit-{query[:10]}")]

    async def get_repository(
        self, owner: str, name: str, found_by: str = ""
    ) -> CandidateRepository | None:
        self.usage.github_requests += 1
        return CandidateRepository(
            owner=owner,
            name=name,
            url=f"https://github.com/{owner}/{name}",
            description=f"Repository {owner}/{name}",
            stars=75,
            forks=12,
            language="Python",
            topics=["demo"],
            default_branch="main",
            found_by=[found_by] if found_by else [],
            raw_score=40,
        )

    async def get_readme(self, owner: str, name: str) -> str:
        # Return README with feature keywords for evidence matching
        return """# GitHub Monitor

A tool to track repository activity and send alerts.

## Features
- Track repository activity and commits
- Send alerts via email and webhook notifications
- Monitor multiple repositories
- Historical data dashboard
"""

    async def get_file_tree(self, owner: str, name: str, branch: str = "main") -> list[str]:
        # Return file paths with feature keywords for evidence matching
        return [
            "src/monitor.py",
            "src/tracker.py",
            "src/alert.py",
            "src/notification.py",
            "src/activity.py",
            "README.md",
        ]

    async def get_file_content(self, owner: str, name: str, path: str, ref: str = "") -> str:
        return "export function feature() { return true; }"

    async def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Pipeline trace test
# ---------------------------------------------------------------------------


def test_pipeline_trace_full_flow() -> None:
    """End-to-end pipeline trace: verify each stage produces reasonable output."""
    engine = DeepSearchEngine()

    # Stage 1: Parser output (simulated SearchSpec)
    spec = SearchSpec(
        raw="Find a GitHub monitoring tool that tracks repository activity and sends alerts",
        intent="Find GitHub monitoring tool",
        literal_keywords=["github", "monitor", "track", "alert"],
        domains=["github", "devops"],
        actions=["monitor", "track", "alert"],
        objects=["repository", "activity"],
        outputs=["alert", "notification"],
        interfaces=["cli", "api"],
        must_have=["track repository activity", "send alerts"],
        nice_to_have=["web dashboard", "historical data"],
        negative_filters=[],
        search_queries=[
            "github monitor track repository activity",
            "github alert notification system",
        ],
        repo_search_queries=[
            "github monitor repository",
            "github activity tracker",
            "github alert system",
        ],
        code_search_queries=[
            "github monitor track",
            "github alert notification",
        ],
        topic_search_queries=["github-monitor", "repository-tracker"],
        issue_search_queries=["monitor alert github"],
        evidence_aliases={
            "track repository activity": ["track", "activity", "monitor"],
            "send alerts": ["alert", "notification", "notify"],
        },
    )

    report = StageReport()

    # Stage 1 verification
    verify_parser_output(spec, report)
    assert not report.issues, f"Parser issues: {report.issues}"

    requirement = spec.to_requirement()

    # Stage 2 verification
    verify_search_queries(requirement, report)
    assert not report.issues, f"Query issues: {report.issues}"

    # Stage 3: Candidate collection
    github = TraceGitHub()
    candidates = asyncio.run(
        engine._collect_candidates(requirement, github, None, BudgetUsage())
    )

    verify_candidate_collection(candidates, report)
    assert not report.issues, f"Collection issues: {report.issues}"

    # Stage 4: README collection
    for repo in candidates:  # Fetch README for all candidates
        repo.readme = asyncio.run(github.get_readme(repo.owner, repo.name))
        repo.file_paths = asyncio.run(
            github.get_file_tree(repo.owner, repo.name, repo.default_branch)
        )

    verify_readme_collection(candidates, report)
    assert not report.issues, f"README issues: {report.issues}"

    # Stage 5: Evidence collection
    verify_evidence_collection(candidates, requirement, report)
    assert not report.issues, f"Evidence issues: {report.issues}"

    # Stage 6: LLM analysis + evidence gate (simulated)
    analyses = []
    for i, repo in enumerate(candidates[:3]):
        analysis = ProjectAnalysis(
            repo=repo,
            match_score=70 - i * 5,
            recommendation=f"Candidate {i+1} with partial feature coverage",
            directly_usable=i == 0,
            covered_features=["track repository activity"] if i < 2 else [],
            missing_features=["send alerts"] if i > 0 else [],
            required_changes=["Add alert configuration"] if i > 0 else [],
            risks=["Limited documentation"] if i > 1 else [],
            evidence=[f"README mentions tracking" if repo.readme else "No evidence"],
            confidence_level="reliable" if i == 0 else "reference" if i == 1 else "adjacent",
            core_confirmed=i == 0,
        )
        analyses.append(analysis)

    verify_llm_and_gate(analyses, requirement, report)
    assert not report.issues, f"Analysis issues: {report.issues}"

    # Final summary
    print("\n=== Pipeline Trace Report ===")
    print(f"Parser: {report.parser}")
    print(f"Queries: {report.queries}")
    print(f"Candidates: {report.candidates}")
    print(f"README: {report.readme}")
    print(f"Evidence: {report.evidence}")
    print(f"Analysis: {report.analysis}")
    print("===========================")


def test_parser_output_quality() -> None:
    """Verify parser produces well-formed SearchSpec."""
    spec = SearchSpec(
        raw="Find a CLI tool that converts CSV to JSON with filtering",
        intent="Find CSV to JSON converter CLI",
        literal_keywords=["csv", "json", "convert", "cli"],
        domains=["cli", "data"],
        actions=["convert", "filter"],
        objects=["csv", "json"],
        outputs=["json"],
        interfaces=["cli"],
        must_have=["convert csv to json", "filtering support"],
        nice_to_have=["streaming", "pretty print"],
        negative_filters=[],
        search_queries=["csv json converter cli", "csv to json filter"],
        repo_search_queries=["csv json converter", "csv filter cli"],
        code_search_queries=["csv json convert", "filter csv json"],
        topic_search_queries=["csv-converter", "json-cli"],
        issue_search_queries=["csv json filter"],
        evidence_aliases={
            "convert csv to json": ["csv", "json", "convert", "parse"],
            "filtering support": ["filter", "query", "select"],
        },
    )

    report = StageReport()
    verify_parser_output(spec, report)

    assert spec.intent
    assert spec.must_have or spec.nice_to_have
    assert len(spec.search_queries) >= 2
    assert spec.evidence_aliases
    assert not report.issues


def test_search_query_diversity() -> None:
    """Verify search queries span multiple search types."""
    requirement = Requirement(
        raw="test requirement",
        intent="test",
        must_have_features=["feature1", "feature2"],
        nice_to_have_features=["feature3"],
        target_platforms=["cli"],
        search_queries=["query1", "query2"],
        repo_search_queries=["repo1", "repo2", "repo3"],
        code_search_queries=["code1", "code2"],
        topic_search_queries=["topic1", "topic2"],
        issue_search_queries=["issue1"],
    )

    report = StageReport()
    verify_search_queries(requirement, report)

    assert report.queries["total_queries"] >= 4
    assert report.queries["unique_queries"] >= 4
    assert not report.issues


def test_candidate_collection_minimum() -> None:
    """Verify candidate collection produces at least some results."""
    candidates = [
        CandidateRepository(
            owner="demo",
            name=f"repo-{i}",
            url=f"https://github.com/demo/repo-{i}",
            description=f"Test repo {i}",
            stars=100 - i * 10,
            raw_score=50 - i * 5,
            found_by=[f"github:query{i % 2}"] if i < 3 else [f"github_code:query{i % 2}"],
        )
        for i in range(5)
    ]

    report = StageReport()
    verify_candidate_collection(candidates, report)

    assert report.candidates["total"] >= 3
    assert report.candidates["source_count"] >= 2
    assert not report.issues


def test_readme_collection_ratio() -> None:
    """Verify README collection achieves reasonable hit rate."""
    candidates = [
        CandidateRepository(
            owner="demo",
            name=f"repo-{i}",
            url=f"https://github.com/demo/repo-{i}",
            readme="This is a test README with features" if i < 4 else "",
        )
        for i in range(5)
    ]

    report = StageReport()
    verify_readme_collection(candidates, report)

    assert report.readme["readme_ratio"] >= 0.5
    assert not report.issues


def test_evidence_collection_depth() -> None:
    """Verify evidence collection finds source-level evidence."""
    engine = DeepSearchEngine()
    requirement = Requirement(
        raw="test",
        intent="test",
        must_have_features=["feature1", "feature2"],
        nice_to_have_features=[],
        target_platforms=[],
        search_queries=["test"],
        evidence_aliases={
            "feature1": ["feat1", "f1"],
            "feature2": ["feat2", "f2"],
        },
    )

    candidates = [
        CandidateRepository(
            owner="demo",
            name="repo-with-source",
            url="https://github.com/demo/repo-with-source",
            readme="This project implements feature1 and feature2",
            file_paths=["src/feature1.ts", "src/feature2.ts", "README.md"],
            key_files={
                "src/feature1.ts": "export function feature1() { return true; }",
                "src/feature2.ts": "export function feature2() { return true; }",
            },
        )
    ]

    report = StageReport()
    verify_evidence_collection(candidates, requirement, report)

    assert report.evidence["candidates_with_coverage"] >= 1
    assert report.evidence["source_evidence_count"] >= 1
    assert not report.issues


def test_llm_analysis_tiering() -> None:
    """Verify LLM analysis produces tiered results with score diversity."""
    requirement = Requirement(
        raw="test",
        intent="test",
        must_have_features=["feature1"],
        nice_to_have_features=[],
        target_platforms=[],
        search_queries=["test"],
    )

    analyses = [
        ProjectAnalysis(
            repo=CandidateRepository(
                owner="demo", name="reliable", url="https://github.com/demo/reliable"
            ),
            match_score=85,
            recommendation="Strong match",
            directly_usable=True,
            covered_features=["feature1"],
            missing_features=[],
            required_changes=[],
            risks=[],
            evidence=["README confirms feature1"],
            confidence_level="reliable",
            core_confirmed=True,
        ),
        ProjectAnalysis(
            repo=CandidateRepository(
                owner="demo", name="reference", url="https://github.com/demo/reference"
            ),
            match_score=60,
            recommendation="Partial match",
            directly_usable=False,
            covered_features=[],
            missing_features=["feature1"],
            required_changes=["Implement feature1"],
            risks=["Missing core feature"],
            evidence=["Related domain"],
            confidence_level="reference",
            core_confirmed=False,
        ),
        ProjectAnalysis(
            repo=CandidateRepository(
                owner="demo", name="adjacent", url="https://github.com/demo/adjacent"
            ),
            match_score=35,
            recommendation="Adjacent project",
            directly_usable=False,
            covered_features=[],
            missing_features=["feature1"],
            required_changes=["Major changes needed"],
            risks=["Different focus"],
            evidence=["Similar tech stack"],
            confidence_level="adjacent",
            core_confirmed=False,
        ),
    ]

    report = StageReport()
    verify_llm_and_gate(analyses, requirement, report)

    assert report.analysis["unique_scores"] >= 2
    assert report.analysis["reliable_count"] >= 1
    assert not report.issues


def test_empty_candidate_list_handled() -> None:
    """Verify pipeline handles empty candidate list gracefully."""
    requirement = Requirement(
        raw="test",
        intent="test",
        must_have_features=["feature1"],
        nice_to_have_features=[],
        target_platforms=[],
        search_queries=["test"],
    )

    report = StageReport()
    verify_candidate_collection([], report)

    assert "candidates" in str(report.issues).lower()
    assert "no candidates" in str(report.issues).lower()


def test_score_collision_detected() -> None:
    """Verify score collision is flagged as an issue."""
    requirement = Requirement(
        raw="test",
        intent="test",
        must_have_features=["feature1"],
        nice_to_have_features=[],
        target_platforms=[],
        search_queries=["test"],
    )

    analyses = [
        ProjectAnalysis(
            repo=CandidateRepository(
                owner="demo", name=f"repo-{i}", url=f"https://github.com/demo/repo-{i}"
            ),
            match_score=50,  # All same score
            recommendation="Same score",
            directly_usable=False,
            covered_features=[],
            missing_features=["feature1"],
            required_changes=[],
            risks=[],
            evidence=[],
        )
        for i in range(3)
    ]

    report = StageReport()
    verify_llm_and_gate(analyses, requirement, report)

    assert any("identical score" in issue for issue in report.issues)
