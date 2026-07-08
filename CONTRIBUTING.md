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

For real searches, `python scripts/start_web.py` creates `config/user_keys.env` when it does not exist. Add:

```env
GITHUB_TOKEN=
LLM_API_KEY=
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-5-mini
```

`GITHUB_TOKEN` should be a read-only token for public repositories. A fine-grained token with Public repositories only, Metadata Read-only, and Contents Read-only is enough for normal usage. Real runs also require network access to `https://api.github.com` and the configured `LLM_BASE_URL`; users in networks where OpenAI is not directly reachable should configure a proxy or use a reachable OpenAI-compatible provider.

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

Do not improve result quality by adding project-specific keyword packs, blacklist terms, translation fallbacks, fixture-shaped aliases, static cleanup regexes, or hard-coded business-domain expansions. If matching is wrong, trace the full flow first: user input -> LLM `SearchSpec` parsing -> search planning -> repository evidence -> ranking/reporting.

The parser exists because user input is diverse. For long or multi-step requests, `SearchSpecParser` must let the LLM infer the repository-searchable capability, actions, objects, interfaces, outputs, and evidence aliases for the current request. Do not replace that with static word deletion, static report rewrites, fixed synonym maps, or query-specific branches.

Before changing search, parsing, evidence, ranking, report, or usage accounting logic, apply the pre-change gate in [AGENTS.md](AGENTS.md). A proposal that needs static aliases, translation rescue terms, prompt-specific branches, or sample-shaped tests is invalid before code is edited.

The same rule applies to tests. Do not add assertions, fixtures, fake reports, or golden outputs that pass because they encode the sample's topic, wording, aliases, stopwords, report rewrites, or expected repository family. Tests should enforce architecture invariants and domain-neutral behavior, not teach the runtime how to pass one failing example.

## Pull Request Shape

- Keep changes focused.
- Include screenshots or a short before/after note for Web changes.
- Add tests for behavior changes.
- Avoid committing local secrets, caches, screenshots from private data, or generated virtual environments.
