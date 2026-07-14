# PPT 生成流水线

当前实现是一条不中断的生成流水线：HTTP 负责创建任务和订阅事件，Go 负责鉴权、模型配置解析、任务内存状态和 SSE，Python Runtime 负责完整生成，并通过 gRPC 服务端流持续返回事件。

```text
Web
  ├─ POST /api/v1/ppt/tasks                 创建任务
  └─ GET  /api/v1/ppt/tasks/{task_id}/events 订阅 SSE
            │
            ▼
Go API：解析 5 个模型节点，创建内存任务，启动 goroutine
            │ GeneratePpt(request) returns (stream AgentEvent)
            ▼
Python Runtime：brief → outline → style → design_lock → write_slides → export
            │ AgentEvent
            ▼
Go TaskHub：更新任务快照、缓存事件、广播 SSE
            │
            └─ TASK_COMPLETED + PPTX 资源，随后 gRPC 流结束
```

这版没有人工确认、暂停/恢复、审阅循环和多 Worker 调度。任一进程在生成中退出，本次任务即失败；客户端重新创建任务即可。

## 1. 一次请求如何流转

1. 客户端使用已登录用户的生成描述、附件和 PPT 选项调用 `POST /api/v1/ppt/tasks`；可以显式指定 `agent_config_id`，省略时 Go 选择当前用户默认或首个可用的 PPT Agent 配置。
2. Go 校验 Agent 配置，并解析 `brief`、`outline`、`style`、`writer`、`designer` 五个节点绑定的模型配置。缺失或停用的配置在任务启动前直接返回 HTTP 错误。
3. Go 创建进程内任务和事件缓冲，立即返回 `task_id`、任务快照及 `events_url`，同时在独立 goroutine 中调用 Python `GeneratePpt`。
4. Python 按固定顺序完成本地 PPT 流水线。节点只处理领域状态；对外进度由统一 `AgentEvent` 表示。
5. Go 每收到一个 gRPC 事件，就更新任务快照并广播到订阅该任务的 SSE 客户端。稍晚连接的客户端先收到内存中已缓存的事件，再接收新事件。
6. Python 成功时发送 `TASK_COMPLETED`，其中 `resources` 和 `final_result.ppt_file` 指向最终 PPTX 的相对路径；失败时发送 `TASK_FAILED`。终态事件之后流结束。
7. 客户端通过任务文件或产物路由下载文件。Go 根据相对路径访问共享产物目录，并在返回文件前校验任务所有者和路径边界。

创建任务与订阅事件是两个请求。SSE 断开只会失去这个订阅，不会取消已经在 Go goroutine 中运行的生成任务；需要停止生成时显式调用取消接口。

## 2. HTTP 接口

所有接口都位于 `/api/v1` 且需要登录：

| 方法 | 路径 | 用途 |
|---|---|---|
| `POST` | `/ppt/tasks` | 创建 PPT 任务，返回任务快照和事件地址 |
| `GET` | `/ppt/tasks/{task_id}` | 读取当前进程中的任务快照 |
| `GET` | `/ppt/tasks/{task_id}/events` | 订阅 SSE；可用 `after` 跳过已消费序号，先重放其后的缓存事件，再推送实时事件 |
| `POST` | `/ppt/tasks/{task_id}/cancel` | 取消任务，并取消对应的 gRPC context |
| `GET` | `/ppt/tasks/{task_id}/file` | 下载最终 PPTX |
| `GET` | `/ppt/tasks/{task_id}/artifacts/*asset_path` | 下载该任务目录内的其他产物 |

任务、任务快照和事件都只存在当前 Go 进程内。因此：

- API 重启后，旧 `task_id` 不可查询，也不能继续订阅或下载；
- 多个 API 实例不会共享任务，第一版不应在无会话粘性的情况下横向扩容；
- SSE 的事件缓存用于弥补“创建任务后稍晚订阅”的时间差，不是持久化日志；
- 当前实现将已结束任务在内存中保留约一小时并惰性清理，不能把这个窗口当成持久化保证；
- `sequence` 只是单个任务内的递增排序游标，不是协议版本号。

