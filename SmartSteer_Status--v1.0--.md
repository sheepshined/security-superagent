# SmartSteer / Security SuperAgent 项目交接状态

> 交接日期：2026-08-10（Asia/Shanghai）  
> 项目目录：`E:\tzb\security-superagent`  
> 项目目标：面向安全运营告警研判，完成基础任务、进阶任务和标准 SuperAgent，强调证据优先、可追溯、可约束和安全降级。  
> 本文不包含任何 API Key、密码、Cookie 或会话令牌。

## 0. 一句话状态

项目已经形成可运行的全栈演示版本：React 控制台、FastAPI API、LangGraph 混合调查图、
Supervisor 与 Network/Endpoint/Identity 专门 Agent、混合 RAG、受控响应、RBAC、SSE、
Trace、MCP、Skill、Docker 和自动化测试均已实现。稳定主链路使用 Mock XDR 合成数据，
实时推理使用 DeepSeek。**深信服 XDR 云平台（XDR 2.0.20）已接入并完成真实数据联调**：
AKSK 联动码签名认证、alerts/incidents 双数据源、告警/举证字段映射均已通过真实平台验证，
Platform Adapter 与契约测试已落地；默认 `XDR_MODE=mock`，切换 `platform` 后走真实数据。

当前代码位于 `main`，基线提交为 `54fbc28f943e707c28fb8fe005c6e3c5d294b8d9`，
标签为 `milestone-gold-v1.1-20260804`。在创建本文之前，工作树与 `origin/main` 同步且无其他改动。

## 1. 已完成内容

### 1.1 工程与交付基线

- 建立 Python 3.11/3.12、FastAPI、Pydantic、LangGraph、LangChain、SQLite、Chroma、
  FastEmbed ONNX、React、TypeScript、Ant Design、MCP 的完整工程。
- 建立私有 GitHub 仓库：`https://github.com/sheepshined/security-superagent`。
- GitHub Actions 已包含：TruffleHog 密钥扫描、Ruff、pytest/覆盖率、关键安全覆盖率、
  30 条发布门槛、前端类型检查、前端单测、Playwright E2E、前后端 Docker 构建。
- 最新远程 CI：`https://github.com/sheepshined/security-superagent/actions/runs/30908580700`，
  对提交 `54fbc28` 的结论为 `success`。
- `.env`、SQLite、Chroma、ONNX 模型、缓存、运行日志和输出目录均不进入 Git。
- 已生成可迁移 Git Bundle、发布候选 ZIP、SHA256 文件和答辩 PPT。
- 用户已明确取消“录制 3 分钟和 90 秒演示视频”；现场演示提词仍保留，不要求视频文件。

### 1.2 基础告警研判闭环

- 支持创建异步 Job、查询 Job/Result、显式 `Idempotency-Key`、同告警多分析版本和重新分析。
- Worker 使用 LangGraph 异步执行；任务、结果、审计事件和 Checkpoint 均落 SQLite。
- SSE 推送节点、Agent、工具、审批、进度、错误和终态；支持断线续传。
- DeepSeek 输出通过 Pydantic 结构化解析；模型失败后继续确定性研判。
- 安全降级规则已经实现：模型异常时标记 `DEGRADED`，最终最高只能为
  `needs_review`，禁止自动执行响应。
- 当前预置场景：
  - `TRI-001`：横向移动真阳性；
  - `TRI-002`：批准扫描误报；
  - `TRI-003`：端点证据缺失、待确认；
  - `SUP-001`：跨网络/端点/身份横向移动攻击链；
  - `SUP-002`：网络证据与授权变更证据冲突。

### 1.3 Agent 与 SuperAgent

- 主流程为 `Plan–Execute–Verify–Replan + Bounded ReAct`，不是无限循环式 ReAct。
- Supervisor 生成结构化调查计划，并行调度 Network、Endpoint、Identity 三个专门 Agent；
  知识检索作为共享能力。
- Specialist Agent 结果统一合并为 Evidence、AgentReport、Unknown 和 Conflict。
- Critic 检查证据覆盖、冲突、引用、预算；关键证据不足时最多生成一次真实修订计划。
- 前端展示计划、Agent 报告、工具、Observation、冲突、Critic 和最终裁决；
  不展示模型隐藏思维链。
