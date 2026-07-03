# Agent Task Platform 启动说明

当前开发方式：

- `MySQL` 和 `Redis` 运行在 Docker 容器里
- `Go` 后端和 `Python gRPC` 服务直接跑在本机（macOS）
- 前端如果要跑，也先运行在本机

## 当前端口

- Go HTTP API：`127.0.0.1:8080`
- MySQL：`127.0.0.1:3307`
- Redis：`127.0.0.1:6379`
- Python gRPC：`127.0.0.1:50051`
- 前端 Vite：`127.0.0.1:5173`

配置文件：

- Go：`configs/config.yaml`（模板 `configs/config.example.yaml`）
- Python：`python-agent/config.yaml`（模板 `python-agent/config.example.yaml`）

两者都已 gitignore，首次使用先从模板复制。

## 最简启动步骤

项目根目录为 `~/Desktop/Project/agent_task_platform`，下面命令均从根目录开始。

### 1. 启动 MySQL / Redis 容器

```bash
cd deployments
docker compose up -d mysql redis
docker compose ps          # 查看状态，确认 running
```

说明：`up -d` 是启动，`ps` 只是查看。两个容器配置了 `restart: unless-stopped`，Docker Desktop 启动后它们通常会自动恢复。不想让它们长期挂着就 `docker compose stop mysql redis`，下次 `start` 恢复。

### 2. 启动 Go 后端

```bash
make run-go
# 等价于: go run ./cmd/server --conf configs/config.yaml
```

### 3. 启动 Python gRPC 服务（另开一个终端）

首次需要准备环境：

```bash
cd python-agent
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
cp config.example.yaml config.yaml
```

之后每次启动（回到仓库根目录）：

```bash
make run-python
# 等价于: cd python-agent && .venv/bin/python main.py --conf config.yaml
```

### 4. 启动前端（可选，另开一个终端）

```bash
cd ../agent-task-platform-fronted
npm install     # 首次
npm run dev     # http://127.0.0.1:5173, /api/* 代理到 127.0.0.1:8080
```

注意：Go、Python、前端要分别在不同终端里跑，都会占住当前窗口。

## proto 代码生成

proto 唯一真源在 `api/proto/v1/provider/provider.proto`，改动后执行：

```bash
make proto     # 同时生成 Go(api/go/) 与 Python(python-agent/api/) 代码
```

依赖：`protoc` + `protoc-gen-go` + `protoc-gen-go-grpc`（Go 侧），python-agent 的 venv 里装有 `grpcio-tools`（Python 侧）。生成物提交进 git，日常构建不需要 protoc。

## 容器管理常用命令

```bash
cd deployments
docker compose up -d mysql redis    # 第一次创建并启动
docker compose stop mysql redis     # 暂停
docker compose start mysql redis    # 恢复
docker compose down                 # 删除容器和网络
```

平时更推荐 stop/start，比反复 down/up 省事。

## Redis 端口冲突说明

如果本机已有别的 Redis/Valkey 占着 `6379`（比如其他项目的容器），compose 里的 redis 会启动失败。二选一：

- 停掉占用者，用本项目的 redis 容器
- 直接复用已有的 Redis（它兼容即可），不启动本项目的 redis 服务

检查端口占用：

```bash
lsof -i :6379
docker ps --format "{{.Names}}\t{{.Ports}}" | grep 6379
```

## 启动成功后应该看到什么

- `docker compose ps`：`agent-task-platform-mysql`（和 redis）状态 running
- Go 后端：`curl http://127.0.0.1:8080/healthz` 正常返回
- Python gRPC：日志出现 `gRPC server listening on 0.0.0.0:50051`

检查端口监听：

```bash
lsof -iTCP:3307,6379,8080,50051 -sTCP:LISTEN
```

## 当前模型相关接口

- `POST /model-configs`
- `GET /model-configs`
- `GET /model-configs/:id`
- `PUT /model-configs/:id`
- `DELETE /model-configs/:id`
- `POST /provider-credentials/test`（这条会走 gRPC 打到 python-agent）

这些接口都需要登录后的 `Bearer Token`。
