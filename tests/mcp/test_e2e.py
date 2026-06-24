"""End-to-end tests for the MCP server.

Tests call tools via FastMCP's call_tool method, exercising the full
server wiring including lifespan, Context injection, and JSON serialization.
"""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest

from fastmcp import FastMCP

from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.mcp.config import ServerConfig
from epimemer.mcp.server import mcp as epimemer_mcp
from epimemer.storage.memory import InMemoryStorage


@asynccontextmanager
async def _test_lifespan(server: FastMCP) -> AsyncIterator[dict]:
    """Test lifespan using in-memory storage and mock providers."""
    yield {
        "storage": InMemoryStorage(),
        "embedding_provider": MockEmbeddingProvider(model_id="mock-embed", dimension=8),
        "config": ServerConfig(
            storage_backend="memory",
            embedding_provider="mock",
        ),
    }


@pytest.fixture
async def server():
    """Create a test server with mock lifespan."""
    # Swap the lifespan for testing
    original_lifespan = epimemer_mcp._lifespan
    epimemer_mcp._lifespan = _test_lifespan

    async with _test_lifespan(epimemer_mcp) as ctx:
        epimemer_mcp._lifespan_result = ctx
        yield epimemer_mcp
        epimemer_mcp._lifespan_result = None

    epimemer_mcp._lifespan = original_lifespan


def _parse_response(result) -> dict:
    """Parse a ToolResult into a dict."""
    text = result.content[0].text
    return json.loads(text)


async def _segment_and_store(server: FastMCP, content: str, metacontext_id: str | None = None) -> dict:
    """Helper: run the two-step ingest flow (segment + store_decomposition)."""
    seg_result = await server.call_tool(
        "segment",
        {"content": content},
    )
    seg_data = _parse_response(seg_result)
    doc_id = seg_data["result"]["document_id"]
    segments = seg_data["result"]["segments"]

    decomposition = [
        {
            "segment_id": s["segment_id"],
            "topics": [f"Topic about: {s['segment_id']}"],
            "facts": [f"Fact from: {s['segment_id']}"],
            "inferences": [f"Inference from: {s['segment_id']}"],
        }
        for s in segments
    ]

    store_args: dict = {
        "document_id": doc_id,
        "segments": decomposition,
    }
    if metacontext_id:
        store_args["metacontext_id"] = metacontext_id

    store_result = await server.call_tool(
        "store_decomposition",
        store_args,
    )
    return _parse_response(store_result)


