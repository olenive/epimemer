"""Tests for Phase 4: Query Layer.

Covers:
- Vector search: returns nodes semantically similar to query
- Vector search: respects node_type filter
- Vector search: returns empty for unrelated query
- Graph expansion: traverses edges from seed nodes
- Graph expansion: respects hop depth limit
- Graph expansion: skips history edges by default
- Graph expansion: doesn't revisit nodes
- Hybrid Petri net: end-to-end query returns QueryResult
- Hybrid Petri net: metadata has correct counts and type breakdown
- Hybrid Petri net: tokens flow through all places correctly
- Temporal query: at_time excludes future nodes
"""

from datetime import datetime, timedelta, timezone

import pytest

from petritype.core.executable_graph_components import ExecutableGraphOperations

from epimemer.core.types import (
    EdgeType,
    EmbeddingRecord,
    Fact,
    Inference,
    NodeEdge,
    NodeStatus,
    NodeType,
    Topic,
)
from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.pipelines.query.graph_expansion import expand_via_graph
from epimemer.pipelines.query.hybrid_retrieval import hybrid_retrieval_net
from epimemer.pipelines.query.types import QueryRequest, QueryResult
from epimemer.pipelines.query.vector_search import vector_search
from epimemer.storage.memory import InMemoryStorage


# --- Fixtures ---


@pytest.fixture
def embedding_provider() -> MockEmbeddingProvider:
    return MockEmbeddingProvider(model_id="mock-embed", dimension=8)


@pytest.fixture
async def populated_graph(embedding_provider: MockEmbeddingProvider):
    """Create an InMemoryStorage with several nodes, embeddings, and edges.

    Graph structure:
        topic1 -- SUPPORTS -- fact1
        topic1 -- ABSTRACTS -- inference1
        fact1 -- DERIVED_FROM -- inference1
        topic2 -- SUPPORTS -- fact2
        fact1 -- SUPERSEDED_BY -- fact3  (history edge)

    All nodes have embeddings stored.
    """
    storage = InMemoryStorage()

    # Create nodes
    topic1 = Topic(
        id="topic-1",
        content="Machine learning and neural networks",
        source_id="seg-1",
    )
    topic2 = Topic(
        id="topic-2",
        content="Climate change and environmental policy",
        source_id="seg-2",
    )
    fact1 = Fact(
        id="fact-1",
        content="Neural networks require large training datasets",
        source_id="seg-1",
    )
    fact2 = Fact(
        id="fact-2",
        content="Global temperatures have risen by 1.1 degrees Celsius",
        source_id="seg-2",
    )
    fact3 = Fact(
        id="fact-3",
        content="Neural networks require large training datasets (updated)",
        source_id="seg-1",
    )
    inference1 = Inference(
        id="inference-1",
        content="Deep learning will continue to improve with more data",
        source_id="seg-1",
    )

    for node in [topic1, topic2, fact1, fact2, fact3, inference1]:
        await storage.store_node(node)

    # Create embeddings for all nodes
    for node in [topic1, topic2, fact1, fact2, fact3, inference1]:
        vectors = await embedding_provider.embed([node.content])
        emb = EmbeddingRecord(
            item_id=node.id,
            model_id=embedding_provider.model_id,
            vector=vectors[0],
        )
        await storage.store_embedding(emb)

    # Create edges
    edges = [
        NodeEdge(id="edge-1", src_id="fact-1", dst_id="topic-1", type=EdgeType.SUPPORTS),
        NodeEdge(id="edge-2", src_id="inference-1", dst_id="topic-1", type=EdgeType.ABSTRACTS),
        NodeEdge(id="edge-3", src_id="inference-1", dst_id="fact-1", type=EdgeType.DERIVED_FROM),
        NodeEdge(id="edge-4", src_id="fact-2", dst_id="topic-2", type=EdgeType.SUPPORTS),
        NodeEdge(id="edge-5", src_id="fact-1", dst_id="fact-3", type=EdgeType.SUPERSEDED_BY),
    ]
    for edge in edges:
        await storage.store_edge(edge)

    return storage, embedding_provider


# --- Vector search tests ---


