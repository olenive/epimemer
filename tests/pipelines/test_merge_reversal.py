"""Reversing a merge (#64 step 0c, REVIEW_MODE.md §7).

**Reversing returns the graph to the status it had before the merge, and
reversing back and forth N times is indistinguishable from doing it once.**
That principle is what most of these tests check, because it is what makes a
reversal safe to offer at all: an undo that leaves residue is a second event,
and a graph accumulating one husk per disagreement is worse than the merge it
was undoing.

The other half is the guard. Reversal ends in the only hard delete this system
performs, so an edge the guard does not notice is an edge the reversal
destroys — and *a contested claim losing its contest record* is exactly the
loss "nothing is destroyed" exists to prevent.
"""

import pytest

from epimemer.core.types import (
    ClaimKind,
    EdgeType,
    EmbeddingRecord,
    Fact,
    Inference,
    NodeEdge,
    NodeStatus,
    ValueSignal,
    read_merge_undo,
)
from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.mcp import tools
from epimemer.pipelines.graph_construction.versioning import (
    ReverseRefused,
    merge_nodes,
    reverse_merge,
)
from epimemer.storage.protocol import MergeOverrides


@pytest.fixture
def embedding_provider() -> MockEmbeddingProvider:
    return MockEmbeddingProvider(model_id="mock-embed", dimension=8)


async def _fact(storage, embedding_provider, content: str) -> Fact:
    node = Fact(
        content=content, source_id="seg-1", claim_kind=ClaimKind.STATE,
        value=ValueSignal(),
    )
    await storage.store_node(node)
    vector = (await embedding_provider.embed([content]))[0]
    await storage.store_embedding(EmbeddingRecord(
        item_id=node.id, model_id=embedding_provider.model_id, vector=vector,
    ))
    return node


async def _merge(storage, embedding_provider, sources, content="Bonn is the capital city.",
                 **kwargs) -> Fact:
    survivor = Fact(
        content=content, source_id="seg-1", claim_kind=ClaimKind.STATE,
        value=ValueSignal(), extraction_method="agent:merge",
        metadata={"merged_from": [s.id for s in sources]},
    )
    await merge_nodes(list(sources), survivor, storage, embedding_provider, **kwargs)
    return survivor


async def _pair(storage, embedding_provider):
    a = await _fact(storage, embedding_provider, "Bonn is the capital.")
    b = await _fact(storage, embedding_provider, "The capital is Bonn.")
    return a, b


def _signatures(edges):
    return sorted((e.src_id, e.dst_id, e.type.value) for e in edges)


async def _all_edges(storage, node_id):
    return [
        *await storage.get_edges_from(node_id),
        *await storage.get_edges_to(node_id),
    ]


