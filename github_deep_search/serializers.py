from __future__ import annotations

from typing import Any

from github_deep_search.decision_brief import build_decision_brief
from github_deep_search.models import BudgetUsage, SearchFailureArtifact, SearchReport
from github_deep_search.public_report import build_public_project_view
from github_deep_search.run_trace import run_trace_to_dict
from github_deep_search.utils import simple_markdown_to_html


def report_to_dict(report: SearchReport, include_html: bool = False) -> dict[str, Any]:
    public_projects = [
        build_public_project_view(item, report.requirement.report_language)
        for item in report.top_projects
    ]
    data: dict[str, Any] = {
        "summary": report.summary,
        "reportMarkdown": report.report_markdown,
        "topProjects": [
            {
                "repo": item.repo.full_name,
                "url": item.repo.url,
                "relevance": public.relevance,
                "summary": public.summary,
                "covered": public.verified_capabilities,
                "stars": item.repo.stars,
                "lastPushedAt": item.repo.last_pushed_at,
            }
            for item, public in zip(report.top_projects, public_projects)
        ],
        "usage": {
            "llmInputTokens": report.usage.llm_input_tokens,
            "llmOutputTokens": report.usage.llm_output_tokens,
            "llmTokenEstimated": report.usage.llm_token_estimated,
        },
    }
    if include_html:
        data["reportHtml"] = simple_markdown_to_html(report.report_markdown)
    return data


def diagnostic_report_to_dict(report: SearchReport, include_html: bool = False) -> dict[str, Any]:
    decision_brief = build_decision_brief(report.requirement, report.top_projects)
    if report.run_trace is None:
        raise ValueError("Diagnostic serialization requires the engine-produced run trace")
    run_trace = report.run_trace
    data: dict[str, Any] = {
        "summary": report.summary,
        "decisionBrief": None if decision_brief is None else {
            "level": decision_brief.level,
            "headline": decision_brief.headline,
            "bestProject": decision_brief.best_project,
            "confirmedFeatures": decision_brief.confirmed_features,
            "gaps": decision_brief.gaps,
            "unconfirmedFeatures": decision_brief.unconfirmed_features,
            "nextStep": decision_brief.next_step,
        },
        "requirement": {
            "raw": report.requirement.raw,
            "reportLanguage": report.requirement.report_language,
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
            "evidenceComponents": report.requirement.evidence_components,
        },
        "reportMarkdown": report.report_markdown,
        "topProjects": [
            {
                "repo": item.repo.full_name,
                "url": item.repo.url,
                "publicSummary": build_public_project_view(
                    item, report.requirement.report_language
                ).summary,
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
                "verifiedCapabilities": item.verified_capabilities,
                "capabilityEvidence": [
                    {
                        "kind": reference.kind,
                        "locator": reference.locator,
                        "excerpt": reference.excerpt,
                    }
                    for reference in item.capability_evidence
                ],
                "capabilityCitationsReviewed": item.capability_citations_reviewed,
                "adjacentEvidence": (
                    None
                    if item.adjacent_evidence is None
                    else {
                        "relevanceScore": item.adjacent_evidence.relevance_score,
                        "capability": item.adjacent_evidence.capability,
                        "groupMatches": item.adjacent_evidence.group_matches,
                        "reference": {
                            "kind": item.adjacent_evidence.reference.kind,
                            "locator": item.adjacent_evidence.reference.locator,
                            "excerpt": item.adjacent_evidence.reference.excerpt,
                            "matchedAliases": item.adjacent_evidence.reference.matched_aliases,
                        },
                    }
                ),
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
                        "componentEvidence": coverage.component_evidence,
                        "requiredComponentCount": coverage.required_component_count,
                        "evidenceReferences": [
                            {
                                "kind": reference.kind,
                                "locator": reference.locator,
                                "excerpt": reference.excerpt,
                                "matchedAliases": reference.matched_aliases,
                                "lineStart": reference.line_start,
                                "lineEnd": reference.line_end,
                            }
                            for reference in coverage.evidence_references
                        ],
                    }
                    for coverage in item.evidence_coverage
                ],
                "foundBy": item.repo.found_by,
            }
            for item in report.top_projects
        ],
        "opportunity": report.opportunity,
        "usage": _usage_to_dict(report.usage),
        "raw": report.raw,
        "runTrace": run_trace_to_dict(run_trace),
    }
    if include_html:
        data["reportHtml"] = simple_markdown_to_html(report.report_markdown)
    return data


def failure_artifact_to_dict(artifact: SearchFailureArtifact) -> dict[str, Any]:
    return {
        "schemaVersion": artifact.schema_version,
        "status": "failed",
        "query": artifact.query,
        "errorReportMarkdown": artifact.error_report_markdown,
        "failure": {
            "kind": artifact.failure.kind,
            "stage": artifact.failure.stage,
            "exceptionType": artifact.failure.exception_type,
            "message": artifact.failure.message,
            "retryable": artifact.failure.retryable,
        },
        "usage": _usage_to_dict(artifact.usage),
        "runTrace": run_trace_to_dict(artifact.run_trace),
    }

def _usage_to_dict(usage: BudgetUsage) -> dict[str, Any]:
    return {
        "githubRequests": usage.github_requests,
        "githubSearchRequests": usage.github_search_requests,
        "githubCodeSearchRequests": usage.github_code_search_requests,
        "githubTopicSearchRequests": usage.github_topic_search_requests,
        "githubIssueSearchRequests": usage.github_issue_search_requests,
        "tavilyCredits": usage.tavily_credits,
        "llmInputTokens": usage.llm_input_tokens,
        "llmOutputTokens": usage.llm_output_tokens,
        "llmTokenEstimated": usage.llm_token_estimated,
        "estimatedUsd": usage.estimated_usd,
        "estimatedUsdComplete": usage.estimated_usd_complete,
        "missingPriceComponents": usage.missing_price_components,
        "elapsedMs": usage.elapsed_ms,
        "warnings": usage.warnings,
        "providerEvents": [
            {
                "provider": event.provider,
                "operation": event.operation,
                "outcome": event.outcome,
                "kind": event.kind,
                "stage": event.stage,
            }
            for event in usage.provider_events
        ],
    }
