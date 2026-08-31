"""Reflect proposing where one claim's period ends and the next begins (boundary proposals).

The other half of *"ingest extracts, reflect proposes"*. A document asserting
*"the city is called Leningrad"* cannot know the claim will ever stop being
true, so the period is stored open — and only something reading the next
document can close it.

Most of what follows is about **what it refuses to propose**. A boundary is an
assertion written into a source's record, so the bar is a date some document
actually gives plus a succession the agent has already judged. Guessing either
is how the graph starts inventing history.
"""

from datetime import UTC, datetime

import pytest

from epimemer.core.temporal import (
    IntervalBasis,
    NamedInstant,
    PreciseInstant,
    UnboundedInstant,
    UnknownInstant,
    ValidityInterval,
)
from epimemer.core.types import (
    EdgeType,
    Fact,
    NodeEdge,
    NodeStatus,
    RawDocument,
    Topic,
    ValueSignal,
)
from epimemer.pipelines.reflection.boundaries import (
    apply_boundary,
    propose_boundaries,
)


def _at(year: int) -> PreciseInstant:
    return PreciseInstant(at=datetime(year, 1, 1, tzinfo=UTC))


def _period(start=None, end=None, **kwargs) -> ValidityInterval:
    return ValidityInterval(
        start=start if start is not None else UnknownInstant(),
        end=end if end is not None else UnknownInstant(),
        basis=kwargs.pop("basis", IntervalBasis.STATED),
        **kwargs,
    )


@pytest.fixture
async def documents(storage) -> tuple[RawDocument, RawDocument]:
    older = RawDocument(content="A 1970 gazetteer", source="doc-1970")
    newer = RawDocument(content="A 2000 gazetteer", source="doc-2000")
    await storage.store_document(older)
    await storage.store_document(newer)
    return older, newer


async def _claim(storage, document, content, *periods, node=None) -> Fact:
    fact = node or Fact(content=content, source_id="seg-1", value=ValueSignal())
    if node is None:
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


async def _succeeds(storage, earlier, later, *, retire=True) -> None:
    """The agent's recorded verdict that the world moved from one to the other."""
    if retire:
        await storage.set_node_status_tx(
            [earlier], status=NodeStatus.HISTORICAL, at=datetime.now(UTC)
        )
    await storage.store_edge(
        NodeEdge(
            src_id=earlier.id,
            dst_id=later.id,
            type=EdgeType.TEMPORALLY_FOLLOWED_BY,
        )
    )


@pytest.fixture
async def renaming(storage, documents):
    """The worked case: an open period, and a successor that names its date."""
    older, newer = documents
    leningrad = await _claim(
        storage, older, "the city is called Leningrad", _period(start=_at(1924))
    )
    petersburg = await _claim(
        storage,
        newer,
        "the city is called Saint Petersburg",
        _period(start=_at(1991)),
    )
    await _succeeds(storage, leningrad, petersburg)
    return leningrad, petersburg, older, newer


class TestWhatItProposes:
    async def test_the_successors_start_closes_the_earlier_period(self, storage, renaming):
        leningrad, petersburg, older, newer = renaming

        [proposal] = await propose_boundaries(storage)

        assert proposal.node.id == leningrad.id
        assert proposal.endpoint == "end"
        assert proposal.at.year == 1991
        assert proposal.source_id == older.id
        # The evidence is a node in the graph, not a sentence in a report.
        assert proposal.because.id == petersburg.id
        assert proposal.because_source_id == newer.id

    async def test_it_shows_what_would_change(self, storage, renaming):
        """Including the part that is easy to miss: the basis stops being stated."""
        [proposal] = await propose_boundaries(storage)

        assert isinstance(proposal.current.end, UnknownInstant)
        assert proposal.current.basis is IntervalBasis.STATED
        assert proposal.proposed.end.at.year == 1991
        assert proposal.proposed.basis is IntervalBasis.INFERRED
        # The other endpoint is untouched: only the open one is filled in.
        assert proposal.proposed.start.at.year == 1924

    async def test_it_reads_the_relation_from_the_other_end_too(self, storage, documents):
        """A dated predecessor opens an undated successor. Same relation."""
        older, newer = documents
        leningrad = await _claim(
            storage,
            older,
            "the city is called Leningrad",
            _period(start=_at(1924), end=_at(1991)),
        )
        petersburg = await _claim(storage, newer, "the city is called Saint Petersburg", _period())
        await _succeeds(storage, leningrad, petersburg)

        [proposal] = await propose_boundaries(storage)

        assert proposal.node.id == petersburg.id
        assert proposal.endpoint == "start"
        assert proposal.at.year == 1991

    async def test_the_boundary_nearest_the_handover_is_the_one_proposed(self, storage, documents):
        """A successor with two episodes closes the predecessor at the first."""
        older, newer = documents
        earlier = await _claim(storage, older, "one claim", _period(start=_at(1900)))
        later = await _claim(
            storage,
            newer,
            "another claim",
            _period(start=_at(1991), end=_at(1996)),
            _period(start=_at(2000), end=_at(2010)),
        )
        await _succeeds(storage, earlier, later)

        [proposal] = await propose_boundaries(storage)

        assert proposal.at.year == 1991

    async def test_nothing_is_written(self, storage, renaming):
        """Proposes, never acts — `reflect` writes nothing."""
        leningrad, _, older, _ = renaming

        await propose_boundaries(storage)

        [edge] = [
            edge
            for edge in await storage.get_edges_from(leningrad.id, edge_type=EdgeType.SOURCED_FROM)
            if edge.dst_id == older.id
        ]
        assert isinstance(edge.validity[0].end, UnknownInstant)


