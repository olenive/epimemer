"""The tools that state an order, answer a dispute about it, and read it back.

The reasoning is tested over pure functions in `tests/pipelines/test_ordering.py`.
What is under test here is the part a backend is needed for: the record written
back, the journal row, the `TIMELINK` retirement a split and a merge need, and
the flags a reader sees on `search` and `reflect`.

Both backends via the `storage` fixture.
"""

from datetime import UTC, datetime

from epimemer.core.types import (
    DecisionKind,
    EdgeType,
    EmbeddingRecord,
    Fact,
    JudgeRef,
    NodeEdge,
    Timeline,
    Timepoint,
)
from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.mcp import tools

ARCHIVIST = JudgeRef(agent_id="archivist", digest="d1")


def _dt(year: int, month: int = 1, day: int = 1) -> datetime:
    return datetime(year, month, day, tzinfo=UTC)


async def _timeline(storage, points) -> Timeline:
    timeline = Timeline(name="the parish", timepoints=list(points))
    await storage.store_timeline(timeline)
    return timeline


async def _disputed(storage):
    """A timeline whose two sources disagree about the order of two points."""
    timeline = await _timeline(
        storage,
        [
            Timepoint(id="fire", label="the fire"),
            Timepoint(id="flood", label="the flood"),
        ],
    )
    await tools.order_timepoints(
        timeline.id,
        storage,
        pairs=[{"earlier_id": "fire", "later_id": "flood"}],
        source_id="doc-1",
        basis="stated",
        judge=ARCHIVIST,
    )
    result, _ = await tools.order_timepoints(
        timeline.id,
        storage,
        pairs=[{"earlier_id": "flood", "later_id": "fire"}],
        source_id="doc-2",
        basis="stated",
        judge=ARCHIVIST,
    )
    return timeline.id, result["temporal_contradictions"][0]["temporal_contradiction_id"]


class TestOrderTimepoints:
    async def test_the_constraint_is_written_to_the_record(self, storage):
        timeline = await _timeline(
            storage,
            [Timepoint(id="fire", label="the fire"), Timepoint(id="flood", label="the flood")],
        )
        result, _ = await tools.order_timepoints(
            timeline.id,
            storage,
            pairs=[{"earlier_id": "fire", "later_id": "flood"}],
            source_id="doc-1",
            basis="stated",
            judge=ARCHIVIST,
        )
        assert result["ordered"] is True
        assert result["pairs"][0]["created"] is True

        stored = await storage.get_timeline(timeline.id)
        assert len(stored.constraints) == 1
        assert stored.constraints[0].source_id == "doc-1"
        assert stored.constraints[0].judged_by.agent_id == "archivist"

    async def test_a_basis_that_is_neither_is_refused(self, storage):
        timeline = await _timeline(storage, [Timepoint(id="fire", label="the fire")])
        result, _ = await tools.order_timepoints(
            timeline.id,
            storage,
            pairs=[],
            source_id="doc-1",
            basis="world knowledge",
            judge=ARCHIVIST,
        )
        assert result["ordered"] is False
        assert "stated" in result["refused"]

    async def test_one_call_writes_one_journal_row(self, storage):
        timeline = await _timeline(
            storage,
            [
                Timepoint(id="p1", label="p1"),
                Timepoint(id="p2", label="p2"),
                Timepoint(id="p3", label="p3"),
            ],
        )
        await tools.order_timepoints(
            timeline.id,
            storage,
            pairs=[
                {"earlier_id": "p1", "later_id": "p2"},
                {"earlier_id": "p2", "later_id": "p3"},
            ],
            source_id="doc-1",
            basis="stated",
            judge=ARCHIVIST,
        )
        rows = await storage.query_decisions(kinds=[DecisionKind.TEMPORAL_ORDER])
        assert len(rows) == 1
        assert rows[0].judged_by.agent_id == "archivist"

    async def test_a_contradiction_comes_back_without_failing_the_call(self, storage):
        timeline_id, contradiction_id = await _disputed(storage)
        stored = await storage.get_timeline(timeline_id)
        assert [c.id for c in stored.temporal_contradictions] == [contradiction_id]
        assert len(stored.constraints) == 2


