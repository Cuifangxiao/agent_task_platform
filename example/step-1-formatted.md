你是一个顶级的PPT策划专家和内容架构师。请基于以下参考资料，创作一个**专业、吸引人、结构多样化**的PPT大纲。

## 用户原始查询

外部科技趋势与内部经营决策，15页中文高管PPT，深色商务风格，整合科技公司估值、AI产品竞争格局、零售经营分析、归因预测和情景结论

## Presentation Asset Guidance
Please distinguish charts, tables, KPI blocks, and dashboards when planning the PPT layout.

## 展示资产
以下是当前可用于报告、HTML 或 PPT 编排的结构化展示资产。
### 表格
- type=table, variable=df_whatif, title=36个月月度时间序列，含销售额_万/利润_万/客单价/流量/营销费用_万元/转化率，用于w..., rows=36, columns=date, 销售额_万, 利润_万, 客单价, 流量
- type=table, variable=df_attr, title=最近12个月月度时间序列，含利润率（目标）及客单价/成本率/营销费用率/转化率（驱动因子）..., rows=12, columns=date, 销售额, 成本, 利润, 营销费用
- type=table, variable=df_marketing, title=按月份+渠道聚合的营销费用/销售额/利润（万元），含营销ROI和异常标记, rows=144, columns=月份, 渠道, 营销费用_万, 销售额_万, 利润_万
- type=table, variable=df_channel_month, title=按月份+渠道聚合的销量/流量/转化率，用于渠道转化率热力图, rows=144, columns=月份, 渠道, 销量, 流量, 转化率
- type=table, variable=df_region_product, title=按区域+产品聚合的销售额和利润（万元），区域按总销售额从高到低排序, rows=25, columns=区域, 产品, 销售额_万, 利润_万
- type=table, variable=df_monthly, title=36个月零售经营月度汇总，含销售额/利润/毛利率/客单价/转化率/营销费用率等指标, rows=36, columns=月份, 销量, 销售额, 成本, 利润
- type=table, variable=df_retail, title=36个月零售经营模拟数据（2023-08至2026-07），包含月份/区域/产品/渠道/销..., rows=3600, columns=月份, 区域, 产品, 渠道, 销量
- type=table, variable=df_ai_models, title=OpenAI/Anthropic/Google最近12个月主要生成式AI产品，含发布日期、..., rows=12, columns=公司, 产品名称, 发布日期, 定位, 模态
- type=table, variable=df_revenue, title=特斯拉、英伟达、微软最近五个完整财年营收（亿美元），含财年口径和来源链接, rows=15, columns=公司, 股票代码, 财年, 财年口径, 营收_亿美元
- type=table, variable=df_market_cap, title=科技龙头（英伟达、微软、特斯拉）最新交易日市值对比，单位十亿美元，来源联网查询, rows=3, columns=公司, 股票代码, 市值_十亿美元, 市值_万亿美元, 币种
优先将图表资产用于趋势展示，将表格资产用于精确证据，将指标卡资产用于核心数字，将看板资产用于总览编排。

## Structured Presentation Slots
available_charts:
- none
available_tables:
- variable=df_whatif, rows=36, columns=date, 销售额_万, 利润_万, 客单价, 流量
- variable=df_attr, rows=12, columns=date, 销售额, 成本, 利润, 营销费用
- variable=df_marketing, rows=144, columns=月份, 渠道, 营销费用_万, 销售额_万, 利润_万
- variable=df_channel_month, rows=144, columns=月份, 渠道, 销量, 流量, 转化率
- variable=df_region_product, rows=25, columns=区域, 产品, 销售额_万, 利润_万
- variable=df_monthly, rows=36, columns=月份, 销量, 销售额, 成本, 利润
- variable=df_retail, rows=3600, columns=月份, 区域, 产品, 渠道, 销量
- variable=df_ai_models, rows=12, columns=公司, 产品名称, 发布日期, 定位, 模态
- variable=df_revenue, rows=15, columns=公司, 股票代码, 财年, 财年口径, 营收_亿美元
- variable=df_market_cap, rows=3, columns=公司, 股票代码, 市值_十亿美元, 市值_万亿美元, 币种
available_kpis:
- none
available_dashboards:
- none
composition_rules:
- 优先使用 charts 展示趋势或对比
- 优先使用 tables 承载精确证据和明细数值
- 优先使用 kpis 呈现核心数字和摘要卡片
- 优先使用 dashboards 组织总览或收束页

