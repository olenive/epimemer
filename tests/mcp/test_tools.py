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
    Topic,
)
from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.llm.mock import MockDecompositionProvider
from epimemer.mcp.config import ServerConfig
from epimemer.mcp.tools import (
    add_timeline_timepoint,
    archive,
    create_metacontext,
    create_timelink,
    create_timeline,
    get_metacontexts_for_node,
    ingest,
    link,
    query_graph,
    query_timeline,
    reflect,
    restore,
    search,
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
def decomposition_provider() -> MockDecompositionProvider:
    return MockDecompositionProvider()


@pytest.fixture
def config() -> ServerConfig:
    return ServerConfig(
        storage_backend="memory",
        embedding_provider="mock",
        decomposition_provider="mock",
        segmentation_strategy="paragraph",
    )


# --- Ingest tests ---


class TestIngest:

    async def test_creates_document_segments_nodes(
        self, storage, embedding_provider, decomposition_provider, config
    ):
        result, meta = await ingest(
            "Paragraph one about ML.\n\nParagraph two about climate.",
            storage,
            embedding_provider,
            decomposition_provider,
            config,
        )
        assert result["segments_created"] == 2
        assert result["nodes_created"]["topics"] > 0
        assert result["nodes_created"]["facts"] > 0
        assert result["nodes_created"]["inferences"] > 0
        assert result["document_id"]

    async def test_creates_edges(
        self, storage, embedding_provider, decomposition_provider, config
    ):
        result, _ = await ingest(
            "Some text about neural networks.",
            storage,
            embedding_provider,
            decomposition_provider,
            config,
        )
        assert result["edges_created"] > 0

    async def test_response_counts_accurate(
        self, storage, embedding_provider, decomposition_provider, config
    ):
        result, meta = await ingest(
            "A single paragraph.",
            storage,
            embedding_provider,
            decomposition_provider,
            config,
        )
        total_nodes = sum(result["nodes_created"].values())
        assert meta.nodes_returned == total_nodes
        assert meta.source_types

    async def test_meta_has_llm_calls(
        self, storage, embedding_provider, decomposition_provider, config
    ):
        _, meta = await ingest(
            "Some content.",
            storage,
            embedding_provider,
            decomposition_provider,
            config,
        )
        # 1 segment * 3 extraction calls
        assert meta.llm_calls == 3

    async def test_embeddings_stored(
        self, storage, embedding_provider, decomposition_provider, config
    ):
        result, _ = await ingest(
            "Text about embeddings.",
            storage,
            embedding_provider,
            decomposition_provider,
            config,
        )
        # Every node should have an embedding
        total_nodes = sum(result["nodes_created"].values())
        # Check by querying a node's embeddings
        from epimemer.core.types import NodeType
        nodes = await storage.query_nodes(node_type=NodeType.TOPIC)
        for node in nodes:
            embs = await storage.get_embeddings_for_item(node.id)
            assert len(embs) >= 1

    async def test_single_paragraph_single_segment(
        self, storage, embedding_provider, decomposition_provider, config
    ):
        result, _ = await ingest(
            "Just one paragraph here.",
            storage,
            embedding_provider,
            decomposition_provider,
            config,
        )
        assert result["segments_created"] == 1


# --- Search tests ---


class TestSearch:

    async def _ingest_content(self, storage, embedding_provider, decomposition_provider, config):
        """Helper to ingest some content for search tests."""
        await ingest(
            "Machine learning models require large datasets for training.",
            storage,
            embedding_provider,
            decomposition_provider,
            config,
        )

    async def test_returns_relevant_nodes(
        self, storage, embedding_provider, decomposition_provider, config
    ):
        await self._ingest_content(storage, embedding_provider, decomposition_provider, config)
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
        self, storage, embedding_provider, decomposition_provider, config
    ):
        await self._ingest_content(storage, embedding_provider, decomposition_provider, config)
        result, _ = await search(
            "Machine learning",
            storage,
            embedding_provider,
            k=5,
            node_types=["topic"],
            graph_hops=0,  # No expansion, pure vector search
        )
        for node in result["nodes"]:
            assert node["node_type"] == "topic"

    async def test_meta_has_source_types(
        self, storage, embedding_provider, decomposition_provider, config
    ):
        await self._ingest_content(storage, embedding_provider, decomposition_provider, config)
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

    async def test_supersedes_node(self, storage):
        t = Topic(content="old content", source_id="s1")
        await storage.store_node(t)

        result, _ = await update(t.id, "new content", storage)
        assert result["old_node_id"] == t.id
        assert result["new_node_id"] != t.id

        # Old node should be superseded
        old = await storage.get_node(t.id)
        assert old.status == NodeStatus.SUPERSEDED

        # New node should exist with new content
        new = await storage.get_node(result["new_node_id"])
        assert new.content == "new content"
        assert isinstance(new, Topic)

    async def test_rejects_nonexistent_node(self, storage):
        with pytest.raises(ValueError, match="not found"):
            await update("nonexistent", "content", storage)

    async def test_preserves_node_type(self, storage):
        f = Fact(content="old fact", source_id="s1")
        await storage.store_node(f)

        result, _ = await update(f.id, "new fact", storage)
        new = await storage.get_node(result["new_node_id"])
        assert isinstance(new, Fact)


