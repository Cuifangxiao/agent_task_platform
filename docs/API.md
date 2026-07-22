# Agent Task Platform 接口文档

本文档描述当前代码实际提供的公开 HTTP API 和 Go API 与 Python Runtime 之间的内部 gRPC 协议。新客户端只应使用 canonical 前缀：

```text
http://<api-host>:<port>/api/v1
```

除进程探针 `GET /healthz` 外，业务接口全部位于 `/api/v1`。PPT 使用 `/api/v1/ppt/tasks` HTTP 接口和 `/api/v1/ppt/ws` WebSocket；旧 conversations/jobs/actions/generate/resume 路由已经删除。

## 1. 通用约定

### 1.1 Content-Type 与时间

- 普通请求/响应：`application/json; charset=utf-8`。
- 文件上传：`multipart/form-data`。
- 文本生成流：`text/event-stream`；PPT 实时通道使用 WebSocket JSON text frame。
- Artifact 下载：按文件返回二进制，不套 JSON envelope。
- 所有时间字符串均为 RFC 3339；Go DTO 中的 `time.Time` 也按 RFC 3339 JSON 格式序列化。

### 1.2 成功响应

除 WebSocket upgrade、SSE 和文件下载外，所有 `/api/v1` 成功响应都使用：

```json
{
  "data": {},
  "meta": {
    "request_id": "d9cf50dce0e546414a63bcf4ed05b1c7"
  }
}
```

`data` 的实际类型由接口定义，可以是对象、数组或标量。创建资源通常返回 `201 Created`，PPT Task 创建返回 `202 Accepted`，其余成功请求返回 `200 OK`。

### 1.3 错误响应

HTTP `/api/v1` 错误（包括 WebSocket upgrade 之前的失败）统一为：

```json
{
  "error": {
    "code": "invalid_request",
    "message": "invalid request",
    "details": [
      { "field": "username", "reason": "min=3" }
    ],
    "request_id": "d9cf50dce0e546414a63bcf4ed05b1c7"
  }
}
```

`details` 仅在绑定/校验错误时出现。当前字段名来自 Go 请求结构字段转为小写，不承诺一定是 snake_case；客户端应以 `code` 和 `message` 为主。

| HTTP 状态 | `error.code` | 含义 |
| --- | --- | --- |
| 400 | `invalid_request` | JSON/路径/查询参数无效，或业务输入不满足约束 |
| 401 | `unauthorized` | 缺少、格式错误、过期或失效的 access token/session |
| 404 | `not_found` | 路由或当前用户可见范围内的资源不存在 |
| 405 | `method_not_allowed` | 路径存在，但 HTTP 方法不支持 |
| 409 | `conflict` | 通用资源冲突，例如名称重复或对终态 Task 取消 |
| 409 | `client_request_conflict` | PPT `client_request_id` 已用于不同请求 |
| 409 | `thread_run_conflict` | PPT thread 已有未退出的生成 worker |
| 409 | `run_not_ready` | PPT Run 已创建但本地 worker 正在完成初始化；可重试 |
| 413 | `payload_too_large` | PPT request body、单文件或文件总量超限 |
| 415 | `unsupported_media_type` | PPT 源文件扩展名不受支持 |
| 500 | `internal_error` | 服务内部错误或依赖不可用 |

### 1.4 Request ID

每个请求都会返回 header：

```http
X-Request-ID: d9cf50dce0e546414a63bcf4ed05b1c7
```

调用方可以传入 `X-Request-ID`。服务仅接受 1–128 个字符，字符集为字母、数字、`-`、`_`、`.`；空值、超长或含其他字符时会生成新的 32 位十六进制 ID。普通 `/api/v1` JSON 响应中的 `meta.request_id` 或 `error.request_id` 与 header 相同。

### 1.5 鉴权与会话

除健康检查、注册/登录/刷新和 WebSocket upgrade 外，canonical HTTP 路由都要求：

```http
Authorization: Bearer <access_token>
```

服务会同时验证：

1. JWT 签名、有效期和 token type 必须是 `access`；
2. access token 的 JTI 不在 Redis 黑名单；
3. Redis 中同一 `user_id + device` 的 session 仍为 active；
4. JWT 中的 `session_id` 与 Redis session 一致。

`GET /api/v1/ppt/ws` 使用 WebSocket subprotocol 携带同一 access token，验证项目不变；详见 10.1。

同一用户在同一 `device` 再次登录会用新 session 覆盖该设备原 session。refresh token 不放在 Authorization header，而是放入刷新接口 JSON body。

## 2. Canonical 路由总览

