"""Tests for the visualization hub (relay + session registry).

The hub is inherently multi-connection, so these run it under a real uvicorn
server on a random port *inside the test event loop* — ingest sockets, browser
sockets, and HTTP RPC calls are then genuinely concurrent, which the synchronous
Starlette TestClient cannot express (an in-flight HTTP RPC must be answered by a
message the test drives onto the ingest socket).
"""

import asyncio
import json
import socket
import urllib.error
import urllib.request
from types import SimpleNamespace

import pytest
import uvicorn
import websockets

from epimemer.core.types import EmbeddingRecord, Fact, Topic
from epimemer.storage.memory import InMemoryStorage
from epimemer.visualization import hub as hubmod
from epimemer.visualization.event_bus import InProcessEventBus
from epimemer.visualization.hub import create_hub_app
from epimemer.visualization.hub_client import start_hub_client
from epimemer.visualization.protocol import (
    INGEST_MAX_MESSAGE_BYTES,
    UNVERSIONED_SCHEMA,
    VIZ_SCHEMA_VERSION,
    PublishEvent,
    Register,
    RpcResponse,
    SessionInfo,
)


def _session(session_id: str, *, backend: str = "surrealdb", graph: str = "memory") -> SessionInfo:
    return SessionInfo(session_id=session_id, pid=1234, backend=backend, active_graph=graph)


def _node_event(graph: str = "memory") -> dict:
    return {
        "category": "graph",
        "event_type": "node_stored",
        "graph": graph,
        "node": {"node_id": "n1", "content": "hi"},
    }


async def _http_get(addr: str, path: str, timeout: float = 10.0) -> tuple[int, str]:
    """Blocking GET run off-loop, so it can be awaited concurrently with ws work."""

    def _do() -> tuple[int, str]:
        try:
            with urllib.request.urlopen(f"http://{addr}{path}", timeout=timeout) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    return await asyncio.to_thread(_do)


async def _wait(pred, timeout: float = 3.0) -> None:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if await pred():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition not met within timeout")


@pytest.fixture
async def hub():
    app = create_hub_app()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]

    # The ceiling `run()` gives uvicorn, so an oversized answer meets the
    # limit it would meet in a real hub.
    server = uvicorn.Server(
        uvicorn.Config(
            app, log_level="warning", lifespan="off", ws_max_size=INGEST_MAX_MESSAGE_BYTES
        )
    )
    task = asyncio.create_task(server.serve(sockets=[sock]))
    for _ in range(300):
        if server.started:
            break
        await asyncio.sleep(0.01)
    assert server.started, "hub server did not start"

    addr = f"127.0.0.1:{port}"
    try:
        yield SimpleNamespace(addr=addr, ingest=f"ws://{addr}/ingest", ws=f"ws://{addr}/ws")
    finally:
        server.should_exit = True
        await task


async def _register(ws, info: SessionInfo) -> None:
    await ws.send(Register(info=info).model_dump_json())


async def _sessions(addr: str) -> list[dict]:
    _, body = await _http_get(addr, "/api/sessions")
    return json.loads(body)


# --- registry ---


async def test_register_lists_session(hub):
    async with websockets.connect(hub.ingest) as ws:
        await _register(ws, _session("s-a"))
        await _wait(lambda: _pred_len(hub.addr, 1))
        sessions = await _sessions(hub.addr)
        assert sessions[0]["session_id"] == "s-a"
        assert sessions[0]["backend"] == "surrealdb"
        assert sessions[0]["active_graph"] == "memory"
        assert sessions[0]["connected"] is True


async def _pred_len(addr: str, n: int) -> bool:
    return len(await _sessions(addr)) == n


# --- the contract's version ---


def test_a_register_names_the_contract_it_speaks():
    sent = json.loads(Register(info=_session("s-a")).model_dump_json())

    assert sent["schema_version"] == VIZ_SCHEMA_VERSION


async def test_health_names_the_contract_the_hub_speaks(hub):
    status, body = await _http_get(hub.addr, "/api/health")

    assert status == 200
    assert json.loads(body)["schema_version"] == VIZ_SCHEMA_VERSION


async def test_each_session_is_listed_with_the_contract_it_speaks(hub):
    async with websockets.connect(hub.ingest) as ws:
        await _register(ws, _session("s-a"))
        await _wait(lambda: _pred_len(hub.addr, 1))

        [listed] = await _sessions(hub.addr)

    assert listed["schema_version"] == VIZ_SCHEMA_VERSION


