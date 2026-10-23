"""Merge settings, per graph and per process.

The reflect threshold's pattern: a process default on `ServerConfig`, read from
`EPIMEMER_MERGE_UNDO_DEPTH` and `EPIMEMER_MERGE_CYCLE_LIMIT`; an override per
graph, set with `configure_merge`; one pure `resolve_merge_settings`. Every
path that resolves the settings is checked against a process default that
differs from the built-in one, since a call site that forgot the config would
otherwise pass on the built-in numbers.
"""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from fastmcp import FastMCP
from pydantic import ValidationError

from epimemer.core.types import (
    BASE_METACONTEXT_ID,
    ClaimKind,
    EdgeType,
    EmbeddingRecord,
    Fact,
    Inference,
    Metacontext,
    NodeEdge,
    NodeType,
    Topic,
    ValueSignal,
    read_merge_undo,
)
from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.mcp import tools
from epimemer.mcp.config import ServerConfig, load_config, merge_defaults
from epimemer.mcp.retrieval_records import new_record_log
from epimemer.mcp.server import mcp as epimemer_mcp
from epimemer.mcp.server import new_connection_state
from epimemer.pipelines.graph_construction.versioning import merge_nodes, reverse_merge
from epimemer.storage.memory import InMemoryStorage
from epimemer.storage.protocol import MergeOverrides, MergeSettings, resolve_merge_settings

GRAPH = "merge-scratch"
_TWIN = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]


@pytest.fixture
def embedding_provider() -> MockEmbeddingProvider:
    return MockEmbeddingProvider(model_id="mock-embed", dimension=8)


class TestTheProcessDefault:
    def test_the_built_in_defaults(self, monkeypatch):
        monkeypatch.delenv("EPIMEMER_MERGE_UNDO_DEPTH", raising=False)
        monkeypatch.delenv("EPIMEMER_MERGE_CYCLE_LIMIT", raising=False)

        config = load_config()

        assert (config.merge_undo_depth, config.merge_cycle_limit) == (10, 2)
        assert merge_defaults(config) == MergeSettings(undo_depth=10, cycle_limit=2)

    def test_the_environment_sets_them(self, monkeypatch):
        monkeypatch.setenv("EPIMEMER_MERGE_UNDO_DEPTH", "4")
        monkeypatch.setenv("EPIMEMER_MERGE_CYCLE_LIMIT", "3")

        assert merge_defaults(load_config()) == MergeSettings(undo_depth=4, cycle_limit=3)

    @pytest.mark.parametrize(
        "variable", ["EPIMEMER_MERGE_UNDO_DEPTH", "EPIMEMER_MERGE_CYCLE_LIMIT"]
    )
    def test_zero_is_refused_at_load(self, monkeypatch, variable):
        monkeypatch.setenv(variable, "0")

        with pytest.raises(ValidationError, match=variable):
            load_config()


class TestTheResolver:
    def test_no_overrides_is_the_process_default(self):
        defaults = MergeSettings(undo_depth=7, cycle_limit=3)

        assert resolve_merge_settings(None, defaults) == defaults
        assert resolve_merge_settings(MergeOverrides(), defaults) == defaults

    def test_an_override_wins_and_the_other_follows_the_default(self):
        defaults = MergeSettings(undo_depth=7, cycle_limit=3)

        resolved = resolve_merge_settings(MergeOverrides(cycle_limit=5), defaults)

        assert resolved == MergeSettings(undo_depth=7, cycle_limit=5)


class TestTheTool:
    async def test_with_nothing_set_it_reports_the_process_default(self, storage):
        result, _ = await tools.configure_merge(
            storage, merge_defaults=MergeSettings(undo_depth=6, cycle_limit=3)
        )

        assert (result["merge_undo_depth"], result["merge_cycle_limit"]) == (6, 3)
        assert result["overridden"] == {}

    async def test_a_cleared_graph_follows_the_default_at_the_time(self, storage):
        await tools.configure_merge(storage, cycle_limit=4)
        await tools.configure_merge(storage, clear=True)

        result, _ = await tools.configure_merge(
            storage, merge_defaults=MergeSettings(undo_depth=10, cycle_limit=5)
        )

        assert result["merge_cycle_limit"] == 5


async def _embed(storage, embedding_provider, node, vector) -> None:
    await storage.store_embedding(
        EmbeddingRecord(item_id=node.id, model_id=embedding_provider.model_id, vector=vector)
    )


async def _fact(storage, embedding_provider, content, vector=None) -> Fact:
    node = Fact(content=content, source_id="seg-1", claim_kind=ClaimKind.STATE, value=ValueSignal())
    await storage.store_node(node)
    await _embed(storage, embedding_provider, node, vector or _TWIN)
    return node


