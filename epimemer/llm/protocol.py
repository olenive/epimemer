"""LLM provider protocol.

Defines the interface for LLM-based extraction operations.
Implementations use Pydantic AI for structured output validation.
"""

from typing import Protocol

from epimemer.core.types import Fact, Inference, Segment, Topic


class DecompositionProvider(Protocol):
    """Protocol for LLM-based text decomposition."""

    async def extract_topics(self, segment: Segment) -> list[Topic]:
        """Extract topic descriptions from a segment."""
        ...

    async def extract_facts(self, segment: Segment) -> list[Fact]:
        """Extract atomic, verifiable facts from a segment."""
        ...

    async def extract_inferences(self, segment: Segment) -> list[Inference]:
        """Extract higher-level interpretive inferences from a segment."""
        ...

    async def synthesize_parent_topic(self, children: list[Topic]) -> Topic:
        """Synthesize a parent topic that summarizes a group of child topics.

        The parent description should capture the shared theme at a higher
        level of abstraction while remaining distinguishable from unrelated topics.
        """
        ...

    async def enrich_topic_description(
        self, topic: Topic, associated_material: list[str]
    ) -> str:
        """Re-synthesize a topic description from accumulated material.

        Takes the current topic and a list of associated text (segment texts,
        fact contents, etc.) and produces an improved description that better
        captures the theme given the richer context.
        """
        ...

    async def split_topic(
        self, topic: Topic, associated_material: list[str]
    ) -> list[Topic]:
        """Split a broad topic into distinct subtopics.

        Given a topic and its associated material, identify the distinct
        sub-themes and produce a topic description for each. Returns an
        empty list if no meaningful split is found.
        """
        ...


class SegmentationProvider(Protocol):
    """Protocol for LLM-guided text segmentation."""

    async def segment_text(self, text: str) -> list[tuple[int, int]]:
        """Identify topic boundary positions in the text.

        Returns a list of (start, end) character offsets for each segment.
        """
        ...
