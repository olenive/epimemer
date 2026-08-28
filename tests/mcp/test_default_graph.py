"""Which graph a server opens, and saying so out loud.

The active graph is **process state**. `use_graph` switches it and nothing
persists the switch, so a client reconnect starts a fresh process and lands back
on whatever configuration resolves to. That is intended — but it means a session
that spent an hour in one graph silently reopens somewhere else, and an ingest
into the wrong graph reports success in every other respect.

Two things follow, and both are pinned here. The default has to be a name
**nobody would give a real graph**, because a default that collides writes into
somebody's data and looks like it worked. And ingest has to **name the graph it
landed in**, because it is the only signal the agent gets before the nodes exist.

Written after an agent ingested 61 nodes of one project's material into another
project's graph, having reconnected mid-session.
"""

import pytest

from epimemer.core.types import (
    BASE_METACONTEXT_ID,
    EmbeddingRecord,
    Fact,
    NodeStatus,
)
from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.mcp import tools
from epimemer.mcp.config import ServerConfig, create_storage


async def _fact(storage, embedder, content: str) -> Fact:
    node = Fact(content=content, source_id="seg1")
    await storage.store_node(node)
    vectors = await embedder.embed([content])
    await storage.store_embedding(EmbeddingRecord(
        item_id=node.id, model_id=embedder.model_id, vector=vectors[0]
    ))
    return node


@pytest.fixture
def embedder():
    return MockEmbeddingProvider(model_id="mock-embed", dimension=8)


@pytest.fixture
def config():
    return ServerConfig(storage_backend="memory", embedding_provider="mock")


class TestWhichGraphAServerOpens:
    def test_nothing_configured_lands_on_the_default_graph(self):
        storage = create_storage(ServerConfig(storage_backend="surrealdb"))

        assert storage.current_database == "default"

    def test_the_default_is_not_a_name_anyone_would_use(self):
        """The regression this file exists for. It was `"memory"`, which is
        also what the dev-history graph is called, so a server started without
        `EPIMEMER_GRAPH` opened a real graph full of unrelated material."""
        assert ServerConfig().surrealdb_database == "default"

    def test_epimemer_graph_wins(self):
        storage = create_storage(
            ServerConfig(storage_backend="surrealdb", graph="field-notes")
        )

        assert storage.current_database == "field-notes"

    def test_the_database_setting_is_used_when_no_graph_is_named(self):
        storage = create_storage(
            ServerConfig(storage_backend="surrealdb", surrealdb_database="archive")
        )

        assert storage.current_database == "archive"

    def test_an_empty_graph_setting_means_unset(self):
        """Not a graph named "". The two would be indistinguishable in an
        environment variable, and one of them is a typo."""
        storage = create_storage(
            ServerConfig(
                storage_backend="surrealdb", graph="", surrealdb_database="archive"
            )
        )

        assert storage.current_database == "archive"

    def test_a_second_server_lands_where_configuration_says(self):
        """Construction is the only thing that decides, and it has no memory of
        where a previous process ended up — which is exactly what makes a
        reconnect mid-session land somewhere the agent did not choose."""
        config = ServerConfig(storage_backend="surrealdb", graph="field-notes")

        first = create_storage(config)
        second = create_storage(config)

        assert first.current_database == second.current_database == "field-notes"


class TestIngestSaysWhereItLanded:
    async def test_segment_names_the_active_graph(self, storage, embedder, config):
        """The earliest place it can be said: before anything is decomposed."""
        result, _ = await tools.segment_text(
            "A report.", storage, embedder, config,
        )

        assert result["active_graph"] == storage.current_database

    async def test_store_decomposition_names_it_again(
        self, storage, embedder, config
    ):
        """Repeated rather than assumed unchanged — the two calls are separate
        requests, and a reconnect can land between them."""
        seg, _ = await tools.segment_text("A report.", storage, embedder, config)

        result, _ = await tools.store_decomposition(
            document_id=seg["document_id"],
            segments=[{
                "segment_id": seg["segments"][0]["segment_id"],
                "topics": ["a topic"], "facts": [], "inferences": [],
            }],
            storage=storage,
            embedding_provider=embedder,
            metacontext_id=BASE_METACONTEXT_ID,
        )

        assert result["active_graph"] == storage.current_database

    async def test_it_follows_a_switch(self, storage, embedder, config):
        await tools.use_graph("elsewhere", storage, confirm=True)

        result, _ = await tools.segment_text(
            "A report.", storage, embedder, config,
        )

        assert result["active_graph"] == "elsewhere"


