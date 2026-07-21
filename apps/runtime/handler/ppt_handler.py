"""Translate between the PPT protobuf and transport-neutral core contracts."""

from __future__ import annotations

from google.protobuf.json_format import MessageToDict
from google.protobuf.timestamp_pb2 import Timestamp

from api import ppt_agent_pb2
from core.deck.contract import (
    AgentEvent,
    EventType,
    GeneratePptInput,
    InputResource,
    ModelConfig,
    PptOptions,
    PptStage,
    ResourceRef,
    ResourceType,
)
from core.deck.service import DeckService


_EVENT_TYPES = {
    EventType.TASK_STARTED: ppt_agent_pb2.TASK_STARTED,
    EventType.STEP_STARTED: ppt_agent_pb2.STEP_STARTED,
    EventType.STEP_PROGRESS: ppt_agent_pb2.STEP_PROGRESS,
    EventType.STEP_COMPLETED: ppt_agent_pb2.STEP_COMPLETED,
    EventType.ARTIFACT_READY: ppt_agent_pb2.ARTIFACT_READY,
    EventType.TASK_COMPLETED: ppt_agent_pb2.TASK_COMPLETED,
    EventType.TASK_FAILED: ppt_agent_pb2.TASK_FAILED,
}
_STAGES = {
    PptStage.PREPARING: ppt_agent_pb2.PREPARING,
    PptStage.PLANNING: ppt_agent_pb2.PLANNING,
    PptStage.GENERATING_CONTENT: ppt_agent_pb2.GENERATING_CONTENT,
    PptStage.GENERATING_ASSETS: ppt_agent_pb2.GENERATING_ASSETS,
    PptStage.GENERATING_PAGES: ppt_agent_pb2.GENERATING_PAGES,
    PptStage.RENDERING: ppt_agent_pb2.RENDERING,
    PptStage.ASSEMBLING_PPT: ppt_agent_pb2.ASSEMBLING_PPT,
    PptStage.FINISHED: ppt_agent_pb2.FINISHED,
}
_RESOURCE_TYPES = {
    ResourceType.INPUT_ATTACHMENT: ppt_agent_pb2.INPUT_ATTACHMENT,
    ResourceType.IMAGE: ppt_agent_pb2.IMAGE,
    ResourceType.SVG: ppt_agent_pb2.SVG,
    ResourceType.HTML: ppt_agent_pb2.HTML,
    ResourceType.PAGE_PREVIEW: ppt_agent_pb2.PAGE_PREVIEW,
    ResourceType.PPTX: ppt_agent_pb2.PPTX,
    ResourceType.OTHER_FILE: ppt_agent_pb2.OTHER_FILE,
}


class PptHandler:
    def __init__(self, service: DeckService) -> None:
        self._service = service

    def generate_ppt(self, request, context=None):
        inp = GeneratePptInput(
            task_id=request.task_id,
            description=request.description,
            attachments=[
                InputResource(
                    resource_id=item.resource_id,
                    name=item.name,
                    mime_type=item.mime_type,
                    content=item.content,
                    sha256=item.sha256,
                )
                for item in request.attachments
            ],
            models=[
                ModelConfig(
                    node_key=item.node_key,
                    provider=item.provider,
                    model=item.model,
                    api_key=item.api_key,
                    base_url=item.base_url,
                    provider_options=MessageToDict(
                        item.provider_options,
                        preserving_proto_field_name=True,
                    ),
                    temperature=item.temperature,
                    max_tokens=item.max_tokens,
                    supports_image_input=item.supports_image_input,
                )
                for item in request.models
            ],
            options=PptOptions(
                language=request.options.language,
                expected_slide_count=request.options.expected_slide_count,
                aspect_ratio=request.options.aspect_ratio,
                template_id=request.options.template_id,
            ),
        )
        for event in self._service.generate_ppt(inp):
            if context is not None and hasattr(context, "is_active") and not context.is_active():
                return
            yield _to_proto(event)


def _to_proto(event: AgentEvent):
    timestamp = Timestamp()
    timestamp.FromDatetime(event.created_at)
    kwargs = {
        "task_id": event.task_id,
        "event_id": event.event_id,
        "revision": event.revision,
        "sequence": event.sequence,
        "created_at": timestamp,
        "type": _EVENT_TYPES[event.type],
        "stage": _STAGES[event.stage],
        "message": event.message,
        "resources": [_resource_to_proto(item) for item in event.resources],
    }
    if event.progress is not None:
        kwargs["progress"] = ppt_agent_pb2.Progress(
            current=event.progress.current,
            total=event.progress.total,
            percent=event.progress.percent,
            unit=event.progress.unit,
        )

    details = sum(item is not None for item in (event.page, event.error, event.final_result))
    if details > 1:
        raise ValueError("AgentEvent detail fields are mutually exclusive")
    if event.page is not None:
        kwargs["page"] = ppt_agent_pb2.PageDetail(
            page_id=event.page.page_id,
            page_index=event.page.page_index,
            page_count=event.page.page_count,
            title=event.page.title,
        )
    elif event.error is not None:
        kwargs["error"] = ppt_agent_pb2.ErrorDetail(
            code=event.error.code,
            message=event.error.message,
            debug_message=event.error.debug_message,
            retryable=event.error.retryable,
        )
    elif event.final_result is not None:
        kwargs["final_result"] = ppt_agent_pb2.FinalResult(
            ppt_file=_resource_to_proto(event.final_result.ppt_file),
            slide_count=event.final_result.slide_count,
            elapsed_ms=event.final_result.elapsed_ms,
        )
    return ppt_agent_pb2.AgentEvent(**kwargs)


def _resource_to_proto(resource: ResourceRef):
    return ppt_agent_pb2.ResourceRef(
        resource_id=resource.resource_id,
        type=_RESOURCE_TYPES[resource.type],
        name=resource.name,
        relative_path=resource.relative_path,
        mime_type=resource.mime_type,
        size_bytes=resource.size_bytes,
        sha256=resource.sha256,
    )


__all__ = ["PptHandler"]