class TestTheGraphComesBack:
    async def test_the_sources_are_active_again_with_their_own_edges(
        self, storage, embedding_provider
    ):
        a, b = await _pair(storage, embedding_provider)
        await storage.store_edge(NodeEdge(
            src_id=a.id, dst_id="doc-a", type=EdgeType.SOURCED_FROM,
        ))
        await storage.store_edge(NodeEdge(
            src_id=b.id, dst_id="doc-b", type=EdgeType.SOURCED_FROM,
        ))
        survivor = await _merge(storage, embedding_provider, [a, b])

        result = await reverse_merge(survivor.id, storage)

        assert result["reversed"] is True
        assert sorted(result["restored_ids"]) == sorted([a.id, b.id])
        for source, document in ((a, "doc-a"), (b, "doc-b")):
            node = await storage.get_node(source.id)
            assert node.status is NodeStatus.ACTIVE
            assert node.superseded_at is None
            edges = await storage.get_edges_from(
                source.id, edge_type=EdgeType.SOURCED_FROM
            )
            assert [e.dst_id for e in edges] == [document]

    async def test_an_edge_the_merge_collapsed_is_split_back_in_two(
        self, storage, embedding_provider
    ):
        """The case the capture exists for. Migration keeps one edge per
        `(src, dst, type)`, so after the merge the graph cannot say that *both*
        sources cited the document — and nothing but the payload can."""
        a, b = await _pair(storage, embedding_provider)
        for source in (a, b):
            await storage.store_edge(NodeEdge(
                src_id=source.id, dst_id="doc-shared", type=EdgeType.SOURCED_FROM,
            ))
        survivor = await _merge(storage, embedding_provider, [a, b])
        assert len(await storage.get_edges_from(
            survivor.id, edge_type=EdgeType.SOURCED_FROM
        )) == 1

        await reverse_merge(survivor.id, storage)

        for source in (a, b):
            edges = await storage.get_edges_from(
                source.id, edge_type=EdgeType.SOURCED_FROM
            )
            assert [e.dst_id for e in edges] == ["doc-shared"]

    async def test_an_intra_set_edge_comes_back(self, storage, embedding_provider):
        """The merge deletes this one outright rather than re-pointing it, so
        nothing else in this file would notice its loss."""
        a, b = await _pair(storage, embedding_provider)
        await storage.store_edge(NodeEdge(
            src_id=a.id, dst_id=b.id, type=EdgeType.RELATED,
            label="restates", kind="relationship",
        ))
        survivor = await _merge(storage, embedding_provider, [a, b])

        await reverse_merge(survivor.id, storage)

        edges = await storage.get_edges_from(a.id, edge_type=EdgeType.RELATED)
        assert [e.dst_id for e in edges] == [b.id]

    async def test_edge_metadata_and_dates_survive_the_cycle(
        self, storage, embedding_provider
    ):
        """`judged_by` will live in edge metadata, so a reversal that replayed
        edges from a partial copy would delete the judge — in the feature whose
        whole purpose is recording one."""
        a, b = await _pair(storage, embedding_provider)
        original = NodeEdge(
            src_id=a.id, dst_id="topic-capitals", type=EdgeType.TAGGED_WITH,
            metadata={"judged_by": "agent-1"},
        )
        await storage.store_edge(original)
        survivor = await _merge(storage, embedding_provider, [a, b])

        await reverse_merge(survivor.id, storage)

        restored = (await storage.get_edges_from(
            a.id, edge_type=EdgeType.TAGGED_WITH
        ))[0]
        assert restored.metadata == {"judged_by": "agent-1"}
        assert restored.created_at == original.created_at

    async def test_the_survivor_and_its_vector_are_gone(
        self, storage, embedding_provider
    ):
        """The vector is stored per item, so deleting the node alone would
        strand an entry the index still returns."""
        a, b = await _pair(storage, embedding_provider)
        survivor = await _merge(storage, embedding_provider, [a, b])

        await reverse_merge(survivor.id, storage)

        assert await storage.get_node(survivor.id) is None
        assert list(await storage.get_embeddings_for_item(survivor.id)) == []
        assert await _all_edges(storage, survivor.id) == []

    async def test_a_dependent_inference_is_returned_to_its_premise_unflagged(
        self, storage, embedding_provider
    ):
        """The merge re-pointed `derived_from` onto the survivor and flagged the
        inference *your premise was reworded*. Reversal points it back, so there
        is nothing left to re-read — a flag here would assert a change the
        reversal has just undone."""
        a, b = await _pair(storage, embedding_provider)
        drawn = Inference(content="So Bonn is in West Germany.", source_id="seg-1")
        await storage.store_node(drawn)
        await storage.store_edge(NodeEdge(
            src_id=drawn.id, dst_id=a.id, type=EdgeType.DERIVED_FROM,
        ))
        survivor = await _merge(storage, embedding_provider, [a, b])
        assert len(await storage.get_edges_from(
            a.id, edge_type=EdgeType.EVIDENCE_MERGED
        )) == 1

        await reverse_merge(survivor.id, storage)

        premises = await storage.get_edges_from(
            drawn.id, edge_type=EdgeType.DERIVED_FROM
        )
        assert [e.dst_id for e in premises] == [a.id]
        assert await storage.get_edges_from(
            a.id, edge_type=EdgeType.EVIDENCE_MERGED
        ) == []

    async def test_the_lineage_edges_the_merge_wrote_are_removed(
        self, storage, embedding_provider
    ):
        a, b = await _pair(storage, embedding_provider)
        survivor = await _merge(storage, embedding_provider, [a, b])

        await reverse_merge(survivor.id, storage)

        for source in (a, b):
            assert await storage.get_edges_from(
                source.id, edge_type=EdgeType.MERGED_INTO
            ) == []


