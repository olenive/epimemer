"""Tests for mock LLM providers."""

import pytest

from epimemer.core.types import Fact, Inference, Segment, Topic
from epimemer.llm.mock import MockDecompositionProvider, MockSegmentationProvider


@pytest.fixture
def segment():
    return Segment(
        source_id="doc1",
        text="Machine learning models require large datasets for training.",
        span_start=0,
        span_end=60,
    )


class TestMockDecompositionProvider:

    async def test_extract_topics(self, segment):
        provider = MockDecompositionProvider()
        topics = await provider.extract_topics(segment)
        assert len(topics) >= 1
        assert all(isinstance(t, Topic) for t in topics)
        assert all(t.source_id == segment.id for t in topics)
        assert all(t.extraction_method == "mock" for t in topics)

    async def test_extract_facts(self, segment):
        provider = MockDecompositionProvider()
        facts = await provider.extract_facts(segment)
        assert len(facts) >= 1
        assert all(isinstance(f, Fact) for f in facts)
        assert all(f.source_id == segment.id for f in facts)

    async def test_extract_inferences(self, segment):
        provider = MockDecompositionProvider()
        inferences = await provider.extract_inferences(segment)
        assert len(inferences) >= 1
        assert all(isinstance(i, Inference) for i in inferences)
        assert all(i.source_id == segment.id for i in inferences)


class TestMockSegmentationProvider:

    async def test_segment_single_paragraph(self):
        provider = MockSegmentationProvider()
        spans = await provider.segment_text("Hello world")
        assert len(spans) == 1
        assert spans[0] == (0, 11)

    async def test_segment_multiple_paragraphs(self):
        provider = MockSegmentationProvider()
        text = "Paragraph one.\n\nParagraph two.\n\nParagraph three."
        spans = await provider.segment_text(text)
        assert len(spans) == 3

    async def test_spans_cover_text(self):
        provider = MockSegmentationProvider()
        text = "First part.\n\nSecond part."
        spans = await provider.segment_text(text)
        for start, end in spans:
            assert text[start:end].strip() != ""
