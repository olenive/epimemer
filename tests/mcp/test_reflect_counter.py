"""The auto-reflect signal counts stores *per graph*, not per process.

The counter lives in storage beside the data it describes. That is what makes
the signal usable: a client that reconnects between ingests (a new process every
time) still accumulates towards the threshold, and the count follows a graph
switch instead of leaking across graphs.
"""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest

from fastmcp import FastMCP

from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.mcp.retrieval_records import new_record_log
from epimemer.mcp.config import ServerConfig
from epimemer.mcp.server import mcp as epimemer_mcp
from epimemer.storage.memory import InMemoryStorage


@asynccontextmanager
async def _session(storage, config: ServerConfig) -> AsyncIterator[FastMCP]:
    """One MCP server session over the given storage — a stand-in for a client
    connection. Leaving and re-entering the context models a reconnect: a fresh
    lifespan context over storage that is already there."""
    original = epimemer_mcp._lifespan
    deps = {
        "storage": storage,
        "embedding_provider": MockEmbeddingProvider(model_id="mock-embed", dimension=8),
        "config": config,
        "event_bus": None,
        "viz_session": None,
        "viz_hub_url": None,
        "retrievals": new_record_log(),
    }

    @asynccontextmanager
    async def _lifespan(_server):
        yield deps

    epimemer_mcp._lifespan = _lifespan
    async with _lifespan(epimemer_mcp) as ctx:
        epimemer_mcp._lifespan_result = ctx
        try:
            yield epimemer_mcp
        finally:
            epimemer_mcp._lifespan_result = None
    epimemer_mcp._lifespan = original


def _result(tool_result) -> dict:
    return json.loads(tool_result.content[0].text)["result"]


async def _ingest(server: FastMCP, content: str) -> dict:
    """Run the two-step ingest flow and return the store_decomposition result."""
    seg = _result(await server.call_tool("segment", {"content": content}))
    return _result(await server.call_tool("store_decomposition", {
        "document_id": seg["document_id"],
        "segments": [
            {"segment_id": s["segment_id"], "topics": [f"Topic {s['segment_id']}"]}
            for s in seg["segments"]
        ],
    }))


@pytest.fixture
def config() -> ServerConfig:
    return ServerConfig(storage_backend="memory", embedding_provider="mock")


async def test_reflect_counter_survives_reconnect(config):
    """Two documents stored either side of a reconnect count as two, not one."""
    storage = InMemoryStorage()

    async with _session(storage, config) as server:
        first = await _ingest(server, "Cats are mammals.")
    assert first["stores_since_reflect"] == 1

    async with _session(storage, config) as server:
        second = await _ingest(server, "Dogs are mammals.")
    assert second["stores_since_reflect"] == 2


async def test_reflect_resets_the_counter_and_reports_what_it_cleared(config):
    storage = InMemoryStorage()

    async with _session(storage, config) as server:
        await _ingest(server, "Cats are mammals.")
        await _ingest(server, "Dogs are mammals.")

        reflected = _result(await server.call_tool("reflect", {}))
        assert reflected["stores_since_last_reflect"] == 2

        after = await _ingest(server, "Birds are not mammals.")
        assert after["stores_since_reflect"] == 1


async def test_reflect_suggested_once_the_threshold_is_reached(config):
    storage = InMemoryStorage()
    config = config.model_copy(update={"reflect_threshold": 2})

    async with _session(storage, config) as server:
        first = await _ingest(server, "Cats are mammals.")
        assert "reflect_suggested" not in first
        assert first["reflect_threshold"] == 2

    # The suggestion must survive a reconnect too — that is the whole point.
    async with _session(storage, config) as server:
        second = await _ingest(server, "Dogs are mammals.")
        assert second["reflect_suggested"] is True


async def test_counter_follows_a_graph_switch(config):
    """Each graph counts its own stores; switching does not carry them over."""
    storage = InMemoryStorage()

    async with _session(storage, config) as server:
        await _ingest(server, "Cats are mammals.")
        await _ingest(server, "Dogs are mammals.")

        await server.call_tool("use_graph", {"name": "other", "confirm": True})
        on_other = await _ingest(server, "Sparrows are birds.")
        assert on_other["stores_since_reflect"] == 1

        await server.call_tool("use_graph", {"name": "default", "confirm": True})
        back_home = await _ingest(server, "Foxes are mammals.")
        assert back_home["stores_since_reflect"] == 3
