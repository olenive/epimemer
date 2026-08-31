"""Tests for segmentation strategies.

Covers:
- Semantic similarity strategy with controlled embeddings
- Paragraph split strategy
- Petri net token flow verification
- Segment validity checks (non-overlapping, full coverage, valid spans)
"""

from petritype.core.executable_graph_components import ExecutableGraphOperations

from epimemer.core.types import RawDocument, Segment
from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.pipelines.segmentation.paragraph_split import (
    paragraph_split_segmentation_net,
)
from epimemer.pipelines.segmentation.semantic_similarity import (
    SentenceList,
    SimilarityScores,
    _cosine_similarity,
    _detect_boundaries,
    _split_into_sentences,
    semantic_similarity_segmentation_net,
)

# --- Helper: Controlled embedding provider for deterministic tests ---


class ControlledEmbeddingProvider:
    """Embedding provider that maps specific sentences to predefined vectors.

    Sentences about the same topic get similar vectors (high cosine similarity).
    Sentences about different topics get dissimilar vectors (low cosine similarity).
    """

    def __init__(self, vector_map: dict[str, list[float]]):
        self._vector_map = vector_map
        self._model_id = "controlled-test"
        self._dimension = len(next(iter(vector_map.values()))) if vector_map else 8

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def dimension(self) -> int:
        return self._dimension

    async def embed(self, texts: list[str]) -> list[list[float]]:
        result = []
        for text in texts:
            if text in self._vector_map:
                result.append(self._vector_map[text])
            else:
                # Default: return a zero-ish vector
                result.append([0.1] * self._dimension)
        return result


def _segments_are_valid(segments: list[Segment], source_content: str, source_id: str) -> None:
    """Assert that segments are valid: non-overlapping, covering source text, correct source_id."""
    assert len(segments) > 0, "Expected at least one segment"

    for seg in segments:
        assert seg.source_id == source_id, (
            f"Segment source_id mismatch: {seg.source_id} != {source_id}"
        )
        assert seg.span_start >= 0, f"Negative span_start: {seg.span_start}"
        assert seg.span_end <= len(source_content), (
            f"span_end exceeds content length: {seg.span_end} > {len(source_content)}"
        )
        assert seg.span_start < seg.span_end, f"Empty span: {seg.span_start} >= {seg.span_end}"
        assert isinstance(seg, Segment), f"Expected Segment, got {type(seg)}"
        # The segment text should match the source content at the given span
        expected_text = source_content[seg.span_start : seg.span_end]
        assert seg.text == expected_text, (
            f"Segment text mismatch at [{seg.span_start}:{seg.span_end}]:\n"
            f"  segment.text = {seg.text!r}\n"
            f"  source slice = {expected_text!r}"
        )

    # Check non-overlapping (sorted by start)
    sorted_segments = sorted(segments, key=lambda s: s.span_start)
    for i in range(len(sorted_segments) - 1):
        current = sorted_segments[i]
        nxt = sorted_segments[i + 1]
        assert current.span_end <= nxt.span_start, (
            f"Overlapping segments: [{current.span_start}:{current.span_end}] and "
            f"[{nxt.span_start}:{nxt.span_end}]"
        )

    # Check coverage: segments should cover the full text content (allowing for whitespace gaps)
    covered = set()
    for seg in segments:
        for i in range(seg.span_start, seg.span_end):
            covered.add(i)
    # Every non-whitespace character should be covered
    for i, ch in enumerate(source_content):
        if not ch.isspace():
            assert i in covered, f"Character at index {i} ({ch!r}) not covered by any segment"


# ========================================================================
# Semantic similarity strategy tests
# ========================================================================


class TestSentenceSplitting:
    def test_split_simple_text(self):
        text = "Hello world. This is a test. Another sentence here."
        sentences = _split_into_sentences(text)
        assert len(sentences) == 3
        assert sentences[0][0] == "Hello world."
        assert sentences[1][0] == "This is a test."
        assert sentences[2][0] == "Another sentence here."

    def test_split_preserves_offsets(self):
        text = "First sentence. Second sentence."
        sentences = _split_into_sentences(text)
        for sent_text, start, end in sentences:
            assert text[start:end] == sent_text

    def test_split_single_sentence(self):
        text = "Just one sentence without a period"
        sentences = _split_into_sentences(text)
        assert len(sentences) == 1

    def test_split_empty_text(self):
        sentences = _split_into_sentences("")
        assert len(sentences) == 0


