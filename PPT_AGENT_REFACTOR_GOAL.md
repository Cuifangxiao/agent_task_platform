# PPTAgent 全链路重构 Goal

## Goal objective

在 `/Users/cxf/Desktop/Project/agent_task_platform` 中完成 PPTAgent 的全链路重构，使 PPT 生成从“依赖单次 SSE 长连接的同步流程”升级为可恢复的后台任务，并系统性改善前后端沟通体验、内容可靠性、视觉设计质量和 PPTX 导出一致性。

这不是只输出方案的审查任务。需要检查现有代码、直接实现、补齐迁移与测试，并以本文件作为持续执行的唯一任务说明。

## Codex Goal 使用方式

建议在 Codex 中创建一个 Goal，并将 Objective 设置为：

> 按照 `PPT_AGENT_REFACTOR_GOAL.md` 完成 PPTAgent 全链路重构，优先交付可运行且测试通过的 P0，再持续完成 P1、P2，直到本文件中的验收标准全部满足。

执行规则：

1. 先阅读本文件和相关代码，输出简短执行计划，然后直接实施。
2. 不要只停留在分析、设计文档、接口空壳或 TODO。
3. 每次 Goal 自动续跑时，先检查当前 Git diff、测试结果和未完成清单，从上次进度继续，不要推倒重来。
4. 优先完成 P0；P0 未通过验收前，不要把主要精力投入低优先级视觉扩展。
5. 一个阶段完成后运行相应测试，再更新任务清单。
6. 不要擅自提交、推送或覆盖与本任务无关的用户改动。
7. 如果受环境阻塞，保留已经完成的正确实现，记录具体阻塞证据，并继续处理不受阻塞影响的部分。

## 开始前需要阅读

- `front/src/pages/PptAgentPage.vue`
- `front/src/api/client.ts`
- `front/src/types.ts`
- `internal/pptagent/`
- `internal/pythonruntime/client.go`
- `api/proto/v1/provider/provider.proto`
- `database/mysql/`
- `python-agent/core/deck/`
- `python-agent/core/deck/REDESIGN.md`
- `python-agent/core/deck/REDESIGN_PHASE2.md`
- `python-agent/tests/`

实施前先运行并记录基线：

```bash
git status --short
go test ./...
cd python-agent && .venv/bin/python -m pytest tests -q
cd ../front && node node_modules/vue-tsc/bin/vue-tsc.js -b --pretty false
```

不要因为工作区已有修改而重置、checkout 或删除用户文件。

---

# P0：必须优先完成

## 1. 后台 Job 与可恢复任务生命周期

当前 PPT 生成依赖一次 HTTP SSE 长连接。断网、刷新或前端停止会取消 Go context、gRPC 和 Python LangGraph，历史任务还可能永久停在 `running`。

将其改造成“后台任务 + SSE 订阅”模型：

1. 增加持久化的 Deck Job 和 Job Event 数据结构及数据库迁移。
2. Job 至少支持以下状态：
   - `queued`
   - `running`
   - `waiting_for_outline`
   - `waiting_for_style`
   - `exporting`
   - `success`
   - `success_with_warnings`
   - `needs_review`
   - `failed`
   - `cancelled`
3. 创建任务后尽快返回 `job_id`；后台 Worker 独立执行，不能依赖浏览器连接存活。
4. SSE 只负责订阅事件，必须支持：
   - `sequence` 或 SSE event id
   - `Last-Event-ID` 或等价的断线续传
   - 页面刷新后恢复任务状态与历史事件
   - 客户端事件去重
5. 提供显式取消接口。前端“停止”必须调用取消接口，并将任务持久化为 `cancelled`。
6. Python、gRPC 或网络异常必须保存真实 stage、错误码、`retryable` 和安全的错误摘要，不能只返回 `stream interrupted`。
7. LangGraph 不能只依赖 `InMemorySaver`。增加持久化 checkpoint 实现；开发环境可使用持久化 SQLite，生产实现保留可替换接口。
8. 保留现有 `/ppt-agent/generate` 和 `/ppt-agent/resume` 的兼容能力，必要时将其改成新 Job API 的兼容层。
9. Job action 必须具备幂等性，避免重复确认大纲、重复选择风格或重复单页重做。

### P0 验收

- 浏览器断开 SSE 后，后台任务继续执行。
- 刷新页面后可以恢复任务和事件进度。
- waiting 状态在服务重启后仍可继续。
- 点击停止后持久化为 `cancelled`，不会在历史中继续显示 `running`。
- 传输异常会持久化为 `failed`，并保留 request/job id 和发生阶段。

## 2. 统一、版本化的事件协议

目前 Python、Go、Vue 分别维护阶段名称，存在 `review`、`review_vlm`、`repair` 等映射不一致。

定义统一事件协议，至少包含：

