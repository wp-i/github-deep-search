from __future__ import annotations

import json
from dataclasses import replace

import pytest

from github_deep_search.config import Settings
from github_deep_search.models import (
    CandidateRepository,
    EvidenceMaterial,
    EvidenceRepository,
    EvidenceResult,
    ParsedRequirement,
    RequirementAssessment,
    RunRequest,
    SearchQueryPair,
    Usage,
    ValidatedInput,
    VerificationRequirement,
)
from github_deep_search.pipeline import PipelineContext, PipelineFailure
from github_deep_search.providers.llm import LLMProviderError
from github_deep_search.stages.analysis import (
    AnalysisStage,
    _Requirement,
    _focused_windows,
    _requirements,
    _score_assessments,
)


_DEFAULT_FINAL = object()


class FakeAnalysisClient:
    def __init__(
        self,
        payload=None,
        failure: LLMProviderError | None = None,
        *,
        final_response=_DEFAULT_FINAL,
        final_failure: LLMProviderError | None = None,
    ) -> None:
        self.payload = payload
        self.failure = failure
        self.final_response = final_response
        self.final_failure = final_failure
        self.last_failure: LLMProviderError | None = None
        self.calls: list[tuple[str, str, str]] = []
        self.closed = False

    async def json_chat(self, system: str, user: str, *, operation: str = "chat"):
        self.calls.append((system, user, operation))
        if operation == "finalize_analysis":
            self.last_failure = self.final_failure
            if self.final_failure is not None:
                return None
            if self.final_response is _DEFAULT_FINAL:
                return _final_payload(self.payload)
            return self.final_response
        self.last_failure = self.failure
        return self.payload

    async def close(self) -> None:
        self.closed = True


def settings() -> Settings:
    return Settings(
        github_token="github-token",
        llm_api_key="llm-key",
        llm_base_url="https://provider.example/v1",
        llm_model="model",
        llm_thinking=None,
        llm_reasoning_effort=None,
        max_github_requests=200,
        max_candidates=80,
        max_evidence_repositories=12,
        run_timeout_seconds=600,
    )


def parsed_requirement() -> ParsedRequirement:
    return ParsedRequirement(
        complete_requirement="Find a desktop tool with export, offline use, and no cloud dependency.",
        core_goal="Find a desktop tool.",
        reasonable_interpretations=("A ready-to-use desktop application.",),
        functional_requirements=("Desktop application", "Export results"),
        constraints=("Works offline",),
        preferences=("Easy installation",),
        exclusions=("Mandatory cloud service",),
        search_query_pairs=(
            SearchQueryPair("desktop", "桌面 工具", "desktop tool"),
            SearchQueryPair("export", "导出", "export"),
        ),
        evidence_targets=("desktop implementation", "offline operation", "export"),
        suggested_repositories=(),
        verification_requirements=(
            VerificationRequirement(
                "function",
                "Desktop application",
                ("Desktop application",),
                check_facets=("capability",),
            ),
            VerificationRequirement(
                "function",
                "Export results",
                ("Export results",),
                check_facets=("capability",),
            ),
            VerificationRequirement(
                "constraint",
                "Works offline",
                ("Works offline",),
                check_facets=("runtime",),
            ),
            VerificationRequirement(
                "preference",
                "Easy installation",
                ("Easy installation",),
                check_facets=("preference",),
            ),
            VerificationRequirement(
                "exclusion",
                "Mandatory cloud service",
                ("Mandatory cloud service",),
                check_facets=("exclusion",),
            ),
        ),
    )


def evidence_result(count: int = 4) -> EvidenceResult:
    repositories = []
    for index in range(1, count + 1):
        repository = CandidateRepository(
            owner=f"owner{index}",
            name=f"project{index}",
            url=f"https://github.com/owner{index}/project{index}",
            description=f"Project {index}",
            last_pushed_at=f"2026-0{index}-01T00:00:00Z",
            license="MIT",
            latest_release_at=f"2026-0{index}-02T00:00:00Z",
        )
        repositories.append(
            EvidenceRepository(
                repository=repository,
                materials=(
                    EvidenceMaterial(
                        kind="readme",
                        path="README.md",
                        url=f"{repository.url}/blob/main/README.md",
                        excerpt=f"Implemented desktop export and offline workflow for project {index}.",
                    ),
                ),
                tree_paths=("README.md", "src/main.py"),
                relation_kind="original",
                relation_key=repository.full_name.casefold(),
            )
        )
    return EvidenceResult(tuple(repositories), (), count, 0)


