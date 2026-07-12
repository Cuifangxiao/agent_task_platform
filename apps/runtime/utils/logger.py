"""日志初始化: dictConfig, 按 logger 名分级。

各模块统一 `logger = logging.getLogger(__name__)`, 不要自己加 handler。
"""

import logging
import logging.config
from pathlib import Path

from config import LogConfig

_FORMAT = "%(asctime)s %(levelname)s [%(process)d:%(threadName)s] [%(name)s] %(message)s"


def configure_logger(cfg: LogConfig) -> None:
    level = cfg.level.upper()
    target = cfg.target.strip().lower()
    if target not in {"console", "file"}:
        raise ValueError(f"unsupported log target: {cfg.target}")

    handlers: dict[str, dict[str, object]] = {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "default",
            "stream": "ext://sys.stdout",
        },
    }
    root_handlers = ["console"]
    log_path: Path | None = None
    if target == "file":
        log_dir = Path(cfg.file_dir).expanduser().resolve()
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "runtime.log"
        handlers["file"] = {
            "class": "logging.handlers.RotatingFileHandler",
            "formatter": "default",
            "filename": str(log_path),
            "encoding": "utf-8",
            "maxBytes": 20 * 1024 * 1024,
            "backupCount": 5,
        }
        root_handlers.append("file")

    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": {"format": _FORMAT},
            },
            "handlers": handlers,
            "root": {"level": level, "handlers": root_handlers},
            "loggers": {
                # 需要单独调级别的 logger 在这里加, 例如:
                # "server.grpc": {"level": "DEBUG"},
                # 压掉三方库的噪音:
                "grpc": {"level": "WARNING"},
            },
        }
    )
    logging.getLogger(__name__).info(
        "logger configured: level=%s target=%s file=%s",
        level,
        target,
        log_path or "",
    )
