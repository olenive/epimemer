"""Tests for MCP tool implementations.

Tests call the pure functions in epimemer.mcp.tools directly
against every storage backend, with mock providers.
"""

import asyncio
import sys

import pytest

from datetime import datetime, timedelta, timezone

from petritype.core.executable_graph_components import (
    ArgumentEdgeToTransition,
    ExecutableGraph,
    ExecutableGraphOperations,
    FunctionTransitionNode,
    ListPlaceNode,
    ReturnedEdgeFromTransition,
)

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
    ValueSignal,
)
from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.mcp.config import ServerConfig
from epimemer.mcp.tools import (
    add_timeline_timepoint,
    apply_reflection,
    archive,
    as_of,
    check_conflicts,
    create_metacontext,
    events_in_window,
    find_nodes,
    create_timelink,
    create_timeline,
    get_metacontexts_for_node,
    graph_stats,
    link,
    list_relations,
    list_sources,
    query_changes,
    query_graph,
    query_timeline,
    record_contradiction,
    record_variant,
    reflect,
    reinforce,
    restore,
    search,
    segment_text,
    store_decomposition,
    supersede_by,
    update,
)
from epimemer.mcp import tools
from epimemer.mcp.server import _resolve_windows
from epimemer.storage.memory import InMemoryStorage
from epimemer.storage.protocol import StorageBackend


# --- Fixtures ---


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
    storage: StorageBackend,
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


# --- Retrieval reinforcement ---


async def _value_signals(storage) -> dict[str, tuple[float, object]]:
    """relevance + last_reinforced for every node, keyed by id."""
    nodes = await storage.query_nodes(status=NodeStatus.ACTIVE)
    return {n.id: (n.value.relevance, n.value.last_reinforced) for n in nodes}


class TestSearchReinforcement:
    """Retrieval is the one automatic upward path on `relevance`.

    Without it relevance only ever decays, so it measures age rather than use
    and cannot tell an old load-bearing node from an old dead one.
    """

    async def test_search_reinforces_returned_nodes(
        self, storage, embedding_provider, config
    ):
        await _two_step_ingest(
            "Machine learning models require large datasets.\n\n"
            "Volcanoes erupt when magma reaches the surface.",
            storage, embedding_provider, config,
        )
        before = await _value_signals(storage)

        result, _ = await search(
            "Machine learning models require large datasets.",
            storage,
            embedding_provider,
            k=1,
            graph_hops=0,
            reinforcement_boost=0.2,
        )
        returned = {n["id"] for n in result["nodes"]}
        assert returned, "need at least one returned node to reinforce"

        after = await _value_signals(storage)
        for node_id, (relevance, last_reinforced) in after.items():
            old_relevance, old_last_reinforced = before[node_id]
            if node_id in returned:
                assert relevance > old_relevance
                assert last_reinforced > old_last_reinforced
            else:
                assert relevance == old_relevance
                assert last_reinforced == old_last_reinforced

    async def test_reinforcement_saturates_rather_than_pinning(
        self, storage, embedding_provider, config
    ):
        """Repeated hits approach 1.0 asymptotically and never exceed it."""
        await _two_step_ingest(
            "Machine learning models require large datasets.",
            storage, embedding_provider, config,
        )
        for _ in range(20):
            result, _ = await search(
                "Machine learning models require large datasets.",
                storage,
                embedding_provider,
                k=1,
                graph_hops=0,
                reinforcement_boost=0.5,
            )
        returned = {n["id"] for n in result["nodes"]}
        after = await _value_signals(storage)
        for node_id in returned:
            assert after[node_id][0] < 1.0

    async def test_search_reinforcement_disabled_at_zero_boost(
        self, storage, embedding_provider, config
    ):
        await _two_step_ingest(
            "Machine learning models require large datasets.",
            storage, embedding_provider, config,
        )
        before = await _value_signals(storage)

        await search(
            "Machine learning models require large datasets.",
            storage,
            embedding_provider,
            k=5,
            reinforcement_boost=0.0,
        )
        assert await _value_signals(storage) == before


# --- Explicit reinforcement (importance) ---


class TestReinforce:
    """`reinforce` is the only agent-facing upward path on `importance`.

    It is deliberately not a raw setter: every bump leaves a trace, so a human
    reviewing a trivial-looking node rated highly can see the justification.
    """

    async def test_reinforce_bumps_and_records_provenance(self, storage):
        node = Fact(content="load-bearing fact", source_id="s1")
        trigger = Fact(content="the new information", source_id="s1")
        await storage.store_node(node)
        await storage.store_node(trigger)

        result, meta = await reinforce(
            node.id,
            reason="cited by the incident review",
            storage=storage,
            related_id=trigger.id,
            importance_step=0.25,
        )

        stored = await storage.get_node(node.id)
        assert stored.value.importance == pytest.approx(0.5 + 0.25 * 0.5)
        assert result["importance"] == pytest.approx(stored.value.importance)
        assert meta.nodes_returned == 1

        trace = stored.metadata["reinforcements"]
        assert len(trace) == 1
        assert trace[0]["reason"] == "cited by the incident review"
        assert trace[0]["related_id"] == trigger.id
        assert trace[0]["at"]

    async def test_reinforce_appends_rather_than_replaces(self, storage):
        node = Fact(content="reinforced twice", source_id="s1")
        await storage.store_node(node)

        await reinforce(node.id, reason="first", storage=storage, importance_step=0.25)
        await reinforce(node.id, reason="second", storage=storage, importance_step=0.25)

        stored = await storage.get_node(node.id)
        # Asymptotic: 0.5 → 0.625 → 0.71875, approaching 1.0 without reaching it.
        assert stored.value.importance == pytest.approx(0.71875)
        assert [r["reason"] for r in stored.metadata["reinforcements"]] == [
            "first", "second",
        ]

    async def test_reinforce_leaves_relevance_alone(self, storage):
        """Importance is a judgment; it must not double as a usage signal."""
        node = Fact(
            content="untouched relevance",
            source_id="s1",
            value=ValueSignal(relevance=0.3),
        )
        await storage.store_node(node)

        await reinforce(node.id, reason="matters", storage=storage)

        stored = await storage.get_node(node.id)
        assert stored.value.relevance == pytest.approx(0.3)

    async def test_reinforce_rejects_unknown_node(self, storage):
        with pytest.raises(ValueError, match="nope"):
            await reinforce("nope", reason="r", storage=storage)

    async def test_reinforce_rejects_unknown_related_id(self, storage):
        node = Fact(content="real", source_id="s1")
        await storage.store_node(node)

        with pytest.raises(ValueError, match="ghost"):
            await reinforce(
                node.id, reason="r", storage=storage, related_id="ghost"
            )

        # ...and the rejected call left nothing behind.
        stored = await storage.get_node(node.id)
        assert stored.value.importance == pytest.approx(0.5)
        assert "reinforcements" not in stored.metadata


