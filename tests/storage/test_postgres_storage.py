"""What only the Postgres backend can get wrong.

Every behavioural test already runs against this backend through the `storage`
and `store` fixtures when `EPIMEMER_TEST_POSTGRES_DSN` is set. This file holds
the properties that come from its being a shared server: two instances on one
graph see each other's writes and settings, counters bumped at once all count,
calls in flight together in one process do not interleave, a graph is a schema
that a delete drops and that nothing else is mistaken for, a dropped connection
is rebuilt, a transaction that fails leaves nothing, timestamps compare as
instants, a vector passes through a blob, and every filter column agrees with
the record it was copied from.

The tests that need a server skip without the DSN, as the fixtures do. The few
that only parse a DSN or name a schema need the driver and nothing else, and
skip where the `postgres` extra is not installed.
"""

import asyncio
import contextlib
import inspect
import itertools
import math
import random
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest

from epimemer.core.types import (
    DecisionKind,
    DecisionRecord,
    EdgeType,
    EmbeddingRecord,
    Fact,
    NodeEdge,
    NodeStatus,
    NodeType,
    RawDocument,
    Segment,
    Timeline,
    Topic,
)
from epimemer.storage.protocol import (
    PassagesCited,
    SchemaTooNew,
    StorageBackend,
    TimelineConflict,
)
from tests.postgres_store import clean_postgres_store, open_postgres, postgres_dsn
from tests.storage.test_sqlite_storage import AT, _Graph, _populate

WHOLE = datetime(2026, 8, 23, 12, 0, 41, tzinfo=UTC)


def _adapter() -> Any:
    """The adapter module, or a skip where the `postgres` extra is not installed."""
    return pytest.importorskip(
        "epimemer.storage.postgres_adapter", reason="the postgres extra is not installed"
    )


def _psycopg() -> Any:
    return pytest.importorskip("psycopg", reason="the postgres extra is not installed")


@contextlib.asynccontextmanager
async def _admin() -> AsyncIterator[Any]:
    """A plain connection to the test database, for what no backend method does:
    making a schema that is not a graph, ending another connection, reading a
    column as stored."""
    dsn = postgres_dsn()
    connection = await _psycopg().AsyncConnection.connect(dsn, autocommit=True)
    try:
        yield connection
    finally:
        await connection.close()


async def _rows(query: Any, params: Any = ()) -> list[tuple]:
    async with _admin() as connection:
        cursor = await connection.execute(query, params)
        return await cursor.fetchall()


async def _schema_exists(schema: str) -> bool:
    rows = await _rows("SELECT 1 FROM pg_catalog.pg_namespace WHERE nspname = %s", (schema,))
    return bool(rows)


async def _terminate(store: Any) -> None:
    """End the store's server-side session, as a restart or a failover would.

    Waits until the server has finished it, so the store's next statement meets
    a closed socket rather than racing the shutdown.
    """
    pid = store._connection.info.backend_pid
    rows = await _rows("SELECT pg_terminate_backend(%s, 5000)", (pid,))
    assert rows == [(True,)]


@pytest.fixture
async def store() -> AsyncIterator[Any]:
    async with clean_postgres_store() as s:
        yield s


@pytest.fixture
async def another() -> AsyncIterator[Any]:
    """Another instance on the same database: another server process, or the CLI.

    Torn down before `store`, which then drops every graph either of them made.
    """
    opened = []

    async def factory(database: str = "default") -> Any:
        s = await open_postgres(database)
        opened.append(s)
        return s

    yield factory
    for s in opened:
        await s.close()


class TestTheWholeProtocol:
    """The parity suite reaches this backend only when a DSN is set, so a method
    missing here, or one whose arguments drifted from the protocol's, would pass
    a default run unseen. These need the driver and no server."""

    def test_every_protocol_member_is_implemented(self):
        backend = _adapter().PostgresStorage
        members = [name for name in dir(StorageBackend) if not name.startswith("_")]

        assert [name for name in members if not hasattr(backend, name)] == []

    def test_every_member_has_the_protocols_shape(self):
        """Same parameters, kinds and defaults, async where the protocol is, and a
        property where the protocol has one."""
        backend = _adapter().PostgresStorage

        def shape(member: Any) -> Any:
            if isinstance(member, property):
                return "property"
            parameters = inspect.signature(member).parameters.values()
            return (
                inspect.iscoroutinefunction(member),
                [(p.name, p.kind, p.default) for p in parameters],
            )

        drifted = {
            name: (shape(getattr(StorageBackend, name)), shape(getattr(backend, name)))
            for name in dir(StorageBackend)
            if not name.startswith("_")
            and shape(getattr(StorageBackend, name)) != shape(getattr(backend, name))
        }

        assert drifted == {}


