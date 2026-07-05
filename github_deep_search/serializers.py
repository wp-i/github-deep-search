from __future__ import annotations

import re
from typing import Any

from github_deep_search.models import SearchReport
from github_deep_search.utils import simple_markdown_to_html


def report_to_dict(report: SearchReport, include_html: bool = False) -> dict[str, Any]:
    report_markdown = _report_markdown_with_project_metadata(report)
    data: dict[str, Any] = {
        "summary": report.summary,
        "requirement": {
            "raw": report.requirement.raw,
            "intent": report.requirement.intent,
            "mustHaveFeatures": report.requirement.must_have_features,
            "niceToHaveFeatures": report.requirement.nice_to_have_features,
            "targetPlatforms": report.requirement.target_platforms,
            "searchQueries": report.requirement.search_queries,
            "repoSearchQueries": report.requirement.repo_search_queries,
            "codeSearchQueries": report.requirement.code_search_queries,
            "topicSearchQueries": report.requirement.topic_search_queries,
            "issueSearchQueries": report.requirement.issue_search_queries,
            "webSearchQueries": report.requirement.web_search_queries,
            "featureConcepts": report.requirement.feature_concepts,
            "evidenceAliases": report.requirement.evidence_aliases,
        },
        "reportMarkdown": report_markdown,
        "topProjects": [
            {
                "repo": item.repo.full_name,
                "url": item.repo.url,
                "score": item.match_score,
                "functionalScore": item.functional_score or item.match_score,
                "suitabilityScore": item.suitability_score or item.match_score,
                "scoreReason": item.score_reason,
                "coreFeature": item.core_feature,
                "coreConfirmed": item.core_confirmed,
                "isCatalog": item.is_catalog,
                "stars": item.repo.stars,
                "lastPushedAt": item.repo.last_pushed_at,
                "language": item.repo.language,
                "license": item.repo.license,
                "recommendation": item.recommendation,
                "directlyUsable": item.directly_usable,
                "isReferenceCandidate": item.is_reference_candidate,
                "confidenceLevel": item.confidence_level,
                "referenceReason": item.reference_reason,
                "coveredFeatures": item.covered_features,
                "differentFeatures": item.different_features,
                "unknownFeatures": item.unknown_features,
                "missingFeatures": item.missing_features,
                "requiredChanges": item.required_changes,
                "risks": item.risks,
                "evidence": item.evidence,
                "evidenceCoverage": [
                    {
                        "feature": coverage.feature,
                        "covered": coverage.covered,
                        "status": coverage.status,
                        "readmeEvidence": coverage.readme_evidence,
                        "sourceEvidence": coverage.source_evidence,
                        "pathEvidence": coverage.path_evidence,
                        "missingReason": coverage.missing_reason,
                        "differenceReason": coverage.difference_reason,
                        "unknownReason": coverage.unknown_reason,
                    }
                    for coverage in item.evidence_coverage
                ],
                "foundBy": item.repo.found_by,
            }
            for item in report.top_projects
        ],
        "opportunity": report.opportunity,
        "usage": {
            "githubRequests": report.usage.github_requests,
            "githubSearchRequests": report.usage.github_search_requests,
            "githubCodeSearchRequests": report.usage.github_code_search_requests,
            "githubTopicSearchRequests": report.usage.github_topic_search_requests,
            "githubIssueSearchRequests": report.usage.github_issue_search_requests,
            "tavilyCredits": report.usage.tavily_credits,
            "llmInputTokens": report.usage.llm_input_tokens,
            "llmOutputTokens": report.usage.llm_output_tokens,
            "llmTokenEstimated": report.usage.llm_token_estimated,
            "estimatedUsd": report.usage.estimated_usd,
            "estimatedUsdComplete": report.usage.estimated_usd_complete,
            "missingPriceComponents": report.usage.missing_price_components,
            "elapsedMs": report.usage.elapsed_ms,
            "warnings": report.usage.warnings,
        },
        "raw": report.raw,
    }
    if include_html:
        data["reportHtml"] = simple_markdown_to_html(report_markdown)
    return data


def _report_markdown_with_project_metadata(report: SearchReport) -> str:
    markdown = report.report_markdown
    for index, item in enumerate(report.top_projects, start=1):
        metadata = _project_title_metadata(item.repo.stars, item.repo.last_pushed_at)
        heading_pattern = re.compile(
            rf"^(###\s+{index}\.\s+{re.escape(item.repo.full_name)}(?:（[^）]+）)?)(?!.*(?:★|更新))$",
            flags=re.MULTILINE,
        )
        markdown = heading_pattern.sub(rf"\1{metadata}", markdown)
    return markdown


def _project_title_metadata(stars: int, last_pushed_at: str | None) -> str:
    updated = str(last_pushed_at or "").strip()
    if "T" in updated:
        updated = updated.split("T", 1)[0]
    elif len(updated) > 10:
        updated = updated[:10]
    updated = updated or "未知"
    return f" · ★ {stars} · 更新 {updated}"
