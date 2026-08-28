"""Per-graph reflection threshold override.

The auto-reflect threshold is process config, which makes it the wrong knob for
"this graph is a scratchpad, nag me sooner" — an env var applies to every graph
the server touches and vanishes on restart. The override lives beside the
counter it governs: per graph, in storage, surviving reconnects.

Clearing is `threshold=None`, which returns the graph to the configured default
rather than pinning today's default as a value — otherwise changing
EPIMEMER_REFLECT_THRESHOLD later would silently not apply to graphs that had
once been overridden.
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
from epimemer.mcp.tools import configure_reflection, effective_reflect_threshold, graph_stats
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


class TestOverrideStorage:
    """The storage protocol half — both backends, per the parity rule."""

    async def test_absent_by_default(self, storage):
        assert await storage.get_reflect_threshold_override() is None

    async def test_set_then_read_back(self, storage):
        await storage.set_reflect_threshold_override(3)

        assert await storage.get_reflect_threshold_override() == 3

    async def test_overwrites_a_previous_override(self, storage):
        await storage.set_reflect_threshold_override(3)
        await storage.set_reflect_threshold_override(25)

        assert await storage.get_reflect_threshold_override() == 25

    async def test_cleared_by_none(self, storage):
        await storage.set_reflect_threshold_override(3)
        await storage.set_reflect_threshold_override(None)

        assert await storage.get_reflect_threshold_override() is None

    async def test_does_not_disturb_the_counter(self, storage):
        """The override and the count share a record; they must not share a value."""
        await storage.bump_reflect_counter()
        await storage.bump_reflect_counter()

        await storage.set_reflect_threshold_override(7)
        assert await storage.get_reflect_counter() == 2

        await storage.set_reflect_threshold_override(None)
        assert await storage.get_reflect_counter() == 2

    async def test_belongs_to_the_graph_that_set_it(self, storage):
        # The backends' starting graph is named differently (`default` vs
        # `main`), so return to whichever this one began on.
        home = storage.current_database
        await storage.set_reflect_threshold_override(3)

        await storage.switch_database("other")
        assert await storage.get_reflect_threshold_override() is None
        await storage.set_reflect_threshold_override(50)

        await storage.switch_database(home)
        assert await storage.get_reflect_threshold_override() == 3

        await storage.switch_database("other")
        assert await storage.get_reflect_threshold_override() == 50


class TestEffectiveThreshold:

    async def test_falls_back_to_the_configured_default(self, storage):
        assert await effective_reflect_threshold(storage, 10) == 10

    async def test_override_wins(self, storage):
        await storage.set_reflect_threshold_override(3)

        assert await effective_reflect_threshold(storage, 10) == 3

    async def test_clearing_restores_the_default(self, storage):
        await storage.set_reflect_threshold_override(3)
        await storage.set_reflect_threshold_override(None)

        assert await effective_reflect_threshold(storage, 10) == 10

    async def test_a_later_default_change_reaches_a_cleared_graph(self, storage):
        """Clearing must not freeze today's default into the graph."""
        await storage.set_reflect_threshold_override(3)
        await storage.set_reflect_threshold_override(None)

        assert await effective_reflect_threshold(storage, 40) == 40


