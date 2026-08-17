# 贡献指南

GitHub Deep Search 当前处于 `0.1.0` 开发阶段。贡献的第一标准不是兼容旧实现，而是
符合已经确认的产品契约。

开始修改前请完整阅读：

- [产品契约](docs/PRODUCT_CONTRACT.md)
- [架构](docs/ARCHITECTURE.md)
- [测试标准](docs/TESTING.md)
- [开发与测试约束](AGENTS.md)

## 本地环境

```bash
python scripts/start_web.py
```

真实运行需要在 `config/user_keys.env` 中设置：

```env
GITHUB_TOKEN=
LLM_API_KEY=
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=
```

GitHub Token 应只拥有公开仓库 Metadata 和 Contents 的读取权限。运行时不得读取私有
仓库，也不得在认证失败后改用匿名 GitHub API。

## 变更流程

1. 确认请求影响的产品契约和所属阶段。
2. 找到当前行为的唯一所有者，列出会被新实现取代的旧逻辑。
3. 先更新契约或数据结构，再实现最小范围变化。
4. 删除被取代的 helper、分支、测试和文档，不叠加补偿逻辑。
5. 先运行确定性测试；语义核心变化再做最小真实 Provider 验证。
6. 检查日志、错误和测试产物中没有凭据或用户敏感数据。

不允许通过静态业务关键词、翻译表、仓库名单、样例分支或测试专用逻辑改善某一个
输入的表现。产品含义必须来自当前输入、当前 LLM 和当前 GitHub 仓库证据。

## 测试

基础检查：

```bash
pip install -r requirements-dev.txt
pytest -q
python -m compileall github_deep_search tests scripts
```

涉及 Web 渲染时再运行浏览器测试。涉及解析、发现、证据或排序时，只有在相关确定性
测试通过后才能调用真实 Provider。首次真实失败后停止后续调用，先定位所属阶段。

真实运行的报告和 trace 只用于当次检查，不提交到仓库，也不形成长期评估目录。

## 提交范围

- 保持改动聚焦于一个明确责任边界。
- 行为变化必须包含符合当前契约的回归测试。
- Web 变化需要验证真实阶段、取消、错误和报告交付状态。
- 不提交 API Key、Token、虚拟环境、缓存、临时报告或调试 trace。
- 不新增 CLI、MCP、Tavily 或其他未进入产品契约的入口和 Provider。
