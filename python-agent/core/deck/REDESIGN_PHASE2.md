# Deck 生成 Phase 2：修硬伤、闭环、健壮性与目录重组

> 日期：2026-07-09
> 前置文档：`REDESIGN.md`（Phase 1 方案）
> 状态：Phase 1 骨架已落地并通过评审，本文档是评审结论的任务化，交给实施者执行。
> 读者：实施本阶段的工程师 / Agent。**先通读，再按"执行顺序"一节动手，不要跳序。**

## 0. Phase 1 评审结论（背景）

已达成：HTML-first 渲染、spec v2 + 密度上限 + 占位检测、tokens 预设、组件化模板、截图预览事件、风格二选一中断、混合导出、页级重做接口、proto/前端全链路接通、测试通过。

未达成/有缺陷（本阶段要解决的全部内容）：

| # | 问题 | 位置 | 级别 |
|---|---|---|---|
| P1 | chart-takeaway 无运行时，成品出现空面板 | `html_renderer.py:287`，writer.md 还在鼓励选它 | 硬伤 |
| P2 | 页级重做把用户反馈文本直接写进幻灯片正文，且全程无 LLM | `service.py:296-300` | 硬伤 |
| P3 | 重做写出 `{n}_v2.html`，exporter 永远读 `{n}.html`，新画面进不了 pptx | `browser.py:51` / `exporter.py:69` | 硬伤 |
| P4 | 字体链路断：fonts 目录不存在、set_content 无 base URL、CSS 无兜底字体栈、Dockerfile 未装 CJK 字体 → Linux 容器中文变豆腐 | `html_renderer.py:107-128`、Dockerfile | 硬伤 |
| P5 | 每页启动一次 Chromium ×（预览+导出）≈ 24 次/deck，外加每次 300ms 固定等待 | `browser.py:105` / `exporter.py:87` | 性能 |
| P6 | validate 的 must_fix 无人消费，`validate → coherence → export` 仍是无条件直线 | `graph.py:376-378` | 闭环缺失 |
| P7 | 无 VLM review 节点；review.md / repair.md 是死文件 | graph.py | 闭环缺失 |
| P8 | coherence 节点发"已检查"消息但 `return {}`，虚假叙事 | `graph.py:319-323` | 诚实性 |
| P9 | 无 DOM 级溢出/重叠检测（只有字符数上限） | validators.py | 闭环缺失 |
| P10 | brief/style/designer 无 LLM：风格二选一永远是同两套预设，style feedback 只拼进 motif 零视觉效果 | `graph.py:709-716,162` | 名不副实 |
| P11 | `design_system/components/*.j2` 全部死代码（真实模板是 html_renderer 里的 f-string），双源漂移 | design_system/components/ | 死代码 |
| P12 | `renderer.py`（v1 遗留）、prompts/slide.md、slides.md、designer.md、coherence.md、brief.md、style.md 均为死文件 | — | 死代码 |
| P13 | content-basic 右侧卡片只显示英文角色标签 "Evidence/Hook" | `html_renderer.py:176` | 观感 |
| P14 | validation_report 中 slide 级 issue 被统计两遍 | `graph.py:305-308` | 小 |
| P15 | `validate_html_static` 的 `https?://` 会误伤正文里合法 URL | `validators.py:78` | 小 |
| P16 | pptx 文本框未设行距（浏览器 line-height:1.45 vs PPT 默认），多行文本与预览错位；`max(4,…)` 4px 下限无意义 | `exporter.py:176-203` | 小 |
| P17 | 仅 1 个端到端测试；validators/exporter/html_renderer 无单测 | tests/ | 违反 Phase 1 硬约束 #7 |

---

## 1. 目标目录结构（本阶段完成后的形态）

按职责拆包。原则：**graph 只剩拓扑；每个 LangGraph 节点一个文件；渲染/导出/质量各自成包；事件与错误有唯一出口**。