class TestMCPProtocol:

    async def test_segment_returns_valid_json(self, server):
        result = await server.call_tool(
            "segment",
            {"content": "Machine learning is a branch of AI."},
        )
        data = _parse_response(result)
        assert "result" in data
        assert len(data["result"]["segments"]) >= 1
        assert data["result"]["document_id"]

    async def test_store_decomposition_returns_valid_json(self, server):
        data = await _segment_and_store(server, "Machine learning is a branch of AI.")
        assert "result" in data
        assert data["result"]["nodes_created"]["topics"] >= 1
        assert data["result"]["nodes_created"]["facts"] >= 1
        assert data["result"]["edges_created"] >= 1

    async def test_two_step_ingest_then_search(self, server):
        await _segment_and_store(server, "Neural networks learn from large datasets.")

        result = await server.call_tool(
            "search",
            {"query": "Neural networks learn from large datasets."},
        )
        data = _parse_response(result)
        assert len(data["result"]["nodes"]) > 0

    async def test_meta_present_on_response(self, server):
        result = await server.call_tool(
            "segment",
            {"content": "Some text to segment."},
        )
        data = _parse_response(result)
        assert "_meta" in data
        assert data["_meta"]["latency_ms"] > 0

    async def test_link_via_protocol(self, server):
        await _segment_and_store(server, "First paragraph.\n\nSecond paragraph.")

        search_result = await server.call_tool(
            "search",
            {"query": "First paragraph", "k": 2, "graph_hops": 0},
        )
        search_data = _parse_response(search_result)
        nodes = search_data["result"]["nodes"]

        if len(nodes) >= 2:
            link_result = await server.call_tool(
                "link",
                {
                    "src_id": nodes[0]["id"],
                    "dst_id": nodes[1]["id"],
                    "edge_type": "supports",
                },
            )
            link_data = _parse_response(link_result)
            assert "result" in link_data
            assert "edge_id" in link_data["result"]

    async def test_reflect_via_protocol(self, server):
        result = await server.call_tool(
            "reflect",
            {},
        )
        data = _parse_response(result)
        assert "result" in data
        assert "similar_pairs" in data["result"]
        assert "nodes_decayed" in data["result"]

    async def test_archive_via_protocol(self, server):
        result = await server.call_tool(
            "archive",
            {"max_age_days": 90},
        )
        data = _parse_response(result)
        assert data["result"]["nodes_archived"] == 0  # Nothing old enough

    async def test_error_returns_structured_json(self, server):
        result = await server.call_tool(
            "update",
            {"node_id": "nonexistent", "new_content": "test"},
        )
        data = _parse_response(result)
        assert "error" in data

    async def test_all_tools_registered(self, server):
        tool_names = {t.name for t in await server.list_tools()}
        expected = {
            "segment",
            "store_decomposition",
            "search",
            "link",
            "update",
            "reflect",
            "apply_reflection",
            "query_graph",
            "archive",
            "restore",
            "create_timeline",
            "add_timepoint",
            "query_timeline",
            "create_timelink",
            "create_metacontext",
            "get_metacontexts",
            "list_graphs",
            "use_graph",
        }
        assert expected.issubset(tool_names)

    async def test_create_timeline_via_protocol(self, server):
        result = await server.call_tool(
            "create_timeline",
            {"name": "AI History", "description": "Key events"},
        )
        data = _parse_response(result)
        assert data["result"]["name"] == "AI History"
        assert data["result"]["timeline_id"]

    async def test_create_metacontext_via_protocol(self, server):
        result = await server.call_tool(
            "create_metacontext",
            {"content": "Real historical events"},
        )
        data = _parse_response(result)
        assert data["result"]["content"] == "Real historical events"

    async def test_ingest_with_metacontext_via_protocol(self, server):
        mc_result = await server.call_tool(
            "create_metacontext",
            {"content": "Science fiction"},
        )
        mc_data = _parse_response(mc_result)
        mc_id = mc_data["result"]["metacontext_id"]

        data = await _segment_and_store(server, "The ships are alive.", metacontext_id=mc_id)
        assert data["result"]["nodes_created"]["topics"] >= 1

    async def test_list_graphs_via_protocol(self, server):
        result = await server.call_tool("list_graphs", {})
        data = _parse_response(result)
        assert "graphs" in data["result"]
        assert "active_graph" in data["result"]

    async def test_list_graphs_returns_default_for_memory(self, server):
        result = await server.call_tool("list_graphs", {})
        data = _parse_response(result)
        assert data["result"]["graphs"] == ["default"]
        assert data["result"]["active_graph"] == "default"

    async def test_use_graph_creates_new_graph(self, server):
        result = await server.call_tool(
            "use_graph",
            {"name": "test_graph", "confirm": True},
        )
        data = _parse_response(result)
        assert data["result"]["status"] == "created"
        assert data["result"]["active_graph"] == "test_graph"

    async def test_delete_graph_works(self, server):
        # Create a graph first
        await server.call_tool(
            "use_graph",
            {"name": "to_delete", "confirm": True},
        )
        # Switch back to default before deleting
        await server.call_tool(
            "use_graph",
            {"name": "default"},
        )
        result = await server.call_tool(
            "delete_graph",
            {"name": "to_delete", "confirm": True},
        )
        data = _parse_response(result)
        assert data["result"]["status"] == "deleted"
