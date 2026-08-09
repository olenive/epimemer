"""Tests for Phase 3: Graph Construction & Value Marking.

Covers:
- Edge creation: correct edge types for each node pair
- Edge creation: edges carry correct src_id and dst_id
- Edge creation: Petri net tokens flow correctly
- Value updates: confidence increases on supporting evidence
- Value updates: novelty increases on contradiction
- Value updates: retrieval reinforcement stamps retrieved_at
- Persist: all nodes and edges stored in storage after persist call
- Versioning: supersede creates new node, marks old as superseded, creates edge
- Versioning: merge marks all sources as merged, creates merged_into edges
- Storage round-trip: store and retrieve complete graph
"""

from datetime import datetime, timezone

import pytest

from petritype.core.executable_graph_components import ExecutableGraphOperations

from epimemer.core.types import (
    EdgeType,
    Fact,
    Inference,
    NodeEdge,
    NodeStatus,
    Segment,
    Topic,
    ValueSignal,
)
from epimemer.pipelines.graph_construction.edge_creation import (
    DecomposedSegment,
    create_edges,
    edge_creation_net,
)
from epimemer.pipelines.graph_construction.versioning import (
    merge_nodes,
    supersede_node,
)
from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.storage.memory import InMemoryStorage


# --- Fixtures ---


@pytest.fixture
def segment() -> Segment:
    return Segment(
        id="seg-1",
        source_id="doc-1",
        text="Machine learning models require large datasets for training.",
        span_start=0,
        span_end=60,
    )


@pytest.fixture
def topic(segment: Segment) -> Topic:
    return Topic(
        id="topic-1",
        content="Machine learning and data requirements",
        source_id=segment.id,
    )


@pytest.fixture
def fact(segment: Segment) -> Fact:
    return Fact(
        id="fact-1",
        content="ML models require large datasets for training.",
        source_id=segment.id,
    )


@pytest.fixture
def inference(segment: Segment) -> Inference:
    return Inference(
        id="inf-1",
        content="Data quality and quantity are key bottlenecks in ML development.",
        source_id=segment.id,
    )


@pytest.fixture
def decomposed(
    segment: Segment,
    topic: Topic,
    fact: Fact,
    inference: Inference,
) -> DecomposedSegment:
    return DecomposedSegment(
        segment=segment,
        topics=[topic],
        facts=[fact],
        inferences=[inference],
    )


@pytest.fixture
def embedding_provider() -> MockEmbeddingProvider:
    return MockEmbeddingProvider(model_id="mock-embed", dimension=8)


# --- Edge Creation Tests ---


