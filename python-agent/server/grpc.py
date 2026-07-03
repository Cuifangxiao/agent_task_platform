"""gRPC 协议接入层。

Servicer 是薄壳: 只做委托 handler + 错误边界(try/except -> StatusCode + 日志),
不写业务逻辑。GrpcServer 负责 server 的组装与启停。
"""

import logging
import traceback
from concurrent import futures

import grpc

from api import provider_pb2, provider_pb2_grpc
from config import GrpcServerConfig
from handler.container import AgentHandlers

logger = logging.getLogger(__name__)


class ProviderRuntimeServicer(provider_pb2_grpc.ProviderRuntimeServiceServicer):
    def __init__(self, handlers: AgentHandlers) -> None:
        self.handlers = handlers

    def TestCredential(self, req, context):
        logger.info(
            "TestCredential Begin. user_id:%s username:%s provider:%s",
            req.user.user_id,
            req.user.username,
            req.provider,
        )
        try:
            resp = self.handlers.provider_handler.test_credential(req)
            logger.info(
                "TestCredential Success. provider:%s request_id:%s",
                req.provider,
                resp.request_id,
            )
            return resp
        except Exception as e:
            logger.error("TestCredential Failed. provider:%s err:%s", req.provider, e)
            logger.error(traceback.format_exc())
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"test credential failed: {e}")
            return provider_pb2.TestCredentialResponse()


class GrpcServer:
    def __init__(self, cfg: GrpcServerConfig, handlers: AgentHandlers) -> None:
        self._cfg = cfg
        self._server = grpc.server(
            futures.ThreadPoolExecutor(max_workers=cfg.max_workers)
        )
        # 在这里注册所有 servicer
        provider_pb2_grpc.add_ProviderRuntimeServiceServicer_to_server(
            ProviderRuntimeServicer(handlers), self._server
        )
        self._server.add_insecure_port(cfg.addr)

    def start(self) -> None:
        self._server.start()
        logger.info("gRPC server listening on %s", self._cfg.addr)

    def stop(self) -> None:
        logger.info("gRPC server stopping (grace=%ss)", self._cfg.shutdown_grace)
        self._server.stop(grace=self._cfg.shutdown_grace).wait()
        logger.info("gRPC server stopped")

    def wait_for_termination(self) -> None:
        self._server.wait_for_termination()
