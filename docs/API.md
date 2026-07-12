# Agent Task Platform 接口文档

本文档描述当前代码实际提供的公开 HTTP API 和 Go API 与 Python Runtime 之间的内部 gRPC 协议。新客户端只应使用 canonical 前缀：

```text
http://<api-host>:<port>/api/v1
```

无版本前缀路由仅用于旧客户端兼容，见文末“旧接口兼容与弃用”。特别注意：`/ppt-agent/generate` 和 `/ppt-agent/resume` **只有 legacy 版本**；`/api/v1` 没有对应的 `/generate`、`/resume`，必须使用 Job、Event、Action 三类接口。

## 1. 通用约定

### 1.1 Content-Type 与时间

- 普通请求/响应：`application/json; charset=utf-8`。
- 文件上传：`multipart/form-data`。
- 流式响应：`text/event-stream`。
- Artifact 下载：按文件返回二进制，不套 JSON envelope。
- 所有时间字符串均为 RFC 3339；Go DTO 中的 `time.Time` 也按 RFC 3339 JSON 格式序列化。

### 1.2 成功响应

除 SSE 和文件下载外，所有 `/api/v1` 成功响应都使用：

```json
{
  "data": {},
  "meta": {
    "request_id": "d9cf50dce0e546414a63bcf4ed05b1c7"
  }
}
```

`data` 的实际类型由接口定义，可以是对象、数组或标量。创建资源通常返回 `201 Created`，PPT Job 创建返回 `202 Accepted`，其余成功请求返回 `200 OK`。

### 1.3 错误响应

`/api/v1` 错误统一为：

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
| 409 | `conflict` | 用户名或配置名称冲突 |
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

除健康检查和注册/登录/刷新外，canonical 路由都要求：

```http
Authorization: Bearer <access_token>
```

服务会同时验证：

1. JWT 签名、有效期和 token type 必须是 `access`；
2. access token 的 JTI 不在 Redis 黑名单；
3. Redis 中同一 `user_id + device` 的 session 仍为 active；
4. JWT 中的 `session_id` 与 Redis session 一致。

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
| GET | `/api/v1/presentations/conversations` | 是 | `ConversationSummary[]` |
| GET | `/api/v1/presentations/conversations/:id` | 是 | `ConversationDetail` |
| DELETE | `/api/v1/presentations/conversations/:id` | 是 | `{deleted, id}` |
| POST | `/api/v1/presentations/jobs` | 是 | `DeckJob`（202） |
| GET | `/api/v1/presentations/jobs/:job_id` | 是 | `DeckJob` |
| GET | `/api/v1/presentations/jobs/:job_id/events` | 是 | 可续传 PPT SSE |
| POST | `/api/v1/presentations/jobs/:job_id/actions` | 是 | `{job, duplicate}` |
| POST | `/api/v1/presentations/jobs/:job_id/cancel` | 是 | `DeckJob` |
| GET | `/api/v1/presentations/metrics` | 是 | `DeckProductMetrics` |
| GET | `/api/v1/presentations/artifacts/:request_id` | 是 | PPTX 文件 |
| GET | `/api/v1/presentations/artifacts/:request_id/*asset_path` | 是 | 指定产物文件 |

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

Agent 定义由迁移/系统维护，不是用户 CRUD 资源。当前 PPT Agent 的可配置模型节点为 `brief`、`outline`、`style`、`writer`、`designer`、`review`、`coherence`；以接口返回的 `nodes` 为最终依据。

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
    { "node_key": "designer", "model_config_id": 10 },
    { "node_key": "review", "model_config_id": 11 },
    { "node_key": "coherence", "model_config_id": 10 }
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

## 9. PPT 会话

### 9.1 会话列表

`GET /api/v1/presentations/conversations`

返回当前用户的 `ConversationSummary[]`，按最近更新时间倒序：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | integer | 会话 ID |
| `agent_config_id` | integer | 创建任务时使用的 Agent 配置 |
| `agent_config_name` | string | 配置名称快照 |
| `title` | string | 从 description 派生，最多保留 48 个 Unicode 字符后加 `...` |
| `status` | string | `running`、`waiting`、`done`、`error`、`cancelled` |
| `latest_request_id` | string，可省略 | 最近一次 Runtime request ID |
| `created_at`, `updated_at` | RFC3339 string | 时间 |

### 9.2 会话详情

