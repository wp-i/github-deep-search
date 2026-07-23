from __future__ import annotations

import asyncio

from github_deep_search.engine import DeepSearchEngine
from github_deep_search.models import (
    BudgetUsage,
    CandidateRepository,
    EvidenceCoverage,
    EvidenceReference,
    ProjectAnalysis,
    Requirement,
)


FEATURE = "filter Orbit videos by duration"


def _requirement() -> Requirement:
    return Requirement(
        raw="Find a tool that filters Orbit videos by duration.",
        intent="Find an Orbit video filter",
        must_have_features=[FEATURE],
        nice_to_have_features=[],
        target_platforms=[],
        search_queries=["Orbit video duration filter"],
        report_language="en",
        repo_search_queries=["Orbit video duration filter", "轨道视频时长过滤"],
        code_search_queries=["duration filter"],
        topic_search_queries=["orbit-video-filter"],
        issue_search_queries=["Orbit video filtering"],
        feature_concepts={
            "domains": ["Orbit"],
            "actions": ["filter"],
            "objects": ["videos", "duration"],
        },
        evidence_aliases={FEATURE: ["Orbit video duration filter"]},
        evidence_components={
            FEATURE: {
                "Orbit video filtering": ["Orbit video filter"],
                "duration condition": ["filter by duration"],
            }
        },
    )


def _analysis(
    name: str,
    score: int,
    *,
    description: str = "",
    core_confirmed: bool = False,
    component_evidence: bool = False,
) -> ProjectAnalysis:
    coverage = (
        [
            EvidenceCoverage(
                feature=FEATURE,
                covered=False,
                component_evidence={"Orbit video filtering": ["README: Orbit video filter"]},
                required_component_count=2,
            )
        ]
        if component_evidence
        else []
    )
    return ProjectAnalysis(
        repo=CandidateRepository(
            owner="demo",
            name=name,
            url=f"https://github.com/demo/{name}",
            description=description,
        ),
        match_score=score,
        recommendation="",
        directly_usable=core_confirmed,
        covered_features=[],
        missing_features=[],
        required_changes=[],
        risks=[],
        evidence=[],
        core_confirmed=core_confirmed,
        evidence_coverage=coverage,
    )


def test_evidence_hydration_keeps_global_leaders_and_source_diversity() -> None:
    ranked = [
        CandidateRepository(
            owner="example",
            name=f"project-{index}",
            url=f"https://github.com/example/project-{index}",
            found_by=["github:shared"] if index < 4 else [f"github:angle-{index}"],
            raw_score=float(index),
        )
        for index in range(8)
    ]

    selected = DeepSearchEngine()._evidence_hydration_pool(ranked, limit=6)

    assert [item.full_name for item in selected[:3]] == [
        "example/project-7",
        "example/project-6",
        "example/project-5",
    ]
    assert len(selected) == 6


class _RepositoryProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def get_repository(
        self,
        owner: str,
        name: str,
        found_by: str,
    ) -> CandidateRepository:
        self.calls += 1
        return CandidateRepository(
            owner=owner,
            name=name,
            url=f"https://github.com/{owner}/{name}",
            found_by=[found_by],
        )


def test_repository_metadata_is_reused_across_discovery_channels() -> None:
    engine = DeepSearchEngine()
    provider = _RepositoryProvider()

    async def run() -> tuple[CandidateRepository | None, CandidateRepository | None]:
        first = await engine._get_repository_cached(  # type: ignore[arg-type]
            provider, "demo", "project", found_by="github_code:first"
        )
        second = await engine._get_repository_cached(  # type: ignore[arg-type]
            provider, "DEMO", "PROJECT", found_by="github_issue:second"
        )
        return first, second

    first, second = asyncio.run(run())

    assert provider.calls == 1
    assert first is second
    assert second and second.found_by == ["github_code:first", "github_issue:second"]


