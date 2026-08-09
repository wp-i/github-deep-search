# Review: role-regression-03

## Verdict

- Overall: fail
- Qualified cases: 2 of 5
- Structural schema checks: 5 of 5 passed
- Hard constraints emitted: 0
- Downstream stages started: no

The structured core fixed scope retention for case 03 and kept case 04 clean.
It did not give independent secondary capabilities an explicit destination, so
case 01 still combined two objects. Cases 02 and 05 also lost adoption or
operating preferences, and case 05 treated the software runtime as the search
domain.

## Consolidated correction

Keep the structured core, but split non-core output into
`secondary_capabilities` and `project_preferences`. Both arrays are merged into
the existing ranking-only `nice_to_have` projection. This gives additional
actions/objects a required destination while separating them from cost,
license, runtime, input/output, deployment, integration, and delivery details.
No non-core slot can become a hard constraint.
