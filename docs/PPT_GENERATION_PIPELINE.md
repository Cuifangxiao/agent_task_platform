# PPT HTML-first 生成流水线

当前生产路径是一条固定为 `16:9` 的 HTML-first 流水线：LLM 先生成结构化内容和单页 HTML/CSS 片段，Runtime 再在受控 Chromium 中完成 DOM 检测与 PNG 截图，最终将每页 PNG 全出血铺入 PPTX。PPTX 与页面预览图视觉一致，但页面内容不可在 PowerPoint 中逐元素编辑。

```text
Web
  ├─ POST /api/v1/ppt/tasks           创建任务
  ├─ GET  /api/v1/ppt/tasks           查询历史任务
  └─ WS   /api/v1/ppt/ws              订阅、重放、取消任务
              │
              ▼
Go API：鉴权、解析 5 个模型绑定、持久化 run/event、WebSocket 广播
              │ GeneratePpt(request) -> stream AgentEvent
              ▼
Python Runtime：Send map-reduce LangGraph -> HTML -> DOM QA -> PNG -> image-only PPTX
              │
              ▼
Go API：ppt_runs + ppt_run_events (MySQL)，TaskHub 仅做热缓存/实时 fan-out
```

## 1. 请求与实时事件

所有 HTTP 接口均在 `/api/v1` 下且需要登录。

| 方法 | 路径 | 用途 |
|---|---|---|
| `POST` | `/ppt/tasks` | 创建任务，支持 JSON 或 multipart 附件，返回 `202` 和 run 快照 |
| `GET` | `/ppt/tasks?thread_id=&limit=` | 从 MySQL 查询当前用户的历史任务 |
| `GET` | `/ppt/tasks/{task_id}` | 从 MySQL 读取 run 快照 |
| `POST` | `/ppt/tasks/{task_id}/cancel` | 取消仍在运行的任务 |
| `GET` | `/ppt/tasks/{task_id}/file` | 下载最终 PPTX |
| `GET` | `/ppt/tasks/{task_id}/artifacts/*asset_path` | 下载任务目录内的其他产物 |
| `WS` | `/ppt/ws` | 复用一条 WebSocket 订阅多个 run |

WebSocket 使用版本化 JSON envelope，浏览器通过 `Sec-WebSocket-Protocol` 完成鉴权。客户端命令包括 `run.subscribe`、`run.unsubscribe`、`run.cancel` 和 `ping`；服务端返回 `connection.ready`、`command.ack`、`run.snapshot`、`run.event`、`command.error` 和 `pong`。

订阅命令中的 `payload.after` 是已消费的最大 `sequence`。Go 会先从 MySQL 的 `ppt_run_events` 重放更大的序号，再转发 TaskHub 的实时事件。因此断线重连不依赖进程内历史缓存。`event_id` 用于事件身份，`sequence` 用于单个 run 内排序和续传。

MySQL 是 run 快照和完整事件日志的真源：

- `ppt_runs` 保存任务状态、阶段、水位、结果和 worker heartbeat；
- `ppt_run_events` 以 `(run_id, sequence)` 为主键保存规范事件；
- TaskHub 仅负责当前进程的取消控制、有界热缓存和 WebSocket fan-out；
- API 重启后可继续查询历史 run 并重放已持久化事件，但不会恢复正在运行的 LangGraph。启动恢复会将遗留的 running run 以 `SERVER_RESTARTED` 持久化为失败终态。

Go 在创建任务前解析 `brief`、`outline`、`style`、`writer`、`designer` 五个模型节点。绑定缺失、模型停用或类型错误会在 worker 启动前拒绝请求。

## 2. Send map-reduce LangGraph

`core/deck/pipeline/graph.py` 将全局 planning/design 与单页生成分成两层。根图包含 8 个节点：`normalize_request -> plan_deck -> select_template -> create_design_spec -> generate_slide -> aggregate_slides -> assemble_image_pptx -> finalize_deck`。`create_design_spec` 完成后通过 LangGraph `Send` 为每个 `SlidePlan` 派发一份隔离 state；`generate_slide` 是含 11 个显式节点的单页子图：

