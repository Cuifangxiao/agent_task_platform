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
class GenerateDeckInput:
    user_id: int
    username: str
    user_description: str
    audience: str
    goal: str
    page_count: int
    language: str
    style: str
    source_text: str
    source_pdf_filename: str = ""
    source_pdf: bytes = b""
    node_model_configs: list[DeckNodeModelConfig] = field(default_factory=list)


@dataclass(frozen=True)
class GenerateDeckOutput:
    request_id: str
    event_type: str
    stage: str = ""
    message: str = ""
    payload_json: str = ""
    artifact_path: str = ""
    done: bool = False
    error: str = ""
    sequence: int = 0
