"""Every tool declares the node ids its response carries (§2, §2.1).

The record is written by construction — one insertion at `_run_with_timeout`,
covering every tool including ones not written yet. The **ids** are not: they
are declared per tool, and a tool that forgets produces a silently-empty record
unless something checks. This is that something.

It is an oracle rather than an enumeration, and that is the point. The list of
"six node-returning tools" in §2 was wrong when it was written — it counted
serializer call sites and missed `check_conflicts`, `reflect` and
`list_sources` — so a test that enumerated tools could not have caught the
seventh. This one seeds a known graph, calls **every registered tool**, and
looks for those ids in what came back.
"""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest
from fastmcp import FastMCP

from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.mcp.config import ServerConfig
from epimemer.mcp import server as server_mod
from epimemer.mcp.retrieval_records import new_record_log
from epimemer.mcp.server import mcp as epimemer_mcp
from epimemer.mcp.types import ResponseMeta
from epimemer.core.types import BASE_METACONTEXT_ID, Metacontext
from epimemer.storage.memory import InMemoryStorage

def _graph_with_the_real() -> InMemoryStorage:
    """An in-memory graph somebody has set up.

    Since the frame requirement a frame is required at ingest and `the-real` is an ordinary
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
    yield {
        "storage": _graph_with_the_real(),
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
    epimemer_mcp._lifespan = _test_lifespan
    async with _test_lifespan(epimemer_mcp) as ctx:
        epimemer_mcp._lifespan_result = ctx
        yield epimemer_mcp
        epimemer_mcp._lifespan_result = None
    epimemer_mcp._lifespan = original


@pytest.fixture
def captured(monkeypatch) -> list[tuple[dict, ResponseMeta]]:
    """Every `(result, meta)` a tool produced.

    `retrieved` is `exclude=True`, so it is deliberately absent from what the
    agent reads — which means the only place to observe it is here, before
    serialization.
    """
    seen: list[tuple[dict, ResponseMeta]] = []
    original = server_mod._build_response

    def _spy(result: dict, meta: ResponseMeta, latency_ms: float) -> str:
        seen.append((result, meta))
        return original(result, meta, latency_ms)

    monkeypatch.setattr(server_mod, "_build_response", _spy)
    return seen


def _parse(result) -> dict:
    return json.loads(result.content[0].text)


async def _seed(server: FastMCP) -> dict:
    """A small graph whose ids the oracle then hunts for in every response."""
    seg = _parse(await server.call_tool("segment", {"expected_graph": "default", 
        "content": (
            "The deployment rollback failed on Tuesday. "
            "JIRA-4417 tracks the certificate rotation."
        ),
        "source": "runbook",
        "published_by": "Platform Team",
    }))["result"]
    document_id = seg["document_id"]
    segment_ids = [s["segment_id"] for s in seg["segments"]]

    stored = _parse(await server.call_tool("store_decomposition", {"expected_graph": "default", 
        "metacontext_id": "the-real",
        "document_id": document_id,
        "segments": [
            {
                "segment_id": segment_ids[0],
                "topics": ["Deployment"],
                "facts": ["The deployment rollback failed on Tuesday"],
                "inferences": ["The release process is fragile"],
            }
        ],
        "tags": ["ops"],
    }))["result"]

    graph = _parse(await server.call_tool(
        "find_nodes", {"expected_graph": "default", "sourced_from": document_id, "limit": 100}
    ))["result"]
    nodes = graph["nodes"]
    by_type = {t: [n["id"] for n in nodes if n["node_type"] == t] for t in
               ("topic", "fact", "inference")}

    timeline = _parse(await server.call_tool(
        "create_timeline", {"expected_graph": "default", "name": "Ops", "description": "ops events"}
    ))["result"]
    timepoint = _parse(await server.call_tool("add_timepoint", {"expected_graph": "default", 
        "timeline_id": timeline["timeline_id"], "label": "the incident",
    }))["result"]
    metacontext = _parse(await server.call_tool(
        "create_metacontext", {"expected_graph": "default", "content": "Runbook frame"}
    ))["result"]

    return {
        "document_id": document_id,
        "segment_ids": segment_ids,
        "stored": stored,
        "node_ids": [n["id"] for n in nodes],
        "by_type": by_type,
        "timeline_id": timeline["timeline_id"],
        "timepoint_id": timepoint["timepoint_id"],
        "metacontext_id": metacontext["metacontext_id"],
    }


def _args(tool: str, seeded: dict) -> dict:
    """Arguments that make each tool do its real work against the seeded graph.

    A tool called with junk would error and carry no ids, which passes the
    oracle vacuously — so every recipe here is a call that actually returns
    something.
    """
    facts = seeded["by_type"]["fact"]
    topics = seeded["by_type"]["topic"]
    inferences = seeded["by_type"]["inference"]
    now = datetime.now(timezone.utc)
    return {
        "segment": {"content": "A second document about rollbacks."},
        "store_decomposition": {
            "metacontext_id": "the-real",
            "document_id": seeded["document_id"],
            "segments": [{
                "segment_id": seeded["segment_ids"][0],
                "facts": ["A later claim about the rollback"],
            }],
        },
        "search": {"query": "deployment rollback", "k": 5},
        "link": {"src_id": facts[0], "dst_id": topics[0], "relation": "concerns"},
        "update": {
            "node_id": facts[0],
            "new_content": "The deployment rollback succeeded on Tuesday",
            "because": "it_was_wrong",
        },
        "supersede_by": {
            "old_id": facts[0], "existing_id": inferences[0],
            "because": "the_world_changed",
        },
        "judge_importance": {
            "node_id": facts[0], "direction": "up", "reason": "central to the incident",
        },
        "check_conflicts": {"fact_ids": facts, "threshold": 0.0},
        "record_contradiction": {"a_id": facts[0], "b_id": inferences[0]},
        "record_variant": {"a_id": facts[0], "b_id": inferences[0]},
        # Refused (the seeded facts carry no claim_kind), and that is the
        # call worth making the oracle watch: a refusal still names the ids
        # it was handed, so it still has to declare them.
        "merge_facts": {"source_ids": facts[:2], "content": "One claim."},
        # Refused (the seeded inferences are below the similarity bar), and a
        # refusal still names the ids it was handed, so it still has to declare
        # them — the same property `merge_facts` is here for.
        "merge_inferences": {
            "source_ids": inferences[:2], "content": "One conclusion.",
        },
        "reflect": {"similarity_threshold": 0.0},
        "apply_reflection": {"judgments": [
            {"node_id": facts[0], "direction": "up", "reason": "central"}
        ]},
        "query_graph": {"node_id": facts[0], "hops": 2},
        "topic_tree": {"topic_id": topics[0], "depth": 2},
        "graph_as_of": {"at": (now + timedelta(minutes=1)).isoformat()},
        "query_changes": {"last_days": 1.0},
        "find_nodes": {"sourced_from": seeded["document_id"], "limit": 100},
        "list_sources": {},
        "list_relations": {},
        # Refused: the seeded graph carries no relation edges, so there is no
        # label to describe. A refusal is still a response, which is what is
        # under test — and this one names no node ids at all, which is itself
        # the property the oracle checks.
        "describe_relation": {"name": "concerns", "description": "what it means"},
        "archive": {"max_age_days": 0},
        "restore": {"archive_data": {"nodes": [], "edges": []}},
        "create_timeline": {"name": "Another"},
        "set_reference_time": {"timeline_id": seeded["timeline_id"]},
        "add_timepoint": {"timeline_id": seeded["timeline_id"], "label": "the outage"},
        "query_timeline": {"timeline_id": seeded["timeline_id"]},
        "create_timelink": {
            "node_id": facts[0],
            "timeline_id": seeded["timeline_id"],
            "timepoint_id": seeded["timepoint_id"],
        },
        "create_metacontext": {"content": "Another frame"},
        "get_metacontexts": {"node_id": facts[0]},
        "graph_stats": {},
        "review": {},
        # Nothing supplied: an empty batch is an answer rather than an error,
        # and it is the response shape under test here rather than a write.
        "apply_review": {},
        # Refused — no field is supplied — and a refusal still names the id back
        # at the agent, which is the property under test.
        "rejudge": {"node_id": facts[0], "because": "checking the shape"},
        # Refused — the seeded facts carry no frame — and a refusal still names
        # the id back at the agent, which is the property under test.
        "reframe": {
            "node_id": facts[0], "withdraw": "not-a-frame",
            "because": "checking the shape",
        },
        # Refused — no source edge names this document — same property.
        "correct_interval": {
            "node_id": facts[0], "source_id": "not-a-document",
            "intervals": [], "because": "checking the shape",
        },
        # Not a merge survivor, so this refuses — and a refusal still names the
        # id back at the agent, which is the property under test.
        "reverse_merge": {"survivor_id": facts[0]},
        "configure_merge": {},
        "configure_warnings": {},
        "configure_reflection": {"threshold": 7},
        "list_graphs": {},
        # Refused: no id is approved in the test graph and there is no channel
        # to a user. A refusal is still a response, which is what is under test.
        "claim_agent": {"agent_id": "a-critic", "description": "a critic"},
        "use_graph": {"name": "default", "confirm": True},
        "delete_graph": {"name": "not-a-graph", "confirm": True},
        "viz_status": {},
    }[tool]


async def _tool_names() -> list[str]:
    return sorted(tool.name for tool in await epimemer_mcp.list_tools())


def _ids_in(text: str, known: list[str]) -> set[str]:
    """Which known node ids the agent could read off this response."""
    return {node_id for node_id in known if node_id in text}


ALL_TOOLS = [
    "segment", "store_decomposition", "search", "link", "update", "supersede_by",
    "judge_importance", "check_conflicts", "record_contradiction", "record_variant",
    "merge_facts", "merge_inferences", "reverse_merge", "configure_merge",
    "configure_warnings",
    "reflect", "apply_reflection", "review", "apply_review", "rejudge",
    "reframe", "correct_interval",
    "query_graph", "topic_tree",
    "graph_as_of", "query_changes", "find_nodes", "list_sources", "list_relations", "describe_relation", "archive",
    "restore", "create_timeline", "set_reference_time", "add_timepoint",
    "query_timeline", "create_timelink", "create_metacontext", "get_metacontexts",
    "graph_stats", "configure_reflection", "list_graphs", "use_graph",
    "claim_agent",
    "delete_graph", "viz_status",
]


@pytest.mark.parametrize("tool", ALL_TOOLS)
async def test_any_node_id_in_a_response_is_declared(server, captured, tool):
    seeded = await _seed(server)
    captured.clear()

    result = await server.call_tool(tool, _args(tool, seeded))

    text = result.content[0].text
    visible = _ids_in(text, seeded["node_ids"])
    assert captured, f"{tool} produced no response to check"
    _, meta = captured[-1]
    declared = {node.node_id for node in (meta.retrieved or [])}

    assert visible <= declared, (
        f"{tool} put {sorted(visible - declared)} in its response without "
        f"declaring them. Every node id the agent can read has to be in "
        f"`meta.retrieved`, or focus mode greys a node it just showed them."
    )


async def test_the_parametrised_list_covers_every_registered_tool():
    """The oracle is only an oracle if it is over *all* tools.

    A new tool added to `server.py` without a line here would otherwise be
    exactly the seventh tool §2's census missed.
    """
    assert set(await _tool_names()) == set(ALL_TOOLS)


async def test_an_undeclared_tool_is_flagged_not_silent(server, captured):
    """§2.1: `None` vs `[]`.

    A tool that returns no nodes and says so is a different thing from one that
    never declared, and the two must not both read as "nothing was retrieved".
    """
    seeded = await _seed(server)
    captured.clear()

    await server.call_tool("graph_stats", {"expected_graph": "default"})
    _, stats_meta = captured[-1]

    await server.call_tool(
        "find_nodes", {"expected_graph": "default", "sourced_from": seeded["document_id"], "limit": 100}
    )
    _, find_meta = captured[-1]

    assert stats_meta.retrieved is None          # never declared
    assert find_meta.retrieved is not None       # declared, and non-empty
    assert len(find_meta.retrieved) > 0


async def test_declaring_nothing_is_not_the_same_as_not_declaring(server, captured):
    await _seed(server)  # a graph exists; this tag does not
    captured.clear()

    await server.call_tool("find_nodes", {"expected_graph": "default", "tagged_with": "no-such-tag", "limit": 5})

    _, meta = captured[-1]
    assert meta.retrieved == []


async def test_retrieved_ids_are_not_serialized_to_the_agent(server, captured):
    """§2.1's `exclude=True`.

    Guards a token-cost regression no other test would notice: the ids are for
    the dashboard, and putting them on the wire makes the agent read a list it
    has no use for.
    """
    seeded = await _seed(server)
    captured.clear()

    result = await server.call_tool("search", {"expected_graph": "default", "query": "deployment", "k": 5})

    _, meta = captured[-1]
    assert meta.retrieved  # the tool did declare
    assert "retrieved" not in json.loads(result.content[0].text)["_meta"]
    assert seeded["node_ids"]  # sanity: the graph was not empty
