# PPT 制作流程梳理

本文根据 [`1.txt`](./1.txt)、[`2.txt`](./2.txt) 和 [`3.txt`](./3.txt) 整理，描述这组样本所体现的 PPT 生成流程，并说明它与当前项目 HTML-first 流水线的对应关系。

> 证据边界：三个 `.txt` 保存的都是发给模型的 **user prompt**，不是完整调用日志；文件中没有模型实际返回的 Deck Outline JSON、Design Plan JSON 或最终 HTML。因此前三个模型阶段可以从提示词合同中确定，而 HTML 后的渲染、校验和 PPTX 导出环节，是结合当前项目实现与文档补全的。

## 一、结论

三个文件分别对应三个连续的模型阶段：

1. **整套 PPT 策划**：把用户需求、参考资料和数据资产整理为演示大纲与全局视觉规范。
2. **逐页视觉设计**：为大纲中的每一页生成具体布局、层级、配色和实现方案。
3. **逐页 HTML 执行**：根据单页信息和设计方案生成可渲染的静态 HTML。

| 原始文件 | 调用粒度 | 提示词要求的模型输出 | 样本中的具体对象 |
|---|---|---|---|
| `1.txt` | 整套一次 | Deck Outline JSON + 全局调色板 | 将原始 15 页内容压缩成 8 页大纲 |
| `2.txt` | 每页一次 | Slide Design Plan JSON | 针对第 2 页“核心议题概览”规划 2×2 卡片目录 |
| `3.txt` | 每页一次 | 静态 HTML | 把第 2 页设计方案落实为 `1280×720` 页面 |

HTML 之后还需要由确定性程序完成浏览器渲染、质量检查、截图和 PPTX 组装。因此完整链路是：

```mermaid
flowchart LR
    A["用户需求、参考资料、数据资产"] --> B["步骤 1：整套 PPT 策划"]
    B --> C["Deck Outline JSON"]
    C --> D["步骤 2：逐页视觉设计"]
    D --> E["Design Plan JSON"]
    E --> F["步骤 3：逐页 HTML 生成"]
    F --> G["静态 HTML"]
    G --> H["Chromium 渲染与 DOM QA"]
    H --> I["逐页 PNG 与实时预览"]
    I --> J["聚合并导出 image-only PPTX"]
```

## 二、步骤 1：整套 PPT 策划

### 模型角色

PPT 策划专家、内容架构师。

### 输入

- 用户原始需求，例如主题、页数、语言和风格。
- 完整 Markdown 参考资料。
- 可用表格、图表、KPI 和 dashboard 资产清单。
- 图表与表格的使用规则。

### 主要任务

- 提炼演示主题、核心观点和叙事顺序。
- 将原始资料重组为“总—分—总”的演示结构。
- 为每页选择合适的 `layout_suggestion` 和 `content_mode`。
- 避免连续三页使用相同布局。
- 生成整套 PPT 共用的视觉风格和全局调色板。

### 输出

只返回一份整套 PPT 的 JSON：

```json
{
  "title": "演示标题",
  "subtitle": "副标题",
  "metadata": {
    "author": "",
    "date": "",
    "keywords": [],
    "design_style": "Dark Business",
    "source_summary": "资料摘要",
    "global_style_spec": {
      "background": "#1A1A24",
      "accent_primary": "#00E5FF",
      "accent_secondary": "#FF3366",
      "text_primary": "#FFFFFF",
      "text_secondary": "#A0A0B0",
      "card_bg": "#252535",
      "card_border": "#3A3A50",
      "decoration": "rgba(0, 229, 255, 0.15)",
      "top_bar": "#00E5FF",
      "overlay_bg": "rgba(26, 26, 36, 0.85)"
    }
  },
  "slides": [
    {
      "id": 1,
      "title": "页面标题",
      "type": "title",
      "key_points": [],
      "layout_suggestion": "cover",
      "content_mode": "paragraph",
      "content_note": "内容来源与本页目的"
    }
  ]
}
```

### 阶段边界

