from __future__ import annotations

import asyncio
import json

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


def test_evidence_hydration_reserves_capacity_for_second_source_round() -> None:
    ranked = [
        CandidateRepository(
            owner="example",
            name="second-shared",
            url="https://github.com/example/second-shared",
            found_by=["github:shared"],
            raw_score=80,
        ),
        CandidateRepository(
            owner="example",
            name="first-new-d",
            url="https://github.com/example/first-new-d",
            found_by=["github:angle-d"],
            raw_score=70,
        ),
        CandidateRepository(
            owner="example",
            name="first-new-e",
            url="https://github.com/example/first-new-e",
            found_by=["github:angle-e"],
            raw_score=60,
        ),
        CandidateRepository(
            owner="example",
            name="leader-shared",
            url="https://github.com/example/leader-shared",
            found_by=["github:shared"],
            raw_score=100,
        ),
        CandidateRepository(
            owner="example",
            name="leader-b",
            url="https://github.com/example/leader-b",
            found_by=["github:angle-b"],
            raw_score=99,
        ),
        CandidateRepository(
            owner="example",
            name="leader-c",
            url="https://github.com/example/leader-c",
            found_by=["github:angle-c"],
            raw_score=98,
        ),
    ]

    selected = DeepSearchEngine()._evidence_hydration_pool(ranked, limit=6)

    assert [item.full_name for item in selected[:3]] == [
        "example/leader-shared",
        "example/leader-b",
        "example/leader-c",
    ]
    assert selected[-1].full_name == "example/second-shared"
    assert len({item.full_name for item in selected}) == 6


def test_ranking_compares_natural_language_aliases_with_topic_separators() -> None:
    feature = "Aster OS 12 desktop control panel"
    requirement = Requirement(
        raw="Find an Aster OS 12 desktop control panel.",
        intent="Find a desktop control panel",
        must_have_features=[feature],
        nice_to_have_features=[],
        target_platforms=["Aster OS 12"],
        search_queries=["Aster OS 12 desktop control panel"],
        feature_concepts={
            "domains": ["Aster OS 12"],
            "objects": ["desktop control panel"],
        },
        evidence_aliases={feature: ["Aster OS 12 desktop control panel"]},
    )
    aligned = CandidateRepository(
        owner="example",
        name="compact-panel",
        url="https://github.com/example/compact-panel",
        description="A compact utility panel.",
        topics=["aster-os-12", "desktop_panel"],
        found_by=["github_topic:desktop-panel"],
    )
    unrelated = CandidateRepository(
        owner="example",
        name="popular-service",
        url="https://github.com/example/popular-service",
        description="A popular hosted service.",
        stars=100_000,
        topics=["hosted.service"],
        found_by=["github_topic:desktop-panel"],
    )

    ranked = DeepSearchEngine()._rank_candidates(requirement, [unrelated, aligned])
    hydrated = DeepSearchEngine()._evidence_hydration_pool(ranked, limit=1)

    assert ranked[0] is aligned
    assert aligned.raw_score > unrelated.raw_score
    assert hydrated == [aligned]
    assert aligned.topics == ["aster-os-12", "desktop_panel"]


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


def test_scattered_semantic_anchor_cannot_reach_report_without_review() -> None:
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
    assert engine._build_adjacent_evidence(requirement, scattered) is not None

    analysis = _analysis("scattered", 30)
    analysis.repo = scattered
    gated, _ = engine._apply_evidence_gate(
        requirement, [analysis], BudgetUsage()
    )
    decisions: list[dict[str, object]] = []

    assert engine._select_report_projects(
        requirement,
        gated,
        BudgetUsage(),
        decision_trace=decisions,
    ) == []
    assert decisions[0]["reason"] == "semantic_adjacency_not_reviewed"


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


def test_adjacent_capability_does_not_repeat_identical_action_and_object() -> None:
    requirement = Requirement(
        raw="Synchronize Aster records.",
        intent="Find an Aster synchronizer",
        must_have_features=["synchronize Aster records"],
        nice_to_have_features=[],
        target_platforms=[],
        search_queries=["Aster synchronize"],
        report_language="en",
        feature_concepts={
            "domains": ["Aster"],
            "actions": ["synchronize"],
            "objects": ["synchronize"],
        },
    )

    capability = DeepSearchEngine()._adjacent_capability(
        requirement,
        {
            "domains": ["Aster"],
            "actions": ["synchronize"],
            "objects": ["synchronize"],
        },
    )

    assert capability == "synchronize for Aster"


