"""Capturing the pre-merge edge partition (#64 step 0a, REVIEW_MODE.md §7).

**Capture or lose.** `merge_nodes_tx` re-points every migrating edge onto the
survivor and collapses duplicates by `(src, dst, type)`, recording nothing about
which source owned which. Two sources citing one document leave a single
`sourced_from` edge afterwards, and no later pass can split it back in two. So
the partition exists at merge time and at no other moment, and a merge taken
before this capture is permanently irreversible.

Nothing reads the payload yet — `reverse_merge` is a later step. These tests
assert that what a reversal will need is *there*, which is the only property
that has a deadline.
"""

from datetime import datetime, timezone

import pytest

from epimemer.core.types import (
    DEFAULT_MERGE_UNDO_DEPTH,
    EdgeType,
    EmbeddingRecord,
    Fact,
    MERGE_UNDO_KEY,
    NodeEdge,
    NodeStatus,
    Topic,
    ValueSignal,
    read_merge_undo,
)
from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.pipelines.graph_construction.versioning import merge_nodes


@pytest.fixture
def embedding_provider() -> MockEmbeddingProvider:
    return MockEmbeddingProvider(model_id="mock-embed", dimension=8)


async def _fact(storage, embedding_provider, content: str) -> Fact:
    node = Fact(content=content, source_id="seg-1", value=ValueSignal())
    await storage.store_node(node)
    vector = (await embedding_provider.embed([content]))[0]
    await storage.store_embedding(EmbeddingRecord(
        item_id=node.id, model_id=embedding_provider.model_id, vector=vector,
    ))
    return node


def _survivor(content: str, sources) -> Fact:
    return Fact(
        content=content, source_id="seg-1", value=ValueSignal(),
        extraction_method="agent:merge",
        metadata={"merged_from": [s.id for s in sources]},
    )


async def _merge(storage, embedding_provider, sources, content, **kwargs) -> Fact:
    survivor = _survivor(content, sources)
    await merge_nodes(list(sources), survivor, storage, embedding_provider, **kwargs)
    return survivor