class TestEdgeCreation:
    """Test the create_edges function and edge types."""

    def test_segment_to_topic_about_edge(self, decomposed: DecomposedSegment) -> None:
        edges = create_edges(decomposed)
        about_edges = [e for e in edges if e.type == EdgeType.ABOUT]
        assert len(about_edges) == 1
        assert about_edges[0].src_id == decomposed.segment.id
        assert about_edges[0].dst_id == decomposed.topics[0].id

    def test_segment_to_fact_contains_edge(self, decomposed: DecomposedSegment) -> None:
        edges = create_edges(decomposed)
        contains_edges = [e for e in edges if e.type == EdgeType.CONTAINS]
        assert len(contains_edges) == 1
        assert contains_edges[0].src_id == decomposed.segment.id
        assert contains_edges[0].dst_id == decomposed.facts[0].id

    def test_segment_to_inference_implies_edge(self, decomposed: DecomposedSegment) -> None:
        edges = create_edges(decomposed)
        implies_edges = [e for e in edges if e.type == EdgeType.IMPLIES]
        assert len(implies_edges) == 1
        assert implies_edges[0].src_id == decomposed.segment.id
        assert implies_edges[0].dst_id == decomposed.inferences[0].id

    def test_fact_to_topic_supports_edge(self, decomposed: DecomposedSegment) -> None:
        edges = create_edges(decomposed)
        supports_edges = [
            e for e in edges
            if e.type == EdgeType.SUPPORTS and e.src_id == decomposed.facts[0].id and e.dst_id == decomposed.topics[0].id
        ]
        assert len(supports_edges) == 1

    def test_inference_to_topic_abstracts_edge(self, decomposed: DecomposedSegment) -> None:
        edges = create_edges(decomposed)
        abstracts_edges = [e for e in edges if e.type == EdgeType.ABSTRACTS]
        assert len(abstracts_edges) == 1
        assert abstracts_edges[0].src_id == decomposed.inferences[0].id
        assert abstracts_edges[0].dst_id == decomposed.topics[0].id

    def test_fact_to_inference_supports_edge(self, decomposed: DecomposedSegment) -> None:
        edges = create_edges(decomposed)
        supports_edges = [
            e for e in edges
            if e.type == EdgeType.SUPPORTS and e.src_id == decomposed.facts[0].id and e.dst_id == decomposed.inferences[0].id
        ]
        assert len(supports_edges) == 1

    def test_total_edge_count_single_of_each(self, decomposed: DecomposedSegment) -> None:
        """With 1 topic, 1 fact, 1 inference, we expect exactly 6 edges."""
        edges = create_edges(decomposed)
        # segment->topic (1) + segment->fact (1) + segment->inference (1)
        # + fact->topic (1) + inference->topic (1) + fact->inference (1)
        assert len(edges) == 6

    def test_edge_count_multiple_nodes(self, segment: Segment) -> None:
        """With 2 topics, 2 facts, 1 inference, verify combinatorial edge count."""
        topics = [
            Topic(id="t1", content="Topic A", source_id=segment.id),
            Topic(id="t2", content="Topic B", source_id=segment.id),
        ]
        facts = [
            Fact(id="f1", content="Fact A", source_id=segment.id),
            Fact(id="f2", content="Fact B", source_id=segment.id),
        ]
        inferences = [
            Inference(id="i1", content="Inference A", source_id=segment.id),
        ]
        decomposed = DecomposedSegment(
            segment=segment,
            topics=topics,
            facts=facts,
            inferences=inferences,
        )
        edges = create_edges(decomposed)

        # segment->topic: 2, segment->fact: 2, segment->inference: 1
        # fact->topic: 2*2=4, inference->topic: 1*2=2, fact->inference: 2*1=2
        # Total = 2 + 2 + 1 + 4 + 2 + 2 = 13
        assert len(edges) == 13

    def test_edges_with_empty_nodes(self, segment: Segment) -> None:
        """Decomposed segment with no extracted nodes produces no edges."""
        decomposed = DecomposedSegment(segment=segment)
        edges = create_edges(decomposed)
        assert len(edges) == 0

    def test_all_edges_have_unique_ids(self, decomposed: DecomposedSegment) -> None:
        edges = create_edges(decomposed)
        ids = [e.id for e in edges]
        assert len(ids) == len(set(ids))

    def test_all_edges_are_node_edge_type(self, decomposed: DecomposedSegment) -> None:
        edges = create_edges(decomposed)
        for edge in edges:
            assert isinstance(edge, NodeEdge)


class TestEdgeCreationPetriNet:
    """Test the edge creation Petri net flow."""

    @pytest.mark.asyncio
    async def test_petri_net_produces_edges(self, decomposed: DecomposedSegment) -> None:
        graph = edge_creation_net(decomposed)
        graph, transitions_fired = await ExecutableGraphOperations.execute_graph(
            graph, stop_after_n_firings=10,
        )
        assert transitions_fired == 1

        edges_place = graph.place_named("Edges")
        assert edges_place is not None
        assert len(edges_place.tokens) == 6

    @pytest.mark.asyncio
    async def test_petri_net_input_consumed(self, decomposed: DecomposedSegment) -> None:
        graph = edge_creation_net(decomposed)
        graph, _ = await ExecutableGraphOperations.execute_graph(
            graph, stop_after_n_firings=10,
        )

        input_place = graph.place_named("DecomposedSegments")
        assert input_place is not None
        assert len(input_place.tokens) == 0

    @pytest.mark.asyncio
    async def test_petri_net_edge_types_correct(self, decomposed: DecomposedSegment) -> None:
        graph = edge_creation_net(decomposed)
        graph, _ = await ExecutableGraphOperations.execute_graph(
            graph, stop_after_n_firings=10,
        )

        edges_place = graph.place_named("Edges")
        edges = edges_place.tokens
        edge_types = {e.type for e in edges}
        assert EdgeType.ABOUT in edge_types
        assert EdgeType.CONTAINS in edge_types
        assert EdgeType.IMPLIES in edge_types
        assert EdgeType.SUPPORTS in edge_types
        assert EdgeType.ABSTRACTS in edge_types