async def _inference(storage, embedding_provider, content) -> Inference:
    node = Inference(content=content, source_id="seg-1")
    await storage.store_node(node)
    await _embed(storage, embedding_provider, node, _TWIN)
    await storage.store_edge(
        NodeEdge(src_id=node.id, dst_id=BASE_METACONTEXT_ID, type=EdgeType.HAS_METACONTEXT)
    )
    return node


async def _topic(storage, embedding_provider, content, vector) -> Topic:
    node = Topic(content=content, source_id="seg-1", value=ValueSignal())
    await storage.store_node(node)
    await _embed(storage, embedding_provider, node, vector)
    return node


async def _vector_of(storage, node_id: str) -> list[float]:
    [record] = await storage.get_embeddings_for_item(node_id)
    return list(record.vector)


async def _merge_and_reverse(storage, embedding_provider, sources, survivor) -> None:
    await merge_nodes(list(sources), survivor, storage, embedding_provider)
    await reverse_merge(survivor.id, storage)


class TestEveryMergePathAppliesTheProcessDefault:
    """A process default that differs from the built-in one, at each path."""

    async def test_merge_nodes(self, storage, embedding_provider):
        a = await _fact(storage, embedding_provider, "Bonn is the capital.")
        b = await _fact(storage, embedding_provider, "The capital is Bonn.")
        shallow = MergeSettings(undo_depth=1, cycle_limit=2)
        first = Fact(content="Bonn is the capital city.", source_id="seg-1")
        await merge_nodes([a, b], first, storage, embedding_provider, merge_defaults=shallow)
        c = await _fact(storage, embedding_provider, "The capital city is Bonn.")
        second = Fact(content="Bonn was the capital city.", source_id="seg-1")

        await merge_nodes([first, c], second, storage, embedding_provider, merge_defaults=shallow)

        assert read_merge_undo(await storage.get_node(first.id)) is None

    async def test_merge_facts_gates_at_the_default_cycle_limit(self, storage, embedding_provider):
        a = await _fact(storage, embedding_provider, "Bonn is the capital.")
        b = await _fact(storage, embedding_provider, "The capital is Bonn.")
        await _merge_and_reverse(
            storage,
            embedding_provider,
            [a, b],
            Fact(content="Bonn is the capital city.", source_id="seg-1"),
        )

        result, _ = await tools.merge_facts(
            source_ids=[a.id, b.id],
            content="Bonn is the capital city.",
            storage=storage,
            embedding_provider=embedding_provider,
            merge_defaults=MergeSettings(undo_depth=10, cycle_limit=1),
        )

        assert result["merged"] is False
        assert "merge_cycle_limit of 1" in result["refused"]

    async def test_merge_facts_keeps_the_default_undo_depth(self, storage, embedding_provider):
        shallow = MergeSettings(undo_depth=1, cycle_limit=2)
        a = await _fact(storage, embedding_provider, "Bonn is the capital.")
        b = await _fact(storage, embedding_provider, "The capital is Bonn.")
        first, _ = await tools.merge_facts(
            source_ids=[a.id, b.id],
            content="Bonn is the capital city.",
            storage=storage,
            embedding_provider=embedding_provider,
            merge_defaults=shallow,
        )
        c = await _fact(
            storage,
            embedding_provider,
            "The capital city is Bonn.",
            vector=await _vector_of(storage, first["fact_id"]),
        )

        second, _ = await tools.merge_facts(
            source_ids=[first["fact_id"], c.id],
            content="Bonn is the capital city of West Germany.",
            storage=storage,
            embedding_provider=embedding_provider,
            merge_defaults=shallow,
        )

        assert second["merged"] is True
        assert read_merge_undo(await storage.get_node(first["fact_id"])) is None

    async def test_merge_inferences_gates_at_the_default_cycle_limit(
        self, storage, embedding_provider
    ):
        one = await _inference(storage, embedding_provider, "The release is unsafe")
        other = await _inference(storage, embedding_provider, "The release cannot be trusted")
        await _merge_and_reverse(
            storage,
            embedding_provider,
            [one, other],
            Inference(content="One reading.", source_id="seg-1"),
        )

        result, _ = await tools.merge_inferences(
            source_ids=[one.id, other.id],
            content="One reading.",
            storage=storage,
            embedding_provider=embedding_provider,
            merge_defaults=MergeSettings(undo_depth=10, cycle_limit=1),
        )

        assert result["merged"] is False
        assert "merge_cycle_limit of 1" in result["refused"]

    async def test_a_topic_merge_keeps_the_default_undo_depth(self, storage, embedding_provider):
        shallow = MergeSettings(undo_depth=1, cycle_limit=2)
        a = await _topic(storage, embedding_provider, "ML basics", _TWIN)
        b = await _topic(storage, embedding_provider, "Machine learning basics", _TWIN)
        await tools.apply_reflection(
            storage,
            embedding_provider,
            merges=[{"source_ids": [a.id, b.id], "content": "Machine learning basics"}],
            merge_defaults=shallow,
        )
        [first] = [node.id for node in await storage.query_nodes(node_type=NodeType.TOPIC)]
        c = await _topic(
            storage, embedding_provider, "Basics of ML", await _vector_of(storage, first)
        )

        result, _ = await tools.apply_reflection(
            storage,
            embedding_provider,
            merges=[{"source_ids": [first, c.id], "content": "The basics of machine learning"}],
            merge_defaults=shallow,
        )

        assert result["topics_merged"] == 1
        assert read_merge_undo(await storage.get_node(first)) is None


