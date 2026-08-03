# PPT 全局大纲阶段

当前 Python Runtime 的全局语义规划位于
`apps/runtime/core/deck/agents/preprocessor/graph.py`。它不再执行“需求解析、逻辑大纲、分页映射”三段模型链，
而是在每个任务进入页面并发前执行一次 `generate_deck_outline`。

## 输入与输出

`outline` 模型一次接收：

- 用户原始需求；
- 已解析的 Markdown / 标准化来源文本；
- 来源文件、图片、图表、表格、KPI、仪表盘等可用资产摘要；
- 用户显式页数与系统 `1..30` 页约束；
- 支持的 layout family、安全画布和首尾页规则。

它必须一次返回完整的 `DeckOutlineSpec`：整套标题与 metadata、唯一的
`GlobalStyleSpec`，以及连续 `s01..sNN` 的 `SlideOutlineSpec` 页面清单。结构化输出及跨字段校验在同一重试边界内完成；
页数、首尾角色、连续 ID、全局色板或相邻布局不合法时会重试，最终失败则终止任务，不会持久化或下传半成品。

## 与页面生成的边界

真实根图直接暴露目标节点：

```text
START
  -> generate_deck_outline
  -> fan_out_slides
  -> Send(generate_slide, slide_1 .. slide_n)
  -> aggregate_slides
  -> assemble_image_pptx
  -> finalize_deck
  -> END
```

全局阶段会把 outline 确定性转换为 renderer 使用的 `DeckPlan`、layout preference 和 `DesignSpec`。
这些 adapter 不调用额外模型。每个并发页面随后严格执行
`prepare_slide -> generate_slide_design(style) -> generate_slide_html(designer)`，再进入原有清洗、DOM QA、repair、fallback、PNG 和 PPTX 路径。

## 落盘产物

每个任务在页面分支开始前写入：

```text
deck-outline.json
global-style.json
deck-plan.json
outline.md
design-spec.json
design-spec.md
spec-lock.json
spec-lock.md
page-blueprints.json
template-manifest.json
```

页面分支另行写入 `slide_specs/page-NNN.design.json`、`page-NNN.content.json` 与
`page-NNN.html.json`。旧 `global-meta.json`、`logical-outline.json`、场景模板和 `page-plan.json`
不再属于当前流程；旧任务也不会被当作 `deck-state.v6` state 恢复执行。

对应测试集中在 `tests/runtime/test_deck_preprocessor.py`、
`test_deck_html_graph_integration.py` 与 `test_deck_pipeline_graph.py`。
