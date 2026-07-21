"""Concrete PPT phases, following the same shape as the soulmate agents."""

from core.deck.agents.design.graph import Design
from core.deck.agents.design.nodes import DesignNodes
from core.deck.agents.generation.graph import Generation
from core.deck.agents.generation.nodes import GenerationNodes
from core.deck.agents.planning.graph import Planning
from core.deck.agents.planning.nodes import PlanningNodes
from core.deck.agents.postprocessor.graph import Postprocessor
from core.deck.agents.postprocessor.nodes import PostprocessorNodes


__all__ = [
    "Design",
    "DesignNodes",
    "Generation",
    "GenerationNodes",
    "Planning",
    "PlanningNodes",
    "Postprocessor",
    "PostprocessorNodes",
]
