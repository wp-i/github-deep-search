from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


ProviderOutcome = Literal["failed", "limited"]


@dataclass
class ProviderEvent:
    provider: str
    operation: str
    outcome: ProviderOutcome
    kind: str
    stage: str = ""


@dataclass
class Usage:
    github_requests: int = 0
    github_search_requests: int = 0
    github_code_search_requests: int = 0
    github_topic_search_requests: int = 0
    github_issue_search_requests: int = 0
    llm_input_tokens: int = 0
    llm_output_tokens: int = 0
    llm_token_estimated: bool = False
    warnings: list[str] = field(default_factory=list)
    provider_events: list[ProviderEvent] = field(default_factory=list)

    @property
    def llm_total_tokens(self) -> int:
        return self.llm_input_tokens + self.llm_output_tokens


BudgetUsage = Usage


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
    latest_release_at: str | None = None
    found_by: list[str] = field(default_factory=list)

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"
