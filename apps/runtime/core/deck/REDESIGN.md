# Deck 生成质量重构方案（HTML 中间表示 + 混合导出）

> 日期：2026-07-09
> 状态：已与产品负责人确认关键决策，待实施
> 读者：负责实施本次重构的工程师 / Agent。本文档自包含，实施前请先通读一遍再动代码。

---

## 0. 已确认的决策（不要再改，除非产品负责人推翻）

| 决策点 | 结论 |
|---|---|
| 中间表示 | 每页一个 **1280×720 px 的自包含 HTML 文档**，由 LLM 生成 |
| 导出形态 | **混合导出**：装饰层（背景/形状/图表/图标）整页截图作底图，正文文字叠加为原生可编辑文本框 |
| 运行依赖 | 允许引入 **playwright-python + headless Chromium**（渲染、截图、几何测量三合一）；不引入 Node.js |
| 视觉审查 | review 节点配 **VLM（多模态模型）**，规则校验打头阵 + VLM 审美/溢出检查 |
| 交互深度 | **全量**：大纲确认（已有）→ **风格样张二选一** → 逐页实时预览 → 页级重做/反馈修改 |
| 前端预览载体 | **PNG 优先**（后端渲染截图即权威画面，与成品像素一致）；HTML 文件同步存档，供下载与后续"前端直渲"演进 |

---

## 1. 现状诊断（为什么要重构）

现有实现：`brief → outline(流式 markdown) → interrupt 确认 → 逐页 JSON spec(串行) → review(只发 warning) → python-pptx 渲染`。具体质量硬伤，全部有代码出处：

1. **审查不回流**。`graph.py` 中 `review → render` 是无条件边（`graph.add_edge("review", "render")`），LLM review 发现的问题只变成 warning 事件流，成品照样渲染。
2. **占位内容进成品**。`graph.py::_fallback_bullets` 会把 `"Key point 1 for {deck_title}"` 写进最终 PPT；`renderer.py::_truncate_text` 超长文本直接 `"..."` 截断，内容丢失而非重排。
3. **视觉层是假的**。`renderer.py::_add_content_slide` 把 `visual_suggestion` 当文字渲染进右侧面板——成品里出现"建议配一张架构图"字样。没有真图表/图片/图标/表格。
4. **千篇一律**。5 种硬编码版式、固定配色、固定几何；spec 只有 title + bullets，表达力太窄。
5. **串行慢**。逐页生成串行传 `previous_slides`，页数多时耗时线性增长。
6. **字体隐患**。`FONT_FAMILY = "Microsoft YaHei"` 在 Linux 服务器上既不存在也有授权问题。

**保留的资产**（重构不许破坏）：gRPC 流式协议与 `GenerateDeckResponse` 字段语义、LangGraph 的中断/恢复机制、按节点下发模型的 `node_model_configs` 机制、outline 的 markdown 流式 delta（前端依赖 `channel=outline_markdown`）、上传文档解析（`core/tools/document_parser.py`）。checkpointer 已在后续全链路重构中升级为持久化 SQLite adapter。

---

## 2. 目标与非目标

**目标**
- 成品观感达到"被设计过"的水准：主题化配色、组件化版式、真实图表、无溢出无截断无占位。
- 内容有据：所有具体数字/事实可追溯到用户输入或上传文档。
- 全程透明：每个阶段 agent 在干什么、干到哪一页，前端实时可见。
- 页级可控：用户可以对单页说"重做/换成图表/文字太多"，只重跑该页。
- 导出的 pptx 文字可编辑（混合导出），画面与预览一致。
- 质量闭环：规则校验 + VLM 审查发现的问题自动定点修复，有轮次上限。

**非目标（本期不做，但留接口）**
- 动画/转场、外部图库/生图接入（设计语言先用"色块+图标+排版"避免图片依赖）、用户品牌模板（.pptx 母版）导入、pptx 原生图表对象、协作编辑。checkpointer 持久化已由后续全链路重构完成。

---

## 3. 总体架构

### 3.1 新的 LangGraph 拓扑