class TestCosineSimilarity:
    def test_identical_vectors(self):
        v = [1.0, 0.0, 0.0]
        assert abs(_cosine_similarity(v, v) - 1.0) < 1e-6

    def test_orthogonal_vectors(self):
        a = [1.0, 0.0, 0.0]
        b = [0.0, 1.0, 0.0]
        assert abs(_cosine_similarity(a, b)) < 1e-6

    def test_opposite_vectors(self):
        a = [1.0, 0.0]
        b = [-1.0, 0.0]
        assert abs(_cosine_similarity(a, b) + 1.0) < 1e-6

    def test_zero_vector(self):
        a = [0.0, 0.0]
        b = [1.0, 0.0]
        assert _cosine_similarity(a, b) == 0.0


class TestBoundaryDetection:
    def test_no_boundaries_uniform_scores(self):
        scores = [0.9, 0.9, 0.9, 0.9]
        boundaries = _detect_boundaries(scores, threshold_factor=1.0)
        assert boundaries == []

    def test_clear_boundary(self):
        # High similarity within topics, low at boundary
        scores = [0.95, 0.93, 0.1, 0.94, 0.92]
        boundaries = _detect_boundaries(scores, threshold_factor=1.0)
        assert 2 in boundaries

    def test_empty_scores(self):
        assert _detect_boundaries([]) == []


class TestSemanticSimilarityStrategy:
    """Test the full semantic similarity segmentation with controlled embeddings."""

    async def test_clear_topic_shift_produces_boundary(self):
        """Text with a clear topic shift should produce a boundary at the expected location."""
        text = (
            "The cat sat on the mat. The cat purred softly. "
            "Quantum physics explains wave-particle duality. "
            "Schrodinger's equation models quantum states."
        )
        doc = RawDocument(content=text)

        # Sentences about cats get one direction, physics gets another
        provider = ControlledEmbeddingProvider(
            {
                "The cat sat on the mat.": [0.9, 0.1, 0.0, 0.0],
                "The cat purred softly.": [0.85, 0.15, 0.0, 0.0],
                "Quantum physics explains wave-particle duality.": [0.0, 0.0, 0.9, 0.1],
                "Schrodinger's equation models quantum states.": [0.0, 0.0, 0.85, 0.15],
            }
        )

        graph = semantic_similarity_segmentation_net(doc, provider)
        updated_graph, fired = await ExecutableGraphOperations.execute_graph(
            graph, stop_after_n_firings=10
        )

        segments = updated_graph.place_named("Segments").tokens
        assert len(segments) == 2, (
            f"Expected 2 segments but got {len(segments)}: {[s.text for s in segments]}"
        )

        # First segment should be about cats, second about physics
        assert "cat" in segments[0].text.lower()
        assert "quantum" in segments[1].text.lower() or "schrodinger" in segments[1].text.lower()

        _segments_are_valid(segments, text, doc.id)

    async def test_single_topic_produces_one_segment(self):
        """Text about a single topic should produce exactly one segment."""
        text = (
            "Python is a programming language. "
            "Python supports multiple paradigms. "
            "Python has a large standard library."
        )
        doc = RawDocument(content=text)

        # All sentences get similar vectors — no topic shift
        provider = ControlledEmbeddingProvider(
            {
                "Python is a programming language.": [0.9, 0.1, 0.0, 0.0],
                "Python supports multiple paradigms.": [0.88, 0.12, 0.0, 0.0],
                "Python has a large standard library.": [0.87, 0.13, 0.0, 0.0],
            }
        )

        graph = semantic_similarity_segmentation_net(doc, provider)
        updated_graph, fired = await ExecutableGraphOperations.execute_graph(
            graph, stop_after_n_firings=10
        )

        segments = updated_graph.place_named("Segments").tokens
        assert len(segments) == 1, f"Expected 1 segment but got {len(segments)}"
        _segments_are_valid(segments, text, doc.id)

    async def test_output_type_is_segment(self):
        """All outputs should be Segment instances with valid spans."""
        text = "First sentence here. Second sentence there."
        doc = RawDocument(content=text)
        provider = MockEmbeddingProvider(dimension=8)

        graph = semantic_similarity_segmentation_net(doc, provider)
        updated_graph, _ = await ExecutableGraphOperations.execute_graph(
            graph, stop_after_n_firings=10
        )

        segments = updated_graph.place_named("Segments").tokens
        assert len(segments) > 0
        for seg in segments:
            assert isinstance(seg, Segment)
            assert seg.span_start >= 0
            assert seg.span_end <= len(text)

    async def test_segments_cover_full_text(self):
        """Segments should be non-overlapping and cover the full text."""
        text = (
            "The weather today is sunny. It will be warm. "
            "Machine learning is transforming technology. Neural networks are powerful."
        )
        doc = RawDocument(content=text)

        # Create a clear topic shift
        provider = ControlledEmbeddingProvider(
            {
                "The weather today is sunny.": [0.9, 0.0, 0.0, 0.0],
                "It will be warm.": [0.85, 0.05, 0.0, 0.0],
                "Machine learning is transforming technology.": [0.0, 0.0, 0.9, 0.0],
                "Neural networks are powerful.": [0.0, 0.0, 0.85, 0.05],
            }
        )

        graph = semantic_similarity_segmentation_net(doc, provider)
        updated_graph, _ = await ExecutableGraphOperations.execute_graph(
            graph, stop_after_n_firings=10
        )

        segments = updated_graph.place_named("Segments").tokens
        _segments_are_valid(segments, text, doc.id)

    async def test_single_sentence_document(self):
        """A document with a single sentence produces one segment."""
        text = "Just one sentence."
        doc = RawDocument(content=text)
        provider = MockEmbeddingProvider(dimension=4)

        graph = semantic_similarity_segmentation_net(doc, provider)
        updated_graph, _ = await ExecutableGraphOperations.execute_graph(
            graph, stop_after_n_firings=10
        )

        segments = updated_graph.place_named("Segments").tokens
        assert len(segments) == 1
        assert segments[0].text == "Just one sentence."


