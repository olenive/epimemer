"""A malformed date given to `add_timepoint` comes back as an error response.

The three dates `add_timepoint` takes are parsed inside the call the boundary
runs (`_run_with_timeout` in `mcp/server.py`), the way `create_timeline` parses
its reference time. A string that is not an ISO datetime then answers with
`{"error": ...}` and `_meta`, like every other failed call, rather than raising
out of the tool.
"""

import json
from types import SimpleNamespace

import pytest

from epimemer.core.types import Timeline
from epimemer.mcp.config import ServerConfig
from epimemer.mcp.retrieval_records import new_record_log
from epimemer.mcp.server import memory_add_timepoint, new_connection_state
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


@pytest.mark.parametrize(
    "dates",
    [
        {"start": "not a date"},
        {"start": "1897-05-01", "end": "not a date"},
        {"recurrence_id": "rec-easter", "occurrence_start": "not a date"},
    ],
    ids=["start", "end", "occurrence_start"],
)
async def test_a_malformed_date_answers_with_an_error(ctx, dates):
    text = await memory_add_timepoint(
        timeline_id="tl-parish", ctx=ctx, expected_graph=GRAPH, **dates
    )

    payload = json.loads(text)
    assert "not a date" in payload["error"]
    assert "latency_ms" in payload["_meta"]
    timeline = await ctx.lifespan_context["storage"].get_timeline("tl-parish")
    assert timeline.timepoints == []
