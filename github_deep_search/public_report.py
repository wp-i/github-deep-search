from __future__ import annotations

import re
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
    description = " ".join(str(analysis.repo.description or "").split())[:280]
    if _content_key(description) == _content_key(analysis.repo.name):
        description = ""
    if description:
        summary = description
        normalized_capabilities = {_content_key(capability) for capability in capabilities}
        if _content_key(summary) in normalized_capabilities and analysis.repo.language:
            summary = (
                f"{summary} (primary language: {analysis.repo.language})"
                if language == "en"
                else f"{summary}（主要语言：{analysis.repo.language}）"
            )
    elif capabilities:
        text = "; ".join(capabilities[:3]) if language == "en" else "；".join(capabilities[:3])
        summary = (
            f"Public project materials confirm: {text}."
            if language == "en"
            else f"项目公开材料确认了与当前需求相关的能力：{text}。"
        )
    else:
        summary = ""
    return PublicProjectView(
        relevance=max(0, min(100, round(analysis.match_score))),
        summary=summary,
        verified_capabilities=capabilities,
    )


def _content_key(value: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", str(value).casefold())
