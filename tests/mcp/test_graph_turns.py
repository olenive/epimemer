"""One tool call is one logical operation, and the graph holds still for it.

`storage/active_graph.py` has the primitive and `tests/storage/test_active_graph.py`
has its semantics. This is about the boundary: which turn each tool call takes,
and that the ingest a `use_graph` was batched alongside cannot end up split
across two graphs.

The boundary matters because the guard is useless at any finer grain — a move
only has to land between two of the storage calls one tool makes, so a turn
taken per query would leave the hole it exists to close.
"""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from fastmcp import FastMCP

from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.mcp.config import ServerConfig
from epimemer.mcp.retrieval_records import new_record_log
from epimemer.mcp.server import MOVES_THE_GRAPH, _graph_turn
from epimemer.mcp.server import mcp as epimemer_mcp
from epimemer.core.types import BASE_METACONTEXT_ID, Metacontext
from epimemer.storage.memory import InMemoryStorage

def _graph_with_the_real() -> InMemoryStorage:
    """An in-memory graph somebody has set up.

    Since the frame requirement a frame is required at ingest and `the-real` is an ordinary
    metacontext, created once like any other. A server fixture without it would
    make every test here start by creating a frame, which tests the fixture.
    """
    store = InMemoryStorage()
    store._graphs[store._database].metacontexts[BASE_METACONTEXT_ID] = Metacontext(
        id=BASE_METACONTEXT_ID,
        content="The Real",
        description="Claims about the real world.",
    )
    return store


@pytest.fixture
def deps():
    return {
        "storage": _graph_with_the_real(),
        "config": ServerConfig(storage_backend="memory", embedding_provider="mock"),
    }


async def _settle(ticks: int = 8) -> None:
    for _ in range(ticks):
        await asyncio.sleep(0)


class TestWhichTurnACallTakes:
    async def test_an_ordinary_tool_does_not_exclude_another(self, deps):
        entered = asyncio.Event()

        async def call(name: str, hold: asyncio.Event | None):
            async with _graph_turn(deps, name, waits_for_user=False):
                entered.set()
                if hold is not None:
                    await hold.wait()

        release = asyncio.Event()
        first = asyncio.create_task(call("epimemer.search", release))
        await entered.wait()

        await asyncio.wait_for(call("epimemer.graph_stats", None), timeout=1)

        release.set()
        await first

    async def test_use_graph_waits_for_the_calls_in_flight(self, deps):
        inside, release = asyncio.Event(), asyncio.Event()

        async def ordinary():
            async with _graph_turn(deps, "epimemer.search", waits_for_user=False):
                inside.set()
                await release.wait()

        async def switcher():
            async with _graph_turn(deps, "epimemer.use_graph", waits_for_user=False):
                return "switched"

        held = asyncio.create_task(ordinary())
        await inside.wait()
        move = asyncio.create_task(switcher())

        await _settle()
        assert not move.done(), "use_graph moved the graph under a live call"

        release.set()
        await held
        assert await asyncio.wait_for(move, timeout=1) == "switched"

    async def test_a_call_waiting_on_a_person_takes_no_turn(self, deps):
        """`claim_agent` blocks until somebody answers an elicitation. A turn held
        across that would stall every snapshot behind a prompt nobody has read,
        turning *the dashboard is seconds stale* into *the dashboard is down*.

        The residue is real and accepted: a snapshot borrow landing inside that
        window can still redirect that one call's write.
        """
        inside, release = asyncio.Event(), asyncio.Event()

        async def waiting_on_a_human():
            async with _graph_turn(deps, "epimemer.claim_agent", waits_for_user=True):
                inside.set()
                await release.wait()

        held = asyncio.create_task(waiting_on_a_human())
        await inside.wait()

        async with deps["storage"].graph_guard.moving():
            pass

        release.set()
        await held

    async def test_every_named_mover_is_a_tool_that_exists(self):
        """A typo here fails open — the tool would quietly take a user's turn and
        move the graph anyway, which is the bug rather than an error."""
        registered = {f"epimemer.{tool.name}" for tool in await epimemer_mcp.list_tools()}

        assert MOVES_THE_GRAPH <= registered, MOVES_THE_GRAPH - registered


