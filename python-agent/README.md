# python-agent

Go 后端通过 gRPC 调用的 Python agent 运行时,负责模型调用、聊天、RAG 等 AI 能力。

## 目录结构

```
python-agent/
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
├── utils/
│   └── logger.py            # dictConfig 日志初始化
└── tests/
```

## 分层约定

请求链路: `Go(gRPC client) -> server(servicer 薄壳) -> handler(转换/编排) -> core(领域逻辑)`

- **server** 只做委托 + 错误边界(try/except -> StatusCode + 日志), 不写业务;
- **handler** 负责 proto <-> core contract DTO 的双向转换, 不处理异常;
- **core** 不 import grpc/proto, 方便单测;
- 依赖方向单向: `server -> handler -> core`, 反向 import 视为违规。

新增一个 RPC 的步骤: 改 `api/proto/` 下的 proto -> 根目录 `make proto` -> server/grpc.py 加薄壳方法 -> handler 加转换方法 -> core 加领域逻辑。

## 本地运行

```bash
cd python-agent
python -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-dev.txt
cp config.example.yaml config.yaml   # 按需改端口等
cp .env.example .env                 # 按需填密钥
.venv/bin/python main.py --conf config.yaml   # 默认监听 0.0.0.0:50051
```

或在仓库根目录 `make run-python`。

## 测试

```bash
.venv/bin/python -m pytest tests/    # 或仓库根目录 make test-python
```

## PPT 生成运行依赖

Deck 生成链路使用 HTML 中间表示: 每页会存档到 `DECK_ARTIFACTS_DIR/{request_id}/slides/{n}.html`
和 `{n}.png`, 最终导出 `{request_id}/deck.pptx`。未配置 `DECK_ARTIFACTS_DIR` 时默认写入
`python-agent/artifacts/decks`。

部署时安装 Python 依赖后，还需要安装 Playwright Chromium:

```bash
.venv/bin/python -m playwright install chromium
```

如果运行环境缺少 Chromium，服务会降级为 Pillow 预览与整页截图导出，并在事件流中发 warning。
生产环境建议随服务镜像放置 `core/deck/design_system/fonts/` 下的 Noto Sans SC 字体资产。

## proto 代码生成

proto 唯一真源在仓库根 `api/proto/v1/provider/provider.proto`, 在仓库根目录执行:

```bash
make proto        # 同时生成 Go(api/go/) 和 Python(python-agent/api/) 代码
```

生成物提交进 git, 构建/部署不依赖本机 protoc。
