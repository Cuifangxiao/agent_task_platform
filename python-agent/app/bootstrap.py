"""资源初始化: 只准备资源(DB 连接、模型配置、prompt 等), 不碰网络监听。

目前没有外部资源, 留作占位; 后续接 DB / LLM provider 配置加载时写在这里。
"""

import logging

from config import Config

logger = logging.getLogger(__name__)


class Bootstrapper:
    def __init__(self, config: Config) -> None:
        self._config = config

    def bootstrap(self) -> None:
        logger.info("bootstrap done (no external resources yet)")