async def test_a_session_whose_register_predates_the_field_is_listed_as_0(hub):
    """Listed as a number, so a client compares it like any other version,
    rather than left out or filled in with the version the hub speaks."""
    older = {"type": "register", "info": _session("s-old").model_dump(mode="json")}
    async with websockets.connect(hub.ingest) as ws:
        await ws.send(json.dumps(older))
        await _wait(lambda: _pred_len(hub.addr, 1))

        [listed] = await _sessions(hub.addr)

    assert UNVERSIONED_SCHEMA == 0
    assert listed["schema_version"] == UNVERSIONED_SCHEMA


async def test_a_re_register_updates_the_listed_version(hub):
    async with websockets.connect(hub.ingest) as ws:
        older = {"type": "register", "info": _session("s-a").model_dump(mode="json")}
        await ws.send(json.dumps(older))
        await _wait(lambda: _pred_len(hub.addr, 1))
        await _register(ws, _session("s-a", graph="other"))

        async def _current() -> bool:
            [listed] = await _sessions(hub.addr)
            return listed["schema_version"] == VIZ_SCHEMA_VERSION

        await _wait(_current)


async def test_a_register_that_does_not_parse_is_refused(hub):
    """The same refusal as a first message that is not a Register at all."""
    malformed = {
        "type": "register",
        "info": _session("s-a").model_dump(mode="json"),
        "schema_version": "one",
    }
    async with websockets.connect(hub.ingest) as ws:
        await ws.send(json.dumps(malformed))
        with pytest.raises(websockets.ConnectionClosed) as closed:
            await asyncio.wait_for(ws.recv(), timeout=3)

    assert closed.value.rcvd.code == 1002
    assert await _sessions(hub.addr) == []


async def test_disconnect_marks_session_not_connected(hub):
    async with websockets.connect(hub.ingest) as ws:
        await _register(ws, _session("s-a"))
        await _wait(lambda: _pred_len(hub.addr, 1))
    # socket closed on exit; session stays listed (grace period) but disconnected

    async def _disconnected() -> bool:
        sessions = await _sessions(hub.addr)
        return len(sessions) == 1 and sessions[0]["connected"] is False

    await _wait(_disconnected)


# --- event fan-out ---


async def test_event_fans_out_with_session_id_and_seq(hub):
    async with websockets.connect(hub.ingest) as sess:
        await _register(sess, _session("s-a"))
        await _wait(lambda: _pred_len(hub.addr, 1))

        async with websockets.connect(hub.ws) as browser:
            await browser.send(json.dumps({"subscribe": {"session": "s-a", "graphs": None}}))
            await asyncio.sleep(0.1)  # let the subscription register

            await sess.send(PublishEvent(payload=_node_event()).model_dump_json())
            msg = json.loads(await asyncio.wait_for(browser.recv(), timeout=3))

            assert msg["event_type"] == "node_stored"
            assert msg["session_id"] == "s-a"
            assert msg["seq"] == 1


async def test_browser_only_receives_its_subscribed_session(hub):
    async with websockets.connect(hub.ingest) as sess_a, websockets.connect(hub.ingest) as sess_b:
        await _register(sess_a, _session("s-a"))
        await _register(sess_b, _session("s-b"))
        await _wait(lambda: _pred_len(hub.addr, 2))

        async with websockets.connect(hub.ws) as browser:
            await browser.send(json.dumps({"subscribe": {"session": "s-a", "graphs": None}}))
            await asyncio.sleep(0.1)

            # An event from the *other* session must not reach this browser.
            await sess_b.send(PublishEvent(payload=_node_event()).model_dump_json())
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(browser.recv(), timeout=0.5)

            # ...but an event from the subscribed session does.
            await sess_a.send(PublishEvent(payload=_node_event()).model_dump_json())
            msg = json.loads(await asyncio.wait_for(browser.recv(), timeout=3))
            assert msg["session_id"] == "s-a"


# --- the log ring (EVENT_LOG.md §4.2) ---


def _action(action_id: str, *, graph: str = "memory", verb: str = "stored") -> dict:
    return {
        "category": "graph",
        "event_type": "graph_action_recorded",
        "graph": graph,
        "action_id": action_id,
        "verb": verb,
        "subjects": ["n1"],
        "counts": {"nodes": 1},
        "summary": f"stored ({action_id})",
    }


