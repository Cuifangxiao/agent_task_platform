# PPT HTML-first 生成流水线

当前生产路径是一条固定为 `16:9` 的 HTML-first 流水线：`outline` 模型先一次性生成整套大纲、页面规划和全局色板；页面随后并行执行 `style` 视觉规划与 `designer` HTML 生成。Runtime 再在受控 Chromium 中完成清洗、DOM 检测、必要修复、PNG 截图，并将每页 PNG 全出血铺入 PPTX。PPTX 与页面预览图视觉一致，但页面内容不可在 PowerPoint 中逐元素编辑。

```text
Web
  ├─ POST /api/v1/ppt/tasks           创建任务
  ├─ GET  /api/v1/ppt/tasks           查询历史任务
  └─ WS   /api/v1/ppt/ws              订阅、重放、取消任务
              │
              ▼
Go API：鉴权、解析 3 个模型绑定、持久化 run/event、WebSocket 广播
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

Go 在创建任务前解析 `outline`、`style`、`designer` 三个模型节点。绑定缺失、模型停用或类型错误会在 worker 启动前拒绝请求；`brief` 和 `writer` 不再是 PPT Agent 的模型配置节点。

## 2. Send map-reduce LangGraph

`core/deck/pipeline/graph.py` 直接编排目标根图，不再用 phase wrapper 隐藏实际节点。`generate_deck_outline` 是唯一的全局 LLM 节点；它返回一份完整、经过校验的 `DeckOutlineSpec`，不得把不完整结果传入下游。随后 `fan_out_slides` 通过 LangGraph `Send` 为每个页面派发一份隔离 state。每个 `generate_slide` 子图严格按“视觉规划 -> HTML 执行”运行：

`prepare_slide -> generate_slide_design -> generate_slide_html -> sanitize_and_assemble_html -> render_html_and_measure_dom -> validate_dom -> [repair/fallback] -> capture_png -> persist_slide_artifacts -> emit_slide_preview`。

完整路由如下：

```text
START
  -> generate_deck_outline                         # outline，一次
  -> fan_out_slides
  -> Send(generate_slide, slide_1 .. slide_n)
       └─ prepare_slide
          -> generate_slide_design                 # style，每页一次
          -> generate_slide_html                   # designer，每页一次
          -> sanitize -> render/measure
               ├─ valid -> capture_png
               ├─ repair -> designer repair -> sanitize
               └─ fallback -> sanitize
          -> persist_slide_artifacts -> emit_slide_preview
  -> aggregate_slides
  -> assemble_image_pptx
  -> finalize_deck
  -> END
