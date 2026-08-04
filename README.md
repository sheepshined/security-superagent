# Security SuperAgent

> Agent 运行时已升级为 LangGraph 状态图，使用 LangChain 完成受控知识工具调用和
> DeepSeek 结构化输出解析，并支持默认关闭、按数据分级启用的 LangSmith 追踪。
> 详细设计见 [docs/AGENT_RUNTIME.md](docs/AGENT_RUNTIME.md)。
> LangGraph 检查点已持久化到本地 SQLite，可通过 `trace_id` 在控制台和 API 中回放。

基于深信服 AI 安全大模型平台设计的告警研判 Agent、RAG 知识增强与 SuperAgent 跨域调查系统。

当前阶段采用 Mock XDR 与合成数据开发，后续通过 Adapter 对接平台云服务。DeepSeek 仅处理合成或脱敏数据。

## 当前里程碑

- P1 工程基线：SQLite 版本迁移、本地 RBAC、显式幂等键、异步 Worker、SSE、
  真实 Plan-Execute-Replan 和模型故障安全降级已经实现。
- P2 知识库：MITRE ATT&CK、CWE Top 25、CISA KEV、Sigma 与中国官方法规来源
  已登记；支持下载校验、解析、分块去重、BM25 + ONNX 向量 + RRF、Chroma
  代际索引和原子切换。离线快照始终作为安全回退。
- 当前继续实施受控响应、模板化工作流、标准 SuperAgent、MCP 和 Skill。

详细计划见 [docs/PROJECT_PLAN.md](docs/PROJECT_PLAN.md)。
开发机、Docker E 盘规划和平台边界见 [docs/ENVIRONMENT.md](docs/ENVIRONMENT.md)。

## 目录

```text
backend/       FastAPI、Agent、RAG、工具和平台适配
frontend/      React + TypeScript + Ant Design 控制台
knowledge/     可离线演示的知识快照和构建说明
tests/         单元、契约、端到端与评测
deploy/        Docker Compose 与部署配置
docs/          项目、开发、测试和演示文档
scripts/       Windows 本地开发脚本
```

## 安全原则

- 不提交 `.env` 或任何 API Key。
- DeepSeek 不接收未经批准的真实 XDR 数据。
- 高风险写动作必须经过 dry-run、策略校验和审批。
- Agent 不能调用任意 shell、SQL 或通用 HTTP 工具。

## DeepSeek 连通性检查

复制 `.env.example` 为本地 `.env`，只在本机填写 `DEEPSEEK_API_KEY`，然后执行：

```powershell
.\scripts\check-deepseek.ps1
```

该检查只发送合成测试文本，不会读取或发送 XDR 数据。

## 知识索引

完整索引使用独立 Compose profile 构建，不占用主演示后端的构建窗口：

```powershell
docker compose -f deploy/compose.yaml --profile indexer run --rm knowledge-indexer
```

索引器先在新代际目录完成下载校验、解析和 Chroma 构建，成功后才原子切换
`ACTIVE` 指针。任何失败代际都不会替换当前可用索引。
