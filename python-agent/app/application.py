"""应用生命周期编排: 信号处理 + run/shutdown。

启动顺序: bootstrap(资源) -> build_agent_handlers(依赖组装) -> transport(协议启停)。
"""

import logging
import signal
import threading

from app.bootstrap import Bootstrapper
from app.transport_runtime import TransportRuntime
from config import Config
from handler.container import build_agent_handlers

logger = logging.getLogger(__name__)


class Application:
    def __init__(self, config: Config) -> None:
        self._config = config
        self._transport: TransportRuntime | None = None
        self._shutdown_started = threading.Event()

    def run(self) -> None:
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

        Bootstrapper(self._config).bootstrap()
        handlers = build_agent_handlers()

        self._transport = TransportRuntime(self._config, handlers)
        self._transport.start()
        logger.info(
            "application started. name:%s env:%s",
            self._config.name,
            self._config.environment,
        )
        self._transport.wait_for_termination()

    def shutdown(self) -> None:
        # 幂等: 信号可能触发多次
        if self._shutdown_started.is_set():
            return
        self._shutdown_started.set()
        logger.info("application shutting down")
        if self._transport is not None:
            self._transport.stop()

    def _handle_signal(self, signum, _frame) -> None:
        logger.info("received signal %s", signum)
        self.shutdown()
