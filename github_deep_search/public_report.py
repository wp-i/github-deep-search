from __future__ import annotations

from dataclasses import dataclass

from github_deep_search.models import ProjectAnalysis


@dataclass(frozen=True)
class PublicProjectView:
    relevance: int
    summary: str
    verified_capabilities: list[str]


def build_public_project_view(
    analysis: ProjectAnalysis,
    language: str,
) -> PublicProjectView:
    capabilities = list(
        dict.fromkeys([*analysis.verified_capabilities, *analysis.covered_features])
    )[:5]
    if capabilities:
        count = len(capabilities)
        if language == "en":
            noun = "capability" if count == 1 else "capabilities"
            summary = f"Public project materials confirm {count} {noun} related to this request."
        else:
            summary = f"项目公开材料确认了 {count} 项与当前需求相关的能力。"
    else:
        summary = ""
    return PublicProjectView(
        relevance=max(0, min(100, round(analysis.match_score))),
        summary=summary,
        verified_capabilities=capabilities,
    )