# --- Link tests ---


class TestLink:

    async def test_creates_edge(self, storage):
        t = Topic(content="topic A", source_id="s1")
        f = Fact(content="fact B", source_id="s1")
        await storage.store_node(t)
        await storage.store_node(f)

        result, _ = await link(t.id, f.id, storage, edge_type="supports")
        assert "edge_id" in result

        edges = await storage.get_edges_from(t.id)
        assert any(e.type == EdgeType.SUPPORTS for e in edges)

    async def test_rejects_invalid_edge_type(self, storage):
        t = Topic(content="topic", source_id="s1")
        await storage.store_node(t)

        with pytest.raises(ValueError, match="Invalid edge_type"):
            await link(t.id, t.id, storage, edge_type="not_a_real_type")

    async def test_rejects_nonexistent_source(self, storage):
        t = Topic(content="topic", source_id="s1")
        await storage.store_node(t)

        with pytest.raises(ValueError, match="Source node"):
            await link("nonexistent", t.id, storage, edge_type="supports")

    async def test_rejects_nonexistent_destination(self, storage):
        t = Topic(content="topic", source_id="s1")
        await storage.store_node(t)

        with pytest.raises(ValueError, match="Destination node"):
            await link(t.id, "nonexistent", storage, edge_type="supports")

    async def test_creates_user_relation_with_kind(self, storage):
        a = Topic(content="article")
        b = Topic(content="BBC")
        await storage.store_node(a)
        await storage.store_node(b)
        result, _ = await link(a.id, b.id, storage, relation="published_by", kind="attribution")
        edge = (await storage.get_edges_from(a.id))[0]
        assert edge.type == EdgeType.RELATED and edge.label == "published_by"
        assert edge.kind == "attribution" and result["kind"] == "attribution"

    async def test_relation_kind_is_reused_per_label(self, storage):
        a, b, c = Topic(content="a"), Topic(content="b"), Topic(content="c")
        for n in (a, b, c):
            await storage.store_node(n)
        await link(a.id, b.id, storage, relation="published_by", kind="attribution")
        # Re-coin the same label with a different kind → the existing kind wins.
        result, _ = await link(a.id, c.id, storage, relation="published_by", kind="relationship")
        assert result["kind"] == "attribution"


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

    async def test_preserves_extraction_method(self, storage, embedding_provider):
        """Correcting the wording does not change where the material came from.

        Left unset, the replacement silently takes the model default, so every
        corrected node would claim a provenance nobody asserted.
        """
        f = Fact(content="old fact", source_id="s1",
                 extraction_method="agent:import")
        await storage.store_node(f)

        result, _ = await update(f.id, "new fact", storage, embedding_provider)
        new = await storage.get_node(result["new_node_id"])

        assert new.extraction_method == "agent:import"


# --- Supersede-by-existing + Case B tests ---


class TestSupersedeBy:

    async def test_supersedes_old_by_existing(self, storage):
        old = Fact(content="CEO is X", source_id="s1")
        new = Fact(content="CEO is Y", source_id="s1")
        await storage.store_node(old)
        await storage.store_node(new)

        result, _ = await supersede_by(old.id, new.id, storage)

        assert result["superseded_id"] == old.id and result["by_id"] == new.id
        assert (await storage.get_node(old.id)).status == NodeStatus.SUPERSEDED
        assert (await storage.get_node(new.id)).status == NodeStatus.ACTIVE
        lineage = await storage.get_edges_from(old.id, edge_type=EdgeType.SUPERSEDED_BY)
        assert len(lineage) == 1 and lineage[0].dst_id == new.id

    async def test_does_not_migrate_edges(self, storage):
        # The existing node carries its own evidence — old's support must not move.
        old = Fact(content="old", source_id="s1")
        new = Fact(content="new", source_id="s1")
        support = Fact(content="2020 report", source_id="s1")
        for node in (old, new, support):
            await storage.store_node(node)
        await storage.store_edge(
            NodeEdge(src_id=support.id, dst_id=old.id, type=EdgeType.SUPPORTS)
        )

        await supersede_by(old.id, new.id, storage)

        assert await storage.get_edges_to(new.id, edge_type=EdgeType.SUPPORTS) == []
        assert len(await storage.get_edges_to(old.id, edge_type=EdgeType.SUPPORTS)) == 1

    async def test_rejects_self_supersede(self, storage):
        t = Topic(content="t", source_id="s1")
        await storage.store_node(t)
        with pytest.raises(ValueError, match="cannot supersede itself"):
            await supersede_by(t.id, t.id, storage)

    async def test_rejects_missing_nodes(self, storage):
        t = Topic(content="t", source_id="s1")
        await storage.store_node(t)
        with pytest.raises(ValueError, match="not found"):
            await supersede_by("nope", t.id, storage)
        with pytest.raises(ValueError, match="not found"):
            await supersede_by(t.id, "nope", storage)


class TestCaseBEvidenceStaleness:

    async def test_supersede_by_flags_inference_via_derived_from(self, storage):
        fact = Fact(content="80% effective", source_id="s1")
        newer = Fact(content="30% effective", source_id="s1")
        inf = Inference(content="drug is highly effective", source_id="s1")
        for node in (fact, newer, inf):
            await storage.store_node(node)
        await storage.store_edge(
            NodeEdge(src_id=inf.id, dst_id=fact.id, type=EdgeType.DERIVED_FROM)
        )

        await supersede_by(fact.id, newer.id, storage)

        flags = await storage.get_edges_to(inf.id, edge_type=EdgeType.EVIDENCE_SUPERSEDED)
        assert len(flags) == 1 and flags[0].src_id == fact.id

    async def test_update_flags_inference_via_supports(self, storage, embedding_provider):
        fact = Fact(content="fact", source_id="s1")
        inf = Inference(content="inference", source_id="s1")
        await storage.store_node(fact)
        await storage.store_node(inf)
        await storage.store_edge(
            NodeEdge(src_id=fact.id, dst_id=inf.id, type=EdgeType.SUPPORTS)
        )

        await update(fact.id, "corrected fact", storage, embedding_provider)

        flags = await storage.get_edges_to(inf.id, edge_type=EdgeType.EVIDENCE_SUPERSEDED)
        assert len(flags) == 1

    async def test_supersede_clears_candidate_edges(self, storage):
        fact = Fact(content="old", source_id="s1")
        newer = Fact(content="new", source_id="s1")
        await storage.store_node(fact)
        await storage.store_node(newer)
        await storage.store_edge(
            NodeEdge(src_id=newer.id, dst_id=fact.id, type=EdgeType.SUPERSESSION_CANDIDATE)
        )

        await supersede_by(fact.id, newer.id, storage)

        assert await storage.get_edges_to(
            fact.id, edge_type=EdgeType.SUPERSESSION_CANDIDATE
        ) == []


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
        assert "pending_review" in result

    async def test_surfaces_pending_review(self, storage, embedding_provider):
        old = Fact(content="old", source_id="s1")
        newer = Fact(content="new", source_id="s1")
        await storage.store_node(old)
        await storage.store_node(newer)
        await storage.store_edge(
            NodeEdge(src_id=newer.id, dst_id=old.id, type=EdgeType.SUPERSESSION_CANDIDATE)
        )

        result, _ = await reflect(storage, embedding_provider)
        flagged_ids = {e["node"]["id"] for e in result["pending_review"]}
        assert old.id in flagged_ids
        entry = next(e for e in result["pending_review"] if e["node"]["id"] == old.id)
        assert entry["review"]["superseded_candidate"] == [newer.id]
        assert entry["node"]["node_type"] == "fact"


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


