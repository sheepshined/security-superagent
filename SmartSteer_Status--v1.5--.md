# SmartSteer / Security SuperAgent 项目交接状态

> 文档版本：v1.5
> 交接日期：2026-08-13（Asia/Shanghai）
> 项目目录：`E:\tzb\security-superagent`
> 项目目标：面向安全运营告警研判，完成基础任务、进阶任务和标准 SuperAgent，强调证据优先、可追溯、可约束和安全降级。
> 本文不包含任何 API Key、密码、Cookie 或会话令牌。

## 0. 一句话状态

项目已经形成可运行的全栈演示版本：React 控制台、FastAPI API、LangGraph 混合调查图、
Supervisor 与 Network/Endpoint/Identity 专门 Agent、混合 RAG、受控响应、RBAC、SSE、
Trace、MCP、Skill、Docker 和自动化测试均已实现。**深信服 XDR 云平台（XDR 2.0.20）已接入并完成真实数据联调**：
AKSK 联动码签名认证、alerts/incidents 双数据源、真实 8 条告警全量拉取、真实告警研判
（安全降级链路）均已通过真实平台验证；**2026-08-13 完成三个适配器缺陷修复**
（响应网关前缀、签名序列化、列表分页截断），并通过真实平台只读探测 + 全量测试 +
Docker 容器复验（新代码）。处置能力三缺口（封禁设备未授权/无隔离新增接口/无账号接口）
已整理为《向官方确认问题清单》。默认 `XDR_MODE=mock`，切换 `platform` 后走真实数据。

当前代码位于 `main`，基线提交为 `54fbc28f943e707c28fb8fe005c6e3c5d294b8d9`，
标签为 `milestone-gold-v1.1-20260804`。**工作树含 2026-08-13 未提交改动**
（适配器修复 + 契约测试 + 分页修复 + 本版本文档，见 §1.9 清单），提交前请复核。

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
- 用户已明确取消"录制 3 分钟和 90 秒演示视频"；现场演示提词仍保留，不要求视频文件。

### 1.2 基础告警研判闭环

- 支持创建异步 Job、查询 Job/Result、显式 `Idempotency-Key`、同告警多分析版本和重新分析。
- Worker 使用 LangGraph 异步执行；任务、结果、审计事件和 Checkpoint 均落 SQLite。
- SSE 推送节点、Agent、工具、审批、进度、错误和终态；支持断线续传。
- DeepSeek 输出通过 Pydantic 结构化解析；模型失败后继续确定性研判。
- 安全降级规则已经实现：模型异常时标记 `DEGRADED`，最终最高只能为
  `needs_review`，禁止自动执行响应。
- 当前预置场景：TRI-001~003、SUP-001~002（合成）；另有真实平台 8 条告警（§1.8）。

### 1.3 Agent 与 SuperAgent

- 主流程为 `Plan–Execute–Verify–Replan + Bounded ReAct`，不是无限循环式 ReAct。
- Supervisor 生成结构化调查计划，并行调度 Network、Endpoint、Identity 三个专门 Agent；
  知识检索作为共享能力。
- Specialist Agent 结果统一合并为 Evidence、AgentReport、Unknown 和 Conflict。
- Critic 检查证据覆盖、冲突、引用、预算；关键证据不足时最多生成一次真实修订计划。
- 前端展示计划、Agent 报告、工具、Observation、冲突、Critic 和最终裁决；
  不展示模型隐藏思维链。
- "本次研判目标"已进入调查计划、专门 Agent 目标、RAG 查询、DeepSeek Prompt 和审计 Trace。

### 1.4 RAG 与本地知识库

- 已实现知识源登记、下载、许可/哈希记录、解析、分块、去重、删除传播、版本化索引和
  ACTIVE 指针原子切换。
- 检索链固定为：访问标签过滤 → BM25 与向量并行召回 → RRF → 技术编号/来源/时效/
  场景重排 → 去重 → 引用完整性校验。
- 向量模型：`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`，使用 FastEmbed ONNX。
- Chroma 使用本地 `PersistentClient`，不增加独立向量数据库服务。
- 当前 E 盘活动索引：`idx-20260804073548-9e7477f5`；状态 `ready`；Chunk 24,238；
  文档 23,659；**Chroma 真向量已启用**（`/readyz` 与容器内均验证 `chroma: ready`）。
- 知识源：MITRE ATT&CK Enterprise、CWE Top 25、CISA KEV、横向移动 Sigma、
  中国网络安全法/数据安全法/个人信息保护法/网络数据安全管理条例/
  关键信息基础设施安全保护条例和合成调查手册。
