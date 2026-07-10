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
Review artifacts are immutable; the command refuses to overwrite an existing
review. It adds:

- `decision-check.json` for the structural 30-second decision contract;
- `adversarial-review.json` for isolated user, semantic, and evidence roles;
- `finding-triage.json` for independent acceptance/rejection of Agent findings;
- `link-review.json` for rendered-link identity and real browser navigation;
- `review-summary.json` for the combined, non-final review status.

Agent severity is a triage suggestion, not a defect decision. Only a finding
with artifact references and an independent `accepted` disposition enters the
defect closure loop. Human blind scores in `review.md` remain required.

Scenario cards describe a user request and review context. They must not name
expected repositories, fixed query wording, aliases, synonym lists, allowlists,
or expected report content. Reviewers compare evidence and tiering invariants,
not repository names or ordering.

See [the V1 test plan](../V1_DELIVERY_TEST_PLAN.md) for the complete scenario
matrix and review rubric.
