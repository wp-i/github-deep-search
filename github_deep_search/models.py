from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


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


@dataclass
class Requirement:
    raw: str
    intent: str
    must_have_features: list[str]
    nice_to_have_features: list[str]
    target_platforms: list[str]
    search_queries: list[str]
    repo_search_queries: list[str] = field(default_factory=list)
    code_search_queries: list[str] = field(default_factory=list)
    topic_search_queries: list[str] = field(default_factory=list)
    issue_search_queries: list[str] = field(default_factory=list)
    web_search_queries: list[str] = field(default_factory=list)
    feature_concepts: dict[str, list[str]] = field(default_factory=dict)
    evidence_aliases: dict[str, list[str]] = field(default_factory=dict)


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
    repo_search_queries: list[str] = field(default_factory=list)
    code_search_queries: list[str] = field(default_factory=list)
    topic_search_queries: list[str] = field(default_factory=list)
    issue_search_queries: list[str] = field(default_factory=list)
    web_search_queries: list[str] = field(default_factory=list)
    evidence_aliases: dict[str, list[str]] = field(default_factory=dict)

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
            repo_search_queries=self.repo_search_queries or self.search_queries,
            code_search_queries=self.code_search_queries,
            topic_search_queries=self.topic_search_queries,
            issue_search_queries=self.issue_search_queries,
            web_search_queries=self.web_search_queries,
            feature_concepts=self.feature_concepts,
            evidence_aliases=self.evidence_aliases,
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