| 方法 | 路径 | 鉴权 | 成功响应 `data` |
| --- | --- | --- | --- |
| GET | `/api/v1/health` | 否 | `{status}` |
| POST | `/api/v1/auth/register` | 否 | `RegisterResponse` |
| POST | `/api/v1/auth/login` | 否 | `AuthResponse` |
| POST | `/api/v1/auth/refresh` | 否 | `AuthResponse` |
| GET | `/api/v1/auth/me` | 是 | `CurrentUser` |
| POST | `/api/v1/auth/logout` | 是 | `LogoutResponse` |
| GET | `/api/v1/providers` | 是 | `ProviderList` |
| POST | `/api/v1/providers/credential-tests` | 是 | `CredentialTest` |
| GET | `/api/v1/model-configs` | 是 | `ModelConfig[]` |
| POST | `/api/v1/model-configs` | 是 | `ModelConfig` |
| GET | `/api/v1/model-configs/:id` | 是 | `ModelConfig` |
| PATCH | `/api/v1/model-configs/:id` | 是 | `ModelConfig` |
| DELETE | `/api/v1/model-configs/:id` | 是 | `{message}` |
| GET | `/api/v1/agents` | 是 | `AgentDefinition[]` |
| GET | `/api/v1/agents/:id` | 是 | `AgentDefinition` |
| GET | `/api/v1/agent-configs` | 是 | `AgentConfig[]` |
| POST | `/api/v1/agent-configs` | 是 | `AgentConfig` |
| GET | `/api/v1/agent-configs/:id` | 是 | `AgentConfig` |
| PATCH | `/api/v1/agent-configs/:id` | 是 | `AgentConfig` |
| DELETE | `/api/v1/agent-configs/:id` | 是 | `{message}` |
| POST | `/api/v1/text-generations` | 是 | 文本 SSE |
| POST | `/api/v1/ppt/tasks` | 是 | `PptTask`（202） |
| GET | `/api/v1/ppt/tasks` | 是 | `PptTask[]` |
| GET | `/api/v1/ppt/tasks/:task_id` | 是 | `PptTask` |
| POST | `/api/v1/ppt/tasks/:task_id/cancel` | 是 | `PptTask` |
| GET | `/api/v1/ppt/tasks/:task_id/file` | 是 | PPTX 文件 |
| GET | `/api/v1/ppt/tasks/:task_id/artifacts/*asset_path` | 是 | 指定任务产物 |
| GET | `/api/v1/ppt/ws` | 是（WS subprotocol） | WebSocket upgrade |

## 3. 健康检查

### `GET /api/v1/health`

无需鉴权。

```json
{
  "data": { "status": "ok" },
  "meta": { "request_id": "..." }
}
```

该接口只表示 HTTP 进程可响应，不逐项探测 MySQL、Redis、Runtime 或外部模型。

## 4. 认证接口

### 4.1 注册

`POST /api/v1/auth/register`

| 字段 | 类型 | 必填 | 约束 |
| --- | --- | --- | --- |
| `username` | string | 是 | 3–32 字符；trim 后不能为空 |
| `password` | string | 是 | 6–72 字符；保存前使用 bcrypt |

请求：

```json
{
  "username": "alice",
  "password": "change-me"
}
```

成功：`201 Created`

```json
{
  "data": { "message": "register ok" },
  "meta": { "request_id": "..." }
}
```

用户名已存在返回 `409 conflict`。

### 4.2 登录

`POST /api/v1/auth/login`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `username` | string | 是 | trim 后匹配用户名 |
| `password` | string | 是 | trim 后校验 |
| `device` | string | 否 | 默认 `web`，超过 64 字符会截断 |

成功 `data`：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `message` | string | `login ok` |
| `token` | string | access token 的兼容别名，与 `access_token` 相同 |
| `access_token` | string | 用于 Bearer 鉴权 |
| `refresh_token` | string | 用于刷新接口 |
| `token_type` | string | `Bearer` |
| `device` | string | 规范化后的设备名 |
| `session_id` | string | 当前 Redis session ID |
| `access_expires_at` | RFC3339 string | access token 过期时间 |
| `refresh_expires_at` | RFC3339 string | refresh token/session 过期时间 |

用户名或密码错误返回 `401 unauthorized`。

### 4.3 刷新 token

`POST /api/v1/auth/refresh`

```json
{
  "refresh_token": "<refresh JWT>"
}
```

返回与登录相同的 `AuthResponse`，`message` 为 `refresh ok`。刷新会签发新的 access/refresh token，并更新同一 session；旧 refresh token 随即不再匹配 Redis 中保存的 token。

### 4.4 当前用户

`GET /api/v1/auth/me`

```json
{
  "data": {
    "user_id": 12,
    "username": "alice",
    "device": "web",
    "session_id": "c8c8..."
  },
  "meta": { "request_id": "..." }
}
```

### 4.5 退出

`POST /api/v1/auth/logout`

无 body。服务删除 Redis session，并将当前 access token JTI 加入黑名单直到其自然过期。

```json
{
  "data": { "message": "logout ok" },
  "meta": { "request_id": "..." }
}
```

## 5. Provider 接口

### 5.1 Provider 与模型目录

`GET /api/v1/providers`

返回：

```json
{
  "data": {
    "providers": [
      {
        "name": "openai",
        "display_name": "OpenAI",
        "description": "...",
        "manager": "openai",
        "sdk": "langchain_openai",
        "default_base_url": "https://api.openai.com/v1",
        "supports_credential_test": true,
        "supports_custom_base_url": true,
        "models": [
          {
            "id": "gpt-4o-mini",
            "model_name": "gpt-4o-mini",
            "display_name": "GPT-4o mini",
            "type": "multimodal",
            "reasoning_supported": false,
            "parameters": [
              {
                "name": "temperature",
                "display_name": "Temperature",
                "type": "number",
                "description": "",
                "default_value": "1",
                "options": [],
                "required": false,
                "min": 0,
                "max": 2
              }
            ]
          }
        ]
      }
    ]
  },
  "meta": { "request_id": "..." }
}
```

目录来自 Runtime 的 `providers/catalog/*.yaml`，因此具体 Provider/模型列表以运行实例为准。`min`、`max` 只在目录声明时出现。

### 5.2 凭据测试

`POST /api/v1/providers/credential-tests`

| 字段 | 类型 | 必填 | 约束 |
| --- | --- | --- | --- |
| `provider` | string | 是 | 2–32 字符，转为小写 |
| `api_key` | string | 是 | 1–512 字符 |
| `base_url` | string | 否 | 最长 255；必须是有效 HTTP/HTTPS URL |

成功 HTTP 响应为 200；测试本身的结果由 `status` 表示：

```json
{
  "data": {
    "request_id": "runtime-generated-uuid",
    "provider": "openai",
    "status": "ok",
    "message": "credential test succeeded"
  },
  "meta": { "request_id": "http-request-id" }
}
```

Runtime 捕获 Provider 连接失败时也可能返回 HTTP 200、`status: "failed"` 与失败说明。这里的 `data.request_id` 是 Runtime 调用 ID，`meta.request_id` 是 HTTP 请求 ID。