`GET /api/v1/presentations/conversations/:id`

返回上述摘要字段，并增加：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `messages` | array | 会话消息投影 |
| `job` | `DeckJob`，可省略 | 该会话最近一个 Job |

每条消息都有 `id`、`role`、`kind`，其余字段随 `kind` 变化：

| `kind` | `role` | 主要字段 |
| --- | --- | --- |
| `user` | `user` | `text`，可选 `fileNames[]` |
| `status` | `assistant` | `title`、`text`、`requestId?`、`state`、`steps[]`、`slides[]` |
| `outline` | `assistant` | `title`、`text`、`requestId`、`outlineMarkdown`、`awaitingConfirm`、`showEditor` |
| `style` | `assistant` | `title`、`text`、`requestId`、`options[]`、`awaitingConfirm` |
| `result` | `assistant` | `title`、`text`、`requestId`、`fileName`、`warnings[]`、`degraded`、`qualityStatus` |

`status.steps[]` 包含 `id`、`label`、`message`、`tone`、可选 `stage`；`status.slides[]` 可能包含页码、标题、要点、layout/component、预览路径、version、status 和 warnings。消息是持久化事件的 UI 兼容投影；精确、可续传的执行记录以 Job Event SSE 为准。

### 9.3 删除会话

`DELETE /api/v1/presentations/conversations/:id`

仅能删除当前用户自己的会话。删除会级联清理该会话的消息、Job、Event、Action、Asset 和 Source 数据，并清理关联的 PPT 产物目录。处于 `queued`、`running` 或 `exporting` 的 Job 会返回 `409 conflict`，需要先取消或等待任务结束；等待大纲/风格确认的会话可以直接删除。

## 10. PPT Job

### 10.1 `DeckJob` 结构

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `job_id` | UUID string | 后台 Job ID |
| `conversation_id` | integer | 自动创建的会话 ID |
| `agent_config_id` | integer | 实际选用的 PPT Agent 配置 |
| `request_id` | UUID string，可省略 | Runtime 开始生成后取得的 request ID，也是产物目录 ID |
| `mode` | string | `quick` 或 `step_by_step` |
| `status` | string | 见 Job 状态表 |
| `stage` | string | 当前规范化流水线阶段 |
| `error_code`, `error_summary` | string，可省略 | 失败信息；敏感 token/key 会被脱敏 |
| `retryable` | boolean | 失败是否被标记为可重试；当前没有通用 retry 路由 |
| `last_event_sequence` | integer | 已持久化的最后事件序号 |
| `quality` | object/JSON value | 最近 artifact 质量数据；尚无数据时为 `{}` |
| `created_at`, `updated_at` | RFC3339 string | 时间 |

### 10.2 Job 状态

| 状态 | 是否终态 | 含义/允许的后续操作 |
| --- | --- | --- |
| `queued` | 否 | 已持久化，等待后台 worker；创建和接受 action 后会短暂处于该状态 |
| `running` | 否 | Runtime 正在执行普通阶段 |
| `waiting_for_outline` | 否（等待态） | SSE 在大纲 interrupt 后关闭；可 `approve_outline` 或 `regenerate_outline` |
| `waiting_for_style` | 否（等待态） | SSE 在风格 interrupt 后关闭；可 `choose_style` |
| `exporting` | 否 | 正在导出/已产生 artifact、等待最终质量状态 |
| `success` | 是 | 成功且无质量警告；可 `regenerate_slide` |
| `success_with_warnings` | 是 | 已导出但有警告/降级；可 `regenerate_slide` |
| `needs_review` | 是 | 已导出但仍有 must-fix 问题；可 `regenerate_slide` |
| `failed` | 是 | 失败；查看 `error_code`、`error_summary`、`retryable` |
| `cancelled` | 是 | 已请求取消并终止后台 gRPC context |

API 重启时，尚未取得 Runtime request ID 的 `queued` Job 会重新启动。其余可恢复扫描范围内的 Job（`queued` 但已有 request ID、`running`、`exporting`）因原 gRPC 调用已经丢失，会转为 `failed`、`error_code: "worker_restarted"`、`retryable: true`。处于等待确认状态的 Job 不在该扫描范围内，保持可继续操作。

### 10.3 创建 Job

`POST /api/v1/presentations/jobs`

推荐使用 multipart：