def test_adjacent_evidence_prefers_semantic_breadth_over_source_kind() -> None:
    feature = "Aster workspace: organize records"
    requirement = Requirement(
        raw="Find an Aster workspace for organizing records.",
        intent="Find an Aster records workspace",
        must_have_features=[feature],
        nice_to_have_features=[],
        target_platforms=[],
        search_queries=["Aster records workspace"],
        report_language="en",
        feature_concepts={
            "domains": ["Aster workspace"],
            "actions": ["organize records"],
            "objects": ["records"],
        },
    )
    repo = CandidateRepository(
        owner="example",
        name="workspace",
        url="https://github.com/example/workspace",
        description="An Aster workspace that can organize records.",
        key_files={"src/runtime.py": "from runtime.records import records"},
    )

    candidates = DeepSearchEngine()._adjacent_evidence_candidates(
        requirement,
        repo,
        limit=2,
    )

    assert [item.reference.locator for item in candidates] == [
        "description",
        "src/runtime.py",
    ]
    assert set(candidates[0].group_matches) == {"domains", "actions", "objects"}
    assert candidates[0].relevance_score > candidates[1].relevance_score


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


def test_unreviewed_complete_adjacency_is_not_published_as_verified() -> None:
    engine = DeepSearchEngine()
    analysis = _analysis(
        "unreviewed",
        30,
        description="An Orbit tool that filters videos by duration.",
    )
    gated, _ = engine._apply_evidence_gate(
        _requirement(), [analysis], BudgetUsage()
    )
    decisions: list[dict[str, object]] = []

    selected = engine._select_report_projects(
        _requirement(), gated, BudgetUsage(), decision_trace=decisions
    )

    assert selected == []
    assert gated[0].verified_capabilities == []
    assert decisions[0]["reason"] == "semantic_adjacency_not_reviewed"


def test_reviewed_isolated_capability_is_not_a_lead() -> None:
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

    decisions: list[dict[str, object]] = []
    selected = engine._select_report_projects(
        _requirement(), gated, BudgetUsage(), decision_trace=decisions
    )

    assert selected == []
    assert decisions[0]["reason"] == "no_coherent_partial_pattern"


def test_reviewed_object_rejection_caps_component_partial_at_lead() -> None:
    engine = DeepSearchEngine()
    analysis = _analysis(
        "component-partial",
        30,
        component_evidence=True,
        description="This tool filters Orbit videos by duration.",
    )
    analysis.capability_citations_reviewed = True
    analysis.capability_object_supported = False
    analysis.capability_partial_components = [
        "Orbit video filtering",
        "duration condition",
    ]
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
    assert selected[0].confidence_level == "lead"
    assert selected[0].is_reference_candidate is True


class _Reviewer:
    def __init__(
        self,
        capabilities: list[str],
        *,
        object_supported: bool = True,
        partial_components: list[dict[str, str]] | None = None,
    ) -> None:
        self.capabilities = capabilities
        self.object_supported = object_supported
        self.partial_components = partial_components or []

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
                    "object_supported": self.object_supported,
                    "object_phrase": "Orbit videos" if self.object_supported else "",
                    "partial_components": self.partial_components,
                }
            ]
        }


class _SemanticReviewer:
    def __init__(self, capability: str) -> None:
        self.capability = capability
        self.user_prompt = ""

    async def json_chat(
        self,
        _system: str,
        user: str,
        *,
        operation: str = "chat",
    ) -> dict[str, object]:
        self.user_prompt = user
        return {
            "evidence": [
                {
                    "id": "demo/related#0",
                    "supported": True,
                    "capabilities": [self.capability],
                    "object_supported": True,
                    "object_phrase": "videos",
                    "partial_components": [],
                }
            ]
        }


class _RetryingObjectReviewer:
    def __init__(self) -> None:
        self.calls = 0

    async def json_chat(
        self,
        _system: str,
        user: str,
        *,
        operation: str = "chat",
    ) -> dict[str, object]:
        self.calls += 1
        item = json.loads(user.splitlines()[-1])["evidence"][0]
        if self.calls == 1:
            return {
                "evidence": [
                    {
                        "id": item["id"],
                        "supported": True,
                        "capabilities": ["filter Orbit videos by duration"],
                    }
                ]
            }
        return {
            "evidence": [
                {
                    "id": item["id"],
                    "supported": True,
                    "capabilities": ["filter Orbit videos by duration"],
                    "object_supported": True,
                    "object_phrase": "Orbit videos",
                    "partial_components": [],
                }
            ]
        }


