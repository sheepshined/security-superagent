# API 说明

所有业务接口位于 `/api/v1`。启用认证时，登录成功后使用 8 小时 HttpOnly 会话
Cookie；所有变更请求还必须携带会话返回的 CSRF 值。OpenAPI 位于 `/docs`。

| 领域 | 方法与路径 | 说明 |
|---|---|---|
| 健康 | `GET /healthz`、`GET /readyz` | 存活和依赖就绪状态 |
| 认证 | `POST /api/v1/auth/login`、`logout`、`GET me` | 本地会话 |
| 用户 | `GET/POST/PATCH /api/v1/admin/users` | 仅管理员 |
| 场景 | `GET /api/v1/scenarios` | 合成演示场景 |
| 任务 | `POST /api/v1/triage/jobs` | 异步提交，支持 `Idempotency-Key` |
| 重跑 | `POST /api/v1/jobs/{job_id}/rerun` | 保存父任务与新分析版本 |
| 查询 | `GET /api/v1/jobs/{job_id}` | 任务状态 |
| 结果 | `GET /api/v1/jobs/{job_id}/result` | 结构化研判结果 |
| 事件 | `GET /api/v1/jobs/{job_id}/events` | 审计事件列表 |
| SSE | `GET /api/v1/jobs/{job_id}/stream` | 节点、Agent、工具、审批和终态事件 |
| Trace | `GET /api/v1/traces/{trace_id}` | Checkpoint 回放 |
| 知识 | `GET /api/v1/knowledge/search` | 混合检索 |
| 索引 | `GET /api/v1/knowledge/indexes/sources/metrics` | 索引治理 |
| 激活 | `POST /api/v1/knowledge/indexes/{id}/activate` | 管理员原子切换 |
| 工具 | `GET /api/v1/tools` | 描述符注册表 |
| 工作流 | `GET /api/v1/workflows/templates/versions` | 固定拓扑与版本 |
| 工作流 | `POST .../{template_id}/versions` | 管理员创建不可变版本 |
| 工作流 | `POST .../versions/{id}/validate/activate` | 校验与激活 |
| 动作 | `POST/GET /api/v1/actions` | 提议和查询响应动作 |
| 动作 | `POST /api/v1/actions/{id}/dry-run` | 生成不可变计划哈希 |
| 动作 | `POST .../{id}/approve/reject` | 四眼审批 |
| 动作 | `POST .../{id}/execute/verify/rollback` | 执行、回查和回滚 |

任务终态包括 `SUCCEEDED`、`DEGRADED`、`FAILED`、`REJECTED`、`EXPIRED` 和
`CANCELLED`。模型不可用、非法 JSON 或超时时不伪造实时结果，任务进入安全降级。

MCP 使用 Streamable HTTP，默认地址为 `http://127.0.0.1:8100/mcp`，只提供：

- `knowledge_search`
- `alert_context`（只读取明确标记的合成告警）
- `trace_summary`（最小化摘要）
