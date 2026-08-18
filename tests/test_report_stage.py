from __future__ import annotations

import pytest

from github_deep_search.models import (
    AnalysisCandidateDecision,
    AnalysisEvidence,
    AnalysisResult,
    CandidateRepository,
    EvidenceMaterial,
    EvidenceRepository,
    EvidenceResult,
    RankedProject,
    RequirementAssessment,
    RunRequest,
    Usage,
    ValidatedInput,
)
from github_deep_search.pipeline import PipelineContext, PipelineFailure
from github_deep_search.stages.report import ReportStage
from tests.test_analysis_stage import settings


def report_context(language: str = "en") -> PipelineContext:
    repositories = []
    decisions = []
    ranked = []
    for index, score in enumerate((92, 84, 76), start=1):
        repository = CandidateRepository(
            owner=f"owner{index}",
            name=f"project{index}",
            url=f"https://github.com/owner{index}/project{index}",
            last_pushed_at=f"2026-0{index}-01T00:00:00Z",
            is_archived=index == 3,
            license="MIT" if index != 2 else None,
            latest_release_at=f"2026-0{index}-02T00:00:00Z" if index == 1 else None,
        )
        material = EvidenceMaterial(
            kind="readme",
            path="README.md",
            url=f"{repository.url}/blob/main/README.md",
            excerpt="Implemented feature.",
        )
        repositories.append(
            EvidenceRepository(
                repository=repository,
                materials=(material,),
                tree_paths=("README.md",),
                relation_kind="original",
                relation_key=repository.full_name.casefold(),
            )
        )
        source = AnalysisEvidence(
            f"R{index:02d}:M01",
            "README.md",
            material.url,
            "Implemented feature.",
        )
        decisions.append(
            AnalysisCandidateDecision(
                repository.full_name,
                True,
                repository.full_name,
                "Contains implementation.",
                (source,),
            )
        )
        ranked.append(
            RankedProject(
                repository=repository.full_name,
                score=score,
                relevance_reason=f"Reason {index}",
                assessments=(
                    RequirementAssessment(
                        "F1",
                        "function",
                        "Core feature",
                        "supported",
                        "README confirms the feature.",
                        (source,),
                    ),
                    RequirementAssessment(
                        "C1",
                        "constraint",
                        "Offline mode",
                        "unverified",
                        "The collected evidence does not confirm offline mode.",
                        (),
                    ),
                ),
                risks=("Archived repository.",) if index == 3 else (),
            )
        )
    raw_input = "Find a useful tool."
    return PipelineContext(
        run_id="run-report",
        request=RunRequest(raw_input),
        settings=settings(),
        validated_input=ValidatedInput(raw_input, language),
        evidence_result=EvidenceResult(tuple(repositories), (), 3, 0),
        analysis_result=AnalysisResult(tuple(decisions), tuple(ranked)),
        usage=Usage(llm_input_tokens=123, llm_output_tokens=45, github_requests=77),
    )


@pytest.mark.asyncio
async def test_report_projects_analysis_without_rescoring_or_reordering() -> None:
    pipeline_context = report_context()

    await ReportStage().execute(pipeline_context)

    report = pipeline_context.final_report
    assert report is not None
    assert [item.repository for item in report.projects] == [
        "owner1/project1",
        "owner2/project2",
        "owner3/project3",
    ]
    assert [item.score for item in report.projects] == [92, 84, 76]
    assert report.projects[0].confirmed[0].requirement == "Core feature"
    assert report.projects[0].gaps[0].status == "unverified"
    assert report.projects[1].license is None
    assert report.projects[2].is_archived is True
    assert report.usage.llm_input_tokens == 123
    assert report.usage.llm_output_tokens == 45
    assert report.usage.llm_total_tokens == 168
    assert "77" not in report.markdown
    assert "owner1/project1 — 92/100" in report.markdown
    assert "https://github.com/owner1/project1/blob/main/README.md" in report.markdown
    assert "Implemented feature." in report.markdown


@pytest.mark.asyncio
async def test_report_uses_report_language_and_requires_exactly_three_projects() -> None:
    chinese_context = report_context("zh")
    await ReportStage().execute(chinese_context)
    assert chinese_context.final_report is not None
    assert chinese_context.final_report.markdown.startswith("# GitHub Deep Search 结果")
    assert "已确认满足" in chinese_context.final_report.markdown

    invalid_context = report_context()
    invalid_context.analysis_result = AnalysisResult(
        invalid_context.analysis_result.candidate_decisions,
        invalid_context.analysis_result.ranked_projects[:2],
    )
    with pytest.raises(PipelineFailure) as error:
        await ReportStage().execute(invalid_context)
    assert error.value.code == "invalid_report_input"
    assert invalid_context.final_report is None
