# PPTAgent 全链路重构实现说明

## 验收结论

`PPT_AGENT_REFACTOR_GOAL.md` 中 P0、P1、P2 的必要代码、迁移和测试已经落地。当前没有用 TODO、空接口或硬编码指标代替验收实现。Goal 原文及其他 Goal 相关 Markdown 未在本轮修改。

唯一未能在本机完成的是 Vite production bundle：现有 `front/node_modules` 缺少 Rolldown 的 Darwin ARM64 原生可选包。前端类型检查与关键交互测试均通过，具体证据见“验证记录”。

## 相关目录与文件清单

- Go Job、SSE、鉴权与指标：`internal/pptagent/`、`internal/router/router.go`、`internal/app/app.go`
- 数据库迁移：`database/mysql/007_create_ppt_deck_jobs.sql`、`database/mysql/008_externalize_ppt_deck_sources.sql`
- 统一 gRPC 协议：`api/proto/v1/provider/provider.proto` 及通过 `make proto` 生成的 Go/Python 文件
- Python 生成流水线：`python-agent/core/deck/`
- Python 测试：`python-agent/tests/`
- 前端任务交互：`front/src/pages/PptAgentPage.vue`、`front/src/pptAgentWorkflow.ts`、`front/src/api/client.ts`、`front/src/types.ts`
- 前端关键交互测试：`front/tests/pptAgentWorkflow.test.ts`

## P0：后台任务、协议与内容保真

创建请求先持久化 `ppt_deck_jobs`、来源元数据和初始事件，Go 后台 worker 使用独立于 HTTP 的 context 调用 Python。SSE 只订阅追加式 Job Event；事件 sequence 在数据库事务中递增，支持 `Last-Event-ID`、刷新恢复和前端去重。取消先持久化 `cancelled`，再取消 worker。传输失败保存实际 stage、request/job id、安全错误摘要、错误码和 retryable。

Job 覆盖 queued、running、waiting_for_outline、waiting_for_style、exporting、success、success_with_warnings、needs_review、failed、cancelled。waiting job 可在重启后继续接收 action；被进程中断的 running/exporting job 会以 `worker_restarted` 持久化为可重试失败。Action 通过 `(job_id, idempotency_key)` 幂等，大纲确认、重新生成、风格选择和单页重做共用同一路径。

LangGraph checkpoint 使用持久化 SQLite adapter。事件协议版本为 v1，Python、Go、TypeScript 统一 brief、outline、outline_review、style、style_review、writer、designer、validate、review_vlm、repair、coherence、export。兼容 `/ppt-agent/generate` 与 `/ppt-agent/resume` 已转为 Job 兼容层。

Slide normalization 不裁标题、正文、列表、表格或图表 series。raw/final spec 分开保存；单页重做还保存版本化 raw spec。密度处理顺序为 Writer 重写、Designer 低密度 variant、保留全部内容拆页、无法修复则 needs_review。Pillow/浏览器 fallback、修复轮次和原因都会形成事件或 must-fix，不会伪装成完整成功。

前端支持快速生成/逐步确认、附件校验及状态、大纲编辑/确认/重做、风格反馈、逐页警告、单页重做、版本化预览、取消、重连和质量/降级展示。已完成 Deck 的全局修改边界有明确文案，不会重置并误导用户。

Artifact 读取先验证 Job user_id。HTML、PNG、PPTX 不能只凭 request_id 跨用户访问；HTML 同时经过脚本、事件属性、iframe/object/embed、外链和 `javascript:` 静态检查，并使用 CSP sandbox。

## P1：生成、事实、VLM 与导出质量

Designer 会真实调用绑定模型，只能选择组件、balanced/editorial/split、视觉层级及白名单 design 值。所有目录组件都有三种确定性几何构图；受控 design 字段实际进入 CSS class。motif 经过稳定哈希生成装饰偏移与角度，不再只是 JSON 元数据。Designer 输出的 HTML、CSS、脚本、URL 和非白名单字段均不会进入页面。