## 6. 模型配置

### 6.1 `ModelConfig` 响应

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | integer | 配置 ID |
| `name` | string | 用户内唯一名称 |
| `provider` | string | Provider key |
| `model_name` | string | 目录中的模型名 |
| `model_type` | string | 能力分类：`text`、`multimodal` 或 `image` |
| `base_url` | string | 自定义地址或空字符串 |
| `api_key_masked` | string | 仅保留最后 4 位；长度 ≤4 时全部为 `*` |
| `has_api_key` | boolean | 是否保存了 API Key |
| `provider_options` | object | Provider 特有选项 |
| `temperature` | number | 0–2 |
| `max_tokens` | integer | 正整数 |
| `is_default` | boolean | 当前用户默认模型配置 |
| `status` | string | `active` 或 `inactive` |
| `created_at`, `updated_at` | RFC3339 string | 时间 |

原始 `api_key` 永远不会出现在响应中。

### 6.2 创建

`POST /api/v1/model-configs`

| 字段 | 类型 | 必填 | 默认/约束 |
| --- | --- | --- | --- |
| `name` | string | 是 | 2–64 字符，用户内唯一 |
| `provider` | string | 是 | 2–32 字符，必须存在于 Provider 目录 |
| `model_name` | string | 是 | 1–128 字符，必须属于该 Provider |
| `model_type` | string | 否 | `text`、`multimodal` 或 `image`；基础默认值为 `text`，最终以 Provider 目录中的能力类型为准 |
| `base_url` | string | 否 | 最长 255；有效 HTTP/HTTPS URL |
| `api_key` | string | 是 | 1–512 字符 |
| `provider_options` | object | 否 | 默认 `{}`；必须是 JSON object |
| `temperature` | number | 否 | 默认 `1.0`，范围 0–2 |
| `max_tokens` | integer | 否 | 默认 `4096`，必须 >0 |
| `is_default` | boolean | 否 | 默认 `false`；设为 true 会清除用户其他默认项 |
| `status` | string | 否 | 默认 `active`；可为 `active`/`inactive` |

创建前会从 Runtime 验证 Provider/模型，并执行凭据测试；凭据状态不是 `ok` 时返回 `400 invalid_request`，不会写入配置。模型能力分类以 Provider 目录为真源：显式传入的 `model_type` 若与目录冲突，同样返回 `400 invalid_request`。成功返回 `201` 和完整 `ModelConfig`。

为兼容旧 Provider 目录，旧目录中的 `type: chat` 会转换为能力分类：Qwen VL，以及名称包含 GPT-4o、GPT-4.1、GPT-5、Vision、Gemini、Claude 3/4 标记的模型映射为 `multimodal`，其余映射为 `text`。

### 6.3 列表与详情

- `GET /api/v1/model-configs`：返回当前用户的 `ModelConfig[]`，默认项优先，其次按更新时间倒序。
- `GET /api/v1/model-configs/:id`：只查询当前用户拥有的配置。

### 6.4 更新

`PATCH /api/v1/model-configs/:id`

body 中所有字段均可选：`name`、`provider`、`model_name`、`model_type`、`base_url`、`api_key`、`provider_options`、`temperature`、`max_tokens`、`is_default`、`status`。字段约束与创建相同；未传字段保持原值，但 `model_type` 会在每次更新时按当前 Provider 目录重新同步。不能用空字符串清除 API Key。

只有 `provider`、`api_key` 或 `base_url` 改变时才重新执行凭据测试；模型名与能力分类仍会在每次更新时对照目录验证，显式 `model_type` 与目录冲突时返回 `400 invalid_request`。

### 6.5 删除

`DELETE /api/v1/model-configs/:id`

```json
{
  "data": { "message": "model config deleted" },
  "meta": { "request_id": "..." }
}
```

## 7. Agent 定义与 Agent 配置

### 7.1 Agent 定义

- `GET /api/v1/agents`
- `GET /api/v1/agents/:id`

`AgentDefinition`：

```json
{
  "id": 1,
  "agent_key": "ppt_agent",
  "name": "PPTAgent",
  "description": "...",
  "nodes": [
    {
      "key": "brief",
      "display_name": "Brief Research",
      "description": "...",
      "requires_model": true
    }
  ],
  "status": "active",
  "created_at": "...",
  "updated_at": "..."
}
```

Agent 定义由迁移/系统维护，不是用户 CRUD 资源。当前 PPT Agent 的可配置模型节点只有 `brief`、`outline`、`style`、`writer`、`designer`；以接口返回的 `nodes` 为最终依据。

### 7.2 `AgentConfig` 响应

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | integer | 配置 ID |
| `agent_id` | integer | Agent 定义 ID |
| `agent_key`, `agent_name` | string | Agent 标识与名称 |
| `name` | string | 用户在该 Agent 下的唯一配置名 |
| `description` | string | 描述 |
| `is_default` | boolean | 当前用户在该 Agent 下的默认配置 |
| `status` | string | `active`/`inactive` |
| `node_bindings` | array | 节点到模型配置的绑定 |
| `created_at`, `updated_at` | RFC3339 string | 时间 |

每个 `node_bindings[]`：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `node_key` | string | 是 | Agent 返回的可配置节点 key |
| `model_config_id` | integer | 是 | 当前用户拥有的 active 文本或多模态模型配置；不接受 `model_type: image` |
| `temperature` | number | 否 | 节点覆盖值，0–2；省略则使用模型配置值 |
| `max_tokens` | integer | 否 | 节点覆盖值，必须 >0；省略则使用模型配置值 |

当前 Agent 节点运行链只支持文本模型和可接收图片输入的多模态模型。绑定生图模型（`model_type: image`）会返回 `400 invalid_request`，不会保存 Agent 配置。

