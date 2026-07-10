"""协议层生命周期: 启停所有对外监听的 server。

以后加 HTTP 运维端口 / 服务注册等, 都挂在这里, 保持"任一步失败回滚已启动组件"。
"""

import logging

from config import Config
from handler.container import AgentHandlers
from server.grpc import GrpcServer

logger = logging.getLogger(__name__)


class TransportRuntime:
    def __init__(self, config: Config, handlers: AgentHandlers) -> None:
        self._grpc_server = GrpcServer(config.server.grpc, handlers)
        self._started = False

    def start(self) -> None:
        try:
            self._grpc_server.start()
            self._started = True
        except Exception:
            logger.error("transport start failed, rolling back")
            self.stop()
            raise

    def stop(self) -> None:
        if self._started:
            self._grpc_server.stop()
            self._started = False

    def wait_for_termination(self) -> None:
        self._grpc_server.wait_for_termination()
