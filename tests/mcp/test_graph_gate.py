"""Which graph a call meant, checked at the boundary (ISSUES the mandatory `expected_graph`).

The active graph is **process state**. A client reconnect starts a fresh process
and lands on whatever configuration resolves to, so a session that spent an hour
in one graph silently reopens somewhere else. The call that follows is correct in
every respect except which graph it ran against.

`expected_graph` is the agent's side of that, and it is checked in exactly one
place — `_run_with_timeout`, inside the turn that holds the graph still.

**This file is written before the parameter is mandatory**, which is deliberate:
the gate has to be provably working *before* absence becomes a refusal, or the
flip that makes it mandatory is the first thing that ever exercises it.

Two of these are oracles rather than enumerations, which is the point:

- `TestEveryContentToolIsGated` calls **every registered tool** rather than a
  list somebody maintains, so a tool added next year is covered on the day it is
  registered. The list-of-seven that the first version of this guard was built
  from is exactly what missed reads.
- `TestTheReconnectThatCausedThis` replays the sequence that cost 61 nodes.
"""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from fastmcp import FastMCP

from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.mcp.config import ServerConfig
from epimemer.mcp.retrieval_records import new_record_log
from epimemer.mcp.server import NAMES_ITS_OWN_GRAPH
from epimemer.mcp.server import mcp as epimemer_mcp
from epimemer.storage.memory import InMemoryStorage

# Read from the server rather than restated here. A second copy would be free to
# disagree with the one the gate consults, and the disagreement would look like
# a passing test — which is the whole failure mode this file exists to close,
# one level up.
EXEMPT: frozenset[str] = frozenset(name.removeprefix("epimemer.") for name in NAMES_ITS_OWN_GRAPH)

ISO = "2026-08-23T12:00:00+00:00"


def _dummy(name: str, spec: dict):
    """A schema-shaped value for one required argument.

    Junk is **correct** here and not a shortcut: the gate fires before the tool
    body runs, so a call that reaches the refusal proves the ordering. A recipe
    that made each tool do real work would prove the gate fires *somewhere*,
    which is a weaker claim and a list to maintain.
    """
    kind = spec.get("type")
    if isinstance(kind, list):
        kind = next((k for k in kind if k != "null"), "string")
    if kind == "string":
        if spec.get("format") == "date-time" or any(
            token in name for token in ("_at", "since", "until", "as_of")
        ):
            return ISO
        return "x"
    return {"integer": 1, "number": 1.0, "boolean": False, "array": [], "object": {}}.get(kind, "x")


def _required_args(tool) -> dict:
    schema = tool.parameters
    properties = schema.get("properties", {})
    return {
        name: _dummy(name, properties[name])
        for name in schema.get("required", [])
        if name in properties
    }


@asynccontextmanager
async def _lifespan(server: FastMCP) -> AsyncIterator[dict]:
    yield _deps(InMemoryStorage())


def _deps(storage: InMemoryStorage) -> dict:
    return {
        "storage": storage,
        "embedding_provider": MockEmbeddingProvider(model_id="mock-embed", dimension=8),
        "config": ServerConfig(storage_backend="memory", embedding_provider="mock"),
        "event_bus": None,
        "viz_session": None,
        "viz_hub_url": None,
        "retrievals": new_record_log(),
    }


@pytest.fixture
async def server():
    original = epimemer_mcp._lifespan
    epimemer_mcp._lifespan = _lifespan
    async with _lifespan(epimemer_mcp) as ctx:
        epimemer_mcp._lifespan_result = ctx
        yield epimemer_mcp, ctx
        epimemer_mcp._lifespan_result = None
    epimemer_mcp._lifespan = original


def _parse(result) -> dict:
    return json.loads(result.content[0].text)


async def _call(server, tool: str, **kwargs) -> dict:
    """The tool's own payload, unwrapped from the `{result, _meta}` envelope."""
    return _parse(await server.call_tool(tool, kwargs))["result"]


class TestTheGateItself:
    async def test_a_mismatch_refuses(self, server):
        srv, _ = server

        result = await _call(srv, "segment", content="A report.", expected_graph="field-notes")

        assert "refused" in result
        assert result["expected_graph"] == "field-notes"
        assert result["active_graph"] == "default"

    async def test_nothing_was_written(self, server):
        srv, deps = server

        await _call(srv, "segment", content="A report.", expected_graph="field-notes")

        assert await deps["storage"].query_nodes() == []
        assert await deps["storage"].get_segments_for_document("any") == []

    async def test_the_refusal_reaches_the_agent_intact(self, server):
        """The defect the boundary move surfaced, pinned so it cannot come back.

        The gate's whole value is a sentence the agent can act on. Before this,
        the tool's own success summariser ran over the refusal dict, raised
        `KeyError` inside the logger, and the agent received
        `{"error": "'segments'"}` — the recovery instructions gone. It passed
        review because the tests exercised the layer *below* the boundary, where
        no summariser runs.
        """
        srv, _ = server

        result = await _call(srv, "segment", content="A report.", expected_graph="field-notes")

        assert "error" not in result
        assert "use_graph('field-notes')" in result["refused"]
        assert "reconnect" in result["refused"]

    async def test_a_match_proceeds(self, server):
        srv, _ = server

        result = await _call(srv, "segment", content="A report.", expected_graph="default")

        assert "refused" not in result
        assert result["document_id"]

    async def test_omitting_it_refuses(self, server):
        """Mandatory, unconditionally, with no setting either way.

        A per-graph flag would be read from whichever graph the call is
        *actually* in, so landing in the wrong one would switch the guard off in
        exactly the case it exists for. A gate that turned itself on once a
        second graph appeared would change what a working call does, based on
        state the agent never touched.
        """
        srv, deps = server

        result = await _call(srv, "segment", content="A report.")

        assert "refused" in result
        assert result["expected_graph"] is None
        assert result["active_graph"] == "default"
        assert await deps["storage"].query_nodes() == []

    async def test_the_absence_refusal_names_the_active_graph(self, server):
        """The agent has to be able to recover, and it cannot see the reconnect
        that put it here."""
        srv, _ = server

        result = await _call(srv, "segment", content="A report.")

        assert "expected_graph='default'" in result["refused"]

    async def test_it_warns_against_pasting_the_answer_back(self, server):
        """The check is worth something only because the two sides are worked
        out independently. An agent that reads the active graph out of the
        refusal and echoes it has made them agree by construction — which
        cannot be enforced, so it is said."""
        srv, _ = server

        result = await _call(srv, "segment", content="A report.")

        assert "independently" in result["refused"]

    async def test_the_gate_follows_a_switch(self, server):
        srv, _ = server
        await _call(srv, "use_graph", name="elsewhere", confirm=True)

        result = await _call(srv, "segment", content="A report.", expected_graph="elsewhere")

        assert "refused" not in result


