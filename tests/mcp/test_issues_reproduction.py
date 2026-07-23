"""Regression tests for the defects documented in ISSUES.md.

Each test reconstructs the scenario from the empirical walkthrough described in
ISSUES.md. They began life as ``xfail(strict=True)`` reproductions — failing
against the buggy code to confirm each defect was present. The defects have
since been fixed (embed-on-supersession, status-filtered vector search, and
edge migration in ``supersede_node``), so the markers are gone and these now
assert the corrected behaviour as permanent regression guards.

Issues 4 and 5 were classified minor / by-design; their tests document the
behaviour that was deliberately kept (history edges are hidden from default
traversal; ``link`` resolves epistemic nodes only).

References are to ISSUES.md (discovered 2026-06-25).
"""

import pytest

from epimemer.core.types import (
    EdgeType,
    EmbeddingRecord,
    Fact,
    NodeEdge,
    NodeStatus,
    RawDocument,
    Topic,
)
from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.mcp.tools import link, query_graph, search, update
from epimemer.storage.protocol import StorageBackend


@pytest.fixture
def embedding_provider() -> MockEmbeddingProvider:
    return MockEmbeddingProvider(model_id="mock-embed", dimension=8)


async def _store_with_embedding(
    storage: StorageBackend,
    embedding_provider: MockEmbeddingProvider,
    node,
) -> None:
    """Store a node the way the normal ingest path does: node + its embedding."""
    await storage.store_node(node)
    vecs = await embedding_provider.embed([node.content])
    await storage.store_embedding(
        EmbeddingRecord(
            item_id=node.id,
            model_id=embedding_provider.model_id,
            vector=vecs[0],
        )
    )


# --- Issue 1: update creates a node with no embedding (unsearchable) ----------


class TestIssue1UpdateEmbedsReplacement:
    """ISSUES.md #1 (high): the corrected node must enter the vector index."""

    async def test_update_embeds_the_replacement_node(
        self, storage, embedding_provider
    ):
        old = Topic(content="Petri nets model concurrency", source_id="s1")
        await _store_with_embedding(storage, embedding_provider, old)

        result, _ = await update(
            old.id, "Petri nets model concurrent systems", storage, embedding_provider
        )
        new_id = result["new_node_id"]

        embeddings = await storage.get_embeddings_for_item(new_id)
        assert len(embeddings) >= 1

    async def test_corrected_node_is_retrievable_by_search(
        self, storage, embedding_provider
    ):
        old = Topic(content="Petri nets model concurrency", source_id="s1")
        await _store_with_embedding(storage, embedding_provider, old)

        new_content = "Petri nets are a modelling language for concurrent systems"
        result, _ = await update(old.id, new_content, storage, embedding_provider)
        new_id = result["new_node_id"]

        search_result, _ = await search(
            new_content, storage, embedding_provider, k=25, graph_hops=0,
        )
        returned_ids = {n["id"] for n in search_result["nodes"]}
        assert new_id in returned_ids


# --- Issue 2: vector_search / search returns superseded nodes -----------------


class TestIssue2SearchExcludesSupersededNodes:
    """ISSUES.md #2 (high): retrieval must apply a status guard."""

    async def test_vector_search_excludes_superseded_nodes(self, storage):
        node = Topic(content="stale content", source_id="s1")
        await storage.store_node(node)
        await storage.store_embedding(
            EmbeddingRecord(item_id=node.id, model_id="m", vector=[1.0, 0.0, 0.0])
        )
        await storage.update_node_status(node.id, NodeStatus.SUPERSEDED)

        # Exact-match query → cosine 1.0 → would top the list if not filtered.
        results = await storage.vector_search([1.0, 0.0, 0.0], "m", k=10)
        returned_ids = {item_id for item_id, _ in results}
        assert node.id not in returned_ids

    async def test_search_does_not_return_superseded_node(
        self, storage, embedding_provider
    ):
        node = Topic(content="neural networks for vision", source_id="s1")
        await _store_with_embedding(storage, embedding_provider, node)
        await storage.update_node_status(node.id, NodeStatus.SUPERSEDED)

        result, _ = await search(
            node.content, storage, embedding_provider, k=10, graph_hops=0,
        )
        returned_ids = {n["id"] for n in result["nodes"]}
        assert node.id not in returned_ids


