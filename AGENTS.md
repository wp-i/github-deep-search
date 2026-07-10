# Agent Rule Gate

This repository has non-negotiable architecture rules. They are not optional
review notes and they are not test-only expectations. Apply this gate before
choosing an implementation strategy or editing files.

These rules override local implementation convenience. If a change would make a
sample look better by teaching the runtime a fixed phrase, wording pattern, or
language marker, the change is forbidden even when the phrase looks generic.

Both new development and bug fixes must follow this sequence: trace the flow,
locate the earliest incorrect stage, make the minimal root-cause adjustment,
and verify with real tests. Do not use temporary compensation, fallback patches,
test-only rewrites, or downstream rescue logic as a development, debugging, or
testing strategy. A change that only masks the observed failure without fixing
the stage that produced it is invalid.

## Regression Requirement for Confirmed Failures

When a change addresses a confirmed failing test case, run that same case again
against the real configured providers after the final code change. The
regression run must produce a complete, readable report; a process that reaches
report generation but fails while encoding, writing, or otherwise delivering
the report does not count as a passing regression.

LLM-assisted search is nondeterministic. For a confirmed failing case, perform
at least two independent real regression runs after the final change and retain
their outputs or traces for comparison. Assess the invariant rather than exact
repository names or ordering: no fabricated capability evidence, no
contradictory candidate tiering, and no empty result when meaningful verified
adjacent projects survive. If outcomes differ, record the difference and trace
any violation before declaring the fix complete.

## Real-World Validation for Test Engineering Changes

Any change to test code, evaluation harnesses, scenario cards, trace schemas,
test fixtures, browser checks, test scripts, or test-report delivery is
incomplete until it has been exercised against the real configured providers.
Passing mocked, fixture-based, static, or self-referential tests alone is not
evidence that the testing change works.

After the final test-engineering change, run a real end-to-end case and retain
the complete readable report and trace. The run must establish both:

1. The changed test or evaluation mechanism records and validates the intended
   production output correctly.
2. The changed mechanism detects a condition that the prior test setup did not
   verify, such as a missing stage artifact, unsupported claim, inconsistent
   tier, incomplete report delivery, or other previously unobserved blind spot.

Use the current request, real Provider responses, and repository evidence to
demonstrate the second point; do not manufacture a passing demonstration with
test-only phrases, fixed repositories, static aliases, or expected reports. If
the real run does not establish both points, keep improving the test engineering
and repeat the real validation. Record the case, configuration fingerprint,
trace, report, observed blind spot, and conclusion in the release evaluation
artifacts.

## Regression Closure Loop

Real validation is a closure loop, not a final checkbox. When a real run,
browser verification, or independent review identifies a reproducible problem:

1. Trace it to the earliest incorrect stage and make the smallest compliant fix.
2. Run the affected deterministic tests and then rerun the same real case.
3. Confirm from the new report, trace, and evidence that the original problem is
   resolved without fabricated claims, contradictory tiering, or lost meaningful
   adjacent results.
4. Treat every newly discovered, evidence-backed problem as a new open item and
   repeat this loop from step 1.

Do not declare a test, fix, release candidate, or regression complete while the
latest real run contains an unresolved evidence-backed issue in scope. For LLM-
assisted search failures, the final successful iteration still requires at least
two independent real runs as specified above.

## No Premature Handoff During Active Validation

Once a regression closure loop has started, do not stop at an intermediate
diagnosis, a passing unit test, a repaired helper script, or a single successful
provider call. Those are progress events, not completion events.

- Keep an explicit plan with the next required action in progress until every
  required deterministic check, real run, review, and closure condition has
  passed.
- When a command fails, inspect and repair the immediate execution boundary,
  then retry the blocked command before reporting status. Do not turn a tool
  failure into a handoff message.
- When a real run discovers a new evidence-backed issue, update the plan and
  continue its closure loop immediately; do not ask whether to proceed unless a
  user decision or unavailable external state is genuinely required.
- Before sending a completion message, explicitly verify that the current plan
  has no in-progress regression step, the latest real runs meet their required
  count, and no in-scope evidence-backed issue remains open.

Use concise progress updates only to state the action now being executed. They
must not imply that the work is complete.