**说明**：用户的查询可能包含两类信息：
1. **与PPT设计相关的要求**：如背景颜色、设计风格、主题偏好等 - 请在设计时**严格遵守**这些要求
2. **与PPT内容相关的主题**：如"分析最近三天英伟达和微软的股价" - 这类内容已经体现在下面的参考资料中

请仔细分析用户查询，提取其中的设计要求（如有），并在大纲的 `metadata.design_style` 和各页设计中体现。

## 参考资料（Markdown文档）

# 外部科技趋势与内部经营决策，15页中文高管PPT，深色商务风格，整合科技公司估值、AI产品竞争格局、零售经营分析、归因预测和情景结论

# 外部科技趋势与内部经营决策

## 第1页：封面
- 主标题：外部科技趋势与内部经营决策
- 副标题：从宏观科技格局到企业经营行动
- 数据来源：本次会话全部研究与经营分析成果
- 报告日期：2026年7月31日
- 声明：市值/营收数据来自联网查询；经营数据为模拟数据（seed=42），仅供方法论参考

## 第2页：目录
1. 外部环境：科技龙头市值与增长格局
2. AI产品竞争：生成式AI产品能力矩阵
3. AI产品定价与模态覆盖
4. 内部经营：36个月销售趋势与KPI
5. 区域与产品结构
6. 渠道效率与营销ROI
7. 利润率根因归因
8. 未来6个月销售预测
9. What-if情景模拟
10. 外部趋势对内部经营的影响
11. 风险矩阵
12. 90天行动路线图
13. 来源附录

## 第3页：外部环境——科技龙头市值格局
引用图表：科技龙头市值对比（artifact_id=5420，横向条形图）

**核心数据（2025-12-31）：**
- 英伟达：45,400亿美元（市值第一，P/S 34.8x）
- 微软：36,125亿美元（市值第二，P/S 12.8x）
- 特斯拉：15,000亿美元（市值第三，P/S 15.4x）

**管理层核心信息：**
英伟达市值超越微软，AI算力需求驱动估值重构。P/S差异（34.8x vs 12.8x）揭示市场对AI基础设施赛道的极高预期溢价，远超传统软件业务。

**数据来源：** public.com / companiesmarketcap.com，查询日期2026-07-31

## 第4页：外部环境——三家科技公司五年营收趋势
引用图表：三家公司五年营收趋势（artifact_id=5422，多系列折线图，v2含CAGR标签）

**核心数据（亿美元）：**
- 微软FY2025：2,817亿（CAGR +13.8%）
- 英伟达FY2025：1,305亿（CAGR +67.5%，AI爆发驱动）
- 特斯拉FY2024：977亿（CAGR +32.6%，近期增速停滞）

**管理层核心信息：**
英伟达五年营收增长7.8倍，CAGR 67.5%，创科技行业增速纪录；其FY2024→FY2025单年翻倍（609→1305亿）验证AI算力需求爆发的可持续性。微软稳健增长，特斯拉增速放缓是结构性信号。

**数据来源：** SEC 10-K / bullfincher.io / stockanalysis.com

## 第5页：AI产品竞争——生成式AI能力矩阵
引用图表：生成式AI产品能力矩阵（artifact_id=5424，气泡图）

**核心洞察（12款产品，2024-05至2025-09）：**
- Google全系列：上下文1000K，价格最低（$0.075~$1.25/1M），性价比最优
- 英伟达GPU支撑：OpenAI GPT-5达400K上下文，$1.25/1M输入，旗舰定价合理化
- Anthropic Claude 4 Opus：输入$15/1M最贵，定位顶级企业级推理

**三维竞争格局：**
- 上下文长度：Google 1000K >> OpenAI 400K >> Anthropic 200K
- 价格竞争：Google最低 → OpenAI中等 → Anthropic最高
- 模态覆盖：Google四模态全覆盖，OpenAI多模态，Anthropic以文本/图像为主

## 第6页：AI产品——厂商定价策略与模态对比
引用Dashboard：生成式AI产品竞争格局（artifact_id=5426）

**定价策略分层：**
- 低价渗透：Google Flash系列（$0.075~$0.15/1M），以规模换市场
- 均衡定价：OpenAI GPT-5（$1.25/1M），旗舰能力合理定价
- 高端溢价：Anthropic Claude 4 Opus（$15/1M），企业级高端市场

