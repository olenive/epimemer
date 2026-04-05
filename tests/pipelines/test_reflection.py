"""Tests for Phase 5: Reflection.

Covers:
- Topic consolidation: finds similar pairs above threshold
- Topic consolidation: dissimilar topics not paired
- Topic consolidation: merge creates new topic, marks originals as merged
- Value decay: relevance decreases after decay pass
- Value decay: returns correct count of decayed nodes
- Value decay: respects min_relevance floor
- Contradiction detection: finds similar fact pairs
- Contradiction detection: excludes already-linked pairs
- Archival: finds old superseded nodes
- Archival: exports nodes and edges correctly
- Archival: active nodes never included
"""

from datetime import datetime, timedelta, timezone

import pytest

from epimemer.core.types import (
    EdgeType,
    EmbeddingRecord,
    Fact,
    NodeEdge,
    NodeStatus,
    Topic,
    ValueSignal,
)
from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.pipelines.reflection.archival import archive_nodes, find_archival_candidates
from epimemer.pipelines.reflection.contradiction_detection import detect_contradictions
from epimemer.pipelines.reflection.topic_consolidation import (
    find_similar_topic_pairs,
    merge_similar_topics,
)
from epimemer.pipelines.reflection.value_decay import apply_decay
from epimemer.storage.memory import InMemoryStorage


# --- Fixtures ---


@pytest.fixture
def embedding_provider() -> MockEmbeddingProvider:
    return MockEmbeddingProvider(model_id="mock-embed", dimension=8)


@pytest.fixture
async def storage_with_similar_topics(embedding_provider: MockEmbeddingProvider):
    """Storage with topic pairs that have identical embeddings (similarity=1.0).

    topic_a and topic_b share the exact same content (and thus same embedding).
    topic_c is completely different.
    """
    storage = InMemoryStorage()

    topic_a = Topic(
        id="topic-a",
        content="Machine learning algorithms",
        source_id="seg-1",
        value=ValueSignal(confidence=0.8, relevance=0.7, novelty=0.6),
    )
    topic_b = Topic(
        id="topic-b",
        content="Machine learning algorithms",  # identical content = identical embedding
        source_id="seg-2",
        value=ValueSignal(confidence=0.6, relevance=0.5, novelty=0.4),
    )
    topic_c = Topic(
        id="topic-c",
        content="Ocean currents and marine biology deep sea exploration",
        source_id="seg-3",
        value=ValueSignal(confidence=0.9, relevance=0.8, novelty=0.3),
    )

    for topic in [topic_a, topic_b, topic_c]:
        await storage.store_node(topic)
        vectors = await embedding_provider.embed([topic.content])
        emb = EmbeddingRecord(
            item_id=topic.id,
            model_id=embedding_provider.model_id,
            vector=vectors[0],
        )
        await storage.store_embedding(emb)

    return storage


@pytest.fixture
async def storage_with_similar_facts(embedding_provider: MockEmbeddingProvider):
    """Storage with fact pairs — some similar, some with existing edges.

    fact_a and fact_b have identical content (will be similar).
    fact_c is different.
    fact_d and fact_e have identical content but are already linked.
    """
    storage = InMemoryStorage()

    fact_a = Fact(
        id="fact-a",
        content="The Earth orbits the Sun",
        source_id="seg-1",
    )
    fact_b = Fact(
        id="fact-b",
        content="The Earth orbits the Sun",  # identical = high similarity
        source_id="seg-2",
    )
    fact_c = Fact(
        id="fact-c",
        content="Quantum entanglement enables secure communication protocols",
        source_id="seg-3",
    )
    fact_d = Fact(
        id="fact-d",
        content="Water boils at 100 degrees Celsius",
        source_id="seg-4",
    )
    fact_e = Fact(
        id="fact-e",
        content="Water boils at 100 degrees Celsius",  # identical but already linked
        source_id="seg-5",
    )

    for fact in [fact_a, fact_b, fact_c, fact_d, fact_e]:
        await storage.store_node(fact)
        vectors = await embedding_provider.embed([fact.content])
        emb = EmbeddingRecord(
            item_id=fact.id,
            model_id=embedding_provider.model_id,
            vector=vectors[0],
        )
        await storage.store_embedding(emb)

    # Link fact_d and fact_e with a SIMILARITY edge
    edge = NodeEdge(
        id="edge-sim-1",
        src_id="fact-d",
        dst_id="fact-e",
        type=EdgeType.SIMILARITY,
    )
    await storage.store_edge(edge)

    return storage