```
START
 → brief          素材整备：解析输入 → fact sheet + 受众/目的/语言/页数推断
 → outline        叙事规划：流式 markdown 大纲（保留现有体验，格式升级）
 → outline_review [interrupt] 用户确认/编辑大纲（已有机制）
 → style          Art Director：生成 2 套 theme tokens + 各渲染 1 张封面样张
 → style_review   [interrupt] 用户二选一（可附加微调意见）
 → write_slides   Writer：逐页结构化内容 spec（并行）
 → design_slides  Designer：逐页 HTML（并行；每页完成即截图推预览）
 → validate       确定性规则校验（渲染后基于 DOM 几何）
 → vlm_review     VLM 看截图挑毛病
 → repair         定点修复（条件边：有 issue 且轮次<上限 → 回 design_slides 对应页）
 → coherence      全局连贯 pass（去重/编号/过渡/术语统一，少量定点改）
 → export         混合导出 pptx
 → END
```

`validate → vlm_review → repair` 构成闭环：`repair` 后重新 `validate`，**每页最多修 2 轮**，全 deck 修复总预算 = 页数 × 1.5 次，超限走确定性兜底（缩字号/删次要元素），绝不无限循环。

### 3.2 角色即节点，节点即模型配置项

`node_model_configs` 的 `node_key` 扩展为（保留 `default` 兜底逻辑，`service.py::_build_deck_llms` 机制不变）：

| node_key | 角色 | 模型建议 |
|---|---|---|
| `brief` | Researcher，提炼 fact sheet | 中档文本模型 |
| `outline` | Strategist，叙事规划 | 最强文本模型 |
| `style` | Art Director，主题 tokens | 强模型 |
| `writer` | 页面文案 | 强模型 |
| `designer` | 页面 HTML | 强模型（代码能力好的） |
| `review` | 视觉审查 | **VLM（必须支持图片输入）** |
| `coherence` | 全局连贯 | 中档 |

`repair` 复用 `designer` 的模型。Go 侧需要在模型配置界面放开这些 node_key（沿用现有下发通道，无协议变化）。

---

## 4. HTML 设计系统（质量的地基，先于一切编码）

### 4.1 画布与文档形态

- 每页 = 一个自包含 HTML 文档，`<body>` 内根元素固定 `<div class="slide">`，尺寸 **1280×720 px**（与 pptx 13.333×7.5 in @96dpi 严格 1:1，px→EMU 换算无损）。
- 渲染时由 `html_renderer` 把共享的 `deck.css` + 当页 theme tokens（CSS 变量）内联进文档头，保证单文件可渲染、可存档、可回放。
- 字体：**思源黑体（Noto Sans SC，OFL 协议免费商用）+ Noto Serif SC**，字体文件随服务部署，HTML 内 `@font-face` 指向本地文件。禁止微软雅黑出现在服务端渲染链路。

### 4.2 Theme tokens（每次生成时由 style 节点产出）

```json
{
  "name": "深海商务",
  "palette": { "bg": "#0F1B2D", "surface": "#16263D", "ink": "#F5F7FA", "muted": "#8FA3BF", "accent": "#4FC3F7", "accent2": "#FFB74D" },
  "fonts": { "display": "Noto Sans SC", "body": "Noto Sans SC", "display_weight": 700 },
  "type_scale": { "h1": 44, "h2": 30, "body": 18, "caption": 13 },
  "spacing_unit": 8, "radius": 12,
  "motif": "细线网格 + 大色块左右分割"
}
```

写成 CSS 变量注入 `:root`。**所有组件只允许引用变量，不允许硬编码颜色/字号**——这是校验器要查的项。style 节点从 6~8 套精修预设出发做题材化微调（比从零生成稳定得多），两套候选必须在"明度基调"或"版式母题"上有明显差异。

### 4.3 组件目录（版式的词汇表）

首批 14 个组件，每个组件 = 一个 Jinja2 模板 + 一段"何时使用/数据 schema/密度上限"的说明（汇总为 `components.md`，同时作为 writer/designer 的 prompt 素材）：