class TestWhatItRefusesToPropose:
    async def test_a_correction_licenses_nothing(self, storage, documents):
        """A claim concluded wrong was never true, so it has no period to close."""
        older, newer = documents
        wrong = await _claim(
            storage, older, "the release shipped in March", _period(start=_at(2020))
        )
        right = await _claim(storage, newer, "the release shipped in May", _period(start=_at(2021)))
        await storage.set_node_status_tx([wrong], status=NodeStatus.CORRECTED, at=datetime.now(UTC))
        await storage.store_edge(
            NodeEdge(src_id=wrong.id, dst_id=right.id, type=EdgeType.SUPERSEDED_BY)
        )

        assert await propose_boundaries(storage) == []

    async def test_two_similar_claims_with_no_succession_between_them(self, storage, documents):
        """Guessing that two facts are successive is the agent's judgment (§3).

        Without the edge there is no verdict to draw a consequence from, and
        reflect would be inventing the succession as well as the date.
        """
        older, newer = documents
        await _claim(storage, older, "the city is called Leningrad", _period(start=_at(1924)))
        await _claim(
            storage,
            newer,
            "the city is called Saint Petersburg",
            _period(start=_at(1991)),
        )

        assert await propose_boundaries(storage) == []

    async def test_a_succession_where_neither_document_gives_a_date(self, storage, documents):
        """§9's own worked example, and the honest outcome is nothing.

        Publication dates bound when a claim was *asserted*, never when the
        previous one stopped holding — closing Leningrad at the 2000 gazetteer's
        date would have the graph assert the city was called Leningrad in 1995.
        """
        older, newer = documents
        leningrad = await _claim(storage, older, "the city is called Leningrad", _period())
        petersburg = await _claim(storage, newer, "the city is called Saint Petersburg", _period())
        await _succeeds(storage, leningrad, petersburg)

        assert await propose_boundaries(storage) == []

    async def test_an_endpoint_a_source_gave_words_for(self, storage, documents):
        """Resolving a label into a date is an explicit act (§4), not a sweep."""
        older, newer = documents
        earlier = await _claim(
            storage,
            older,
            "one claim",
            _period(start=_at(1900), end=NamedInstant(label="the war years")),
        )
        later = await _claim(storage, newer, "another claim", _period(start=_at(1991)))
        await _succeeds(storage, earlier, later)

        assert await propose_boundaries(storage) == []

    async def test_an_endpoint_a_source_said_does_not_exist(self, storage, documents):
        """`unbounded` is a source saying there is no boundary to close."""
        older, newer = documents
        earlier = await _claim(
            storage,
            older,
            "one claim",
            _period(start=_at(1900), end=UnboundedInstant()),
        )
        later = await _claim(storage, newer, "another claim", _period(start=_at(1991)))
        await _succeeds(storage, earlier, later)

        assert await propose_boundaries(storage) == []

    async def test_a_boundary_the_evidence_contradicts(self, storage, documents):
        """The predecessor was witnessed after the successor began.

        Whatever else is true, its period did not end in 1991 — so proposing
        that would ask the reviewer to approve something storage would refuse.
        """
        older, newer = documents
        earlier = await _claim(
            storage,
            older,
            "one claim",
            _period(start=_at(1900), witnessed_at=_at(1995)),
        )
        later = await _claim(storage, newer, "another claim", _period(start=_at(1991)))
        await _succeeds(storage, earlier, later)

        assert await propose_boundaries(storage) == []

    async def test_periods_measured_on_different_clocks(self, storage, documents):
        """No conversion exists between an in-universe date and a real one."""
        older, newer = documents
        earlier = await _claim(storage, older, "one claim", _period(start=_at(1900)))
        later = await _claim(
            storage,
            newer,
            "another claim",
            _period(start=_at(1991), timeline_id="third-age"),
        )
        await _succeeds(storage, earlier, later)

        assert await propose_boundaries(storage) == []

    async def test_a_period_that_is_already_closed(self, storage, documents):
        older, newer = documents
        earlier = await _claim(storage, older, "one claim", _period(start=_at(1900), end=_at(1950)))
        later = await _claim(storage, newer, "another claim", _period(start=_at(1991)))
        await _succeeds(storage, earlier, later)

        # The successor's start is already located too, so neither side is open.
        assert await propose_boundaries(storage) == []

    async def test_topics_have_no_periods_to_propose_over(self, storage, documents):
        """A topic is a subject, not an assertion — nothing there to be true (§1)."""
        older, newer = documents
        earlier = Topic(content="the city", source_id="seg-1", value=ValueSignal())
        later = Topic(content="the renamed city", source_id="seg-1", value=ValueSignal())
        for topic in (earlier, later):
            await storage.store_node(topic)
        await _claim(storage, older, "", _period(start=_at(1924)), node=earlier)
        await _claim(storage, newer, "", _period(start=_at(1991)), node=later)
        await _succeeds(storage, earlier, later)

        assert await propose_boundaries(storage) == []