class TestTheGuardMovedToTheBoundary:
    """`tests/mcp/test_graph_gate.py` is where the wrong-graph gate is tested
    now, and the move is the finding rather than a tidy-up.

    These tests used to call `tools.segment_text(..., expected_graph=...)`
    directly and assert on the refusal dict. They passed, and the refusal they
    asserted on **never reached an agent**: at the MCP boundary the tool's own
    success summariser ran over that dict inside `_log`, raised `KeyError`, and
    the response became `{"error": "'segments'"}` — with the sentence telling
    the agent to call `use_graph` gone.

    **A test at the layer below the boundary cannot see what the boundary does
    to the answer.** That is the carry-forward, and it is why the replacement
    goes through `mcp.call_tool` even though it is slower and more setup.
    """

    async def test_the_gate_is_not_at_this_layer_any_more(self, storage, embedder, config):
        """One home for the policy, on `_judge_for_write`'s reasoning: a second
        check on its own account could differ from the first without anybody
        noticing. A caller down here passes its own storage handle and has no
        ambient active graph to be wrong about."""
        import inspect

        assert "expected_graph" not in inspect.signature(tools.segment_text).parameters
        assert "expected_graph" not in inspect.signature(tools.store_decomposition).parameters
        assert "expected_graph" not in inspect.signature(tools.restore).parameters


class TestWhyAnIdThatDoesNotResolveIsNotEnough:
    """The guard first covered three tools, on the argument that every other
    write dereferences a node id and so already fails on the wrong graph. The
    calls below do fail — and each failure is worse than a refusal, which is
    what the mandatory `expected_graph` overturned.

    It also ignored reads entirely, and a wrong-graph read is the worse half: a
    misfiled write leaves the material and its journal row together in the graph
    that received them, while a wrong-graph `search` returns a plausible answer
    the agent reasons from and leaves nothing behind at all.
    """

    async def test_a_raise_does_not_say_which_graph(self, storage, embedder):
        """*Node not found* sends an agent looking for a missing node. The next
        move is a workaround; it should have been `use_graph`."""
        node = await _fact(storage, embedder, "a claim")
        await tools.use_graph("elsewhere", storage, confirm=True)

        with pytest.raises(ValueError) as raised:
            await tools.judge_importance(node.id, "up", "cited", storage)

        assert "graph" not in str(raised.value).lower()

    async def test_linking_across_a_switch_raises_the_same_way(
        self, storage, embedder
    ):
        a = await _fact(storage, embedder, "one")
        b = await _fact(storage, embedder, "two")
        await tools.use_graph("elsewhere", storage, confirm=True)

        with pytest.raises(ValueError):
            await tools.link(a.id, b.id, storage, relation="about")

    async def test_reflection_does_not_even_raise(self, storage, embedder):
        """It skips, silently, and reports a successful reflection that applied
        nothing. That is the failure this whole issue is about wearing a
        different hat."""
        node = await _fact(storage, embedder, "a trivial aside")
        await tools.use_graph("elsewhere", storage, confirm=True)

        result, _ = await tools.apply_reflection(
            storage, embedder, archivals=[node.id],
        )

        assert result["nodes_archived"] == 0
        assert "refused" not in result, "no refusal, no error — just nothing"

    async def test_a_read_in_the_wrong_graph_answers_rather_than_failing(
        self, storage, embedder
    ):
        """No id to fail on. The agent asked a question and got an answer, and
        nothing anywhere records that it came from the wrong place."""
        await _fact(storage, embedder, "the deployment rolled back")
        await tools.use_graph("elsewhere", storage, confirm=True)

        result, _ = await tools.search(
            "deployment", storage, embedder, k=5,
        )

        assert result["nodes"] == [] and result["segments"] == []
        assert "refused" not in result and "error" not in result