`cover`（封面）、`agenda`（目录）、`section`(章节页)、`content-basic`（观点+要点）、`kpi-cards`（2-4 个大数字卡）、`timeline`（时间线）、`steps`（流程步骤）、`comparison`（左右对比）、`quadrant`（四象限）、`chart-takeaway`（图表+结论，图表用 ECharts）、`data-table`（表格）、`quote`（大引言）、`summary`（总结卡片）、`end`（尾页）。

密度上限示例（写进 schema，校验器强制）：`content-basic` 最多 4 条要点、每条 ≤ 28 个中文字符；`kpi-cards` 数字 ≤ 8 字符、标签 ≤ 12 字符。**超限不是截断，是打回 writer 重写。**

### 4.4 Designer 的自由度分两档

- **档位 A（M1 落地）**：designer 的输出是"选组件 + 填 slot + 在预留的 `custom_css` 区域内做有限微调"。HTML 由 Jinja 模板确定性渲染，LLM 不直接写 HTML。稳定、可校验、不会跑飞。
- **档位 B（M2+ 放开）**：新增 `freeform` 组件，designer 在设计系统约束内直接写整页 HTML（只许用 tokens 变量、内置字体、无外链、无 `<script>`（ECharts 除外，见 4.5））。上限更高，配套校验必须先就位。

### 4.5 图表与图标

- 图表统一 **ECharts（本地 JS 文件，页面内执行）**：designer 只产出 `chart` 数据（type/series/axis），模板负责初始化。截图时图表自然成像，**归入装饰层**（混合导出里不可编辑，可接受）；图表的结论句（takeaway）是文本层，可编辑。截图前必须等待 ECharts 渲染完成（`page.wait_for_function` 检查实例 `finished` 事件或固定 300ms 兜底）。
- 图标用 **iconify 离线 JSON 子集**（挑 200~400 个常用业务图标随库分发），模板内联为 SVG。
- 本期无外部图片。预留 `image_provider` 接口（输入描述 → 返回本地图片路径），签名定义好、实现留空。

---

## 5. 数据契约（spec v2）

新增 `core/deck/spec.py`，用 dataclass + JSON Schema 双份定义（schema 用于 LLM 输出校验与重试）：

```jsonc
// DeckSpec
{
  "title": "…", "narrative": "一句话故事线",
  "audience": "…", "goal": "…", "language": "zh",
  "theme": { /* 选定的 tokens */ },
  "slides": [ /* SlideSpec */ ]
}

// SlideSpec
{
  "index": 3,
  "role": "evidence",              // hook|context|evidence|turn|solution|action|summary
  "component": "kpi-cards",
  "title": "获客成本半年下降 37%",   // 必须是观点句，不是名词短语
  "content": { /* 组件对应的结构化 slot 数据 */ },
  "chart": null,                    // chart-takeaway 组件时非空
  "notes": "演讲者备注",
  "sources": ["fact#12", "fact#3"]  // 引用 fact sheet 条目 id
}
```

**写作规范（进 writer 的 prompt，也进校验器）**：标题=观点句；一页一个信息；数字优先于形容词；所有具体数字必须能对应 `sources`；**禁止任何占位文本**——LLM 输出解析失败的处理方式是带错误信息重试（≤2 次），重试仍失败则向前端发 `error` 级事件并跳过该页进入人工处理，绝不静默填充。

---

## 6. 各阶段详细设计

### 6.1 brief（素材整备）

输入：`user_description` + 上传文档文本（解析逻辑沿用 `service.py::_parse_uploaded_files`）。
输出：`brief` 对象 = fact sheet（`[{id, statement, value?, unit?, source_file?}]`）+ 推断的 audience/goal/场合/语言/建议页数。
要点：这是后续所有阶段的**唯一事实来源**。输入很薄时（<200 字且无附件），fact sheet 允许为空，此时 writer 的 prompt 自动切换为"通识内容、不得编造具体数字"模式。

### 6.2 outline（叙事规划）

保持现有流式 markdown 体验（`text_delta` + `channel=outline_markdown` 一个字都不能变）。格式升级：每页条目增加 `角色:`（叙事角色）与 `组件:`（建议组件）两个字段；`_parse_outline_slides` 的正则解析相应扩展，**解析必须容错**（用户会手改 markdown）：缺字段给默认值，多余内容忽略。