def _advisory(action_id: str, *, graph: str = "memory", tool: str = "record_variant") -> dict:
    return {
        "category": "graph",
        "event_type": "advisory_raised",
        "graph": graph,
        "action_id": action_id,
        "tool": tool,
        "kind": "same_metacontext_variant",
        "message": "these facts share a metacontext",
        "subjects": ["n1", "n2"],
        "detail": {},
        "action": "proceed",
        "surfaced": True,
        "notify_user": False,
        "judged_by": None,
    }


async def _subscribe(browser, session: str, graphs=None) -> None:
    await browser.send(json.dumps({"subscribe": {"session": session, "graphs": graphs}}))


async def _drain(browser, n: int, timeout: float = 3.0) -> list[dict]:
    return [json.loads(await asyncio.wait_for(browser.recv(), timeout=timeout)) for _ in range(n)]


async def test_ring_evicts_oldest_and_backfills_on_subscribe(hub, monkeypatch):
    """§4.2: bounded and replayable.

    A browser opened *after* the agent did something is the normal case — you
    open the dashboard because you noticed. A live-only stream is empty exactly
    then.
    """
    monkeypatch.setattr(hubmod, "LOG_RING_CAPACITY", 3)
    async with websockets.connect(hub.ingest) as sess:
        await _register(sess, _session("s-a"))
        await _wait(lambda: _pred_len(hub.addr, 1))

        for i in range(5):
            await sess.send(PublishEvent(payload=_action(f"{i:03d}")).model_dump_json())
        await asyncio.sleep(0.1)

        async with websockets.connect(hub.ws) as browser:
            await _subscribe(browser, "s-a")
            replayed = await _drain(browser, 3)

    assert [m["action_id"] for m in replayed] == ["002", "003", "004"]
    assert all(m["session_id"] == "s-a" for m in replayed)


async def test_the_ring_keeps_only_the_coarse_stream(hub):
    """It selects on `event_type`, not on category — every graph event carries
    `category: graph`, and remembering all of them is the firehose §3 refused."""
    async with websockets.connect(hub.ingest) as sess:
        await _register(sess, _session("s-a"))
        await _wait(lambda: _pred_len(hub.addr, 1))

        await sess.send(PublishEvent(payload=_node_event()).model_dump_json())
        await sess.send(PublishEvent(payload=_action("001")).model_dump_json())
        await asyncio.sleep(0.1)

        async with websockets.connect(hub.ws) as browser:
            await _subscribe(browser, "s-a")
            replayed = await _drain(browser, 1)
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(browser.recv(), timeout=0.3)

    assert [m["event_type"] for m in replayed] == ["graph_action_recorded"]


async def test_warnings_and_acts_replay_as_one_sequence(hub):
    """`EVENT_LOG.md` §12.3: the ring keeps both, because replay has to
    reproduce arrival order and a warning reads as a line after what it was
    about."""
    async with websockets.connect(hub.ingest) as sess:
        await _register(sess, _session("s-a"))
        await _wait(lambda: _pred_len(hub.addr, 1))

        await sess.send(PublishEvent(payload=_action("001")).model_dump_json())
        await sess.send(PublishEvent(payload=_advisory("002")).model_dump_json())
        await sess.send(PublishEvent(payload=_action("003")).model_dump_json())
        await asyncio.sleep(0.1)

        async with websockets.connect(hub.ws) as browser:
            await _subscribe(browser, "s-a")
            replayed = await _drain(browser, 3)

    assert [m["action_id"] for m in replayed] == ["001", "002", "003"]
    assert [m["event_type"] for m in replayed] == [
        "graph_action_recorded",
        "advisory_raised",
        "graph_action_recorded",
    ]


async def test_a_warning_relays_live_to_a_subscribed_browser(hub):
    async with websockets.connect(hub.ingest) as sess:
        await _register(sess, _session("s-a"))
        await _wait(lambda: _pred_len(hub.addr, 1))

        async with websockets.connect(hub.ws) as browser:
            await _subscribe(browser, "s-a")
            await sess.send(PublishEvent(payload=_advisory("001")).model_dump_json())
            (relayed,) = await _drain(browser, 1)

    assert relayed["event_type"] == "advisory_raised"
    assert relayed["session_id"] == "s-a"