def test_incomplete_object_review_uses_existing_bounded_retry() -> None:
    reviewer = _RetryingObjectReviewer()
    repo = CandidateRepository(
        owner="demo",
        name="filter",
        url="https://github.com/demo/filter",
        readme="This extension can filter Orbit videos by duration.",
    )

    reviewed = asyncio.run(
        DeepSearchEngine()._review_adjacent_capabilities(  # type: ignore[arg-type]
            _requirement(), [repo], reviewer
        )
    )["demo/filter"]

    assert reviewer.calls == 2
    assert reviewed[0] == ["filter Orbit videos by duration"]
    assert reviewed[2] is True
    assert reviewed[3] == []


class _ObjectReviewer:
    def __init__(
        self,
        *,
        object_supported: bool,
        object_phrase: str,
    ) -> None:
        self.object_supported = object_supported
        self.object_phrase = object_phrase

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
                    "id": "demo/related#0",
                    "supported": True,
                    "capabilities": ["filters scheduled entries"],
                    "object_supported": self.object_supported,
                    "object_phrase": self.object_phrase,
                    "partial_components": [],
                }
            ]
        }


def test_nonexact_object_requires_explicit_local_object_adjudication() -> None:
    engine = DeepSearchEngine()
    repo = CandidateRepository(
        owner="demo",
        name="related",
        url="https://github.com/demo/related",
        description="An Orbit tool that filters scheduled entries.",
    )

    accepted = asyncio.run(
        engine._review_adjacent_capabilities(  # type: ignore[arg-type]
            _requirement(),
            [repo],
            _ObjectReviewer(
                object_supported=True,
                object_phrase="scheduled entries",
            ),
        )
    )
    rejected = asyncio.run(
        engine._review_adjacent_capabilities(  # type: ignore[arg-type]
            _requirement(),
            [repo],
            _ObjectReviewer(
                object_supported=True,
                object_phrase="unquoted object",
            ),
        )
    )

    assert accepted["demo/related"][0] == ["filters scheduled entries"]
    assert accepted["demo/related"][2] is True
    assert rejected["demo/related"][0] == ["filters scheduled entries"]
    assert rejected["demo/related"][2] is False


class _IdentityContextReviewer:
    def __init__(self) -> None:
        self.payload: dict[str, object] = {}

    async def json_chat(
        self,
        _system: str,
        user: str,
        *,
        operation: str = "chat",
    ) -> dict[str, object]:
        self.payload = json.loads(user.splitlines()[-1])
        item = self.payload["evidence"][0]  # type: ignore[index]
        return {
            "evidence": [
                {
                    "id": item["id"],
                    "supported": True,
                    "capabilities": [
                        "remains visible while other applications are active"
                    ],
                    "object_supported": True,
                    "object_phrase": "planning surface for recurring work items",
                    "partial_components": [],
                }
            ]
        }


def test_object_review_context_includes_bounded_repository_identity() -> None:
    engine = DeepSearchEngine()
    requirement = _complementary_requirement()
    repo = CandidateRepository(
        owner="demo",
        name="identity",
        url="https://github.com/demo/identity",
        description="A quiet workspace for personal planning.",
        readme=(
            "A planning surface for recurring work items.\n\n"
            "The window remains visible while other applications are active."
        ),
    )
    reviewer = _IdentityContextReviewer()

    reviewed = asyncio.run(
        engine._review_adjacent_capabilities(  # type: ignore[arg-type]
            requirement, [repo], reviewer
        )
    )["demo/identity"]

    item = reviewer.payload["evidence"][0]  # type: ignore[index]
    context_excerpts = {
        context["excerpt"] for context in item["repository_context"]
    }
    assert "A quiet workspace for personal planning." in context_excerpts
    assert "A planning surface for recurring work items." in context_excerpts
    assert reviewed[0] == [
        "remains visible while other applications are active"
    ]