### 6.3 style + style_review（风格二选一）

1. style 节点产出两套差异化 tokens；
2. 用固定的封面样张数据（deck 标题 + narrative）各渲染 1 张封面 PNG；
3. 发 `style_options` 事件（见 §7），随后 `interrupt`，payload 含两套 `{style_id, name, cover_png, tokens_summary}`;
4. resume 时带回 `{style_id, feedback}`，feedback 非空则 style 节点按意见微调选中的 tokens（一次，不再确认）。

### 6.4 write_slides / design_slides（并行）

- 大纲确认后每页的 role/key_message/邻页摘要都已确定 → **页间无依赖，可并行**。并行度 = min(4, 页数)（受浏览器 page 池约束，见 §11）。
- writer 产出 SlideSpec → 发 `slide_spec_ready`；designer 渲染 HTML → Playwright 截图 → 发 `slide_preview`。**每页完成即推送，不等全量**——这是前端"看着 PPT 一页页长出来"的体验来源。
- LangGraph 实现提示：并行用 `Send` API 做 map-reduce（`write_slides` 之后 fan-out 到页级子任务，reduce 收集回 state），或在节点内用线程池——实施者任选，但事件顺序必须保证同一页的 `slide_spec_ready → slide_preview` 有序（`sequence` 字段由 service 层统一递增，天然满足）。

### 6.5 validate（确定性校验，渲染后跑在 DOM 上）

在 Playwright `page.evaluate` 里执行检查脚本，输出结构化 issues：

| 规则 | 判定 |
|---|---|
| 文本溢出 | 任意元素 `scrollHeight > clientHeight + 2` 或包围盒超出 1280×720 |
| 元素重叠 | 文本层元素两两包围盒相交面积 > 4% |
| 对比度 | 文本色 vs 实际背景色 < WCAG AA (4.5:1，大字 3:1) |
| 密度超标 | 组件 schema 定义的字符/条目上限 |
| tokens 越界 | 计算样式中出现 tokens 之外的颜色/字体 |
| 占位残留 | 文本命中占位模式（`Key point`、`TODO`、`示例`、`占位` 等黑名单） |

规则问题**不烧 token**，直接生成修复指令。

### 6.6 vlm_review（VLM 审查）

- 输入：全部页面截图（每 6 页拼一张 2×3 网格图控制图片数）+ rubric。
- rubric 维度（**只允许报这些类型**，防 VLM 发散）：溢出/拥挤、对齐失衡、层级不清（主次不分明）、跨页风格漂移、图文不匹配、观感低级感（如颜色刺眼）。
- 输出 schema：`[{page, severity: "must_fix"|"nice_to_have", kind, description, suggestion}]`。只有 `must_fix` 触发 repair；`nice_to_have` 作为 `validation_report` 事件透出给前端展示。

### 6.7 repair（定点修复）

issue 路由回对应页的 designer（prompt = 原 SlideSpec + 原 HTML + issue 列表 + "只改问题，不重设计"），产出新 HTML → 重新截图 → 重新 validate 该页。每页 ≤2 轮，超限走兜底：按优先级执行确定性降级（字号 -10% → 删 `nice_to_have` 元素 → 换密度更低的组件），并发 warning 事件如实告知。

### 6.8 coherence（全局连贯）

输入全部页面的 title/key_message/组件序列，检查：标题重复、编号连贯、相邻页组件雷同（连续 3 页同组件要求换）、术语/大小写不一致。产出少量页级定点修改（走 repair 通道，不占用其轮次预算，全局 ≤3 处）。

### 6.9 export（混合导出，本方案唯一的硬核技术组件）

算法（新增 `core/deck/exporter.py`）：

