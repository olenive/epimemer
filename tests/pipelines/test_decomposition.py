"""Tests for Phase 2: Decomposition & Topic Assignment.

Covers:
- Decomposition Petri net: mock LLM returns valid Topic, Fact, Inference models
- Decomposition: all three types end up in correct output places
- Decomposition: provenance (source_id, extraction_method) is set correctly
- Topic assignment: segment similar to existing topic links to it
- Topic assignment: segment dissimilar triggers LLM fallback and creates new topic
- Topic assignment: threshold edge cases
- Petri net flow: correct number of transitions fire, tokens in correct places
"""

import pytest

from petritype.core.executable_graph_components import ExecutableGraphOperations

from epimemer.core.types import (
    EmbeddingRecord,
    Fact,
    Inference,
    NodeType,
    Segment,
    Topic,
    ValueSignal,
)
from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.llm.mock import MockDecompositionProvider
from epimemer.pipelines.decomposition.llm_decomposition import (
    DecompositionResult,
    distribute_decomposition_result,
    extract_all,
    llm_decomposition_net,
)
from epimemer.pipelines.decomposition.topic_assignment import assign_topics
from epimemer.storage.memory import InMemoryStorage


# --- Fixtures ---


@pytest.fixture
def segment() -> Segment:
    return Segment(
        source_id="doc-1",
        text="Machine learning models require large datasets for training and evaluation.",
        span_start=0,
        span_end=73,
    )


@pytest.fixture
def decomposition_provider() -> MockDecompositionProvider:
    return MockDecompositionProvider()


@pytest.fixture
def embedding_provider() -> MockEmbeddingProvider:
    return MockEmbeddingProvider(dimension=8)


@pytest.fixture
def storage() -> InMemoryStorage:
    return InMemoryStorage()


# ========================================================================
# Decomposition Petri net tests
# ========================================================================


class TestDecompositionExtraction:
    """Test the extract_all transition function directly."""

    async def test_extract_all_returns_decomposition_result(
        self, segment, decomposition_provider
    ):
        """extract_all should return a DecompositionResult with all three lists."""
        result = await extract_all(segment, decomposition_provider)
        assert isinstance(result, DecompositionResult)
        assert len(result.topics) >= 1
        assert len(result.facts) >= 1
        assert len(result.inferences) >= 1

    async def test_extract_all_types_are_correct(
        self, segment, decomposition_provider
    ):
        """Each item in the result should be of the correct type."""
        result = await extract_all(segment, decomposition_provider)
        assert all(isinstance(t, Topic) for t in result.topics)
        assert all(isinstance(f, Fact) for f in result.facts)
        assert all(isinstance(i, Inference) for i in result.inferences)

    async def test_extract_all_provenance_source_id(
        self, segment, decomposition_provider
    ):
        """All extracted nodes should carry the source segment's id."""
        result = await extract_all(segment, decomposition_provider)
        for topic in result.topics:
            assert topic.source_id == segment.id
        for fact in result.facts:
            assert fact.source_id == segment.id
        for inference in result.inferences:
            assert inference.source_id == segment.id

    async def test_extract_all_provenance_extraction_method(
        self, segment, decomposition_provider
    ):
        """All extracted nodes should record their extraction method."""
        result = await extract_all(segment, decomposition_provider)
        for topic in result.topics:
            assert topic.extraction_method == "mock"
        for fact in result.facts:
            assert fact.extraction_method == "mock"
        for inference in result.inferences:
            assert inference.extraction_method == "mock"


class TestDistributeDecompositionResult:
    """Test the output distribution function."""

    def test_distribution_routes_to_correct_places(self):
        """The distribution function should map each type to its named place."""
        topics = [Topic(content="topic1", source_id="s1")]
        facts = [Fact(content="fact1", source_id="s1")]
        inferences = [Inference(content="inf1", source_id="s1")]
        result = DecompositionResult(
            topics=topics, facts=facts, inferences=inferences
        )
        distributed = distribute_decomposition_result(result)
        assert len(distributed["Topics"]) == 1
        assert len(distributed["Facts"]) == 1
        assert len(distributed["Inferences"]) == 1
        assert distributed["Topics"][0].id == topics[0].id
        assert distributed["Facts"][0].id == facts[0].id
        assert distributed["Inferences"][0].id == inferences[0].id

    def test_distribution_with_empty_lists(self):
        """Empty extraction results should produce empty lists in distribution."""
        result = DecompositionResult()
        distributed = distribute_decomposition_result(result)
        assert distributed["Topics"] == []
        assert distributed["Facts"] == []
        assert distributed["Inferences"] == []