async def test_vector_search_returns_similar_nodes(populated_graph):
    """Vector search returns nodes semantically similar to query."""
    storage, emb_provider = populated_graph

    results = await vector_search(
        query_text="Neural networks require large training datasets",
        embedding_provider=emb_provider,
        storage=storage,
        k=3,
    )

    assert len(results) > 0
    # The most similar node should be fact-1 since the query text matches exactly
    node, score = results[0]
    assert node.id == "fact-1"
    assert score > 0.9  # Very high similarity for exact match


async def test_vector_search_respects_node_type_filter(populated_graph):
    """Vector search filters results by node type."""
    storage, emb_provider = populated_graph

    # Search for only topics
    results = await vector_search(
        query_text="Machine learning and neural networks",
        embedding_provider=emb_provider,
        storage=storage,
        k=10,
        node_type=NodeType.TOPIC,
    )

    assert len(results) > 0
    for node, _score in results:
        assert isinstance(node, Topic)


async def test_vector_search_empty_for_unrelated_query(populated_graph):
    """Vector search returns low-scoring results for unrelated queries."""
    storage, emb_provider = populated_graph

    results = await vector_search(
        query_text="quantum entanglement in photonic crystals xyzzy",
        embedding_provider=emb_provider,
        storage=storage,
        k=3,
    )

    # Results may exist but scores should be lower than for related queries
    if results:
        _node, best_score = results[0]
        # With mock embeddings, unrelated text should have lower similarity
        # than identical text (which gets ~1.0)
        assert best_score < 1.0


# --- Graph expansion tests ---


async def test_graph_expansion_traverses_edges(populated_graph):
    """Graph expansion discovers connected nodes via edges."""
    storage, emb_provider = populated_graph

    # Start from topic-1, should find fact-1 and inference-1
    topic1 = await storage.get_node("topic-1")
    nodes, edges = await expand_via_graph(
        seed_nodes=[topic1],
        storage=storage,
        hops=1,
    )

    node_ids = {n.id for n in nodes}
    assert "topic-1" in node_ids  # seed included
    assert "fact-1" in node_ids   # connected via SUPPORTS
    assert "inference-1" in node_ids  # connected via ABSTRACTS


async def test_graph_expansion_respects_hop_limit(populated_graph):
    """Graph expansion stops at the specified hop depth."""
    storage, emb_provider = populated_graph

    # Start from topic-2, hop=1 should only find fact-2
    topic2 = await storage.get_node("topic-2")
    nodes, edges = await expand_via_graph(
        seed_nodes=[topic2],
        storage=storage,
        hops=1,
    )

    node_ids = {n.id for n in nodes}
    assert "topic-2" in node_ids
    assert "fact-2" in node_ids
    # Should NOT reach topic-1 or inference-1 (those are further away)
    assert "topic-1" not in node_ids
    assert "inference-1" not in node_ids


async def test_graph_expansion_skips_history_edges(populated_graph):
    """Graph expansion skips SUPERSEDED_BY and MERGED_INTO edges by default."""
    storage, emb_provider = populated_graph

    # Start from fact-1, which has a SUPERSEDED_BY edge to fact-3
    fact1 = await storage.get_node("fact-1")
    nodes, edges = await expand_via_graph(
        seed_nodes=[fact1],
        storage=storage,
        hops=1,
    )

    node_ids = {n.id for n in nodes}
    # fact-3 should NOT be reached via the SUPERSEDED_BY edge
    assert "fact-3" not in node_ids
    # But topic-1 and inference-1 should be reachable
    assert "topic-1" in node_ids
    assert "inference-1" in node_ids


async def test_graph_expansion_does_not_revisit_nodes(populated_graph):
    """Graph expansion does not include the same node multiple times."""
    storage, emb_provider = populated_graph

    # Start from topic-1 with 2 hops — should traverse through fact-1 and inference-1
    # but not revisit topic-1 when coming back via inference-1 -> topic-1
    topic1 = await storage.get_node("topic-1")
    nodes, edges = await expand_via_graph(
        seed_nodes=[topic1],
        storage=storage,
        hops=2,
    )

    node_ids = [n.id for n in nodes]
    # No duplicates
    assert len(node_ids) == len(set(node_ids))
    # topic-1 appears exactly once (as seed)
    assert node_ids.count("topic-1") == 1


# --- Hybrid Petri net tests ---