def context(count: int = 4, *, language: str = "en") -> PipelineContext:
    raw_input = "Find a desktop tool with export, offline use, and no cloud dependency."
    return PipelineContext(
        run_id="run-1",
        request=RunRequest(raw_input),
        settings=settings(),
        validated_input=ValidatedInput(raw_input, language),
        parsed_requirement=parsed_requirement(),
        evidence_result=evidence_result(count),
        usage=Usage(),
    )


def valid_payload(count: int = 4, *, eligible_count: int | None = None) -> dict:
    eligible_count = count if eligible_count is None else eligible_count
    decisions = []
    for index in range(1, count + 1):
        repository = f"owner{index}/project{index}"
        decisions.append(
            {
                "repository": repository,
                "eligible": index <= eligible_count,
                "canonical_project": repository,
                "qualification_reason": "README contains an implemented workflow.",
                "matched_function_ids": ["F1"] if index <= eligible_count else [],
                "evidence_ids": [f"R{index:02d}:M01:S01"],
            }
        )
    selected = []
    for index in range(1, min(3, eligible_count) + 1):
        selected.append(
            {
                "repository": f"owner{index}/project{index}",
                "selection_reason": "Strong coverage of the complete request.",
            }
        )
    return {"candidate_decisions": decisions, "selected_projects": selected}


def _final_payload(selection: dict) -> dict:
    requirement_ids = ("F1", "F2", "C1", "P1", "X1")
    decision_map = {
        item["repository"]: item for item in selection["candidate_decisions"]
    }
    ranked = []
    for item in selection["selected_projects"]:
        repository = item["repository"]
        evidence_id = decision_map[repository]["evidence_ids"][0]
        ranked.append(
            {
                "repository": repository,
                "assessments": [
                    {
                        "requirement_id": requirement_id,
                        "status": "supported" if requirement_id != "X1" else "unverified",
                        "explanation": "The supplied repository evidence supports this decision.",
                        "evidence_ids": [evidence_id] if requirement_id != "X1" else [],
                    }
                    for requirement_id in requirement_ids
                ],
            }
        )
    return {"ranked_projects": ranked}


def test_analysis_uses_atomic_parse_checks_as_its_only_scored_requirements() -> None:
    pipeline_context = context()
    pipeline_context.parsed_requirement = replace(
        parsed_requirement(),
        verification_requirements=(
            VerificationRequirement(
                "function",
                "Search Chinese documents",
                ("Provide full-text search", "Support Chinese text"),
                check_facets=("capability", "natural_language"),
            ),
            VerificationRequirement(
                "constraint",
                "Run on the internal network",
                ("Support self-hosted deployment",),
                check_facets=("scope",),
            ),
        ),
    )

    requirements = _requirements(pipeline_context)

    assert [(item.id, item.kind, item.text) for item in requirements] == [
        ("F1", "function", "Provide full-text search"),
        ("F2", "function", "Support Chinese text"),
        ("C1", "constraint", "Support self-hosted deployment"),
    ]
    assert requirements[1].parent_requirement == "Search Chinese documents"
    assert requirements[1].facet == "natural_language"


@pytest.mark.asyncio
async def test_analysis_rejects_missing_atomic_requirement_inventory() -> None:
    pipeline_context = context()
    pipeline_context.parsed_requirement = replace(
        parsed_requirement(),
        verification_requirements=(),
    )
    client = FakeAnalysisClient(valid_payload())

    with pytest.raises(PipelineFailure) as caught:
        await AnalysisStage(client).execute(pipeline_context)

    assert caught.value.code == "analysis_prerequisite_missing"
    assert client.calls == []


def test_analysis_score_groups_atomic_checks_by_parent_requirement() -> None:
    requirements = {
        "F1": _Requirement("F1", "function", "Search", "Search Chinese text", "capability"),
        "F2": _Requirement(
            "F2", "function", "Chinese text", "Search Chinese text", "natural_language"
        ),
        "C1": _Requirement("C1", "constraint", "Run locally", "Run locally", "runtime"),
        "P1": _Requirement("P1", "preference", "Simple UI", "Simple UI", "preference"),
    }
    assessments = (
        RequirementAssessment("F1", "function", "Search", "supported", "ok", ()),
        RequirementAssessment("F2", "function", "Chinese text", "partial", "some", ()),
        RequirementAssessment("C1", "constraint", "Run locally", "partial", "some", ()),
        RequirementAssessment("P1", "preference", "Simple UI", "supported", "ok", ()),
    )

    assert _score_assessments(assessments, requirements) == 69


