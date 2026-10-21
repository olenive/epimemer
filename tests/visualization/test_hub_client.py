"""Tests for the session-side hub client.

Each test runs a throwaway ``websockets`` server as a stand-in hub, so we can
observe exactly what the client sends (Register / PublishEvent / RpcResponse)
and drive RPC requests back at it.
"""

import asyncio
import json
from types import SimpleNamespace

from websockets.asyncio.server import serve

from epimemer.core.advisories import AdvisoryAction, AdvisoryKind, WarningPolicy
from epimemer.core.types import EmbeddingRecord, Fact, Topic
from epimemer.storage.memory import InMemoryStorage
from epimemer.storage.protocol import WarningOverrides
from epimemer.visualization import hub_client
from epimemer.visualization.event_bus import InProcessEventBus
from epimemer.visualization.events import NodeStatusChanged
from epimemer.visualization.hub_client import start_hub_client
from epimemer.visualization.protocol import (
    INGEST_MAX_MESSAGE_BYTES,
    VIZ_SCHEMA_VERSION,
    RpcRequest,
    RpcResponse,
    SessionInfo,
)


def _info() -> SessionInfo:
    return SessionInfo(session_id="s1", pid=1, backend="memory", active_graph="default")


async def _make_fake_hub(port: int = 0):
    received: asyncio.Queue = asyncio.Queue()
    state: dict = {"ws": None, "connections": 0}

    async def handler(ws) -> None:
        state["connections"] += 1
        state["ws"] = ws
        try:
            async for raw in ws:
                await received.put(json.loads(raw))
        except Exception:
            pass

    server = await serve(handler, "127.0.0.1", port)
    bound_port = server.sockets[0].getsockname()[1]
    return SimpleNamespace(
        server=server,
        received=received,
        state=state,
        port=bound_port,
        url=f"ws://127.0.0.1:{bound_port}/ingest",
    )


async def _close(hub) -> None:
    hub.server.close()
    await hub.server.wait_closed()


async def test_client_registers_forwards_event_and_answers_rpc():
    hub = await _make_fake_hub()
    storage = InMemoryStorage()
    bus = InProcessEventBus()
    stop = await start_hub_client(bus, storage, _info(), hub.url)
    try:
        first = await asyncio.wait_for(hub.received.get(), timeout=3)
        assert first["type"] == "register"
        assert first["info"]["session_id"] == "s1"
        assert first["info"]["backend"] == "memory"
        assert first["schema_version"] == VIZ_SCHEMA_VERSION

        # A bus event is forwarded verbatim as a PublishEvent.
        await bus.publish(
            NodeStatusChanged(
                graph="g1", node_id="n1", old_status="active", new_status="superseded"
            )
        )
        fwd = await asyncio.wait_for(hub.received.get(), timeout=3)
        assert fwd["type"] == "event"
        assert fwd["payload"]["event_type"] == "node_status_changed"
        assert fwd["payload"]["node_id"] == "n1"

        # A list_graphs RPC is answered from this process's own storage.
        await hub.state["ws"].send(
            RpcRequest(request_id="r1", method="list_graphs").model_dump_json()
        )
        resp = await asyncio.wait_for(hub.received.get(), timeout=3)
        assert resp["type"] == "rpc_response"
        assert resp["request_id"] == "r1"
        assert resp["result"]["backend"] == "memory"
        assert isinstance(resp["result"]["graphs"], list)
        assert "active_graph" in resp["result"]
    finally:
        await stop()
        await _close(hub)


async def test_the_warnings_rpc_answers_without_writing_anything():
    """`EVENT_LOG.md` §12.4: the panel is read-only, so this returns the
    settings `configure_warnings` reports and touches nothing.

    The process default travels as a value, the way every other setting does,
    and the graph's own overrides are laid over it here.
    """
    hub = await _make_fake_hub()
    storage = InMemoryStorage()
    stop = await start_hub_client(
        InProcessEventBus(),
        storage,
        _info(),
        hub.url,
        default_warning_policy=WarningPolicy(surface=False),
    )
    try:
        await asyncio.wait_for(hub.received.get(), timeout=3)  # the register

        await hub.state["ws"].send(RpcRequest(request_id="r1", method="warnings").model_dump_json())
        resp = await asyncio.wait_for(hub.received.get(), timeout=3)

        assert resp["result"]["graph"] == storage.current_database
        assert resp["result"]["surface"] is False
        assert resp["result"]["actions"]["cross_metacontext"] == "proceed"
        assert resp["result"]["overridden"] == {}
        # Read-only: the graph has still never been configured.
        assert await storage.get_warning_overrides() == WarningOverrides()
    finally:
        await stop()
        await _close(hub)