1. Playwright 打开每页 HTML，viewport 1280×720，`deviceScaleFactor=2`（截图 2560×1440，pptx 里清晰）。
2. `page.evaluate` 提取所有文本元素。**约定：模板中所有"用户可编辑正文"必须带 `data-text` 属性**（标题/要点/数字/标签/结论句），装饰性文字（水印、页码可不带）。对每个元素提取：文本内容（含换行）、`getBoundingClientRect`、计算样式（font-size/weight/color/line-height/text-align/font-family/letter-spacing）。
3. 对所有 `data-text` 元素设 `visibility: hidden`（保留布局占位）→ 整页截图 = **装饰底图**。
4. python-pptx 组装：页面 13.333×7.5 in；底图 `add_picture` 全幅；逐文本元素叠加 textbox。坐标换算 `1px = 9525 EMU`（96dpi 下严格无损）。textbox 关闭 margin、`word_wrap=True`、宽度 = 测量宽度 + 2% 余量、高度 = 测量高度 + 1px，字号四舍五入到 0.5pt。pptx 内字体名直接写 `"Noto Sans SC"` 并设置 `east asian font`；同时在文档属性中不嵌入字体（体积考虑），用户机器缺字体时 fallback 为系统黑体，位置不会跑（框定死）。
5. `notes` 写入 speaker notes（沿用现有 `_add_speaker_notes` 思路）。
6. **降级路径**：任一页提取/组装异常 → 该页整页截图直贴（文本不可编辑），发 warning 事件写明哪一页降级、原因。导出永远成功。
7. 产物目录：`DECK_ARTIFACTS_DIR/{request_id}/`，含 `deck.pptx`、`slides/{n}.html`、`slides/{n}.png`、`spec.json`（完整 DeckSpec，页级重做和审计用）。

已知风险（换行差异）：浏览器与 PowerPoint 的断行算法不同，长段落可能出现行数差一行。缓解：模板中正文以短句/条目为主（设计规范本来就限制密度）；textbox 尺寸取测量值加余量；M3 加"pptx→LibreOffice 缩略图 vs HTML 截图"的像素 diff 终检，diff 超阈值的页自动降级为整页截图。

---

## 7. 事件协议与接口变更

### 7.1 事件（`GenerateDeckResponse` 字段不动，扩展 `event_type` 与 `payload_json`）

沿用：`stage_started` / `stage_progress` / `text_delta` / `outline` / `interrupt` / `artifact` / `error` / `done`。

新增 `event_type`（payload 结构如下，全部走 `payload_json`）：

| event_type | payload | 说明 |
|---|---|---|
| `agent_message` | `{agent, message}` | "我在干什么"的叙述流，每个节点开始/关键动作都发，前端做活动日志 |
| `fact_sheet_ready` | `{facts_count, audience, goal, suggested_pages}` | brief 完成 |
| `style_options` | `{options: [{style_id, name, cover_png, tokens_summary}]}` | 两套样张就绪，紧随其后是 `interrupt`（stage=`style_review`） |
| `slide_spec_ready` | `{index, title, component, role}` | 某页文案就绪 |
| `slide_preview` | `{index, png, html, version, status}` | 某页画面就绪/更新（repair 后 version+1，前端直接替换） |
| `validation_report` | `{issues: [...]}` | 校验/VLM 结果摘要 |
| `repair_progress` | `{index, round, kind}` | 正在修哪页 |
| `export_progress` | `{current, total}` | 导出进度 |

**资产分发**：`png`/`html`/`cover_png` 一律是相对 `DECK_ARTIFACTS_DIR/{request_id}/` 的路径。需要 **Go 侧新增一个静态文件下载接口**（按 request_id + 相对路径鉴权后回源该目录）。为降低前端首帧延迟，`slide_preview` 与 `style_options` 的 payload 里**同时附带 ≤60KB 的 webp 缩略图 base64**（字段 `thumb_b64`），大图走下载接口。

### 7.2 proto 变更（只加字段，不改已有编号）

```protobuf
message ResumeDeckRequest {
  UserContext user = 1;
  string request_id = 2;
  string outline_markdown = 3;                 // 兼容：旧调用等价 action=approve_outline
  repeated DeckNodeModelConfig node_model_configs = 4;
  string action = 5;        // 新增: approve_outline | choose_style | regenerate_slide
  string payload_json = 6;  // 新增: choose_style→{style_id, feedback}; regenerate_slide→{index, feedback}
}
```

`action` 为空时按 `approve_outline` 处理（向后兼容）。`contract.py::ResumeDeckInput`、`handler/deck_handler.py`、Go 侧调用方同步加字段。