`prepare_slide -> generate_slide_content -> generate_slide_html -> sanitize_and_assemble_html -> render_html_and_measure_dom -> validate_dom -> [repair/fallback] -> capture_png -> persist_slide_artifacts -> emit_slide_preview`。

完整路由如下：

```text
START
  -> normalize_request -> plan_deck
  -> select_template -> create_design_spec
  -> Send(generate_slide, slide_1 .. slide_n)
       └─ prepare_slide -> writer -> designer -> sanitize -> render/measure
                                                    ├─ valid -> capture_png
                                                    ├─ repair -> designer repair -> sanitize
                                                    └─ fallback -> sanitize
          -> persist_slide_artifacts -> emit_slide_preview
  -> aggregate_slides -> assemble_image_pptx -> finalize_deck -> END
```

`plan_deck` 按每个 recipe 的 variants 预分配 `layout_preference`，并把最近 4 页的规划期构图摘要传给 designer，因此各页不再依赖其他页面的实际 `layout_history`。并发分支只向 reducer-backed 的 `completed_slides`、`layout_history` 和 `validation_issues` 写入结果；`aggregate_slides` 在 fan-in 后校验页表完整性并一次性写 manifest。预览按完成顺序实时发布，消费者继续按 `page_index`/`page_id` 定位页面。

默认最多并发 6 个页面，可通过 `DECK_SLIDE_CONCURRENCY=1..30` 调整；Chromium 默认使用 3 个 thread-affine session，可通过 `DECK_BROWSER_CONCURRENCY` 调整，但不会超过页面并发数。每个 run 仍创建独立的 `PptGraph`、LLM 依赖和浏览器 session pool；浏览器对象、LLM client、文件句柄和图片二进制数据都不进入 LangGraph state。

## 3. Schema 与 state

LLM 边界使用 `core/deck/domain/schemas.py` 中的严格 Pydantic schema，默认 `extra="forbid"`。模型输出优先使用 structured output；退化路径也必须是完整 JSON，不从 Markdown fence 或文本子串中猜测 JSON。

| Schema | 职责 |
|---|---|
| `DeckBriefInterpretation` / `DeckBrief` | 首个 Brief Agent 直接解释用户原始输入，产出主题、受众、沟通目标、语言、页数、内容要求与 source mode；运行时附带原始来源文本供下游直接校验 |
| `DeckPlan` / `SlidePlan` | 整套叙事弧、连续的 `s01...` 页面名册、页面角色、结论式标题、布局族和节奏 |
| `DesignSpec` | 全局主题 ID、色板、中英文字体、字号、间距、形状、图标风格和背景 motif |
| `SlideContentSpec` | 单页结构化内容块、强调项和候选图标；精确主张直接接受 source text 守卫 |
| `HtmlSlideSpec` | 单页 layout family/variant、构图摘要、HTML body fragment、scoped CSS 和图标 key |
| `RendererDiagnostic` | `security` / `content` / `environment` 分类，严重度、selector、几何信息与修复建议 |
| `SlideArtifactMetadata` | 每页 HTML/PNG 路径、内容哈希、渲染环境、模型元数据、repair/fallback 结果 |

六个 active prompt 均采用“角色/任务、输入合同、权威顺序、决策规则、质量门、输出合同”的正式结构：

| Prompt | 核心决策 |
|---|---|
| `deck_brief.md` | 直接接收原始用户指令，区分任务命令与真实主题，理解自然语言页数、受众、内容和风格要求，产出可检验的 communication job |
| `deck_planner.md` | 选择叙事原型，按单页唯一任务分配证据、layout family 与页面节奏 |
| `design_director.md` | 锁定 catalog token，只细化适配本次叙事的背景、motif 与 density policy |
| `slide_content.md` | 按 recipe 选择 block 形态并逐项约束来源、限定条件、邻页去重与图标语义 |
| `html_slide_designer.md` | 从焦点和阅读路径出发构图，完整执行 content、DOM、CSS、SVG、安全和几何合同 |
| `html_repair.md` | 按诊断优先级和最小改动阶梯修复，不改事实、标题、主题或 layout family |