async def test_the_warnings_rpc_reports_what_this_graph_set_for_itself():
    hub = await _make_fake_hub()
    storage = InMemoryStorage()
    await storage.set_warning_overrides(
        WarningOverrides(by_kind={AdvisoryKind.CROSS_METACONTEXT: AdvisoryAction.FLAG})
    )
    stop = await start_hub_client(InProcessEventBus(), storage, _info(), hub.url)
    try:
        await asyncio.wait_for(hub.received.get(), timeout=3)  # the register

        await hub.state["ws"].send(RpcRequest(request_id="r1", method="warnings").model_dump_json())
        resp = await asyncio.wait_for(hub.received.get(), timeout=3)

        assert resp["result"]["actions"]["cross_metacontext"] == "flag"
        assert resp["result"]["overridden"]["by_kind"] == {"cross_metacontext": "flag"}
    finally:
        await stop()
        await _close(hub)


async def test_the_embeddings_rpc_answers_from_this_sessions_storage():
    hub = await _make_fake_hub()
    storage = InMemoryStorage()
    topic = Topic(content="a topic", source_id="s1")
    fact = Fact(content="a fact", source_id="s1")
    for node in (topic, fact):
        await storage.store_node(node)
        await storage.store_embedding(
            EmbeddingRecord(item_id=node.id, model_id="model-a", vector=[0.5, 0.25])
        )
    stop = await start_hub_client(InProcessEventBus(), storage, _info(), hub.url)
    try:
        await asyncio.wait_for(hub.received.get(), timeout=3)  # the register

        await hub.state["ws"].send(
            RpcRequest(
                request_id="r1",
                method="embeddings",
                params={"graph": storage.current_database, "node_type": "topic"},
            ).model_dump_json()
        )
        resp = await asyncio.wait_for(hub.received.get(), timeout=3)

        assert resp["request_id"] == "r1"
        assert resp["error"] is None
        assert resp["result"]["schema_version"] == VIZ_SCHEMA_VERSION
        assert resp["result"]["models"] == {"model-a": 2}
        assert resp["result"]["embeddings"] == [
            {
                "node_id": topic.id,
                "node_type": "topic",
                "model_id": "model-a",
                "vector": [0.5, 0.25],
            }
        ]
    finally:
        await stop()
        await _close(hub)


async def test_the_embeddings_rpc_reports_an_unknown_type_as_an_error():
    """The hub refuses one first, but the session is what answers, so it
    refuses too rather than answering as if the graph had no vectors."""
    hub = await _make_fake_hub()
    storage = InMemoryStorage()
    stop = await start_hub_client(InProcessEventBus(), storage, _info(), hub.url)
    try:
        await asyncio.wait_for(hub.received.get(), timeout=3)  # the register

        await hub.state["ws"].send(
            RpcRequest(
                request_id="r1",
                method="embeddings",
                params={"graph": storage.current_database, "node_type": "topics"},
            ).model_dump_json()
        )
        resp = await asyncio.wait_for(hub.received.get(), timeout=3)

        assert resp["result"] is None
        assert "topics" in resp["error"]
    finally:
        await stop()
        await _close(hub)


