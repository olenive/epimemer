"""SQLite storage backend.

One file per graph, `<directory>/<graph>.sqlite`, no server and no dependency
beyond the standard library's `sqlite3` and numpy. `dev-docs/STORAGE_BACKENDS.md`
is the design, and this module is held to it.

**Every table has the same shape**: `uid TEXT PRIMARY KEY`, a column for each
field a query filters or orders on, and `data`, the whole record as JSON
(`normalize_for_storage`, then `model_dump(mode="json")`). `data` is the truth
and is what every read rehydrates through `model_validate`. The filter columns
are copies written in the same statement, and they exist only to carry an index
or a `WHERE`; a column disagreeing with `data` is a bug. Embeddings are the one
exception, and a deliberate one: the vector lives in a `BLOB` of little-endian
float64 rather than in `data`, because floats as JSON cost a gigabyte at
100,000 nodes and float64 hands back exactly the floats that were stored.

**Statements run inline on the event loop**, synchronously, with no thread and
no executor. A local file operation takes microseconds to low milliseconds, the
SurrealDB adapter already serialises every call through one connection, and a
thread pool would buy nothing while costing the ordering the graph guard
assumes: a statement could then still be running against one graph after the
guard had let a switch move to the next. Do not "fix" this.

**The connection runs in autocommit mode**, so every statement commits on its
own and a transaction exists only where the code says so: the `*_tx`
methods, the two read-modify-writes (resetting a counter, merging a relation
label) and `store_timeline`'s version check, each inside `_transaction`. None
of them awaits anything inside it, so a transaction is never left open across a
suspension point.

**WAL mode is what lets a second process share a graph**: the `epimemer`
CLI can read and write a file the server holds open, and two connections to
one file see each other's commits.
"""

import contextlib
import json
import os
import sqlite3
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel

from epimemer.core.seat import JudgeConfirmation
from epimemer.core.temporal import merged_validity
from epimemer.core.types import (
    Agent,
    DecisionKind,
    DecisionRecord,
    EdgeType,
    EmbeddingRecord,
    EpistemicNode,
    Fact,
    Inference,
    JudgeRef,
    JudgeUsage,
    Metacontext,
    NodeEdge,
    NodeStatus,
    NodeType,
    RawDocument,
    RelationLabel,
    RelationVerdict,
    Segment,
    Timeline,
    Topic,
    migration_disposition,
    recorded_relation_label,
    standing_relation_pairs,
    with_retirement,
    with_return,
)
from epimemer.storage.active_graph import GraphGuard, make_graph_guard
from epimemer.storage.bm25 import bm25_scores, containment_first
from epimemer.storage.protocol import (
    GRAPH_NAME_PATTERN,
    EdgeDirection,
    MergeOverrides,
    PassagesCited,
    SchemaTooNew,
    TimelineConflict,
    WarningOverrides,
    normalize_for_storage,
    require_own_passages,
    validate_graph_name,
)
from epimemer.storage.timestamps import iso_micros

# --- Schema ---

_SCHEMA_VERSION = 1

# STRICT so a value of the wrong type is refused at the write rather than
# stored and misread later: a vector that arrived as text, a counter as a float.
# No foreign keys anywhere. Edges to retired nodes, decisions about deleted
# judges and verdicts about renamed labels are all meaningful on an append-only
# graph, and every backend treats these ids as soft references.
_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS document (
        uid TEXT PRIMARY KEY,
        source TEXT,
        created_at TEXT NOT NULL,
        data TEXT NOT NULL
    ) STRICT
    """,
    "CREATE INDEX IF NOT EXISTS document_source ON document (source)",
    """
    CREATE TABLE IF NOT EXISTS segment (
        uid TEXT PRIMARY KEY,
        document_id TEXT NOT NULL,
        data TEXT NOT NULL
    ) STRICT
    """,
    "CREATE INDEX IF NOT EXISTS segment_document ON segment (document_id)",
    # Topics, facts and inferences share one table, so a node's type is one
    # indexed read away rather than a probe of three tables in turn.
    """
    CREATE TABLE IF NOT EXISTS node (
        uid TEXT PRIMARY KEY,
        node_type TEXT NOT NULL,
        status TEXT NOT NULL,
        content TEXT NOT NULL,
        created_at TEXT NOT NULL,
        superseded_at TEXT,
        data TEXT NOT NULL
    ) STRICT
    """,
    "CREATE INDEX IF NOT EXISTS node_type_status ON node (node_type, status)",
    "CREATE INDEX IF NOT EXISTS node_content ON node (content)",
    """
    CREATE TABLE IF NOT EXISTS node_edge (
        uid TEXT PRIMARY KEY,
        src_id TEXT NOT NULL,
        dst_id TEXT NOT NULL,
        type TEXT NOT NULL,
        data TEXT NOT NULL
    ) STRICT
    """,
    "CREATE INDEX IF NOT EXISTS node_edge_src_type ON node_edge (src_id, type)",
    "CREATE INDEX IF NOT EXISTS node_edge_dst_type ON node_edge (dst_id, type)",
    """
    CREATE TABLE IF NOT EXISTS embedding (
        uid TEXT PRIMARY KEY,
        item_id TEXT NOT NULL,
        model_id TEXT NOT NULL,
        vector BLOB NOT NULL,
        data TEXT NOT NULL
    ) STRICT
    """,
    "CREATE INDEX IF NOT EXISTS embedding_item_model ON embedding (item_id, model_id)",
    "CREATE INDEX IF NOT EXISTS embedding_model ON embedding (model_id)",
    """
    CREATE TABLE IF NOT EXISTS timeline (
        uid TEXT PRIMARY KEY,
        data TEXT NOT NULL
    ) STRICT
    """,
    """
    CREATE TABLE IF NOT EXISTS metacontext (
        uid TEXT PRIMARY KEY,
        status TEXT NOT NULL,
        data TEXT NOT NULL
    ) STRICT
    """,
    # Unique, because `(name, kind)` is the key edges join a label on, and the
    # writer is what keeps it one record per pair.
    """
    CREATE TABLE IF NOT EXISTS relation_label (
        uid TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        kind TEXT NOT NULL,
        data TEXT NOT NULL
    ) STRICT
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS relation_label_name_kind ON relation_label (name, kind)",
    # The pair a verdict is about, smaller id first, so `(a, b)` and `(b, a)`
    # are one key. Append-only: nothing updates or deletes a row here.
    """
    CREATE TABLE IF NOT EXISTS relation_verdict (
        uid TEXT PRIMARY KEY,
        label_a TEXT,
        label_b TEXT,
        data TEXT NOT NULL
    ) STRICT
    """,
    "CREATE INDEX IF NOT EXISTS relation_verdict_pair ON relation_verdict (label_a, label_b)",
    """
    CREATE TABLE IF NOT EXISTS agent (
        uid TEXT PRIMARY KEY,
        data TEXT NOT NULL
    ) STRICT
    """,
    # The journal. `subject_ids` is the list as JSON, read through `json_each`:
    # a record names several subjects, so no scalar column can hold them and
    # no index can serve a membership test on them.
    """
    CREATE TABLE IF NOT EXISTS decision (
        uid TEXT PRIMARY KEY,
        agent_id TEXT,
        kind TEXT NOT NULL,
        subject_ids TEXT NOT NULL,
        reviews TEXT,
        decided_at TEXT NOT NULL,
        data TEXT NOT NULL
    ) STRICT
    """,
    "CREATE INDEX IF NOT EXISTS decision_agent ON decision (agent_id)",
    "CREATE INDEX IF NOT EXISTS decision_kind ON decision (kind)",
    "CREATE INDEX IF NOT EXISTS decision_reviews ON decision (reviews)",
    "CREATE INDEX IF NOT EXISTS decision_decided_at ON decision (decided_at)",
    # Per-graph settings and counters, one row each, JSON in `value`. An absent
    # row is the default the in-memory backend starts with.
    """
    CREATE TABLE IF NOT EXISTS graph_state (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    ) STRICT
    """,
    """
    CREATE TABLE IF NOT EXISTS schema_version (
        version INTEGER NOT NULL
    ) STRICT
    """,
)

# The keys of `graph_state`. Named once so a getter and its setter cannot
# disagree about which row they mean.
_REFLECT_COUNTER = "reflect_counter"
_BACKUP_COUNTER = "backup_counter"
_REFLECT_THRESHOLD = "reflect_threshold_override"
_BACKUP_THRESHOLD = "backup_threshold_override"
_MERGE_OVERRIDES = "merge_overrides"
_WARNING_OVERRIDES = "warning_overrides"
_APPROVED_AGENT_IDS = "approved_agent_ids"
_JUDGE_CONFIRMATIONS = "judge_confirmations"
_REQUIRE_JUDGE = "require_judge"

_NODE_CLASSES: dict[str, type] = {
    NodeType.TOPIC.value: Topic,
    NodeType.FACT.value: Fact,
    NodeType.INFERENCE.value: Inference,
}

# Lists of ids reach a query as one JSON parameter and come back out as rows
# through `json_each`, so a request of any size is one statement with no
# chunking and no limit on bound variables. SQLite still serves the `IN` from
# the index on the column it tests.
_IN_LIST = "IN (SELECT value FROM json_each(?))"


