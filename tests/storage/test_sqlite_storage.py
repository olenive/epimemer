"""What only the SQLite backend can get wrong.

Every behavioural test already runs against this backend through the `storage`
and `store` fixtures. This file holds the properties that come from its being
files on disk: a graph outlives the object that wrote it, a second connection
sees the first one's commits, deleting a graph removes files, a vector passes
through a blob, timestamp columns compare as text, a transaction that fails
leaves nothing, every filter column agrees with the record it was copied
from, and a second process holding the write lock is waited for, and one that
dies inside a transaction leaves nothing. Each test gets its own directory
under `tmp_path`.
"""

import json
import math
import random
import sqlite3
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from epimemer.core.seat import JudgeConfirmation
from epimemer.core.types import (
    Agent,
    DecisionKind,
    DecisionRecord,
    EdgeType,
    EmbeddingRecord,
    Fact,
    Inference,
    JudgeRef,
    Metacontext,
    NodeEdge,
    NodeStatus,
    RawDocument,
    RelationLabel,
    RelationVerdict,
    Segment,
    Timeline,
    Timepoint,
    Topic,
)
from epimemer.storage.protocol import MergeOverrides, SchemaTooNew, TimelineConflict
from epimemer.storage.sqlite_adapter import SQLiteStorage
from epimemer.storage.timestamps import iso_micros

AT = datetime(2026, 10, 8, 9, 30, 15, 123456, tzinfo=UTC)
JUDGE = JudgeRef(agent_id="k1", digest="d1")


@pytest.fixture
def directory(tmp_path: Path) -> Path:
    return tmp_path / "graphs"


@pytest.fixture
async def store(directory: Path):
    s = SQLiteStorage(directory)
    await s.connect()
    yield s
    await s.close()


async def _opened(directory: Path, database: str = "default") -> SQLiteStorage:
    s = SQLiteStorage(directory, database=database)
    await s.connect()
    return s


class _Graph:
    """One of every record kind, with the settings beside them."""

    document = RawDocument(
        id="d1",
        content="The ticket JIRA-4417 was closed.",
        source="notes.md",
        metadata={"lang": "en"},
        created_at=AT,
    )
    segment = Segment(
        id="s1", source_id="d1", text="The ticket JIRA-4417 was closed.", span_start=0, span_end=32
    )
    topic = Topic(id="t1", content="Tickets", description="Work items")
    fact = Fact(id="f1", content="JIRA-4417 was closed", source_id="s1", judged_by=JUDGE)
    inference = Inference(id="i1", content="The work is done", source_id="s1")
    retired = Fact(
        id="f0",
        content="JIRA-4417 is open",
        source_id="s1",
        status=NodeStatus.CORRECTED,
        superseded_at=AT,
    )
    edges = (
        NodeEdge(id="e1", src_id="f1", dst_id="t1", type=EdgeType.TAGGED_WITH_TOPIC),
        NodeEdge(
            id="e2",
            src_id="f1",
            dst_id="d1",
            type=EdgeType.RELATED,
            label="reported_in",
            judged_by=JUDGE,
        ),
    )
    embedding = EmbeddingRecord(id="emb1", item_id="f1", model_id="m", vector=[0.1, 0.2, 0.3])
    timeline = Timeline(id="tl1", name="Work", timepoints=[Timepoint(id="tp1", label="then")])
    metacontext = Metacontext(id="mc1", content="The Real")
    label = RelationLabel(id="rl1", name="reported_in", kind="relationship", judged_by=JUDGE)
    verdict = RelationVerdict(
        id="v1", label_ids=["rl2", "rl1"], verdict="distinct", because="different senses"
    )
    agent = Agent(id="k1", name="Judge", last_seen_at=AT)
    decision = DecisionRecord(
        id="dec1",
        kind=DecisionKind.INGEST,
        subject_ids=["f1", "t1"],
        judged_by=JUDGE,
        decided_at=AT.replace(microsecond=0),
    )
    review = DecisionRecord(id="dec2", kind=DecisionKind.MERGE, subject_ids=[], reviews="dec1")
    confirmation = JudgeConfirmation(
        session_id="conv-1", agent_id="k1", model="claude-opus-5-5", confirmed_at=AT
    )


