from __future__ import annotations

from dataclasses import dataclass

from github_deep_search.models import ProjectAnalysis, Requirement


@dataclass(frozen=True)
class DecisionBrief:
    level: str
    headline: str
    best_project: str | None
    confirmed_features: list[str]
    gaps: list[str]
    unconfirmed_features: list[str]
    next_step: str


def build_decision_brief(
    requirement: Requirement,
    analyses: list[ProjectAnalysis],
) -> DecisionBrief | None:
    if not analyses:
        return None

    best = analyses[0]
    confirmed = _supported_features(best)
    gaps = _unique([*best.different_features, *best.missing_features])
    unconfirmed = _unique(
        [
            *best.unknown_features,
            *[
                item.feature
                for item in best.evidence_coverage
                if item.status == "unknown" and not item.covered
            ],
        ]
    )
    level, headline = _level_and_headline(best)
    has_confirmed_core = best.core_confirmed
    if not has_confirmed_core:
        headline = f"当前保留 {len(analyses)} 个相邻或参考项目，尚无证据支持指定首选。"
    return DecisionBrief(
        level=level,
        headline=headline,
        best_project=best.repo.full_name if has_confirmed_core else None,
        confirmed_features=confirmed,
        gaps=gaps,
        unconfirmed_features=unconfirmed,
        next_step=(
            _next_step(best, gaps, unconfirmed)
            if has_confirmed_core
            else "并列核对候选项目的已确认能力与适用范围，优先补查核心需求证据。"
        ),
    )


def format_decision_brief(brief: DecisionBrief | None) -> list[str]:
    if brief is None:
        return []
    lines = [f"- 建议：{brief.headline}"]
    if brief.confirmed_features:
        lines.append(f"- 已确认：{'、'.join(brief.confirmed_features[:3])}")
    if brief.gaps:
        lines.append(f"- 核心缺口：{'、'.join(brief.gaps[:3])}")
    if brief.unconfirmed_features:
        lines.append(f"- 尚未确认：{'、'.join(brief.unconfirmed_features[:3])}")
    lines.append(f"- 下一步：{brief.next_step}")
    return lines


def _level_and_headline(analysis: ProjectAnalysis) -> tuple[str, str]:
    name = analysis.repo.full_name
    if analysis.directly_usable and analysis.core_confirmed:
        return "direct", f"{name} 可优先作为直接采用候选。"
    if analysis.core_confirmed:
        return "verified", f"{name} 已有核心能力证据，适合继续评估。"
    if analysis.confidence_level == "lead":
        return "adjacent", f"{name} 仅是相邻线索，不应视为已验证匹配。"
    return "reference", f"{name} 可作为参考项目，核心能力仍需核查。"


def _next_step(
    analysis: ProjectAnalysis,
    gaps: list[str],
    unconfirmed: list[str],
) -> str:
    if gaps:
        return f"打开 {analysis.repo.full_name} 的证据与源码，先核查「{gaps[0]}」。"
    if unconfirmed:
        if analysis.adjacent_evidence is not None:
            locator = analysis.adjacent_evidence.reference.locator
            return (
                f"打开 {analysis.repo.full_name} 的 {locator} 及相关源码，"
                f"逐项核对是否明确支持「{unconfirmed[0]}」。"
            )
        return f"打开 {analysis.repo.full_name} 的证据与源码，确认「{unconfirmed[0]}」。"
    return f"打开 {analysis.repo.full_name} 的证据与源码，评估集成成本和维护状态。"


def _supported_features(analysis: ProjectAnalysis) -> list[str]:
    supported = [
        item.feature
        for item in analysis.evidence_coverage
        if item.status == "supported" or item.covered
    ]
    return _unique(supported)


def _first(values: list[str]) -> str:
    for value in values:
        if value.strip():
            return value.strip()
    return ""


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        cleaned = value.strip()
        key = cleaned.casefold()
        if cleaned and key not in seen:
            seen.add(key)
            result.append(cleaned)
    return result
