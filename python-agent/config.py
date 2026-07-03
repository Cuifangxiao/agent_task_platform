"""应用配置。

结构化配置走 YAML(config.yaml, 由 main.py --conf 指定) + Pydantic 校验;
密钥类只走 .env(python-dotenv 加载), 不进 YAML。

独立放在顶层模块, 避免与 app/ 等包产生循环导入。
用法:
    init_config(path)  # 进程启动时调用一次
    get_config()       # 其余地方按需读取
"""

from typing import Optional

import yaml
from pydantic import BaseModel, Field


class LogConfig(BaseModel):
    level: str = "INFO"
    # console: 只打到 stdout; file: 同时写滚动文件(预留)
    target: str = "console"
    file_dir: str = "./logs"


class GrpcServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = Field(default=50051, gt=0, le=65535)
    max_workers: int = Field(default=10, gt=0)
    # 优雅停机时,给正在处理的请求留的时间(秒)
    shutdown_grace: float = Field(default=5.0, ge=0)

    @property
    def addr(self) -> str:
        return f"{self.host}:{self.port}"


class ServerConfig(BaseModel):
    grpc: GrpcServerConfig = Field(default_factory=GrpcServerConfig)


class Config(BaseModel):
    name: str = "python-agent"
    environment: str = "dev"
    log: LogConfig = Field(default_factory=LogConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)


_config: Optional[Config] = None


def init_config(path: str) -> Config:
    global _config
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        _config = Config(**raw)
    except Exception as e:
        raise ValueError(f"load config from {path} failed: {e}") from e
    return _config


def get_config() -> Config:
    if _config is None:
        raise RuntimeError("config not initialized, call init_config() first")
    return _config