```json
{
  "event_version": 1,
  "job_id": "...",
  "request_id": "...",
  "conversation_id": 1,
  "sequence": 1,
  "event_type": "...",
  "stage": "...",
  "stage_status": "pending|running|waiting|done|warning|error",
  "message": "...",
  "progress": {
    "current": 1,
    "total": 10
  },
  "slide": {
    "index": 1,
    "version": 2
  },
  "severity": "info|warning|error",
  "action_required": "approve_outline|choose_style|null",
  "retryable": false,
  "payload": {}
}
```

要求：

1. 更新 proto、Go DTO、Python contract 和 TypeScript 类型。
2. 使用项目已有 proto 生成流程，不要手改生成文件。
3. 统一 stage 枚举，至少覆盖：
   - `brief`
   - `outline`
   - `outline_review`
   - `style`
   - `style_review`
   - `writer`
   - `designer`
   - `validate`
   - `review_vlm`
   - `repair`
   - `coherence`
   - `export`
4. 前端根据结构化状态渲染，不要通过 `message` 文案猜测任务状态。
5. 不再把 base64 缩略图长期写入聊天历史 JSON；数据库保存资源 URL、版本和质量状态。
6. Job Event 使用追加式存储，避免每个事件都重写不断增长的整段状态 JSON。
7. 预览资源必须包含版本号或版本查询参数，避免更新后命中旧 Object URL。

### P0 验收

- Python、Go、TypeScript 使用同一组 stage/event 语义。
- `sequence` 能用于重放、去重和恢复。
- `review_vlm`、`repair`、`coherence` 能在前端进度中正确显示。
- 单页重做后立即显示新版本预览。

## 3. 禁止静默丢内容

`python-agent/core/deck/spec.py::compact_slide_spec` 当前会在校验前裁掉标题、正文、列表项和表格内容，这是不可接受的。

要求：

1. 禁止在质量校验前静默截断或丢弃用户/模型内容。
2. 保存 raw slide spec 和 final slide spec。
3. 密度超限按以下顺序处理：
   - Writer 重写精简
   - Designer 切换低密度版式
   - 必要时拆成两页
   - 仍失败则标记 `needs_review`
4. 不允许用简单字符串切片伪装修复成功。
5. 确定性 fallback 不能生成占位文本，也不能重复同一句话填满页面。
6. 修复过程必须记录原因、修改轮次和最终状态。

### P0 验收

- 长标题、超长正文、五条以上 bullet、超大表格不会被静默删除。
- 修复失败时任务不会显示为完全成功。
- 新测试能够证明语义内容没有在 normalization 阶段丢失。

## 4. 前端开放真实修改能力

当前页面看起来是聊天，但发送新消息会重置当前对话；后端已有单页重做能力，前端却没有入口。

完成以下交互：

1. 根据任务阶段切换操作区：
   - 初始阶段：输入需求、上传附件
   - 大纲阶段：编辑、确认、重新生成大纲
   - 风格阶段：选择样张并输入风格调整意见
   - 页面阶段：逐页预览、查看警告和状态
   - 完成阶段：下载、全局修改、单页重做
2. 每张幻灯片增加“重做此页”入口，提交：

```json
{
  "action": "regenerate_slide",
  "payload": {
    "index": 3,
    "feedback": "减少文字，改成左右对比"
  }
}
```

3. 单页重做后必须显示新版本，不能继续使用旧 Object URL。
4. 已完成对话中的新消息应作为当前 Deck 的修改请求；如果当前版本无法支持全局修改，就修正文案并明确能力边界，不能继续误导用户。
5. 显示真实阶段、页数进度、可重试错误、每页质量警告、自动修复记录和导出降级状态。
6. 增加“快速生成”和“逐步确认”两种模式。
7. 上传前检查格式、单文件大小、总大小，并显示上传和解析状态。

## 5. Artifact 用户隔离

当前 artifact 下载只校验 UUID 和路径，没有确认资源属于当前用户。

必须完成：

1. 下载 PPTX、PNG、HTML 前验证 conversation/job 的 `user_id`。
2. 不允许仅凭 `request_id` 下载其他用户资源。
3. 增加跨用户下载返回 403/404 的测试。
4. HTML 预览继续禁止外链资源和任意脚本。

---

# P1：生成质量与导出质量

## 6. 真正启用 Designer 和 Coherence

数据库要求绑定 `designer`、`coherence` 模型，但当前生成图没有真正调用它们。

要求：

1. Writer 只负责结构化内容。
2. Designer 负责：
   - 选择 component
   - 选择 layout variant
   - 确定视觉层级
   - 生成受控的 design tokens/custom slots
3. Designer 不允许输出任意外链、任意脚本或不受控 HTML。
4. 每个主要 component 至少提供 3 个可靠的版式变体，避免所有 Deck 只是换颜色。
5. `motif` 必须真正影响装饰构图，而不是只保存在 JSON 中。
6. Coherence 节点在导出前检查并修复：
   - 重复标题
   - 跨页重复内容
   - 术语不一致
   - 叙事断层
   - 章节节奏
   - 页码和顺序
7. 删除未使用的模型绑定，或者确保每个 `requires_model` 节点在运行时真实调用，并增加调用测试。

## 7. 来源和事实可靠性

