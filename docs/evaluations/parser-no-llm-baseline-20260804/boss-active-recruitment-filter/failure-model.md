# Failure Model: No-LLM Requirement Role Projection

## Case identity

- Case ID: `boss-active-recruitment-filter`
- Baseline run: `baseline-no-llm-01`
- Implementation category: parsing
- Base commit: `d43b709e6fe1dd8e4ddede24c3299c77fbab6a9c`
- Request: `Boss直聘中没有时间筛选和关键词筛选，我需要筛选一周内活跃的招聘需求，最好还能通过指定关键词在搜索结果中进一步搜索`
- Configuration: authenticated GitHub, LLM disabled, configured Tavily enabled

## Observed symptoms and evidence

| Symptom | Retained evidence |
| --- | --- |
| Problem background was projected as a required capability. | `baseline-no-llm-01/report.json` -> `requirement.mustHaveFeatures[0]` |
| The explicit preference was projected as required and `niceToHaveFeatures` was empty. | `baseline-no-llm-01/report.json` -> `requirement.mustHaveFeatures[2]` and `niceToHaveFeatures` |
| Domain, action, object, output, and interface concepts were empty. | `baseline-no-llm-01/report.json` -> `requirement.featureConcepts` |
| Literal conversational clauses became repository, code, topic, issue, and web queries. | `baseline-no-llm-01/report.json` -> `requirement.*SearchQueries` |
| Parse was reported completed despite an unqualified semantic plan. | `baseline-no-llm-01/trace.json` -> `parse.status=completed` |
| Discovery returned 80 candidates and evidence analyzed 20, but all report candidates were filtered. | `baseline-no-llm-01/report.json` -> `raw.candidate_count=80`, `deep_pool_count=20`, and analysis selection decisions |
| Eighteen candidates were filtered for no meaningful adjacent evidence; two were catalogs. | `baseline-no-llm-01/report.json` -> `raw.pipeline_snapshots.analysis.selection_decisions` |
| The final empty report cited the optional keyword refinement as the core requirement. | `baseline-no-llm-01/report.md` and `report.json` -> `raw.core_requirement` |

Artifact hashes captured before implementation:

| Artifact | SHA-256 |
| --- | --- |
| `request.json` | `4a98955aece327c108935ca2409fcdf6899fb0b12eabce6ada22424e132ef7f2` |
| `trace.json` | `af1425df3267a585a6d2bc85a7a7f03cb1975d134cca76806df6487b897134f1` |
| `report.json` | `522c3f5c1840310923bef3f3c873582eb460dc8612058ee2575c9b08cc4756b2` |
| `report.md` | `def815eda1d75da204bfc032d8886b855a36e2a336b80e2d9c3627880fffcc98` |

## Expected, actual, and violated invariant

Expected: a search may start only from a semantically qualified requirement plan. Structural clauses are anchors, not requirement roles. When no semantic interpreter or audited fixed plan exists, parsing must stop with an actionable result before discovery.

Actual: the no-LLM branch treats every punctuation-delimited clause as `must_have`, fabricates literal channel queries and evidence components, and returns a normal `SearchSpec`. The orchestrator records parse as completed and spends real provider budget on the invalid plan.

Violated invariant: absence of a semantic interpreter must never be represented as a successfully classified core, hard-constraint, and optional requirement plan.

## Complete data flow and earliest incorrect stage

1. User input reaches `DeepSearchEngine.run` unchanged.
2. No LLM client is constructed.
3. `SearchSpecParser.parse(query, None)` enters the no-LLM branch.
4. `_explicit_requirement_clauses` creates three structural anchors. This punctuation split is structurally valid and is not yet the failure.
5. `_literal_only_spec` assigns all three anchors to `must_have`, leaves semantic concept groups empty, and derives literal channel queries and evidence aliases. This is the earliest incorrect stage.
6. `SearchSpec.to_requirement` faithfully projects the invalid roles.
7. Discovery executes 18 GitHub search queries plus four Tavily queries and retains 80 candidates.
8. Evidence evaluates three invalid required features for each deep candidate; 58 coverage items remain unknown.
9. Analysis cannot construct adjacent evidence because action/object/domain concepts are empty. Eighteen non-catalog candidates are filtered for `no_meaningful_adjacent_evidence`.
10. Report delivery returns an empty result and names the optional refinement as the core requirement.

All later symptoms share the parser-owned root cause. There is no independent evidence in this case that discovery, evidence gating, ranking, or reporting requires a correction.

## Frozen range

Allowlist:

- `github_deep_search/spec_parser.py`
  - the no-LLM branch in `SearchSpecParser.parse`
  - `_literal_only_spec` and helpers used only by that superseded path
- `tests/test_spec_parser.py`
  - domain-neutral no-LLM parser contract
- this case's evaluation artifacts and `docs/evaluations/ACTIVE_CHANGE.md`

Denylist:

- normal LLM role and plan prompts, schemas, retries, and validation
- `github_deep_search/engine.py`
- discovery providers and query execution
- evidence collection, evidence gating, ranking, project selection, and report wording
- configuration, UI, Web, MCP, and serializers
- product-specific phrases, optionality markers, negation markers, translations, aliases, or repositories in runtime or test logic

## Consolidated design and removal plan

The parser will fail explicitly when invoked without an LLM. It will not create a degraded `SearchSpec`. Existing engine failure handling will classify this as a parse-stage invalid request and produce the standard readable failure artifact.

Remove the superseded literal semantic plan and every helper used only to fabricate its queries, aliases, topics, or clause-as-feature projection. Do not add a second heuristic parser, fallback, retry, keyword list, or downstream rescue.

The normal LLM role-classification path is an independent next case. This change prevents the confirmed no-LLM failure from contaminating downstream stages; it does not claim to qualify LLM role decisions.

## Case-specific acceptance matrix

| Layer | Acceptance condition | Evidence required |
| --- | --- | --- |
| Parser deterministic | `parse(non_empty_request, None)` raises an actionable parse error and returns no `SearchSpec`. | Focused parser test |
| Existing LLM parser | Valid queued role and plan responses still produce the existing `SearchSpec` contract. | Existing parser suite |
| Architecture | No static phrase, language marker, synonym, translation, or domain rule is added. | Architecture-rule tests and diff review |
| Removal | No literal semantic fallback or exclusively owned helper remains. | Diff/consolidation audit |
| Same real case | Authenticated run with LLM disabled stops at parse, performs zero GitHub search requests, and preserves a readable failure artifact. | Post-change request, trace, report JSON, and Markdown |
| Trace | `parse=failed`; discovery, evidence, analysis, and report delivery are `not_started`. | Post-change `trace.json` |
| Delivery | Failure reason tells the operator to configure semantic parsing or supply an audited fixed plan; it does not emit an empty-search conclusion. | Post-change `report.md` |
| Regression | Full deterministic tests and compilation pass. | Command logs |

## Deferred independent case

The claim that LLM-enabled role classification also mis-splits conversational background, core outcome, and optional refinements requires its own retained `role_data`, generated `SearchSpec`, trace, and scope freeze. It must not be mixed into this no-LLM correction without that evidence.

## Completion status

The frozen correction is complete. The same real request now fails explicitly
at parse with zero GitHub search requests, zero Tavily credits, and no downstream
stages started. Focused tests passed (`37 passed`), the full suite passed
(`163 passed, 1 skipped`), and compilation succeeded. The expected-failure
artifact was reviewed without an LLM and has no open finding in this case's
frozen range. See `completion.md` for the final matrix and consolidation audit.