async def test_a_warning_stays_inside_the_viewed_graph(hub):
    """The same rule acts follow: a warning about graph A must not reach a
    browser watching graph B, live or replayed."""
    async with websockets.connect(hub.ingest) as sess:
        await _register(sess, _session("s-a"))
        await _wait(lambda: _pred_len(hub.addr, 1))

        await sess.send(PublishEvent(payload=_advisory("001", graph="alpha")).model_dump_json())
        await sess.send(PublishEvent(payload=_advisory("002", graph="beta")).model_dump_json())
        await asyncio.sleep(0.1)

        async with websockets.connect(hub.ws) as browser:
            await _subscribe(browser, "s-a", graphs=["beta"])
            replayed = await _drain(browser, 1)
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(browser.recv(), timeout=0.3)

    assert [m["action_id"] for m in replayed] == ["002"]


async def test_the_warning_settings_rpc_reaches_the_session(hub):
    """Read-only, and answered where the graph's overrides live (`EVENT_LOG.md` §12.4)."""
    async with websockets.connect(hub.ingest) as sess:
        await _register(sess, _session("s-a"))
        await _wait(lambda: _pred_len(hub.addr, 1))

        pending = asyncio.create_task(_http_get(hub.addr, "/api/warnings?session=s-a"))
        req = json.loads(await asyncio.wait_for(sess.recv(), timeout=3))
        assert req["method"] == "warnings"
        canned = {
            "graph": "memory",
            "surface": True,
            "actions": {"cross_metacontext": "proceed"},
            "overridden": {},
        }
        await sess.send(RpcResponse(request_id=req["request_id"], result=canned).model_dump_json())

        status, body = await asyncio.wait_for(pending, timeout=3)

    assert status == 200
    assert json.loads(body) == canned


async def test_the_warning_settings_route_needs_a_session(hub):
    status, body = await _http_get(hub.addr, "/api/warnings")

    assert status == 400
    assert "session" in json.loads(body)["error"]


async def test_backfill_stays_inside_the_viewed_graph(hub):
    """`VISUALISATION.md`, *Rules every panel follows*: an entry from graph A
    must never highlight into graph B."""
    async with websockets.connect(hub.ingest) as sess:
        await _register(sess, _session("s-a"))
        await _wait(lambda: _pred_len(hub.addr, 1))

        await sess.send(PublishEvent(payload=_action("001", graph="alpha")).model_dump_json())
        await sess.send(PublishEvent(payload=_action("002", graph="beta")).model_dump_json())
        await asyncio.sleep(0.1)

        async with websockets.connect(hub.ws) as browser:
            await _subscribe(browser, "s-a", graphs=["beta"])
            replayed = await _drain(browser, 1)
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(browser.recv(), timeout=0.3)

    assert [m["action_id"] for m in replayed] == ["002"]


async def test_action_ids_are_monotonic_across_browser_reconnects(hub):
    """§4.1: `seq` cannot carry this.

    It is assigned per browser connection at send time and restarts at 0 on
    reconnect, so two views of the same act disagree about its number. The
    `action_id` is assigned by the session that emitted the act, so it does not
    — which is what lets a log dedup a replay against what it already holds.
    A `seq`-based implementation passes every other test in this file.
    """
    async with websockets.connect(hub.ingest) as sess:
        await _register(sess, _session("s-a"))
        await _wait(lambda: _pred_len(hub.addr, 1))

        async with websockets.connect(hub.ws) as first:
            await _subscribe(first, "s-a")
            await asyncio.sleep(0.1)
            for i in range(2):
                await sess.send(PublishEvent(payload=_action(f"{i:03d}")).model_dump_json())
            live = await _drain(first, 2)

        await sess.send(PublishEvent(payload=_action("002")).model_dump_json())
        await asyncio.sleep(0.1)

        async with websockets.connect(hub.ws) as second:
            await _subscribe(second, "s-a")
            replayed = await _drain(second, 3)

    assert [m["seq"] for m in live] == [1, 2]
    assert [m["seq"] for m in replayed] == [1, 2, 3]  # seq restarted
    ids = [m["action_id"] for m in replayed]
    assert ids == sorted(ids) == ["000", "001", "002"]  # the ids did not
    assert [m["action_id"] for m in live] == ids[:2]


# --- the retrieval-record mirror (RETRIEVAL_PROVENANCE.md §3.2) ---


