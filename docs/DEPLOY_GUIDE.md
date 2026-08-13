# Security SuperAgent 部署指南

> 面向:拿到本仓库的协作者/评审环境搭建
> 更新:2026-08-13

## 0. 前置要求

- Docker + Docker Compose
- 内存建议 ≥ 8GB(后端限 2g、知识库构建 3g、网关 384m、前端 256m)
- 能访问 Docker Hub / PyPI(**受限网络见第 5 节**)

## 1. 获取代码

```bash
git clone https://github.com/sheepshined/security-superagent.git
cd security-superagent
```

## 2. 配置 .env(必做)

```bash
cp .env.example .env
```

`.env` 中**必须修改**的两项:

| 键 | 说明 |
|---|---|
| `SECURITY_SUPERAGENT_DATA_ROOT` | 数据根目录(runtime/知识索引/模型缓存),模板里是作者机器的路径,**改成你自己的目录**,如 `D:/ssa-data` |
| `BOOTSTRAP_ADMIN_PASSWORD` | 前端登录密码,换成强随机值 |

其余按运行模式选配,见第 4 节。`.env` 已被 gitignore,密钥不进仓库。

## 3. 启动与验证

```bash
docker compose -f deploy/compose.yaml up -d
```

首次启动会现场构建镜像(约几分钟)。验证:

| 服务 | 地址 | 预期 |
|---|---|---|
| 后端健康 | `http://127.0.0.1:8000/readyz` | `"status":"ready"` 全绿 |
| 前端 | `http://127.0.0.1:5173` | 用 `BOOTSTRAP_ADMIN_USERNAME` / 密码登录 |
| MCP 网关 | `http://127.0.0.1:8100/mcp` | streamable-http(initialize 200) |

**知识库(可选)**:默认不构建索引,系统自动降级快照模式(可用但检索弱)。要构建完整索引(24238 chunks,需联网下载数据源):

```bash
docker compose -f deploy/compose.yaml --profile indexer run --rm knowledge-indexer
```

## 4. 运行模式

### 4.1 完全离线演示(mock,默认)

模板默认 `XDR_MODE=mock`、DeepSeek key 为空——**无需任何平台凭据/API key**,纯本地演示即可跑通完整研判流程(合成告警)。

### 4.2 真实 XDR 平台联调(platform)

```ini
XDR_MODE=platform
XDR_BASE_URL=https://<平台地址>
XDR_AUTH_CODE=<平台联动码>
XDR_TLS_VERIFY=true
XDR_CA_BUNDLE=<平台证书路径>   # 自签证书时导入,容器内映射为 /app/certs/
```

安全铁律:`ALLOW_EXTERNAL_MODEL_REAL_DATA=false` 保持不动——真实数据不出本机,外部模型被拒,研判走确定性路径(结果 `model_health=DEGRADED` 属预期设计)。

### 4.3 外部工具通道(平台 AI 共创接入,可选)

```ini
PUBLIC_BASE_URL=https://<公网可达地址>   # 注入 OpenAPI servers,供 GPTs 风格导入
EXTERNAL_API_KEY=<随机密钥>             # 只读外部调用的 X-API-Key / Bearer
```

- 只读 schema:`GET /openapi-external.json`(10 个只读接口,写接口不暴露)
- 一键研判:`GET /api/v1/triage/investigate?alert_uuid=<告警uuid>`,带 `X-API-Key` 头
- **平台适配铁律**:给 GPTs 风格平台的接口一律用 **GET + query 参数**(平台对 POST 参数序列化有缺陷,已实测);详见《AI共创平台工作流接入配置》

两个键留空 = 通道关闭,纯本地模式,无任何影响。

## 5. 受限网络部署(Docker Hub 不可达)

在能构建的机器上打好镜像,打包分发:

```bash
# 构建机(可访问 Docker Hub)
docker build -f deploy/backend.Dockerfile -t security-superagent-backend .
docker build -f deploy/frontend.Dockerfile -t security-superagent-frontend .

# 打包
docker save -o ssa-backend.tar security-superagent-backend
docker save -o ssa-frontend.tar security-superagent-frontend

# 目标机
docker load -i ssa-backend.tar
docker load -i ssa-frontend.tar
```

目标机改 `deploy/compose.local.yaml` 中的 `image:` 为对应镜像名后 `up -d`(该文件是作者在受限网络下的变通,勿用于 CI/发布)。

## 6. 常见问题

| 现象 | 原因/处理 |
|---|---|
| `readyz` 里 `chroma/knowledge_index=snapshot_fallback`、`knowledge_chunks=0` | 未构建知识索引,属正常降级;需要完整检索就按第 3 节跑 indexer |
| 本机 HTTP 代理拦截 loopback | 直连本机端口时设 `NO_PROXY=127.0.0.1,localhost` |
| 端口被占 | 改 `compose.yaml` 的 ports 映射(默认 8000/5173/8100) |
| Linux 下数据卷权限问题 | 给 `SECURITY_SUPERAGENT_DATA_ROOT` 目录写权限,或改挂载属主 |
| 真实平台 401/签名失败 | 对照《XDR 接入》文档:前缀 `/api/xdr/v1/`、POST+JSON body、`ensure_ascii=True` 序列化、签名后禁止改请求 |

## 7. 相关文档

- 项目状态/架构:`SmartSteer_Status--v1.5--.md`
- 平台开放问题清单:`docs/XDR_PLATFORM_OPEN_ISSUES.md`
- 平台共创工作流接入:《AI共创平台工作流接入配置》(tzb_txt)
