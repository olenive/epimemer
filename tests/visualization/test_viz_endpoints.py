"""Tests for viz-only storage methods, HTTP endpoints, and snapshot isolation."""

import pytest
from starlette.testclient import TestClient

from epimemer.core.types import (
    Fact,
    Inference,
    NodeEdge,
    EdgeType,
    NodeStatus,
    Topic,
)
from epimemer.storage.memory import InMemoryStorage
from epimemer.visualization.event_bus import create_event_bus
from epimemer.visualization.events import node_to_view, edge_to_view
from epimemer.visualization.ws_server import create_app


@pytest.fixture
def storage():
    return InMemoryStorage()


@pytest.fixture
def bus():
    return create_event_bus()


@pytest.fixture
def client(bus, storage):
    app = create_app(bus, storage)
    return TestClient(app)


# --- viz_list_nodes / viz_list_edges ---


class TestVizStorageMethods:

    async def test_viz_list_nodes_returns_active_by_default(self, storage):
        t1 = Topic(content="Active topic", source_id="s1")
        t2 = Topic(content="Superseded topic", source_id="s1", status=NodeStatus.SUPERSEDED)
        await storage.store_node(t1)
        await storage.store_node(t2)

        nodes = await storage.viz_list_nodes("default")
        assert len(nodes) == 1
        assert nodes[0].content == "Active topic"

    async def test_viz_list_nodes_with_historical_status(self, storage):
        t1 = Topic(content="Active", source_id="s1")
        t2 = Topic(content="Superseded", source_id="s1", status=NodeStatus.SUPERSEDED)
        await storage.store_node(t1)
        await storage.store_node(t2)

        nodes = await storage.viz_list_nodes("default", historical_status=NodeStatus.SUPERSEDED)
        assert len(nodes) == 1
        assert nodes[0].content == "Superseded"

    async def test_viz_list_edges(self, storage):
        edge = NodeEdge(src_id="a", dst_id="b", type=EdgeType.SUPPORTS)
        await storage.store_edge(edge)

        edges = await storage.viz_list_edges("default")
        assert len(edges) == 1
        assert edges[0].src_id == "a"

    async def test_viz_list_nodes_cross_graph(self, storage):
        """Viz reads from a different graph without switching active."""
        t1 = Topic(content="Default topic", source_id="s1")
        await storage.store_node(t1)

        await storage.switch_database("other")
        t2 = Topic(content="Other topic", source_id="s1")
        await storage.store_node(t2)
        await storage.switch_database("default")

        # Read from "other" without switching
        nodes = await storage.viz_list_nodes("other")
        assert len(nodes) == 1
        assert nodes[0].content == "Other topic"

        # Active database unchanged
        assert storage.current_database == "default"

    async def test_viz_list_nodes_nonexistent_graph(self, storage):
        nodes = await storage.viz_list_nodes("no-such-graph")
        assert nodes == []

    async def test_viz_list_edges_nonexistent_graph(self, storage):
        edges = await storage.viz_list_edges("no-such-graph")
        assert edges == []


# --- HTTP endpoint tests ---


class TestHttpEndpoints:

    def test_api_graphs(self, client, storage):
        resp = client.get("/api/graphs")
        assert resp.status_code == 200
        data = resp.json()
        assert data["graphs"] == ["default"]
        assert data["active_graph"] == "default"

    def test_api_snapshot_missing_param(self, client):
        resp = client.get("/api/snapshot")
        assert resp.status_code == 400
        assert "Missing" in resp.json()["error"]

    def test_api_snapshot_not_found(self, client):
        resp = client.get("/api/snapshot?graph=nonexistent")
        assert resp.status_code == 404

    def test_api_snapshot_returns_nodes_and_edges(self, client, storage):
        t = Topic(content="Test topic", source_id="s1")
        storage.nodes[t.id] = t
        e = NodeEdge(src_id=t.id, dst_id=t.id, type=EdgeType.SUPPORTS)
        storage.edges[e.id] = e

        resp = client.get("/api/snapshot?graph=default")
        assert resp.status_code == 200
        data = resp.json()
        assert data["graph"] == "default"
        assert len(data["nodes"]) == 1
        assert len(data["edges"]) == 1
        # Verify NodeView shape
        node = data["nodes"][0]
        assert "node_id" in node
        assert "node_type" in node
        assert "confidence" in node
        # Verify EdgeView shape
        edge = data["edges"][0]
        assert "edge_id" in edge
        assert "edge_type" in edge

    def test_api_snapshot_empty_graph(self, client, storage):
        resp = client.get("/api/snapshot?graph=default")
        assert resp.status_code == 200
        data = resp.json()
        assert data["nodes"] == []
        assert data["edges"] == []

    def test_api_snapshot_does_not_affect_active_graph(self, client, storage):
        """Fetching a snapshot for a different graph must not switch the active graph."""
        import asyncio
        loop = asyncio.new_event_loop()
        loop.run_until_complete(storage.switch_database("other"))
        loop.run_until_complete(storage.switch_database("default"))
        loop.close()

        assert storage.current_database == "default"
        resp = client.get("/api/snapshot?graph=other")
        assert resp.status_code == 200
        assert storage.current_database == "default"


# --- Conversion helper tests ---


class TestViewConversion:

    def test_node_to_view_topic(self):
        t = Topic(content="A topic", source_id="s1")
        view = node_to_view(t, "my-graph")
        assert view.node_id == t.id
        assert view.node_type == "topic"
        assert view.content == "A topic"
        assert view.status == "active"
        assert view.graph == "my-graph"
        assert view.source_id == "s1"
        assert view.extraction_method == "llm"
        assert 0.0 <= view.novelty <= 1.0
        assert 0.0 <= view.confidence <= 1.0

    def test_node_to_view_fact(self):
        f = Fact(content="A fact", source_id="s2")
        view = node_to_view(f, "g")
        assert view.node_type == "fact"

    def test_node_to_view_inference(self):
        i = Inference(content="An inference", source_id="s3")
        view = node_to_view(i, "g")
        assert view.node_type == "inference"

    def test_node_to_view_superseded(self):
        t = Topic(content="Old", source_id="s1", status=NodeStatus.SUPERSEDED)
        view = node_to_view(t, "g")
        assert view.status == "superseded"

    def test_edge_to_view(self):
        e = NodeEdge(src_id="a", dst_id="b", type=EdgeType.SUPPORTS, weight=0.8)
        view = edge_to_view(e, "my-graph")
        assert view.edge_id == e.id
        assert view.src_id == "a"
        assert view.dst_id == "b"
        assert view.edge_type == "supports"
        assert view.weight == 0.8
        assert view.graph == "my-graph"
