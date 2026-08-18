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
from epimemer.mcp import tools
from epimemer.pipelines.graph_construction.versioning import supersede_by_existing
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


async def test_graph_expansion_excludes_non_active_neighbors(embedding_provider):
    """Retired nodes must not come back one hop away.

    `supersede_by_existing` deliberately does NOT migrate the loser's edges (the
    winner carries its own evidence), so a superseded node keeps its knowledge
    edges to active nodes. Without a status filter on the graph-hop path, any
    search reaching the active neighbour drags the retired node back into the
    results — the status guard on the vector path does not cover this.
    """
    storage = InMemoryStorage()

    fact_a = Fact(id="fact-a", content="A", source_id="doc-1")
    fact_b = Fact(id="fact-b", content="B", source_id="doc-1")
    inference = Inference(id="inf-1", content="I", source_id="doc-1")
    for node in (fact_a, fact_b, inference):
        await storage.store_node(node)

    supports = NodeEdge(
        id="edge-a-i",
        src_id="fact-a",
        dst_id="inf-1",
        type=EdgeType.SUPPORTS,
    )
    await storage.store_edge(supports)

    await supersede_by_existing(fact_a, "fact-b", storage, status=NodeStatus.CORRECTED)
    assert (await storage.get_node("fact-a")).status is NodeStatus.CORRECTED
    # The edge survives supersession by design — that is what makes this a bug.
    assert any(e.id == "edge-a-i" for e in await storage.get_edges_to("inf-1"))

    seed = await storage.get_node("inf-1")
    nodes, edges = await expand_via_graph(
        seed_nodes=[seed],
        storage=storage,
        hops=1,
    )

    assert "fact-a" not in {n.id for n in nodes}
    # An edge to a hidden node is dangling noise — drop it too.
    assert "edge-a-i" not in {e.id for e in edges}

    # Same guarantee through the tool layer, which is where an agent sees it.
    result, _ = await tools.query_graph("inf-1", storage, hops=1)
    assert "fact-a" not in {n["id"] for n in result["nodes"]}
    assert "edge-a-i" not in {e["id"] for e in result["edges"]}


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

    # Fork, two retrieval arms, fusion, expansion, assembly.
    graph, fired = await ExecutableGraphOperations.execute_graph(graph, stop_after_n_firings=6)

    assert fired == 6

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
    graph, fired = await ExecutableGraphOperations.execute_graph(graph, stop_after_n_firings=6)

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

    # Before execution: QueryRequest holds the only token.
    assert len(graph.place_named("QueryRequest").tokens) == 1
    for place in ("VectorQuery", "LexicalQuery", "Seeds", "QueryResult"):
        assert len(graph.place_named(place).tokens) == 0

    # The fork feeds both arms from one request.
    graph, _ = await ExecutableGraphOperations.execute_graph(graph, stop_after_n_firings=1)
    assert len(graph.place_named("QueryRequest").tokens) == 0
    assert len(graph.place_named("VectorQuery").tokens) == 1
    assert len(graph.place_named("LexicalQuery").tokens) == 1

    # Both arms run. Either may go first — the fusion is what waits, and it
    # cannot fire until both its input places hold a token.
    graph, _ = await ExecutableGraphOperations.execute_graph(graph, stop_after_n_firings=2)
    assert len(graph.place_named("VectorResults").tokens) == 1
    assert len(graph.place_named("LexicalResults").tokens) == 1
    assert len(graph.place_named("Seeds").tokens) == 0

    # Fusion consumes both and produces one seed set.
    graph, _ = await ExecutableGraphOperations.execute_graph(graph, stop_after_n_firings=1)
    assert len(graph.place_named("VectorResults").tokens) == 0
    assert len(graph.place_named("LexicalResults").tokens) == 0
    assert len(graph.place_named("Seeds").tokens) == 1

    # Expansion, then assembly.
    graph, _ = await ExecutableGraphOperations.execute_graph(graph, stop_after_n_firings=1)
    assert len(graph.place_named("Seeds").tokens) == 0
    assert len(graph.place_named("ExpandedResults").tokens) == 1

    graph, _ = await ExecutableGraphOperations.execute_graph(graph, stop_after_n_firings=1)
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


# --- The lexical arm inside the net ---


@pytest.fixture
async def ticket_graph(embedding_provider):
    """Facts whose identifiers are the only thing separating them.

    Every one of these embeds to roughly "short alphanumeric string" — which is
    the scenario the lexical arm exists for, and the one where a vector-only
    search cannot tell 4417 from 4418 however well it works.
    """
    storage = InMemoryStorage()
    contents = [
        "Ticket JIRA-4417 was closed after the deployment rollback",
        "Ticket JIRA-4418 remains open pending the deployment review",
        "Ticket JIRA-4419 was reassigned to the platform team",
        "Ticket JIRA-4420 is blocked on a certificate rotation",
        "Ticket JIRA-4421 covers the quarterly audit backlog",
    ]
    facts = []
    for content in contents:
        fact = Fact(content=content, source_id="seg-1")
        await storage.store_node(fact)
        vectors = await embedding_provider.embed([content])
        await storage.store_embedding(EmbeddingRecord(
            item_id=fact.id, model_id=embedding_provider.model_id, vector=vectors[0]
        ))
        facts.append(fact)
    return storage, embedding_provider, facts


async def _run(request, embedding_provider, storage) -> QueryResult:
    graph, _ = await tools._run_net(
        hybrid_retrieval_net(request, embedding_provider, storage), "retrieval", None
    )
    return graph.place_named("QueryResult").tokens[0]