class TestApplyReflectionSupersessions:

    async def test_resolves_flagged_node(self, storage, embedding_provider):
        old = Fact(content="CEO is X", source_id="s1")
        winner = Fact(content="CEO is Y", source_id="s1")
        inf = Inference(content="X leads strategy", source_id="s1")
        for node in (old, winner, inf):
            await storage.store_node(node)
        # old is flagged as a supersession candidate by winner, and supports inf.
        await storage.store_edge(
            NodeEdge(src_id=winner.id, dst_id=old.id, type=EdgeType.SUPERSESSION_CANDIDATE)
        )
        await storage.store_edge(
            NodeEdge(src_id=old.id, dst_id=inf.id, type=EdgeType.SUPPORTS)
        )

        result, _ = await apply_reflection(
            storage, embedding_provider,
            supersessions=[{"old_id": old.id, "by_id": winner.id}],
        )

        assert result["supersessions_applied"] == 1
        assert (await storage.get_node(old.id)).status == NodeStatus.SUPERSEDED
        assert (await storage.get_node(winner.id)).status == NodeStatus.ACTIVE
        lineage = await storage.get_edges_from(old.id, edge_type=EdgeType.SUPERSEDED_BY)
        assert len(lineage) == 1 and lineage[0].dst_id == winner.id
        # Candidacy is cleared and the dependent inference is flagged evidence_stale.
        assert await storage.get_edges_to(
            old.id, edge_type=EdgeType.SUPERSESSION_CANDIDATE
        ) == []
        assert len(await storage.get_edges_to(
            inf.id, edge_type=EdgeType.EVIDENCE_SUPERSEDED
        )) == 1

    async def test_skips_self_and_missing(self, storage, embedding_provider):
        a = Fact(content="a", source_id="s1")
        await storage.store_node(a)

        result, _ = await apply_reflection(
            storage, embedding_provider,
            supersessions=[
                {"old_id": a.id, "by_id": a.id},        # self-supersede
                {"old_id": a.id, "by_id": "missing"},   # missing winner
                {"old_id": "missing", "by_id": a.id},   # missing loser
            ],
        )
        assert result["supersessions_applied"] == 0
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

    async def test_restore_is_atomic_on_bad_record(self, storage):
        # A malformed edge (missing dst_id) must abort the whole restore so the
        # otherwise-valid node never lands — all-or-nothing.
        archive_data = {
            "nodes": [
                {
                    "id": "restore-atomic-1",
                    "content": "should not persist",
                    "source_id": "s1",
                    "node_type": "topic",
                    "status": "active",
                    "created_at": "2026-01-01T00:00:00Z",
                }
            ],
            "edges": [{"id": "edge-bad", "src_id": "restore-atomic-1", "type": "supports"}],
        }
        with pytest.raises(Exception):
            await restore(archive_data, storage)

        assert await storage.get_node("restore-atomic-1") is None


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


# --- Review loop: detection + verdict recording ---


async def _store_fact_with_embedding(
    storage, model_id, content, vector, *, metacontext_id=None
):
    f = Fact(content=content, source_id="s1")
    await storage.store_node(f)
    await storage.store_embedding(
        EmbeddingRecord(item_id=f.id, model_id=model_id, vector=vector)
    )
    if metacontext_id is not None:
        await storage.store_edge(
            NodeEdge(src_id=f.id, dst_id=metacontext_id, type=EdgeType.HAS_METACONTEXT)
        )
    return f


class TestCheckConflicts:

    async def test_surfaces_similar_active_facts(self, storage, embedding_provider):
        model_id = embedding_provider.model_id
        query = await _store_fact_with_embedding(storage, model_id, "CEO is Alice", [1.0, 0.0])
        similar = await _store_fact_with_embedding(
            storage, model_id, "Alice leads the company", [1.0, 0.0]
        )
        await _store_fact_with_embedding(storage, model_id, "unrelated", [0.0, 1.0])

        result, meta = await check_conflicts(
            [query.id], storage, embedding_provider, threshold=0.5
        )

        assert len(result["conflicts"]) == 1
        entry = result["conflicts"][0]
        assert entry["fact"]["id"] == query.id
        cand_ids = {c["id"] for c in entry["candidates"]}
        assert similar.id in cand_ids
        # The fact never appears as its own candidate.
        assert query.id not in cand_ids
        assert meta.nodes_returned == len(entry["candidates"])

    async def test_excludes_self_and_below_threshold(self, storage, embedding_provider):
        model_id = embedding_provider.model_id
        query = await _store_fact_with_embedding(storage, model_id, "a", [1.0, 0.0])
        await _store_fact_with_embedding(storage, model_id, "b", [0.0, 1.0])

        result, _ = await check_conflicts(
            [query.id], storage, embedding_provider, threshold=0.9
        )
        # Only self (excluded) and an orthogonal fact (below 0.9) → nothing.
        assert result["conflicts"] == []

    async def test_flags_cross_frame_candidate(self, storage, embedding_provider):
        model_id = embedding_provider.model_id
        fiction = Metacontext(content="Fiction")
        await storage.store_metacontext(fiction)
        query = await _store_fact_with_embedding(
            storage, model_id, "Napoleon lost at Waterloo", [1.0, 0.0]
        )
        variant = await _store_fact_with_embedding(
            storage, model_id, "Napoleon won at Waterloo", [1.0, 0.0],
            metacontext_id=fiction.id,
        )

        result, _ = await check_conflicts(
            [query.id], storage, embedding_provider, threshold=0.5
        )
        cand = next(
            c for c in result["conflicts"][0]["candidates"] if c["id"] == variant.id
        )
        assert cand["same_frame"] is False
        assert "Fiction" in cand["metacontexts"]

    async def test_skips_facts_without_embeddings(self, storage, embedding_provider):
        f = Fact(content="no embedding", source_id="s1")
        await storage.store_node(f)
        result, _ = await check_conflicts([f.id], storage, embedding_provider)
        assert result["conflicts"] == []