- 活动索引、模型、缓存和运行数据实际存放在 `E:\tzb\security-superagent-data`（约 1.20 GB）。

### 1.5 认证、受控响应和低代码

- 本地 RBAC：管理员、分析员、审批员；密码 Argon2id；会话 HttpOnly Cookie；CSRF 强制。
- 高风险动作状态机：`PROPOSED → DRY_RUN_PASSED → PENDING_APPROVAL → APPROVED/REJECTED →
  EXECUTING → SUCCEEDED/FAILED → VERIFIED/ROLLED_BACK/EXPIRED`。
- 高风险动作必须经过 dry-run、不可变计划哈希、独立审批、Response Adapter 和回查。
- 分析员不能批准自己发起的高风险动作。
- 工作流固定安全拓扑；支持不可变工作流版本、节点启停、模型、超时、预算、allowlist 配置。

### 1.6 前端、MCP 和 Skill

- React 控制台：登录、运营总览、任务、工作流、知识中心、审批中心和 Trace 页面。
- MCP Gateway 只读三能力：知识搜索、告警上下文、最小化 Trace 摘要。
  （2026-08-13 容器复验：三工具正常，`alert_context` 用真实告警 ID 实测 `isError: false`）
- 已实现 `lateral-movement-investigation` Skill v1（工作流/Prompt/允许工具/知识范围/Schema/评测绑定）。

### 1.7 评测与验证

- 金标准 v1.1：5 场景 × 6 问题 = 30 条题目级标准；2026-08-13 复验 30/30、
  `release_gate_passed`、Citation Precision@1 30/30、未授权高风险动作 0。
- 2026-08-13 复验门禁：Ruff ✅；后端 **133/133**（含契约测试 47/47）；总分支覆盖率
  **85.59%**；关键安全覆盖率 100% 行/分支；前端类型检查/3 单测/生产构建 08-10 已通过。
- Playwright SUP-001 E2E 与 Docker 构建在提交 `54fbc28` 的 CI 通过；现场演示前建议再跑一次 E2E。

### 1.8 深信服 XDR 平台接入（真实数据联调完成，2026-08-12）

- 平台：`https://103.198.125.151:1443`（XDR 2.0.20），联动码 auth_code → AES-CBC 解码
  AK/SK → AKSK HMAC-SHA256 签名；`backend/app/adapters/aksk.py` 为官方 Python SDK 的
  忠实移植，黄金向量与官方逐字节一致。
- 真实调用方式（已按平台实测修正 eolink 文档标注）：
  - 接口前缀 **`/api/xdr/v1/`**（`/api/v1.0/openapi/` 是 AI 共创网关，返回 `please log in`）；
  - `alerts/list` 用 **POST + JSON body**（pageSize 最小 5，uuIds 原生数组）；
  - `alerts/:uuid/proof` 用 **GET**；返回平铺字段 dict + `proofType` 列表。
- 字段映射已修正：`url/domain/fileMd5/exploitCveId` 可能为列表；`pname/xforwardedFor` 小写；
  severity (0,10]/(10,30]/(30,50]/(50,70]/(70,100] → LOW/LOW/MEDIUM/HIGH/CRITICAL；
  incidents 用 `incidentSeverity`(0-4)、`startTime/endTime`、`alertIds` 聚合关联告警举证。
- 已实现：`PlatformXDRAdapter`（alerts/incidents 可切换 + 全量翻页）、`PlatformResponseAdapter`
  （blockiprule 封禁族）、`platform_tls.py` 证书指纹 PIN、`scripts/check_xdr_connectivity.py`
  只读冒烟、`tests/contract/` 契约测试。
- 真实平台数据：8 条告警全量拉取并逐条取到举证（调查文档见 §7 交付物）；真实告警研判
  走通 LangGraph 全流程：`verdict=needs_review`、2 条 network_flow（access_label=platform）、
  4 条 RAG 引用、`model_health=DEGRADED`（真实数据不出边界，安全门禁符合设计）。
- 安全缺口已修复：前端按 `/readyz` 动态传 `mode/source`；后端 `jobs.py` 在 `XDR_MODE=platform`
  时强制 `REAL_SECURITY_DATA`，DeepSeek/LangSmith 门禁拒绝真实数据外发。

### 1.9 2026-08-13 修复与验证（工作树未提交改动清单）