```

`DeckOutlineSpec.slides` 已经是最终语义页面清单，单页包含稳定 `id`、标题、角色、要点、layout suggestion、content mode 和内容说明。Processor 不再运行第二个分页模型，而是确定性生成兼容现有 renderer/QA/fallback 的 `DeckPlan`、`DesignSpec`、recipe variant 与内容合同。阶段一的 `GlobalStyleSpec` 是唯一颜色来源；catalog 只补充经过浏览器验证的字体、间距、几何、shape 和本地图标合同。

页面之间继续并行，单页 design plan 和 HTML 必须使用相同 `slide_id`。并发分支只向 reducer-backed 的 `completed_slides`、`layout_history` 和 `validation_issues` 写入结果；`aggregate_slides` 在 fan-in 后检查缺页/多页并按 `slide_id`/页索引恢复顺序，一次性写 manifest。Postprocessor 只接收这些已完成页面执行导出。预览按完成顺序实时发布，消费者继续按 `page_index`/`page_id` 定位页面。

默认最多并发 6 个页面，可通过 `DECK_SLIDE_CONCURRENCY=1..30` 调整；Chromium 默认使用 3 个 thread-affine session，可通过 `DECK_BROWSER_CONCURRENCY` 调整，但不会超过页面并发数。每个 run 仍创建独立的 `PptGraph`、LLM 依赖和浏览器 session pool；浏览器对象、LLM client、文件句柄和图片二进制数据都不进入 LangGraph state。

## 3. Schema 与 state

LLM 边界使用 `core/deck/domain/schemas.py` 中的严格 Pydantic schema，默认 `extra="forbid"`。模型输出优先使用 structured output；退化路径也必须是完整 JSON，不从 Markdown fence 或文本子串中猜测 JSON。除字段级校验外，Runtime 还在同一次结构化重试边界内校验页数、连续 ID、当前页 ID、全局色板继承和布局多样性；校验失败会重试，最终失败则终止该阶段，不允许半成品进入下游。

| Schema | 职责 |
|---|---|
| `GlobalStyleSpec` | 阶段一生成的完整全局色板；所有颜色使用 `#RRGGBB`，并校验正文与背景/表面的可读对比度 |
| `DeckOutlineSpec` | 一次性整套规划：标题、副标题、metadata、全局色板和 `1..30` 个连续页面 |
| `SlideOutlineSpec` | 单页语义合同：`id`、标题、角色、key points、layout suggestion、content mode、content note |
| `SlideDesignPlan` | `style` 的逐页输出：可审计 design rationale、layout、继承色板、字体、关键元素与视觉层级 |
| `DeckPlan` / `SlidePlan` | 从 outline 确定性派生的 renderer/事件兼容合同；不产生额外模型调用 |
| `DesignSpec` | 从 `GlobalStyleSpec` 和受信 catalog 派生的可执行主题、字体、字号、间距、形状与图标合同 |
| `SlideContentSpec` | 从页面 outline 与相关来源确定性整理的 QA/fallback 内容合同，不再由 `writer` 模型生成 |
| `HtmlSlideSpec` | `designer` 的逐页输出：layout family/variant、构图摘要、HTML body fragment、scoped CSS 和图标 key |
| `RendererDiagnostic` | `security` / `content` / `environment` 分类，严重度、selector、几何信息与修复建议 |
| `SlideArtifactMetadata` | 每页 HTML/PNG 路径、内容哈希、渲染环境、模型元数据、repair/fallback 结果 |

四个 active prompt 均采用“角色/任务、输入合同、决策规则、质量门、输出合同”的正式结构：

| Prompt | 核心决策 |
|---|---|
| `deck_outline_planner.md` | 接收用户要求、标准化来源、资产摘要与页数约束，一次返回完整 `DeckOutlineSpec` |
| `slide_visual_planner.md` | 接收当前页、全局摘要/色板、相邻布局和相关来源切片，返回 `SlideDesignPlan` |
| `slide_html_builder.md` | 接收当前页与对应 design plan，返回符合安全渲染合同的 `HtmlSlideSpec` |
| `html_repair.md` | 复用 `designer`，按诊断优先级和最小改动阶梯修复，不改事实、标题、全局色板或页面设计意图 |

当前机器可读版本标识为 `deck-state.v6`、`html-deck.v8` 和 `chromium-html.v2`。本次只改变语义生成链路和产物合同，浏览器协议未改变，因此 renderer version 保持不变。正式节点提示词统一采用岗位合同；prompt 由 `load_node_prompt()` 缓存，部署新版本后必须重启 runtime 进程。`core/deck/pipeline/state.py` 将 state 分为以下逻辑区域：

- `DeckRunState`：版本、run/request ID 和图内状态；失败终态及错误详情由图外的 service 事件统一发布；
- `DeckInputState`：用户描述、解析后文本、source chunks、页数、语言和模板偏好；
- `DeckPreprocessorState`：`deck_outline`、`global_style_spec`、标准化 source text 和请求页数；
- `DeckProcessorPlanningState`：确定性派生的 `deck_plan` 与 `layout_preferences`；
- `DeckProcessorDesignState`：受信主题几何与可执行 `design_spec`；page blueprints 和 execution lock 只落盘，不进入 state；
- `DeckProcessorSlideState`：当前 `SlideOutlineSpec`、`SlideDesignPlan`、内容/HTML 合同、清洗结果、诊断、repair 次数和 PNG 元数据；
- `DeckProcessorResultState`：通过 reducer 归并的 `completed_slides` 幂等映射、构图历史和质量问题；
- `DeckPostprocessorState`：最终 PPTX 的 `artifact_path`。