def _record(record_id: str, *, graph: str = "memory", payload: str = "{}") -> dict:
    return {
        "category": "graph",
        "event_type": "retrieval_recorded",
        "graph": graph,
        "record": {
            "record_id": record_id,
            "tool": "epimemer.search",
            "query": "deployment",
            "graph": graph,
            "retrieved": [{"node_id": "n1", "provenance": "vector", "score": 0.82}],
            "response_text": payload,
            "truncated": False,
        },
    }


async def test_records_survive_session_death_in_the_hub_ring(hub):
    """§3.2 revised. Session-side-only placement made records unreachable the
    moment the MCP process exited — the hub keeps disconnected sessions but
    raises on RPC to them, which is exactly the "open the dashboard after
    noticing" case this feature calls normal.
    """
    async with websockets.connect(hub.ingest) as sess:
        await _register(sess, _session("s-a"))
        await _wait(lambda: _pred_len(hub.addr, 1))
        await sess.send(PublishEvent(payload=_record("001")).model_dump_json())
        await asyncio.sleep(0.1)
    # The session is gone. The hub still lists it, and the RPC to it would now
    # raise — but the ring is here.

    async def _disconnected() -> bool:
        return (await _sessions(hub.addr))[0]["connected"] is False

    await _wait(_disconnected)

    async with websockets.connect(hub.ws) as browser:
        await _subscribe(browser, "s-a")
        replayed = await _drain(browser, 1)

    assert [m["record"]["record_id"] for m in replayed] == ["001"]


async def test_records_never_mix_across_sessions(hub):
    """Per-session keying is a contract, not an accident of placement.

    The identity unit is the MCP process, one per conversation; a browser that
    saw another session's records would be reading another conversation's.
    """
    async with websockets.connect(hub.ingest) as a, websockets.connect(hub.ingest) as b:
        await _register(a, _session("s-a"))
        await _register(b, _session("s-b"))
        await _wait(lambda: _pred_len(hub.addr, 2))

        await a.send(PublishEvent(payload=_record("a-1")).model_dump_json())
        await b.send(PublishEvent(payload=_record("b-1")).model_dump_json())
        await a.send(PublishEvent(payload=_record("a-2")).model_dump_json())
        await asyncio.sleep(0.1)

        async with websockets.connect(hub.ws) as browser:
            await _subscribe(browser, "s-a")
            replayed = await _drain(browser, 2)
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(browser.recv(), timeout=0.3)

    assert [m["record"]["record_id"] for m in replayed] == ["a-1", "a-2"]


async def test_the_two_rings_are_bounded_separately(hub, monkeypatch):
    """Records carry payloads and acts do not, so they are sized by different
    things — one capacity for both would be wrong twice."""
    monkeypatch.setattr(hubmod, "RETRIEVAL_RING_CAPACITY", 2)
    monkeypatch.setattr(hubmod, "LOG_RING_CAPACITY", 4)
    async with websockets.connect(hub.ingest) as sess:
        await _register(sess, _session("s-a"))
        await _wait(lambda: _pred_len(hub.addr, 1))

        for i in range(4):
            await sess.send(PublishEvent(payload=_record(f"r{i}")).model_dump_json())
            await sess.send(PublishEvent(payload=_action(f"a{i}")).model_dump_json())
        await asyncio.sleep(0.1)

        async with websockets.connect(hub.ws) as browser:
            await _subscribe(browser, "s-a")
            replayed = await _drain(browser, 6)

    kinds = [m["event_type"] for m in replayed]
    assert kinds.count("graph_action_recorded") == 4
    assert kinds.count("retrieval_recorded") == 2


async def test_the_retrievals_rpc_reaches_the_session(hub):
    """The payload route: served by the process that produced it, which is what
    still works when the hub's mirror is guarded down to metadata."""
    async with websockets.connect(hub.ingest) as sess:
        await _register(sess, _session("s-a"))
        await _wait(lambda: _pred_len(hub.addr, 1))

        pending = asyncio.create_task(_http_get(hub.addr, "/api/retrievals?session=s-a"))
        req = json.loads(await asyncio.wait_for(sess.recv(), timeout=3))
        assert req["method"] == "retrievals"
        await sess.send(
            RpcResponse(
                request_id=req["request_id"],
                result={"records": [{"record_id": "001", "response_text": "{}"}]},
            ).model_dump_json()
        )

        status, body = await asyncio.wait_for(pending, timeout=3)

    assert status == 200
    assert json.loads(body)["records"][0]["record_id"] == "001"