def _node_type_of(node: EpistemicNode) -> NodeType:
    match node:
        case Topic():
            return NodeType.TOPIC
        case Fact():
            return NodeType.FACT
        case Inference():
            return NodeType.INFERENCE
    raise TypeError(f"not a node: {type(node).__name__}")


def _value(member: Any) -> Any:
    """An enum member as the value a filter column holds, anything else as is."""
    return member.value if isinstance(member, Enum) else member


def _json(value: Any) -> str:
    # Unescaped unicode so the file reads naturally in any SQLite tool; compact
    # because the journal and the edge table are the largest tables there are.
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _json_list(values: Iterable[Any]) -> str:
    return _json([_value(value) for value in values])


def _iso_or_none(at: datetime | None) -> str | None:
    return None if at is None else iso_micros(at)


# --- Rows: a record as the columns its table stores ---
#
# Each filter column is read off the same JSON dump that becomes `data`, so the
# column holds the very value the record carries rather than a second rendering
# of it. Timestamps are the exception, by design: the column holds `iso_micros`
# of the instant, the one shape that compares correctly as text, while `data`
# keeps Pydantic's rendering, which nothing compares.

Row = dict[str, Any]


def _dumped[M: BaseModel](model: M, **dump: Any) -> tuple[M, dict]:
    record = normalize_for_storage(model)
    return record, record.model_dump(mode="json", **dump)


def _document_row(doc: RawDocument) -> Row:
    record, dump = _dumped(doc)
    return {
        "uid": record.id,
        "source": dump["source"],
        "created_at": iso_micros(record.created_at),
        "data": _json(dump),
    }


def _segment_row(segment: Segment) -> Row:
    record, dump = _dumped(segment)
    return {"uid": record.id, "document_id": dump["source_id"], "data": _json(dump)}


def _node_row(node: EpistemicNode) -> Row:
    record, dump = _dumped(node)
    return {
        "uid": record.id,
        "node_type": _node_type_of(record).value,
        "status": dump["status"],
        "content": dump["content"],
        "created_at": iso_micros(record.created_at),
        "superseded_at": _iso_or_none(record.superseded_at),
        "data": _json(dump),
    }


def _edge_row(edge: NodeEdge) -> Row:
    record, dump = _dumped(edge)
    return {
        "uid": record.id,
        "src_id": dump["src_id"],
        "dst_id": dump["dst_id"],
        "type": dump["type"],
        "data": _json(dump),
    }


def _vector_blob(vector: Sequence[float]) -> bytes:
    return np.asarray(vector, dtype="<f8").tobytes()


def _embedding_row(embedding: EmbeddingRecord) -> Row:
    # The vector is left out of `data`: it is the blob, and a second copy as
    # JSON text would cost exactly what the blob exists to save.
    record, dump = _dumped(embedding, exclude={"vector"})
    return {
        "uid": record.id,
        "item_id": dump["item_id"],
        "model_id": dump["model_id"],
        "vector": _vector_blob(record.vector),
        "data": _json(dump),
    }


def _timeline_row(timeline: Timeline) -> Row:
    record, dump = _dumped(timeline)
    return {"uid": record.id, "data": _json(dump)}


def _metacontext_row(mc: Metacontext) -> Row:
    record, dump = _dumped(mc)
    return {"uid": record.id, "status": dump["status"], "data": _json(dump)}


def _relation_label_row(label: RelationLabel) -> Row:
    record, dump = _dumped(label)
    return {"uid": record.id, "name": dump["name"], "kind": dump["kind"], "data": _json(dump)}


def _label_pair(label_ids: Iterable[str]) -> tuple[str | None, str | None]:
    """The lowest and highest distinct id: the pair key for a two-label verdict.

    Every verdict whose ids form the same set as a request has the same two ends
    as the request, so the pair columns narrow a lookup without deciding it; the
    set comparison that decides it runs on the rehydrated row.
    """
    distinct = sorted(set(label_ids))
    return (distinct[0], distinct[-1]) if distinct else (None, None)


def _relation_verdict_row(verdict: RelationVerdict) -> Row:
    record, dump = _dumped(verdict)
    label_a, label_b = _label_pair(dump["label_ids"])
    return {"uid": record.id, "label_a": label_a, "label_b": label_b, "data": _json(dump)}


def _agent_row(agent: Agent) -> Row:
    record, dump = _dumped(agent)
    return {"uid": record.id, "data": _json(dump)}


def _decision_row(decision: DecisionRecord) -> Row:
    record, dump = _dumped(decision)
    judge = dump["judged_by"]
    return {
        "uid": record.id,
        "agent_id": None if judge is None else judge["agent_id"],
        "kind": dump["kind"],
        "subject_ids": _json(dump["subject_ids"]),
        "reviews": dump["reviews"],
        "decided_at": iso_micros(record.decided_at),
        "data": _json(dump),
    }


# --- Reading records back ---


def _load[M: BaseModel](model: type[M], data: str) -> M:
    return model.model_validate(json.loads(data))


def _load_node(node_type: str, data: str) -> EpistemicNode:
    return _NODE_CLASSES[node_type].model_validate(json.loads(data))


def _load_embedding(data: str, vector: bytes) -> EmbeddingRecord:
    record = json.loads(data)
    record["vector"] = np.frombuffer(vector, dtype="<f8").tolist()
    return EmbeddingRecord.model_validate(record)


def _models[M: BaseModel](
    model: type[M], connection: sqlite3.Connection, sql: str, params: Sequence[Any] = ()
) -> list[M]:
    return [_load(model, data) for (data,) in connection.execute(sql, params)]


def _nodes(
    connection: sqlite3.Connection, where: str = "", params: Sequence[Any] = (), tail: str = ""
) -> list[EpistemicNode]:
    """Nodes in insertion order, which is the order the in-memory backend's dict
    keeps and the order "first node with this content" means."""
    sql = f"SELECT node_type, data FROM node {where} ORDER BY rowid {tail}"
    return [_load_node(node_type, data) for node_type, data in connection.execute(sql, params)]


def _edges(
    connection: sqlite3.Connection, where: str = "", params: Sequence[Any] = ()
) -> list[NodeEdge]:
    sql = f"SELECT data FROM node_edge {where} ORDER BY rowid"
    return _models(NodeEdge, connection, sql, params)


def _embeddings(
    connection: sqlite3.Connection, where: str = "", params: Sequence[Any] = ()
) -> list[EmbeddingRecord]:
    sql = f"SELECT data, vector FROM embedding {where} ORDER BY rowid"
    return [_load_embedding(data, vector) for data, vector in connection.execute(sql, params)]


def _embedding_where(
    *, item_ids: Sequence[str] | None = None, model_id: str | None = None
) -> tuple[str, list[Any]]:
    """The embedding filters as one WHERE clause and its parameters.

    Shared by the active graph's batched fetch and the viz read of a named
    graph, so an item and a model select the same rows from either. An empty
    list matches nothing.
    """
    clauses: list[str] = []
    params: list[Any] = []
    if item_ids is not None:
        clauses.append(f"item_id {_IN_LIST}")
        params.append(_json_list(item_ids))
    if model_id is not None:
        clauses.append("model_id = ?")
        params.append(model_id)
    return (f"WHERE {' AND '.join(clauses)}" if clauses else ""), params


def _node(connection: sqlite3.Connection, node_id: str) -> EpistemicNode | None:
    found = _nodes(connection, "WHERE uid = ?", (node_id,))
    return found[0] if found else None


def _stored_node(connection: sqlite3.Connection, node_id: str) -> EpistemicNode:
    """The node as stored, which is what every lifecycle change is applied to.

    A transaction's node argument says *which* node to change; its fields are a
    snapshot the caller took at some earlier moment, and appending an episode to
    a stale history would silently drop every episode since.
    """
    node = _node(connection, node_id)
    if node is None:
        raise KeyError(f"Node {node_id} not found")
    return node


# --- Writing ---


def _write_sql(table: str, columns: Sequence[str], *, upsert: bool) -> str:
    """`INSERT` for `columns`, or an upsert on `uid` that keeps the row in place.

    `ON CONFLICT DO UPDATE` rather than `INSERT OR REPLACE`: a replace deletes
    and re-inserts, which moves the row to the end of the table, and the
    insertion order is what every unordered read hands back. Table and column
    names here are this module's own constants, never a caller's string.
    """
    marks = ", ".join("?" for _ in columns)
    sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({marks})"
    if not upsert:
        return sql
    updates = ", ".join(f"{column} = excluded.{column}" for column in columns if column != "uid")
    return f"{sql} ON CONFLICT (uid) DO UPDATE SET {updates}"


def _write(
    connection: sqlite3.Connection, table: str, rows: Sequence[Row], *, upsert: bool
) -> None:
    if not rows:
        return
    columns = list(rows[0])
    connection.executemany(
        _write_sql(table, columns, upsert=upsert),
        [tuple(row[column] for column in columns) for row in rows],
    )


def _put_node(connection: sqlite3.Connection, node: EpistemicNode) -> None:
    _write(connection, "node", [_node_row(node)], upsert=True)


def _put_edges(connection: sqlite3.Connection, edges: Iterable[NodeEdge]) -> None:
    _write(connection, "node_edge", [_edge_row(edge) for edge in edges], upsert=True)


