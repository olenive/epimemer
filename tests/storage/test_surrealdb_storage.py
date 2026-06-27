"""Tests for SurrealDB storage backend.

Uses mem:// (embedded) mode so no external SurrealDB instance is needed.
"""

from datetime import datetime, timezone

import pytest

from epimemer.core.types import (
    EdgeType,
    EmbeddingRecord,
    Fact,
    Inference,
    NodeEdge,
    NodeStatus,
    NodeType,
    RawDocument,
    Segment,
    Topic,
)
from epimemer.storage.surrealdb_adapter import SurrealDBStorage


@pytest.fixture
async def store():
    s = SurrealDBStorage(url="mem://")
    await s.connect()
    yield s
    await s.close()


class TestDocumentStorage:

    async def test_store_and_retrieve(self, store):
        doc = RawDocument(content="Hello world")
        await store.store_document(doc)
        got = await store.get_document(doc.id)
        assert got is not None
        assert got.content == "Hello world"

    async def test_get_missing_returns_none(self, store):
        got = await store.get_document("nonexistent")
        assert got is None


class TestSegmentStorage:

    async def test_store_and_retrieve(self, store):
        doc = RawDocument(content="Hello world")
        await store.store_document(doc)
        seg = Segment(source_id=doc.id, text="Hello", span_start=0, span_end=5)
        await store.store_segment(seg)
        segments = await store.get_segments_for_document(doc.id)
        assert len(segments) == 1
        assert segments[0].text == "Hello"

    async def test_multiple_segments_ordered(self, store):
        seg1 = Segment(source_id="d1", text="A", span_start=0, span_end=1)
        seg2 = Segment(source_id="d1", text="B", span_start=2, span_end=3)
        await store.store_segment(seg2)  # Insert out of order
        await store.store_segment(seg1)
        segments = await store.get_segments_for_document("d1")
        assert len(segments) == 2
        assert segments[0].text == "A"  # Should be ordered by span_start


class TestNodeStorage:

    async def test_store_and_retrieve_topic(self, store):
        t = Topic(content="ML topic", source_id="s1")
        await store.store_node(t)
        got = await store.get_node(t.id)
        assert isinstance(got, Topic)
        assert got.content == "ML topic"

    async def test_store_and_retrieve_fact(self, store):
        f = Fact(content="A fact", source_id="s1")
        await store.store_node(f)
        got = await store.get_node(f.id)
        assert isinstance(got, Fact)

    async def test_store_and_retrieve_inference(self, store):
        i = Inference(content="An inference", source_id="s1")
        await store.store_node(i)
        got = await store.get_node(i.id)
        assert isinstance(got, Inference)

    async def test_get_missing_returns_none(self, store):
        got = await store.get_node("nonexistent")
        assert got is None

    async def test_query_by_type(self, store):
        t = Topic(content="topic", source_id="s1")
        f = Fact(content="fact", source_id="s1")
        await store.store_node(t)
        await store.store_node(f)
        topics = await store.query_nodes(node_type=NodeType.TOPIC)
        facts = await store.query_nodes(node_type=NodeType.FACT)
        assert len(topics) == 1
        assert len(facts) == 1

    async def test_query_by_status(self, store):
        t = Topic(content="topic", source_id="s1")
        await store.store_node(t)
        await store.update_node_status(t.id, NodeStatus.SUPERSEDED)
        active = await store.query_nodes(status=NodeStatus.ACTIVE)
        superseded = await store.query_nodes(status=NodeStatus.SUPERSEDED)
        assert len(active) == 0
        assert len(superseded) == 1

    async def test_value_signal_preserved(self, store):
        t = Topic(content="topic", source_id="s1")
        t.value.novelty = 0.3
        t.value.confidence = 0.9
        await store.store_node(t)
        got = await store.get_node(t.id)
        assert got.value.novelty == 0.3
        assert got.value.confidence == 0.9


