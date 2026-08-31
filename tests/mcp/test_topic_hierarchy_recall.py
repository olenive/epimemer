"""Hierarchy-aware recall: the retrieval half of topic splitting.

Splitting a broad topic builds a SUBTOPIC_OF DAG (child → parent), but that
structure only pays off if retrieval exposes it. These tests pin the two things
that make a split useful at recall time: search annotating returned topics with
their neighbours in the hierarchy, and `topic_tree` as a drill-down primitive
that returns shape and previews rather than the whole subtree's material.

Hierarchies are built through the public `apply_reflection` path so the tests
guard the structure the system actually produces, not a hand-built one.
"""

import pytest

from epimemer.core.types import (
    EdgeType,
    EmbeddingRecord,
    Fact,
    Topic,
)
from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.mcp.tools import apply_reflection, search, topic_tree


@pytest.fixture
def embedding_provider() -> MockEmbeddingProvider:
    return MockEmbeddingProvider(model_id="mock-embed", dimension=8)


async def _store_topic_with_embedding(storage, model_id, content, vector):
    t = Topic(content=content, source_id="s1")
    await storage.store_node(t)
    await storage.store_embedding(EmbeddingRecord(item_id=t.id, model_id=model_id, vector=vector))
    return t


async def _children_of(storage, parent_id):
    """The topics pointing at `parent_id` via SUBTOPIC_OF, content-keyed."""
    edges = await storage.get_edges_to(parent_id, edge_type=EdgeType.SUBTOPIC_OF)
    children = {}
    for edge in edges:
        node = await storage.get_node(edge.src_id)
        if node is not None:
            children[node.content] = node
    return children


async def _build_hierarchy(storage, embedding_provider, query_vector):
    """A three-level hierarchy: root → two mid topics → two leaves under one mid.

    Every topic is embedded on `query_vector` so vector search returns all of
    them and only the annotation logic decides what each node carries.
    """
    root = await _store_topic_with_embedding(
        storage,
        embedding_provider.model_id,
        "root topic",
        query_vector,
    )
    await apply_reflection(
        storage,
        embedding_provider,
        splits=[{"topic_id": root.id, "subtopics": ["mid one", "mid two"]}],
    )
    mids = await _children_of(storage, root.id)
    await apply_reflection(
        storage,
        embedding_provider,
        splits=[{"topic_id": mids["mid one"].id, "subtopics": ["leaf a", "leaf b"]}],
    )
    leaves = await _children_of(storage, mids["mid one"].id)
    return root, mids, leaves


class TestSearchHierarchyAnnotations:
    async def test_annotates_parents_and_subtopics(self, storage, embedding_provider):
        qvec = (await embedding_provider.embed(["anything"]))[0]
        root, mids, leaves = await _build_hierarchy(storage, embedding_provider, qvec)

        result, _ = await search(
            "anything",
            storage,
            embedding_provider,
            k=20,
            graph_hops=0,
        )
        by_id = {n["id"]: n for n in result["nodes"]}

        # The root is nobody's child, but it has both mid topics under it.
        assert "parents" not in by_id[root.id]
        assert {s["id"] for s in by_id[root.id]["subtopics"]} == {
            mids["mid one"].id,
            mids["mid two"].id,
        }

        # A mid topic sits in the middle: one parent above, two leaves below.
        mid_one = by_id[mids["mid one"].id]
        assert [p["id"] for p in mid_one["parents"]] == [root.id]
        assert {s["id"] for s in mid_one["subtopics"]} == {leaves["leaf a"].id, leaves["leaf b"].id}

        # A leaf has a parent and nothing below it.
        leaf_a = by_id[leaves["leaf a"].id]
        assert [p["id"] for p in leaf_a["parents"]] == [mids["mid one"].id]
        assert "subtopics" not in leaf_a

    async def test_annotations_carry_previews_not_full_content(self, storage, embedding_provider):
        """Annotations exist so the agent can decide whether to drill.

        Inlining the neighbour's full material would defeat that — the caller
        would receive everything it was supposed to be able to skip.
        """
        qvec = (await embedding_provider.embed(["anything"]))[0]
        long_content = "x" * 500
        parent = await _store_topic_with_embedding(
            storage,
            embedding_provider.model_id,
            long_content,
            qvec,
        )
        await apply_reflection(
            storage,
            embedding_provider,
            splits=[{"topic_id": parent.id, "subtopics": ["child"]}],
        )
        children = await _children_of(storage, parent.id)

        result, _ = await search(
            "anything",
            storage,
            embedding_provider,
            k=20,
            graph_hops=0,
        )
        by_id = {n["id"]: n for n in result["nodes"]}

        preview = by_id[children["child"].id]["parents"][0]["content_preview"]
        assert preview != long_content
        assert len(preview) <= 101  # 100 chars plus the ellipsis
        assert long_content.startswith(preview[:100])

    async def test_unrelated_topics_carry_no_hierarchy_keys(self, storage, embedding_provider):
        """A topic outside any hierarchy must not gain empty annotation keys."""
        qvec = (await embedding_provider.embed(["anything"]))[0]
        lone = await _store_topic_with_embedding(
            storage,
            embedding_provider.model_id,
            "lone topic",
            qvec,
        )

        result, _ = await search(
            "anything",
            storage,
            embedding_provider,
            k=20,
            graph_hops=0,
        )
        node = next(n for n in result["nodes"] if n["id"] == lone.id)

        assert "parents" not in node
        assert "subtopics" not in node

    async def test_non_topic_nodes_are_not_annotated(self, storage, embedding_provider):
        """Only Topics participate in SUBTOPIC_OF; facts must be left alone."""
        qvec = (await embedding_provider.embed(["anything"]))[0]
        f = Fact(content="a fact", source_id="s1")
        await storage.store_node(f)
        await storage.store_embedding(
            EmbeddingRecord(item_id=f.id, model_id=embedding_provider.model_id, vector=qvec)
        )

        result, _ = await search(
            "anything",
            storage,
            embedding_provider,
            k=20,
            graph_hops=0,
        )
        node = next(n for n in result["nodes"] if n["id"] == f.id)

        assert "parents" not in node
        assert "subtopics" not in node


