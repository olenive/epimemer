"""Folding a claim's retired versions into the claim that replaced them.

The condition under which returning `HISTORICAL` by default is not a ranking
regression. *"The city is called Leningrad"* and *"the city is called Saint
Petersburg"* are near-identical text, so both score near the top of the same
search, and a claim with four predecessors fills half a top-10 with versions of
one thing.

What the fold must **not** do is the other half of the test: two current claims
joined by a lineage edge are two answers, and a claim provably true at the moment
the caller asked about is *the* answer — hiding either under something later is
the same defect this exists to prevent, arriving from another direction.
"""

from datetime import datetime, timezone

import pytest

from epimemer.core.types import (
    EdgeType,
    Fact,
    NodeEdge,
    NodeStatus,
    ValueSignal,
)
from epimemer.pipelines.query.lineage import fold_lineage


def _fact(node_id: str, content: str, status: NodeStatus = NodeStatus.ACTIVE) -> Fact:
    return Fact(
        id=node_id,
        content=content,
        source_id="seg-1",
        status=status,
        value=ValueSignal(),
        superseded_at=(
            None if status is NodeStatus.ACTIVE else datetime.now(timezone.utc)
        ),
    )


async def _store(storage, nodes, edges=()):
    for node in nodes:
        await storage.store_node(node)
    for src, dst, edge_type in edges:
        await storage.store_edge(
            NodeEdge(src_id=src, dst_id=dst, type=edge_type)
        )


@pytest.fixture
def petersburg() -> list[Fact]:
    """The renaming chain, oldest first — each name correct in its turn."""
    return [
        _fact("leningrad", "the city is called Leningrad", NodeStatus.HISTORICAL),
        _fact("petersburg", "the city is called Saint Petersburg"),
    ]


class TestARetiredVersionGivesUpItsSlot:
    async def test_it_folds_into_the_successor_that_also_matched(
        self, storage, petersburg
    ):
        old, current = petersburg
        await _store(
            storage,
            petersburg,
            [(old.id, current.id, EdgeType.TEMPORALLY_FOLLOWED_BY)],
        )

        kept, lineage = await fold_lineage([old, current], storage)

        assert [node.id for node in kept] == [current.id]
        assert [node.id for node in lineage[current.id]] == [old.id]

    async def test_a_successor_nobody_matched_leaves_it_where_it_was(
        self, storage, petersburg
    ):
        """It matched on its own merits and nothing displaced it."""
        old, current = petersburg
        await _store(
            storage,
            petersburg,
            [(old.id, current.id, EdgeType.TEMPORALLY_FOLLOWED_BY)],
        )

        kept, lineage = await fold_lineage([old], storage)

        assert [node.id for node in kept] == [old.id]
        assert lineage == {}

    async def test_a_correction_folds_the_same_way(self, storage):
        """Both retirements crowd a result; only the edge type differs."""
        wrong = _fact("wrong", "the release shipped in March", NodeStatus.CORRECTED)
        right = _fact("right", "the release shipped in May")
        await _store(
            storage, [wrong, right], [(wrong.id, right.id, EdgeType.SUPERSEDED_BY)]
        )

        kept, lineage = await fold_lineage([wrong, right], storage)

        assert [node.id for node in kept] == [right.id]
        assert [node.id for node in lineage[right.id]] == [wrong.id]

    async def test_a_whole_chain_hangs_off_one_answer(self, storage):
        """T3's worked case: four predecessors, one slot, history attached."""
        chain = [
            _fact(f"name-{index}", f"the city is called {name}", NodeStatus.HISTORICAL)
            for index, name in enumerate(["Petersburg", "Petrograd", "Leningrad"])
        ]
        current = _fact("current", "the city is called Saint Petersburg")
        nodes = [*chain, current]
        await _store(
            storage,
            nodes,
            [
                (nodes[index].id, nodes[index + 1].id, EdgeType.TEMPORALLY_FOLLOWED_BY)
                for index in range(len(nodes) - 1)
            ],
        )

        kept, lineage = await fold_lineage(nodes, storage)

        assert [node.id for node in kept] == [current.id]
        assert [node.id for node in lineage[current.id]] == [n.id for n in chain]