class TestNCyclesEqualOne:
    """Status is restored, history is appended — the one place exactness does
    not hold, and it is by design: `lifecycle` is append-only because a node
    leaving the active set twice is #53's recurrence."""

    async def _cycle(self, storage, embedding_provider, sources):
        survivor = await _merge(storage, embedding_provider, sources)
        await reverse_merge(survivor.id, storage)

    async def test_two_cycles_leave_the_same_active_graph_as_one(
        self, storage, embedding_provider
    ):
        a, b = await _pair(storage, embedding_provider)
        await storage.store_edge(NodeEdge(
            src_id=a.id, dst_id="doc-a", type=EdgeType.SOURCED_FROM,
        ))

        await self._cycle(storage, embedding_provider, [a, b])
        after_one = _signatures(await _all_edges(storage, a.id))
        await storage.set_merge_overrides(MergeOverrides(cycle_limit=5))
        await self._cycle(storage, embedding_provider, [a, b])

        assert _signatures(await _all_edges(storage, a.id)) == after_one
        node = await storage.get_node(a.id)
        assert node.status is NodeStatus.ACTIVE
        assert node.superseded_at is None

    async def test_each_cycle_appends_one_closed_episode(
        self, storage, embedding_provider
    ):
        """The record that it happened, which is not a flag and is not
        returned — and is what `merge_cycle_limit` reads."""
        a, b = await _pair(storage, embedding_provider)
        await storage.set_merge_overrides(MergeOverrides(cycle_limit=5))

        await self._cycle(storage, embedding_provider, [a, b])
        await self._cycle(storage, embedding_provider, [a, b])

        node = await storage.get_node(a.id)
        assert len(node.lifecycle) == 2
        assert all(
            episode.because is NodeStatus.MERGED and episode.restored_at is not None
            for episode in node.lifecycle
        )

    async def test_the_third_merge_of_an_oscillating_pair_refuses(
        self, storage, embedding_provider
    ):
        """0b's gate, reached the only way it can be: through real reversals."""
        a, b = await _pair(storage, embedding_provider)
        await self._cycle(storage, embedding_provider, [a, b])
        await self._cycle(storage, embedding_provider, [a, b])

        result, _ = await tools.merge_facts(
            source_ids=[a.id, b.id], content="Bonn is the capital city.",
            storage=storage, embedding_provider=embedding_provider,
        )

        assert result["merged"] is False
        assert "merge_cycle_limit" in result["refused"]


class TestTheGuardRefusesRatherThanDestroys:
    async def test_a_fact_that_never_merged_is_refused_distinguishably(
        self, storage, embedding_provider
    ):
        plain = await _fact(storage, embedding_provider, "Bonn is the capital.")

        refusal = await reverse_merge(plain.id, storage)

        assert isinstance(refusal, ReverseRefused)
        assert "not made by a merge" in refusal.reason

    async def test_an_evicted_payload_refuses_differently_and_says_it_is_permanent(
        self, storage, embedding_provider
    ):
        """One is a mistake the caller can correct, the other no setting brings
        back. A single message for both would send someone to look for a knob
        that cannot help."""
        a, b = await _pair(storage, embedding_provider)
        first = await _merge(storage, embedding_provider, [a, b], undo_depth=1)
        c = await _fact(storage, embedding_provider, "The capital city is Bonn.")
        await _merge(
            storage, embedding_provider, [first, c],
            content="Bonn is the capital city of West Germany.", undo_depth=1,
        )

        refusal = await reverse_merge(first.id, storage)

        assert isinstance(refusal, ReverseRefused)
        assert "permanent" in refusal.reason

    async def test_a_survivor_merged_again_is_refused(
        self, storage, embedding_provider
    ):
        a, b = await _pair(storage, embedding_provider)
        first = await _merge(storage, embedding_provider, [a, b])
        c = await _fact(storage, embedding_provider, "The capital city is Bonn.")
        await _merge(
            storage, embedding_provider, [first, c],
            content="Bonn is the capital city of West Germany.",
        )

        refusal = await reverse_merge(first.id, storage)

        assert isinstance(refusal, ReverseRefused)
        assert "merged into" in refusal.reason
        assert "Reverse that merge first" in refusal.reason

    @pytest.mark.parametrize("edge_type", [
        EdgeType.CONTRADICTION, EdgeType.SIMILARITY, EdgeType.VARIANT_OF,
        EdgeType.TAGGED_WITH, EdgeType.RELATED,
    ])
    async def test_anything_added_since_the_merge_refuses_rather_than_being_deleted(
        self, storage, embedding_provider, edge_type
    ):
        """Stated as a set difference rather than a list of types, so an edge
        type invented next year is refused by default rather than deleted by
        omission. The parametrisation is a sample of that set, not its
        definition."""
        a, b = await _pair(storage, embedding_provider)
        survivor = await _merge(storage, embedding_provider, [a, b])
        later = await _fact(storage, embedding_provider, "Berlin is the capital.")
        added = NodeEdge(
            src_id=survivor.id, dst_id=later.id, type=edge_type,
            **({"label": "disputes", "kind": "relationship"}
               if edge_type is EdgeType.RELATED else {}),
        )
        await storage.store_edge(added)

        refusal = await reverse_merge(survivor.id, storage)

        assert isinstance(refusal, ReverseRefused)
        assert edge_type.value in refusal.reason
        assert await storage.get_node(survivor.id) is not None
        assert len(await _all_edges(storage, survivor.id)) > 0

    async def test_an_incoming_edge_added_since_the_merge_also_refuses(
        self, storage, embedding_provider
    ):
        """The sweep is over both directions. An inference drawn on the survivor
        after the merge points *at* it, and would be orphaned by the delete."""
        a, b = await _pair(storage, embedding_provider)
        survivor = await _merge(storage, embedding_provider, [a, b])
        later = Inference(content="So the capital moved.", source_id="seg-1")
        await storage.store_node(later)
        await storage.store_edge(NodeEdge(
            src_id=later.id, dst_id=survivor.id, type=EdgeType.DERIVED_FROM,
        ))

        refusal = await reverse_merge(survivor.id, storage)

        assert isinstance(refusal, ReverseRefused)
        assert "derived_from" in refusal.reason

    async def test_a_retired_survivor_is_refused(self, storage, embedding_provider):
        a, b = await _pair(storage, embedding_provider)
        survivor = await _merge(storage, embedding_provider, [a, b])
        await tools.update(
            node_id=survivor.id, new_content="Bonn was the capital city.",
            because="the_world_changed",
            storage=storage, embedding_provider=embedding_provider,
        )

        refusal = await reverse_merge(survivor.id, storage)

        assert isinstance(refusal, ReverseRefused)
        assert "historical" in refusal.reason

    async def test_an_id_that_names_nothing_raises(self, storage):
        """A malformed request rather than a judgment the graph declines —
        the same split `merge_facts` makes."""
        with pytest.raises(ValueError, match="not found"):
            await reverse_merge("no-such-node", storage)


