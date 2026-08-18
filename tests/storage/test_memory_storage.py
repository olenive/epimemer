"""Tests for in-memory storage backend."""

from datetime import datetime, timezone

import pytest

from epimemer.core.types import (
    EdgeType,
    EmbeddingRecord,
    Fact,
    Inference,
    Metacontext,
    NodeEdge,
    NodeStatus,
    NodeType,
    RawDocument,
    Segment,
    Timeline,
    Timepoint,
    Topic,
)
from epimemer.storage.memory import InMemoryStorage


@pytest.fixture
def store():
    return InMemoryStorage()


class TestLifecycleNoOps:

    async def test_inmemory_connect_close_are_noops(self, store):
        # InMemoryStorage implements the full protocol, so connect/close exist
        # and do nothing — callers invoke them unconditionally (no hasattr).
        assert await store.connect() is None

        doc = RawDocument(content="alive")
        await store.store_document(doc)
        got = await store.get_document(doc.id)
        assert got is not None and got.content == "alive"

        # close is a no-op: idempotent and leaves the in-memory data intact.
        assert await store.close() is None
        assert await store.close() is None
        still = await store.get_document(doc.id)
        assert still is not None and still.content == "alive"


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

    async def test_multiple_segments(self, store):
        seg1 = Segment(source_id="d1", text="A", span_start=0, span_end=1)
        seg2 = Segment(source_id="d1", text="B", span_start=2, span_end=3)
        seg3 = Segment(source_id="d2", text="C", span_start=0, span_end=1)
        for s in (seg1, seg2, seg3):
            await store.store_segment(s)
        assert len(await store.get_segments_for_document("d1")) == 2
        assert len(await store.get_segments_for_document("d2")) == 1


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
        await store.set_node_status_tx(
            [t], status=NodeStatus.SUPERSEDED, at=datetime.now(timezone.utc)
        )
        active = await store.query_nodes(status=NodeStatus.ACTIVE)
        superseded = await store.query_nodes(status=NodeStatus.SUPERSEDED)
        assert len(active) == 0
        assert len(superseded) == 1

    async def test_update_nonexistent_raises(self, store):
        absent = Topic(content="never stored", source_id="s1")
        with pytest.raises(KeyError):
            await store.set_node_status_tx(
                [absent], status=NodeStatus.SUPERSEDED, at=datetime.now(timezone.utc)
            )


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
        assert await store.get_edges_from("x") == []
        assert await store.get_edges_to("x") == []

    async def test_delete_edge(self, store):
        e = NodeEdge(src_id="a", dst_id="b", type=EdgeType.SUPPORTS)
        await store.store_edge(e)
        assert len(await store.get_edges_from("a")) == 1
        await store.delete_edge(e.id)
        assert await store.get_edges_from("a") == []
        assert await store.get_edges_to("b") == []

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
        await store.set_node_status_tx(
            [topic], status=NodeStatus.SUPERSEDED, at=datetime.now(timezone.utc)
        )
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
        assert results[0].model_id == "model-a"

    async def test_vector_search(self, store):
        # Store a topic and its embedding
        t = Topic(content="ML", source_id="s1")
        await store.store_node(t)
        emb = EmbeddingRecord(item_id=t.id, model_id="test", vector=[1.0, 0.0, 0.0])
        await store.store_embedding(emb)

        # Search with similar vector
        results = await store.vector_search([0.9, 0.1, 0.0], "test", k=5)
        assert len(results) == 1
        assert results[0][0] == t.id
        assert results[0][1] > 0.9  # high similarity

    async def test_vector_search_filter_by_type(self, store):
        t = Topic(content="topic", source_id="s1")
        f = Fact(content="fact", source_id="s1")
        await store.store_node(t)
        await store.store_node(f)
        emb_t = EmbeddingRecord(item_id=t.id, model_id="test", vector=[1.0, 0.0])
        emb_f = EmbeddingRecord(item_id=f.id, model_id="test", vector=[0.0, 1.0])
        await store.store_embedding(emb_t)
        await store.store_embedding(emb_f)

        # Search only topics
        results = await store.vector_search([1.0, 0.0], "test", node_type=NodeType.TOPIC)
        assert len(results) == 1
        assert results[0][0] == t.id

    # Excluding inactive nodes is protocol-level and lives in
    # test_storage_parity.py, where both backends must satisfy it.


