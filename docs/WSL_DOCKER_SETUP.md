# WSL2 与 Docker Desktop（E 盘）安装清单

当前 Windows 11 家庭版可以使用 WSL2 和 Docker Desktop 的 Linux 容器后端；本项目不需要
Windows Containers。开启 WSL2 是一次性系统操作，需要管理员权限，并可能要求重启。

## 1. 安装 WSL2

保存工作后，以管理员身份打开 PowerShell：

```powershell
New-Item -ItemType Directory -Force E:\WSL\Ubuntu
wsl --install -d Ubuntu --location E:\WSL\Ubuntu
```

如果下载卡在 0%，改用：

```powershell
wsl --install --web-download -d Ubuntu --location E:\WSL\Ubuntu
```

按提示重启。重启后执行：

```powershell
wsl --update
wsl --set-default-version 2
wsl --version
wsl -l -v
```

首次打开 Ubuntu 时设置 Linux 用户名和密码。不要在 WSL 中重复安装 Docker Engine，避免
与 Docker Desktop 的 WSL2 后端冲突。

## 2. 限制 WSL2 资源

在 `%UserProfile%\.wslconfig` 中设置：

```ini
[wsl2]
memory=4GB
processors=4
swap=2GB
swapFile=E:\\WSL\\wsl-swap.vhdx
```

保存后执行：

```powershell
wsl --shutdown
```

## 3. 将 Docker Desktop 安装和数据放到 E 盘

从 Docker 官方网站下载 `Docker Desktop Installer.exe`。若希望程序本体和 WSL 数据都放在
E 盘，以管理员身份在安装包所在目录执行：

```powershell
New-Item -ItemType Directory -Force E:\DockerDesktop\app
New-Item -ItemType Directory -Force E:\DockerDesktop\data
Start-Process -FilePath ".\Docker Desktop Installer.exe" -Wait -ArgumentList @(
  "install",
  "--accept-license",
  "--backend=wsl-2",
  "--installation-dir=E:\DockerDesktop\app",
  "--wsl-default-data-root=E:\DockerDesktop\data",
  "--no-windows-containers"
)
```

首次启动后检查：

- General：启用 `Use WSL 2 based engine`。
- Resources / WSL Integration：启用 Ubuntu。
- Resources：保持 Resource Saver 开启。
- Disk image/data location：确认位于 `E:\DockerDesktop\data`。

## 4. 验证

```powershell
wsl --version
wsl -l -v
docker version
docker compose version
docker run --rm hello-world
```

将以上命令的普通输出发给开发线程即可；不要发送 `.env`、API Key、Docker 凭据或平台密钥。

## 5. 启动项目容器

在项目根目录执行：

```powershell
docker compose -f deploy\compose.yaml build
docker compose -f deploy\compose.yaml up -d
docker compose -f deploy\compose.yaml ps
```

前端地址为 `http://127.0.0.1:5173`，后端健康检查为
`http://127.0.0.1:8000/healthz`，就绪检查为 `http://127.0.0.1:8000/readyz`。

业务 SQLite 与 LangGraph Checkpoint 保存在 Compose 命名卷 `deploy_runtime-data` 中，
重新构建或强制重建容器后仍会保留。普通停止可使用：

```powershell
docker compose -f deploy\compose.yaml down
```

除非明确需要清空全部本地研判数据，不要添加 `-v` 参数。
