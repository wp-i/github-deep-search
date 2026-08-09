# Review: role-regression-06

## Verdict

- Overall artifact qualification: fail
- Correct primary-core selection: 5 of 5
- Complete non-core coverage: 5 of 5
- Zero hard constraints: 5 of 5
- Clean projected core wording: 2 of 5
- Downstream stages started: no

The ordered inventory solves the semantic role problem. The remaining failure is
introduced by composing action and object fields when the provider already
placed the object in its action phrase. Semantic cleanup would violate the
architecture rules. The smallest root correction is to represent each ordered
capability as one atomic, self-contained repository-searchable string. Array
ordering still gives the parser a structural single-core decision without any
concatenation or cleanup.
