# PPT 前处理架构

当前 Python Runtime 已把 PPT 前四步收敛为独立 `preprocessor`，代码位于：

```text
apps/runtime/core/deck/agents/preprocessor/
├─ graph.py                 # 三节点 LangGraph 与编排入口
├─ schema.py                # GlobalMeta / LogicalOutline / PagePlan
├─ templates.py             # 只负责读取场景叙事模板（资产 A）
└─ assets/scenes/*.json     # 答辩、汇报、路演、教学、通用场景
```

## 处理时序

```text
用户自然语言与附件文本
  ↓
parse_requirements
  → GlobalMeta（结构化全局元数据）
  ↓
create_logical_outline
  → LogicalOutline（层级内容树，无页面概念）
  ↓
map_outline_to_pages
  → 此处首次读取 SceneNarrativeTemplate（资产 A）
  → PagePlan（page_no + page_category + topic）
  ↓
preprocessor 结束
```

`create_logical_outline` 不接收叙事模板或版式模板。`map_outline_to_pages` 只接收资产 A，不 import `design_system`，也不读取 layout recipe、HTML 模板或渲染参数。

## 三份稳定产物

`GlobalMeta` 负责表达整套演示的主题、受众、场景、沟通目标、语言、目标页数、内容约束、来源模式和用户明确提出的风格偏好。运行时会附带标准化 source text，但模型不能自行输出 source text。

`LogicalOutline` 负责表达章节、主题、主题意图和关键点。它不能包含 page、slide、page count、page category、layout 或渲染字段，因而增减页数时不需要重写内容逻辑。

`PagePlan` 是前处理的终点：

```json
{
  "scene": "thesis_defense",
  "pages": [
    {"page_no": 1, "page_category": "cover", "topic": "课题名称"},
    {"page_no": 2, "page_category": "catalog", "topic": "内容结构"},
    {"page_no": 3, "page_category": "content", "topic": "研究背景"}
  ]
}
```

单页对象严格只允许 `page_no`、`page_category` 和 `topic`。Pydantic 使用 `extra="forbid"`，所以 `layout_id`、`layout_template`、`layout_family`、`content`、`media`、`html` 或 `svg` 都会直接校验失败。

## 资产 A：场景叙事模板

资产格式与业务定义保持一致：

```json
{
  "scene": "thesis_defense",
  "mandatory_page_types": ["cover", "catalog", "thanks"],
  "suggest_page_order": ["cover", "catalog", "content", "content", "chart", "summary", "thanks"],
  "page_min": 10,
  "page_max": 16
}
```

分页节点会将用户目标页数限制在场景模板的 `page_min/page_max` 内，并验证连续页码、精确总页数及必需页面类型。模型输出不合法时，确定性分页仍遵守同一份资产 A。

## 与 processor / postprocessor 的边界

根图严格串联三个 phase：

```text
preprocessor_phase -> processor_phase -> postprocessor_phase
```

`processor` 位于 `core/deck/agents/processor/`。它在 PagePlan 完成后才执行 layout 规划，随后完成主题设计、逐页内容、HTML/CSS 生成、清洗、DOM 测量、校验、有限修复、fallback 和 PNG 持久化。它不会导出 PPTX，也不会修改或回写 PagePlan。

`postprocessor` 位于 `core/deck/agents/postprocessor/`，只消费 processor 已验证的页面 PNG 与元数据，负责质量报告、manifest、image-only PPTX 汇总、导出事件和最终文件校验。它不调用 LLM，也不重新生成或修复 HTML。

## 落盘文件

每个任务目录新增：

```text
global-meta.json
logical-outline.json
logical-outline.txt
scene-narrative-template.json
page-plan.json
```

测试 `tests/runtime/test_deck_preprocessor.py` 覆盖三节点时序、资产 A 读取、答辩模板页数边界、禁止版式字段、前处理依赖隔离、落盘产物及 processor 不回写 PagePlan。
