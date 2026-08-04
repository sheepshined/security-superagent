# 开发与部署环境基线

## 1. 当前开发机

- 操作系统：Windows 11 24H2。
- CPU：Intel Core i9-13900HX，24 核 / 32 线程。
- 内存：约 16GB；开发时避免同时运行不必要的重型服务。
- 项目位置：`E:\tzb\security-superagent`。
- 当前已安装：Git、Node.js、npm。
- 当前未安装：WSL2、Docker Desktop。
- 项目 Python 目标版本：3.11；临时验证允许 3.12。

本轮实测运行时：

- Windows 11 家庭中文版，Build 26100。
- Python 3.12.13（项目独立 `.venv`）。
- Node.js 24.15.0、npm 11.12.1。
- LangGraph 1.2.10、LangChain 1.3.14、LangSmith 0.10.14。
- FastAPI 0.141.1、Pydantic 2.13.4。
- Docker 当前仍未安装。
- LangGraph SQLite 检查点位于
  `E:\tzb\security-superagent\data\runtime\langgraph-checkpoints.sqlite3`。
- Job、Case、Result 和业务审计事件位于
  `E:\tzb\security-superagent\data\runtime\security_superagent.sqlite3`。

WSL2 和 Docker Desktop 的 E 盘安装步骤见
[WSL_DOCKER_SETUP.md](WSL_DOCKER_SETUP.md)。

## 2. Docker 与 WSL 规划

Docker 安装涉及管理员权限、Windows 功能和可能的重启，由用户在进入容器联调阶段执行。

数据位置统一放在 E 盘：

```text
E:\DockerDesktop\data     Docker 虚拟磁盘、镜像和卷
E:\DockerDesktop\cache    构建缓存
E:\WSL                    WSL 发行版数据
```

资源限制：

- Docker/WSL 内存上限 4GB，紧张时降到 3GB。
- CPU 分配 4–6 核。
- Swap 1–2GB。
- 启用 Docker Resource Saver。
- 演示时只启动必要服务。
- 不迁移或关闭 Windows 页文件。

在 Docker 安装完成前，开发和演示使用 Windows 原生启动脚本。

## 3. 未来比赛平台

平台预计以云服务方式提供，具体端点、认证、字段、错误码和权限待官方资料到位后确认。

逻辑边界：

- 分布式 XDR：告警、流量、端点、身份、日志和响应联动。
- 平台垂域模型：真实 XDR 数据的默认推理模型。
- 算力/智能体服务：运行本项目后端、Agent、RAG 和适配层。
- 探针：采集网络流量并上报 XDR，不属于本项目开发范围。

平台资料到位前使用 `MockXDRAdapter`。正式接入只修改 Adapter 与配置映射，不改变领域模型和业务 Agent。

## 4. 数据与模型边界

- DeepSeek 只处理合成或脱敏数据。
- 未经批准的真实 XDR 数据不得发送给外部模型。
- API Key 只写入本地 `.env`，不得发送到聊天、提交到 Git 或进入日志。
- 高风险真实动作必须 dry-run、策略校验、人工审批和执行回查。

## 5. Windows 原生启动

安装依赖：

```powershell
.\scripts\bootstrap.ps1
```

分别启动后端和前端：

```powershell
.\scripts\run-backend.ps1
.\scripts\run-frontend.ps1
```

默认地址：

- 控制台：`http://127.0.0.1:5173`
- API 文档：`http://127.0.0.1:8000/docs`
- 健康检查：`http://127.0.0.1:8000/healthz`
- 就绪检查：`http://127.0.0.1:8000/readyz`
