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
│   │   ├── domain/          #   contracts、Pydantic schemas、错误与事实规则
│   │   ├── pipeline/        #   根图、节点、state、events 与 instrumentation
│   │   ├── runtime/         #   LLM 调用、模型注册与请求级指标
│   │   ├── workspace/       #   来源解析与 task-local 路径
│   │   ├── design_system/   #   catalog、主题、recipes、base CSS 与本地图标
│   │   ├── html/            #   sanitizer、assembler、Chromium DOM QA 与 fallback
│   │   ├── export/          #   image-only PPTX 导出
│   │   └── prompts/         #   六个正式节点提示词
│   ├── agents/              #   agent 编排: 组合 llm/rag/memory/tools
│   ├── llm/                 #   模型接入层: 各 provider 客户端(统一 BaseLLMClient 接口)
│   ├── rag/  memory/  tools/
└── utils/
    └── logger.py            # dictConfig 日志初始化
```

测试统一放在仓库根目录的 `tests/runtime/`。

Deck 内部依赖保持单向：`service -> pipeline -> {html, export, runtime, workspace, design_system} -> domain`。各子包的 `__init__.py` 不做 eager re-export，避免普通包导入提前加载 LangGraph、Playwright 或形成初始化循环。

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

## 测试

```bash
.venv/bin/python -m pytest ../../tests/runtime/    # 或仓库根目录 make test-runtime
```

## PPT 生成运行依赖

PPT 使用独立的 gRPC 服务端流。Go 发送一次 `GeneratePpt` 请求，Runtime 为每个
run 创建独立的 `core/deck/pipeline/graph.py::PptGraph`，通过 custom stream 持续返回 `AgentEvent`。
当前 HTML-first 根图直接暴露 18 个节点：

```text
normalize_request -> plan_deck -> select_template -> create_design_spec
-> prepare_slide -> generate_slide_content -> generate_slide_html
-> sanitize_and_assemble_html -> render_html_and_measure_dom -> validate_dom
   -> [repair_slide_html -> sanitize...] | [build_fallback_html -> sanitize...] | capture_png
-> persist_slide_artifacts -> emit_slide_preview -> advance_slide
   -> [more: prepare_slide] | [done: assemble_image_pptx -> finalize_deck]
```

LLM 边界使用 `DeckBrief`、`DeckPlan`、`DesignSpec`、`SlideContentSpec` 和
`HtmlSlideSpec` 等严格 Pydantic schema；LangGraph state 只保存可序列化字典、诊断和相对产物信息。
`completed_slides` 是以 `slide_id` 为键的幂等结果映射。每个请求从头执行，当前没有
LangGraph checkpointer、人工 review 或 pause/resume。

设计系统位于 `core/deck/design_system/`，提供 4 套完整主题（`paper_editorial`、
`executive_signal`、`midnight_product`、`alpine_research`）、10 个 layout recipe 和 24 个本地 Lucide 图标。
模型生成的 HTML/CSS 会经过 parser-backed allowlist 清洗，再装入含 CSP、全局 DesignSpec token 和
trusted base CSS 的文档壳。Chromium 对每页执行越界、溢出、重叠、最小字号、节点预算和字体可用性检查；
内容错误最多修复两次，然后进入确定性 recipe fallback。数字、人物、实质性引语、客户案例和已实现结果
还会经过来源守卫；有效但无关的 `source_ref` 不能授权新事实。

渲染画布为 `1600×900` CSS px，`deviceScaleFactor=2`，每页输出 `3200×1800` PNG。生产预览事件
只发布 `PAGE_PREVIEW` PNG，使用稳定的 `sXX-preview` resource ID；已清洗 HTML 会持久化供审计，
但不通过页预览事件暴露。每条页预览事件还携带以 `artifact_hash` 为值的必填 `revision`，
用于客户端精确识别已持久化页面版本。最终 PPTX 每页只含一张全出血 PNG，因此视觉稳定但页内元素不可编辑。

模型角色仍固定为 `brief`、`outline`、`style`、`writer`、`designer`，配置由 Go 请求注入。
上传文件当前只用于文本/provenance；上传图片不会进入生成节点，当前没有多模态视觉分析。

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
