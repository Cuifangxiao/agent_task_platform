"""gRPC 服务器组装与启动。"""

import signal
import threading
from concurrent import futures

import grpc

from api.provider.v1 import provider_pb2_grpc

from app.config import Settings
from app.grpc_api.provider_runtime import ProviderRuntimeServicer
from app.logger import get_logger
from app.services.provider_service import ProviderService

logger = get_logger(__name__)


def build_server(settings: Settings) -> grpc.Server:
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=settings.grpc.max_workers)
    )

    # 在这里组装依赖并注册所有 servicer
    provider_pb2_grpc.add_ProviderRuntimeServiceServicer_to_server(
        ProviderRuntimeServicer(ProviderService()),
        server,
    )

    server.add_insecure_port(settings.grpc.listen_addr)
    return server


def serve(settings: Settings) -> None:
    server = build_server(settings)
    server.start()
    logger.info(
        "python agent gRPC server listening on %s (env=%s)",
        settings.grpc.listen_addr,
        settings.env,
    )

    stop_event = threading.Event()

    def _handle_signal(signum, _frame):
        logger.info("received signal %s, shutting down", signum)
        stop_event.set()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    stop_event.wait()
    server.stop(grace=settings.grpc.shutdown_grace).wait()
    logger.info("server stopped")
