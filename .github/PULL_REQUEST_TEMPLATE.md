## Summary

- 

## Contract and ownership

- Affected stage or boundary:
- Behavior owner:
- Contract changed or preserved:
- Superseded logic, tests, or documentation removed:

## Verification

- [ ] Tests represent the current product contract.
- [ ] The change fixes the owning logic without a downstream compensation branch.
- [ ] The diff is limited to the smallest confirmed responsibility boundary.
- [ ] `pytest -q`
- [ ] `python -m compileall -q github_deep_search tests scripts`
- [ ] Real Provider validation was run only if parsing, discovery, evidence, or ranking semantics changed.
- [ ] No token, API key, authorization header, private input, cache, trace, or generated report is included.

## Notes

Add browser verification for Web changes. If a meaningful conflict with
`docs/PRODUCT_CONTRACT.md` was discovered, record the decision before implementation.