### 7.3 页级重做语义

- **导出前**（style_review 之后、export 之前其实不存在等待点，所以实际是导出后）：为简化状态机，**页级重做统一发生在成品出来之后**——`regenerate_slide` 从 checkpoint 恢复，带 feedback 重跑该页 writer/designer/validate，然后**只重跑 export**（其余页复用已存档的 html/png），产出新版 pptx，重发 `slide_preview` 与 `artifact`。
- 这要求 export 的输入完全来自 `{request_id}/` 目录的存档而非内存 state —— 实施时把"页产物存档"作为 design_slides 的固定副作用。

---

## 8. Prompt 文件规划（全部重写，沿用 `prompts/` 目录 + `_load_node_prompt` 机制）

`brief.md`、`outline.md`、`style.md`、`writer.md`、`designer.md`（内嵌组件目录文档）、`review.md`（VLM rubric）、`repair.md`、`coherence.md`。

通用要求：每个 prompt 必须包含输出 JSON Schema（designer 档位 A 输出的是组件选择+slot JSON，不是 HTML）、语言跟随规则（成品语言 = 用户输入语言）、禁占位条款、"不得引入 fact sheet 之外的具体数字"条款。设计类 prompt 参考 [anthropics/skills 的 pptx skill](https://github.com/anthropics/skills/blob/main/skills/pptx/SKILL.md) 中的设计规范写法（先声明设计方向、对比度要求、留白原则）。

---

## 9. 代码结构（建议，实施者可微调但需保持模块边界）

```
core/deck/
  contract.py          # 扩展 ResumeDeckInput(action, payload_json)
  spec.py              # NEW: DeckSpec/SlideSpec/ThemeTokens + JSON Schema + 校验/重试助手
  graph.py             # 重构为 §3.1 拓扑（现有 outline 流式逻辑整体保留搬移）
  service.py           # 事件映射扩展(§7.1)、resume 三种 action 分发
  design_system/
    deck.css           # 设计系统样式（只引用 CSS 变量）
    presets.json       # 6~8 套 tokens 预设
    components.md      # 组件目录文档（也是 prompt 素材）
    components/*.j2    # Jinja2 组件模板（文本 slot 一律带 data-text）
    fonts/  icons/  vendor/echarts.min.js
  html_renderer.py     # NEW: SlideSpec + tokens → 自包含 HTML
  browser.py           # NEW: Playwright 单例 + page 池(上限4) + 截图/测量/校验脚本注入
  validators.py        # NEW: §6.5 规则
  vlm_review.py        # NEW: 拼图 + rubric 调用 + issue 解析
  exporter.py          # NEW: §6.9 混合导出
  renderer.py          # 删除（或保留为 legacy fallback，不再默认走）
  prompts/*.md         # §8
tests/
  test_deck_spec.py  test_validators.py  test_exporter.py(固定 HTML fixture)
  test_deck_graph.py   # 更新到新拓扑（现有 fake-llm 测试手法可沿用）
```

新依赖写入依赖清单：`playwright`、`jinja2`；部署文档注明 `playwright install chromium --with-deps`、字体文件与 ECharts/iconify 资产的获取方式。

---

## 10. 里程碑与验收标准（按序交付，每个里程碑可独立演示）

**M1 — HTML 渲染骨架**：spec v2 + tokens 预设 + 8 个核心组件 + html_renderer + browser + 逐页截图 `slide_preview` 事件 + 临时导出（整页截图版 pptx）。
✅ 验收：任一 brief 跑通全流程，前端能实时看到逐页 PNG 长出来，观感肉眼可见强于现状；无占位文本；outline 流式体验不回退。

**M2 — 混合导出 + 质量闭环（规则层）**：exporter 混合导出 + validators + repair 回路 + 剩余 6 组件。
✅ 验收：导出 pptx 在 PowerPoint/WPS 中正文可编辑、画面与预览一致；构造的溢出用例能被自动修复；异常页正确降级并如实报 warning。

**M3 — 风格二选一 + VLM + 页级重做**：style/style_review 节点 + proto 加字段 + vlm_review + `regenerate_slide`。
✅ 验收：完整交互流可演示（大纲确认→选风格→看着生成→对第 N 页提意见→只有第 N 页重做→新 pptx）。

**M4 — 素材整备 + 连贯 + 评测**：brief 节点 fact sheet + coherence + 评测集。
✅ 验收：黄金 brief 集（见 §12）三维评分相对 M1 有提升且无回退项。

---

## 11. 风险与缓解

| 风险 | 缓解 |
|---|---|
| 混合导出换行/字体差异 | 密度限制+短句化；textbox 余量；字号取整；M3 像素 diff 终检；超阈值页自动降级整页截图 |
| Chromium 内存/并发 | browser 进程单例、page 池上限 4、每 deck 结束回收 page；渲染超时 10s 熔断 |
| LLM 写 HTML 跑飞 | M1/M2 只开档位 A（模板渲染）；档位 B 上线前静态白名单校验（禁外链/`<script>`/`position:fixed` 等），前端 iframe 一律 sandbox |
| VLM 误报导致修复震荡 | 只认 rubric 内类型 + must_fix 才修 + 每页 2 轮上限 + 修复后必须过规则校验才算数 |
| 事件量变大冲击前端 | `agent_message` 节流（同 stage 500ms 合并）；大图走下载接口、payload 只带小缩略图 |
| 中文字体体积 | 思源黑体 OFL 免版权；可选 pyftsubset 子集化（非必须） |
| Checkpoint 重启恢复 | 后续全链路重构已使用 SQLite-backed saver；`spec.json` 仍使导出后页级重做不依赖 checkpoint |

---

## 12. 评测（防盲调，M4 落地但从 M1 就开始攒）

- `tests/golden_briefs/`：5~8 个固定 brief，覆盖：纯中文商务汇报、技术分享、带上传文档（数据密集）、英文、极简输入（<50 字）。
- 跑批脚本输出两类指标：
  1. **确定性指标**（每次 CI 可跑）：溢出数、占位命中数、对比度违规数、tokens 越界数、导出降级页数、端到端耗时。
  2. **VLM judge 三维评分**（人工触发）：内容质量/设计质量/连贯性，1~5 分，维度定义参考 PPTAgent 论文的 PPTEval。
- 规则：改 prompt / 组件 / 流程前后各跑一遍，确定性指标不许回退，评分回退需给出理由。

---

## 13. 给实施者的硬约束（逐条自查后才算完成）

1. `GenerateDeckResponse` 已有字段的语义一个不改；新信息一律走 `payload_json`。proto 只加字段不改编号。
2. outline 的流式 delta（`channel=outline_markdown`）行为与现在逐字节一致。
3. 任何 LLM 结构化输出解析失败 → 带错误重试 ≤2 次 → 仍失败发 error 事件；**全链路禁止占位文本进成品**（校验器兜底）。
4. `_fallback_bullets`、`_truncate_text` 式的"静默降质"手法禁止再出现；所有降级必须发事件如实告知。
5. 每个组件模板的文本 slot 必须带 `data-text`，且组件必须同时登记进 `components.md` 与密度校验表——三者不同步视为未完成。
6. 并发渲染共享 browser 单例，任何路径（含异常）不得泄漏 page。
7. `tests/` 全绿；新增模块（spec/validators/exporter/html_renderer）都有对应测试；exporter 测试用仓库内固定 HTML fixture，不依赖 LLM。
8. 部署文档更新：新依赖、`playwright install chromium`、字体/ECharts/iconify 资产、`DECK_ARTIFACTS_DIR` 目录结构、Go 侧静态文件接口的约定。

---

## 附：参考实现（实施前值得翻阅）

- [anthropics/skills — pptx skill](https://github.com/anthropics/skills/tree/main/skills/pptx)：HTML→pptx 工作流、缩略图视觉校验、设计规范 prompt 的写法（注意它是 Node/PptxGenJS 全原生映射路线，我们是混合导出，不照搬转换器）。
- PPTAgent（中科院，ICML'25）：edit-based 生成思想与 PPTEval 评测三维度。
- open-slide：固定画布 + 组件化 + 评论式反馈循环的 HTML 流派参考。