## 3. gRPC 契约与事件

协议真源是 `contracts/proto/ppt/ppt_agent.proto`：

```proto
rpc GeneratePpt(GeneratePptRequest) returns (stream AgentEvent);
```

Go 只发送一次请求，Python 单向连续返回事件，因此无需双向流。请求包含任务描述、内联附件、五个模型节点配置和 PPT 选项；不包含 checkpoint、当前阶段、恢复载荷或历史事件。

### 事件类型

| `EventType` | 发送时机 | 前端处理 |
|---|---|---|
| `TASK_STARTED` | Python 接受任务并开始准备 | 初始化运行状态 |
| `STEP_STARTED` | 一个可展示阶段开始 | 更新阶段和提示文案 |
| `STEP_PROGRESS` | 页、资源或步骤产生进度 | 更新 `current/total/percent/unit`，可同时展示页信息 |
| `STEP_COMPLETED` | 一个可展示阶段完成 | 标记该阶段完成 |
| `ARTIFACT_READY` | SVG、HTML、预览图等中间产物可用 | 从 `resources` 读取可选产物 |
| `TASK_COMPLETED` | PPTX 已写入共享目录 | 读取 `final_result`，结束订阅 |
| `TASK_FAILED` | 流开始后的生成错误或连接中断 | 展示结构化错误，结束订阅 |

### 稳定阶段

| `PptStage` | 用户可见含义 | 可能覆盖的内部工作 |
|---|---|---|
| `PREPARING` | 正在准备输入 | 校验请求、保存附件、初始化项目目录 |
| `PLANNING` | 正在规划演示文稿 | brief、outline、style |
| `GENERATING_CONTENT` | 正在生成内容 | writer 生成逐页结构化内容 |
| `GENERATING_ASSETS` | 正在准备视觉资产 | 图片和页面依赖资源 |
| `GENERATING_PAGES` | 正在生成页面 | designer 逐页生成 SVG/HTML |
| `RENDERING` | 正在渲染预览 | 本地页面渲染和预览产物 |
| `ASSEMBLING_PPT` | 正在组装 PowerPoint | 本地 PPTX 导出 |
| `FINISHED` | 已结束 | 成功或失败终态 |

阶段是前端稳定契约，不应直接等同于 Python 函数名。以后可以在不改变前端阶段的前提下拆分内部实现。

每个事件都带 `task_id`、`sequence`、`created_at`、`type`、`stage` 和面向用户的 `message`。按需再带 `progress`、`resources`，以及 `page`、`error`、`final_result` 中的一种结构化详情。资源只传任务目录下的 `relative_path`，Python 不生成 HTTP URL；Go 在 HTTP 边界补全受鉴权保护的下载 URL。

## 4. Python 流水线

Python 保留现有的本地生成能力。数据库仍要求五个模型角色，但模型角色并不与 LangGraph 节点一一对应；实际图由 `build_ppt_graph` 每次全新编译：

```text
brief
  → outline
  → style
  → design_lock
  → write_slides（逐页调用 writer，再调用 designer，并渲染预览）
  → export（组装 PPTX）
```

`brief`、`outline`、`style` 分别使用同名模型配置；`design_lock` 固化设计契约；`write_slides` 在一个逐页节点内同时使用 `writer` 与 `designer`。因此外部配置始终只有 `brief`、`outline`、`style`、`writer`、`designer` 五项，而内部仍可保留不需要独立模型绑定的确定性节点。

不再执行以下节点或分支：

- outline/style 的人工 review 与 resume；
- deterministic review、VLM review、coherence review；
- repair/regenerate 循环；
- LangGraph checkpointer 和 checkpoint 数据库；
- `needs_review`、review action、页版本或 artifact 版本语义。

