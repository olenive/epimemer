"""A boundary proposal can be declined, and a declined one is not offered again.

`reflect` proposes filling one open endpoint of one source's period, using a
date from the claim on the other side of a `temporally_followed_by` edge.
Accepting one had a writer and declining one had none, so a proposal the judge
disagreed with came back on every reflect, for ever.

The case that found it, from the project's own memory graph: *"nothing
unreleased remains on main after release 0.2.7"*, open from the day of that
release, followed by the same claim about 0.2.8 two days later. Closing the
first period on the second release date would have the graph assert main
stayed clean in between, and commits landed on main the day of the first
release. The judge declined, and the decline could not be recorded.

**A decline is a journal row, never an edge.** The two claims in a succession
are often also nominated as a similar or contradictory pair, and an `assessed`
edge between them would silence those nominations as well.

Both backends, through the `storage` fixture.
"""

from datetime import UTC, datetime

import pytest

from epimemer.core.temporal import (
    IntervalBasis,
    PreciseInstant,
    UnknownInstant,
    ValidityInterval,
)
from epimemer.core.types import (
    DecisionKind,
    EdgeType,
    Fact,
    JudgeRef,
    NodeEdge,
    NodeStatus,
    RawDocument,
    ValueSignal,
)
from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.mcp import tools
from epimemer.pipelines.reflection.boundaries import boundary_question

CRITIC = JudgeRef(agent_id="critic", digest="d1")


@pytest.fixture
def embedding_provider() -> MockEmbeddingProvider:
    return MockEmbeddingProvider(model_id="mock-embed", dimension=8)


async def _dated_claim(storage, document: RawDocument, content: str, start: datetime) -> Fact:
    fact = Fact(content=content, source_id="seg-1", value=ValueSignal())
    await storage.store_node(fact)
    await storage.store_edge(
        NodeEdge(
            src_id=fact.id,
            dst_id=document.id,
            type=EdgeType.SOURCED_FROM,
            validity=[
                ValidityInterval(
                    start=PreciseInstant(at=start),
                    end=UnknownInstant(),
                    basis=IntervalBasis.STATED,
                )
            ],
        )
    )
    return fact


async def releases(storage) -> tuple[Fact, Fact, RawDocument]:
    """The case that found the defect: two releases, and a succession between them."""
    first_note = RawDocument(content="Release notes for 0.2.7", source="notes-0.2.7")
    second_note = RawDocument(content="Release notes for 0.2.8", source="notes-0.2.8")
    await storage.store_document(first_note)
    await storage.store_document(second_note)
    first = await _dated_claim(
        storage,
        first_note,
        "Nothing unreleased remains on main after release 0.2.7.",
        datetime(2026, 9, 19, tzinfo=UTC),
    )
    second = await _dated_claim(
        storage,
        second_note,
        "Nothing unreleased remains on main after release 0.2.8.",
        datetime(2026, 9, 21, tzinfo=UTC),
    )
    await storage.set_node_status_tx([first], status=NodeStatus.HISTORICAL, at=datetime.now(UTC))
    await storage.store_edge(
        NodeEdge(src_id=first.id, dst_id=second.id, type=EdgeType.TEMPORALLY_FOLLOWED_BY)
    )
    return first, second, first_note


def decline_of(proposal: dict, reason: str = "Commits landed on main that day.") -> dict:
    """A decline copied from a proposal as `reflect` returns it: JSON all the way."""
    return {
        "node_id": proposal["node"]["id"],
        "source_id": proposal["source_id"],
        "endpoint": proposal["endpoint"],
        "at": proposal["at"],
        "timeline_id": proposal["timeline_id"],
        "reason": reason,
    }


async def _proposals(storage, embedding_provider) -> list[dict]:
    result, _ = await tools.reflect(storage, embedding_provider)
    return result["boundary_proposals"]