def _put_embedding(connection: sqlite3.Connection, embedding: EmbeddingRecord) -> None:
    _write(connection, "embedding", [_embedding_row(embedding)], upsert=True)


# A stored timeline's version. A record written before the field existed has no
# `version` key and reads as 0, which is what a caller holding it carries.
_TIMELINE_VERSION = "coalesce(data ->> '$.version', 0)"


def _put_timeline(connection: sqlite3.Connection, timeline: Timeline) -> None:
    """Store `timeline` on the version check (`next_timeline_version`), in a transaction.

    The comparison is in the `UPDATE`'s `WHERE`, so the version checked is the
    version of the row replaced. An `UPDATE` that matched nothing is either a
    timeline never stored or a stale copy. Only the first may insert, and only
    at version 0; `ON CONFLICT DO NOTHING` leaves a row somebody else wrote
    alone. Anything still unwritten raises, inside the caller's transaction, so
    everything else that transaction wrote rolls back with it.
    """
    expected = timeline.version
    row = _timeline_row(timeline.model_copy(update={"version": expected + 1}))
    updated = connection.execute(
        f"UPDATE timeline SET data = ? WHERE uid = ? AND {_TIMELINE_VERSION} = ?",
        (row["data"], row["uid"], expected),
    )
    if updated.rowcount == 1:
        return
    if expected == 0:
        inserted = connection.execute(
            "INSERT INTO timeline (uid, data) VALUES (?, ?) ON CONFLICT (uid) DO NOTHING",
            (row["uid"], row["data"]),
        )
        if inserted.rowcount == 1:
            return
    found = connection.execute(
        f"SELECT {_TIMELINE_VERSION} FROM timeline WHERE uid = ?", (row["uid"],)
    ).fetchone()
    raise TimelineConflict(timeline.id, expected, None if found is None else found[0])


def _delete_edges(connection: sqlite3.Connection, edge_ids: Iterable[str]) -> None:
    ids = list(edge_ids)
    if ids:
        connection.execute(f"DELETE FROM node_edge WHERE uid {_IN_LIST}", (_json(ids),))


@contextlib.contextmanager
def _transaction(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """`BEGIN IMMEDIATE`, the body, `COMMIT`; `ROLLBACK` if anything raises.

    Immediate, so the write lock is taken at the start rather than at the first
    write: a transaction that read first and then found another process holding
    the lock could only fail, while one that waits at `BEGIN` (for up to
    `busy_timeout`) reads a state nobody else can change before it commits.
    """
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield connection
    except BaseException:
        connection.execute("ROLLBACK")
        raise
    try:
        connection.execute("COMMIT")
    except BaseException:
        # A COMMIT that failed (a lock it could not take) leaves the transaction
        # open, and the next statement on this connection would join it.
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise


# --- Graph state ---


def _state(connection: sqlite3.Connection, key: str) -> Any:
    """A setting's stored value, or None when the graph has never set it."""
    row = connection.execute("SELECT value FROM graph_state WHERE key = ?", (key,)).fetchone()
    return None if row is None else json.loads(row[0])


def _set_state(connection: sqlite3.Connection, key: str, value: Any) -> None:
    """Store a setting, or remove its row for None, which reads back as the default.

    Removing rather than storing `null` keeps one representation of *follow the
    process default*: a threshold cleared and a threshold never set are the same
    row, which is no row.
    """
    if value is None:
        connection.execute("DELETE FROM graph_state WHERE key = ?", (key,))
        return
    connection.execute(
        "INSERT INTO graph_state (key, value) VALUES (?, ?) "
        "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        (key, _json(value)),
    )


def _bump(connection: sqlite3.Connection, key: str) -> int:
    """Add one to a counter and return the new count, in one statement.

    One statement rather than a read and a write, so two processes bumping the
    same graph cannot both read 4 and both write 5.
    """
    rows = connection.execute(
        "INSERT INTO graph_state (key, value) VALUES (?, '1') "
        "ON CONFLICT (key) DO UPDATE SET value = CAST(CAST(value AS INTEGER) + 1 AS TEXT) "
        "RETURNING value",
        (key,),
    ).fetchall()
    return int(rows[0][0])


def _reset(connection: sqlite3.Connection, key: str) -> int:
    """Zero a counter and return what it held, as one transaction."""
    with _transaction(connection):
        previous = _state(connection, key)
        _set_state(connection, key, 0)
    return int(previous or 0)


# --- Opening a graph ---


def _create_schema(connection: sqlite3.Connection) -> None:
    for statement in _SCHEMA:
        connection.execute(statement)
    connection.execute("INSERT INTO schema_version (version) VALUES (?)", (_SCHEMA_VERSION,))


def _migrate_schema(connection: sqlite3.Connection, graph: str) -> None:
    """Bring an existing graph file up to `_SCHEMA_VERSION`, a step at a time.

    Runs inside the transaction that opens the file, so a step that fails leaves
    the file at the version it came in at, and the marker is stamped once at
    the end. No steps yet: this is where the first one goes, as
    `if version < 2: ...` above the stamp, in the shape the SurrealDB adapter's
    `_migrate_schema` uses. None of that adapter's six migrations is ported;
    they rewrite graphs that only ever existed in SurrealDB, and a graph moves
    here through a bundle, which arrives at the current version.

    A file stamped above `_SCHEMA_VERSION` was written by a newer Epimemer and
    raises `SchemaTooNew` before anything is written, which rolls the opening
    transaction back with the file exactly as it was.
    """
    row = connection.execute("SELECT version FROM schema_version").fetchone()
    version = 0 if row is None else int(row[0])
    if version > _SCHEMA_VERSION:
        raise SchemaTooNew(graph, version, _SCHEMA_VERSION)
    if version == _SCHEMA_VERSION:
        return

    connection.execute("DELETE FROM schema_version")
    connection.execute("INSERT INTO schema_version (version) VALUES (?)", (_SCHEMA_VERSION,))


def _has_schema(connection: sqlite3.Connection) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_version'"
    ).fetchone()
    return row is not None


def _open(path: Path) -> sqlite3.Connection:
    """Open a graph file for reading and writing, creating and initialising it if absent.

    The busy timeout is set first, so even switching the journal mode waits for
    another process's lock rather than failing on it. A file this code cannot
    open, one a newer Epimemer wrote among them, closes the connection again and
    raises, so the caller is left holding whatever it held before.
    """
    connection = sqlite3.connect(path, autocommit=True)
    try:
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        with _transaction(connection):
            if _has_schema(connection):
                _migrate_schema(connection, path.stem)
            else:
                _create_schema(connection)
    except BaseException:
        connection.close()
        raise
    return connection


@contextlib.contextmanager
def _reading(path: Path) -> Iterator[sqlite3.Connection]:
    """A second, read-only connection to another graph's file, closed afterwards.

    A URI built by `as_uri` rather than by formatting the path in, so a
    directory whose name holds `?` or `#` cannot be read as URI syntax.
    """
    connection = sqlite3.connect(f"{path.absolute().as_uri()}?mode=ro", uri=True, autocommit=True)
    try:
        connection.execute("PRAGMA busy_timeout=5000")
        yield connection
    finally:
        connection.close()


# --- Edge migration, shared by supersession and merging ---


def _migrate_edges(
    connection: sqlite3.Connection, old_ids: set[str], new_id: str, *, status: NodeStatus
) -> None:
    """Carry edges touching `old_ids` onto `new_id`, per `migration_disposition`.

    The in-memory backend's `_migrate_edges_inplace`, step for step, over the
    same edges in the same order. `status` is why the old node is retiring and
    decides each edge's fate: a correction or a merge re-points everything but
    history, review and judgments; a world-change re-points nothing and copies
    only the metacontext and the tags.

    Self-loops are dropped and duplicate `(src, dst, type)` edges collapse to
    one, which keeps the periods of every edge folded into it. Survivors are
    written at the end, since a later duplicate can still widen one's validity.
    """
    ids = _json(sorted(old_ids))
    incident = _edges(connection, f"WHERE src_id {_IN_LIST} OR dst_id {_IN_LIST}", (ids, ids))
    survivors: dict[tuple[str, str, str], NodeEdge] = {}
    dropped: list[str] = []
    for edge in incident:
        disposition = migration_disposition(edge.type, status)
        if disposition == "keep":
            continue
        new_src = new_id if edge.src_id in old_ids else edge.src_id
        new_dst = new_id if edge.dst_id in old_ids else edge.dst_id
        if new_src == edge.src_id and new_dst == edge.dst_id:
            continue
        signature = (new_src, new_dst, _value(edge.type))
        survivor = survivors.get(signature)
        if new_src == new_dst or survivor is not None:
            # A copy that would collide is not made; a move drops the edge, since
            # leaving it would strand it on an endpoint it no longer describes.
            # What the dropped edge asserted is kept on the survivor.
            if survivor is not None:
                survivor.validity = merged_validity(survivor.validity, edge.validity)
            if disposition == "move":
                dropped.append(edge.id)
            continue
        if disposition == "copy":
            # Rebuilt rather than copied, so `id` and `created_at` come from
            # their factories: this is a new edge made now.
            survivors[signature] = NodeEdge(
                **(
                    edge.model_dump(exclude={"id", "created_at"})
                    | {"src_id": new_src, "dst_id": new_dst}
                )
            )
            continue
        survivors[signature] = edge.model_copy(update={"src_id": new_src, "dst_id": new_dst})
    _delete_edges(connection, dropped)
    _put_edges(connection, survivors.values())