def test_executor_consumes_the_fixed_query_plan_without_phrase_invention() -> None:
    engine = DeepSearchEngine()
    requirement = _requirement()

    assert engine._planned_repo_search_queries(requirement) == requirement.repo_search_queries
    assert engine._planned_topic_search_queries(requirement) == requirement.topic_search_queries
    assert engine._planned_issue_search_queries(requirement) == requirement.issue_search_queries
    assert engine._planned_code_search_queries(requirement) == ['"duration filter" in:file,path']
    assert (
        engine._to_github_repo_query("Orbit video duration filter")
        == "Orbit video duration filter in:name,description,readme"
    )


def test_adjacent_evidence_requires_domain_action_and_object_locally() -> None:
    engine = DeepSearchEngine()
    requirement = _requirement()
    useful = CandidateRepository(
        owner="demo",
        name="filter",
        url="https://github.com/demo/filter",
        description="An Orbit tool that filters videos using duration rules.",
    )
    scattered = CandidateRepository(
        owner="demo",
        name="scattered",
        url="https://github.com/demo/scattered",
        description="Orbit video analytics.",
        readme="unrelated details\n" * 20 + "Filters local log files by duration.",
    )

    assert engine._build_adjacent_evidence(requirement, useful) is not None
    assert engine._build_adjacent_evidence(requirement, scattered) is None


def test_adjacent_evidence_uses_current_spec_component_phrases() -> None:
    engine = DeepSearchEngine()
    requirement = _requirement()
    requirement.feature_concepts["literal_keywords"] = ["free"]
    requirement.evidence_components[FEATURE]["duration condition"].append(
        "screen by duration"
    )
    repo = CandidateRepository(
        owner="demo",
        name="screen",
        url="https://github.com/demo/screen",
        description="An Orbit tool that can screen by duration for videos.",
    )

    groups = engine._adjacent_concept_groups(requirement)

    assert "screen by duration" in groups["actions"]
    assert "free" not in groups["objects"]
    assert engine._build_adjacent_evidence(requirement, repo) is not None
    assert engine._relevant_verified_capabilities(
        requirement,
        ["screen by duration"],
        repo.description,
    ) == ["screen by duration"]


def test_topics_and_external_project_lists_do_not_prove_runtime_capability() -> None:
    engine = DeepSearchEngine()
    requirement = _requirement()
    topics_only = CandidateRepository(
        owner="demo",
        name="topics",
        url="https://github.com/demo/topics",
        description="No runtime behavior is described.",
        topics=["orbit", "video-filter"],
    )
    catalog = CandidateRepository(
        owner="demo",
        name="catalog",
        url="https://github.com/demo/catalog",
        readme="- [Other project](https://github.com/example/other): Orbit video duration filter.",
    )

    assert engine._build_adjacent_evidence(requirement, topics_only) is None
    assert engine._build_adjacent_evidence(requirement, catalog) is None


def test_reviewed_empty_capabilities_are_not_restored_from_metadata() -> None:
    engine = DeepSearchEngine()
    analysis = _analysis(
        "metadata-only",
        30,
        description="Orbit tool filters videos by duration.",
    )
    analysis.capability_citations_reviewed = True
    gated, _ = engine._apply_evidence_gate(_requirement(), [analysis], BudgetUsage())

    assert gated[0].adjacent_evidence is not None
    assert engine._select_report_projects(_requirement(), gated, BudgetUsage()) == []


def test_reviewed_narrow_capability_remains_a_low_confidence_lead() -> None:
    engine = DeepSearchEngine()
    analysis = _analysis(
        "partial",
        30,
        description="This tool filters Orbit videos by duration.",
    )
    analysis.capability_citations_reviewed = True
    analysis.verified_capabilities = ["filters Orbit videos by duration"]
    analysis.capability_evidence = [
        EvidenceReference(
            kind="repository_metadata",
            locator="description",
            excerpt="This tool filters Orbit videos by duration.",
        )
    ]
    gated, _ = engine._apply_evidence_gate(_requirement(), [analysis], BudgetUsage())

    selected = engine._select_report_projects(_requirement(), gated, BudgetUsage())

    assert len(selected) == 1
    assert selected[0].verified_capabilities == ["filters Orbit videos by duration"]
    assert selected[0].confidence_level == "lead"