class TestTimelineStorage:

    async def test_store_and_retrieve(self, store):
        tl = Timeline(name="AI History", description="Key AI events")
        await store.store_timeline(tl)
        got = await store.get_timeline(tl.id)
        assert got is not None
        assert got.name == "AI History"

    async def test_get_missing_returns_none(self, store):
        got = await store.get_timeline("nonexistent")
        assert got is None

    async def test_query_all(self, store):
        tl1 = Timeline(name="Timeline 1")
        tl2 = Timeline(name="Timeline 2")
        await store.store_timeline(tl1)
        await store.store_timeline(tl2)
        all_tl = await store.query_timelines()
        assert len(all_tl) == 2

    async def test_store_with_timepoints(self, store):
        tp = Timepoint(label="event 1")
        tl = Timeline(name="Events", timepoints=[tp])
        await store.store_timeline(tl)
        got = await store.get_timeline(tl.id)
        assert len(got.timepoints) == 1
        assert got.timepoints[0].id == tp.id


class TestMetacontextStorage:

    async def test_store_and_retrieve(self, store):
        mc = Metacontext(content="Real historical events")
        await store.store_metacontext(mc)
        got = await store.get_metacontext(mc.id)
        assert got is not None
        assert got.content == "Real historical events"

    async def test_get_missing_returns_none(self, store):
        got = await store.get_metacontext("nonexistent")
        assert got is None

    async def test_query_by_status(self, store):
        mc1 = Metacontext(content="active context")
        mc2 = Metacontext(content="old context", status=NodeStatus.SUPERSEDED)
        await store.store_metacontext(mc1)
        await store.store_metacontext(mc2)

        active = await store.query_metacontexts(status=NodeStatus.ACTIVE)
        assert len(active) == 1
        assert active[0].content == "active context"

        superseded = await store.query_metacontexts(status=NodeStatus.SUPERSEDED)
        assert len(superseded) == 1


class TestMultiGraphContract:

    async def test_current_database_is_default(self, store):
        assert store.current_database == "default"

    async def test_list_databases_returns_default(self, store):
        databases = await store.list_databases()
        assert databases == ["default"]

    async def test_switch_creates_new_graph(self, store):
        await store.switch_database("second")
        assert store.current_database == "second"
        assert "second" in await store.list_databases()

    async def test_switch_to_existing_graph(self, store):
        await store.switch_database("second")
        await store.switch_database("default")
        assert store.current_database == "default"

    async def test_graph_isolation(self, store):
        """Data stored in one graph is not visible from another."""
        topic = Topic(content="Graph A topic", source_id="seg1")
        await store.store_node(topic)

        await store.switch_database("graph-b")
        assert await store.get_node(topic.id) is None
        nodes = await store.query_nodes()
        assert len(nodes) == 0

        # Switch back and verify data is still there
        await store.switch_database("default")
        assert await store.get_node(topic.id) is not None

    async def test_delete_database(self, store):
        await store.switch_database("to-delete")
        await store.switch_database("default")
        await store.delete_database("to-delete")
        assert "to-delete" not in await store.list_databases()

    async def test_delete_active_database_raises(self, store):
        with pytest.raises(ValueError):
            await store.delete_database("default")

    async def test_delete_nonexistent_database_raises(self, store):
        with pytest.raises(KeyError):
            await store.delete_database("no-such-graph")