class TestDecompositionPetriNet:
    """Test the full decomposition Petri net execution."""

    async def test_all_types_in_correct_places(
        self, segment, decomposition_provider
    ):
        """After execution, topics/facts/inferences should be in their typed places."""
        graph = llm_decomposition_net(segment, decomposition_provider)
        updated_graph, fired = await ExecutableGraphOperations.execute_graph(
            graph, max_transitions=10
        )

        topics = updated_graph.place_named("Topics").tokens
        facts = updated_graph.place_named("Facts").tokens
        inferences = updated_graph.place_named("Inferences").tokens

        assert len(topics) >= 1
        assert len(facts) >= 1
        assert len(inferences) >= 1
        assert all(isinstance(t, Topic) for t in topics)
        assert all(isinstance(f, Fact) for f in facts)
        assert all(isinstance(i, Inference) for i in inferences)

    async def test_single_transition_fires(
        self, segment, decomposition_provider
    ):
        """The decomposition net should fire exactly 1 transition."""
        graph = llm_decomposition_net(segment, decomposition_provider)
        updated_graph, fired = await ExecutableGraphOperations.execute_graph(
            graph, max_transitions=10
        )
        assert fired == 1

    async def test_segment_consumed(self, segment, decomposition_provider):
        """After execution, the Segments place should be empty."""
        graph = llm_decomposition_net(segment, decomposition_provider)
        updated_graph, fired = await ExecutableGraphOperations.execute_graph(
            graph, max_transitions=10
        )
        assert len(updated_graph.place_named("Segments").tokens) == 0

    async def test_provenance_in_petri_net_output(
        self, segment, decomposition_provider
    ):
        """Nodes produced through the Petri net should carry correct provenance."""
        graph = llm_decomposition_net(segment, decomposition_provider)
        updated_graph, _ = await ExecutableGraphOperations.execute_graph(
            graph, max_transitions=10
        )

        for topic in updated_graph.place_named("Topics").tokens:
            assert topic.source_id == segment.id
            assert topic.extraction_method == "mock"
        for fact in updated_graph.place_named("Facts").tokens:
            assert fact.source_id == segment.id
            assert fact.extraction_method == "mock"
        for inference in updated_graph.place_named("Inferences").tokens:
            assert inference.source_id == segment.id
            assert inference.extraction_method == "mock"

    async def test_no_more_transitions_after_execution(
        self, segment, decomposition_provider
    ):
        """After full execution, no more transitions should be enabled."""
        graph = llm_decomposition_net(segment, decomposition_provider)
        updated_graph, _ = await ExecutableGraphOperations.execute_graph(
            graph, max_transitions=10
        )
        # Try firing again — should fire 0
        updated_graph, fired = await ExecutableGraphOperations.execute_graph(
            updated_graph, max_transitions=10
        )
        assert fired == 0


class TestDecompositionPetriNetFlow:
    """Test step-by-step token flow through the Petri net."""

    async def test_initial_state(self, segment, decomposition_provider):
        """Initially, only the Segments place should have a token."""
        graph = llm_decomposition_net(segment, decomposition_provider)
        assert len(graph.place_named("Segments").tokens) == 1
        assert len(graph.place_named("Topics").tokens) == 0
        assert len(graph.place_named("Facts").tokens) == 0
        assert len(graph.place_named("Inferences").tokens) == 0

    async def test_after_one_step(self, segment, decomposition_provider):
        """After one transition, segment is consumed and all output places filled."""
        graph = llm_decomposition_net(segment, decomposition_provider)
        updated_graph, fired = await ExecutableGraphOperations.execute_graph(
            graph, max_transitions=1
        )
        assert fired == 1
        assert len(updated_graph.place_named("Segments").tokens) == 0
        assert len(updated_graph.place_named("Topics").tokens) >= 1
        assert len(updated_graph.place_named("Facts").tokens) >= 1
        assert len(updated_graph.place_named("Inferences").tokens) >= 1


