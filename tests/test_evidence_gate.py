from __future__ import annotations

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