async def _populate(store: SQLiteStorage) -> None:
    g = _Graph
    await store.store_document(g.document)
    await store.store_segment(g.segment)
    for node in (g.topic, g.fact, g.inference, g.retired):
        await store.store_node(node)
    for edge in g.edges:
        await store.store_edge(edge)
    await store.store_embedding(g.embedding)
    await store.store_timeline(g.timeline)
    await store.store_metacontext(g.metacontext)
    await store.store_relation_label(g.label)
    await store.record_relation_verdict(g.verdict)
    await store.upsert_agent(g.agent)
    await store.record_decision(g.decision)
    await store.record_decision(g.review)
    for _ in range(3):
        await store.bump_reflect_counter()
    await store.set_reflect_threshold_override(7)
    await store.set_backup_counter(2)
    await store.set_backup_threshold_override(11)
    await store.set_merge_overrides(MergeOverrides(undo_depth=3))
    await store.set_approved_agent_ids(["k1"])
    await store.set_judge_confirmations([g.confirmation])
    await store.set_require_judge(True)


class TestAGraphOutlivesItsConnection:
    async def test_every_record_and_setting_reads_back_after_a_reopen(self, directory):
        writer = await _opened(directory)
        await _populate(writer)
        await writer.close()

        reader = await _opened(directory)
        try:
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
            assert await reader.get_merge_overrides() == MergeOverrides(undo_depth=3)
            assert await reader.get_approved_agent_ids() == ["k1"]
            assert await reader.get_judge_confirmations() == [g.confirmation]
            assert await reader.get_require_judge() is True
        finally:
            await reader.close()

    async def test_the_vector_search_answers_the_same_after_a_reopen(self, directory):
        writer = await _opened(directory)
        await _populate(writer)
        before = await writer.vector_search([0.1, 0.2, 0.3], "m")
        await writer.close()

        reader = await _opened(directory)
        try:
            assert await reader.vector_search([0.1, 0.2, 0.3], "m") == before
            assert [item for item, _ in before] == ["f1"]
        finally:
            await reader.close()

    async def test_connect_after_close_works_again(self, store):
        await store.store_node(_Graph.topic)
        await store.close()
        await store.connect()

        assert await store.get_node("t1") == _Graph.topic


class TestTwoConnectionsShareAGraph:
    """The WAL promise: the CLI beside a running server sees, and is seen."""

    async def test_each_sees_the_others_writes(self, directory):
        server = await _opened(directory)
        cli = await _opened(directory)
        try:
            await server.store_node(_Graph.fact)
            assert await cli.get_node("f1") == _Graph.fact

            await cli.store_node(_Graph.topic)
            assert await server.get_node("t1") == _Graph.topic
        finally:
            await cli.close()
            await server.close()

    async def test_settings_are_read_from_the_file_on_every_call(self, directory):
        """`epimemer agents confirm` writes the approved list from another process,
        and the server has to see it without restarting."""
        server = await _opened(directory)
        cli = await _opened(directory)
        try:
            assert await server.get_approved_agent_ids() == []
            await cli.set_approved_agent_ids(["k1"])
            assert await server.get_approved_agent_ids() == ["k1"]

            await server.bump_reflect_counter()
            assert await cli.bump_reflect_counter() == 2
            assert await server.get_reflect_counter() == 2
        finally:
            await cli.close()
            await server.close()

    async def test_a_write_from_a_stale_timeline_is_refused(self, directory):
        server = await _opened(directory)
        cli = await _opened(directory)
        try:
            await server.store_timeline(_Graph.timeline)
            mine = await server.get_timeline("tl1")
            theirs = await cli.get_timeline("tl1")

            await server.store_timeline(mine.model_copy(update={"name": "mine"}))
            with pytest.raises(TimelineConflict):
                await cli.store_timeline(theirs.model_copy(update={"name": "theirs"}))

            assert (await cli.get_timeline("tl1")).name == "mine"
        finally:
            await cli.close()
            await server.close()


