# PPT 生成流程（SVG Project Workspace）

本流程借鉴 [PPT Master](https://github.com/hugohe3/ppt-master/blob/main/README_CN.md) 的核心思路，但按服务端任务平台重新实现：保留 Go/API、SSE、checkpoint 和前端交互，重构 Runtime 内部的内容契约、执行顺序、质量门与产物目录。

## 设计原则

1. **内容事实与视觉执行分离**：`sources/` 保存归一化内容和事实账本；`design-spec` 解释设计；`spec-lock` 是机器执行锁。
2. **页数先冻结再绘制**：确认后的页面清单、布局、节奏和模板来源不可在 Executor 阶段自动增删；密度问题通过同页改写解决。
3. **逐页端到端执行**：每页依次完成 Content Editor、磁盘锁重读、SVG Artist、静态检查和浏览器预览，再进入下一页。
4. **SVG 是唯一作者源**：PowerPoint、PNG 和 HTML 都是派生物；修复与单页重做只更新 `svg_output/` 的当前版本。
5. **质量问题不静默降级**：固定组件兜底会明确产生 must-fix/`needs_review`；配置了 Designer 时，第 1 页多轮失败会立即停止整套生成。
6. **模板默认不锁死内容**：未显式指定模板时，默认使用自由设计结构；模板包作为用户可选方向，而不是通过关键词偷偷套用。
7. **主题选择不做实时绘制**：目录阶段只读取预生成的静态封面；Designer 与 Chromium 都从模板确认后的逐页执行阶段才开始工作。

## 主流程

```text
请求/附件
  → Source Intake：归一化文本、来源分块、图片入池、事实账本
  → Strategist：brief + 叙事大纲
  → 用户确认大纲
  → Template Curator：从 11 套目录中筛选 6 套静态封面 + 八项确认
  → 用户选择方向
  → Design Lock：design-spec + spec-lock + page-blueprints + manifests
  → Page Executor（严格逐页）
       Content Editor → 重读 spec-lock → SVG Artist → SVG Gate → Chromium Preview
  → Deterministic Validate ↔ Repair
  → VLM Visual Review ↔ Repair
  → Coherence（最多一轮）→ 对改动页定向复核
  → Native SVG → DrawingML PPTX
  → PPTX 结构校验 + 质量报告 + SVG 冻结备份
```

## 请求项目目录

每个请求对应一个可恢复项目：`DECK_ARTIFACTS_DIR/{request_id}/`。

```text
{request_id}/
├── sources/                 # 归一化内容、source chunks、fact ledger、raw/final spec
├── analysis/                # 预留的机器分析事实，不存设计决策
├── images/                  # 经清洗的运行时图片池
├── templates/               # 锁定页面使用的结构 SVG，以及 request-local 静态目录封面
├── svg_output/              # 唯一作者源：page-001.svg、历史版本
├── previews/                # 正式页面的 HTML/PNG 派生预览
├── notes/total.md           # 汇总讲稿；PPTX 同时内嵌逐页备注
├── reports/                 # quality / generation / export manifest、VLM contact sheet
├── exports/deck.pptx        # 默认下载的原生可编辑 PPTX
├── backup/<timestamp>/
│   └── svg_output/          # 本次导出使用的作者源冻结快照
├── design-spec.json|md      # 人类可审阅设计说明
├── spec-lock.json|md        # Executor 唯一机器执行锁
├── page-blueprints.json
├── asset-manifest.json
└── template-manifest.json
```

旧任务的 `slides/{n}.*` 与根目录 `deck.pptx` 仍可读；新任务只写新目录。Go 默认下载先查 `exports/deck.pptx`，不存在时再回退旧路径。

## 机器质量门

- 页面 roster 必须与锁定蓝图一一对应。
- SVG 必须使用固定画布、受支持元素、唯一 ID 和安全图片路径。
- 颜色只能来自执行锁；字体只能来自锁定字体；正文和注释分别执行最小字号。
- SlideSpec 标题、正文、数字和图表标签不可丢失或凭空增加。
- 实际元素必须覆盖足够的锁定几何区域，不能只补正确 metadata 假装继承模板。
- 相邻页不能复用相同几何指纹。
- native PPTX 导出后必须可打开、页数一致、文本框几何合法、可编辑文本数量不丢失。
- `quality-report.json` 中存在 must-fix 时结果标为 `needs_review`；结构/安全/内容丢失类问题禁止导出。

## 模型配置建议

- `designer`：适合代码/长文本输出的强模型，`max_tokens` 至少 8K。
- `review`：必须声明图片输入能力；否则视觉复核会产生明确 must-fix。
- 追求质量时不要把便宜小模型同时绑定到 Strategist、Writer、Designer 和 Review；工作流只能约束下限，最终视觉上限仍由模型决定。

## 当前边界

- 已实现自带图片入池和轮换分配，尚未自动接入网络搜图/AI 生图及许可署名工作流。
- 当前模板目录共 11 套，其中 7 套通用 Layout 来自 PPT Master 固定版本并保留 MIT 许可；品牌、企业 Logo 和专有字体模板未导入。
- PPT Master 原始模板包含渐变、filter、path 等当前原生导出不允许的能力，因此静态封面保留原貌，正式执行则使用转换后的 theme、geometry 与受限 SVG motif，不能把原始 SVG 直接复制到 `svg_output/`。
- 后续仍需扩展经过人工 QA 的 chart/icon/illustration 资产，并为模板目录增加完整浏览、分类和搜索界面。
- 现有自动测试验证契约、路径、安全、原生可编辑结构和流程闭环；真实审美仍应使用固定 golden brief、真实模型录制响应和 Office/LibreOffice 渲染回归。