class TestWhatMustNeverFold:
    async def test_two_active_claims_joined_by_a_lineage_edge_both_stay(
        self, storage
    ):
        """`restore` leaves exactly this shape, and `link` can write it by hand.

        The rule reads the status, not the edge: an ACTIVE node is a current
        claim whatever any edge says came after it, and folding it would hide a
        live answer under another one.
        """
        recurred = _fact("recurred", "the city is called Saint Petersburg")
        interim = _fact("interim", "the city is called Leningrad")
        await _store(
            storage,
            [recurred, interim],
            [(recurred.id, interim.id, EdgeType.TEMPORALLY_FOLLOWED_BY)],
        )

        kept, lineage = await fold_lineage([recurred, interim], storage)

        assert {node.id for node in kept} == {recurred.id, interim.id}
        assert lineage == {}

    async def test_a_claim_the_caller_asked_about_keeps_its_slot(
        self, storage, petersburg
    ):
        """What `valid_as_of` protects: the 1980 answer is not 1991's footnote."""
        old, current = petersburg
        await _store(
            storage,
            petersburg,
            [(old.id, current.id, EdgeType.TEMPORALLY_FOLLOWED_BY)],
        )

        kept, lineage = await fold_lineage(
            [old, current], storage, unfoldable=[old.id]
        )

        assert [node.id for node in kept] == [old.id, current.id]
        assert lineage == {}

    async def test_history_stops_at_a_protected_node_rather_than_passing_it(
        self, storage
    ):
        """Otherwise the history hangs off a node the caller did not ask about."""
        oldest = _fact("oldest", "called Petrograd", NodeStatus.HISTORICAL)
        middle = _fact("middle", "called Leningrad", NodeStatus.HISTORICAL)
        current = _fact("current", "called Saint Petersburg")
        nodes = [oldest, middle, current]
        await _store(
            storage,
            nodes,
            [
                (oldest.id, middle.id, EdgeType.TEMPORALLY_FOLLOWED_BY),
                (middle.id, current.id, EdgeType.TEMPORALLY_FOLLOWED_BY),
            ],
        )

        kept, lineage = await fold_lineage(
            nodes, storage, unfoldable=[middle.id]
        )

        assert [node.id for node in kept] == [middle.id, current.id]
        assert [node.id for node in lineage[middle.id]] == [oldest.id]


class TestTheWalkTerminates:
    async def test_a_cycle_in_the_chain_does_not_hang(self, storage):
        """`temporally_followed_by` permits cycles, and a recurrence closes one.

        Saint Petersburg → Petrograd → Leningrad → Saint Petersburg is legal and
        real data, so a walk that trusted the edges to be acyclic would hang on
        the case the edge type was designed for.
        """
        first = _fact("first", "called Saint Petersburg", NodeStatus.HISTORICAL)
        second = _fact("second", "called Leningrad", NodeStatus.HISTORICAL)
        await _store(
            storage,
            [first, second],
            [
                (first.id, second.id, EdgeType.TEMPORALLY_FOLLOWED_BY),
                (second.id, first.id, EdgeType.TEMPORALLY_FOLLOWED_BY),
            ],
        )

        kept, lineage = await fold_lineage([first, second], storage)

        # A cycle has no last version, so the better-ranked member hosts the
        # rest. Both folding into each other would terminate and still lose the
        # answer, which is the failure worth pinning.
        assert [node.id for node in kept] == [first.id]
        assert [node.id for node in lineage[first.id]] == [second.id]

    async def test_a_self_loop_is_not_a_fold(self, storage):
        alone = _fact("alone", "called Leningrad", NodeStatus.HISTORICAL)
        await _store(
            storage, [alone], [(alone.id, alone.id, EdgeType.TEMPORALLY_FOLLOWED_BY)]
        )

        kept, lineage = await fold_lineage([alone], storage)

        assert [node.id for node in kept] == [alone.id]
        assert lineage == {}

    async def test_an_empty_result_needs_no_queries(self, storage):
        assert await fold_lineage([], storage) == ([], {})

    async def test_an_all_current_result_costs_nothing(self, storage):
        """The common case: nothing is retired, so nothing can fold.

        Asserted as query count rather than as output because the output is the
        same either way — and two batched edge reads on every search that
        happens to return only current claims is the shape batching exists
        to keep out of the read paths.
        """
        current = [_fact("a", "one claim"), _fact("b", "another claim")]
        await _store(storage, current)

        asked = 0
        inner = storage.get_edges_for

        async def counting(*args, **kwargs):
            nonlocal asked
            asked += 1
            return await inner(*args, **kwargs)

        storage.get_edges_for = counting
        kept, lineage = await fold_lineage(current, storage)

        assert asked == 0
        assert [node.id for node in kept] == [node.id for node in current]
        assert lineage == {}
