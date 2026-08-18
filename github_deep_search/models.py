from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal


ReportLanguage = Literal["zh", "en"]
EvidenceMaterialKind = Literal["readme", "file"]
RepositoryRelationKind = Literal["original", "fork", "mirror"]
EvidenceRejectionReason = Literal["not_found", "private", "empty", "no_material"]
RequirementKind = Literal["function", "constraint", "preference", "exclusion"]
RequirementFacet = Literal[
    "capability",
    "deployment",
    "platform",
    "runtime",
    "scope",
    "natural_language",
    "scale",
    "format",
    "input_mode",
    "preference",
    "exclusion",
    "other_condition",
]
RequirementStatus = Literal["supported", "partial", "conflicts", "unverified"]
StageName = Literal["input", "parse", "discovery", "evidence", "analysis", "report"]
StageStatus = Literal["not_started", "in_progress", "completed", "failed", "cancelled"]
RunStatus = Literal["running", "completed", "failed", "cancelled"]
RunEventType = Literal[
    "stage_started",
    "stage_completed",
    "supplemental_discovery",
    "warning",
    "run_completed",
    "run_failed",
    "run_cancelled",
]

STAGE_NAMES: tuple[StageName, ...] = (
    "input",
    "parse",
    "discovery",
    "evidence",
    "analysis",
    "report",
)


@dataclass(frozen=True)
class RunRequest:
    raw_input: str


@dataclass(frozen=True)
class ValidatedInput:
    raw_input: str
    report_language: ReportLanguage


@dataclass(frozen=True)
class SearchQueryPair:
    purpose: str
    zh: str
    en: str


@dataclass(frozen=True)
class VerificationRequirement:
    kind: RequirementKind
    requirement: str
    checks: tuple[str, ...]
    facet: RequirementFacet = "capability"
    source_unit_ids: tuple[str, ...] = ()
    check_facets: tuple[RequirementFacet, ...] = ()


@dataclass(frozen=True)
class InputCoverage:
    unit_id: str
    text: str
    disposition: Literal["requirement", "context"]
    requirement_checks: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class ParsedRequirement:
    complete_requirement: str
    core_goal: str
    reasonable_interpretations: tuple[str, ...]
    functional_requirements: tuple[str, ...]
    constraints: tuple[str, ...]
    preferences: tuple[str, ...]
    exclusions: tuple[str, ...]
    search_query_pairs: tuple[SearchQueryPair, ...]
    evidence_targets: tuple[str, ...]
    suggested_repositories: tuple[str, ...]
    github_language_qualifier: str | None = None
    verification_requirements: tuple[VerificationRequirement, ...] = ()
    input_coverage: tuple[InputCoverage, ...] = ()


@dataclass(frozen=True)
class StageProgress:
    name: StageName
    status: StageStatus = "not_started"
    started_at: datetime | None = None
    finished_at: datetime | None = None


@dataclass(frozen=True)
class RunError:
    code: str
    message: str
    stage: StageName | None = None


@dataclass(frozen=True)
class RunEvent:
    sequence: int
    type: RunEventType
    occurred_at: datetime
    stage: StageName | None = None
    iteration: int | None = None
    message: str | None = None


@dataclass(frozen=True)
class RunSnapshot:
    id: str
    request: RunRequest
    status: RunStatus
    stages: tuple[StageProgress, ...]
    created_at: datetime
    updated_at: datetime
    report_language: ReportLanguage | None = None
    warnings: tuple[str, ...] = ()
    supplemental_discovery_iteration: int = 0
    error: RunError | None = None
    last_event_sequence: int = 0
    report: FinalReport | None = None


@dataclass
class Usage:
    github_requests: int = 0
    llm_input_tokens: int = 0
    llm_output_tokens: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def llm_total_tokens(self) -> int:
        return self.llm_input_tokens + self.llm_output_tokens


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
    is_private: bool = False
    is_archived: bool = False
    is_fork: bool = False
    parent_full_name: str | None = None
    mirror_url: str | None = None
    size_kb: int = 0
    latest_release_at: str | None = None
    found_by: list[str] = field(default_factory=list)

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


@dataclass(frozen=True)
class DiscoveryResult:
    candidates: tuple[CandidateRepository, ...]
    successful_queries: tuple[str, ...]
    failed_queries: tuple[str, ...]
    verified_suggestions: tuple[str, ...]


@dataclass(frozen=True)
class EvidenceMaterial:
    kind: EvidenceMaterialKind
    path: str
    url: str
    excerpt: str


@dataclass(frozen=True)
class EvidenceRepository:
    repository: CandidateRepository
    materials: tuple[EvidenceMaterial, ...]
    tree_paths: tuple[str, ...]
    relation_kind: RepositoryRelationKind
    relation_key: str


@dataclass(frozen=True)
class RejectedEvidenceCandidate:
    full_name: str
    reason: EvidenceRejectionReason


@dataclass(frozen=True)
class EvidenceResult:
    repositories: tuple[EvidenceRepository, ...]
    rejected_candidates: tuple[RejectedEvidenceCandidate, ...]
    inspected_count: int
    supplemental_discovery_count: int


@dataclass(frozen=True)
class AnalysisEvidence:
    evidence_id: str
    label: str
    url: str
    quote: str | None = None


@dataclass(frozen=True)
class AnalysisCandidateDecision:
    repository: str
    eligible: bool
    canonical_project: str
    qualification_reason: str
    evidence: tuple[AnalysisEvidence, ...]
    matched_function_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class RequirementAssessment:
    requirement_id: str
    kind: RequirementKind
    requirement: str
    status: RequirementStatus
    explanation: str
    evidence: tuple[AnalysisEvidence, ...]


@dataclass(frozen=True)
class RankedProject:
    repository: str
    score: int
    relevance_reason: str
    assessments: tuple[RequirementAssessment, ...]
    risks: tuple[str, ...]


@dataclass(frozen=True)
class AnalysisResult:
    candidate_decisions: tuple[AnalysisCandidateDecision, ...]
    ranked_projects: tuple[RankedProject, ...]


@dataclass(frozen=True)
class ReportEvidence:
    evidence_id: str
    label: str
    url: str
    quote: str | None = None


@dataclass(frozen=True)
class ReportRequirement:
    requirement_id: str
    kind: RequirementKind
    requirement: str
    status: RequirementStatus
    explanation: str
    evidence: tuple[ReportEvidence, ...]


@dataclass(frozen=True)
class ReportProject:
    repository: str
    url: str
    score: int
    relevance_reason: str
    confirmed: tuple[ReportRequirement, ...]
    gaps: tuple[ReportRequirement, ...]
    last_pushed_at: str | None
    is_archived: bool
    license: str | None
    latest_release_at: str | None
    risks: tuple[str, ...]


@dataclass(frozen=True)
class PublicUsage:
    llm_input_tokens: int
    llm_output_tokens: int
    llm_total_tokens: int


@dataclass(frozen=True)
class FinalReport:
    language: ReportLanguage
    projects: tuple[ReportProject, ...]
    markdown: str
    usage: PublicUsage