class TestAtomicOperations:
    """supersede_node_tx / merge_nodes_tx must be all-or-nothing."""

    async def test_supersede_tx_applies_all_writes(self, store):
        old = Topic(content="old topic", source_id="s1")
        fact = Fact(content="supporting fact", source_id="s1")
        await store.store_node(old)
        await store.store_node(fact)
        await store.store_edge(
            NodeEdge(src_id=fact.id, dst_id=old.id, type=EdgeType.SUPPORTS)
        )

        new = Topic(content="new topic", source_id="s1")
        new_emb = EmbeddingRecord(item_id=new.id, model_id="m", vector=[0.0, 1.0, 0.0])
        lineage = NodeEdge(src_id=old.id, dst_id=new.id, type=EdgeType.SUPERSEDED_BY)
        await store.supersede_node_tx(
            old, new, new_emb, lineage,             status=NodeStatus.CORRECTED,
            superseded_at=datetime.now(timezone.utc),
        )

        assert (await store.get_node(old.id)).status == NodeStatus.CORRECTED
        assert (await store.get_node(new.id)).content == "new topic"
        assert len(await store.get_embeddings_for_item(new.id)) == 1
        migrated = await store.get_edges_to(new.id, edge_type=EdgeType.SUPPORTS)
        assert len(migrated) == 1 and migrated[0].src_id == fact.id
        lin = await store.get_edges_from(old.id, edge_type=EdgeType.SUPERSEDED_BY)
        assert len(lin) == 1 and lin[0].dst_id == new.id

    async def test_supersede_tx_rolls_back_on_failure(self, store, monkeypatch):
        old = Topic(content="old", source_id="s1")
        await store.store_node(old)
        await store.store_embedding(
            EmbeddingRecord(item_id=old.id, model_id="m", vector=[1.0, 0.0, 0.0])
        )

        new = Topic(content="new", source_id="s1")
        new_emb = EmbeddingRecord(item_id=new.id, model_id="m", vector=[0.0, 1.0, 0.0])
        lineage = NodeEdge(src_id=old.id, dst_id=new.id, type=EdgeType.SUPERSEDED_BY)

        # Inject a failure after the node/embedding writes, during migration.
        def boom(*args, **kwargs):
            raise RuntimeError("injected failure")

        monkeypatch.setattr(store, "_migrate_edges_inplace", boom)

        with pytest.raises(RuntimeError, match="injected failure"):
            await store.supersede_node_tx(
                old, new, new_emb, lineage,                 status=NodeStatus.CORRECTED,
                superseded_at=datetime.now(timezone.utc),
            )

        # Nothing applied: old still active, new node/embedding/edge absent.
        assert (await store.get_node(old.id)).status == NodeStatus.ACTIVE
        assert await store.get_node(new.id) is None
        assert await store.get_embeddings_for_item(new.id) == []
        assert await store.get_edges_from(old.id) == []

    async def test_write_batch_tx_inserts_all(self, store):
        t = Topic(content="t", source_id="s1")
        f = Fact(content="f", source_id="s1")
        edge = NodeEdge(src_id=f.id, dst_id=t.id, type=EdgeType.SUPPORTS)
        emb = EmbeddingRecord(item_id=t.id, model_id="m", vector=[1.0, 0.0])

        await store.write_batch_tx(nodes=[t, f], edges=[edge], embeddings=[emb])

        assert await store.get_node(t.id) is not None
        assert await store.get_node(f.id) is not None
        assert len(await store.get_edges_to(t.id, edge_type=EdgeType.SUPPORTS)) == 1
        assert len(await store.get_embeddings_for_item(t.id)) == 1

    async def test_write_batch_tx_rolls_back_on_failure(self, store):
        t = Topic(content="t", source_id="s1")
        edge = NodeEdge(src_id="x", dst_id=t.id, type=EdgeType.SUPPORTS)
        emb = EmbeddingRecord(item_id=t.id, model_id="m", vector=[1.0])

        def boom_embeddings():
            yield emb
            raise RuntimeError("injected failure")

        with pytest.raises(RuntimeError, match="injected failure"):
            await store.write_batch_tx(
                nodes=[t], edges=[edge], embeddings=boom_embeddings()
            )

        # The node, edge, and embedding added before the failure are all undone.
        assert await store.get_node(t.id) is None
        assert await store.get_edges_to(t.id) == []
        assert await store.get_embeddings_for_item(t.id) == []

    async def test_supersede_tx_skips_review_edges(self, store):
        """Review edges (metadata) are not migrated onto the replacement."""
        old = Topic(content="old", source_id="s1")
        newer = Fact(content="newer claim", source_id="s1")
        support = Fact(content="supporting", source_id="s1")
        for node in (old, newer, support):
            await store.store_node(node)
        # A review edge flagging `old`, plus a normal supporting edge.
        await store.store_edge(
            NodeEdge(src_id=newer.id, dst_id=old.id, type=EdgeType.SUPERSESSION_CANDIDATE)
        )
        await store.store_edge(
            NodeEdge(src_id=support.id, dst_id=old.id, type=EdgeType.SUPPORTS)
        )

        new = Topic(content="new", source_id="s1")
        new_emb = EmbeddingRecord(item_id=new.id, model_id="m", vector=[0.0, 1.0])
        lineage = NodeEdge(src_id=old.id, dst_id=new.id, type=EdgeType.SUPERSEDED_BY)
        await store.supersede_node_tx(
            old, new, new_emb, lineage,             status=NodeStatus.CORRECTED,
            superseded_at=datetime.now(timezone.utc),
        )

        # Knowledge edge migrated; review edge did not.
        assert len(await store.get_edges_to(new.id, edge_type=EdgeType.SUPPORTS)) == 1
        assert await store.get_edges_to(new.id, edge_type=EdgeType.SUPERSESSION_CANDIDATE) == []
        # The review edge still points at the old (now superseded) node.
        assert len(
            await store.get_edges_to(old.id, edge_type=EdgeType.SUPERSESSION_CANDIDATE)
        ) == 1