- “本次研判目标”已经真正进入调查计划、专门 Agent 目标、RAG 查询、DeepSeek Prompt
  和审计 Trace，修复了早期“问题只记录但没有送入 Agent”的缺陷。

### 1.4 RAG 与本地知识库

- 已实现知识源登记、下载、许可/哈希记录、解析、分块、去重、删除传播、版本化索引和
  ACTIVE 指针原子切换。
- 检索链固定为：访问标签过滤 → BM25 与向量并行召回 → RRF → 技术编号/来源/时效/
  场景重排 → 去重 → 引用完整性校验。
- 向量模型：`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`，使用 FastEmbed ONNX。
- Chroma 使用本地 `PersistentClient`，不增加独立向量数据库服务。
- 当前 E 盘活动索引：
  - 索引 ID：`idx-20260804073548-9e7477f5`；
  - 状态：`ready`；
  - Chunk：24,238；
  - 文档：23,659；
  - Chroma：已启用；
  - 数据源：MITRE ATT&CK Enterprise、CWE Top 25、CISA KEV、横向移动 Sigma、
    中国网络安全法/数据安全法/个人信息保护法/网络数据安全管理条例/
    关键信息基础设施安全保护条例和合成调查手册。
- 活动索引、模型、缓存和运行数据实际存放在 `E:\tzb\security-superagent-data`，
  当前约 1.20 GB；不进入 Git。

### 1.5 认证、受控响应和低代码

- 本地 RBAC 已实现管理员、分析员、审批员三种角色。
- 密码使用 Argon2id；会话使用 HttpOnly Cookie；状态变更接口强制 CSRF。
- 高风险动作状态机已经实现：
  `PROPOSED → DRY_RUN_PASSED → PENDING_APPROVAL → APPROVED/REJECTED →`
  `EXECUTING → SUCCEEDED/FAILED → VERIFIED/ROLLED_BACK/EXPIRED`。
- 高风险动作必须经过 dry-run、不可变计划哈希、独立审批、Mock Response Adapter 和回查。
- 分析员不能批准自己发起的高风险动作。
- 工作流采用固定安全拓扑，Critic、确定性策略裁决和响应安全门不能关闭。
- 支持不可变工作流版本、节点启停、模型、超时、重试、预算、Agent、工具 allowlist 和
  知识范围配置。

### 1.6 前端、MCP 和 Skill

- React 控制台包含登录、运营总览、任务、工作流、知识中心、审批中心和 Trace 页面。
- 可创建研判、填写研判目标、观看 SSE 进度、查看证据/引用/Agent 报告、审批模拟响应和
  回放安全裁剪后的 Trace。
- MCP Gateway 使用 Streamable HTTP，只暴露三个只读能力：知识搜索、合成告警上下文、
  最小化 Trace 摘要；核心 Agent 不依赖 MCP 才能运行。
- 已实现 `lateral-movement-investigation` Skill v1，包含工作流、Prompt、允许工具、
  知识范围、Schema、场景和评测集绑定。

### 1.7 评测与验证

- 金标准 v1.1：5 个可复现证据场景 × 每场景 6 个独立问题，共 30 条题目级标准。
- 已修订并复核四项关键证据：
  - TRI-001 增加变更证据；
  - TRI-002 增加网络流量证据；
  - SUP-001 增加变更证据；
  - SUP-002 增加资产证据。
- 评测不再采用宽松的“任意引用命中”，而是检查案例完整证据、题目焦点证据、
  Prompt 接入、Citation Precision@1、结构化输出、动作集合和越权动作。
- 2026-08-10 本地复验：
  - Ruff：通过；
  - 后端：80/80；
  - 总分支覆盖率：85.46%；
  - 认证、确定性策略门、工具注册、响应状态机关键安全覆盖率：100% 行/分支；
  - 金标准：30/30；
  - Citation Precision@1：30/30；
  - 未授权高风险动作：0；
  - 前端 TypeScript 类型检查：通过；
  - 前端单元/组件测试：3/3；
  - 前端生产构建：通过。
- Playwright SUP-001 E2E、真实管理员会话边界、Docker 构建和容器健康检查曾在
  提交 `54fbc28` 上通过；本次交接时 Docker Desktop 未运行，因此未再次执行容器 E2E。