```
core/deck/
├── __init__.py              # 只导出 DeckService（对外唯一入口）
├── contract.py              # gRPC 边界 dataclass（保持不动，镜像 proto）
├── service.py               # [瘦身 ≤250行] generate/resume 分发、gRPC 事件流映射、浏览器会话生命周期
├── events.py                # [NEW] EventType 常量 + 事件构造函数（全工程唯一的事件 dict 出口）
├── errors.py                # [NEW] DeckError 体系: InputError/ModelOutputError/RenderError/ExportError
├── spec.py                  # DeckSpec/SlideSpec/ThemeTokens + 密度限制 + normalize（保持单文件）
│
├── pipeline/                # LangGraph 节点层（graph.py 拆解至此）
│   ├── __init__.py          # build_deck_graph：只有节点装配与边，≤120 行
│   ├── state.py             # DeckGraphState 定义 + 从 state 读取的类型化 helper
│   ├── llm.py               # llm registry / invoke_json_with_retries / stream_text / prompt 加载
│   ├── brief.py             # brief 节点（T11: LLM 化，regex 保留为无模型 fallback）
│   ├── outline.py           # outline 节点 + markdown 解析（现 graph.py 的解析函数搬入）
│   ├── style.py             # style 节点（T10: LLM 选样）+ style_review interrupt
│   ├── slides.py            # writer 循环 + HTML 渲染 + 预览事件
│   ├── review.py            # validate + vlm_review + repair 回路（T6-T9）
│   ├── export.py            # export 节点
│   └── regenerate.py        # 页级重做流程（从 service.py 搬入，T2: 走 writer LLM）
│
├── rendering/               # HTML → 像素
│   ├── __init__.py
│   ├── html.py              # 组件 → HTML（原 html_renderer.py；f-string 模板保留为唯一模板源）
│   ├── charts.py            # [NEW] chart 数据 → 内联 SVG（T1）
│   ├── browser.py           # [重写] BrowserSession：每请求一实例、懒启动、page 复用、截图 + DOM 度量
│   └── fallback.py          # Pillow 降级渲染（原 browser.py 下半部分搬入）
│
├── export/                  # HTML → PPTX
│   ├── __init__.py
│   ├── pptx.py              # 混合导出组装（原 exporter.py）
│   └── text_layer.py        # data-text 提取 JS（唯一副本）+ px→pt/EMU 换算 + 行距映射
│
├── quality/                 # 确定性校验 + VLM
│   ├── __init__.py
│   ├── issues.py            # ValidationIssue + 去重合并（修 P14）
│   ├── rules.py             # spec 级校验 + DOM 级溢出/重叠/一致性校验（吸收 coherence 检查）
│   └── vlm.py               # VLM 审查：拼图、rubric、issue 解析（T8）
│
├── design_system/
│   ├── deck.css
│   ├── presets.json         # 扩到 6 套（含 ≥2 套深色/高对比），style 节点从中选
│   └── components.md        # 组件目录文档（prompt 素材，和 html.py 保持同步）
│
├── prompts/
│   ├── brief.md  outline.md  style.md  writer.md  review.md  repair.md
│   └── (删除: slide.md, slides.md, designer.md, coherence.md)
│
└── (删除: graph.py→pipeline/, html_renderer.py→rendering/html.py, browser.py→rendering/,
        exporter.py→export/pptx.py, validators.py→quality/rules.py,
        renderer.py, design_system/components/*.j2)

tests/
├── test_deck_graph.py       # 端到端（保留并更新 import）
└── deck/
    ├── test_spec.py         # normalize / compact / placeholder 检测
    ├── test_rules.py        # 密度 / 对比度 / DOM 溢出（用 fixture HTML）
    ├── test_charts.py       # SVG 图表 3 类型 + 空数据降级
    ├── test_html_render.py  # 每组件 golden HTML 断言（data-text 覆盖率、无占位）
    ├── test_text_layer.py   # 提取 JS 结果 → 文本框几何换算
    ├── test_pptx_export.py  # fixture HTML → pptx（文本可编辑、版本一致性 T3）
    ├── test_events.py       # 事件构造 schema 断言
    └── fixtures/            # slide_basic.html / slide_overflow.html / spec_sample.json
```

搬移规则：**用 `git mv` 保留历史**；一次性完成（见执行顺序 Phase B），重组提交里**只挪不改**（允许改 import），行为变化放在各自任务的提交里。`handler/`、`app/` 对 deck 的引用只允许指向 `core.deck.DeckService`。

---

## 2. 任务清单（带验收标准）

### Phase A — 止血（P0，小 diff 快速修，可在旧结构上做）

**T1 图表做实（修 P1）**
- 新增 `rendering/charts.py`：把 `slide.chart` 渲染成**内联 SVG**（支持 `bar` / `line` / `pie` 三种，颜色取 tokens 变量，数值标签用 `data-text` 标记以便可编辑）。不引入 ECharts、不引入 `<script>`。
- chart 数据 schema 收紧并写进 writer.md：`{"type": "bar|line|pie", "series": [{"label": str, "value": number}], "unit": str}`；数值必须来自 fact sheet。
- `chart` 为空/非法时：组件自动降级为 `content-basic`（用 takeaway 当 body），并记 `nice_to_have` issue，事件如实告知。
- ✅ 验收:三种图形各有单测（SVG 里数值/标签齐全）；空 chart 降级路径有单测;writer prompt 更新;不再可能出现空面板。