def test_analysis_focus_keeps_relevant_original_windows_with_hard_bounds() -> None:
    text = "prefix\n" + ("x" * 4000) + "search capability evidence" + ("y" * 4000)
    windows = _focused_windows(
        text,
        ("search",),
        window_length=900,
        max_windows=2,
    )

    assert len(windows) == 2
    assert all(len(window) <= 900 for window in windows)
    assert windows[0] == text.strip()[:900]
    assert "search capability evidence" in windows[1]
    assert all(window in text for window in windows)


@pytest.mark.asyncio
async def test_analysis_compares_every_repository_once_and_builds_one_ranked_result() -> None:
    client = FakeAnalysisClient(valid_payload())
    stage = AnalysisStage(client)
    pipeline_context = context()

    await stage.execute(pipeline_context)

    assert len(client.calls) == 2
    assert client.calls[0][2] == "analyze_evidence"
    assert client.calls[1][2] == "finalize_analysis"
    request = json.loads(client.calls[0][1])
    assert request["raw_input"] == pipeline_context.request.raw_input
    assert [item["id"] for item in request["parsed_requirement"]["requirements"]] == [
        "F1",
        "F2",
        "C1",
        "P1",
        "X1",
    ]
    assert request["parsed_requirement"]["requirements"][0] == {
        "id": "F1",
        "kind": "function",
        "text": "Desktop application",
        "parent_requirement": "Desktop application",
        "facet": "capability",
    }
    assert request["parsed_requirement"]["eligibility_capabilities"] == [
        {
            "id": "F1",
            "text": "Desktop application",
            "parent_requirement": "Desktop application",
        },
        {
            "id": "F2",
            "text": "Export results",
            "parent_requirement": "Export results",
        },
    ]
    assert [item["repository"] for item in request["repositories"]] == [
        "owner1/project1",
        "owner2/project2",
        "owner3/project3",
        "owner4/project4",
    ]
    assert not any(
        item["kind"] == "tree"
        for item in request["repositories"][0]["evidence_segments"]
    )
    assert any(
        item["evidence_id"] == "R01:M01:S01"
        for item in request["repositories"][0]["evidence_segments"]
    )
    assert request["mandatory_selection_checks"][0].startswith(
        "Eligibility is not full-request fit"
    )
    assert "matched_function_ids" in client.calls[0][0]
    assert "other non-function condition alone never grants eligibility" in client.calls[0][0]
    assert "not full-request fit or a project-category threshold" in client.calls[0][0]
    final_request = json.loads(client.calls[1][1])
    assert [item["repository"] for item in final_request["repositories"]] == [
        "owner1/project1",
        "owner2/project2",
        "owner3/project3",
    ]
    assert final_request["selected_projects"] == valid_payload()["selected_projects"]
    assert "same order" in client.calls[1][0]
    assert "initial_ranked_projects" not in final_request
    assert "generic, neighboring, broader, or alternative" in client.calls[1][0]
    assert pipeline_context.analysis_result is not None
    assert [item.repository for item in pipeline_context.analysis_result.ranked_projects] == [
        "owner1/project1",
        "owner2/project2",
        "owner3/project3",
    ]
    assert [item.score for item in pipeline_context.analysis_result.ranked_projects] == [80, 80, 80]
    assert pipeline_context.analysis_result.ranked_projects[0].relevance_reason == (
        "Evidence confirms 4 requirements and partially confirms 0; 0 conflict and 1 "
        "remain unverified. Main confirmations: Desktop application; Export results."
    )
    assert pipeline_context.analysis_result.ranked_projects[0].risks == (
        "Unverified: Mandatory cloud service",
    )
    assert len(pipeline_context.analysis_result.ranked_projects[0].assessments) == 5
    assert pipeline_context.analysis_result.ranked_projects[0].assessments[0].evidence[0].url.endswith(
        "/blob/main/README.md"
    )
    assert pipeline_context.analysis_result.ranked_projects[0].assessments[0].evidence[0].quote == (
        "Implemented desktop export and offline workflow for project 1."
    )