# --- RPC round-trip ---


async def test_snapshot_rpc_round_trip(hub):
    async with websockets.connect(hub.ingest) as sess:
        await _register(sess, _session("s-a"))
        await _wait(lambda: _pred_len(hub.addr, 1))

        get_task = asyncio.create_task(
            _http_get(hub.addr, "/api/snapshot?session=s-a&graph=memory")
        )
        req = json.loads(await asyncio.wait_for(sess.recv(), timeout=3))
        assert req["type"] == "rpc_request"
        assert req["method"] == "snapshot"
        assert req["params"]["graph"] == "memory"

        canned = {"graph": "memory", "nodes": [], "edges": []}
        await sess.send(RpcResponse(request_id=req["request_id"], result=canned).model_dump_json())

        status, body = await get_task
        assert status == 200
        assert json.loads(body) == canned


async def test_embeddings_rpc_round_trip(hub):
    async with websockets.connect(hub.ingest) as sess:
        await _register(sess, _session("s-a"))
        await _wait(lambda: _pred_len(hub.addr, 1))

        get_task = asyncio.create_task(
            _http_get(hub.addr, "/api/embeddings?session=s-a&graph=memory&node_type=topic")
        )
        req = json.loads(await asyncio.wait_for(sess.recv(), timeout=3))
        assert req["method"] == "embeddings"
        assert req["params"] == {"graph": "memory", "node_type": "topic"}

        canned = {
            "graph": "memory",
            "schema_version": VIZ_SCHEMA_VERSION,
            "node_type": "topic",
            "models": {"m": 2},
            "embeddings": [
                {"node_id": "t1", "node_type": "topic", "model_id": "m", "vector": [0.5, 0.25]}
            ],
        }
        await sess.send(RpcResponse(request_id=req["request_id"], result=canned).model_dump_json())

        status, body = await get_task
        assert status == 200
        assert json.loads(body) == canned


async def test_embeddings_without_a_type_asks_for_every_type(hub):
    async with websockets.connect(hub.ingest) as sess:
        await _register(sess, _session("s-a"))
        await _wait(lambda: _pred_len(hub.addr, 1))

        get_task = asyncio.create_task(
            _http_get(hub.addr, "/api/embeddings?session=s-a&graph=memory")
        )
        req = json.loads(await asyncio.wait_for(sess.recv(), timeout=3))
        assert req["params"] == {"graph": "memory", "node_type": None}
        await sess.send(RpcResponse(request_id=req["request_id"], result={}).model_dump_json())

        status, _ = await get_task
        assert status == 200


@pytest.mark.parametrize("query", ["", "?session=s-a", "?graph=memory", "?session=&graph=memory"])
async def test_embeddings_needs_a_session_and_a_graph(hub, query):
    status, body = await _http_get(hub.addr, f"/api/embeddings{query}")

    assert status == 400
    assert json.loads(body)["error"] == "Missing 'session' or 'graph' query parameter"


@pytest.mark.parametrize("node_type", ["topics", "Topic", "edge", ""])
async def test_embeddings_refuses_a_type_it_does_not_know(hub, node_type):
    """Refused at the hub, before any session is asked, with the values that
    would have worked."""
    status, body = await _http_get(
        hub.addr, f"/api/embeddings?session=s-a&graph=memory&node_type={node_type}"
    )

    assert status == 400
    error = json.loads(body)["error"]
    assert "node_type" in error
    assert all(allowed in error for allowed in ("topic", "fact", "inference"))