Coherence 在导出前真实调用独立绑定模型，支持受控标题更新、精确术语/内容替换、转场 notes 和完整 permutation 页序修复。数字不能在 coherence replacement 中被改变。修复后的页面会重新渲染为新版本，再经过 validate；重复标题、重复内容、术语、叙事断层、章节节奏或页序问题无法安全解决时保留 must-fix，并最终进入 needs_review。

附件按文件、页码和段落保存 source chunks。fact ledger 不再固定截为 24 条，包含 fact id、statement、value、unit、文件、页/段和 excerpt，并随成品写入 `fact-ledger.json`；provenance 写入 `source-chunks.json`。Writer 每页只收到相关 facts 和检索片段。数字、百分比、金额、日期、图表数值和关键实体必须与本页引用 fact 匹配；有外部资料的 evidence/context 页必须引用来源。未支持的主张是 must-fix。详细引用写入 speaker notes。

VLM 必须存在且声明图片输入能力；否则分别产生 `vlm_model_missing` 或 `vlm_image_unsupported` must-fix。大 Deck 同时审查全局 contact sheet 和每 8 页 batch；调用失败产生 `vlm_review_failed`，不会当作通过。repair budget 用尽后，artifact 仍携带全部 issues，并进入 needs_review。

PPTX 混合导出复用 HTML 的实际字体、字号、粗细、颜色、对齐、行距和 bullet。导出后重新打开文件，校验页数、可编辑文本框数量、字体记录、文本框尺寸与画布边界，并生成 `export-manifest.json`。文字层失败的页记录 degraded page，前端明确提示不可编辑。无 LibreOffice 的环境使用结构、文本层、bullet、字体和 PPTX 内嵌背景字节一致性回归测试。

## P2：性能、资源引用与真实观测

Writer 使用上限 4 的线程池，future 按原页序收集；prompt 提供前后页 outline 摘要。Designer 和 BrowserSession 保持顺序执行，不在线程间共享浏览器对象。

附件上传后写入权限为 0600 的受控 `deck-sources/{job_id}` 目录。数据库只保存 resource id、相对路径、大小和 SHA-256；Go 调用 gRPC 时发送受控本地资源引用，不再重新装载完整 BLOB。Python 仅允许读取 `DECK_SOURCES_DIR`（默认与 deck artifacts 同级）内的真实文件，并复核路径、大小和 SHA-256；旧任务仍兼容内联 bytes。

前端持久显示上传/任务创建耗时，Python 在 graph 前发送附件解析耗时；生成、审查、修复、coherence 和 export 的节点事件显示各自耗时。

节点 metrics 不再填固定 0。实际模型响应会收集 provider/model、token 使用量、模型调用数、JSON/调用重试次数、fallback 次数、repair 次数、VLM 问题数和 export degraded；并发 Writer 通过 context 传播共享线程安全 collector，Designer 单独记录自己的模型指标。

`GET /ppt-agent/metrics` 提供任务成功率、断线任务数、断线恢复率、单页重做率、最终 artifact 的平均 must-fix 数、自动修复成功率、已导出任务下载率和平均生成时间。SSE 断开、恢复和下载均写入产品事件。

## 验证记录

- `go test ./...`：通过。
- `go test -race ./internal/pptagent`：通过。
- `go vet ./...`：通过。
- `cd python-agent && .venv/bin/python -m pytest tests -q`：通过；仅有 Python 3.14 下 Pydantic v1 compatibility warning。
- `cd front && npm run test:ppt`：3 项关键阶段、SSE 去重和版本缓存测试通过。
- `cd front && node node_modules/vue-tsc/bin/vue-tsc.js -b --pretty false`：通过。
- `git diff --check`：通过。

环境证据：

- `npm run build` 先被 `.bin/vue-tsc` 的本地执行权限阻塞；直接运行 Vue 类型检查已通过。
- `node node_modules/vite/bin/vite.js build` 进入 Vite 后失败，根因是缺少 `@rolldown/binding-darwin-arm64`。遵照任务约束，没有删除 lockfile/node_modules，也没有擅自重装依赖。
- 当前 Python venv 未安装 requirements 中的 Playwright，系统也没有 `soffice`/LibreOffice。运行时会显式标记预览或文本层降级；测试按目标要求覆盖了无 LibreOffice 时的结构与文本层回归路径。