class TestThePartitionIsCaptured:
    async def test_each_captured_edge_names_the_source_it_belonged_to(
        self, storage, embedding_provider
    ):
        a = await _fact(storage, embedding_provider, "Bonn is the capital.")
        b = await _fact(storage, embedding_provider, "The capital is Bonn.")
        await storage.store_edge(NodeEdge(
            src_id=a.id, dst_id="doc-a", type=EdgeType.SOURCED_FROM,
        ))
        await storage.store_edge(NodeEdge(
            src_id=b.id, dst_id="doc-b", type=EdgeType.SOURCED_FROM,
        ))

        survivor = await _merge(
            storage, embedding_provider, [a, b], "Bonn is the capital city.",
        )

        undo = read_merge_undo(await storage.get_node(survivor.id))
        owners = {
            captured.edge["dst_id"]: captured.owner_id for captured in undo.edges
        }
        assert owners == {"doc-a": a.id, "doc-b": b.id}
        assert undo.source_ids == [a.id, b.id]

    async def test_two_sources_citing_one_document_are_kept_apart(
        self, storage, embedding_provider
    ):
        """The case that makes this urgent rather than merely useful.

        Migration collapses these two edges into one on the survivor, so
        afterwards the graph cannot say that *both* sources cited the document.
        Without the capture there is nothing left to split.
        """
        a = await _fact(storage, embedding_provider, "Bonn is the capital.")
        b = await _fact(storage, embedding_provider, "The capital is Bonn.")
        for source in (a, b):
            await storage.store_edge(NodeEdge(
                src_id=source.id, dst_id="doc-shared", type=EdgeType.SOURCED_FROM,
            ))

        survivor = await _merge(
            storage, embedding_provider, [a, b], "Bonn is the capital city.",
        )

        collapsed = await storage.get_edges_from(
            survivor.id, edge_type=EdgeType.SOURCED_FROM
        )
        assert len(collapsed) == 1, "migration still collapses, as it always did"

        undo = read_merge_undo(await storage.get_node(survivor.id))
        assert sorted(c.owner_id for c in undo.edges) == sorted([a.id, b.id])

    async def test_an_intra_set_edge_is_captured_and_flagged(
        self, storage, embedding_provider
    ):
        """The only class the merge deletes outright rather than re-points.

        A migrating edge between two merging sources becomes a self-loop, which
        `_migrate_edges_inplace` drops. It is on neither the sources nor the
        survivor afterwards, so nothing but this flag would bring it back.
        """
        a = await _fact(storage, embedding_provider, "Bonn is the capital.")
        b = await _fact(storage, embedding_provider, "The capital is Bonn.")
        await storage.store_edge(NodeEdge(
            src_id=a.id, dst_id=b.id, type=EdgeType.RELATED,
            label="restates", kind="relationship",
        ))

        survivor = await _merge(
            storage, embedding_provider, [a, b], "Bonn is the capital city.",
        )

        assert await storage.get_edges_from(survivor.id, edge_type=EdgeType.RELATED) == []
        for source in (a, b):
            assert await storage.get_edges_from(source.id, edge_type=EdgeType.RELATED) == []
            assert await storage.get_edges_to(source.id, edge_type=EdgeType.RELATED) == []

        undo = read_merge_undo(await storage.get_node(survivor.id))
        intra = [c for c in undo.edges if c.intra_set]
        assert len(intra) == 1
        assert intra[0].edge["src_id"] == a.id and intra[0].edge["dst_id"] == b.id

    async def test_edge_metadata_and_created_at_are_captured(
        self, storage, embedding_provider
    ):
        """The whole edge, never a hand-listed subset.

        `judged_by` will live in edge metadata (§3.1), so a payload missing it
        would have a reversal replay every edge with its attribution stripped —
        in the feature whose entire purpose is recording who judged what.
        """
        a = await _fact(storage, embedding_provider, "Bonn is the capital.")
        b = await _fact(storage, embedding_provider, "The capital is Bonn.")
        stamped = NodeEdge(
            src_id=a.id, dst_id="topic-capitals", type=EdgeType.TAGGED_WITH,
            metadata={"judged_by": "agent-1"},
        )
        await storage.store_edge(stamped)

        survivor = await _merge(
            storage, embedding_provider, [a, b], "Bonn is the capital city.",
        )

        undo = read_merge_undo(await storage.get_node(survivor.id))
        captured = next(c for c in undo.edges if c.edge["dst_id"] == "topic-capitals")
        assert captured.edge["metadata"] == {"judged_by": "agent-1"}
        assert captured.edge["created_at"] is not None
        assert NodeEdge(**captured.edge).created_at == stamped.created_at

    async def test_the_survivors_wording_is_kept_with_the_payload(
        self, storage, embedding_provider
    ):
        """Reversal deletes the survivor, so this is the only copy that outlives
        it — and a reversal that cannot quote what it withdrew is not much of a
        record."""
        a = await _fact(storage, embedding_provider, "Bonn is the capital.")
        b = await _fact(storage, embedding_provider, "The capital is Bonn.")

        survivor = await _merge(
            storage, embedding_provider, [a, b], "Bonn is the capital city.",
        )

        undo = read_merge_undo(await storage.get_node(survivor.id))
        assert undo.survivor_content == "Bonn is the capital city."
        assert undo.merged_at <= datetime.now(timezone.utc)

    async def test_topics_merged_through_reflection_are_captured_too(
        self, storage, embedding_provider
    ):
        """`merge_nodes` is generic over node kinds, so the payload rides along
        rather than being a fact-only feature nobody remembers to widen."""
        a = Topic(content="Capital cities", source_id="seg-1")
        b = Topic(content="Cities that are capitals", source_id="seg-1")
        for topic in (a, b):
            await storage.store_node(topic)
            vector = (await embedding_provider.embed([topic.content]))[0]
            await storage.store_embedding(EmbeddingRecord(
                item_id=topic.id, model_id=embedding_provider.model_id, vector=vector,
            ))
        await storage.store_edge(NodeEdge(
            src_id=a.id, dst_id="doc-a", type=EdgeType.SOURCED_FROM,
        ))

        survivor = Topic(
            content="Capital cities", source_id="seg-1",
            extraction_method="agent:merge",
            metadata={"merged_from": [a.id, b.id]},
        )
        await merge_nodes([a, b], survivor, storage, embedding_provider)

        undo = read_merge_undo(await storage.get_node(survivor.id))
        assert [c.edge["dst_id"] for c in undo.edges] == ["doc-a"]