# ========================================================================
# Topic assignment tests
# ========================================================================


class ControlledEmbeddingProvider:
    """Embedding provider that returns controlled vectors for testing topic matching.

    Maps specific texts to predefined vectors so we can control cosine similarity.
    """

    def __init__(
        self,
        vector_map: dict[str, list[float]],
        default_vector: list[float] | None = None,
    ):
        self._vector_map = vector_map
        self._default = default_vector or [0.1] * 8
        self._model_id = "controlled-test"
        self._dimension = len(self._default)

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def dimension(self) -> int:
        return self._dimension

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [
            self._vector_map.get(text, self._default) for text in texts
        ]


async def _prepopulate_storage_with_topic(
    storage: InMemoryStorage,
    topic: Topic,
    vector: list[float],
    model_id: str,
) -> None:
    """Store a topic and its embedding in the storage backend."""
    await storage.store_node(topic)
    embedding = EmbeddingRecord(
        item_id=topic.id,
        model_id=model_id,
        vector=vector,
    )
    await storage.store_embedding(embedding)


class TestTopicAssignmentExistingMatch:
    """Test topic assignment when segment matches an existing topic."""

    async def test_similar_segment_matches_existing_topic(self):
        """A segment similar to an existing topic should return that topic."""
        existing_topic = Topic(
            content="Discussion about machine learning training",
            source_id="prev-segment",
        )
        # Both the segment and the existing topic get nearly identical vectors
        topic_vector = [0.9, 0.1, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        segment_vector = [0.88, 0.12, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]

        segment = Segment(
            source_id="doc-1",
            text="ML models need large training sets",
            span_start=0,
            span_end=35,
        )

        provider = ControlledEmbeddingProvider({
            segment.text: segment_vector,
            existing_topic.content: topic_vector,
        })

        storage = InMemoryStorage()
        await _prepopulate_storage_with_topic(
            storage, existing_topic, topic_vector, provider.model_id
        )

        result = await assign_topics(
            segment=segment,
            embedding_provider=provider,
            decomposition_provider=MockDecompositionProvider(),
            storage=storage,
            similarity_threshold=0.75,
        )

        assert len(result) >= 1
        assert any(t.id == existing_topic.id for t in result)

    async def test_matched_topic_is_not_stored_again(self):
        """Matching an existing topic should not create a new one in storage."""
        existing_topic = Topic(
            content="Existing topic about data science",
            source_id="prev-segment",
        )
        topic_vector = [0.9, 0.1, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        segment_vector = [0.88, 0.12, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]

        segment = Segment(
            source_id="doc-1",
            text="Data science techniques and methods",
            span_start=0,
            span_end=36,
        )

        provider = ControlledEmbeddingProvider({
            segment.text: segment_vector,
        })

        storage = InMemoryStorage()
        await _prepopulate_storage_with_topic(
            storage, existing_topic, topic_vector, provider.model_id
        )

        initial_node_count = len(storage.nodes)

        await assign_topics(
            segment=segment,
            embedding_provider=provider,
            decomposition_provider=MockDecompositionProvider(),
            storage=storage,
            similarity_threshold=0.75,
        )

        # No new nodes should have been stored
        assert len(storage.nodes) == initial_node_count


class TestTopicAssignmentLLMFallback:
    """Test topic assignment when no existing topic matches."""

    async def test_dissimilar_segment_creates_new_topic(self):
        """A segment dissimilar to all existing topics should trigger LLM fallback."""
        existing_topic = Topic(
            content="Discussion about cooking recipes",
            source_id="prev-segment",
        )
        # Orthogonal vectors => cosine similarity near 0
        topic_vector = [0.9, 0.1, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        segment_vector = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.1, 0.9]

        segment = Segment(
            source_id="doc-1",
            text="Quantum computing breakthroughs in 2025",
            span_start=0,
            span_end=39,
        )

        provider = ControlledEmbeddingProvider({
            segment.text: segment_vector,
        })

        storage = InMemoryStorage()
        await _prepopulate_storage_with_topic(
            storage, existing_topic, topic_vector, provider.model_id
        )

        result = await assign_topics(
            segment=segment,
            embedding_provider=provider,
            decomposition_provider=MockDecompositionProvider(),
            storage=storage,
            similarity_threshold=0.75,
        )

        assert len(result) >= 1
        # The result should be a new topic (not the existing one)
        assert all(t.id != existing_topic.id for t in result)

    async def test_new_topic_stored_in_storage(self):
        """LLM-generated topics should be stored in the storage backend."""
        segment = Segment(
            source_id="doc-1",
            text="A completely novel subject matter",
            span_start=0,
            span_end=33,
        )

        provider = MockEmbeddingProvider(dimension=8)
        storage = InMemoryStorage()
        decomposition_provider = MockDecompositionProvider()

        result = await assign_topics(
            segment=segment,
            embedding_provider=provider,
            decomposition_provider=decomposition_provider,
            storage=storage,
            similarity_threshold=0.75,
        )

        # New topics should be in storage
        for topic in result:
            stored = await storage.get_node(topic.id)
            assert stored is not None
            assert isinstance(stored, Topic)

    async def test_new_topic_embedding_stored(self):
        """LLM-generated topics should have their embeddings stored for future matching."""
        segment = Segment(
            source_id="doc-1",
            text="Something entirely new",
            span_start=0,
            span_end=22,
        )

        provider = MockEmbeddingProvider(dimension=8)
        storage = InMemoryStorage()

        result = await assign_topics(
            segment=segment,
            embedding_provider=provider,
            decomposition_provider=MockDecompositionProvider(),
            storage=storage,
            similarity_threshold=0.75,
        )

        # Each new topic should have an embedding stored
        for topic in result:
            embeddings = await storage.get_embeddings_for_item(
                topic.id, provider.model_id
            )
            assert len(embeddings) >= 1

    async def test_empty_storage_triggers_llm(self):
        """With no existing topics, LLM fallback should always be used."""
        segment = Segment(
            source_id="doc-1",
            text="First ever segment in the system",
            span_start=0,
            span_end=32,
        )

        provider = MockEmbeddingProvider(dimension=8)
        storage = InMemoryStorage()

        result = await assign_topics(
            segment=segment,
            embedding_provider=provider,
            decomposition_provider=MockDecompositionProvider(),
            storage=storage,
            similarity_threshold=0.75,
        )

        assert len(result) >= 1
        assert all(isinstance(t, Topic) for t in result)


class TestTopicAssignmentThresholdEdgeCases:
    """Test threshold edge cases for topic matching."""

    async def test_similarity_just_above_threshold_matches(self):
        """A similarity score just above the threshold should match."""
        existing_topic = Topic(
            content="Existing topic",
            source_id="prev-segment",
        )
        # Vectors with cosine similarity ~ 0.76 (just above 0.75)
        # Use unit vectors: [cos(theta), sin(theta)] where theta gives similarity
        # cos(theta) = 0.76 => theta ~ 0.708 rad
        # vec1 = [1, 0, 0, ...], vec2 = [0.76, 0.65, 0, ...]
        topic_vector = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        segment_vector = [0.76, 0.65, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]

        segment = Segment(
            source_id="doc-1",
            text="Borderline similar text",
            span_start=0,
            span_end=23,
        )

        provider = ControlledEmbeddingProvider({
            segment.text: segment_vector,
        })

        storage = InMemoryStorage()
        await _prepopulate_storage_with_topic(
            storage, existing_topic, topic_vector, provider.model_id
        )

        result = await assign_topics(
            segment=segment,
            embedding_provider=provider,
            decomposition_provider=MockDecompositionProvider(),
            storage=storage,
            similarity_threshold=0.75,
        )

        # Should match existing topic
        assert any(t.id == existing_topic.id for t in result)

    async def test_similarity_just_below_threshold_creates_new(self):
        """A similarity score just below the threshold should not match."""
        existing_topic = Topic(
            content="Existing topic",
            source_id="prev-segment",
        )
        # Vectors with cosine similarity ~ 0.74 (just below 0.75)
        topic_vector = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        segment_vector = [0.74, 0.67, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]

        segment = Segment(
            source_id="doc-1",
            text="Borderline dissimilar text",
            span_start=0,
            span_end=25,
        )

        provider = ControlledEmbeddingProvider({
            segment.text: segment_vector,
        })

        storage = InMemoryStorage()
        await _prepopulate_storage_with_topic(
            storage, existing_topic, topic_vector, provider.model_id
        )

        result = await assign_topics(
            segment=segment,
            embedding_provider=provider,
            decomposition_provider=MockDecompositionProvider(),
            storage=storage,
            similarity_threshold=0.75,
        )

        # Should NOT match existing topic — should be new
        assert all(t.id != existing_topic.id for t in result)

    async def test_exact_threshold_matches(self):
        """Similarity exactly at the threshold should match (>=)."""
        existing_topic = Topic(
            content="Threshold test topic",
            source_id="prev-segment",
        )
        # Use identical vectors for similarity = 1.0, and set threshold to 1.0
        topic_vector = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        segment_vector = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]

        segment = Segment(
            source_id="doc-1",
            text="Exact match text",
            span_start=0,
            span_end=16,
        )

        provider = ControlledEmbeddingProvider({
            segment.text: segment_vector,
        })

        storage = InMemoryStorage()
        await _prepopulate_storage_with_topic(
            storage, existing_topic, topic_vector, provider.model_id
        )

        result = await assign_topics(
            segment=segment,
            embedding_provider=provider,
            decomposition_provider=MockDecompositionProvider(),
            storage=storage,
            similarity_threshold=1.0,
        )

        assert any(t.id == existing_topic.id for t in result)

    async def test_custom_threshold(self):
        """A lower threshold should match more broadly."""
        existing_topic = Topic(
            content="Broadly matching topic",
            source_id="prev-segment",
        )
        topic_vector = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        # This gives cosine similarity ~ 0.53 (below 0.75 but above 0.5)
        segment_vector = [0.53, 0.85, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]

        segment = Segment(
            source_id="doc-1",
            text="Loosely related text",
            span_start=0,
            span_end=20,
        )

        provider = ControlledEmbeddingProvider({
            segment.text: segment_vector,
        })

        storage = InMemoryStorage()
        await _prepopulate_storage_with_topic(
            storage, existing_topic, topic_vector, provider.model_id
        )

        # With threshold=0.75, should NOT match
        result_high = await assign_topics(
            segment=segment,
            embedding_provider=provider,
            decomposition_provider=MockDecompositionProvider(),
            storage=storage,
            similarity_threshold=0.75,
        )
        assert all(t.id != existing_topic.id for t in result_high)

        # With threshold=0.5, should match
        result_low = await assign_topics(
            segment=segment,
            embedding_provider=provider,
            decomposition_provider=MockDecompositionProvider(),
            storage=storage,
            similarity_threshold=0.5,
        )
        assert any(t.id == existing_topic.id for t in result_low)


class TestTopicAssignmentValueSignals:
    """Test value signal handling in topic assignment."""

    async def test_new_topic_has_high_novelty(self):
        """New topics created via LLM fallback should have novelty=1.0."""
        segment = Segment(
            source_id="doc-1",
            text="Something completely novel",
            span_start=0,
            span_end=26,
        )

        provider = MockEmbeddingProvider(dimension=8)
        storage = InMemoryStorage()

        result = await assign_topics(
            segment=segment,
            embedding_provider=provider,
            decomposition_provider=MockDecompositionProvider(),
            storage=storage,
            similarity_threshold=0.75,
        )

        for topic in result:
            assert topic.value.novelty == 1.0

    async def test_matched_topic_has_reduced_novelty(self):
        """Matched existing topics should have reduced novelty."""
        existing_topic = Topic(
            content="Known topic with high novelty",
            source_id="prev-segment",
            value=ValueSignal(novelty=1.0, confidence=0.5, relevance=0.5),
        )
        topic_vector = [0.9, 0.1, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        segment_vector = [0.88, 0.12, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]

        segment = Segment(
            source_id="doc-1",
            text="Text matching the known topic",
            span_start=0,
            span_end=29,
        )

        provider = ControlledEmbeddingProvider({
            segment.text: segment_vector,
        })

        storage = InMemoryStorage()
        await _prepopulate_storage_with_topic(
            storage, existing_topic, topic_vector, provider.model_id
        )

        result = await assign_topics(
            segment=segment,
            embedding_provider=provider,
            decomposition_provider=MockDecompositionProvider(),
            storage=storage,
            similarity_threshold=0.75,
        )

        matched = [t for t in result if t.id == existing_topic.id]
        assert len(matched) == 1
        assert matched[0].value.novelty < 1.0