state 中存的 schema 对象都是 `model_dump(mode="json")` 后的可序列化字典。`current_slide_index` 是零基偏移，outline 中的稳定 ID 为 `s01..s30`，renderer plan 与对外页码是一基。

## 4. 完整主题、布局 recipe 与图标

`core/deck/design_system/` 是 HTML 几何、字体、间距、shape、recipe 和本地图标的受信真源；`DeckOutlineSpec.metadata.global_style_spec` 是整份 deck 唯一的颜色真源。Runtime 选择最匹配的 catalog 主题作为经过浏览器验证的执行骨架，再用 `GlobalStyleSpec` 确定性替换其 palette。每页可选择不同 recipe 和 variant，但不得引入第二套色板。因此每套主题骨架都拥有完整的 10 种页型，而不是只有一张封面。

| 主题 ID | 适用场景 | 核心风格 |
|---|---|---|
| `paper_editorial` | 品牌、叙事、复盘、通用汇报 | 暖白纸张、番茄红信号色、编辑式构图 |
| `executive_signal` | 管理层、经营、战略、咨询 | 白底深蓝、单一橙色信号、证据与决策导向 |
| `midnight_product` | 产品发布、AI、技术方案、路演 | 深色背景、荧光青/琥珀、大尺度视觉锚点 |
| `alpine_research` | 研究、教学、学术、医疗 | 云白、松绿和冰川蓝，问题—方法—结论结构 |

模板参数只接受 `auto` 或上述四个当前主题 ID；显式传入未知或已删除的 ID 会直接报错。这里的主题色仅是 catalog 默认值，不会覆盖阶段一已经验证的全局色板。

10 个 layout family 为：`cover_minimal`、`section_break`、`statement_focus`、`editorial_split`、`three_points`、`comparison`、`process`、`timeline`、`key_metric`、`closing_resolution`。`recipes.json` 对每个 family 明确角色、slot、容量、结构、variant 和视觉锚点；缺少任一 family 或重复定义都会在 catalog 加载时失败。

`icons/lucide.json` 内置 24 个经校验的 Lucide SVG 图标，含中英文语义 tag 和第三方许可说明。LLM 只能输出 `<span data-icon="..."></span>` 占位符；清洗后由本地 trusted registry 注入 SVG，不允许远程图标、任意 SVG 属性或外部 URL。

## 5. HTML 安全边界、DOM QA 与修复

`designer` 不输出完整文档，只输出一个与当前 `SlideOutlineSpec` / `SlideDesignPlan` 绑定的 `HtmlSlideSpec`：页面 body fragment 和以 `.slide` 为作用域的 CSS。`HTMLAssembler` 在模型输出与 Chromium 之间建立了硬边界：

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

几何检查之外，Runtime 还对模型 HTML 做基础语义守卫：必须保留确定性 `SlideContentSpec` 中的标题和至少一个批准的正文内容块；`key_metric` 必须显示合同中的原始 metric value。视觉规划与 HTML prompt 同时要求只使用当前页 outline、相关来源切片和本地资产，但当前确定性守卫不会对所有自由文本事实逐项做来源蕴含校验。

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
│  └─ assets/                            # 上传图片的任务本地副本；模型仅接收有界元数据
├─ deck-outline.json                     # 阶段一完整 DeckOutlineSpec
├─ global-style.json                     # 唯一 GlobalStyleSpec 快照
├─ deck-plan.json                        # 确定性 renderer adapter
├─ outline.md                            # renderer-facing 可读视图
├─ design-spec.json                      # 全局色板 + 受信执行 token
├─ design-spec.md
├─ spec-lock.json
├─ spec-lock.md
├─ page-blueprints.json
├─ template-manifest.json
├─ slide_specs/
│  ├─ page-001.design.json               # 阶段二 SlideDesignPlan
│  ├─ page-001.content.json              # 确定性 QA/fallback 内容合同
│  └─ page-001.html.json                 # 阶段三 HtmlSlideSpec
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