def test_alternative_semantic_anchors_require_review_when_the_other_is_absent() -> None:
    engine = DeepSearchEngine()
    analyses = [
        _analysis(
            "missing-object",
            30,
            description="An Orbit tool that filters scheduled entries.",
        ),
        _analysis(
            "missing-domain",
            30,
            description="This utility filters videos by duration.",
        ),
        _analysis(
            "missing-action",
            30,
            description="An Orbit videos dashboard.",
        ),
        _analysis(
            "domain-only",
            30,
            description="An Orbit desktop utility.",
        ),
    ]
    gated, _ = engine._apply_evidence_gate(
        _requirement(), analyses, BudgetUsage()
    )

    assert all(item.adjacent_evidence is not None for item in gated)
    missing_object = next(
        item for item in gated if item.repo.name == "missing-object"
    )
    assert missing_object.adjacent_evidence is not None
    assert missing_object.adjacent_evidence.capability == ""

    decisions: list[dict[str, object]] = []
    assert engine._select_report_projects(
        _requirement(),
        gated,
        BudgetUsage(),
        decision_trace=decisions,
    ) == []
    assert [item["reason"] for item in decisions] == [
        "semantic_adjacency_not_reviewed",
        "semantic_adjacency_not_reviewed",
        "semantic_adjacency_not_reviewed",
        "semantic_adjacency_not_reviewed",
    ]


def test_reviewed_semantic_adjacency_uses_exact_repository_phrase() -> None:
    engine = DeepSearchEngine()
    analysis = _analysis(
        "related",
        30,
        description="This utility filters videos by duration.",
    )
    reviewer = _SemanticReviewer("filters videos by duration")
    reviewed = asyncio.run(
        engine._review_adjacent_capabilities(  # type: ignore[arg-type]
            _requirement(), [analysis.repo], reviewer
        )
    )["demo/related"]
    (
        analysis.verified_capabilities,
        analysis.capability_evidence,
        analysis.capability_object_supported,
        analysis.capability_partial_components,
    ) = reviewed
    analysis.capability_citations_reviewed = True
    gated, _ = engine._apply_evidence_gate(
        _requirement(), [analysis], BudgetUsage()
    )

    selected = engine._select_report_projects(
        _requirement(), gated, BudgetUsage()
    )

    assert len(selected) == 1
    assert selected[0].adjacent_evidence is not None
    assert selected[0].adjacent_evidence.capability == "filters videos by duration"
    assert selected[0].verified_capabilities == ["filters videos by duration"]
    assert selected[0].confidence_level == "reference"
    assert "each returned phrase is independently material to the current" in (
        reviewer.user_prompt
    )
    assert "Sibling repository_context excerpts may establish" in (
        reviewer.user_prompt
    )
    assert "not as proof that the repository satisfies the complete request" in (
        reviewer.user_prompt
    )
    assert "do not use such an omission by itself to reject" in (
        reviewer.user_prompt
    )


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
    object_rejected = asyncio.run(
        engine._review_adjacent_capabilities(  # type: ignore[arg-type]
            _requirement(),
            [repo],
            _Reviewer(
                ["filter Orbit videos by duration"],
                object_supported=False,
            ),
        )
    )

    assert accepted["demo/filter"][0] == ["filter Orbit videos by duration"]
    assert accepted["demo/filter"][1][0].locator == "README"
    assert rejected["demo/filter"] == ([], [], True, [])
    assert object_rejected["demo/filter"][0] == [
        "filter Orbit videos by duration"
    ]
    assert object_rejected["demo/filter"][2] is False
    assert object_rejected["demo/filter"][3] == []


def test_object_rejected_capability_requires_valid_distinct_partial_components() -> None:
    repo = CandidateRepository(
        owner="demo",
        name="filter",
        url="https://github.com/demo/filter",
        readme="This extension can filter Orbit videos by duration.",
    )
    reviewer = _Reviewer(
        ["filter Orbit videos by duration"],
        object_supported=False,
        partial_components=[
            {
                "component": "Orbit video filtering",
                "phrase": "filter Orbit videos",
            },
            {
                "component": "duration condition",
                "phrase": "by duration",
            },
            {
                "component": "unknown component",
                "phrase": "This extension",
            },
            {
                "component": "duration condition",
                "phrase": "non-local phrase",
            },
        ],
    )

    reviewed = asyncio.run(
        DeepSearchEngine()._review_adjacent_capabilities(  # type: ignore[arg-type]
            _requirement(), [repo], reviewer
        )
    )["demo/filter"]

    assert reviewed[3] == ["Orbit video filtering", "duration condition"]
    assert set(reviewed[0]) == {
        "filter Orbit videos by duration",
        "filter Orbit videos",
        "by duration",
    }
    assert all(
        any(capability in reference.excerpt for reference in reviewed[1])
        for capability in reviewed[0]
    )


