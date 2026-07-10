# ADR 0001: SearchSpec owns current-request semantics

- Status: Accepted
- Date: 2026-07-10

## Context

Natural-language requests vary by language, detail, workflow shape, and
uncertainty. Deterministic search code cannot interpret that open-ended product
meaning without accumulating static aliases, translation maps, stopword lists,
or query-specific branches. Those mechanisms make one sample look better while
silently narrowing the system.

## Decision

The current request and the LLM-generated `SearchSpec` are the only semantic
planning inputs. `SearchSpecParser` owns must-have/nice-to-have separation,
channel queries, evidence aliases, and compositional evidence components.

Downstream code may perform structural validation, normalization,
deduplication, locality checks, evidence collection, coverage scoring, and
unknown/missing/different classification. It may not supplement product meaning
with static domain knowledge.

## Consequences

- Parser prompt/schema changes are the correct place to improve current-request
  interpretation.
- Engine validation remains domain-neutral and reusable across languages and
  product categories.
- A literal fallback is allowed only as an explicit degraded parser outcome;
  it must be visible through provider events and a partial trace.
- Evaluation scenarios cannot be loaded by runtime code.

## Rejected alternatives

- Static synonym, translation, stopword, and keyword packs.
- Repository allowlists or sample-specific boosts.
- Downstream report rewriting that hides an incorrect `SearchSpec`.

## Validation

- `tests/test_spec_parser.py`
- `tests/test_architecture_rules.py`
- [Evidence gating architecture](../evidence-gating.md)
