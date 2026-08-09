# Review: role-regression-01

## Verdict

- Overall: fail
- Qualified cases: 0 of 5
- Structural schema checks: 5 of 5 passed
- Semantic single-core and preference-coverage checks: 0 of 5 passed
- Downstream stages started: no

## Shared finding

The new hard-constraint removal worked, but `background_context` became a
semantic escape slot. The provider copied whole requests into it or moved real
operating/input preferences there, while the core still absorbed a secondary
capability, an adoption property, or a language preference in three cases. A
structurally valid response can therefore still lose or duplicate desired
details.

## Earliest incorrect stage

The first-call role schema remains the earliest incorrect stage. The
three-slot design asks the provider to distinguish background from ranking even
though background is not consumed by the product, and it does not enforce one
dominant action-object pair strongly enough. No plan or downstream output was
used to reach this conclusion.

## Required consolidated correction

Remove `background_context` instead of adding validation or recovery around it.
Use exactly `core_need` plus `ranking_preferences`; instruct the provider to
ignore pure problem narration, preserve the explicitly named scope in the core,
choose one dominant action-object pair, and place every other desired detail in
ranking. The role projection remains one core and zero hard constraints.