当前机器可读版本标识为 `deck-state.v4`、`html-deck.v5` 和 `chromium-html.v2`。六个正式节点提示词统一采用岗位合同：输入权威顺序、决策流程、角色级质量门、失败策略和严格输出边界均由 prompt 明确定义。prompt 由 `load_node_prompt()` 缓存，部署新版本后必须重启 runtime 进程。`core/deck/pipeline/state.py` 将 state 分为七个逻辑区域：

- `DeckRunState`：版本、run/request ID 和图内状态；失败终态及错误详情由图外的 service 事件统一发布；
- `DeckInputState`：用户描述、解析后文本、source chunks、页数、语言和模板偏好；
- `DeckPlanningState`：规范 `brief`、`deck_plan` 与预分配的 `layout_preferences`；
- `DeckDesignState`：选中主题与规范 `design_spec`；page blueprints 和 execution lock 只落盘，不进入 state；
- `DeckCurrentSlideState`：当前页内容、HTML、清洗结果、诊断、repair 次数和 PNG 元数据；
- `DeckGenerationState`：通过 reducer 归并的 `completed_slides` 幂等映射、构图历史和质量问题；
- `DeckOutputState`：最终 PPTX 的 `artifact_path`。

state 中存的 schema 对象都是 `model_dump(mode="json")` 后的可序列化字典。`current_slide_index` 是零基偏移，`SlidePlan.index` 和对外页码是一基。

## 4. 完整主题、布局 recipe 与图标

`core/deck/design_system/` 是 HTML 设计系统真源。主题和布局分层定义：一套主题锁定整份 deck 的色彩、字体、间距和 motif；每页再在相同主题下选用一个 recipe。因此每套主题都拥有完整的 10 种页型，而不是只有一张封面。

| 主题 ID | 适用场景 | 核心风格 |
|---|---|---|
| `paper_editorial` | 品牌、叙事、复盘、通用汇报 | 暖白纸张、番茄红信号色、编辑式构图 |
| `executive_signal` | 管理层、经营、战略、咨询 | 白底深蓝、单一橙色信号、证据与决策导向 |
| `midnight_product` | 产品发布、AI、技术方案、路演 | 深色背景、荧光青/琥珀、大尺度视觉锚点 |
| `alpine_research` | 研究、教学、学术、医疗 | 云白、松绿和冰川蓝，问题—方法—结论结构 |

模板参数只接受 `auto` 或上述四个当前主题 ID；显式传入未知或已删除的 ID 会直接报错。

10 个 layout family 为：`cover_minimal`、`section_break`、`statement_focus`、`editorial_split`、`three_points`、`comparison`、`process`、`timeline`、`key_metric`、`closing_resolution`。`recipes.json` 对每个 family 明确角色、slot、容量、结构、variant 和视觉锚点；缺少任一 family 或重复定义都会在 catalog 加载时失败。

`icons/lucide.json` 内置 24 个经校验的 Lucide SVG 图标，含中英文语义 tag 和第三方许可说明。LLM 只能输出 `<span data-icon="..."></span>` 占位符；清洗后由本地 trusted registry 注入 SVG，不允许远程图标、任意 SVG 属性或外部 URL。

## 5. HTML 安全边界、DOM QA 与修复

LLM 不输出完整文档，只输出一个 `HtmlSlideSpec`：页面 body fragment 和以 `.slide` 为作用域的 CSS。`HTMLAssembler` 在模型输出与 Chromium 之间建立了硬边界：

