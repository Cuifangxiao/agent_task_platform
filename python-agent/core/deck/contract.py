"""Deck generation input/output contracts."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class DeckNodeModelConfig:
    node_key: str
    provider: str
    model: str
    api_key: str
    base_url: str
    provider_options_json: str
    temperature: float
    max_tokens: int
    supports_image_input: bool = False


@dataclass(frozen=True)
class DeckSourceFile:
    filename: str
    content: bytes = b""
    resource_id: str = ""
    local_path: str = ""
    sha256: str = ""
    size_bytes: int = 0


@dataclass(frozen=True)
class GenerateDeckInput:
    user_id: int
    username: str
    user_description: str
    job_id: str = ""
    generation_mode: str = "step_by_step"
    source_file_filename: str = ""
    source_file: bytes = b""
    source_files: list[DeckSourceFile] = field(default_factory=list)
    node_model_configs: list[DeckNodeModelConfig] = field(default_factory=list)


@dataclass(frozen=True)
class ResumeDeckInput:
    user_id: int
    username: str
    request_id: str
    job_id: str = ""
    outline_markdown: str = ""
    node_model_configs: list[DeckNodeModelConfig] = field(default_factory=list)
    action: str = ""
    payload_json: str = ""


@dataclass(frozen=True)
class GenerateDeckOutput:
    request_id: str
    event_type: str
    job_id: str = ""
    event_version: int = 1
    stage: str = ""
    message: str = ""
    channel: str = ""
    text_delta: str = ""
    content: str = ""
    outline_markdown: str = ""
    payload_json: str = ""
    artifact_path: str = ""
    done: bool = False
    error: str = ""
    sequence: int = 0
    stage_status: str = ""
    progress_current: int = 0
    progress_total: int = 0
    slide_index: int = 0
    slide_version: int = 0
    severity: str = "info"
    action_required: str = ""
    retryable: bool = False
    error_code: str = ""