async def test_an_answer_over_the_ceiling_is_an_error_and_the_session_stays(hub):
    """An answer the socket cannot carry would close it with 1009 and drop the
    session. The session sends an error in its place, which reaches the caller
    as any session error does, and the next request on the same socket works.

    One topic node's vector is given a dimension no model has, so a single
    real answer crosses the real ceiling against the in-memory backend.
    """
    storage = InMemoryStorage()
    topic = Topic(content="a topic", source_id="s1")
    fact = Fact(content="a fact", source_id="s1")
    for node in (topic, fact):
        await storage.store_node(node)
    # 19 bytes a float on the wire, so 900,000 of them is about 17 MB.
    await storage.store_embedding(
        EmbeddingRecord(item_id=topic.id, model_id="m", vector=[0.1234567890123456] * 900_000)
    )
    await storage.store_embedding(EmbeddingRecord(item_id=fact.id, model_id="m", vector=[0.5]))
    info = SessionInfo(session_id="s-big", pid=1, backend="memory", active_graph="default")
    stop = await start_hub_client(InProcessEventBus(), storage, info, hub.ingest)
    try:
        await _wait(lambda: _pred_len(hub.addr, 1))

        status, body = await _http_get(hub.addr, "/api/embeddings?session=s-big&graph=default")

        assert status == 502
        error = json.loads(body)["error"]
        assert error.startswith("the embeddings answer would be ")
        assert f"{INGEST_MAX_MESSAGE_BYTES:,}-byte message ceiling" in error
        assert error.endswith("ask for one node_type: topic, fact or inference")

        [listed] = await _sessions(hub.addr)
        assert listed["connected"] is True
        status, body = await _http_get(
            hub.addr, "/api/embeddings?session=s-big&graph=default&node_type=fact"
        )
        assert status == 200
        assert [e["node_id"] for e in json.loads(body)["embeddings"]] == [fact.id]
    finally:
        await stop()


async def test_rpc_timeout_returns_504(hub, monkeypatch):
    monkeypatch.setattr(hubmod, "RPC_TIMEOUT_SECONDS", 0.3)
    async with websockets.connect(hub.ingest) as sess:
        await _register(sess, _session("s-a"))
        await _wait(lambda: _pred_len(hub.addr, 1))

        # Never answer the RpcRequest → the endpoint must give up with 504.
        status, _ = await _http_get(hub.addr, "/api/graphs?session=s-a")
        assert status == 504


async def test_rpc_unknown_session_returns_404(hub):
    status, _ = await _http_get(hub.addr, "/api/graphs?session=nope")
    assert status == 404


# --- bind-race (run() port handling) ---


async def test_run_exits_0_when_a_healthy_hub_holds_the_port(hub, monkeypatch):
    host, port = hub.addr.split(":")
    monkeypatch.setenv("EPIMEMER_VIZ_HOST", host)
    monkeypatch.setenv("EPIMEMER_VIZ_PORT", port)
    # Port is held by the fixture's healthy hub → lost the spawn race → clean exit.
    # run() is blocking (bind attempt + health probe); off-load it so the event
    # loop keeps driving the fixture's hub to answer the probe.
    assert await asyncio.to_thread(hubmod.run) == 0


async def test_run_exits_1_when_a_stranger_holds_the_port(monkeypatch):
    stranger = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    stranger.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    stranger.bind(("127.0.0.1", 0))
    stranger.listen()
    port = stranger.getsockname()[1]
    try:
        monkeypatch.setenv("EPIMEMER_VIZ_HOST", "127.0.0.1")
        monkeypatch.setenv("EPIMEMER_VIZ_PORT", str(port))
        assert await asyncio.to_thread(hubmod.run) == 1
    finally:
        stranger.close()


def test_run_gives_uvicorn_the_protocols_ceiling(monkeypatch, tmp_path):
    """The limit is the protocol's constant, passed explicitly, rather than a
    default nobody chose that a uvicorn upgrade could move."""
    configs = []

    class _Server:
        def __init__(self, config):
            configs.append(config)

        async def serve(self, sockets):
            return None

    bound = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    bound.bind(("127.0.0.1", 0))
    monkeypatch.setattr(hubmod, "_try_bind", lambda host, port: bound)
    monkeypatch.setattr(hubmod, "_pidfile_path", lambda: tmp_path / "viz-hub.pid")
    monkeypatch.setattr(hubmod.uvicorn, "Server", _Server)
    try:
        assert hubmod.run() == 0
    finally:
        bound.close()

    [config] = configs
    assert config.ws_max_size == INGEST_MAX_MESSAGE_BYTES


# --- the bundle is build output ---


async def test_a_missing_bundle_says_how_to_build_it(hub, monkeypatch, tmp_path):
    """A checkout has no bundle until it is built. The API keeps serving; the
    page says what to run rather than failing with a missing file."""
    monkeypatch.setattr(hubmod, "STATIC_DIR", tmp_path / "not-built")

    status, body = await _http_get(hub.addr, "/")
    assert status == 503
    assert "make build-frontend" in body

    health_status, _ = await _http_get(hub.addr, "/api/health")
    assert health_status == 200
