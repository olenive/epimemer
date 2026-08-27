"""Where else this agent has decided things (ISSUES #73).

#72 settled that the journal stays per graph — `subject_ids` are node ids, and a
node id resolves only where it lives — and left one thing genuinely missing: a
reviewer had no way to find out there was more elsewhere. The graph it is in
answers loudly; every other graph is silent in a way indistinguishable from
empty.

**The reviewer this exists for is a later, different agent.** The one that made
the decisions switched the graphs itself and never needed telling. So the
locator is not a convenience for the author — it is the only channel the
successor has.

Two properties are pinned here rather than left to docstrings, because both are
judgment calls a later change would otherwise quietly reverse:

- **A locator may overcount and must never undercount.** Only the filters
  `query_decisions` already implements are mirrored into it. A count too high
  sends someone to look and find less; a count too low leaves them not looking.
- **Reading another graph must not disturb this one**, must not *create* one,
  and — on the backend that has to borrow the connection — must take the guard's
  mover turn rather than raising inside a tool call that already holds a user's.
"""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest
from fastmcp import FastMCP

from epimemer.core.types import DecisionKind, DecisionRecord, JudgeRef
from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.mcp import tools
from epimemer.mcp.config import ServerConfig
from epimemer.mcp.retrieval_records import new_record_log
from epimemer.mcp.server import MOVES_THE_GRAPH
from epimemer.mcp.server import mcp as epimemer_mcp
from epimemer.storage.memory import InMemoryStorage
from epimemer.storage.surrealdb_adapter import SurrealDBStorage


CRITIC = JudgeRef(agent_id="critic", digest="d1")
EDITOR = JudgeRef(agent_id="editor", digest="d2")

NOW = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)


def _row(judge: JudgeRef | None = CRITIC, *, at: datetime = NOW) -> DecisionRecord:
    return DecisionRecord(
        kind=DecisionKind.INGEST,
        subject_ids=["node-1"],
        judged_by=judge,
        decided_at=at,
    )


async def _journal(storage, graph: str, *records: DecisionRecord) -> None:
    """Write rows into `graph` and put the active graph back where it was.

    Switching rather than borrowing: that is the sequence #72 kept as the
    fallback, and using it to *build* the fixture keeps the test's own writes
    out of the mechanism it is testing.
    """
    home = storage.current_database
    await storage.switch_database(graph)
    for record in records:
        await storage.record_decision(record)
    await storage.switch_database(home)


class TestItCountsWhereElseToLook:
    async def test_it_names_the_other_graphs_and_not_this_one(self, storage):
        await _journal(storage, "field-notes", _row(), _row())
        await storage.record_decision(_row())

        result, _ = await tools.review(storage)

        # Not "default": the two backends start on different names, and a test
        # that hard-codes one of them is asserting a fixture rather than the
        # behaviour.
        assert result["graph"] == storage.current_database
        assert {g["graph"] for g in result["elsewhere"]["graphs"]} == {"field-notes"}
        assert result["elsewhere"]["total"] == 2
        # The near graph's own answer is `decisions_scanned`, and counting it
        # twice would make the total read as a corpus size.
        assert result["decisions_scanned"] == 1

    async def test_a_graph_with_no_journal_is_counted_zero_not_omitted(self, storage):
        """*Nothing there* is an answer a reviewer can act on; a graph missing
        from the list reads as *not checked*, which is the silence #73 is
        about."""
        await _journal(storage, "field-notes", _row())
        await _journal(storage, "petritype-server")

        result, _ = await tools.review(storage)

        counts = {g["graph"]: g["decisions"] for g in result["elsewhere"]["graphs"]}
        assert counts == {"field-notes": 1, "petritype-server": 0}
        assert result["elsewhere"]["unreadable"] == []

    async def test_it_carries_no_rows_and_no_ids(self, storage):
        """Counts and nothing else. A row read out of another graph arrives
        readable but not actionable — every write path is single-graph, so an
        id from over there dereferences nowhere here (#72)."""
        await _journal(storage, "field-notes", _row(), _row())

        result, _ = await tools.review(storage)

        payload = json.dumps(result["elsewhere"])
        assert "node-1" not in payload
        for entry in result["elsewhere"]["graphs"]:
            assert set(entry) == {"graph", "decisions"}

    async def test_an_empty_namespace_is_an_empty_locator(self, storage):
        await storage.record_decision(_row())

        result, _ = await tools.review(storage)

        assert result["elsewhere"]["graphs"] == []
        assert result["elsewhere"]["total"] == 0


