# Agent Task Platform

## PPTAgent 后台任务链路

PPTAgent 现在以可恢复后台 Job 运行，不再依赖浏览器维持一次 SSE 长连接：

- `POST /ppt-agent/jobs` 创建任务并立即返回 `job_id`
- `GET /ppt-agent/jobs/:job_id/events` 订阅追加式事件；支持 SSE `id`、`Last-Event-ID` 和客户端去重
- `POST /ppt-agent/jobs/:job_id/actions` 幂等提交大纲确认、重新生成大纲、风格选择或单页重做
- `POST /ppt-agent/jobs/:job_id/cancel` 持久化取消状态并终止后台 gRPC
- `GET /ppt-agent/jobs/:job_id` 在刷新后恢复状态，`GET /ppt-agent/metrics` 返回产品质量指标
- artifact 下载会根据 Job/Conversation 的 `user_id` 鉴权，不能仅凭 `request_id` 跨用户读取

MySQL migration `007_create_ppt_deck_jobs.sql` 保存 Job、附件、追加式事件、幂等 action 和版本化 asset。Python LangGraph checkpoint 使用持久化 SQLite（`artifacts/deck-checkpoints.sqlite3`）；waiting 状态可跨 runtime 重启恢复。旧 `/ppt-agent/generate` 与 `/ppt-agent/resume` 保留为新 Job 模型的 SSE 兼容层。

前端提供“快速生成 / 逐步确认”、大纲编辑/重新生成、风格反馈、质量与降级警告、取消和单页重做。预览使用版本化资源路径，重做后不会继续复用旧 Object URL。

验证命令：

```bash
go test ./...
cd python-agent && .venv/bin/python -m pytest tests -q
cd ../front && node node_modules/vue-tsc/bin/vue-tsc.js -b --pretty false
```

## 当前完成情况

目前项目已经完成了基础认证系统：

- `POST /register`：用户注册
- `POST /login`：用户登录
- `POST /refresh`：刷新 access token
- `POST /logout`：退出登录并使当前会话失效
- `GET /me`：获取当前登录用户信息
- `GET /healthz`：服务健康检查

当前后端基础能力：

- 使用 `Gin` 作为 HTTP 框架
- 使用 `MySQL` 存储用户数据
- 使用 `Redis` 存储登录会话、refresh token、access token 黑名单
- 使用 `JWT` 作为 access token 和 refresh token

## 接下来做什么

建议按下面的阶段逐步推进。

### 第一阶段：模型配置管理

目标：

- 让用户先保存自己要使用的模型配置
- 支持不同 provider、不同模型、不同参数

建议字段：

- provider
- model_name
- base_url
- api_key
- temperature
- max_tokens
- is_default
- status

建议接口：

- `POST /model-configs`
- `GET /model-configs`
- `GET /model-configs/:id`
- `PUT /model-configs/:id`
- `DELETE /model-configs/:id`
- `PATCH /model-configs/:id/activate`

### 第二阶段：聊天 MVP

目标：

- 让用户可以向 AI 发送消息
- 存储会话和聊天记录

建议数据表：

- `chat_sessions`
- `chat_messages`

建议接口：

- `POST /chat/sessions`
- `GET /chat/sessions`
- `GET /chat/sessions/:id`
- `POST /chat/sessions/:id/messages`
- `GET /chat/sessions/:id/messages`

这一阶段的最小可用流程：

- 用户选择一个模型配置
- 后端根据该模型配置调用模型接口
- 将用户消息和 AI 回复都写入 `chat_messages`

### 第三阶段：Agent 配置

目标：

- 在模型配置之上增加一层 `agent` 抽象
- 让用户配置“一个带人设和规则的智能体”

建议字段：

- name
- description
- model_config_id
- system_prompt
- welcome_message
- memory_strategy
- tool_config
- status

建议接口：

- `POST /agents`
- `GET /agents`
- `GET /agents/:id`
- `PUT /agents/:id`
- `DELETE /agents/:id`

### 第四阶段：Agent 对话

目标：

- 用户不再直接和模型对话
- 改为和某个 agent 对话

建议接口：

- `POST /agent-chat/sessions`
- `GET /agent-chat/sessions`
- `POST /agent-chat/sessions/:id/messages`
- `GET /agent-chat/sessions/:id/messages`

到这一阶段，整体链路会变成：

`用户 -> agent -> model config -> provider API`

### 第五阶段：高级 Agent 能力

后续可以继续加入：

- 流式输出
- tool calling
- 文件上传
- 知识库 / RAG
- 多步骤任务规划
- 自动生成会话标题
- token 用量统计
- 调用日志和审计日志

## 用户应该跟模型聊，还是跟 Agent 聊？

简短结论：

- 从产品视角看，用户更适合“跟 agent 聊”
- 从第一版实现角度看，先做“跟 model config 聊”会更简单

推荐落地路径：

1. 先做 `model config`
2. 再做 `chat session + chat message`
3. 先跑通一版“直接调用模型聊天”
4. 再加 `agent`
5. 让 `agent` 绑定一个 `model config`

这样做更稳的原因：

- 模型调用是最底层的核心能力
- 聊天记录的存储可以先独立完成
- 后面加 agent 时，可以复用前面的聊天能力

如果你希望一开始在 API 设计上就更像“产品系统”，也可以对外先暴露 `agent` 概念，但内部先做一个很薄的 agent：

- 一个 agent 只绑定一个 model config
- 一个 agent 先只支持一个 system prompt
- 一个 agent 暂时不接 tools

这是一个比较平衡、也很适合当前阶段的做法。

## 建议的近期实现顺序

如果继续一步一步做，接下来最推荐的三个任务是：

1. 建 `model_configs` 表，并完成对应 CRUD 接口
2. 建 `chat_sessions` 和 `chat_messages` 表
3. 先做一个简单的聊天接口，能够按选定模型配置调用模型

## 当前项目目标

这个项目现在可以逐步演进成：

`认证系统 + 会话管理 + 模型配置管理 + Agent 配置管理 + 聊天平台`