class TestSemanticPetriNetFlow:
    """Test that tokens flow correctly through the Petri net places."""

    async def test_tokens_flow_through_all_places(self):
        """Verify that tokens are produced in each intermediate place."""
        text = "Hello world. Goodbye world."
        doc = RawDocument(content=text)
        provider = MockEmbeddingProvider(dimension=8)

        graph = semantic_similarity_segmentation_net(doc, provider)

        # Initially: RawDocument has 1 token, others empty
        assert len(graph.place_named("RawDocument").tokens) == 1
        assert len(graph.place_named("SentenceList").tokens) == 0
        assert len(graph.place_named("SimilarityScores").tokens) == 0
        assert len(graph.place_named("Segments").tokens) == 0

        # Fire transition 1: split_sentences
        graph, fired = await ExecutableGraphOperations.execute_graph(graph, stop_after_n_firings=1)
        assert fired == 1
        assert len(graph.place_named("RawDocument").tokens) == 0
        assert len(graph.place_named("SentenceList").tokens) == 1
        sentence_list = graph.place_named("SentenceList").tokens[0]
        assert isinstance(sentence_list, SentenceList)
        assert len(sentence_list.sentences) == 2

        # Fire transition 2: compute_similarities
        graph, fired = await ExecutableGraphOperations.execute_graph(graph, stop_after_n_firings=1)
        assert fired == 1
        assert len(graph.place_named("SentenceList").tokens) == 0
        assert len(graph.place_named("SimilarityScores").tokens) == 1
        sim_scores = graph.place_named("SimilarityScores").tokens[0]
        assert isinstance(sim_scores, SimilarityScores)
        assert len(sim_scores.scores) == 1  # 2 sentences -> 1 similarity score

        # Fire transition 3: detect_boundaries_and_form_segments
        graph, fired = await ExecutableGraphOperations.execute_graph(graph, stop_after_n_firings=1)
        assert fired == 1
        assert len(graph.place_named("SimilarityScores").tokens) == 0
        assert len(graph.place_named("Segments").tokens) >= 1

        # No more transitions should fire
        graph, fired = await ExecutableGraphOperations.execute_graph(graph, stop_after_n_firings=1)
        assert fired == 0

    async def test_three_transitions_fire(self):
        """The net should fire exactly 3 transitions for a single document."""
        text = "First. Second. Third."
        doc = RawDocument(content=text)
        provider = MockEmbeddingProvider(dimension=8)

        graph = semantic_similarity_segmentation_net(doc, provider)
        updated_graph, fired = await ExecutableGraphOperations.execute_graph(
            graph, stop_after_n_firings=10
        )

        assert fired == 3


