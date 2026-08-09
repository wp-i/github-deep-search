# Review: role-regression-02

## Verdict

- Overall: fail
- Qualified cases: 3 of 5
- Structural schema checks: 5 of 5 passed
- Hard constraints emitted: 0
- Downstream stages started: no

Cases 02, 04, and 05 meet the invariant. Case 01 still joins popular videos
and comments as two primary objects and therefore loses comment search as a
ranking preference. Case 03 chooses a reasonable aggregation action but omits
the explicitly named government-procurement scope. Both failures originate in
the free-text `core_need` schema; no downstream evidence is required.

## Consolidation decision

A third prompt-only wording patch would leave the same unconstrained string
owner in place. Replace that free string with one structured core consisting of
an optional named scope, one action, and one primary object. Project those three
fields deterministically into the existing single core-requirement string.
