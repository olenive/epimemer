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

from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.mcp import tools
from epimemer.mcp.config import ServerConfig, create_storage


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