class TestRecordContradiction:

    async def test_records_same_frame_and_signals_notify(self, storage):
        a = Fact(content="X is true", source_id="s1")
        b = Fact(content="X is false", source_id="s1")
        await storage.store_node(a)
        await storage.store_node(b)

        result, _ = await record_contradiction(a.id, b.id, storage)

        assert result["created"] is True
        assert result["same_frame"] is True
        assert result["notify_user"] is True
        edges = await storage.get_edges_from(a.id, edge_type=EdgeType.CONTRADICTION)
        assert len(edges) == 1 and edges[0].dst_id == b.id

    async def test_idempotent_either_direction(self, storage):
        a = Fact(content="a", source_id="s1")
        b = Fact(content="b", source_id="s1")
        await storage.store_node(a)
        await storage.store_node(b)

        first, _ = await record_contradiction(a.id, b.id, storage)
        second, _ = await record_contradiction(b.id, a.id, storage)  # reversed

        assert first["created"] is True
        assert second["created"] is False
        assert second["edge_id"] == first["edge_id"]
        from_a = await storage.get_edges_from(a.id, edge_type=EdgeType.CONTRADICTION)
        from_b = await storage.get_edges_from(b.id, edge_type=EdgeType.CONTRADICTION)
        assert len(from_a) + len(from_b) == 1

    async def test_cross_frame_does_not_notify(self, storage):
        fiction = Metacontext(content="Fiction")
        await storage.store_metacontext(fiction)
        a = Fact(content="real", source_id="s1")
        b = Fact(content="fictional", source_id="s1")
        await storage.store_node(a)
        await storage.store_node(b)
        await storage.store_edge(
            NodeEdge(src_id=b.id, dst_id=fiction.id, type=EdgeType.HAS_METACONTEXT)
        )

        result, _ = await record_contradiction(a.id, b.id, storage)
        assert result["same_frame"] is False
        assert result["notify_user"] is False
        assert "warning" in result

    async def test_rejects_self_and_missing(self, storage):
        a = Fact(content="a", source_id="s1")
        await storage.store_node(a)
        with pytest.raises(ValueError, match="cannot contradict itself"):
            await record_contradiction(a.id, a.id, storage)
        with pytest.raises(ValueError, match="not found"):
            await record_contradiction(a.id, "nope", storage)


class TestRecordVariant:

    async def test_records_cross_frame_variant(self, storage):
        novel = Metacontext(content="Novel-X")
        await storage.store_metacontext(novel)
        real = Fact(content="Napoleon lost at Waterloo", source_id="s1")
        fic = Fact(content="Napoleon won at Waterloo", source_id="s1")
        await storage.store_node(real)
        await storage.store_node(fic)
        await storage.store_edge(
            NodeEdge(src_id=fic.id, dst_id=novel.id, type=EdgeType.HAS_METACONTEXT)
        )

        result, _ = await record_variant(real.id, fic.id, storage)
        assert result["created"] is True
        assert result["same_frame"] is False
        assert "warning" not in result
        edges = await storage.get_edges_from(real.id, edge_type=EdgeType.VARIANT_OF)
        assert len(edges) == 1 and edges[0].dst_id == fic.id

    async def test_same_frame_warns(self, storage):
        a = Fact(content="a", source_id="s1")
        b = Fact(content="b", source_id="s1")
        await storage.store_node(a)
        await storage.store_node(b)
        result, _ = await record_variant(a.id, b.id, storage)
        assert result["same_frame"] is True
        assert "warning" in result

    async def test_idempotent_either_direction(self, storage):
        a = Fact(content="a", source_id="s1")
        b = Fact(content="b", source_id="s1")
        await storage.store_node(a)
        await storage.store_node(b)
        first, _ = await record_variant(a.id, b.id, storage)
        second, _ = await record_variant(b.id, a.id, storage)
        assert second["created"] is False and second["edge_id"] == first["edge_id"]


class TestReflectFrameAware:

    async def _fact(self, storage, model_id, content, vector, *, mc=None):
        f = Fact(content=content, source_id="s1")
        await storage.store_node(f)
        await storage.store_embedding(
            EmbeddingRecord(item_id=f.id, model_id=model_id, vector=vector)
        )
        if mc is not None:
            await storage.store_edge(
                NodeEdge(src_id=f.id, dst_id=mc, type=EdgeType.HAS_METACONTEXT)
            )
        return f

    async def test_cross_frame_pairs_dropped_from_contradictions(
        self, storage, embedding_provider
    ):
        model_id = embedding_provider.model_id
        fiction = Metacontext(content="Fiction")
        await storage.store_metacontext(fiction)
        # Same-frame near-identical pair (both untagged → base reality).
        await self._fact(storage, model_id, "real A", [1.0, 0.0])
        await self._fact(storage, model_id, "real B", [1.0, 0.0])
        # Cross-frame near-identical pair (one tagged fiction).
        await self._fact(storage, model_id, "story A", [0.0, 1.0])
        await self._fact(storage, model_id, "story B", [0.0, 1.0], mc=fiction.id)

        result, _ = await reflect(storage, embedding_provider)
        contents = {
            frozenset({p["fact_a"]["content"], p["fact_b"]["content"]})
            for p in result["contradictions"]
        }
        # The same-frame pair is surfaced; the cross-frame pair is filtered out.
        assert frozenset({"real A", "real B"}) in contents
        assert frozenset({"story A", "story B"}) not in contents


# --- Retrieval visibility: frame-scoping + review labels (Phase 2c) ---


class TestSearchFrameScoping:

    async def test_includes_base_excludes_sibling_frames(
        self, storage, embedding_provider
    ):
        mc_real = Metacontext(content="Real world")
        mc_fiction = Metacontext(content="Fiction")
        await storage.store_metacontext(mc_real)
        await storage.store_metacontext(mc_fiction)

        # All facts share the query's embedding so vector search returns them all;
        # only the frame filter decides what comes back.
        query = "anything"
        qvec = (await embedding_provider.embed([query]))[0]
        real = await _store_fact_with_embedding(
            storage, embedding_provider.model_id, "real fact", qvec,
            metacontext_id=mc_real.id,
        )
        fic = await _store_fact_with_embedding(
            storage, embedding_provider.model_id, "fiction fact", qvec,
            metacontext_id=mc_fiction.id,
        )
        base = await _store_fact_with_embedding(
            storage, embedding_provider.model_id, "base fact", qvec,
        )

        result, _ = await search(
            query, storage, embedding_provider,
            k=20, graph_hops=0, metacontext_id=mc_real.id,
        )
        ids = {n["id"] for n in result["nodes"]}
        assert real.id in ids       # in-frame
        assert base.id in ids       # untagged base reality is always in scope
        assert fic.id not in ids    # sibling frame excluded

    async def test_cross_frame_returns_all_frames(self, storage, embedding_provider):
        mc_real = Metacontext(content="Real world")
        mc_fiction = Metacontext(content="Fiction")
        await storage.store_metacontext(mc_real)
        await storage.store_metacontext(mc_fiction)

        query = "anything"
        qvec = (await embedding_provider.embed([query]))[0]
        real = await _store_fact_with_embedding(
            storage, embedding_provider.model_id, "real fact", qvec,
            metacontext_id=mc_real.id,
        )
        fic = await _store_fact_with_embedding(
            storage, embedding_provider.model_id, "fiction fact", qvec,
            metacontext_id=mc_fiction.id,
        )

        result, _ = await search(
            query, storage, embedding_provider,
            k=20, graph_hops=0, metacontext_id=mc_real.id, cross_frame=True,
        )
        ids = {n["id"] for n in result["nodes"]}
        assert {real.id, fic.id} <= ids