# ========================================================================
# Paragraph split strategy tests
# ========================================================================


class TestParagraphSplitStrategy:
    async def test_splits_on_double_newlines(self):
        """Text with double newlines should be split into paragraph segments."""
        text = "First paragraph here.\n\nSecond paragraph here.\n\nThird paragraph here."
        doc = RawDocument(content=text)

        graph = paragraph_split_segmentation_net(doc)
        updated_graph, fired = await ExecutableGraphOperations.execute_graph(
            graph, stop_after_n_firings=10
        )

        segments = updated_graph.place_named("Segments").tokens
        assert len(segments) == 3
        assert segments[0].text == "First paragraph here."
        assert segments[1].text == "Second paragraph here."
        assert segments[2].text == "Third paragraph here."

    async def test_output_type_is_segment(self):
        """All outputs should be Segment instances."""
        text = "One.\n\nTwo."
        doc = RawDocument(content=text)

        graph = paragraph_split_segmentation_net(doc)
        updated_graph, _ = await ExecutableGraphOperations.execute_graph(
            graph, stop_after_n_firings=10
        )

        segments = updated_graph.place_named("Segments").tokens
        for seg in segments:
            assert isinstance(seg, Segment)

    async def test_segments_non_overlapping_and_cover_text(self):
        """Segments should cover all non-whitespace characters without overlap."""
        text = "Alpha bravo.\n\nCharlie delta.\n\nEcho foxtrot."
        doc = RawDocument(content=text)

        graph = paragraph_split_segmentation_net(doc)
        updated_graph, _ = await ExecutableGraphOperations.execute_graph(
            graph, stop_after_n_firings=10
        )

        segments = updated_graph.place_named("Segments").tokens
        _segments_are_valid(segments, text, doc.id)

    async def test_single_paragraph(self):
        """Text without double newlines produces one segment."""
        text = "Just a single paragraph with no breaks."
        doc = RawDocument(content=text)

        graph = paragraph_split_segmentation_net(doc)
        updated_graph, _ = await ExecutableGraphOperations.execute_graph(
            graph, stop_after_n_firings=10
        )

        segments = updated_graph.place_named("Segments").tokens
        assert len(segments) == 1
        assert segments[0].text == text

    async def test_empty_paragraphs_ignored(self):
        """Empty paragraphs (multiple consecutive newlines) should be ignored."""
        text = "First.\n\n\n\nSecond."
        doc = RawDocument(content=text)

        graph = paragraph_split_segmentation_net(doc)
        updated_graph, _ = await ExecutableGraphOperations.execute_graph(
            graph, stop_after_n_firings=10
        )

        segments = updated_graph.place_named("Segments").tokens
        assert len(segments) == 2
        assert segments[0].text == "First."
        assert segments[1].text == "Second."

    async def test_valid_spans(self):
        """Segment spans should correctly index into the source document."""
        text = "Hello world.\n\nGoodbye world."
        doc = RawDocument(content=text)

        graph = paragraph_split_segmentation_net(doc)
        updated_graph, _ = await ExecutableGraphOperations.execute_graph(
            graph, stop_after_n_firings=10
        )

        segments = updated_graph.place_named("Segments").tokens
        for seg in segments:
            assert text[seg.span_start : seg.span_end] == seg.text


class TestParagraphPetriNetFlow:
    async def test_single_transition_fires(self):
        """The paragraph net has only one transition."""
        text = "A.\n\nB."
        doc = RawDocument(content=text)

        graph = paragraph_split_segmentation_net(doc)
        updated_graph, fired = await ExecutableGraphOperations.execute_graph(
            graph, stop_after_n_firings=10
        )

        assert fired == 1

    async def test_tokens_flow_correctly(self):
        """RawDocument is consumed and Segments are produced."""
        text = "X.\n\nY."
        doc = RawDocument(content=text)

        graph = paragraph_split_segmentation_net(doc)

        # Initially: RawDocument has 1 token
        assert len(graph.place_named("RawDocument").tokens) == 1
        assert len(graph.place_named("Segments").tokens) == 0

        # After execution
        updated_graph, fired = await ExecutableGraphOperations.execute_graph(
            graph, stop_after_n_firings=10
        )

        assert len(updated_graph.place_named("RawDocument").tokens) == 0
        assert len(updated_graph.place_named("Segments").tokens) == 2
