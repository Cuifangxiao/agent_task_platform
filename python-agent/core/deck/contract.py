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


@dataclass(frozen=True)
class DeckSourceFile:
    filename: str
    content: bytes


@dataclass(frozen=True)
class GenerateDeckInput:
    user_id: int
    username: str
    user_description: str
    source_file_filename: str = ""
    source_file: bytes = b""
    source_files: list[DeckSourceFile] = field(default_factory=list)
    node_model_configs: list[DeckNodeModelConfig] = field(default_factory=list)


@dataclass(frozen=True)
class ResumeDeckInput:
    user_id: int
    username: str
    request_id: str
    outline_markdown: str = ""
    node_model_configs: list[DeckNodeModelConfig] = field(default_factory=list)
    action: str = ""
    payload_json: str = ""


@dataclass(frozen=True)
class GenerateDeckOutput:
    request_id: str
    event_type: str
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