class TestSearchFrameScopingBeyondTopK:
    """Frame-scoping must not be capped by the vector top-k.

    Vector search ranks first and returns k hits; the frame filter runs after.
    So a frame whose relevant nodes rank below k is dropped before the filter
    ever sees it — the query comes back short, or empty. These pin that an
    in-frame node still surfaces when out-of-frame nodes outrank it.
    """

    async def test_frame_scoped_search_reaches_beyond_top_k(
        self, storage, embedding_provider
    ):
        mc = Metacontext(content="Frame")
        sibling = Metacontext(content="Sibling")
        await storage.store_metacontext(mc)
        await storage.store_metacontext(sibling)

        model_id = embedding_provider.model_id
        k = 3
        qvec = (await embedding_provider.embed(["anything"]))[0]

        # k sibling-frame facts, each maximally similar to the query, fill the
        # top-k. They are excluded by the filter (not base reality), so pre-fix
        # the frame comes back empty.
        for i in range(k):
            await _store_fact_with_embedding(
                storage, model_id, f"sibling {i}", qvec, metacontext_id=sibling.id
            )
        # One in-frame fact, strictly less similar, so it ranks below the top-k.
        weaker = [qvec[0] * 0.5, *qvec[1:]]
        in_frame = await _store_fact_with_embedding(
            storage, model_id, "in-frame fact", weaker, metacontext_id=mc.id
        )

        result, _ = await search(
            "anything", storage, embedding_provider,
            k=k, graph_hops=0, metacontext_id=mc.id,
        )
        ids = {n["id"] for n in result["nodes"]}
        assert in_frame.id in ids  # missed pre-fix: filtered out of the top-k

    async def test_frame_scoped_search_iterates_past_initial_overfetch(
        self, storage, embedding_provider
    ):
        """Enough distractors that one over-fetch still misses the in-frame node;
        the fetch has to grow until the store is exhausted and it surfaces."""
        mc = Metacontext(content="Frame")
        sibling = Metacontext(content="Sibling")
        await storage.store_metacontext(mc)
        await storage.store_metacontext(sibling)

        model_id = embedding_provider.model_id
        k = 3
        qvec = (await embedding_provider.embed(["anything"]))[0]

        for i in range(15):  # well past the initial k*4 over-fetch of 12
            await _store_fact_with_embedding(
                storage, model_id, f"sibling {i}", qvec, metacontext_id=sibling.id
            )
        weaker = [qvec[0] * 0.5, *qvec[1:]]
        in_frame = await _store_fact_with_embedding(
            storage, model_id, "in-frame fact", weaker, metacontext_id=mc.id
        )

        result, _ = await search(
            "anything", storage, embedding_provider,
            k=k, graph_hops=0, metacontext_id=mc.id,
        )
        ids = {n["id"] for n in result["nodes"]}
        assert in_frame.id in ids
        # Nothing out-of-frame leaks in on the way to finding it.
        assert ids == {in_frame.id}


class TestReviewLabelsInRetrieval:

    async def test_query_graph_flags_superseded_candidate(self, storage):
        old = Fact(content="old", source_id="s1")
        newer = Fact(content="new", source_id="s1")
        await storage.store_node(old)
        await storage.store_node(newer)
        await storage.store_edge(
            NodeEdge(src_id=newer.id, dst_id=old.id, type=EdgeType.SUPERSESSION_CANDIDATE)
        )

        result, _ = await query_graph(old.id, storage, hops=0)
        node = result["nodes"][0]
        assert node["id"] == old.id
        assert node["review"]["superseded_candidate"] == [newer.id]

    async def test_query_graph_flags_contested(self, storage):
        a = Fact(content="X true", source_id="s1")
        b = Fact(content="X false", source_id="s1")
        await storage.store_node(a)
        await storage.store_node(b)
        await storage.store_edge(
            NodeEdge(src_id=a.id, dst_id=b.id, type=EdgeType.CONTRADICTION)
        )

        result, _ = await query_graph(a.id, storage, hops=0)
        assert result["nodes"][0]["review"]["contested"] == [b.id]

    async def test_clean_node_has_no_review_field(self, storage):
        t = Topic(content="fine", source_id="s1")
        await storage.store_node(t)
        result, _ = await query_graph(t.id, storage, hops=0)
        assert "review" not in result["nodes"][0]

    async def test_search_surfaces_review_labels(self, storage, embedding_provider):
        query = "anything"
        qvec = (await embedding_provider.embed([query]))[0]
        old = await _store_fact_with_embedding(
            storage, embedding_provider.model_id, "old fact", qvec
        )
        newer = Fact(content="new fact", source_id="s1")
        await storage.store_node(newer)
        await storage.store_edge(
            NodeEdge(src_id=newer.id, dst_id=old.id, type=EdgeType.SUPERSESSION_CANDIDATE)
        )

        result, _ = await search(query, storage, embedding_provider, k=20, graph_hops=0)
        flagged = next(n for n in result["nodes"] if n["id"] == old.id)
        assert flagged["review"]["superseded_candidate"] == [newer.id]


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
        result, meta = await graph_stats(storage, default_reflect_threshold=10)
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

        result, meta = await graph_stats(storage, default_reflect_threshold=10)
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

        result, _ = await graph_stats(storage, default_reflect_threshold=10)
        assert result["nodes_by_type"]["topic"] == 0
        assert result["total_nodes"] == 0

    async def test_counts_metacontexts(self, storage):
        await storage.store_metacontext(Metacontext(content="Real world"))
        await storage.store_metacontext(Metacontext(content="Fiction"))

        result, _ = await graph_stats(storage, default_reflect_threshold=10)
        assert result["metacontexts"] == 2


# --- Temporal queries: as_of + query_changes ---