**模态覆盖趋势：**
- 2024年主流：文本+图像双模态
- 2025年进化：Google率先实现文本/图像/音频/视频四模态全覆盖
- 视频生成：OpenAI Sora（2025-05）开辟全新赛道

**对企业采购决策的启示：**
长上下文需求优先选Google；高精度推理优先选Anthropic；多模态+性价比优先选OpenAI GPT-5

## 第7页：内部经营——36个月销售趋势与核心KPI
引用图表：月度销售额与利润趋势（artifact_id=5433，v2色盲友好配色）

**核心KPI（基于模拟数据，seed=42）：**
- 36月累计销售额：28,848万元（月均增速+1.5%，年化+19.6%）
- 36月累计利润：18,748万元（毛利率64.97%）
- 近6月利润率：64.86%（⚠️ 低于65%基准，较前期-0.22ppt）
- 营销ROI：8.7倍 / 平均客单价：1,020元 / 转化率：2.16%

**关键运营事件：**
- 2024-12：年末促销峰值7,327万
- 2026-02：春节旺季跃升至11,213万（环比+45%）

**数据说明：** 模拟零售经营数据，月度自然月汇总，毛利口径

## 第8页：内部经营——区域与产品结构
引用图表：区域与产品销售贡献（artifact_id=5435，分组条形图）

**区域销售贡献（36月累计）：**
华东23.7% > 华南21.9% > 华北19.9% > 华中18.0% > 西南16.4%

**产品销售占比：**
平板电脑50% / 智能手表25% / 无线耳机12% / 智能音箱8% / 智能手环5%

**结构风险提示：**
- 平板电脑集中度50%，单一品类依赖过高
- 华南华中近6月利润率同步下滑（-0.59ppt / -0.57ppt），需区域专项应对
- 西南区域贡献最低（16.4%），存在增长空间

## 第9页：内部经营——渠道效率与营销ROI
引用图表：渠道转化率热力图（artifact_id=5436）+ 营销效率气泡图（artifact_id=5437）

**渠道转化率（最近12个月）：**
- 线下门店转化率峰值最高：2.42%（2025-02）
- 线上直营ROI最优：7.99~10.13倍
- 2026年一季度多渠道同步下滑，需排查系统性原因

**营销效率：**
- 全渠道平均ROI：8.7倍（每投1元营销费用产生8.7元销售额）
- 营销费用率：近6月11.5%，较前期+0.1ppt，处于合理区间
- 趋势线显示营销投入与销售额强正相关，规模效应明显

## 第10页：利润率根因归因分析
引用图表：利润率变化归因分析（artifact_id=5439，瀑布图）

**分析周期：** 前6个月（2025-08~2026-01）vs 近6个月（2026-02~2026-07）

**可核验因子分解：**
- 利润率总变化：65.09% → 64.86%，**-0.22ppt**
- 成本率贡献：**-0.22ppt**（34.91%→35.14%，第一主因）
- 营销费用率贡献：**-0.09ppt**（11.42%→11.50%，次因）
- 客单价残差：**+0.09ppt**（1,017→1,024元，正向对冲）

**维度归因：**
- 渠道：线上直营-0.66ppt（最大拖累）→ 线下门店+0.49ppt（最大改善）
- 产品：智能手环-0.49ppt（最大拖累）→ 智能音箱+0.25ppt（最大改善）
- 区域：华南-0.59ppt、华中-0.57ppt（同步下滑）→ 华北+0.32ppt（改善）

**分析方法：** LMDI对数平均分解法

## 第11页：未来6个月销售预测与情景模拟
引用分析：prediction_analysis + whatif_simulation结果

**预测结论（2026-08至2027-01）：**
- 基线预测：销售额延续上升趋势，季节性波动明显
- 2026年末（11-12月）预计出现季节性高峰
- 置信区间随预测时间延长而扩大

**What-if情景模拟：**
三因子假设：营销费用+15% / 单价-3% / 流量+10%
- 情景销售额较基线提升约 **+17.9%**
- 流量+10%为最大正向驱动
- 净效益为正：流量和营销收益覆盖降价损失

**敏感性说明：**
客单价弹性=1.0（零售行业经验值，非历史回归）；弹性在0.7~1.3区间对应情景提升幅度约+16%~+19%