class TestAcceptingOne:
    async def _boundary(self, storage, proposal):
        return await apply_boundary(
            storage,
            node_id=proposal.node.id,
            source_id=proposal.source_id,
            endpoint=proposal.endpoint,
            at=proposal.at,
            timeline_id=proposal.timeline_id,
        )

    async def _periods(self, storage, node_id, source_id):
        [edge] = [
            edge
            for edge in await storage.get_edges_from(node_id, edge_type=EdgeType.SOURCED_FROM)
            if edge.dst_id == source_id
        ]
        return edge.validity

    async def test_the_period_closes_and_says_it_was_inferred(self, storage, renaming):
        leningrad, _, older, _ = renaming
        [proposal] = await propose_boundaries(storage)

        assert await self._boundary(storage, proposal) is None

        [period] = await self._periods(storage, leningrad.id, older.id)
        assert period.end.at.year == 1991
        assert period.basis is IntervalBasis.INFERRED
        assert period.start.at.year == 1924

    async def test_the_claim_now_falls_clear_of_its_successor(self, storage, renaming):
        """What closing the period is *for*: the soundness check can see it.

        While the period was open nothing could be concluded about the two
        together, so an inference drawn across them was uncheckable.
        """
        from epimemer.core.temporal import assertions_are_disjoint

        leningrad, petersburg, older, newer = renaming
        [proposal] = await propose_boundaries(storage)
        await self._boundary(storage, proposal)

        before = await self._periods(storage, petersburg.id, newer.id)
        after = await self._periods(storage, leningrad.id, older.id)
        assert assertions_are_disjoint(after, before) is True

    async def test_the_same_proposal_twice_is_refused_the_second_time(self, storage, renaming):
        [proposal] = await propose_boundaries(storage)
        await self._boundary(storage, proposal)

        refusal = await self._boundary(storage, proposal)

        assert refusal is not None
        assert "still open" in refusal.reason
        # And it is gone from the proposals, having been answered.
        assert await propose_boundaries(storage) == []

    async def test_an_ambiguous_request_is_refused_rather_than_guessed(self, storage, documents):
        """Two open periods from one source on one clock name no single target.

        The thing being overwritten is what a source is recorded as asserting,
        so picking one is not a tie to break.
        """
        older, newer = documents
        earlier = await _claim(
            storage,
            older,
            "one claim",
            _period(start=_at(1900)),
            _period(start=_at(1950)),
        )
        later = await _claim(storage, newer, "another claim", _period(start=_at(1991)))
        await _succeeds(storage, earlier, later)

        refusal = await apply_boundary(
            storage,
            node_id=earlier.id,
            source_id=older.id,
            endpoint="end",
            at=datetime(1991, 1, 1, tzinfo=UTC),
        )

        assert refusal is not None and "2 periods" in refusal.reason
        assert all(
            isinstance(period.end, UnknownInstant)
            for period in await self._periods(storage, earlier.id, older.id)
        )

    async def test_a_source_the_claim_does_not_have(self, storage, renaming):
        leningrad, _, _, newer = renaming

        refusal = await apply_boundary(
            storage,
            node_id=leningrad.id,
            source_id=newer.id,
            endpoint="end",
            at=datetime(1991, 1, 1, tzinfo=UTC),
        )

        assert refusal is not None and "0 provenance edges" in refusal.reason

    async def test_a_date_the_period_cannot_hold(self, storage, renaming):
        """Before its own start. Refused with the model's own words."""
        leningrad, _, older, _ = renaming

        refusal = await apply_boundary(
            storage,
            node_id=leningrad.id,
            source_id=older.id,
            endpoint="end",
            at=datetime(1900, 1, 1, tzinfo=UTC),
        )

        assert refusal is not None and "must start before it ends" in refusal.reason

    async def test_a_claim_retired_as_wrong(self, storage, renaming):
        leningrad, _, older, _ = renaming
        await storage.set_node_status_tx(
            [leningrad], status=NodeStatus.CORRECTED, at=datetime.now(UTC)
        )

        refusal = await apply_boundary(
            storage,
            node_id=leningrad.id,
            source_id=older.id,
            endpoint="end",
            at=datetime(1991, 1, 1, tzinfo=UTC),
        )

        assert refusal is not None

    async def test_an_endpoint_that_is_not_one(self, storage, renaming):
        leningrad, _, older, _ = renaming

        refusal = await apply_boundary(
            storage,
            node_id=leningrad.id,
            source_id=older.id,
            endpoint="middle",
            at=datetime(1991, 1, 1, tzinfo=UTC),
        )

        assert refusal is not None and "not an endpoint" in refusal.reason