## 2. 当前代码结构

```text
security-superagent/
├─ backend/app/                  Python 后端
│  ├─ main.py                    FastAPI 入口
│  ├─ core/config.py             环境配置及安全默认值
│  ├─ domain/                    Job、Evidence、Result、Action、Workflow 等领域模型
│  ├─ api/                       /api/v1 路由和依赖注入
│  ├─ adapters/                  XDR、Model、Response 协议及 Mock/DeepSeek 适配器
│  │  ├─ aksk.py                 AKSK HMAC-SHA256 签名器（联动码解码 + 签名）
│  │  ├─ platform_xdr.py         PlatformXDRAdapter（alerts/incidents 双数据源）
│  │  ├─ platform_response.py    PlatformResponseAdapter（封禁/隔离/账号动作映射）
│  │  └─ xdr_schemas.py          平台响应模型与字段映射（告警/举证→领域模型）
│  ├─ agents/
│  │  ├─ supervisor.py           Supervisor 与专门 Agent 调度
│  │  ├─ triage/graph.py         主 LangGraph 调查图
│  │  ├─ triage/planning.py      调查计划、Critic 与 Replan
│  │  ├─ triage/react.py         有界 ReAct 子图
│  │  ├─ triage/structured.py    DeepSeek 结构化研判
│  │  └─ approval/graph.py       人工审批 interrupt/resume 图
│  ├─ rag/                       下载、解析、索引、Chroma、混合检索和目录
│  ├─ persistence/               SQLite 连接、迁移和 LangGraph Checkpoint
│  ├─ runtime/jobs.py            Worker、任务状态、超时和 SSE 事件
│  ├─ services/                  认证、风险、研判、工作流、响应动作状态机
│  ├─ tools/registry.py          工具描述符、Schema、风险、角色、超时和副作用
│  ├─ observability/langsmith.py LangSmith 数据门禁与 Trace 配置
│  ├─ mcp_server.py              只读 MCP Gateway
│  ├─ evaluation.py              30 条离线发布门槛评测器
│  └─ data/scenarios/            TRI/SUP 合成场景
├─ frontend/                     React + TypeScript + Vite + Ant Design
│  ├─ src/App.tsx                主研判控制台
│  ├─ src/OperationsPages.tsx    总览、审批、知识、工作流等运营页面
│  ├─ src/api.ts                 API、SSE、CSRF 和幂等请求封装
│  ├─ src/types.ts               前端领域类型
│  ├─ e2e/superagent.spec.ts     Playwright 主场景测试
│  └─ package.json               前端依赖与测试/构建命令
├─ knowledge/
│  ├─ sources.json               知识源注册表
│  └─ snapshots/                 仓库内小型离线回退快照
├─ evaluations/
│  ├─ gold/triage-rag-30.json    30 条题目级金标准 v1.1
│  └─ security/adversarial-50.json 50 条 Prompt Injection/越权样本
├─ skills/lateral-movement-investigation/  横向移动 Skill v1
├─ deploy/                       Compose、Dockerfile、Nginx
├─ scripts/                      启动、健康检查、索引验证、Soak、打包脚本
├─ tests/                        API 与单元测试
├─ docs/                         架构、API、测试、安全、部署、计划和演示文档
├─ deliverables/                 答辩 PPT
├─ outputs/                      本地审核表等输出；被 Git 忽略
├─ .env                          本机密钥与运行配置；被 Git 忽略
├─ .env.example                  安全配置模板
├─ pyproject.toml                Python 依赖和质量门禁
└─ README.md                     项目入口文档
```

关键阅读顺序：

1. `README.md`
2. 本文 `SmartSteer_Status.md`
3. `docs/ARCHITECTURE.md`
4. `docs/AGENT_RUNTIME.md`
5. `docs/SECURITY.md`
6. `docs/TESTING.md`
7. `backend/app/agents/triage/graph.py`
8. `backend/app/agents/supervisor.py`

## 3. 关键参数与当前有效配置

### 3.1 本机 `.env` 状态

以下只记录状态，不记录密钥值：

