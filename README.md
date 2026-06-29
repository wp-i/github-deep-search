# GitHub Deep Search

用一句自然语言产品想法，深度搜索 GitHub，找到真正值得研究、复用或借鉴的开源项目。

如果你正在判断“这个想法是不是已经有人做过”“有没有项目可以直接改”“哪些仓库只是参考”，GitHub Deep Search 会把需求拆成结构化搜索，走 GitHub 仓库、代码、Topic、Issue 等多路召回，读取 README / 文件路径 / 关键源码证据，然后输出 Top 项目、匹配理由、差异、缺口和本次消耗。

**一个很短的例子**

输入：

```text
我想做一个浏览器插件，可以总结网页内容，并把摘要同步到 Notion。
```

输出会重点回答：

- 哪些 GitHub 仓库最接近？
- 哪些能直接复用，哪些只是参考项目？
- 证据来自 README、源码还是路径？
- 还缺哪些核心能力？
- 本次用了多少 GitHub 请求和 LLM tokens？

## 真实运行截图

下面截图来自一次配置了真实 API key 的本地运行。它们只是文档截图，不会被 Web 应用加载，也不是内置 Demo、假仓库排行或预置报告。

![GitHub Deep Search ready state](docs/assets/real-search-ready.png)

![GitHub Deep Search real result](docs/assets/real-run-report-cropped.png)

截图信息：

- 日期：2026-06-30
- 查询：`Find an open-source Python terminal UI library that supports tables, progress bars, markdown rendering, and rich text styling.`
- 截图中 Top 结果：`Textualize/rich`
- 报告记录的 LLM token 用量：输入 `38,236`，输出 `3,386`，合计 `41,622`
- 完整记录：[docs/REAL_RUNS.md](docs/REAL_RUNS.md)

## 快速开始

Clone 后进入项目目录，一行命令启动 Web：

```bash
python scripts/start_web.py
```

打开终端输出的地址，通常是 http://127.0.0.1:8001。

启动器会自动创建 `.venv`、安装依赖、在缺失时创建 `config/user_keys.env`，然后启动 Web 服务。

## 必须配置 API Key

真实调研需要 provider 凭证。没有 key 时可以打开界面，但不会得到可信的调研报告。

编辑 `config/user_keys.env`：

```env
GITHUB_TOKEN=your_public_read_token
LLM_API_KEY=your_openai_compatible_key
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=your-model-name
TAVILY_API_KEY=
```

- `GITHUB_TOKEN`：真实使用基本必需。未认证 GitHub 请求额度太低，不适合做可靠召回。
- `LLM_API_KEY`：必需。用于需求解析、查询规划、候选项目比较和最终报告。
- `TAVILY_API_KEY`：可选。用于 Web 交叉验证和补充发现。

建议 GitHub token 只授予公开仓库只读权限，不要授予写权限。

## 预期消耗与成本

每次报告都会展示实际 token 用量，JSON 结果也会返回 usage 字段。美元成本取决于你使用的模型和服务商，可以通过下面配置让项目估算：

```env
LLM_INPUT_USD_PER_1M=0
LLM_OUTPUT_USD_PER_1M=0
TAVILY_USD_PER_CREDIT=0.008
```

默认本地预算：

| 模式 | GitHub 请求上限 | 候选项目上限 | Tavily 上限 | 典型 LLM tokens |
| --- | ---: | ---: | ---: | ---: |
| `standard` | 40 | 30 | 最多 4 credits | 15k-45k |
| `high` | 72 | 54 | 最多 4 credits | 30k-80k |
| `continue` | 92 | 69 | 最多 4 credits | 40k-110k |

Web 默认使用 `detailed + continue`，因为首次体验通常更关心召回质量，而不是最小消耗。

说明：