| form 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `description` | text | 是 | trim 后 1–5000 字符 |
| `agent_config_id` | positive integer text | 否 | 指定当前用户的 active `ppt_agent` 配置；省略时选默认 active 配置，否则选第一个 active 配置；非空值必须是正整数 |
| `mode` | text | 否 | 仅接受小写 `quick` 或 `step_by_step`；空值默认 `step_by_step` |
| `source_files` | file，可重复 | 否 | 推荐的多文件字段 |
| `source_file` | file，可重复 | 否 | 兼容字段，与 `source_files` 一起收集 |
| `file` | file，可重复 | 否 | 兼容字段，与上述字段一起收集 |

示例：

```bash
curl -X POST http://127.0.0.1:8080/api/v1/presentations/jobs \
  -H "Authorization: Bearer $TOKEN" \
  -F "description=根据报告生成季度业务复盘" \
  -F "mode=step_by_step" \
  -F "agent_config_id=3" \
  -F "source_files=@report.pdf" \
  -F "source_files=@metrics.csv"
```

成功立即返回 `202 Accepted` 和 `DeckJob`，通常初始 `status` 为 `queued`；生成过程不依赖创建请求保持连接。

#### 文件格式和限制

multipart 上传允许扩展名（大小写不敏感）：

```text
.pdf  .txt  .md  .markdown  .csv  .json  .yaml  .yml  .docx  .png  .jpg  .jpeg  .webp
```

PNG/JPEG/WebP 不进入文本解析。Runtime 会解码、校验并去除元数据后重新编码到当前任务的 `images/` 目录；限制为单文件 20 MiB、单边 8192 px、总像素 40 MP。SVG 只能通过资产清单引用该任务目录中的受控相对路径。

| 限制 | 值 |
| --- | --- |
| 单文件内容 | ≤ 20 MiB（20 × 2²⁰ bytes） |
| 所有源文件内容合计 | ≤ 100 MiB（100 × 2²⁰ bytes） |
| 整个 multipart request body | ≤ 101 MiB（101 × 2²⁰ bytes） |

空文件会返回 `400 invalid_request`；不支持的扩展名返回 `415 unsupported_media_type`；request body、单文件或总量超限返回 `413 payload_too_large`。源文件会先持久化到 artifact 同级的 `deck-sources/<job_id>`，MySQL 保存 resource ID、相对路径、SHA-256 和大小；后台传给 Runtime 的是受控本地路径和完整性元数据。

#### JSON 兼容请求

当 Content-Type 不是 multipart 时，Handler 也接受 JSON：

```json
{
  "agent_config_id": 3,
  "description": "生成产品介绍",
  "mode": "quick",
  "source_files": [
    {
      "filename": "notes.md",
      "content": "IyBUaXRsZQ=="
    }
  ]
}
```

`content` 对应 Go `[]byte`，必须是 base64 字符串。JSON 与 multipart 共用 `agent_config_id`、`mode`、扩展名、空文件、单文件 20 MiB 和总计 100 MiB 校验；multipart 另外有 101 MiB request body 上限。`source_files[]` 还可被 JSON decoder 接收 `resource_id`、`sha256`、`size_bytes`，但这些是服务端资源元数据，创建 Job 时会被丢弃并重新生成；客户端不应发送。顶层 `source_file_name` 也是旧版内部兼容字段，canonical Job JSON 不应使用。multipart 更适合实际文件上传，推荐外部客户端优先使用。

### 10.4 查询 Job

`GET /api/v1/presentations/jobs/:job_id`

返回当前用户拥有的 `DeckJob`。前端刷新后可先查询 Job，再从 `last_event_sequence` 或本地最后消费序号决定如何续订事件。

### 10.5 提交 Action

`POST /api/v1/presentations/jobs/:job_id/actions`

Header：

```http
Idempotency-Key: <caller-generated-key>
```

该 header 可选，建议不超过 128 字符。省略时服务按规范化 action、outline 和 payload 计算 SHA-256 key。相同 Job 下相同 key 只创建一次操作。

Body 通用结构：

```json
{
  "action": "approve_outline",
  "outline_markdown": "# 大纲\n...",
  "payload": {}
}
```

| action | Job 前置状态 | 必填内容 | `payload` |
| --- | --- | --- | --- |
| `approve_outline` | `waiting_for_outline` | 非空 `outline_markdown` | `{}` |
| `regenerate_outline` | `waiting_for_outline` | 无 | `{}` |
| `choose_style` | `waiting_for_style` | 无 | 可含 `style_id`（兼容 `styleId`）和 `feedback` |
| `regenerate_slide` | `success` / `success_with_warnings` / `needs_review` | `payload.index` 必须是正整数 | 可选 `payload.feedback` |

