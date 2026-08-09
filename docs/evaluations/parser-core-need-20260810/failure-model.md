# Failure Model: Single Core Need Role Split

## Scope and evidence

- Suite ID: `single-core-role-split`
- Implementation category: parsing
- Base commit: `d43b709e6fe1dd8e4ddede24c3299c77fbab6a9c`
- Baseline: `baseline-role-results.json`
- Cases and expected semantics: `cases.json`
- Provider: configured real LLM (`deepseek-v4-flash`)
- Executed stage: first LLM role call only
- GitHub discovery, evidence, ranking, and reporting: not started

Two preliminary executions were discarded before evidence capture because the
PowerShell stdin/output boundary replaced Chinese characters. The retained
baseline used a UTF-8-safe invocation and ASCII-escaped JSON output; its request
strings and provider responses are intact.

## Product invariant

Each request produces exactly one repository-searchable core need. The core
contains the essential domain or platform, primary action, and primary object
needed to preserve the user's main job. Every other desired capability, cost,
license, deployment mode, runtime environment, output format, integration, and
convenience detail is a ranking preference. Background explains the problem but
does not become search material. The role stage emits no positive hard
constraints.

## Symptoms, expected behavior, and actual behavior

| Case | Expected | Actual | Violated invariant |
| --- | --- | --- | --- |
| 01 long feature list | One core for discovering/searching popular Douyin videos; all other details rank. | Comments, screenshots, and file delivery were bundled into the core; free became hard. | Secondary details expanded the core and created a hard gate. |
| 02 background narrative | One core for unified WeCom history search; filters, export, license, and deployment rank. | Contact/date filtering entered the core; open source and intranet deployment became hard. | Search refinements and adoption preferences became gates. |
| 03 numbered workflow | One core for finding/aggregating public procurement opportunities; later steps rank. | Every numbered step, including the explicitly optional weekly report, entered the core. | Structural workflow steps were treated as one mandatory conjunction. |
| 04 question style | One core for speaker identification; accuracy, export, and hardware rank. | Timeline and summary export entered both the core and preferences. | A secondary output contaminated the core and was semantically duplicated. |
| 05 fragmented mixed input | One OCR extraction core; environment, locality, transformation, output, and API details rank. | Local/no-cloud details became hard constraints. | Non-core preferences became independent gates. |

All five responses had `structural_errors=[]`; the existing validator therefore
accepts each semantically incorrect split.

## Complete role-stage data flow and earliest incorrect stage

1. The exact user request enters `SearchSpecParser._role_prompt`.
2. The current schema requests `primary_product_form`, `primary_user_job`,
   `adoption_constraints`, `environment_preferences`, and
   `experience_preferences`.
3. The prompt explicitly tells the provider to preserve several actions together
   in `primary_user_job`, without defining one dominant reusable search need.
4. The provider follows that contract and bundles secondary actions and outputs
   into the primary job in cases 01, 03, and 04.
5. `_roles_from_llm_data` maps every `adoption_constraints` value into
   `hard_constraints`, producing excess gates in cases 01, 02, and 05.
6. `_role_validation_errors` checks shape and exact duplicates but has no
   structural representation of “one core, everything else ranks”; all five
   responses pass.
7. Plan generation was intentionally not executed in this suite.

The earliest incorrect stage shared by all five cases is the first LLM role
schema/prompt and its direct projection. No downstream correction is permitted.

## Frozen range

Allowlist:

- `github_deep_search/spec_parser.py`
  - `_RequirementRoles` storage needed by the role stage
  - `_role_prompt`
  - `_roles_from_llm_data`
  - `_role_validation_errors`
- `tests/test_spec_parser.py`
  - domain-neutral role-schema, projection, and frozen-plan consistency tests
- `docs/evaluations/parser-core-need-20260810/`
- `docs/evaluations/ACTIVE_CHANGE.md`

Denylist:

- the second LLM plan's query/evidence generation behavior and query counts
- `SearchSpec`, `Requirement`, engine orchestration, discovery providers,
  evidence, ranking, project selection, serializers, reports, UI, and config
- sample-specific phrases, domain rules, keyword packs, static optionality or
  negation markers, translations, aliases, and expected repositories
