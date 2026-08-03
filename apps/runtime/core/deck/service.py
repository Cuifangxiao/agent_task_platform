"""Application service for one uninterrupted PPT generation stream."""

from __future__ import annotations

import hashlib
import logging
import re
import time
from collections.abc import Callable, Iterator
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from core.deck.domain.contracts import (
    AgentEvent,
    ErrorDetail,
    EventType,
    FinalResult,
    GeneratePptInput,
    PipelineEvent,
    PptStage,
    Progress,
    ResourceRef,
    ResourceType,
)
from core.deck.domain.errors import DeckError
from core.deck.runtime.llm import ModelInvocationError
from core.deck.runtime.model_config import build_ppt_llms, describe_model_assignment
from core.deck.workspace.paths import DeckProjectPaths, artifact_dir
from core.deck.html.browser import SecureBrowserSessionPool, resolve_browser_concurrency
from core.deck.agents.processor.graph import resolve_slide_concurrency
from core.deck.pipeline.graph import PptGraph
from core.deck.domain.schemas import PROMPT_VERSION, RENDERER_VERSION, SCHEMA_VERSION
from core.deck.workspace.sources import (
    parse_uploaded_files_with_provenance,
    persist_normalized_source_bundle,
    persist_uploaded_local_assets,
)


logger = logging.getLogger(__name__)