# --- End to end, through the server the client actually talks to ---


def _suspending(provider):
    """A mock provider that actually gives up the loop.

    Without this the end-to-end test proves nothing and passes either way: with
    in-memory storage and a hash-based embedder, **every await completes without
    suspending**, so `asyncio.gather` runs the ingest to completion before the
    switch starts and there is no race to lose. A test whose subject cannot
    occur is worse than no test — it reports green for the wrong reason.
    """
    real = provider.embed

    async def embed(texts: list[str]) -> list[list[float]]:
        await asyncio.sleep(0)
        return await real(texts)

    provider.embed = embed
    return provider


@asynccontextmanager
async def _lifespan_with(storage: InMemoryStorage) -> AsyncIterator[dict]:
    yield {
        "storage": storage,
        "embedding_provider": _suspending(
            MockEmbeddingProvider(model_id="mock-embed", dimension=8)
        ),
        "config": ServerConfig(storage_backend="memory", embedding_provider="mock"),
        "event_bus": None,
        "viz_session": None,
        "viz_hub_url": None,
        "retrievals": new_record_log(),
    }


@pytest.fixture
async def server_on() -> AsyncIterator[tuple[FastMCP, InMemoryStorage]]:
    storage = _graph_with_the_real()
    original = epimemer_mcp._lifespan
    epimemer_mcp._lifespan = lambda s: _lifespan_with(storage)
    async with _lifespan_with(storage) as ctx:
        epimemer_mcp._lifespan_result = ctx
        yield epimemer_mcp, storage
        epimemer_mcp._lifespan_result = None
    epimemer_mcp._lifespan = original


def _result(raw) -> dict:
    return json.loads(raw.content[0].text)["result"]


class TestABatchedSwitchDoesNotSplitAnIngest:
    """The failure this closes, at the size an agent would actually produce it:
    an ingest and a `use_graph` issued in one block, which is what Claude Code's
    own instructions ask for whenever two calls look independent."""

    async def test_the_ingest_lands_whole_in_the_graph_it_started_in(self, server_on):
        server, storage = server_on
        started_on = storage.current_database

        seg = _result(await server.call_tool("segment", {"expected_graph": "default", 
            "content": "The first paragraph.\n\nThe second paragraph.",
        }))
        decomposition = [
            {
                "segment_id": s["segment_id"],
                "topics": [f"topic {i}"],
                "facts": [f"fact {i}"],
                "inferences": [f"inference {i}"],
            }
            for i, s in enumerate(seg["segments"])
        ]

        ingest, switch = await asyncio.gather(
            server.call_tool("store_decomposition", {"expected_graph": "default", 
        "metacontext_id": "the-real",
                "document_id": seg["document_id"],
                "segments": decomposition,
            }),
            server.call_tool("use_graph", {"name": "elsewhere", "confirm": True}),
        )

        assert _result(switch)["status"] in {"switched", "created"}
        assert storage.current_database == "elsewhere"

        created = _result(ingest)["nodes_created"]
        expected = created["topics"] + created["facts"] + created["inferences"]
        assert expected > 0

        landed_here = await storage.query_nodes()
        assert landed_here == [], "part of the ingest followed the switch"

        await storage.switch_database(started_on)
        landed_there = await storage.query_nodes()
        assert len(landed_there) == expected

    async def test_the_switch_still_happens(self, server_on):
        """The guard delays a move; it must never drop one — a `use_graph` that
        silently did nothing would be the wrong-graph incident with the graphs
        the other way round."""
        server, storage = server_on

        await asyncio.gather(
            server.call_tool("graph_stats", {"expected_graph": "default"}),
            server.call_tool("use_graph", {"name": "elsewhere", "confirm": True}),
            server.call_tool("list_graphs", {}),
        )

        assert storage.current_database == "elsewhere"
