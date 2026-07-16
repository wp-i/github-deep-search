# V1 Evaluation Assets

This directory contains versioned scenario cards and release-evaluation
artifacts. It is intentionally outside the runtime package: production search
code must never read scenario cards, reviewer scores, historical reports, or
expected outcomes.

Use `scripts/run_live_scenario.py` to run one explicit scenario card against
the configured providers. It creates a secret-free artifact directory with the
request metadata, serialized report, run trace, readable report, and a review
template. The script records a fingerprint of public configuration values, not
credentials.

After a live run, use `scripts/review_scenario_run.py <run-dir>` exactly once.
The first run is not qualified until `review-summary.json` has status `pass` and
the retained blind review is complete. Do not start the second independent run
while the first report is unqualified; fix the earliest failing stage and run a
new single report instead. After the first report qualifies, run the second with
`scripts/run_live_scenario.py ... --fixed-plan-from <first-qualified-run-dir>`.
This reuses the complete audited Requirement/SearchSpec while independently
rerunning real discovery, evidence, analysis, and delivery. Then review the
second run with `--compare-to <first-qualified-run-dir>` so a changed search
plan, severe candidate/count drift, or score drift is a release-blocking finding
rather than accepted LLM variation.
After independently accepting or rejecting every referenced finding and filling
all blind-review scores plus the pass/fail verdict in `review.md`, run
`scripts/review_scenario_run.py <run-dir> --finalize`. Only a final summary with
status `pass` qualifies the report.
Review artifacts are immutable; the command refuses to overwrite an existing
review. It adds:

- `decision-check.json` for the structural 30-second decision contract;
- `consistency-check.json` for optional independent-run candidate and score stability;
- `adversarial-review.json` for isolated user, semantic, and evidence roles;
- `finding-triage.json` for independent acceptance/rejection of Agent findings;
- `link-review.json` for rendered-link identity and real browser navigation;
- `review-summary.json` for the combined report qualification status.

Agent severity is a triage suggestion, not a defect decision. Only a finding
with artifact references and an independent `accepted` disposition enters the
defect closure loop. Human blind scores in `review.md` remain required.

Scenario cards describe a user request and review context. They must not name
expected repositories, fixed query wording, aliases, synonym lists, allowlists,
or expected report content. Reviewers compare evidence and tiering invariants,
not repository names or ordering.

See [the V1 test plan](../V1_DELIVERY_TEST_PLAN.md) for the complete scenario
matrix and review rubric.