`payload` 应为 JSON object，Runtime 端限制其序列化内容不超过 64 KB。

成功：

```json
{
  "data": {
    "job": { "job_id": "...", "status": "queued", "...": "..." },
    "duplicate": false
  },
  "meta": { "request_id": "..." }
}
```

若 idempotency key 已存在，返回当前 Job 且 `duplicate: true`，不会再次启动 Runtime。Action 被接受后应重新订阅 events；Action 接口本身不返回生成 SSE。

### 10.6 取消 Job

`POST /api/v1/presentations/jobs/:job_id/cancel`

无 body。非终态 Job 会持久化为 `cancelled`、发出 `cancelled` event，并取消当前后台 gRPC context。对已经终止的 Job 调用时返回其当前状态。

## 11. PPT Job Event SSE

### 11.1 订阅与恢复

`GET /api/v1/presentations/jobs/:job_id/events`

可通过任一方式指定已消费序号：

```http
Last-Event-ID: 42
```

```text
GET /api/v1/presentations/jobs/:job_id/events?after=42
```

两者同时提供时取较大非负整数。服务返回该 Job 中 `sequence > after` 的持久化事件，每批最多读取 200 条；无新事件时约每 350 ms 轮询。断线不取消后台 Job。

调用方应在每次收到事件后保存 SSE `id`；重连时传回 `Last-Event-ID`，并按 ID 去重。若恢复游标 >0，系统会另外记录 `subscription_resumed` 指标事件。

### 11.2 SSE 帧

```text
id: 17
event: stage_progress
data: {"event_version":1,"job_id":"...","conversation_id":8,"request_id":"...","event_type":"stage_progress","stage":"writer","stage_status":"running","message":"Writing slides","severity":"info","retryable":false,"sequence":17}

```

`event:` 等于 data 的 `event_type`；`id:` 与 data 的 `sequence` 相同。等待态和终态事件带 `done: true`，发送后当前 SSE 连接结束。若客户端已追到等待/终态最后一个事件，重新订阅可能只收到一个无 ID 的合成 `done` 帧。

### 11.3 Event data 字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `event_version` | integer | 当前为 `1` |
| `job_id` | string，可省略 | Job ID |
| `conversation_id` | integer，可省略 | 会话 ID |
| `request_id` | string | Runtime request ID；早期队列事件可能为空 |
| `event_type` | string，可省略 | 事件名 |
| `stage` | string，可省略 | 当前规范化阶段 |
| `message` | string，可省略 | 人类可读说明 |
| `channel` | string，可省略 | `text_delta` 的逻辑通道 |
| `text_delta` | string，可省略 | 文本增量 |
| `content` | string，可省略 | 完整文本内容 |
| `outline_markdown` | string，可省略 | 大纲内容 |
| `payload` | 任意 JSON，可省略 | 事件特有数据 |
| `artifact_url` | string，可省略 | artifact event 中为 canonical 下载地址 `/api/v1/presentations/artifacts/{request_id}` |
| `done` | boolean，可省略 | 当前流是否在此事件结束 |
| `error` | string，可省略 | 错误摘要 |
| `error_code` | string，可省略 | 稳定程度高于 message 的错误标识 |
| `stage_status` | string，可省略 | `pending`、`running`、`waiting`、`done`、`warning`、`error` |
| `progress` | object，可省略 | `{current, total}` |
| `slide` | object，可省略 | `{index, version}` |
| `severity` | string，可省略 | `info`、`warning`、`error` |
| `action_required` | string，可省略 | 通常为 `approve_outline` 或 `choose_style` |
| `retryable` | boolean | 是否建议重试 |
| `sequence` | integer | Job 内单调递增的持久化序号 |

### 11.4 阶段

规范化阶段按工作流大致为：

```text
brief -> outline -> outline_review -> style -> style_review -> writer
      -> designer -> validate -> review_vlm -> repair -> coherence -> export
```

`quick` 模式会自动通过大纲和风格 review；`step_by_step` 会在 `outline_review`、`style_review` 产生 interrupt 并等待 Action。

### 11.5 当前事件类型