class TestEdgeStorage:

    async def test_store_and_retrieve_edges(self, store):
        e = NodeEdge(src_id="a", dst_id="b", type=EdgeType.SUPPORTS)
        await store.store_edge(e)
        from_edges = await store.get_edges_from("a")
        to_edges = await store.get_edges_to("b")
        assert len(from_edges) == 1
        assert len(to_edges) == 1
        assert from_edges[0].type == EdgeType.SUPPORTS

    async def test_no_edges_returns_empty(self, store):
        assert len(await store.get_edges_from("x")) == 0
        assert len(await store.get_edges_to("x")) == 0

    async def test_delete_edge(self, store):
        e = NodeEdge(src_id="a", dst_id="b", type=EdgeType.SUPPORTS)
        await store.store_edge(e)
        assert len(await store.get_edges_from("a")) == 1
        await store.delete_edge(e.id)
        assert len(await store.get_edges_from("a")) == 0
        assert len(await store.get_edges_to("b")) == 0

    async def test_delete_missing_edge_is_noop(self, store):
        await store.delete_edge("nonexistent")  # must not raise


class TestCounts:

    async def test_count_nodes_empty(self, store):
        counts = await store.count_nodes_by_type()
        assert counts == {NodeType.TOPIC: 0, NodeType.FACT: 0, NodeType.INFERENCE: 0}

    async def test_count_nodes_by_type(self, store):
        await store.store_node(Topic(content="t", source_id="s1"))
        await store.store_node(Fact(content="f1", source_id="s1"))
        await store.store_node(Fact(content="f2", source_id="s1"))
        await store.store_node(Inference(content="i", source_id="s1"))
        counts = await store.count_nodes_by_type()
        assert counts[NodeType.TOPIC] == 1
        assert counts[NodeType.FACT] == 2
        assert counts[NodeType.INFERENCE] == 1

    async def test_count_nodes_respects_status(self, store):
        topic = Topic(content="t", source_id="s1")
        await store.store_node(topic)
        await store.update_node_status(topic.id, NodeStatus.SUPERSEDED)
        active = await store.count_nodes_by_type(status=NodeStatus.ACTIVE)
        superseded = await store.count_nodes_by_type(status=NodeStatus.SUPERSEDED)
        assert active[NodeType.TOPIC] == 0
        assert superseded[NodeType.TOPIC] == 1

    async def test_count_edges_by_type(self, store):
        await store.store_edge(NodeEdge(src_id="a", dst_id="b", type=EdgeType.SUPPORTS))
        await store.store_edge(NodeEdge(src_id="c", dst_id="b", type=EdgeType.SUPPORTS))
        await store.store_edge(
            NodeEdge(src_id="d", dst_id="a", type=EdgeType.DERIVED_FROM)
        )
        counts = await store.count_edges_by_type()
        assert counts[EdgeType.SUPPORTS] == 2
        assert counts[EdgeType.DERIVED_FROM] == 1
        assert counts[EdgeType.ABOUT] == 0


class TestEmbeddingStorage:

    async def test_store_and_retrieve(self, store):
        emb = EmbeddingRecord(item_id="n1", model_id="model-a", vector=[0.1, 0.2])
        await store.store_embedding(emb)
        results = await store.get_embeddings_for_item("n1")
        assert len(results) == 1
        assert results[0].model_id == "model-a"

    async def test_filter_by_model(self, store):
        emb1 = EmbeddingRecord(item_id="n1", model_id="model-a", vector=[0.1, 0.2])
        emb2 = EmbeddingRecord(item_id="n1", model_id="model-b", vector=[0.3, 0.4])
        await store.store_embedding(emb1)
        await store.store_embedding(emb2)
        results = await store.get_embeddings_for_item("n1", model_id="model-a")
        assert len(results) == 1

    async def test_vector_search(self, store):
        t = Topic(content="ML", source_id="s1")
        await store.store_node(t)
        emb = EmbeddingRecord(item_id=t.id, model_id="test", vector=[1.0, 0.0, 0.0])
        await store.store_embedding(emb)

        results = await store.vector_search([0.9, 0.1, 0.0], "test", k=5)
        assert len(results) == 1
        assert results[0][0] == t.id
        assert results[0][1] > 0.9

    async def test_vector_search_excludes_superseded(self, store):
        t = Topic(content="ML", source_id="s1")
        await store.store_node(t)
        emb = EmbeddingRecord(item_id=t.id, model_id="test", vector=[1.0, 0.0, 0.0])
        await store.store_embedding(emb)
        await store.update_node_status(t.id, NodeStatus.SUPERSEDED)

        # Exact-match query, but the node is superseded → must be filtered out.
        results = await store.vector_search([1.0, 0.0, 0.0], "test", k=5)
        assert all(item_id != t.id for item_id, _ in results)