async def test_the_lexical_arm_seeds_the_node_the_identifier_names(ticket_graph):
    """The net's whole reason for gaining a second arm.

    The near-miss shares every token of the query except the number, so a
    conjunctive lexical match separates them where similarity cannot.
    """
    storage, provider, facts = ticket_graph
    request = QueryRequest(query_text="JIRA-4417", k=3, graph_hops=0)

    result = await _run(request, provider, storage)

    assert result.provenance[facts[0].id] == "lexical"
    assert result.provenance.get(facts[1].id) != "lexical"


async def test_every_returned_node_is_labelled(ticket_graph):
    """Provenance is not optional decoration: a node in the result with no
    label would be a node the system cannot say how it found."""
    storage, provider, facts = ticket_graph
    request = QueryRequest(query_text="JIRA-4417", k=5, graph_hops=1)

    result = await _run(request, provider, storage)

    assert {node.id for node in result.nodes} == set(result.provenance)


async def test_expansion_labels_what_it_dragged_in(populated_graph):
    """A neighbour reached by an edge is `expanded`, whatever the arms did.

    The distinction the focus panel exists to draw — *this matched; that one was
    dragged in by an edge from it* — and the reason this is an enum rather than
    a boolean.
    """
    storage, provider = populated_graph
    request = QueryRequest(query_text="Neural networks", k=1, graph_hops=1)

    result = await _run(request, provider, storage)

    assert "expanded" in set(result.provenance.values())


async def test_a_prose_query_adds_no_lexical_seeds(ticket_graph):
    """R3's floor doing its work inside the net.

    Every token of this query is in every fact, so BM25 says nothing about any
    of them and the lexical arm contributes no seeds at all — which is what
    keeps the fallback path from adding noise to ordinary prose searches.
    """
    storage, provider, _ = ticket_graph
    request = QueryRequest(query_text="the ticket", k=5, graph_hops=0)

    result = await _run(request, provider, storage)

    assert "lexical" not in set(result.provenance.values())


async def test_a_retired_node_is_not_a_lexical_seed(ticket_graph):
    """R7 through the net: both arms take the same view of what exists."""
    storage, provider, facts = ticket_graph
    await storage.set_node_status_tx(
        [facts[0]], status=NodeStatus.CORRECTED, at=datetime.now(timezone.utc)
    )
    request = QueryRequest(query_text="JIRA-4417", k=5, graph_hops=0)

    result = await _run(request, provider, storage)

    assert facts[0].id not in result.provenance


async def test_a_segment_hit_bridges_to_the_nodes_extracted_from_it(
    embedding_provider,
):
    """§1.1: the identifier is only in the source text, never in the claim.

    This is the half of lexical search that survives an agent paraphrasing. No
    search of any kind recovers `JIRA-4417` from the fact — it is not in it —
    but the segment kept the raw passage, and the fact points back at it.
    """
    from epimemer.core.types import Segment

    storage = InMemoryStorage()
    passages = [
        "Ops confirmed that ticket JIRA-4417 was closed overnight",
        "A separate note about the coffee machine on floor two",
        "Minutes from the weekly planning meeting, nothing decided",
        "A reminder that the office moves next month",
        "Notes on the new starter onboarding checklist",
    ]
    segments = []
    for index, text in enumerate(passages):
        segment = Segment(
            source_id="doc-1", text=text, span_start=index, span_end=index + 1
        )
        await storage.store_segment(segment)
        segments.append(segment)

    # The fact paraphrases the passage and drops the identifier entirely.
    paraphrase = Fact(content="the deployment ticket was closed", source_id=segments[0].id)
    await storage.store_node(paraphrase)
    vectors = await embedding_provider.embed([paraphrase.content])
    await storage.store_embedding(EmbeddingRecord(
        item_id=paraphrase.id,
        model_id=embedding_provider.model_id,
        vector=vectors[0],
    ))

    request = QueryRequest(query_text="JIRA-4417", k=5, graph_hops=0)
    result = await _run(request, embedding_provider, storage)

    assert result.provenance[paraphrase.id] == "segment"
    assert [hit.segment_id for hit in result.segments] == [segments[0].id]
    assert result.segments[0].document_id == "doc-1"


async def test_the_last_node_type_is_still_a_lexical_seed_without_segments(
    embedding_provider,
):
    """A graph with no matching segment must not relabel a whole node type.

    The lexical arm returns one ranking per node type and then one more for the
    segment bridge, and the caller once told the two apart by slicing the last
    entry off. With no segment hit there is no entry to slice, so the last node
    type's ranking was taken for the bridge and its hits came back labelled
    `vector` — the arm found them, and the result denied it.

    Inference is last in `NodeType`, which is why the identifier lives on one
    here.
    """
    storage = InMemoryStorage()
    contents = [
        "Concluded that JIRA-4417 caused the outage",
        "Concluded that the platform team is overloaded",
        "Concluded that the release cadence is too slow",
        "Concluded that onboarding takes about a fortnight",
        "Concluded that the office move will slip",
    ]
    inferences = []
    for content in contents:
        inference = Inference(content=content, source_id="seg-1")
        await storage.store_node(inference)
        vectors = await embedding_provider.embed([content])
        await storage.store_embedding(EmbeddingRecord(
            item_id=inference.id,
            model_id=embedding_provider.model_id,
            vector=vectors[0],
        ))
        inferences.append(inference)

    request = QueryRequest(query_text="JIRA-4417", k=5, graph_hops=0)
    result = await _run(request, embedding_provider, storage)

    assert result.provenance[inferences[0].id] == "lexical"
