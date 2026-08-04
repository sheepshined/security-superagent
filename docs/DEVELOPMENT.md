# 开发指南

## 环境

- Python 3.11 或 3.12
- Node.js 22
- Docker Desktop + WSL2
- 数据根目录建议 `E:\tzb\security-superagent-data`

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev,ai]"
cd frontend
npm.cmd ci
```

从 `.env.example` 创建 `.env`。不得把 API Key、初始化密码、平台凭据写入源码、测试
快照或命令输出。开发时建议先使用 `MODEL_PROVIDER=mock` 与 `XDR_MODE=mock`。

## 本地运行

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --reload
cd frontend
npm.cmd run dev
```

## 代码边界

- `backend/app/adapters`：外部 XDR、模型和响应协议。
- `backend/app/agents`：LangGraph、Supervisor、专门 Agent 和 Critic。
- `backend/app/rag`：来源、解析、索引与混合检索。
- `backend/app/services`：认证、工作流、动作等领域服务。
- `backend/app/runtime`：异步 Job Worker 和审计事件。
- `frontend/src`：非技术用户控制台。

新增写工具必须先提供描述符，包括输入 Schema、风险、角色、超时、副作用和幂等
策略，并补充状态机与越权测试。不得为 Agent 增加任意 shell、SQL 或通用 HTTP 工具。

## 数据与迁移

数据库在启动时按版本迁移，迁移只能向前且不能删除历史任务。知识源更新先构建新
代际，验证 Manifest、哈希与 Chunk 数后再激活；构建失败不得改写活动索引。
