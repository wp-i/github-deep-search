from __future__ import annotations

from github_deep_search.models import (
    FinalReport,
    PublicUsage,
    ReportEvidence,
    ReportProject,
    ReportRequirement,
)


def fake_final_report(language: str = "en") -> FinalReport:
    projects = tuple(
        ReportProject(
            repository=f"example/project-{index}",
            url=f"https://github.com/example/project-{index}",
            score=100 - index * 10,
            relevance_reason=f"Controlled relevance reason {index}.",
            confirmed=(
                ReportRequirement(
                    requirement_id="F1",
                    kind="function",
                    requirement="Controlled core capability",
                    status="supported",
                    explanation="The controlled README confirms this capability.",
                    evidence=(
                        ReportEvidence(
                            evidence_id=f"R{index:02d}:M01",
                            label="README.md",
                            url=f"https://github.com/example/project-{index}/blob/main/README.md",
                            quote="Controlled evidence excerpt.",
                        ),
                    ),
                ),
            ),
            gaps=(
                ReportRequirement(
                    requirement_id="C1",
                    kind="constraint",
                    requirement="Controlled deployment constraint",
                    status="unverified",
                    explanation="The controlled evidence does not confirm this constraint.",
                    evidence=(),
                ),
            ),
            last_pushed_at="2026-01-01T00:00:00Z",
            is_archived=False,
            license="MIT",
            latest_release_at="2026-01-02T00:00:00Z",
            risks=(),
        )
        for index in range(1, 4)
    )
    usage = PublicUsage(120, 30, 150)
    return FinalReport(
        language=language,
        projects=projects,
        markdown="# Controlled report\n",
        usage=usage,
    )