class TestTwoInstancesShareAGraph:
    """The reason this backend exists: several servers writing one graph."""

    async def test_each_sees_the_others_writes(self, store, another):
        other = await another()

        await store.store_node(_Graph.fact)
        assert await other.get_node("f1") == _Graph.fact

        await other.store_node(_Graph.topic)
        assert await store.get_node("t1") == _Graph.topic

    async def test_approved_ids_set_by_one_are_the_others_next_read(self, store, another):
        """`epimemer agents confirm` writes the approved list from another process,
        and the server has to see it without restarting."""
        other = await another()
        assert await store.get_approved_agent_ids() == []

        await other.set_approved_agent_ids(["k1"])

        assert await store.get_approved_agent_ids() == ["k1"]

    async def test_bumps_from_both_at_once_all_count(self, store, another):
        other = await another()

        counts = await asyncio.gather(
            *(instance.bump_reflect_counter() for _ in range(25) for instance in (store, other))
        )

        # Every bump saw a count nobody else saw, and none was lost.
        assert sorted(counts) == list(range(1, 51))
        assert await other.get_reflect_counter() == 50

    async def test_a_reset_racing_bumps_loses_none_of_them(self, store, another):
        other = await another()
        for _ in range(5):
            await store.bump_backup_counter()

        results = await asyncio.gather(
            other.reset_backup_counter(), *(store.bump_backup_counter() for _ in range(10))
        )

        # What the reset took plus what was left after it is every bump made.
        assert results[0] + await store.get_backup_counter() == 15

    async def test_a_graph_one_creates_the_other_lists_and_reads(self, store, another):
        other = await another()

        await other.switch_database("team")
        await other.store_node(_Graph.topic)

        assert await store.list_databases() == ["default", "team"]
        assert [node.id for node in await store.viz_list_nodes("team")] == ["t1"]

    async def test_two_opening_a_new_graph_at_once_create_it_once(self, store, another):
        first, second = await another(), await another()

        await asyncio.gather(first.switch_database("fresh"), second.switch_database("fresh"))
        await first.store_node(_Graph.topic)

        assert await second.get_node("t1") == _Graph.topic
        assert await store.list_databases() == ["default", "fresh"]

    async def test_a_recut_waits_for_claims_another_server_is_writing(self, store, another):
        """The re-cut's check sees what another server has not committed yet.

        At Postgres's default isolation a count reads only committed rows, so
        without the lock on the node table a re-cut would count none, delete
        the passage, and leave the other server's claim citing nothing. With it
        the re-cut waits for that transaction, counts the claim, and refuses.
        """
        adapter = _adapter()
        other = await another()
        doc = RawDocument(id="d-recut", content="One passage about the harbour.")
        whole = Segment(id="s-whole", source_id=doc.id, text=doc.content, span_start=0, span_end=10)
        await store.store_document(doc)
        await store.store_segment(whole)
        written, release = asyncio.Event(), asyncio.Event()

        async def decomposing(connection: Any, g: Any) -> None:
            claim = Fact(id="f-cites", content="The harbour", source_id=whole.id)
            await adapter._put_node(connection, g, claim)
            written.set()
            # Held open, as a decomposition still writing would be.
            await release.wait()

        holding = asyncio.create_task(other._atomic(decomposing))
        await written.wait()
        recut = asyncio.create_task(
            store.resegment_tx(
                doc.id,
                [Segment(source_id=doc.id, text=doc.content, span_start=0, span_end=10)],
                segmentation={"strategy": "agent", "passages": 1, "history": []},
            )
        )
        try:
            await asyncio.sleep(0.3)
            waited = not recut.done()
        finally:
            # Released whatever happened, or the open transaction would hold
            # the teardown's schema drop for ever.
            release.set()
            await holding

        assert waited, "the re-cut ran while another server was writing claims"
        with pytest.raises(PassagesCited) as refused:
            await recut

        assert refused.value.citing == 1
        assert [s.id for s in await store.get_segments_for_document(doc.id)] == [whole.id]

    async def test_a_second_writer_with_a_stale_timeline_is_refused(self, store, another):
        """Two people editing one timeline: the second write was made from a copy
        the first one has since replaced, so it is refused rather than written
        over the first."""
        other = await another()
        await store.store_timeline(Timeline(id="tl-shared", name="the parish"))
        mine = await store.get_timeline("tl-shared")
        theirs = await other.get_timeline("tl-shared")

        await store.store_timeline(mine.model_copy(update={"description": "mine"}))
        with pytest.raises(TimelineConflict) as refused:
            await other.store_timeline(theirs.model_copy(update={"description": "theirs"}))

        assert (refused.value.expected, refused.value.found) == (1, 2)
        kept = await other.get_timeline("tl-shared")
        assert (kept.version, kept.description) == (2, "mine")

    async def test_of_two_writes_from_one_version_at_once_exactly_one_lands(self, store, another):
        """The race itself, on two connections: whichever `UPDATE` waits on the
        other's row lock re-reads the row once it commits and matches nothing."""
        other = await another()
        ids = [f"tl-race-{i}" for i in range(10)]
        for timeline_id in ids:
            await store.store_timeline(Timeline(id=timeline_id, name="raced"))
        read = {timeline_id: await store.get_timeline(timeline_id) for timeline_id in ids}

        results = await asyncio.gather(
            *(
                instance.store_timeline(
                    read[timeline_id].model_copy(update={"description": writer})
                )
                for timeline_id in ids
                for instance, writer in ((store, "store"), (other, "other"))
            ),
            return_exceptions=True,
        )

        for timeline_id, outcome in zip(
            ids, itertools.batched(results, 2, strict=True), strict=True
        ):
            landed = [result for result in outcome if result == timeline_id]
            refused = [result for result in outcome if isinstance(result, TimelineConflict)]
            assert (len(landed), len(refused)) == (1, 1), outcome
            stored = await store.get_timeline(timeline_id)
            assert stored.version == 2
            assert stored.description == ("store" if outcome[0] == timeline_id else "other")

    async def test_of_two_creating_one_timeline_at_once_exactly_one_lands(self, store, another):
        other = await another()

        results = await asyncio.gather(
            store.store_timeline(Timeline(id="tl-new", name="by store")),
            other.store_timeline(Timeline(id="tl-new", name="by other")),
            return_exceptions=True,
        )

        assert sorted(type(result).__name__ for result in results) == ["TimelineConflict", "str"]
        stored = await store.get_timeline("tl-new")
        assert stored.version == 1
        assert stored.name == ("by store" if results[0] == "tl-new" else "by other")


