"""Tests for MCP tool implementations.

Tests call the pure functions in epimemer.mcp.tools directly
with InMemoryStorage + mock providers.
"""

import pytest

from datetime import datetime, timezone

from epimemer.core.types import (
    EdgeType,
    EmbeddingRecord,
    Fact,
    Inference,
    Metacontext,
    NodeEdge,
    NodeStatus,
    NodeType,
    Topic,
)
from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.mcp.config import ServerConfig
from epimemer.mcp.tools import (
    add_timeline_timepoint,
    apply_reflection,
    archive,
    create_metacontext,
    create_timelink,
    create_timeline,
    get_metacontexts_for_node,
    graph_stats,
    link,
    query_graph,
    query_timeline,
    reflect,
    restore,
    search,
    segment_text,
    store_decomposition,
    update,
)
from epimemer.storage.memory import InMemoryStorage


# --- Fixtures ---


@pytest.fixture
def storage() -> InMemoryStorage:
    return InMemoryStorage()


@pytest.fixture
def embedding_provider() -> MockEmbeddingProvider:
    return MockEmbeddingProvider(model_id="mock-embed", dimension=8)


@pytest.fixture
def config() -> ServerConfig:
    return ServerConfig(
        storage_backend="memory",
        embedding_provider="mock",
        segmentation_strategy="paragraph",
    )


# --- Helpers ---


async def _two_step_ingest(
    content: str,
    storage: InMemoryStorage,
    embedding_provider: MockEmbeddingProvider,
    config: ServerConfig,
    *,
    metacontext_id: str | None = None,
) -> tuple[dict, dict]:
    """Run the two-step ingest: segment then store decomposition with dummy extraction."""
    seg_result, seg_meta = await segment_text(
        content, storage, embedding_provider, config,
    )
    segments = [
        {
            "segment_id": s["segment_id"],
            "topics": [f"Topic about: {s['segment_id']}"],
            "facts": [f"Fact from: {s['segment_id']}"],
            "inferences": [f"Inference from: {s['segment_id']}"],
        }
        for s in seg_result["segments"]
    ]
    store_result, store_meta = await store_decomposition(
        document_id=seg_result["document_id"],
        segments=segments,
        storage=storage,
        embedding_provider=embedding_provider,
        metacontext_id=metacontext_id,
    )
    return seg_result, store_result


# --- Segment tests ---


class TestSegment:

    async def test_segments_text_and_stores_document(
        self, storage, embedding_provider, config
    ):
        result, meta = await segment_text(
            "Paragraph one about ML.\n\nParagraph two about climate.",
            storage, embedding_provider, config,
        )
        assert len(result["segments"]) == 2
        assert result["document_id"]
        # Document should be stored
        doc = await storage.get_document(result["document_id"])
        assert doc is not None

    async def test_segments_stored_in_storage(
        self, storage, embedding_provider, config
    ):
        result, _ = await segment_text(
            "First.\n\nSecond.",
            storage, embedding_provider, config,
        )
        stored = await storage.get_segments_for_document(result["document_id"])
        assert len(stored) == 2

    async def test_single_paragraph(
        self, storage, embedding_provider, config
    ):
        result, _ = await segment_text(
            "Just one paragraph here.",
            storage, embedding_provider, config,
        )
        assert len(result["segments"]) == 1


# --- Store Decomposition tests ---


class TestStoreDecomposition:

    async def test_creates_nodes_and_edges(
        self, storage, embedding_provider, config
    ):
        _, store_result = await _two_step_ingest(
            "Paragraph one about ML.\n\nParagraph two about climate.",
            storage, embedding_provider, config,
        )
        assert store_result["nodes_created"]["topics"] == 2
        assert store_result["nodes_created"]["facts"] == 2
        assert store_result["nodes_created"]["inferences"] == 2
        assert store_result["edges_created"] > 0

    async def test_embeddings_stored(
        self, storage, embedding_provider, config
    ):
        await _two_step_ingest(
            "Text about embeddings.",
            storage, embedding_provider, config,
        )
        from epimemer.core.types import NodeType
        topics = await storage.query_nodes(node_type=NodeType.TOPIC)
        for topic in topics:
            embs = await storage.get_embeddings_for_item(topic.id)
            assert len(embs) >= 1

    async def test_response_counts_accurate(
        self, storage, embedding_provider, config
    ):
        _, store_result = await _two_step_ingest(
            "A single paragraph.",
            storage, embedding_provider, config,
        )
        total_nodes = sum(store_result["nodes_created"].values())
        assert total_nodes == 3  # 1 topic + 1 fact + 1 inference

    async def test_with_metacontext(
        self, storage, embedding_provider, config
    ):
        mc = Metacontext(content="Science fiction")
        await storage.store_metacontext(mc)

        await _two_step_ingest(
            "The Culture ships are sentient AIs.",
            storage, embedding_provider, config,
            metacontext_id=mc.id,
        )

        from epimemer.core.types import NodeType
        topics = await storage.query_nodes(node_type=NodeType.TOPIC)
        for topic in topics:
            edges = await storage.get_edges_from(topic.id)
            mc_edges = [e for e in edges if e.type == EdgeType.HAS_METACONTEXT]
            assert len(mc_edges) == 1
            assert mc_edges[0].dst_id == mc.id