class TestAddTimepointRunsTheChecks:
    async def test_a_dated_point_can_open_a_contradiction(self, storage):
        timeline = await _timeline(
            storage,
            [Timepoint(id="fire", start=_dt(1899)), Timepoint(id="flood", label="the flood")],
        )
        await tools.order_timepoints(
            timeline.id,
            storage,
            pairs=[{"earlier_id": "flood", "later_id": "fire"}],
            source_id="doc-1",
            basis="stated",
            judge=ARCHIVIST,
        )
        # A source says the flood came first; dating it later closes the loop.
        result, _ = await tools.add_timeline_timepoint(
            timeline.id, storage, start=_dt(1901), label="the second flood"
        )
        assert result["temporal_contradictions"] == []

        # Now order the new point before the fire as well, which crosses a date.
        result, _ = await tools.order_timepoints(
            timeline.id,
            storage,
            pairs=[{"earlier_id": result["timepoint_id"], "later_id": "fire"}],
            source_id="doc-2",
            basis="stated",
            judge=ARCHIVIST,
        )
        assert result["temporal_contradictions"]
        assert result["temporal_contradictions"][0]["kind"] == "cycle"

    async def test_a_vague_point_opens_nothing(self, storage):
        timeline = await _timeline(storage, [Timepoint(id="fire", start=_dt(1899))])
        result, _ = await tools.add_timeline_timepoint(
            timeline.id, storage, label="some time later"
        )
        assert result["temporal_contradictions"] == []
        assert result["kind"] == "vague"


class TestQueryTimeline:
    async def test_the_ordering_modes_answer_for_a_point_with_no_date(self, storage):
        timeline = await _timeline(
            storage,
            [
                Timepoint(id="p1", start=_dt(1890)),
                Timepoint(id="p2", label="the middle"),
                Timepoint(id="p3", start=_dt(1910)),
            ],
        )
        await tools.order_timepoints(
            timeline.id,
            storage,
            pairs=[
                {"earlier_id": "p1", "later_id": "p2"},
                {"earlier_id": "p2", "later_id": "p3"},
            ],
            source_id="doc-1",
            basis="stated",
            judge=ARCHIVIST,
        )
        result, _ = await tools.query_timeline(timeline.id, storage, between=["p1", "p3"])
        assert [tp["id"] for tp in result["timepoints"]] == ["p2"]
        assert result["timepoints"][0]["earliest"] == _dt(1890).isoformat()
        assert result["timepoints"][0]["latest"] == _dt(1910).isoformat()

        after, _ = await tools.query_timeline(timeline.id, storage, after="p1")
        assert {tp["id"] for tp in after["timepoints"]} == {"p2", "p3"}
        before, _ = await tools.query_timeline(timeline.id, storage, before="p3")
        assert {tp["id"] for tp in before["timepoints"]} == {"p1", "p2"}

    async def test_stated_only_leaves_out_what_a_judge_inferred(self, storage):
        # All three undated, so the only steps are the ones a source asserted
        # and the basis filter is the whole of what moves the answer.
        timeline = await _timeline(
            storage,
            [
                Timepoint(id="p1", label="the first"),
                Timepoint(id="p2", label="the middle"),
                Timepoint(id="p3", label="the last"),
            ],
        )
        await tools.order_timepoints(
            timeline.id,
            storage,
            pairs=[{"earlier_id": "p1", "later_id": "p2"}],
            source_id="doc-1",
            basis="stated",
            judge=ARCHIVIST,
        )
        await tools.order_timepoints(
            timeline.id,
            storage,
            pairs=[{"earlier_id": "p2", "later_id": "p3"}],
            source_id="doc-2",
            basis="inferred",
            judge=ARCHIVIST,
        )
        both, _ = await tools.query_timeline(timeline.id, storage, after="p1")
        assert {tp["id"] for tp in both["timepoints"]} == {"p2", "p3"}

        stated, _ = await tools.query_timeline(timeline.id, storage, after="p1", basis="stated")
        assert {tp["id"] for tp in stated["timepoints"]} == {"p2"}

    async def test_a_contested_point_is_marked_and_can_be_left_out(self, storage):
        timeline_id, contradiction_id = await _disputed(storage)
        result, _ = await tools.query_timeline(timeline_id, storage)
        marked = {tp["id"]: tp for tp in result["timepoints"]}
        assert marked["fire"]["contested"] is True
        assert marked["fire"]["temporal_contradiction_id"] == contradiction_id
        # A contested point gets no derived position.
        assert "earliest" not in marked["fire"]

        without, _ = await tools.query_timeline(timeline_id, storage, include_contested=False)
        assert without["timepoints"] == []


