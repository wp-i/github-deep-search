# ADR 0002: Evidence status is separate from report prose

- Status: Accepted
- Date: 2026-07-10

## Context

LLM analysis can produce fluent recommendations that exceed the supplied
README, path, or source evidence. Keeping model prose and evidence status in the
same trust tier allows an unconfirmed capability to appear supported even when
scores and coverage fields say otherwise.

## Decision

`EvidenceCoverage` is authoritative for capability status. Deterministic
evidence gating computes `core_confirmed`, caps scores, and assigns reliable,
reference, or adjacent tiers. Public fields must agree with that result.

When a project is marked reference/lead, `recommendation` is replaced by the
evidence-derived `reference_reason`. Unknown remains unknown; missing requires
explicit repository material; a different workflow must not be presented as
support for the requested one.

## Consequences

- LLM prose is advisory until it passes the evidence gate.
- Adjacent projects remain visible when meaningful, but cannot imply core
  support or direct usability.
- JSON, Markdown, Web, and MCP share the same evidence decision.
- Stronger structured evidence references can be added later without changing
  the status meanings.

## Rejected alternatives

- Trusting the recommendation because a model saw the README.
- Deleting all unknown candidates.
- Rewriting only the Markdown while leaving contradictory JSON fields.

## Validation

- `tests/test_evidence_gate.py`
- `tests/test_quality_contracts.py`
- Final real regression summary:
  `tmp/test-engineering-validation/20260710-failure-trace/summary.json`
