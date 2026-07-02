# python-agent

Go 后端通过 gRPC 调用的 Python agent 运行时,负责模型调用、聊天、RAG 等 AI 能力。

## 目录结构

```
python-agent/
├── main.py                  # 入口:加载配置、初始化日志、启动 gRPC server
├── requirements.txt
├── .env.example             # 环境变量说明(复制为 .env 或直接导出)
├── Dockerfile
├── gen/                     # protoc 生成的代码(scripts/generate-proto.ps1 生成,勿手改)
│   └── api/provider/v1/
├── app/
│   ├── config.py            # 环境变量配置
│   ├── logger.py            # 日志初始化
│   ├── server.py            # gRPC server 组装、依赖注入、优雅停机
│   ├── grpc_api/            # gRPC 接入层:servicer,只做 proto <-> 内部类型转换
│   │   └── provider_runtime.py
│   ├── services/            # 业务服务层:纯 Python 逻辑,不依赖 gRPC
│   │   └── provider_service.py
│   ├── agents/              # agent 编排:组合 llm/rag/memory/tools
│   │   ├── base.py          # BaseAgent / AgentContext
│   │   └── chat_agent.py    # 基础聊天 agent
│   ├── llm/                 # 模型接入层:各 provider 客户端(统一 BaseLLMClient 接口)
│   ├── rag/                 # RAG:loader/splitter/embedding/vector_store/retriever
│   ├── memory/              # 会话记忆
│   └── tools/               # agent 工具
└── tests/
```

## 分层约定

请求链路:`Go(gRPC client) -> grpc_api(servicer) -> services(业务) -> agents/llm/rag(能力)`

- **grpc_api** 只做消息接收和转换,不写业务;
- **services** 不 import grpc,方便单测;
- 新增一个 RPC 的步骤:改 proto -> 重新生成代码 -> grpc_api 加 servicer 方法 -> services 加业务方法。

## 本地运行

```bash
cd python-agent
pip install -r requirements.txt
python main.py    # 默认监听 0.0.0.0:50051
```

配置项见 `.env.example`,均可通过环境变量覆盖。

## 测试

```bash
pip install pytest
pytest tests/
```