## 第12页：外部趋势对内部经营的影响
**AI技术趋势 × 零售经营的三大交叉影响：**

1. **AI定价下降 → 营销效率提升机会**
   Google Gemini Flash（$0.075/1M）等低成本AI大幅降低智能营销门槛；
   结合现有营销ROI 8.7x的基础，引入AI驱动个性化推荐可将转化率从2.16%提升至2.5%+，
   对应销售额潜在增量约+16%

2. **多模态AI → 产品展示与客服升级**
   OpenAI GPT-5和Google Gemini 2.5 Pro支持文本/图像/音频/视频多模态；
   可用于智能手表/平板电脑等产品的交互式展示和售后服务自动化，
   降低线下门店运营成本，提升渠道效率

3. **长上下文AI（1000K tokens）→ 供应链决策辅助**
   Google Gemini系列1000K超长上下文可处理完整36个月经营数据；
   可用于自动化生成月度经营报告、识别成本率异常（如智能手环成本率上升）、
   辅助采购谈判策略制定

## 第13页：风险矩阵
**高影响 × 高概率：**
- 成本率持续上升（已连续两期高于基准）
- 平板电脑集中度过高（50%单品类依赖）
- 线上直营效率下滑（利润率-0.66ppt）

**高影响 × 中概率：**
- AI竞争加速导致营销工具迭代成本上升
- 英伟达GPU供给紧张导致AI采购成本上升
- 华南华中区域竞争加剧

**中影响 × 高概率：**
- 转化率下降趋势延续（近6月均值2.14%）
- 季节性波动风险（淡旺季落差约40%）
- AI产品迭代速度过快导致采购决策滞后

**应对优先级：** 成本管控 > 渠道优化 > AI能力引入 > 产品结构多元化

## 第14页：90天行动路线图
**第1个月（2026-08）：控成本、稳基础**
- 周1-2：启动供应链成本审查（重点：智能手环/手表原材料）
- 周3-4：线上直营渠道效率诊断（促销折扣策略审查）
- 里程碑：成本率异常根因报告 + 线上直营改善方案

**第2个月（2026-09）：优渠道、增流量**
- 周1-2：营销预算重新分配（线上平台→线上直营，ROI优先）
- 周3：华南华中区域专项会议，引入线下门店最佳实践
- 周4：AI营销工具POC测试（Google Gemini Flash，低成本验证）
- 里程碑：营销ROI提升方案 + AI工具评估报告

**第3个月（2026-10）：调结构、备旺季**
- 周1-2：产品结构调整方案（平板电脑占比50%→45%目标）
- 周3：年末促销方案制定（基于What-if结论：流量+10%/营销+15%/ROI优先）
- 周4：AI能力引入路线图确认（多模态产品展示/客服自动化优先级）
- 里程碑：Q4旺季作战方案 + 90天成果复盘

**成功指标：**
- 成本率：从35.14%压回34.9%以下
- 线上直营利润率：恢复至65%以上
- 营销ROI：从8.7提升至9.5以上
- 转化率：从2.14%回升至2.25%以上

## 第15页：来源附录
**外部科技数据来源：**
1. 英伟达市值：public.com（2026-07-31）
2. 微软市值：companiesmarketcap.com（2026-07-31）
3. 特斯拉市值：public.com（2026-07-31）
4. 英伟达营收FY2021-2025：bullfincher.io
5. 微软营收FY2021-2025：stockanalysis.com + 微软官方IR
6. 特斯拉营收FY2020-2024：SEC 10-K
7. OpenAI产品信息：openai.com/index/
8. Anthropic产品信息：anthropic.com/news/
9. Google产品信息：blog.google/technology/google-deepmind/
10. AI产品定价：macrotrends.net / bullfincher.io / costgoat.com

**内部经营数据说明：**
- 数据类型：模拟零售经营数据，随机种子seed=42，完全可复现
- 数据规模：5区域×5产品×4渠道×36个月=3,600条明细
- 分析方法：LMDI归因 / 时序预测 / 弹性回归What-if
- 免责声明：经营数据为模拟演示数据，不代表任何真实企业经营情况，分析结论仅供方法论参考

**本次会话产物索引：**
- 图表：11个 | Dashboard：4个 | 报告：3个 | Excel：1个 | PPT：2个 | 流程图：1个
- 完整清单详见《研究与经营资产索引.xlsx》

