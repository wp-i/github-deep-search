<h1 align="center">GitHub Deep Search</h1>

<p align="center">
  根据一段想法或需求，从公开 GitHub 仓库中找出与完整输入最相关的三个开源项目。
</p>

## 当前状态

项目版本统一为 `0.1.0`，当前只用于开发和测试，不进行发布。

产品契约已经完成重新设计，运行时正在按新契约清理和重建。在新的六阶段流水线全部
完成并通过验证前，仓库中的旧搜索结果不能作为产品质量背书。

## 产品目标

用户可以输入不超过 2000 个字符的自然语言描述，例如“我想要一个……”“我需要……”
“我的设计是……”或对某个现状的描述。系统不向用户追问，而是独立完成：

```text
输入 → 解析 → 发现 → 证据 → 分析 → 报告
```

一次成功执行必须返回三个经过 GitHub 验证、相互独立的公开仓库：

- 优先寻找能够完整覆盖核心功能的项目；
- 没有完整匹配时，降级为覆盖部分需求的项目；
- 再没有时，保留至少覆盖一个重要需求点的相邻项目；
- 无法验证三个有效项目时明确失败，不使用重复、无实现或无证据的仓库凑数。

最终只展示一个 0～100 的关联度分数。分数只在本次运行中用于排序，不跨运行比较。

完整规则见 [产品契约](docs/PRODUCT_CONTRACT.md)。

## 运行边界

- 唯一用户入口是 Web。
- 搜索数据只来自认证 GitHub API；不使用通用网页搜索。
- 需求理解和最终分析使用 OpenAI-compatible Chat Completions。
- `GITHUB_TOKEN` 和 `LLM_API_KEY` 都是必需配置。
- 只搜索公开仓库，不读取或展示私有仓库。
- 每个本地实例同一时间只运行一个任务。
- 用户关闭或刷新页面不会自动取消任务；任务可重新连接、主动取消或等待超时。
- 报告只在网页中显示，不自动保存到磁盘。
- 用户可以主动复制 Markdown 或下载 Markdown；下载完全由浏览器基于当前页面生成。

## 本地启动

```bash
python scripts/start_web.py
```

启动器会准备本地 Python 环境并启动 Web，默认地址为
`http://127.0.0.1:8001`。

在 `config/user_keys.env` 中配置：

```env
GITHUB_TOKEN=your_public_read_token
LLM_API_KEY=your_openai_compatible_key
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=your-model-name
```

建议 GitHub Token 只授予公开仓库的 Metadata Read-only 和 Contents
Read-only 权限。缺少凭据、鉴权失败或限流时，运行明确失败，不降级到匿名 API。

也可以使用 Docker：

```bash
docker compose up --build
```

## 用户会看到什么

Web 会展示真实六阶段进度，不显示虚假百分比。完成后，每个项目包含：

- GitHub 仓库链接和本次关联度分数；
- 与用户需求相关的原因；
- 已确认满足的内容；
- 明确不满足或尚未确认的内容；
- 来自 README、目录、源码或配置的证据；
- 最后代码更新时间、归档状态、许可证和 Release 情况；
- 必要的采用风险。

报告只公开 LLM 输入、输出和总 token，不展示费用估算或内部调试 trace。

## 文档

- [产品契约](docs/PRODUCT_CONTRACT.md)
- [架构](docs/ARCHITECTURE.md)
- [测试标准](docs/TESTING.md)
- [本轮重建设计与清理记录](docs/CHANGE_RECORD_20260817.md)
- [贡献指南](CONTRIBUTING.md)
- [开发与测试约束](AGENTS.md)

## 开发

```bash
pip install -r requirements-dev.txt
pytest -q
python -m compileall github_deep_search tests scripts
```

修改代码前必须先确认测试仍然代表当前产品契约。普通 UI、序列化和内部重构不要求
额外调用独立评审 LLM；涉及解析、发现、证据或排序语义时，确定性测试通过后再执行
最小必要的真实 Provider 验证。

## License

本项目使用仓库根目录中的 [LICENSE](LICENSE)。