本阶段只决定整套演示的内容结构和视觉基线，不生成具体 HTML。

样本提示词硬性限制最多 8 页，所以原始需求虽然写了 15 页，实际大纲被压缩为 8 页。

## 三、步骤 2：逐页视觉设计

### 模型角色

PPT 视觉设计师、交互设计师。

### 输入

- 步骤 1 生成的标题、元数据和全局调色板。
- 当前幻灯片的 `Slide Outline`。
- 全部幻灯片列表，用于理解上下文和前后页关系。
- 原始参考资料和结构化资产。
- 18 类布局模板及适用场景。
- 相邻页面的布局信息。

### 主要任务

- 判断当前页真正要表达的核心结论。
- 从参考资料中选择与本页有关的证据和数据。
- 在大纲建议的基础上确定具体布局。
- 避免与前后页使用相同布局。
- 明确内容结构、视觉层级、字体、间距和关键元素。
- 检查页面边界、内容数量、页码空间和全局风格一致性。

### 输出

每一页返回一份设计方案 JSON：

```json
{
  "slide_id": 1,
  "thinking_process": {
    "analyzing_request": "页面目标与内容需求摘要",
    "content_structure": "标题、主体和辅助信息如何组织",
    "layout_design": "选用的布局及选择原因",
    "code_implementation": "建议的 HTML/CSS/SVG 实现方式",
    "design_choices": "颜色、字体、间距等设计决策",
    "style_consistency_check": "与整套 PPT 规范的一致性检查",
    "finalization": "边界、对齐、重叠和可读性检查"
  },
  "design_specifications": {
    "layout_type": "dual-column",
    "color_scheme": {},
    "typography": {},
    "key_elements": [],
    "visual_hierarchy": []
  }
}
```

### 阶段边界

本阶段负责“怎么设计”，但不输出最终 HTML。步骤 3 中的 `设计方案` 就是本阶段的结果。

每页设计相互独立，在共享全局规范后可以并行执行；跨页一致性由全局调色板、布局历史和邻页信息保证。

## 四、步骤 3：逐页 HTML 生成

### 模型角色

前端开发工程师、PPT 执行设计师。

### 输入

- 当前页的幻灯片信息。
- 步骤 2 生成的设计方案。
- 步骤 1 生成的全局调色板。
- 与当前页相关的参考资料和数据资产。
- 可选背景图片地址。

### 主要任务

- 将设计方案落实为完整、静态、可直接渲染的 HTML。
- 从参考资料提取真实内容，不只复述 `key_points`。
- 严格执行全局颜色、字体、布局和几何约束。

### 输出约束

- 只返回 HTML，不返回 Markdown 代码围栏或解释。
- 页面固定为 `1280×720`。
- 所有样式使用内联 `style`。
- 使用 Noto Sans SC。
- 禁止 JavaScript、动画、过渡和交互。
- 禁止 CSS 渐变。
- 所有正文内容必须位于 `content-layer` 中。
- 控制列表、卡片、时间轴和目录节点数量。
- 预留页码区域，防止元素越界或重叠。

### 阶段边界

本阶段只产生页面 HTML。它本身不会生成 `.pptx`。

## 五、渲染、校验和导出

这部分没有出现在三个样本提示词中，但它是把 HTML 变成 PPT 的必要环节。当前项目已经实现：

1. 清洗模型生成的 HTML/CSS，并注入受信任的页面外壳。
2. 在受控 Chromium 中渲染页面。
3. 测量 DOM，检查越界、溢出、重叠、空内容、字号和对比度。
4. 校验失败时最多进行两轮定向修复，仍失败则使用确定性 fallback。
5. 截取 `3200×1800` PNG，并发布逐页预览。
6. 聚合所有已通过校验的页面。
7. 将每张 PNG 全出血铺入一页 16:9 PowerPoint。
8. 反向打开 PPTX，校验页数、图片数量和覆盖范围。

最终 PPTX 与 PNG 预览视觉同源，但页内文字、图标和图表不可单独编辑。