# --- Versioning Tests ---


class TestVersioning:
    """Test node supersession and merging."""

    @pytest.mark.asyncio
    async def test_supersede_marks_old_as_superseded(
        self, storage: InMemoryStorage, embedding_provider: MockEmbeddingProvider
    ) -> None:
        old_topic = Topic(
            id="old-topic",
            content="Original topic",
            source_id="seg-1",
        )
        new_topic = Topic(
            id="new-topic",
            content="Updated topic",
            source_id="seg-2",
        )
        await storage.store_node(old_topic)

        await supersede_node(old_topic, new_topic, storage, embedding_provider)

        stored_old = await storage.get_node("old-topic")
        assert stored_old is not None
        assert stored_old.status == NodeStatus.SUPERSEDED
        assert stored_old.superseded_at is not None

    @pytest.mark.asyncio
    async def test_supersede_stores_new_node(
        self, storage: InMemoryStorage, embedding_provider: MockEmbeddingProvider
    ) -> None:
        old_topic = Topic(
            id="old-topic-2",
            content="Original topic",
            source_id="seg-1",
        )
        new_topic = Topic(
            id="new-topic-2",
            content="Updated topic",
            source_id="seg-2",
        )
        await storage.store_node(old_topic)

        await supersede_node(old_topic, new_topic, storage, embedding_provider)

        stored_new = await storage.get_node("new-topic-2")
        assert stored_new is not None
        assert stored_new.status == NodeStatus.ACTIVE

    @pytest.mark.asyncio
    async def test_supersede_creates_edge(
        self, storage: InMemoryStorage, embedding_provider: MockEmbeddingProvider
    ) -> None:
        old_topic = Topic(
            id="old-topic-3",
            content="Original topic",
            source_id="seg-1",
        )
        new_topic = Topic(
            id="new-topic-3",
            content="Updated topic",
            source_id="seg-2",
        )
        await storage.store_node(old_topic)

        edge = await supersede_node(old_topic, new_topic, storage, embedding_provider)

        assert edge.src_id == "old-topic-3"
        assert edge.dst_id == "new-topic-3"
        assert edge.type == EdgeType.SUPERSEDED_BY

    @pytest.mark.asyncio
    async def test_supersede_edge_stored_in_storage(
        self, storage: InMemoryStorage, embedding_provider: MockEmbeddingProvider
    ) -> None:
        old_topic = Topic(
            id="old-topic-4",
            content="Original",
            source_id="seg-1",
        )
        new_topic = Topic(
            id="new-topic-4",
            content="Updated",
            source_id="seg-2",
        )
        await storage.store_node(old_topic)

        await supersede_node(old_topic, new_topic, storage, embedding_provider)

        edges = await storage.get_edges_from("old-topic-4")
        assert len(edges) == 1
        assert edges[0].type == EdgeType.SUPERSEDED_BY

    @pytest.mark.asyncio
    async def test_merge_marks_all_sources_as_merged(
        self, storage: InMemoryStorage, embedding_provider: MockEmbeddingProvider
    ) -> None:
        t1 = Topic(id="merge-src-1", content="Topic A", source_id="seg-1")
        t2 = Topic(id="merge-src-2", content="Topic B", source_id="seg-2")
        merged = Topic(id="merge-dst", content="Merged topic", source_id="seg-1")
        await storage.store_node(t1)
        await storage.store_node(t2)

        await merge_nodes([t1, t2], merged, storage, embedding_provider)

        stored_1 = await storage.get_node("merge-src-1")
        stored_2 = await storage.get_node("merge-src-2")
        assert stored_1 is not None
        assert stored_2 is not None
        assert stored_1.status == NodeStatus.MERGED
        assert stored_2.status == NodeStatus.MERGED
        assert stored_1.superseded_at is not None
        assert stored_2.superseded_at is not None

    @pytest.mark.asyncio
    async def test_merge_stores_merged_node(
        self, storage: InMemoryStorage, embedding_provider: MockEmbeddingProvider
    ) -> None:
        t1 = Topic(id="merge-src-3", content="Topic A", source_id="seg-1")
        t2 = Topic(id="merge-src-4", content="Topic B", source_id="seg-2")
        merged = Topic(id="merge-dst-2", content="Merged topic", source_id="seg-1")
        await storage.store_node(t1)
        await storage.store_node(t2)

        await merge_nodes([t1, t2], merged, storage, embedding_provider)

        stored = await storage.get_node("merge-dst-2")
        assert stored is not None
        assert stored.status == NodeStatus.ACTIVE

    @pytest.mark.asyncio
    async def test_merge_creates_edges(
        self, storage: InMemoryStorage, embedding_provider: MockEmbeddingProvider
    ) -> None:
        t1 = Topic(id="merge-src-5", content="Topic A", source_id="seg-1")
        t2 = Topic(id="merge-src-6", content="Topic B", source_id="seg-2")
        merged = Topic(id="merge-dst-3", content="Merged topic", source_id="seg-1")
        await storage.store_node(t1)
        await storage.store_node(t2)

        edges = await merge_nodes([t1, t2], merged, storage, embedding_provider)

        assert len(edges) == 2
        for edge in edges:
            assert edge.type == EdgeType.MERGED_INTO
            assert edge.dst_id == "merge-dst-3"

        src_ids = {e.src_id for e in edges}
        assert src_ids == {"merge-src-5", "merge-src-6"}

    @pytest.mark.asyncio
    async def test_merge_edges_stored_in_storage(
        self, storage: InMemoryStorage, embedding_provider: MockEmbeddingProvider
    ) -> None:
        t1 = Topic(id="merge-src-7", content="Topic A", source_id="seg-1")
        t2 = Topic(id="merge-src-8", content="Topic B", source_id="seg-2")
        merged = Topic(id="merge-dst-4", content="Merged topic", source_id="seg-1")
        await storage.store_node(t1)
        await storage.store_node(t2)

        await merge_nodes([t1, t2], merged, storage, embedding_provider)

        edges_1 = await storage.get_edges_from("merge-src-7")
        edges_2 = await storage.get_edges_from("merge-src-8")
        assert len(edges_1) == 1
        assert len(edges_2) == 1
        assert edges_1[0].type == EdgeType.MERGED_INTO
        assert edges_2[0].type == EdgeType.MERGED_INTO

    @pytest.mark.asyncio
    async def test_merge_embeds_merged_node(
        self, storage: InMemoryStorage, embedding_provider: MockEmbeddingProvider
    ) -> None:
        """The merged node must be embedded so search can return it."""
        t1 = Topic(id="merge-emb-1", content="Topic A", source_id="seg-1")
        t2 = Topic(id="merge-emb-2", content="Topic B", source_id="seg-2")
        merged = Topic(id="merge-emb-dst", content="Topic A and B", source_id="seg-1")
        await storage.store_node(t1)
        await storage.store_node(t2)

        await merge_nodes([t1, t2], merged, storage, embedding_provider)

        embeddings = await storage.get_embeddings_for_item("merge-emb-dst")
        assert len(embeddings) >= 1

    @pytest.mark.asyncio
    async def test_merge_migrates_edges_dedupes_and_drops_self_loops(
        self, storage: InMemoryStorage, embedding_provider: MockEmbeddingProvider
    ) -> None:
        """Sources' edges move onto the merged node; duplicates and self-loops go."""
        t1 = Topic(id="merge-mig-1", content="Topic A", source_id="seg-1")
        t2 = Topic(id="merge-mig-2", content="Topic B", source_id="seg-2")
        fact = Fact(id="merge-mig-fact", content="shared evidence", source_id="seg-1")
        merged = Topic(id="merge-mig-dst", content="Topic A and B", source_id="seg-1")
        for node in (t1, t2, fact):
            await storage.store_node(node)
        # The same fact supports both sources → collapses to one edge after merge.
        await storage.store_edge(
            NodeEdge(src_id=fact.id, dst_id=t1.id, type=EdgeType.SUPPORTS)
        )
        await storage.store_edge(
            NodeEdge(src_id=fact.id, dst_id=t2.id, type=EdgeType.SUPPORTS)
        )
        # An edge between the two sources becomes a self-loop after the merge.
        await storage.store_edge(
            NodeEdge(src_id=t1.id, dst_id=t2.id, type=EdgeType.SUPPORTS)
        )

        await merge_nodes([t1, t2], merged, storage, embedding_provider)

        # The two shared supports edges collapse into a single edge.
        supports_into_merged = await storage.get_edges_to(
            "merge-mig-dst", edge_type=EdgeType.SUPPORTS
        )
        assert len(supports_into_merged) == 1
        assert supports_into_merged[0].src_id == fact.id
        assert await storage.get_edges_to("merge-mig-1", edge_type=EdgeType.SUPPORTS) == []
        assert await storage.get_edges_to("merge-mig-2", edge_type=EdgeType.SUPPORTS) == []

        # The source-to-source edge did not survive as a self-loop.
        out = await storage.get_edges_from("merge-mig-dst")
        assert [e for e in out if e.dst_id == "merge-mig-dst"] == []


