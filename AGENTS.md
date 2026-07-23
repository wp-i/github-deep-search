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

## Mandatory Trace-Locate-Change Protocol

The short sequence above is a release gate, not a slogan. Before the first code
edit, create one explicit failure model for the whole request. It must record:

1. Every observed symptom and the artifact that demonstrates it.
2. Expected behavior, actual behavior, and the invariant that is violated.
3. The complete data flow for each symptom and the earliest incorrect stage.
4. Which symptoms share one root cause and which require independent fixes.
5. A single acceptance matrix covering deterministic checks, report delivery,
   and real-provider validation.

Do not edit from the first visible symptom alone. The proposed design must cover
the complete failure model, name the one stage that owns each correction, and
identify any existing branches, helpers, or fixtures that it supersedes. If the
design adds a downstream correction while leaving the producing stage wrong,
reject it before editing.

### Case-by-Case Locate-Converge-Freeze Gate

Every reported failure must pass the following ordered gate before any runtime,
test, prompt, configuration, or evaluation code is edited:

1. **Carefully locate.** Retain the exact request, readable report, complete
   trace, configuration fingerprint, provider outcomes, generated `SearchSpec`,
   planned queries, discovery candidates, evidence decisions, ranking inputs,
   and report decision. Trace the case through user input -> LLM `SearchSpec`
   parsing -> search planning -> discovery -> repository evidence ->
   ranking/reporting. A final empty or incorrect report is a symptom, not proof
   that parsing, discovery, evidence, or ranking owns the failure.
2. **Converge the scope.** State the expected behavior, actual behavior, violated
   invariant, earliest incorrect stage, and the artifact that proves that stage
   is incorrect. List later abnormal outputs as consequences unless independent
   evidence proves another root cause. If required artifacts are missing, run a
   trace-only reproduction without implementation edits and stop until the
   earliest incorrect stage can be identified.
3. **Freeze the permitted range.** Before editing, record an explicit allowlist
   of owning modules, functions, tests, and behaviors that may change, plus an
   explicit denylist of adjacent stages that must remain untouched. The frozen
   range must be the smallest range sufficient to correct the proven root cause,
   not the smallest range that can cosmetically change the final report.
4. **Adjust only inside the frozen range.** Make one consolidated root-cause
   change in the owning path. Do not perform incidental cleanup, architecture
   expansion, prompt accumulation, new fallbacks, new retries, score tuning, or
   report rewrites outside the frozen record. If new evidence requires another
   owner or a wider range, stop editing, update the failure model, repeat the
   locate and converge steps, and publish a new freeze record before resuming.
5. **Verify the frozen invariant.** Run deterministic checks for the owning
   stage, then rerun the same real case. Inspect intermediate artifacts as well
   as the final report. Only after the case qualifies may independent fixed-plan
   confirmation and any separately diagnosed case begin.

Treat multiple failing requests as independent cases until their retained traces
prove the same earliest incorrect stage and invariant. Similar final wording,
including two empty reports, is not evidence of a shared root cause. Do not batch
their fixes, widen the frozen range, or add their product terms to runtime logic.
For each case, keep a separate failure model, scope freeze, acceptance rows, and
representative artifact. A shared implementation change is allowed only after a
cross-case comparison demonstrates one domain-neutral root cause owned by the
same path.

The working update immediately before an allowed edit must therefore include:
case identifier, evidence paths, earliest incorrect stage, frozen allowlist,
frozen denylist, single owning behavior, removal plan, and case-specific
acceptance matrix. Without that record, implementation work is prohibited.

### Anti-Patch-Stack Gate

Each behavior must have one owning implementation path. A new corrective branch,
fallback, retry, serializer rewrite, or score adjustment may not be layered over
an earlier attempted fix without first explaining why the earlier design failed
and removing or consolidating the superseded logic.

When a deterministic check or real run reveals a new in-scope failure after a
code edit:

1. Stop implementation edits.
2. Add the new evidence to the failure model and retrace it to the earliest
   incorrect stage.
3. Re-evaluate the whole acceptance matrix and the current design, not only the
   latest example.
4. Resume editing only with a consolidated root-cause design.

Before a third implementation iteration, perform and record a consolidation
audit of every touched function: confirm a single owner for each behavior and
delete superseded helpers, compensating branches, duplicate projections, and
obsolete fixtures. Repeatedly increasing retries, thresholds, floors, caps, or
output rewrites is not a closure strategy unless raw evidence proves that the
owning stage is correct and the changed value is itself the root cause.

### Required Change Record

The working update immediately before editing must state the implementation
category, complete failing invariant, earliest incorrect stage, consolidated
design, removal plan, and acceptance matrix. The completion record must show
that the final diff still matches that design, no superseded path remains, and
all matrix rows passed. A sequence of numbered output directories or successful
process exits is not evidence of design closure.

### No Anonymous Provider Downgrade

GitHub discovery, evidence collection, user runs, and real-provider tests require
an authenticated `GITHUB_TOKEN`. Missing credentials, HTTP 401, permission
rejection, and exhausted provider rate limits must produce an explicit failed run.
Never clear, replace, or omit the configured token to continue through GitHub's
anonymous public API, and never present an anonymous or rate-limited partial search
as a valid report or regression artifact. Unit tests may mock the authenticated
HTTP transport, but they must still construct the client with a non-empty test
credential so the production authentication contract remains exercised.

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

### Mandatory Report Qualification Review

A completed process and a readable Markdown file are not evidence that a report
is qualified. Review every retained real report with the repository's scenario
review workflow. The review must validate project summaries, verified
capabilities, relevance scores, stars, update dates, decision clarity, evidence
boundaries, rendered links, and provider/trace completeness.

Do not run the second independent regression merely because the first process
completed. The first report must pass its single-run content review first. If it
does not pass, retain it as one failed artifact, trace and fix the earliest
incorrect stage, then produce one new report. Only after a report qualifies may
the second independent run begin.

The second run must be reviewed against the first qualified run. Treat empty
project-set overlap, materially unstable result counts, disjoint relevance
ranges for disjoint results, or material score drift for the same project as
review failures requiring trace and closure. Do not label those conditions as
normal LLM variance without evidence-backed adjudication.

The second run must reuse the complete audited Requirement/SearchSpec from the
first qualified run via the scenario runner's explicit fixed-plan input. It
must independently rerun real discovery, evidence collection, analysis, and
report delivery. Do not regenerate a second search plan and then attribute
candidate-pool drift to downstream LLM analysis. The comparison review must
confirm identical semantic, query, and evidence plan fields before assessing
project overlap or score drift.

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