### 7.3 创建

`POST /api/v1/agent-configs`

```json
{
  "agent_id": 1,
  "name": "默认 PPT 工作流",
  "description": "统一使用生产模型",
  "node_bindings": [
    { "node_key": "brief", "model_config_id": 10 },
    { "node_key": "outline", "model_config_id": 10 },
    { "node_key": "style", "model_config_id": 10 },
    { "node_key": "writer", "model_config_id": 10 },
    { "node_key": "designer", "model_config_id": 10 }
  ],
  "is_default": true,
  "status": "active"
}
```

| 字段 | 必填 | 约束 |
| --- | --- | --- |
| `agent_id` | 是 | 正整数；Agent 必须 active |
| `name` | 是 | 2–64 字符 |
| `description` | 否 | 最长 255 |
| `node_bindings` | 是 | 非空；每个 `requires_model` 节点恰好一个绑定，不允许重复/未知 key |
| `is_default` | 否 | 默认 false；默认配置必须同时为 active |
| `status` | 否 | 默认 `active`；`active`/`inactive` |

成功返回 `201`。同一用户、同一 Agent 下名称冲突返回 `409`。

### 7.4 列表、详情、更新与删除

- `GET /api/v1/agent-configs?agent_id=<positive-id>`：`agent_id` 可选；返回当前用户配置数组。
- `GET /api/v1/agent-configs/:id`：返回当前用户拥有的配置。
- `PATCH /api/v1/agent-configs/:id`：可选字段为 `name`、`description`、`node_bindings`、`is_default`、`status`；不能更换 `agent_id`。一旦提交 `node_bindings`，它会整体替换旧绑定并重新完整校验。
- `DELETE /api/v1/agent-configs/:id`：成功 `data` 为 `{"message":"agent config deleted"}`。

## 8. 文本流式生成

### `POST /api/v1/text-generations`

请求：

| 字段 | 类型 | 必填 | 约束/行为 |
| --- | --- | --- | --- |
| `model_config_id` | integer | 否 | >0 时使用指定配置；省略/0 时选默认 active 非生图配置，否则选第一个 active 非生图配置 |
| `message` | string | 是 | 1–20000 字符，trim 后不能为空 |
| `system_prompt` | string | 否 | 最长 20000 字符 |

文本生成当前不接受 `model_type: image`：显式指定生图配置时返回 `400 invalid_request`；自动选择模型配置时会跳过所有 `image` 配置，仅在 active 的文本/多模态配置中选择。

成功后返回 SSE，不使用 `{data,meta}`：

```text
event: delta
data: {"request_id":"...","delta":"你好","sequence":1}

event: done
data: {"request_id":"...","done":true,"sequence":1}

```

| SSE event | data | 说明 |
| --- | --- | --- |
| `delta` | `{request_id, delta, done?, error?, sequence}` | 文本增量 |
| `done` | 同一结构，`done: true` | 正常结束 |
| `error` | 同一结构，`error` 非空，通常 `done: true` | Provider/流错误 |

文本 SSE 当前不写 `id:`，也不支持 `Last-Event-ID` 重放。若在建立 SSE 前发现请求、模型配置或鉴权错误，仍返回标准 JSON error envelope。

## 9. PPT Task

PPT 使用持久化 Run，不再使用旧 Conversation、Job、Action 或恢复接口。MySQL 保存 Run 快照和完整 canonical 事件；Go `TaskHub` 负责当前进程的 worker 控制、短期热缓存与实时广播。列表、详情和产物使用 HTTP，实时快照与事件使用单条 WebSocket 连接。

### 9.1 `PptTask`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `schema_version` | string | 当前为 `"1"` |
| `event_id` | UUID string | 最近已应用事件 ID；首个事件前为空字符串 |
| `thread_id` | string | Go 侧逻辑分组与同线程 worker 互斥 ID；当前不承载 Python 上下文 |
| `run_id` | UUID string | 本次生成 Run ID，也是产物目录名 |
| `task_id` | UUID string | `run_id` 的兼容别名，两者始终相等 |
| `client_request_id` | string，可省略 | 客户端幂等键 |
| `agent_config_id` | integer | 创建请求中的 Agent 配置 ID；`0` 表示自动选择 |
| `description` | string | 已 trim 的 PPT 需求 |
| `status` | string | `running`、`succeeded`、`failed`、`cancelled` |
| `stage` | string | 当前稳定阶段 |
| `message` | string | 最近事件的用户可读说明 |
| `last_event_sequence` | uint64 | 当前任务最后一个事件序号 |
| `result` | `FinalResult`，可省略 | 成功结果 |
| `error` | `ErrorDetail`，可省略 | 失败或取消详情 |
| `file_url` | string，可省略 | 最终 PPTX 下载地址 |
| `created_at`、`updated_at` | RFC3339 string | 时间 |
| `finished_at` | RFC3339 string，可省略 | 进入终态的时间 |

任务只属于创建它的用户。其他用户查询同一 `task_id` 统一得到 `404 not_found`。

### 9.2 创建任务

`POST /api/v1/ppt/tasks`

成功返回 `202 Accepted` 和 `PptTask`。服务先完整校验描述和五个模型节点绑定，再把初始 Run 提交到 MySQL，注册本地 worker 后返回；输入或模型校验失败不会留下 Run，持久化失败也不会启动 Python Runtime。

JSON 请求不支持附件：

```json
{
  "thread_id": "5a5f7de4-1c1c-4aeb-8da3-c6994c2b723b",
  "client_request_id": "web-create-019f7e80",
  "agent_config_id": 1,
  "description": "生成一份介绍 Agent Task Platform 架构的 8 页中文演示文稿"
}
```

