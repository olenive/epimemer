"""The temporal soundness check over stored inferences (the soundness check).

The strongest form of the problem the validity model exists for: not a display defect but a
soundness defect in the layer the system provides. The graph stores inferences
the agent drew, and until validity existed nothing could notice that an
inference combined claims no source ever put in the same period.

Half of these tests are about **silence**. `unknown` is the common outcome, so a
check that fired whenever it could not place two premises would flag most of the
graph and be switched off within a week — and the flag's whole value is that it
is rare.
"""

from datetime import UTC, datetime

import pytest

from epimemer.core.temporal import IntervalBasis, ValidityInterval
from epimemer.core.types import (
    EdgeType,
    Fact,
    Inference,
    NodeEdge,
    NodeStatus,
    RawDocument,
    ValueSignal,
)
from epimemer.pipelines.reflection.soundness import find_unsound_inferences


def _period(start: int | None = None, end: int | None = None) -> ValidityInterval:
    def instant(year: int | None) -> dict:
        if year is None:
            return {"instant_kind": "unknown"}
        return {
            "instant_kind": "precise",
            "at": datetime(year, 1, 1, tzinfo=UTC).isoformat(),
        }

    return ValidityInterval(start=instant(start), end=instant(end), basis=IntervalBasis.STATED)


@pytest.fixture
async def document(storage) -> RawDocument:
    doc = RawDocument(content="A history", source="test")
    await storage.store_document(doc)
    return doc


async def _premise(storage, document, content: str, *periods: ValidityInterval) -> Fact:
    """A fact, and the source edge carrying whatever periods it was given."""
    fact = Fact(content=content, source_id="seg-1", value=ValueSignal())
    await storage.store_node(fact)
    await storage.store_edge(
        NodeEdge(
            src_id=fact.id,
            dst_id=document.id,
            type=EdgeType.SOURCED_FROM,
            validity=list(periods),
        )
    )
    return fact


async def _inference(
    storage, content: str, premises, *, edge_type=EdgeType.DERIVED_FROM
) -> Inference:
    inference = Inference(content=content, source_id="seg-1", value=ValueSignal())
    await storage.store_node(inference)
    for premise in premises:
        edge = (
            NodeEdge(src_id=inference.id, dst_id=premise.id, type=edge_type)
            if edge_type is EdgeType.DERIVED_FROM
            else NodeEdge(src_id=premise.id, dst_id=inference.id, type=edge_type)
        )
        await storage.store_edge(edge)
    return inference


class TestWhatItFlags:
    async def test_premises_no_source_puts_in_the_same_period(self, storage, document):
        """The motivating case, and the reason the validity model outranks everything else."""
        early = await _premise(storage, document, "Labour was in government", _period(1997, 2010))
        late = await _premise(storage, document, "the policy was in force", _period(2024, 2030))
        drawn = await _inference(storage, "Labour introduced the policy", [early, late])

        [flagged] = await find_unsound_inferences(storage)

        assert flagged.inference.id == drawn.id
        [pair] = flagged.disjoint_premises
        assert {pair.a.id, pair.b.id} == {early.id, late.id}

    async def test_the_dates_come_with_the_flag(self, storage, document):
        """A verdict whose evidence is hidden cannot be argued with."""
        early = await _premise(storage, document, "one claim", _period(1997, 2010))
        late = await _premise(storage, document, "another", _period(2024, 2030))
        await _inference(storage, "a conclusion", [early, late])

        [flagged] = await find_unsound_inferences(storage)
        [pair] = flagged.disjoint_premises
        by_id = {pair.a.id: pair.a, pair.b.id: pair.b}

        assert by_id[early.id].periods[0].end.at.year == 2010
        assert by_id[late.id].periods[0].start.at.year == 2024
        assert by_id[early.id].content == "one claim"

    async def test_a_premise_reached_by_supports_counts_too(self, storage, document):
        """Both edges record the same relation, as evidence-staleness already reads them."""
        early = await _premise(storage, document, "one claim", _period(1997, 2010))
        late = await _premise(storage, document, "another", _period(2024, 2030))
        await _inference(storage, "a conclusion", [early, late], edge_type=EdgeType.SUPPORTS)

        [flagged] = await find_unsound_inferences(storage)

        assert len(flagged.disjoint_premises) == 1

    async def test_one_finding_per_pair_not_two(self, storage, document):
        """`a` disjoint from `b` is the same finding as `b` disjoint from `a`."""
        early = await _premise(storage, document, "one claim", _period(1997, 2010))
        late = await _premise(storage, document, "another", _period(2024, 2030))
        await _inference(storage, "a conclusion", [early, late])

        [flagged] = await find_unsound_inferences(storage)

        assert len(flagged.disjoint_premises) == 1

    async def test_only_the_offending_pair_is_named(self, storage, document):
        """A third premise that overlaps both is not part of the finding."""
        early = await _premise(storage, document, "one claim", _period(1997, 2010))
        late = await _premise(storage, document, "another", _period(2024, 2030))
        spanning = await _premise(storage, document, "throughout", _period(1990, 2040))
        await _inference(storage, "a conclusion", [early, late, spanning])

        [flagged] = await find_unsound_inferences(storage)
        [pair] = flagged.disjoint_premises

        assert {pair.a.id, pair.b.id} == {early.id, late.id}


