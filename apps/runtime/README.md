# Agent Runtime

Go 后端通过 gRPC 调用的 Python agent 运行时,负责模型调用、聊天、RAG 等 AI 能力。

## 目录结构

```
apps/runtime/
├── main.py                  # 进程入口: --conf 指定配置 -> load_dotenv -> init_config -> 日志 -> Application
├── config.py                # Pydantic 配置模型 + init_config/get_config 全局单例
├── config.example.yaml      # 配置模板(本地 cp 为 config.yaml, 已 gitignore)
├── .env.example             # 密钥类环境变量说明(复制为 .env)
├── requirements.txt         # 运行依赖
├── requirements-dev.txt     # 开发依赖(grpcio-tools / pytest)
├── Dockerfile
├── api/                     # protoc 生成的 gRPC 桩代码(根目录 make proto 生成, 勿手改)
├── app/                     # 生命周期层: 只管启动顺序与关停
│   ├── application.py       #   信号处理 + run/shutdown 编排
│   ├── bootstrap.py         #   资源初始化(DB/模型配置等, 不碰网络)
│   └── transport_runtime.py #   协议层启停(失败回滚)
├── server/                  # 协议接入层
│   └── grpc.py              #   Servicer 薄壳(错误边界) + GrpcServer 生命周期
├── handler/                 # 编排层: proto <-> core contract 的转换
│   ├── container.py         #   AgentHandlers + build_agent_handlers(手写 DI)
│   ├── provider_handler.py  #   Provider/Text proto <-> core
│   └── ppt_handler.py       #   PPT AgentEvent proto <-> core
├── core/                    # 领域内核: 零 proto/grpc 依赖, 可独立单测
│   ├── provider/            #   contract.py(Input/Output DTO) + service.py(业务)
│   ├── deck/                #   PPT HTML-first 领域模块
│   │   ├── service.py       #   应用入口与终态事件
│   │   ├── agents/          #   三阶段 agent（各自持有 graph.py）
│   │   │   ├── preprocessor/   # 一次性整套大纲、页面规划与全局色板
│   │   │   ├── processor/      # 逐页视觉规划、HTML、DOM QA、repair、PNG
│   │   │   └── postprocessor/  # manifest、质量报告、PPTX 导出校验
│   │   ├── domain/          #   contracts、Pydantic schemas、错误与事实规则
│   │   ├── pipeline/        #   三阶段根图、共享 state、events 与 instrumentation
│   │   ├── runtime/         #   LLM 调用、模型注册与请求级指标
│   │   ├── workspace/       #   来源解析与 task-local 路径
│   │   ├── design_system/   #   catalog、主题、recipes、base CSS 与本地图标
│   │   ├── html/            #   sanitizer、assembler、Chromium DOM QA 与 fallback
│   │   ├── export/          #   image-only PPTX 导出
│   │   └── prompts/         #   outline、逐页视觉规划、HTML 与 repair 提示词
└── utils/
    └── logger.py            # dictConfig 日志初始化
```

测试统一放在仓库根目录的 `tests/runtime/`。

Deck 内部依赖保持单向：`service -> pipeline/graph -> agents/{preprocessor, processor, postprocessor}`。Preprocessor 不依赖 `design_system/html/export`；processor 使用 `design_system/html` 生成并修复页面；postprocessor 使用 `export` 汇总 PPTX。

## 分层约定

请求链路: `Go(gRPC client) -> server(servicer 薄壳) -> handler(转换/编排) -> core(领域逻辑)`

- **server** 只做委托 + 错误边界(try/except -> StatusCode + 日志), 不写业务;
- **handler** 负责 proto <-> core contract DTO 的双向转换, 不处理异常;
- **core** 不 import grpc/proto, 方便单测;
- 依赖方向单向: `server -> handler -> core`, 反向 import 视为违规。

新增一个 RPC 的步骤：修改 `contracts/proto/` 下的协议，执行根目录 `make proto`，再依次补充 `server/grpc.py`、handler 转换和 core 领域逻辑。

## 本地运行