# --- Reflect tests ---


class TestReflect:

    async def test_runs_all_operations(self, storage, embedding_provider):
        # Add some topics to give reflection something to work with
        t1 = Topic(content="machine learning", source_id="s1")
        t2 = Topic(content="deep learning", source_id="s2")
        await storage.store_node(t1)
        await storage.store_node(t2)

        # Add embeddings for them
        for t in [t1, t2]:
            vecs = await embedding_provider.embed([t.content])
            await storage.store_embedding(
                EmbeddingRecord(item_id=t.id, model_id=embedding_provider.model_id, vector=vecs[0])
            )

        result, _ = await reflect(storage, embedding_provider)
        # These fields should be present even if zero
        assert "topics_merged" in result
        assert "nodes_decayed" in result
        assert "contradictions_found" in result


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

        # From t1, 0 hops: only t1 itself
        result, _ = await query_graph(t1.id, storage, hops=0)
        assert len(result["nodes"]) == 1

    async def test_rejects_nonexistent_node(self, storage):
        with pytest.raises(ValueError, match="not found"):
            await query_graph("nonexistent", storage)


# --- Archive tests ---


class TestArchive:

    async def test_finds_old_superseded_nodes(self, storage):
        from datetime import datetime, timedelta, timezone

        t = Topic(content="old topic", source_id="s1")
        await storage.store_node(t)
        # Mark as superseded long ago
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
        # Create a node and a timeline with a timepoint
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

        # Create timelink
        result, _ = await create_timelink(t.id, tl_id, tp_id, storage)
        assert result["edge_id"]
        assert result["timepoint_id"] == tp_id

        # Verify edge exists
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
        # Create metacontext and node
        mc = Metacontext(content="Fictional")
        await storage.store_metacontext(mc)

        t = Topic(content="Vampires", source_id="s1")
        await storage.store_node(t)

        # Link them
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


# --- Ingest with metacontext ---


class TestIngestWithMetacontext:

    async def test_ingest_assigns_metacontext(
        self, storage, embedding_provider, decomposition_provider, config
    ):
        # Create a metacontext first
        mc = Metacontext(content="Science fiction")
        await storage.store_metacontext(mc)

        result, _ = await ingest(
            "The Culture ships are sentient AIs.",
            storage, embedding_provider, decomposition_provider, config,
            metacontext_id=mc.id,
        )

        # All created nodes should have HAS_METACONTEXT edges
        from epimemer.core.types import NodeType
        topics = await storage.query_nodes(node_type=NodeType.TOPIC)
        for topic in topics:
            edges = await storage.get_edges_from(topic.id)
            mc_edges = [e for e in edges if e.type == EdgeType.HAS_METACONTEXT]
            assert len(mc_edges) == 1
            assert mc_edges[0].dst_id == mc.id


# --- Search with metacontext ---


class TestSearchWithMetacontext:

    async def test_search_filtered_by_metacontext(
        self, storage, embedding_provider, decomposition_provider, config
    ):
        # Create two metacontexts
        mc_real = Metacontext(content="Real world")
        mc_fiction = Metacontext(content="Fiction")
        await storage.store_metacontext(mc_real)
        await storage.store_metacontext(mc_fiction)

        # Ingest with different metacontexts
        await ingest(
            "Neural networks are used in image recognition.",
            storage, embedding_provider, decomposition_provider, config,
            metacontext_id=mc_real.id,
        )
        await ingest(
            "The neural lace allows direct brain-computer interface.",
            storage, embedding_provider, decomposition_provider, config,
            metacontext_id=mc_fiction.id,
        )

        # Search filtered to real world only
        result, meta = await search(
            "Neural networks",
            storage, embedding_provider,
            k=10, graph_hops=0,
            metacontext_id=mc_real.id,
        )

        # All returned nodes should have "Real world" metacontext
        for node in result["nodes"]:
            assert "metacontexts" in node
            assert "Real world" in node["metacontexts"]