**T2 页级重做走 writer LLM（修 P2）**
- `regenerate_slide`：把 `feedback + 原 SlideSpec + 组件目录` 交给 writer 模型重写 spec（复用 `invoke_json_with_retries` + `normalize_slide_spec`），feedback **只进 prompt，禁止进任何 content 字段**。
- 未下发 writer 模型配置时：返回 `error` 事件（提示需要 writer 模型），**不做**无 LLM 的假重写。
- prompts/repair.md 改写为这个场景的系统提示（用户反馈驱动的单页重写）。
- ✅ 验收：FakeLLM 单测——输入反馈"文字太多"，断言新 spec 由 LLM 输出决定、feedback 字符串不出现在任何 `data-text` 文本里；无模型时得到 error 事件。

**T3 版本一致性（修 P3）**
- 约定：`slides/{n}.html` / `{n}.png` 永远是**当前权威版本**；`{n}_v{k}.html/png` 只是历史副本。`render_slide` 每次同时写权威文件 + 历史文件；exporter 只读权威文件。
- ✅ 验收：单测——regenerate 后 export，断言 pptx 背景图与新版 png 一致（比对文件哈希或提取图片字节）；`{n}_v2.html` 历史副本存在。

**T4 字体链路（修 P4）**
- Dockerfile：`apt-get install -y fonts-noto-cjk`（放在 playwright install 之后、`COPY . .` 之前的层）。
- `deck.css` / theme CSS 变量：字体栈改为 `"Noto Sans SC", "Noto Sans CJK SC", "PingFang SC", "Microsoft YaHei", sans-serif`。
- 删除 `_font_faces()`（相对路径在 `set_content` 下永远 404，属于死代码）；如未来要自带字体，用 base64 内嵌，本阶段不做。
- ✅ 验收：容器构建后 `fc-list | grep -i "noto sans cjk"` 非空（写进部署文档的自检命令）；mac 本地渲染不回归。

**T5 诚实性快修（修 P8、P13、P14、P15）**
- 删除 coherence 空壳节点（其职责并入 T7 的 rules），不再发虚假"已检查"事件。
- content-basic 右侧卡片：改为展示 `key takeaway`（content.body 的强调排版）或该页 chart/序号图形；删除英文角色标签。
- `validation_report` 在 `quality/issues.py` 里按 `(page, kind, description)` 去重后再发。
- `validate_html_static` 的外链检查改为只匹配**属性里的**外部资源（`src=|href=|url(` 后跟 `http`），正文文本中的 URL 不再误伤。
- ✅ 验收：全量事件里无"做了但其实没做"的叙述；中文 deck 无孤立英文标签；同一 issue 只出现一次。

### Phase B — 目录重组（一次性）

**T6 按 §1 结构 `git mv` + 拆分 graph.py/service.py**
- 重组提交只挪不改行为；`__init__.py` 收紧导出面；解决所有 import；tests 引用同步更新。
- 新增 `events.py`：定义 `EventType` 常量类 + 每种事件一个构造函数（含 payload 字段校验）。**全部** `writer({...})` 裸 dict 调用替换为构造函数。
- 新增 `errors.py`：`DeckInputError`（用户输入问题，映射 error 事件 + 明确提示）、`ModelOutputError`（LLM 输出不可用，重试后仍失败）、`RenderError`、`ExportError`。service 层按类型决定事件文案与是否终止。
- ✅ 验收：`pytest` 全绿；`grep -rn "writer({" core/deck/pipeline` 结果为 0（全走 events.py）；`service.py` ≤250 行；`pipeline/__init__.py` ≤120 行且只含拓扑；无循环导入（`python -c "import core.deck"` 通过）。

### Phase C — 质量闭环（P0，本阶段核心价值）

**T7 BrowserSession 重写（修 P5，是 T8-T10 的地基）**
- 每个请求一个 `BrowserSession`：懒启动（首次渲染才 launch）、**单 browser + 单 page 复用**跨全部页面与导出、`close()` 幂等。
- 生命周期归 service 层：`generate_deck` / `resume_deck` 用 `contextlib.closing`/try-finally 包住整个流，异常路径必关。exporter 不再自行 launch，改为接收 session。
- 注意：Playwright sync API **不允许跨线程共享**——session 归属单个请求线程即可满足；文档里写明"不要做进程级单例"。
- `wait_until` 从 `networkidle` 改为 `load`；固定 300ms 等待只保留在含 SVG 动画/图表的页面（本方案 SVG 是静态的，可直接删）。
- ✅ 验收：单测（mock playwright）断言 12 页渲染 + 导出全程 `launch` 恰好 1 次、异常路径 `close` 被调用；本机 benchmark 记录到 PR 描述（预期 12 页渲染从分钟级降到 <10s）。