# --- Search tests ---


class TestSearch:

    async def _ingest_content(self, storage, embedding_provider, config):
        await _two_step_ingest(
            "Machine learning models require large datasets for training.",
            storage, embedding_provider, config,
        )

    async def test_returns_relevant_nodes(
        self, storage, embedding_provider, config
    ):
        await self._ingest_content(storage, embedding_provider, config)
        result, meta = await search(
            "Machine learning models require large datasets for training.",
            storage,
            embedding_provider,
            k=5,
            graph_hops=1,
        )
        assert len(result["nodes"]) > 0
        assert meta.nodes_returned > 0

    async def test_respects_node_type_filter(
        self, storage, embedding_provider, config
    ):
        await self._ingest_content(storage, embedding_provider, config)
        result, _ = await search(
            "Machine learning",
            storage,
            embedding_provider,
            k=5,
            node_types=["topic"],
            graph_hops=0,
        )
        for node in result["nodes"]:
            assert node["node_type"] == "topic"

    async def test_meta_has_source_types(
        self, storage, embedding_provider, config
    ):
        await self._ingest_content(storage, embedding_provider, config)
        _, meta = await search(
            "Machine learning",
            storage,
            embedding_provider,
        )
        assert isinstance(meta.source_types, dict)


# --- Link tests ---


class TestLink:

    async def test_creates_edge(self, storage):
        t = Topic(content="topic A", source_id="s1")
        f = Fact(content="fact B", source_id="s1")
        await storage.store_node(t)
        await storage.store_node(f)

        result, _ = await link(
            t.id, f.id, "supports", storage,
        )
        assert "edge_id" in result

        edges = await storage.get_edges_from(t.id)
        assert any(e.type == EdgeType.SUPPORTS for e in edges)

    async def test_rejects_invalid_edge_type(self, storage):
        t = Topic(content="topic", source_id="s1")
        await storage.store_node(t)

        with pytest.raises(ValueError, match="Invalid edge_type"):
            await link(t.id, t.id, "not_a_real_type", storage)

    async def test_rejects_nonexistent_source(self, storage):
        t = Topic(content="topic", source_id="s1")
        await storage.store_node(t)

        with pytest.raises(ValueError, match="Source node"):
            await link("nonexistent", t.id, "supports", storage)

    async def test_rejects_nonexistent_destination(self, storage):
        t = Topic(content="topic", source_id="s1")
        await storage.store_node(t)

        with pytest.raises(ValueError, match="Destination node"):
            await link(t.id, "nonexistent", "supports", storage)


# --- Update tests ---


class TestUpdate:

    async def test_supersedes_node(self, storage, embedding_provider):
        t = Topic(content="old content", source_id="s1")
        await storage.store_node(t)

        result, _ = await update(t.id, "new content", storage, embedding_provider)
        assert result["old_node_id"] == t.id
        assert result["new_node_id"] != t.id

        old = await storage.get_node(t.id)
        assert old.status == NodeStatus.SUPERSEDED

        new = await storage.get_node(result["new_node_id"])
        assert new.content == "new content"
        assert isinstance(new, Topic)

    async def test_rejects_nonexistent_node(self, storage, embedding_provider):
        with pytest.raises(ValueError, match="not found"):
            await update("nonexistent", "content", storage, embedding_provider)

    async def test_preserves_node_type(self, storage, embedding_provider):
        f = Fact(content="old fact", source_id="s1")
        await storage.store_node(f)

        result, _ = await update(f.id, "new fact", storage, embedding_provider)
        new = await storage.get_node(result["new_node_id"])
        assert isinstance(new, Fact)

    async def test_preserves_value_signal(self, storage, embedding_provider):
        t = Topic(content="old content", source_id="s1")
        t.value.confidence = 0.9
        t.value.relevance = 0.8
        t.value.novelty = 0.3
        await storage.store_node(t)

        result, _ = await update(t.id, "new content", storage, embedding_provider)
        new = await storage.get_node(result["new_node_id"])

        # A content correction must not reset reinforcement history.
        assert new.value.confidence == 0.9
        assert new.value.relevance == 0.8
        assert new.value.novelty == 0.3

        # The signal is copied, not shared: reinforcing the correction must not
        # rewrite the superseded original's recorded value.
        new.value.confidence = 0.1
        old = await storage.get_node(t.id)
        assert old.value.confidence == 0.9