@pytest.mark.asyncio
async def test_analysis_rejects_cross_repository_evidence_and_partial_requirement_coverage() -> None:
    selection = valid_payload()
    cross_reference = _final_payload(selection)
    cross_reference["ranked_projects"][0]["assessments"][0]["evidence_ids"] = [
        "R02:M01:S01"
    ]
    cross_stage = AnalysisStage(
        FakeAnalysisClient(selection, final_response=cross_reference)
    )
    with pytest.raises(PipelineFailure) as cross_error:
        cross_context = context()
        await cross_stage.execute(cross_context)
    assert cross_error.value.code == "invalid_analysis_finalization_output"
    assert cross_context.usage.warnings == [
        "Final analysis output validation failed: unknown or cross-repository evidence reference"
    ]

    missing = _final_payload(selection)
    missing["ranked_projects"][0]["assessments"].pop()
    missing_stage = AnalysisStage(FakeAnalysisClient(selection, final_response=missing))
    with pytest.raises(PipelineFailure) as missing_error:
        await missing_stage.execute(context())
    assert missing_error.value.code == "invalid_analysis_finalization_output"


@pytest.mark.asyncio
async def test_analysis_rejects_metadata_only_evidence_for_user_visible_behavior() -> None:
    selection = valid_payload()
    metadata_only = _final_payload(selection)
    metadata_only["ranked_projects"][0]["assessments"][0]["evidence_ids"] = [
        "R01:META"
    ]
    pipeline_context = context()

    with pytest.raises(PipelineFailure) as error:
        await AnalysisStage(
            FakeAnalysisClient(selection, final_response=metadata_only)
        ).execute(pipeline_context)

    assert error.value.code == "invalid_analysis_finalization_output"
    assert pipeline_context.usage.warnings == [
        "Final analysis output validation failed: requirement assessment needs README or file-content evidence"
    ]


@pytest.mark.asyncio
async def test_analysis_rejects_non_unique_project_groups() -> None:
    duplicate_group = valid_payload()
    duplicate_group["candidate_decisions"][1]["canonical_project"] = "owner1/project1"
    stage = AnalysisStage(FakeAnalysisClient(duplicate_group))
    with pytest.raises(PipelineFailure) as error:
        await stage.execute(context())
    assert error.value.code == "invalid_analysis_output"


@pytest.mark.asyncio
async def test_analysis_candidate_eligibility_requires_a_function_and_material_evidence() -> None:
    non_function = valid_payload()
    non_function["candidate_decisions"][0]["matched_function_ids"] = ["C1"]
    with pytest.raises(PipelineFailure) as non_function_error:
        await AnalysisStage(FakeAnalysisClient(non_function)).execute(context())
    assert non_function_error.value.code == "invalid_analysis_output"

    metadata_only = valid_payload()
    metadata_only["candidate_decisions"][0]["evidence_ids"] = ["R01:META"]
    with pytest.raises(PipelineFailure) as metadata_error:
        await AnalysisStage(FakeAnalysisClient(metadata_only)).execute(context())
    assert metadata_error.value.code == "invalid_analysis_output"

    qualified_context = context()
    qualified_context.parsed_requirement = replace(
        parsed_requirement(),
        verification_requirements=(
            VerificationRequirement(
                "function",
                "Search Chinese documents",
                ("Provide full-text search", "Support Chinese text"),
                check_facets=("capability", "natural_language"),
            ),
            VerificationRequirement(
                "constraint",
                "Run locally",
                ("Run locally",),
                check_facets=("runtime",),
            ),
        ),
    )
    qualifier_only = valid_payload()
    qualifier_only["candidate_decisions"][0]["matched_function_ids"] = ["F2"]
    with pytest.raises(PipelineFailure) as qualifier_error:
        await AnalysisStage(FakeAnalysisClient(qualifier_only)).execute(
            qualified_context
        )
    assert qualifier_error.value.code == "invalid_analysis_output"


@pytest.mark.asyncio
async def test_analysis_computes_scores_from_statuses_and_sorts_stably() -> None:
    payload = valid_payload(count=3)
    finalized = json.loads(json.dumps(_final_payload(payload)))
    for project in finalized["ranked_projects"]:
        if project["repository"] == "owner1/project1":
            for assessment in project["assessments"]:
                if assessment["requirement_id"] == "F2":
                    assessment["status"] = "unverified"
                    assessment["evidence_ids"] = []
        if project["repository"] == "owner2/project2":
            for assessment in project["assessments"]:
                if assessment["requirement_id"] == "X1":
                    assessment["status"] = "supported"
                    assessment["evidence_ids"] = ["R02:M01:S01"]

    pipeline_context = context(count=3)
    await AnalysisStage(
        FakeAnalysisClient(payload, final_response=finalized)
    ).execute(pipeline_context)

    assert pipeline_context.analysis_result is not None
    assert [
        (project.repository, project.score)
        for project in pipeline_context.analysis_result.ranked_projects
    ] == [
        ("owner2/project2", 100),
        ("owner3/project3", 80),
        ("owner1/project1", 53),
    ]


