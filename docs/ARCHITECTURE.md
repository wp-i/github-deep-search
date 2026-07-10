# GitHub Deep Search Architecture

This document describes the repository as it exists in V1. It is a map for
reviewing data ownership, evidence boundaries, failure semantics, and public
outputs; it is not a target-state redesign.

## Runtime flow

```text
user input
  -> SearchSpecParser / SearchSpec
  -> channel-specific discovery plan
  -> GitHub and optional Tavily candidates
  -> README, path, and source evidence
  -> deterministic evidence gate and tiering
  -> SearchReport
  -> Markdown / JSON / Web / MCP projections
```

`DeepSearchEngine.run` is the orchestrator. It owns the five public trace
stages: `parse`, `discovery`, `evidence`, `analysis`, and `report_delivery`.
Each stage is opened before work begins and closed from the work actually
observed in that stage.

## Ownership and dependency direction

| Owner | Responsibility | Must not own |
| --- | --- | --- |
| `spec_parser.py` | Interpret the current request into `SearchSpec`, including channel queries, must-have/nice-to-have separation, evidence aliases, and compositional evidence components | Repository ranking or fixed product-domain dictionaries |
| `providers/` | Perform external requests and record usage, warnings, and structured `ProviderEvent` outcomes | Requirement meaning, evidence conclusions, or candidate tiering |
| `engine.py` discovery | Execute the current `SearchSpec` plan and merge candidates | Capability claims derived only from metadata or popularity |
| `engine.py` evidence gate | Collect local repository material and classify supplied requirements as supported, different, missing, or unknown | Product-specific synonyms, translation rescue, or sample-specific branches |
| `engine.py` tiering/reporting | Separate reliable, reference, and adjacent results while retaining meaningful low-confidence leads | Promoting an unconfirmed core capability or deleting every uncertain result |
| `serializers.py`, `web.py`, `mcp_server.py` | Project the same report or failure artifact into public interfaces | Reinterpreting evidence or rescuing a failed upstream decision |
| `tests/` and release evaluation artifacts | Verify domain-neutral contracts and real-provider behavior | Runtime input, hidden prompts, repository allowlists, or expected sample answers |

Dependencies point from orchestration toward domain models and provider
adapters. Evaluation assets are consumers of public artifacts and are never
read by the runtime search path.

## Semantic boundary

Product meaning comes only from the current user input, the generated
`SearchSpec`, and repository evidence. The parser owns interpretation. The
engine may normalize, deduplicate, collect, compare, score, and classify the
parser-supplied structure, but it must not add business meaning through static
keyword packs or fixture knowledge.

See [ADR 0001](adr/0001-search-spec-semantic-ownership.md).

## Evidence and public claims

`EvidenceCoverage` is the current compatibility contract for each requested
feature. `EvidenceReference` is its additive, repository-local observation record:
it identifies the evidence kind, README/path/source locator, bounded excerpt,
matched aliases, and line position when the material is line-addressable. The
existing evidence string fields remain the backward-compatible projection. Its
status is one of:

- `supported`: repository material confirms the capability;
- `different`: repository material supports a materially different workflow;
- `missing`: repository material explicitly says the capability is absent;
- `unknown`: the collected material does not establish a conclusion.

The evidence gate caps scores and determines `core_confirmed`. A project with
an unconfirmed core requirement may remain visible only as a reference or
adjacent lead. When tiering marks a project as reference/lead, its public
`recommendation` is replaced by the evidence-derived `reference_reason`; raw
LLM recommendation prose cannot cross that boundary.

See [ADR 0002](adr/0002-evidence-status-and-report-claims.md) and
[evidence-gating.md](evidence-gating.md).

## Run trace and failure semantics

The stable public trace schema is defined by `RunTrace`, `StageOutcome`, and
`RunFailure` in `models.py` and recorded by `RunTraceRecorder`.

Stage status has four values:

- `completed`: the stage finished without a structured provider limitation;
- `partial`: the stage produced usable output but one or more provider events
  were failed or limited;
- `failed`: execution stopped in this stage;
- `not_started`: an earlier failure prevented the stage from running.

Run status is `completed`, `partial`, or `failed`. A failed run raises
`SearchRunFailed`, which carries a serializable `SearchFailureArtifact` with a
readable error report, usage, failure classification, completed stages, and
not-started stages. CLI, Web, and MCP project this same artifact. A normal
no-match result is not a runtime failure: it remains a completed or partial
`SearchReport` with zero or adjacent results and an explanation.

Structured `ProviderEvent` records are assigned to the stage in which they
occur. Trace status and search completeness use these events directly rather
than parsing human-readable warning text.

See [ADR 0003](adr/0003-run-trace-public-contract.md).

## Public outputs

`SearchReport` is the successful runtime result. `report_to_dict` adds two
public projections without changing evidence:

- `decisionBrief`: the decision tier, strongest confirmed features, gaps,
  unconfirmed features, and next action;
- `runTrace`: the recorded five-stage trace.

Markdown stays compact. JSON carries the complete structured contract. Web and
MCP consume the same serialization functions so interface-specific code does
not recreate search conclusions.

## Validation boundaries

Deterministic checks run with `pytest -q` and Python compilation. Browser
rendering checks are explicit `e2e` tests. Real-provider runs are release
evaluation artifacts, not ordinary unit tests; they retain the request, a
secret-free configuration fingerprint, report, trace, provider events, and
invariant assessment. A confirmed LLM-assisted failure requires two independent
real runs after the final fix.

The current failure-trace validation package is under
`tmp/test-engineering-validation/20260710-failure-trace/`. It includes the
pre-fix comparison and the final two-run summary.
