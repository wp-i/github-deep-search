from __future__ import annotations

import asyncio

from github_deep_search.engine import DeepSearchEngine
from github_deep_search.models import (
    BudgetUsage,
    CandidateRepository,
    EvidenceCoverage,
    ProjectAnalysis,
    Requirement,
)


def _analysis(repo: CandidateRepository, score: int = 90) -> ProjectAnalysis:
    return ProjectAnalysis(
        repo=repo,
        match_score=score,
        recommendation="Model recommendation",
        directly_usable=True,
        covered_features=["unsupported model claim"],
        missing_features=[],
        required_changes=["Model says an unmentioned feature must be added"],
        risks=["Model says an unmentioned capability is absent"],
        evidence=["unsupported model evidence"],
    )


class _AnalysisPromptLLM:
    def __init__(self) -> None:
        self.user_prompt = ""

    async def json_chat(
        self,
        _system: str,
        user: str,
        *,
        operation: str = "chat",
    ) -> dict[str, object]:
        assert operation == "repository_analysis"
        self.user_prompt = user
        return {
            "projects": [
                {
                    "repo": "demo/planner",
                    "match_score": 10,
                    "directly_usable": False,
                    "covered_features": [],
                    "different_features": [],
                    "missing_features": [],
                    "unknown_features": ["AsterOS desktop planner"],
                    "required_changes": [],
                    "risks": [],
                    "evidence": [],
                    "component_citations": [],
                    "difference_citations": [],
                }
            ]
        }


def test_repository_analysis_contract_distinguishes_explicit_alternatives() -> None:
    feature = "AsterOS desktop planner"
    requirement = Requirement(
        raw="Find an AsterOS desktop planner.",
        intent="Find a desktop planner",
        must_have_features=[feature],
        nice_to_have_features=[],
        target_platforms=["AsterOS"],
        search_queries=["AsterOS desktop planner"],
        evidence_components={
            feature: {
                "platform": ["AsterOS desktop"],
                "artifact": ["desktop planner"],
            }
        },
    )
    repo = CandidateRepository(
        owner="demo",
        name="planner",
        url="https://github.com/demo/planner",
        readme="A desktop planner available only for BorealisOS.",
        evidence_coverage=[
            EvidenceCoverage(
                feature=feature,
                covered=False,
                status="unknown",
                required_component_count=2,
            )
        ],
    )
    llm = _AnalysisPromptLLM()

    analyses = asyncio.run(
        DeepSearchEngine()._analyze_top_projects(  # type: ignore[arg-type]
            requirement, [repo], llm
        )
    )

    assert "mutually exclusive alternative to a requested constraint" in llm.user_prompt
    assert "different, not unknown" in llm.user_prompt
    assert "merely unmentioned or unproved remains unknown" in llm.user_prompt
    assert '"recommendation"' not in llm.user_prompt
    assert analyses[0].recommendation == ""


def test_evidence_coverage_tracks_readme_and_source_locations() -> None:
    engine = DeepSearchEngine()
    requirement = Requirement(
        raw="Summarize web pages and sync summaries to Notion.",
        intent="Find a browser extension",
        must_have_features=["summarize web pages", "sync summaries to Notion"],
        nice_to_have_features=[],
        target_platforms=["browser extension"],
        search_queries=["web page summary Notion extension"],
        evidence_aliases={
            "summarize web pages": ["summarize web pages"],
            "sync summaries to Notion": [
                "sync summaries to Notion",
                "sync_summaries_to_notion",
            ],
        },
    )
    repo = CandidateRepository(
        owner="demo",
        name="extension",
        url="https://github.com/demo/extension",
        readme="This extension can summarize web pages.",
        file_paths=["src/sync_summaries_to_notion.ts"],
        key_files={
            "src/sync_summaries_to_notion.ts": (
                "// sync summaries to Notion\n"
                "export async function syncSummariesToNotion() {}"
            )
        },
    )

    coverage = engine._build_evidence_coverage(repo, requirement)
    by_feature = {item.feature: item for item in coverage}

    assert by_feature["summarize web pages"].status == "supported"
    assert by_feature["summarize web pages"].readme_evidence
    assert by_feature["sync summaries to Notion"].status == "supported"
    assert by_feature["sync summaries to Notion"].source_evidence
    assert all(item.evidence_references for item in coverage)


