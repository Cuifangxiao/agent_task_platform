"""Transport-neutral contracts for one uninterrupted PPT generation run."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


MODEL_NODE_KEYS = frozenset({"brief", "outline", "style", "writer", "designer"})


@dataclass(frozen=True)
class ModelConfig:
    node_key: str
    provider: str
    model: str
    api_key: str = ""
    base_url: str = ""
    provider_options: dict[str, Any] = field(default_factory=dict)
    temperature: float = 0.0
    max_tokens: int = 0
    supports_image_input: bool = False


@dataclass(frozen=True)
class InputResource:
    resource_id: str
    name: str
    mime_type: str = ""
    content: bytes = b""
    sha256: str = ""

    @property
    def filename(self) -> str:
        """Compatibility name used by the local document/image readers."""

        return self.name

    @property
    def size_bytes(self) -> int:
        return len(self.content)


@dataclass(frozen=True)
class PptOptions:
    language: str = ""
    expected_slide_count: int = 0
    aspect_ratio: str = "16:9"
    template_id: str = "auto"


@dataclass(frozen=True)
class GeneratePptInput:
    task_id: str
    description: str
    attachments: list[InputResource] = field(default_factory=list)
    models: list[ModelConfig] = field(default_factory=list)
    options: PptOptions = field(default_factory=PptOptions)


class EventType(str, Enum):
    TASK_STARTED = "task_started"
    STEP_STARTED = "step_started"
    STEP_PROGRESS = "step_progress"
    STEP_COMPLETED = "step_completed"
    ARTIFACT_READY = "artifact_ready"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"


class PptStage(str, Enum):
    PREPARING = "preparing"
    PLANNING = "planning"
    GENERATING_CONTENT = "generating_content"
    GENERATING_ASSETS = "generating_assets"
    GENERATING_PAGES = "generating_pages"
    RENDERING = "rendering"
    ASSEMBLING_PPT = "assembling_ppt"
    FINISHED = "finished"


class ResourceType(str, Enum):
    INPUT_ATTACHMENT = "input_attachment"
    IMAGE = "image"
    SVG = "svg"
    HTML = "html"
    PAGE_PREVIEW = "page_preview"
    PPTX = "pptx"
    OTHER_FILE = "other_file"


@dataclass(frozen=True)
class Progress:
    current: int = 0
    total: int = 0
    percent: float = 0.0
    unit: str = ""


@dataclass(frozen=True)
class ResourceRef:
    resource_id: str
    type: ResourceType
    name: str
    relative_path: str
    mime_type: str = ""
    size_bytes: int = 0
    sha256: str = ""


@dataclass(frozen=True)
class PageDetail:
    page_id: str
    page_index: int
    page_count: int
    title: str = ""


@dataclass(frozen=True)
class ErrorDetail:
    code: str
    message: str
    debug_message: str = ""
    retryable: bool = False


@dataclass(frozen=True)
class FinalResult:
    ppt_file: ResourceRef
    slide_count: int
    elapsed_ms: int


@dataclass(frozen=True)
class AgentEvent:
    task_id: str
    sequence: int
    created_at: datetime
    type: EventType
    stage: PptStage
    message: str = ""
    progress: Progress | None = None
    resources: tuple[ResourceRef, ...] = ()
    page: PageDetail | None = None
    error: ErrorDetail | None = None
    final_result: FinalResult | None = None


@dataclass(frozen=True)
class PipelineEvent:
    """Custom event emitted inside LangGraph before task metadata is attached."""

    type: EventType
    stage: PptStage
    message: str = ""
    progress: Progress | None = None
    resources: tuple[ResourceRef, ...] = ()
    page: PageDetail | None = None


__all__ = [
    "AgentEvent",
    "ErrorDetail",
    "EventType",
    "FinalResult",
    "GeneratePptInput",
    "InputResource",
    "MODEL_NODE_KEYS",
    "ModelConfig",
    "PageDetail",
    "PipelineEvent",
    "PptOptions",
    "PptStage",
    "Progress",
    "ResourceRef",
    "ResourceType",
]