@pytest.fixture
async def storage_with_archival_candidates(embedding_provider: MockEmbeddingProvider):
    """Storage with nodes of various statuses and ages.

    - old_superseded: SUPERSEDED, 120 days old (should be archival candidate)
    - recent_superseded: SUPERSEDED, 30 days old (too recent)
    - old_merged: MERGED, 100 days old (should be archival candidate)
    - active_topic: ACTIVE (never archived)
    """
    storage = InMemoryStorage()
    now = datetime.now(timezone.utc)

    old_superseded = Fact(
        id="fact-old-superseded",
        content="Old superseded fact",
        source_id="seg-1",
        status=NodeStatus.SUPERSEDED,
        superseded_at=now - timedelta(days=120),
        created_at=now - timedelta(days=200),
    )
    recent_superseded = Fact(
        id="fact-recent-superseded",
        content="Recently superseded fact",
        source_id="seg-2",
        status=NodeStatus.SUPERSEDED,
        superseded_at=now - timedelta(days=30),
        created_at=now - timedelta(days=60),
    )
    old_merged = Topic(
        id="topic-old-merged",
        content="Old merged topic",
        source_id="seg-3",
        status=NodeStatus.MERGED,
        superseded_at=now - timedelta(days=100),
        created_at=now - timedelta(days=150),
    )
    active_topic = Topic(
        id="topic-active",
        content="Active topic that should not be archived",
        source_id="seg-4",
    )

    # The replacement nodes
    replacement_fact = Fact(
        id="fact-replacement",
        content="Replacement fact",
        source_id="seg-1",
    )
    merged_topic = Topic(
        id="topic-merged-result",
        content="Merged topic result",
        source_id="seg-3",
    )

    for node in [old_superseded, recent_superseded, old_merged, active_topic,
                 replacement_fact, merged_topic]:
        await storage.store_node(node)

    # History edges
    edge1 = NodeEdge(
        id="edge-superseded-1",
        src_id="fact-old-superseded",
        dst_id="fact-replacement",
        type=EdgeType.SUPERSEDED_BY,
    )
    edge2 = NodeEdge(
        id="edge-merged-1",
        src_id="topic-old-merged",
        dst_id="topic-merged-result",
        type=EdgeType.MERGED_INTO,
    )
    await storage.store_edge(edge1)
    await storage.store_edge(edge2)

    return storage


# --- Topic consolidation tests ---


async def test_find_similar_topic_pairs_above_threshold(
    storage_with_similar_topics, embedding_provider
):
    """Topics with identical content are found as similar pairs."""
    storage = storage_with_similar_topics

    pairs = await find_similar_topic_pairs(
        storage, embedding_provider, similarity_threshold=0.85
    )

    assert len(pairs) >= 1
    # topic-a and topic-b have identical embeddings (similarity = 1.0)
    ids_in_pairs = {(p[0].id, p[1].id) for p in pairs}
    assert ("topic-a", "topic-b") in ids_in_pairs or ("topic-b", "topic-a") in ids_in_pairs

    # The score for the identical pair should be 1.0
    for a, b, score in pairs:
        if {a.id, b.id} == {"topic-a", "topic-b"}:
            assert score == pytest.approx(1.0)


async def test_dissimilar_topics_not_paired(
    storage_with_similar_topics, embedding_provider
):
    """Topics with very different content are not paired at the default threshold."""
    storage = storage_with_similar_topics

    pairs = await find_similar_topic_pairs(
        storage, embedding_provider, similarity_threshold=0.99
    )

    # At threshold 0.99, only truly identical embeddings should pair
    for a, b, score in pairs:
        pair_ids = {a.id, b.id}
        # topic-c should not be paired with topic-a or topic-b
        assert "topic-c" not in pair_ids


async def test_merge_creates_new_topic_marks_originals(
    storage_with_similar_topics, embedding_provider
):
    """Merging two topics creates a new one and marks originals as merged."""
    storage = storage_with_similar_topics

    topic_a = await storage.get_node("topic-a")
    topic_b = await storage.get_node("topic-b")

    merged = await merge_similar_topics(topic_a, topic_b, storage)

    # Merged topic is stored
    stored_merged = await storage.get_node(merged.id)
    assert stored_merged is not None
    assert stored_merged.content == merged.content
    assert merged.extraction_method == "merge"

    # Content combines both
    assert "Machine learning algorithms" in merged.content

    # Value signals combined correctly
    assert merged.value.confidence == pytest.approx(0.8)  # max
    assert merged.value.relevance == pytest.approx(0.7)  # max
    assert merged.value.novelty == pytest.approx(0.5)  # average of 0.6 and 0.4

    # Originals marked as merged
    original_a = await storage.get_node("topic-a")
    original_b = await storage.get_node("topic-b")
    assert original_a.status == NodeStatus.MERGED
    assert original_b.status == NodeStatus.MERGED

    # merged_into edges exist
    edges_from_a = await storage.get_edges_from("topic-a")
    merged_edges_a = [e for e in edges_from_a if e.type == EdgeType.MERGED_INTO]
    assert len(merged_edges_a) == 1
    assert merged_edges_a[0].dst_id == merged.id

    edges_from_b = await storage.get_edges_from("topic-b")
    merged_edges_b = [e for e in edges_from_b if e.type == EdgeType.MERGED_INTO]
    assert len(merged_edges_b) == 1
    assert merged_edges_b[0].dst_id == merged.id