生成状态只保存本次执行需要的输入、brief、outline、style、逐页内容、资源和最终文件。gRPC context、模型客户端、数据库连接、事件历史与大块二进制文件不进入流水线状态。

模型配置仍保存在 MySQL。数据库中的 PPT Agent 节点 schema 只有 `brief`、`outline`、`style`、`writer`、`designer`；迁移会清理该 Agent 下不属于这五个节点的旧绑定，并删除已退出架构的 PPT conversation、job、event、action、source 和版本化 asset 表。这里所说的“无数据库”仅指 PPT 任务、事件、checkpoint 和中间状态不落库，不是移除用户、登录或模型配置数据库。

## 5. 失败与取消语义

失败分成三个边界：

| 边界 | 行为 |
|---|---|
| 任务尚未创建 | Go 参数、权限或模型绑定校验失败，返回普通 HTTP 错误，不创建内存任务 |
| gRPC 流尚未开始 | Python 以标准 gRPC status 拒绝请求；Go 将任务置为失败并广播 `TASK_FAILED` |
| gRPC 流已经开始 | Python 尽量先发送一个 `TASK_FAILED`，再正常结束流 |

如果 Go 在未收到终态事件时遇到 gRPC 异常或提前 EOF，会补发：

```json
{
  "type": "TASK_FAILED",
  "stage": "GENERATING_CONTENT",
  "message": "PPT generation service connection was interrupted",
  "error": {
    "code": "PYTHON_STREAM_LOST",
    "retryable": true
  }
}
```

失败事件保留中断时的当前阶段，便于定位故障发生在哪里；示例中的 `GENERATING_CONTENT` 仅代表一种可能。

取消接口会取消 Go 持有的 task context，从而取消 Python gRPC 调用，并让任务进入终态。取消不是暂停：同一任务不能继续执行，用户需要重新创建任务。

服务崩溃也没有恢复语义。不要通过伪造 `sequence`、阶段或中间文件来尝试续跑；重新提交完整请求是唯一恢复方式。

## 6. 共享产物目录

Python 和 Go 必须能访问同一个物理目录：

```text
Python: DECK_ARTIFACTS_DIR/{task_id}/...
Go:     python_agent.artifacts_dir/{task_id}/...
```

本地默认值是仓库中的 `apps/runtime/artifacts/decks`。Runtime 应从 `apps/runtime` 启动；API 从仓库根目录启动时，默认 `python_agent.artifacts_dir` 与它指向同一位置。

部署约束：

- 两个进程在不同容器时，给两个路径挂载同一个共享卷；
- 两个进程在不同主机且没有共享文件系统时，这版产物下载不可用；
- Python 事件只允许返回相对任务目录的路径，不能泄露主机绝对路径；
- `task_id` 必须作为安全的单个目录名使用；
- Go 下载前必须清理并解析路径，确认最终绝对路径仍位于 `artifacts_dir/{task_id}` 内；
- 产物 URL 必须绑定任务所有者，不能凭路径跨任务或跨用户读取。

本地生成仍会产出 SVG、HTML/预览资源和最终 PPTX；文件布局以 `apps/runtime/core/deck/paths.py` 定义为准，HTTP 层不依赖某一套硬编码的中间目录结构。

## 7. 代码导航

