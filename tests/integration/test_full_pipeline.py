"""End-to-end integration tests for the full Epimemer pipeline.

Exercises the complete flow: segment → store_decomposition → search →
reflect → update → timeline → metacontext → archive → restore,
against every storage backend, with mock providers.
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
from epimemer.mcp.config import ServerConfig
from epimemer.mcp.tools import (
    add_timeline_timepoint,
    archive,
    create_metacontext,
    create_timelink,
    create_timeline,
    get_metacontexts_for_node,
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
from epimemer.storage.protocol import StorageBackend


@pytest.fixture
def embedding_provider() -> MockEmbeddingProvider:
    return MockEmbeddingProvider(model_id="mock-embed", dimension=8)


@pytest.fixture
def config() -> ServerConfig:
    return ServerConfig(
        storage_backend="memory",
        embedding_provider="mock",
    )


async def _two_step_ingest(
    content: str,
    storage: StorageBackend,
    embedding_provider: MockEmbeddingProvider,
    config: ServerConfig,
    *,
    metacontext_id: str | None = None,
) -> tuple[dict, dict]:
    """Run the two-step ingest flow with dummy extraction."""
    seg_result, _ = await segment_text(
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
    store_result, _ = await store_decomposition(
        document_id=seg_result["document_id"],
        segments=segments,
        storage=storage,
        embedding_provider=embedding_provider,
        metacontext_id=metacontext_id,
    )
    return seg_result, store_result


class TestFullPipeline:

    async def test_ingest_search_cycle(
        self, storage, embedding_provider, config
    ):
        """Ingest content via two-step flow, then search and find it."""
        _, store_result = await _two_step_ingest(
            "Machine learning models use gradient descent for optimization.",
            storage, embedding_provider, config,
        )
        assert store_result["nodes_created"]["topics"] >= 1

        search_result, search_meta = await search(
            "Machine learning models use gradient descent for optimization.",
            storage, embedding_provider,
            k=5, graph_hops=1,
        )
        assert len(search_result["nodes"]) > 0
        assert search_meta.nodes_returned > 0

    async def test_ingest_reflect_consolidation(
        self, storage, embedding_provider, config
    ):
        """Ingest multiple docs, run reflection."""
        await _two_step_ingest(
            "Deep learning uses neural networks.",
            storage, embedding_provider, config,
        )
        await _two_step_ingest(
            "Artificial intelligence includes machine learning.",
            storage, embedding_provider, config,
        )

        reflect_result, _ = await reflect(storage, embedding_provider)
        assert "similar_pairs" in reflect_result
        assert "contradictions" in reflect_result

    async def test_update_creates_version_history(
        self, storage, embedding_provider, config
    ):
        """Ingest, find a node, update it, verify versioning."""
        await _two_step_ingest(
            "Python is a programming language.",
            storage, embedding_provider, config,
        )

        topics = await storage.query_nodes(node_type=NodeType.TOPIC)
        assert len(topics) > 0
        topic = topics[0]

        update_result, _ = await update(
            topic.id, "Python is a versatile language", storage, embedding_provider
        )
        assert update_result["old_node_id"] == topic.id
        assert update_result["new_node_id"] != topic.id

        old = await storage.get_node(topic.id)
        assert old.status == NodeStatus.SUPERSEDED

        edges_from_old = await storage.get_edges_from(topic.id)
        history_edges = [e for e in edges_from_old if e.type == EdgeType.SUPERSEDED_BY]
        assert len(history_edges) == 1
        assert history_edges[0].dst_id == update_result["new_node_id"]

    async def test_timeline_workflow(
        self, storage, embedding_provider, config
    ):
        """Create timeline, add timepoints, link to nodes, query."""
        await _two_step_ingest(
            "GPT-4 was released in March 2023.",
            storage, embedding_provider, config,
        )

        tl_result, _ = await create_timeline("AI Releases", storage)
        tl_id = tl_result["timeline_id"]

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

        facts = await storage.query_nodes(node_type=NodeType.FACT)
        if facts:
            link_result, _ = await create_timelink(facts[0].id, tl_id, tp_id, storage)
            assert link_result["timepoint_id"] == tp_id

        query_result, _ = await query_timeline(
            tl_id, storage,
            target=datetime(2023, 6, 1, tzinfo=timezone.utc),
            k=1,
        )
        assert len(query_result["timepoints"]) == 1

    async def test_metacontext_workflow(
        self, storage, embedding_provider, config
    ):
        """Create metacontexts, ingest with context, search with filter."""
        mc_real, _ = await create_metacontext("Real world", storage)
        mc_fiction, _ = await create_metacontext("Science fiction", storage)

        await _two_step_ingest(
            "Neural networks process information in layers.",
            storage, embedding_provider, config,
            metacontext_id=mc_real["metacontext_id"],
        )
        await _two_step_ingest(
            "The neural lace connects directly to the brain.",
            storage, embedding_provider, config,
            metacontext_id=mc_fiction["metacontext_id"],
        )

        real_results, _ = await search(
            "Neural",
            storage, embedding_provider,
            k=10, graph_hops=0,
            metacontext_id=mc_real["metacontext_id"],
        )

        for node in real_results["nodes"]:
            assert "metacontexts" in node
            assert "Real world" in node["metacontexts"]

        if real_results["nodes"]:
            node_id = real_results["nodes"][0]["id"]
            mc_result, _ = await get_metacontexts_for_node(node_id, storage)
            assert len(mc_result["metacontexts"]) > 0

    async def test_archive_restore_cycle(
        self, storage, embedding_provider, config
    ):
        """Ingest, supersede, archive old nodes, restore them."""
        await _two_step_ingest(
            "Old information about computing.",
            storage, embedding_provider, config,
        )

        topics = await storage.query_nodes(node_type=NodeType.TOPIC)
        assert len(topics) > 0
        old_time = datetime.now(timezone.utc) - timedelta(days=200)
        await storage.update_node_status(
            topics[0].id, NodeStatus.SUPERSEDED, superseded_at=old_time,
        )

        archive_result, _ = await archive(storage, max_age_days=90)
        assert archive_result["nodes_archived"] >= 1
        archive_data = archive_result["archive_data"]

        fresh_storage = InMemoryStorage()
        restore_result, _ = await restore(archive_data, fresh_storage)
        assert restore_result["nodes_restored"] >= 1

    async def test_full_sequence(
        self, storage, embedding_provider, config
    ):
        """Run a realistic sequence of operations."""
        mc, _ = await create_metacontext("Technical documentation", storage)
        mc_id = mc["metacontext_id"]

        docs = [
            "Python supports multiple programming paradigms.",
            "Rust provides memory safety without garbage collection.",
            "TypeScript adds static typing to JavaScript.",
        ]
        for doc in docs:
            await _two_step_ingest(
                doc, storage, embedding_provider, config,
                metacontext_id=mc_id,
            )

        tl, _ = await create_timeline("Language Evolution", storage)
        tl_id = tl["timeline_id"]
        await add_timeline_timepoint(tl_id, storage, start=datetime(1991, 1, 1, tzinfo=timezone.utc), label="Python created")
        await add_timeline_timepoint(tl_id, storage, start=datetime(2010, 1, 1, tzinfo=timezone.utc), label="Rust created")
        await add_timeline_timepoint(tl_id, storage, start=datetime(2012, 1, 1, tzinfo=timezone.utc), label="TypeScript created")

        results, meta = await search(
            "Programming languages",
            storage, embedding_provider,
            k=10, graph_hops=1,
        )
        assert meta.nodes_returned > 0

        reflect_result, _ = await reflect(storage, embedding_provider)
        assert "similar_pairs" in reflect_result

        topics = await storage.query_nodes(node_type=NodeType.TOPIC)
        assert len(topics) > 0
        graph_result, _ = await query_graph(topics[0].id, storage, hops=1)
        assert len(graph_result["nodes"]) > 0
