"""Thin gRPC adapters for provider utilities and PPT generation."""

from __future__ import annotations

import logging
from concurrent import futures

import grpc

from api import ppt_agent_pb2_grpc, provider_pb2, provider_pb2_grpc
from config import GrpcServerConfig
from handler.container import AgentHandlers


logger = logging.getLogger(__name__)
MAX_GRPC_MESSAGE_BYTES = 300 * 1024 * 1024
RUNTIME_ERROR_MESSAGE = "runtime request failed"


class ProviderRuntimeServicer(provider_pb2_grpc.ProviderRuntimeServiceServicer):
    """Provider service intentionally contains only credential/list/text APIs."""

    def __init__(self, handlers: AgentHandlers) -> None:
        self.handlers = handlers

    def TestCredential(self, request, context):
        try:
            return self.handlers.provider_handler.test_credential(request)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("TestCredential failed: provider=%s", request.provider)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(RUNTIME_ERROR_MESSAGE)
            return provider_pb2.TestCredentialResponse()

    def ListProviders(self, request, context):
        del request
        try:
            return self.handlers.provider_handler.list_providers()
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("ListProviders failed")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(RUNTIME_ERROR_MESSAGE)
            return provider_pb2.ListProvidersResponse()

    def StreamText(self, request, context):
        try:
            yield from self.handlers.provider_handler.stream_text(request)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("StreamText failed: provider=%s model=%s", request.provider, request.model)
            yield provider_pb2.StreamTextResponse(error=RUNTIME_ERROR_MESSAGE, done=True)


class PptAgentServicer(ppt_agent_pb2_grpc.PptAgentServiceServicer):
    def __init__(self, handlers: AgentHandlers) -> None:
        self.handlers = handlers

    def GeneratePpt(self, request, context):
        logger.info("GeneratePpt started: task_id=%s", request.task_id)
        try:
            yield from self.handlers.ppt_handler.generate_ppt(request, context)
            logger.info("GeneratePpt finished: task_id=%s", request.task_id)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("GeneratePpt transport failed: task_id=%s", request.task_id)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(RUNTIME_ERROR_MESSAGE)


class GrpcServer:
    def __init__(self, cfg: GrpcServerConfig, handlers: AgentHandlers) -> None:
        self._cfg = cfg
        self._server = grpc.server(
            futures.ThreadPoolExecutor(max_workers=cfg.max_workers),
            options=[
                ("grpc.max_receive_message_length", MAX_GRPC_MESSAGE_BYTES),
                ("grpc.max_send_message_length", MAX_GRPC_MESSAGE_BYTES),
            ],
        )
        provider_pb2_grpc.add_ProviderRuntimeServiceServicer_to_server(
            ProviderRuntimeServicer(handlers), self._server
        )
        ppt_agent_pb2_grpc.add_PptAgentServiceServicer_to_server(
            PptAgentServicer(handlers), self._server
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


__all__ = ["GrpcServer", "PptAgentServicer", "ProviderRuntimeServicer"]