class TestATimelineWrittenBeforeItsVersion:
    """A record stored before `Timeline.version` existed has no `version` key,
    and reads, and is checked, as version 0."""

    async def test_it_takes_a_write_at_version_zero(self, store):
        store._db.execute(
            "INSERT INTO timeline (uid, data) VALUES (?, ?)",
            ("tl-old", json.dumps({"id": "tl-old", "name": "older"})),
        )

        older = await store.get_timeline("tl-old")
        assert older.version == 0
        await store.store_timeline(older.model_copy(update={"name": "revised"}))

        assert (await store.get_timeline("tl-old")).version == 1
        with pytest.raises(TimelineConflict):
            await store.store_timeline(older.model_copy(update={"name": "stale"}))


class TestDeletingAGraph:
    async def _make_other(self, store: SQLiteStorage) -> Path:
        await store.switch_database("other")
        await store.store_node(_Graph.topic)
        await store.switch_database("default")
        path = store._path("other")
        # Closing the last connection checkpoints the log and removes both
        # sidecars, so they are put back: what is under test is that a delete
        # removes them when they are there.
        for suffix in ("-wal", "-shm"):
            Path(f"{path}{suffix}").touch()
        return path

    async def test_the_file_and_both_sidecars_go(self, store):
        path = await self._make_other(store)

        await store.delete_database("other")

        assert not path.exists()
        assert not Path(f"{path}-wal").exists()
        assert not Path(f"{path}-shm").exists()
        assert await store.list_databases() == ["default"]

    async def test_the_active_graph_is_refused(self, store):
        with pytest.raises(ValueError):
            await store.delete_database("default")

        assert store._path("default").is_file()
        assert await store.list_databases() == ["default"]

    async def test_a_name_with_no_file_is_refused(self, store):
        with pytest.raises(KeyError):
            await store.delete_database("never-made")

    async def test_a_name_that_is_not_a_graph_name_is_refused_before_any_path(self, store):
        with pytest.raises(ValueError):
            await store.delete_database("../default")


class TestVectorsRoundTripExactly:
    async def test_a_384_wide_vector_of_awkward_floats_comes_back_bit_for_bit(self, directory):
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
        assert len(vector) == 384
        embedding = EmbeddingRecord(id="emb384", item_id="f1", model_id="m", vector=vector)

        writer = await _opened(directory)
        await writer.store_embedding(embedding)
        assert [x.hex() for x in (await writer.get_embeddings_for_item("f1"))[0].vector] == [
            x.hex() for x in vector
        ]
        await writer.close()

        reader = await _opened(directory)
        try:
            (stored,) = await reader.get_embeddings_for_item("f1")
            assert [x.hex() for x in stored.vector] == [x.hex() for x in vector]
            assert stored == embedding
        finally:
            await reader.close()

    async def test_the_record_json_does_not_carry_the_vector_a_second_time(self, store):
        await store.store_embedding(_Graph.embedding)

        (data,) = store._db.execute("SELECT data FROM embedding").fetchone()

        assert "vector" not in json.loads(data)


