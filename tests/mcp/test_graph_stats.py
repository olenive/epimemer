"""`graph_stats` surfaces reflection pressure alongside the graph's size.

The auto-reflect counter is persistent per graph, but until now it was only
visible in `store_decomposition` responses — you could only learn how close the
graph was to a suggested reflect by storing something. `graph_stats` is the
question "what is in this graph and what does it need?", so the counter, the
effective threshold, and whether reflect is due belong in its answer.

The *default* threshold is process config, so it is passed in rather than read
from storage; these tests pin that the tool reports the threshold it was given
and that the MCP wrapper hands it the configured one. Per-graph overrides of
that default are covered in `test_configure_reflection.py`.
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
from epimemer.mcp.tools import graph_stats
from epimemer.core.types import BASE_METACONTEXT_ID, Metacontext
from epimemer.storage.memory import InMemoryStorage

def _graph_with_the_real() -> InMemoryStorage:
    """An in-memory graph somebody has set up.

    Since #76 a frame is required at ingest and `the-real` is an ordinary
    metacontext, created once like any other frame.
    """
    store = InMemoryStorage()
    store._graphs[store._database].metacontexts[BASE_METACONTEXT_ID] = Metacontext(
        id=BASE_METACONTEXT_ID,
        content="The Real",
        description="Claims about the real world.",
    )
    return store


class TestReflectCounterInStats:

    async def test_reports_reflect_counter(self, storage):
        await storage.bump_reflect_counter()
        await storage.bump_reflect_counter()

        result, _ = await graph_stats(storage, default_reflect_threshold=5)

        assert result["stores_since_reflect"] == 2
        assert result["reflect_threshold"] == 5
        assert result["reflect_suggested"] is False

    async def test_fresh_graph_reports_zero(self, storage):
        result, _ = await graph_stats(storage, default_reflect_threshold=10)

        assert result["stores_since_reflect"] == 0
        assert result["reflect_suggested"] is False

    async def test_suggests_reflect_from_the_threshold_onwards(self, storage):
        """The boundary is inclusive, matching store_decomposition's `>=`.

        Two readouts disagreeing about whether reflect is due would be worse
        than either being wrong on its own.
        """
        await storage.bump_reflect_counter()
        await storage.bump_reflect_counter()

        at_threshold, _ = await graph_stats(storage, default_reflect_threshold=2)
        assert at_threshold["reflect_suggested"] is True

        await storage.bump_reflect_counter()
        past_threshold, _ = await graph_stats(storage, default_reflect_threshold=2)
        assert past_threshold["stores_since_reflect"] == 3
        assert past_threshold["reflect_suggested"] is True

    async def test_suggestion_clears_after_a_reset(self, storage):
        await storage.bump_reflect_counter()
        await storage.bump_reflect_counter()
        await storage.reset_reflect_counter()

        result, _ = await graph_stats(storage, default_reflect_threshold=2)

        assert result["stores_since_reflect"] == 0
        assert result["reflect_suggested"] is False

    async def test_reflect_keys_are_always_present(self, storage):
        """Even on an empty graph — an absent key is indistinguishable from
        `false` to a caller, and this readout exists to be checked."""
        result, _ = await graph_stats(storage, default_reflect_threshold=10)

        assert result["empty"] is True
        assert {"stores_since_reflect", "reflect_threshold", "reflect_suggested"} <= (
            result.keys()
        )


# --- The MCP wrapper must hand the tool the configured threshold ---


@asynccontextmanager
async def _session(storage, config: ServerConfig) -> AsyncIterator[FastMCP]:
    """One MCP server session over the given storage — a stand-in for a client
    connection."""
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


@pytest.fixture
def config() -> ServerConfig:
    return ServerConfig(storage_backend="memory", embedding_provider="mock")


async def test_tool_reports_the_configured_threshold(config):
    storage = _graph_with_the_real()
    config = config.model_copy(update={"reflect_threshold": 3})

    async with _session(storage, config) as server:
        seg = _result(await server.call_tool("segment", {"expected_graph": "default", "content": "Cats are mammals."}))
        await server.call_tool("store_decomposition", {"expected_graph": "default", 
        "metacontext_id": "the-real",
            "document_id": seg["document_id"],
            "segments": [
                {"segment_id": s["segment_id"], "topics": ["Cats"]}
                for s in seg["segments"]
            ],
        })

        stats = _result(await server.call_tool("graph_stats", {"expected_graph": "default"}))

    assert stats["stores_since_reflect"] == 1
    assert stats["reflect_threshold"] == 3
    assert stats["reflect_suggested"] is False
