# Validation Log

## Deterministic checks

| Check | Result |
| --- | --- |
| `pytest -q tests/test_spec_parser.py tests/test_architecture_rules.py` | `37 passed` |
| `pytest -q` with a fresh workspace-local pytest temp directory | `163 passed, 1 skipped in 15.22s` |
| `python -m compileall github_deep_search tests scripts` | Passed |
| `git diff --check` | Passed |
| Removed-helper search in `github_deep_search` and `tests` | No runtime occurrence; one architecture-test forbidden-name sentinel only |
| JSON parse of scenario and post-change review summary | Passed |

The first full-suite attempt reached `155 passed, 1 skipped` but could not create
eight fixtures under the pre-existing user temp directory
`C:\Users\w\AppData\Local\Temp\pytest-of-w`. The suite was rerun from a fresh
workspace-local temp directory and passed completely; no product or test code
was changed to address the environment boundary.

## Same-case real-provider check

- Request: unchanged from `scenario.json`
- LLM: disabled
- GitHub credential contract: authenticated and validated
- GitHub authentication requests: `1`
- GitHub search requests: `0`
- Tavily credits: `0`
- LLM tokens: `0`
- Trace result: expected `invalid_request` at `parse`
- Downstream stages: all `not_started`
- Readable failure artifact: delivered

The post-change artifact is stored in `post-fix-no-llm-01/` and reviewed in
`post-fix-no-llm-01/review.md`.