class TestItAgreesWithTheReaderItPointsAt:
    """The filters it does mirror have to mean the same thing on both sides.

    A locator that counts a window differently from the review you run when you
    get there is worse than no locator: it makes the reviewer distrust both
    numbers, with no way to tell which one is wrong.
    """

    async def test_by_agent_counts_only_that_agent(self, storage):
        await _journal(storage, "field-notes", _row(CRITIC), _row(EDITOR), _row(None))

        result, _ = await tools.review(storage, mode="by_agent", agent_id="critic")

        assert result["elsewhere"]["total"] == 1

    async def test_an_unattributed_row_matches_no_agent(self, storage):
        """Unknown is not an id — the same rule `query_decisions` states."""
        await _journal(storage, "field-notes", _row(None), _row(None))

        result, _ = await tools.review(storage, mode="by_agent", agent_id="critic")

        assert result["elsewhere"]["total"] == 0

    async def test_a_window_bounds_it_the_way_the_reader_does(self, storage):
        """`since` inclusive, `until` exclusive — the half-open convention, on
        both sides of the line."""
        await _journal(
            storage, "field-notes",
            _row(at=NOW - timedelta(days=2)),
            _row(at=NOW),
            _row(at=NOW + timedelta(days=2)),
        )

        result, _ = await tools.review(
            storage, mode="since", since=NOW, until=NOW + timedelta(days=2)
        )

        assert result["elsewhere"]["total"] == 1

    async def test_the_boundary_row_is_counted_the_same_as_it_is_read(self, storage):
        """The row that sits exactly on `since`, counted here and read there.

        Timestamps are stored as text on one backend and compared as text, so a
        bound that renders differently from the row puts them in the wrong
        order. Both sides go through one clause builder for that reason; this is
        what would fail if they stopped.
        """
        on_the_bound = _row(at=NOW)
        await _journal(storage, "field-notes", on_the_bound)

        located, _ = await tools.review(storage, mode="since", since=NOW)

        home = storage.current_database
        await storage.switch_database("field-notes")
        read = await storage.query_decisions(since=NOW)
        await storage.switch_database(home)

        assert located["elsewhere"]["total"] == len(read) == 1


class TestWhatItDeliberatelyDoesNotNarrowBy:
    """Wider, never narrower — and it says which filters ran.

    `certainty_ceiling` and `mode="unreviewed"` are narrowings a reviewer
    applies while *browsing*. Mirroring them would put review semantics into a
    second implementation on two backends, and every such mirror is a place the
    two can disagree. Overcounting costs a wasted look; undercounting costs the
    look entirely.
    """

    async def test_the_ceiling_does_not_narrow_it(self, storage):
        await _journal(storage, "field-notes", _row(), _row())

        result, _ = await tools.review(storage, certainty_ceiling=0.2)

        assert result["elsewhere"]["total"] == 2

    async def test_unreviewed_mode_does_not_narrow_it(self, storage):
        await _journal(storage, "field-notes", _row(), _row())

        result, _ = await tools.review(storage, mode="unreviewed")

        assert result["elsewhere"]["total"] == 2

    async def test_counted_with_says_which_filters_ran(self, storage):
        """So a reviewer who switches and sees fewer rows knows why, rather
        than reading the difference as a defect in one of the two."""
        await _journal(storage, "field-notes", _row())

        result, _ = await tools.review(
            storage, mode="by_agent", agent_id="critic",
            since=NOW - timedelta(days=1), certainty_ceiling=0.4,
        )

        counted_with = result["elsewhere"]["counted_with"]
        assert set(counted_with) == {"agent_ids", "since", "until"}
        # The ids the handle resolved to, not the handle: a judge nothing here
        # has a record for resolves to itself, which is this graph's case.
        assert counted_with["agent_ids"] == ["critic"]
        assert result["judge"]["asked_for"] == "critic"
        assert "certainty_ceiling" not in counted_with
        assert "mode" not in counted_with

    async def test_a_refused_mode_locates_nothing(self, storage):
        """The refusal is the whole answer — sweeping every graph for a call
        that was never going to run is work nobody asked for."""
        await _journal(storage, "field-notes", _row())

        result, _ = await tools.review(storage, mode="by_agent")

        assert "refused" in result
        assert "elsewhere" not in result


