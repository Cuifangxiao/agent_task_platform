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
│   ├── deck/                #   PPT contract、线性 pipeline、本地渲染与导出
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

PPT 使用独立的 gRPC 服务端流：Go 发送一次 `GeneratePpt` 请求，Runtime 按
`brief -> outline -> style -> design_lock -> write_slides -> export` 顺序执行本地生成，并持续返回
`AgentEvent`。每个请求从头执行单向流水线，期间不暂停等待人工输入；运行状态只通过事件流返回。

模型节点固定为 `brief`、`outline`、`style`、`writer`、`designer`。模型配置来自 Go 请求，
Runtime 不读取业务数据库。生成过程中产生的 SVG、HTML、预览和最终 PPTX 都写入
`DECK_ARTIFACTS_DIR/{task_id}/`；未配置时默认写入 `apps/runtime/artifacts/decks`。

部署时安装 Python 依赖后，还需要安装 Playwright Chromium:

```bash
.venv/bin/python -m playwright install chromium
```

若 Go 与 Runtime 位于不同容器，`python_agent.artifacts_dir` 和 `DECK_ARTIFACTS_DIR`
必须挂载到同一个物理目录。Runtime 在事件中只返回任务目录内的相对路径；绝对路径、HTTP URL、
鉴权和下载边界都由 Go 处理。完整流程见仓库根目录的 `docs/PPT_GENERATION_PIPELINE.md`。

## proto 代码生成

proto 真源在仓库根 `contracts/proto/v1/provider/provider.proto` 和
`contracts/proto/ppt/ppt_agent.proto`，在仓库根目录执行：

```bash
make proto        # 同时生成 Go(contracts/gen/go/) 和 Python(apps/runtime/api/) 代码
```

生成物提交进 git, 构建/部署不依赖本机 protoc。