def test_path_name_is_provisional_until_sampled_content_proves_behavior() -> None:
    engine = DeepSearchEngine()
    feature = "automatic document conversion"
    requirement = Requirement(
        raw="Find a tool that automatically converts documents.",
        intent="Find an automatic document converter",
        must_have_features=[feature],
        nice_to_have_features=[],
        target_platforms=[],
        search_queries=[feature],
        evidence_components={
            feature: {
                "conversion behavior": ["automatic conversion", "converts documents"],
            }
        },
    )
    path_only_repo = CandidateRepository(
        owner="demo",
        name="path-only",
        url="https://github.com/demo/path-only",
        file_paths=["planning/automatic conversion roadmap.md"],
    )
    sampled_repo = CandidateRepository(
        owner="demo",
        name="sampled",
        url="https://github.com/demo/sampled",
        file_paths=["src/converter.ts"],
        key_files={
            "src/converter.ts": (
                "// Converts documents through automatic conversion\n"
                "export function convert() {}"
            ),
        },
    )

    path_only = engine._build_evidence_coverage(path_only_repo, requirement)[0]
    sampled = engine._build_evidence_coverage(sampled_repo, requirement)[0]

    assert path_only.status == "unknown"
    assert path_only.covered is False
    assert path_only.path_evidence
    assert path_only.component_evidence["conversion behavior"]
    assert any(reference.kind == "path" for reference in path_only.evidence_references)
    assert sampled.status == "supported"
    assert sampled.covered is True
    assert sampled.source_evidence


def test_unconfirmed_feature_stays_unknown_and_is_not_reported_as_missing() -> None:
    engine = DeepSearchEngine()
    requirement = Requirement(
        raw="Summarize web pages and export PDF.",
        intent="Find a browser extension",
        must_have_features=["summarize web pages", "export PDF"],
        nice_to_have_features=[],
        target_platforms=[],
        search_queries=["web summary PDF"],
        evidence_aliases={
            "summarize web pages": ["summarize web pages"],
            "export PDF": ["export PDF"],
        },
    )
    repo = CandidateRepository(
        owner="demo",
        name="summary",
        url="https://github.com/demo/summary",
        readme="Summarize web pages in the browser.",
    )

    gated, stats = engine._apply_evidence_gate(
        requirement,
        [_analysis(repo)],
        BudgetUsage(),
    )

    assert gated[0].covered_features == ["summarize web pages"]
    assert gated[0].unknown_features == ["export PDF"]
    assert gated[0].missing_features == []
    assert gated[0].directly_usable is False
    assert gated[0].core_confirmed is False
    assert gated[0].match_score <= 49
    assert gated[0].risks == []
    assert gated[0].required_changes == []
    assert gated[0].recommendation == gated[0].score_reason
    assert gated[0].recommendation != "Model recommendation"
    assert stats["unknown_feature_count"] == 1


def test_complete_repository_evidence_can_produce_a_high_score() -> None:
    engine = DeepSearchEngine()
    features = [
        "self-hosted",
        "OCR",
        "full-text search",
        "automatic tagging",
        "Docker deployment",
        "local data storage",
    ]
    requirement = Requirement(
        raw="Self-hosted document manager with OCR, search, tags, Docker and local data.",
        intent="Find a document manager",
        must_have_features=features,
        nice_to_have_features=[],
        target_platforms=[],
        search_queries=["self hosted OCR document manager"],
        evidence_aliases={
            "self-hosted": ["self-hosted document manager"],
            "OCR": ["uses OCR"],
            "full-text search": ["searchable archive"],
            "automatic tagging": ["automatic tagging"],
            "Docker deployment": ["Docker Compose"],
            "local data storage": ["local data storage"],
        },
    )
    repo = CandidateRepository(
        owner="demo",
        name="documents",
        url="https://github.com/demo/documents",
        readme=(
            "A self-hosted document manager that uses OCR to create a searchable archive. "
            "It supports automatic tagging, Docker Compose, and local data storage."
        ),
    )

    gated, _ = engine._apply_evidence_gate(
        requirement,
        [_analysis(repo)],
        BudgetUsage(),
    )

    assert gated[0].core_confirmed is True
    assert gated[0].covered_features == features
    assert gated[0].match_score >= 80


