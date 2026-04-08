"""Tests for in-memory storage backend."""

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
        await store.update_node_status(t.id, NodeStatus.SUPERSEDED)
        active = await store.query_nodes(status=NodeStatus.ACTIVE)
        superseded = await store.query_nodes(status=NodeStatus.SUPERSEDED)
        assert len(active) == 0
        assert len(superseded) == 1

    async def test_update_nonexistent_raises(self, store):
        with pytest.raises(KeyError):
            await store.update_node_status("nonexistent", NodeStatus.SUPERSEDED)


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

    async def test_does_not_support_multi_graph(self, store):
        assert store.supports_multi_graph is False

    async def test_current_database_is_ephemeral(self, store):
        assert store.current_database == "ephemeral"

    async def test_list_databases_returns_ephemeral(self, store):
        databases = await store.list_databases()
        assert databases == ["ephemeral"]

    async def test_switch_database_raises(self, store):
        with pytest.raises(NotImplementedError):
            await store.switch_database("other")

    async def test_delete_database_raises(self, store):
        with pytest.raises(NotImplementedError):
            await store.delete_database("ephemeral")