class TestReadingAnotherGraphMustNotDisturbThisOne:
    async def test_the_active_graph_is_where_it_was_afterwards(self, storage):
        await _journal(storage, "field-notes", _row())
        await storage.switch_database("petritype-server")

        await tools.review(storage)

        assert storage.current_database == "petritype-server"

    async def test_this_graph_answers_from_this_graph_after_a_sweep(self, storage):
        """The borrow gives the connection back before the next read, or the
        near answer would come from whichever graph was counted last."""
        await _journal(storage, "field-notes", _row(), _row(), _row())
        await storage.record_decision(_row())

        result, _ = await tools.review(storage)

        assert result["decisions_scanned"] == 1
        assert len(result["decisions"]) == 1

    async def test_asking_about_a_graph_that_does_not_exist_does_not_create_it(
        self, storage
    ):
        """A locator that manufactured the graphs it was asked about would turn
        one review into a namespace full of empty databases."""
        before = set(await storage.list_databases())

        counts = await storage.count_decisions_by_graph(["no-such-graph"])

        assert counts == {}, "a graph that does not exist must be omitted, not zero"
        assert set(await storage.list_databases()) == before

    async def test_a_graph_that_cannot_be_read_is_named_rather_than_dropped(
        self, storage
    ):
        """Omitted from the counts, listed in `unreadable`. A graph deleted
        between the listing and the counting is not a graph holding nothing."""
        await _journal(storage, "field-notes", _row())
        counts = await storage.count_decisions_by_graph(["field-notes", "vanished"])

        assert counts == {"field-notes": 1}


# --- End to end, through the boundary that takes the turn ---


@asynccontextmanager
async def _lifespan_with(storage) -> AsyncIterator[dict]:
    yield {
        "storage": storage,
        "embedding_provider": MockEmbeddingProvider(model_id="mock-embed", dimension=8),
        "config": ServerConfig(storage_backend="memory", embedding_provider="mock"),
        "event_bus": None,
        "viz_session": None,
        "viz_hub_url": None,
        "retrievals": new_record_log(),
    }


@pytest.fixture(params=["memory", "surrealdb"])
async def server_on(request) -> AsyncIterator[tuple[FastMCP, object]]:
    if request.param == "memory":
        storage = InMemoryStorage()
    else:
        storage = SurrealDBStorage(url="mem://")
        await storage.connect()

    original = epimemer_mcp._lifespan
    epimemer_mcp._lifespan = lambda s: _lifespan_with(storage)
    async with _lifespan_with(storage) as ctx:
        epimemer_mcp._lifespan_result = ctx
        yield epimemer_mcp, storage
        epimemer_mcp._lifespan_result = None
    epimemer_mcp._lifespan = original

    if request.param == "surrealdb":
        await storage.close()


def _result(raw) -> dict:
    return json.loads(raw.content[0].text)["result"]


class TestTheTurnTheSweepNeeds:
    """To move the active graph you must exclude the calls using it, and you
    cannot do that while being one — `moving()` inside `using()` raises rather
    than waiting for itself (#16). So a read that borrows the connection has to
    declare itself a mover at the boundary.

    On the in-memory backend the sweep is a dict lookup and borrows nothing, so
    this passes there whether or not the declaration exists. That asymmetry is
    exactly why the fixture runs both.
    """

    async def test_review_declares_itself_a_mover(self):
        assert "epimemer.review" in MOVES_THE_GRAPH

    async def test_a_review_through_the_server_sweeps_without_raising(self, server_on):
        server, storage = server_on
        await _journal(storage, "field-notes", _row(), _row())

        result = _result(await server.call_tool(
            "review", {"expected_graph": storage.current_database}
        ))

        assert "error" not in result, result
        assert result["elsewhere"]["total"] == 2

    async def test_the_sweep_leaves_the_server_where_it_found_it(self, server_on):
        """A borrow that failed to give the connection back would send the
        *next* tool call to another graph — the wrong-graph incident's
        mechanism, manufactured inside the server."""
        server, storage = server_on
        home = storage.current_database
        await _journal(storage, "field-notes", _row())

        await server.call_tool("review", {"expected_graph": home})
        stats = _result(await server.call_tool("graph_stats", {"expected_graph": home}))

        assert storage.current_database == home
        assert "error" not in stats, stats