class DeckService:
    """Run a fresh, request-scoped PPT agent for every task."""

    def __init__(self, graph_factory: Callable[..., Any] | None = None) -> None:
        self._graph_factory = graph_factory

    def generate_ppt(self, inp: GeneratePptInput) -> Iterator[AgentEvent]:
        task_id = inp.task_id.strip()
        sequence = 0
        last_stage = PptStage.PREPARING
        started_at = time.perf_counter()
        browser_session: SecureBrowserSessionPool | None = None

        try:
            description = inp.description.strip()
            if not task_id:
                raise ValueError("task_id is required")
            if not description:
                raise ValueError("description is required")

            project = DeckProjectPaths.for_request(artifact_dir(), task_id).ensure()
            sequence += 1
            yield _agent_event(
                task_id,
                sequence,
                EventType.TASK_STARTED,
                PptStage.PREPARING,
                "PPT generation started",
            )

            llms = build_ppt_llms(inp)
            slide_concurrency = resolve_slide_concurrency()
            browser_session = SecureBrowserSessionPool(
                resolve_browser_concurrency(slide_concurrency=slide_concurrency)
            )
            uploaded_file_names, uploaded_file_text, source_chunks = parse_uploaded_files_with_provenance(inp)
            local_assets = persist_uploaded_local_assets(project.root, inp)
            persist_normalized_source_bundle(
                project.root,
                source_names=uploaded_file_names,
                normalized_text=uploaded_file_text,
                source_chunks=source_chunks,
                local_assets=local_assets,
            )
            attachment_count = len(inp.attachments)
            sequence += 1
            yield _agent_event(
                task_id,
                sequence,
                EventType.STEP_COMPLETED,
                PptStage.PREPARING,
                "Input resources prepared",
                progress=Progress(
                    current=attachment_count,
                    total=attachment_count,
                    percent=100.0,
                    unit="attachments",
                ),
            )

            initial_state = {
                "schema_version": SCHEMA_VERSION,
                "prompt_version": PROMPT_VERSION,
                "renderer_version": RENDERER_VERSION,
                "task_id": task_id,
                "request_id": task_id,
                "project_dir": str(project.root),
                "status": "created",
                # The outline model owns semantic interpretation and the title.
                # Application code only enforces page-count and safety contracts.
                "deck_title": "",
                "uploaded_file_text": uploaded_file_text,
                "source_chunks": source_chunks,
                "local_assets": local_assets,
                "user_description": description,
                "current_slide_index": 0,
                "completed_slides": {},
                "layout_history": [],
                "validation_issues": [],
            }
            logger.info(
                "generate ppt: task_id=%s models=%s attachments=%s",
                task_id,
                describe_model_assignment(inp),
                attachment_count,
            )

            graph_factory = self._graph_factory or PptGraph
            graph_module = graph_factory(llms, artifact_dir(), browser_session)
            final_state: dict[str, Any] = {}
            ppt_resource: ResourceRef | None = None
            for mode, data in graph_module.stream(initial_state, stream_mode=["custom", "values"]):
                if mode == "values":
                    if isinstance(data, dict):
                        final_state = data
                    continue
                if mode != "custom" or not isinstance(data, PipelineEvent):
                    continue
                if data.type in {EventType.TASK_COMPLETED, EventType.TASK_FAILED}:
                    logger.warning(
                        "ignored pipeline-owned terminal event: task_id=%s type=%s",
                        task_id,
                        data.type.value,
                    )
                    continue

                hydrated = _hydrate_pipeline_event(data, project)
                last_stage = hydrated.stage
                sequence += 1
                event = _agent_event(
                    task_id,
                    sequence,
                    hydrated.type,
                    hydrated.stage,
                    hydrated.message,
                    progress=hydrated.progress,
                    resources=hydrated.resources,
                    page=hydrated.page,
                    event_id=hydrated.event_id,
                    revision=hydrated.revision,
                )
                for resource in event.resources:
                    if resource.type is ResourceType.PPTX:
                        ppt_resource = resource
                yield event

            if ppt_resource is None:
                ppt_resource = _ppt_resource_from_state(final_state, project)
            completed = final_state.get("completed_slides")
            slide_count = len(completed) if isinstance(completed, dict) else 0
            elapsed_ms = round((time.perf_counter() - started_at) * 1000)
            sequence += 1
            yield _agent_event(
                task_id,
                sequence,
                EventType.TASK_COMPLETED,
                PptStage.FINISHED,
                "PPT generation completed",
                resources=(ppt_resource,),
                final_result=FinalResult(
                    ppt_file=ppt_resource,
                    slide_count=slide_count,
                    elapsed_ms=elapsed_ms,
                ),
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.exception("generate ppt failed: task_id=%s", task_id)
            sequence += 1
            error_message = str(exc) if isinstance(exc, (DeckError, ValueError)) else "PPT generation failed"
            yield _agent_event(
                task_id,
                sequence,
                EventType.TASK_FAILED,
                last_stage,
                error_message,
                error=ErrorDetail(
                    code=_error_code(exc),
                    message=error_message,
                    debug_message=str(exc),
                    retryable=_retryable_error(exc),
                ),
            )
        finally:
            if browser_session is not None:
                try:
                    browser_session.close()
                except Exception:  # pylint: disable=broad-exception-caught
                    logger.exception("failed to close browser session: task_id=%s", task_id)


def _agent_event(
    task_id: str,
    sequence: int,
    event_type: EventType,
    stage: PptStage,
    message: str,
    *,
    progress: Progress | None = None,
    resources: tuple[ResourceRef, ...] = (),
    page=None,
    error: ErrorDetail | None = None,
    final_result: FinalResult | None = None,
    event_id: str = "",
    revision: str = "",
) -> AgentEvent:
    return AgentEvent(
        task_id=task_id,
        sequence=sequence,
        created_at=datetime.now(timezone.utc),
        type=event_type,
        stage=stage,
        message=message,
        progress=progress,
        resources=resources,
        page=page,
        error=error,
        final_result=final_result,
        event_id=event_id,
        revision=revision,
    )


def _hydrate_pipeline_event(event: PipelineEvent, project: DeckProjectPaths) -> PipelineEvent:
    resources = tuple(_hydrate_resource(resource, project) for resource in event.resources)
    return replace(event, resources=resources)


def _hydrate_resource(resource: ResourceRef, project: DeckProjectPaths) -> ResourceRef:
    relative = PurePosixPath(resource.relative_path)
    if not resource.relative_path or relative.is_absolute() or ".." in relative.parts:
        raise ValueError("artifact relative_path must stay inside the task directory")
    path = project.root.joinpath(*relative.parts).resolve()
    try:
        path.relative_to(project.root)
    except ValueError as exc:
        raise ValueError("artifact relative_path must stay inside the task directory") from exc
    if not path.is_file():
        raise ValueError(f"artifact does not exist: {resource.relative_path}")
    return replace(
        resource,
        name=resource.name or path.name,
        size_bytes=path.stat().st_size,
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    )


def _ppt_resource_from_state(state: dict[str, Any], project: DeckProjectPaths) -> ResourceRef:
    raw_path = str(state.get("artifact_path") or "").strip()
    if not raw_path:
        raise ValueError("PPT pipeline finished without an artifact")
    path = Path(raw_path).resolve()
    try:
        relative_path = project.relative(path)
    except ValueError as exc:
        raise ValueError("PPT artifact is outside the task directory") from exc
    return _hydrate_resource(
        ResourceRef(
            resource_id="pptx",
            type=ResourceType.PPTX,
            name=path.name,
            relative_path=relative_path,
            mime_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ),
        project,
    )


def _error_code(exc: Exception) -> str:
    name = type(exc).__name__
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower() or "ppt_runtime_error"


def _retryable_error(exc: Exception) -> bool:
    # Schema/content/configuration failures have already exhausted their local
    # repair path.  Only provider/transport failures are useful to retry as a
    # new task under the current public contract.
    return isinstance(exc, ModelInvocationError)


__all__ = ["DeckService"]