**T8 DOM 级校验（修 P9）**
- 在截图同一 page 会话里 `evaluate` 检查脚本（JS 放 `export/text_layer.py` 同级或 `quality/rules.py` 内常量，**唯一副本**）：
  - 溢出：任意元素 `scrollHeight > clientHeight + 2` 或包围盒越出 1280×720；
  - 重叠：`[data-text]` 元素两两相交面积 > 4%；
  - 结果并入该页 issues（`must_fix`）。
- ✅ 验收：`fixtures/slide_overflow.html`（超长标题）检出 overflow；`slide_basic.html` 零误报；单测不依赖 LLM。

**T9 repair 回路（修 P6）**
- 拓扑改为：`slides → validate → (有 must_fix 且未超预算 ? repair → validate : review_vlm)`（LangGraph 条件边）。
- repair 策略：优先把 `issue + 原 spec` 交给 writer 重写该页 spec 再渲染；重写后仍 must_fix → 确定性降级（字号降一档 → 换低密度组件），**每次降级都发 warning 事件如实说明**。每页 ≤2 轮，全 deck 修复总次数 ≤ 页数×1.5。
- ✅ 验收：FakeLLM 端到端测试——构造一页超密 spec，观察 `repair_progress` 事件序列、最终 report 无 must_fix；轮次上限有测试（故意让 LLM 一直输出超密内容 → 走降级 + warning，流程不死循环）。

**T10 VLM review 节点（修 P7）**
- `quality/vlm.py`：全部页面 png 按 2×3 拼网格（Pillow），带 rubric（溢出/拥挤、对齐失衡、层级不清、跨页风格漂移、图文不匹配、低级感）调用 `review` 模型；输出 `[{page, severity, kind, description, suggestion}]`，只认 rubric 内 kind。
- **未配置 review 模型时跳过该节点**，发一条如实的 `stage_progress`（"未配置视觉审查模型，已跳过"），流程不断。
- must_fix 走 T9 的 repair 通道（共享轮次预算）；nice_to_have 只进 validation_report。
- prompts/review.md 按此 rubric 重写。
- ✅ 验收：Fake VLM 单测驱动一轮 repair；未配置时端到端流程完整走通。

### Phase D — 多 agent 做实 + 健壮性（P1）

**T11 style 节点 LLM 化（修 P10）**
- presets.json 扩到 6 套（新增 ≥2 套深色、1 套高饱和），每套加 `works_best_for` 描述字段。
- style 节点：把 brief + 大纲摘要 + 6 套预设描述交给 `style` 模型，选出 2 套并允许微调 palette/type_scale（输出仍需过 `validate_theme_contrast`，不合格回退原预设并发 warning）；无 `style` 模型时回退当前逻辑（取前两套）并如实发事件。
- style feedback：交给同一模型把意见翻译成 tokens 修改（同样过对比度校验），不再拼进 motif。
- ✅ 验收：FakeLLM 单测——不同 brief 得到不同候选组合；反馈"想要深色的"→ tokens.bg 变深且对比度校验通过；无模型回退路径有测试。

**T12 brief 节点 LLM 化（修 P10 另一半）**
- 有 `brief` 模型时：LLM 提炼 fact sheet（`{id, statement, value?, unit?, source_file?}`）+ audience/goal/language/suggested_pages；无模型时保留现有 regex 版并在事件里说明"使用了确定性素材提取"。
- writer 的 `sources` 校验：引用不存在的 fact id → `nice_to_have` issue（防编造引用）。
- ✅ 验收：FakeLLM 单测；fact id 校验单测。

**T13 健壮性收尾**
- 每个 pipeline 节点顶层 try/except：意外异常 → `errors.py` 分类 → error 事件带 stage 与可读信息，**不再让整个 gRPC 流只剩一句 "Deck generation failed"**。
- 渲染超时 10s/页：超时走 Pillow fallback + warning（现有 fallback 保留）。
- `payload_json` 反序列化上限 64KB；`style_id` 非法时选第一套**并发 warning**（现在是静默）。
- 导出后校验：pptx 文件非空、页数 == spec 页数，不符则 ExportError。
- ✅ 验收：注入故障的单测各一个（节点抛异常 / 渲染超时 / 非法 style_id / 空 pptx）。

