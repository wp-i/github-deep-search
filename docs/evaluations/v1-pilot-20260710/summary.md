# V1 Scenario Pilot Summary

- Release evaluation: `v1-pilot-20260710`
- Case: `pilot-nonexpert-link-price`
- Configuration fingerprint: `4287dda79b5933f7d295233e43a7cdd7daf89b10e6324262b0d4096d8b69a2da`
- Real runs retained: 11 (including two final post-closure regressions)
- Automated conclusion: pass; independent human triage pending
- Independent human score: pending

## What changed

This pilot introduced a versioned scenario-card schema, an explicit live runner,
a secret-free artifact layout, a blind-review template, and a runtime-isolation
contract. The runtime package does not read evaluation assets.

The runner initially exposed a Windows-specific run-ID formatting failure before
any Provider call. The run-ID generator was made platform-neutral and covered by
a deterministic test. Artifact validation was also extended from exact configured
credential checks to length-qualified GitHub, OpenAI, and Bearer token shapes.
This avoids both leaking credential-shaped values and falsely classifying a
repository path containing `asterisk-` as an API key.

## Real-provider evidence

| Run | Trace | Provider events | Returned tier invariant | Report delivery |
| --- | --- | --- | --- | --- |
| `20260710t043346z-2fe7a0a7` | `partial`; evidence partial | One GitHub evidence request failed | Three leads; core unconfirmed; recommendation equals reference reason | Complete and readable |
| `20260710t043728z-670d6c0c` | `partial`; discovery and evidence partial | One GitHub discovery and one evidence request failed | Three leads; core unconfirmed; recommendation equals reference reason | Complete and readable |

Both runs contain `request.json`, `trace.json`, `report.json`, `report.md`, and
`review.md`. The final secret-shape scan passed across all ten files.

## Previously unverified blind spot

Before this pilot, real runs were retained in ad-hoc local directories and did
not prove that a versioned scenario card could generate a complete, reviewable
release package. The pilot now records Provider limitations in both the public
trace and the reviewer-facing machine facts, while preserving a readable report.
It also verifies that evaluation assets stay outside the runtime dependency
path.

## Conclusion and open work

The scenario-card mechanism records and validates real production output and
detects partial Provider execution that a report-only check could miss. No
fabricated core support, contradictory reliable tier, recommendation mismatch,
or secret-shaped value was found in the retained artifacts.

This conclusion covers the pilot mechanism and this case only. The scenario
matrix still requires expansion to 42 cards, independent blind scoring, link
review across the release set, broader adversarial review coverage, and a
defect matrix before V1 release.

## Review integration and closure iteration

The next evaluation slice added three review artifacts to a retained run:

- `decision-check.json`: a five-item structural 30-second decision check;
- `adversarial-review.json` plus `finding-triage.json`: independent user,
  semantic, and evidence reviews whose severities remain triage suggestions
  until an independent reviewer accepts them;
- `link-review.json`: offline GitHub owner/name validation followed by a real
  Playwright navigation from the rendered report link.

The first integrated review found that `raw.top_ranked_candidates.score` mixed
an unbounded pre-analysis retrieval score with the public 0–100 evidence score.
The field is now `discovery_score`, carries `score_basis=pre_analysis_retrieval`,
and is documented separately from `topProjects.score`.

A subsequent review exposed a more important evidence boundary: LLM-reported
`different_features` could enter public gaps even while the corresponding
coverage remained unknown. The final evidence gate now discards unverified
model differences; only `EvidenceCoverage(status=different)` and deterministic
constraint differences may enter `differentFeatures`. Discard counts remain
visible in `raw.evidence_gate` and usage warnings.

Final independent real regressions after that change:

| Run | Trace | Unverified model differences | Public result invariant |
| --- | --- | ---: | --- |
| `20260710t072918z-6c000072` | partial analysis | 0; analysis Provider response was partial | One lead; no public differences; both core features unknown |
| `20260710t073214z-7f1570db` | completed | 53 discarded | Two leads; no public differences; both core features unknown |

Both final reports retained `discovery_score` semantics, readable report
delivery, no fabricated support, no reliable-tier contradiction, and no
recommendation/reference mismatch. The latest integrated review passed the
structural decision check (5/5) and real GitHub link review (HTTP 200 with
owner/name identity preserved). Agent findings remain in human triage; their
severity does not automatically become a defect conclusion.

## Evidence-reference closure iteration

The first live run after adding `EvidenceReference` returned only unknown
coverage and therefore did not exercise a populated public reference. The
follow-up traces showed that the selected projects reached the component-based
coverage path, where unknown coverage had no observation record. The root-cause
fix adds an empty-alias reference only for material actually inspected; when no
README, path, or source material is available, it records the candidate identity
as an audit observation. Neither form changes coverage status, score, tier, or
public support claims.

Final real run `20260710t081525z-d6fa0515` retained a complete, readable report
and five-stage trace. Its evidence stage was `partial` because one GitHub file
request returned 404, but it emitted six structured references; five were
empty-alias unknown observations. All three returned projects kept the core
capability unconfirmed and `differentFeatures` empty. This demonstrates the
previously unobserved condition—unknown, component-based coverage would lose
its audit record—without manufacturing capability evidence.
