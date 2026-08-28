"""End-to-end tests for the MCP server.

Tests call tools via FastMCP's call_tool method, exercising the full
server wiring including lifespan, Context injection, and JSON serialization.
"""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import pytest

from fastmcp import FastMCP

from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.mcp.retrieval_records import new_record_log
from epimemer.mcp.config import ServerConfig
from epimemer.mcp.server import mcp as epimemer_mcp
from epimemer.core.types import BASE_METACONTEXT_ID, Metacontext
from epimemer.storage.memory import InMemoryStorage

def _graph_with_the_real() -> InMemoryStorage:
    """An in-memory graph somebody has set up.

    Since #76 a frame is required at ingest and `the-real` is an ordinary
    metacontext, created once like any other. A server fixture without it would
    make every test here start by creating a frame, which tests the fixture.
    """
    store = InMemoryStorage()
    store._graphs[store._database].metacontexts[BASE_METACONTEXT_ID] = Metacontext(
        id=BASE_METACONTEXT_ID,
        content="The Real",
        description="Claims about the real world.",
    )
    return store


@asynccontextmanager
async def _test_lifespan(server: FastMCP) -> AsyncIterator[dict]:
    """Test lifespan using in-memory storage and mock providers."""
    yield {
        "storage": _graph_with_the_real(),
        "embedding_provider": MockEmbeddingProvider(model_id="mock-embed", dimension=8),
        "config": ServerConfig(
            storage_backend="memory",
            embedding_provider="mock",
        ),
        "event_bus": None,
        "viz_session": None,
        "viz_hub_url": None,
        "retrievals": new_record_log(),
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


async def _segment_and_store(
    server: FastMCP, content: str, metacontext_id: str = "the-real",
    graph: str = "default",
) -> dict:
    """Helper: run the two-step ingest flow (segment + store_decomposition).

    `graph` is threaded rather than defaulted at each call because both steps
    have to name the same one, and a test that switches graphs first has to say
    so — which is the whole point of the parameter (#71).
    """
    seg_result = await server.call_tool(
        "segment",
        {"expected_graph": graph, "content": content},
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
        "metacontext_id": metacontext_id,
    }

    store_result = await server.call_tool(
        "store_decomposition",
        {"expected_graph": graph, **store_args},
    )
    return _parse_response(store_result)


class TestMCPProtocol:

    async def test_segment_returns_valid_json(self, server):
        result = await server.call_tool(
            "segment",
            {"expected_graph": "default", "content": "Machine learning is a branch of AI."},
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
            {"expected_graph": "default", "query": "Neural networks learn from large datasets."},
        )
        data = _parse_response(result)
        assert len(data["result"]["nodes"]) > 0

    async def test_meta_present_on_response(self, server):
        result = await server.call_tool(
            "segment",
            {"expected_graph": "default", "content": "Some text to segment."},
        )
        data = _parse_response(result)
        assert "_meta" in data
        assert data["_meta"]["latency_ms"] > 0

    async def test_link_via_protocol(self, server):
        await _segment_and_store(server, "First paragraph.\n\nSecond paragraph.")

        search_result = await server.call_tool(
            "search",
            {"expected_graph": "default", "query": "First paragraph", "k": 2, "graph_hops": 0},
        )
        search_data = _parse_response(search_result)
        nodes = search_data["result"]["nodes"]

        if len(nodes) >= 2:
            link_result = await server.call_tool(
                "link",
                {"expected_graph": "default", 
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
            {"expected_graph": "default"},
        )
        data = _parse_response(result)
        assert "result" in data
        assert "similar_pairs" in data["result"]

    async def test_archive_via_protocol(self, server):
        result = await server.call_tool(
            "archive",
            {"expected_graph": "default", "max_age_days": 90},
        )
        data = _parse_response(result)
        assert data["result"]["nodes_archived"] == 0  # Nothing old enough

    async def test_error_returns_structured_json(self, server):
        result = await server.call_tool(
            "update",
            {"expected_graph": "default", 
                "node_id": "nonexistent", "new_content": "test",
                "because": "it_was_wrong",
            },
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
            {"expected_graph": "default", "name": "AI History", "description": "Key events"},
        )
        data = _parse_response(result)
        assert data["result"]["name"] == "AI History"
        assert data["result"]["timeline_id"]

    async def test_add_timepoint_converts_offset_to_utc(self, server):
        """An offset-aware start must be *converted* to UTC, not stripped.

        `.replace(tzinfo=utc)` discards the offset instead of converting, so
        12:00+02:00 was stored as 12:00Z rather than 10:00Z — silently shifting
        every timestamp the storage layer then compares lexicographically.
        """
        created = _parse_response(
            await server.call_tool("create_timeline", {"expected_graph": "default", "name": "Offsets"})
        )
        timeline_id = created["result"]["timeline_id"]

        await server.call_tool(
            "add_timepoint",
            {"expected_graph": "default", "timeline_id": timeline_id, "start": "2024-01-01T12:00:00+02:00"},
        )

        queried = _parse_response(
            await server.call_tool("query_timeline", {"expected_graph": "default", "timeline_id": timeline_id})
        )
        starts = [tp["start"] for tp in queried["result"]["timepoints"]]
        assert len(starts) == 1
        assert datetime.fromisoformat(starts[0]) == datetime(
            2024, 1, 1, 10, 0, tzinfo=timezone.utc
        )

    async def test_query_timeline_range_converts_offset_to_utc(self, server):
        """Range bounds are parsed on the same broken path as `start`.

        The timepoint is stored with an unambiguous UTC string so only the
        *bounds* are under test. Storing it with an offset too would make the
        test blind: both values would shift by the same amount and the errors
        would cancel.
        """
        created = _parse_response(
            await server.call_tool("create_timeline", {"expected_graph": "default", "name": "Ranges"})
        )
        timeline_id = created["result"]["timeline_id"]

        await server.call_tool(
            "add_timepoint",
            {"expected_graph": "default", "timeline_id": timeline_id, "start": "2024-01-01T10:00:00+00:00"},
        )

        # 11:00+02:00 == 09:00Z .. 13:00+02:00 == 11:00Z — brackets 10:00Z.
        # Left unconverted the bounds read 11:00Z..13:00Z and miss it.
        inside = _parse_response(
            await server.call_tool(
                "query_timeline",
                {"expected_graph": "default", 
                    "timeline_id": timeline_id,
                    "range_start": "2024-01-01T11:00:00+02:00",
                    "range_end": "2024-01-01T13:00:00+02:00",
                },
            )
        )
        assert len(inside["result"]["timepoints"]) == 1

        # 13:00+02:00 == 11:00Z .. 15:00+02:00 == 13:00Z — entirely after it.
        outside = _parse_response(
            await server.call_tool(
                "query_timeline",
                {"expected_graph": "default", 
                    "timeline_id": timeline_id,
                    "range_start": "2024-01-01T13:00:00+02:00",
                    "range_end": "2024-01-01T15:00:00+02:00",
                },
            )
        )
        assert outside["result"]["timepoints"] == []

    async def test_create_metacontext_via_protocol(self, server):
        result = await server.call_tool(
            "create_metacontext",
            {"expected_graph": "default", "content": "Real historical events"},
        )
        data = _parse_response(result)
        assert data["result"]["content"] == "Real historical events"

    async def test_ingest_with_metacontext_via_protocol(self, server):
        mc_result = await server.call_tool(
            "create_metacontext",
            {"expected_graph": "default", "content": "Science fiction"},
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


class TestClaimAgentThroughTheServer:
    """The registry at the surface an agent actually reaches.

    The tools-level behaviour is pinned in `test_claim_agent.py`; what this file
    can add is the wiring — Context injection, the JSON envelope, and what
    happens where FastMCP has no session to bind to.
    """

    async def test_an_unapproved_id_is_refused_and_the_refusal_carries_advice(
        self, server
    ):
        result = await server.call_tool(
            "claim_agent",
            {"expected_graph": "default", "agent_id": "self-appointed", "description": "a critic"},
        )

        data = _parse_response(result)["result"]
        assert data["status"] == "refused"
        # No elicitation channel exists in this harness, which is exactly the
        # case the CLI and the env var are the fallbacks for.
        assert "EPIMEMER_APPROVED_AGENTS" in data["reason"]

    async def test_an_approved_id_is_claimed(self, server):
        storage = epimemer_mcp._lifespan_result["storage"]
        await storage.set_approved_agent_ids(["critic"])

        result = await server.call_tool(
            "claim_agent",
            {"expected_graph": "default", "agent_id": "critic",
             "description": "a critic"},
        )

        data = _parse_response(result)["result"]
        assert data["status"] == "claimed"
        assert data["digest"]
        assert (await storage.get_agent("critic")) is not None

    async def test_no_session_leaves_the_claim_recorded_and_unbound(self, server):
        """`call_tool` here opens no MCP session, so there is nothing to bind to.

        Reported rather than raised: the agent is recorded either way, and a
        claim that bound nothing has to be visible instead of silent.
        """
        storage = epimemer_mcp._lifespan_result["storage"]
        await storage.set_approved_agent_ids(["critic"])

        result = await server.call_tool(
            "claim_agent",
            {"expected_graph": "default", "agent_id": "critic",
             "description": "a critic"},
        )

        data = _parse_response(result)["result"]
        assert data["status"] == "claimed"
        assert data["session_bound"] is False

    async def test_configured_ids_reach_a_graph_created_later(self, server):
        """`EPIMEMER_APPROVED_AGENTS` is per server, and approval is per graph.

        A switch that skipped the seeding would leave an elicitation-less client
        unable to admit a judge to the new graph at all.
        """
        deps = epimemer_mcp._lifespan_result
        deps["config"] = deps["config"].model_copy(
            update={"approved_agents": ["critic"]}
        )

        await server.call_tool("use_graph", {"name": "elsewhere", "confirm": True})
        result = await server.call_tool(
            "claim_agent",
            {"expected_graph": "elsewhere", "agent_id": "critic",
             "description": "a critic"},
        )

        assert _parse_response(result)["result"]["status"] == "claimed"