# --- Storage Round-Trip Tests ---


class TestStorageRoundTrip:
    """Test complete graph storage and retrieval."""

    @pytest.mark.asyncio
    async def test_full_graph_round_trip(self, storage: InMemoryStorage) -> None:
        """Store a complete decomposed segment with edges, then verify retrieval."""
        segment = Segment(
            id="rt-seg",
            source_id="doc-rt",
            text="Round trip test text.",
            span_start=0,
            span_end=21,
        )
        topic = Topic(id="rt-topic", content="Test topic", source_id=segment.id)
        fact = Fact(id="rt-fact", content="Test fact", source_id=segment.id)
        inference = Inference(id="rt-inf", content="Test inference", source_id=segment.id)

        decomposed = DecomposedSegment(
            segment=segment,
            topics=[topic],
            facts=[fact],
            inferences=[inference],
        )
        edges = create_edges(decomposed)
        await storage.store_segment(decomposed.segment)
        for node in [*decomposed.topics, *decomposed.facts, *decomposed.inferences]:
            await storage.store_node(node)
        for edge in edges:
            await storage.store_edge(edge)

        # Verify segment
        stored_segments = await storage.get_segments_for_document("doc-rt")
        assert len(stored_segments) == 1

        # Verify all nodes
        stored_topic = await storage.get_node("rt-topic")
        stored_fact = await storage.get_node("rt-fact")
        stored_inf = await storage.get_node("rt-inf")
        assert stored_topic is not None
        assert stored_fact is not None
        assert stored_inf is not None

        # Verify edges
        seg_edges = await storage.get_edges_from("rt-seg")
        assert len(seg_edges) == 3

        fact_edges = await storage.get_edges_from("rt-fact")
        # fact -> topic (SUPPORTS) + fact -> inference (SUPPORTS)
        assert len(fact_edges) == 2

        inf_edges = await storage.get_edges_from("rt-inf")
        # inference -> topic (ABSTRACTS)
        assert len(inf_edges) == 1

    @pytest.mark.asyncio
    async def test_round_trip_with_versioning(
        self, storage: InMemoryStorage, embedding_provider: MockEmbeddingProvider
    ) -> None:
        """Store nodes, supersede one, verify the full history is retrievable."""
        original = Topic(
            id="rt-v-orig",
            content="Original topic",
            source_id="seg-1",
        )
        await storage.store_node(original)

        updated = Topic(
            id="rt-v-new",
            content="Updated topic",
            source_id="seg-1",
        )
        edge = await supersede_node(original, updated, storage, embedding_provider)

        # Original is superseded
        stored_orig = await storage.get_node("rt-v-orig")
        assert stored_orig.status == NodeStatus.SUPERSEDED

        # New is active
        stored_new = await storage.get_node("rt-v-new")
        assert stored_new.status == NodeStatus.ACTIVE

        # Edge links them
        stored_edge = await storage.get_edges_from("rt-v-orig")
        assert len(stored_edge) == 1
        assert stored_edge[0].dst_id == "rt-v-new"
        assert stored_edge[0].type == EdgeType.SUPERSEDED_BY