class TestOneInstanceUnderConcurrentCalls:
    """Two tool calls in flight in one process share one connection, and a
    transaction's statements must not be joined by another call's."""

    async def test_writes_alongside_a_failing_batch_are_kept(self, store):
        """Were a plain write to run between the batch's `BEGIN` and its failure,
        it would join the transaction and be rolled back with it."""
        await store.store_node(_Graph.topic)
        fresh = Fact(id="f-new", content="new", source_id="s1")
        others = [Fact(id=f"f{i}", content=f"fact {i}", source_id="s1") for i in range(20)]

        results = await asyncio.gather(
            store.write_batch_tx(nodes=[fresh, _Graph.topic]),
            *(store.store_node(fact) for fact in others),
            return_exceptions=True,
        )

        assert isinstance(results[0], _psycopg().errors.UniqueViolation)
        assert all(result == fact.id for result, fact in zip(results[1:], others, strict=True))
        assert await store.get_node("f-new") is None
        assert set(await store.get_nodes([fact.id for fact in others])) == {
            fact.id for fact in others
        }

    async def test_batches_and_bumps_together_all_land(self, store):
        facts = [Fact(id=f"f{i}", content=f"fact {i}", source_id="s1") for i in range(10)]
        edges = [
            NodeEdge(id=f"e{i}", src_id=f"f{i}", dst_id="t1", type=EdgeType.TAGGED_WITH_TOPIC)
            for i in range(10)
        ]

        await asyncio.gather(
            *(
                store.write_batch_tx(nodes=[fact], edges=[edge])
                for fact, edge in zip(facts, edges, strict=True)
            ),
            *(store.bump_backup_counter() for _ in range(10)),
        )

        assert len(await store.query_nodes(node_type=NodeType.FACT)) == 10
        assert len(await store.query_edges()) == 10
        assert await store.get_backup_counter() == 10


class TestDeletingAGraph:
    async def test_the_schema_goes(self, store):
        await store.switch_database("other")
        await store.store_node(_Graph.topic)
        await store.switch_database("default")

        await store.delete_database("other")

        assert not await _schema_exists("other")
        assert await store.list_databases() == ["default"]

    async def test_the_active_graph_is_refused(self, store):
        with pytest.raises(ValueError):
            await store.delete_database("default")

        assert await _schema_exists("default")
        assert await store.list_databases() == ["default"]

    async def test_a_name_with_no_graph_is_refused(self, store):
        with pytest.raises(KeyError):
            await store.delete_database("never-made")