class TestQueryChanges:
    # Fixed half-open window [W_START, W_END) with deterministic timestamps.
    W_START = datetime(2026, 6, 10, tzinfo=timezone.utc)
    W_END = datetime(2026, 6, 20, tzinfo=timezone.utc)

    async def test_returns_node_born_in_window(self, store):
        f = Fact(
            content="born inside", source_id="s1",
            created_at=datetime(2026, 6, 15, tzinfo=timezone.utc),
        )
        await store.store_node(f)
        changed = await store.query_changes(start=self.W_START, end=self.W_END)
        assert [n.id for n in changed] == [f.id]

    async def test_returns_node_retired_in_window(self, store):
        # Born before the window, retired inside it.
        t = Topic(
            content="retired inside", source_id="s1",
            created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
            status=NodeStatus.SUPERSEDED,
            superseded_at=datetime(2026, 6, 15, tzinfo=timezone.utc),
        )
        await store.store_node(t)
        changed = await store.query_changes(start=self.W_START, end=self.W_END)
        assert [n.id for n in changed] == [t.id]

    async def test_excludes_node_fully_outside(self, store):
        before = Topic(
            content="before", source_id="s1",
            created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        )
        after = Inference(
            content="after", source_id="s1",
            created_at=datetime(2026, 6, 25, tzinfo=timezone.utc),
        )
        await store.store_node(before)
        await store.store_node(after)
        changed = await store.query_changes(start=self.W_START, end=self.W_END)
        assert changed == []

    async def test_respects_node_type_filter(self, store):
        f = Fact(
            content="fact in", source_id="s1",
            created_at=datetime(2026, 6, 15, tzinfo=timezone.utc),
        )
        t = Topic(
            content="topic in", source_id="s1",
            created_at=datetime(2026, 6, 16, tzinfo=timezone.utc),
        )
        await store.store_node(f)
        await store.store_node(t)
        facts = await store.query_changes(
            start=self.W_START, end=self.W_END, node_type=NodeType.FACT
        )
        assert [n.id for n in facts] == [f.id]

    async def test_half_open_boundaries(self, store):
        # start is inclusive, end is exclusive.
        at_start = Fact(content="at start", source_id="s1", created_at=self.W_START)
        at_end = Fact(content="at end", source_id="s1", created_at=self.W_END)
        await store.store_node(at_start)
        await store.store_node(at_end)
        changed = await store.query_changes(start=self.W_START, end=self.W_END)
        assert [n.id for n in changed] == [at_start.id]


class TestSourceTopicAndRelationHelpers:

    async def test_get_node_by_content(self, store):
        t = Topic(content="BBC")
        await store.store_node(t)
        got = await store.get_node_by_content("BBC", node_type=NodeType.TOPIC)
        assert got is not None and got.id == t.id
        assert await store.get_node_by_content("missing") is None

    async def test_relabel_edges(self, store):
        a, b = Topic(content="a"), Topic(content="b")
        await store.store_node(a)
        await store.store_node(b)
        await store.store_edge(NodeEdge(
            src_id=a.id, dst_id=b.id, type=EdgeType.RELATED,
            label="written_by", kind="attribution",
        ))
        assert await store.relabel_edges("written_by", "authored_by") == 1
        assert (await store.get_edges_from(a.id))[0].label == "authored_by"

    async def test_get_relation_kind(self, store):
        a, b = Topic(content="a"), Topic(content="b")
        await store.store_node(a)
        await store.store_node(b)
        await store.store_edge(NodeEdge(
            src_id=a.id, dst_id=b.id, type=EdgeType.RELATED,
            label="funded_by", kind="attribution",
        ))
        assert await store.get_relation_kind("funded_by") == "attribution"
        assert await store.get_relation_kind("unknown") is None


