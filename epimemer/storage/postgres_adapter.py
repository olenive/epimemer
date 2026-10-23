"""Postgres storage backend.

One schema per graph in one Postgres database, reached over one connection.
`dev-docs/STORAGE_BACKENDS.md` is the design, and this module has the shape of
`sqlite_adapter.py`: the same tables, the same filter columns copied out of a
`data` record, the same helpers, with Postgres types. What differs is said
here.

**Every table has the SQLite shape**: `uid` as the primary key, a column for
each field a query filters or orders on, and `data`, the whole record as
`jsonb` (`normalize_for_storage`, then `model_dump(mode="json")`). `data` is
the truth and is what every read rehydrates through `model_validate`; a filter
column disagreeing with it is a bug. Vectors are `bytea` of little-endian
float64, the blob the SQLite backend writes, and timestamps are `timestamptz`,
so a bound is a datetime and nothing is rendered to text to be compared. Each
table also carries `seq`, an identity column that stands in for SQLite's
`rowid`: insertion order is what every unordered read hands back, and an
upsert keeps a row's `seq` as it keeps its place.

**Every statement names its schema**, `"<graph>".node`, through
`psycopg.sql.Identifier`, and nothing sets `search_path`. Switching graphs
changes one attribute and nothing on the connection, so a reconnect cannot
lose the active graph, and reading another graph is an ordinary query against
another schema: no second connection, no borrowing, no mover turn.

**The connection runs in autocommit mode**, so every statement commits on its
own and a transaction exists only where the code says so: the `*_tx`
methods, the two read-modify-writes (resetting a counter, merging a relation
label), `store_timeline`'s version check, and opening or deleting a graph, each
inside `connection.transaction()`.

**One lock serialises every unit of work on the connection.** psycopg
serialises single operations, but two tool calls in flight in one process could
still have their statements alternate, and a transaction's statements must not
be joined by another call's. Every method holds `_lock` for the whole of its
work, and a transaction awaits nothing but its own statements.

**Several processes can share a graph**, which is what this backend is for.
Every per-graph setting and counter is read from the database on each call and
never cached here. A lifecycle change locks the node row it starts from
(`FOR UPDATE`), so two servers retiring one node apply their episodes one
after the other rather than one over the other; a counter moves in one
statement, so two bumps at once both count; merging a relation label locks
that table against other writers for the length of its transaction; a re-cut
locks the node table against writers while it checks that no claim cites the
passages it replaces; and a timeline is replaced only by an `UPDATE` whose
`WHERE` names the version the writer read, so of two servers writing one
timeline from one version, the second is refused rather than written over the
first.
"""

import asyncio
import contextlib
import hashlib
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Literal

import numpy as np
import psycopg
from psycopg import AsyncConnection, errors, sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.types.json import Jsonb
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
    DeferralOverrides,
    EdgeDirection,
    MergeOverrides,
    PassagesCited,
    ReflectCompletion,
    SchemaTooNew,
    TimelineConflict,
    WarningOverrides,
    normalize_for_storage,
    require_own_passages,
    validate_graph_name,
)

# --- Schema ---

_SCHEMA_VERSION = 1

# The table whose presence makes a schema a graph: one row holding the graph's
# name and its schema version. Named for this project rather than plainly
# `schema_version`, because a graph is something an agent can delete, and
# deleting one drops its schema with everything in it. Flyway before version 5
# and many hand-written migration scripts keep a table called `schema_version`
# in their own schema, often `public`, and a marker that common would offer a
# team's application schema as a graph to be dropped.
_MARKER = "epimemer_graph"

# Postgres keeps 63 bytes of an identifier and reserves names beginning `pg_`,
# while a graph name may have 64 characters and begin with anything. Graph
# names are ASCII, so characters and bytes are the same count here.
_MAX_IDENTIFIER = 63
# Never in a graph name, so a derived schema name (`_schema_for`) can never be
# another graph's plain one.
_DERIVED = "~"

