# Review: role-regression-04

## Verdict

- Overall: fail
- Qualified cases: 3 of 5
- Preference coverage: complete in all five cases
- Structural schema checks: 5 of 5 passed
- Hard constraints emitted: 0
- Downstream stages started: no

The non-core split closed the preference-loss failure. The remaining two issues
are both core-selection precision: case 01 combines two competing objects rather
than choosing one, and case 04 repeats its object inside the action. The prompt
needs one domain-neutral selection precedence and explicit field isolation; no
schema, mapping, retry, or downstream change is indicated.