_W_START = datetime(2026, 6, 10, tzinfo=timezone.utc)
_W_END = datetime(2026, 6, 20, tzinfo=timezone.utc)


def _fact_at(content, created, *, status=NodeStatus.ACTIVE, retired=None):
    return Fact(
        content=content, source_id="s1", created_at=created,
        status=status, superseded_at=retired,
    )


class TestEventsInWindow:

    def test_created_in_window(self):
        f = _fact_at("x", datetime(2026, 6, 15, tzinfo=timezone.utc))
        evs = events_in_window(f, _W_START, _W_END)
        assert [e.kind for e in evs] == ["created"]
        assert evs[0].at == f.created_at

    def test_superseded_in_window(self):
        f = _fact_at(
            "x", datetime(2026, 6, 1, tzinfo=timezone.utc),
            status=NodeStatus.SUPERSEDED,
            retired=datetime(2026, 6, 15, tzinfo=timezone.utc),
        )
        assert [e.kind for e in events_in_window(f, _W_START, _W_END)] == ["superseded"]

    def test_merged_in_window(self):
        f = _fact_at(
            "x", datetime(2026, 6, 1, tzinfo=timezone.utc),
            status=NodeStatus.MERGED,
            retired=datetime(2026, 6, 15, tzinfo=timezone.utc),
        )
        assert [e.kind for e in events_in_window(f, _W_START, _W_END)] == ["merged"]

    def test_created_and_retired_same_window_yields_two_events(self):
        f = _fact_at(
            "x", datetime(2026, 6, 12, tzinfo=timezone.utc),
            status=NodeStatus.SUPERSEDED,
            retired=datetime(2026, 6, 15, tzinfo=timezone.utc),
        )
        assert [e.kind for e in events_in_window(f, _W_START, _W_END)] == [
            "created", "superseded",
        ]

    def test_outside_window_yields_nothing(self):
        f = _fact_at("x", datetime(2026, 6, 1, tzinfo=timezone.utc))
        assert events_in_window(f, _W_START, _W_END) == []


class TestAsOf:

    async def test_snapshot_returns_active_set_at_instant(self, storage):
        old = _fact_at(
            "old", datetime(2026, 6, 1, tzinfo=timezone.utc),
            status=NodeStatus.SUPERSEDED,
            retired=datetime(2026, 6, 15, tzinfo=timezone.utc),
        )
        new = _fact_at("new", datetime(2026, 6, 15, tzinfo=timezone.utc))
        await storage.store_node(old)
        await storage.store_node(new)

        # Before new is born and before old is retired: only old is live.
        early, _ = await as_of(datetime(2026, 6, 10, tzinfo=timezone.utc), storage)
        assert [n["id"] for n in early["nodes"]] == [old.id]

        # After old retired and new born: only new is live.
        late, meta = await as_of(datetime(2026, 6, 20, tzinfo=timezone.utc), storage)
        assert [n["id"] for n in late["nodes"]] == [new.id]
        assert meta.nodes_returned == 1

    async def test_omits_review_labels(self, storage):
        # A node with an incoming supersession_candidate edge would be labelled
        # `superseded_candidate` by review_labels — as_of must not surface that.
        old = _fact_at("old", datetime(2026, 6, 1, tzinfo=timezone.utc))
        new = _fact_at("new", datetime(2026, 6, 2, tzinfo=timezone.utc))
        await storage.store_node(old)
        await storage.store_node(new)
        await storage.store_edge(
            NodeEdge(src_id=new.id, dst_id=old.id, type=EdgeType.SUPERSESSION_CANDIDATE)
        )
        result, _ = await as_of(datetime(2026, 6, 10, tzinfo=timezone.utc), storage)
        assert all("review" not in n for n in result["nodes"])

    async def test_node_type_filter(self, storage):
        f = _fact_at("f", datetime(2026, 6, 1, tzinfo=timezone.utc))
        t = Topic(content="t", source_id="s1",
                  created_at=datetime(2026, 6, 1, tzinfo=timezone.utc))
        await storage.store_node(f)
        await storage.store_node(t)
        result, _ = await as_of(
            datetime(2026, 6, 10, tzinfo=timezone.utc), storage, node_types=["fact"]
        )
        assert [n["id"] for n in result["nodes"]] == [f.id]


class TestQueryChangesTool:

    async def test_groups_by_window_with_event_tags(self, storage):
        a = _fact_at("a", datetime(2026, 6, 15, tzinfo=timezone.utc))   # window 1
        b = _fact_at("b", datetime(2026, 6, 25, tzinfo=timezone.utc))   # window 2
        await storage.store_node(a)
        await storage.store_node(b)

        w1 = (_W_START, _W_END)
        w2 = (datetime(2026, 6, 20, tzinfo=timezone.utc),
              datetime(2026, 6, 30, tzinfo=timezone.utc))
        result, meta = await query_changes([w1, w2], storage)

        win1, win2 = result["windows"]
        assert [c["id"] for c in win1["changes"]] == [a.id]
        assert [e["kind"] for e in win1["changes"][0]["events"]] == ["created"]
        assert [c["id"] for c in win2["changes"]] == [b.id]
        assert meta.nodes_returned == 2

    async def test_two_events_for_create_and_retire_in_window(self, storage):
        f = _fact_at(
            "f", datetime(2026, 6, 12, tzinfo=timezone.utc),
            status=NodeStatus.SUPERSEDED,
            retired=datetime(2026, 6, 15, tzinfo=timezone.utc),
        )
        await storage.store_node(f)
        result, _ = await query_changes([(_W_START, _W_END)], storage)
        changes = result["windows"][0]["changes"]
        # The node appears exactly once, carrying both lifecycle events.
        assert [c["id"] for c in changes] == [f.id]
        assert [e["kind"] for e in changes[0]["events"]] == ["created", "superseded"]

    async def test_includes_metacontext_and_review_labels(self, storage):
        fiction = Metacontext(content="Fiction")
        await storage.store_metacontext(fiction)
        a = _fact_at("a", datetime(2026, 6, 15, tzinfo=timezone.utc))
        newer = _fact_at("newer", datetime(2026, 6, 16, tzinfo=timezone.utc))
        await storage.store_node(a)
        await storage.store_node(newer)
        await storage.store_edge(
            NodeEdge(src_id=a.id, dst_id=fiction.id, type=EdgeType.HAS_METACONTEXT)
        )
        # newer nominates a as superseded → a gets a `superseded_candidate` label.
        await storage.store_edge(
            NodeEdge(src_id=newer.id, dst_id=a.id, type=EdgeType.SUPERSESSION_CANDIDATE)
        )
        result, _ = await query_changes([(_W_START, _W_END)], storage)
        by_id = {c["id"]: c for c in result["windows"][0]["changes"]}
        assert by_id[a.id]["metacontexts"] == ["Fiction"]
        assert "superseded_candidate" in by_id[a.id]["review"]