### Phase E — 可读性与测试（贯穿，最后收口）

**T14 工程规范落地（见 §3）+ 死代码清理（修 P11、P12）**
- 删除：`renderer.py`、`design_system/components/*.j2`、prompts/{slide,slides,designer,coherence}.md。
- 魔法数字提为命名常量并注释单位：`THUMB_MAX_BYTES = 60_000`、`RENDER_TIMEOUT_MS`、`TEXTBOX_WIDTH_SLACK = 1.02` 等；exporter 的 `max(4,…)` 4px 下限删除，直接用测量宽高 + slack。
- pptx 文本框补行距：`paragraph.line_spacing = lineHeight / fontSize`（修 P16）。
- ✅ 验收：仓库内无死文件；`grep -rn "\b(300|60000|1.02)\b" core/deck --include="*.py"` 命中处均为命名常量定义。

**T15 测试补齐（修 P17）**
- 按 §1 的 tests/deck/ 结构补齐；exporter/DOM 校验测试依赖 Playwright 的用例加 `@pytest.mark.skipif`（chromium 不可用则跳过并说明）。
- 每组件一个 golden HTML 断言：含 `data-text` 的可编辑文本覆盖所有用户内容、无占位词、`.slide` 根存在。
- ✅ 验收：`pytest -q` 全绿；核心模块（spec/rules/charts/text_layer/pptx）每个公共函数至少一个测试。

---

## 3. 工程规范（健壮性 + 可读性，实施全程遵守）

1. **事件诚实原则**：事件文案必须描述真实发生的事。没做的检查不许说"已检查"；每次降级（Pillow 兜底、整页截图导出、组件降级、字号降档、跳过 VLM）必须发对应 warning/progress 事件。
2. **禁止静默降质**：任何截断、fallback、忽略都要么修复要么上报，`_soft_limit` 是唯一允许的收敛手段且发生在 spec 层（渲染前），渲染后发现超密走 repair。
3. **错误分层**：用户可修复的（输入/配置缺失）→ `DeckInputError` + 明确提示；模型问题 → 重试 ≤2 → `ModelOutputError`；基础设施问题（浏览器/导出）→ 降级 + warning，降无可降才 error。裸 `except Exception` 只允许出现在节点顶层和降级点，且必须 log + 事件。
4. **唯一出口**：事件 dict 只能由 `events.py` 构造；data-text 提取 JS 只有一份；组件模板只有 `rendering/html.py` 一份；组件清单只有 `spec.COMPONENTS` 一份（components.md 是它的文档投影，改组件必须两处同步——加一条单测校验两者一致）。
5. **函数与模块尺度**：单函数 ≤60 行、单文件 ≤400 行（spec.py 例外 ≤500）；超了就拆。公共函数全类型注解；模块 docstring 一句话说清职责边界（"本模块只负责 X，不做 Y"）。
6. **资源纪律**：Playwright page/browser 的获取处必须能看到对应的释放路径（with/try-finally）；文件写入统一经过 request_dir helper，禁止散落的 `mkdir`。
7. **兼容红线**（沿袭 Phase 1）：`GenerateDeckResponse` 字段语义不变；outline 流式 delta（`channel=outline_markdown`）逐字节不变；proto 只加字段不改编号；`slide_preview`/`style_options` payload 已被前端消费（`front/src/pages/PptAgentPage.vue`），改字段名要同步前端。
8. **提交纪律**：目录重组是独立提交（只挪不改）；每个任务一个提交，提交前 `pytest -q` 全绿；benchmark 数字写进提交说明。

---

## 4. 执行顺序

```
Phase A (T1-T5 止血)  →  Phase B (T6 重组，独立提交)  →  Phase C (T7→T8→T9→T10 闭环)
→  Phase D (T11-T13)  →  Phase E (T14-T15 收口)
```

理由：A 的硬伤 diff 小、上线急，先在旧结构修掉；B 重组后所有新功能（C/D）都在新结构上开发，避免反复挪家；T7（浏览器会话）必须在 T8/T9/T10 之前，因为 DOM 校验、repair 重渲染、VLM 拼图都依赖同一 session 的低成本重渲染。

## 5. 明确不做（防实施跑偏）

- 不引入 Node.js / ECharts / 外部图库 / 生图（图表 = 内联 SVG）。
- 不做并行页面生成（写作串行保留上下文传递，Phase 3 再评估）。
- 不换 checkpointer（InMemorySaver 保持）。
- 不改 gRPC 服务定义（现有 5 个 RPC 够用）。
- 不做前端改版（前端只需兼容既有 payload 字段，新增字段向后兼容）。