| 配置 | 当前状态/有效值 |
|---|---|
| `APP_ENV` | `development` |
| `APP_HOST` / `APP_PORT` | `127.0.0.1:8000` |
| `MODEL_PROVIDER` | `deepseek` |
| `DEEPSEEK_API_KEY` | 已配置，值未记录 |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` |
| `DEEPSEEK_MODEL` | `deepseek-v4-flash` |
| `ALLOW_EXTERNAL_MODEL_REAL_DATA` | `false` |
| `LANGSMITH_TRACING` | `true` |
| `LANGSMITH_API_KEY` | 已配置，值未记录 |
| `LANGSMITH_PROJECT` | `security-superagent-dev` |
| `LANGSMITH_ENDPOINT` | `https://api.smith.langchain.com` |
| `LANGSMITH_ALLOW_REAL_SECURITY_DATA` | `false` |
| `XDR_MODE` | `platform`（联调中；默认 `mock`，契约已通过） |
| `XDR_BASE_URL` | `https://103.198.125.151:1443`（已配置） |
| `XDR_AUTH_CODE` | 已配置，值未记录 |
| `XDR_API_KEY` | 空（优先使用联动码） |
| `XDR_DATA_SOURCE` | `alerts`（`incidents` 已验证可用） |
| `XDR_TLS_VERIFY` | `false`（测试环境自签证书临时放行；联调稳定后应改为 `true` + `XDR_CA_BUNDLE`） |
| `AUTH_REQUIRED` | `.env` 未显式写入，代码默认 `true` |
| 初始化管理员 | 用户名和密码均已配置；本文不记录具体凭据 |
| `SESSION_HOURS` | `.env` 未显式写入，代码默认 8 小时 |
| `COOKIE_SECURE` | `.env` 未显式写入，代码默认 `false`，仅适合本地 HTTP |
| `JOB_HARD_TIMEOUT_SECONDS` | `.env` 未显式写入，代码默认 120 秒 |
| `KNOWLEDGE_EMBEDDING_MODEL` | `.env` 未显式写入，代码默认多语种 MiniLM 模型 |
| `SECURITY_SUPERAGENT_DATA_ROOT` | `.env` 未显式写入，Compose 默认 `E:/tzb/security-superagent-data` |

注意：新窗口不得读取并输出 `.env` 的实际密钥或密码；只允许检查“已设置/未设置”。

### 3.2 Agent、模型和任务预算

| 参数 | 当前值 |
|---|---:|
| DeepSeek 单次超时 | 30 秒 |
| DeepSeek 最大尝试 | 2 次 |
| 尝试间退避 | 0.25 秒 × 当前尝试序号 |
| Job 硬上限 | 120 秒 |
| 专门 Agent 并发 | 最多 3 路 |
| 调查计划最大 Replan | 1 次 |
| Bounded ReAct 最大迭代 | 2 次 |
| 调查计划最大工具调用 | 6 次 |
| 工作流配置允许最大工具调用默认值 | 8 次 |
| 工作流节点默认超时 | 30 秒 |
| 用户研判目标最大长度 | 4,000 字符 |
| 高风险动作默认过期 | 30 分钟 |

### 3.3 Docker、端口和数据

| 服务 | 端口 | 内存限制 | 说明 |
|---|---:|---:|---|
| Frontend | `5173` | 256 MB | Nginx 托管 React |
| Backend | `8000` | 2 GB | FastAPI、Worker、LangGraph、RAG |
| MCP Gateway | `127.0.0.1:8100` | 384 MB | 只绑定本机，只读工具 |
| Knowledge Indexer | 无常驻端口 | 3 GB | Compose `indexer` profile，和主演示错峰运行 |

- Docker Desktop 总资源按用户决定维持约 4 GB。
- 后端业务数据库：`data/runtime/security_superagent.sqlite3`。
- LangGraph Checkpoint：`data/runtime/langgraph-checkpoints.sqlite3`。
- Docker 挂载后的实际运行目录：`E:\tzb\security-superagent-data\runtime`。
- 活动知识索引：`E:\tzb\security-superagent-data\knowledge-index`。
- ONNX/FastEmbed 模型：`E:\tzb\security-superagent-data\models`。
- 当前交接时 Docker Desktop 未运行，未发现 Docker 进程；启动新开发窗口后应先手动启动。

### 3.4 发布门槛

