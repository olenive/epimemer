"""`InMemoryStorage` answers edge lookups from an index, and it never drifts.

`get_edges_from` / `get_edges_to` used to filter the whole edge set on every
call, so every operation that walks nodes and asks for their edges cost O(N·E).
That is why `list_sources` measured *quadratic* in-memory while SurrealDB — which
answers the same question from an index — measured linear (`dev-docs/BENCHMARKS.md`).

An index that silently disagrees with the edges it indexes is worse than no
index: lookups would start returning wrong answers rather than slow ones. These
tests therefore check two different things.

- **Behaviour**, through the public API only: every lookup must return what a
  brute-force scan of the edge set would have returned. This is the contract and
  it holds however the lookup is implemented.
- **Structure**, reaching into `_g` deliberately: the index itself must contain
  exactly the entries the edges imply, after every write path — including the
  compound transactions that mutate edges in place or roll back. Checking only
  behaviour would let a stale entry survive until some later lookup tripped over
  it, and would not notice the index quietly falling out of use.

Backend-internal, so this constructs its own store rather than taking the
parameterized `storage` fixture.
"""

from datetime import datetime, timezone

import pytest

from epimemer.core.types import (
    NodeStatus,
    EdgeType,
    EmbeddingRecord,
    Fact,
    NodeEdge,
    Topic,
)
from epimemer.storage.memory import InMemoryStorage


@pytest.fixture
def store():
    return InMemoryStorage()


def _brute_from(store, node_id: str, edge_type: EdgeType | None = None):
    return sorted(
        e.id
        for e in store.edges.values()
        if e.src_id == node_id and (edge_type is None or e.type == edge_type)
    )


def _brute_to(store, node_id: str, edge_type: EdgeType | None = None):
    return sorted(
        e.id
        for e in store.edges.values()
        if e.dst_id == node_id and (edge_type is None or e.type == edge_type)
    )


async def _found_from(store, node_id: str, edge_type: EdgeType | None = None):
    return sorted(
        e.id for e in await store.get_edges_from(node_id, edge_type=edge_type)
    )


async def _found_to(store, node_id: str, edge_type: EdgeType | None = None):
    return sorted(e.id for e in await store.get_edges_to(node_id, edge_type=edge_type))


def _assert_index_matches_edges(store, graph: str | None = None) -> None:
    """The index holds exactly what the edge set implies — no more, no less.

    Rebuilt from scratch and compared, so both a missing entry (a lookup that
    would return too little) and an orphan entry (one naming a deleted or
    re-pointed edge) fail here rather than at some unrelated call site.
    """
    g = store._graphs[graph] if graph is not None else store._g

    expected_src: dict[str, set[str]] = {}
    expected_dst: dict[str, set[str]] = {}
    for edge in g.edges.values():
        expected_src.setdefault(edge.src_id, set()).add(edge.id)
        expected_dst.setdefault(edge.dst_id, set()).add(edge.id)

    assert g.by_src == expected_src
    assert g.by_dst == expected_dst


async def _assert_lookups_match_brute_force(store) -> None:
    """Every endpoint in the graph, both directions, filtered and unfiltered."""
    endpoints = {e.src_id for e in store.edges.values()} | {
        e.dst_id for e in store.edges.values()
    }
    types = {e.type for e in store.edges.values()}
    for node_id in endpoints:
        assert await _found_from(store, node_id) == _brute_from(store, node_id)
        assert await _found_to(store, node_id) == _brute_to(store, node_id)
        for edge_type in types:
            assert await _found_from(store, node_id, edge_type) == _brute_from(
                store, node_id, edge_type
            )
            assert await _found_to(store, node_id, edge_type) == _brute_to(
                store, node_id, edge_type
            )


async def _a_small_graph(store):
    """Two topics, three facts, and edges in both directions between them."""
    topics = [Topic(content=f"topic {i}", source_id="s1") for i in range(2)]
    facts = [Fact(content=f"fact {i}", source_id="s1") for i in range(3)]
    for node in topics + facts:
        await store.store_node(node)
    edges = [
        NodeEdge(src_id=facts[0].id, dst_id=topics[0].id, type=EdgeType.SUPPORTS),
        NodeEdge(src_id=facts[1].id, dst_id=topics[0].id, type=EdgeType.SUPPORTS),
        NodeEdge(src_id=facts[2].id, dst_id=topics[1].id, type=EdgeType.SUPPORTS),
        NodeEdge(src_id=topics[0].id, dst_id=topics[1].id, type=EdgeType.RELATED),
        NodeEdge(src_id=facts[0].id, dst_id=facts[1].id, type=EdgeType.CONTRADICTION),
    ]
    for edge in edges:
        await store.store_edge(edge)
    return topics, facts, edges