def test_single_partial_component_does_not_publish_its_context_phrase() -> None:
    repo = CandidateRepository(
        owner="demo",
        name="filter",
        url="https://github.com/demo/filter",
        readme="This extension can filter Orbit videos by duration.",
    )
    reviewer = _Reviewer(
        ["filter Orbit videos by duration"],
        object_supported=False,
        partial_components=[
            {
                "component": "Orbit video filtering",
                "phrase": "filter Orbit videos",
            }
        ],
    )

    reviewed = asyncio.run(
        DeepSearchEngine()._review_adjacent_capabilities(  # type: ignore[arg-type]
            _requirement(), [repo], reviewer
        )
    )["demo/filter"]

    assert reviewed[0] == ["filter Orbit videos by duration"]
    assert reviewed[3] == ["Orbit video filtering"]


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
                    "object_supported": True,
                    "object_phrase": "videos",
                    "partial_components": [],
                },
                {
                    "id": "demo/filter#1",
                    "supported": True,
                    "capabilities": ["filter Orbit videos"],
                    "object_supported": True,
                    "object_phrase": "Orbit videos",
                    "partial_components": [],
                },
            ]
        }


class _MixedObjectReviewer:
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
                    "object_supported": True,
                    "object_phrase": "videos",
                    "partial_components": [],
                },
                {
                    "id": "demo/filter#1",
                    "supported": True,
                    "capabilities": ["filter Orbit videos"],
                    "object_supported": False,
                    "object_phrase": "",
                    "partial_components": [],
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

    capabilities, evidence, object_supported, partial_components = asyncio.run(
        DeepSearchEngine()._review_adjacent_capabilities(  # type: ignore[arg-type]
            _requirement(), [repo], _WindowReviewer()
        )
    )["demo/filter"]

    assert set(capabilities) == {"filter videos by duration", "filter Orbit videos"}
    assert {item.locator for item in evidence} == {"README", "description"}
    assert object_supported is True
    assert partial_components == []


def test_repository_object_support_requires_consistent_item_judgments() -> None:
    repo = CandidateRepository(
        owner="demo",
        name="filter",
        url="https://github.com/demo/filter",
        description="This tool can filter Orbit videos.",
        readme="Orbit users can filter videos by duration.",
    )

    reviewed = asyncio.run(
        DeepSearchEngine()._review_adjacent_capabilities(  # type: ignore[arg-type]
            _requirement(), [repo], _MixedObjectReviewer()
        )
    )["demo/filter"]

    assert reviewed[0]
    assert reviewed[2] is False


def _complementary_requirement() -> Requirement:
    return Requirement(
        raw="Find an Aster board that remains visible.",
        intent="Find a persistent Aster board",
        must_have_features=["Aster board remains visible"],
        nice_to_have_features=["BorealisOS package"],
        target_platforms=[],
        search_queries=["Aster board visible"],
        report_language="en",
        feature_concepts={
            "domains": ["Aster"],
            "actions": ["remains visible"],
            "objects": ["board"],
        },
    )


class _ComplementaryReviewer:
    def __init__(self) -> None:
        self.payload: dict[str, object] = {}

    async def json_chat(
        self,
        _system: str,
        user: str,
        *,
        operation: str = "chat",
    ) -> dict[str, object]:
        self.payload = json.loads(user.splitlines()[-1])
        evidence = []
        for item in self.payload["evidence"]:  # type: ignore[index]
            excerpt = str(item["excerpt"])
            capability = (
                "An Aster board for local work"
                if "Aster board" in excerpt
                else "remains visible while other applications are active"
            )
            evidence.append(
                {
                    "id": item["id"],
                    "supported": True,
                    "capabilities": [capability],
                    "object_supported": True,
                    "object_phrase": "Aster board",
                    "partial_components": [],
                }
            )
        return {"evidence": evidence}


def test_complementary_repository_sections_share_context_but_keep_exact_claims() -> None:
    engine = DeepSearchEngine()
    requirement = _complementary_requirement()
    analysis = _analysis("related", 30)
    analysis.repo = CandidateRepository(
        owner="demo",
        name="related",
        url="https://github.com/demo/related",
        description="An Aster board for local work.",
        readme="The window remains visible while other applications are active.",
    )
    reviewer = _ComplementaryReviewer()

    reviewed = asyncio.run(
        engine._review_adjacent_capabilities(  # type: ignore[arg-type]
            requirement, [analysis.repo], reviewer
        )
    )["demo/related"]

    payload_items = reviewer.payload["evidence"]  # type: ignore[index]
    assert len(payload_items) == 2
    assert all(len(item["repository_context"]) == 2 for item in payload_items)
    assert reviewer.payload["core_requirements"] == ["Aster board remains visible"]
    assert reviewer.payload["optional_preferences"] == ["BorealisOS package"]
    assert reviewer.payload["core_concepts"] == {
        "domains": ["aster"],
        "actions": ["remains visible"],
        "objects": ["board"],
    }
    assert set(reviewed[0]) == {
        "An Aster board for local work",
        "remains visible while other applications are active",
    }
    assert all(
        capability in reference.excerpt
        for capability, reference in zip(reviewed[0], reviewed[1], strict=True)
    )

    (
        analysis.verified_capabilities,
        analysis.capability_evidence,
        analysis.capability_object_supported,
        analysis.capability_partial_components,
    ) = reviewed
    analysis.capability_citations_reviewed = True
    gated, _ = engine._apply_evidence_gate(
        requirement, [analysis], BudgetUsage()
    )
    selected = engine._select_report_projects(requirement, gated, BudgetUsage())

    assert len(selected) == 1
    assert set(selected[0].verified_capabilities) == set(reviewed[0])
    assert selected[0].confidence_level == "reference"


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
        70,
        description="An Orbit tool that filters videos by duration.",
    )
    irrelevant = _analysis("poster", 95, description="An Orbit video poster generator.")
    for analysis in (partial, adjacent, irrelevant):
        analysis.adjacent_evidence = engine._build_adjacent_evidence(
            requirement, analysis.repo
        )
    partial.capability_citations_reviewed = True
    partial.capability_object_supported = True
    partial.verified_capabilities = ["Orbit video filter"]
    partial.capability_evidence = [
        EvidenceReference(
            kind="repository_metadata",
            locator="description",
            excerpt=partial.repo.description,
        )
    ]
    adjacent.capability_citations_reviewed = True
    adjacent.capability_partial_components = [
        "Orbit video filtering",
        "duration condition",
    ]
    adjacent.verified_capabilities = ["filters videos by duration"]
    adjacent.capability_evidence = [
        EvidenceReference(
            kind="repository_metadata",
            locator="description",
            excerpt=adjacent.repo.description,
        )
    ]

    selected = engine._select_report_projects(
        requirement,
        [irrelevant, adjacent, partial, reliable],
        BudgetUsage(),
    )

    assert [item.repo.name for item in selected] == ["reliable", "partial", "adjacent"]
    assert partial.is_reference_candidate is True
    assert adjacent.confidence_level == "lead"


