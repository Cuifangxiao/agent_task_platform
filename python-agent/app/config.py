"""应用配置。

所有配置都从环境变量读取,带默认值,方便本地直接跑、也方便 Docker 里注入。
参考 .env.example。
"""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class GRPCSettings:
    host: str
    port: int
    max_workers: int
    # 优雅停机时,给正在处理的请求留的时间(秒)
    shutdown_grace: float

    @property
    def listen_addr(self) -> str:
        return f"{self.host}:{self.port}"


@dataclass(frozen=True)
class Settings:
    env: str
    log_level: str
    grpc: GRPCSettings


def load_settings() -> Settings:
    return Settings(
        env=os.getenv("APP_ENV", "dev"),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        grpc=GRPCSettings(
            host=os.getenv("GRPC_HOST", "0.0.0.0"),
            port=int(os.getenv("GRPC_PORT", "50051")),
            max_workers=int(os.getenv("GRPC_MAX_WORKERS", "10")),
            shutdown_grace=float(os.getenv("GRPC_SHUTDOWN_GRACE", "5")),
        ),
    )
