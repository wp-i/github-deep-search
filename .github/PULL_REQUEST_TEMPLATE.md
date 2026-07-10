## Summary

- 

## Engineering Proof

- Change category: parsing / evidence collection / generic validation / ranking-reporting / UI / usage accounting
- Earliest affected stage: user input / SearchSpec / search planning / repository evidence / ranking-reporting
- Contract changed or preserved:
- Why a static alias, translation fallback, query-specific branch, fixture-shaped test, or downstream compensation was not used:
- Risk and rollback/verification note:

For confirmed search-quality failures, link two independent real-provider regression artifacts and state the invariant checked. Do not require identical repository names or ordering.

## Verification

- [ ] `pytest -q`
- [ ] `python -m compileall github_deep_search tests scripts`
- [ ] I traced the behavior to its earliest incorrect stage before changing search, parsing, evidence, ranking, reporting, or usage logic.
- [ ] Tests validate a domain-neutral contract and do not teach the runtime this PR's sample wording, aliases, or expected repositories.
- [ ] Test-engineering changes include a real-provider report/trace proving the mechanism works and detects a previously unverified blind spot.
- [ ] Documentation/ADR updates are included when a public contract, module boundary, or engineering decision changes.

## Notes

Add screenshots or a short before/after note for Web UI changes.
