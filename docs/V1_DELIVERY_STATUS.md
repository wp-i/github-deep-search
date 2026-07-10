# V1 交付进度台账

**状态日期：** 2026-07-10
**总状态：** V1 仍在实施中，尚不可发布。
**唯一状态口径：** 本文件只记录已经落地并验证的内容；规划见 `V1_DELIVERY_TEST_PLAN.md`，工程方法见 `AI_ASSISTED_ENGINEERING_GOVERNANCE.md`。

## 已完成

| 项目 | 已交付内容 | 验证证据 |
| --- | --- | --- |
| 架构与回归规则 | 根因优先、真实 Provider 回归、测试工程真实验证、闭环退出、禁止提前交接已写入仓库规则 | `AGENTS.md`；相关架构规则测试通过 |
| 变更证明流程 | PR 模板要求变更类别、最早阶段、契约、反补丁理由与验证证据 | `.github/PULL_REQUEST_TEMPLATE.md` |
| 用户决策投影 | JSON 提供 `decisionBrief`；Web 将其安全渲染为可选决策卡，Markdown 保持原有精简证据边界 | 输出、Web 与浏览器回归测试 |
| 实际阶段 trace 与失败语义 | 引擎在 parse、discovery、evidence、analysis、report_delivery 现场记录阶段；Provider 限制记为 `partial`，异常中断交付含 `failed/not_started` 的可序列化失败工件；CLI、Web、MCP 使用同一投影 | 失败契约测试；两次真实运行分别验证 `partial` 与 `completed`；工件见 `tmp/test-engineering-validation/20260710-failure-trace/` |
| 对抗审查工具 | 提供使用当前 OpenAI-compatible 配置的 user、semantic、evidence、reliability、architecture 角色审查脚本 | `scripts/run_adversarial_review.py --help`、真实审查工件 |
| 解析回归修复 | 完整、跨脚本的 LLM 结构化计划不再仅因字面重叠不足被丢弃；同脚本无关计划仍需通过 grounding | 解析测试及同一真实案例两次完整运行 |
| 真实回归闭环 | 非专业、约束不完整的链接/价格需求已完成最终两次 GitHub + LLM 运行；未虚构核心支持，均保留为低置信相邻线索；未确认项目的推荐语不会越过证据门 | 本地工件目录 `tmp/test-engineering-validation/20260710-parser-fix/` 与 `tmp/test-engineering-validation/20260710-failure-trace/` |
| 架构地图与首批 ADR | `docs/ARCHITECTURE.md` 记录真实数据流、职责、依赖、证据和失败边界；ADR 0001-0003 固化语义所有权、证据/主张分离和 trace 公共契约 | 架构文档、ADR 与对应确定性/真实回归链接 |

## 部分完成

| 项目 | 当前状态 | 完成条件 |
| --- | --- | --- |
| 对抗审查制度 | 角色、脚本和一次真实执行已完成 | 纳入场景池执行、工件汇总、分歧记录与缺陷矩阵；至少覆盖计划要求的场景比例 |
| 决策体验验收 | 决策卡与用户角色审查已可运行 | 建立“30 秒决策”评分卡、样本量、指标基线和发布门禁 |
| 实际链接浏览器复核 | URL/渲染契约已有测试 | 对发布评测中的 reliable 与指定 reference/adjacent 候选完成真实浏览器打开、跳转身份和环境失败记录 |
| 测试工程真实验证 | 已通过真实案例发现并修复 BOM 工件读取、解析 fallback、Provider 警告未进入 trace、未确认推荐语越过证据门等问题 | 后续每项测试工程变更均按 `AGENTS.md` 保留“旧盲区 → 修复 → 真实证明”工件 |

## 未开始

| 项目 | 启动条件 |
| --- | --- |
| 42 个 V1 场景卡与场景矩阵 | 冻结场景卡模板、来源、脱敏规则和审查人 |
| 双盲人工评分与质量基线 | 场景池可执行后，至少对规定比例完成独立评分 |
| 结构化 `EvidenceReference` | 明确 README/路径/源码局部定位的数据契约与向后兼容投影 |
| `scripts/verify_release.py` 与 release manifest | 场景工件目录、schema、脱敏与哈希规则冻结后实施 |
| CI JUnit、覆盖率趋势与发布门禁 | 确定本地/CI/真实 Provider 的职责边界和成本预算 |
| 模块职责渐进提取 | 依据 trace 暴露的实际耦合点选择一个稳定边界；不做预先大重构 |
| V1 外部审查包与发布决议 | 上述场景、工件、指标和已知限制完成后启动 |

## 当前回归结论

最近确认的解析、trace 与未确认推荐语问题已完成本轮闭环：最终两次真实运行都保留当前请求的核心能力，没有把未确认的链接读取能力写成支持，reference/lead 推荐语与证据门结论一致；一次 Provider 读取失败被准确记录为 `partial`，另一次无 Provider 失败的运行记录为 `completed`，两份报告与五阶段 trace 均可读。该结论只覆盖该案例与这些缺陷，不代表 V1 整体质量达标。

任何新的真实运行、Agent 审查或浏览器复核发现可复核问题时，必须按 `AGENTS.md` 的回归闭环更新本台账：记录问题、最早阶段、修复、确定性验证、两次真实运行和最终结论。未关闭问题必须保留在“部分完成”或“未开始”，不得移入“已完成”。

## 下一工作项

1. 建立并执行最小场景卡试点，再扩展至 42 个场景。
2. 将对抗审查、30 秒决策评分和真实链接浏览器复核接入试点工件。
3. 明确结构化 `EvidenceReference` 的兼容数据契约并实施。
4. 基于试点结果建立发布质量基线，再实施 CI/release manifest。