- GitHub 请求本身不由本项目计费，但受 GitHub rate limit 限制。
- Tavily 是可选项；当前集成使用 basic search，并记录返回的 credit usage。
- LLM 成本公式：`input_tokens / 1,000,000 * LLM_INPUT_USD_PER_1M + output_tokens / 1,000,000 * LLM_OUTPUT_USD_PER_1M`。
- 如果价格字段保持 `0`，报告仍会显示 token 用量，但美元估算不完整。
- 服务商价格和限额会变化，批量运行前请以自己的服务商控制台为准。

参考：

- [GitHub REST API rate limits](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api)
- [Tavily credits and pricing](https://docs.tavily.com/documentation/api-credits)
- [OpenAI API pricing](https://openai.com/api/pricing/)

## 信任边界

这个项目刻意避免任何会让首次体验“看起来比真实系统更好”的东西：

- 不内置 Demo 报告。
- 不内置假仓库、假排行或 seeded result data。
- 不使用静态产品领域同义词表、业务关键词包、仓库白名单或黑名单排序捷径。
- 测试夹具不会被 Web、CLI、MCP server 或搜索引擎运行时加载。
- 每份真实报告都来自当前用户输入、实时 provider 响应、采集到的仓库证据和配置的 LLM。

## 为什么不是直接问 LLM？

直接问 LLM 很快，但常见问题是结果过时、证据不足、容易把“像那么回事”的项目说成可用。手动搜 GitHub 又很慢，而且容易漏掉代码搜索、Topic、Issue 和 README 里的线索。

GitHub Deep Search 做的是中间层：

- LLM 负责理解当前需求并规划搜索角度。
- 程序实际调用 GitHub 多路搜索。
- 程序读取 README、文件路径和关键源码证据。
- 排名优先看证据覆盖，而不是只看 star。
- 最终报告区分“可直接复用”“参考项目”“相邻但不够匹配”。

## CLI

```bash
python -m github_deep_search "找一个可自部署的 AI Agent 可视化工作流编排工具，最好有插件机制"
```

详细模式：

```bash
python -m github_deep_search "your requirement" --mode detailed --format markdown
```

更高搜索预算：

```bash
python -m github_deep_search "your requirement" --budget high --format json
python -m github_deep_search "your requirement" --budget continue --format json
```

## Docker

```bash
docker compose up --build
```

然后打开 http://127.0.0.1:8001。

## 工作流

```text
自然语言需求
=> 结构化 SearchSpec
=> GitHub repo / code / topic / issue 搜索
=> 可选 Tavily Web 发现
=> README、文件树、关键源码证据采集
=> 证据覆盖排序
=> 项目对比报告
```

搜索层不会通过硬编码产品同义词、样例关键词包或项目白名单来“调结果”。产品语义必须来自当前用户输入、生成的 `SearchSpec` 和真实仓库证据。

## Web 体验

- 一行命令启动。
- Header 显示 API key 配置状态。
- 无内置 Demo 报告或预置结果数据。
- 展示解析、搜索、证据采集、分析、报告生成进度。
- 支持复制 Markdown 和下载 JSON。

## 项目状态

这是一个早期开源原型，目标是让产品想法和技术选型阶段的 GitHub 调研更快、更有证据感。核心流程已经可跑，后续会继续围绕召回质量、报告可读性和成本控制迭代。

Roadmap: [docs/ROADMAP.md](docs/ROADMAP.md)

## MCP

安装可选依赖：

```bash
pip install -r requirements-mcp.txt
```

运行：

```bash
python -m github_deep_search.mcp_server
```

MCP tool 名称：`github_deep_search`。

## 测试

```bash
pip install -r requirements.txt
pytest -q
python -m compileall github_deep_search tests
```

Web 渲染回归：

```powershell
pip install -r requirements-e2e.txt
python -m playwright install chromium
pytest -q -m e2e
```

Live eval 默认跳过，需要显式开启：

```powershell
$env:RUN_LIVE_EVAL = "1"
pytest -q -m live
```

## 贡献

欢迎提交真实搜索 miss、复现 query、UX 反馈、Provider 兼容性修复和聚焦的 PR。请先阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。

如果这个项目帮你节省了调研时间，给一个 star 会让更多正在做产品想法验证的人看到它。