| 字段 | 必填 | 约束/行为 |
| --- | --- | --- |
| `thread_id` | 否 | 最长 128 字符；省略时由 Go 生成 UUID |
| `client_request_id` | 否 | 最长 128 字符；建议每次用户创建意图生成一个稳定值 |
| `agent_config_id` | 否 | 正整数；省略或 0 时选择当前用户默认 active PPT 配置，否则选择首个可用配置 |
| `description` | 是 | trim 后非空，最多 5000 个 Unicode 字符 |

语言、目标页数、画面比例和视觉模板不作为独立请求字段。需求解析模型从 `description` 的自然语言中生成全局元数据；Processor 再依据该元数据和页面内容自动选择内部模板。

有附件时使用 `multipart/form-data`：

| 表单字段 | 必填 | 说明 |
| --- | --- | --- |
| `thread_id` | 否 | 规则同 JSON |
| `client_request_id` | 否 | 规则同 JSON |
| `agent_config_id` | 否 | 正整数；规则同 JSON |
| `description` | 是 | PPT 需求 |
| `attachments` | 否 | 可重复文件字段；推荐名称 |
| `source_files`、`source_file`、`file` | 否 | 与 `attachments` 等价的上传兼容名称 |

支持的附件扩展名：

```text
.pdf .txt .md .markdown .csv .json .yaml .yml .docx
.png .jpg .jpeg .webp
```

空文件返回 `400`，不支持的扩展名返回 `415`。单个文件最多 20 MiB，文件内容合计最多 100 MiB，multipart request body 最多 101 MiB；超限返回 `413`。Go 读取附件后计算 SHA-256，摘要参与持久化幂等指纹，并把字节内容直接放入本次内部 gRPC 请求；当前不持久化附件原文。

PPT Agent 配置必须 active，并且恰好能解析 `brief`、`outline`、`style`、`writer`、`designer` 五个 active 文本/多模态模型。任一节点缺失、模型停用或 `max_tokens <= 0` 都拒绝创建任务。

`client_request_id` 在当前用户内是 MySQL 唯一的持久幂等键：同一键与完全相同的规范化请求会跨请求、跨 API 重启返回原 Task；同一键换了 thread、需求、配置、附件或 options 会返回 `409 client_request_conflict`。同一用户的同一 `thread_id` 同时只允许一个尚未退出的 worker，数据库唯一约束与本地 Hub 共同保护该规则；冲突返回 `409 thread_run_conflict`，不同 thread 可并行。当前 thread 仍只用于 Go 侧分组和互斥，不是 Python checkpoint。

### 9.3 列表与查询任务

`GET /api/v1/ppt/tasks?thread_id=<id>&limit=<1..100>`

返回当前用户的 `PptTask[]`，按 `created_at` 倒序。`thread_id` 可选；`limit` 默认 20，最大 100。

`GET /api/v1/ppt/tasks/:task_id`

返回 MySQL 中当前用户可见的持久化任务快照。API 重启和 TaskHub 热缓存清理不会删除历史；任务从未存在或不属于当前用户时返回 `404`。

### 9.4 取消任务

`POST /api/v1/ppt/tasks/:task_id/cancel`

无 body。服务先在同一数据库事务中写入 `TASK_FAILED` 终态事件并更新 Run 快照，其中 `error.code` 为 `TASK_CANCELLED`、状态为 `cancelled`；事务提交后才广播 WS 事件并取消 Python gRPC context。创建事务到本地 cancel function 安装完成使用同一 run transition lock，因此并发取消会等待初始化完成，不会丢失取消意图；极端初始化异常可返回可重试的 `409 run_not_ready`。取消是终态，不支持 resume；对已经结束的任务取消返回 `409 conflict`。

## 10. PPT canonical WebSocket v1

PPT 前端不使用 SSE，而是通过 `GET /api/v1/ppt/ws` 升级一条长连接，在其上 multiplex 多个 run。`POST /api/v1/text-generations` 的文本 SSE 是独立接口，不属于此协议。

### 10.1 握手、鉴权与 envelope

浏览器不能为 WebSocket 握手任意设置 `Authorization`，因此需同时传入两个 subprotocol：

```js
new WebSocket("wss://api.example.com/api/v1/ppt/ws", [
  "agent-task-v1",
  "bearer." + accessToken
])
```

Go 在 upgrade 前取出 access token，复用普通 JWT、Redis blacklist 与 session 校验，并且只回显 `agent-task-v1`，不回显 bearer subprotocol。鉴权失败时在 upgrade 前返回 HTTP `401`。当前 upgrader 使用 Gorilla 的默认同源检查。浏览器在 native `open` 后仍等待 `connection.ready`；当前 Web 客户端等待 8 秒仍未收到时会关闭并退避重连。

客户端和服务端都使用 canonical JSON envelope：

```json
{
  "schema_version": "1",
  "type": "run.subscribe",
  "request_id": "cmd-9a6b",
  "payload": {"run_id": "<uuid>", "after": 12}
}
```

`schema_version` 当前必须是 `"1"`。每个客户端 command 都必须有 1–128 字符的 `request_id`，并在连接最近 4096 个 request ID 的去重窗口内唯一；服务端用它关联 ack/error/snapshot/event。服务端 envelope 另外始终包含 RFC3339 `created_at`。只接受最大 64 KiB 的 JSON text frame；envelope 和 run command payload 的未知字段、额外 JSON 值会被拒绝。

### 10.2 Client commands

| `type` | `payload` | 行为 |
| --- | --- | --- |
| `run.subscribe` | `{run_id, after?}` | 订阅或替换该 run 的现有订阅；`after` 默认 0 |
| `run.unsubscribe` | `{run_id}` | 只停止该连接上的订阅，不取消 run |
| `run.cancel` | `{run_id}` | 显式取消 run，不要求先订阅 |
| `ping` | 任意 JSON，通常 `{}` | 应用层心跳，服务原样放入 `pong.payload` |