class TestEveryContentToolIsGated:
    """An oracle over the registry, not a list.

    The first version of this guard covered three tools, chosen by an argument
    about which writes could land silently. It missed every read — and a
    wrong-graph read is the worse half, because a misfiled write leaves the
    material and its journal row sitting together in the graph that received
    them, while a wrong-graph `search` returns a plausible answer the agent
    reasons from and leaves no artifact at all.
    """

    async def test_every_registered_tool_takes_the_parameter(self, server):
        srv, _ = server

        missing = sorted(
            tool.name
            for tool in await srv.list_tools()
            if tool.name not in EXEMPT
            and "expected_graph" not in tool.parameters.get("properties", {})
        )

        assert missing == [], (
            f"{missing} can be called without saying which graph they mean. "
            "Add `expected_graph` and forward it to `_run_with_timeout`, or add "
            "the tool to EXEMPT with a reason."
        )

    async def test_every_registered_tool_refuses_a_wrong_graph(self, server):
        srv, _ = server

        ungated = []
        for tool in await srv.list_tools():
            if tool.name in EXEMPT:
                continue
            result = await _call(
                srv,
                tool.name,
                **_required_args(tool),
                expected_graph="somewhere-else",
            )
            if result.get("expected_graph") != "somewhere-else":
                ungated.append((tool.name, result))

        assert ungated == [], (
            f"{[name for name, _ in ungated]} ran instead of refusing when the "
            "agent named a graph the server is not on."
        )

    async def test_the_exempt_list_is_exempt_for_a_reason(self, server):
        """A tool must not land here by being forgotten. Each of these takes the
        graph as its subject, so a second way to name one would be two sources
        of truth for one argument."""
        srv, _ = server
        names = {tool.name for tool in await srv.list_tools()}

        assert EXEMPT <= names, (
            "NAMES_ITS_OWN_GRAPH exempts a tool that is not registered — a "
            "renamed tool would be silently ungated"
        )
        for name in EXEMPT:
            tool = next(t for t in await srv.list_tools() if t.name == name)
            assert "expected_graph" not in tool.parameters.get("properties", {})

    async def test_a_refused_call_writes_nothing_whatever_the_tool(self, server):
        """Not one tool at a time: the gate returns before `coro()` is awaited,
        so the tool body never runs at all."""
        srv, deps = server
        storage = deps["storage"]

        for tool in await srv.list_tools():
            if tool.name in EXEMPT:
                continue
            await _call(srv, tool.name, **_required_args(tool), expected_graph="somewhere-else")

        assert await storage.query_nodes() == []
        assert await storage.query_decisions() == []
        assert storage.current_database == "default", (
            "a refused call must not have moved the graph either"
        )


class TestTheReconnectThatCausedThis:
    """The sequence that put 61 nodes of one project into another project's
    graph, replayed.

    The agent's belief is formed by a `use_graph` early in the session. The
    client reconnects; the server comes back on its configured default. Nothing
    tells the agent, because from where it stands nothing happened.
    """

    async def test_a_write_after_a_reconnect_is_refused(self, server):
        srv, deps = server
        await _call(srv, "use_graph", name="field-notes", confirm=True)
        assert deps["storage"].current_database == "field-notes"

        # The reconnect: the server's active graph reverts to configuration and
        # the agent's belief does not.
        await deps["storage"].switch_database("default")

        result = await _call(srv, "segment", content="A project report.", expected_graph="field-notes")

        assert "refused" in result
        assert result["active_graph"] == "default"

    async def test_the_same_write_without_the_parameter_cannot_happen_now(self, server):
        """The incident itself, and it no longer has a path.

        This is the call that put 61 nodes of one project into another's: an
        ingest that named no graph, after a reconnect the agent could not see,
        reporting success in every respect except where it went. There is now no
        way to make it — omitting the parameter refuses before anything runs.
        """
        srv, deps = server
        await _call(srv, "use_graph", name="field-notes", confirm=True)
        await deps["storage"].switch_database("default")

        result = await _call(srv, "segment", content="A project report.")

        assert "refused" in result
        assert "document_id" not in result
        assert await deps["storage"].query_nodes() == []

    async def test_a_read_after_a_reconnect_is_refused_too(self, server):
        """The half the first guard missed. A wrong-graph read returns a
        plausible answer with nothing left behind to find later."""
        srv, deps = server
        await _call(srv, "use_graph", name="field-notes", confirm=True)
        await deps["storage"].switch_database("default")

        result = await _call(srv, "search", query="the project", expected_graph="field-notes")

        assert "refused" in result
        assert "nodes" not in result
