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
│   └── provider_handler.py
├── core/                    # 领域内核: 零 proto/grpc 依赖, 可独立单测
│   ├── provider/            #   contract.py(Input/Output DTO) + service.py(业务)
│   ├── agents/              #   agent 编排: 组合 llm/rag/memory/tools
│   ├── llm/                 #   模型接入层: 各 provider 客户端(统一 BaseLLMClient 接口)
│   ├── rag/  memory/  tools/
└── utils/
    └── logger.py            # dictConfig 日志初始化
```

测试统一放在仓库根目录的 `tests/runtime/`。

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

Deck 生成链路以 SVG 为作者源：每页权威文件写入
`DECK_ARTIFACTS_DIR/{request_id}/svg_output/page-{n}.svg`，浏览器产物写入 `previews/`，
质量报告写入 `reports/`，最终原生 PPTX 写入 `exports/deck.pptx`。导出时会把本次
`svg_output/` 冻结到 `backup/<timestamp>/svg_output/`。未配置 `DECK_ARTIFACTS_DIR` 时默认写入
`apps/runtime/artifacts/decks`。

部署时安装 Python 依赖后，还需要安装 Playwright Chromium:

```bash
.venv/bin/python -m playwright install chromium
```

如果运行环境缺少 Chromium，服务会降级为 Pillow 预览与整页截图导出，并在事件流中发 warning。
生产环境建议随服务镜像放置 `core/deck/design_system/fonts/` 下的 Noto Sans SC 字体资产。

Deck workflow 使用 `core/deck/checkpoint.py::PersistentSQLiteSaver` 持久化 LangGraph checkpoint，默认数据库位于 artifacts 目录下的 `deck-checkpoints.sqlite3`。Go 层负责持久化 Job/Event 并独立消费 gRPC；Python 流中断不会再被 HTTP 请求 context 直接取消。

统一事件协议版本为 `event_version=1`，stage 包含 `brief`、`outline`、`outline_review`、`style`、`style_review`、`writer`、`designer`、`validate`、`review_vlm`、`repair`、`coherence`、`export`。页面按顺序端到端生成；每页 Content Editor 与 SVG Artist 共用最新锁和前页视觉签名。Designer 和 Coherence 都会调用各自绑定模型。VLM 模型必须声明图片输入能力，否则产生明确的 `vlm_image_unsupported` must-fix。

内容 normalization 不再裁切标题、正文、bullet 或表格。原始与最终 spec 分别保存为 `sources/spec.raw.json`、`sources/spec.final.json`；修复预算耗尽仍有 must-fix 时 artifact 标记 `needs_review`。整页图片降级会写入 warnings/degraded，前端会提示文字不可编辑。

## proto 代码生成

proto 唯一真源在仓库根 `contracts/proto/v1/provider/provider.proto`，在仓库根目录执行：

```bash
make proto        # 同时生成 Go(contracts/gen/go/) 和 Python(apps/runtime/api/) 代码
```

生成物提交进 git, 构建/部署不依赖本机 protoc。