| event type | 来源/说明 |
| --- | --- |
| `job_queued` | API 已创建并持久化 Job |
| `stage_started`, `stage_progress` | 阶段开始/进度 |
| `agent_message` | Agent 阶段说明 |
| `fact_sheet_ready` | brief 事实表完成 |
| `text_delta` | 大纲等流式文本增量 |
| `outline` | 完整大纲 |
| `style_options` | 风格候选 |
| `slide_started`, `slide_spec_ready`, `slide_ready` | Writer 页级事件 |
| `slide_preview` | HTML/PNG 预览就绪，含页码与版本 |
| `validation_report` | 结构/质量校验结果 |
| `repair_progress` | 自动修复或用户单页重做进度 |
| `export_progress` | 导出进度 |
| `artifact` | PPTX/质量信息就绪，并增加 `artifact_url` |

`style_options` 的每个候选方向除预览图外，还包含 `communication_mode`、`visual_style`、`template_id`、`template_mode`、`template_summary`、`rationale`、`confirmations`（八项执行确认）、`layout_principles` 与 `image_strategy`。`template_mode=free_design` 表示自由设计；其他模式会在逐页蓝图中实际注入模板 SVG 结构，而不是只记录模板名称。
| `interrupt` | 等待大纲确认或风格选择；`done: true` |
| `action_accepted` | API 已接受幂等 Action |
| `subscription_disconnected` | 订阅断开但 Job 继续，用于指标 |
| `subscription_resumed` | 带游标恢复订阅，用于指标 |
| `artifact_downloaded` | 成功提供过一次产物下载，用于指标 |
| `done` | 当前运行成功结束；`done: true` |
| `error` | Job 或订阅错误；`done: true` |
| `cancelled` | Job 被取消；`done: true` |

客户端应允许出现新的 event type，并以通用字段和 `payload` 做向前兼容。

## 12. PPT 指标

### `GET /api/v1/presentations/metrics`

返回当前用户范围内的聚合指标：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `total_jobs` | integer | Job 总数 |
| `successful_jobs` | integer | `success` + `success_with_warnings` |
| `exported_jobs` | integer | 上述状态再加 `needs_review` |
| `success_rate` | number | successful / total |
| `disconnected_jobs` | integer | 曾记录订阅断开的 Job 数 |
| `recovered_subscriptions` | integer | 曾记录恢复订阅的 Job 数 |
| `subscription_recovery_rate` | number | 恢复数（不超过断开数）/断开数 |
| `regenerated_jobs` | integer | 提交过 `regenerate_slide` 的 Job 数 |
| `regenerate_rate` | number | regenerated / total |
| `slide_regenerate_rate` | number | 当前与 `regenerate_rate` 相同 |
| `average_must_fix_issues` | number | 每个最终 artifact 的平均 must-fix 问题数 |
| `auto_repair_success_rate` | number | 有非用户反馈 repair 的 Job 中成功比例 |
| `download_success_rate` | number | 有下载事件的 Job / exported jobs |
| `average_generation_ms` | number | 有起止时间 Job 的平均耗时（毫秒） |

分母为 0 时对应 rate 为 0。

## 13. Artifact 下载

### 13.1 下载 PPTX

`GET /api/v1/presentations/artifacts/:request_id`

成功返回文件，`Content-Disposition` 文件名为：

```text
deck-<request_id>.pptx
```

### 13.2 下载指定资源

`GET /api/v1/presentations/artifacts/:request_id/*asset_path`

用于下载事件/会话中出现的相对路径，例如：

```text
/api/v1/presentations/artifacts/<request_id>/slides/1_v2.png
/api/v1/presentations/artifacts/<request_id>/slides/1_v2.html
```

安全与约束：

- `request_id` 必须匹配 36 字符十六进制/连字符格式，并且必须属于当前用户的 Job；
- `asset_path` 必须是 artifact 目录内的现有普通文件，拒绝绝对路径、`..` 穿越和目录；
- HTML 若含 `script`/`iframe`/`object`/`embed`、事件属性、外部 HTTP URL、`javascript:` 等模式会返回 404；
- 可提供的 HTML 会增加严格 CSP、`nosniff`；
- 文件成功返回时不使用 JSON envelope，错误仍使用 canonical error envelope。

## 14. 内部 gRPC 协议

### 14.1 基本信息

