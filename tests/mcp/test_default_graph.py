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

from epimemer.core.types import EmbeddingRecord, Fact, NodeStatus
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
        )

        assert result["active_graph"] == storage.current_database

    async def test_it_follows_a_switch(self, storage, embedder, config):
        await tools.use_graph("elsewhere", storage, confirm=True)

        result, _ = await tools.segment_text(
            "A report.", storage, embedder, config,
        )

        assert result["active_graph"] == "elsewhere"


class TestRefusingAWriteToTheWrongGraph:
    """`active_graph` in the response is a hint an agent may read. This is the
    check the machine makes.

    The incident that prompted both was silent precisely because every response
    said success — so answering it with a *better success response* leaves the
    failure attention-dependent, which is the same shape one layer along.
    """

    async def test_a_mismatch_refuses_before_anything_is_written(
        self, storage, embedder, config
    ):
        result, _ = await tools.segment_text(
            "A report.", storage, embedder, config, expected_graph="field-notes",
        )

        assert "refused" in result
        assert result["active_graph"] == storage.current_database
        assert result["expected_graph"] == "field-notes"
        assert await storage.query_nodes() == []
        assert "document_id" not in result, "nothing was stored to refer to"

    async def test_the_refusal_says_how_to_recover(self, storage, embedder, config):
        """The agent can fix this itself, and the message has to say so — the
        reconnect that caused it is invisible from where the agent stands."""
        result, _ = await tools.segment_text(
            "A report.", storage, embedder, config, expected_graph="field-notes",
        )

        assert "use_graph('field-notes')" in result["refused"]
        assert "reconnect" in result["refused"]

    async def test_a_match_proceeds(self, storage, embedder, config):
        result, _ = await tools.segment_text(
            "A report.", storage, embedder, config,
            expected_graph=storage.current_database,
        )

        assert "refused" not in result
        assert result["document_id"]

    async def test_omitting_it_proceeds(self, storage, embedder, config):
        """Optional on purpose: a single-graph server has nothing to confuse,
        and requiring it there would be ceremony."""
        result, _ = await tools.segment_text("A report.", storage, embedder, config)

        assert "refused" not in result

    async def test_store_decomposition_is_checked_independently(
        self, storage, embedder, config
    ):
        """The case the incident actually took. A document segmented in the
        wrong graph *has* its segments there, so step two is internally
        consistent and lands the whole decomposition beside it — the existing
        `Segment not found` guard never fires, and cannot."""
        seg, _ = await tools.segment_text("A report.", storage, embedder, config)

        result, _ = await tools.store_decomposition(
            document_id=seg["document_id"],
            segments=[{
                "segment_id": seg["segments"][0]["segment_id"],
                "topics": ["a topic"], "facts": [], "inferences": [],
            }],
            storage=storage,
            embedding_provider=embedder,
            expected_graph="field-notes",
        )

        assert "refused" in result
        assert await storage.query_nodes() == [], "nothing was decomposed"

    async def test_restore_is_checked_because_a_blob_names_no_graph(
        self, storage, embedder, config
    ):
        """The other write that carries its own content: an archive restores
        into whichever graph is active, since nothing in it says which."""
        node = await _fact(storage, embedder, "a trivial aside")
        exported, _ = await tools.apply_reflection(
            storage, embedder, archivals=[node.id],
        )

        result, _ = await tools.restore(
            storage, archive_data=exported["archive_data"], expected_graph="field-notes",
        )

        assert "refused" in result
        assert (await storage.get_node(node.id)).status is NodeStatus.ARCHIVED

    async def test_the_guard_follows_a_switch(self, storage, embedder, config):
        await tools.use_graph("elsewhere", storage, confirm=True)

        result, _ = await tools.segment_text(
            "A report.", storage, embedder, config, expected_graph="elsewhere",
        )

        assert "refused" not in result


class TestWhichWritesNeedTheGuardAtAll:
    """The list is complete rather than a starting point: only a tool that
    creates content **without dereferencing an existing id** can land silently
    in the wrong graph. Everything else takes node ids, and an id from another
    graph names nothing here."""

    async def test_a_node_id_from_another_graph_already_fails(
        self, storage, embedder
    ):
        node = await _fact(storage, embedder, "a claim")
        await tools.use_graph("elsewhere", storage, confirm=True)

        with pytest.raises(ValueError):
            await tools.judge_importance(
                node.id, "up", "cited", storage,
            )

    async def test_linking_across_a_switch_fails(self, storage, embedder):
        a = await _fact(storage, embedder, "one")
        b = await _fact(storage, embedder, "two")
        await tools.use_graph("elsewhere", storage, confirm=True)

        with pytest.raises(ValueError):
            await tools.link(a.id, b.id, storage, relation="about")

    async def test_reflection_applies_nothing_it_cannot_find(
        self, storage, embedder
    ):
        """It skips rather than raising, which is the same protection reached a
        different way: no write lands."""
        node = await _fact(storage, embedder, "a trivial aside")
        await tools.use_graph("elsewhere", storage, confirm=True)

        result, _ = await tools.apply_reflection(
            storage, embedder, archivals=[node.id],
        )

        assert result["nodes_archived"] == 0