class TestTimestampColumnsCompareAsInstants:
    WHOLE = datetime(2026, 8, 23, 12, 0, 41, tzinfo=UTC)

    async def test_a_node_born_on_a_whole_second_is_inside_a_bound_half_a_second_later(self, store):
        node = Fact(id="f1", content="x", source_id="s1", created_at=self.WHOLE)
        await store.store_node(node)

        later = await store.query_nodes(at_time=self.WHOLE + timedelta(milliseconds=500))
        earlier = await store.query_nodes(at_time=self.WHOLE - timedelta(milliseconds=500))

        assert [n.id for n in later] == ["f1"]
        assert earlier == []

    async def test_the_column_carries_the_microseconds_even_when_they_are_zero(self, store):
        await store.store_node(Fact(id="f1", content="x", source_id="s1", created_at=self.WHOLE))

        (column,) = store._db.execute("SELECT created_at FROM node").fetchone()

        assert column == "2026-08-23T12:00:41.000000Z"

    async def test_a_bound_in_another_zone_is_the_same_instant(self, store):
        node = Fact(id="f1", content="x", source_id="s1", created_at=self.WHOLE)
        await store.store_node(node)
        elsewhere = (self.WHOLE + timedelta(milliseconds=500)).astimezone(
            timezone(timedelta(hours=-7))
        )

        assert [n.id for n in await store.query_nodes(at_time=elsewhere)] == ["f1"]


class TestAFailedTransactionLeavesNothing:
    async def test_a_batch_that_fails_on_its_second_node_keeps_none_of_it(self, store):
        await store.store_node(_Graph.topic)
        await store.store_timeline(_Graph.timeline)
        renamed = _Graph.timeline.model_copy(update={"name": "Renamed", "version": 1})
        fresh = Fact(id="f-new", content="new", source_id="s1")

        # Insert-only: an id already in the graph fails the batch.
        with pytest.raises(sqlite3.IntegrityError):
            await store.write_batch_tx(
                nodes=[fresh, _Graph.topic],
                timelines=[renamed],
            )

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

    async def test_the_connection_is_usable_and_committed_after_a_rollback(self, directory):
        writer = await _opened(directory)
        reader = await _opened(directory)
        try:
            await writer.store_node(_Graph.topic)
            with pytest.raises(sqlite3.IntegrityError):
                await writer.write_batch_tx(nodes=[_Graph.fact, _Graph.topic])

            await writer.store_node(_Graph.inference)

            assert set(await reader.get_nodes(["t1", "f1", "i1"])) == {"t1", "i1"}
        finally:
            await reader.close()
            await writer.close()


def _instant(rendered: str) -> str:
    return iso_micros(datetime.fromisoformat(rendered))


