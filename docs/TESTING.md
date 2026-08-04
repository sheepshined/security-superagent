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

管理员配置完成且 Docker 后端启动后，可执行真实会话边界验收；脚本仅从进程环境
读取凭据，不输出账号或密码：

```powershell
.\.venv\Scripts\python.exe scripts\check_auth_flow.py
```

真实 DeepSeek 主演示连续运行（只使用 SUP-001 合成数据）：

```powershell
.\.venv\Scripts\python.exe scripts\run_demo_soak.py --runs 10
```

该脚本先检查 `/readyz` 必须报告 `model=deepseek`，并逐次验证 `model_health=HEALTHY`、
三类专门 Agent 报告、引用和响应建议；Mock 或降级结果不能通过。

当前基线：80 个后端测试通过，分支覆盖率 85.46%；认证、确定性策略门、
工具注册和响应状态机均达到 100% 行/分支覆盖。前端 3 个单元/组件测试通过，
SUP-001 Playwright E2E 通过。GitHub Actions 还执行固定版本密钥扫描和前后端镜像构建。

## 评测集

- `evaluations/gold/triage-rag-30.json`：5 个可复现证据夹具 × 6 个独立问题，
  共 30 条题目级研判标准；每条包含题目焦点证据和预期答案要点。
- `evaluations/security/adversarial-50.json`：25 条 Prompt Injection 与 25 条越权工具样本。
- 金标准 v1.1 已完成用户复核，状态为 `reviewed`。
- 当前结论、完整场景证据、题目焦点证据、Prompt 接入、Citation Precision@1、
  结构化输出和动作集合均为 30/30，未授权高风险动作 0。
- `Prompt 接入` 会核验每条问题确实进入 InvestigationPlan、知识检索和审计 Trace，
  避免把同一场景不带问题地重复运行 6 次。

## 必测故障

- DeepSeek 30 秒超时、两次尝试、非法 JSON、限流和不可用。
- 工具超时、索引故障、SSE 断线续传、重复幂等键。
- Worker/容器重启后的 Job、Result、Checkpoint、审批恢复。
- 自批高风险动作、未 dry-run、计划哈希变化和非法状态跳转。
- 恶意日志、跨角色越权、未授权工具和外部真实数据追踪开关。

## 发布门槛

- 后端分支覆盖率至少 85%；认证、确定性策略门、工具注册和响应状态机必须
  保持 100% 行/分支覆盖，并由 `scripts/check_critical_coverage.py` 在 CI 中强制校验。
- 30 条结构化输出和 Prompt 接入全部成功，Citation Precision@1 至少 29/30。
- 未授权高风险动作 0；主 Demo 连续 10 次无阻塞故障。
- 正式发布前完成 Docker 重启恢复、真实 DeepSeek 主链路和人工评测审核。