单连接最多同时订阅 32 个 run。客户端应在 90 秒读超时内发送 command；当前 Web 客户端每 25 秒发送 `ping`。

### 10.3 Server frames

| `type` | `payload` | 说明 |
| --- | --- | --- |
| `connection.ready` | `{connection_id, schema_version}` | upgrade 完成后的首个服务端帧 |
| `command.ack` | `{command, run_id?}` | command 已成功处理 |
| `run.snapshot` | `PptTask` | 订阅或取消后的完整快照 |
| `run.event` | `AgentEvent` | MySQL 历史重放或当前进程实时事件 |
| `command.error` | `{code,message,command?,run_id?,retryable,last_event_sequence?}` | command 失败；连接通常继续可用 |
| `pong` | 客户端 `ping.payload` | 应用层心跳响应 |

`run.subscribe` 成功后的顺序是 `command.ack` → `run.snapshot` → 所有 `sequence > after` 的 `run.event` → 实时 `run.event`。`after` 只是事件游标；客户端不能用 snapshot 的 `last_event_sequence` 跳过尚未收到的事件。收到终态事件后服务只移除该 run 订阅，WebSocket 仍保持并可继续承载其他 run。

终态 snapshot 也不能单独证明 replay 已消费完成：本地 event cursor 小于 snapshot watermark 时，客户端继续保留重订阅意图；只有 cursor 已追上 watermark，或实际应用了终态 event，才结束该 run 的期望订阅。

Run 快照和 canonical 事件以 MySQL 为权威；TaskHub 只提供当前进程的 live fan-out。断线、API 重启或热缓存淘汰后，客户端都按每个 run 已处理的最大 event `sequence` 重发 `run.subscribe {after}`，服务可从 `ppt_run_events` 连续补发。若 `after` 大于服务端 watermark，会返回可重试的 `event_cursor_ahead` 和 `last_event_sequence`；当前 Web 客户端会将该 run 的 cursor 重置为 0，全量回放后重建投影。发送队列溢出或订阅流中断时，服务端使用 close code `1013` 要求客户端重连续传。

### 10.4 `AgentEvent`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `schema_version` | string | 当前为 `"1"` |
| `event_id` | UUID string | 事件唯一 ID |
| `thread_id` | string | 所属 thread |
| `run_id` | UUID string | 所属 run |
| `task_id` | UUID string | `run_id` 兼容别名 |
| `sequence` | uint64 | run 内从 1 开始严格递增；排序与续传游标 |
| `created_at` | RFC3339 string | 事件时间 |
| `channel` | string | `lifecycle`、`updates` 或 `artifacts` |
| `type` | string | 事件类型 |
| `stage` | string | 稳定前端阶段 |
| `message` | string | 可直接展示的说明 |
| `progress` | object，可省略 | `current`、`total`、`percent`、`unit` |
| `resources` | array，可省略 | 本事件就绪的文件资源 |
| `page` | object，可省略 | `page_id`、`page_index`、`page_count`、`title` |
| `error` | object，可省略 | `code`、`message`、`retryable`；Runtime debug 不对外转发 |
| `final_result` | object，可省略 | `ppt_file`、`slide_count`、`elapsed_ms` |

| `type` | `channel` | 含义 |
| --- | --- | --- |
| `TASK_STARTED` | `lifecycle` | Python 已开始处理 |
| `STEP_STARTED` | `updates` | 一个可展示步骤开始 |
| `STEP_PROGRESS` | `updates` | 页、资源或步骤进度 |
| `STEP_COMPLETED` | `updates` | 一个可展示步骤完成 |
| `ARTIFACT_READY` | `artifacts` | 一个或多个中间产物可下载 |
| `TASK_COMPLETED` | `lifecycle` | PPTX 已完成；终态 |
| `TASK_FAILED` | `lifecycle` | 生成失败或取消；终态 |

取消不引入独立的 `TASK_CANCELLED` 事件类型，而由 `TASK_FAILED + error.code=TASK_CANCELLED` 表示。Task 快照的 `status` 会是 `cancelled`。`run.cancel` 成功会返回 ack 和终态 snapshot；若已订阅该 run，还会收到终态 event。断开 WebSocket 或 `run.unsubscribe` 不会取消生成，只有显式 `run.cancel` 或 HTTP cancel 才会取消。

### 10.5 稳定阶段

| `stage` | 含义 |
| --- | --- |
| `PREPARING` | 校验输入、保存附件、建立项目目录 |
| `PLANNING` | brief、outline、style |
| `GENERATING_CONTENT` | writer 生成逐页内容 |
| `GENERATING_ASSETS` | 准备页面资源 |
| `GENERATING_PAGES` | designer 生成页面 |
| `RENDERING` | 渲染 HTML、执行 DOM QA 并生成 PNG 预览 |
| `ASSEMBLING_PPT` | 组装 PPTX |
| `FINISHED` | 成功完成 |

阶段是稳定外部契约，不等同于 Python 函数名。失败事件会保留发生故障时的阶段。

### 10.6 错误与失败收口

`command.error.code` 的稳定值包括：`invalid_message`、`invalid_command`、`unsupported_schema_version`、`unsupported_command`、`unsupported_data_type`、`duplicate_request_id`、`subscription_limit_exceeded`、`run_not_found`、`run_not_ready`、`event_cursor_ahead`、`run_already_finished` 和 `internal_error`。`run_not_ready` 与 `event_cursor_ahead` 可重试；客户端应以 `retryable` 决定是否重连/重试，不应依赖 `message` 文案。

流开始前的无效 Python 请求使用标准 gRPC status。Go 无法建立 Runtime 流时发布 `TASK_FAILED/PYTHON_RUNTIME_UNAVAILABLE`；未收到终态就异常断流或提前 EOF 时发布 `TASK_FAILED/PYTHON_STREAM_LOST`。这两类错误均可标记 `retryable: true`，但“重试”是重新创建 Task，不是恢复旧 Task。

