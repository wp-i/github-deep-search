# Agent Rule Gate

This repository has non-negotiable architecture rules. They are not optional
review notes and they are not test-only expectations. Apply this gate before
choosing an implementation strategy or editing files.

These rules override local implementation convenience. If a change would make a
sample look better by teaching the runtime a fixed phrase, wording pattern, or
language marker, the change is forbidden even when the phrase looks generic.

## Mandatory Pre-Change Gate

Before changing any search, parsing, evidence, ranking, report, or cost/usage
logic, the agent must:

1. Read `CONTRIBUTING.md` and `docs/evidence-gating.md`.
2. State the proposed implementation category in the working update:
   parsing, evidence collection, generic validation, ranking/reporting, UI, or
   usage accounting.
3. Trace the failing behavior through the flow: user input -> LLM `SearchSpec`
   parsing -> search planning -> repository evidence -> ranking/reporting.
4. Check the proposal against the forbidden-change list below.
5. If the proposal fails the gate, reject that approach before editing and
   choose a compliant design.

Do not proceed directly from a failing user-facing example to a fixture-shaped
code change.

## Forbidden Before Editing

Never improve result quality by adding any of the following to the search,
parsing, evidence, ranking, or report pipeline:

- Product-specific keyword packs.
- Static synonym or alias tables.
- Translation fallback terms or bilingual rescue maps.
- Static natural-language cleanup regexes for prompt preambles, filler wording,
  uncertainty markers, negation markers, optionality markers, or feasibility
  comments in any language.
- Static stopword, weak-word, generic-word, catalog-word, report-artifact-word,
  output-type-word, or broad-tool-word lists used to change parsing, search,
  evidence, ranking, filtering, confidence, or report conclusions.
- Repository allowlists, denylists, or sample-specific ranking boosts.
- Query-specific branches for known prompts.
- Fixture-shaped tests that pass only because the implementation knows the
  sample topic.
- Test-only static phrases, aliases, expected reports, or assertions that force
  the runtime back toward fixed-word cleanup, fixed report rewriting, or
  sample-specific ranking behavior.

If a design needs one of these, the design is invalid before any code is
written.

## Required Empty-Result Semantics

Do not confuse "do not fabricate" with "delete every uncertain result."

- Never present an unconfirmed feature as supported.
- Never assign a high-confidence score from platform, environment, output, or
  UI evidence alone when the core action/object is unconfirmed.
- A compound requirement cannot be proven by a single weak fragment of that
  requirement. Evidence must support the requested capability, not merely share
  one word with it.
- If no direct match is confirmed, retain evidence-backed partial matches and
  adjacent leads with low confidence.
- Return an empty project list only when no meaningful runnable or adjacent
  project survives verification, or when discovery itself fails to produce
  usable candidates.
- A report must not use filler such as "none" or "no leads" when candidate
  evidence exists. It must explain the confidence tier and what remains
  unconfirmed.

## Compliant Fix Directions

When behavior is wrong, use one of these directions:

- Improve `SearchSpecParser` prompt/schema validation so the LLM produces
  better current-request `must_have`, `nice_to_have`, queries, and
  `evidence_aliases`.
- Validate parser structure without teaching the runtime natural-language
  phrase meanings. Allowed deterministic parser checks are structural shape,
  field presence, deduplication, exact key coverage, punctuation/list splitting,
  and normalization. They must not decide that a phrase is filler, optional,
  negative, generic, broad, catalog-like, or output-only because it matches a
  fixed word or regex.
- For numbered or manual-operation workflows, preserve user-written steps as
  anchors, but allow the LLM to infer the repository-searchable capability. Do
  not force raw numbered steps back into `must_have` when the generated
  `SearchSpec` already represents the workflow with grounded actions, objects,
  outputs, queries, and evidence aliases.
- Improve evidence collection from repository metadata, README, paths, and
  source files without adding product-domain knowledge.
- Improve domain-neutral validation: normalization, deduplication, locality,
  coverage scoring, and unknown/missing/different classification.
- Improve tiering and report language so reliable, reference, and adjacent
  results are clearly separated.

The key invariant: product meaning must come from the current user request, the
generated `SearchSpec`, and real repository evidence.