- 后端总分支覆盖率至少 85%。
- 认证、确定性策略门、工具注册和响应状态机必须保持 100% 行/分支覆盖。
- 30 条结构化输出、证据和 Prompt 接入必须全部通过。
- Citation Precision@1 至少 29/30；当前为 30/30。
- 未授权高风险动作必须为 0。
- 主演示要求真实 DeepSeek 连续 10 次无阻塞故障；该项尚未最终完成。
- Platform Adapter 未通过契约测试前不得替换 Mock 主演示。

## 4. 尚未解决完的问题

按优先级排列：

### P0：外部平台接入已完成真实联调（2026-08-12）

- 深信服 XDR 云平台（`https://103.198.125.151:1443`，XDR 2.0.20）已接入。
- 认证：联动码（auth_code）→ AES-CBC 解码 AK/SK → AKSK HMAC-SHA256 签名；
  `backend/app/adapters/aksk.py` 移植官方 Python SDK，三组黄金向量与官方逐字节一致。
- 真实调用方式（与 eolink 文档标注不同，已按平台实测修正）：
  - 接口前缀 `/api/xdr/v1/`（eolink 文档同前缀，`/api/v1.0/openapi/` 是 AI 共创网关，返回 `please log in`）；
  - `alerts/list` 用 **POST + JSON body**（`{"page","pageSize","uuIds"}`，pageSize 最小 5，uuIds 原生数组）；
  - `alerts/:uuid/proof` 用 **GET**；返回 `proof` 为平铺字段 dict + `proofType` 列表。
- 字段映射已按真实返回修正：`url/domain/fileMd5/exploitCveId` 可能是列表；`pname/xforwardedFor` 为小写拼写；
  `severity` 数值 (0,10]/(10,30]/(30,50]/(50,70]/(70,100] → LOW/LOW/MEDIUM/HIGH/CRITICAL。
- 已实现：`PlatformXDRAdapter`（alerts/incidents 可切换）、`PlatformResponseAdapter`
  （`network.block_indicator`→blockiprule，`edr.isolate_host`/`iam.disable_account` 安全降级 not_supported）、
  `scripts/check_xdr_connectivity.py` 只读冒烟、`tests/contract/` 35 条契约测试。
- **安全缺口已修复**：前端曾硬编码 `mode:"mock"`，平台模式下真实告警会被误标 SYNTHETIC 而绕过
  数据分类门禁；已改为前端按 `/readyz` 的 `xdr` 模式动态传 `mode/source`，且后端 `jobs.py`
  在 `XDR_MODE=platform` 时强制 `REAL_SECURITY_DATA`（DeepSeek/LangSmith 门禁仍默认关闭，真实数据不出边界）。
- 遗留阻塞项：平台 OpenAPI 无"新增隔离主机"接口，`edr.isolate_host` 保持 not_supported，
  待向平台方确认；`analysisInfo` 智能引擎字段可作后续增强。
- 契约测试通过前不得把 `XDR_MODE` 默认改为 `platform`；当前 `.env` 可切 platform 联调。

### P0：LangSmith 追踪尚未重新验收

- 早期真实运行曾出现 LangSmith `403 Forbidden`。
- 当前 `.env` 中追踪已开启且 Key 已设置，但 2026-08-10 没有执行新的 Trace 写入测试，
  因而不能宣称 403 已修复。
- 下一窗口应检查 Key 是否属于当前 Personal Workspace、项目写权限和 Workspace 绑定；
  只发送合成 SUP-001 数据。

### P1：真实 DeepSeek 稳定性门槛未完成

- 已验证过真实 DeepSeek 成功研判，也观察过连续两次 30 秒超时后正确安全降级。
- 尚未完成 `scripts/run_demo_soak.py --runs 10` 的连续 10 次 HEALTHY 主演示门槛。
- 当前 30/30 是离线确定性金标准回归，不等同于 30 次真实 DeepSeek 语义评测。
- 建议增加“真实 DeepSeek 30 条语义评测报告”，按 `expected_answer_points` 评分，
  但不得让外部模型处理真实 XDR 数据。

### P1：本次窗口未复验容器链路