`generation-manifest.json` 记录 `deck-state.v6`、`html-deck.v8` 和仍未改变的
`chromium-html.v2`。旧任务不会被当作 v6 state 继续执行；已完成任务的历史事件和产物仍可按原路径查询。

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

MySQL migration 会在 Go API 启动时自动执行：`migrations/mysql/011_create_ppt_run_store.sql` 创建 PPT run/event 表，`012_widen_ppt_event_ids.sql` 将生产者稳定事件 ID 列扩为 `VARCHAR(160)` 并保留唯一约束，`014_refactor_ppt_agent_model_nodes.sql` 将 PPT Agent 定义更新为 `outline`、`style`、`designer` 三个必需节点。014 使用 upsert 更新 Agent schema，只删除不再支持的节点绑定，因此已有三个保留节点的 model、temperature 和 max-token override 不变；旧 `brief` / `writer` 绑定会被清理。脚本遵循仓库 migration runner 的重复执行约定。本地联调时先启动 MySQL、Redis 和 Runtime，再启动 Go API 与 Web。

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
go test -overlay=tests/go-overlay.json ./apps/api/internal/modules/pptagent/... ./apps/api/internal/platform/mysql/...
```

PowerShell 需要把 overlay 参数作为一个参数传入：

```powershell
go test '-overlay=tests/go-overlay.json' ./apps/api/internal/modules/pptagent/... ./apps/api/internal/platform/mysql/...
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

单元测试中的 fake renderer 不能代替真实环境 smoke test。发布前至少用一份中文输入在目标容器中生成完整 PPT，确认 outline 每个任务只调用一次、每页严格按 design -> HTML 执行、三阶段 `slide_id` 一致、所有页面继承同一 `GlobalStyleSpec`，并检查每张 PNG 为 `3200×1800`、`quality-report.json` 无 error、PPTX 页数与 `DeckOutlineSpec` / renderer `DeckPlan` 一致。最后通过 WebSocket `after` 水位演练一次断线续传。

## 10. 当前限制

- 只支持 `16:9`，画布固定为 `1600×900` CSS px。
- 文本附件会解析为正文和 provenance；上传图片持久化在任务本地 `sources/assets/`，其有界元数据进入 outline 和逐页资源筛选，但当前没有多模态视觉分析、图片生成或用户图片 HTML 编排。
- 没有 LangGraph checkpointer、人工审批、pause/resume 或从中间页续跑；MySQL 事件续传只恢复前端消费，不恢复 Python 图执行。
- 并发上限需要结合模型供应商限流和部署内存调优；默认页面 6、Chromium 3。
- PPTX 是 image-only 交付，不支持在 PowerPoint 中编辑文字、图标、图表或版式元素。
- HTML 不得依赖网络、远程字体、远程图片或 JavaScript；所有资产必须来自受控本地设计系统。
- MySQL 持久化任务与事件，但最终二进制产物仍依赖共享文件系统，尚无对象存储后端。

## 11. 代码导航

| 位置 | 职责 |
|---|---|
| `apps/runtime/core/deck/pipeline/graph.py` | 仅串联 preprocessor / processor / postprocessor 三阶段 |
| `apps/runtime/core/deck/agents/preprocessor/` | 一次性生成 `DeckOutlineSpec` 与全局色板，并执行页数/ID/来源校验 |
| `apps/runtime/core/deck/agents/processor/` | 确定性 renderer adapter、逐页 `SlideDesignPlan`、HTML、DOM QA、修复、fallback、PNG |
| `apps/runtime/core/deck/agents/postprocessor/` | 质量报告、manifest、PPTX 汇总导出与最终校验 |
| `apps/runtime/core/deck/domain/` | 传输契约、版本化 Pydantic schema、错误与事实规则 |
| `apps/runtime/core/deck/pipeline/` | 三阶段根图、共享 state、events 与 instrumentation |
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
| `migrations/mysql/014_refactor_ppt_agent_model_nodes.sql` | 将 PPT Agent 模型节点从五个迁移为 `outline` / `style` / `designer`，保留存量三节点绑定 |