# Every statement here goes through `_q`, which fills `{g}` with the graph's
# schema as a quoted identifier. Literal braces would be read as placeholders,
# so none of this module's SQL contains one.
#
# `uid` collates as "C", byte order, which for UTF-8 is code-point order: the
# journal breaks ties on `uid`, and the order has to be the one Python's string
# comparison gives the other backends, whatever locale the database was made
# with. No foreign keys anywhere: edges to retired nodes, decisions about
# deleted judges and verdicts about renamed labels are all meaningful on an
# append-only graph, and every backend treats these ids as soft references.
_SCHEMA = (
    "CREATE SCHEMA IF NOT EXISTS {g}",
    """
    CREATE TABLE IF NOT EXISTS {g}.document (
        seq bigint GENERATED ALWAYS AS IDENTITY,
        uid text COLLATE "C" PRIMARY KEY,
        source text,
        created_at timestamptz NOT NULL,
        data jsonb NOT NULL
    )
    """,
    # Hash rather than B-tree for the long text columns: a B-tree entry is
    # limited to about 2.7 kB, and a document source or a node's content can be
    # longer than that, while every query on either is an equality.
    "CREATE INDEX IF NOT EXISTS document_source ON {g}.document USING hash (source)",
    """
    CREATE TABLE IF NOT EXISTS {g}.segment (
        seq bigint GENERATED ALWAYS AS IDENTITY,
        uid text COLLATE "C" PRIMARY KEY,
        document_id text NOT NULL,
        data jsonb NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS segment_document ON {g}.segment (document_id)",
    # Topics, facts and inferences share one table, so a node's type is one
    # indexed read away rather than a probe of three tables in turn.
    """
    CREATE TABLE IF NOT EXISTS {g}.node (
        seq bigint GENERATED ALWAYS AS IDENTITY,
        uid text COLLATE "C" PRIMARY KEY,
        node_type text NOT NULL,
        status text NOT NULL,
        content text NOT NULL,
        created_at timestamptz NOT NULL,
        superseded_at timestamptz,
        data jsonb NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS node_type_status ON {g}.node (node_type, status)",
    "CREATE INDEX IF NOT EXISTS node_content ON {g}.node USING hash (content)",
    """
    CREATE TABLE IF NOT EXISTS {g}.node_edge (
        seq bigint GENERATED ALWAYS AS IDENTITY,
        uid text COLLATE "C" PRIMARY KEY,
        src_id text NOT NULL,
        dst_id text NOT NULL,
        type text NOT NULL,
        data jsonb NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS node_edge_src_type ON {g}.node_edge (src_id, type)",
    "CREATE INDEX IF NOT EXISTS node_edge_dst_type ON {g}.node_edge (dst_id, type)",
    """
    CREATE TABLE IF NOT EXISTS {g}.embedding (
        seq bigint GENERATED ALWAYS AS IDENTITY,
        uid text COLLATE "C" PRIMARY KEY,
        item_id text NOT NULL,
        model_id text NOT NULL,
        vector bytea NOT NULL,
        data jsonb NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS embedding_item_model ON {g}.embedding (item_id, model_id)",
    "CREATE INDEX IF NOT EXISTS embedding_model ON {g}.embedding (model_id)",
    """
    CREATE TABLE IF NOT EXISTS {g}.timeline (
        seq bigint GENERATED ALWAYS AS IDENTITY,
        uid text COLLATE "C" PRIMARY KEY,
        data jsonb NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS {g}.metacontext (
        seq bigint GENERATED ALWAYS AS IDENTITY,
        uid text COLLATE "C" PRIMARY KEY,
        status text NOT NULL,
        data jsonb NOT NULL
    )
    """,
    # Unique, because `(name, kind)` is the key edges join a label on, and the
    # writer is what keeps it one record per pair.
    """
    CREATE TABLE IF NOT EXISTS {g}.relation_label (
        seq bigint GENERATED ALWAYS AS IDENTITY,
        uid text COLLATE "C" PRIMARY KEY,
        name text NOT NULL,
        kind text NOT NULL,
        data jsonb NOT NULL
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS relation_label_name_kind ON {g}.relation_label (name, kind)",
    # The pair a verdict is about, smaller id first, so `(a, b)` and `(b, a)`
    # are one key. Append-only: nothing updates or deletes a row here.
    """
    CREATE TABLE IF NOT EXISTS {g}.relation_verdict (
        seq bigint GENERATED ALWAYS AS IDENTITY,
        uid text COLLATE "C" PRIMARY KEY,
        label_a text,
        label_b text,
        data jsonb NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS relation_verdict_pair ON {g}.relation_verdict (label_a, label_b)",
    """
    CREATE TABLE IF NOT EXISTS {g}.agent (
        seq bigint GENERATED ALWAYS AS IDENTITY,
        uid text COLLATE "C" PRIMARY KEY,
        data jsonb NOT NULL
    )
    """,
    # The journal. `subject_ids` is the list as `jsonb`, tested with the
    # containment and key-existence operators, which a GIN index can serve if
    # the journal ever grows enough to want one.
    """
    CREATE TABLE IF NOT EXISTS {g}.decision (
        seq bigint GENERATED ALWAYS AS IDENTITY,
        uid text COLLATE "C" PRIMARY KEY,
        agent_id text,
        kind text NOT NULL,
        subject_ids jsonb NOT NULL,
        reviews text,
        decided_at timestamptz NOT NULL,
        data jsonb NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS decision_agent ON {g}.decision (agent_id)",
    "CREATE INDEX IF NOT EXISTS decision_kind ON {g}.decision (kind)",
    "CREATE INDEX IF NOT EXISTS decision_reviews ON {g}.decision (reviews)",
    "CREATE INDEX IF NOT EXISTS decision_decided_at ON {g}.decision (decided_at)",
    # Per-graph settings and counters, one row each. An absent row is the
    # default the in-memory backend starts with.
    """
    CREATE TABLE IF NOT EXISTS {g}.graph_state (
        key text PRIMARY KEY,
        value jsonb NOT NULL
    )
    """,
    f"CREATE TABLE IF NOT EXISTS {{g}}.{_MARKER} (name text NOT NULL, version integer NOT NULL)",
)

# The keys of `graph_state`. Named once so a getter and its setter cannot
# disagree about which row they mean.
_REFLECT_COUNTER = "reflect_counter"
_BACKUP_COUNTER = "backup_counter"
_REFLECT_THRESHOLD = "reflect_threshold_override"
_BACKUP_THRESHOLD = "backup_threshold_override"
_REFLECT_ROUND = "reflect_round"
_MERGE_OVERRIDES = "merge_overrides"
_DEFERRAL_OVERRIDES = "deferral_overrides"
_WARNING_OVERRIDES = "warning_overrides"
_APPROVED_AGENT_IDS = "approved_agent_ids"
_JUDGE_CONFIRMATIONS = "judge_confirmations"
_REQUIRE_JUDGE = "require_judge"

_NODE_CLASSES: dict[str, type] = {
    NodeType.TOPIC.value: Topic,
    NodeType.FACT.value: Fact,
    NodeType.INFERENCE.value: Inference,
}

# Lists of ids reach a query as one array parameter, so a request of any size is
# one statement. Typed, so an empty list is an empty text array rather than a
# literal Postgres has to guess the type of.
_ANY = "= ANY(%s::text[])"

type Connection = AsyncConnection[Any]
type Work[T] = Callable[[Connection, sql.Identifier], Awaitable[T]]


def _q(template: str, g: sql.Identifier) -> sql.Composed:
    """A statement with its graph's schema in place of `{g}`.

    Every table name in this module is written as `{g}.<table>`, so a statement
    cannot reach a graph other than the one it was composed for. The template is
    always this module's own text; values travel as parameters.
    """
    return sql.SQL(template).format(g=g)


def _schema_for(name: str) -> str:
    """The schema a graph lives in, or ValueError for a name no backend accepts.

    The graph's own name wherever Postgres can hold it, which is every name in
    practice. A name it cannot hold, one of 64 characters or one beginning
    `pg_`, lives in `~<its first 45 characters>~<16 hex digits of its hash>`:
    63 characters, still recognisable in a schema listing, and marked with a
    character no graph name contains. Which graph a derived schema holds is
    read from its marker, since a hash cannot be read backwards.
    """
    validate_graph_name(name)
    if len(name) <= _MAX_IDENTIFIER and not name.startswith("pg_"):
        return name
    digest = hashlib.blake2b(name.encode(), digest_size=8).hexdigest()
    return f"{_DERIVED}{name[:45]}{_DERIVED}{digest}"


def _is_graph_name(name: Any) -> bool:
    return isinstance(name, str) and GRAPH_NAME_PATTERN.fullmatch(name) is not None


def _address(dsn: str) -> str:
    """Where a DSN points, as prose: host, port and database, never the password.

    Parsed rather than shown, because the DSN may carry the password and this
    string is printed in the startup log and returned to agents. A part the DSN
    leaves out is libpq's default, said as such.
    """
    parts = conninfo_to_dict(dsn)
    host = parts.get("host") or parts.get("hostaddr") or "the default host"
    port = parts.get("port")
    where = host if port is None else f"{host}:{port}"
    database = parts.get("dbname") or "named after the user"
    return f"Postgres at {where}, database {database}, one schema per graph"


def _lock_key(name: str) -> int:
    """The advisory lock that serialises opening and deleting one graph.

    Two servers starting at once on a new graph would otherwise both try to
    create its schema, and Postgres refuses the second `CREATE ... IF NOT EXISTS`
    racing the first rather than waiting for it. A stable 64-bit hash of the
    name, since Python's `hash` changes between processes.
    """
    digest = hashlib.blake2b(f"epimemer-graph:{name}".encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big", signed=True)


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


def _values(members: Iterable[Any]) -> list[Any]:
    return [_value(member) for member in members]


def _aware(at: datetime) -> datetime:
    """An instant as `timestamptz` takes it.

    A naive datetime is taken to be UTC, which is what every writer in this
    project means by one, and what `iso_micros` assumes on the other backends.
    Passed as is it would be read in the session's time zone instead.
    """
    return at.replace(tzinfo=UTC) if at.tzinfo is None else at


def _aware_or_none(at: datetime | None) -> datetime | None:
    return None if at is None else _aware(at)


# --- Rows: a record as the columns its table stores ---
#
# Each filter column is read off the same JSON dump that becomes `data`, so the
# column holds the very value the record carries. Timestamp columns hold the
# record's instant as a datetime; `data` keeps Pydantic's rendering, which
# nothing compares.

Row = dict[str, Any]


def _dumped[M: BaseModel](model: M, **dump: Any) -> tuple[M, dict]:
    record = normalize_for_storage(model)
    return record, record.model_dump(mode="json", **dump)


def _document_row(doc: RawDocument) -> Row:
    record, dump = _dumped(doc)
    return {
        "uid": record.id,
        "source": dump["source"],
        "created_at": _aware(record.created_at),
        "data": Jsonb(dump),
    }


def _segment_row(segment: Segment) -> Row:
    record, dump = _dumped(segment)
    return {"uid": record.id, "document_id": dump["source_id"], "data": Jsonb(dump)}


def _node_row(node: EpistemicNode) -> Row:
    record, dump = _dumped(node)
    return {
        "uid": record.id,
        "node_type": _node_type_of(record).value,
        "status": dump["status"],
        "content": dump["content"],
        "created_at": _aware(record.created_at),
        "superseded_at": _aware_or_none(record.superseded_at),
        "data": Jsonb(dump),
    }


def _edge_row(edge: NodeEdge) -> Row:
    record, dump = _dumped(edge)
    return {
        "uid": record.id,
        "src_id": dump["src_id"],
        "dst_id": dump["dst_id"],
        "type": dump["type"],
        "data": Jsonb(dump),
    }


def _vector_blob(vector: Sequence[float]) -> bytes:
    return np.asarray(vector, dtype="<f8").tobytes()


def _embedding_row(embedding: EmbeddingRecord) -> Row:
    # The vector is left out of `data`: it is the blob, and a second copy as
    # JSON would cost exactly what the blob exists to save.
    record, dump = _dumped(embedding, exclude={"vector"})
    return {
        "uid": record.id,
        "item_id": dump["item_id"],
        "model_id": dump["model_id"],
        "vector": _vector_blob(record.vector),
        "data": Jsonb(dump),
    }


def _timeline_row(timeline: Timeline) -> Row:
    record, dump = _dumped(timeline)
    return {"uid": record.id, "data": Jsonb(dump)}


def _metacontext_row(mc: Metacontext) -> Row:
    record, dump = _dumped(mc)
    return {"uid": record.id, "status": dump["status"], "data": Jsonb(dump)}


def _relation_label_row(label: RelationLabel) -> Row:
    record, dump = _dumped(label)
    return {"uid": record.id, "name": dump["name"], "kind": dump["kind"], "data": Jsonb(dump)}


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
    return {"uid": record.id, "label_a": label_a, "label_b": label_b, "data": Jsonb(dump)}


def _agent_row(agent: Agent) -> Row:
    record, dump = _dumped(agent)
    return {"uid": record.id, "data": Jsonb(dump)}


def _decision_row(decision: DecisionRecord) -> Row:
    record, dump = _dumped(decision)
    judge = dump["judged_by"]
    return {
        "uid": record.id,
        "agent_id": None if judge is None else judge["agent_id"],
        "kind": dump["kind"],
        "subject_ids": Jsonb(dump["subject_ids"]),
        "reviews": dump["reviews"],
        "decided_at": _aware(record.decided_at),
        "data": Jsonb(dump),
    }


# --- Statements ---


async def _execute(
    connection: Connection, query: sql.Composable, params: Sequence[Any] | Mapping[str, Any] = ()
) -> None:
    await connection.execute(query, params)


async def _fetch(
    connection: Connection, query: sql.Composable, params: Sequence[Any] | Mapping[str, Any] = ()
) -> list[tuple]:
    cursor = await connection.execute(query, params)
    return await cursor.fetchall()


async def _fetch_one(
    connection: Connection, query: sql.Composable, params: Sequence[Any] | Mapping[str, Any] = ()
) -> tuple | None:
    cursor = await connection.execute(query, params)
    return await cursor.fetchone()


# --- Reading records back ---


def _load_node(node_type: str, data: dict) -> EpistemicNode:
    return _NODE_CLASSES[node_type].model_validate(data)


def _load_embedding(data: dict, vector: bytes) -> EmbeddingRecord:
    return EmbeddingRecord.model_validate(
        data | {"vector": np.frombuffer(vector, dtype="<f8").tolist()}
    )


async def _models[M: BaseModel](
    model: type[M], connection: Connection, query: sql.Composable, params: Sequence[Any] = ()
) -> list[M]:
    return [model.model_validate(data) for (data,) in await _fetch(connection, query, params)]


async def _nodes(
    connection: Connection,
    g: sql.Identifier,
    where: str = "",
    params: Sequence[Any] = (),
    tail: str = "",
) -> list[EpistemicNode]:
    """Nodes in insertion order, which is the order the in-memory backend's dict
    keeps and the order "first node with this content" means."""
    query = _q(f"SELECT node_type, data FROM {{g}}.node {where} ORDER BY seq {tail}", g)
    return [
        _load_node(node_type, data) for node_type, data in await _fetch(connection, query, params)
    ]


async def _edges(
    connection: Connection, g: sql.Identifier, where: str = "", params: Sequence[Any] = ()
) -> list[NodeEdge]:
    query = _q(f"SELECT data FROM {{g}}.node_edge {where} ORDER BY seq", g)
    return await _models(NodeEdge, connection, query, params)


async def _embeddings(
    connection: Connection, g: sql.Identifier, where: str = "", params: Sequence[Any] = ()
) -> list[EmbeddingRecord]:
    query = _q(f"SELECT data, vector FROM {{g}}.embedding {where} ORDER BY seq", g)
    return [
        _load_embedding(data, vector) for data, vector in await _fetch(connection, query, params)
    ]


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
        clauses.append(f"item_id {_ANY}")
        params.append(_values(item_ids))
    if model_id is not None:
        clauses.append("model_id = %s")
        params.append(model_id)
    return (f"WHERE {' AND '.join(clauses)}" if clauses else ""), params


async def _node(connection: Connection, g: sql.Identifier, node_id: str) -> EpistemicNode | None:
    found = await _nodes(connection, g, "WHERE uid = %s", (node_id,))
    return found[0] if found else None


async def _stored_node(connection: Connection, g: sql.Identifier, node_id: str) -> EpistemicNode:
    """The node as stored, which is what every lifecycle change is applied to,
    locked until the transaction it is read in ends.

    A transaction's node argument says *which* node to change; its fields are a
    snapshot the caller took at some earlier moment, and appending an episode to
    a stale history would silently drop every episode since. The row lock is the
    same argument across processes: a second server changing this node waits,
    then reads the history this one wrote.
    """
    found = await _nodes(connection, g, "WHERE uid = %s", (node_id,), "FOR UPDATE")
    if not found:
        raise KeyError(f"Node {node_id} not found")
    return found[0]


# --- Writing ---


def _write_sql(table: str, columns: Sequence[str], g: sql.Identifier, *, upsert: bool):
    """`INSERT` for `columns`, or an upsert on `uid` that keeps the row in place.

    `ON CONFLICT DO UPDATE` leaves `seq` as it was, so an updated record keeps
    its place in insertion order, as SQLite's upsert keeps its `rowid`.
    """
    names = [sql.Identifier(column) for column in columns]
    insert = sql.SQL("INSERT INTO {g}.{table} ({columns}) VALUES ({marks})").format(
        g=g,
        table=sql.Identifier(table),
        columns=sql.SQL(", ").join(names),
        marks=sql.SQL(", ").join(sql.Placeholder() * len(columns)),
    )
    if not upsert:
        return insert
    updates = sql.SQL(", ").join(
        sql.SQL("{column} = EXCLUDED.{column}").format(column=name)
        for column, name in zip(columns, names, strict=True)
        if column != "uid"
    )
    return sql.SQL("{insert} ON CONFLICT (uid) DO UPDATE SET {updates}").format(
        insert=insert, updates=updates
    )


async def _write(
    connection: Connection, g: sql.Identifier, table: str, rows: Sequence[Row], *, upsert: bool
) -> None:
    if not rows:
        return
    columns = list(rows[0])
    async with connection.cursor() as cursor:
        await cursor.executemany(
            _write_sql(table, columns, g, upsert=upsert),
            [tuple(row[column] for column in columns) for row in rows],
        )


async def _put_node(connection: Connection, g: sql.Identifier, node: EpistemicNode) -> None:
    await _write(connection, g, "node", [_node_row(node)], upsert=True)


async def _put_edges(connection: Connection, g: sql.Identifier, edges: Iterable[NodeEdge]) -> None:
    await _write(connection, g, "node_edge", [_edge_row(edge) for edge in edges], upsert=True)


async def _put_embedding(
    connection: Connection, g: sql.Identifier, embedding: EmbeddingRecord
) -> None:
    await _write(connection, g, "embedding", [_embedding_row(embedding)], upsert=True)


# A stored timeline's version. A record written before the field existed has no
# `version` key and reads as 0, which is what a caller holding it carries.
_TIMELINE_VERSION = "coalesce((data ->> 'version')::bigint, 0)"


async def _put_timeline(connection: Connection, g: sql.Identifier, timeline: Timeline) -> None:
    """Store `timeline` on the version check (`next_timeline_version`), in a transaction.

    The comparison is in the `UPDATE`'s `WHERE`, which is what makes it hold
    across servers: under read committed, an `UPDATE` that waited on another
    transaction's lock re-reads the row once that transaction commits and
    re-tests the `WHERE` against it, so a writer that read the version the
    other one replaced matches nothing. An `UPDATE` that matched nothing is
    either a timeline never stored or a stale copy. Only the first may insert,
    and only at version 0; `ON CONFLICT DO NOTHING` waits out a concurrent
    insert of the same id and then leaves it alone. Anything still unwritten
    raises, inside the caller's transaction, so everything else that
    transaction wrote rolls back with it.
    """
    expected = timeline.version
    row = _timeline_row(timeline.model_copy(update={"version": expected + 1}))
    updated = await connection.execute(
        _q(
            f"UPDATE {{g}}.timeline SET data = %s WHERE uid = %s AND {_TIMELINE_VERSION} = %s",
            g,
        ),
        (row["data"], row["uid"], expected),
    )
    if updated.rowcount == 1:
        return
    if expected == 0:
        inserted = await connection.execute(
            _q(
                "INSERT INTO {g}.timeline (uid, data) VALUES (%s, %s) ON CONFLICT (uid) DO NOTHING",
                g,
            ),
            (row["uid"], row["data"]),
        )
        if inserted.rowcount == 1:
            return
    found = await _fetch_one(
        connection,
        _q(f"SELECT {_TIMELINE_VERSION} FROM {{g}}.timeline WHERE uid = %s", g),
        (row["uid"],),
    )
    raise TimelineConflict(timeline.id, expected, None if found is None else found[0])


async def _delete_edges(connection: Connection, g: sql.Identifier, edge_ids: Iterable[str]) -> None:
    ids = list(edge_ids)
    if ids:
        await _execute(connection, _q(f"DELETE FROM {{g}}.node_edge WHERE uid {_ANY}", g), (ids,))


# --- Graph state ---


async def _state(connection: Connection, g: sql.Identifier, key: str) -> Any:
    """A setting's stored value, or None when the graph has never set it."""
    row = await _fetch_one(
        connection, _q("SELECT value FROM {g}.graph_state WHERE key = %s", g), (key,)
    )
    return None if row is None else row[0]


async def _set_state(connection: Connection, g: sql.Identifier, key: str, value: Any) -> None:
    """Store a setting, or remove its row for None, which reads back as the default.

    Removing rather than storing `null` keeps one representation of *follow the
    process default*: a threshold cleared and a threshold never set are the same
    row, which is no row.
    """
    if value is None:
        await _execute(connection, _q("DELETE FROM {g}.graph_state WHERE key = %s", g), (key,))
        return
    await _execute(
        connection,
        _q(
            "INSERT INTO {g}.graph_state (key, value) VALUES (%s, %s) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            g,
        ),
        (key, Jsonb(value)),
    )


async def _bump(connection: Connection, g: sql.Identifier, key: str) -> int:
    """Add one to a counter and return the new count, in one statement.

    An `UPDATE ... RETURNING` folded into the insert that creates the row on
    first use. One statement rather than a read and a write, so two processes
    bumping the same graph cannot both read 4 and both write 5: the second
    waits on the first's row lock and adds to what it wrote.
    """
    row = await _fetch_one(
        connection,
        _q(
            "INSERT INTO {g}.graph_state AS state (key, value) VALUES (%s, '1') "
            "ON CONFLICT (key) DO UPDATE SET value = to_jsonb(state.value::bigint + 1) "
            "RETURNING value",
            g,
        ),
        (key,),
    )
    assert row is not None
    return int(row[0])


async def _reset(connection: Connection, g: sql.Identifier, key: str) -> int:
    """Zero a counter and return what it held. Run inside a transaction.

    The row is locked as it is read, so a bump from another process lands
    either before the reset, and is counted in what it returns, or after it.
    A counter with no row already reads as zero and is left without one.
    """
    row = await _fetch_one(
        connection,
        _q("SELECT value FROM {g}.graph_state WHERE key = %s FOR UPDATE", g),
        (key,),
    )
    if row is None:
        return 0
    await _set_state(connection, g, key, 0)
    return int(row[0] or 0)


async def _complete_round(connection: Connection, g: sql.Identifier) -> ReflectCompletion:
    """Zero the reflect counter and advance the round if it was above zero.

    Run inside one transaction. `_reset` locks the counter's row as it reads
    it, so a second completion waits and then reads the zero this one wrote,
    and a store landing meanwhile is counted either here or in the next round.
    The round moves by `_bump`'s single upsert, and an error anywhere rolls
    the reset back with it.
    """
    cleared = await _reset(connection, g, _REFLECT_COUNTER)
    if cleared > 0:
        reflect_round = await _bump(connection, g, _REFLECT_ROUND)
    else:
        reflect_round = int(await _state(connection, g, _REFLECT_ROUND) or 0)
    return ReflectCompletion(stores_cleared=cleared, reflect_round=reflect_round)


# --- Opening a graph ---


async def _marked_schemas(connection: Connection, among: Sequence[str] | None = None) -> list[str]:
    """The schemas holding the marker table, all of them or those in `among`.

    Read from the catalog, so asking creates nothing.
    """
    query = (
        "SELECT n.nspname FROM pg_catalog.pg_namespace AS n "
        "JOIN pg_catalog.pg_class AS c ON c.relnamespace = n.oid "
        "WHERE c.relname = %s AND c.relkind = 'r'"
    )
    if among is None:
        return [schema for (schema,) in await _fetch(connection, sql.SQL(query), (_MARKER,))]
    rows = await _fetch(
        connection, sql.SQL(query + " AND n.nspname = ANY(%s::text[])"), (_MARKER, list(among))
    )
    return [schema for (schema,) in rows]


async def _stored_name(connection: Connection, schema: str) -> str | None:
    """The graph name a schema's marker records, or None if it has gone."""
    try:
        row = await _fetch_one(
            connection,
            _q(f"SELECT name FROM {{g}}.{_MARKER} LIMIT 1", sql.Identifier(schema)),
        )
    except errors.UndefinedTable, errors.InvalidSchemaName:
        return None
    return None if row is None else row[0]


async def _holds(connection: Connection, schema: str, name: str) -> bool:
    """Whether a marked schema is the graph `name`.

    A plain schema is the graph it is named after. A derived one is checked
    against its marker, so a name whose hash collided with another graph's is
    never handed that graph.
    """
    return schema == name or await _stored_name(connection, schema) == name


async def _graphs_among(connection: Connection, names: Sequence[str]) -> set[str]:
    """Which of `names` are graphs here. Each must be a legal graph name."""
    by_schema = {_schema_for(name): name for name in names}
    return {
        by_schema[schema]
        for schema in await _marked_schemas(connection, list(by_schema))
        if await _holds(connection, schema, by_schema[schema])
    }


async def _graph_presence(connection: Connection, name: str) -> tuple[bool, bool]:
    """Whether the graph `name` exists, and whether its schema holds anything.

    A schema can hold something without being this graph: another
    application's tables, or a derived name that collided. Opening refuses
    both rather than adding this module's tables to them.
    """
    schema = _schema_for(name)
    row = await _fetch_one(
        connection,
        sql.SQL(
            "SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_class AS c "
            "JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace "
            "WHERE n.nspname = %s)"
        ),
        (schema,),
    )
    occupied = row is not None and bool(row[0])
    is_graph = bool(await _marked_schemas(connection, [schema])) and await _holds(
        connection, schema, name
    )
    return is_graph, occupied


async def _migrate_schema(connection: Connection, g: sql.Identifier, name: str) -> None:
    """Bring an existing graph up to `_SCHEMA_VERSION`, a step at a time.

    Runs inside the transaction that opens the graph, so a step that fails
    leaves the graph at the version it came in at, and the marker is stamped
    once at the end. No steps yet: this is where the first one goes, as
    `if version < 2: ...` above the stamp, in the shape the SurrealDB adapter's
    `_migrate_schema` uses. None of that adapter's migrations is ported; they
    rewrite graphs that only ever existed in SurrealDB, and a graph moves here
    through a bundle, which arrives at the current version.

    A graph stamped above `_SCHEMA_VERSION` was written by a newer Epimemer and
    raises `SchemaTooNew` before anything is written, which rolls the opening
    transaction back with the graph exactly as it was.
    """
    row = await _fetch_one(connection, _q(f"SELECT max(version) FROM {{g}}.{_MARKER}", g))
    version = 0 if row is None or row[0] is None else int(row[0])
    if version > _SCHEMA_VERSION:
        raise SchemaTooNew(name, version, _SCHEMA_VERSION)
    if version == _SCHEMA_VERSION:
        return

    await _execute(
        connection, _q(f"UPDATE {{g}}.{_MARKER} SET version = %s", g), (_SCHEMA_VERSION,)
    )


async def _open_graph(connection: Connection, name: str) -> None:
    """Create the graph's schema and tables if absent, or migrate them if present.

    Run inside a transaction, which holds an advisory lock on the graph's name,
    so two processes opening a new graph at once create it once and a failed
    step leaves nothing half made. A schema that exists and holds tables without
    the marker belongs to something else, and is refused rather than filled
    with this module's tables.
    """
    schema = _schema_for(name)
    g = sql.Identifier(schema)
    await _execute(connection, sql.SQL("SELECT pg_advisory_xact_lock(%s)"), (_lock_key(name),))
    is_graph, occupied = await _graph_presence(connection, name)
    if is_graph:
        await _migrate_schema(connection, g, name)
        return
    if occupied:
        raise ValueError(
            f"Postgres schema '{schema}' already holds tables that are not the Epimemer "
            f"graph '{name}', so it is not opened as one. Choose another graph name."
        )
    # One round trip for the whole schema. `prepare=False`: a prepared
    # statement holds one command, and this is many.
    await connection.execute(_q(";\n".join(_SCHEMA), g), prepare=False)
    await _execute(
        connection,
        _q(f"INSERT INTO {{g}}.{_MARKER} (name, version) VALUES (%s, %s)", g),
        (name, _SCHEMA_VERSION),
    )


async def _open(dsn: str) -> Connection:
    return await psycopg.AsyncConnection.connect(dsn, autocommit=True)


# --- Edge migration, shared by supersession and merging ---


async def _migrate_edges(
    connection: Connection,
    g: sql.Identifier,
    old_ids: set[str],
    new_id: str,
    *,
    status: NodeStatus,
) -> None:
    """Carry edges touching `old_ids` onto `new_id`, per `migration_disposition`.

    The SQLite backend's `_migrate_edges`, step for step, over the same edges in
    the same order. `status` is why the old node is retiring and decides each
    edge's fate: a correction or a merge re-points everything but history,
    review and judgments; a world-change re-points nothing and copies only the
    metacontext and the tags.

    Self-loops are dropped and duplicate `(src, dst, type)` edges collapse to
    one, which keeps the periods of every edge folded into it. Survivors are
    written at the end, since a later duplicate can still widen one's validity.
    """
    ids = sorted(old_ids)
    incident = await _edges(connection, g, f"WHERE src_id {_ANY} OR dst_id {_ANY}", (ids, ids))
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
    await _delete_edges(connection, g, dropped)
    await _put_edges(connection, g, survivors.values())


async def _retire(
    connection: Connection,
    g: sql.Identifier,
    node_id: str,
    *,
    status: NodeStatus,
    at: datetime,
    counterpart: str | None,
    judge: JudgeRef | None,
) -> None:
    node = await _stored_node(connection, g, node_id)
    lifecycle = with_retirement(
        node.lifecycle, at=at, because=status, counterpart=counterpart, judge=judge
    )
    await _put_node(
        connection,
        g,
        node.model_copy(update={"status": status, "superseded_at": at, "lifecycle": lifecycle}),
    )


async def _return(
    connection: Connection, g: sql.Identifier, node_id: str, *, at: datetime, judge: JudgeRef | None
) -> None:
    node = await _stored_node(connection, g, node_id)
    await _put_node(
        connection,
        g,
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
    means. `since` is inclusive and `until` exclusive, and both are instants
    compared with `timestamptz`, never text.
    """
    clauses: list[str] = []
    params: list[Any] = []
    if agent_ids is not None:
        clauses.append(f"agent_id {_ANY}")
        params.append(_values(agent_ids))
    if kinds is not None:
        clauses.append(f"kind {_ANY}")
        params.append(_values(kinds))
    if subject_id is not None:
        clauses.append("subject_ids @> %s")
        params.append(Jsonb([subject_id]))
    if subject_ids is not None:
        # Any of them: `?|` asks whether any of the strings is an element of the
        # array, and an empty array of strings is never one.
        clauses.append("subject_ids ?| %s::text[]")
        params.append(_values(subject_ids))
    if reviews is not None:
        clauses.append("reviews = %s")
        params.append(reviews)
    if since is not None:
        clauses.append("decided_at >= %s")
        params.append(_aware(since))
    if until is not None:
        clauses.append("decided_at < %s")
        params.append(_aware(until))
    return (f"WHERE {' AND '.join(clauses)}" if clauses else ""), params


# Newest first, ties broken by id: a batch written in one call shares a
# timestamp to the microsecond, and an order the clock does not settle has to
# be settled the same way on every backend.
_JOURNAL_ORDER = "ORDER BY decided_at DESC, uid DESC"


async def _decisions(
    connection: Connection,
    g: sql.Identifier,
    where: str,
    params: Sequence[Any],
    limit: int | None = None,
) -> list[DecisionRecord]:
    tail = "" if limit is None else " LIMIT %s"
    return await _models(
        DecisionRecord,
        connection,
        _q(f"SELECT data FROM {{g}}.decision {where} {_JOURNAL_ORDER}{tail}", g),
        [*params, *([] if limit is None else [int(limit)])],
    )


# Does this node name any of `%(ids)s` as a judge? The SQL half of
# `node_judge_ids`, and it has to enumerate the same four places: a field added
# there and missed here would let `epimemer agents delete` remove a judge some
# node still points at. A lifecycle that is not an array has no episodes, and
# is read as an empty one rather than refused.
_NODE_NAMES_JUDGE = (
    "data -> 'judged_by' ->> 'agent_id' = ANY(%(ids)s::text[]) "
    "OR data -> 'value' -> 'importance_judged_by' ->> 'agent_id' = ANY(%(ids)s::text[]) "
    "OR EXISTS (SELECT 1 FROM jsonb_array_elements(CASE jsonb_typeof(data -> 'lifecycle') "
    "WHEN 'array' THEN data -> 'lifecycle' ELSE '[]'::jsonb END) AS episode "
    "WHERE episode -> 'retired_by' ->> 'agent_id' = ANY(%(ids)s::text[]) "
    "OR episode -> 'restored_by' ->> 'agent_id' = ANY(%(ids)s::text[]))"
)

_RECORD_NAMES_JUDGE = "data -> 'judged_by' ->> 'agent_id' = ANY(%(ids)s::text[])"


def _cosine_scores(matrix: np.ndarray, query: np.ndarray) -> np.ndarray:
    """Cosine similarity of every row against `query`, in one matrix product.

    A zero vector on either side scores 0.0 rather than dividing by zero, which
    is the in-memory backend's rule.
    """
    norms = np.linalg.norm(matrix, axis=1) * np.linalg.norm(query)
    dots = matrix @ query
    return np.divide(dots, norms, out=np.zeros_like(dots), where=norms != 0)


class PostgresStorage:
    """Postgres implementation of StorageBackend: one schema per graph in the
    database `dsn` names."""

    def __init__(self, dsn: str, database: str = "default"):
        self._dsn = dsn
        # Parsed now, so a malformed DSN is refused at startup rather than at the
        # first call, and so `location` never has to touch the DSN again.
        self._address = _address(dsn)
        # The graph's name, and the schema it lives in, which is the same name
        # unless Postgres could not hold it (`_schema_for`).
        self._schema = _schema_for(database)
        self._database = database
        self._connection: Connection | None = None
        self._lock = asyncio.Lock()
        self._guard = make_graph_guard()

    # --- Lifecycle ---

    async def connect(self) -> None:
        async with self._lock:
            await self._drop_connection()
            connection = await _open(self._dsn)
            try:
                async with connection.transaction():
                    await _open_graph(connection, self._database)
            except BaseException:
                await connection.close()
                raise
            self._connection = connection

    async def close(self) -> None:
        async with self._lock:
            await self._drop_connection()

    async def _drop_connection(self) -> None:
        connection, self._connection = self._connection, None
        if connection is not None:
            await connection.close()

    # --- Surviving a dropped connection ---
    #
    # A server outlives the databases it talks to: a Postgres restart, a
    # failover or a network blip closes the socket under a connection that
    # otherwise looks the same. psycopg notices at the next statement, raises
    # `OperationalError` and marks the connection closed.
    #
    # Outside a transaction the unit of work is run once more on a fresh
    # connection. That is safe because of what the autocommit writes here are:
    # upserts and deletes by id, which repeat to the same state, and insert-only
    # appends, which refuse a repeat rather than doubling it. The one write that
    # can count twice is a counter bump whose reply was lost after it committed,
    # and a "stores since the last reflect" count one too high is a smaller harm
    # than a failed store.
    #
    # Inside a transaction the error propagates: the transaction went with the
    # socket, and Postgres rolled it back. The next call finds the connection
    # closed and opens a new one before it starts.
    #
    # The rebuild runs inside `_lock` and never through `_run` or `_atomic`,
    # so a failure while rebuilding is reported to the caller rather than
    # waiting on a lock its own task holds.

    async def _live(self) -> Connection:
        """The open connection, rebuilt first if it was lost since the last call."""
        if self._connection is None:
            raise RuntimeError("Not connected. Call connect() first.")
        if self._connection.closed:
            return await self._reopen()
        return self._connection

    async def _reopen(self) -> Connection:
        """Replace a closed connection. Only called with `_lock` held.

        The stale connection is kept until a new one is open, so a database that
        is still down leaves the next call to try again rather than reporting
        that nothing was ever connected.
        """
        stale = self._connection
        if stale is not None:
            with contextlib.suppress(Exception):
                await stale.close()
        self._connection = await _open(self._dsn)
        return self._connection

    async def _run[T](self, work: Work[T]) -> T:
        """`work` against the active graph, outside any transaction, retried once
        on a fresh connection if the connection was lost."""
        async with self._lock:
            connection = await self._live()
            graph = sql.Identifier(self._schema)
            try:
                return await work(connection, graph)
            except psycopg.OperationalError:
                # Some operational errors leave the connection usable (a
                # cancelled statement, a lock timeout); only a lost one is
                # worth a second attempt.
                if not connection.closed:
                    raise
            return await work(await self._reopen(), graph)

    async def _atomic[T](self, work: Work[T]) -> T:
        """`work` against the active graph inside one transaction, never retried."""
        async with self._lock:
            connection = await self._live()
            async with connection.transaction():
                return await work(connection, sql.Identifier(self._schema))

    async def _read_graph[T](self, database: str, read: Work[T], absent: T) -> T:
        """`read` against another graph's schema, or `absent` if there is no such graph.

        A name that cannot be a graph here is never turned into an identifier,
        and naming a graph does not create it. A graph deleted between the check
        and the read answers as absent too.
        """
        if not _is_graph_name(database):
            return absent

        async def work(connection: Connection, _active: sql.Identifier) -> T:
            if database not in await _graphs_among(connection, [database]):
                return absent
            try:
                return await read(connection, sql.Identifier(_schema_for(database)))
            except errors.UndefinedTable, errors.InvalidSchemaName:
                return absent

        return await self._run(work)

    # --- Documents ---

    async def store_document(self, doc: RawDocument) -> str:
        row = _document_row(doc)
        await self._run(lambda c, g: _write(c, g, "document", [row], upsert=True))
        return doc.id

    async def get_document(self, doc_id: str) -> RawDocument | None:
        found = await self._run(
            lambda c, g: _models(
                RawDocument, c, _q("SELECT data FROM {g}.document WHERE uid = %s", g), (doc_id,)
            )
        )
        return found[0] if found else None

    async def get_document_by_source(self, source: str) -> RawDocument | None:
        # Two spellings rather than `IS NOT DISTINCT FROM`, which no index serves.
        where, params = ("source IS NULL", ()) if source is None else ("source = %s", (source,))
        found = await self._run(
            lambda c, g: _models(
                RawDocument,
                c,
                _q(f"SELECT data FROM {{g}}.document WHERE {where} ORDER BY seq LIMIT 1", g),
                params,
            )
        )
        return found[0] if found else None

    async def query_documents(self) -> Sequence[RawDocument]:
        return await self._run(
            lambda c, g: _models(
                RawDocument, c, _q("SELECT data FROM {g}.document ORDER BY seq", g)
            )
        )

    # --- Segments ---

    async def store_segment(self, segment: Segment) -> str:
        row = _segment_row(segment)
        await self._run(lambda c, g: _write(c, g, "segment", [row], upsert=True))
        return segment.id

    async def get_segments_for_document(self, doc_id: str) -> Sequence[Segment]:
        return await self._run(
            lambda c, g: _models(
                Segment,
                c,
                _q("SELECT data FROM {g}.segment WHERE document_id = %s ORDER BY seq", g),
                (doc_id,),
            )
        )

    async def get_segments(self, segment_ids: Sequence[str]) -> dict[str, Segment]:
        wanted = list(dict.fromkeys(segment_ids))
        if not wanted:
            return {}
        found = await self._run(
            lambda c, g: _models(
                Segment, c, _q(f"SELECT data FROM {{g}}.segment WHERE uid {_ANY}", g), (wanted,)
            )
        )
        return {segment.id: segment for segment in found}

    async def query_segments(self) -> Sequence[Segment]:
        return await self._run(
            lambda c, g: _models(Segment, c, _q("SELECT data FROM {g}.segment ORDER BY seq", g))
        )

    # --- Epistemic Nodes ---

    async def store_node(self, node: EpistemicNode) -> str:
        await self._run(lambda c, g: _put_node(c, g, node))
        return node.id

    async def get_node(self, node_id: str) -> EpistemicNode | None:
        return await self._run(lambda c, g: _node(c, g, node_id))

    async def get_nodes(self, node_ids: Sequence[str]) -> dict[str, EpistemicNode]:
        wanted = list(dict.fromkeys(node_ids))
        if not wanted:
            return {}
        found = await self._run(lambda c, g: _nodes(c, g, f"WHERE uid {_ANY}", (wanted,)))
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
            clauses.append("node_type = %s")
            params.append(_value(node_type))
        if at_time is None:
            clauses.append("status = %s")
            params.append(_value(status))
        else:
            # Active at that instant, whatever the status is now: born by then,
            # and not yet retired.
            at = _aware(at_time)
            clauses.append("created_at <= %s AND (superseded_at IS NULL OR superseded_at > %s)")
            params += [at, at]
        where = f"WHERE {' AND '.join(clauses)}"
        return await self._run(lambda c, g: _nodes(c, g, where, params))

    async def get_node_by_content(
        self,
        content: str,
        *,
        node_type: NodeType | None = None,
        status: NodeStatus = NodeStatus.ACTIVE,
    ) -> EpistemicNode | None:
        where = "WHERE content = %s AND status = %s"
        params: list[Any] = [content, _value(status)]
        if node_type is not None:
            where += " AND node_type = %s"
            params.append(_value(node_type))
        found = await self._run(lambda c, g: _nodes(c, g, where, params, "LIMIT 1"))
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
        episodes live inside `data`, and any node that has episodes at all comes
        back from the query and is decided here, on real datetimes, by the same
        predicate the in-memory backend applies.
        """
        lower, upper = _aware(start), _aware(end)
        where = (
            "WHERE ((created_at >= %s AND created_at < %s) "
            "OR (superseded_at >= %s AND superseded_at < %s) "
            "OR data -> 'lifecycle' <> '[]'::jsonb)"
        )
        params: list[Any] = [lower, upper, lower, upper]
        if node_type is not None:
            where += " AND node_type = %s"
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

        found = await self._run(lambda c, g: _nodes(c, g, where, params))
        return [node for node in found if changed(node)]

    async def get_relation_kind(self, label: str) -> str | None:
        row = await self._run(
            lambda c, g: _fetch_one(
                c,
                _q(
                    "SELECT data ->> 'kind' FROM {g}.node_edge "
                    "WHERE type = %s AND data ->> 'label' = %s ORDER BY seq LIMIT 1",
                    g,
                ),
                (EdgeType.RELATED.value, label),
            )
        )
        return None if row is None else row[0]

    async def count_nodes_by_type(
        self,
        *,
        status: NodeStatus = NodeStatus.ACTIVE,
    ) -> dict[NodeType, int]:
        counts = {node_type: 0 for node_type in NodeType}
        rows = await self._run(
            lambda c, g: _fetch(
                c,
                _q(
                    "SELECT node_type, count(*) FROM {g}.node WHERE status = %s GROUP BY node_type",
                    g,
                ),
                (_value(status),),
            )
        )
        for node_type, count in rows:
            counts[NodeType(node_type)] = int(count)
        return counts

    async def count_nodes_without_metacontext(
        self,
        *,
        status: NodeStatus = NodeStatus.ACTIVE,
    ) -> int:
        row = await self._run(
            lambda c, g: _fetch_one(
                c,
                _q(
                    "SELECT count(*) FROM {g}.node WHERE status = %s AND NOT EXISTS "
                    "(SELECT 1 FROM {g}.node_edge AS edge "
                    "WHERE edge.src_id = node.uid AND edge.type = %s)",
                    g,
                ),
                (_value(status), EdgeType.HAS_METACONTEXT.value),
            )
        )
        assert row is not None
        return int(row[0])

    # --- Edges ---

    async def store_edge(self, edge: NodeEdge) -> str:
        await self._run(lambda c, g: _put_edges(c, g, [edge]))
        return edge.id

    async def delete_edge(self, edge_id: str) -> None:
        await self._run(lambda c, g: _delete_edges(c, g, [edge_id]))

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
        where = f"WHERE {endpoint} {_ANY}"
        params: list[Any] = [wanted]
        if edge_type is not None:
            where += " AND type = %s"
            params.append(_value(edge_type))
        for edge in await self._run(lambda c, g: _edges(c, g, where, params)):
            found[getattr(edge, endpoint)].append(edge)
        return found

    async def count_edges_by_type(self) -> dict[EdgeType, int]:
        counts = {edge_type: 0 for edge_type in EdgeType}
        rows = await self._run(
            lambda c, g: _fetch(c, _q("SELECT type, count(*) FROM {g}.node_edge GROUP BY type", g))
        )
        for edge_type, count in rows:
            counts[EdgeType(edge_type)] = int(count)
        return counts

    async def query_edges(self) -> Sequence[NodeEdge]:
        return await self._run(lambda c, g: _edges(c, g))

    # --- Atomic compound operations ---
    #
    # Each runs inside one transaction and awaits nothing inside it but its own
    # statements, mirroring the in-memory backend step for step. Every change to
    # a node's lifecycle starts from the row as stored, locked, never from the
    # argument.

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
        async def work(connection: Connection, g: sql.Identifier) -> None:
            await _retire(
                connection,
                g,
                old_node.id,
                status=status,
                at=superseded_at,
                counterpart=new_node.id,
                judge=judge,
            )
            await _put_node(connection, g, new_node)
            await _put_embedding(connection, g, new_embedding)
            await _migrate_edges(connection, g, {old_node.id}, new_node.id, status=status)
            await _put_edges(connection, g, [lineage_edge, *evidence_edges])
            await _delete_edges(connection, g, clear_edge_ids)

        await self._atomic(work)

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
        async def work(connection: Connection, g: sql.Identifier) -> None:
            await _retire(
                connection,
                g,
                old_node.id,
                status=status,
                at=superseded_at,
                counterpart=existing_id,
                judge=judge,
            )
            # No new node, no embedding, no migration: the existing node stands.
            await _put_edges(connection, g, [lineage_edge, *evidence_edges])
            await _delete_edges(connection, g, clear_edge_ids)

        await self._atomic(work)

    async def set_node_status_tx(
        self,
        nodes: Sequence[EpistemicNode],
        *,
        status: NodeStatus,
        at: datetime,
        edges: Sequence[NodeEdge] = (),
        judge: JudgeRef | None = None,
    ) -> None:
        async def work(connection: Connection, g: sql.Identifier) -> None:
            for node in nodes:
                if status is NodeStatus.ACTIVE:
                    await _return(connection, g, node.id, at=at, judge=judge)
                else:
                    await _retire(
                        connection, g, node.id, status=status, at=at, counterpart=None, judge=judge
                    )
            # In the same transaction, so a reactivated node is never left
            # ACTIVE without the edge saying what asserted it again.
            await _put_edges(connection, g, edges)

        await self._atomic(work)

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
        async def work(connection: Connection, g: sql.Identifier) -> None:
            await _put_node(connection, g, merged_node)
            await _put_embedding(connection, g, merged_embedding)
            # Migrate before writing lineage edges, so they are not re-pointed.
            await _migrate_edges(
                connection,
                g,
                {source.id for source in source_nodes},
                merged_node.id,
                status=NodeStatus.MERGED,
            )
            for source in source_nodes:
                await _retire(
                    connection,
                    g,
                    source.id,
                    status=NodeStatus.MERGED,
                    at=merged_at,
                    counterpart=merged_node.id,
                    judge=judge,
                )
            # Evidence flags after migration, so each stays on the source whose
            # wording went away rather than being re-pointed at the survivor.
            await _put_edges(connection, g, [*lineage_edges, *evidence_edges])

        await self._atomic(work)

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

        async def work(connection: Connection, g: sql.Identifier) -> None:
            # Deletions first: a restored edge may reuse a `(src, dst, type)` the
            # merge had moved onto the survivor.
            await _delete_edges(connection, g, delete_edge_ids)
            await _put_edges(connection, g, restored_edges)
            for source in source_nodes:
                await _return(connection, g, source.id, at=restored_at, judge=judge)
            # A survivor still carrying an edge means the reversal guard let
            # something through, and deleting the node would take it along.
            row = await _fetch_one(
                connection,
                _q("SELECT count(*) FROM {g}.node_edge WHERE src_id = %s OR dst_id = %s", g),
                (survivor.id, survivor.id),
            )
            remaining = 0 if row is None else int(row[0])
            if remaining:
                raise ValueError(
                    f"node {survivor.id} still has {remaining} edges and must not be "
                    f"deleted: a hard delete would take them with it."
                )
            await _execute(connection, _q("DELETE FROM {g}.node WHERE uid = %s", g), (survivor.id,))
            # The vector is stored per item, so deleting the node alone would
            # strand an entry the search still returns.
            await _execute(
                connection, _q("DELETE FROM {g}.embedding WHERE item_id = %s", g), (survivor.id,)
            )

        await self._atomic(work)

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
        node_rows = [_node_row(node) for node in nodes]
        edge_rows = [_edge_row(edge) for edge in edges]
        embedding_rows = [_embedding_row(embedding) for embedding in embeddings]

        async def work(connection: Connection, g: sql.Identifier) -> None:
            await _write(connection, g, "node", node_rows, upsert=False)
            await _write(connection, g, "node_edge", edge_rows, upsert=False)
            for timeline in timelines:
                await _put_timeline(connection, g, timeline)
            await _write(connection, g, "embedding", embedding_rows, upsert=False)

        await self._atomic(work)

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

        async def work(connection: Connection, g: sql.Identifier) -> None:
            await _put_timeline(connection, g, timeline)
            await _put_edges(connection, g, [*new_edges, *retired_edges])

        await self._atomic(work)

    async def reopen_tx(
        self,
        decision: DecisionRecord,
        *,
        retired_edges: Sequence[NodeEdge] = (),
        relation_verdicts: Sequence[RelationVerdict] = (),
    ) -> None:
        """One withdrawn suppression and the journal row that records it."""

        async def work(connection: Connection, g: sql.Identifier) -> None:
            await _put_edges(connection, g, retired_edges)
            await _write(
                connection,
                g,
                "relation_verdict",
                [_relation_verdict_row(v) for v in relation_verdicts],
                upsert=False,
            )
            await _write(connection, g, "decision", [_decision_row(decision)], upsert=True)

        await self._atomic(work)

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
        timestamp columns hold the instants given; `data` keeps each record
        exactly as it arrived. Relation labels go in directly rather than
        through `store_relation_label`, which merges, and merging is what this
        method exists not to do.
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

        async def work(connection: Connection, g: sql.Identifier) -> None:
            for table, rows in sections:
                await _write(connection, g, table, rows, upsert=False)

        await self._atomic(work)

    async def resegment_tx(
        self,
        document_id: str,
        segments: Sequence[Segment],
        *,
        segmentation: Mapping[str, Any],
    ) -> int:
        """A document's passages replaced, if nothing cites them. See the protocol.

        Two locks make the count and the delete one unit against other servers
        on this graph. The document row, `FOR UPDATE`, puts two re-cuts of one
        document one after the other. The node table, in `SHARE` mode, waits
        for every transaction already writing nodes to finish and holds off any
        that would start, so the count sees every committed claim and none can
        land before the delete; readers are not held up. Each statement after
        the locks reads the state as committed by then, which is what Postgres
        gives a transaction at its default isolation. A refusal raises inside
        the transaction, which rolls back having written nothing.
        """
        require_own_passages(document_id, segments)
        rows = [_segment_row(segment) for segment in segments]

        async def work(connection: Connection, g: sql.Identifier) -> int:
            found = await _models(
                RawDocument,
                connection,
                _q("SELECT data FROM {g}.document WHERE uid = %s FOR UPDATE", g),
                (document_id,),
            )
            if not found:
                raise KeyError(f"Document {document_id} not found")
            await _execute(connection, _q("LOCK TABLE {g}.node IN SHARE MODE", g))
            row = await _fetch_one(
                connection,
                _q(
                    "SELECT count(*) FROM {g}.node WHERE data ->> 'source_id' IN "
                    "(SELECT uid FROM {g}.segment WHERE document_id = %s)",
                    g,
                ),
                (document_id,),
            )
            citing = 0 if row is None else int(row[0])
            if citing:
                raise PassagesCited(document_id=document_id, citing=citing)
            cursor = await connection.execute(
                _q("DELETE FROM {g}.segment WHERE document_id = %s", g), (document_id,)
            )
            replaced = cursor.rowcount
            await _write(connection, g, "segment", rows, upsert=False)
            document = found[0]
            metadata = {**document.metadata, "segmentation": dict(segmentation)}
            await _write(
                connection,
                g,
                "document",
                [_document_row(document.model_copy(update={"metadata": metadata}))],
                upsert=True,
            )
            return replaced

        return await self._atomic(work)

    # --- Embeddings ---

    async def store_embedding(self, embedding: EmbeddingRecord) -> str:
        await self._run(lambda c, g: _put_embedding(c, g, embedding))
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
        for embedding in await self._run(lambda c, g: _embeddings(c, g, where, params)):
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
        where = f"WHERE embedding.model_id = %s AND node.status {_ANY}"
        params: list[Any] = [model_id, _values(statuses)]
        if node_type is not None:
            where += " AND node.node_type = %s"
            params.append(_value(node_type))
        rows = await self._run(
            lambda c, g: _fetch(
                c,
                _q(
                    "SELECT embedding.item_id, embedding.vector FROM {g}.embedding AS embedding "
                    f"JOIN {{g}}.node AS node ON node.uid = embedding.item_id {where} "
                    "ORDER BY embedding.seq",
                    g,
                ),
                params,
            )
        )
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
        the partition whatever its status. Ties break on id. Postgres full-text
        search is not used: it would be a third analyzer with its own rules for
        `JIRA-4417`, and the set parity the backends promise on rare terms rests
        on one.
        """
        if not terms:
            return []

        if corpus == "segments":
            rows = await self._run(
                lambda c, g: _fetch(
                    c, _q("SELECT uid, data ->> 'text' FROM {g}.segment ORDER BY seq", g)
                )
            )
            documents = dict(rows)
            eligible = set(documents)
        else:
            if node_type is None:
                raise ValueError(
                    "text_search(corpus='nodes') requires a node_type: BM25 "
                    "statistics are per node table, so a merged multi-type list "
                    "would sort incomparable scores."
                )
            rows = await self._run(
                lambda c, g: _fetch(
                    c,
                    _q(
                        "SELECT uid, content, status FROM {g}.node WHERE node_type = %s "
                        "ORDER BY seq",
                        g,
                    ),
                    (_value(node_type),),
                )
            )
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
        found = await self._run(
            lambda c, g: _nodes(c, g, f"WHERE data ->> 'source_id' {_ANY}", (wanted,))
        )
        for node in found:
            by_source[node.source_id].append(node)
        return by_source

    # --- Timelines ---

    async def store_timeline(self, timeline: Timeline) -> str:
        await self._atomic(lambda c, g: _put_timeline(c, g, timeline))
        return timeline.id

    async def get_timeline(self, timeline_id: str) -> Timeline | None:
        found = await self._run(
            lambda c, g: _models(
                Timeline, c, _q("SELECT data FROM {g}.timeline WHERE uid = %s", g), (timeline_id,)
            )
        )
        return found[0] if found else None

    async def query_timelines(self) -> Sequence[Timeline]:
        return await self._run(
            lambda c, g: _models(Timeline, c, _q("SELECT data FROM {g}.timeline ORDER BY seq", g))
        )

    # --- Metacontexts ---

    async def store_metacontext(self, mc: Metacontext) -> str:
        row = _metacontext_row(mc)
        await self._run(lambda c, g: _write(c, g, "metacontext", [row], upsert=True))
        return mc.id

    async def get_metacontext(self, mc_id: str) -> Metacontext | None:
        found = await self._run(
            lambda c, g: _models(
                Metacontext, c, _q("SELECT data FROM {g}.metacontext WHERE uid = %s", g), (mc_id,)
            )
        )
        return found[0] if found else None

    async def query_metacontexts(
        self,
        *,
        status: NodeStatus = NodeStatus.ACTIVE,
    ) -> Sequence[Metacontext]:
        return await self._run(
            lambda c, g: _models(
                Metacontext,
                c,
                _q("SELECT data FROM {g}.metacontext WHERE status = %s ORDER BY seq", g),
                (_value(status),),
            )
        )

    # --- Relation labels ---

    async def store_relation_label(self, label: RelationLabel) -> str:
        """Upsert by `(name, kind)`: the record already there keeps its id.

        A read, a merge and a write, in one transaction that first locks the
        table against other writers, so another process cannot record the same
        label between the read and the write. Readers are not held up.
        """

        async def work(connection: Connection, g: sql.Identifier) -> str:
            await _execute(
                connection, _q("LOCK TABLE {g}.relation_label IN SHARE ROW EXCLUSIVE MODE", g)
            )
            existing = await _models(
                RelationLabel,
                connection,
                _q("SELECT data FROM {g}.relation_label WHERE name = %s AND kind = %s", g),
                (label.name, label.kind),
            )
            stored = recorded_relation_label(existing[0] if existing else None, label)
            await _write(
                connection, g, "relation_label", [_relation_label_row(stored)], upsert=True
            )
            return stored.id

        return await self._atomic(work)

    async def get_relation_label(self, name: str, kind: str) -> RelationLabel | None:
        found = await self._run(
            lambda c, g: _models(
                RelationLabel,
                c,
                _q("SELECT data FROM {g}.relation_label WHERE name = %s AND kind = %s", g),
                (name, kind),
            )
        )
        return found[0] if found else None

    async def query_relation_labels(self) -> Sequence[RelationLabel]:
        return await self._run(
            lambda c, g: _models(
                RelationLabel, c, _q("SELECT data FROM {g}.relation_label ORDER BY seq", g)
            )
        )

    # --- Relation verdicts ---

    async def record_relation_verdict(self, verdict: RelationVerdict) -> str:
        """Append one verdict. A plain insert: the table is append-only, so an id
        that is already there is a retry the caller should have refused."""
        row = _relation_verdict_row(verdict)
        await self._run(lambda c, g: _write(c, g, "relation_verdict", [row], upsert=False))
        return verdict.id

    async def judged_relation_pairs(self) -> set[tuple[str, str]]:
        return standing_relation_pairs(await self.query_relation_verdicts())

    async def relation_verdicts_for(self, label_ids: Sequence[str]) -> Sequence[RelationVerdict]:
        """Verdicts on exactly this set of labels, newest first.

        Newest by `decided_at` compared as datetimes; a verdict appended later
        wins a tie, as it does in the in-memory backend.
        """
        wanted = set(label_ids)
        label_a, label_b = _label_pair(wanted)
        found = await self._run(
            lambda c, g: _models(
                RelationVerdict,
                c,
                _q(
                    "SELECT data FROM {g}.relation_verdict "
                    "WHERE label_a IS NOT DISTINCT FROM %s::text "
                    "AND label_b IS NOT DISTINCT FROM %s::text ORDER BY seq DESC",
                    g,
                ),
                (label_a, label_b),
            )
        )
        matching = [verdict for verdict in found if set(verdict.label_ids) == wanted]
        return sorted(matching, key=lambda verdict: verdict.decided_at, reverse=True)

    async def query_relation_verdicts(self) -> Sequence[RelationVerdict]:
        return await self._run(
            lambda c, g: _models(
                RelationVerdict, c, _q("SELECT data FROM {g}.relation_verdict ORDER BY seq", g)
            )
        )

    # --- Reflection bookkeeping ---

    async def _get_state(self, key: str) -> Any:
        return await self._run(lambda c, g: _state(c, g, key))

    async def _put_state(self, key: str, value: Any) -> None:
        await self._run(lambda c, g: _set_state(c, g, key, value))

    async def get_reflect_counter(self) -> int:
        return int(await self._get_state(_REFLECT_COUNTER) or 0)

    async def bump_reflect_counter(self) -> int:
        return await self._run(lambda c, g: _bump(c, g, _REFLECT_COUNTER))

    async def set_reflect_counter(self, count: int) -> None:
        await self._put_state(_REFLECT_COUNTER, int(count))

    async def get_reflect_threshold_override(self) -> int | None:
        return await self._get_state(_REFLECT_THRESHOLD)

    async def set_reflect_threshold_override(self, threshold: int | None) -> None:
        await self._put_state(_REFLECT_THRESHOLD, threshold)

    async def get_reflect_round(self) -> int:
        return int(await self._get_state(_REFLECT_ROUND) or 0)

    async def set_reflect_round(self, round: int) -> None:
        await self._put_state(_REFLECT_ROUND, int(round))

    async def complete_reflect_round(self) -> ReflectCompletion:
        return await self._atomic(_complete_round)

    async def _reset(self, key: str) -> int:
        return await self._atomic(lambda c, g: _reset(c, g, key))

    # --- Backup bookkeeping ---

    async def get_backup_counter(self) -> int:
        return int(await self._get_state(_BACKUP_COUNTER) or 0)

    async def bump_backup_counter(self) -> int:
        return await self._run(lambda c, g: _bump(c, g, _BACKUP_COUNTER))

    async def reset_backup_counter(self) -> int:
        return await self._reset(_BACKUP_COUNTER)

    async def set_backup_counter(self, count: int) -> None:
        await self._put_state(_BACKUP_COUNTER, int(count))

    async def get_backup_threshold_override(self) -> int | None:
        return await self._get_state(_BACKUP_THRESHOLD)

    async def set_backup_threshold_override(self, threshold: int | None) -> None:
        await self._put_state(_BACKUP_THRESHOLD, threshold)

    async def get_merge_overrides(self) -> MergeOverrides:
        stored = await self._get_state(_MERGE_OVERRIDES)
        return MergeOverrides() if stored is None else MergeOverrides.model_validate(stored)

    async def set_merge_overrides(self, overrides: MergeOverrides) -> None:
        await self._put_state(_MERGE_OVERRIDES, overrides.model_dump(mode="json"))

    async def get_deferral_overrides(self) -> DeferralOverrides:
        stored = await self._get_state(_DEFERRAL_OVERRIDES)
        return DeferralOverrides() if stored is None else DeferralOverrides.model_validate(stored)

    async def set_deferral_overrides(self, overrides: DeferralOverrides) -> None:
        await self._put_state(_DEFERRAL_OVERRIDES, overrides.model_dump(mode="json"))

    async def get_warning_overrides(self) -> WarningOverrides:
        stored = await self._get_state(_WARNING_OVERRIDES)
        return WarningOverrides() if stored is None else WarningOverrides.model_validate(stored)

    async def set_warning_overrides(self, overrides: WarningOverrides) -> None:
        await self._put_state(_WARNING_OVERRIDES, overrides.model_dump(mode="json"))

    # --- Agents ---

    async def get_agent(self, agent_id: str) -> Agent | None:
        found = await self._run(
            lambda c, g: _models(
                Agent, c, _q("SELECT data FROM {g}.agent WHERE uid = %s", g), (agent_id,)
            )
        )
        return found[0] if found else None

    async def upsert_agent(self, agent: Agent) -> None:
        row = _agent_row(agent)
        await self._run(lambda c, g: _write(c, g, "agent", [row], upsert=True))

    async def list_agents(self) -> list[Agent]:
        return await self._run(
            lambda c, g: _models(Agent, c, _q("SELECT data FROM {g}.agent ORDER BY seq", g))
        )

    async def delete_agent(self, agent_id: str) -> None:
        await self._run(
            lambda c, g: _execute(c, _q("DELETE FROM {g}.agent WHERE uid = %s", g), (agent_id,))
        )

    async def judge_usage(self, agent_ids: Sequence[str]) -> JudgeUsage:
        """Counted in one statement of aggregates, never by materialising rows."""
        keys = list(dict.fromkeys(agent_ids))
        if not keys:
            return JudgeUsage()
        row = await self._run(
            lambda c, g: _fetch_one(
                c,
                _q(
                    "SELECT "
                    "(SELECT count(*) FROM {g}.decision WHERE agent_id = ANY(%(ids)s::text[])), "
                    f"(SELECT count(*) FROM {{g}}.node WHERE {_NODE_NAMES_JUDGE}), "
                    f"(SELECT count(*) FROM {{g}}.node_edge WHERE {_RECORD_NAMES_JUDGE}), "
                    f"(SELECT count(*) FROM {{g}}.relation_label WHERE {_RECORD_NAMES_JUDGE}) "
                    f"+ (SELECT count(*) FROM {{g}}.relation_verdict WHERE {_RECORD_NAMES_JUDGE})",
                    g,
                ),
                {"ids": keys},
            )
        )
        assert row is not None
        decisions, nodes, edges, relations = (int(count) for count in row)
        return JudgeUsage(decisions=decisions, nodes=nodes, edges=edges, relations=relations)

    async def get_approved_agent_ids(self) -> list[str]:
        return list(await self._get_state(_APPROVED_AGENT_IDS) or [])

    async def set_approved_agent_ids(self, ids: list[str]) -> None:
        await self._put_state(_APPROVED_AGENT_IDS, list(ids))

    async def get_judge_confirmations(self) -> list[JudgeConfirmation]:
        """Newest last, as the protocol says; a stable sort leaves a list that
        already is exactly as it was written."""
        stored = await self._get_state(_JUDGE_CONFIRMATIONS) or []
        confirmations = [JudgeConfirmation.model_validate(entry) for entry in stored]
        return sorted(confirmations, key=lambda confirmation: confirmation.confirmed_at)

    async def set_judge_confirmations(self, confirmations: list[JudgeConfirmation]) -> None:
        await self._put_state(
            _JUDGE_CONFIRMATIONS,
            [confirmation.model_dump(mode="json") for confirmation in confirmations],
        )

    async def get_require_judge(self) -> bool | None:
        stored = await self._get_state(_REQUIRE_JUDGE)
        return None if stored is None else bool(stored)

    async def set_require_judge(self, required: bool | None) -> None:
        await self._put_state(_REQUIRE_JUDGE, required)

    # --- The decision journal ---

    async def record_decision(self, record: DecisionRecord) -> str:
        row = _decision_row(record)
        await self._run(lambda c, g: _write(c, g, "decision", [row], upsert=True))
        return record.id

    async def get_decision(self, decision_id: str) -> DecisionRecord | None:
        found = await self._run(
            lambda c, g: _models(
                DecisionRecord,
                c,
                _q("SELECT data FROM {g}.decision WHERE uid = %s", g),
                (decision_id,),
            )
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
        return await self._run(lambda c, g: _decisions(c, g, where, params, limit))

    async def reviewed_decision_ids(self, decision_ids: Sequence[str]) -> set[str]:
        ids = list(dict.fromkeys(decision_ids))
        if not ids:
            return set()
        rows = await self._run(
            lambda c, g: _fetch(
                c,
                _q(f"SELECT DISTINCT reviews FROM {{g}}.decision WHERE reviews {_ANY}", g),
                (ids,),
            )
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
        """Counts per named graph, each read from that graph's own schema.

        Nothing is borrowed, so no mover turn is taken: another schema is read
        on the same connection without going near the active graph. A graph
        with no schema is left out of the answer rather than counted zero, and
        asking about one does not create it.
        """
        where, params = _decision_where(agent_ids=agent_ids, since=since, until=until)
        names = [name for name in dict.fromkeys(databases) if _is_graph_name(name)]
        if not names:
            return {}

        async def work(connection: Connection, _active: sql.Identifier) -> dict[str, int]:
            graphs = await _graphs_among(connection, names)
            counts: dict[str, int] = {}
            for name in names:
                if name not in graphs:
                    continue
                try:
                    row = await _fetch_one(
                        connection,
                        _q(
                            f"SELECT count(*) FROM {{g}}.decision {where}",
                            sql.Identifier(_schema_for(name)),
                        ),
                        params,
                    )
                except errors.UndefinedTable, errors.InvalidSchemaName:
                    # Deleted between listing and counting: omitted, not zero.
                    continue
                counts[name] = 0 if row is None else int(row[0])
            return counts

        return await self._run(work)

    # --- Multi-graph management ---

    @property
    def backend_name(self) -> str:
        return "postgres"

    @property
    def location(self) -> str:
        return self._address

    @property
    def current_database(self) -> str:
        return self._database

    @property
    def graph_guard(self) -> GraphGuard:
        return self._guard

    async def list_databases(self) -> list[str]:
        """The graphs in the database: the schemas holding the marker table, sorted.

        Other schemas in the database are not graphs and are not offered. A
        plain schema is named after its graph; a derived one, which carries a
        character no graph name has, is the graph its marker names.
        """

        async def work(connection: Connection, _g: sql.Identifier) -> list[str]:
            names = []
            for schema in await _marked_schemas(connection):
                if not schema.startswith(_DERIVED):
                    name: str | None = schema
                else:
                    name = await _stored_name(connection, schema)
                if _is_graph_name(name) and _schema_for(name) == schema:
                    names.append(name)
            return sorted(names)

        return await self._run(work)

    async def switch_database(self, database: str) -> None:
        """Open the graph's schema, creating and initialising it if absent.

        Takes the guard's mover turn, so a call in flight finishes against the
        graph it started on. The schema is set up before the attribute moves, so
        a graph that cannot be opened leaves the active one in place.
        """
        schema = _schema_for(database)
        async with self._guard.moving():
            await self._atomic(lambda c, _g: _open_graph(c, database))
            self._schema, self._database = schema, database

    async def delete_database(self, database: str) -> None:
        """Drop a graph's schema and everything in it.

        Refuses the active graph, as every backend does, and refuses a name with
        no graph behind it, which includes a schema that exists but is not one:
        this never drops a schema it did not make.
        """
        schema = _schema_for(database)
        if database == self._database:
            raise ValueError(f"Cannot delete the currently active graph '{database}'")

        async def work(connection: Connection, _g: sql.Identifier) -> None:
            await _execute(
                connection, sql.SQL("SELECT pg_advisory_xact_lock(%s)"), (_lock_key(database),)
            )
            is_graph, _ = await _graph_presence(connection, database)
            if not is_graph:
                raise KeyError(f"Graph '{database}' does not exist")
            await _execute(
                connection, sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
            )

        await self._atomic(work)

    # --- Viz reads (cross-graph, no switching) ---
    #
    # Each reads the named graph's schema on the same connection. No `moving()`
    # turn, for the in-memory backend's reason: nothing about the active graph is
    # borrowed, so there is nothing to give back. A graph that does not exist
    # answers with an empty list.

    async def viz_list_nodes(
        self,
        database: str,
        *,
        historical_status: NodeStatus = NodeStatus.ACTIVE,
    ) -> Sequence[EpistemicNode]:
        return await self._read_graph(
            database,
            lambda c, g: _nodes(c, g, "WHERE status = %s", (_value(historical_status),)),
            [],
        )

    async def viz_list_edges(
        self,
        database: str,
    ) -> Sequence[NodeEdge]:
        return await self._read_graph(database, lambda c, g: _edges(c, g), [])

    async def viz_list_timelines(
        self,
        database: str,
    ) -> Sequence[Timeline]:
        return await self._read_graph(
            database,
            lambda c, g: _models(Timeline, c, _q("SELECT data FROM {g}.timeline ORDER BY seq", g)),
            [],
        )

    async def viz_list_metacontexts(
        self,
        database: str,
    ) -> Sequence[Metacontext]:
        return await self._read_graph(
            database,
            lambda c, g: _models(
                Metacontext,
                c,
                _q("SELECT data FROM {g}.metacontext WHERE status = %s ORDER BY seq", g),
                (NodeStatus.ACTIVE.value,),
            ),
            [],
        )

    async def viz_list_relation_labels(
        self,
        database: str,
    ) -> Sequence[RelationLabel]:
        return await self._read_graph(
            database,
            lambda c, g: _models(
                RelationLabel, c, _q("SELECT data FROM {g}.relation_label ORDER BY seq", g)
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
        return await self._read_graph(database, lambda c, g: _decisions(c, g, where, params), [])

    async def viz_list_embeddings(
        self,
        database: str,
        *,
        item_ids: Sequence[str] | None = None,
        model_id: str | None = None,
    ) -> Sequence[EmbeddingRecord]:
        where, params = _embedding_where(item_ids=item_ids, model_id=model_id)
        return await self._read_graph(database, lambda c, g: _embeddings(c, g, where, params), [])
