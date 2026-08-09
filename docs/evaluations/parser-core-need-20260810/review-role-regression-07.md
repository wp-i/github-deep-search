# Review: role-regression-07

## Verdict

- Overall: fail
- Qualified cases: 2 of 5
- Hard constraints emitted: 0
- Downstream stages started: no

Removing structured fields caused the provider to combine core candidates again,
omit scope, and invent an inferred capability. The string-only inventory is
rejected. The previously successful structured inventory must remain, but each
entry needs one provider-authored canonical statement so deterministic projection
does not concatenate overlapping fields.