```bash
cd apps/runtime
python -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-dev.txt
cp config.example.yaml config.yaml   # 按需改端口等
cp .env.example .env                 # 按需填密钥
.venv/bin/python main.py --conf config.yaml   # 默认仅监听 127.0.0.1:50051
```

或在仓库根目录执行 `make run-runtime`。

内置 Provider 目录包含 OpenAI、DeepSeek、Qwen、Google Gemini 和 Kimi。Gemini 与 Kimi 均通过各自官方的 OpenAI 兼容端点调用，用户在 Web 的模型管理页填写对应平台 API Key 即可，无需在 Runtime `.env` 中配置密钥。

## 测试

```bash
.venv/bin/python -m pytest ../../tests/runtime/    # 或仓库根目录 make test-runtime
```

## PPT 生成运行依赖

PPT 使用独立的 gRPC 服务端流。Go 发送一次 `GeneratePpt` 请求，Runtime 为每个
run 创建独立的 `core/deck/pipeline/graph.py::PptGraph`，通过 custom stream 持续返回 `AgentEvent`。
核心 LLM 链路只有三个模型角色：`outline`、`style`、`designer`。`outline` 每个任务只调用一次，
产出整套 `DeckOutlineSpec` 和唯一的 `GlobalStyleSpec`；随后 Runtime 使用 LangGraph `Send` 做页面级
map-reduce，每页严格先执行 `style` 视觉规划，再执行 `designer` HTML 生成：

```text
START
  -> generate_deck_outline                         # outline，一次
  -> fan_out_slides
  -> Send(generate_slide, slide_1 .. slide_n)
     -> prepare_slide
     -> generate_slide_design                      # style，每页一次
     -> generate_slide_html                        # designer，每页一次
     -> sanitize_and_assemble_html -> render_html_and_measure_dom -> validate_dom
        -> valid: capture_png
        -> repair: repair_slide_html -> sanitize... # designer，最多两轮
        -> fallback: build_fallback_html -> sanitize...
     -> persist_slide_artifacts -> emit_slide_preview
  -> aggregate_slides
  -> assemble_image_pptx -> finalize_deck
  -> END
```

阶段一直接接收用户要求、标准化来源、资产摘要和页数约束，输出 `DeckOutlineSpec`。它包含连续的
`SlideOutlineSpec` 页面清单、全局 metadata 和完整 `GlobalStyleSpec`；页数保持 `1..30`，页面 ID 必须从
`s01` 连续排列。Runtime 再确定性派生兼容现有 renderer 的 `DeckPlan`、`DesignSpec`、recipe variant 和
`SlideContentSpec`，这一步不产生额外模型调用。

每个页面分支中的 `SlideDesignPlan` 必须继承同一个 `GlobalStyleSpec`，并记录简短、可审计的
`design_rationale`，而不是隐藏思维链。`HtmlSlideSpec` 接收对应页的 outline、完整 design plan、相关来源切片
和本地资产约束。LangGraph state 只保存可序列化字典、诊断和相对产物信息；`completed_slides`、
`layout_history` 和 `validation_issues` 通过 reducer 在 fan-in 时确定性归并，最终按 `slide_id`/页索引恢复顺序。
每个请求从头执行，当前没有 LangGraph checkpointer、人工 review 或 pause/resume。

默认页面并发数为 6、Chromium session pool 并发数为 3；可分别通过
`DECK_SLIDE_CONCURRENCY` 和 `DECK_BROWSER_CONCURRENCY` 调整。预览按完成顺序发送，调用方按
`page_index`/`page_id` 定位页面。

设计系统位于 `core/deck/design_system/`，提供 4 套完整主题（`paper_editorial`、
`executive_signal`、`midnight_product`、`alpine_research`）、10 个 layout recipe 和 24 个本地 Lucide 图标。
catalog 继续提供经过浏览器验证的几何、字体、间距、shape 和图标合同；页面颜色只来自阶段一的
`GlobalStyleSpec`，确定性映射为 renderer `DesignSpec.palette`，逐页视觉规划不能发明冲突色板。
模型生成的 HTML/CSS 会经过 parser-backed allowlist 清洗，再装入含 CSP、全局 DesignSpec token 和
trusted base CSS 的文档壳。Chromium 对每页执行越界、溢出、重叠、最小字号、节点预算和字体可用性检查；
内容错误最多修复两次，然后进入确定性 recipe fallback。确定性内容守卫要求 HTML 保留批准的标题、
至少一个正文内容块和 key metric 原值；prompt 同时要求模型只使用本页 outline 与相关来源切片。
当前实现不会对全部自由文本事实逐项执行来源蕴含校验。