| 层 | 文件 | 职责 |
|---|---|---|
| 协议 | `contracts/proto/ppt/ppt_agent.proto` | 独立 PPT gRPC 服务、请求、事件和资源契约 |
| Go 领域类型 | `apps/api/internal/modules/pptagent/types.go` | 任务、事件、资源、Runtime port |
| Go 内存状态 | `apps/api/internal/modules/pptagent/hub.go` | 任务快照、事件重放、订阅广播、终态 |
| Go 编排 | `apps/api/internal/modules/pptagent/service.go` | 创建/取消任务、接收 gRPC 流、失败收口 |
| Go 模型解析 | `apps/api/internal/modules/pptagent/model_resolution.go` | 从 Agent 配置解析五个模型节点 |
| Go HTTP | `apps/api/internal/modules/pptagent/handler.go` | 任务 HTTP API 与 SSE |
| Go 产物 | `apps/api/internal/modules/pptagent/artifact.go` | 任务目录约束和文件下载 |
| Go gRPC adapter | `apps/api/internal/platform/pythonruntime/client.go` | 领域请求/事件与 protobuf 的转换 |
| 路由 | `apps/api/internal/transport/http/router/router.go` | 注册 `/api/v1/ppt/tasks` 路由 |
| Python gRPC | `apps/runtime/server/grpc.py` | 注册服务、gRPC status 与流边界 |
| Python handler | `apps/runtime/handler/ppt_handler.py` | protobuf 与 core contract 的转换 |
| Python 领域入口 | `apps/runtime/core/deck/service.py` | 运行一次完整生成并输出领域事件 |
| Python 流水线 | `apps/runtime/core/deck/pipeline/__init__.py` | 组装无 review/checkpoint 的线性生成流程 |
| Python 产物路径 | `apps/runtime/core/deck/paths.py` | 定义 task-local 项目目录和导出路径 |

## 8. 本地启动与验证

PPT 任务本身不落库，但登录和模型配置仍依赖 MySQL/Redis。第一次运行先准备基础设施和配置：

```powershell
docker compose -f deploy/docker-compose.yml up -d mysql redis

Copy-Item apps/api/config/config.example.yaml apps/api/config/config.yaml
Copy-Item apps/runtime/config.example.yaml apps/runtime/config.yaml
Copy-Item apps/runtime/.env.example apps/runtime/.env

py -3.11 -m venv apps/runtime/.venv
& apps/runtime/.venv/Scripts/python.exe -m pip install -r apps/runtime/requirements.txt -r apps/runtime/requirements-dev.txt
& apps/runtime/.venv/Scripts/python.exe -m playwright install chromium
npm --prefix apps/web install
```

如果修改了 PPT proto，从支持 GNU Make 的 Git Bash/WSL 执行 `make proto`；它会同时更新 Go 与 Python 生成代码。然后在三个终端启动：

```powershell
# 终端 1
Set-Location apps/runtime
& ./.venv/Scripts/python.exe main.py --conf config.yaml
```

```powershell
# 终端 2：仓库根目录
go run ./apps/api/cmd/server --conf apps/api/config/config.yaml
```

```powershell
# 终端 3：仓库根目录
npm --prefix apps/web run dev
```

先验证 API 健康状态：

```powershell
curl.exe http://127.0.0.1:8080/api/v1/health
```

登录并创建好包含五个模型绑定的 PPT Agent 配置后，可用访问令牌走完整接口：

```powershell
$headers = @{ Authorization = "Bearer $env:ACCESS_TOKEN" }
$body = @{
  agent_config_id = 1
  description = "生成一份介绍 Agent Task Platform 架构的演示文稿"
  options = @{
    language = "zh-CN"
    expected_slide_count = 8
    aspect_ratio = "16:9"
  }
} | ConvertTo-Json -Depth 5

$created = Invoke-RestMethod -Method Post `
  -Uri http://127.0.0.1:8080/api/v1/ppt/tasks `
  -Headers $headers -ContentType application/json -Body $body
```

响应使用项目统一 envelope；从其中取出 `task_id` 后订阅返回的 `events_url`。SSE 应从 `TASK_STARTED` 开始，并且只以 `TASK_COMPLETED` 或 `TASK_FAILED` 结束。成功事件中的 PPTX URL 应能在同一用户身份下下载。

提交前运行：

```powershell
make test-go

Push-Location apps/runtime
& ./.venv/Scripts/python.exe -m pytest ../../tests/runtime -q
Pop-Location

npm --prefix apps/web test
npm --prefix apps/web run build
```