def _retire(
    connection: sqlite3.Connection,
    node_id: str,
    *,
    status: NodeStatus,
    at: datetime,
    counterpart: str | None,
    judge: JudgeRef | None,
) -> None:
    node = _stored_node(connection, node_id)
    lifecycle = with_retirement(
        node.lifecycle, at=at, because=status, counterpart=counterpart, judge=judge
    )
    _put_node(
        connection,
        node.model_copy(update={"status": status, "superseded_at": at, "lifecycle": lifecycle}),
    )


def _return(
    connection: sqlite3.Connection, node_id: str, *, at: datetime, judge: JudgeRef | None
) -> None:
    node = _stored_node(connection, node_id)
    _put_node(
        connection,
        node.model_copy(
            update={
                "status": NodeStatus.ACTIVE,
                "superseded_at": None,
                "lifecycle": with_return(node.lifecycle, at=at, judge=judge),
            }
        ),
    )


# --- The decision journal's filters ---


def _decision_where(
    *,
    agent_ids: Sequence[str] | None = None,
    kinds: Sequence[DecisionKind] | None = None,
    subject_id: str | None = None,
    subject_ids: Sequence[str] | None = None,
    reviews: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> tuple[str, list[Any]]:
    """The journal filters as one WHERE clause and its parameters.

    Shared by every read of the journal, because a locator that counts a window
    differently from the reader it sends you to is worse than no locator. An
    empty list matches nothing, which is what a caller naming an empty set
    means. `since` is inclusive and `until` exclusive, and both are rendered
    through `iso_micros` because `decided_at` is compared as text.
    """
    clauses: list[str] = []
    params: list[Any] = []
    if agent_ids is not None:
        clauses.append(f"agent_id {_IN_LIST}")
        params.append(_json_list(agent_ids))
    if kinds is not None:
        clauses.append(f"kind {_IN_LIST}")
        params.append(_json_list(kinds))
    if subject_id is not None:
        clauses.append("EXISTS (SELECT 1 FROM json_each(decision.subject_ids) WHERE value = ?)")
        params.append(subject_id)
    if subject_ids is not None:
        clauses.append(
            f"EXISTS (SELECT 1 FROM json_each(decision.subject_ids) WHERE value {_IN_LIST})"
        )
        params.append(_json_list(subject_ids))
    if reviews is not None:
        clauses.append("reviews = ?")
        params.append(reviews)
    if since is not None:
        clauses.append("decided_at >= ?")
        params.append(iso_micros(since))
    if until is not None:
        clauses.append("decided_at < ?")
        params.append(iso_micros(until))
    return (f"WHERE {' AND '.join(clauses)}" if clauses else ""), params


# Newest first, ties broken by id: a batch written in one call shares a
# timestamp to the microsecond, and an order the clock does not settle has to
# be settled the same way on every backend.
_JOURNAL_ORDER = "ORDER BY decided_at DESC, uid DESC"


def _decisions(
    connection: sqlite3.Connection, where: str, params: Sequence[Any], limit: int | None = None
) -> list[DecisionRecord]:
    tail = "" if limit is None else " LIMIT ?"
    return _models(
        DecisionRecord,
        connection,
        f"SELECT data FROM decision {where} {_JOURNAL_ORDER}{tail}",
        [*params, *([] if limit is None else [int(limit)])],
    )


# Does this node name any of `:ids` as a judge? The SQL half of
# `node_judge_ids`, and it has to enumerate the same four places: a field added
# there and missed here would let `epimemer agents delete` remove a judge some
# node still points at.
_NODE_NAMES_JUDGE = (
    "data ->> '$.judged_by.agent_id' IN (SELECT value FROM json_each(:ids)) "
    "OR data ->> '$.value.importance_judged_by.agent_id' IN (SELECT value FROM json_each(:ids)) "
    "OR EXISTS (SELECT 1 FROM json_each(node.data, '$.lifecycle') AS episode "
    "WHERE episode.value ->> '$.retired_by.agent_id' IN (SELECT value FROM json_each(:ids)) "
    "OR episode.value ->> '$.restored_by.agent_id' IN (SELECT value FROM json_each(:ids)))"
)

_RECORD_NAMES_JUDGE = "data ->> '$.judged_by.agent_id' IN (SELECT value FROM json_each(:ids))"


def _cosine_scores(matrix: np.ndarray, query: np.ndarray) -> np.ndarray:
    """Cosine similarity of every row against `query`, in one matrix product.

    A zero vector on either side scores 0.0 rather than dividing by zero, which
    is the in-memory backend's rule.
    """
    norms = np.linalg.norm(matrix, axis=1) * np.linalg.norm(query)
    dots = matrix @ query
    return np.divide(dots, norms, out=np.zeros_like(dots), where=norms != 0)


class SQLiteStorage:
    """SQLite implementation of StorageBackend: one file per graph in `directory`."""

    def __init__(self, directory: str | os.PathLike[str], database: str = "default"):
        # Absolute from the start, so the graphs stay where they were configured
        # whatever the working directory later becomes, and `location` names a
        # place a user can find.
        self._directory = Path(directory).expanduser().absolute()
        # Validated here because the name becomes a file name, and a name that
        # could climb out of the directory must never reach `Path`.
        self._database = validate_graph_name(database)
        self._connection: sqlite3.Connection | None = None
        self._guard = make_graph_guard()

    # --- Lifecycle ---

    async def connect(self) -> None:
        if self._directory.exists() and not self._directory.is_dir():
            raise NotADirectoryError(
                f"{self._directory} exists and is not a directory. The SQLite backend keeps "
                "one file per graph in a directory: point EPIMEMER_SQLITE_DIR at one."
            )
        self._directory.mkdir(parents=True, exist_ok=True)
        await self.close()
        self._connection = _open(self._path(self._database))

    async def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    @property
    def _db(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("Not connected. Call connect() first.")
        return self._connection

    def _path(self, database: str) -> Path:
        return self._directory / f"{database}.sqlite"

    def _read_graph[T](
        self, database: str, read: Callable[[sqlite3.Connection], T], absent: T
    ) -> T:
        """`read` against another graph's file, or `absent` if there is no such graph.

        A name that is not a legal graph name cannot be a graph, and is never
        turned into a path. Naming a graph here does not create it.
        """
        if not isinstance(database, str) or not GRAPH_NAME_PATTERN.fullmatch(database):
            return absent
        path = self._path(database)
        if not path.is_file():
            return absent
        with _reading(path) as connection:
            return read(connection)

    # --- Documents ---

    async def store_document(self, doc: RawDocument) -> str:
        _write(self._db, "document", [_document_row(doc)], upsert=True)
        return doc.id

    async def get_document(self, doc_id: str) -> RawDocument | None:
        found = _models(RawDocument, self._db, "SELECT data FROM document WHERE uid = ?", (doc_id,))
        return found[0] if found else None

    async def get_document_by_source(self, source: str) -> RawDocument | None:
        found = _models(
            RawDocument,
            self._db,
            "SELECT data FROM document WHERE source IS ? ORDER BY rowid LIMIT 1",
            (source,),
        )
        return found[0] if found else None

    async def query_documents(self) -> Sequence[RawDocument]:
        return _models(RawDocument, self._db, "SELECT data FROM document ORDER BY rowid")

    # --- Segments ---

    async def store_segment(self, segment: Segment) -> str:
        _write(self._db, "segment", [_segment_row(segment)], upsert=True)
        return segment.id

    async def get_segments_for_document(self, doc_id: str) -> Sequence[Segment]:
        return _models(
            Segment,
            self._db,
            "SELECT data FROM segment WHERE document_id = ? ORDER BY rowid",
            (doc_id,),
        )

    async def get_segments(self, segment_ids: Sequence[str]) -> dict[str, Segment]:
        wanted = list(dict.fromkeys(segment_ids))
        if not wanted:
            return {}
        found = _models(
            Segment, self._db, f"SELECT data FROM segment WHERE uid {_IN_LIST}", (_json(wanted),)
        )
        return {segment.id: segment for segment in found}

    async def query_segments(self) -> Sequence[Segment]:
        return _models(Segment, self._db, "SELECT data FROM segment ORDER BY rowid")

    # --- Epistemic Nodes ---

    async def store_node(self, node: EpistemicNode) -> str:
        _put_node(self._db, node)
        return node.id

    async def get_node(self, node_id: str) -> EpistemicNode | None:
        return _node(self._db, node_id)

    async def get_nodes(self, node_ids: Sequence[str]) -> dict[str, EpistemicNode]:
        wanted = list(dict.fromkeys(node_ids))
        if not wanted:
            return {}
        found = _nodes(self._db, f"WHERE uid {_IN_LIST}", (_json(wanted),))
        return {node.id: node for node in found}

    async def query_nodes(
        self,
        *,
        node_type: NodeType | None = None,
        status: NodeStatus = NodeStatus.ACTIVE,
        at_time: datetime | None = None,
    ) -> Sequence[EpistemicNode]:
        clauses: list[str] = []
        params: list[Any] = []
        if node_type is not None:
            clauses.append("node_type = ?")
            params.append(_value(node_type))
        if at_time is None:
            clauses.append("status = ?")
            params.append(_value(status))
        else:
            # Active at that instant, whatever the status is now: born by then,
            # and not yet retired.
            at = iso_micros(at_time)
            clauses.append("created_at <= ? AND (superseded_at IS NULL OR superseded_at > ?)")
            params += [at, at]
        return _nodes(self._db, f"WHERE {' AND '.join(clauses)}", params)

    async def get_node_by_content(
        self,
        content: str,
        *,
        node_type: NodeType | None = None,
        status: NodeStatus = NodeStatus.ACTIVE,
    ) -> EpistemicNode | None:
        where = "WHERE content = ? AND status = ?"
        params: list[Any] = [content, _value(status)]
        if node_type is not None:
            where += " AND node_type = ?"
            params.append(_value(node_type))
        found = _nodes(self._db, where, params, "LIMIT 1")
        return found[0] if found else None

    async def query_changes(
        self,
        *,
        start: datetime,
        end: datetime,
        node_type: NodeType | None = None,
    ) -> Sequence[EpistemicNode]:
        """Born, retired, or with any lifecycle episode in `[start, end)`.

        Birth and the last retirement are answered by the indexed columns. The
        episodes live inside `data`, rendered by Pydantic, and are never compared
        as text: any node that has episodes at all comes back from the query and
        is decided here, on real datetimes, by the same predicate the in-memory
        backend applies.
        """
        lower, upper = iso_micros(start), iso_micros(end)
        where = (
            "WHERE ((created_at >= ? AND created_at < ?) "
            "OR (superseded_at IS NOT NULL AND superseded_at >= ? AND superseded_at < ?) "
            "OR json_array_length(data, '$.lifecycle') > 0)"
        )
        params: list[Any] = [lower, upper, lower, upper]
        if node_type is not None:
            where += " AND node_type = ?"
            params.append(_value(node_type))

        def changed(node: EpistemicNode) -> bool:
            born = start <= node.created_at < end
            retired = node.superseded_at is not None and start <= node.superseded_at < end
            in_episode = any(
                start <= episode.retired_at < end
                or (episode.restored_at is not None and start <= episode.restored_at < end)
                for episode in node.lifecycle
            )
            return born or retired or in_episode

        return [node for node in _nodes(self._db, where, params) if changed(node)]

    async def get_relation_kind(self, label: str) -> str | None:
        row = self._db.execute(
            "SELECT data ->> '$.kind' FROM node_edge "
            "WHERE type = ? AND data ->> '$.label' = ? ORDER BY rowid LIMIT 1",
            (EdgeType.RELATED.value, label),
        ).fetchone()
        return None if row is None else row[0]

    async def count_nodes_by_type(
        self,
        *,
        status: NodeStatus = NodeStatus.ACTIVE,
    ) -> dict[NodeType, int]:
        counts = {node_type: 0 for node_type in NodeType}
        rows = self._db.execute(
            "SELECT node_type, count(*) FROM node WHERE status = ? GROUP BY node_type",
            (_value(status),),
        )
        for node_type, count in rows:
            counts[NodeType(node_type)] = count
        return counts

    async def count_nodes_without_metacontext(
        self,
        *,
        status: NodeStatus = NodeStatus.ACTIVE,
    ) -> int:
        row = self._db.execute(
            "SELECT count(*) FROM node WHERE status = ? AND uid NOT IN "
            "(SELECT src_id FROM node_edge WHERE type = ?)",
            (_value(status), EdgeType.HAS_METACONTEXT.value),
        ).fetchone()
        return int(row[0])

    # --- Edges ---

    async def store_edge(self, edge: NodeEdge) -> str:
        _put_edges(self._db, [edge])
        return edge.id

    async def delete_edge(self, edge_id: str) -> None:
        self._db.execute("DELETE FROM node_edge WHERE uid = ?", (edge_id,))

    async def get_edges_from(
        self, node_id: str, *, edge_type: EdgeType | None = None
    ) -> Sequence[NodeEdge]:
        found = await self.get_edges_for([node_id], direction="from", edge_type=edge_type)
        return found[node_id]

    async def get_edges_to(
        self, node_id: str, *, edge_type: EdgeType | None = None
    ) -> Sequence[NodeEdge]:
        found = await self.get_edges_for([node_id], direction="to", edge_type=edge_type)
        return found[node_id]

    async def get_edges_for(
        self,
        node_ids: Sequence[str],
        *,
        direction: EdgeDirection,
        edge_type: EdgeType | None = None,
    ) -> dict[str, list[NodeEdge]]:
        wanted = list(dict.fromkeys(node_ids))
        # Pre-seeded, so an id with no edges is a key with an empty list.
        found: dict[str, list[NodeEdge]] = {node_id: [] for node_id in wanted}
        if not wanted:
            return found
        endpoint = "src_id" if direction == "from" else "dst_id"
        where = f"WHERE {endpoint} {_IN_LIST}"
        params: list[Any] = [_json(wanted)]
        if edge_type is not None:
            where += " AND type = ?"
            params.append(_value(edge_type))
        for edge in _edges(self._db, where, params):
            found[getattr(edge, endpoint)].append(edge)
        return found

    async def count_edges_by_type(self) -> dict[EdgeType, int]:
        counts = {edge_type: 0 for edge_type in EdgeType}
        for edge_type, count in self._db.execute(
            "SELECT type, count(*) FROM node_edge GROUP BY type"
        ):
            counts[EdgeType(edge_type)] = count
        return counts

    async def query_edges(self) -> Sequence[NodeEdge]:
        return _edges(self._db)

    # --- Atomic compound operations ---
    #
    # Each runs inside one `BEGIN IMMEDIATE` transaction and awaits nothing
    # inside it, mirroring the in-memory backend step for step. Every change to
    # a node's lifecycle starts from the row as stored, never from the argument.

    async def supersede_node_tx(
        self,
        old_node: EpistemicNode,
        new_node: EpistemicNode,
        new_embedding: EmbeddingRecord,
        lineage_edge: NodeEdge,
        *,
        status: NodeStatus,
        superseded_at: datetime,
        evidence_edges: Sequence[NodeEdge] = (),
        clear_edge_ids: Sequence[str] = (),
        judge: JudgeRef | None = None,
    ) -> None:
        with _transaction(self._db) as connection:
            _retire(
                connection,
                old_node.id,
                status=status,
                at=superseded_at,
                counterpart=new_node.id,
                judge=judge,
            )
            _put_node(connection, new_node)
            _put_embedding(connection, new_embedding)
            _migrate_edges(connection, {old_node.id}, new_node.id, status=status)
            _put_edges(connection, [lineage_edge, *evidence_edges])
            _delete_edges(connection, clear_edge_ids)

    async def supersede_by_existing_tx(
        self,
        old_node: EpistemicNode,
        existing_id: str,
        lineage_edge: NodeEdge,
        *,
        status: NodeStatus,
        superseded_at: datetime,
        evidence_edges: Sequence[NodeEdge] = (),
        clear_edge_ids: Sequence[str] = (),
        judge: JudgeRef | None = None,
    ) -> None:
        with _transaction(self._db) as connection:
            _retire(
                connection,
                old_node.id,
                status=status,
                at=superseded_at,
                counterpart=existing_id,
                judge=judge,
            )
            # No new node, no embedding, no migration: the existing node stands.
            _put_edges(connection, [lineage_edge, *evidence_edges])
            _delete_edges(connection, clear_edge_ids)

    async def set_node_status_tx(
        self,
        nodes: Sequence[EpistemicNode],
        *,
        status: NodeStatus,
        at: datetime,
        edges: Sequence[NodeEdge] = (),
        judge: JudgeRef | None = None,
    ) -> None:
        with _transaction(self._db) as connection:
            for node in nodes:
                if status is NodeStatus.ACTIVE:
                    _return(connection, node.id, at=at, judge=judge)
                else:
                    _retire(
                        connection, node.id, status=status, at=at, counterpart=None, judge=judge
                    )
            # In the same transaction, so a reactivated node is never left
            # ACTIVE without the edge saying what asserted it again.
            _put_edges(connection, edges)

    async def merge_nodes_tx(
        self,
        source_nodes: Sequence[EpistemicNode],
        merged_node: EpistemicNode,
        merged_embedding: EmbeddingRecord,
        lineage_edges: Sequence[NodeEdge],
        *,
        merged_at: datetime,
        evidence_edges: Sequence[NodeEdge] = (),
        judge: JudgeRef | None = None,
    ) -> None:
        with _transaction(self._db) as connection:
            _put_node(connection, merged_node)
            _put_embedding(connection, merged_embedding)
            # Migrate before writing lineage edges, so they are not re-pointed.
            _migrate_edges(
                connection,
                {source.id for source in source_nodes},
                merged_node.id,
                status=NodeStatus.MERGED,
            )
            for source in source_nodes:
                _retire(
                    connection,
                    source.id,
                    status=NodeStatus.MERGED,
                    at=merged_at,
                    counterpart=merged_node.id,
                    judge=judge,
                )
            # Evidence flags after migration, so each stays on the source whose
            # wording went away rather than being re-pointed at the survivor.
            _put_edges(connection, [*lineage_edges, *evidence_edges])

    async def reverse_merge_tx(
        self,
        survivor: EpistemicNode,
        source_nodes: Sequence[EpistemicNode],
        restored_edges: Sequence[NodeEdge],
        *,
        restored_at: datetime,
        delete_edge_ids: Sequence[str],
        judge: JudgeRef | None = None,
    ) -> None:
        """See the protocol. **The only hard delete of a node in this backend**,
        and it must never be reachable from an MCP tool (REVIEW_MODE.md §7.7)."""
        with _transaction(self._db) as connection:
            # Deletions first: a restored edge may reuse a `(src, dst, type)` the
            # merge had moved onto the survivor.
            _delete_edges(connection, delete_edge_ids)
            _put_edges(connection, restored_edges)
            for source in source_nodes:
                _return(connection, source.id, at=restored_at, judge=judge)
            # A survivor still carrying an edge means the reversal guard let
            # something through, and deleting the node would take it along.
            (remaining,) = connection.execute(
                "SELECT count(*) FROM node_edge WHERE src_id = ? OR dst_id = ?",
                (survivor.id, survivor.id),
            ).fetchone()
            if remaining:
                raise ValueError(
                    f"node {survivor.id} still has {remaining} edges and must not be "
                    f"deleted: a hard delete would take them with it."
                )
            connection.execute("DELETE FROM node WHERE uid = ?", (survivor.id,))
            # The vector is stored per item, so deleting the node alone would
            # strand an entry the search still returns.
            connection.execute("DELETE FROM embedding WHERE item_id = ?", (survivor.id,))

    async def write_batch_tx(
        self,
        *,
        nodes: Sequence[EpistemicNode] = (),
        edges: Sequence[NodeEdge] = (),
        embeddings: Sequence[EmbeddingRecord] = (),
        timelines: Sequence[Timeline] = (),
    ) -> None:
        """Insert-only for nodes, edges and embeddings, so re-writing an id fails
        the batch rather than quietly replacing a record; timelines upsert on
        the version check. The rollback is the transaction's, which puts back a
        replaced timeline's previous content without any bookkeeping here."""
        if not (nodes or edges or embeddings or timelines):
            return
        with _transaction(self._db) as connection:
            _write(connection, "node", [_node_row(node) for node in nodes], upsert=False)
            _write(connection, "node_edge", [_edge_row(edge) for edge in edges], upsert=False)
            for timeline in timelines:
                _put_timeline(connection, timeline)
            _write(connection, "embedding", [_embedding_row(e) for e in embeddings], upsert=False)

    async def write_timeline_tx(
        self,
        timeline: Timeline,
        *,
        kind: DecisionKind,
        retired_edges: Sequence[NodeEdge] = (),
        new_edges: Sequence[NodeEdge] = (),
        counts: Mapping[str, int] | None = None,
        judge: JudgeRef | None = None,
    ) -> None:
        """One timeline decision and the links it moved. See the protocol.

        `kind`, `counts` and `judge` describe the decision for the event log and
        change nothing here, which is why this backend reads none of them.
        """
        with _transaction(self._db) as connection:
            _put_timeline(connection, timeline)
            _put_edges(connection, [*new_edges, *retired_edges])

    async def reopen_tx(
        self,
        decision: DecisionRecord,
        *,
        retired_edges: Sequence[NodeEdge] = (),
        relation_verdicts: Sequence[RelationVerdict] = (),
    ) -> None:
        """One withdrawn suppression and the journal row that records it."""
        with _transaction(self._db) as connection:
            _put_edges(connection, retired_edges)
            _write(
                connection,
                "relation_verdict",
                [_relation_verdict_row(v) for v in relation_verdicts],
                upsert=False,
            )
            _write(connection, "decision", [_decision_row(decision)], upsert=True)

    async def write_verbatim_tx(
        self,
        *,
        documents: Sequence[RawDocument] = (),
        segments: Sequence[Segment] = (),
        nodes: Sequence[EpistemicNode] = (),
        edges: Sequence[NodeEdge] = (),
        embeddings: Sequence[EmbeddingRecord] = (),
        timelines: Sequence[Timeline] = (),
        metacontexts: Sequence[Metacontext] = (),
        relation_labels: Sequence[RelationLabel] = (),
        relation_verdicts: Sequence[RelationVerdict] = (),
        decisions: Sequence[DecisionRecord] = (),
        agents: Sequence[Agent] = (),
    ) -> None:
        """Every record as given, in one transaction. See the protocol.

        Plain inserts, one `executemany` per table, so an id that is already
        there fails the whole restore rather than overwriting what it found. The
        timestamp columns are `iso_micros` renderings of the instants given, as
        on every write here; `data` keeps each record exactly as it arrived.
        Relation labels go in directly rather than through
        `store_relation_label`, which merges, and merging is what this method
        exists not to do.
        """
        sections: list[tuple[str, list[Row]]] = [
            ("document", [_document_row(doc) for doc in documents]),
            ("segment", [_segment_row(segment) for segment in segments]),
            ("node", [_node_row(node) for node in nodes]),
            ("node_edge", [_edge_row(edge) for edge in edges]),
            ("embedding", [_embedding_row(embedding) for embedding in embeddings]),
            ("timeline", [_timeline_row(timeline) for timeline in timelines]),
            ("metacontext", [_metacontext_row(mc) for mc in metacontexts]),
            ("relation_label", [_relation_label_row(label) for label in relation_labels]),
            ("relation_verdict", [_relation_verdict_row(v) for v in relation_verdicts]),
            ("decision", [_decision_row(record) for record in decisions]),
            ("agent", [_agent_row(agent) for agent in agents]),
        ]
        if not any(rows for _, rows in sections):
            return
        with _transaction(self._db) as connection:
            for table, rows in sections:
                _write(connection, table, rows, upsert=False)

    async def resegment_tx(
        self,
        document_id: str,
        segments: Sequence[Segment],
        *,
        segmentation: Mapping[str, Any],
    ) -> int:
        """A document's passages replaced, if nothing cites them. See the protocol.

        `BEGIN IMMEDIATE` holds the write lock from the first read, so no other
        connection, in this process or another, can store a node between the
        count and the delete. A refusal raises inside the transaction, which
        rolls back having written nothing.
        """
        require_own_passages(document_id, segments)
        with _transaction(self._db) as connection:
            found = _models(
                RawDocument, connection, "SELECT data FROM document WHERE uid = ?", (document_id,)
            )
            if not found:
                raise KeyError(f"Document {document_id} not found")
            (citing,) = connection.execute(
                "SELECT count(*) FROM node WHERE data ->> '$.source_id' IN "
                "(SELECT uid FROM segment WHERE document_id = ?)",
                (document_id,),
            ).fetchone()
            if citing:
                raise PassagesCited(document_id=document_id, citing=citing)
            replaced = connection.execute(
                "DELETE FROM segment WHERE document_id = ?", (document_id,)
            ).rowcount
            _write(connection, "segment", [_segment_row(s) for s in segments], upsert=False)
            document = found[0]
            metadata = {**document.metadata, "segmentation": dict(segmentation)}
            _write(
                connection,
                "document",
                [_document_row(document.model_copy(update={"metadata": metadata}))],
                upsert=True,
            )
        return replaced

    # --- Embeddings ---

    async def store_embedding(self, embedding: EmbeddingRecord) -> str:
        _put_embedding(self._db, embedding)
        return embedding.id

    async def get_embeddings_for_item(
        self, item_id: str, model_id: str | None = None
    ) -> Sequence[EmbeddingRecord]:
        found = await self.get_embeddings_for_items([item_id], model_id=model_id)
        return found[item_id]

    async def get_embeddings_for_items(
        self, item_ids: Sequence[str], *, model_id: str | None = None
    ) -> dict[str, list[EmbeddingRecord]]:
        wanted = list(dict.fromkeys(item_ids))
        found: dict[str, list[EmbeddingRecord]] = {item_id: [] for item_id in wanted}
        if not wanted:
            return found
        where, params = _embedding_where(item_ids=wanted, model_id=model_id)
        for embedding in _embeddings(self._db, where, params):
            found[embedding.item_id].append(embedding)
        return found

    async def vector_search(
        self,
        query_vector: list[float],
        model_id: str,
        *,
        k: int = 10,
        node_type: NodeType | None = None,
        statuses: frozenset[NodeStatus] = frozenset({NodeStatus.ACTIVE}),
    ) -> Sequence[tuple[str, float]]:
        """Brute-force cosine similarity over the model's vectors, best first.

        Only embeddings of nodes are candidates, filtered by status and type
        before anything is ranked, so `k` counts results the caller can use.
        The blobs are stacked into one `(n, d)` array and scored in one matrix
        product. A stable sort keeps equal scores in insertion order, which is
        the order the in-memory backend's sort leaves them in.
        """
        where = f"WHERE embedding.model_id = ? AND node.status {_IN_LIST}"
        params: list[Any] = [model_id, _json_list(statuses)]
        if node_type is not None:
            where += " AND node.node_type = ?"
            params.append(_value(node_type))
        rows = self._db.execute(
            "SELECT embedding.item_id, embedding.vector FROM embedding "
            f"JOIN node ON node.uid = embedding.item_id {where} ORDER BY embedding.rowid",
            params,
        ).fetchall()
        if not rows:
            return []
        matrix = np.stack([np.frombuffer(vector, dtype="<f8") for _, vector in rows])
        scores = _cosine_scores(matrix, np.asarray(query_vector, dtype=np.float64))
        order = np.argsort(-scores, kind="stable")[:k]
        return [(rows[i][0], float(scores[i])) for i in order]

    # --- Lexical search ---

    async def text_search(
        self,
        terms: Sequence[str],
        *,
        corpus: Literal["nodes", "segments"],
        k: int = 10,
        node_type: NodeType | None = None,
        statuses: frozenset[NodeStatus] = frozenset({NodeStatus.ACTIVE}),
        verify_containment: bool = False,
    ) -> Sequence[tuple[str, float]]:
        """BM25 over one corpus partition, scored by `storage/bm25.py`.

        The in-memory backend's method, line for line: the whole partition is
        scored and *then* filtered by status, because IDF counts every row of
        the partition whatever its status. Ties break on id. FTS5 is not used:
        it would be a third tokenizer with its own rules for `JIRA-4417`, and
        the set parity the backends promise on rare terms rests on one.
        """
        if not terms:
            return []

        if corpus == "segments":
            rows = self._db.execute("SELECT uid, data ->> '$.text' FROM segment ORDER BY rowid")
            documents = dict(rows.fetchall())
            eligible = set(documents)
        else:
            if node_type is None:
                raise ValueError(
                    "text_search(corpus='nodes') requires a node_type: BM25 "
                    "statistics are per node table, so a merged multi-type list "
                    "would sort incomparable scores."
                )
            rows = self._db.execute(
                "SELECT uid, content, status FROM node WHERE node_type = ? ORDER BY rowid",
                (_value(node_type),),
            ).fetchall()
            wanted = {_value(status) for status in statuses}
            documents = {uid: content for uid, content, _ in rows}
            eligible = {uid for uid, _, status in rows if status in wanted}

        # The zero rule is applied here rather than in the scorer, so containment
        # can rescue a floored hit before it is dropped.
        candidates = [
            (doc_id, score)
            for doc_id, score in bm25_scores(documents, terms).items()
            if doc_id in eligible
        ]
        if not verify_containment:
            hits = [(doc_id, score) for doc_id, score in candidates if score > 0.0]
            hits.sort(key=lambda hit: (-hit[1], hit[0]))
            return hits[:k]

        return containment_first(candidates, documents, terms)[:k]

    async def get_nodes_by_source(
        self, source_ids: Sequence[str]
    ) -> dict[str, list[EpistemicNode]]:
        wanted = list(dict.fromkeys(source_ids))
        if not wanted:
            return {}
        by_source: dict[str, list[EpistemicNode]] = {source_id: [] for source_id in wanted}
        for node in _nodes(self._db, f"WHERE data ->> '$.source_id' {_IN_LIST}", (_json(wanted),)):
            by_source[node.source_id].append(node)
        return by_source

    # --- Timelines ---

    async def store_timeline(self, timeline: Timeline) -> str:
        with _transaction(self._db) as connection:
            _put_timeline(connection, timeline)
        return timeline.id

    async def get_timeline(self, timeline_id: str) -> Timeline | None:
        found = _models(
            Timeline, self._db, "SELECT data FROM timeline WHERE uid = ?", (timeline_id,)
        )
        return found[0] if found else None

    async def query_timelines(self) -> Sequence[Timeline]:
        return _models(Timeline, self._db, "SELECT data FROM timeline ORDER BY rowid")

    # --- Metacontexts ---

    async def store_metacontext(self, mc: Metacontext) -> str:
        _write(self._db, "metacontext", [_metacontext_row(mc)], upsert=True)
        return mc.id

    async def get_metacontext(self, mc_id: str) -> Metacontext | None:
        found = _models(
            Metacontext, self._db, "SELECT data FROM metacontext WHERE uid = ?", (mc_id,)
        )
        return found[0] if found else None

    async def query_metacontexts(
        self,
        *,
        status: NodeStatus = NodeStatus.ACTIVE,
    ) -> Sequence[Metacontext]:
        return _models(
            Metacontext,
            self._db,
            "SELECT data FROM metacontext WHERE status = ? ORDER BY rowid",
            (_value(status),),
        )

    # --- Relation labels ---

    async def store_relation_label(self, label: RelationLabel) -> str:
        """Upsert by `(name, kind)`: the record already there keeps its id.

        A read, a merge and a write, in one transaction so another process
        cannot record the same label between the read and the write.
        """
        with _transaction(self._db) as connection:
            existing = _models(
                RelationLabel,
                connection,
                "SELECT data FROM relation_label WHERE name = ? AND kind = ?",
                (label.name, label.kind),
            )
            stored = recorded_relation_label(existing[0] if existing else None, label)
            _write(connection, "relation_label", [_relation_label_row(stored)], upsert=True)
        return stored.id

    async def get_relation_label(self, name: str, kind: str) -> RelationLabel | None:
        found = _models(
            RelationLabel,
            self._db,
            "SELECT data FROM relation_label WHERE name = ? AND kind = ?",
            (name, kind),
        )
        return found[0] if found else None

    async def query_relation_labels(self) -> Sequence[RelationLabel]:
        return _models(RelationLabel, self._db, "SELECT data FROM relation_label ORDER BY rowid")

    # --- Relation verdicts ---

    async def record_relation_verdict(self, verdict: RelationVerdict) -> str:
        """Append one verdict. A plain insert: the table is append-only, so an id
        that is already there is a retry the caller should have refused."""
        _write(self._db, "relation_verdict", [_relation_verdict_row(verdict)], upsert=False)
        return verdict.id

    async def judged_relation_pairs(self) -> set[tuple[str, str]]:
        return standing_relation_pairs(await self.query_relation_verdicts())

    async def relation_verdicts_for(self, label_ids: Sequence[str]) -> Sequence[RelationVerdict]:
        """Verdicts on exactly this set of labels, newest first.

        Newest by `decided_at` compared as datetimes, never as text; a verdict
        appended later wins a tie, as it does in the in-memory backend.
        """
        wanted = set(label_ids)
        label_a, label_b = _label_pair(wanted)
        found = _models(
            RelationVerdict,
            self._db,
            "SELECT data FROM relation_verdict WHERE label_a IS ? AND label_b IS ? "
            "ORDER BY rowid DESC",
            (label_a, label_b),
        )
        matching = [verdict for verdict in found if set(verdict.label_ids) == wanted]
        return sorted(matching, key=lambda verdict: verdict.decided_at, reverse=True)

    async def query_relation_verdicts(self) -> Sequence[RelationVerdict]:
        return _models(
            RelationVerdict, self._db, "SELECT data FROM relation_verdict ORDER BY rowid"
        )

    # --- Reflection bookkeeping ---

    async def get_reflect_counter(self) -> int:
        return int(_state(self._db, _REFLECT_COUNTER) or 0)

    async def bump_reflect_counter(self) -> int:
        return _bump(self._db, _REFLECT_COUNTER)

    async def reset_reflect_counter(self) -> int:
        return _reset(self._db, _REFLECT_COUNTER)

    async def set_reflect_counter(self, count: int) -> None:
        _set_state(self._db, _REFLECT_COUNTER, int(count))

    async def get_reflect_threshold_override(self) -> int | None:
        return _state(self._db, _REFLECT_THRESHOLD)

    async def set_reflect_threshold_override(self, threshold: int | None) -> None:
        _set_state(self._db, _REFLECT_THRESHOLD, threshold)

    # --- Backup bookkeeping ---

    async def get_backup_counter(self) -> int:
        return int(_state(self._db, _BACKUP_COUNTER) or 0)

    async def bump_backup_counter(self) -> int:
        return _bump(self._db, _BACKUP_COUNTER)

    async def reset_backup_counter(self) -> int:
        return _reset(self._db, _BACKUP_COUNTER)

    async def set_backup_counter(self, count: int) -> None:
        _set_state(self._db, _BACKUP_COUNTER, int(count))

    async def get_backup_threshold_override(self) -> int | None:
        return _state(self._db, _BACKUP_THRESHOLD)

    async def set_backup_threshold_override(self, threshold: int | None) -> None:
        _set_state(self._db, _BACKUP_THRESHOLD, threshold)

    async def get_merge_overrides(self) -> MergeOverrides:
        stored = _state(self._db, _MERGE_OVERRIDES)
        return MergeOverrides() if stored is None else MergeOverrides.model_validate(stored)

    async def set_merge_overrides(self, overrides: MergeOverrides) -> None:
        _set_state(self._db, _MERGE_OVERRIDES, overrides.model_dump(mode="json"))

    async def get_warning_overrides(self) -> WarningOverrides:
        stored = _state(self._db, _WARNING_OVERRIDES)
        return WarningOverrides() if stored is None else WarningOverrides.model_validate(stored)

    async def set_warning_overrides(self, overrides: WarningOverrides) -> None:
        _set_state(self._db, _WARNING_OVERRIDES, overrides.model_dump(mode="json"))

    # --- Agents ---

    async def get_agent(self, agent_id: str) -> Agent | None:
        found = _models(Agent, self._db, "SELECT data FROM agent WHERE uid = ?", (agent_id,))
        return found[0] if found else None

    async def upsert_agent(self, agent: Agent) -> None:
        _write(self._db, "agent", [_agent_row(agent)], upsert=True)

    async def list_agents(self) -> list[Agent]:
        return _models(Agent, self._db, "SELECT data FROM agent ORDER BY rowid")

    async def delete_agent(self, agent_id: str) -> None:
        self._db.execute("DELETE FROM agent WHERE uid = ?", (agent_id,))

    async def judge_usage(self, agent_ids: Sequence[str]) -> JudgeUsage:
        """Counted in one statement of aggregates, never by materialising rows."""
        keys = list(dict.fromkeys(agent_ids))
        if not keys:
            return JudgeUsage()
        decisions, nodes, edges, relations = self._db.execute(
            "SELECT "
            "(SELECT count(*) FROM decision "
            "WHERE agent_id IN (SELECT value FROM json_each(:ids))), "
            f"(SELECT count(*) FROM node WHERE {_NODE_NAMES_JUDGE}), "
            f"(SELECT count(*) FROM node_edge WHERE {_RECORD_NAMES_JUDGE}), "
            f"(SELECT count(*) FROM relation_label WHERE {_RECORD_NAMES_JUDGE}) "
            f"+ (SELECT count(*) FROM relation_verdict WHERE {_RECORD_NAMES_JUDGE})",
            {"ids": _json(keys)},
        ).fetchone()
        return JudgeUsage(decisions=decisions, nodes=nodes, edges=edges, relations=relations)

    async def get_approved_agent_ids(self) -> list[str]:
        return list(_state(self._db, _APPROVED_AGENT_IDS) or [])

    async def set_approved_agent_ids(self, ids: list[str]) -> None:
        _set_state(self._db, _APPROVED_AGENT_IDS, list(ids))

    async def get_judge_confirmations(self) -> list[JudgeConfirmation]:
        """Newest last, as the protocol says; a stable sort leaves a list that
        already is exactly as it was written."""
        stored = _state(self._db, _JUDGE_CONFIRMATIONS) or []
        confirmations = [JudgeConfirmation.model_validate(entry) for entry in stored]
        return sorted(confirmations, key=lambda confirmation: confirmation.confirmed_at)

    async def set_judge_confirmations(self, confirmations: list[JudgeConfirmation]) -> None:
        _set_state(
            self._db,
            _JUDGE_CONFIRMATIONS,
            [confirmation.model_dump(mode="json") for confirmation in confirmations],
        )

    async def get_require_judge(self) -> bool | None:
        stored = _state(self._db, _REQUIRE_JUDGE)
        return None if stored is None else bool(stored)

    async def set_require_judge(self, required: bool | None) -> None:
        _set_state(self._db, _REQUIRE_JUDGE, required)

    # --- The decision journal ---

    async def record_decision(self, record: DecisionRecord) -> str:
        _write(self._db, "decision", [_decision_row(record)], upsert=True)
        return record.id

    async def get_decision(self, decision_id: str) -> DecisionRecord | None:
        found = _models(
            DecisionRecord, self._db, "SELECT data FROM decision WHERE uid = ?", (decision_id,)
        )
        return found[0] if found else None

    async def query_decisions(
        self,
        *,
        agent_ids: Sequence[str] | None = None,
        kinds: Sequence[DecisionKind] | None = None,
        subject_id: str | None = None,
        subject_ids: Sequence[str] | None = None,
        reviews: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int | None = None,
    ) -> list[DecisionRecord]:
        where, params = _decision_where(
            agent_ids=agent_ids,
            kinds=kinds,
            subject_id=subject_id,
            subject_ids=subject_ids,
            reviews=reviews,
            since=since,
            until=until,
        )
        return _decisions(self._db, where, params, limit)

    async def reviewed_decision_ids(self, decision_ids: Sequence[str]) -> set[str]:
        ids = list(dict.fromkeys(decision_ids))
        if not ids:
            return set()
        rows = self._db.execute(
            f"SELECT DISTINCT reviews FROM decision WHERE reviews {_IN_LIST}", (_json(ids),)
        )
        return {reviews for (reviews,) in rows}

    async def count_decisions_by_graph(
        self,
        databases: Sequence[str],
        *,
        agent_ids: Sequence[str] | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> dict[str, int]:
        """Counts per named graph, each read from that graph's own file.

        Nothing is borrowed, so no mover turn is taken: a second connection
        reads another graph without going near the active one. A graph with no
        file is left out of the answer rather than counted zero, and asking
        about one does not create it.
        """
        where, params = _decision_where(agent_ids=agent_ids, since=since, until=until)

        def count(connection: sqlite3.Connection) -> int:
            (total,) = connection.execute(
                f"SELECT count(*) FROM decision {where}", params
            ).fetchone()
            return int(total)

        counts: dict[str, int] = {}
        for database in dict.fromkeys(databases):
            total = self._read_graph(database, count, None)
            if total is not None:
                counts[database] = total
        return counts

    # --- Multi-graph management ---

    @property
    def backend_name(self) -> str:
        return "sqlite"

    @property
    def location(self) -> str:
        return f"in {self._directory}, one <graph>.sqlite file per graph"

    @property
    def current_database(self) -> str:
        return self._database

    @property
    def graph_guard(self) -> GraphGuard:
        return self._guard

    async def list_databases(self) -> list[str]:
        """The graph files in the directory, by name without the suffix.

        A file whose name is not a legal graph name is not offered: it is not
        something `switch_database` could open.
        """
        if not self._directory.is_dir():
            return []
        return sorted(
            path.stem
            for path in self._directory.glob("*.sqlite")
            if path.is_file() and GRAPH_NAME_PATTERN.fullmatch(path.stem)
        )

    async def switch_database(self, database: str) -> None:
        """Open `<database>.sqlite`, creating and initialising it if absent.

        Takes the guard's mover turn, so a call in flight finishes against the
        graph it started on. The new file is opened before the old one is
        closed, so a graph that cannot be opened leaves the active one in place.
        """
        validate_graph_name(database)
        async with self._guard.moving():
            self._directory.mkdir(parents=True, exist_ok=True)
            connection = _open(self._path(database))
            if self._connection is not None:
                self._connection.close()
            self._connection = connection
            self._database = database

    async def delete_database(self, database: str) -> None:
        """Remove a graph's file and the `-wal` and `-shm` files beside it."""
        validate_graph_name(database)
        if database == self._database:
            raise ValueError(f"Cannot delete the currently active graph '{database}'")
        path = self._path(database)
        if not path.is_file():
            raise KeyError(f"Graph '{database}' does not exist")
        path.unlink()
        for suffix in ("-wal", "-shm"):
            Path(f"{path}{suffix}").unlink(missing_ok=True)

    # --- Viz reads (cross-graph, no switching) ---
    #
    # Each opens the named graph's file on a second, read-only connection and
    # closes it again. No `moving()` turn, for the in-memory backend's reason:
    # nothing about the active graph is borrowed, so there is nothing to give
    # back. A graph with no file answers with an empty list.

    async def viz_list_nodes(
        self,
        database: str,
        *,
        historical_status: NodeStatus = NodeStatus.ACTIVE,
    ) -> Sequence[EpistemicNode]:
        return self._read_graph(
            database,
            lambda connection: _nodes(connection, "WHERE status = ?", (_value(historical_status),)),
            [],
        )

    async def viz_list_edges(
        self,
        database: str,
    ) -> Sequence[NodeEdge]:
        return self._read_graph(database, _edges, [])

    async def viz_list_timelines(
        self,
        database: str,
    ) -> Sequence[Timeline]:
        return self._read_graph(
            database,
            lambda connection: _models(
                Timeline, connection, "SELECT data FROM timeline ORDER BY rowid"
            ),
            [],
        )

    async def viz_list_metacontexts(
        self,
        database: str,
    ) -> Sequence[Metacontext]:
        return self._read_graph(
            database,
            lambda connection: _models(
                Metacontext,
                connection,
                "SELECT data FROM metacontext WHERE status = ? ORDER BY rowid",
                (NodeStatus.ACTIVE.value,),
            ),
            [],
        )

    async def viz_list_relation_labels(
        self,
        database: str,
    ) -> Sequence[RelationLabel]:
        return self._read_graph(
            database,
            lambda connection: _models(
                RelationLabel, connection, "SELECT data FROM relation_label ORDER BY rowid"
            ),
            [],
        )

    async def viz_list_decisions(
        self,
        database: str,
        *,
        kinds: Sequence[DecisionKind] | None = None,
    ) -> Sequence[DecisionRecord]:
        where, params = _decision_where(kinds=kinds)
        return self._read_graph(
            database, lambda connection: _decisions(connection, where, params), []
        )

    async def viz_list_embeddings(
        self,
        database: str,
        *,
        item_ids: Sequence[str] | None = None,
        model_id: str | None = None,
    ) -> Sequence[EmbeddingRecord]:
        where, params = _embedding_where(item_ids=item_ids, model_id=model_id)
        return self._read_graph(
            database, lambda connection: _embeddings(connection, where, params), []
        )