- Docker Desktop 当前停止，因此本次只复验了本机 Python/前端门禁。
- 启动 Docker 后需重新检查 Backend、Frontend、MCP、活动 Chroma、ONNX 和重启恢复。
- Playwright E2E 与 Docker 镜像构建已在同一提交的 GitHub CI 中通过，但仍建议现场演示前再跑一次。

### P2：文档存在少量历史状态

- `docs/ENVIRONMENT.md` 仍含“Docker 尚未安装”的早期记录，当前事实是已安装但交接时未启动。
- `docs/AGENT_RUNTIME.md` 末尾仍把专门 Agent/SuperAgent 描述为后续扩展，实际已经实现。
- `docs/PROJECT_PLAN.md` 某些风险和日期是原始规划快照，应保留历史或标注“已完成/已变化”。
- 后续应统一 README、环境、运行时和项目计划，避免答辩时出现状态冲突。

### P2：非阻塞技术债

- 前端生产构建存在 Ant Design vendor chunk 约 1.066 MB 的警告；可用动态导入/路由拆包优化，
  当前不影响运行。
- 前端仅有 3 个单元/组件测试，核心 UI 主要依赖 SUP-001 E2E；可补登录、审批、SSE 断线和
  DEGRADED 页面测试。
- pytest 有一条 Starlette `TestClient`/`httpx` 弃用提示；当前不影响测试，但升级依赖前需处理。
- `COOKIE_SECURE=false` 仅适合本地 HTTP；正式 HTTPS 平台必须改为 `true` 并核对反向代理设置。

## 5. 下一步建议

### 第一步：恢复可观测的本地运行环境

1. 手动启动 Docker Desktop，确认总内存约 4 GB。
2. 在项目根目录运行：

   ```powershell
   docker compose -f deploy/compose.yaml up -d --build
   docker compose -f deploy/compose.yaml ps
   ```

3. 检查：

   ```text
   http://localhost:5173
   http://localhost:8000/readyz
   http://127.0.0.1:8100/mcp
   ```

4. `/readyz` 应显示 SQLite、Worker、Chroma、活动索引、ONNX、DeepSeek、LangSmith 和 MCP 状态。

### 第二步：先解决 LangSmith 403

1. 使用当前 Personal Workspace 新建或确认一个可写 API Key。
2. 只用 SUP-001 合成数据执行一次实时 DeepSeek 任务。
3. 在 LangSmith 中确认根 LangGraph Run、节点、模型和工具子 Run 可见。
4. 确认 Trace 不包含 API Key、密码、Cookie、真实个人信息或未经批准的真实 XDR 数据。
5. 若仍为 403，记录响应正文中的权限/Workspace 信息，但不得记录 Key。

### 第三步：完成真实 DeepSeek 稳定性验收

```powershell
.\.venv\Scripts\python.exe scripts\run_demo_soak.py --runs 10
```

要求每次均为 DeepSeek、`model_health=HEALTHY`、三类 Agent 报告完整、引用完整，且无安全降级。
如出现超时，应先统计失败率与耗时，再决定调整 Prompt 长度、上下文预算或模型超时；
不得通过把 DEGRADED 当成功来满足指标。

### 第四步：补真实模型语义评测

- 将 30 条题目逐条作为 `analyst_query` 运行真实 DeepSeek。
- 对比 verdict、关键证据、Citation Precision@1、`expected_answer_points` 和动作安全性。
- 将结果保存为新的、无密钥的 JSON/Markdown 报告；不要修改现有离线金标准的含义。
- 失败样本进入固定回归集，再调整 Prompt、检索重排或确定性策略。

### 第五步：平台资料到达后立即插入 Adapter 联调

- 收集 Base URL、认证、字段 Schema、分页、限流、错误码、Webhook、数据分类和响应接口。
- 新增 Platform XDR/Model/Response Adapter，不改核心 Agent 领域模型。
- 建立 Mock/Platform 契约测试和脱敏策略。
- 未通过契约测试时继续使用 Mock 主演示并记录阻塞原因。

### 第六步：冻结交付

- 修正文档历史状态。
- 重新执行 Docker 重启恢复、认证、MCP、E2E、10 次 Soak、30 条金标准和 50 条对抗样本。
- 检查 PPT 和现场安全表述；不制作演示视频。
- 创建新里程碑 tag、Git Bundle、SHA256 校验和最终提交包。