@asynccontextmanager
async def _session(storage, config: ServerConfig) -> AsyncIterator[FastMCP]:
    original = epimemer_mcp._lifespan
    deps = {
        "storage": storage,
        "embedding_provider": MockEmbeddingProvider(model_id="mock-embed", dimension=8),
        "config": config,
        "event_bus": None,
        "viz_session": None,
        "viz_hub_url": None,
        "retrievals": new_record_log(),
        "connection_state": new_connection_state(),
    }

    @asynccontextmanager
    async def _lifespan(_server):
        yield deps

    epimemer_mcp._lifespan = _lifespan
    async with _lifespan(epimemer_mcp) as ctx:
        epimemer_mcp._lifespan_result = ctx
        try:
            yield epimemer_mcp
        finally:
            epimemer_mcp._lifespan_result = None
    epimemer_mcp._lifespan = original


async def _call(server: FastMCP, tool: str, **arguments) -> dict:
    response = await server.call_tool(tool, {"expected_graph": GRAPH, **arguments})
    payload = json.loads(response.content[0].text)
    assert "result" in payload, payload
    return payload["result"]


def _graph() -> InMemoryStorage:
    store = InMemoryStorage(database=GRAPH)
    store._graphs[GRAPH].metacontexts[BASE_METACONTEXT_ID] = Metacontext(
        id=BASE_METACONTEXT_ID, content="The Real", description="Claims about the real world."
    )
    return store


class TestThroughTheBoundary:
    """The server hands every merge path the process default from its config."""

    @pytest.fixture
    def config(self) -> ServerConfig:
        return ServerConfig(
            storage_backend="memory",
            embedding_provider="mock",
            merge_undo_depth=1,
            merge_cycle_limit=1,
        )

    async def test_configure_merge_reports_the_configured_default(self, config):
        async with _session(_graph(), config) as server:
            result = await _call(server, "configure_merge")

        assert (result["merge_undo_depth"], result["merge_cycle_limit"]) == (1, 1)

    async def test_merge_facts_gates_at_the_configured_cycle_limit(
        self, config, embedding_provider
    ):
        storage = _graph()
        a = await _fact(storage, embedding_provider, "Bonn is the capital.")
        b = await _fact(storage, embedding_provider, "The capital is Bonn.")
        await _merge_and_reverse(
            storage,
            embedding_provider,
            [a, b],
            Fact(content="Bonn is the capital city.", source_id="seg-1"),
        )

        async with _session(storage, config) as server:
            result = await _call(
                server, "merge_facts", source_ids=[a.id, b.id], content="Bonn is the capital city."
            )

        assert "merge_cycle_limit of 1" in result["refused"]

    async def test_merge_inferences_gates_at_the_configured_cycle_limit(
        self, config, embedding_provider
    ):
        storage = _graph()
        one = await _inference(storage, embedding_provider, "The release is unsafe")
        other = await _inference(storage, embedding_provider, "The release cannot be trusted")
        await _merge_and_reverse(
            storage,
            embedding_provider,
            [one, other],
            Inference(content="One reading.", source_id="seg-1"),
        )

        async with _session(storage, config) as server:
            result = await _call(
                server, "merge_inferences", source_ids=[one.id, other.id], content="One reading."
            )

        assert "merge_cycle_limit of 1" in result["refused"]

    async def test_a_topic_merge_keeps_the_configured_undo_depth(self, config, embedding_provider):
        storage = _graph()
        a = await _topic(storage, embedding_provider, "ML basics", _TWIN)
        b = await _topic(storage, embedding_provider, "Machine learning basics", _TWIN)

        async with _session(storage, config) as server:
            await _call(
                server,
                "apply_reflection",
                merges=[{"source_ids": [a.id, b.id], "content": "Machine learning basics"}],
            )
            [first] = [node.id for node in await storage.query_nodes(node_type=NodeType.TOPIC)]
            c = await _topic(
                storage, embedding_provider, "Basics of ML", await _vector_of(storage, first)
            )
            result = await _call(
                server,
                "apply_reflection",
                merges=[{"source_ids": [first, c.id], "content": "The basics of ML"}],
            )

        assert result["topics_merged"] == 1
        assert read_merge_undo(await storage.get_node(first)) is None