def test_reviewed_adjacent_projection_keeps_capability_reference_paired() -> None:
    engine = DeepSearchEngine()
    analysis = _analysis(
        "paired",
        30,
        description="This tool filters Orbit videos by duration.",
    )
    analysis.adjacent_evidence = engine._build_adjacent_evidence(
        _requirement(), analysis.repo
    )
    assert analysis.adjacent_evidence is not None
    analysis.adjacent_evidence.reference = EvidenceReference(
        kind="repository_metadata",
        locator="description",
        excerpt="Orbit settings are available.",
    )
    analysis.capability_citations_reviewed = True
    analysis.capability_object_supported = True
    analysis.verified_capabilities = ["filters Orbit videos by duration"]
    analysis.capability_evidence = [
        EvidenceReference(
            kind="repository_metadata",
            locator="description",
            excerpt="This tool filters Orbit videos by duration.",
        )
    ]

    selected = engine._select_report_projects(
        _requirement(), [analysis], BudgetUsage()
    )

    assert len(selected) == 1
    assert selected[0].adjacent_evidence is not None
    assert selected[0].adjacent_evidence.capability == (
        "filters Orbit videos by duration"
    )
    assert selected[0].adjacent_evidence.capability in (
        selected[0].adjacent_evidence.reference.excerpt
    )


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