class TestOnlyMarkedSchemasAreGraphs:
    """A team's database holds other schemas. None of them is offered as a
    graph, dropped as one, or filled with this backend's tables."""

    @pytest.fixture
    async def app_schema(self, store) -> AsyncIterator[str]:
        """Another application's schema, with a `schema_version` table of its own:
        the name Flyway and many migration scripts use."""
        async with _admin() as connection:
            await connection.execute('CREATE SCHEMA "app"')
            await connection.execute('CREATE TABLE "app".schema_version (version integer)')
            await connection.execute('INSERT INTO "app".schema_version VALUES (7)')
        try:
            yield "app"
        finally:
            async with _admin() as connection:
                await connection.execute('DROP SCHEMA IF EXISTS "app" CASCADE')

    async def test_it_is_not_listed(self, store, app_schema):
        assert await store.list_databases() == ["default"]

    async def test_it_is_not_dropped(self, store, app_schema):
        with pytest.raises(KeyError):
            await store.delete_database(app_schema)

        assert await _rows('SELECT version FROM "app".schema_version') == [(7,)]

    async def test_it_is_not_opened(self, store, app_schema):
        with pytest.raises(ValueError, match="not the Epimemer graph"):
            await store.switch_database(app_schema)

        assert store.current_database == "default"
        assert await _rows(
            "SELECT relname FROM pg_catalog.pg_class AS c "
            "JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'app' AND c.relkind = 'r'"
        ) == [("schema_version",)]

    async def test_it_is_not_read(self, store, app_schema):
        assert await store.viz_list_nodes(app_schema) == []
        assert await store.count_decisions_by_graph([app_schema]) == {}

    async def test_an_empty_schema_made_for_a_graph_is_adopted(self, store):
        """A DBA who creates the schema and grants it to Epimemer's role has made a
        place for the graph, and it is used."""
        async with _admin() as connection:
            await connection.execute('CREATE SCHEMA "made-by-dba"')

        await store.switch_database("made-by-dba")
        await store.store_node(_Graph.topic)

        assert await store.list_databases() == ["default", "made-by-dba"]
        assert await store.get_node("t1") == _Graph.topic


class TestNamesPostgresCannotHoldAsTheyAre:
    """A graph name may be 64 characters and may begin `pg_`; a schema name may
    not. Both still work, in a schema whose name is derived."""

    LONG_B = "A" * 63 + "B"
    LONG_C = "A" * 63 + "C"

    async def test_two_64_character_names_alike_in_63_are_two_graphs(self, store):
        await store.switch_database(self.LONG_B)
        await store.store_node(_Graph.topic)
        await store.switch_database(self.LONG_C)
        await store.store_node(_Graph.fact)
        await store.switch_database("default")

        assert await store.list_databases() == sorted(["default", self.LONG_B, self.LONG_C])
        assert [n.id for n in await store.viz_list_nodes(self.LONG_B)] == ["t1"]
        assert [n.id for n in await store.viz_list_nodes(self.LONG_C)] == ["f1"]
        assert await store.count_decisions_by_graph([self.LONG_B]) == {self.LONG_B: 0}

        await store.delete_database(self.LONG_B)

        assert await store.list_databases() == sorted(["default", self.LONG_C])

    async def test_a_name_beginning_pg_is_a_graph_like_any_other(self, store):
        await store.switch_database("pg_notes")
        await store.store_node(_Graph.topic)

        assert store.current_database == "pg_notes"
        assert await store.list_databases() == ["default", "pg_notes"]
        assert await store.get_node("t1") == _Graph.topic

    def test_a_derived_schema_name_is_never_a_graph_name(self):
        adapter = _adapter()

        derived = adapter._schema_for(self.LONG_B)

        assert len(derived) == 63
        assert derived.startswith("~")
        with pytest.raises(ValueError):
            adapter.validate_graph_name(derived)
        assert adapter._schema_for("default") == "default"


class TestLocation:
    """Where the graphs are, for the startup log and `list_graphs`, which an agent
    reads: so never the password."""

    def test_a_url_gives_host_port_and_database_and_not_the_password(self):
        location = (
            _adapter()
            .PostgresStorage("postgresql://team:s3cret-pw@db.example.org:6543/teamdb")
            .location
        )

        assert "db.example.org:6543" in location
        assert "teamdb" in location
        assert "s3cret-pw" not in location

    def test_key_value_pairs_are_read_the_same_way(self):
        location = (
            _adapter()
            .PostgresStorage(
                "host=db.example.org port=6543 dbname=teamdb user=team password=s3cret-pw "
                "sslmode=require"
            )
            .location
        )

        assert "db.example.org:6543" in location
        assert "teamdb" in location
        assert "s3cret-pw" not in location

    def test_a_malformed_dsn_is_refused_when_the_store_is_made(self):
        adapter = _adapter()

        with pytest.raises(_psycopg().ProgrammingError):
            adapter.PostgresStorage("host=db.example.org port")

    async def test_the_server_under_test_is_named(self, store):
        conninfo = _psycopg().conninfo.conninfo_to_dict(postgres_dsn())

        assert conninfo["host"] in store.location


