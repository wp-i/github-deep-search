# Completion Record: No-LLM Parser Qualification

## Final implementation

- Category: parsing
- Owning behavior: `SearchSpecParser.parse(query, None)`
- Consolidated change: reject semantic planning when neither an LLM nor an
  audited fixed requirement plan is available.
- Removed path: `_literal_only_spec` and all helpers exclusively used to turn
  literal clauses into requirement roles, queries, topics, and aliases.
- Downstream changes: none

## Acceptance matrix

| Layer | Result | Evidence |
| --- | --- | --- |
| Parser deterministic | Pass | `parse(non_empty_request, None)` raises the actionable `ValueError` asserted by `tests/test_spec_parser.py`. |
| Existing LLM parser | Pass | Focused parser and architecture suites: `37 passed`. |
| Architecture | Pass | No static phrase, language marker, synonym, translation, product rule, or sample-specific test was added. |
| Removal | Pass | The literal fallback and its exclusively owned helpers no longer occur in runtime code. |
| Same real case | Pass | `post-fix-no-llm-01` stops at parse after one authenticated GitHub credential check and performs zero GitHub searches. |
| Trace | Pass | `parse=failed`; discovery, evidence, analysis, and report delivery are `not_started`. |
| Delivery | Pass | The readable failure report names the failed stage and the two valid ways to supply a semantic plan. |
| Full regression | Pass | `validation.md`: `163 passed, 1 skipped`; `python -m compileall github_deep_search tests scripts` succeeded. |
| Artifact qualification | Pass | `post-fix-no-llm-01/review.md` qualifies the expected-failure contract without invoking an LLM. |

## Post-change artifact hashes

| Artifact | SHA-256 |
| --- | --- |
| `request.json` | `4a98955aece327c108935ca2409fcdf6899fb0b12eabce6ada22424e132ef7f2` |
| `trace.json` | `e5a3ae4815804a01a585910b401537a773138fe4c5da98cf6837c851f28bfb4b` |
| `report.json` | `946b252055947470381c9f43583d7c862d8e71b5b82c19318f41c4b4eb750eea` |
| `report.md` | `50519e70a22609a27ddc3331e47e72cd6dfcc127beb4b91343d526b4e1e9c592` |

## Consolidation audit

The final runtime diff has one owner for the no-LLM behavior. It deletes the
superseded fallback instead of layering another parser, retry, serializer,
ranking adjustment, or reporting rescue over it. The LLM-enabled parser path
and all downstream modules remain unchanged.

## Deferred independent case

This increment does not claim that the configured LLM correctly classifies
background, core requirements, constraints, and preferences. That behavior
requires a new trace containing the role response and generated `SearchSpec`,
followed by a separate failure model and scope freeze before any prompt or schema
change.
