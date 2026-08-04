# 安全设计

## 身份和授权

- 首次启动由本地环境变量初始化管理员，密码使用 Argon2id 哈希。
- 管理员、分析员、审批员按最小权限分工。
- 8 小时 HttpOnly 会话 Cookie；状态变更接口强制 CSRF。
- 分析员不能批准自己发起的高风险动作，审批必须形成独立审计事件。

## 模型和数据

- DeepSeek/LangSmith 默认只处理合成或批准脱敏数据。
- 真实 XDR 数据的外部模型与 Trace 开关默认关闭。
- Prompt、日志和证据均视为不可信数据，不能改变工具 allowlist 或角色权限。
- 模型故障后输出 `DEGRADED/needs_review`，禁止自动响应。

## 工具和响应

- Tool Registry 拒绝未注册工具和不符合 Schema 的参数。
- 高风险动作路径：`PROPOSED → DRY_RUN_PASSED → PENDING_APPROVAL → APPROVED →`
  `EXECUTING → SUCCEEDED → VERIFIED`；同时支持拒绝、失败、回滚和过期。
- dry-run 生成不可变计划哈希，执行前重新校验，防止审批后参数替换。
- 当前 Response Adapter 只模拟副作用；平台写接口到达后必须通过契约测试。

## 供应链和密钥

- `.env`、数据库、模型、索引和缓存在 `.gitignore` 中排除。
- 提交和 CI 使用固定 TruffleHog 版本检查可验证密钥。
- GitHub Actions 使用最小 `contents: read` 权限，凭据不持久化到 checkout。
- 日志、Trace、镜像和 Git 均不得包含 API Key、会话令牌或密码。

## 已知边界

- 当前 XDR/Response 主演示是明确标注的 Mock 合成环境。
- 平台资料未到达，真实鉴权、分页、限流、错误码、Webhook 和数据分类映射待实现。
- Docker Desktop 总内存仍需由用户调至 6GB；索引器与主演示必须错峰。