class TestADroppedConnection:
    """A server outlives the database it talks to: a restart or a failover ends
    the session under a connection that looks the same."""

    async def test_the_next_read_reconnects_and_answers(self, store):
        await store.store_node(_Graph.topic)
        await _terminate(store)

        assert await store.get_node("t1") == _Graph.topic

    async def test_the_next_write_reconnects_and_lands_once(self, store):
        await _terminate(store)

        await store.store_node(_Graph.topic)

        assert await store.query_nodes(node_type=NodeType.TOPIC) == [_Graph.topic]

    async def test_a_transaction_fails_and_the_call_after_it_reconnects(self, store):
        """The transaction went with the socket, so it is reported rather than
        retried; the next call starts on a new connection."""
        await _terminate(store)

        with pytest.raises(_psycopg().OperationalError):
            await store.write_batch_tx(nodes=[_Graph.fact])

        assert await store.get_node("f1") is None
        await store.write_batch_tx(nodes=[_Graph.fact])
        assert await store.get_node("f1") == _Graph.fact

    async def test_the_active_graph_survives_a_reconnect(self, store):
        await store.switch_database("other")
        await _terminate(store)

        await store.store_node(_Graph.topic)

        assert store.current_database == "other"
        assert [n.id for n in await store.viz_list_nodes("other")] == ["t1"]
        assert await store.viz_list_nodes("default") == []

    async def test_a_closed_store_says_so_rather_than_reconnecting(self, store):
        await store.close()

        with pytest.raises(RuntimeError, match="Not connected"):
            await store.get_node("t1")


class TestAGraphOutlivesItsConnection:
    async def test_every_record_and_setting_reads_back_from_a_new_instance(self, store, another):
        await _populate(store)
        await store.close()

        reader = await another()
        g = _Graph
        assert await reader.get_document("d1") == g.document
        assert await reader.get_segments(["s1"]) == {"s1": g.segment}
        assert await reader.get_nodes(["t1", "f1", "i1", "f0"]) == {
            "t1": g.topic,
            "f1": g.fact,
            "i1": g.inference,
            "f0": g.retired,
        }
        assert {edge.id: edge for edge in await reader.query_edges()} == {
            edge.id: edge for edge in g.edges
        }
        assert await reader.get_embeddings_for_item("f1") == [g.embedding]
        # Stored once, so at version 1.
        assert await reader.get_timeline("tl1") == g.timeline.model_copy(update={"version": 1})
        assert await reader.get_metacontext("mc1") == g.metacontext
        assert await reader.get_relation_label("reported_in", "relationship") == g.label
        assert list(await reader.query_relation_verdicts()) == [g.verdict]
        assert await reader.get_agent("k1") == g.agent
        assert await reader.query_decisions() == [g.review, g.decision]

        assert await reader.get_reflect_counter() == 3
        assert await reader.get_reflect_threshold_override() == 7
        assert await reader.get_backup_counter() == 2
        assert await reader.get_backup_threshold_override() == 11
        assert (await reader.get_merge_overrides()).undo_depth == 3
        assert await reader.get_approved_agent_ids() == ["k1"]
        assert await reader.get_judge_confirmations() == [g.confirmation]
        assert await reader.get_require_judge() is True

    async def test_connect_after_close_works_again(self, store):
        await store.store_node(_Graph.topic)
        await store.close()
        await store.connect()

        assert await store.get_node("t1") == _Graph.topic

    async def test_a_new_graph_records_its_name_and_starts_at_version_one(self, store):
        assert await _rows('SELECT name, version FROM "default".epimemer_graph') == [("default", 1)]


