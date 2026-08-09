# Completion: Single Core Need Role Split

## Outcome

The first LLM parsing call now emits one ordered, discriminated
`requirement_items` array. Item 0 is the sole repository-searchable core;
every later capability or preference projects only to `nice_to_have`.
`hard_constraints` is structurally empty.

## Root-cause closure

- Implementation category: parsing
- Complete failing invariant: long or fragmented requests produced multiple
  mandatory features instead of one core need with ranking-only refinements.
- Earliest incorrect stage: first LLM role schema/prompt and direct projection.
- Owning implementation: `_role_prompt`, `_roles_from_llm_data`,
  `_RequirementRoles`, and role validation/consistency checks.
- Consolidated design: one mutually exclusive item inventory, structured
  capability items, one canonical statement per item, item 0 selected as core.
- Removal: the five-slot product/job/adoption/environment/experience schema and
  all superseded intermediate role schemas are absent from runtime. No fallback,
  semantic cleanup, product mapping, or downstream rescue remains.

## Final diff audit

The runtime diff stays inside the frozen parsing owner. Tests use neutral sensor
examples; the five real product scenarios exist only in retained evaluation
artifacts. No search planning, provider discovery, evidence, ranking, report,
UI, model, or configuration implementation changed.

Every acceptance row in `failure-model.md` passed. The two independent provider
runs and their reviews are retained alongside `validation.md`.
