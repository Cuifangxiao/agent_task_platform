# Agent Task Platform 启动说明

这份文档按当前更适合的开发方式来写：

- `MySQL` 和 `Redis` 运行在 Docker 容器里
- `Go` 后端运行在 Windows
- `Python gRPC` 服务运行在 Windows
- 前端如果要跑，也先运行在 Windows

也就是说，当前阶段不要求把 Go / Python 都迁到 WSL。  
你现在主要要保证的是：`mysql` 和 `redis` 这两个容器先起来。

## 当前端口

项目当前默认使用下面这组端口：

- Go HTTP API：`127.0.0.1:8080`
- MySQL：`127.0.0.1:3307`
- Redis：`127.0.0.1:6379`
- Python gRPC：`127.0.0.1:50051`
- 前端 Vite：`127.0.0.1:5173`

后端配置文件在：

- `F:\Go_Projects\agent-task-platform\configs\config.yaml`

## 现在推荐的运行方式

当前推荐分成两部分：

1. 用 Docker 启动 `mysql` 和 `redis`
2. 用 Windows 本机启动 `Go` 和 `Python`

这样做的好处是：

- 数据库和缓存环境固定
- Windows 上调试 Go / Python 更直接
- 等功能稳定后，再整体迁到 WSL 或打包部署

## Docker 这两个命令是什么意思

常用的是这两条：

```bash
docker compose up -d mysql redis
docker compose ps
```

它们不是一回事：

- `docker compose up -d mysql redis`
  这条才是“启动”
  会把 `docker-compose.yml` 里的 `mysql` 和 `redis` 两个服务在后台拉起来

- `docker compose ps`
  这条不是启动
  它只是“查看状态”
  用来确认容器是否已经运行、容器名是什么、端口映射是否正确

所以可以把它理解成：

- `up -d` = 启动
- `ps` = 查看

## 会不会自动启动

这个问题要分开看。

### 1. WSL 会不会因为这两个命令自动常驻

不会。

`docker compose up` 不会把 Ubuntu 变成一个“永远前台运行的开发环境”。  
WSL 本身通常还是按需唤起。

### 2. Docker 容器会不会自动恢复

有可能。

当前 `deployments/docker-compose.yml` 里，`mysql` 和 `redis` 都配置了：

```yaml
restart: unless-stopped
```

它的含义是：

- 只要 Docker 引擎启动了
- 而且你之前没有手动把容器停成“不要恢复”
- 那么这些容器通常会自动恢复运行

所以更准确地说：

- 不是 “WSL 自动启动了 mysql/redis”
- 而是 “Docker Desktop 启动后，容器可能自动恢复”

如果你不想让它们长期挂着，开发完可以手动停：

```bash
docker compose stop mysql redis
```

下次再恢复：

```bash
docker compose start mysql redis
```

## 你当前最简的开发模式

你现在的目标可以直接简化成这句话：

> 只让 Docker 提供 MySQL 和 Redis，Go / Python 继续跑在 Windows。

这时连接关系就是：

- Go 连 `127.0.0.1:3307` 和 `127.0.0.1:6379`
- Python 如果需要 Redis / MySQL，也同样连宿主机端口

这和当前 `config.yaml` 是一致的。

## 最简 6 条命令

如果你现在只想按“Docker 起 mysql/redis，Windows 跑 go/python”这条路径工作，最常用的就是下面这 6 条命令。

### 1. 启动 MySQL / Redis 容器

在 WSL 终端或 PowerShell 中都可以执行：

```bash
cd /mnt/f/Go_Projects/agent-task-platform/deployments
docker compose up -d mysql redis
docker compose ps
```

如果你在 Windows PowerShell 里执行，对应写法是：

```powershell
cd F:\Go_Projects\agent-task-platform\deployments
docker compose up -d mysql redis
docker compose ps
```

### 2. 启动 Go 后端

在 Windows PowerShell 中：

```powershell
cd F:\Go_Projects\agent-task-platform
go run ./cmd/server -conf ./configs/config.yaml
```

### 3. 启动 Python gRPC 服务

在另一个 Windows PowerShell 中：

