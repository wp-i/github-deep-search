# Expected-Failure Qualification Review

## Scope

- Case ID: `boss-active-recruitment-filter`
- Run ID: `post-fix-no-llm-01`
- Review date: 2026-08-04
- Review type: bounded manual review of an expected parse-stage failure
- LLM usage during review: none

The repository's standard scenario-review script is designed for successful
`SearchReport` artifacts: it invokes the configured review LLM and expects a
`reportMarkdown` field. This run intentionally produced an `errorReportMarkdown`
artifact before discovery, so applying that script would both violate this case's
no-LLM boundary and misclassify the expected failure. This review therefore
qualifies only the frozen parser-failure contract. It does not qualify a search
result and cannot be used as a fixed-plan source.

## Machine-readable facts

- Trace status: `failed` as expected
- Failed stage: `parse`
- Failure kind: `invalid_request`
- Retryable: `false`
- GitHub authentication checks: `1`
- GitHub search requests: `0`
- Tavily credits: `0`
- LLM input/output tokens: `0/0`
- Discovery, evidence, analysis, and report delivery: `not_started`
- Readable failure report: yes

## Qualification checks

| Check | Result | Evidence |
| --- | --- | --- |
| No unqualified `SearchSpec` escapes parsing | Pass | `trace.json` records `parse=failed` with no outputs. |
| No downstream provider work executes | Pass | Usage records zero GitHub searches and zero Tavily credits; all later trace stages are `not_started`. |
| Failure is explicit and actionable | Pass | `report.md` requires a configured LLM or an audited fixed requirement plan. |
| Failure is not presented as an empty search result | Pass | `report.md` is titled `Search run failed` and contains no candidate conclusion. |
| Report is readable and delivered | Pass | `report.md` contains the failed stage, kind, reason, retryability, and stage disposition. |

## Qualification verdict

- Verdict: **pass for the expected parser-failure contract**
- Search-report qualification: **not applicable**
- Eligible as a fixed-plan source: **no**
- Open finding in the frozen range: **none**

The independent LLM-enabled role-classification case remains unreviewed and is
outside this run's qualification.