## Referenced Resources
### Artifact: 5420
- id: 5420
- type: artifact

### Artifact: 5422
- id: 5422
- type: artifact

### Artifact: 5424
- id: 5424
- type: artifact

### Artifact: 5433
- id: 5433
- type: artifact

### Artifact: 5435
- id: 5435
- type: artifact

### Artifact: 5439
- id: 5439
- type: artifact

### Artifact: 5444
- id: 5444
- type: artifact

### Artifact: 5445
- id: 5445
- type: artifact

## 展示资产
以下是当前可用于报告、HTML 或 PPT 编排的结构化展示资产。
### 表格
- type=table, variable=df_whatif, title=36个月月度时间序列，含销售额_万/利润_万/客单价/流量/营销费用_万元/转化率，用于w..., rows=36, columns=date, 销售额_万, 利润_万, 客单价, 流量
- type=table, variable=df_attr, title=最近12个月月度时间序列，含利润率（目标）及客单价/成本率/营销费用率/转化率（驱动因子）..., rows=12, columns=date, 销售额, 成本, 利润, 营销费用
- type=table, variable=df_marketing, title=按月份+渠道聚合的营销费用/销售额/利润（万元），含营销ROI和异常标记, rows=144, columns=月份, 渠道, 营销费用_万, 销售额_万, 利润_万
- type=table, variable=df_channel_month, title=按月份+渠道聚合的销量/流量/转化率，用于渠道转化率热力图, rows=144, columns=月份, 渠道, 销量, 流量, 转化率
- type=table, variable=df_region_product, title=按区域+产品聚合的销售额和利润（万元），区域按总销售额从高到低排序, rows=25, columns=区域, 产品, 销售额_万, 利润_万
- type=table, variable=df_monthly, title=36个月零售经营月度汇总，含销售额/利润/毛利率/客单价/转化率/营销费用率等指标, rows=36, columns=月份, 销量, 销售额, 成本, 利润
- type=table, variable=df_retail, title=36个月零售经营模拟数据（2023-08至2026-07），包含月份/区域/产品/渠道/销..., rows=3600, columns=月份, 区域, 产品, 渠道, 销量
- type=table, variable=df_ai_models, title=OpenAI/Anthropic/Google最近12个月主要生成式AI产品，含发布日期、..., rows=12, columns=公司, 产品名称, 发布日期, 定位, 模态
- type=table, variable=df_revenue, title=特斯拉、英伟达、微软最近五个完整财年营收（亿美元），含财年口径和来源链接, rows=15, columns=公司, 股票代码, 财年, 财年口径, 营收_亿美元
- type=table, variable=df_market_cap, title=科技龙头（英伟达、微软、特斯拉）最新交易日市值对比，单位十亿美元，来源联网查询, rows=3, columns=公司, 股票代码, 市值_十亿美元, 市值_万亿美元, 币种
优先将图表资产用于趋势展示，将表格资产用于精确证据，将指标卡资产用于核心数字，将看板资产用于总览编排。

## Structured Presentation Slots
available_charts:
- none
available_tables:
- variable=df_whatif, rows=36, columns=date, 销售额_万, 利润_万, 客单价, 流量
- variable=df_attr, rows=12, columns=date, 销售额, 成本, 利润, 营销费用
- variable=df_marketing, rows=144, columns=月份, 渠道, 营销费用_万, 销售额_万, 利润_万
- variable=df_channel_month, rows=144, columns=月份, 渠道, 销量, 流量, 转化率
- variable=df_region_product, rows=25, columns=区域, 产品, 销售额_万, 利润_万
- variable=df_monthly, rows=36, columns=月份, 销量, 销售额, 成本, 利润
- variable=df_retail, rows=3600, columns=月份, 区域, 产品, 渠道, 销量
- variable=df_ai_models, rows=12, columns=公司, 产品名称, 发布日期, 定位, 模态
- variable=df_revenue, rows=15, columns=公司, 股票代码, 财年, 财年口径, 营收_亿美元
- variable=df_market_cap, rows=3, columns=公司, 股票代码, 市值_十亿美元, 市值_万亿美元, 币种
available_kpis:
- none
available_dashboards:
- none
composition_rules:
- 优先使用 charts 展示趋势或对比
- 优先使用 tables 承载精确证据和明细数值
- 优先使用 kpis 呈现核心数字和摘要卡片
- 优先使用 dashboards 组织总览或收束页

