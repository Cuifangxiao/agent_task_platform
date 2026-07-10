"""日志初始化: dictConfig, 按 logger 名分级。

各模块统一 `logger = logging.getLogger(__name__)`, 不要自己加 handler。
"""

import logging.config

from config import LogConfig

_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"


def configure_logger(cfg: LogConfig) -> None:
    level = cfg.level.upper()
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": {"format": _FORMAT},
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "default",
                    "stream": "ext://sys.stdout",
                },
            },
            "root": {"level": level, "handlers": ["console"]},
            "loggers": {
                # 需要单独调级别的 logger 在这里加, 例如:
                # "server.grpc": {"level": "DEBUG"},
                # 压掉三方库的噪音:
                "grpc": {"level": "WARNING"},
            },
        }
    )