class TestAFailedTransactionLeavesNothing:
    async def test_a_batch_that_fails_on_its_second_node_keeps_none_of_it(self, store):
        await store.store_node(_Graph.topic)
        await store.store_timeline(_Graph.timeline)
        renamed = _Graph.timeline.model_copy(update={"name": "Renamed", "version": 1})
        fresh = Fact(id="f-new", content="new", source_id="s1")

        # Insert-only: an id already in the graph fails the batch.
        with pytest.raises(_psycopg().errors.UniqueViolation):
            await store.write_batch_tx(nodes=[fresh, _Graph.topic], timelines=[renamed])

        assert await store.get_node("f-new") is None
        assert (await store.get_timeline("tl1")).name == "Work"

    async def test_a_merge_that_fails_after_its_first_writes_is_undone(self, store):
        """The merged node, its embedding and the edge migration are written before
        the missing source is discovered, and none of them may survive it."""
        source = Fact(id="f1", content="a", source_id="s1")
        await store.store_node(source)
        await store.store_edge(
            NodeEdge(id="e1", src_id="f1", dst_id="t1", type=EdgeType.TAGGED_WITH_TOPIC)
        )
        merged = Fact(id="m1", content="a and b", source_id="s1")

        with pytest.raises(KeyError):
            await store.merge_nodes_tx(
                [source, Fact(id="missing", content="b", source_id="s1")],
                merged,
                EmbeddingRecord(item_id="m1", model_id="m", vector=[1.0, 0.0]),
                [],
                merged_at=AT,
            )

        assert await store.get_node("m1") is None
        assert await store.get_embeddings_for_item("m1") == []
        assert await store.get_node("f1") == source
        assert [(e.src_id, e.dst_id) for e in await store.query_edges()] == [("f1", "t1")]

    async def test_a_refused_reversal_puts_back_what_it_had_already_changed(self, store):
        survivor = Fact(id="m1", content="merged", source_id="s1")
        source = Fact(
            id="f1", content="a", source_id="s1", status=NodeStatus.MERGED, superseded_at=AT
        )
        await store.store_node(survivor)
        await store.store_node(source)
        moved = NodeEdge(id="e1", src_id="m1", dst_id="t1", type=EdgeType.TAGGED_WITH_TOPIC)
        kept = NodeEdge(id="e2", src_id="m1", dst_id="t2", type=EdgeType.TAGGED_WITH_TOPIC)
        await store.store_edge(moved)
        await store.store_edge(kept)

        # `e2` is not in the plan, so the survivor still has an edge after the
        # deletions, and the reversal must refuse.
        with pytest.raises(ValueError):
            await store.reverse_merge_tx(
                survivor, [source], [], restored_at=AT, delete_edge_ids=["e1"]
            )

        assert (await store.get_node("f1")).status is NodeStatus.MERGED
        assert await store.get_node("m1") == survivor
        assert {e.id for e in await store.query_edges()} == {"e1", "e2"}

    async def test_the_connection_is_usable_and_committed_after_a_rollback(self, store, another):
        reader = await another()
        await store.store_node(_Graph.topic)
        with pytest.raises(_psycopg().errors.UniqueViolation):
            await store.write_batch_tx(nodes=[_Graph.fact, _Graph.topic])

        await store.store_node(_Graph.inference)

        assert set(await reader.get_nodes(["t1", "f1", "i1"])) == {"t1", "i1"}


class TestATimelineWrittenBeforeItsVersion:
    """A record stored before `Timeline.version` existed has no `version` key,
    and reads, and is checked, as version 0."""

    async def test_it_takes_a_write_at_version_zero(self, store):
        await _rows(
            'INSERT INTO "default".timeline (uid, data) VALUES (%s, %s::jsonb) RETURNING uid',
            ("tl-old", '{"id": "tl-old", "name": "older"}'),
        )

        older = await store.get_timeline("tl-old")
        assert older.version == 0
        await store.store_timeline(older.model_copy(update={"name": "revised"}))

        assert (await store.get_timeline("tl-old")).version == 1
        with pytest.raises(TimelineConflict):
            await store.store_timeline(older.model_copy(update={"name": "stale"}))


class TestTimestampsCompareAsInstants:
    """The whole-second case that text comparison gets wrong cannot arise with
    `timestamptz` bounds; these pin that it does not."""

    async def test_a_node_born_on_a_whole_second_is_inside_a_bound_half_a_second_later(self, store):
        await store.store_node(Fact(id="f1", content="x", source_id="s1", created_at=WHOLE))

        later = await store.query_nodes(at_time=WHOLE + timedelta(milliseconds=500))
        earlier = await store.query_nodes(at_time=WHOLE - timedelta(milliseconds=500))

        assert [n.id for n in later] == ["f1"]
        assert earlier == []

    async def test_the_column_holds_the_instant(self, store):
        await store.store_node(Fact(id="f1", content="x", source_id="s1", created_at=WHOLE))

        assert await _rows('SELECT created_at FROM "default".node') == [(WHOLE,)]

    async def test_a_bound_in_another_zone_is_the_same_instant(self, store):
        await store.store_node(Fact(id="f1", content="x", source_id="s1", created_at=WHOLE))
        elsewhere = (WHOLE + timedelta(milliseconds=500)).astimezone(timezone(timedelta(hours=-7)))

        assert [n.id for n in await store.query_nodes(at_time=elsewhere)] == ["f1"]

    async def test_a_naive_bound_is_taken_as_utc(self, store):
        await store.store_node(Fact(id="f1", content="x", source_id="s1", created_at=WHOLE))
        naive = (WHOLE + timedelta(milliseconds=500)).replace(tzinfo=None)

        assert [n.id for n in await store.query_nodes(at_time=naive)] == ["f1"]

    async def test_the_journal_window_is_half_open_on_a_whole_second(self, store):
        await store.record_decision(
            DecisionRecord(id="d1", kind=DecisionKind.INGEST, subject_ids=[], decided_at=WHOLE)
        )

        assert [d.id for d in await store.query_decisions(since=WHOLE)] == ["d1"]
        assert await store.query_decisions(until=WHOLE) == []
        assert [
            d.id for d in await store.query_decisions(until=WHOLE + timedelta(microseconds=1))
        ] == ["d1"]

    async def test_journal_ties_break_on_id_in_code_point_order(self, store):
        """The order Python's string comparison gives the other backends, whatever
        collation the database was created with."""
        ids = ["a", "B", "_", "-", "Z9"]
        for decision_id in ids:
            await store.record_decision(
                DecisionRecord(
                    id=decision_id, kind=DecisionKind.INGEST, subject_ids=[], decided_at=WHOLE
                )
            )

        assert [d.id for d in await store.query_decisions()] == sorted(ids, reverse=True)