class TestWhatItStaysSilentAbout:
    async def test_undated_premises(self, storage, document):
        """Most of the graph, and always will be. Silence is the design."""
        one = await _premise(storage, document, "one claim")
        other = await _premise(storage, document, "another")
        await _inference(storage, "a conclusion", [one, other])

        assert await find_unsound_inferences(storage) == []

    async def test_one_premise_dated_and_one_not(self, storage, document):
        dated = await _premise(storage, document, "one claim", _period(1997, 2010))
        undated = await _premise(storage, document, "another")
        await _inference(storage, "a conclusion", [dated, undated])

        assert await find_unsound_inferences(storage) == []

    async def test_periods_that_cannot_be_placed(self, storage, document):
        """A period with unknown endpoints might well overlap; firing would be
        a check on ignorance, which §11 rules out."""
        dated = await _premise(storage, document, "one claim", _period(1997, 2010))
        vague = await _premise(storage, document, "another", _period())
        await _inference(storage, "a conclusion", [dated, vague])

        assert await find_unsound_inferences(storage) == []

    async def test_premises_that_overlap(self, storage, document):
        early = await _premise(storage, document, "one claim", _period(1997, 2010))
        overlapping = await _premise(storage, document, "another", _period(2005, 2030))
        await _inference(storage, "a conclusion", [early, overlapping])

        assert await find_unsound_inferences(storage) == []

    async def test_a_second_source_asserting_a_later_episode(self, storage, document):
        """The union per premise, and the safe error direction.

        A second source says the first claim held again in the 2020s. That is
        enough for the two to have been asserted together, and an intersection
        rule would flag a sound inference instead.
        """
        recurring = await _premise(
            storage, document, "one claim", _period(1997, 2010), _period(2024, 2030)
        )
        late = await _premise(storage, document, "another", _period(2025, 2026))
        await _inference(storage, "a conclusion", [recurring, late])

        assert await find_unsound_inferences(storage) == []

    async def test_premises_on_different_clocks(self, storage, document):
        """An inference across an in-universe claim and a real one is
        temporally uncheckable, which is not the same as unsound."""
        real = await _premise(storage, document, "one claim", _period(1997, 2010))
        in_universe = Fact(content="another", source_id="seg-1", value=ValueSignal())
        await storage.store_node(in_universe)
        await storage.store_edge(
            NodeEdge(
                src_id=in_universe.id,
                dst_id=document.id,
                type=EdgeType.SOURCED_FROM,
                validity=[
                    ValidityInterval(
                        start={
                            "instant_kind": "precise",
                            "at": datetime(2024, 1, 1, tzinfo=UTC).isoformat(),
                        },
                        timeline_id="third-age",
                        basis=IntervalBasis.STATED,
                    )
                ],
            )
        )
        await _inference(storage, "a conclusion", [real, in_universe])

        assert await find_unsound_inferences(storage) == []

    async def test_an_inference_that_is_no_longer_active(self, storage, document):
        """Reflect works the active graph; a retired inference is already judged."""
        early = await _premise(storage, document, "one claim", _period(1997, 2010))
        late = await _premise(storage, document, "another", _period(2024, 2030))
        retired = await _inference(storage, "a conclusion", [early, late])
        await storage.set_node_status_tx(
            [retired], status=NodeStatus.CORRECTED, at=datetime.now(UTC)
        )

        assert await find_unsound_inferences(storage) == []

    async def test_a_graph_with_no_inferences(self, storage, document):
        await _premise(storage, document, "one claim", _period(1997, 2010))

        assert await find_unsound_inferences(storage) == []


class TestItOnlyReads:
    async def test_nothing_is_written(self, storage, document):
        """Flags, never blocks — and never acts. `reflect` proposes."""
        early = await _premise(storage, document, "one claim", _period(1997, 2010))
        late = await _premise(storage, document, "another", _period(2024, 2030))
        drawn = await _inference(storage, "a conclusion", [early, late])
        before = [(node.id, node.status) for node in await storage.query_nodes()]

        assert await find_unsound_inferences(storage) != []

        after = [(node.id, node.status) for node in await storage.query_nodes()]
        assert sorted(after) == sorted(before)
        assert (await storage.get_node(drawn.id)).status is NodeStatus.ACTIVE