1. `html5lib` 解析 fragment，`bleach` 按 tag/attribute allowlist 清洗 HTML/SVG；
2. `tinycss2` 解析 CSS，只保留 `.slide` scoped selector、允许的 property 和受控 CSS variable；
3. 禁止 script、event handler、`href/src`、`url()`、HTTP/data URL、`@import`、外部字体、form、frame、worker 和可执行 CSS；
4. 强制 HTML/CSS 长度、DOM node、SVG path、CSS rule/declaration 和 shadow 预算；
5. 注入不可由模型修改的 `base.css`、`DesignSpec` token 与 CSP 文档壳。

Chromium 渲染配置固定为 `1600×900` CSS px，`deviceScaleFactor=2`，最终 PNG 为 `3200×1800`。request-scoped session pool 中每个 Playwright sync session 固定在自己的单线程 executor 上，多个 session 可并行渲染；每次渲染尝试仍创建独立 browser context 和 page。运行时禁止下载与 service worker，拦截所有网络请求，等待 `document.fonts.ready` 和两帧 `requestAnimationFrame`，并关闭动画、过渡和光标。DOM 测量后直接在同一 page 生成投机 PNG；验证失败立即删除，验证成功后 `capture_png` 只校验哈希并原子移动到正式路径，不会再次加载 HTML。截图还会验证尺寸与不透明性，字体 canvas 探测结果按单个 session 和字体族缓存。

DOM QA 至少检查：

- 恰好一个 `.slide` 根节点及固定画布尺寸；
- html/body/slide scroll overflow；
- `[data-box]` 语义区域的空内容、越界、文字溢出、隐藏与非预期重叠；
- 所有可见文本都必须属于语义区域，并检查有效透明度、文本/背景对比度和覆盖遮挡；
- 封面标题 `66px`、内页标题 `46px`、正文 `21px`、注释 `16px` 的最小字号；
- title line budget、DOM/SVG/shadow 预算、未解析图标和批准字体是否可用。

几何检查之外，Runtime 还对模型 HTML 做语义守卫：必须保留批准的 assertion title 和至少一个正文内容块；`key_metric` 必须显示原始 metric value；HTML 中出现的数字只能来自 `SlideContentSpec`（页码和步骤序号除外）。人物、引语、客户案例和已经实现的结果属于高风险事实形态：DeckPlan 和 SlideContentSpec 直接对 runtime 携带的 source text 做来源校验，HTML 设计模型也不能在排版阶段新增这类表述。建议、目标、可能性等非事实性框架文案仍可正常生成。这可以阻止“版式看起来正常，但模型偷偷换了标题、数字或事实”的页面进入截图。

`environment` 类错误（Chromium 缺失、字体不可用、超时、文件系统失败）直接终止任务；`security` 或 `content` 类问题最多触发两轮定向 HTML repair。修复仍失败时，按当前的 10 种 recipe 之一生成确定性 fallback HTML，并再次经过同一清洗和 DOM QA。fallback 也不通过时才将任务置为失败。

## 6. 预览事件与 PPTX

生产流程在每页 `capture_png -> persist_slide_artifacts` 成功后发送一个 `ARTIFACT_READY`：

- `resources` 中只有 `PAGE_PREVIEW` / `image/png`；
- `resource_id` 是稳定的 `sXX-preview`，同页重新发布时前端会替换而不是追加；
- `event_id` 由 `(request_id, slide_id, content_hash, preview_hash)` 确定；相同页面重放保持不变，内容或 PNG 改变时产生新 ID；
- `revision` 是该页已持久化的 `SlideArtifactMetadata.artifact_hash`，属于页预览事件必填字段；
- `page.page_id` 是稳定 `slide_id`，`page_index` 保持一基；
- 已清洗 HTML 仍持久化到任务目录供审计，但不作为页预览事件发布；
- 对外 `PptStage` 与事件类型保持稳定，新增的内部节点不暴露给前端。