## Mandatory Pre-Change Gate

Before changing any search, parsing, evidence, ranking, report, or cost/usage
logic, the agent must:

1. Read `CONTRIBUTING.md` and `docs/evidence-gating.md`.
2. State the proposed implementation category in the working update:
   parsing, evidence collection, generic validation, ranking/reporting, UI, or
   usage accounting.
3. Trace the failing behavior through the flow: user input -> LLM `SearchSpec`
   parsing -> search planning -> repository evidence -> ranking/reporting.
4. Identify the earliest incorrect stage and fix that stage directly.
5. Reject any proposal that depends on temporary compensation, downstream
   rescue behavior, or test-only rewrites instead of the root-cause fix.
6. Check the proposal against the forbidden-change list below.
7. If the proposal fails the gate, reject that approach before editing and
   choose a compliant design.

Do not proceed directly from a failing user-facing example to a fixture-shaped
code change.

Do not leave cleanup for later. A completed fix must not contain redundant
patches, compensating branches, or test fixtures that were added to make a
specific failing example pass.

## Forbidden Before Editing

Never improve result quality by adding any of the following to the search,
parsing, evidence, ranking, or report pipeline:

- Product-specific keyword packs.
- Static synonym or alias tables.
- Translation fallback terms or bilingual rescue maps.
- Static natural-language cleanup regexes for prompt preambles, filler wording,
  uncertainty markers, negation markers, optionality markers, or feasibility
  comments in any language.
- Static stopword, weak-word, generic-word, catalog-word, report-artifact-word,
  output-type-word, or broad-tool-word lists used to change parsing, search,
  evidence, ranking, filtering, confidence, or report conclusions.
- Repository allowlists, denylists, or sample-specific ranking boosts.
- Query-specific branches for known prompts.
- Fixture-shaped tests that pass only because the implementation knows the
  sample topic.
- Test-only static phrases, aliases, expected reports, or assertions that force
  the runtime back toward fixed-word cleanup, fixed report rewriting, or
  sample-specific ranking behavior.

If a design needs one of these, the design is invalid before any code is
written.

## Required Empty-Result Semantics

Do not confuse "do not fabricate" with "delete every uncertain result."

- Never present an unconfirmed feature as supported.
- Never assign a high-confidence score from platform, environment, output, or
  UI evidence alone when the core action/object is unconfirmed.
- A compound requirement cannot be proven by a single weak fragment of that
  requirement. Evidence must support the requested capability, not merely share
  one word with it.
- If no direct match is confirmed, retain evidence-backed partial matches and
  adjacent leads with low confidence.
- Return an empty project list only when no meaningful runnable or adjacent
  project survives verification, or when discovery itself fails to produce
  usable candidates.
- A report must not use filler such as "none" or "no leads" when candidate
  evidence exists. It must explain the confidence tier and what remains
  unconfirmed.

## Compliant Fix Directions

When behavior is wrong, use one of these directions:

- Improve `SearchSpecParser` prompt/schema validation so the LLM produces
  better current-request `must_have`, `nice_to_have`, queries, and
  `evidence_aliases`.
- Validate parser structure without teaching the runtime natural-language
  phrase meanings. Allowed deterministic parser checks are structural shape,
  field presence, deduplication, exact key coverage, punctuation/list splitting,
  and normalization. They must not decide that a phrase is filler, optional,
  negative, generic, broad, catalog-like, or output-only because it matches a
  fixed word or regex.
- For numbered or manual-operation workflows, preserve user-written steps as
  anchors, but allow the LLM to infer the repository-searchable capability. Do
  not force raw numbered steps back into `must_have` when the generated
  `SearchSpec` already represents the workflow with grounded actions, objects,
  outputs, queries, and evidence aliases.
- Improve evidence collection from repository metadata, README, paths, and
  source files without adding product-domain knowledge.
- Improve domain-neutral validation: normalization, deduplication, locality,
  coverage scoring, and unknown/missing/different classification.
- Improve tiering and report language so reliable, reference, and adjacent
  results are clearly separated.

The key invariant: product meaning must come from the current user request, the
generated `SearchSpec`, and real repository evidence.