class TestWhatIsDeliberatelyNotCaptured:
    async def test_an_edge_the_merge_leaves_alone_is_not_captured(
        self, storage, embedding_provider
    ):
        """Judgment edges stay on the source under `migration_disposition`
        (#65), so capturing one would have a reversal recreate an edge that
        never left — a duplicate, not a restoration."""
        a = await _fact(storage, embedding_provider, "Bonn is the capital.")
        b = await _fact(storage, embedding_provider, "The capital is Bonn.")
        other = await _fact(storage, embedding_provider, "Berlin is the capital.")
        await storage.store_edge(NodeEdge(
            src_id=a.id, dst_id=other.id, type=EdgeType.CONTRADICTION,
        ))

        survivor = await _merge(
            storage, embedding_provider, [a, b], "Bonn is the capital city.",
        )

        assert len(await storage.get_edges_from(a.id, edge_type=EdgeType.CONTRADICTION)) == 1
        undo = read_merge_undo(await storage.get_node(survivor.id))
        assert [c.edge["type"] for c in undo.edges] == []

    async def test_a_node_that_never_merged_carries_no_payload(
        self, storage, embedding_provider
    ):
        plain = await _fact(storage, embedding_provider, "Bonn is the capital.")
        assert read_merge_undo(await storage.get_node(plain.id)) is None


class TestTheDepthBound:
    """Depth is a property of the *chain*, not of any one node's list.

    `A+B→S1`, then `S1+C→S2`: unwinding `S2` back to `A, B, C` needs `S2`'s
    partition **and** `S1`'s. So the bound is on how far back along the lineage
    payloads are retained, and eviction happens as the chain grows.
    """

    async def _chain(self, storage, embedding_provider, *, depth_setting):
        a = await _fact(storage, embedding_provider, "Bonn is the capital.")
        b = await _fact(storage, embedding_provider, "The capital is Bonn.")
        await storage.store_edge(NodeEdge(
            src_id=a.id, dst_id="doc-a", type=EdgeType.SOURCED_FROM,
        ))
        first = await _merge(
            storage, embedding_provider, [a, b], "Bonn is the capital city.",
            undo_depth=depth_setting,
        )
        c = await _fact(storage, embedding_provider, "The capital city is Bonn.")
        second = await _merge(
            storage, embedding_provider, [first, c], "Bonn is the capital city of West Germany.",
            undo_depth=depth_setting,
        )
        return first, second

    async def test_within_the_bound_every_level_stays_reversible(
        self, storage, embedding_provider
    ):
        first, second = await self._chain(
            storage, embedding_provider, depth_setting=DEFAULT_MERGE_UNDO_DEPTH,
        )
        assert read_merge_undo(await storage.get_node(first.id)) is not None
        assert read_merge_undo(await storage.get_node(second.id)) is not None

    async def test_past_the_bound_the_older_payload_is_cleared(
        self, storage, embedding_provider
    ):
        """Depth 1 keeps only the merge just made."""
        first, second = await self._chain(
            storage, embedding_provider, depth_setting=1,
        )
        assert read_merge_undo(await storage.get_node(first.id)) is None
        assert read_merge_undo(await storage.get_node(second.id)) is not None

    async def test_eviction_discards_capability_and_never_a_claim(
        self, storage, embedding_provider
    ):
        """The first structure here that deliberately forgets, so what it keeps
        is worth asserting: the node, its content, its status, its lifecycle and
        its lineage all survive. Only the replay instructions go."""
        first, _ = await self._chain(storage, embedding_provider, depth_setting=1)

        evicted = await storage.get_node(first.id)
        assert evicted.content == "Bonn is the capital city."
        assert evicted.status is NodeStatus.MERGED
        assert evicted.lifecycle and evicted.lifecycle[-1].because is NodeStatus.MERGED
        assert len(await storage.get_edges_from(first.id, edge_type=EdgeType.MERGED_INTO)) == 1
        assert MERGE_UNDO_KEY not in evicted.metadata

    async def test_an_evicted_payload_is_distinguishable_from_one_that_never_existed(
        self, storage, embedding_provider
    ):
        """A reversal refusing has to say which, since one is permanent and the
        other is a mistake. `merged_from` is what tells them apart, so eviction
        must not touch it."""
        first, _ = await self._chain(storage, embedding_provider, depth_setting=1)
        never_merged = await _fact(storage, embedding_provider, "Unrelated claim.")

        evicted = await storage.get_node(first.id)
        assert evicted.metadata.get("merged_from")
        assert (await storage.get_node(never_merged.id)).metadata.get("merged_from") is None

    async def test_a_depth_below_one_is_refused(self, storage, embedding_provider):
        """It would capture the partition and discard it in the same call, which
        is worse than not capturing at all — the cost with none of the benefit."""
        a = await _fact(storage, embedding_provider, "Bonn is the capital.")
        b = await _fact(storage, embedding_provider, "The capital is Bonn.")
        with pytest.raises(ValueError, match="at least 1"):
            await _merge(
                storage, embedding_provider, [a, b], "Bonn is the capital city.",
                undo_depth=0,
            )