| 改动 | 文件 | 说明 |
|---|---|---|
| 响应网关前缀修复 | `backend/app/adapters/platform_response.py` | 三处 block 调用从 `/api/v1.0/openapi/`（实测 401）迁移到 `/api/xdr/v1/` 且 GET→POST+JSON body；`blockIpRule.view` 数组化并补 `mode:"in"`；unblock 按实测契约（`code=Success` 但 `success=0` 时经 detail 交叉验证再判 rolled_back） |
| 签名序列化修复 | `platform_xdr.py` / `platform_response.py` | 平台网关对 payload 做字节多重集验签；body 含中文等非 ASCII 时必须 `json.dumps(ensure_ascii=True)` 序列化并逐字节发送（httpx `json=` 按 UTF-8 原文发送会 401） |
| 列表分页修复 | `platform_xdr.py` | `list_scenarios()` 只取第 1 页会静默截断；新增 `_list_all_alerts()` 全量翻页（消费 envelope `total`、短页终止、跨页去重、10,000 页守卫超限显式报错） |
| 契约测试更新 | `tests/contract/test_platform_response_contract.py`、`test_platform_xdr_contract.py` | 断言更新为真实平台契约；新增多页/短页/去重 3 条；契约测试 47/47 |
| 文档 | `docs/XDR_PLATFORM_OPEN_ISSUES.md`（新增）、`outputs/xdr-alert-survey-20260813.md`+`xdr-alert-raw-20260813.json`（新增，不入库）、本文件 v1.5、`E:\tzb\tzb_txt\` 四份文档 v1.5 |

真实平台写链路验证结果：dry_run/detail/unblock 契约已实测；**create（新增封禁规则）被平台侧阻塞**——
`blockiprule/network` 要求 devices 非空（"设备不能为空"），而 `device/blockdevice/list` 实测
`item: null`（当前实例无任何已授权联动封禁设备），create→verify→unblock 全链路待平台
接入封禁设备后补验。

## 2. 当前代码结构

```text
security-superagent/
├─ backend/app/                  Python 后端
│  ├─ main.py                    FastAPI 入口
│  ├─ core/config.py             环境配置及安全默认值
│  ├─ domain/                    Job、Evidence、Result、Action、Workflow 等领域模型
│  ├─ api/                       /api/v1 路由和依赖注入
│  ├─ adapters/                  XDR、Model、Response 协议及 Mock/DeepSeek/Platform 适配器
│  │  ├─ aksk.py                 AKSK HMAC-SHA256 签名器（联动码解码 + 签名，官方 SDK 移植）
│  │  ├─ platform_xdr.py         PlatformXDRAdapter（alerts/incidents 双源 + 全量翻页）
│  │  ├─ platform_response.py    PlatformResponseAdapter（封禁执行/回查/解除）
│  │  ├─ platform_tls.py         自签证书指纹 PIN 校验
│  │  └─ xdr_schemas.py          平台响应模型与字段映射（告警/举证→领域模型）
│  ├─ agents/
│  │  ├─ supervisor.py           Supervisor 与专门 Agent 调度
│  │  ├─ triage/graph.py         主 LangGraph 调查图（9 节点）
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
├─ knowledge/                    知识源注册表与离线快照
├─ evaluations/                  gold/triage-rag-30.json + security/adversarial-50.json
├─ skills/lateral-movement-investigation/  横向移动 Skill v1
├─ deploy/                       Compose、Dockerfile、Nginx（含 compose.local.yaml 本地重建流程）
├─ scripts/                      启动、健康检查、索引验证、Soak、打包、XDR 连通性脚本
├─ tests/                        API、单元与契约测试（tests/contract/ 为平台契约）
├─ docs/                         架构、API、测试、安全、部署文档 + XDR_PLATFORM_OPEN_ISSUES.md
├─ deliverables/                 答辩 PPT
└─ outputs/                      本地调查/审核输出；被 Git 忽略
```

关键阅读顺序：README → 本文 → `docs/ARCHITECTURE.md` → `docs/AGENT_RUNTIME.md` →
`docs/XDR_PLATFORM_OPEN_ISSUES.md` → `backend/app/agents/triage/graph.py` →
`backend/app/adapters/platform_xdr.py` → `backend/app/adapters/platform_response.py`。

## 3. 关键参数与当前有效配置

### 3.1 本机 `.env` 状态（只记录状态，不记录密钥值）

| 配置 | 当前状态/有效值 |
|---|---|
| `APP_ENV` / `APP_HOST` / `APP_PORT` | `development` / `127.0.0.1:8000` |
| `MODEL_PROVIDER` / `DEEPSEEK_MODEL` | `deepseek` / `deepseek-v4-flash`（真实研判已验证可用） |
| `DEEPSEEK_API_KEY` / `DEEPSEEK_BASE_URL` | 已配置，值未记录 / `https://api.deepseek.com` |
| `ALLOW_EXTERNAL_MODEL_REAL_DATA` | `false` |
| `LANGSMITH_TRACING` / `LANGSMITH_PROJECT` | `true` / `security-superagent-dev`（403 待复验，见 §4） |
| `LANGSMITH_ALLOW_REAL_SECURITY_DATA` | `false` |
| `XDR_MODE` | `platform`（联调中；代码默认 `mock`） |
| `XDR_BASE_URL` | `https://103.198.125.151:1443` |
| `XDR_AUTH_CODE` | 已配置，值未记录；`XDR_API_KEY` 空（优先联动码） |
| `XDR_DATA_SOURCE` | `alerts`（`incidents` 已验证可用） |
| `XDR_TLS_VERIFY` | `true`（连通性脚本实测输出）；配合 `XDR_CA_BUNDLE` 走证书指纹 PIN（`platform_tls.py`） |
| `AUTH_REQUIRED` | 代码默认 `true`；初始化管理员已配置，本文不记录凭据 |
| `SESSION_HOURS` / `COOKIE_SECURE` | 默认 8 小时 / 默认 `false`（仅适合本地 HTTP） |
| `JOB_HARD_TIMEOUT_SECONDS` | 默认 120 秒 |
| `KNOWLEDGE_EMBEDDING_MODEL` | 默认多语种 MiniLM |
| `SECURITY_SUPERAGENT_DATA_ROOT` | Compose 默认 `E:/tzb/security-superagent-data` |