async def test_a_snapshot_over_the_ceiling_is_an_error_and_the_socket_carries_on(monkeypatch):
    """The snapshot reaches the cliff later than the vectors do, and meets the
    same guard. The ceiling is lowered rather than a graph built to 16 MiB:
    the guard reads it at call time for exactly this."""
    hub = await _make_fake_hub()
    storage = InMemoryStorage()
    await storage.store_node(Topic(content="x" * 2000, source_id="s1"))
    stop = await start_hub_client(InProcessEventBus(), storage, _info(), hub.url)
    try:
        await asyncio.wait_for(hub.received.get(), timeout=3)  # the register
        monkeypatch.setattr(
            hub_client, "INGEST_MAX_MESSAGE_BYTES", hub_client._ANSWER_HEADROOM_BYTES + 1000
        )

        await hub.state["ws"].send(
            RpcRequest(
                request_id="r1", method="snapshot", params={"graph": storage.current_database}
            ).model_dump_json()
        )
        refused = await asyncio.wait_for(hub.received.get(), timeout=3)

        assert refused["request_id"] == "r1"
        assert refused["result"] is None
        assert refused["error"].startswith("the snapshot answer would be ")
        assert refused["error"].endswith(
            f"this graph is too large for one snapshot at schema version {VIZ_SCHEMA_VERSION}"
        )

        monkeypatch.setattr(hub_client, "INGEST_MAX_MESSAGE_BYTES", INGEST_MAX_MESSAGE_BYTES)
        await hub.state["ws"].send(
            RpcRequest(request_id="r2", method="list_graphs").model_dump_json()
        )
        answered = await asyncio.wait_for(hub.received.get(), timeout=3)

        assert answered["request_id"] == "r2"
        assert answered["error"] is None
        assert answered["result"]["backend"] == "memory"
    finally:
        await stop()
        await _close(hub)


def test_the_error_that_replaces_an_answer_always_fits_in_the_headroom():
    """Every method, at sizes no socket will see, with the longest node type."""
    requests = [
        RpcRequest(request_id="f" * 32, method=method, params={"graph": "g", "node_type": None})
        for method in ("list_graphs", "snapshot", "retrievals", "warnings", "embeddings")
    ]
    requests.append(
        RpcRequest(
            request_id="f" * 32,
            method="embeddings",
            params={"graph": "g", "node_type": "inference"},
        )
    )
    huge = 10**18
    for req in requests:
        reason = hub_client._too_large(req, huge, huge)
        sent = RpcResponse(request_id=req.request_id, error=reason).model_dump_json()

        assert len(sent.encode()) < hub_client._ANSWER_HEADROOM_BYTES


def test_an_answer_that_fits_is_sent_unchanged():
    req = RpcRequest(request_id="r1", method="list_graphs")

    sent = json.loads(hub_client._reply(req, {"graphs": ["default"]}))

    assert sent == {
        "type": "rpc_response",
        "request_id": "r1",
        "result": {"graphs": ["default"]},
        "error": None,
    }


def test_the_answer_is_measured_in_bytes_rather_than_characters(monkeypatch):
    """Node content goes out unescaped, so a graph written in Cyrillic takes
    two bytes a character on the wire."""
    req = RpcRequest(request_id="r1", method="snapshot", params={"graph": "g"})
    result = {"nodes": [{"content": "Ленинград" * 100}]}
    characters = len(RpcResponse(request_id="r1", result=result).model_dump_json())
    monkeypatch.setattr(
        hub_client, "INGEST_MAX_MESSAGE_BYTES", hub_client._ANSWER_HEADROOM_BYTES + characters
    )

    sent = json.loads(hub_client._reply(req, result))

    assert sent["result"] is None
    assert "too large for one snapshot" in sent["error"]


async def test_client_reconnects_and_reregisters():
    hub1 = await _make_fake_hub()
    port = hub1.port
    stop = await start_hub_client(InProcessEventBus(), InMemoryStorage(), _info(), hub1.url)
    try:
        first = await asyncio.wait_for(hub1.received.get(), timeout=3)
        assert first["type"] == "register"

        # Drop the hub, then bring a fresh one up on the same port.
        await _close(hub1)
        await asyncio.sleep(0.2)
        hub2 = await _make_fake_hub(port=port)
        try:
            # Client should reconnect (capped backoff) and register again.
            reg = await asyncio.wait_for(hub2.received.get(), timeout=8)
            assert reg["type"] == "register"
            assert reg["info"]["session_id"] == "s1"
        finally:
            await _close(hub2)
    finally:
        await stop()


async def test_unreachable_hub_logs_to_stderr_once(capsys):
    # Nothing listens on port 1 → connection refused on every attempt.
    stop = await start_hub_client(
        InProcessEventBus(), InMemoryStorage(), _info(), "ws://127.0.0.1:1/ingest"
    )
    # Long enough for at least two retry attempts (backoff 1s then 2s).
    await asyncio.sleep(2.5)
    await stop()

    err = capsys.readouterr().err
    # The one-time stderr note is emitted exactly once; later retries are debug.
    assert err.count("[epimemer] viz hub unreachable") == 1