class TestVectorsRoundTripExactly:
    async def test_a_384_wide_vector_of_awkward_floats_comes_back_bit_for_bit(self, store, another):
        rng = random.Random(4417)
        awkward = [
            0.1 + 0.2,
            1 / 3,
            -0.0,
            5e-324,
            2.2250738585072014e-308,
            1.7976931348623157e308,
            -math.pi,
            math.e * 1e-17,
        ]
        vector = awkward + [rng.uniform(-1, 1) * 10 ** rng.randint(-30, 30) for _ in range(376)]
        embedding = EmbeddingRecord(id="emb384", item_id="f1", model_id="m", vector=vector)

        await store.store_embedding(embedding)

        reader = await another()
        (stored,) = await reader.get_embeddings_for_item("f1")
        assert [x.hex() for x in stored.vector] == [x.hex() for x in vector]
        assert stored == embedding

    async def test_the_record_does_not_carry_the_vector_a_second_time(self, store):
        await store.store_embedding(_Graph.embedding)

        ((data,),) = await _rows('SELECT data FROM "default".embedding')

        assert "vector" not in data


class TestNumbersInJsonb:
    """`jsonb` keeps a number as `numeric`, so it has no 64-bit ceiling and no
    float type of its own: a value comes back as what `numeric` makes of it."""

    async def test_metadata_numbers_read_back_equal(self, store):
        big = 2**63 + 1
        node = Topic(
            id="t-numbers", content="numbers", metadata={"big": big, "sci": 1e16, "zero": -0.0}
        )

        await store.store_node(node)
        got = (await store.get_node("t-numbers")).metadata

        assert got == node.metadata
        assert got["big"] == big and isinstance(got["big"], int)
        # Pinned, not chosen: `==` holds for both of these, and what it hides is
        # that `1e16` comes back as the integer 10**16, and `-0.0` as `0.0`,
        # since `numeric` has neither a float type nor a signed zero.
        assert type(got["sci"]) is int
        assert math.copysign(1.0, got["zero"]) == 1.0


def _instant(rendered: str | None) -> datetime | None:
    return None if rendered is None else datetime.fromisoformat(rendered)


class TestFilterColumnsAgreeWithTheRecord:
    """`data` is the truth; a column is a copy of a field in it, and nothing else."""

    async def test_every_column_of_every_row_matches_its_data(self, store):
        await _populate(store)
        await store.set_node_status_tx([_Graph.inference], status=NodeStatus.ARCHIVED, at=AT)
        node_types = {"t1": "topic", "f1": "fact", "f0": "fact", "i1": "inference"}

        checked = 0
        for uid, source, created_at, data in await _rows(
            'SELECT uid, source, created_at, data FROM "default".document'
        ):
            assert (uid, source, created_at) == (
                data["id"],
                data["source"],
                _instant(data["created_at"]),
            )
            checked += 1
        for uid, document_id, data in await _rows(
            'SELECT uid, document_id, data FROM "default".segment'
        ):
            assert (uid, document_id) == (data["id"], data["source_id"])
            checked += 1
        for uid, node_type, status, content, created_at, superseded_at, data in await _rows(
            "SELECT uid, node_type, status, content, created_at, superseded_at, data "
            'FROM "default".node'
        ):
            assert (uid, node_type, status, content, created_at, superseded_at) == (
                data["id"],
                node_types[uid],
                data["status"],
                data["content"],
                _instant(data["created_at"]),
                _instant(data["superseded_at"]),
            )
            checked += 1
        for uid, src_id, dst_id, edge_type, data in await _rows(
            'SELECT uid, src_id, dst_id, type, data FROM "default".node_edge'
        ):
            assert (uid, src_id, dst_id, edge_type) == (
                data["id"],
                data["src_id"],
                data["dst_id"],
                data["type"],
            )
            checked += 1
        for uid, item_id, model_id, data in await _rows(
            'SELECT uid, item_id, model_id, data FROM "default".embedding'
        ):
            assert (uid, item_id, model_id) == (data["id"], data["item_id"], data["model_id"])
            checked += 1
        for uid, status, data in await _rows('SELECT uid, status, data FROM "default".metacontext'):
            assert (uid, status) == (data["id"], data["status"])
            checked += 1
        for uid, name, kind, data in await _rows(
            'SELECT uid, name, kind, data FROM "default".relation_label'
        ):
            assert (uid, name, kind) == (data["id"], data["name"], data["kind"])
            checked += 1
        for uid, label_a, label_b, data in await _rows(
            'SELECT uid, label_a, label_b, data FROM "default".relation_verdict'
        ):
            pair = sorted(set(data["label_ids"]))
            assert (uid, label_a, label_b) == (data["id"], pair[0], pair[-1])
            checked += 1
        for uid, agent_id, kind, subject_ids, reviews, decided_at, data in await _rows(
            "SELECT uid, agent_id, kind, subject_ids, reviews, decided_at, data "
            'FROM "default".decision'
        ):
            judge = data["judged_by"]
            assert (uid, agent_id, kind, subject_ids, reviews, decided_at) == (
                data["id"],
                None if judge is None else judge["agent_id"],
                data["kind"],
                data["subject_ids"],
                data["reviews"],
                _instant(data["decided_at"]),
            )
            checked += 1
        for table in ("timeline", "agent"):
            for uid, data in await _rows(f'SELECT uid, data FROM "default".{table}'):
                assert uid == data["id"]
                checked += 1

        # One of every kind was written, so a table read as empty is a bug here.
        assert checked == 16

    async def test_a_retirement_moves_the_columns_with_the_record(self, store):
        await store.store_node(_Graph.fact)
        await store.set_node_status_tx([_Graph.fact], status=NodeStatus.HISTORICAL, at=AT)

        assert await _rows(
            "SELECT status, superseded_at FROM \"default\".node WHERE uid = 'f1'"
        ) == [("historical", AT)]

        await store.set_node_status_tx([_Graph.fact], status=NodeStatus.ACTIVE, at=AT)

        assert await _rows(
            "SELECT status, superseded_at FROM \"default\".node WHERE uid = 'f1'"
        ) == [("active", None)]