注意：新窗口不得读取并输出 `.env` 的实际密钥或密码；只允许检查"已设置/未设置"。

### 3.2 Agent、模型和任务预算（当前值）

| 参数 | 当前值 | 参数 | 当前值 |
|---|---|---:|---:|
| DeepSeek 单次超时 | 30 秒 | 尝试间退避 | 0.25 秒 × 序号 |
| DeepSeek 最大尝试 | 2 次 | Job 硬上限 | 120 秒 |
| 专门 Agent 并发 | 最多 3 路 | 最大 Replan | 1 次 |
| Bounded ReAct 最大迭代 | 2 次 | 调查计划最大工具调用 | 6 次 |
| 工作流默认最大工具调用 | 8 次 | 节点默认超时 | 30 秒 |
| 研判目标最大长度 | 4,000 字符 | 高风险动作默认过期 | 30 分钟 |
| 告警列表默认 pageSize | 50（5~200） | 全量翻页 | 消费 total，10,000 页守卫 |

### 3.3 Docker、端口和数据

| 服务 | 端口 | 内存限制 | 说明 |
|---|---:|---:|---|
| Frontend | `5173` | 256 MB | Nginx 托管 React |
| Backend | `8000` | 2 GB | FastAPI、Worker、LangGraph、RAG |
| MCP Gateway | `127.0.0.1:8100` | 384 MB | 只绑定本机，只读工具 |
| Knowledge Indexer | 无常驻端口 | 3 GB | Compose `indexer` profile，错峰运行 |

- **本机 Docker Hub 不可达**：本地重建走 `deploy/compose.local.yaml` +
  `deploy/backend.local.Dockerfile`（FROM 本地缓存 `deploy-backend:latest`，COPY 新代码 +
  补装 `pycryptodome`）；2026-08-13 已按此流程重建并复验通过。
- 容器内平台证书：`XDR_CA_BUNDLE=/app/certs/Sangfor-Platform.crt`（指纹 PIN）。
- 直连 MCP 前需设 `NO_PROXY` 绕过本机 HTTP 代理（`check_mcp.py` 已内置处理）。

### 3.4 发布门槛

- 后端总分支覆盖率 ≥85%（当前 85.59%）。
- 认证、确定性策略门、工具注册和响应状态机必须 100% 行/分支覆盖（当前满足）。
- 30 条结构化输出、证据和 Prompt 接入必须全部通过（当前 30/30）。
- Citation Precision@1 ≥29/30（当前 30/30）；未授权高风险动作必须为 0。
- 主演示要求真实 DeepSeek 连续 10 次无阻塞故障（`run_demo_soak.py --runs 10`，**尚未完成**）。
- Platform Adapter 契约测试全部通过（当前 47/47）。

