"""The record is written once, at the choke point every tool passes through.

`_run_with_timeout` holds the tool name, the complete result dict, the meta and
the latency, and is the last thing to touch a response before `_build_response`
serializes it. Wrapping each node-returning tool instead would be six
insertions that drift — and the census of which six was wrong twice
(RETRIEVAL_PROVENANCE.md §2).
"""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastmcp import FastMCP

from epimemer.core.types import BASE_METACONTEXT_ID, Metacontext
from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.mcp.config import ServerConfig
from epimemer.mcp.retrieval_records import new_record_log, records_of
from epimemer.mcp.server import mcp as epimemer_mcp
from epimemer.storage.memory import InMemoryStorage
from epimemer.visualization.event_bus import create_event_bus
from epimemer.visualization.events import RetrievalRecorded
from epimemer.visualization.ring import RETRIEVAL_RING_CAPACITY


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


def _lifespan_for(*, viz_host: str = "127.0.0.1"):
    bus = create_event_bus()
    log = new_record_log()

    @asynccontextmanager
    async def _test_lifespan(server: FastMCP) -> AsyncIterator[dict]:
        yield {
            "storage": _graph_with_the_real(),
            "embedding_provider": MockEmbeddingProvider(model_id="mock-embed", dimension=8),
            "config": ServerConfig(
                storage_backend="memory",
                embedding_provider="mock",
                viz_host=viz_host,
            ),
            "event_bus": bus,
            "viz_session": None,
            "viz_hub_url": None,
            "retrievals": log,
        }

    return _test_lifespan, bus, log


@asynccontextmanager
async def _running(*, viz_host: str = "127.0.0.1"):
    lifespan, bus, log = _lifespan_for(viz_host=viz_host)
    original = epimemer_mcp._lifespan
    epimemer_mcp._lifespan = lifespan
    async with lifespan(epimemer_mcp) as ctx:
        epimemer_mcp._lifespan_result = ctx
        published: list[RetrievalRecorded] = []
        bus.subscribe(RetrievalRecorded, handler=lambda e: published.append(e))
        try:
            yield epimemer_mcp, log, published
        finally:
            epimemer_mcp._lifespan_result = None
            epimemer_mcp._lifespan = original


def _parse(result) -> dict:
    return json.loads(result.content[0].text)


async def _seed(server: FastMCP) -> list[str]:
    seg = _parse(
        await server.call_tool(
            "segment",
            {"expected_graph": "default", "content": "The deployment rollback failed on Tuesday."},
        )
    )["result"]
    await server.call_tool(
        "store_decomposition",
        {
            "expected_graph": "default",
            "metacontext_id": "the-real",
            "document_id": seg["document_id"],
            "segments": [
                {
                    "segment_id": seg["segments"][0]["segment_id"],
                    "facts": ["The deployment rollback failed on Tuesday"],
                    "topics": ["Deployment"],
                }
            ],
        },
    )
    found = _parse(
        await server.call_tool(
            "find_nodes",
            {"expected_graph": "default", "sourced_from": seg["document_id"], "limit": 50},
        )
    )["result"]
    return [n["id"] for n in found["nodes"]]


class TestOneInsertionCoversEveryTool:
    async def test_a_search_is_recorded_with_what_it_returned(self):
        async with _running() as (server, log, _):
            node_ids = await _seed(server)
            before = len(records_of(log))

            result = await server.call_tool(
                "search", {"expected_graph": "default", "query": "deployment", "k": 5}
            )

            records = records_of(log)
            assert len(records) == before + 1
            record = records[-1]
            assert record.tool == "epimemer.search"
            assert record.declared is True
            assert {n.node_id for n in record.retrieved_nodes} <= set(node_ids)
            assert record.response_text == result.content[0].text

    async def test_a_tool_that_never_declared_still_gets_a_record(self):
        """§2, corrected: the *record* is by construction; only the ids are by
        declaration. A tool that forgets produces a record marked undeclared,
        not no record at all."""
        async with _running() as (server, log, _):
            await _seed(server)

            await server.call_tool("graph_stats", {"expected_graph": "default"})

            record = records_of(log)[-1]
            assert record.tool == "epimemer.graph_stats"
            assert record.declared is False

    async def test_the_query_is_the_arguments_when_there_is_no_query_text(self):
        async with _running() as (server, log, _):
            await _seed(server)

            await server.call_tool(
                "search", {"expected_graph": "default", "query": "rollback", "k": 3}
            )

            assert "rollback" in records_of(log)[-1].query

    async def test_a_failed_call_writes_no_record(self):
        """There is no response to record. The selector lists what the agent
        was handed, and an error was not that."""
        async with _running() as (server, log, _):
            await _seed(server)
            before = len(records_of(log))

            await server.call_tool(
                "query_graph", {"expected_graph": "default", "node_id": "not-a-node"}
            )

            assert len(records_of(log)) == before

    async def test_records_ring_is_bounded_in_the_session_too(self):
        async with _running() as (server, log, _):
            await _seed(server)
            for _ in range(RETRIEVAL_RING_CAPACITY + 3):
                await server.call_tool("graph_stats", {"expected_graph": "default"})

            assert len(records_of(log)) == RETRIEVAL_RING_CAPACITY


class TestMirroring:
    """§3.2 revised: records mirror to the hub so they survive session death —
    the "open the dashboard after noticing" case this feature calls normal."""

    async def test_each_record_is_published_for_the_hub_to_mirror(self):
        async with _running() as (server, log, published):
            await _seed(server)
            published.clear()

            await server.call_tool(
                "search", {"expected_graph": "default", "query": "deployment", "k": 5}
            )

            assert len(published) == 1
            assert published[0].record["tool"] == "epimemer.search"
            assert published[0].record["response_text"] != ""

    async def test_payloads_stay_session_side_on_nonloopback_bind(self):
        """§3.2's guard. The sensitivity argument is kept where it is real: a
        hub anyone can reach holds structural metadata only — which record,
        which tool, which graph, which ids — and no query text or payload. The
        session still holds the whole thing for the `retrievals` RPC.
        """
        async with _running(viz_host="0.0.0.0") as (server, log, published):
            await _seed(server)
            published.clear()

            await server.call_tool(
                "search", {"expected_graph": "default", "query": "deployment", "k": 5}
            )

            mirrored = published[0].record
            assert mirrored["query"] == ""
            assert mirrored["response_text"] == ""
            # ...but the ids survive, or the selector and focus mode have
            # nothing to work with.
            assert mirrored["retrieved"]

            held = records_of(log)[-1]
            assert held.query != ""
            assert held.response_text != ""

    async def test_the_guard_is_the_bind_not_the_tool(self):
        """Every record is mirrored — deciding which ones are "real" retrievals
        would be a second census, drifting like the first (§3, amended)."""
        async with _running() as (server, log, published):
            await _seed(server)
            published.clear()

            await server.call_tool("graph_stats", {"expected_graph": "default"})

            assert [e.record["tool"] for e in published] == ["epimemer.graph_stats"]