合法 Runtime 事件若因 MySQL 故障无法可靠提交，不会先向 WS 广播；数据库恢复后收口为可重试的 `TASK_FAILED/EVENT_PERSISTENCE_FAILED`。worker 已退出但终态仍未落库时，当前单实例进程会后台重试 `GO_WORKER_EXITED` 终态并释放 worker guard，进程重启仍由 `SERVER_RESTARTED` 做最终兜底。文件不存在或 size/hash 不匹配则收口为可重试的 `TASK_FAILED/ARTIFACT_NOT_READY`，不会提交对应 ready/success 事件。

## 11. PPT 产物下载

### 11.1 最终 PPTX

`GET /api/v1/ppt/tasks/:task_id/file`

只有任务已收到包含 PPTX 相对路径的成功结果后才可下载。响应文件名为：

```text
deck-<task_id>.pptx
```

### 11.2 指定产物

`GET /api/v1/ppt/tasks/:task_id/artifacts/*asset_path`

`asset_path` 来自事件 `resources[].url`。当前 Runtime 只发布 PNG 页面预览和最终 PPTX；协议中的 HTML/SVG 类型仅保留给其他生产者或历史数据。Go 校验：

- `task_id` 是规范 UUID，并属于当前用户；
- 相对路径不是绝对路径、卷路径或 `..` 穿越；
- 解析后的绝对路径仍位于 `python_agent.artifacts_dir/<task_id>/`；
- 目标是实际存在的普通文件。

若服务历史数据或其他生产者产生 HTML/SVG，响应仍附加 CSP、sandbox 和 `X-Content-Type-Options: nosniff`。Python 事件传输 `relative_path`，公开 URL 由 Go 生成；事件不得泄露 Runtime 绝对路径。

Go 的 `python_agent.artifacts_dir` 与 Python 的 `DECK_ARTIFACTS_DIR` 必须指向同一个物理目录。不同容器需要共享卷，不共享文件系统的跨主机部署暂不支持产物下载。

## 12. PPT 当前持久化边界

MySQL 现在持久化：

- `ppt_runs`：owner、thread、幂等指纹、请求摘要、当前状态/阶段、水位、成功结果、错误和 worker 生命周期；
- `ppt_run_events`：每个经过 Go 校验和脱敏的 canonical `AgentEvent`，按 `(run_id, sequence)` 连续保存；
- 事件插入和 Run 快照推进位于同一事务，数据库提交成功后才允许 WebSocket 广播；
- `ARTIFACT_READY` / `TASK_COMPLETED` 落库前，Go 会从下载使用的同一 artifact root 打开相关文件，确认其为非空普通文件，并在 Runtime 提供 size/hash 时逐项核对；失败会改写为 `TASK_FAILED/ARTIFACT_NOT_READY`。

WebSocket 的 `connection.ready`、command、ack、ping/pong 等传输帧不写数据库。TaskHub 中的订阅、取消函数和热 replay 仍是进程内状态，但终态历史、查询、幂等和事件重放不再依赖其一小时 TTL。

当前 Python Runtime 没有 checkpointer 或 durable queue，所以 API 启动时会把上一个单实例进程遗留的 active Run 收口：仍为 `running` 的 Run 追加 `TASK_FAILED/SERVER_RESTARTED`，已有终态但 worker 尚未释放的 Run 只释放互斥槽。该策略适用于当前单 API 实例；滚动部署或多实例 worker 仍需 `worker_instance_id + lease + fencing token`，不能把本实现视为跨实例接管。

PPTX、预览和中间资源仍存放在本地/共享 `python_agent.artifacts_dir`，MySQL 只保存相对路径、大小和 hash 等事件元数据。数据库记录存在不代表文件在跨主机或卷清理后仍存在；生产多主机场景需要对象存储。Python 仍不提供 review、action、pause、resume、repair 或版本化 artifact 接口。

## 13. 内部 gRPC 协议

### 13.1 服务与生成物

Runtime 默认监听 `127.0.0.1:50051`，当前使用内部明文 gRPC。它不验证 HTTP JWT，只接受 Go API 解析好的模型配置和用户输入，因此端口必须留在可信网络。

| 用途 | Proto 真源 | Package / Service | Go 生成代码 |
| --- | --- | --- | --- |
| Provider 与文本 | `contracts/proto/v1/provider/provider.proto` | `provider.v1.ProviderRuntimeService` | `contracts/gen/go/v1/provider` |
| PPT | `contracts/proto/ppt/ppt_agent.proto` | `ppt.agent.PptAgentService` | `contracts/gen/go/pptagent` |

两套 Python 生成代码都位于 `apps/runtime/api`。PPT 请求可内联最多 100 MiB 附件，Go 调用和 Runtime server 的 gRPC 单条消息上限为 300 MiB。修改任一 proto 后从仓库根目录执行 `make proto`，不要手工编辑生成文件。

### 13.2 RPC 总览

| Service | RPC | 类型 | 请求 | 响应 |
| --- | --- | --- | --- | --- |
| `ProviderRuntimeService` | `TestCredential` | unary | `TestCredentialRequest` | `TestCredentialResponse` |
| `ProviderRuntimeService` | `ListProviders` | unary | `ListProvidersRequest` | `ListProvidersResponse` |
| `ProviderRuntimeService` | `StreamText` | server streaming | `StreamTextRequest` | stream `StreamTextResponse` |
| `PptAgentService` | `GeneratePpt` | server streaming | `GeneratePptRequest` | stream `AgentEvent` |

没有 `GenerateDeck`、`ResumeDeck` 或双向 PPT RPC。

### 13.3 Provider 共享消息与 RPC