class TestFilterColumnsAgreeWithTheRecord:
    """`data` is the truth; a column is a copy of a field in it, and nothing else."""

    async def test_every_column_of_every_row_matches_its_data(self, store):
        await _populate(store)
        await store.set_node_status_tx([_Graph.inference], status=NodeStatus.ARCHIVED, at=AT)
        node_types = {"t1": "topic", "f1": "fact", "f0": "fact", "i1": "inference"}

        def rows(sql: str) -> list[tuple]:
            return store._db.execute(sql).fetchall()

        checked = 0
        for uid, source, created_at, data in rows(
            "SELECT uid, source, created_at, data FROM document"
        ):
            record = json.loads(data)
            assert (uid, source, created_at) == (
                record["id"],
                record["source"],
                _instant(record["created_at"]),
            )
            checked += 1
        for uid, document_id, data in rows("SELECT uid, document_id, data FROM segment"):
            record = json.loads(data)
            assert (uid, document_id) == (record["id"], record["source_id"])
            checked += 1
        for uid, node_type, status, content, created_at, superseded_at, data in rows(
            "SELECT uid, node_type, status, content, created_at, superseded_at, data FROM node"
        ):
            record = json.loads(data)
            assert (uid, node_type, status, content, created_at, superseded_at) == (
                record["id"],
                node_types[uid],
                record["status"],
                record["content"],
                _instant(record["created_at"]),
                None if record["superseded_at"] is None else _instant(record["superseded_at"]),
            )
            checked += 1
        for uid, src_id, dst_id, edge_type, data in rows(
            "SELECT uid, src_id, dst_id, type, data FROM node_edge"
        ):
            record = json.loads(data)
            assert (uid, src_id, dst_id, edge_type) == (
                record["id"],
                record["src_id"],
                record["dst_id"],
                record["type"],
            )
            checked += 1
        for uid, item_id, model_id, data in rows(
            "SELECT uid, item_id, model_id, data FROM embedding"
        ):
            record = json.loads(data)
            assert (uid, item_id, model_id) == (record["id"], record["item_id"], record["model_id"])
            checked += 1
        for uid, status, data in rows("SELECT uid, status, data FROM metacontext"):
            record = json.loads(data)
            assert (uid, status) == (record["id"], record["status"])
            checked += 1
        for uid, name, kind, data in rows("SELECT uid, name, kind, data FROM relation_label"):
            record = json.loads(data)
            assert (uid, name, kind) == (record["id"], record["name"], record["kind"])
            checked += 1
        for uid, label_a, label_b, data in rows(
            "SELECT uid, label_a, label_b, data FROM relation_verdict"
        ):
            record = json.loads(data)
            pair = sorted(set(record["label_ids"]))
            assert (uid, label_a, label_b) == (record["id"], pair[0], pair[-1])
            checked += 1
        for uid, agent_id, kind, subject_ids, reviews, decided_at, data in rows(
            "SELECT uid, agent_id, kind, subject_ids, reviews, decided_at, data FROM decision"
        ):
            record = json.loads(data)
            judge = record["judged_by"]
            assert (uid, agent_id, kind, json.loads(subject_ids), reviews, decided_at) == (
                record["id"],
                None if judge is None else judge["agent_id"],
                record["kind"],
                record["subject_ids"],
                record["reviews"],
                _instant(record["decided_at"]),
            )
            checked += 1
        for table in ("timeline", "agent"):
            for uid, data in rows(f"SELECT uid, data FROM {table}"):
                assert uid == json.loads(data)["id"]
                checked += 1

        # One of every kind was written, so a table read as empty is a bug here.
        assert checked == 16

    async def test_a_retirement_moves_the_columns_with_the_record(self, store):
        await store.store_node(_Graph.fact)
        await store.set_node_status_tx([_Graph.fact], status=NodeStatus.HISTORICAL, at=AT)

        status, superseded_at = store._db.execute(
            "SELECT status, superseded_at FROM node WHERE uid = 'f1'"
        ).fetchone()

        assert (status, superseded_at) == ("historical", "2026-10-08T09:30:15.123456Z")

        await store.set_node_status_tx([_Graph.fact], status=NodeStatus.ACTIVE, at=AT)
        status, superseded_at = store._db.execute(
            "SELECT status, superseded_at FROM node WHERE uid = 'f1'"
        ).fetchone()

        assert (status, superseded_at) == ("active", None)


class TestGraphsAreFiles:
    async def test_list_databases_sees_a_graph_another_instance_created(self, store, directory):
        other = await _opened(directory, database="made-elsewhere")
        await other.close()

        assert await store.list_databases() == ["default", "made-elsewhere"]

    async def test_switching_to_a_name_with_no_file_creates_it(self, store, directory):
        assert not (directory / "fresh.sqlite").exists()

        await store.switch_database("fresh")
        await store.store_node(_Graph.topic)

        assert (directory / "fresh.sqlite").is_file()
        assert store.current_database == "fresh"
        assert await store.list_databases() == ["default", "fresh"]
        assert await store.get_node("t1") == _Graph.topic

    async def test_a_viz_read_of_a_missing_graph_creates_nothing(self, store, directory):
        assert await store.viz_list_nodes("absent") == []
        assert await store.count_decisions_by_graph(["absent"]) == {}

        assert not (directory / "absent.sqlite").exists()

    async def test_a_viz_read_of_another_graph_leaves_the_active_one_alone(self, store):
        await store.switch_database("other")
        await store.store_node(_Graph.topic)
        await store.switch_database("default")

        assert [n.id for n in await store.viz_list_nodes("other")] == ["t1"]
        assert store.current_database == "default"
        assert await store.get_node("t1") is None

    async def test_a_file_where_the_directory_should_be_is_refused_by_name(self, tmp_path):
        occupied = tmp_path / "graphs"
        occupied.write_text("not a directory")

        with pytest.raises(NotADirectoryError, match="EPIMEMER_SQLITE_DIR"):
            await SQLiteStorage(occupied).connect()

    def test_a_graph_name_that_could_leave_the_directory_is_refused(self, tmp_path):
        with pytest.raises(ValueError):
            SQLiteStorage(tmp_path, database="../escape")

    async def test_location_names_the_directory_the_graphs_are_in(self, store, directory):
        assert str(directory) in store.location

    async def test_a_new_file_starts_at_schema_version_one(self, store):
        assert store._db.execute("SELECT version FROM schema_version").fetchall() == [(1,)]