## 4. 尚未解决完的问题

按优先级排列：

### P0：LangSmith 追踪尚未重新验收

- 早期真实运行曾出现 LangSmith `403 Forbidden`；`.env` 中追踪已开启且 Key 已设置，
  但至今没有执行新的 Trace 写入测试，不能宣称 403 已修复。
- 下一窗口应只发送合成 SUP-001 数据，检查 Key 的 Workspace 归属、项目写权限与绑定。

### P0：平台处置能力三缺口（已整理为官方申请清单，等待平台侧）

- 已输出 `docs/XDR_PLATFORM_OPEN_ISSUES.md`（2026-08-13）：向深信服官方的确认清单，
  含 3 类处置缺口 + 7 项文档勘误 + 数据侧特性说明 + 我方已修复项说明。
- ① 联动封禁：接口族齐备且已修复可通，但**平台当前无任何已授权封禁设备**
  （`device/blockdevice/list` 返回 item:null；`blockiprule/network` 报"设备不能为空"），
  需平台授权 AF/EDR 设备并确认数值型 devId 获取方式后，才能完成 create→verify→unblock 终验。
- ② 主机隔离：129 接口中隔离组只有 list+unisolate，**无"新增隔离"接口**，需官方确认
  是否只能走 SOAR 剧本或 Web 端下发。
- ③ 身份处置：129 接口中账号/身份类接口为 0，需官方确认是否有账号禁用/锁定或 IAM/AD 联动方案。

### P0（数据侧）：平台告警举证只有网络层

- 全部 8 条真实告警 proofType 仅为 `http`/`networkInfo` → 只有 network_flow 证据；
  缺 process_tree（端点）与 identity_event（身份）举证，因此每次研判恒有两条 unknowns
  （`services/triage.py:_unknowns`）并保守判定 `needs_review`。属平台引擎（NDR/WAF）
  数据侧特性，非本项目缺陷；需平台接入 EDR/身份源后才能完整研判。
- 相关未消费字段：`analysisInfo`（智能引擎思考 JSON）、`gptResult` 已存在于平台返回中，
  可作第二道研判辅助输入（列入官方问题清单）。

### P1：真实 DeepSeek 稳定性门槛未完成

- 已验证真实 DeepSeek 成功研判与连续两次 30 秒超时后的安全降级；但
  `scripts/run_demo_soak.py --runs 10` 的连续 10 次 HEALTHY 门槛尚未完成。
- 当前 30/30 是离线确定性金标准回归，不等同于真实模型语义评测。

### P1：真实 DeepSeek 30 条语义评测未完成

- 建议按 `expected_answer_points` 评分；不得让外部模型处理真实 XDR 数据。

### P2：文档历史状态与评测口径

- `docs/ENVIRONMENT.md`、`docs/AGENT_RUNTIME.md`、`docs/PROJECT_PLAN.md` 仍有早期状态描述，
  需统一清理（本次已把交接文档与 `E:\tzb\tzb_txt\` 四份文档更新到 v1.5）。
- 金标准离线评测固定使用确定性检索器（`evaluation.py:51`），该设计决策尚未在代码与
  文档中显式声明；生产活动索引现已启用 Chroma 真向量（24238 chunks），但无真向量 smoke 对照测试。
- `structured_output_pass` 在 `evaluation.py:107` 仍硬编码 `True`，指标口径失真。

### P2：非阻塞技术债

- 前端生产构建存在 Ant Design vendor chunk 约 1.066 MB 警告；可用动态导入优化。
- 前端仅 3 个单元/组件测试；建议补登录、审批、SSE 断线和 DEGRADED 页面测试。
- pytest 有一条 Starlette `TestClient`/`httpx` 弃用提示。
- `COOKIE_SECURE=false` 仅适合本地 HTTP；正式 HTTPS 平台必须改为 `true` 并核对反向代理。

## 5. 下一步建议

1. **把官方申请清单发给深信服**（`docs/XDR_PLATFORM_OPEN_ISSUES.md`），重点催：
   封禁设备授权与 devId 获取方式、隔离新增接口、账号处置接口、告警推送接口。
2. **LangSmith 403 复验**：只用合成 SUP-001 数据执行一次实时 DeepSeek 任务，
   确认根 Run/节点/模型子 Run 可见且不含任何真实数据与密钥。
3. **真实 DeepSeek 稳定性验收**：`.\\.venv\\Scripts\\python.exe scripts\\run_demo_soak.py --runs 10`，
   要求每次 `model_health=HEALTHY`、三类 Agent 报告与引用完整、无安全降级。
4. **补真实模型语义评测**：30 条题目逐条以 `analyst_query` 跑真实 DeepSeek，
   对照 verdict/证据/Citation Precision@1/`expected_answer_points`/动作安全，
   产出无密钥 JSON/Markdown 报告；失败样本进固定回归集。
5. **平台设备授权到位后**：完成 blockiprule create→verify→unblock 全链路终验
   （用 TEST-NET 保留地址 + 即建即解），并把 `_not_supported` 的隔离/账号状态按官方答复更新。
6. **冻结交付**：清理 docs 历史状态、补跑 E2E、提交 2026-08-13 工作树改动、
   创建新里程碑 tag、Git Bundle、SHA256 校验和最终提交包。

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
  --cov=backend/app --cov-report=term-missing `
  --cov-report=json:data/test-tmp/handoff-next-coverage.json