渲染画布为 `1600×900` CSS px，`deviceScaleFactor=2`，每页输出 `3200×1800` PNG。生产预览事件
只发布 `PAGE_PREVIEW` PNG，使用稳定的 `sXX-preview` resource ID；已清洗 HTML 会持久化供审计，
但不通过页预览事件暴露。每条页预览事件还携带以 `artifact_hash` 为值的必填 `revision`，
用于客户端精确识别已持久化页面版本。最终 PPTX 每页只含一张全出血 PNG，因此视觉稳定但页内元素不可编辑。

模型角色固定为 `outline`、`style`、`designer`，配置由 Go 请求注入。MySQL migration
`014_refactor_ppt_agent_model_nodes.sql` 更新 Agent 节点定义，保留这三个既有绑定并清理其他节点绑定。
当前 state schema 为 `deck-state.v6`，prompt 版本为 `html-deck.v8`；
renderer 协议未改变，仍为 `chromium-html.v2`。
文本附件以正文/provenance 进入 outline 和逐页相关来源上下文。图片会持久化到任务本地
`sources/assets/`，其有界元数据可参与 outline 和逐页资源筛选；当前仍不做多模态视觉分析，
也不允许模型把上传图片直接编排进 HTML。

安装 Python 依赖后，还需要 Playwright Chromium：

```bash
.venv/bin/python -m playwright install chromium
```

Linux/CI 可使用：

```bash
.venv/bin/python -m playwright install --with-deps chromium
sudo apt-get install -y fontconfig fonts-noto-cjk fonts-inter
```

四套主题只使用 `Noto Sans CJK SC`（中文与混排）和 `Inter`（拉丁文）。运行
`fc-list : family | rg -i 'Noto Sans CJK SC|Inter'` 检查目标环境；字体缺失是硬性渲染错误。
生产 Runtime 应使用非 root 用户，并显式开启 Chromium sandbox。

生成 10 页模板画廊和三套 6 页 HTML/PNG/PPTX 样例：

```bash
PYTHONPATH=. .venv/bin/python scripts/generate_html_deck_samples.py
```

输出位于 `artifacts/deck-samples/`；macOS 可通过 `--chinese-font "PingFang SC" --latin-font "Arial"`
使用本机字体进行开发预览。生产验收仍应使用 `Noto Sans CJK SC` / `Inter`。

生成产物写入 `DECK_ARTIFACTS_DIR/{task_id}/`，默认为 `apps/runtime/artifacts/decks`。若 Go 与 Runtime
位于不同容器，`python_agent.artifacts_dir` 和 `DECK_ARTIFACTS_DIR` 必须挂载到同一物理目录。
Runtime 事件只返回相对路径；绝对路径、URL、鉴权和下载边界由 Go 处理。Go 使用 WebSocket
`/api/v1/ppt/ws` 发布事件，并将 run 快照和完整事件日志持久化到 MySQL 的 `ppt_runs` / `ppt_run_events`；
这条路径不使用 SSE，TaskHub 仅保留实时 fan-out 和取消控制。

完整架构、artifact 布局、字体合约、测试命令和当前限制见
`docs/PPT_GENERATION_PIPELINE.md`。

## proto 代码生成

proto 真源在仓库根 `contracts/proto/v1/provider/provider.proto` 和
`contracts/proto/ppt/ppt_agent.proto`，在仓库根目录执行：

```bash
make proto        # 同时生成 Go(contracts/gen/go/) 和 Python(apps/runtime/api/) 代码
```

生成物提交进 git, 构建/部署不依赖本机 protoc。