def test_compound_requirement_is_not_confirmed_by_one_component() -> None:
    engine = DeepSearchEngine()
    feature = "filter Aster portal records with a browser extension"
    requirement = Requirement(
        raw="Need a browser extension that filters Aster portal records.",
        intent="Find an Aster record filter",
        must_have_features=[feature],
        nice_to_have_features=[],
        target_platforms=["browser extension"],
        search_queries=["Aster portal filter extension"],
        feature_concepts={
            "domains": ["Aster portal"],
            "actions": ["filter"],
            "objects": ["records"],
            "interfaces": ["browser extension"],
        },
        evidence_aliases={feature: ["filter Aster portal records"]},
        evidence_components={
            feature: {
                "domain": ["Aster portal"],
                "action": ["filter records"],
                "interface": ["browser extension"],
            }
        },
    )
    repo = CandidateRepository(
        owner="demo",
        name="generic-filter",
        url="https://github.com/demo/generic-filter",
        readme="A browser extension that filters records.",
    )

    coverage = engine._build_evidence_coverage(repo, requirement)
    gated, _ = engine._apply_evidence_gate(
        requirement,
        [_analysis(repo)],
        BudgetUsage(),
    )

    assert coverage[0].covered is False
    assert "domain" not in coverage[0].component_evidence
    assert gated[0].covered_features == []
    assert gated[0].core_confirmed is False
    assert gated[0].match_score <= 49


def test_current_request_aliases_are_used_without_cross_language_rescue() -> None:
    engine = DeepSearchEngine()
    requirement = Requirement(
        raw="找一个浏览器插件",
        intent="寻找浏览器插件",
        must_have_features=["浏览器插件"],
        nice_to_have_features=[],
        target_platforms=[],
        search_queries=["browser extension", "浏览器 插件"],
        evidence_aliases={"浏览器插件": ["browser extension"]},
    )
    repo = CandidateRepository(
        owner="demo",
        name="extension",
        url="https://github.com/demo/extension",
        readme="A browser extension for saving pages.",
    )

    coverage = engine._build_evidence_coverage(repo, requirement)

    assert coverage[0].status == "unknown"
    assert coverage[0].covered is False


def test_evidence_gate_discards_unverified_model_claims() -> None:
    engine = DeepSearchEngine()
    feature = "automated map puzzle completion"
    requirement = Requirement(
        raw=feature,
        intent="Find automation",
        must_have_features=[feature],
        nice_to_have_features=[],
        target_platforms=[],
        search_queries=[feature],
        evidence_aliases={feature: [feature]},
    )
    repo = CandidateRepository(
        owner="demo",
        name="runtime",
        url="https://github.com/demo/runtime",
        readme="An Android virtual runtime.",
    )

    gated, _ = engine._apply_evidence_gate(
        requirement,
        [_analysis(repo, score=100)],
        BudgetUsage(),
    )

    assert gated[0].covered_features == []
    assert gated[0].evidence == []
    assert gated[0].core_confirmed is False
    assert gated[0].match_score <= 49


def test_unconfirmed_core_scores_preserve_evidence_differences_below_ceiling() -> None:
    engine = DeepSearchEngine()
    core = "combine Aster records into a dashboard"
    constraint = "available without a subscription"
    requirement = Requirement(
        raw=f"{core}; {constraint}",
        intent="Find an Aster dashboard",
        must_have_features=[core, constraint],
        nice_to_have_features=[],
        target_platforms=[],
        search_queries=["Aster dashboard"],
        feature_concepts={
            "domains": ["Aster"],
            "actions": ["combine"],
            "objects": ["records"],
            "outputs": ["dashboard"],
        },
        evidence_components={
            core: {
                "domain": ["Aster"],
                "action": ["combine"],
                "output": ["dashboard"],
            },
            constraint: {
                "availability": ["without a subscription"],
            },
        },
    )
    strong = _analysis(
        CandidateRepository(
            owner="demo",
            name="strong",
            url="https://github.com/demo/strong",
            description="Aster dashboard",
        )
    )
    weak = _analysis(
        CandidateRepository(
            owner="demo",
            name="weak",
            url="https://github.com/demo/weak",
            description="Aster dashboard",
        )
    )
    strong.evidence_coverage = [
        EvidenceCoverage(
            feature=core,
            covered=False,
            status="unknown",
            component_evidence={"domain": ["Aster"], "action": ["combine"]},
            required_component_count=3,
        ),
        EvidenceCoverage(feature=constraint, covered=True, status="supported"),
    ]
    weak.evidence_coverage = [
        EvidenceCoverage(
            feature=core,
            covered=False,
            status="unknown",
            component_evidence={"domain": ["Aster"]},
            required_component_count=3,
        ),
        EvidenceCoverage(feature=constraint, covered=True, status="supported"),
    ]

    gated, stats = engine._apply_evidence_gate(
        requirement,
        [strong, weak],
        BudgetUsage(),
    )
    scores = {analysis.repo.name: analysis.match_score for analysis in gated}

    assert 0 <= scores["weak"] < scores["strong"] <= 49
    assert stats["score_capped_count"] == 2


