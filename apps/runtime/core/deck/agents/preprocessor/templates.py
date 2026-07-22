"""Repository for scene narrative templates (asset A only)."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from core.deck.agents.preprocessor.schema import SceneNarrativeTemplate


SCENE_TEMPLATE_DIR = Path(__file__).resolve().parent / "assets" / "scenes"
SCENE_ALIASES = {
    "academic_defense": "thesis_defense",
    "defense": "thesis_defense",
    "thesis": "thesis_defense",
    "答辩": "thesis_defense",
    "论文答辩": "thesis_defense",
    "工作汇报": "project_report",
    "项目汇报": "project_report",
    "汇报": "project_report",
    "pitch": "business_pitch",
    "路演": "business_pitch",
    "商业宣讲": "business_pitch",
    "教学": "teaching",
    "课程": "teaching",
}


class SceneTemplateRepository:
    """Load and validate narrative assets without knowing any layout assets."""

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = Path(directory or SCENE_TEMPLATE_DIR)

    def resolve(self, scene: str) -> SceneNarrativeTemplate:
        normalized = str(scene or "").strip().lower()
        template_id = SCENE_ALIASES.get(normalized, normalized or "general")
        candidate = self.directory / f"{template_id}.json"
        if not candidate.is_file():
            candidate = self.directory / "general.json"
        return _load_template(candidate.resolve())

    def list_scene_ids(self) -> tuple[str, ...]:
        return tuple(sorted(path.stem for path in self.directory.glob("*.json") if path.is_file()))


@lru_cache(maxsize=32)
def _load_template(path: Path) -> SceneNarrativeTemplate:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return SceneNarrativeTemplate.model_validate(payload)


__all__ = ["SCENE_TEMPLATE_DIR", "SceneTemplateRepository"]
