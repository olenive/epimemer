"""Tests for WebSocket relay: sequence numbers and subscription filtering."""

import json

import pytest
from starlette.testclient import TestClient

from epimemer.core.types import Topic
from epimemer.storage.memory import InMemoryStorage
from epimemer.visualization.event_bus import create_event_bus
from epimemer.visualization.events import NodeStored, node_to_view
from epimemer.visualization.ws_server import create_app


@pytest.fixture
def bus():
    return create_event_bus()


@pytest.fixture
def storage():
    return InMemoryStorage()


@pytest.fixture
def client(bus, storage):
    app = create_app(bus, storage)
    return TestClient(app)


def _make_node_stored(graph: str = "default") -> NodeStored:
    topic = Topic(content="test", source_id="s1")
    return NodeStored(
        graph=graph,
        node=node_to_view(topic, graph),
    )


class TestSequenceNumbers:

    def test_events_have_incrementing_seq(self, client, bus):
        with client.websocket_connect("/ws") as ws:
            import asyncio
            loop = asyncio.new_event_loop()

            loop.run_until_complete(bus.publish(_make_node_stored()))
            msg1 = json.loads(ws.receive_text())

            loop.run_until_complete(bus.publish(_make_node_stored()))
            msg2 = json.loads(ws.receive_text())

            loop.run_until_complete(bus.publish(_make_node_stored()))
            msg3 = json.loads(ws.receive_text())

            loop.close()

            assert msg1["seq"] == 1
            assert msg2["seq"] == 2
            assert msg3["seq"] == 3


class TestSubscriptionFiltering:

    def test_unsubscribed_receives_all(self, client, bus):
        """Without a subscribe message, client receives events from all graphs."""
        with client.websocket_connect("/ws") as ws:
            import asyncio
            loop = asyncio.new_event_loop()

            loop.run_until_complete(bus.publish(_make_node_stored("graph-a")))
            loop.run_until_complete(bus.publish(_make_node_stored("graph-b")))

            msg1 = json.loads(ws.receive_text())
            msg2 = json.loads(ws.receive_text())

            loop.close()

            assert msg1["seq"] == 1
            assert msg2["seq"] == 2

    def test_subscribed_filters_events(self, client, bus):
        """After subscribing to a specific graph, only matching events are forwarded."""
        with client.websocket_connect("/ws") as ws:
            ws.send_text(json.dumps({"subscribe": ["graph-a"]}))

            import asyncio
            loop = asyncio.new_event_loop()

            loop.run_until_complete(bus.publish(_make_node_stored("graph-a")))
            loop.run_until_complete(bus.publish(_make_node_stored("graph-b")))
            loop.run_until_complete(bus.publish(_make_node_stored("graph-a")))

            msg1 = json.loads(ws.receive_text())
            msg2 = json.loads(ws.receive_text())

            loop.close()

            # Only graph-a events received, seq still increments per-connection
            assert msg1["seq"] == 1
            assert msg1["graph"] == "graph-a"
            assert msg2["seq"] == 2
            assert msg2["graph"] == "graph-a"

    def test_subscribe_null_resets_to_all(self, client, bus):
        """Sending subscribe: null switches back to receiving all events."""
        with client.websocket_connect("/ws") as ws:
            ws.send_text(json.dumps({"subscribe": ["graph-a"]}))

            import asyncio
            loop = asyncio.new_event_loop()

            # This should be filtered out
            loop.run_until_complete(bus.publish(_make_node_stored("graph-b")))

            # Reset to all
            ws.send_text(json.dumps({"subscribe": None}))

            loop.run_until_complete(bus.publish(_make_node_stored("graph-b")))
            msg = json.loads(ws.receive_text())

            loop.close()

            assert msg["graph"] == "graph-b"