| 项目 | 值 |
| --- | --- |
| Proto 真源 | `contracts/proto/v1/provider/provider.proto` |
| Package | `provider.v1` |
| Service | `ProviderRuntimeService` |
| Go 生成代码 | `contracts/gen/go/v1/provider` |
| Python 生成代码 | `apps/runtime/api` |
| 默认地址 | `127.0.0.1:50051`（Runtime 默认只监听 loopback） |
| 传输 | 当前为内部明文 gRPC，无 TLS |
| Deck 单条 gRPC 消息上限 | 300 MiB（Go 调用端与 Python server 双向） |

Provider 目录和凭据测试的 Go 调用超时为 5 秒；流式调用使用可取消 context。Runtime 不验证 HTTP JWT，而是信任 API 写入的 `UserContext`，因此 gRPC 端口只能放在可信内部网络。容器或跨主机部署时需在 Runtime YAML 中显式把 host 改为 `0.0.0.0` 或私网地址，并由容器网络、防火墙或服务网格限制入口。

### 14.2 RPC 总览

| RPC | 类型 | 请求 | 响应 |
| --- | --- | --- | --- |
| `TestCredential` | unary | `TestCredentialRequest` | `TestCredentialResponse` |
| `ListProviders` | unary | `ListProvidersRequest` | `ListProvidersResponse` |
| `StreamText` | server streaming | `StreamTextRequest` | stream `StreamTextResponse` |
| `GenerateDeck` | server streaming | `GenerateDeckRequest` | stream `GenerateDeckResponse` |
| `ResumeDeck` | server streaming | `ResumeDeckRequest` | stream `GenerateDeckResponse` |

### 14.3 共享消息

`UserContext`：

| 字段 | Proto 类型 | 说明 |
| --- | --- | --- |
| `user_id` | `int64` | 已认证用户 ID |
| `username` | `string` | 用户名 |
| `session_id` | `string` | HTTP session ID；后台 PPT worker 目前可能只填 user/name |
| `device` | `string` | 登录设备；后台 PPT worker 目前可能为空 |

`DeckNodeModelConfig`：

| 字段 | Proto 类型 | 说明 |
| --- | --- | --- |
| `node_key` | `string` | Agent 节点 key |
| `provider` | `string` | Provider key |
| `model` | `string` | 模型名 |
| `api_key` | `string` | 模型凭据，只在内部传输 |
| `base_url` | `string` | Provider 地址 |
| `provider_options_json` | `string` | JSON object 字符串 |
| `temperature` | `double` | 节点最终参数 |
| `max_tokens` | `int32` | 节点最终参数 |
| `supports_image_input` | `bool` | VLM review 是否可传图片 |

`DeckSourceFile`：

| 字段 | Proto 类型 | 说明 |
| --- | --- | --- |
| `filename` | `string` | 原文件名 |
| `content` | `bytes` | 内联内容；后台 Job 的外置文件通常为空 |
| `resource_id` | `string` | API 生成的资源 UUID |
| `local_path` | `string` | 受控共享文件路径 |
| `sha256` | `string` | 内容摘要 |
| `size_bytes` | `int64` | 预期大小 |

### 14.4 Provider RPC

`TestCredentialRequest`：`user: UserContext`、`provider: string`、`api_key: string`、`base_url: string`。

`TestCredentialResponse`：`request_id: string`、`provider: string`、`status: string`、`message: string`。

`ListProvidersRequest` 为空。`ListProvidersResponse.providers[]` 中每个 `ProviderDescriptor` 包含：

| 字段 | Proto 类型 |
| --- | --- |
| `name`, `display_name`, `description`, `manager`, `sdk`, `default_base_url` | `string` |
| `supports_credential_test`, `supports_custom_base_url` | `bool` |
| `models` | repeated `ProviderModel` |

`ProviderModel` 包含 `id`、`model_name`、`display_name`、`type`、`reasoning_supported`、`parameters[]`。其中 `type` 只使用 `text`、`multimodal`、`image` 三种能力分类；旧 Provider 目录中的 `chat` 会按模型名称兼容归一为 `text` 或 `multimodal`。`ProviderModelParameter` 包含 `name`、`display_name`、`type`、`description`、`default_value`、`options[]`、`required`，以及带 presence 的 optional `min`/`max` double。

### 14.5 文本 RPC

`StreamTextRequest`：

