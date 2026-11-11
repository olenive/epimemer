"""A malformed argument comes back as an error response, whichever tool took it.

A tool parses and checks its arguments inside the call the boundary runs
(`_run_with_timeout` in `mcp/server.py`), so a date that is not ISO, a window
that ends before it starts, or an archive whose `nodes` is not a list answers
with `{"error": ...}` and `_meta`, like every other failed call, rather than
raising out of the tool before the boundary has seen it.
"""

import json
from types import SimpleNamespace

import pytest

from epimemer.core.types import Timeline
from epimemer.mcp.config import ServerConfig
from epimemer.mcp.retrieval_records import new_record_log
from epimemer.mcp.server import (
    memory_query_changes,
    memory_query_timeline,
    memory_restore,
    new_connection_state,
)
from epimemer.storage.memory import InMemoryStorage

GRAPH = "default"


@pytest.fixture
async def ctx() -> SimpleNamespace:
    storage = InMemoryStorage()
    await storage.store_timeline(Timeline(id="tl-parish", name="the parish"))
    deps = {
        "storage": storage,
        "config": ServerConfig(storage_backend="memory", embedding_provider="mock"),
        "event_bus": None,
        "retrievals": new_record_log(),
        "version": "9.9.9",
        "connection_state": new_connection_state(),
        "read_installed_version": lambda: None,
    }
    return SimpleNamespace(lifespan_context=deps)


def _error(text: str) -> str:
    payload = json.loads(text)
    assert "latency_ms" in payload["_meta"]
    return payload["error"]


@pytest.mark.parametrize("argument", ["target", "range_start", "range_end", "next_after"])
async def test_query_timeline_answers_a_malformed_date_with_an_error(ctx, argument):
    text = await memory_query_timeline(
        timeline_id="tl-parish", ctx=ctx, expected_graph=GRAPH, **{argument: "not a date"}
    )

    assert "not a date" in _error(text)


@pytest.mark.parametrize(
    ("windows", "said"),
    [
        ([["not a date", "2026-11-02T00:00:00"]], "not a date"),
        ([["2026-11-02T00:00:00", "2026-11-01T00:00:00"]], "must be before end"),
        ([[]], "index"),
    ],
    ids=["malformed date", "start after end", "empty window"],
)
async def test_query_changes_answers_a_bad_window_with_an_error(ctx, windows, said):
    text = await memory_query_changes(ctx=ctx, windows=windows, expected_graph=GRAPH)

    assert said in _error(text)


async def test_restore_answers_an_archive_with_no_node_list_with_an_error(ctx):
    text = await memory_restore(ctx=ctx, archive_data={"nodes": 7}, expected_graph=GRAPH)

    assert _error(text)