class TestDecliningAProposal:
    async def test_it_is_not_offered_again(self, storage, embedding_provider):
        await releases(storage)
        [proposal] = await _proposals(storage, embedding_provider)

        result, _ = await tools.apply_reflection(
            storage, embedding_provider, boundaries_declined=[decline_of(proposal)], judge=CRITIC
        )

        assert result["boundaries_declined"] == 1
        assert result["boundaries_declined_refused"] == []
        assert await _proposals(storage, embedding_provider) == []

    async def test_one_row_records_the_question_and_the_reason(self, storage, embedding_provider):
        first, _, first_note = await releases(storage)
        [proposal] = await _proposals(storage, embedding_provider)

        await tools.apply_reflection(
            storage, embedding_provider, boundaries_declined=[decline_of(proposal)], judge=CRITIC
        )

        [row] = await storage.query_decisions(kinds=[DecisionKind.BOUNDARY_DECLINED])
        assert row.subject_ids == [first.id, first_note.id]
        assert row.covers == [boundary_question("end", datetime(2026, 9, 21, tzinfo=UTC), None)]
        assert row.certainty_basis == "Commits landed on main that day."
        assert row.judged_by.agent_id == "critic"
        # Declined, not accepted: a reviewer selecting `boundary` sees nothing.
        assert await storage.query_decisions(kinds=[DecisionKind.BOUNDARY]) == []

    async def test_the_period_stays_open(self, storage, embedding_provider):
        first, _, first_note = await releases(storage)
        [proposal] = await _proposals(storage, embedding_provider)

        await tools.apply_reflection(
            storage, embedding_provider, boundaries_declined=[decline_of(proposal)], judge=CRITIC
        )

        [edge] = [
            edge
            for edge in await storage.get_edges_from(first.id, edge_type=EdgeType.SOURCED_FROM)
            if edge.dst_id == first_note.id
        ]
        assert isinstance(edge.validity[0].end, UnknownInstant)

    async def test_it_counts_toward_what_was_applied(self, storage, embedding_provider):
        await releases(storage)
        [proposal] = await _proposals(storage, embedding_provider)

        _, meta = await tools.apply_reflection(
            storage, embedding_provider, boundaries_declined=[decline_of(proposal)], judge=CRITIC
        )

        assert meta.nodes_returned == 1

    async def test_an_unknown_node_is_refused_with_a_reason(self, storage, embedding_provider):
        await releases(storage)
        [proposal] = await _proposals(storage, embedding_provider)
        entry = decline_of(proposal) | {"node_id": "no-such-claim"}

        result, _ = await tools.apply_reflection(
            storage, embedding_provider, boundaries_declined=[entry], judge=CRITIC
        )

        assert result["boundaries_declined"] == 0
        [refused] = result["boundaries_declined_refused"]
        assert refused["node_id"] == "no-such-claim"
        assert "no such claim" in refused["reason"]
        assert await storage.query_decisions(kinds=[DecisionKind.BOUNDARY_DECLINED]) == []

    async def test_an_unknown_source_is_refused_with_a_reason(self, storage, embedding_provider):
        await releases(storage)
        [proposal] = await _proposals(storage, embedding_provider)
        entry = decline_of(proposal) | {"source_id": "no-such-document"}

        result, _ = await tools.apply_reflection(
            storage, embedding_provider, boundaries_declined=[entry], judge=CRITIC
        )

        [refused] = result["boundaries_declined_refused"]
        assert "0 provenance edges" in refused["reason"]
        assert len(await _proposals(storage, embedding_provider)) == 1

    async def test_an_endpoint_that_is_not_one_is_refused(self, storage, embedding_provider):
        await releases(storage)
        [proposal] = await _proposals(storage, embedding_provider)
        entry = decline_of(proposal) | {"endpoint": "middle"}

        result, _ = await tools.apply_reflection(
            storage, embedding_provider, boundaries_declined=[entry], judge=CRITIC
        )

        [refused] = result["boundaries_declined_refused"]
        assert "not an endpoint" in refused["reason"]

    async def test_a_decline_about_another_date_leaves_the_proposal(
        self, storage, embedding_provider
    ):
        """A different date is a different question."""
        await releases(storage)
        [proposal] = await _proposals(storage, embedding_provider)
        entry = decline_of(proposal) | {"at": "2026-09-20T00:00:00+00:00"}

        result, _ = await tools.apply_reflection(
            storage, embedding_provider, boundaries_declined=[entry], judge=CRITIC
        )

        assert result["boundaries_declined"] == 1
        assert len(await _proposals(storage, embedding_provider)) == 1

    async def test_the_pair_is_still_offered_as_a_pair(self, storage, embedding_provider):
        """No `assessed` edge: similarity and contradiction nominations are untouched."""
        first, second, _ = await releases(storage)
        [proposal] = await _proposals(storage, embedding_provider)

        await tools.apply_reflection(
            storage, embedding_provider, boundaries_declined=[decline_of(proposal)], judge=CRITIC
        )

        for direction in ("from", "to"):
            assessed = await storage.get_edges_for(
                [first.id, second.id], direction=direction, edge_type=EdgeType.ASSESSED
            )
            assert all(edges == [] for edges in assessed.values())