- downstream rescue, retry expansion, score changes, or report rewrites

## Initial consolidated design and removal plan (superseded by retained runs)

Replace the five ambiguous role input slots with exactly three semantic slots:

- `background_context`: problem explanation that must not enter the search plan
- `core_need`: one non-empty repository-searchable statement containing the
  essential scope, primary action, and primary object
- `ranking_preferences`: every other desired property, preserved once

The direct projection remains compatible with the existing downstream contract:
`core_need` becomes the sole `core_requirement`, `hard_constraints` is always
empty, and `ranking_preferences` becomes `nice_to_have`. Remove the superseded
product-form/user-job composition and adoption/environment/experience mapping.
Do not change plan generation or downstream consumers in this increment.

## Acceptance matrix

| Layer | Acceptance condition | Evidence |
| --- | --- | --- |
| Schema | Role response has exactly one non-empty discriminated item array; item 0 is one structured capability and sole core. | Deterministic parser tests |
| Projection | The projected roles contain one core, zero hard constraints, and all remaining desired details as ranking preferences. | Deterministic parser tests |
| Frozen plan | The second stage cannot add a hard constraint or reclassify a ranking preference. | Existing role-consistency tests |
| Architecture | No product phrase, language marker, synonym, translation, or static semantic classifier is added. | Architecture tests and diff audit |
| Five-case run 1 | Every case has one semantically correct core, no hard constraint, and complete non-core preference coverage. | Retained real role outputs and manual case review |
| Five-case run 2 | A second independent run passes the same semantic invariants and is materially consistent with run 1. | Retained comparison review |
| Scope | No GitHub, evidence, ranking, or reporting stage starts. | Role-only runner record |
| Regression | Focused and full deterministic tests plus compilation pass. | Validation log |

## Working update immediately before implementation

- Case identifiers: `case-01` through `case-05` in `cases.json`
- Earliest incorrect stage: first LLM role schema/prompt and direct projection
- Single owning behavior: produce one core search need and rank every other
  desired detail
- Removal: delete the five superseded semantic slots and their projection logic
- Frozen allowlist and denylist: as stated above
- Acceptance: the single matrix above applies because retained traces prove the
  same domain-neutral owner and invariant across all five cases

## First post-change run and consolidation

`role-regression-01.json` failed manual qualification for all five cases. The
change successfully produced zero hard constraints, but the new
`background_context` slot duplicated whole requests or absorbed actual desired
properties. The core also retained a secondary capability, adoption property,
or language preference in cases 01, 02, and 04. Case 03 separated the named
government-procurement scope from its core action.

This is new in-scope evidence at the same earliest stage, so implementation was
stopped before a second real run. The complete role design was re-evaluated:
background is not consumed by downstream search and does not need an output
slot. Keeping it creates a second semantic classification problem without
product value.

Revised consolidated design:

- role JSON has exactly `core_need` and `ranking_preferences`
- pure problem narration is ignored, not emitted
- `core_need` contains the explicitly named scope plus one dominant primary
  action-object pair; independently useful secondary capabilities do not join it
- every other desired property is preserved once in `ranking_preferences`
- projection still produces one core, zero hard constraints, and all ranking
  preferences as `nice_to_have`

Revised removal plan: remove `background_context` from `_RequirementRoles`, the
prompt schema, projection validation, and tests. Do not add semantic cleanup,
fallbacks, or downstream compensation. The allowlist, denylist, owner, and
acceptance matrix remain otherwise unchanged.

## Second post-change run and mandatory pre-third consolidation audit

`role-regression-02.json` qualified cases 02, 04, and 05. Case 01 still placed
two independently useful objects in one free-text core, and case 03 omitted its
explicitly named scope. The two-slot design fixed lost preferences and hard
constraints but its unconstrained core string cannot structurally represent the
one-action/one-object invariant.

Consolidation audit of every touched function before a third iteration:

| Function or test area | Single owner | Superseded logic status |
| --- | --- | --- |
| `_RequirementRoles` | Stores the frozen role projection and exposes the existing downstream dictionary. | Five-slot fields and failed background storage are absent. |
| `_role_prompt` | Owns all semantic role instructions. | Five-slot and three-slot prompts are fully replaced; no retry prompt or example branch exists. |
| `_roles_from_llm_data` | Owns exact structural projection. | No legacy-shape acceptance, heuristic cleanup, or fallback remains. |
| `_role_validation_errors` | Owns domain-neutral shape/deduplication checks. | No product wording or semantic marker exists. |
| `_role_consistency_errors` | Prevents the plan from adding must-have items or moving preferences. | One core and zero hard constraints have one enforcement path. |
| `tests/test_spec_parser.py` | Verifies schema, projection, rejection, retry, and architecture invariants. | Superseded positive tests were removed; the old shape appears only in a rejection test. |

No duplicate projection, compensating branch, retry expansion, or obsolete
helper survives. The third and final consolidated design changes the free-text
core into a structural object with exactly `scope`, `action`, and
`primary_object`. `scope` may be empty only when the request names none; action
and object must be non-empty strings. Each field names one concept, and all
other desired details stay in `ranking_preferences`. Direct composition creates
the sole downstream core string. This remains the same owner and frozen range.

## Third post-change run and role-boundary redesign

`role-regression-03.json` qualified only cases 03 and 04. Case 01 still combined
two objects because the schema did not give a secondary searchable capability a
dedicated destination. Case 02 omitted its adoption preference. Case 05 used a
runtime environment as search scope and omitted its no-cloud preference.

The structured core itself remains the correct single owner; the failure is the
undifferentiated non-core array. The consolidated role boundary is therefore:

- `core_need={scope, action, primary_object}` for exactly one search need
- `secondary_capabilities=[]` for every independently useful action/object not
  selected as the core
- `project_preferences=[]` for every other desired cost, adoption, runtime,
  deployment, language, input/output, integration, delivery, quality, or
  convenience property

Both non-core arrays are merged once into `nice_to_have`; neither can create a
hard constraint. Pure problem narration remains omitted. Search scope describes
the subject/domain whose material is handled, never where the software runs.
This replaces the undifferentiated ranking array; it does not add a parallel
projection or fallback. The existing allowlist and denylist remain unchanged.

## Fourth post-change run and final core-selection rule

`role-regression-04.json` preserved every non-core desired detail and qualified
cases 02, 03, and 05. Case 01 combined two competing objects rather than choosing
one. Case 04 repeated its object inside the action field. Both are field-isolation
errors in `_role_prompt`; the structured schema and direct projection are
otherwise correct.

The whole acceptance matrix was rechecked. The remaining domain-neutral rule is:

1. Choose an explicitly stated main/core/primary capability when present.
2. Otherwise choose the capability that states the requested tool or workflow's
   overall purpose.
3. If multiple candidates remain equally plausible, choose the first
   independently useful capability in the user's ordering.
4. Never combine candidates to avoid the choice; move every unselected candidate
   to `secondary_capabilities`.
5. `action` contains only the operation and never repeats `primary_object`;
   `primary_object` contains one noun phrase.

This replaces the looser counterfactual selection wording. It adds no product
example, language marker, static classifier, fallback, or second implementation
path. All touched functions still have the single owners recorded in the
pre-third consolidation audit.

## Fifth post-change run and replacement of direct-core generation

`role-regression-05.json` again qualified 3/5. Case 01 moved multiple candidate
objects into the action field and used a generic artifact as its object; case 04
repeated the object in the action. This proves that asking the provider to emit
the selected core directly remains the wrong owner boundary. Further wording
changes are rejected.

The direct-core schema is superseded by one ordered capability inventory:

- `capabilities` is a non-empty array; every entry has exactly `scope`, `action`,
  and `primary_object` and represents one independently useful capability
- the provider orders entries using the already frozen selection precedence
- `project_preferences` contains all desired non-capability properties
- `_roles_from_llm_data` deterministically takes `capabilities[0]` as the sole
  core and composes every remaining capability into ranking
- there is no model-emitted core field, hard-constraint field, fallback, or
  semantic Python classifier

`_RequirementRoles` may own one domain-neutral capability-string formatter so
the core and secondary projection use the same implementation. This is inside
the existing role-projection allowlist and replaces duplicate formatting; it
does not widen the downstream scope.

## Sixth post-change run and removal of structured-string composition

