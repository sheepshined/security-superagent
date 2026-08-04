# Security SuperAgent

面向安全运营告警研判的可审计智能体系统。项目以 FastAPI、LangGraph、LangChain、
DeepSeek、SQLite、Chroma、React 和 MCP 为技术基线，当前在 Mock XDR 合成数据上
完成基础任务、进阶任务和标准 SuperAgent 主链路，平台资料到达后通过 Adapter 接入。

## 当前成果

- 异步任务、SSE 实时进度、持久化 Checkpoint、Trace 回放和显式幂等。
- Supervisor 并行调度 Network、Endpoint、Identity 三个专门 Agent，共享 RAG 能力。
- Plan-Execute-Critic-Replan 混合流程；最多一次真实修订，保留证据冲突和未知项。
- MITRE ATT&CK、CWE Top 25、CISA KEV、Sigma 和中国官方法规混合检索。
- BM25 + 多语种 ONNX 向量 + RRF + 规则重排 + 引用完整性校验。
- 描述符 Tool Registry、固定安全拓扑工作流模板和不可变工作流版本。
- 高风险响应必须 dry-run、不可变计划哈希、四眼审批、模拟执行和执行回查。
- 本地 RBAC：管理员、分析员、审批员，Argon2id、HttpOnly Cookie 和 CSRF。
- 只读 Streamable HTTP MCP：知识搜索、合成告警上下文、最小化 Trace 摘要。
- `lateral-movement-investigation` Skill v1 与 SUP-001/SUP-002 演示场景。
- 30 条研判/RAG 金标准草案、50 条 Prompt Injection/越权安全样本。

## 一键启动

1. 从 `.env.example` 复制本地 `.env`，至少设置：

   - `BOOTSTRAP_ADMIN_USERNAME`
   - `BOOTSTRAP_ADMIN_PASSWORD`（至少 12 位，不进入 Git）
   - `DEEPSEEK_API_KEY`

2. 启动服务：

```powershell
docker compose -f deploy/compose.yaml up -d --build
```

3. 打开 `http://localhost:5173`。后端就绪状态位于
   `http://localhost:8000/readyz`，MCP 只绑定本机 `127.0.0.1:8100`。

完整索引由独立 profile 构建，成功后才原子切换活动版本：

```powershell
docker compose -f deploy/compose.yaml --profile indexer run --rm knowledge-indexer
```

## 验证

```powershell
.\.venv\Scripts\ruff.exe check backend tests scripts
.\.venv\Scripts\python.exe -m pytest --cov --cov-report=term-missing
.\.venv\Scripts\python.exe -m app.evaluation --summary-only
.\.venv\Scripts\python.exe scripts\check_mcp.py http://127.0.0.1:8100/mcp
cd frontend
npm.cmd run typecheck
npm.cmd test
npm.cmd run build
npm.cmd run e2e
```

当前本地门禁：后端 78 项通过，分支覆盖率 85.48%，认证、确定性策略门、
工具注册和响应状态机均为 100% 行/分支覆盖；前端 3 项单元/组件测试和
SUP-001 浏览器 E2E 通过；离线评测 30/30，未授权高风险动作 0。30 条金标准仍需
用户人工审核，不能将自动一致性结果等同于人工标注完成。

## 安全边界

- DeepSeek 和 LangSmith 只接收合成或已批准脱敏的数据。
- 模型失败时继续确定性研判并标记 `DEGRADED`，最高只能 `needs_review`。
- Agent 无任意 shell、SQL 或通用 HTTP 工具；MCP 不暴露写操作。
- `.env`、数据库、模型、完整索引、缓存和运行日志不进入 Git。
- Platform Adapter 未通过契约测试前，Mock 演示保持为稳定主链路。

## 文档

- [架构设计](docs/ARCHITECTURE.md)
- [API 说明](docs/API.md)
- [开发指南](docs/DEVELOPMENT.md)
- [测试与验收](docs/TESTING.md)
- [Docker 部署](docs/DEPLOYMENT.md)
- [安全设计](docs/SECURITY.md)
- [演示脚本](docs/DEMO.md)
- [Agent 运行时](docs/AGENT_RUNTIME.md)
- [完整项目计划](docs/PROJECT_PLAN.md)

## 当前外部依赖

- 本地管理员已初始化，真实登录、Cookie、CSRF、授权和会话撤销验收通过。
- Docker Desktop 按用户确认采用约 4GB；完整索引与主演示已验证，索引构建须错峰。
- XDR 平台鉴权、字段、分页、限流、Webhook 和响应接口资料尚未提供。
- 30 条评测样本需要用户确认，正式现场演示前需要确认安全结论表述。