class TestResolveWindows:
    NOW = datetime(2026, 6, 28, 12, 0, tzinfo=timezone.utc)

    def test_defaults_to_last_24h(self):
        windows = _resolve_windows(self.NOW)
        assert windows == [(self.NOW - timedelta(hours=24), self.NOW)]

    def test_last_hours_trailing_window(self):
        windows = _resolve_windows(self.NOW, last_hours=6)
        assert windows == [(self.NOW - timedelta(hours=6), self.NOW)]

    def test_explicit_windows_with_open_end_uses_now(self):
        windows = _resolve_windows(
            self.NOW, windows=[["2026-06-20T00:00:00+00:00", ""]]
        )
        assert windows == [(datetime(2026, 6, 20, tzinfo=timezone.utc), self.NOW)]

    def test_windows_take_precedence_over_relative(self):
        windows = _resolve_windows(
            self.NOW, last_hours=6,
            windows=[["2026-06-01T00:00:00+00:00", "2026-06-02T00:00:00+00:00"]],
        )
        assert windows == [(
            datetime(2026, 6, 1, tzinfo=timezone.utc),
            datetime(2026, 6, 2, tzinfo=timezone.utc),
        )]

    def test_rejects_inverted_window(self):
        with pytest.raises(ValueError):
            _resolve_windows(
                self.NOW,
                windows=[["2026-06-20T00:00:00+00:00", "2026-06-10T00:00:00+00:00"]],
            )

# --- Sources, tags-as-topics, relations ---


class _FixedEmbed:
    """Embedding provider returning a fixed vector per exact string (for tests)."""
    model_id = "fixed"

    def __init__(self, mapping):
        self.mapping = mapping

    async def embed(self, texts):
        return [self.mapping[t] for t in texts]


async def _ingest(storage, ep, config, content, *, source, tags=None, facts):
    """Segment + decompose one document into the given facts."""
    seg, _ = await segment_text(content, storage, ep, config, source=source)
    sid = seg["segments"][0]["segment_id"]
    await store_decomposition(
        document_id=seg["document_id"],
        segments=[{"segment_id": sid, "topics": [], "facts": facts, "inferences": []}],
        storage=storage, embedding_provider=ep, tags=tags,
    )
    return seg["document_id"]


class TestIngestSourcesAndTags:

    async def test_sourced_from_edge_per_node(self, storage, embedding_provider, config):
        doc_id = await _ingest(storage, embedding_provider, config, "One para.",
                               source="ISSUES.md", facts=["a fact"])
        facts = await storage.query_nodes(node_type=NodeType.FACT)
        assert facts
        for f in facts:
            srcs = await storage.get_edges_from(f.id, edge_type=EdgeType.SOURCED_FROM)
            assert [e.dst_id for e in srcs] == [doc_id]

    async def test_tagged_with_creates_and_reuses_one_topic(self, storage, embedding_provider, config):
        await _ingest(storage, embedding_provider, config, "Alpha.",
                      source="A.md", tags=["billing"], facts=["af"])
        await _ingest(storage, embedding_provider, config, "Beta.",
                      source="B.md", tags=["billing"], facts=["bf"])
        billing = await storage.get_node_by_content("billing", node_type=NodeType.TOPIC)
        assert billing is not None
        # Exactly one billing Topic, with a tagged_with edge from each fact.
        topics = [t for t in await storage.query_nodes(node_type=NodeType.TOPIC)
                  if t.content == "billing"]
        assert len(topics) == 1
        taggers = await storage.get_edges_to(billing.id, edge_type=EdgeType.TAGGED_WITH)
        assert len(taggers) == 2

    async def test_published_by_entity_edge(self, storage, embedding_provider, config):
        seg, _ = await segment_text(
            "An article.", storage, embedding_provider, config,
            source="BBC article", published_by="BBC",
        )
        bbc = await storage.get_node_by_content("BBC", node_type=NodeType.TOPIC)
        assert bbc is not None
        edges = await storage.get_edges_to(bbc.id)
        assert any(
            e.type == EdgeType.RELATED and e.label == "published_by"
            and e.kind == "attribution" for e in edges
        )


class TestFindNodesTraversal:

    async def _two(self, storage, ep, config):
        await _ingest(storage, ep, config, "Alpha.", source="ISSUES.md",
                      tags=["billing"], facts=["af"])
        await _ingest(storage, ep, config, "Beta.", source="README.md",
                      tags=["weather"], facts=["bf"])

    async def test_find_by_sourced_from(self, storage, embedding_provider, config):
        doc_id = await _ingest(storage, embedding_provider, config, "Alpha.",
                               source="ISSUES.md", facts=["af"])
        result, _ = await find_nodes(storage, sourced_from=doc_id)
        assert {n["content"] for n in result["nodes"]} == {"af"}

    async def test_find_by_sourced_from_document_name(self, storage, embedding_provider, config):
        await _ingest(storage, embedding_provider, config, "Alpha.",
                      source="ISSUES.md", facts=["af"])
        # Resolves the document by its human source name, not just its id.
        result, _ = await find_nodes(storage, sourced_from="ISSUES.md")
        assert {n["content"] for n in result["nodes"]} == {"af"}

    async def test_find_by_tagged_with_name(self, storage, embedding_provider, config):
        await self._two(storage, embedding_provider, config)
        result, _ = await find_nodes(storage, tagged_with="billing")
        assert {n["content"] for n in result["nodes"]} == {"af"}

    async def test_requires_a_hub(self, storage):
        with pytest.raises(ValueError):
            await find_nodes(storage)


class TestListSourcesAndRelations:

    async def test_list_sources(self, storage, embedding_provider, config):
        await _ingest(storage, embedding_provider, config, "Alpha.",
                      source="ISSUES.md", facts=["af"])
        await segment_text("Article.", storage, embedding_provider, config,
                           source="BBC article", published_by="BBC")
        result, _ = await list_sources(storage)
        names = {s["name"] for s in result["sources"]}
        assert "BBC" in names  # the publishing entity is a source

    async def test_list_relations(self, storage, embedding_provider, config):
        await segment_text("Article.", storage, embedding_provider, config,
                           source="BBC article", published_by="BBC")
        result, _ = await list_relations(storage)
        labels = {r["label"]: r["kind"] for r in result["relations"]}
        assert labels.get("published_by") == "attribution"