| 字段 | Proto 类型 |
| --- | --- |
| `user` | `UserContext` |
| `provider`, `model`, `api_key`, `base_url` | `string` |
| `prompt`, `system_prompt` | `string` |
| `temperature` | `double` |
| `max_tokens` | `int32` |
| `provider_options_json` | `string` |

`StreamTextResponse`：

| 字段 | Proto 类型 | 说明 |
| --- | --- | --- |
| `request_id` | `string` | Runtime 调用 UUID |
| `delta` | `string` | 文本增量 |
| `done` | `bool` | 流结束 |
| `error` | `string` | 错误文本 |
| `sequence` | `int32` | Runtime 内文本序号 |

### 14.6 PPT 生成 RPC

`GenerateDeckRequest`：

| 字段 | Proto 类型 | 说明 |
| --- | --- | --- |
| `user` | `UserContext` | 调用用户 |
| `user_description` | `string` | PPT 需求 |
| `source_file_filename` | `string` | 单文件兼容字段 |
| `source_file` | `bytes` | 单文件兼容字段 |
| `node_model_configs` | repeated `DeckNodeModelConfig` | 各节点最终模型配置 |
| `source_files` | repeated `DeckSourceFile` | 多文件资源 |
| `job_id` | `string` | API Job UUID |
| `generation_mode` | `string` | `quick`/`step_by_step` |

`ResumeDeckRequest`：

| 字段 | Proto 类型 | 说明 |
| --- | --- | --- |
| `user` | `UserContext` | 调用用户 |
| `request_id` | `string` | 原生成 Runtime request ID/checkpoint thread ID |
| `outline_markdown` | `string` | 确认后的大纲 |
| `node_model_configs` | repeated `DeckNodeModelConfig` | 节点模型配置 |
| `action` | `string` | 四种 PPT action 之一 |
| `payload_json` | `string` | action JSON object，Runtime 限制 64 KB |
| `job_id` | `string` | API Job UUID |

两个 RPC 都返回 `GenerateDeckResponse` stream：

| 字段 | Proto 类型 | HTTP SSE 映射 |
| --- | --- | --- |
| `request_id` | `string` | `request_id` |
| `event_type` | `string` | `event_type` 与 SSE event name |
| `stage` | `string` | 规范化为 canonical stage |
| `message` | `string` | `message` |
| `channel` | `string` | `channel` |
| `text_delta` | `string` | `text_delta` |
| `content` | `string` | `content` |
| `payload_json` | `string` | 解析为 JSON `payload`；无效 JSON 会作为字符串编码 |
| `artifact_path` | `string` | Runtime 内部路径；公开 URL 由 API 生成 |
| `done` | `bool` | `done` |
| `error` | `string` | `error` |
| `sequence` | `int64` | Runtime 序号；持久化 Job SSE 使用数据库事件序号 |
| `outline_markdown` | `string` | `outline_markdown` |
| `event_version` | `int32` | `event_version` |
| `job_id` | `string` | `job_id` |
| `stage_status` | `string` | `stage_status` |
| `progress` | `DeckProgress` | `{current,total}` |
| `slide` | `DeckSlideEvent` | `{index,version}` |
| `severity` | `string` | `severity` |
| `action_required` | `string` | `action_required` |
| `retryable` | `bool` | `retryable` |
| `error_code` | `string` | `error_code` |

Runtime 的顶层 RPC 异常不会把异常详情直接返回客户端：文本/Deck 流只暴露安全摘要 `runtime request failed`，Deck 同时使用 `error_code: "runtime_internal"`；完整异常与堆栈仅写服务端日志。工作流内部主动产生的结构化业务错误仍按上表的 `error`/`error_code` 返回。

修改协议后从仓库根目录执行 `make proto`，不要手工编辑任一生成文件。

## 15. 旧接口兼容与弃用

### 15.1 兼容原则

旧路由没有 `/api/v1` 前缀，继续保留是为了迁移既有客户端。其普通 JSON 成功响应直接返回历史 payload，不含 `{data,meta}`；错误一般为 `{"message":"..."}`，绑定错误还可能有顶层 `error` 字符串。Request ID header 仍会返回。新代码不得依赖旧 payload 形状。

除 `/healthz`、`/login`、`/refresh`、`/register` 外，旧路由同样需要 Bearer access token。

### 15.2 路由映射