## 6. 新窗口快速验证命令

Windows 受限会话可能不能写系统 TEMP，pytest 应把临时目录放到 E 盘：

```powershell
cd E:\tzb\security-superagent

$testTemp = 'E:\tzb\security-superagent\data\test-tmp\handoff-next'
New-Item -ItemType Directory -Path $testTemp -Force | Out-Null
$env:TEMP = $testTemp
$env:TMP = $testTemp

.\.venv\Scripts\ruff.exe check backend tests scripts
.\.venv\Scripts\python.exe -m pytest `
  --basetemp=data/test-tmp/handoff-next-base `
  -o cache_dir=data/test-tmp/handoff-next-cache `
  --cov=backend/app `
  --cov-report=term-missing `
  --cov-report=json:data/test-tmp/handoff-next-coverage.json
.\.venv\Scripts\python.exe scripts\check_critical_coverage.py `
  data/test-tmp/handoff-next-coverage.json
.\.venv\Scripts\python.exe -m app.evaluation --summary-only

Set-Location frontend
npm.cmd run typecheck
npm.cmd test -- --run --reporter=verbose
npm.cmd run build
Set-Location ..
```

容器启动后再执行：

```powershell
.\.venv\Scripts\python.exe scripts\check_mcp.py http://127.0.0.1:8100/mcp
.\.venv\Scripts\python.exe scripts\check_auth_flow.py
Set-Location frontend
npm.cmd run e2e
Set-Location ..
```

认证脚本需要从进程环境读取本地管理员凭据；不得在命令历史、聊天或日志中打印密码。

## 7. 版本、备份和交付物

| 项目 | 位置/标识 |
|---|---|
| 当前提交 | `54fbc28f943e707c28fb8fe005c6e3c5d294b8d9` |
| 当前标签 | `milestone-gold-v1.1-20260804` |
| 私有仓库 | `https://github.com/sheepshined/security-superagent` |
| 最新成功 CI | `https://github.com/sheepshined/security-superagent/actions/runs/30908580700` |
| Git Bundle | `E:\tzb\backups\security-superagent-gold-v1.1-20260804.bundle` |
| 发布候选包 | `E:\tzb\releases\gold-v1.1-20260804\security-superagent-release-candidate-54fbc28f943e.zip` |
| 发布包校验 | 同目录 `.zip.sha256` |
| 答辩 PPT | `deliverables\Security-SuperAgent-答辩稿.pptx` |
| 金标准审核表 | `outputs\019fb5fa-63a2-7ce1-a7cb-72f97c600ca9\Security-SuperAgent-评测集审核表.xlsx` |
| 金标准审计记录 | `docs\GOLD_STANDARD_V1_1.md` |

GitHub 账号更换时，可保留本地 `.git` 或从 Bundle 恢复，登录新账号、创建新私有仓库、
替换 remote 后推送全部分支和标签；业务代码、Docker 数据和知识索引无需修改。

## 8. 新窗口开发原则

- 不要重新实现已经完成的 Agent、RAG、审批状态机或金标准修订。
- 不要把 Mock 结果伪装为实时 DeepSeek，也不要把 DEGRADED 当作成功演示。
- 不要向 Agent 暴露任意 Shell、SQL 或通用 HTTP 工具。
- 不要把真实 XDR 数据发送给 DeepSeek/LangSmith，除非数据已批准脱敏且策略显式允许。
- 不要提交 `.env`、数据库、模型、索引、缓存、测试产物或日志。
- 不要在 Platform Adapter 通过契约测试前替换稳定 Mock 主链路。
- 每次修改后优先运行与改动范围相称的测试；安全门、审批、认证和引用逻辑修改必须跑全量门禁。

## 9. 建议给新窗口的第一条指令

> 请先完整阅读 `E:\tzb\security-superagent\SmartSteer_Status.md`，然后检查 Git 状态、
> Docker Desktop、`/readyz` 和 LangSmith 403。不要重做已完成功能，也不要输出 `.env` 密钥。
> 优先完成 LangSmith 可观测性验收和真实 DeepSeek 10 次 Soak；如果 XDR 平台资料已经到达，
> 则暂停非阻塞优化，先实现 Platform Adapter 契约测试。