class TestGraphsAreSchemas:
    async def test_switching_to_a_new_name_creates_its_schema(self, store):
        assert not await _schema_exists("fresh")

        await store.switch_database("fresh")
        await store.store_node(_Graph.topic)

        assert await _schema_exists("fresh")
        assert store.current_database == "fresh"
        assert await store.get_node("t1") == _Graph.topic

    async def test_a_viz_read_of_a_missing_graph_creates_nothing(self, store):
        assert await store.viz_list_nodes("absent") == []
        assert await store.count_decisions_by_graph(["absent"]) == {}

        assert not await _schema_exists("absent")

    async def test_a_viz_read_of_another_graph_leaves_the_active_one_alone(self, store):
        await store.switch_database("other")
        await store.store_node(_Graph.topic)
        await store.switch_database("default")

        assert [n.id for n in await store.viz_list_nodes("other")] == ["t1"]
        assert store.current_database == "default"
        assert await store.get_node("t1") is None

    async def test_a_content_longer_than_a_btree_entry_is_stored_and_found(self, store):
        """A B-tree entry holds about 2.7 kB, and a node's content can be longer."""
        long = Topic(id="t-long", content="x" * 10_000)

        await store.store_node(long)

        assert await store.get_node_by_content("x" * 10_000) == long


async def _stamp_newer(version: int) -> None:
    """Put the graph `newer` at `version`, as a newer Epimemer would have left it."""
    async with _admin() as connection:
        await connection.execute('UPDATE "newer".epimemer_graph SET version = %s', (version,))


class TestAGraphANewerEpimemerWrote:
    """A graph stamped above the version this code knows is refused, never opened.

    Opening it would write into tables whose meaning may have changed under
    the same names. The refusal names both versions and the remedy, leaves the
    store holding what it held before, and writes nothing.
    """

    async def test_connect_refuses_it_and_names_both_versions(self, store):
        await store.switch_database("newer")
        await store.store_node(_Graph.topic)
        await store.switch_database("default")
        await _stamp_newer(99)

        refused = _adapter().PostgresStorage(postgres_dsn(), database="newer")
        with pytest.raises(SchemaTooNew, match="schema version 99.*up to 1.*Upgrade Epimemer"):
            await refused.connect()

        assert refused._connection is None
        assert await _rows('SELECT name, version FROM "newer".epimemer_graph') == [("newer", 99)]
        assert await _rows('SELECT uid FROM "newer".node') == [("t1",)]

    async def test_switching_to_it_is_refused_and_the_active_graph_stays(self, store):
        await store.switch_database("newer")
        await store.switch_database("default")
        await _stamp_newer(99)

        with pytest.raises(SchemaTooNew) as refused:
            await store.switch_database("newer")

        assert (refused.value.graph, refused.value.stored, refused.value.supported) == (
            "newer",
            99,
            1,
        )
        assert store.current_database == "default"
        await store.store_node(_Graph.topic)
        assert await store.get_node("t1") == _Graph.topic
        assert await _rows('SELECT version FROM "newer".epimemer_graph') == [(99,)]
        assert await _rows('SELECT count(*) FROM "newer".node') == [(0,)]