## 语言要求

**非常重要**：生成的PPT内容（标题、副标题、要点等）的语言必须与参考资料的主要语言保持一致。
- 如果参考资料主要是中文，则大纲和所有内容必须使用中文
- 如果参考资料主要是英文，则大纲和所有内容必须使用英文

检测到的参考资料语言：zh

## 创作要求

**重要：你不需要严格按照 Markdown 的结构来组织 PPT。请将 Markdown 视为参考资料，自由发挥创意，设计最佳的演示结构。**

1. **内容提炼**
   - 深度阅读 Markdown 内容，理解核心主题和关键信息
   - 提取最有价值的数据、观点、结论
   - 识别适合可视化展示的内容（数据、对比、流程等）
   - 过滤技术性内容（HTML 代码、复杂表格等）

2. **PPT 结构设计（总-分-总）**
   - **总（开篇）**：
     - **封面页**：根据内容主题设计吸引人的标题（可以比 Markdown 标题更精炼）
     - **目录/概述页**：设计清晰的逻辑框架（不必完全对应 Markdown 章节）
     - **核心观点摘要页**（使用 layout:`big_number` 震撼开场）
   - **分（详细展开）**：根据内容选择 Timeline, Comparison, Chart 等丰富布局
     - 按照演示逻辑组织，而非 Markdown 顺序
     - 每页聚焦一个核心观点或数据洞察
     - 将复杂内容拆分为多页，保持简洁
     - 数据密集内容用可视化方式呈现
   - **总（结尾）**：总结页（提炼核心结论和要点） -> 呼吁行动(`layout: action_plan`) -> 结束页

3. **要点精炼原则**
   - 每个要点控制在 20-30 字以内
   - 突出数字、百分比、关键结论
   - 使用简洁有力的表述
   - 每页最多 3-4 个要点

4. **布局建议：详细的幻灯片信息规范**
    - `content_mode`: `list` (列表), `paragraph` (深度段落), `data` (数据), `mixed` (混合)，.
    - `layout_suggestion`: `cover`, `list`, `card`, `left-right`, `big_number`, `chart`, `table`, `timeline`, `process`, `dashboard`, `comparison`, `swot`, `pyramid`, `action_plan`, `summary`.

   **这是最重要的步骤**：请深入分析 Markdown 的内容逻辑，**不要仅仅生成普通的列表页**。根据内容特征，匹配以下高级布局：

   | Markdown 内容特征 | 必须使用的 Layout Suggestion | 推荐 Content Mode |
   | :--- | :--- | :--- |
   | **涉及多个 KPI 指标、数据概览、核心数字** | `dashboard` (仪表盘) | `mixed` |
   | **涉及年份、日期、阶段、发展历程** | `timeline` (时间轴) | `list` |
   | **涉及步骤、流程、循环、逻辑顺序** | `process` (流程图) | `list` |
   | **涉及优劣势对比、竞品分析、A vs B** | `comparison` (对比分析) | `list` |
   | **涉及SWOT分析、四象限分类** | `swot` (四象限) | `list` |
   | **涉及组织架构、层级关系、优先级** | `pyramid` (金字塔/层级) | `list` |
   | **涉及下一步计划、待办事项** | `action_plan` (行动计划) | `list` |
   | **涉及总结、核心结论、摘要** | `summary` (摘要总结) | `mixed` |
   | **包含具体数据表格或统计趋势** | `chart` (图表) 或 `table` (表格) | `data` |
   | **包含极具冲击力的单一数据或金句** | `big_number` (大数字/金句) | `data` |
   | **深度解析某个概念或故事** | `left-right` (左图右文) | `paragraph` |
   | **普通的清单或要点** | `list` (列表) | `list` |

   - **多样性要求**: **禁止**连续 3 页使用相同的 layout。

5. **幻灯片数量**
   - 页数**不得超过** 8 页（硬性上限）
   - **用户 query 优先**：若用户明确要求生成 N 页，且 N ≤ 8，则按 N 页生成；若用户要求的页数 > 8，则按 8 页生成
   - 若用户未指定页数，请根据内容丰富度自主决定，但不超过 8 页
   - 保持每页信息密度适中