| Legacy | Canonical successor | 备注 |
| --- | --- | --- |
| `GET /healthz` | `GET /api/v1/health` | legacy 返回裸 `{status}` |
| `POST /register` | `POST /api/v1/auth/register` | 公开 |
| `POST /login` | `POST /api/v1/auth/login` | 公开 |
| `POST /refresh` | `POST /api/v1/auth/refresh` | 公开 |
| `GET /me` | `GET /api/v1/auth/me` |  |
| `POST /logout` | `POST /api/v1/auth/logout` |  |
| `GET /providers` | `GET /api/v1/providers` |  |
| `POST /provider-credentials/test` | `POST /api/v1/providers/credential-tests` | 路径命名已统一 |
| `GET /model-configs` | `GET /api/v1/model-configs` |  |
| `POST /model-configs` | `POST /api/v1/model-configs` |  |
| `GET /model-configs/:id` | `GET /api/v1/model-configs/:id` |  |
| `PUT /model-configs/:id` | `PATCH /api/v1/model-configs/:id` | legacy PUT 实际也是部分更新 |
| `DELETE /model-configs/:id` | `DELETE /api/v1/model-configs/:id` |  |
| `GET /agents` | `GET /api/v1/agents` |  |
| `GET /agents/:id` | `GET /api/v1/agents/:id` |  |
| `GET /agent-configs` | `GET /api/v1/agent-configs` |  |
| `POST /agent-configs` | `POST /api/v1/agent-configs` |  |
| `GET /agent-configs/:id` | `GET /api/v1/agent-configs/:id` |  |
| `PUT /agent-configs/:id` | `PATCH /api/v1/agent-configs/:id` | legacy PUT 实际也是部分更新 |
| `DELETE /agent-configs/:id` | `DELETE /api/v1/agent-configs/:id` |  |
| `POST /agent/stream-text` | `POST /api/v1/text-generations` | SSE |
| `GET /ppt-agent/conversations` | `GET /api/v1/presentations/conversations` |  |
| `GET /ppt-agent/conversations/:id` | `GET /api/v1/presentations/conversations/:id` |  |
| `DELETE /ppt-agent/conversations/:id` | `DELETE /api/v1/presentations/conversations/:id` |  |
| `POST /ppt-agent/jobs` | `POST /api/v1/presentations/jobs` | 推荐立即迁移 |
| `GET /ppt-agent/jobs/:job_id` | `GET /api/v1/presentations/jobs/:job_id` |  |
| `GET /ppt-agent/jobs/:job_id/events` | `GET /api/v1/presentations/jobs/:job_id/events` | 可续传 SSE |
| `POST /ppt-agent/jobs/:job_id/actions` | `POST /api/v1/presentations/jobs/:job_id/actions` |  |
| `POST /ppt-agent/jobs/:job_id/cancel` | `POST /api/v1/presentations/jobs/:job_id/cancel` |  |
| `GET /ppt-agent/metrics` | `GET /api/v1/presentations/metrics` |  |
| `GET /ppt-agent/artifacts/:request_id[/<asset>]` | `GET /api/v1/presentations/artifacts/:request_id[/<asset>]` | 文件响应 |

### 15.3 仅 legacy 的 PPT 直连流

`POST /ppt-agent/generate` 和 `POST /ppt-agent/resume` 是旧版“HTTP 请求直接持有 Runtime SSE”的接口，**没有 `/api/v1` 版本**。两者响应会带：

```http
Deprecation: true
Link: </api/v1/presentations/jobs>; rel="successor-version"
```

或 resume 的：

```http
Link: </api/v1/presentations/jobs/:job_id/actions>; rel="successor-version"
```

`/ppt-agent/generate` 接受与创建 Job 相同的 multipart/JSON 生成参数，但直接返回 PPT SSE；`/ppt-agent/resume` 接受：

```json
{
  "request_id": "<runtime request uuid>",
  "outline_markdown": "...",
  "action": "approve_outline",
  "payload": {}
}
```

迁移方式：

1. 用 `POST /api/v1/presentations/jobs` 创建后台 Job；
2. 用 `GET .../jobs/:job_id/events` 消费和恢复事件；
3. 遇到 `interrupt` 后用 `POST .../jobs/:job_id/actions` 提交确认；
4. 刷新页面时用 `GET .../jobs/:job_id` 恢复状态。

只有 `generate`/`resume` 目前显式返回 `Deprecation` header；其他无前缀兼容路由虽未发该 header，也不应被新客户端采用。代码当前未声明具体移除日期。
