"""protoc 生成的 gRPC 桩代码,由根目录 `make proto` 生成,不要手改。

服务代码统一 `from api import provider_pb2, provider_pb2_grpc`。
"""

from . import provider_pb2
from . import provider_pb2_grpc

__all__ = ["provider_pb2", "provider_pb2_grpc"]