# Another process on the same graph file, as the `epimemer` CLI is beside a
# running server. Written with the standard library alone, so what is under
# test is the file and its locks rather than a second copy of this adapter.
_HOLD_THE_WRITE_LOCK = """
import sqlite3, sys, time
connection = sqlite3.connect(sys.argv[1], isolation_level=None)
connection.execute("BEGIN IMMEDIATE")
print("locked", flush=True)
time.sleep(float(sys.argv[2]))
connection.execute("COMMIT")
"""

# Begins a transaction, writes a node row and enough padding that SQLite spills
# the uncommitted pages into the write-ahead log, then exits without committing
# or rolling back, as a process killed part way through a write would.
_DIE_BEFORE_COMMIT = """
import os, sqlite3, sys
connection = sqlite3.connect(sys.argv[1], isolation_level=None)
connection.execute("PRAGMA cache_size=10")
connection.execute("BEGIN IMMEDIATE")
connection.execute(
    "INSERT INTO node (uid, node_type, status, content, created_at, data) "
    "VALUES (?, 'fact', 'active', 'never committed', '2026-10-08T09:30:15.123456Z', ?)",
    (sys.argv[2], '{"id": "%s", "content": "never committed", "source_id": "s1"}' % sys.argv[2]),
)
for i in range(500):
    connection.execute(
        "INSERT INTO graph_state (key, value) VALUES (?, ?)", ("pad-%d" % i, '"%s"' % ("x" * 4000))
    )
os._exit(1)
"""


def _holding_the_write_lock(path: Path, seconds: float) -> subprocess.Popen:
    """Another process holding `path`'s write lock for `seconds`, returned once it has it."""
    holder = subprocess.Popen(
        [sys.executable, "-c", _HOLD_THE_WRITE_LOCK, str(path), str(seconds)],
        stdout=subprocess.PIPE,
        text=True,
    )
    if holder.stdout.readline().strip() != "locked":
        holder.kill()
        holder.wait()
        pytest.fail("the second process never took the write lock")
    return holder


