# Evidence Gating Architecture

Evidence gating is a deterministic verification step. It must not understand product domains by hard-coded business rules.

## Ownership

- The LLM/parser owns requirement understanding.
- `SearchSpec.repo_search_queries`, `SearchSpec.code_search_queries`, `SearchSpec.topic_search_queries`, `SearchSpec.issue_search_queries`, and `SearchSpec.web_search_queries` own channel-specific search planning.
- `SearchSpec.evidence_aliases` owns the concrete README/source/path phrases that can prove each `must_have`.
- The engine owns generic evidence collection, alias sanitization, matching, scoring penalties, and reference-candidate fallback.
- `must_have` owns the user's core desired outcome and hard constraints. `nice_to_have` owns optional additions, uncertain wording, and implementation guesses.

## Non-Negotiable Rule

Do not improve tests or search quality by adding sample-specific aliases, synonyms, feature keyword tables, translation mappings, cross-language expansion terms, or business word lists to the search/filter/evidence pipeline.

Bad examples:

- Adding a hard-coded alias table for deadlines, completion toggles, browser extensions, Notion sync, rounded corners, or any other fixture topic.
- Adding query-specific branches for known test prompts.
- Adding a fallback translation pass or hard-coded bilingual term map to rescue one language or domain.
- Treating a passing live sample as proof that engine-side business synonyms are acceptable.
- Adding stopword, blacklist, curated-list, or ranking-penalty word tables that encode product-domain judgment in Python code.

Correct fixes:

- Improve the `SearchSpecParser` prompt/schema so the LLM emits better `evidence_aliases`.
- Validate that every `must_have` has evidence aliases.
- Parse core and extension separately: core outcomes and hard constraints go to `must_have`; optional additions, provider choices, credentials, and uncertain implementation guesses go to `nice_to_have`.
- Keep deterministic validators domain-neutral: normalize strings, deduplicate, enforce shape, and match supplied evidence aliases.
- Return results in three tiers: reliable matches, evidence-backed partial matches, then relatively closest adjacent projects.
- Treat an unconfirmed core requirement as a strong score reduction and an adjacent-result label, not as a deletion rule. Never present an unconfirmed core requirement as supported.
- Adjacent projects may be retained only when they are close to the core requirement. If the requirement names a domain/platform, the adjacent project must match that domain/platform before output/interface evidence can keep it visible. It must remain a low-score lead and keep the core capability unconfirmed.
- A project that matches only extension details or implementation guesses is not a meaningful adjacent project. Provider credentials, API keys, deployment guesses, UI format, or optional integrations cannot compensate for an unconfirmed core outcome.
- Output/interface-only matches are not meaningful adjacent projects when the core direction is unconfirmed. Examples include projects that only share screenshots, reports, web UI, books, image editing, trading, or project-directory traits with the request.
- Verify compound core requirements compositionally: domain/platform, action, and object must co-occur in a local project statement. Do not promote a project from isolated keywords scattered across its README.
- Ignore documentation-only image paths and screenshot galleries as capability evidence. Require all named entities for compound integrations.
- Score unknown as unconfirmed rather than partial success; cap the score by weighted confirmed coverage.
- Exclude repository catalogs and project lists because mentions inside collected entries are not capabilities of the repository itself.
- Return an empty project list only when no meaningful runnable or adjacent project survives verification.
- When returning an empty project list, include a concise user-facing explanation of searched channels, why candidates were filtered, and the next search strategy.

Hard-coded infrastructure constants are allowed only when they are not product-domain judgment, for example API paths, default request limits, output field names, or UI labels. Product meaning must come from the current user input, generated `SearchSpec`, and repository evidence. Moving a fixture-shaped keyword table from Python into JSON, a translation fallback, a prompt example, or a test fixture does not make it acceptable.
