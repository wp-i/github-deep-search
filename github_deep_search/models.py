from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


StageStatus = Literal["not_started", "completed", "partial", "failed"]
FailureKind = Literal["invalid_request", "provider", "execution", "report_delivery"]
ProviderOutcome = Literal["failed", "limited"]


@dataclass
class ProviderEvent:
    provider: str
    operation: str
    outcome: ProviderOutcome
    kind: str
    stage: str = ""


@dataclass(frozen=True)
class RunFailure:
    kind: FailureKind
    stage: str
    exception_type: str
    message: str
    retryable: bool = False


@dataclass(frozen=True)
class StageOutcome:
    name: str
    status: StageStatus
    inputs: dict[str, int] = field(default_factory=dict)
    outputs: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    failure: RunFailure | None = None


@dataclass(frozen=True)
class RunTrace:
    schema_version: str
    status: Literal["completed", "partial", "failed"]
    stages: list[StageOutcome]
    failure: RunFailure | None = None


@dataclass
class BudgetUsage:
    github_requests: int = 0
    github_search_requests: int = 0
    github_code_search_requests: int = 0
    github_topic_search_requests: int = 0
    github_issue_search_requests: int = 0
    tavily_credits: int = 0
    llm_input_tokens: int = 0
    llm_output_tokens: int = 0
    llm_token_estimated: bool = False
    estimated_usd: float = 0.0
    estimated_usd_complete: bool = True
    missing_price_components: list[str] = field(default_factory=list)
    elapsed_ms: int = 0
    warnings: list[str] = field(default_factory=list)
    provider_events: list[ProviderEvent] = field(default_factory=list)


@dataclass
class Requirement:
    raw: str
    intent: str
    must_have_features: list[str]
    nice_to_have_features: list[str]
    target_platforms: list[str]
    search_queries: list[str]
    report_language: Literal["zh", "en"] = "zh"
    repo_search_queries: list[str] = field(default_factory=list)
    code_search_queries: list[str] = field(default_factory=list)
    topic_search_queries: list[str] = field(default_factory=list)
    issue_search_queries: list[str] = field(default_factory=list)
    web_search_queries: list[str] = field(default_factory=list)
    feature_concepts: dict[str, list[str]] = field(default_factory=dict)
    evidence_aliases: dict[str, list[str]] = field(default_factory=dict)
    evidence_components: dict[str, dict[str, list[str]]] = field(default_factory=dict)


@dataclass
class SearchSpec:
    raw: str
    intent: str
    literal_keywords: list[str]
    domains: list[str]
    actions: list[str]
    objects: list[str]
    outputs: list[str]
    interfaces: list[str]
    must_have: list[str]
    nice_to_have: list[str]
    negative_filters: list[str]
    search_queries: list[str]
    report_language: Literal["zh", "en"] = "zh"
    repo_search_queries: list[str] = field(default_factory=list)
    code_search_queries: list[str] = field(default_factory=list)
    topic_search_queries: list[str] = field(default_factory=list)
    issue_search_queries: list[str] = field(default_factory=list)
    web_search_queries: list[str] = field(default_factory=list)
    evidence_aliases: dict[str, list[str]] = field(default_factory=dict)
    evidence_components: dict[str, dict[str, list[str]]] = field(default_factory=dict)

    @property
    def feature_concepts(self) -> dict[str, list[str]]:
        return {
            "literal_keywords": self.literal_keywords,
            "domains": self.domains,
            "actions": self.actions,
            "objects": self.objects,
            "outputs": self.outputs,
            "interfaces": self.interfaces,
        }

    def to_requirement(self) -> Requirement:
        return Requirement(
            raw=self.raw,
            intent=self.intent,
            must_have_features=self.must_have,
            nice_to_have_features=self.nice_to_have,
            target_platforms=self.interfaces,
            search_queries=self.search_queries,
            report_language=self.report_language,
            repo_search_queries=self.repo_search_queries or self.search_queries,
            code_search_queries=self.code_search_queries,
            topic_search_queries=self.topic_search_queries,
            issue_search_queries=self.issue_search_queries,
            web_search_queries=self.web_search_queries,
            feature_concepts=self.feature_concepts,
            evidence_aliases=self.evidence_aliases,
            evidence_components=self.evidence_components,
        )


@dataclass
class CandidateRepository:
    owner: str
    name: str
    url: str
    description: str = ""
    stars: int = 0
    forks: int = 0
    language: str | None = None
    topics: list[str] = field(default_factory=list)
    last_pushed_at: str | None = None
    license: str | None = None
    default_branch: str = "main"
    found_by: list[str] = field(default_factory=list)
    readme: str = ""
    file_paths: list[str] = field(default_factory=list)
    key_files: dict[str, str] = field(default_factory=dict)
    source_evidence: list[str] = field(default_factory=list)
    evidence_coverage: list["EvidenceCoverage"] = field(default_factory=list)
    raw_score: float = 0.0
    evidence_score: float = 0.0
    core_signal_score: float = 0.0

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


@dataclass
class EvidenceReference:
    """A repository-local, reviewable location for one evidence observation."""

    kind: Literal["repository_metadata", "readme", "path", "source"]
    locator: str
    excerpt: str
    matched_aliases: list[str] = field(default_factory=list)
    line_start: int | None = None
    line_end: int | None = None


@dataclass
class AdjacentEvidence:
    """Repository-local evidence for a useful but unconfirmed adjacent capability."""

    reference: EvidenceReference
    group_matches: dict[str, list[str]] = field(default_factory=dict)
    relevance_score: int = 0
    capability: str = ""


@dataclass
class EvidenceCoverage:
    feature: str
    covered: bool
    status: Literal["supported", "different", "missing", "unknown"] = "unknown"
    readme_evidence: list[str] = field(default_factory=list)
    source_evidence: list[str] = field(default_factory=list)
    path_evidence: list[str] = field(default_factory=list)
    missing_reason: str = ""
    difference_reason: str = ""
    unknown_reason: str = ""
    component_evidence: dict[str, list[str]] = field(default_factory=dict)
    required_component_count: int = 0
    evidence_references: list[EvidenceReference] = field(default_factory=list)


@dataclass
class ProjectAnalysis:
    repo: CandidateRepository
    match_score: int
    recommendation: str
    directly_usable: bool
    covered_features: list[str]
    missing_features: list[str]
    required_changes: list[str]
    risks: list[str]
    evidence: list[str]
    different_features: list[str] = field(default_factory=list)
    unknown_features: list[str] = field(default_factory=list)
    functional_score: int = 0
    suitability_score: int = 0
    score_reason: str = ""
    core_feature: str = ""
    core_confirmed: bool = False
    is_catalog: bool = False
    evidence_coverage: list[EvidenceCoverage] = field(default_factory=list)
    is_reference_candidate: bool = False
    confidence_level: str = "reliable"
    reference_reason: str = ""
    verified_capabilities: list[str] = field(default_factory=list)
    capability_evidence: list[EvidenceReference] = field(default_factory=list)
    capability_citations_reviewed: bool = False
    adjacent_evidence: AdjacentEvidence | None = None


@dataclass
class SearchReport:
    query: str
    requirement: Requirement
    top_projects: list[ProjectAnalysis]
    opportunity: str
    summary: str
    report_markdown: str
    usage: BudgetUsage
    raw: dict[str, Any] = field(default_factory=dict)
    run_trace: RunTrace | None = None


@dataclass(frozen=True)
class SearchFailureArtifact:
    schema_version: str
    query: str
    error_report_markdown: str
    usage: BudgetUsage
    run_trace: RunTrace
    failure: RunFailure