class TestTheToolSurface:
    async def test_a_refusal_comes_back_rather_than_raising(
        self, storage, embedding_provider
    ):
        plain = await _fact(storage, embedding_provider, "Bonn is the capital.")

        result, _ = await tools.reverse_merge(survivor_id=plain.id, storage=storage)

        assert result["reversed"] is False
        assert "not made by a merge" in result["refused"]

    async def test_the_withdrawn_wording_is_returned_after_the_node_is_gone(
        self, storage, embedding_provider
    ):
        """A reversal that cannot quote what it withdrew is not much of a
        record, and the node holding the text has just been destroyed."""
        a, b = await _pair(storage, embedding_provider)
        survivor = await _merge(storage, embedding_provider, [a, b])

        result, meta = await tools.reverse_merge(
            survivor_id=survivor.id, storage=storage,
        )

        assert result["survivor_content"] == "Bonn is the capital city."
        assert await storage.get_node(survivor.id) is None
        assert meta.nodes_returned == 2


class TestTheSettingsAreReal:
    async def test_the_graphs_undo_depth_is_what_a_merge_applies(
        self, storage, embedding_provider
    ):
        """Resolved inside `merge_nodes` rather than at each call site, so a new
        merge path cannot ship with the bound quietly missing."""
        await storage.set_merge_overrides(MergeOverrides(undo_depth=1))
        a, b = await _pair(storage, embedding_provider)
        first = await _merge(storage, embedding_provider, [a, b])
        c = await _fact(storage, embedding_provider, "The capital city is Bonn.")
        await _merge(
            storage, embedding_provider, [first, c],
            content="Bonn is the capital city of West Germany.",
        )

        assert read_merge_undo(await storage.get_node(first.id)) is None

    async def test_configure_merge_reports_what_is_in_force(
        self, storage, embedding_provider
    ):
        result, _ = await tools.configure_merge(storage=storage, cycle_limit=4)

        assert result["merge_cycle_limit"] == 4
        assert result["merge_undo_depth"] == 10
        assert result["overridden"] == {"cycle_limit": 4}

    async def test_clearing_returns_to_the_defaults(self, storage):
        await tools.configure_merge(storage=storage, cycle_limit=4, undo_depth=3)

        result, _ = await tools.configure_merge(storage=storage, clear=True)

        assert result["overridden"] == {}
        assert result["merge_cycle_limit"] == 2
        assert result["merge_undo_depth"] == 10

    @pytest.mark.parametrize("kwargs", [
        {"undo_depth": 0}, {"cycle_limit": 0},
    ])
    async def test_a_setting_below_one_is_refused(self, storage, kwargs):
        """Zero undo depth captures and discards in one breath; zero cycle limit
        refuses the first ordinary correction."""
        with pytest.raises(ValueError, match="at least 1"):
            await tools.configure_merge(storage=storage, **kwargs)