# --- Reflect tests ---


class TestReflect:

    async def test_runs_all_operations(self, storage, embedding_provider):
        t1 = Topic(content="machine learning", source_id="s1")
        t2 = Topic(content="deep learning", source_id="s2")
        await storage.store_node(t1)
        await storage.store_node(t2)

        for t in [t1, t2]:
            vecs = await embedding_provider.embed([t.content])
            await storage.store_embedding(
                EmbeddingRecord(item_id=t.id, model_id=embedding_provider.model_id, vector=vecs[0])
            )

        result, _ = await reflect(storage, embedding_provider)
        assert "similar_pairs" in result
        assert "nodes_decayed" in result
        assert "contradictions" in result


# --- Apply Reflection (merge) tests ---


class TestApplyReflectionMerge:

    async def _store_topic(self, storage, embedding_provider, content, vector):
        t = Topic(content=content, source_id="s1")
        await storage.store_node(t)
        await storage.store_embedding(
            EmbeddingRecord(
                item_id=t.id, model_id=embedding_provider.model_id, vector=vector
            )
        )
        return t

    async def test_merge_collapses_near_duplicates(self, storage, embedding_provider):
        a = await self._store_topic(storage, embedding_provider, "ML basics", [1.0, 0.0])
        b = await self._store_topic(
            storage, embedding_provider, "Machine learning basics", [1.0, 0.0]
        )

        result, _ = await apply_reflection(
            storage, embedding_provider,
            merges=[{"source_ids": [a.id, b.id], "content": "Machine learning basics"}],
            merge_similarity_threshold=0.9,
        )

        assert result["topics_merged"] == 1
        assert result["merges_rejected"] == 0
        assert (await storage.get_node(a.id)).status == NodeStatus.MERGED
        assert (await storage.get_node(b.id)).status == NodeStatus.MERGED
        # Exactly one active topic remains — the merged node — and it is embedded.
        actives = await storage.query_nodes(node_type=NodeType.TOPIC)
        assert len(actives) == 1
        assert actives[0].content == "Machine learning basics"
        assert len(await storage.get_embeddings_for_item(actives[0].id)) == 1

    async def test_merge_rejected_below_threshold(self, storage, embedding_provider):
        a = await self._store_topic(storage, embedding_provider, "ML", [1.0, 0.0])
        b = await self._store_topic(storage, embedding_provider, "Cooking", [0.0, 1.0])

        result, _ = await apply_reflection(
            storage, embedding_provider,
            merges=[{"source_ids": [a.id, b.id], "content": "X"}],
            merge_similarity_threshold=0.9,
        )

        assert result["topics_merged"] == 0
        assert result["merges_rejected"] == 1
        # Distinct topics are left untouched and active.
        assert (await storage.get_node(a.id)).status == NodeStatus.ACTIVE
        assert (await storage.get_node(b.id)).status == NodeStatus.ACTIVE

    async def test_merge_refused_without_embeddings(self, storage, embedding_provider):
        # Similarity cannot be verified without embeddings → refuse.
        a = Topic(content="A", source_id="s1")
        b = Topic(content="B", source_id="s2")
        await storage.store_node(a)
        await storage.store_node(b)

        result, _ = await apply_reflection(
            storage, embedding_provider,
            merges=[{"source_ids": [a.id, b.id], "content": "AB"}],
        )

        assert result["topics_merged"] == 0
        assert result["merges_rejected"] == 1
        assert (await storage.get_node(a.id)).status == NodeStatus.ACTIVE


# --- Query Graph tests ---