class TestASecondProcess:
    """A write waits up to the five-second busy timeout for another process's
    transaction, and fails cleanly after it. A process that dies inside a
    transaction leaves the file as it was before the transaction began."""

    async def test_a_write_waits_for_another_processs_transaction_and_lands(self, store):
        holder = _holding_the_write_lock(store._path("default"), 1.0)
        try:
            started = time.monotonic()
            await store.store_node(_Graph.fact)
            waited = time.monotonic() - started
        finally:
            assert holder.wait(timeout=10) == 0

        assert waited >= 0.5, f"the write did not wait for the lock ({waited:.2f}s)"
        assert await store.get_node("f1") == _Graph.fact

    async def test_a_lock_held_past_the_busy_timeout_fails_the_write_and_writes_nothing(
        self, store
    ):
        holder = _holding_the_write_lock(store._path("default"), 7.0)
        try:
            started = time.monotonic()
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                await store.store_node(_Graph.fact)
            gave_up_after = time.monotonic() - started
        finally:
            assert holder.wait(timeout=15) == 0

        # It gave up at the timeout rather than waiting for the commit.
        assert 4.5 <= gave_up_after < 7.0, gave_up_after
        assert await store.get_node("f1") is None
        # The failed statement left no transaction open, so the next write is
        # a write of its own.
        assert not store._db.in_transaction
        await store.store_node(_Graph.topic)
        assert await store.get_node("t1") == _Graph.topic

    async def test_a_process_that_dies_before_its_commit_leaves_nothing(self, directory):
        writer = await _opened(directory)
        await writer.store_node(_Graph.topic)
        path = writer._path("default")
        await writer.close()

        died = subprocess.run(
            [sys.executable, "-c", _DIE_BEFORE_COMMIT, str(path), "f-lost"], timeout=30
        )
        assert died.returncode == 1
        # The uncommitted pages reached the log, so opening the file has pages
        # to discard rather than an empty log to skip.
        assert Path(f"{path}-wal").stat().st_size > 0

        reader = await _opened(directory)
        try:
            assert await reader.get_node("f-lost") is None
            assert await reader.get_node("t1") == _Graph.topic
            assert reader._db.execute("SELECT count(*) FROM graph_state").fetchone() == (0,)
            assert reader._db.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
            await reader.store_node(_Graph.fact)
            assert await reader.get_node("f1") == _Graph.fact
        finally:
            await reader.close()

    async def test_a_graph_deleted_under_another_instance_keeps_answering_it(
        self, store, directory
    ):
        """Pinned, not chosen. `delete_database` refuses only the graph its own
        instance has open, and cannot see another instance's connection. That
        instance keeps the unlinked file through its open descriptors: its next
        write is accepted and its next read returns it, and both are lost when
        it closes, with nothing raised anywhere."""
        other = await _opened(directory, database="other")
        try:
            await other.store_node(_Graph.topic)

            await store.delete_database("other")
            assert not (directory / "other.sqlite").exists()

            await other.store_node(_Graph.fact)
            assert await other.get_nodes(["t1", "f1"]) == {"t1": _Graph.topic, "f1": _Graph.fact}
        finally:
            await other.close()

        assert await store.list_databases() == ["default"]
        assert [p.name for p in directory.iterdir() if p.name.startswith("other")] == []


def _stamp(path: Path, version: int) -> None:
    """Put a graph file at `version`, as a newer Epimemer would have left it."""
    connection = sqlite3.connect(path, autocommit=True)
    try:
        connection.execute("UPDATE schema_version SET version = ?", (version,))
    finally:
        connection.close()


def _stored_version(path: Path) -> list[tuple[int]]:
    connection = sqlite3.connect(path, autocommit=True)
    try:
        return connection.execute("SELECT version FROM schema_version").fetchall()
    finally:
        connection.close()


class TestAGraphANewerEpimemerWrote:
    """A file stamped above the version this code knows is refused, never opened.

    Opening it would write into tables whose meaning may have changed under
    the same names. The refusal names both versions and the remedy, leaves the
    store holding what it held before, and writes nothing.
    """

    async def test_connect_refuses_it_and_names_both_versions(self, directory):
        writer = await _opened(directory)
        await writer.store_node(_Graph.topic)
        await writer.close()
        path = directory / "default.sqlite"
        _stamp(path, 99)
        before = path.read_bytes()

        store = SQLiteStorage(directory)
        with pytest.raises(SchemaTooNew, match="schema version 99.*up to 1.*Upgrade Epimemer"):
            await store.connect()

        assert store._connection is None
        assert _stored_version(path) == [(99,)]
        assert path.read_bytes() == before

    async def test_switching_to_it_is_refused_and_the_active_graph_stays(self, store, directory):
        newer = await _opened(directory, "newer")
        await newer.close()
        _stamp(directory / "newer.sqlite", 99)

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
        assert _stored_version(directory / "newer.sqlite") == [(99,)]
