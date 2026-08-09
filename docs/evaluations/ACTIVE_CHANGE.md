# Active Change: Single Core Need Role Split

## Snapshot

- Base commit: `d43b709e6fe1dd8e4ddede24c3299c77fbab6a9c`
- Suite ID: `single-core-role-split`
- Implementation category: parsing
- Worktree state at start of this increment: prior no-LLM parser correction
  complete and uncommitted; status matched its retained handoff
- Current status: inactive; completed increment queued for commit with all real
  and deterministic closure checks passed

## Prior completed increment retained in this worktree

The no-LLM parser qualification remains complete and is documented under
`docs/evaluations/parser-no-llm-baseline-20260804/`. Its production and test
changes are intentional prerequisites: parsing without a semantic interpreter
fails explicitly instead of fabricating a literal `SearchSpec`.

## Intentional files for the active increment

- `github_deep_search/spec_parser.py`
- `tests/test_spec_parser.py`
- `docs/evaluations/ACTIVE_CHANGE.md`
- `docs/evaluations/parser-core-need-20260810/`

No plan-generation, model, engine, provider, evidence, ranking, report, UI, or
configuration file is in scope.

## Retained active evidence

- Cases: `docs/evaluations/parser-core-need-20260810/cases.json`
- Current-provider baseline: `docs/evaluations/parser-core-need-20260810/baseline-role-results.json`
- Failure model and frozen range: `docs/evaluations/parser-core-need-20260810/failure-model.md`
- Failed first post-change run: `docs/evaluations/parser-core-need-20260810/role-regression-01.json`
- First-run review: `docs/evaluations/parser-core-need-20260810/review-role-regression-01.md`
- Failed second post-change run: `docs/evaluations/parser-core-need-20260810/role-regression-02.json`
- Second-run review: `docs/evaluations/parser-core-need-20260810/review-role-regression-02.md`
- Failed third post-change run: `docs/evaluations/parser-core-need-20260810/role-regression-03.json`
- Third-run review: `docs/evaluations/parser-core-need-20260810/review-role-regression-03.md`
- Failed fourth post-change run: `docs/evaluations/parser-core-need-20260810/role-regression-04.json`
- Fourth-run review: `docs/evaluations/parser-core-need-20260810/review-role-regression-04.md`
- Failed fifth post-change run: `docs/evaluations/parser-core-need-20260810/role-regression-05.json`
- Fifth-run review: `docs/evaluations/parser-core-need-20260810/review-role-regression-05.md`
- Unqualified sixth run: `docs/evaluations/parser-core-need-20260810/role-regression-06.json`
- Sixth-run review: `docs/evaluations/parser-core-need-20260810/review-role-regression-06.md`
- Failed seventh run: `docs/evaluations/parser-core-need-20260810/role-regression-07.json`
- Seventh-run review: `docs/evaluations/parser-core-need-20260810/review-role-regression-07.md`
- Failed eighth run: `docs/evaluations/parser-core-need-20260810/role-regression-08.json`
- Eighth-run review: `docs/evaluations/parser-core-need-20260810/review-role-regression-08.md`
- Failed ninth run: `docs/evaluations/parser-core-need-20260810/role-regression-09.json`
- Ninth-run review: `docs/evaluations/parser-core-need-20260810/review-role-regression-09.md`
- First qualified run: `docs/evaluations/parser-core-need-20260810/role-regression-10.json`
- First qualified review: `docs/evaluations/parser-core-need-20260810/review-role-regression-10.md`
- Independent qualified run: `docs/evaluations/parser-core-need-20260810/role-regression-11.json`
- Independent comparison review: `docs/evaluations/parser-core-need-20260810/review-role-regression-11.md`
- Validation summary: `docs/evaluations/parser-core-need-20260810/validation.md`
- Completion record: `docs/evaluations/parser-core-need-20260810/completion.md`

## Located root cause

The current first-call schema encourages multiple requested actions to be
bundled into `primary_user_job`, then projects adoption details as independent
hard constraints. All five differently phrased requests passed structural
validation while violating the same “one core, everything else ranks” product
invariant. No downstream stage was executed.

## Consolidated design after first real run

The three-slot attempt is superseded because `background_context` duplicated
whole requests and absorbed desired preferences. The role LLM will now return
exactly `core_need` and `ranking_preferences`; pure problem narration is ignored.
The core keeps the named scope and one dominant action-object pair. Direct
projection still produces one core requirement, zero hard constraints, and
ranking preferences as `nice_to_have`. Both the old five-slot path and the failed
three-slot path are removed rather than retained as fallbacks.

The second real run proved that the remaining free-text core can still combine
two primary objects or omit an explicit scope. The mandatory pre-third audit is
recorded in `failure-model.md`. The final consolidated role schema is
`core_need={scope, action, primary_object}` plus `ranking_preferences`; it keeps
one semantic owner while making the core cardinality structurally explicit.

The third real run proved that one undifferentiated non-core array does not
reliably preserve secondary capabilities and operating/adoption details. The
superseding schema retains the structured core and replaces that array with
`secondary_capabilities` plus `project_preferences`; both have one ranking-only
projection.

The fourth run closed preference coverage but exposed two core-selection
precision errors. The frozen final adjustment replaces the loose selection
question with a domain-neutral precedence and requires action/object field
isolation. No mapping or schema change is permitted.

The fifth run proved that direct core generation cannot structurally enforce the
choice: the provider can move multiple objects into another field. The
superseding design is an ordered atomic capability inventory; the parser selects
the first item and ranks all remaining entries. This replaces, rather than
layers over, the direct-core schema.

The sixth run proved the ordered inventory selects and preserves semantics for
all five cases. Only three-field string composition remains wrong. The final
consolidation removes those fields and uses ordered atomic capability strings;
no semantic cleanup replaces them.

The seventh run disproved string-only inventory. The final structure restores
the successful ordered scope/action/object entries and adds one canonical
statement per entry; only statements are projected, with no concatenation.

The eighth run achieved correct core selection and coverage in 5/5 but one case
duplicated meaning across the two top-level non-core arrays. The final schema is
one discriminated `requirement_items` array, eliminating that overlap at the
producer rather than cleaning duplicates later.

The tenth and eleventh runs independently qualified all five cases with one
core, full preference coverage, zero hard constraints, no invention, and no
duplicate ranking meaning. Both used the same prompt fingerprint and stopped
before plan generation.

## Closure

This parsing increment is complete and this handoff is inactive. The focused suite passed 39 tests;
the full suite passed 165 tests with 1 unrelated skip; compilation, JSON
validation, architecture grep, and diff checks passed. Two independent real
role-only runs qualified all five cases. No plan generation or downstream stage
was executed. The worktree remains intentionally uncommitted for user review.