class TestConfigureReflectionTool:

    async def test_sets_the_override_and_reports_the_effect(self, storage):
        result, _ = await configure_reflection(
            storage, threshold=3, default_threshold=10
        )

        assert result["reflect_threshold"] == 3
        assert result["overridden"] is True
        assert await storage.get_reflect_threshold_override() == 3

    async def test_clears_the_override(self, storage):
        await storage.set_reflect_threshold_override(3)

        result, _ = await configure_reflection(
            storage, threshold=None, default_threshold=10
        )

        assert result["reflect_threshold"] == 10
        assert result["overridden"] is False
        assert await storage.get_reflect_threshold_override() is None

    async def test_reports_the_current_count_alongside(self, storage):
        """Setting a threshold is a decision about the count, so show it."""
        await storage.bump_reflect_counter()
        await storage.bump_reflect_counter()

        result, _ = await configure_reflection(
            storage, threshold=2, default_threshold=10
        )

        assert result["stores_since_reflect"] == 2
        assert result["reflect_suggested"] is True

    async def test_rejects_a_threshold_below_one(self, storage):
        """Zero would mean every store suggests a reflect, which is not a
        threshold; a negative one is nonsense. Reject rather than clamp, so a
        mistyped value is visible instead of silently reinterpreted."""
        with pytest.raises(ValueError, match="at least 1"):
            await configure_reflection(storage, threshold=0, default_threshold=10)

        with pytest.raises(ValueError, match="at least 1"):
            await configure_reflection(storage, threshold=-5, default_threshold=10)

        assert await storage.get_reflect_threshold_override() is None

    async def test_does_not_reset_the_counter(self, storage):
        """Raising the threshold is 'not yet'; it must not discard the signal."""
        await storage.bump_reflect_counter()
        await storage.bump_reflect_counter()

        await configure_reflection(storage, threshold=50, default_threshold=10)

        assert await storage.get_reflect_counter() == 2


class TestOverrideReachesTheReadouts:

    async def test_graph_stats_reports_the_override(self, storage):
        await storage.set_reflect_threshold_override(2)
        await storage.bump_reflect_counter()
        await storage.bump_reflect_counter()

        result, _ = await graph_stats(storage, default_reflect_threshold=10)

        assert result["reflect_threshold"] == 2
        assert result["reflect_threshold_overridden"] is True
        assert result["reflect_suggested"] is True

    async def test_graph_stats_marks_an_unoverridden_threshold(self, storage):
        result, _ = await graph_stats(storage, default_reflect_threshold=10)

        assert result["reflect_threshold"] == 10
        assert result["reflect_threshold_overridden"] is False


# --- Through the MCP server: persistence and the ingest path ---


@asynccontextmanager
async def _session(storage, config: ServerConfig) -> AsyncIterator[FastMCP]:
    """One MCP server session over the given storage. Leaving and re-entering
    models a reconnect: a fresh lifespan context over storage already there."""
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
    seg = _result(await server.call_tool("segment", {"expected_graph": "default", "content": content}))
    return _result(await server.call_tool("store_decomposition", {"expected_graph": "default", 
        "metacontext_id": "the-real",
        "document_id": seg["document_id"],
        "segments": [
            {"segment_id": s["segment_id"], "topics": [f"Topic {s['segment_id']}"]}
            for s in seg["segments"]
        ],
    }))


@pytest.fixture
def config() -> ServerConfig:
    return ServerConfig(storage_backend="memory", embedding_provider="mock")


async def test_configure_reflection_persists_override(config):
    """The override outlives the process that set it — the point of storing it."""
    storage = _graph_with_the_real()

    async with _session(storage, config) as server:
        set_result = _result(
            await server.call_tool("configure_reflection", {"expected_graph": "default", "threshold": 2})
        )
        assert set_result["reflect_threshold"] == 2

    async with _session(storage, config) as server:
        stats = _result(await server.call_tool("graph_stats", {"expected_graph": "default"}))

    assert stats["reflect_threshold"] == 2
    assert stats["reflect_threshold_overridden"] is True


async def test_store_decomposition_honours_the_override(config):
    """The suggestion an agent actually acts on comes from the ingest response,
    so the override has to reach it and not just graph_stats."""
    storage = _graph_with_the_real()

    async with _session(storage, config) as server:
        await server.call_tool("configure_reflection", {"expected_graph": "default", "threshold": 2})

        first = await _ingest(server, "Cats are mammals.")
        assert first["reflect_threshold"] == 2
        assert "reflect_suggested" not in first

        second = await _ingest(server, "Dogs are mammals.")
        assert second["reflect_suggested"] is True


async def test_clearing_returns_to_the_configured_default(config):
    storage = _graph_with_the_real()
    config = config.model_copy(update={"reflect_threshold": 7})

    async with _session(storage, config) as server:
        await server.call_tool("configure_reflection", {"expected_graph": "default", "threshold": 2})
        cleared = _result(await server.call_tool("configure_reflection", {"expected_graph": "default"}))

    assert cleared["reflect_threshold"] == 7
    assert cleared["overridden"] is False