6. **元数据生成规则**
   - **author（作者）字段**：
     - 仅当用户查询中明确提到身份时才填写（如"我是张三"、"公司：XX科技"）
     - 否则必须留空字符串 `""`
     - 不要根据内容自行推断或编造作者信息
   - **date（日期）字段**：
     - 仅当用户查询中明确提到当前日期/ppt演讲时间时才填写（如"2026年1月报告"、"Q1季度"）
     - 否则必须留空字符串 `""`
     - 不要自动填写当前日期或根据内容推断日期
   - 包含 `design_style` (简短描述即可，如 "Clean Business", "Dark Tech")。
   - 包含 `global_style_spec`：与 `design_style` 一致的**全局调色板**（完整 10 个键，见下方 JSON 结构）。
     - 所有色值须风格统一、语义正确：前景文字与背景高对比，强调色协调；
     - 未明确指定深色风格时，`background` 默认浅色/白底；
     - 该调色板会**统一应用于所有页面**，请一次性给全 10 个键，不要省略。

请以JSON格式返回大纲，格式如下:

```json
{
    "title": "精炼的演示标题（可以不同于 Markdown 标题）",
    "subtitle": "副标题或核心价值主张",
    "metadata": {
        "author": "**仅当用户查询中明确提到身份时才填写**，否则留空字符串",
        "date": "**仅当用户查询中明确提到当前日期/ppt演讲时间时才填写**，否则留空字符串",
        "keywords": ["关键词1", "关键词2", "关键词3"],
        "design_style": "根据主题选择的设计风格。**重要：如果用户查询中没有明确指定颜色或风格偏好，默认使用浅色/白色背景风格**（如：简约白色背景+浅灰点缀+清新商务风、纯白背景+淡蓝强调色+极简专业风）",
        "source_summary": "用1-2句话总结 Markdown 核心内容，供后续幻灯片生成参考",
        "global_style_spec": {
            "background": "全局背景色 #RRGGBB（未明确指定深色风格时默认浅色/白底）",
            "accent_primary": "主强调色 #RRGGBB",
            "accent_secondary": "次强调色 #RRGGBB",
            "text_primary": "主文字色 #RRGGBB（须与 background 形成高对比）",
            "text_secondary": "次文字色 #RRGGBB",
            "card_bg": "卡片背景色 #RRGGBB",
            "card_border": "卡片描边色 #RRGGBB",
            "decoration": "装饰元素色 rgba(...)",
            "top_bar": "顶部装饰条色 #RRGGBB",
            "overlay_bg": "蒙层背景色 rgba(...)"
        }
    },
    "slides": [
        {
            "id": 1,
            "title": "封面标题",
            "type": "title",
            "key_points": [],
            "layout_suggestion": "cover",
            "content_mode": "paragraph",
            "content_note": "从 Markdown 提取的相关信息摘要"
        },
        {
            "id": 2,
            "title": "核心洞察或目录",
            "type": "toc",
            "key_points": ["逻辑点1", "逻辑点2", "逻辑点3"],
            "layout_suggestion": "list",
            "content_mode": "list",
            "content_note": "对应 Markdown 中的哪些内容"
        },
        {
            "id": 3,
            "title": "具体内容页标题",
            "type": "content",
            "key_points": [
                "要点1：简洁有力（20-30字）",
                "要点2：包含数据更佳",
                "要点3：突出结论"
            ],
            "layout_suggestion": "card",
            "content_mode": "mixed",
            "content_note": "这页内容基于 Markdown 的 XX 部分，重点展示 YY 数据"
        }
    ]
}
```

## 内容约束

- 每页幻灯片 2-4 个要点
- 每个要点 20-30 字以内
- 目录页最多 5-6 项
- 数据页重点突出关键数字
- 避免技术术语堆砌

## 特殊内容处理

1. **表格数据**：提取关键数值，设计为卡片式对比
2. **HTML/代码块**：完全忽略，只提取描述性文字
3. **长段落**：提炼核心观点，用要点呈现
4. **数据分析**：数字 + 结论的形式

**重要提示**：
- 在每个 slide 的 `content_note` 字段中，简要说明这页内容来源于 Markdown 的哪部分，以便后续生成时参考
- `metadata.source_summary` 也要填写，方便整体设计时保持风格一致

**只返回JSON，不要任何额外说明。**
