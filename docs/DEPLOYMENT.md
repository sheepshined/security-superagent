# Docker 部署

## 资源规划

Docker Desktop 建议总内存 6GB，E 盘预留至少 20GB。Compose 上限：后端 2GB、
前端 256MB、MCP 384MB、按需索引器 3GB。MCP 原计划 256MB，但完整活动索引实测
常驻约 231MB，故调整为 384MB 以保留瞬时余量。

数据目录默认挂载到：

```text
E:\tzb\security-superagent-data\runtime
E:\tzb\security-superagent-data\knowledge-index
E:\tzb\security-superagent-data\knowledge-cache
E:\tzb\security-superagent-data\models
```

## 启动与检查

```powershell
docker compose -f deploy/compose.yaml up -d --build
docker compose -f deploy/compose.yaml ps
curl.exe --noproxy "*" http://127.0.0.1:8000/readyz
.\.venv\Scripts\python.exe scripts\check_mcp.py http://127.0.0.1:8100/mcp
```

`readyz` 应报告 SQLite、Worker、Chroma、活动索引、ONNX、Model、LangSmith 和 MCP
状态。认证显示 `not_initialized` 时，应在 `.env` 中设置管理员初始化变量并重建后端，
不能用关闭认证代替正式演示登录。

## 索引构建

```powershell
docker compose -f deploy/compose.yaml --profile indexer run --rm knowledge-indexer
```

索引构建与主演示错峰。后端在构建期间继续读取旧的活动索引，新代完成后原子切换。

## 备份

业务代码使用 Git tag 和 Git Bundle；运行数据另行备份 E 盘目录。模型、索引、缓存、
数据库和 `.env` 不进入 Git。GitHub 账号更换时只需更换 remote 并推送所有分支和标签。