最终导出使用空白 `16:9` presentation，每页只添加一张全出血 PNG。浏览器 capture 阶段先锁定预览为 `3200×1800`，导出器再校验 PNG/16:9，并反向打开 PPTX 检查页数、每页图片数量和画布覆盖范围；manifest 明确记录 `image_based: true` 与 `editable: false`。

## 7. 产物目录

Python 和 Go 必须访问同一个物理目录：

```text
Python: DECK_ARTIFACTS_DIR/{run_id}/...
Go:     python_agent.artifacts_dir/{run_id}/...
```

本地默认为 `apps/runtime/artifacts/decks`。单个任务的主要文件布局如下：

```text
{run_id}/
├─ sources/                              # 归一化文本与 provenance
├─ deck-brief.json
├─ deck-plan.json
├─ outline.md
├─ design-spec.json
├─ design-spec.md
├─ spec-lock.json
├─ spec-lock.md
├─ page-blueprints.json
├─ template-manifest.json
├─ slide_specs/
│  ├─ page-001.content.json
│  └─ page-001.html.json
├─ previews/
│  ├─ page-001.html                 # 已清洗、已组装的完整文档
│  └─ page-001.png                  # 3200×1800 对外预览
├─ diagnostics/page-001.json
├─ reports/
│  ├─ slide-artifacts.json
│  ├─ export-manifest.json
│  ├─ generation-manifest.json
│  └─ quality-report.json
└─ exports/deck.pptx
```

原始模型输出不写入可下载的任务树。它只进入
`${DECK_PRIVATE_DEBUG_DIR:-<artifacts-sibling-private-root>}/{run_id}/raw-model-output/`；生产环境可将
`DECK_PRIVATE_DEBUG_DIR` 指向独立、受限且带生命周期策略的卷。Python 事件只返回 run 目录下的相对路径，Go 校验 owner 和路径边界后再填充 HTTP URL。MySQL 只持久化 run/event 元数据，不存储 PNG/PPTX 二进制数据；容器间必须共享 artifact volume。

## 8. 本地安装与字体合约

先安装 Python 依赖和 Playwright Chromium：

```bash
cd apps/runtime
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/python -m playwright install chromium
```

Linux/CI 可让 Playwright 同时安装 Chromium 系统依赖：

```bash
.venv/bin/python -m playwright install --with-deps chromium
sudo apt-get update
sudo apt-get install -y fontconfig fonts-noto-cjk fonts-inter
```

四套主题只使用两种批准字体：中文与混排使用 `Noto Sans CJK SC`，拉丁文使用 `Inter`。Docker 镜像安装 `fonts-noto-cjk` 与 `fonts-inter`；自建运行环境可将相应 `.ttf`/`.otf` 安装到系统字体目录后刷新 fontconfig：

```bash
sudo install -d /usr/local/share/fonts/deck
sudo install -m 0644 /path/to/deck-fonts/*.ttf /usr/local/share/fonts/deck/
sudo fc-cache -f
fc-list : family | rg -i 'Noto Sans CJK SC|Inter'
```

Runtime 不下载 Web font，字体合约不满足会被当作渲染环境错误，不会悄然换字体。生产镜像以非 root 用户运行，并显式要求 Chromium sandbox；不应通过 `--no-sandbox` 规避部署问题。

MySQL migration 会在 Go API 启动时自动执行：`migrations/mysql/011_create_ppt_run_store.sql` 创建 PPT run/event 表，`012_widen_ppt_event_ids.sql` 将生产者稳定事件 ID 列扩为 `VARCHAR(160)` 并保留唯一约束。本地联调时先启动 MySQL、Redis 和 Runtime，再启动 Go API 与 Web。

## 9. 验证

Runtime 全量测试：

```bash
cd apps/runtime
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. \
  .venv/bin/python -m pytest -p no:cacheprovider -q ../../tests/runtime
```

Go 的持久化、WebSocket、事件 reducer 和 artifact 边界测试：

```bash
cd ../..
go test ./apps/api/internal/modules/pptagent/... ./apps/api/internal/platform/mysql/...
```

Web 的事件投影、预览去重与构建验证：

