# Review: role-regression-05

## Verdict

- Overall: fail
- Qualified cases: 3 of 5
- Preference coverage: complete
- Hard constraints emitted: 0
- Downstream stages started: no

The provider evaded the one-object rule by placing multiple objects in the
action and returning a generic artifact as the object in case 01. Case 04 again
repeated the object inside the action. Repeatedly refining a direct-core prompt
is not a valid closure strategy.

## Root design decision

The provider will enumerate all independently useful capabilities as an ordered
array of structured scope/action/object items. The parser, not the provider's
free-form core string, deterministically selects the first item as the only core
and projects every later item as ranking. Project properties remain a separate
ranking-only array. This makes core cardinality an actual structural invariant.