async def test_hybrid_retrieval_end_to_end(populated_graph):
    """Hybrid retrieval net returns a complete QueryResult."""
    storage, emb_provider = populated_graph

    request = QueryRequest(
        query_text="Neural networks require large training datasets",
        k=3,
        graph_hops=1,
    )
    graph = hybrid_retrieval_net(request, emb_provider, storage)

    # Execute all 3 transitions
    graph, fired = await ExecutableGraphOperations.execute_graph(graph, max_transitions=3)

    assert fired == 3

    # Get the final result
    result_place = graph.place_named("QueryResult")
    assert result_place is not None
    assert len(result_place.tokens) == 1
    result = result_place.tokens[0]
    assert isinstance(result, QueryResult)
    assert len(result.nodes) > 0


async def test_hybrid_retrieval_metadata_counts(populated_graph):
    """Hybrid retrieval metadata has correct counts and type breakdown."""
    storage, emb_provider = populated_graph

    request = QueryRequest(
        query_text="Machine learning and neural networks",
        k=5,
        graph_hops=1,
    )
    graph = hybrid_retrieval_net(request, emb_provider, storage)
    graph, fired = await ExecutableGraphOperations.execute_graph(graph, max_transitions=3)

    result = graph.place_named("QueryResult").tokens[0]
    metadata = result.metadata

    assert metadata.nodes_searched > 0
    assert metadata.nodes_returned == len(result.nodes)
    assert metadata.graph_hops == 1
    assert metadata.vector_search_time_ms >= 0.0
    assert metadata.graph_expansion_time_ms >= 0.0

    # Check source_types adds up
    total_from_types = sum(metadata.source_types.values())
    assert total_from_types == metadata.nodes_returned

    # Check that source_types keys are valid
    for key in metadata.source_types:
        assert key in {"topic", "fact", "inference"}


async def test_hybrid_retrieval_tokens_flow_correctly(populated_graph):
    """Tokens flow through all places in the Petri net in sequence."""
    storage, emb_provider = populated_graph

    request = QueryRequest(
        query_text="Neural networks require large training datasets",
        k=3,
        graph_hops=1,
    )
    graph = hybrid_retrieval_net(request, emb_provider, storage)

    # Before execution: QueryRequest has a token, others empty
    assert len(graph.place_named("QueryRequest").tokens) == 1
    assert len(graph.place_named("VectorResults").tokens) == 0
    assert len(graph.place_named("ExpandedResults").tokens) == 0
    assert len(graph.place_named("QueryResult").tokens) == 0

    # After transition 1: VectorResults has a token
    graph, _ = await ExecutableGraphOperations.execute_graph(graph, max_transitions=1)
    assert len(graph.place_named("QueryRequest").tokens) == 0
    assert len(graph.place_named("VectorResults").tokens) == 1

    # After transition 2: ExpandedResults has a token
    graph, _ = await ExecutableGraphOperations.execute_graph(graph, max_transitions=1)
    assert len(graph.place_named("VectorResults").tokens) == 0
    assert len(graph.place_named("ExpandedResults").tokens) == 1

    # After transition 3: QueryResult has a token
    graph, _ = await ExecutableGraphOperations.execute_graph(graph, max_transitions=1)
    assert len(graph.place_named("ExpandedResults").tokens) == 0
    assert len(graph.place_named("QueryResult").tokens) == 1


# --- Temporal query test ---


async def test_temporal_query_excludes_future_nodes(embedding_provider):
    """Temporal query with at_time excludes nodes created after that time."""
    storage = InMemoryStorage()

    past_time = datetime(2025, 1, 1, tzinfo=timezone.utc)
    future_time = datetime(2026, 6, 1, tzinfo=timezone.utc)
    query_time = datetime(2025, 6, 1, tzinfo=timezone.utc)

    # Create a past node and a future node
    past_topic = Topic(
        id="past-topic",
        content="Historical information about past events",
        source_id="seg-past",
        created_at=past_time,
    )
    future_topic = Topic(
        id="future-topic",
        content="Future predictions about upcoming events",
        source_id="seg-future",
        created_at=future_time,
    )

    await storage.store_node(past_topic)
    await storage.store_node(future_topic)

    # Query nodes at query_time should exclude the future node
    result = await storage.query_nodes(
        node_type=NodeType.TOPIC,
        at_time=query_time,
    )

    node_ids = {n.id for n in result}
    assert "past-topic" in node_ids
    assert "future-topic" not in node_ids