class TestVerdicts:
    async def test_retire_closes_the_contradiction_and_frees_the_other(self, storage):
        timeline_id, contradiction_id = await _disputed(storage)
        stored = await storage.get_timeline(timeline_id)
        doomed, survivor = stored.temporal_contradictions[0].constraint_ids

        result, _ = await tools.resolve_temporal_contradiction(
            timeline_id,
            storage,
            contradiction_id=contradiction_id,
            verdict="retire_constraint",
            constraint_id=doomed,
            because="the second source narrates rather than orders",
            judge=ARCHIVIST,
        )
        assert result["resolved"] is True
        assert result["returned_to_live"] == [survivor]

        after, _ = await tools.query_timeline(timeline_id, storage)
        assert all(tp["contested"] is False for tp in after["timepoints"])
        rows = await storage.query_decisions(kinds=[DecisionKind.TEMPORAL_VERDICT])
        assert len(rows) == 1

    async def test_a_split_moves_the_named_links_and_leaves_the_rest(self, storage):
        timeline_id, contradiction_id = await _disputed(storage)
        moving = Fact(content="The fire took the roof", source_id="seg-1")
        staying = Fact(content="The fire was seen from the hill", source_id="seg-1")
        for node in (moving, staying):
            await storage.store_node(node)
            await storage.store_edge(
                NodeEdge(
                    src_id=node.id,
                    dst_id=timeline_id,
                    type=EdgeType.TIMELINK,
                    metadata={"timepoint_id": "fire"},
                )
            )

        stored = await storage.get_timeline(timeline_id)
        result, _ = await tools.resolve_temporal_contradiction(
            timeline_id,
            storage,
            contradiction_id=contradiction_id,
            verdict="not_the_same_event",
            point_id="fire",
            constraints_to_move=[stored.temporal_contradictions[0].constraint_ids[0]],
            nodes_to_move=[moving.id],
            because="two fires, and each source saw a different one",
            judge=ARCHIVIST,
        )
        assert result["resolved"] is True
        assert len(result["links_retired"]) == 1
        assert len(result["links_written"]) == 1
        assert result["nodes_left_on_original"] == [staying.id]

        links = await storage.get_edges_from(moving.id, edge_type=EdgeType.TIMELINK)
        live = [edge for edge in links if edge.retired_at is None]
        assert len(live) == 1
        assert live[0].metadata["timepoint_id"] == result["new_timepoint_id"]
        # The old link is kept rather than deleted, and names its replacement.
        old = [edge for edge in links if edge.retired_at is not None]
        assert len(old) == 1
        assert old[0].superseded_by == live[0].id
        assert old[0].retired_by.agent_id == "archivist"

    async def test_hold_keeps_it_open_and_stops_the_nomination(self, storage):
        timeline_id, contradiction_id = await _disputed(storage)
        result, _ = await tools.resolve_temporal_contradiction(
            timeline_id,
            storage,
            contradiction_id=contradiction_id,
            verdict="hold",
            because="two equally good accounts and nothing on hand settles it",
            judge=ARCHIVIST,
        )
        assert result["resolved"] is True

        nominated, _ = await tools.reflect(storage, MockEmbeddingProvider())
        assert nominated["temporal_contradictions"] == []

        queried, _ = await tools.query_timeline(timeline_id, storage)
        assert all(tp["contested"] is True for tp in queried["timepoints"])

    async def test_new_evidence_clears_a_hold_and_the_nomination_returns(self, storage):
        timeline_id, contradiction_id = await _disputed(storage)
        await tools.resolve_temporal_contradiction(
            timeline_id,
            storage,
            contradiction_id=contradiction_id,
            verdict="hold",
            because="nothing settles it",
            judge=ARCHIVIST,
        )
        await tools.add_timeline_timepoint(timeline_id, storage, label="the storm")
        stored = await storage.get_timeline(timeline_id)
        storm = next(tp for tp in stored.timepoints if tp.label == "the storm")
        await tools.order_timepoints(
            timeline_id,
            storage,
            pairs=[{"earlier_id": storm.id, "later_id": "fire"}],
            source_id="doc-3",
            basis="stated",
            judge=ARCHIVIST,
        )
        nominated, _ = await tools.reflect(storage, MockEmbeddingProvider())
        assert [
            found["temporal_contradiction_id"] for found in nominated["temporal_contradictions"]
        ] == [contradiction_id]

    async def test_an_unknown_verdict_is_refused(self, storage):
        timeline_id, contradiction_id = await _disputed(storage)
        result, _ = await tools.resolve_temporal_contradiction(
            timeline_id,
            storage,
            contradiction_id=contradiction_id,
            verdict="ignore_it",
            because="no",
            judge=ARCHIVIST,
        )
        assert result["resolved"] is False
        assert "retire_constraint" in result["refused"]