class TestTopicTree:
    async def test_returns_ancestors_and_nested_descendants(self, storage, embedding_provider):
        qvec = (await embedding_provider.embed(["anything"]))[0]
        root, mids, leaves = await _build_hierarchy(storage, embedding_provider, qvec)

        result, meta = await topic_tree(root.id, storage, depth=2)

        assert result["topic"]["id"] == root.id
        assert result["ancestors"] == []

        mid_one = next(s for s in result["subtopics"] if s["id"] == mids["mid one"].id)
        assert {c["id"] for c in mid_one["subtopics"]} == {leaves["leaf a"].id, leaves["leaf b"].id}
        mid_two = next(s for s in result["subtopics"] if s["id"] == mids["mid two"].id)
        assert mid_two["subtopics"] == []

        # root + 2 mids + 2 leaves
        assert meta.nodes_returned == 5

    async def test_ancestors_run_from_nearest_parent_to_root(self, storage, embedding_provider):
        qvec = (await embedding_provider.embed(["anything"]))[0]
        root, mids, leaves = await _build_hierarchy(storage, embedding_provider, qvec)

        result, _ = await topic_tree(leaves["leaf a"].id, storage, depth=2)

        assert [a["id"] for a in result["ancestors"]] == [mids["mid one"].id, root.id]
        assert result["subtopics"] == []

    async def test_depth_limits_descent_and_flags_more(self, storage, embedding_provider):
        """At the depth limit, a node with children says so rather than lying.

        Without the flag the caller cannot tell a true leaf from a truncated
        branch, and drill-down stops one level short of the material.
        """
        qvec = (await embedding_provider.embed(["anything"]))[0]
        root, mids, _ = await _build_hierarchy(storage, embedding_provider, qvec)

        result, _ = await topic_tree(root.id, storage, depth=1)

        mid_one = next(s for s in result["subtopics"] if s["id"] == mids["mid one"].id)
        mid_two = next(s for s in result["subtopics"] if s["id"] == mids["mid two"].id)
        assert mid_one["subtopics"] == []
        assert mid_one["has_more"] is True
        # mid two really is a leaf, so it must not claim there is more.
        assert "has_more" not in mid_two

    async def test_returns_previews_not_material(self, storage, embedding_provider):
        qvec = (await embedding_provider.embed(["anything"]))[0]
        long_content = "y" * 500
        parent = await _store_topic_with_embedding(
            storage,
            embedding_provider.model_id,
            long_content,
            qvec,
        )
        await apply_reflection(
            storage,
            embedding_provider,
            splits=[{"topic_id": parent.id, "subtopics": ["child"]}],
        )

        result, _ = await topic_tree(parent.id, storage, depth=2)

        assert "content" not in result["topic"]
        assert len(result["topic"]["content_preview"]) <= 101

    async def test_rejects_depth_below_one(self, storage, embedding_provider):
        """depth=0 would return a tree with no tree in it — reject, don't guess."""
        t = Topic(content="a topic", source_id="s1")
        await storage.store_node(t)

        with pytest.raises(ValueError, match="depth"):
            await topic_tree(t.id, storage, depth=0)

    async def test_rejects_unknown_topic(self, storage):
        with pytest.raises(ValueError, match="Topic"):
            await topic_tree("nonexistent", storage)

    async def test_rejects_non_topic_node(self, storage):
        f = Fact(content="a fact", source_id="s1")
        await storage.store_node(f)

        with pytest.raises(ValueError, match="Topic"):
            await topic_tree(f.id, storage)