def test_verified_component_citation_must_match_repository_material_and_alias() -> None:
    engine = DeepSearchEngine()
    feature = "combine Aster and Boreal measurements"
    excerpt = "This project reads Aster measurements from the public endpoint."
    requirement = Requirement(
        raw=feature,
        intent=feature,
        must_have_features=[feature],
        nice_to_have_features=[],
        target_platforms=[],
        search_queries=[feature],
        evidence_aliases={feature: ["Aster measurements", "Boreal measurements"]},
        evidence_components={
            feature: {
                "Aster input": ["Aster measurements"],
                "Boreal input": ["Boreal measurements"],
            }
        },
    )
    repo = CandidateRepository(
        owner="demo",
        name="aster-reader",
        url="https://github.com/demo/aster-reader",
        readme=excerpt,
    )
    coverage = engine._build_evidence_coverage(repo, requirement)

    verified = engine._apply_verified_component_citations(
        repo,
        requirement,
        coverage,
        [
            {
                "feature": feature,
                "component": "Aster input",
                "locator": "README",
                "excerpt": excerpt,
            },
            {
                "feature": feature,
                "component": "Boreal input",
                "locator": "README",
                "excerpt": excerpt,
            },
        ],
    )

    assert list(verified[0].component_evidence) == ["Aster input"]
    assert verified[0].covered is False


def test_readme_component_match_requires_verified_repository_local_citation() -> None:
    engine = DeepSearchEngine()
    feature = "operate without a subscription"
    excerpt = "This repository operates without a subscription."
    requirement = Requirement(
        raw=feature,
        intent=feature,
        must_have_features=[feature],
        nice_to_have_features=[],
        target_platforms=[],
        search_queries=[feature],
        evidence_aliases={feature: ["without a subscription"]},
        evidence_components={
            feature: {
                "availability": ["without a subscription"],
            }
        },
    )
    repo = CandidateRepository(
        owner="demo",
        name="local-runtime",
        url="https://github.com/demo/local-runtime",
        readme=excerpt,
    )

    provisional = engine._build_evidence_coverage(repo, requirement)

    assert provisional[0].status == "unknown"
    assert provisional[0].covered is False
    assert provisional[0].readme_evidence == []
    assert "availability" in provisional[0].component_evidence

    verified = engine._apply_verified_component_citations(
        repo,
        requirement,
        provisional,
        [
            {
                "feature": feature,
                "component": "availability",
                "locator": "README",
                "excerpt": excerpt,
            }
        ],
    )

    assert verified[0].status == "supported"
    assert verified[0].covered is True
    assert verified[0].readme_evidence == [f"README: {excerpt}"]


def test_source_evidence_scores_above_readme_only_evidence() -> None:
    engine = DeepSearchEngine()
    feature = "export PDF"
    requirement = Requirement(
        raw=feature,
        intent=feature,
        must_have_features=[feature],
        nice_to_have_features=[],
        target_platforms=[],
        search_queries=[feature],
        evidence_aliases={feature: [feature]},
    )
    source_repo = CandidateRepository(
        owner="demo",
        name="source",
        url="https://github.com/demo/source",
        key_files={"src/pdf.ts": "// export PDF\nexport function createPdf() {}"},
    )
    readme_repo = CandidateRepository(
        owner="demo",
        name="readme",
        url="https://github.com/demo/readme",
        readme="This project can export PDF.",
    )

    source_score = engine._verified_match_score(
        requirement,
        source_repo,
        engine._build_evidence_coverage(source_repo, requirement),
        None,
    )
    readme_score = engine._verified_match_score(
        requirement,
        readme_repo,
        engine._build_evidence_coverage(readme_repo, requirement),
        None,
    )

    assert source_score > readme_score


def test_catalog_repository_is_not_treated_as_a_project_candidate() -> None:
    engine = DeepSearchEngine()
    repo = CandidateRepository(
        owner="demo",
        name="catalog",
        url="https://github.com/demo/catalog",
        readme=(
            "\n".join(f"- https://github.com/demo/project-{index}" for index in range(220))
            + ("\nCatalog description." * 1200)
        ),
    )

    assert engine._is_catalog_repository(repo) is True


def test_executor_does_not_invent_queries_from_evidence_aliases() -> None:
    requirement = Requirement(
        raw="Need a scheduler.",
        intent="Find a scheduler",
        must_have_features=["constraint scheduling"],
        nice_to_have_features=[],
        target_platforms=[],
        search_queries=["scheduler", "调度器"],
        repo_search_queries=[],
        evidence_aliases={
            "constraint scheduling": ["constraint solver", "task scheduler"]
        },
    )

    assert DeepSearchEngine()._planned_repo_search_queries(requirement) == []