class TestMergeTimepoints:
    async def test_a_merge_moves_every_link(self, storage):
        timeline = await _timeline(
            storage,
            [
                Timepoint(id="launch", label="the launch"),
                Timepoint(id="golive", label="the go-live"),
            ],
        )
        facts = [Fact(content=f"note {n}", source_id="seg-1") for n in range(2)]
        for node in facts:
            await storage.store_node(node)
            await storage.store_edge(
                NodeEdge(
                    src_id=node.id,
                    dst_id=timeline.id,
                    type=EdgeType.TIMELINK,
                    metadata={"timepoint_id": "golive"},
                )
            )

        result, _ = await tools.merge_timepoints(
            timeline.id,
            storage,
            survivor_id="launch",
            merged_id="golive",
            because="two documents, one event",
            judge=ARCHIVIST,
        )
        assert result["merged"] is True
        assert len(result["links_written"]) == 2

        for node in facts:
            live = [
                edge
                for edge in await storage.get_edges_from(node.id, edge_type=EdgeType.TIMELINK)
                if edge.retired_at is None
            ]
            assert [edge.metadata["timepoint_id"] for edge in live] == ["launch"]

        queried, _ = await tools.query_timeline(timeline.id, storage)
        assert [tp["id"] for tp in queried["timepoints"]] == ["launch"]
        rows = await storage.query_decisions(kinds=[DecisionKind.TIMEPOINT_MERGE])
        assert len(rows) == 1

    async def test_a_merge_is_refused_when_a_source_ordered_the_two(self, storage):
        timeline = await _timeline(
            storage,
            [
                Timepoint(id="launch", label="the launch"),
                Timepoint(id="golive", label="the go-live"),
            ],
        )
        await tools.order_timepoints(
            timeline.id,
            storage,
            pairs=[{"earlier_id": "launch", "later_id": "golive"}],
            source_id="doc-1",
            basis="stated",
            judge=ARCHIVIST,
        )
        result, _ = await tools.merge_timepoints(
            timeline.id,
            storage,
            survivor_id="launch",
            merged_id="golive",
            because="one event",
            judge=ARCHIVIST,
        )
        assert result["merged"] is False
        assert "doc-1" in result["refused"]


class TestDateContestedOnSearch:
    async def _fact(self, storage, embedder, content):
        node = Fact(content=content, source_id="seg-1")
        await storage.store_node(node)
        vector = (await embedder.embed([content]))[0]
        await storage.store_embedding(
            EmbeddingRecord(item_id=node.id, model_id=embedder.model_id, vector=vector)
        )
        return node

    async def _dated(self, storage, node, timeline_id, timepoint_id):
        await storage.store_edge(
            NodeEdge(
                src_id=node.id,
                dst_id=timeline_id,
                type=EdgeType.TIMELINK,
                metadata={"timepoint_id": timepoint_id},
            )
        )

    async def test_a_fact_dated_to_a_contested_point_carries_the_flag(self, storage):
        embedder = MockEmbeddingProvider()
        timeline_id, contradiction_id = await _disputed(storage)
        dated = await self._fact(storage, embedder, "The treasury was empty at the fire")
        await self._dated(storage, dated, timeline_id, "fire")
        plain = await self._fact(storage, embedder, "The treasury was empty at the fire, too")

        result, _ = await tools.search(
            "The treasury was empty at the fire", storage, embedder, k=10
        )
        by_id = {n["id"]: n for n in result["nodes"]}
        assert by_id[dated.id]["date_contested"][0]["temporal_contradiction_id"] == (
            contradiction_id
        )
        # A fact with no timelink carries neither flag.
        assert "date_contested" not in by_id[plain.id]

    async def test_a_fact_on_an_undisputed_point_carries_nothing(self, storage):
        embedder = MockEmbeddingProvider()
        timeline = await _timeline(storage, [Timepoint(id="fire", start=_dt(1897))])
        node = await self._fact(storage, embedder, "The treasury was empty at the fire")
        await self._dated(storage, node, timeline.id, "fire")

        result, _ = await tools.search(
            "The treasury was empty at the fire", storage, embedder, k=10
        )
        assert result["nodes"]
        assert "date_contested" not in result["nodes"][0]


class TestCreateTimelink:
    async def test_the_response_names_the_bounds_and_whether_it_is_contested(self, storage):
        timeline = await _timeline(
            storage,
            [
                Timepoint(id="p1", start=_dt(1890)),
                Timepoint(id="p2", label="the middle"),
                Timepoint(id="p3", start=_dt(1910)),
            ],
        )
        await tools.order_timepoints(
            timeline.id,
            storage,
            pairs=[
                {"earlier_id": "p1", "later_id": "p2"},
                {"earlier_id": "p2", "later_id": "p3"},
            ],
            source_id="doc-1",
            basis="stated",
            judge=ARCHIVIST,
        )
        node = Fact(content="The roof went on", source_id="seg-1")
        await storage.store_node(node)

        result, _ = await tools.create_timelink(node.id, timeline.id, storage, timepoint_id="p2")
        assert result["kind"] == "vague"
        assert result["earliest"] == _dt(1890).isoformat()
        assert result["latest"] == _dt(1910).isoformat()
        assert result["contested"] is False