class TestTraversalVsMigration:

    async def test_sourced_from_migrates_but_search_does_not_expand(
        self, storage, embedding_provider, config
    ):
        from epimemer.pipelines.graph_construction.versioning import supersede_node
        from epimemer.pipelines.query.graph_expansion import expand_via_graph
        doc_id = await _ingest(storage, embedding_provider, config, "Para.",
                               source="ISSUES.md", facts=["the fact"])
        fact = (await storage.query_nodes(node_type=NodeType.FACT))[0]

        # Default expansion from the fact must NOT cross sourced_from to the doc.
        nodes, _ = await expand_via_graph([fact], storage, hops=2)
        assert doc_id not in {n.id for n in nodes}

        # Supersession migrates the sourced_from edge onto the replacement.
        new = Fact(content="the corrected fact", source_id=fact.source_id)
        await supersede_node(fact, new, storage, embedding_provider)
        migrated = await storage.get_edges_from(new.id, edge_type=EdgeType.SOURCED_FROM)
        assert [e.dst_id for e in migrated] == [doc_id]

    async def test_tagged_with_is_traversed(self, storage, embedding_provider, config):
        from epimemer.pipelines.query.graph_expansion import expand_via_graph
        await _ingest(storage, embedding_provider, config, "Para.",
                      source="A.md", tags=["billing"], facts=["the fact"])
        fact = (await storage.query_nodes(node_type=NodeType.FACT))[0]
        billing = await storage.get_node_by_content("billing", node_type=NodeType.TOPIC)
        nodes, _ = await expand_via_graph([fact], storage, hops=1)
        assert billing.id in {n.id for n in nodes}


class TestRelationConsolidation:

    async def test_find_similar_relation_pairs(self, storage):
        from epimemer.pipelines.reflection.relation_consolidation import (
            find_similar_relation_pairs,
        )
        emb = _FixedEmbed({
            "authored_by": [1.0, 0.0], "written_by": [1.0, 0.0], "funded_by": [0.0, 1.0],
        })
        a, b, c, d = (Topic(content=x) for x in ("a", "b", "c", "d"))
        for n in (a, b, c, d):
            await storage.store_node(n)
        await storage.store_edge(NodeEdge(src_id=a.id, dst_id=b.id, type=EdgeType.RELATED,
                                          label="authored_by", kind="relationship"))
        await storage.store_edge(NodeEdge(src_id=a.id, dst_id=c.id, type=EdgeType.RELATED,
                                          label="written_by", kind="relationship"))
        await storage.store_edge(NodeEdge(src_id=a.id, dst_id=d.id, type=EdgeType.RELATED,
                                          label="funded_by", kind="relationship"))
        pairs = await find_similar_relation_pairs(storage, emb, similarity_threshold=0.9)
        got = {frozenset((p["label_a"], p["label_b"])) for p in pairs}
        assert got == {frozenset(("authored_by", "written_by"))}

    async def test_apply_relation_merges(self, storage, embedding_provider):
        a, b, c = (Topic(content=x) for x in ("a", "b", "c"))
        for n in (a, b, c):
            await storage.store_node(n)
        await storage.store_edge(NodeEdge(src_id=a.id, dst_id=b.id, type=EdgeType.RELATED,
                                          label="written_by", kind="relationship"))
        await storage.store_edge(NodeEdge(src_id=a.id, dst_id=c.id, type=EdgeType.RELATED,
                                          label="authored_by", kind="relationship"))
        result, _ = await apply_reflection(
            storage, embedding_provider,
            relation_merges=[{"labels": ["written_by"], "into": "authored_by"}],
        )
        assert result["relations_consolidated"] == 1
        assert result["edges_relabeled"] == 1
        labels = {e.label for e in await storage.get_edges_from(a.id)}
        assert labels == {"authored_by"}

    async def test_reflect_surfaces_similar_relations_key(self, storage, embedding_provider):
        result, _ = await reflect(storage, embedding_provider)
        assert "similar_relations" in result and isinstance(result["similar_relations"], list)


class TestGraphNameValidationTools:
    """`use_graph` / `delete_graph` take arbitrary agent-supplied strings and
    reach SurrealQL that interpolates the database name."""

    async def test_use_graph_rejects_hostile_name(self, storage):
        result, _ = await tools.use_graph(
            "pwn`; REMOVE DATABASE `victim", storage, confirm=True
        )
        assert result["status"] == "invalid_name"
        assert "pwn`; REMOVE DATABASE `victim" not in await storage.list_databases()

    async def test_delete_graph_rejects_hostile_name(self, storage):
        before, _ = await tools.list_graphs(storage)
        result, _ = await tools.delete_graph("a;b", storage, confirm=True)
        assert result["status"] == "invalid_name"
        after, _ = await tools.list_graphs(storage)
        assert after["graphs"] == before["graphs"]

    async def test_use_graph_still_accepts_legal_name(self, storage):
        result, _ = await tools.use_graph("my-graph_2", storage, confirm=True)
        assert result["status"] in {"created", "switched"}
        assert result["active_graph"] == "my-graph_2"


class TestRunNetStdout:
    """`_run_net` used to swap `sys.stdout` for `sys.stderr` around execution to
    keep engine debug prints off MCP's stdio transport.

    Two problems: the swap is process-global across `await` points, so with
    overlapping tool calls one call saves another's redirected stdout as its
    "original" and the swap never unwinds; and the engine's prints are gated
    behind `verbose`, which defaults off, so there is nothing to suppress.
    """

    async def test_run_net_does_not_touch_stdout(self, capsys):
        original_stdout = sys.stdout

        async def _slow_double(x: int) -> int:
            # Yield control so the two runs genuinely interleave.
            await asyncio.sleep(0)
            return x * 2

        def _build() -> ExecutableGraph:
            return ExecutableGraphOperations.construct_graph([
                ListPlaceNode("Input", int, [5]),
                ListPlaceNode("Output", int),
                FunctionTransitionNode("double", _slow_double),
                ArgumentEdgeToTransition("Input", "double", "x"),
                ReturnedEdgeFromTransition("double", "Output"),
            ])

        results = await asyncio.gather(
            tools._run_net(_build(), "pipeline-a", None),
            tools._run_net(_build(), "pipeline-b", None),
        )

        for graph, fired in results:
            assert fired == 1
            output = next(p for p in graph.places if p.name == "Output")
            assert output.tokens == [10]

        assert sys.stdout is original_stdout
        assert capsys.readouterr().out == ""


def test_tool_count_matches_integration_doc():
    """INTEGRATION.md's stated tool count must track the actual registrations.

    A cheap guard against the doc drift catalogued in ISSUES.md — the count can
    only be stated in one canonical place, and this fails the moment a tool is
    added or removed without updating it.
    """
    import re
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    registered = (repo_root / "epimemer" / "mcp" / "server.py").read_text().count("@mcp.tool(")

    integration = (repo_root / "INTEGRATION.md").read_text()
    stated = re.search(r"listed with (\d+) tools", integration)
    assert stated is not None, "INTEGRATION.md no longer states a tool count to check"
    assert int(stated.group(1)) == registered, (
        f"INTEGRATION.md says {stated.group(1)} tools but server.py registers {registered}"
    )