class TestLookupsAgreeWithABruteForceScan:
    """The contract, stated without reference to how it is implemented."""

    async def test_edges_are_found_after_being_stored(self, store):
        await _a_small_graph(store)
        await _assert_lookups_match_brute_force(store)

    async def test_edges_are_not_found_after_being_deleted(self, store):
        _, _, edges = await _a_small_graph(store)
        gone = edges[0]

        await store.delete_edge(gone.id)

        assert gone.id not in await _found_from(store, gone.src_id)
        assert gone.id not in await _found_to(store, gone.dst_id)
        await _assert_lookups_match_brute_force(store)
        _assert_index_matches_edges(store)

    async def test_the_type_filter_still_applies(self, store):
        topics, facts, _ = await _a_small_graph(store)

        supports = await _found_to(store, topics[0].id, EdgeType.SUPPORTS)

        assert supports == _brute_to(store, topics[0].id, EdgeType.SUPPORTS)
        assert len(supports) == 2
        # Same node, different type: the index gives the candidates, the filter
        # still has to narrow them.
        assert await _found_to(store, topics[0].id, EdgeType.RELATED) == []
        assert await _found_from(store, topics[0].id, EdgeType.RELATED) == [
            e.id for e in await store.get_edges_from(topics[0].id)
        ]
        assert await _found_from(store, facts[0].id, EdgeType.CONTRADICTION) == [
            e.id
            for e in store.edges.values()
            if e.src_id == facts[0].id and e.type == EdgeType.CONTRADICTION
        ]

    async def test_a_node_with_no_edges_returns_empty(self, store):
        await _a_small_graph(store)
        orphan = Topic(content="orphan", source_id="s1")
        await store.store_node(orphan)

        assert await store.get_edges_from(orphan.id) == []
        assert await store.get_edges_to(orphan.id) == []

    async def test_an_unknown_node_returns_empty(self, store):
        await _a_small_graph(store)

        assert await store.get_edges_from("no-such-node") == []
        assert await store.get_edges_to("no-such-node") == []

    async def test_lookups_still_return_detached_copies(self, store):
        """The index must not become a way to hand out the stored object."""
        _, _, edges = await _a_small_graph(store)
        edge = edges[0]

        returned = (await store.get_edges_from(edge.src_id))[0]
        returned.label = "mutated"

        assert (await store.get_edges_from(edge.src_id))[0].label != "mutated"


class _ScanForbidden(dict):
    """An edge dict that refuses to be enumerated.

    Random access by id still works, so an indexed lookup is unaffected; any
    implementation that walks the whole edge set fails loudly.
    """

    def _refuse(self, *args, **kwargs):
        raise AssertionError("edge lookups must not enumerate the whole edge set")

    values = _refuse
    items = _refuse
    keys = _refuse
    __iter__ = _refuse


class TestLookupsDoNotScan:
    """The point of the whole exercise, and the only part behaviour cannot show.

    Every other test here passes just as well against a full scan — the index
    could be maintained perfectly and never consulted, and `list_sources` would
    stay quadratic. This is what pins the lookup to the index.
    """

    async def test_get_edges_from_answers_without_enumerating(self, store):
        _, facts, _ = await _a_small_graph(store)
        expected = await _found_from(store, facts[0].id)
        store._g.edges = _ScanForbidden(store._g.edges)

        found = await store.get_edges_from(facts[0].id)

        assert sorted(e.id for e in found) == expected

    async def test_get_edges_to_answers_without_enumerating(self, store):
        topics, _, _ = await _a_small_graph(store)
        expected = await _found_to(store, topics[0].id, EdgeType.SUPPORTS)
        store._g.edges = _ScanForbidden(store._g.edges)

        found = await store.get_edges_to(topics[0].id, edge_type=EdgeType.SUPPORTS)

        assert sorted(e.id for e in found) == expected

    async def test_a_node_with_no_edges_answers_without_enumerating(self, store):
        await _a_small_graph(store)
        store._g.edges = _ScanForbidden(store._g.edges)

        assert await store.get_edges_from("no-such-node") == []
        assert await store.get_edges_to("no-such-node") == []


