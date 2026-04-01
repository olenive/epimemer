"""Mock LLM provider for testing.

Returns deterministic, predictable outputs for unit tests.
No API calls are made.
"""

from epimemer.core.types import Fact, Inference, Segment, Topic


class MockDecompositionProvider:
    """Deterministic mock decomposition provider for testing."""

    async def extract_topics(self, segment: Segment) -> list[Topic]:
        return [
            Topic(
                content=f"This segment discusses themes related to: {segment.text[:80]}",
                source_id=segment.id,
                extraction_method="mock",
            )
        ]

    async def extract_facts(self, segment: Segment) -> list[Fact]:
        return [
            Fact(
                content=f"The text states: {segment.text[:80]}",
                source_id=segment.id,
                extraction_method="mock",
            )
        ]

    async def extract_inferences(self, segment: Segment) -> list[Inference]:
        return [
            Inference(
                content=f"Based on the text, one can infer: {segment.text[:80]}",
                source_id=segment.id,
                extraction_method="mock",
            )
        ]

    async def synthesize_parent_topic(self, children: list[Topic]) -> Topic:
        child_summaries = "; ".join(c.content[:40] for c in children)
        return Topic(
            content=f"Parent theme encompassing: {child_summaries}",
            source_id=children[0].source_id,
            extraction_method="mock:parent_synthesis",
            metadata={"synthesized_from": [c.id for c in children]},
        )

    async def enrich_topic_description(
        self, topic: Topic, associated_material: list[str]
    ) -> str:
        material_summary = "; ".join(m[:40] for m in associated_material[:3])
        return f"{topic.content} [enriched with: {material_summary}]"

    async def split_topic(
        self, topic: Topic, associated_material: list[str]
    ) -> list[Topic]:
        if len(associated_material) < 2:
            return []
        # Deterministic split: create one subtopic per material item (up to 3)
        return [
            Topic(
                content=f"Subtopic of '{topic.content[:40]}': {m[:60]}",
                source_id=topic.source_id,
                extraction_method="mock:split",
                metadata={"split_from": topic.id},
            )
            for m in associated_material[:3]
        ]


class MockSegmentationProvider:
    """Mock segmentation that splits on double newlines."""

    async def segment_text(self, text: str) -> list[tuple[int, int]]:
        segments = []
        start = 0
        parts = text.split("\n\n")
        for part in parts:
            if part.strip():
                end = start + len(part)
                segments.append((start, end))
            start += len(part) + 2  # +2 for the \n\n
        if not segments:
            segments.append((0, len(text)))
        return segments
