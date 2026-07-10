# ADR 0003: RunTrace is a public audit contract

- Status: Accepted
- Date: 2026-07-10

## Context

A trace projected only after a successful report cannot explain interrupted
runs. Flat warning strings also cannot reliably identify which stage was
limited. Reviewers need to distinguish a normal no-match result, a partial
provider run, an execution failure, and report-delivery failure.

## Decision

`DeepSearchEngine` records five stage lifecycles as work occurs. Providers emit
structured `ProviderEvent` records; the engine assigns them to the active stage.
The public model consists of `RunTrace`, `StageOutcome`, `RunFailure`, and
`SearchFailureArtifact`.

A stage can be `completed`, `partial`, `failed`, or `not_started`. Failed runs
raise `SearchRunFailed` with a safe, serializable artifact. CLI, Web, and MCP use
the same failure serializer. A product no-match is a successful report outcome,
not a runtime failure.

## Consequences

- Partial provider behavior remains usable and visible.
- Failure artifacts omit raw exception details while retaining stage, kind,
  exception type, retryability, usage, and a readable report.
- Human warning wording can change without breaking trace classification.
- Entry points can choose transport status codes without changing the artifact.

## Rejected alternatives

- Reconstructing every stage from the final `SearchReport`.
- Parsing warning strings to infer provider failures.
- Returning only an HTTP/CLI error message without completed-stage history.

## Validation

- `tests/test_quality_contracts.py`
- `tests/test_web.py`
- `tmp/test-engineering-validation/20260710-failure-trace/summary.json`