class TestTheIndexTracksEverySimpleWrite:

    async def test_store_edge_indexes_both_endpoints(self, store):
        await _a_small_graph(store)
        _assert_index_matches_edges(store)

    async def test_delete_edge_leaves_no_orphan_entry(self, store):
        _, _, edges = await _a_small_graph(store)
        for edge in edges:
            await store.delete_edge(edge.id)
            _assert_index_matches_edges(store)

        # Every entry gone, not merely emptied — an index of empty sets would
        # grow without bound on a long-lived graph.
        assert store._g.by_src == {}
        assert store._g.by_dst == {}

    async def test_deleting_a_missing_edge_changes_nothing(self, store):
        await _a_small_graph(store)
        before_src = {k: set(v) for k, v in store._g.by_src.items()}

        await store.delete_edge("no-such-edge")

        assert store._g.by_src == before_src
        _assert_index_matches_edges(store)

    async def test_re_storing_an_edge_under_new_endpoints_moves_its_entry(self, store):
        """`store_edge` upserts by id, so the old endpoints must be un-indexed."""
        topics, facts, _ = await _a_small_graph(store)
        edge = NodeEdge(src_id=facts[0].id, dst_id=topics[0].id, type=EdgeType.RELATED)
        await store.store_edge(edge)

        moved = edge.model_copy(update={"src_id": facts[2].id, "dst_id": topics[1].id})
        await store.store_edge(moved)

        assert edge.id not in await _found_from(store, facts[0].id)
        assert edge.id in await _found_from(store, facts[2].id)
        _assert_index_matches_edges(store)
        await _assert_lookups_match_brute_force(store)