`UserContext`：

| 字段 | Proto 类型 | 说明 |
| --- | --- | --- |
| `user_id` | `int64` | 已认证用户 ID |
| `username` | `string` | 用户名 |
| `session_id` | `string` | HTTP session ID |
| `device` | `string` | 登录设备 |

`TestCredentialRequest` 包含 `user`、`provider`、`api_key`、`base_url`；响应包含 `request_id`、`provider`、`status`、`message`。

`ListProvidersRequest` 为空。`ListProvidersResponse.providers[]` 的 `ProviderDescriptor` 包含：

| 字段 | Proto 类型 |
| --- | --- |
| `name`、`display_name`、`description`、`manager`、`sdk`、`default_base_url` | `string` |
| `supports_credential_test`、`supports_custom_base_url` | `bool` |
| `models` | repeated `ProviderModel` |

`ProviderModel` 包含 `id`、`model_name`、`display_name`、`type`、`reasoning_supported`、`parameters[]`。`type` 只使用 `text`、`multimodal`、`image`。`ProviderModelParameter` 包含 `name`、`display_name`、`type`、`description`、`default_value`、`options[]`、`required` 以及 optional `min`/`max`。

`StreamTextRequest`：

| 字段 | Proto 类型 |
| --- | --- |
| `user` | `UserContext` |
| `provider`、`model`、`api_key`、`base_url` | `string` |
| `prompt`、`system_prompt` | `string` |
| `temperature` | `double` |
| `max_tokens` | `int32` |
| `provider_options_json` | `string` |

`StreamTextResponse` 包含 `request_id`、`delta`、`done`、`error`、`sequence`。

### 13.4 PPT 请求

```proto
rpc GeneratePpt(GeneratePptRequest) returns (stream AgentEvent);
```

`GeneratePptRequest`：

| 字段 | Proto 类型 | 说明 |
| --- | --- | --- |
| `task_id` | `string` | Go 生成的 Run UUID；当前 `task_id == run_id` |
| `description` | `string` | PPT 需求 |
| `attachments` | repeated `InputResource` | 内联输入附件 |
| `models` | repeated `ModelConfig` | 五个节点的最终模型配置 |

`InputResource`：

| 字段 | Proto 类型 | 说明 |
| --- | --- | --- |
| `resource_id`、`name`、`mime_type`、`sha256` | `string` | 资源标识和元数据 |
| `content` | `bytes` | 附件完整内容 |

`ModelConfig`：

| 字段 | Proto 类型 | 说明 |
| --- | --- | --- |
| `node_key` | `string` | `brief`、`outline`、`style`、`writer`、`designer` 之一 |
| `provider`、`model`、`api_key`、`base_url` | `string` | Provider 调用配置；API key 只在内部传输 |
| `provider_options` | `google.protobuf.Struct` | Provider 扩展选项 |
| `temperature` | `double` | 节点最终参数 |
| `max_tokens` | `int32` | 节点最终参数 |
| `supports_image_input` | `bool` | 模型图片输入能力 |

`GeneratePptRequest` 不包含语言、页数、画面比例或模板选项；这些值不由 Go 注入。公开 Task 的 `thread_id` 当前也不在该 proto 中；Go 只用它做列表分组和单 thread worker 互斥，调用 Python 时仍传 `task_id=run_id`。因此 Python 不具有 thread checkpoint 语义。请求也不包含用户会话、当前阶段、resume 数据、review action 或版本号。

### 13.5 PPT 事件与资源

`AgentEvent`：

| 字段 | Proto 类型 | 说明 |
| --- | --- | --- |
| `task_id` | `string` | Task UUID |
| `sequence` | `uint64` | 单任务递增序号 |
| `created_at` | `google.protobuf.Timestamp` | 事件时间 |
| `type` | `EventType` | 统一事件类型 |
| `stage` | `PptStage` | 稳定展示阶段 |
| `message` | `string` | 用户可读消息 |
| `progress` | `Progress` | `current`、`total`、`percent`、`unit` |
| `resources` | repeated `ResourceRef` | 本事件相关文件 |
| `detail` | oneof | `PageDetail`、`ErrorDetail`、`FinalResult` 之一 |

`ResourceRef`：

| 字段 | Proto 类型 | 说明 |
| --- | --- | --- |
| `resource_id` | `string` | 资源 ID |
| `type` | `ResourceType` | `INPUT_ATTACHMENT`、`IMAGE`、`SVG`、`HTML`、`PAGE_PREVIEW`、`PPTX`、`OTHER_FILE` |
| `name` | `string` | 文件名 |
| `relative_path` | `string` | `DECK_ARTIFACTS_DIR/<task_id>/` 下的相对路径 |
| `mime_type` | `string` | MIME |
| `size_bytes` | `uint64` | 文件大小 |
| `sha256` | `string` | 可选摘要 |

Python 不返回公开 URL。Go 把相对路径转换为当前用户可访问的 Task 下载 URL，并在 Go canonicalization / WebSocket 边界移除 `ErrorDetail.debug_message`、补齐 `schema_version`、`event_id`、`thread_id`、`run_id`、`task_id` 和 `channel`。成功流必须以 `TASK_COMPLETED` 结束，失败流尽量以 `TASK_FAILED` 结束；Go 会为无终态断流补发失败事件。

## 14. 进程探针与已删除接口

`GET /healthz` 是唯一不带 `/api/v1` 的路由，仅供容器和负载均衡器探测进程；业务健康检查可使用 `GET /api/v1/health`。旧的 `/login`、`/providers` 等无前缀业务路由没有注册。

旧 `/ppt-agent/*` 与 `/api/v1/presentations/*` 路由没有兼容层，也没有 resume/action 替代语义；PPT 客户端必须直接使用 `/api/v1/ppt/tasks` 与 `/api/v1/ppt/ws`。