class TestQueryGraph:

    async def test_returns_neighbor_subgraph(self, storage):
        t = Topic(content="topic", source_id="s1")
        f = Fact(content="fact", source_id="s1")
        await storage.store_node(t)
        await storage.store_node(f)
        await storage.store_edge(
            NodeEdge(src_id=f.id, dst_id=t.id, type=EdgeType.SUPPORTS)
        )

        result, meta = await query_graph(t.id, storage, hops=1)
        node_ids = {n["id"] for n in result["nodes"]}
        assert t.id in node_ids
        assert f.id in node_ids
        assert meta.nodes_returned == 2
        assert meta.graph_hops == 1

    async def test_respects_hop_limit(self, storage):
        t1 = Topic(content="t1", source_id="s1")
        t2 = Topic(content="t2", source_id="s2")
        f = Fact(content="fact", source_id="s1")
        await storage.store_node(t1)
        await storage.store_node(t2)
        await storage.store_node(f)
        await storage.store_edge(
            NodeEdge(src_id=f.id, dst_id=t1.id, type=EdgeType.SUPPORTS)
        )
        await storage.store_edge(
            NodeEdge(src_id=f.id, dst_id=t2.id, type=EdgeType.SUPPORTS)
        )

        result, _ = await query_graph(t1.id, storage, hops=0)
        assert len(result["nodes"]) == 1

    async def test_rejects_nonexistent_node(self, storage):
        with pytest.raises(ValueError, match="not found"):
            await query_graph("nonexistent", storage)


# --- Archive tests ---


class TestArchive:

    async def test_finds_old_superseded_nodes(self, storage):
        from datetime import timedelta

        t = Topic(content="old topic", source_id="s1")
        await storage.store_node(t)
        old_time = datetime.now(timezone.utc) - timedelta(days=200)
        await storage.update_node_status(t.id, NodeStatus.SUPERSEDED, superseded_at=old_time)

        result, meta = await archive(storage, max_age_days=90)
        assert result["nodes_archived"] == 1
        assert len(result["archive_data"]["nodes"]) == 1

    async def test_excludes_active_nodes(self, storage):
        t = Topic(content="active topic", source_id="s1")
        await storage.store_node(t)

        result, _ = await archive(storage, max_age_days=0)
        assert result["nodes_archived"] == 0


# --- Restore tests ---


class TestRestore:

    async def test_reimports_nodes(self, storage):
        archive_data = {
            "nodes": [
                {
                    "id": "restored-1",
                    "content": "restored topic",
                    "source_id": "s1",
                    "node_type": "topic",
                    "status": "active",
                    "created_at": "2026-01-01T00:00:00Z",
                }
            ],
            "edges": [
                {
                    "id": "edge-r1",
                    "src_id": "restored-1",
                    "dst_id": "other",
                    "type": "supports",
                }
            ],
        }
        result, meta = await restore(archive_data, storage)
        assert result["nodes_restored"] == 1
        assert result["edges_restored"] == 1

        node = await storage.get_node("restored-1")
        assert node is not None
        assert node.content == "restored topic"


# --- Timeline tool tests ---