@pytest.mark.asyncio
async def test_analysis_fails_without_publishing_when_fewer_than_three_independent_projects_exist() -> None:
    pipeline_context = context(count=3)
    stage = AnalysisStage(FakeAnalysisClient(valid_payload(count=3, eligible_count=2)))

    with pytest.raises(PipelineFailure) as error:
        await stage.execute(pipeline_context)

    assert error.value.code == "insufficient_qualified_repositories"
    assert pipeline_context.analysis_result is None
    assert pipeline_context.usage.warnings == [
        "Analysis found 2 eligible independent repository groups."
    ]


@pytest.mark.asyncio
async def test_analysis_distinguishes_provider_failure_from_invalid_structure_and_closes_client() -> None:
    failure = LLMProviderError("provider failed", retryable=True)
    failed_client = FakeAnalysisClient(None, failure)
    failed_stage = AnalysisStage(failed_client)
    with pytest.raises(PipelineFailure) as provider_error:
        await failed_stage.execute(context())
    assert provider_error.value.code == "llm_analysis_failed"
    await failed_stage.aclose()
    assert failed_client.closed is True

    invalid_stage = AnalysisStage(FakeAnalysisClient({"candidate_decisions": []}))
    with pytest.raises(PipelineFailure) as invalid_error:
        await invalid_stage.execute(context())
    assert invalid_error.value.code == "invalid_analysis_output"


@pytest.mark.asyncio
async def test_analysis_finalization_failure_or_project_change_never_publishes_selection() -> None:
    failure = LLMProviderError("final provider failed", retryable=True)
    failed_context = context()
    failed_stage = AnalysisStage(
        FakeAnalysisClient(valid_payload(), final_failure=failure)
    )
    with pytest.raises(PipelineFailure) as provider_error:
        await failed_stage.execute(failed_context)
    assert provider_error.value.code == "llm_analysis_finalization_failed"
    assert failed_context.analysis_result is None

    selection = valid_payload()
    changed = _final_payload(selection)
    changed["ranked_projects"][0] = {
        **changed["ranked_projects"][0],
        "repository": "owner4/project4",
    }
    changed_context = context()
    changed_stage = AnalysisStage(
        FakeAnalysisClient(selection, final_response=changed)
    )
    with pytest.raises(PipelineFailure) as changed_error:
        await changed_stage.execute(changed_context)
    assert changed_error.value.code == "invalid_analysis_finalization_output"
    assert changed_context.analysis_result is None
    assert changed_context.usage.warnings == [
        "Final analysis output validation failed: final analysis changed the selected projects or their order"
    ]

    inconsistent_selection = valid_payload()
    inconsistent_final = _final_payload(inconsistent_selection)
    for assessment in inconsistent_final["ranked_projects"][0]["assessments"]:
        if assessment["requirement_id"] == "F1":
            assessment["status"] = "unverified"
            assessment["evidence_ids"] = []
    inconsistent_context = context()
    with pytest.raises(PipelineFailure) as inconsistent_error:
        await AnalysisStage(
            FakeAnalysisClient(
                inconsistent_selection,
                final_response=inconsistent_final,
            )
        ).execute(inconsistent_context)
    assert inconsistent_error.value.code == "invalid_analysis_finalization_output"
    assert inconsistent_context.analysis_result is None
    assert inconsistent_context.usage.warnings == [
        "Final analysis output validation failed: final assessments do not confirm an initially matched capability"
    ]


@pytest.mark.asyncio
async def test_analysis_rejects_english_prose_for_a_chinese_report() -> None:
    pipeline_context = context(language="zh")

    with pytest.raises(PipelineFailure) as error:
        await AnalysisStage(FakeAnalysisClient(valid_payload())).execute(pipeline_context)

    assert error.value.code == "invalid_analysis_output"
    assert pipeline_context.usage.warnings == [
        "Analysis output validation failed: analysis prose does not use the requested report language"
    ]