## 六、三步之间的数据关系

| 阶段 | 处理范围 | 核心输入 | 核心输出 | 是否调用模型 |
|---|---|---|---|---|
| 步骤 1：大纲策划 | 整套 PPT | 用户需求、资料、资产 | Deck Outline、全局风格 | 是 |
| 步骤 2：视觉设计 | 单页 | 当前页、大纲上下文、全局风格 | Design Plan | 是 |
| 步骤 3：HTML 执行 | 单页 | 当前页、Design Plan、资料 | HTML | 是 |
| 渲染与 QA | 单页 | HTML | 已验证 PNG | 否，修复时可能调用模型 |
| PPTX 导出 | 整套 PPT | 所有 PNG | `.pptx`、manifest、质量报告 | 否 |

## 七、与当前项目三个模型节点的对应关系

当前项目现行生产配置只有 `outline`、`style`、`designer` 三个模型节点，与样本三步基本一一对应：

| 样本流程 | 当前项目节点 |
|---|---|
| 步骤 1：整套 PPT 策划 | `outline`：一次生成完整大纲、页面规划和全局样式 |
| 步骤 2：视觉设计规划 | `style`：逐页生成可审计的视觉设计方案 |
| 步骤 3：页面内容与 HTML | `designer`：逐页生成 HTML，必要时也负责定向修复 |
| HTML 校验、截图、导出 | 本地确定性程序 |

来源标准化、页面内容合同和 renderer plan 由程序从 outline 确定性派生，不产生额外模型调用。三个模型节点可分别配置模型与重试策略。

## 八、样本中需要注意的问题

### 1. 原始页数与硬上限冲突

用户要求 15 页，但步骤 1 的提示词强制最多 8 页。应由统一的产品规则决定页数，避免提示词在模型侧悄悄截断。

### 2. 文件封装格式不统一

- `1.txt`、`3.txt` 是合法 JSON 消息数组，但都只有一条 `user` 消息，没有 `assistant` 返回值。
- `2.txt` 使用单引号形式的 Python 对象外观，并含未转义换行，既不是合法 JSON，也不能被 `ast.literal_eval` 直接解析。
- 三个文件是“提示词样本”，不能单独用于复现最终 PPT；至少还需模型返回值、渲染配置和导出 manifest。

建议统一保存为 UTF-8 JSONL，并通过 JSON 序列化器写入，避免字符串拼接。

### 3. 全局颜色与示例颜色冲突

步骤 2 要求严格使用全局调色板，但输出示例又出现 `#0f172a`、`#60a5fa` 等另一套色值。模型可能模仿示例而违反全局规范。示例应直接引用设计 token，不能放入冲突色值。

### 4. 提示词重复传递大量资料

步骤 2 和步骤 3 都携带完整参考资料及资产清单，页数增加后会显著消耗上下文。更合理的做法是先建立来源索引，再只向单页节点传递相关证据切片。

### 5. 不宜要求完整思考过程

生产流程只需要可审计的设计决策摘要。建议将 `thinking_process` 改名为 `design_rationale`，保留布局理由、视觉层级和检查结果，不要求模型输出完整内部推理。

### 6. 外部网络资源不稳定

样本允许 Unsplash 背景和 FontAwesome CDN，但当前安全渲染禁止网络请求。生产环境应使用本地图片库、受控图标注册表和已安装字体。

## 九、推荐的中间产物

```text
run/
├─ global-meta.json
├─ logical-outline.json
├─ page-plan.json
├─ deck-plan.json
├─ design-spec.json
├─ slide_specs/
│  ├─ page-001.content.json
│  └─ page-001.html.json
├─ previews/
│  ├─ page-001.html
│  └─ page-001.png
├─ diagnostics/
│  └─ page-001.json
├─ reports/
│  ├─ quality-report.json
│  └─ generation-manifest.json
└─ exports/
   └─ deck.pptx
```

这使每一步都能独立检查、重试和审计，也能明确区分模型原始输出、清洗后的 HTML、渲染图片和最终交付文件。