class TestTimelineTools:

    async def test_create_timeline(self, storage):
        result, meta = await create_timeline("AI History", storage, description="Key AI events")
        assert result["name"] == "AI History"
        assert result["timeline_id"]

        tl = await storage.get_timeline(result["timeline_id"])
        assert tl is not None
        assert tl.name == "AI History"

    async def test_add_timepoint(self, storage):
        result, _ = await create_timeline("test", storage)
        tl_id = result["timeline_id"]

        result, _ = await add_timeline_timepoint(
            tl_id, storage,
            start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            label="New Year 2024",
        )
        assert result["timepoint_id"]
        assert result["timepoints_count"] == 1

    async def test_add_multiple_timepoints_sorted(self, storage):
        result, _ = await create_timeline("test", storage)
        tl_id = result["timeline_id"]

        await add_timeline_timepoint(tl_id, storage, start=datetime(2024, 6, 1, tzinfo=timezone.utc), label="June")
        await add_timeline_timepoint(tl_id, storage, start=datetime(2024, 1, 1, tzinfo=timezone.utc), label="Jan")

        tl = await storage.get_timeline(tl_id)
        assert tl.timepoints[0].label == "Jan"
        assert tl.timepoints[1].label == "June"

    async def test_add_timepoint_nonexistent_timeline(self, storage):
        with pytest.raises(ValueError, match="not found"):
            await add_timeline_timepoint("nonexistent", storage, label="X")

    async def test_query_nearest(self, storage):
        result, _ = await create_timeline("test", storage)
        tl_id = result["timeline_id"]

        await add_timeline_timepoint(tl_id, storage, start=datetime(2020, 1, 1, tzinfo=timezone.utc), label="2020")
        await add_timeline_timepoint(tl_id, storage, start=datetime(2024, 1, 1, tzinfo=timezone.utc), label="2024")

        result, meta = await query_timeline(
            tl_id, storage,
            target=datetime(2023, 1, 1, tzinfo=timezone.utc),
            k=1,
        )
        assert len(result["timepoints"]) == 1

    async def test_query_range(self, storage):
        result, _ = await create_timeline("test", storage)
        tl_id = result["timeline_id"]

        await add_timeline_timepoint(tl_id, storage, start=datetime(2020, 1, 1, tzinfo=timezone.utc), label="2020")
        await add_timeline_timepoint(tl_id, storage, start=datetime(2022, 1, 1, tzinfo=timezone.utc), label="2022")
        await add_timeline_timepoint(tl_id, storage, start=datetime(2024, 1, 1, tzinfo=timezone.utc), label="2024")

        result, _ = await query_timeline(
            tl_id, storage,
            range_start=datetime(2021, 1, 1, tzinfo=timezone.utc),
            range_end=datetime(2023, 1, 1, tzinfo=timezone.utc),
        )
        assert len(result["timepoints"]) == 1  # Only 2022

    async def test_create_timelink(self, storage):
        t = Topic(content="AI topic", source_id="s1")
        await storage.store_node(t)

        tl_result, _ = await create_timeline("AI Timeline", storage)
        tl_id = tl_result["timeline_id"]
        tp_result, _ = await add_timeline_timepoint(
            tl_id, storage,
            start=datetime(2023, 3, 1, tzinfo=timezone.utc),
            label="GPT-4 release",
        )
        tp_id = tp_result["timepoint_id"]

        result, _ = await create_timelink(t.id, tl_id, tp_id, storage)
        assert result["edge_id"]
        assert result["timepoint_id"] == tp_id

        edges = await storage.get_edges_from(t.id)
        tl_edges = [e for e in edges if e.type == EdgeType.TIMELINK]
        assert len(tl_edges) == 1
        assert tl_edges[0].metadata["timepoint_id"] == tp_id

    async def test_create_timelink_nonexistent_node(self, storage):
        tl_result, _ = await create_timeline("test", storage)
        with pytest.raises(ValueError, match="Node"):
            await create_timelink("nonexistent", tl_result["timeline_id"], "tp-1", storage)

    async def test_create_timelink_nonexistent_timepoint(self, storage):
        t = Topic(content="topic", source_id="s1")
        await storage.store_node(t)
        tl_result, _ = await create_timeline("test", storage)
        with pytest.raises(ValueError, match="Timepoint"):
            await create_timelink(t.id, tl_result["timeline_id"], "nonexistent", storage)


# --- Metacontext tool tests ---


class TestMetacontextTools:

    async def test_create_metacontext(self, storage):
        result, _ = await create_metacontext("Real historical events", storage)
        assert result["content"] == "Real historical events"
        assert result["metacontext_id"]

        mc = await storage.get_metacontext(result["metacontext_id"])
        assert mc is not None

    async def test_get_metacontexts_for_node(self, storage):
        mc = Metacontext(content="Fictional")
        await storage.store_metacontext(mc)

        t = Topic(content="Vampires", source_id="s1")
        await storage.store_node(t)

        await storage.store_edge(NodeEdge(
            src_id=t.id, dst_id=mc.id, type=EdgeType.HAS_METACONTEXT,
        ))

        result, meta = await get_metacontexts_for_node(t.id, storage)
        assert len(result["metacontexts"]) == 1
        assert result["metacontexts"][0]["content"] == "Fictional"

    async def test_get_metacontexts_empty(self, storage):
        t = Topic(content="topic", source_id="s1")
        await storage.store_node(t)

        result, _ = await get_metacontexts_for_node(t.id, storage)
        assert result["metacontexts"] == []

    async def test_ensure_base_metacontext_reserved_and_idempotent(self, storage):
        from epimemer.core.types import BASE_METACONTEXT_ID
        from epimemer.mcp.tools import ensure_base_metacontext

        mc1 = await ensure_base_metacontext(storage)
        assert mc1.id == BASE_METACONTEXT_ID
        assert mc1.content == "The Real"

        mc2 = await ensure_base_metacontext(storage)
        assert mc2.id == mc1.id
        all_mcs = await storage.query_metacontexts()
        assert sum(1 for m in all_mcs if m.id == BASE_METACONTEXT_ID) == 1