class _Reviewer:
    def __init__(self, capabilities: list[str]) -> None:
        self.capabilities = capabilities

    async def json_chat(
        self,
        _system: str,
        _user: str,
        *,
        operation: str = "chat",
    ) -> dict[str, object]:
        return {
            "evidence": [
                {
                    "id": "demo/filter#0",
                    "supported": True,
                    "capabilities": self.capabilities,
                }
            ]
        }


def test_focused_capability_review_accepts_only_exact_local_phrases() -> None:
    engine = DeepSearchEngine()
    repo = CandidateRepository(
        owner="demo",
        name="filter",
        url="https://github.com/demo/filter",
        readme="This extension can filter Orbit videos by duration.",
    )
    accepted = asyncio.run(
        engine._review_adjacent_capabilities(  # type: ignore[arg-type]
            _requirement(), [repo], _Reviewer(["filter Orbit videos by duration"])
        )
    )
    rejected = asyncio.run(
        engine._review_adjacent_capabilities(  # type: ignore[arg-type]
            _requirement(), [repo], _Reviewer(["filters by popularity"])
        )
    )

    assert accepted["demo/filter"][0] == ["filter Orbit videos by duration"]
    assert accepted["demo/filter"][1][0].locator == "README"
    assert rejected["demo/filter"] == ([], [])


class _WindowReviewer:
    async def json_chat(
        self,
        _system: str,
        _user: str,
        *,
        operation: str = "chat",
    ) -> dict[str, object]:
        return {
            "evidence": [
                {
                    "id": "demo/filter#0",
                    "supported": True,
                    "capabilities": ["filter videos by duration"],
                },
                {
                    "id": "demo/filter#1",
                    "supported": True,
                    "capabilities": ["filter Orbit videos"],
                },
            ]
        }


def test_focused_review_keeps_distinct_repository_evidence_windows() -> None:
    repo = CandidateRepository(
        owner="demo",
        name="filter",
        url="https://github.com/demo/filter",
        description="This tool can filter Orbit videos.",
        readme="Orbit users can filter videos by duration.",
    )

    capabilities, evidence = asyncio.run(
        DeepSearchEngine()._review_adjacent_capabilities(  # type: ignore[arg-type]
            _requirement(), [repo], _WindowReviewer()
        )
    )["demo/filter"]

    assert set(capabilities) == {"filter videos by duration", "filter Orbit videos"}
    assert {item.locator for item in evidence} == {"README", "description"}


def test_report_selection_keeps_reliable_partial_and_adjacent_tiers() -> None:
    engine = DeepSearchEngine()
    requirement = _requirement()
    reliable = _analysis("reliable", 82, core_confirmed=True)
    partial = _analysis(
        "partial",
        60,
        component_evidence=True,
        description="An Orbit video filter with duration rules.",
    )
    adjacent = _analysis(
        "adjacent",
        40,
        description="An Orbit tool that filters videos by duration.",
    )
    irrelevant = _analysis("poster", 95, description="An Orbit video poster generator.")
    for analysis in (partial, adjacent, irrelevant):
        analysis.adjacent_evidence = engine._build_adjacent_evidence(
            requirement, analysis.repo
        )

    selected = engine._select_report_projects(
        requirement,
        [irrelevant, adjacent, partial, reliable],
        BudgetUsage(),
    )

    assert [item.repo.name for item in selected] == ["reliable", "partial", "adjacent"]
    assert partial.is_reference_candidate is True
    assert adjacent.confidence_level == "lead"


def test_capability_relevance_is_bound_to_each_claims_own_evidence() -> None:
    analysis = _analysis("filter", 30)
    analysis.verified_capabilities = ["filter Orbit videos", "filter local files"]
    analysis.capability_evidence = [
        EvidenceReference(
            kind="readme",
            locator="README",
            excerpt="This extension can filter Orbit videos.",
        ),
        EvidenceReference(
            kind="readme",
            locator="README",
            excerpt="The utility can filter local files.",
        ),
    ]

    capabilities, evidence = DeepSearchEngine()._relevant_capability_citations(
        _requirement(), analysis
    )

    assert capabilities == ["filter Orbit videos"]
    assert [item.excerpt for item in evidence] == [
        "This extension can filter Orbit videos."
    ]
