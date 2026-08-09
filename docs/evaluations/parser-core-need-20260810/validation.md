# Validation: Single Core Need Role Split

## Real role-only validation

- `role-regression-10`: qualified 5/5
- `role-regression-11`: qualified 5/5
- Prompt fingerprint equal across runs: yes
- Exact core semantics equal across runs: 5/5
- Total qualified case results: 10/10
- Positive hard constraints: 0
- Provider warnings: 0
- Plan generation or downstream stages started: no

The five input styles are a long feature list, background narrative, numbered
workflow, conversational question, and fragmented mixed-language request.

## Deterministic validation

- Focused parser and architecture tests: `39 passed`
- Full suite: `165 passed, 1 skipped`
- `python -m compileall github_deep_search tests scripts`: pass
- `git diff --check`: pass
- Retained suite JSON parse: 13 files valid
- Runtime architecture grep: no old role-slot names and no five-case product
  terms in `spec_parser.py`

The skipped test is pre-existing and unrelated to the frozen parsing range.