当前只检查 fact id 是否存在，无法确认数字和结论真的来自原文。

要求：

1. 附件按文件、页码、段落切块并保存 provenance。
2. 建立 fact ledger，包含：
   - fact id
   - statement
   - value
   - unit
   - source file
   - page/section
   - source excerpt
3. Writer 每页只接收与当前大纲相关的检索片段，不要每页重复发送全部附件文本。
4. 检测所有数字、百分比、金额、日期和关键实体是否有来源。
5. `sources` 不仅要验证 fact id 存在，还要验证可见数字与对应事实一致。
6. 引用写入 speaker notes；必要时支持页面底部显示精简来源。
7. 无来源的具体数字必须触发 `must_fix`，不能直接导出。

## 8. VLM 与质量门禁

1. VLM Review 必须校验绑定模型是否支持图片输入；前端配置时也要提示能力。
2. 大量页面分批审查，同时保留全局 contact sheet 检查整套一致性。
3. VLM 调用失败时输出明确降级状态，不能静默当作审查通过。
4. repair budget 用尽且仍有 `must_fix` 时，任务必须进入 `needs_review` 或 `success_with_warnings`。
5. 最终质量状态和具体问题必须在前端可见。

## 9. PPTX 导出一致性

1. 预览和 PPTX 使用同一套确定字体资源。
2. 混合导出尽量保留：
   - 字体族
   - 字号
   - 粗细
   - 颜色
   - 对齐
   - 行距
   - 项目符号
3. 导出后检查：
   - 文件可打开
   - 页数正确
   - 每页可编辑文本框数量合理
   - 无文字溢出
   - 字体可用
   - 预览和导出画面尽可能一致
4. Playwright 或文字层提取失败而降级为整页图片时：
   - 记录 degraded export
   - 前端提示文字不可编辑
   - 最终状态不能伪装成无警告成功
5. 增加导出视觉回归测试；如果环境有 LibreOffice，将 PPTX 回渲染为 PNG 与预览做差异检查，否则至少做结构和文本层检查。

---

# P2：性能、体验和可观测性

## 10. 性能优化

1. Writer 按页面进行有上限的并行处理，建议并发 3–4，禁止无限并发。
2. 保持页面顺序稳定，并提供前后页摘要维持叙事一致性。
3. `BrowserSession` 不是线程安全的，不要跨线程共享；使用安全页面池或顺序渲染。
4. 避免附件在 Go、gRPC、Python 之间多次完整复制；优先使用上传资源 ID 或受控临时对象存储。
5. 前端显示上传、解析、生成、审查和导出的分阶段耗时。

## 11. 可观测性

为每个节点记录：

- 耗时
- provider/model
- token 使用量
- 重试次数
- fallback 次数
- repair 次数
- VLM 问题数量
- export 是否 degraded

同时增加可用于产品质量评估的指标：

- 任务成功率
- 断线恢复率
- 单页重做率
- must-fix 平均数量
- 自动修复成功率
- 用户下载成功率
- 平均生成时间

---

# 测试要求

完成后至少运行：

```bash
go test ./...
cd python-agent && .venv/bin/python -m pytest tests -q
cd ../front && node node_modules/vue-tsc/bin/vue-tsc.js -b --pretty false
```

如果 Vite 构建因为本地缺失 Rolldown 原生可选依赖失败，记录环境原因；不要通过删除 lockfile、删除 node_modules 或随意重装依赖破坏工作区。

必须新增或补齐以下测试：

- SSE 断线后任务继续
- 事件按 sequence 重放和去重
- 服务重启后 waiting 任务可恢复
- action 幂等性
- 取消任务持久化
- 传输异常持久化为 failed
- stage 映射一致
- 单页重做后预览版本更新
- 长内容不被静默截断
- Designer、Coherence 模型真实调用
- 无来源数字被拦截
- VLM 不支持图片时明确报错
- degraded export 状态
- artifact 用户隔离
- 前端关键阶段交互

同步更新：

- `README.md`
- `接口文档.md`
- `python-agent/README.md`
- PPT 重构方案文档

确保文档与最终实现一致。

---

# 最终完成标准

只有同时满足以下条件，Goal 才能标记 complete：

1. P0、P1、P2 的必要实现均已落地，没有只留接口或 TODO。
2. 任务不再依赖单次 SSE 连接存活。
3. 刷新、断线、取消、失败和恢复都有正确持久化状态。
4. 不再发生静默内容截断。
5. 前端可以选择风格反馈、查看质量警告并重做单页。
6. Designer、Coherence 和 VLM 的实际行为与配置一致。
7. 数字和关键事实具备可验证来源。
8. PPTX 降级和质量风险对用户透明。
9. Artifact 下载具备用户隔离。
10. Go、Python、前端类型检查及新增测试全部通过；环境性失败有明确证据。
11. 没有覆盖与本任务无关的用户改动。

若因为明确的外部依赖无法满足某项，不能直接宣称完成；必须记录阻塞证据、已完成范围和解除阻塞所需条件。
