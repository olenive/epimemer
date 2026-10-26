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
from datetime import UTC, datetime, timedelta

import pytest
from fastmcp import FastMCP

from epimemer.core.types import BASE_METACONTEXT_ID, Metacontext, NodeStatus
from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.mcp import server as server_mod
from epimemer.mcp.config import ServerConfig
from epimemer.mcp.retrieval_records import new_record_log
from epimemer.mcp.server import mcp as epimemer_mcp
from epimemer.mcp.server import new_connection_state
from epimemer.mcp.types import ResponseMeta
from epimemer.storage.memory import InMemoryStorage


def _graph_with_the_real() -> InMemoryStorage:
    """An in-memory graph somebody has set up.

    A metacontext is required at ingest and `the-real` is an ordinary
    metacontext, created once like any other. A server fixture without it would
    make every test here start by creating a metacontext, which tests the
    fixture.
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
        "version": "test",
        "event_bus": None,
        "viz_session": None,
        "viz_hub_url": None,
        "retrievals": new_record_log(),
        "connection_state": new_connection_state(),
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
    seg = _parse(
        await server.call_tool(
            "segment",
            {
                "expected_graph": "default",
                "content": (
                    "The deployment rollback failed on Tuesday. "
                    "JIRA-4417 tracks the certificate rotation."
                ),
                "source": "runbook",
                "published_by": "Platform Team",
            },
        )
    )["result"]
    document_id = seg["document_id"]
    segment_ids = [s["segment_id"] for s in seg["segments"]]

    stored = _parse(
        await server.call_tool(
            "store_decomposition",
            {
                "expected_graph": "default",
                "metacontext_id": "the-real",
                "document_id": document_id,
                "segments": [
                    {
                        "segment_id": segment_ids[0],
                        "topics": ["Deployment"],
                        # Two facts, because the pair tools join fact to fact.
                        "facts": [
                            "The deployment rollback failed on Tuesday",
                            "JIRA-4417 tracks the certificate rotation",
                        ],
                        "inferences": ["The release process is fragile"],
                    }
                ],
                "tags": ["ops"],
                "tag_descriptions": {"ops": "Running and operating the service."},
            },
        )
    )["result"]

    graph = _parse(
        await server.call_tool(
            "find_nodes", {"expected_graph": "default", "sourced_from": document_id, "limit": 100}
        )
    )["result"]
    nodes = graph["nodes"]
    by_type = {
        t: [n["id"] for n in nodes if n["node_type"] == t] for t in ("topic", "fact", "inference")
    }

    timeline = _parse(
        await server.call_tool(
            "create_timeline",
            {"expected_graph": "default", "name": "Ops", "description": "ops events"},
        )
    )["result"]
    timepoint = _parse(
        await server.call_tool(
            "add_timepoint",
            {
                "expected_graph": "default",
                "timeline_id": timeline["timeline_id"],
                "label": "the incident",
            },
        )
    )["result"]
    metacontext = _parse(
        await server.call_tool(
            "create_metacontext", {"expected_graph": "default", "content": "Runbook metacontext"}
        )
    )["result"]
    historical_fact_id = await _seed_a_retired_fact(server)

    return {
        "document_id": document_id,
        "segment_ids": segment_ids,
        "stored": stored,
        # Every node the graph holds, whatever its status and however it got
        # there. Listing only what the seeded document sourced left out the
        # topic node created from the `ops` tag and every retired node, so a
        # response naming either passed the oracle without being checked.
        "node_ids": await _every_node_id(server),
        "by_type": by_type,
        "timeline_id": timeline["timeline_id"],
        "timepoint_id": timepoint["timepoint_id"],
        "metacontext_id": metacontext["metacontext_id"],
        "historical_fact_id": historical_fact_id,
    }


# Word for word the content of the fact `_seed_a_retired_fact` retires, so an
# ingest that states it again meets a historical twin.
RETIRED_CLAIM = "The certificate rotation is pending"


async def _seed_a_retired_fact(server: FastMCP) -> str:
    """A fact the world moved past, from a document of its own.

    A second document so the first one's `find_nodes` listing, which the recipes
    below index into, is unchanged.
    """
    seg = _parse(
        await server.call_tool(
            "segment",
            {
                "expected_graph": "default",
                "content": f"{RETIRED_CLAIM}.",
                "source": "status page",
            },
        )
    )["result"]
    await server.call_tool(
        "store_decomposition",
        {
            "expected_graph": "default",
            "metacontext_id": "the-real",
            "document_id": seg["document_id"],
            "segments": [
                {"segment_id": seg["segments"][0]["segment_id"], "facts": [RETIRED_CLAIM]}
            ],
        },
    )
    found = _parse(
        await server.call_tool(
            "find_nodes", {"expected_graph": "default", "sourced_from": seg["document_id"]}
        )
    )["result"]
    (pending,) = [n["id"] for n in found["nodes"] if n["content"] == RETIRED_CLAIM]
    await server.call_tool(
        "update",
        {
            "expected_graph": "default",
            "node_id": pending,
            "new_content": "The certificate rotation completed",
            "because": "the_world_changed",
        },
    )
    return pending


async def _every_node_id(server: FastMCP) -> list[str]:
    """Every node in the active graph, in every status.

    Read from the store rather than through a tool, because the oracle must not
    depend on what some tool chooses to list.
    """
    storage = server._lifespan_result["storage"]
    return [node.id for status in NodeStatus for node in await storage.query_nodes(status=status)]


def _args(tool: str, seeded: dict) -> dict:
    """Arguments that make each tool do its real work against the seeded graph.

    A tool called with junk would error and carry no ids, which passes the
    oracle vacuously — so every recipe here is a call that actually returns
    something.
    """
    facts = seeded["by_type"]["fact"]
    topics = seeded["by_type"]["topic"]
    inferences = seeded["by_type"]["inference"]
    now = datetime.now(UTC)
    return {
        "segment": {"content": "A second document about rollbacks."},
        # Refused: the seeded document is decomposed, so its passages are
        # cited. A refusal is still a response, which is what is under test.
        "resegment": {
            "document_id": seeded["document_id"],
            "cuts": ["The deployment rollback failed"],
        },
        # Both paths that put an existing node in the response. The `ops` tag
        # is already described, so the supplied line is not written and the
        # warning names the topic node; the retired claim, stated again, comes
        # back as a historical twin naming the retired fact and the new one.
        "store_decomposition": {
            "metacontext_id": "the-real",
            "document_id": seeded["document_id"],
            "segments": [
                {
                    "segment_id": seeded["segment_ids"][0],
                    "facts": ["A later claim about the rollback", RETIRED_CLAIM],
                }
            ],
            "tags": ["ops"],
            "tag_descriptions": {"ops": "Keeping the service running."},
        },
        "search": {"query": "deployment rollback", "k": 5},
        "link": {"src_id": facts[0], "dst_id": topics[0], "relation": "concerns"},
        "update": {
            "node_id": facts[0],
            "new_content": "The deployment rollback succeeded on Tuesday",
            "because": "it_was_wrong",
        },
        "rename_topic": {"topic": topics[0], "name": "Deployments"},
        "supersede_by": {
            "old_id": facts[0],
            "existing_id": facts[1],
            "because": "the_world_changed",
        },
        "judge_importance": {
            "node_id": facts[0],
            "direction": "up",
            "reason": "central to the incident",
        },
        "check_conflicts": {"fact_ids": facts, "threshold": 0.0},
        "record_contradiction": {"a_id": facts[0], "b_id": facts[1]},
        "record_variant": {"a_id": facts[0], "b_id": facts[1]},
        # Refused (the seeded facts carry no claim_kind), and that is the
        # call worth making the oracle watch: a refusal still names the ids
        # it was handed, so it still has to declare them.
        "merge_facts": {"source_ids": facts[:2], "content": "One claim."},
        # Refused (the seeded inferences are below the similarity bar), and a
        # refusal still names the ids it was handed, so it still has to declare
        # them — the same property `merge_facts` is here for.
        "merge_inferences": {
            "source_ids": inferences[:2],
            "content": "One conclusion.",
        },
        "reflect": {"similarity_threshold": 0.0},
        "apply_reflection": {
            "judgments": [{"node_id": facts[0], "direction": "up", "reason": "central"}]
        },
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
        "order_timepoints": {
            "timeline_id": seeded["timeline_id"],
            "pairs": [],
            "source_id": seeded["document_id"],
            "basis": "stated",
        },
        # Refused — the seeded timeline carries no contradiction — and a refusal
        # still names ids back at the agent, which is the property under test.
        "resolve_temporal_contradiction": {
            "timeline_id": seeded["timeline_id"],
            "contradiction_id": "not-a-contradiction",
            "verdict": "hold",
            "because": "checking the shape",
        },
        # Refused for the same reason: one of the two points does not exist.
        "merge_timepoints": {
            "timeline_id": seeded["timeline_id"],
            "survivor_id": seeded["timepoint_id"],
            "merged_id": "not-a-timepoint",
            "because": "checking the shape",
        },
        "query_timeline": {"timeline_id": seeded["timeline_id"]},
        "add_recurrence": {
            "timeline_id": seeded["timeline_id"],
            "label": "the standup",
            "anchor": "2024-01-01T09:00:00Z",
            "period": "P1D",
        },
        # Refused — the seeded timeline carries no rule — and a refusal still
        # names ids back at the agent, which is the property under test.
        "end_recurrence": {
            "timeline_id": seeded["timeline_id"],
            "recurrence_id": "not-a-recurrence",
            "because": "checking the shape",
        },
        # Refused for the same reason.
        "record_recurrence_exception": {
            "timeline_id": seeded["timeline_id"],
            "recurrence_id": "not-a-recurrence",
            "occurrence_start": "2024-01-02T09:00:00Z",
            "kind": "cancelled",
        },
        "create_timelink": {
            "node_id": facts[0],
            "timeline_id": seeded["timeline_id"],
            "timepoint_id": seeded["timepoint_id"],
        },
        "create_metacontext": {"content": "Another metacontext"},
        "set_metacontext_base": {"metacontext_id": seeded["metacontext_id"], "base_id": "the-real"},
        "get_metacontexts": {"node_id": facts[0]},
        "graph_stats": {},
        "review": {},
        # Nothing supplied: an empty batch is an answer rather than an error,
        # and it is the response shape under test here rather than a write.
        "apply_review": {},
        # Refused — no field is supplied — and a refusal still names the id back
        # at the agent, which is the property under test.
        "rejudge": {"node_id": facts[0], "because": "checking the shape"},
        # Refused — the seeded facts carry no metacontext — and a refusal still names
        # the id back at the agent, which is the property under test.
        "reassign_metacontext": {
            "node_id": facts[0],
            "withdraw": "not-a-metacontext",
            "because": "checking the shape",
        },
        # Refused — no source edge names this document — same property.
        "correct_interval": {
            "node_id": facts[0],
            "source_id": "not-a-document",
            "intervals": [],
            "because": "checking the shape",
        },
        # Not a merge survivor, so this refuses — and a refusal still names the
        # id back at the agent, which is the property under test.
        "reverse_merge": {"survivor_id": facts[0]},
        # Refused: nothing has judged this pair, so there is no suppression to
        # withdraw. A refusal naming both ids is still a response carrying them.
        "reopen": {"node_ids": [facts[0], inferences[0]], "reason": "worth another look"},
        "configure_merge": {},
        "configure_deferral": {},
        "configure_warnings": {},
        "configure_reflection": {"threshold": 7},
        "configure_backup": {"threshold": 7},
        # Refused: no destination is configured in the test server. A refusal is
        # still a response, which is what is under test.
        "backup_graph": {},
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


async def _call(server: FastMCP, tool: str, args: dict):
    """Call `tool` naming the graph wherever the tool takes one.

    Read off the tool's own schema rather than a list, so a tool added later is
    called the way an agent would call it. Without the name every graph-scoped
    call is refused at the gate, names no node, and passes the oracle having
    tested nothing.
    """
    (registered,) = [t for t in await server.list_tools() if t.name == tool]
    if "expected_graph" in registered.parameters.get("properties", {}):
        args = {"expected_graph": "default", **args}
    return await server.call_tool(tool, args)


def _refused_at_the_graph_gate(text: str) -> bool:
    """Whether the call stopped at the graph guard, which answers in this shape."""
    result = json.loads(text).get("result", {})
    return isinstance(result, dict) and {"refused", "expected_graph", "active_graph"} <= set(result)


def _ids_in(text: str, known: list[str]) -> set[str]:
    """Which known node ids the agent could read off this response."""
    return {node_id for node_id in known if node_id in text}


ALL_TOOLS = [
    "segment",
    "resegment",
    "store_decomposition",
    "search",
    "link",
    "update",
    "rename_topic",
    "supersede_by",
    "judge_importance",
    "check_conflicts",
    "record_contradiction",
    "record_variant",
    "merge_facts",
    "merge_inferences",
    "reverse_merge",
    "configure_merge",
    "configure_deferral",
    "configure_warnings",
    "reflect",
    "apply_reflection",
    "reopen",
    "review",
    "apply_review",
    "rejudge",
    "reassign_metacontext",
    "correct_interval",
    "query_graph",
    "topic_tree",
    "graph_as_of",
    "query_changes",
    "find_nodes",
    "list_sources",
    "list_relations",
    "describe_relation",
    "archive",
    "restore",
    "create_timeline",
    "set_reference_time",
    "add_timepoint",
    "order_timepoints",
    "resolve_temporal_contradiction",
    "merge_timepoints",
    "add_recurrence",
    "end_recurrence",
    "record_recurrence_exception",
    "query_timeline",
    "create_timelink",
    "create_metacontext",
    "set_metacontext_base",
    "get_metacontexts",
    "graph_stats",
    "configure_reflection",
    "configure_backup",
    "backup_graph",
    "list_graphs",
    "use_graph",
    "claim_agent",
    "delete_graph",
    "viz_status",
]


@pytest.mark.parametrize("tool", ALL_TOOLS)
async def test_any_node_id_in_a_response_is_declared(server, captured, tool):
    seeded = await _seed(server)
    captured.clear()

    result = await _call(server, tool, _args(tool, seeded))

    text = result.content[0].text
    assert not _refused_at_the_graph_gate(text), (
        f"{tool} was refused at the graph gate, so the oracle would pass it unchecked"
    )
    # The graph after the call as well as before it: a write that names a node
    # it has just created shows the agent that id too.
    visible = _ids_in(text, [*seeded["node_ids"], *await _every_node_id(server)])
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
        "find_nodes",
        {"expected_graph": "default", "sourced_from": seeded["document_id"], "limit": 100},
    )
    _, find_meta = captured[-1]

    assert stats_meta.retrieved is None  # never declared
    assert find_meta.retrieved is not None  # declared, and non-empty
    assert len(find_meta.retrieved) > 0


async def test_declaring_nothing_is_not_the_same_as_not_declaring(server, captured):
    await _seed(server)  # a graph exists; this tag does not
    captured.clear()

    await server.call_tool(
        "find_nodes", {"expected_graph": "default", "tagged_with_topic": "no-such-tag", "limit": 5}
    )

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

    result = await server.call_tool(
        "search", {"expected_graph": "default", "query": "deployment", "k": 5}
    )

    _, meta = captured[-1]
    assert meta.retrieved  # the tool did declare
    assert "retrieved" not in json.loads(result.content[0].text)["_meta"]
    assert seeded["node_ids"]  # sanity: the graph was not empty


async def _ingest(
    server, captured, seeded, *, segment: dict, **fields
) -> tuple[dict, ResponseMeta]:
    """One `store_decomposition` into the seeded document, and the meta it declared."""
    result = await _call(
        server,
        "store_decomposition",
        {
            "metacontext_id": "the-real",
            "document_id": seeded["document_id"],
            "segments": [{"segment_id": seeded["segment_ids"][0], **segment}],
            **fields,
        },
    )
    _, meta = captured[-1]
    return _parse(result)["result"], meta


class TestStoreDecompositionDeclaresWhatItNames:
    """An ingest response names existing nodes on two paths, and both declare.

    The dashboard caught one on 21 October: a tag supplied with a description
    it already had, whose warning names the topic node created from the tag. The
    other is a fact stated word for word as one the graph retired, which comes
    back as a historical twin naming the retired fact and the new one.
    """

    async def test_the_topic_node_a_warning_names_is_declared(self, server, captured):
        seeded = await _seed(server)

        result, meta = await _ingest(
            server,
            captured,
            seeded,
            segment={"facts": ["The pager rota changed"]},
            tags=["ops"],
            tag_descriptions={"ops": "Keeping the service running."},
        )

        (warning,) = result["warnings"]
        assert warning["kind"] == "description_not_written"
        assert set(warning["subjects"]) <= {node.node_id for node in meta.retrieved}

    async def test_a_historical_twin_declares_both_facts(self, server, captured):
        seeded = await _seed(server)

        result, meta = await _ingest(server, captured, seeded, segment={"facts": [RETIRED_CLAIM]})

        (twin,) = result["historical_twins"]
        assert twin["historical_id"] == seeded["historical_fact_id"]
        assert {twin["historical_id"], twin["fact_id"]} <= {node.node_id for node in meta.retrieved}

    async def test_a_muted_warning_names_nothing_and_declares_nothing(self, server, captured):
        """`retrieved` is what the response carried. A warning the graph keeps
        from the agent puts no id in front of it, so none is declared."""
        seeded = await _seed(server)
        await _call(server, "configure_warnings", {"surface": False})

        result, meta = await _ingest(
            server,
            captured,
            seeded,
            segment={"facts": ["The pager rota changed"]},
            tags=["ops"],
            tag_descriptions={"ops": "Keeping the service running."},
        )

        assert "warnings" not in result
        assert meta.retrieved == []

    async def test_an_ingest_naming_no_node_declares_an_empty_list(self, server, captured):
        """Declared and empty, so the dashboard reads "nothing named" rather
        than "never said"."""
        seeded = await _seed(server)

        _, meta = await _ingest(
            server, captured, seeded, segment={"facts": ["The pager rota changed"]}
        )

        assert meta.retrieved == []


async def test_find_nodes_declares_the_base_fact_a_variant_names(server, captured):
    """`variants_in` hangs the base fact on each result as `varies_from`, and
    the agent reads that id as surely as the one it hangs on."""
    seeded = await _seed(server)
    derived = seeded["metacontext_id"]
    await _call(server, "set_metacontext_base", {"metacontext_id": derived, "base_id": "the-real"})
    await _call(
        server,
        "store_decomposition",
        {
            "metacontext_id": derived,
            "document_id": seeded["document_id"],
            "segments": [
                {
                    "segment_id": seeded["segment_ids"][0],
                    "facts": ["The deployment rollback succeeded on Tuesday"],
                }
            ],
        },
    )
    listed = _parse(
        await _call(
            server,
            "find_nodes",
            {"sourced_from": seeded["document_id"], "metacontexts": [derived]},
        )
    )["result"]
    (variant,) = [node["id"] for node in listed["nodes"] if node["node_type"] == "fact"]
    base_fact = seeded["by_type"]["fact"][0]
    await _call(server, "record_variant", {"a_id": variant, "b_id": base_fact})
    captured.clear()

    result = _parse(await _call(server, "find_nodes", {"variants_in": derived}))["result"]

    (found,) = result["nodes"]
    assert (found["id"], found["varies_from"]["id"]) == (variant, base_fact)
    _, meta = captured[-1]
    assert {variant, base_fact} <= {node.node_id for node in meta.retrieved}