```bash
npm --prefix apps/web test
npm --prefix apps/web run build
```

生成 10 页 recipe 画廊和三套 6 页浏览器样例：

```bash
cd apps/runtime
PYTHONPATH=. .venv/bin/python scripts/generate_html_deck_samples.py
```

默认要求生产字体 `Noto Sans CJK SC` / `Inter`。macOS 本地预览可显式使用已安装字体，例如
`--chinese-font "PingFang SC" --latin-font "Arial"`。输出在
`apps/runtime/artifacts/deck-samples/`，每套包含 sanitized HTML、3200×1800 PNG、逐页诊断、contact sheet 和 image-only PPTX。

生产镜像构建时 `.env`、`config.yaml` 与本地产物不会进入 build context；运行服务时需挂载或注入配置：

```bash
docker build -t deck-runtime apps/runtime
docker run --rm -v "$PWD/apps/runtime/config.yaml:/app/config.yaml:ro" deck-runtime
```

单元测试中的 fake renderer 不能代替真实环境 smoke test。发布前至少用一份中文输入在目标容器中生成完整 PPT，检查每张 PNG 为 `3200×1800`、`quality-report.json` 无 error、PPTX 页数与 `DeckPlan` 一致，并通过 WebSocket `after` 水位演练一次断线续传。

## 10. 当前限制

- 只支持 `16:9`，画布固定为 `1600×900` CSS px。
- 附件会解析为文本和 provenance；上传图片不会进入生成节点，当前没有多模态视觉分析、图片生成或用户图片编排。
- 没有 LangGraph checkpointer、人工审批、pause/resume 或从中间页续跑；MySQL 事件续传只恢复前端消费，不恢复 Python 图执行。
- 并发上限需要结合模型供应商限流和部署内存调优；默认页面 6、Chromium 3。
- PPTX 是 image-only 交付，不支持在 PowerPoint 中编辑文字、图标、图表或版式元素。
- HTML 不得依赖网络、远程字体、远程图片或 JavaScript；所有资产必须来自受控本地设计系统。
- MySQL 持久化任务与事件，但最终二进制产物仍依赖共享文件系统，尚无对象存储后端。

## 11. 代码导航

| 位置 | 职责 |
|---|---|
| `apps/runtime/core/deck/pipeline/graph.py` | Send fan-out 根图、单页子图、结果 fan-in 与条件路由 |
| `apps/runtime/core/deck/domain/` | 传输契约、版本化 Pydantic schema、错误与事实规则 |
| `apps/runtime/core/deck/pipeline/` | 根图、四阶段节点、state、events 与 instrumentation |
| `apps/runtime/core/deck/runtime/` | LLM 调用、模型注册、prompt 加载与请求级指标 |
| `apps/runtime/core/deck/workspace/` | 来源解析与 task-local 产物目录合约 |
| `apps/runtime/core/deck/design_system/` | 严格 catalog、4 主题、10 recipes、base CSS 和本地图标库 |
| `apps/runtime/core/deck/html/` | sanitizer、assembler、图标注入、Chromium DOM QA 与 fallback |
| `apps/runtime/core/deck/export/image_pptx.py` | image-only PPTX 导出与反向校验 |
| `apps/runtime/scripts/generate_html_deck_samples.py` | 10-recipe 画廊与三套真实 Chromium 样例 |
| `apps/runtime/core/deck/service.py` | gRPC 生成入口、资源 hydrate 与终态事件 |
| `apps/api/internal/modules/pptagent/repository.go` | MySQL run/event 持久化与重启收口 |
| `apps/api/internal/modules/pptagent/websocket.go` | WebSocket 命令、快照、重放和实时 fan-out |
| `migrations/mysql/011_create_ppt_run_store.sql` | `ppt_runs` / `ppt_run_events` schema |
| `migrations/mysql/012_widen_ppt_event_ids.sql` | 稳定 preview event ID 的 160 字节兼容迁移 |