class TestStoreIsolation:
    """The store must not share object identity with its callers.

    Keeping the caller's object (and handing it back out again) means a caller
    mutating a "returned" node silently rewrites the store, with no write call.
    SurrealDB round-trips through serialization and cannot behave that way, so
    the aliasing let code pass in-memory that was broken against the real
    backend — it is why the insert-only `store_*` bug stayed invisible.
    """

    async def test_mutating_returned_node_does_not_change_store(self, store):
        topic = Topic(content="original", source_id="s1")
        await store.store_node(topic)

        got = await store.get_node(topic.id)
        got.content = "mutated"
        got.value.confidence = 0.999

        again = await store.get_node(topic.id)
        assert again.content == "original"
        assert again.value.confidence != 0.999

    async def test_mutating_caller_object_after_store_does_not_change_store(self, store):
        topic = Topic(content="original", source_id="s1")
        await store.store_node(topic)

        topic.content = "mutated"
        topic.value.confidence = 0.999

        got = await store.get_node(topic.id)
        assert got.content == "original"
        assert got.value.confidence != 0.999

    async def test_mutating_queried_node_does_not_change_store(self, store):
        topic = Topic(content="original", source_id="s1")
        await store.store_node(topic)

        (await store.query_nodes())[0].content = "mutated"

        assert (await store.get_node(topic.id)).content == "original"

    async def test_mutating_returned_edge_does_not_change_store(self, store):
        a, b = Topic(content="a"), Topic(content="b")
        await store.store_node(a)
        await store.store_node(b)
        edge = NodeEdge(src_id=a.id, dst_id=b.id, type=EdgeType.RELATED, label="cites")
        await store.store_edge(edge)

        (await store.get_edges_from(a.id))[0].label = "mutated"

        assert (await store.get_edges_from(a.id))[0].label == "cites"

    async def test_mutating_returned_timeline_does_not_change_store(self, store):
        timeline = Timeline(name="tl")
        await store.store_timeline(timeline)

        got = await store.get_timeline(timeline.id)
        got.timepoints.append(Timepoint(label="smuggled"))

        assert (await store.get_timeline(timeline.id)).timepoints == []

    async def test_mutating_returned_document_does_not_change_store(self, store):
        doc = RawDocument(content="original")
        await store.store_document(doc)

        (await store.get_document(doc.id)).content = "mutated"

        assert (await store.get_document(doc.id)).content == "original"

    async def test_mutating_returned_metacontext_does_not_change_store(self, store):
        mc = Metacontext(content="frame")
        await store.store_metacontext(mc)

        (await store.get_metacontext(mc.id)).description = "mutated"

        assert (await store.get_metacontext(mc.id)).description == ""

    async def test_mutating_returned_embedding_does_not_change_store(self, store):
        emb = EmbeddingRecord(item_id="i1", model_id="m", vector=[0.1, 0.2])
        await store.store_embedding(emb)

        (await store.get_embeddings_for_item("i1"))[0].vector[0] = 0.999

        assert (await store.get_embeddings_for_item("i1"))[0].vector == [0.1, 0.2]

    async def test_write_batch_tx_does_not_alias_caller_objects(self, store):
        node = Topic(content="original", source_id="s1")
        edge_target = Topic(content="target", source_id="s1")
        edge = NodeEdge(src_id=node.id, dst_id=edge_target.id, type=EdgeType.RELATED,
                        label="cites")
        emb = EmbeddingRecord(item_id=node.id, model_id="m", vector=[0.1])

        await store.write_batch_tx(nodes=[node, edge_target], edges=[edge],
                                   embeddings=[emb])

        node.content = "mutated"
        edge.label = "mutated"
        emb.vector[0] = 0.999

        assert (await store.get_node(node.id)).content == "original"
        assert (await store.get_edges_from(node.id))[0].label == "cites"
        assert (await store.get_embeddings_for_item(node.id))[0].vector == [0.1]

    async def test_supersede_tx_does_not_alias_caller_objects(self, store):
        old = Fact(content="old", source_id="s1")
        await store.store_node(old)

        new = Fact(content="new", source_id="s1")
        new_emb = EmbeddingRecord(item_id=new.id, model_id="m", vector=[0.2])
        lineage = NodeEdge(src_id=old.id, dst_id=new.id, type=EdgeType.SUPERSEDED_BY)

        await store.supersede_node_tx(
            old_node=old,
            new_node=new,
            new_embedding=new_emb,
            lineage_edge=lineage,
            superseded_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        status=NodeStatus.CORRECTED,
    )

        new.content = "mutated"
        lineage.label = "mutated"

        assert (await store.get_node(new.id)).content == "new"
        assert (await store.get_edges_from(old.id))[0].label != "mutated"