class TestReviewEdgeTraversal:

    async def test_review_edges_hidden_from_default_traversal(self, storage):
        a = Fact(content="a", source_id="s1")
        b = Fact(content="b", source_id="s1")
        await storage.store_node(a)
        await storage.store_node(b)
        await storage.store_edge(
            NodeEdge(src_id=b.id, dst_id=a.id, type=EdgeType.SUPERSESSION_CANDIDATE)
        )

        # Default traversal treats the review edge as metadata — not followed.
        result, _ = await query_graph(a.id, storage, hops=1)
        assert b.id not in {n["id"] for n in result["nodes"]}

        # ...but it is reachable with an explicit edge_types filter.
        result2, _ = await query_graph(
            a.id, storage, hops=1, edge_types=["supersession_candidate"]
        )
        assert b.id in {n["id"] for n in result2["nodes"]}


# --- Search with metacontext ---


class TestSearchWithMetacontext:

    async def test_search_filtered_by_metacontext(
        self, storage, embedding_provider, config
    ):
        mc_real = Metacontext(content="Real world")
        mc_fiction = Metacontext(content="Fiction")
        await storage.store_metacontext(mc_real)
        await storage.store_metacontext(mc_fiction)

        await _two_step_ingest(
            "Neural networks are used in image recognition.",
            storage, embedding_provider, config,
            metacontext_id=mc_real.id,
        )
        await _two_step_ingest(
            "The neural lace allows direct brain-computer interface.",
            storage, embedding_provider, config,
            metacontext_id=mc_fiction.id,
        )

        result, meta = await search(
            "Neural networks",
            storage, embedding_provider,
            k=10, graph_hops=0,
            metacontext_id=mc_real.id,
        )

        for node in result["nodes"]:
            assert "metacontexts" in node
            assert "Real world" in node["metacontexts"]


class TestGraphStats:

    async def test_empty_graph(self, storage):
        result, meta = await graph_stats(storage)
        assert result["total_nodes"] == 0
        assert result["total_edges"] == 0
        assert result["empty"] is True
        assert result["nodes_by_type"] == {"topic": 0, "fact": 0, "inference": 0}
        assert result["edges_by_type"] == {}
        assert result["metacontexts"] == 0
        assert meta.nodes_returned == 0

    async def test_counts_nodes_and_edges_by_type(self, storage):
        topic = Topic(content="t", source_id="s1")
        fact_a = Fact(content="f1", source_id="s1")
        fact_b = Fact(content="f2", source_id="s1")
        inference = Inference(content="i", source_id="s1")
        for node in (topic, fact_a, fact_b, inference):
            await storage.store_node(node)
        await storage.store_edge(
            NodeEdge(src_id=fact_a.id, dst_id=topic.id, type=EdgeType.SUPPORTS)
        )
        await storage.store_edge(
            NodeEdge(src_id=fact_b.id, dst_id=topic.id, type=EdgeType.SUPPORTS)
        )
        await storage.store_edge(
            NodeEdge(src_id=inference.id, dst_id=fact_a.id, type=EdgeType.DERIVED_FROM)
        )

        result, meta = await graph_stats(storage)
        assert result["total_nodes"] == 4
        assert result["nodes_by_type"] == {"topic": 1, "fact": 2, "inference": 1}
        assert result["total_edges"] == 3
        assert result["edges_by_type"] == {"supports": 2, "derived_from": 1}
        assert result["empty"] is False
        assert meta.nodes_returned == 4

    async def test_excludes_superseded_nodes(self, storage):
        topic = Topic(content="t", source_id="s1")
        await storage.store_node(topic)
        await storage.update_node_status(topic.id, NodeStatus.SUPERSEDED)

        result, _ = await graph_stats(storage)
        assert result["nodes_by_type"]["topic"] == 0
        assert result["total_nodes"] == 0

    async def test_counts_metacontexts(self, storage):
        await storage.store_metacontext(Metacontext(content="Real world"))
        await storage.store_metacontext(Metacontext(content="Fiction"))

        result, _ = await graph_stats(storage)
        assert result["metacontexts"] == 2