```powershell
cd F:\Go_Projects\agent-task-platform\python-agent
python main.py
```

### 4. 启动前端

在另一个 Windows PowerShell 中：

```powershell
cd F:\Go_Projects\agent-task-platform-fronted
npm install
npm run dev
```

如果依赖已经装过，后面通常直接执行：

```powershell
cd F:\Go_Projects\agent-task-platform-fronted
npm run dev
```

默认前端地址：

```text
http://127.0.0.1:5173
```

前端会把 `/api/*` 代理到：

```text
http://127.0.0.1:8080
```

如果把上面合并成你平时最常复制的 6 条命令，就是：

```text
cd F:\Go_Projects\agent-task-platform\deployments
docker compose up -d mysql redis
docker compose ps
cd F:\Go_Projects\agent-task-platform
go run ./cmd/server -conf ./configs/config.yaml
cd F:\Go_Projects\agent-task-platform\python-agent
python main.py
```

注意：

- `go run ...` 和 `python main.py` 需要分别在不同终端里执行
- 上面这 6 条命令里，最后两块不能在同一个终端里串着跑到底，因为 Go 服务启动后会占住当前窗口

## 平时最实用的命令

### 第一次创建并启动容器

```powershell
cd F:\Go_Projects\agent-task-platform\deployments
docker compose up -d mysql redis
docker compose ps
```

### 暂停容器

```powershell
cd F:\Go_Projects\agent-task-platform\deployments
docker compose stop mysql redis
```

### 恢复容器

```powershell
cd F:\Go_Projects\agent-task-platform\deployments
docker compose start mysql redis
docker compose ps
```

### 删除 compose 创建的容器和网络

```powershell
cd F:\Go_Projects\agent-task-platform\deployments
docker compose down
```

一般开发时更推荐：

- 第一次用 `up -d`
- 平时用 `stop`
- 下次用 `start`

这样比反复 `down` / `up` 更省事。

## Redis 端口冲突说明

如果 Windows 上还有一个本地 Redis 服务占着 `6379`，那么 Docker 里的 Redis 容器会启动失败。

当前这台机器之前存在一个 Windows 服务：

- 服务名：`Redis`
- 默认端口：`6379`

如果你决定“以后开发时只用 Docker 里的 Redis”，那就应该停掉 Windows 上那个 Redis 服务，避免冲突。

管理员 PowerShell 里可以这样处理：

```powershell
Set-Service -Name Redis -StartupType Manual
Stop-Service -Name Redis -Force
```

如果你想彻底禁用开机自启：

```powershell
Set-Service -Name Redis -StartupType Disabled
```

说明：

- `Manual`：不开机自动启动，但仍然可以手动启动
- `Disabled`：完全禁用，之后要先改回来才能启动

## 启动成功后应该看到什么

### Docker

执行：

```powershell
docker compose ps
```

正常应该能看到：

- `agent-task-platform-mysql`
- `agent-task-platform-redis`

并且状态通常是 `running`

### Go 后端

启动后应该能访问：

```text
http://127.0.0.1:8080/healthz
```

### Python gRPC

启动后应该监听：

```text
127.0.0.1:50051
```

## 常用检查命令

检查容器状态：

```powershell
cd F:\Go_Projects\agent-task-platform\deployments
docker compose ps
```

检查端口是否被监听：

```powershell
Get-NetTCPConnection -LocalPort 3307,6379,8080,50051 -State Listen
```

检查健康接口：

```powershell
curl http://127.0.0.1:8080/healthz
```

## 当前模型相关接口

当前后端已经有这些 model config 接口：

- `POST /model-configs`
- `GET /model-configs`
- `GET /model-configs/:id`
- `PUT /model-configs/:id`
- `DELETE /model-configs/:id`
- `POST /provider-credentials/test`

这些接口都需要登录后的 `Bearer Token`。

## 备注

当前项目代码在 Windows 盘：

- `F:\Go_Projects\agent-task-platform`

这对你现在这种“Windows 跑 Go/Python，Docker 提供 MySQL/Redis”的方式是最省事的。  
后面如果要切到 WSL 全开发，再考虑把项目整体迁过去就行。
