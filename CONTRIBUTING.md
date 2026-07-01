# Contributing

Thanks for helping GitHub Deep Search become useful for more product and engineering research tasks.

## High-Value Contributions

- Reproducible search misses with the exact query, expected kind of project, and actual output.
- Better result evaluation tests that do not hard-code fixture-specific synonyms.
- Web UX improvements that reduce first-run friction.
- Provider compatibility fixes for OpenAI-compatible LLM endpoints.
- Documentation examples based on real project-discovery workflows.

## Local Setup

```bash
python scripts/start_web.py
```

For real searches, copy `config/user_keys.example.env` to `config/user_keys.env` and add:

```env
GITHUB_TOKEN=
LLM_API_KEY=
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-5-mini
```

## Test Before Opening a PR

```bash
pytest -q
python -m compileall github_deep_search tests scripts
```

Rendered Web checks are optional but useful when touching `github_deep_search/static`:

```powershell
pip install -r requirements-e2e.txt
python -m playwright install chromium
pytest -q -m e2e
```

## Architecture Boundary

Do not improve result quality by adding project-specific keyword packs, blacklist terms, translation fallbacks, fixture-shaped aliases, or hard-coded business-domain expansions. If matching is wrong, improve requirement parsing, evidence collection, or the generic evidence validator.

Before changing search, parsing, evidence, ranking, report, or usage accounting logic, apply the pre-change gate in [AGENTS.md](AGENTS.md). A proposal that needs static aliases, translation rescue terms, prompt-specific branches, or sample-shaped tests is invalid before code is edited.

## Pull Request Shape

- Keep changes focused.
- Include screenshots or a short before/after note for Web changes.
- Add tests for behavior changes.
- Avoid committing local secrets, caches, screenshots from private data, or generated virtual environments.
