"""Concrete PPT phases, following the same shape as the soulmate agents."""

from core.deck.agents.design.graph import Design
from core.deck.agents.generation.graph import Generation
from core.deck.agents.planning.graph import Planning
from core.deck.agents.postprocessor.graph import Postprocessor


__all__ = ["Design", "Generation", "Planning", "Postprocessor"]