# --- Value decay tests ---


async def test_value_decay_reduces_relevance():
    """Relevance decreases after a decay pass."""
    storage = InMemoryStorage()

    topic = Topic(
        id="topic-decay",
        content="A topic with high relevance",
        source_id="seg-1",
        value=ValueSignal(relevance=0.8, confidence=0.5, novelty=0.5),
    )
    await storage.store_node(topic)

    await apply_decay(storage, decay_rate=0.1)

    updated = await storage.get_node("topic-decay")
    assert updated.value.relevance == pytest.approx(0.72)  # 0.8 * 0.9


async def test_value_decay_returns_correct_count():
    """apply_decay returns the number of nodes decayed."""
    storage = InMemoryStorage()

    for i in range(5):
        topic = Topic(
            id=f"topic-{i}",
            content=f"Topic number {i}",
            source_id=f"seg-{i}",
            value=ValueSignal(relevance=0.5),
        )
        await storage.store_node(topic)

    count = await apply_decay(storage, decay_rate=0.1)
    assert count == 5


async def test_value_decay_respects_min_relevance():
    """Decay does not push relevance below min_relevance."""
    storage = InMemoryStorage()

    topic = Topic(
        id="topic-floor",
        content="A topic near the floor",
        source_id="seg-1",
        value=ValueSignal(relevance=0.02),
    )
    await storage.store_node(topic)

    await apply_decay(storage, decay_rate=0.9, min_relevance=0.01)

    updated = await storage.get_node("topic-floor")
    assert updated.value.relevance >= 0.01


# --- Contradiction detection tests ---


async def test_detect_contradictions_finds_similar_pairs(
    storage_with_similar_facts, embedding_provider
):
    """Facts with identical content are detected as potential contradiction candidates."""
    storage = storage_with_similar_facts

    pairs = await detect_contradictions(
        storage, embedding_provider, similarity_threshold=0.80
    )

    # fact_a and fact_b have identical embeddings and are not already linked
    pair_id_sets = [{a.id, b.id} for a, b, _s in pairs]
    assert {"fact-a", "fact-b"} in pair_id_sets


async def test_detect_contradictions_excludes_already_linked(
    storage_with_similar_facts, embedding_provider
):
    """Fact pairs already linked by SIMILARITY edges are excluded."""
    storage = storage_with_similar_facts

    pairs = await detect_contradictions(
        storage, embedding_provider, similarity_threshold=0.80
    )

    # fact_d and fact_e have identical embeddings but are already linked
    pair_id_sets = [{a.id, b.id} for a, b, _s in pairs]
    assert {"fact-d", "fact-e"} not in pair_id_sets


# --- Archival tests ---


async def test_archival_finds_old_superseded_nodes(storage_with_archival_candidates):
    """Old superseded and merged nodes are found as archival candidates."""
    storage = storage_with_archival_candidates

    candidates = await find_archival_candidates(storage, max_age_days=90)

    candidate_ids = {n.id for n in candidates}
    assert "fact-old-superseded" in candidate_ids  # 120 days old
    assert "topic-old-merged" in candidate_ids  # 100 days old


async def test_archival_excludes_recent_superseded(storage_with_archival_candidates):
    """Recently superseded nodes are not archival candidates."""
    storage = storage_with_archival_candidates

    candidates = await find_archival_candidates(storage, max_age_days=90)

    candidate_ids = {n.id for n in candidates}
    assert "fact-recent-superseded" not in candidate_ids  # only 30 days old


async def test_archival_active_nodes_never_included(storage_with_archival_candidates):
    """Active nodes are never included in archival candidates."""
    storage = storage_with_archival_candidates

    candidates = await find_archival_candidates(storage, max_age_days=0)

    candidate_ids = {n.id for n in candidates}
    assert "topic-active" not in candidate_ids


async def test_archive_nodes_exports_correctly(storage_with_archival_candidates):
    """archive_nodes exports nodes and edges to a serializable dict."""
    storage = storage_with_archival_candidates

    candidates = await find_archival_candidates(storage, max_age_days=90)
    result = await archive_nodes(candidates, storage)

    assert "nodes" in result
    assert "edges" in result
    assert len(result["nodes"]) == len(candidates)

    # Edges should include the history edges for archived nodes
    edge_ids = {e["id"] for e in result["edges"]}
    assert "edge-superseded-1" in edge_ids
    assert "edge-merged-1" in edge_ids

    # Node data should be serializable dicts
    for node_dict in result["nodes"]:
        assert "id" in node_dict
        assert "content" in node_dict
        assert "status" in node_dict