# --- Issue 3: update / supersede_node orphans the node's edges ----------------


class TestIssue3SupersedeMigratesEdges:
    """ISSUES.md #3 (medium): the replacement inherits the original's edges."""

    async def test_supporting_edge_follows_to_replacement(
        self, storage, embedding_provider
    ):
        old = Topic(content="old topic", source_id="s1")
        fact = Fact(content="a supporting fact", source_id="s1")
        await storage.store_node(old)
        await storage.store_node(fact)
        await storage.store_edge(
            NodeEdge(src_id=fact.id, dst_id=old.id, type=EdgeType.SUPPORTS)
        )

        result, _ = await update(old.id, "new topic", storage, embedding_provider)
        new_id = result["new_node_id"]

        # The supporting fact now backs the current node, not the dead one.
        supports_into_new = await storage.get_edges_to(
            new_id, edge_type=EdgeType.SUPPORTS
        )
        assert len(supports_into_new) == 1
        assert supports_into_new[0].src_id == fact.id

        # ...and no longer backs the superseded original.
        supports_into_old = await storage.get_edges_to(
            old.id, edge_type=EdgeType.SUPPORTS
        )
        assert supports_into_old == []


# --- Issue 4: superseded_by lineage is not traversable (minor / by design) ----


class TestIssue4LineageTraversal:
    """ISSUES.md #4 (minor): history edges are hidden from default traversal.

    This was kept intentionally — lineage is metadata, not knowledge — so the
    test documents both the default behaviour and the explicit-filter workaround.
    """

    async def test_default_traversal_hides_replacement(
        self, storage, embedding_provider
    ):
        old = Topic(content="old topic", source_id="s1")
        await storage.store_node(old)
        result, _ = await update(old.id, "new topic", storage, embedding_provider)
        new_id = result["new_node_id"]

        graph, _ = await query_graph(old.id, storage, hops=2)
        node_ids = {n["id"] for n in graph["nodes"]}
        edge_types = {e["type"] for e in graph["edges"]}

        assert new_id not in node_ids
        assert "superseded_by" not in edge_types

    async def test_explicit_edge_type_filter_surfaces_replacement(
        self, storage, embedding_provider
    ):
        old = Topic(content="old topic", source_id="s1")
        await storage.store_node(old)
        result, _ = await update(old.id, "new topic", storage, embedding_provider)
        new_id = result["new_node_id"]

        graph, _ = await query_graph(
            old.id, storage, hops=2, edge_types=["superseded_by"]
        )
        node_ids = {n["id"] for n in graph["nodes"]}
        assert new_id in node_ids


# --- Issue 5: link cannot target source-document nodes (minor) ----------------


class TestIssue5LinkCannotTargetDocument:
    """ISSUES.md #5 (minor): `link` resolves epistemic nodes only.

    Provenance (`about`) edges remain ingest-only; this restriction is kept and
    documented rather than changed.
    """

    async def test_link_from_document_node_is_rejected(self, storage):
        doc = RawDocument(content="source document text")
        await storage.store_document(doc)
        topic = Topic(content="a topic", source_id=doc.id)
        await storage.store_node(topic)

        with pytest.raises(ValueError, match="Source node"):
            await link(doc.id, topic.id, storage, edge_type="about")


# --- Combined: the headline user-visible failure (Issues 1 + 2) ---------------


class TestCombinedUpdateRetrieval:
    """ISSUES.md intro: after `update`, the correction is findable and the stale
    version no longer ranks. Requires Issue 1 (embed) and Issue 2 (status filter)."""

    async def test_update_makes_correction_findable_and_hides_stale(
        self, storage, embedding_provider
    ):
        old = Topic(content="Petri nets model concurrency", source_id="s1")
        await _store_with_embedding(storage, embedding_provider, old)

        new_content = "Petri nets are a modelling language for concurrent systems"
        result, _ = await update(old.id, new_content, storage, embedding_provider)
        new_id = result["new_node_id"]

        search_result, _ = await search(
            new_content, storage, embedding_provider, k=25, graph_hops=0,
        )
        returned_ids = {n["id"] for n in search_result["nodes"]}

        assert new_id in returned_ids        # correction is retrievable (Issue 1)
        assert old.id not in returned_ids    # superseded original is gone (Issue 2)
