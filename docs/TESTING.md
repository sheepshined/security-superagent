# 测试与验收

## 自动化门禁

```powershell
.\.venv\Scripts\ruff.exe check backend tests scripts
.\.venv\Scripts\python.exe -m pytest --cov --cov-report=term-missing
.\.venv\Scripts\python.exe -m app.evaluation --summary-only
cd frontend
npm.cmd run typecheck
npm.cmd test
npm.cmd run build
npm.cmd run e2e
```

真实 DeepSeek 主演示连续运行（只使用 SUP-001 合成数据）：

```powershell
.\.venv\Scripts\python.exe scripts\run_demo_soak.py --runs 10
```

该脚本先检查 `/readyz` 必须报告 `model=deepseek`，并逐次验证 `model_health=HEALTHY`、
三类专门 Agent 报告、引用和响应建议；Mock 或降级结果不能通过。

当前基线：64 个后端测试通过，覆盖率 87.11%，前端 3 个单元/组件测试通过，
SUP-001 Playwright E2E 通过。GitHub Actions 还执行固定版本密钥扫描和前后端镜像构建。

## 评测集

- `evaluations/gold/triage-rag-30.json`：30 条研判、证据、引用和动作预期。
- `evaluations/security/adversarial-50.json`：25 条 Prompt Injection 与 25 条越权工具样本。
- 当前自动一致性结果为 30/30，Citation Precision 30/30，结构化输出 30/30，
  未授权高风险动作 0。
- 金标准状态是 `pending_user_review`；用户审核完成前不得标记为人工验收完成。

## 必测故障

- DeepSeek 30 秒超时、两次尝试、非法 JSON、限流和不可用。
- 工具超时、索引故障、SSE 断线续传、重复幂等键。
- Worker/容器重启后的 Job、Result、Checkpoint、审批恢复。
- 自批高风险动作、未 dry-run、计划哈希变化和非法状态跳转。
- 恶意日志、跨角色越权、未授权工具和外部真实数据追踪开关。

## 发布门槛

- 后端总覆盖率至少 85%；认证、策略门、工具校验和响应状态机持续补强。
- 30 条结构化输出全部成功，Citation Precision 至少 29/30。
- 未授权高风险动作 0；主 Demo 连续 10 次无阻塞故障。
- 正式发布前完成 Docker 重启恢复、真实 DeepSeek 主链路和人工评测审核。