.\.venv\Scripts\python.exe scripts\check_critical_coverage.py `
  data/test-tmp/handoff-next-coverage.json
.\.venv\Scripts\python.exe -m app.evaluation --summary-only
.\.venv\Scripts\python.exe scripts\check_xdr_connectivity.py

Set-Location frontend
npm.cmd run typecheck
npm.cmd test -- --run --reporter=verbose
npm.cmd run build
Set-Location ..
```

容器启动后（本机 Docker Hub 不可达，用本地缓存重建）：

```powershell
docker build -f deploy/backend.local.Dockerfile -t deploy-backend:local .
docker build -f deploy/backend.local.Dockerfile -t deploy-mcp-gateway:local .
docker compose -f deploy/compose.local.yaml up -d
curl.exe --noproxy "*" http://127.0.0.1:8000/readyz
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
| 当前提交 | `54fbc28f943e707c28fb8fe005c6e3c5d294b8d9`（工作树含 2026-08-13 未提交改动，见 §1.9） |
| 当前标签 | `milestone-gold-v1.1-20260804` |
| 私有仓库 | `https://github.com/sheepshined/security-superagent` |
| 最新成功 CI | `https://github.com/sheepshined/security-superagent/actions/runs/30908580700` |
| Git Bundle | `E:\tzb\backups\security-superagent-gold-v1.1-20260804.bundle` |
| 发布候选包 | `E:\tzb\releases\gold-v1.1-20260804\security-superagent-release-candidate-54fbc28f943e.zip` |
| 官方问题清单（新） | `docs\XDR_PLATFORM_OPEN_ISSUES.md` |
| 真实告警调查文档（新） | `outputs\xdr-alert-survey-20260813.md` + `xdr-alert-raw-20260813.json`（不入库） |
| 答辩 PPT | `deliverables\Security-SuperAgent-答辩稿.pptx` |
| 金标准审核表 | `outputs\019fb5fa-63a2-7ce1-a7cb-72f97c600ca9\Security-SuperAgent-评测集审核表.xlsx` |

## 8. 新窗口开发原则

- 不要重新实现已经完成的 Agent、RAG、审批状态机或金标准修订。
- 不要把 Mock 结果伪装为实时 DeepSeek，也不要把 DEGRADED 当作成功演示。
- 不要向 Agent 暴露任意 Shell、SQL 或通用 HTTP 工具。
- 不要把真实 XDR 数据发送给 DeepSeek/LangSmith，除非数据已批准脱敏且策略显式允许。
- 不要提交 `.env`、数据库、模型、索引、缓存、测试产物或日志。
- 平台签名请求必须"序列化一次、逐字节发送"（`ensure_ascii=True`），签名后不得改动请求。
- 平台写操作（封禁等）只允许在用户明确授权下、用 TEST-NET 目标 + 即建即解方式验证。
- 每次修改后优先运行与改动范围相称的测试；安全门、审批、认证和引用逻辑修改必须跑全量门禁。

## 9. 建议给新窗口的第一条指令

> 请先完整阅读 `E:\tzb\security-superagent\SmartSteer_Status--v1.5--.md` 和
> `docs/XDR_PLATFORM_OPEN_ISSUES.md`，然后检查 Git 状态（2026-08-13 工作树改动是否已提交）、
> Docker 状态与 `/readyz`。不要重做已完成功能，也不要输出 `.env` 密钥。
> 优先完成：LangSmith 可观测性验收、真实 DeepSeek 10 次 Soak、向深信服官方发送
> 处置能力确认清单；平台封禁设备授权到位后立即补验 create→verify→unblock 全链路。