class TestAtomicOperations:
    """Backend-native atomic supersede/merge via single-query transactions."""

    async def test_supersede_tx_migrates_embeds_and_supersedes(self, store):
        old = Topic(content="old topic", source_id="s1")
        fact = Fact(content="supporting fact", source_id="s1")
        await store.store_node(old)
        await store.store_node(fact)
        await store.store_edge(
            NodeEdge(src_id=fact.id, dst_id=old.id, type=EdgeType.SUPPORTS)
        )

        new = Topic(content="new topic", source_id="s1")
        new_emb = EmbeddingRecord(item_id=new.id, model_id="m", vector=[0.1, 0.2, 0.3])
        lineage = NodeEdge(src_id=old.id, dst_id=new.id, type=EdgeType.SUPERSEDED_BY)
        await store.supersede_node_tx(
            old, new, new_emb, lineage, superseded_at=datetime.now(timezone.utc)
        )

        assert (await store.get_node(old.id)).status == NodeStatus.SUPERSEDED
        assert (await store.get_node(new.id)).content == "new topic"
        assert len(await store.get_embeddings_for_item(new.id)) == 1
        into_new = await store.get_edges_to(new.id, edge_type=EdgeType.SUPPORTS)
        assert len(into_new) == 1 and into_new[0].src_id == fact.id
        assert len(await store.get_edges_to(old.id, edge_type=EdgeType.SUPPORTS)) == 0
        lin = await store.get_edges_from(old.id, edge_type=EdgeType.SUPERSEDED_BY)
        assert len(lin) == 1 and lin[0].dst_id == new.id

    async def test_supersede_tx_rolls_back_on_failure(self, store):
        old = Topic(content="old", source_id="s1")
        await store.store_node(old)

        new = Topic(content="new", source_id="s1")
        # Squat the new node's uid so the in-transaction INSERT collides and
        # aborts the whole transaction.
        await store.store_node(Topic(id=new.id, content="squatter", source_id="s1"))

        new_emb = EmbeddingRecord(item_id=new.id, model_id="m", vector=[0.1, 0.2])
        lineage = NodeEdge(src_id=old.id, dst_id=new.id, type=EdgeType.SUPERSEDED_BY)
        with pytest.raises(Exception):
            await store.supersede_node_tx(
                old, new, new_emb, lineage, superseded_at=datetime.now(timezone.utc)
            )

        # Rolled back: the old node was never marked superseded.
        assert (await store.get_node(old.id)).status == NodeStatus.ACTIVE
        assert len(await store.get_edges_from(old.id, edge_type=EdgeType.SUPERSEDED_BY)) == 0

    async def test_write_batch_tx_inserts_all(self, store):
        t = Topic(content="t", source_id="s1")
        f = Fact(content="f", source_id="s1")
        edge = NodeEdge(src_id=f.id, dst_id=t.id, type=EdgeType.SUPPORTS)
        emb = EmbeddingRecord(item_id=t.id, model_id="m", vector=[1.0, 0.0])

        await store.write_batch_tx(nodes=[t, f], edges=[edge], embeddings=[emb])

        assert (await store.get_node(t.id)).content == "t"
        assert (await store.get_node(f.id)).content == "f"
        assert len(await store.get_edges_to(t.id, edge_type=EdgeType.SUPPORTS)) == 1
        assert len(await store.get_embeddings_for_item(t.id)) == 1

    async def test_write_batch_tx_rolls_back_on_failure(self, store):
        fresh = Topic(content="fresh", source_id="s1")
        collide = Topic(content="collide", source_id="s1")
        # Squat the colliding node's uid so its insert aborts the whole batch.
        await store.store_node(Topic(id=collide.id, content="squatter", source_id="s1"))

        with pytest.raises(Exception):
            await store.write_batch_tx(nodes=[fresh, collide])

        # The fresh node never landed.
        assert await store.get_node(fresh.id) is None

    async def test_supersede_by_existing_tx_flags_and_clears(self, store):
        fact = Fact(content="old", source_id="s1")
        existing = Fact(content="new", source_id="s1")
        inf = Inference(content="inference", source_id="s1")
        for node in (fact, existing, inf):
            await store.store_node(node)
        await store.store_edge(
            NodeEdge(src_id=inf.id, dst_id=fact.id, type=EdgeType.DERIVED_FROM)
        )
        candidate = NodeEdge(
            src_id=existing.id, dst_id=fact.id, type=EdgeType.SUPERSESSION_CANDIDATE
        )
        await store.store_edge(candidate)

        lineage = NodeEdge(
            src_id=fact.id, dst_id=existing.id, type=EdgeType.SUPERSEDED_BY
        )
        evidence = [
            NodeEdge(src_id=fact.id, dst_id=inf.id, type=EdgeType.EVIDENCE_SUPERSEDED)
        ]
        await store.supersede_by_existing_tx(
            fact, existing.id, lineage,
            superseded_at=datetime.now(timezone.utc),
            evidence_edges=evidence,
            clear_edge_ids=[candidate.id],
        )

        assert (await store.get_node(fact.id)).status == NodeStatus.SUPERSEDED
        assert (await store.get_node(existing.id)).status == NodeStatus.ACTIVE
        assert len(
            await store.get_edges_from(fact.id, edge_type=EdgeType.SUPERSEDED_BY)
        ) == 1
        assert len(
            await store.get_edges_to(inf.id, edge_type=EdgeType.EVIDENCE_SUPERSEDED)
        ) == 1
        assert await store.get_edges_to(
            fact.id, edge_type=EdgeType.SUPERSESSION_CANDIDATE
        ) == []

    async def test_merge_tx_migrates_dedupes_and_drops_self_loops(self, store):
        a = Topic(content="a", source_id="s1")
        b = Topic(content="b", source_id="s1")
        fact = Fact(content="shared evidence", source_id="s1")
        for node in (a, b, fact):
            await store.store_node(node)
        await store.store_edge(
            NodeEdge(src_id=fact.id, dst_id=a.id, type=EdgeType.SUPPORTS)
        )
        await store.store_edge(
            NodeEdge(src_id=fact.id, dst_id=b.id, type=EdgeType.SUPPORTS)
        )
        # Edge between the sources → becomes a self-loop on merge, must be dropped.
        await store.store_edge(
            NodeEdge(src_id=a.id, dst_id=b.id, type=EdgeType.SUPPORTS)
        )

        merged = Topic(content="merged", source_id="s1")
        merged_emb = EmbeddingRecord(item_id=merged.id, model_id="m", vector=[0.1, 0.2])
        lineage = [
            NodeEdge(src_id=a.id, dst_id=merged.id, type=EdgeType.MERGED_INTO),
            NodeEdge(src_id=b.id, dst_id=merged.id, type=EdgeType.MERGED_INTO),
        ]
        await store.merge_nodes_tx(
            [a, b], merged, merged_emb, lineage, merged_at=datetime.now(timezone.utc)
        )

        # The two shared supports edges collapse to one; the self-loop is gone.
        into_merged = await store.get_edges_to(merged.id, edge_type=EdgeType.SUPPORTS)
        assert len(into_merged) == 1 and into_merged[0].src_id == fact.id
        self_loops = [
            e for e in await store.get_edges_from(merged.id)
            if e.dst_id == merged.id
        ]
        assert self_loops == []
        assert (await store.get_node(a.id)).status == NodeStatus.MERGED
        assert (await store.get_node(b.id)).status == NodeStatus.MERGED
        assert len(await store.get_embeddings_for_item(merged.id)) == 1
        assert len(await store.get_edges_to(merged.id, edge_type=EdgeType.MERGED_INTO)) == 2
