"""End-to-end integration tests for the full Epimemer pipeline.

Exercises the complete flow: ingest → search → reflect → update →
timeline → metacontext → archive → restore, using InMemoryStorage
and mock providers.
"""

from datetime import datetime, timedelta, timezone

import pytest

from epimemer.core.types import (
    EdgeType,
    Metacontext,
    NodeStatus,
    NodeType,
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
    query_graph,
    query_timeline,
    reflect,
    restore,
    search,
    update,
)
from epimemer.storage.memory import InMemoryStorage


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
    )


class TestFullPipeline:

    async def test_ingest_search_cycle(
        self, storage, embedding_provider, decomposition_provider, config
    ):
        """Ingest content, then search and find it."""
        # Ingest
        ingest_result, ingest_meta = await ingest(
            "Machine learning models use gradient descent for optimization.",
            storage, embedding_provider, decomposition_provider, config,
        )
        assert ingest_result["segments_created"] >= 1
        assert ingest_meta.llm_calls > 0

        # Search
        search_result, search_meta = await search(
            "Machine learning models use gradient descent for optimization.",
            storage, embedding_provider,
            k=5, graph_hops=1,
        )
        assert len(search_result["nodes"]) > 0
        assert search_meta.nodes_returned > 0

    async def test_ingest_reflect_consolidation(
        self, storage, embedding_provider, decomposition_provider, config
    ):
        """Ingest multiple docs, run reflection."""
        await ingest(
            "Deep learning uses neural networks.",
            storage, embedding_provider, decomposition_provider, config,
        )
        await ingest(
            "Artificial intelligence includes machine learning.",
            storage, embedding_provider, decomposition_provider, config,
        )

        reflect_result, _ = await reflect(storage, embedding_provider)
        assert "topics_merged" in reflect_result
        assert "nodes_decayed" in reflect_result
        assert "contradictions_found" in reflect_result

    async def test_update_creates_version_history(
        self, storage, embedding_provider, decomposition_provider, config
    ):
        """Ingest, find a node, update it, verify versioning."""
        await ingest(
            "Python is a programming language.",
            storage, embedding_provider, decomposition_provider, config,
        )

        # Get a topic node
        topics = await storage.query_nodes(node_type=NodeType.TOPIC)
        assert len(topics) > 0
        topic = topics[0]

        # Update it
        update_result, _ = await update(topic.id, "Python is a versatile language", storage)
        assert update_result["old_node_id"] == topic.id
        assert update_result["new_node_id"] != topic.id

        # Old node superseded
        old = await storage.get_node(topic.id)
        assert old.status == NodeStatus.SUPERSEDED

        # History edge exists from old → new
        edges_from_old = await storage.get_edges_from(topic.id)
        history_edges = [e for e in edges_from_old if e.type == EdgeType.SUPERSEDED_BY]
        assert len(history_edges) == 1
        assert history_edges[0].dst_id == update_result["new_node_id"]

    async def test_timeline_workflow(
        self, storage, embedding_provider, decomposition_provider, config
    ):
        """Create timeline, add timepoints, link to nodes, query."""
        # Ingest some content
        await ingest(
            "GPT-4 was released in March 2023.",
            storage, embedding_provider, decomposition_provider, config,
        )

        # Create timeline
        tl_result, _ = await create_timeline("AI Releases", storage)
        tl_id = tl_result["timeline_id"]

        # Add timepoints
        tp_result, _ = await add_timeline_timepoint(
            tl_id, storage,
            start=datetime(2023, 3, 14, tzinfo=timezone.utc),
            label="GPT-4 release",
        )
        tp_id = tp_result["timepoint_id"]

        await add_timeline_timepoint(
            tl_id, storage,
            start=datetime(2024, 5, 13, tzinfo=timezone.utc),
            label="GPT-4o release",
        )

        # Link a fact to a timepoint
        facts = await storage.query_nodes(node_type=NodeType.FACT)
        if facts:
            link_result, _ = await create_timelink(facts[0].id, tl_id, tp_id, storage)
            assert link_result["timepoint_id"] == tp_id

        # Query timeline
        query_result, _ = await query_timeline(
            tl_id, storage,
            target=datetime(2023, 6, 1, tzinfo=timezone.utc),
            k=1,
        )
        assert len(query_result["timepoints"]) == 1

    async def test_metacontext_workflow(
        self, storage, embedding_provider, decomposition_provider, config
    ):
        """Create metacontexts, ingest with context, search with filter."""
        # Create metacontexts
        mc_real, _ = await create_metacontext("Real world", storage)
        mc_fiction, _ = await create_metacontext("Science fiction", storage)

        # Ingest with different metacontexts
        await ingest(
            "Neural networks process information in layers.",
            storage, embedding_provider, decomposition_provider, config,
            metacontext_id=mc_real["metacontext_id"],
        )
        await ingest(
            "The neural lace connects directly to the brain.",
            storage, embedding_provider, decomposition_provider, config,
            metacontext_id=mc_fiction["metacontext_id"],
        )

        # Search filtered to real world
        real_results, _ = await search(
            "Neural",
            storage, embedding_provider,
            k=10, graph_hops=0,
            metacontext_id=mc_real["metacontext_id"],
        )

        # All results should have "Real world" metacontext
        for node in real_results["nodes"]:
            assert "metacontexts" in node
            assert "Real world" in node["metacontexts"]

        # Verify metacontexts are retrievable for individual nodes
        if real_results["nodes"]:
            node_id = real_results["nodes"][0]["id"]
            mc_result, _ = await get_metacontexts_for_node(node_id, storage)
            assert len(mc_result["metacontexts"]) > 0

    async def test_archive_restore_cycle(
        self, storage, embedding_provider, decomposition_provider, config
    ):
        """Ingest, supersede, archive old nodes, restore them."""
        await ingest(
            "Old information about computing.",
            storage, embedding_provider, decomposition_provider, config,
        )

        # Supersede a node
        topics = await storage.query_nodes(node_type=NodeType.TOPIC)
        assert len(topics) > 0
        old_time = datetime.now(timezone.utc) - timedelta(days=200)
        await storage.update_node_status(
            topics[0].id, NodeStatus.SUPERSEDED, superseded_at=old_time,
        )

        # Archive
        archive_result, _ = await archive(storage, max_age_days=90)
        assert archive_result["nodes_archived"] >= 1
        archive_data = archive_result["archive_data"]

        # Restore into a fresh storage
        fresh_storage = InMemoryStorage()
        restore_result, _ = await restore(archive_data, fresh_storage)
        assert restore_result["nodes_restored"] >= 1

    async def test_full_sequence(
        self, storage, embedding_provider, decomposition_provider, config
    ):
        """Run a realistic sequence of operations."""
        # 1. Create metacontext
        mc, _ = await create_metacontext("Technical documentation", storage)
        mc_id = mc["metacontext_id"]

        # 2. Ingest multiple documents
        docs = [
            "Python supports multiple programming paradigms.",
            "Rust provides memory safety without garbage collection.",
            "TypeScript adds static typing to JavaScript.",
        ]
        for doc in docs:
            await ingest(
                doc, storage, embedding_provider, decomposition_provider, config,
                metacontext_id=mc_id,
            )

        # 3. Create a timeline
        tl, _ = await create_timeline("Language Evolution", storage)
        tl_id = tl["timeline_id"]
        await add_timeline_timepoint(tl_id, storage, start=datetime(1991, 1, 1, tzinfo=timezone.utc), label="Python created")
        tp_result, _ = await add_timeline_timepoint(tl_id, storage, start=datetime(2010, 1, 1, tzinfo=timezone.utc), label="Rust created")
        await add_timeline_timepoint(tl_id, storage, start=datetime(2012, 1, 1, tzinfo=timezone.utc), label="TypeScript created")

        # 4. Search
        results, meta = await search(
            "Programming languages",
            storage, embedding_provider,
            k=10, graph_hops=1,
        )
        assert meta.nodes_returned > 0

        # 5. Reflect
        reflect_result, _ = await reflect(storage, embedding_provider)
        assert "topics_merged" in reflect_result

        # 6. Verify graph structure
        topics = await storage.query_nodes(node_type=NodeType.TOPIC)
        assert len(topics) > 0
        graph_result, _ = await query_graph(topics[0].id, storage, hops=1)
        assert len(graph_result["nodes"]) > 0
