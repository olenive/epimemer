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
from epimemer.llm.mock import MockDecompositionProvider
from epimemer.mcp.config import ServerConfig
from epimemer.mcp.server import mcp as epimemer_mcp
from epimemer.storage.memory import InMemoryStorage


@asynccontextmanager
async def _test_lifespan(server: FastMCP) -> AsyncIterator[dict]:
    """Test lifespan using in-memory storage and mock providers."""
    yield {
        "storage": InMemoryStorage(),
        "embedding_provider": MockEmbeddingProvider(model_id="mock-embed", dimension=8),
        "decomposition_provider": MockDecompositionProvider(),
        "config": ServerConfig(
            storage_backend="memory",
            embedding_provider="mock",
            decomposition_provider="mock",
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


class TestMCPProtocol:

    async def test_ingest_returns_valid_json(self, server):
        result = await server.call_tool(
            "memory.ingest",
            {"content": "Machine learning is a branch of AI."},
        )
        data = _parse_response(result)
        assert "result" in data
        assert data["result"]["segments_created"] >= 1

    async def test_ingest_then_search(self, server):
        # Ingest
        await server.call_tool(
            "memory.ingest",
            {"content": "Neural networks learn from large datasets."},
        )

        # Search
        result = await server.call_tool(
            "memory.search",
            {"query": "Neural networks learn from large datasets."},
        )
        data = _parse_response(result)
        assert len(data["result"]["nodes"]) > 0

    async def test_meta_present_on_response(self, server):
        result = await server.call_tool(
            "memory.ingest",
            {"content": "Some text to ingest."},
        )
        data = _parse_response(result)
        assert "_meta" in data
        assert data["_meta"]["llm_calls"] >= 3
        assert data["_meta"]["latency_ms"] > 0

    async def test_link_via_protocol(self, server):
        # Ingest to create some nodes
        ingest_result = await server.call_tool(
            "memory.ingest",
            {"content": "First paragraph.\n\nSecond paragraph."},
        )
        ingest_data = _parse_response(ingest_result)

        # Search for nodes to get their IDs
        search_result = await server.call_tool(
            "memory.search",
            {"query": "First paragraph", "k": 2, "graph_hops": 0},
        )
        search_data = _parse_response(search_result)
        nodes = search_data["result"]["nodes"]

        if len(nodes) >= 2:
            # Try linking two nodes
            link_result = await server.call_tool(
                "memory.link",
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
            "memory.reflect",
            {},
        )
        data = _parse_response(result)
        assert "result" in data
        assert "topics_merged" in data["result"]

    async def test_archive_via_protocol(self, server):
        result = await server.call_tool(
            "memory.archive",
            {"max_age_days": 90},
        )
        data = _parse_response(result)
        assert data["result"]["nodes_archived"] == 0  # Nothing old enough

    async def test_error_returns_structured_json(self, server):
        # Try to update a nonexistent node
        result = await server.call_tool(
            "memory.update",
            {"node_id": "nonexistent", "new_content": "test"},
        )
        data = _parse_response(result)
        assert "error" in data

    async def test_all_tools_registered(self, server):
        tool_names = {t.name for t in await server.list_tools()}
        expected = {
            "memory.ingest",
            "memory.search",
            "memory.link",
            "memory.update",
            "memory.reflect",
            "memory.query_graph",
            "memory.archive",
            "memory.restore",
            "memory.create_timeline",
            "memory.add_timepoint",
            "memory.query_timeline",
            "memory.create_timelink",
            "memory.create_metacontext",
            "memory.get_metacontexts",
        }
        assert expected.issubset(tool_names)

    async def test_create_timeline_via_protocol(self, server):
        result = await server.call_tool(
            "memory.create_timeline",
            {"name": "AI History", "description": "Key events"},
        )
        data = _parse_response(result)
        assert data["result"]["name"] == "AI History"
        assert data["result"]["timeline_id"]

    async def test_create_metacontext_via_protocol(self, server):
        result = await server.call_tool(
            "memory.create_metacontext",
            {"content": "Real historical events"},
        )
        data = _parse_response(result)
        assert data["result"]["content"] == "Real historical events"

    async def test_ingest_with_metacontext_via_protocol(self, server):
        # Create metacontext first
        mc_result = await server.call_tool(
            "memory.create_metacontext",
            {"content": "Science fiction"},
        )
        mc_data = _parse_response(mc_result)
        mc_id = mc_data["result"]["metacontext_id"]

        # Ingest with metacontext
        result = await server.call_tool(
            "memory.ingest",
            {"content": "The ships are alive.", "metacontext_id": mc_id},
        )
        data = _parse_response(result)
        assert data["result"]["segments_created"] >= 1
