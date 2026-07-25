"""Tests for viz-only storage methods, snapshot assembly, and view conversion.

The HTTP/relay layer moved to the standalone hub (`test_hub.py`); the read-side
assembly that used to live in the embedded server's `/api/snapshot` and
`/api/graphs` handlers now lives in `visualization/snapshot.py` and is tested
here directly, against storage.
"""

import pytest

from epimemer.core.types import (
    Fact,
    Inference,
    NodeEdge,
    EdgeType,
    NodeStatus,
    Topic,
)
from epimemer.storage.memory import InMemoryStorage
from epimemer.visualization.events import node_to_view, edge_to_view
from epimemer.visualization.snapshot import assemble_snapshot, list_graphs_result


@pytest.fixture
def storage():
    return InMemoryStorage()


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


# --- Snapshot / graph-list assembly (was the embedded server's HTTP handlers) ---


class TestSnapshotAssembly:

    async def test_list_graphs_result_includes_backend(self, storage):
        result = await list_graphs_result(storage)
        assert result["graphs"] == ["default"]
        assert result["active_graph"] == "default"
        assert result["backend"] == "memory"

    async def test_list_graphs_result_seeds_reflection_pressure(self, storage):
        """A browser connecting mid-session needs the current numbers, not just
        the events that happen after it arrives."""
        await storage.bump_reflect_counter()
        await storage.bump_reflect_counter()

        result = await list_graphs_result(storage, default_reflect_threshold=10)

        assert result["reflect"] == {
            "count": 2,
            "threshold": 10,
            "suggested": False,
        }

    async def test_list_graphs_result_reports_a_due_reflect(self, storage):
        await storage.bump_reflect_counter()

        result = await list_graphs_result(storage, default_reflect_threshold=1)

        assert result["reflect"]["suggested"] is True

    async def test_list_graphs_result_honours_a_threshold_override(self, storage):
        await storage.set_reflect_threshold_override(3)

        result = await list_graphs_result(storage, default_reflect_threshold=10)

        assert result["reflect"]["threshold"] == 3

    async def test_assemble_snapshot_returns_node_and_edge_views(self, storage):
        t = Topic(content="Test topic", source_id="s1")
        await storage.store_node(t)
        e = NodeEdge(src_id=t.id, dst_id=t.id, type=EdgeType.SUPPORTS)
        await storage.store_edge(e)

        data = await assemble_snapshot(storage, "default")
        assert data["graph"] == "default"
        assert len(data["nodes"]) == 1
        assert len(data["edges"]) == 1
        node = data["nodes"][0]
        assert "node_id" in node and "node_type" in node and "confidence" in node
        edge = data["edges"][0]
        assert "edge_id" in edge and "edge_type" in edge

    async def test_assemble_snapshot_empty_graph(self, storage):
        data = await assemble_snapshot(storage, "default")
        assert data["nodes"] == []
        assert data["edges"] == []

    async def test_assemble_snapshot_does_not_switch_active_graph(self, storage):
        await storage.switch_database("other")
        await storage.switch_database("default")
        assert storage.current_database == "default"

        await assemble_snapshot(storage, "other")
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
        assert view.extraction_method == "unspecified"
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