class TestTheIndexTracksTheCompoundTransactions:
    """The write paths that mutate edges directly rather than via `store_edge`."""

    async def test_write_batch_tx(self, store):
        topic = Topic(content="t", source_id="s1")
        fact = Fact(content="f", source_id="s1")
        edge = NodeEdge(src_id=fact.id, dst_id=topic.id, type=EdgeType.SUPPORTS)

        await store.write_batch_tx(nodes=[topic, fact], edges=[edge])

        assert await _found_to(store, topic.id) == [edge.id]
        _assert_index_matches_edges(store)

    async def test_a_rolled_back_write_batch_leaves_no_orphan_entries(self, store):
        _, _, existing = await _a_small_graph(store)
        topic = Topic(content="t", source_id="s1")
        doomed = NodeEdge(src_id="x", dst_id=topic.id, type=EdgeType.SUPPORTS)

        def boom_embeddings():
            yield EmbeddingRecord(item_id=topic.id, model_id="m", vector=[1.0])
            raise RuntimeError("injected failure")

        with pytest.raises(RuntimeError, match="injected failure"):
            await store.write_batch_tx(
                nodes=[topic], edges=[doomed], embeddings=boom_embeddings()
            )

        # The undone edge must be absent from the index, not merely from `edges`.
        assert await store.get_edges_to(topic.id) == []
        assert doomed.id not in store._g.by_src.get("x", set())
        _assert_index_matches_edges(store)
        assert sorted(store.edges) == sorted(e.id for e in existing)

    async def test_supersede_node_tx_follows_the_migrated_edges(self, store):
        """Migration re-points `src_id`/`dst_id` on stored edges in place — the
        index entry has to move with them."""
        old = Topic(content="old", source_id="s1")
        fact = Fact(content="supporting", source_id="s1")
        await store.store_node(old)
        await store.store_node(fact)
        supporting = NodeEdge(src_id=fact.id, dst_id=old.id, type=EdgeType.SUPPORTS)
        await store.store_edge(supporting)

        new = Topic(content="new", source_id="s1")
        lineage = NodeEdge(src_id=old.id, dst_id=new.id, type=EdgeType.SUPERSEDED_BY)
        await store.supersede_node_tx(
            old,
            new,
            EmbeddingRecord(item_id=new.id, model_id="m", vector=[0.0, 1.0]),
            lineage,
            superseded_at=datetime.now(timezone.utc),
        status=NodeStatus.CORRECTED,
    )

        assert await _found_to(store, new.id, EdgeType.SUPPORTS) == [supporting.id]
        assert await _found_to(store, old.id, EdgeType.SUPPORTS) == []
        assert await _found_from(store, old.id, EdgeType.SUPERSEDED_BY) == [lineage.id]
        _assert_index_matches_edges(store)
        await _assert_lookups_match_brute_force(store)

    async def test_supersede_node_tx_clears_indexed_edges(self, store):
        old = Topic(content="old", source_id="s1")
        candidate = Fact(content="newer claim", source_id="s1")
        await store.store_node(old)
        await store.store_node(candidate)
        cleared = NodeEdge(
            src_id=candidate.id,
            dst_id=old.id,
            type=EdgeType.SUPERSESSION_CANDIDATE,
        )
        await store.store_edge(cleared)

        new = Topic(content="new", source_id="s1")
        await store.supersede_node_tx(
            old,
            new,
            EmbeddingRecord(item_id=new.id, model_id="m", vector=[0.0, 1.0]),
            NodeEdge(src_id=old.id, dst_id=new.id, type=EdgeType.SUPERSEDED_BY),
            superseded_at=datetime.now(timezone.utc),
            clear_edge_ids=[cleared.id],
        status=NodeStatus.CORRECTED,
    )

        assert await store.get_edges_from(candidate.id) == []
        _assert_index_matches_edges(store)

    async def test_a_rolled_back_supersede_restores_the_index(self, store, monkeypatch):
        """The rollback swaps the whole graph back — the index must come with it."""
        _, _, edges = await _a_small_graph(store)
        old = Topic(content="old", source_id="s1")
        await store.store_node(old)
        before_src = {k: set(v) for k, v in store._g.by_src.items()}
        before_dst = {k: set(v) for k, v in store._g.by_dst.items()}

        def boom(*args, **kwargs):
            raise RuntimeError("injected failure")

        monkeypatch.setattr(store, "_migrate_edges_inplace", boom)

        new = Topic(content="new", source_id="s1")
        with pytest.raises(RuntimeError, match="injected failure"):
            await store.supersede_node_tx(
                old,
                new,
                EmbeddingRecord(item_id=new.id, model_id="m", vector=[0.0, 1.0]),
                NodeEdge(src_id=old.id, dst_id=new.id, type=EdgeType.SUPERSEDED_BY),
                superseded_at=datetime.now(timezone.utc),
            status=NodeStatus.CORRECTED,
        )

        assert store._g.by_src == before_src
        assert store._g.by_dst == before_dst
        _assert_index_matches_edges(store)
        assert sorted(store.edges) == sorted(e.id for e in edges)

    async def test_supersede_by_existing_tx(self, store):
        old = Fact(content="old", source_id="s1")
        keeper = Fact(content="keeper", source_id="s1")
        await store.store_node(old)
        await store.store_node(keeper)

        lineage = NodeEdge(src_id=old.id, dst_id=keeper.id, type=EdgeType.SUPERSEDED_BY)
        await store.supersede_by_existing_tx(
            old, keeper.id, lineage,
            status=NodeStatus.CORRECTED,
            superseded_at=datetime.now(timezone.utc),
        )

        assert await _found_to(store, keeper.id) == [lineage.id]
        _assert_index_matches_edges(store)

    async def test_merge_nodes_tx_follows_migration_dedup_and_self_loops(self, store):
        """Merging drops self-loops and collapses duplicates — both are deletions
        the index has to see."""
        a = Topic(content="a", source_id="s1")
        b = Topic(content="b", source_id="s1")
        fact = Fact(content="shared support", source_id="s1")
        for node in (a, b, fact):
            await store.store_node(node)
        # Two edges that collapse into one after the merge...
        await store.store_edge(
            NodeEdge(src_id=fact.id, dst_id=a.id, type=EdgeType.SUPPORTS)
        )
        await store.store_edge(
            NodeEdge(src_id=fact.id, dst_id=b.id, type=EdgeType.SUPPORTS)
        )
        # ...and one that becomes a self-loop and is dropped.
        await store.store_edge(NodeEdge(src_id=a.id, dst_id=b.id, type=EdgeType.RELATED))

        merged = Topic(content="merged", source_id="s1")
        await store.merge_nodes_tx(
            [a, b],
            merged,
            EmbeddingRecord(item_id=merged.id, model_id="m", vector=[1.0, 0.0]),
            [
                NodeEdge(src_id=a.id, dst_id=merged.id, type=EdgeType.MERGED_INTO),
                NodeEdge(src_id=b.id, dst_id=merged.id, type=EdgeType.MERGED_INTO),
            ],
            merged_at=datetime.now(timezone.utc),
        )

        assert len(await _found_to(store, merged.id, EdgeType.SUPPORTS)) == 1
        assert await store.get_edges_from(a.id, edge_type=EdgeType.RELATED) == []
        _assert_index_matches_edges(store)
        await _assert_lookups_match_brute_force(store)


class TestIndexesAreConfinedToTheirGraph:

    async def test_each_graph_indexes_only_its_own_edges(self, store):
        _, _, first = await _a_small_graph(store)

        await store.switch_database("second")
        _, _, second = await _a_small_graph(store)

        _assert_index_matches_edges(store, graph="default")
        _assert_index_matches_edges(store, graph="second")
        # No edge id from the first graph appears anywhere in the second's index.
        second_ids = {i for ids in store._g.by_src.values() for i in ids}
        assert not second_ids & {e.id for e in first}

    async def test_switching_back_sees_the_original_index(self, store):
        _, _, first = await _a_small_graph(store)
        await store.switch_database("second")
        await _a_small_graph(store)

        await store.switch_database("default")

        await _assert_lookups_match_brute_force(store)
        assert first[0].id in await _found_from(store, first[0].src_id)
