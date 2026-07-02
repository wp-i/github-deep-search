# Real Run Captures

These assets are captured from real local runs. They are not loaded by the application and are not used as seeded result data.

## 2026-07-03: Python Terminal UI Library Search

Query:

```text
找一个开源 Python 终端 UI 库，支持表格、进度条、Markdown 渲染和富文本样式。
```

Captured assets:

- [Web workbench screenshot](assets/web-workbench-20260702.png)
- [Web result screenshot](assets/web-result-20260702.png)

Observed result summary:

- Top result: `Textualize/rich` (100/100)
- Secondary results visible in the capture: `Textualize/trogon` (76/100), `ceccopierangiolieugenio/pyTermTk` (74/100)
- The refreshed result demonstrates the Top 3 output path with evidence-backed alternatives instead of a single perfect-match item.
- Exact LLM and GitHub usage are recorded in each exported JSON report and vary by configured provider, model, and search budget.
- The run used locally configured provider credentials; no API key values are stored in this repository.

Trust boundary:

- These screenshots are documentation evidence of a real run, not an in-app demo mode.
- The runtime does not read these image files.
- The runtime does not contain bundled reports, fake repositories, seeded rankings, repository allowlists, or static product-domain keyword packs.
- Future screenshots should include the query, capture date, result list, and exported usage metadata so users can judge freshness and cost.