`role-regression-06.json` selected the correct first core and preserved all
non-core content in all five cases. It did not qualify because composing action
plus object duplicated wording in three projected core strings. Adding token,
substring, conjunction, or language cleanup would be an invalid semantic patch.

The proven value of the new design is ordering, not the three internal string
fields. The final consolidated schema is therefore:

- `capabilities` is a non-empty ordered array of atomic, self-contained,
  repository-searchable capability strings
- each independently useful action/object pair is a separate string
- `capabilities[0]` is the sole core; later values rank
- `project_preferences` contains all desired non-capability properties and also ranks

Remove `_RequirementRoles` scope/action/object storage and its formatter, and
remove tuple parsing from `_roles_from_llm_data`. No cleanup or fallback replaces
them. Exact list/string shape and deduplication remain the only deterministic
checks. This is a consolidation inside the same frozen role owner.

## Seventh post-change run and canonical capability statement

`role-regression-07.json` regressed to 2/5: string-only entries again combined
video and comment search, omitted the named scope in another core, and invented
an unrequested transcription capability. Comparing runs 06 and 07 isolates the
design boundary: the ordered structured entry successfully enforced semantic
atomization and coverage; only deterministic field concatenation was wrong.

The final capability item therefore has exactly four fields:

- `scope`, `action`, and `primary_object` preserve the successful atomization
- `statement` is one concise, self-contained expression of those same three
  fields, with no additional capability or property

The parser selects `capabilities[0].statement` as the sole core and every later
statement as ranking. It validates exact shape and non-empty strings but does not
rewrite, concatenate, or semantically clean any value. This restores the proven
run-06 structure while deleting its faulty formatter and rejecting the failed
run-07 string-only path.

## Eighth post-change run and mutually exclusive item inventory

`role-regression-08.json` selected the correct core and preserved all desired
details in all five cases. Cases 01-04 qualified. Case 05 duplicated four ranking
details across the capability and project-preference arrays. Deterministic fuzzy
or semantic deduplication is forbidden and would not fix the producing stage.

The last overlapping boundary is removed by one `requirement_items` array:

- item 0 is a capability object and is the sole core
- every later desired detail appears once as either a capability object or a
  preference object
- capability objects contain `kind`, `statement`, `scope`, `action`, and
  `primary_object`
- preference objects contain only `kind` and `statement`
- all statements after item 0 are merged into ranking regardless of kind

The array is ordered by the existing core-selection precedence. Pure problem
narration is omitted. The parser validates the discriminated shape and projects
statements only; it performs no semantic classification, concatenation, fuzzy
deduplication, or fallback. This replaces both prior top-level arrays.

## Ninth post-change run and scope/input boundary

`role-regression-09.json` qualified cases 01-04 and eliminated cross-role
duplication. Case 05 treated PDF/image input formats as scope and consequently
lost the receipt domain from its core statement. The prompt's definition of
scope as including a content source is the earliest incorrect rule.

Replace that definition with: scope is the explicitly named subject entity,
business/content domain, or product platform. An input or output container,
file/media format, machine, runtime, deployment location, or delivery
environment is a preference unless transforming that format is itself the
request's primary job. No schema, mapping, retry, validator, or downstream
change is indicated.

## Qualified closure runs

`role-regression-10.json` and `role-regression-11.json` are two independent real
provider executions with the same five raw requests and identical prompt
fingerprint. Both qualified 5/5 under the full invariant: one semantically
correct core, zero hard constraints, complete non-core coverage, no invented
capability, and no duplicated ranking meaning. The projected role sets are
materially consistent across runs.

Both executions stopped immediately after `_role_prompt` output was projected
by `_roles_from_llm_data`. No search-plan call or downstream stage started.

## Final completion record

The final diff implements the consolidated discriminated-item design described
after run 08 and the scope correction described after run 09. No superseded
runtime schema, fallback, duplicate projection, semantic cleanup, or downstream
compensation remains. The legacy five-slot field names occur only in a rejection
test and negative prompt assertions.

The complete acceptance matrix passed: focused tests reported 39 passes; the
full suite reported 165 passes and 1 unrelated skip; compilation and diff checks
passed; and two independent real role-only runs qualified all five cases with
identical core results. `validation.md` retains the closure summary.
