from __future__ import annotations

from github_deep_search.models import (
    AnalysisEvidence,
    FinalReport,
    PublicUsage,
    ReportEvidence,
    ReportProject,
    ReportRequirement,
)
from github_deep_search.pipeline import PipelineContext, PipelineFailure


class ReportStage:
    name = "report"

    async def execute(self, context: PipelineContext) -> None:
        validated = context.validated_input
        evidence = context.evidence_result
        analysis = context.analysis_result
        if validated is None or evidence is None or analysis is None:
            raise PipelineFailure(
                "report_prerequisite_missing",
                "The validated request, repository evidence, and analysis are unavailable for reporting.",
            )
        if len(analysis.ranked_projects) != 3:
            raise PipelineFailure(
                "invalid_report_input",
                "The analysis did not provide exactly three ranked repositories.",
            )

        repositories = {
            item.repository.full_name.casefold(): item.repository
            for item in evidence.repositories
        }
        projects: list[ReportProject] = []
        for ranked in analysis.ranked_projects:
            repository = repositories.get(ranked.repository.casefold())
            if repository is None:
                raise PipelineFailure(
                    "invalid_report_input",
                    "The analysis referenced a repository outside the collected evidence.",
                )
            assessments = tuple(_report_requirement(item) for item in ranked.assessments)
            projects.append(
                ReportProject(
                    repository=repository.full_name,
                    url=repository.url,
                    score=ranked.score,
                    relevance_reason=ranked.relevance_reason,
                    confirmed=tuple(
                        item for item in assessments if item.status == "supported"
                    ),
                    gaps=tuple(
                        item for item in assessments if item.status != "supported"
                    ),
                    last_pushed_at=repository.last_pushed_at,
                    is_archived=repository.is_archived,
                    license=repository.license,
                    latest_release_at=repository.latest_release_at,
                    risks=ranked.risks,
                )
            )

        usage = PublicUsage(
            llm_input_tokens=context.usage.llm_input_tokens,
            llm_output_tokens=context.usage.llm_output_tokens,
            llm_total_tokens=context.usage.llm_total_tokens,
        )
        project_tuple = tuple(projects)
        context.final_report = FinalReport(
            language=validated.report_language,
            projects=project_tuple,
            markdown=_markdown(project_tuple, validated.report_language, usage),
            usage=usage,
        )


def _report_requirement(assessment) -> ReportRequirement:
    return ReportRequirement(
        requirement_id=assessment.requirement_id,
        kind=assessment.kind,
        requirement=assessment.requirement,
        status=assessment.status,
        explanation=assessment.explanation,
        evidence=tuple(_report_evidence(item) for item in assessment.evidence),
    )


def _report_evidence(evidence: AnalysisEvidence) -> ReportEvidence:
    return ReportEvidence(
        evidence_id=evidence.evidence_id,
        label=evidence.label,
        url=evidence.url,
        quote=evidence.quote,
    )


def _markdown(
    projects: tuple[ReportProject, ...],
    language: str,
    usage: PublicUsage,
) -> str:
    if language == "zh":
        lines = ["# GitHub Deep Search 结果", ""]
        labels = {
            "reason": "相关原因",
            "confirmed": "已确认满足",
            "gaps": "部分满足、明确不符或尚未确认",
            "facts": "仓库事实",
            "risks": "必要风险",
            "pushed": "最后代码更新时间",
            "archived": "归档状态",
            "license": "许可证",
            "release": "最新 Release",
            "yes": "已归档",
            "no": "未归档",
            "unknown": "未提供",
            "no_risks": "分析未列出额外风险。",
            "usage": "LLM Token 用量",
            "input": "输入",
            "output": "输出",
            "total": "总计",
        }
        statuses = {
            "supported": "支持",
            "partial": "部分支持",
            "conflicts": "明确不符",
            "unverified": "无法确认",
        }
    else:
        lines = ["# GitHub Deep Search Results", ""]
        labels = {
            "reason": "Why it is relevant",
            "confirmed": "Confirmed requirements",
            "gaps": "Partial, conflicting, or unverified requirements",
            "facts": "Repository facts",
            "risks": "Necessary risks",
            "pushed": "Last code update",
            "archived": "Archive status",
            "license": "License",
            "release": "Latest release",
            "yes": "Archived",
            "no": "Not archived",
            "unknown": "Not provided",
            "no_risks": "The analysis listed no additional risks.",
            "usage": "LLM token usage",
            "input": "Input",
            "output": "Output",
            "total": "Total",
        }
        statuses = {
            "supported": "Supported",
            "partial": "Partial",
            "conflicts": "Conflicts",
            "unverified": "Unverified",
        }

    for index, project in enumerate(projects, start=1):
        lines.extend(
            [
                f"## {index}. {_inline(project.repository)} — {project.score}/100",
                "",
                f"[GitHub]({project.url})",
                "",
                f"### {labels['reason']}",
                "",
                _inline(project.relevance_reason),
                "",
                f"### {labels['confirmed']}",
                "",
            ]
        )
        lines.extend(_assessment_lines(project.confirmed, statuses))
        lines.extend(["", f"### {labels['gaps']}", ""])
        lines.extend(_assessment_lines(project.gaps, statuses))
        lines.extend(
            [
                "",
                f"### {labels['facts']}",
                "",
                f"- {labels['pushed']}: {_inline(project.last_pushed_at or labels['unknown'])}",
                f"- {labels['archived']}: {labels['yes'] if project.is_archived else labels['no']}",
                f"- {labels['license']}: {_inline(project.license or labels['unknown'])}",
                f"- {labels['release']}: {_inline(project.latest_release_at or labels['unknown'])}",
                "",
                f"### {labels['risks']}",
                "",
            ]
        )
        if project.risks:
            lines.extend(f"- {_inline(risk)}" for risk in project.risks)
        else:
            lines.append(labels["no_risks"])
        lines.append("")

    lines.extend(
        [
            f"## {labels['usage']}",
            "",
            f"- {labels['input']}: {usage.llm_input_tokens}",
            f"- {labels['output']}: {usage.llm_output_tokens}",
            f"- {labels['total']}: {usage.llm_total_tokens}",
            "",
        ]
    )
    return "\n".join(lines)


def _assessment_lines(
    assessments: tuple[ReportRequirement, ...],
    statuses: dict[str, str],
) -> list[str]:
    lines: list[str] = []
    for assessment in assessments:
        sources = ", ".join(
            (
                f"[{_inline(item.label)}]({item.url})"
                + (f": “{_inline(item.quote)}”" if item.quote else "")
            )
            for item in assessment.evidence
        )
        suffix = f" ({sources})" if sources else ""
        lines.append(
            f"- **{statuses[assessment.status]} · {_inline(assessment.requirement)}**："
            f"{_inline(assessment.explanation)}{suffix}"
        )
    if not lines:
        lines.append("- —")
    return lines


def _inline(value: str) -> str:
    compact = " ".join(value.replace("\x00", "").split())
    for character in ("\\", "`", "*", "_", "[", "]", "<", ">"):
        compact = compact.replace(character, f"\\{character}")
    return compact
