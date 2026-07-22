"""Public entrypoint for the deck preprocessor phase."""

from core.deck.agents.preprocessor.graph import Preprocessor
from core.deck.agents.preprocessor.schema import (
    GlobalMeta,
    GlobalMetaInterpretation,
    LogicalOutline,
    OutlineSection,
    OutlineTopic,
    PageCategory,
    PagePlan,
    PagePlanItem,
    SceneNarrativeTemplate,
)

__all__ = [
    "GlobalMeta",
    "GlobalMetaInterpretation",
    "LogicalOutline",
    "OutlineSection",
    "OutlineTopic",
    "PageCategory",
    "PagePlan",
    "PagePlanItem",
    "Preprocessor",
    "SceneNarrativeTemplate",
]
