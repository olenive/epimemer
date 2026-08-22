"""SurrealDB storage backend.

Implements the StorageBackend protocol using SurrealDB.
Supports ws://, http://, and mem:// (embedded) connections.

Note: We use 'uid' as our application-level ID field to avoid conflicts
with SurrealDB's built-in 'id' field (which uses RecordID type).
"""

import asyncio
import contextlib
from datetime import datetime
from typing import Any, Awaitable, Callable, Literal, Sequence

from surrealdb import AsyncSurreal
from websockets.exceptions import ConnectionClosed, WebSocketException

from epimemer.core.temporal import merged_validity
from epimemer.core.types import (
    EdgeType,
    EmbeddingRecord,
    EpistemicNode,
    Fact,
    Inference,
    LifecycleEpisode,
    Metacontext,
    NodeEdge,
    NodeStatus,
    NodeType,
    RawDocument,
    Segment,
    Timeline,
    Topic,
    migration_disposition,
    moved_edge_types,
    with_retirement,
    with_return,
)
from epimemer.storage.bm25 import containment_first
from epimemer.storage.protocol import (
    EdgeDirection,
    MergeOverrides,
    drop_none_values,
    validate_graph_name,
)


# Schemes the SDK maps to `AsyncEmbeddedSurrealConnection`, which is not a
# connection to anything: the engine, and therefore the whole graph, lives inside
# the object. Rebuilding one hands back an empty database dressed up as a
# recovery, so the reconnect path must never touch them. (Such a connection also
# has no socket to lose, so this guard is belt-and-braces — but the failure it
# prevents is silent data loss.)
_EMBEDDED_SCHEMES = ("mem://", "memory", "file://", "surrealkv://")


def _is_embedded(url: str) -> bool:
    return url.startswith(_EMBEDDED_SCHEMES)


_NODE_TYPE_TO_TABLE = {
    NodeType.TOPIC: "topic",
    NodeType.FACT: "fact",
    NodeType.INFERENCE: "inference",
}

_TABLE_TO_NODE_CLASS: dict[str, type] = {
    "topic": Topic,
    "fact": Fact,
    "inference": Inference,
}


def _node_to_table(node: EpistemicNode) -> str:
    match node:
        case Topic():
            return "topic"
        case Fact():
            return "fact"
        case Inference():
            return "inference"


def _record_to_node(table: str, data: dict) -> EpistemicNode:
    cls = _TABLE_TO_NODE_CLASS[table]
    # Map 'uid' back to 'id' for our Pydantic model
    cleaned = _clean_record(data)
    return cls.model_validate(cleaned)


def _node_tables(node_type: NodeType | None) -> str:
    """The node table(s) a search covers, as a SurrealQL `FROM` target."""
    if node_type is None:
        return "topic, fact, inference"
    return _NODE_TYPE_TO_TABLE[node_type]


# How far past `k` to reach when ranking before filtering by status. Tried in
# order: each attempt is one cheap unfiltered top-N plus a membership check on
# those few ids, so escalating is affordable, and it only happens on graphs
# where the best matches are mostly retired.
_OVERFETCH_FACTORS = (3, 10)


async def _ranked_items(
    query, query_vector: list[float], model_id: str, limit: int
) -> list[tuple[str, float]]:
    """Top `limit` embeddings by cosine similarity, with no status filter.

    Cheap and linear: one pass over the embeddings for a model. Everything
    expensive about this query historically came from the filter, not the scan.
    """
    rows = await query(
        """
        SELECT
            item_id,
            vector::similarity::cosine(vector, $query_vector) AS score
        FROM embedding
        WHERE model_id = $model_id
        ORDER BY score DESC
        LIMIT $limit
        """,
        {"query_vector": query_vector, "model_id": model_id, "limit": limit},
    )
    return [(r["item_id"], r["score"]) for r in rows]


async def _ids_with_status(
    query,
    node_type: NodeType | None,
    statuses: frozenset[NodeStatus],
    *,
    among: list[str] | None = None,
) -> list[str]:
    """Uids of nodes in any of `statuses` — all of them, or only those in `among`.

    Restricting to `among` is the cheap direction: a handful of candidate ids
    checked against the unique index on `uid`. Passing `among=None` reads every
    matching id, which is only worth doing to feed the exact query below.

    The status list is a bound parameter rather than an interpolated literal:
    these values come from an enum today, and a filter built by string-joining
    caller-shaped values is how the next one stops being safe.
    """
    tables = _node_tables(node_type)
    if not statuses:
        return []
    wanted = sorted(status.value for status in statuses)
    if among is None:
        return list(
            await query(
                f"SELECT VALUE uid FROM {tables} WHERE status IN $statuses",
                {"statuses": wanted},
            )
        )
    if not among:
        return []
    return list(
        await query(
            f"SELECT VALUE uid FROM {tables} "
            f"WHERE status IN $statuses AND uid IN $ids",
            {"statuses": wanted, "ids": among},
        )
    )


async def _ranked_items_with_status(
    query,
    query_vector: list[float],
    model_id: str,
    k: int,
    node_type: NodeType | None,
    statuses: frozenset[NodeStatus],
) -> list[tuple[str, float]]:
    """Exact top-k over the retrievable nodes only: rank what survives the filter.

    Two round-trips rather than one query with a subquery. Expressing the filter
    as `item_id IN (SELECT ...)` reads well but makes SurrealDB re-run that
    subquery *per embedding row*, so the cost becomes embeddings × nodes — the
    quadratic term that made search the first operation in this system to fail
    its tool timeout. Fetching the ids first and binding them as a parameter
    turns the per-row work into an array membership test.

    Still linear in (rows × retrievable nodes) in-engine, so this is the
    fallback rather than the usual path — correct at any ratio of retired nodes,
    and bounded, but it grows.

    (It cannot be written as one `LET $wanted = (...); SELECT ...` call: this
    driver returns the *first* statement's result, so the select's rows would be
    thrown away and `LET`'s `None` returned in their place.)
    """
    wanted = await _ids_with_status(query, node_type, statuses)
    if not wanted:
        return []
    rows = await query(
        """
        SELECT
            item_id,
            vector::similarity::cosine(vector, $query_vector) AS score
        FROM embedding
        WHERE model_id = $model_id AND item_id IN $wanted
        ORDER BY score DESC
        LIMIT $k
        """,
        {
            "query_vector": query_vector,
            "model_id": model_id,
            "k": k,
            "wanted": wanted,
        },
    )
    return [(r["item_id"], r["score"]) for r in rows]


# The exact-content lookup, named so a test can assert its *plan* rather than
# only its answer. `WITH INDEX` is the whole point: without it SurrealDB
# resolves this through `idx_{table}_status`, which matches every active row,
# and applies `content` as a predicate afterwards — a scan of the live table on
# every ingest (#48). Defining a `content` index does not fix that on its own;
# the planner keeps choosing the status index, and so does a composite
# `(content, status)`. Only naming the index moves `content` into the access
# path and leaves `status` as the cheap post-filter.
CONTENT_LOOKUP = (
    "SELECT * FROM {table} WITH INDEX idx_{table}_content "
    "WHERE content = $content AND status = $status LIMIT 1"
)


# `IN` does not use an index in this SurrealDB. Verified with EXPLAIN on
# `src_id IN $ids` against `idx_edge_src`, with and without an explicit
# `WITH INDEX` hint: both plan a full scan, and the list is then evaluated per
# row. A batched fetch therefore costs O(rows x ids), which is why chunking is
# nearly worthless on the edge table — 3,000 nodes over 9,000 edges took 692 ms
# at 200 ids per chunk and 511 ms as a single query.
#
# Above this many ids it is cheaper to read the candidate rows and match them
# here. Measured on node_edge: the crossover sat at 100-200 ids at 400, 1,200
# and 3,000 nodes alike — stable because both sides of the trade grow linearly
# in table size — and at 3,000 nodes the whole-type read was 34 ms against
# 511 ms for the `IN`. Edges only: repeated on heavier rows, the crossover was
# past 400 ids for nodes and past 1,000 for embeddings, a vector being the most
# expensive row in the store to fetch for nobody.
_EDGE_IN_PREDICATE_MAX = 100

# Ids per `IN` list for the fetches that stay on `IN` at every size. The ids
# travel as a bind parameter, so this is not a statement-length limit — it
# bounds the per-row work of the predicate and the result set held at once.
# Small enough that the default suite can afford to cross the seam, because a
# chunk boundary no test reaches is where a batched fetch silently drops rows.
_NODE_FETCH_CHUNK = 250
_EMBEDDING_FETCH_CHUNK = 250


# --- Full-text search ---
#
# The analyzer is the one both backends are written against: `class`
# tokenization cuts a token wherever the character class changes, so `JIRA-4417`
# becomes `jira`, `-`, `4417` and the rare piece is separable from the common
# prefix. That is the whole mechanism — measured, `search::analyze` returns
# exactly that (`dev-docs/LEXICAL_SEARCH.md` §2.3).

_ANALYZER_DDL = (
    "DEFINE ANALYZER IF NOT EXISTS epimemer_text "
    "TOKENIZERS class FILTERS lowercase, ascii, snowball(english);"
)

# The two backends this adapter connects to are two different SurrealDBs, and
# they do not agree on how a full-text index is declared. The standalone 3.0.5
# server renamed the keyword and dropped the parameter list; the engine embedded
# in the Python SDK — which is what `mem://` is, and therefore what most of the
# test suite runs against — is an older core that only parses the 2.x form.
# Each *rejects the other's syntax outright*, so there is no spelling that
# satisfies both and no version to write against.
#
# Everything else about full-text search is identical on the two: the analyzer
# DDL, the match operator, `search::score`, and the positive scores themselves
# (measured to seven decimal places). Only this one statement forks.
_FTS_INDEX_MODERN = (
    "DEFINE INDEX IF NOT EXISTS idx_{table}_fts ON {table} "
    "FIELDS {field} FULLTEXT ANALYZER epimemer_text BM25;"
)
_FTS_INDEX_LEGACY = (
    "DEFINE INDEX IF NOT EXISTS idx_{table}_fts ON {table} "
    "FIELDS {field} SEARCH ANALYZER epimemer_text BM25(1.2,0.75);"
)

# Which field of which table is searchable. Nodes answer "what do I believe?"
# and segments answer "where did I read that?", and a rare identifier is almost
# always the second question: `store_decomposition` is agent-driven, so a
# paraphrased fact may never contain the id the source text did (§1.1).
_FTS_TARGETS = (
    ("topic", "content"),
    ("fact", "content"),
    ("inference", "content"),
    ("segment", "text"),
)

# How far past `k` the inner ranking reaches before the status gate is applied.
# The gate cannot go inside: adding any non-match predicate to a `WHERE` that
# ORs two match references makes the engine stop using the full-text index, and
# the `@@` operator then matches a document that contains *any* token of a term
# rather than all of them — so `JIRA-4417` starts returning `JIRA-4418`, at a
# positive score that the zero-rule truncation does not catch. Measured on
# 3.0.5; the subquery keeps the inner `WHERE` pure, which keeps the index.
#
# The reach only bites when more than this many *positively-scored* rows are all
# retired, because the inner ranking is by score and the zero-scored bulk sinks
# below it. Cheaper than the alternative for the same reason `vector_search`
# over-fetches rather than filtering first.
_TEXT_SEARCH_OVERFETCH = 10

# How far it reaches for a *declared* term instead, whose containment check
# reads the text of what was fetched (R8). The reasoning above inverts here: the
# hit containment exists to rescue is the one whose score is at or below the IDF
# floor, which in a ranking by score is the bottom — precisely what the reach
# cuts off. So the declared path fetches an order of magnitude wider.
#
# It cannot fetch *everything*, and the alternative — a containment predicate in
# the `WHERE`, letting the engine find the rows directly — is the one thing R8
# forbids, for the reason directly above. Residual, stated rather than hidden: a
# term whose every token is common across more than this many documents can
# still have its literal match fall outside the window. A declared term is an
# identifier or a name, which is the case where the candidate set is small
# enough that the reach never binds at all.
_CONTAINMENT_OVERFETCH = 100


async def _define_fts_indexes(query) -> None:
    """Define the full-text indexes in whichever dialect this engine speaks.

    The first target pays for the negotiation; the rest reuse what worked. If
    both dialects fail the error is raised with the other one chained to it, so
    a third SurrealDB that speaks neither is diagnosable rather than mysterious.

    Defining the index **backfills existing rows** — verified on both engines —
    so an existing graph needs no migration step. It also means the first
    connect after this ships is slower than every connect before it, once, in a
    place with no progress reporting. Measured in `dev-docs/BENCHMARKS.md`.
    """
    template = _FTS_INDEX_MODERN
    for table, field in _FTS_TARGETS:
        try:
            await query(template.format(table=table, field=field))
        except Exception as unsupported:
            if template is _FTS_INDEX_LEGACY:
                raise
            template = _FTS_INDEX_LEGACY
            try:
                await query(template.format(table=table, field=field))
            except Exception as also_unsupported:
                raise also_unsupported from unsupported


def _upsert(table: str, *, data: str = "data", uid: str = "uid") -> str:
    """SurrealQL to upsert a row keyed on the application id (``uid``).

    `INSERT INTO` is *silently ignored* when the UNIQUE index on `uid` is
    violated — no error, no update — which makes every re-store a no-op.
    `UPSERT ... WHERE` inserts when nothing matches and replaces the content
    when a row does, keeping the generated record id stable so records written
    before this changed keep working.

    `data`/`uid` name the bind parameters, so a batch that upserts several rows
    of one table in a single transaction can give each statement its own.
    """
    return f"UPSERT {table} CONTENT ${data} WHERE uid = ${uid}"


# --- Reflection bookkeeping ---
#
# One fixed record per graph holds the "stores since the last reflect" count, so
# the read-modify-write is a single atomic statement on the server rather than a
# racy round trip. `?? 0` covers the create path, where the field does not exist
# yet. Both mutations return the row so the caller needs no follow-up read:
# BUMP returns the incremented value, RESET the value it cleared.

_REFLECT_RECORD = "graph_state:reflect"
_REFLECT_FIELD = "stores_since_reflect"

_REFLECT_GET = f"SELECT {_REFLECT_FIELD} FROM {_REFLECT_RECORD};"
_REFLECT_BUMP = (
    f"UPSERT {_REFLECT_RECORD} SET {_REFLECT_FIELD} = "
    f"({_REFLECT_FIELD} ?? 0) + 1 RETURN AFTER;"
)
_REFLECT_RESET = f"UPSERT {_REFLECT_RECORD} SET {_REFLECT_FIELD} = 0 RETURN BEFORE;"

# The per-graph threshold override shares the record with the counter — same
# scope, same lifetime — but never the same field. Clearing writes NONE, which
# removes the key rather than storing a zero that would read back as a
# threshold of 0.
_THRESHOLD_FIELD = "reflect_threshold_override"

_THRESHOLD_GET = f"SELECT {_THRESHOLD_FIELD} FROM {_REFLECT_RECORD};"
_THRESHOLD_SET = (
    f"UPSERT {_REFLECT_RECORD} SET {_THRESHOLD_FIELD} = $threshold RETURN AFTER;"
)


def _reflect_count(rows) -> int:
    """Read the counter out of a reflect-state row set.

    A graph that has never stored anything yields no row (SELECT) or a null one
    (RETURN BEFORE on the create path); both mean zero.
    """
    if not rows or rows[0] is None:
        return 0
    return int(rows[0].get(_REFLECT_FIELD) or 0)


def _threshold_override(rows) -> int | None:
    """Read the threshold override out of a reflect-state row set.

    No row, a null row, or a row without the field all mean "no override" —
    distinct from a stored value, which is why this cannot reuse
    `_reflect_count`'s `or 0` collapse.
    """
    if not rows or rows[0] is None:
        return None
    value = rows[0].get(_THRESHOLD_FIELD)
    return None if value is None else int(value)


# The per-graph merge overrides share the reflect record, on the same grounds as
# the threshold: one graph-state row, same scope, same lifetime, never the same
# field. Written whole rather than per field so that clearing one override and
# leaving the other alone is a single unambiguous write. A `None` field encodes
# as NONE and so removes the key, which is what "follow the process default"
# has to mean — a stored 0 would read back as a limit of zero.
_MERGE_FIELD = "merge_overrides"

_MERGE_GET = f"SELECT {_MERGE_FIELD} FROM {_REFLECT_RECORD};"
_MERGE_SET = (
    f"UPSERT {_REFLECT_RECORD} SET {_MERGE_FIELD} = $overrides RETURN AFTER;"
)


def _merge_overrides(rows) -> MergeOverrides:
    """Read the merge overrides out of a reflect-state row set.

    No row, a null row, or a row without the field all mean "nothing
    overridden", which is an all-`None` record rather than an absence: every
    field independently answers *follow the default*.
    """
    if not rows or rows[0] is None:
        return MergeOverrides()
    stored = rows[0].get(_MERGE_FIELD)
    return MergeOverrides() if not stored else MergeOverrides.model_validate(stored)


def _serialize(model) -> dict:
    """Serialize a Pydantic model to a dict suitable for SurrealDB.

    Renames 'id' to 'uid' to avoid conflicting with SurrealDB's built-in id.

    Dropping None-valued keys is what SurrealDB would do anyway — the driver
    encodes every `None` as `NONE`, which stores no key — so this changes
    nothing here. It is applied explicitly so the returned dict matches what
    actually lands in the row, and so the shared contract is visible at the
    boundary rather than being an accident of the driver's encoding.
    """
    data = drop_none_values(model.model_dump(mode="json"))
    data["uid"] = data.pop("id")
    return data


def _edge_row(edge: NodeEdge) -> dict:
    """Serialize an edge for SurrealDB (uid-renamed, enum type as its value)."""
    row = _serialize(edge)
    row["type"] = edge.type.value
    return row


# Timestamps are uniform UTC ISO-8601 strings, so these string comparisons are
# chronologically correct — the same property `query_changes` already relies on
# for `created_at` and `superseded_at`. The `?? []` is load-bearing: on a row
# written before episodes existed the field is absent, and `array::len(NONE)` is
# an error rather than zero.
_EPISODE_IN_WINDOW = (
    "array::len((lifecycle ?? [])"
    "[WHERE retired_at >= $start AND retired_at < $end]) > 0 "
    "OR array::len((lifecycle ?? [])"
    "[WHERE restored_at != NONE AND restored_at >= $start "
    "AND restored_at < $end]) > 0"
)


def _episode_rows(episodes: Sequence[LifecycleEpisode]) -> list[dict]:
    """A lifecycle history as SurrealDB stores it (ISO strings, no null keys).

    The whole list is written each time rather than appended to in the database.
    Both engines this backend runs against would take an `array::append`, but
    only one of them has the object functions that closing an episode needs, and
    a history that is assembled two different ways is a history that can differ
    two different ways. Planning it here also matches how `merge_nodes_tx`
    already plans its edge migration, for the same reason: the adapter is
    single-connection and documented as unsafe for concurrent callers, so
    nothing interleaves between the read and the write.
    """
    return [drop_none_values(ep.model_dump(mode="json")) for ep in episodes]


def _clean_record(record: dict) -> dict:
    """Clean a SurrealDB record for Pydantic deserialization.

    Maps 'uid' back to 'id' and removes SurrealDB's RecordID 'id' field.
    """
    cleaned = {k: v for k, v in record.items() if k != "id"}
    if "uid" in cleaned:
        cleaned["id"] = cleaned.pop("uid")
    return cleaned


class SurrealDBStorage:
    """SurrealDB implementation of StorageBackend."""

    def __init__(
        self,
        url: str = "mem://",
        user: str = "root",
        password: str = "root",
        namespace: str = "epimemer",
        database: str = "main",
    ):
        self._url = url
        self._user = user
        self._password = password
        self._namespace = namespace
        self._database = database
        self._db: AsyncSurreal | None = None
        # The database actually selected on the wire. Equal to `_database` except
        # inside a viz read, which points the connection at another graph and
        # restores it in a `finally` — a reconnect in that window has to come
        # back pointed where the caller believes it is.
        self._selected = database
        self._reconnect_lock = asyncio.Lock()

    async def connect(self) -> None:
        self._db = AsyncSurreal(self._url)
        await self._db.connect(self._url)
        if not _is_embedded(self._url):
            await self._db.signin({"username": self._user, "password": self._password})
        await self._db.use(self._namespace, self._selected)
        await self._setup_schema()

    # --- Surviving a dropped connection ---
    #
    # The SDK does not reconnect, and worse, does not admit to being
    # disconnected: `AsyncWsSurrealConnection.connect()` returns early whenever
    # `self.socket` is truthy, and nothing clears `socket` when the connection
    # drops — the receive task swallows the `ConnectionClosed` and exits. So a
    # server restart, a network blip or a laptop sleep leaves an object that
    # looks connected while every send raises, for the life of the process.
    #
    # Retrying the operation is safe *here*, which is a property of this schema
    # rather than a general truth: every write is an `UPSERT ... WHERE uid` or an
    # `INSERT INTO` under a UNIQUE `uid` index (silently ignored on collision),
    # and multi-statement writes are transactional, so a connection lost in
    # flight aborts them server-side. A retry is a no-op or an idempotent repeat.
    #
    # Not covered: the operation already in flight when the socket died. The SDK
    # cancels its pending futures, so that caller sees `CancelledError`, and
    # telling that apart from a real cancellation is not worth the ambiguity.
    # That one call fails; the next one reconnects.

    async def _reconnect(self, stale: AsyncSurreal) -> None:
        """Rebuild a connection whose socket has gone.

        Serialized, because everything in flight fails together and each caller
        lands here: whoever takes the lock first rebuilds, and the rest find
        `_db` already replaced and go straight to their retry.
        """
        async with self._reconnect_lock:
            if self._db is not stale:
                return
            self._db = None
            with contextlib.suppress(Exception):
                await stale.close()
            await self.connect()

    async def _call(self, operation: Callable[[AsyncSurreal], Awaitable[Any]]) -> Any:
        conn = self.db
        try:
            return await operation(conn)
        except (ConnectionClosed, WebSocketException):
            if _is_embedded(self._url):
                raise
            await self._reconnect(conn)
            return await operation(self.db)

    async def _query(self, query: str, params: dict | None = None) -> Any:
        return await self._call(lambda conn: conn.query(query, params))

    async def _query_raw(self, query: str, params: dict | None = None) -> Any:
        return await self._call(lambda conn: conn.query_raw(query, params))

    async def _use(self, database: str) -> None:
        await self._call(lambda conn: conn.use(self._namespace, database))
        self._selected = database

    @property
    def backend_name(self) -> str:
        return "surrealdb"

    @property
    def current_database(self) -> str:
        return self._database

    @property
    def namespace(self) -> str:
        return self._namespace

    async def list_databases(self) -> list[str]:
        """List all databases in the current namespace."""
        result = await self._query("INFO FOR NS;")
        # SurrealDB returns a dict with "databases" key
        databases = result.get("databases", {}) if isinstance(result, dict) else {}
        return sorted(databases.keys())

    async def switch_database(self, database: str) -> None:
        """Switch to a different database and set up its schema."""
        validate_graph_name(database)
        await self._use(database)
        self._database = database
        await self._setup_schema()

    async def delete_database(self, database: str) -> None:
        """Delete a database from the current namespace.

        The name is validated rather than parameterized: SurrealQL takes the
        database name as an identifier here, not a value, so it cannot be bound.
        `validate_graph_name` restricts it to characters that carry no meaning
        inside the backticks.
        """
        validate_graph_name(database)
        await self._query(f"REMOVE DATABASE IF EXISTS `{database}`;")

    # --- Viz reads (cross-graph, no switching of active state) ---

    async def viz_list_nodes(
        self,
        database: str,
        *,
        historical_status: NodeStatus = NodeStatus.ACTIVE,
    ) -> Sequence[EpistemicNode]:
        """List all nodes in a graph for visualization snapshot.

        Temporarily switches the SurrealDB connection to the target database,
        queries, then switches back. Not safe for concurrent MCP calls — the
        viz server should serialize snapshot reads or use a separate connection
        for production deployments.
        """
        original_db = self._selected
        try:
            await self._use(database)
            results = []
            for table in ("topic", "fact", "inference"):
                rows = await self._query(
                    f"SELECT * FROM {table} WHERE status = $status",
                    {"status": historical_status.value},
                )
                results.extend(_record_to_node(table, r) for r in rows)
            return results
        finally:
            await self._use(original_db)

    async def viz_list_edges(
        self,
        database: str,
    ) -> Sequence[NodeEdge]:
        """List all edges in a graph for visualization snapshot."""
        original_db = self._selected
        try:
            await self._use(database)
            rows = await self._query("SELECT * FROM node_edge")
            return [NodeEdge.model_validate(_clean_record(r)) for r in rows]
        finally:
            await self._use(original_db)

    async def viz_list_timelines(
        self,
        database: str,
    ) -> Sequence[Timeline]:
        """List all timelines in a graph for visualization snapshot.

        Timepoints come back embedded, as they are stored — a timeline is one
        record, not a parent with child rows.
        """
        original_db = self._selected
        try:
            await self._use(database)
            rows = await self._query("SELECT * FROM timeline")
            return [Timeline.model_validate(_clean_record(r)) for r in rows]
        finally:
            await self._use(original_db)

    async def viz_list_metacontexts(
        self,
        database: str,
    ) -> Sequence[Metacontext]:
        """List all active metacontexts in a graph for visualization."""
        original_db = self._selected
        try:
            await self._use(database)
            rows = await self._query(
                "SELECT * FROM metacontext WHERE status = $status",
                {"status": NodeStatus.ACTIVE.value},
            )
            return [Metacontext.model_validate(_clean_record(r)) for r in rows]
        finally:
            await self._use(original_db)

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None
        # A viz read that died before its `finally` could leave the selection
        # pointed elsewhere; a later `connect()` must not inherit that.
        self._selected = self._database

    async def _setup_schema(self) -> None:
        """Define tables and indexes. Idempotent.

        Issued straight at the connection rather than through `_query`, which is
        deliberate: this runs *inside* `connect()`, so a retry here would re-enter
        `connect()` while `_reconnect` still holds its lock. A schema that cannot
        be set up is a failed connection, and the caller should hear about it.
        """
        query = self.db.query
        await query("""
            DEFINE TABLE IF NOT EXISTS document SCHEMALESS;
            DEFINE INDEX IF NOT EXISTS idx_document_uid ON document FIELDS uid UNIQUE;
        """)
        await query("""
            DEFINE TABLE IF NOT EXISTS segment SCHEMALESS;
            DEFINE INDEX IF NOT EXISTS idx_segment_uid ON segment FIELDS uid UNIQUE;
            DEFINE INDEX IF NOT EXISTS idx_segment_source ON segment FIELDS source_id;
        """)

        for table in ("topic", "fact", "inference"):
            await query(f"""
                DEFINE TABLE IF NOT EXISTS {table} SCHEMALESS;
                DEFINE INDEX IF NOT EXISTS idx_{table}_uid ON {table} FIELDS uid UNIQUE;
                DEFINE INDEX IF NOT EXISTS idx_{table}_status ON {table} FIELDS status;
                DEFINE INDEX IF NOT EXISTS idx_{table}_content ON {table} FIELDS content;
            """)

        await query("""
            DEFINE TABLE IF NOT EXISTS node_edge SCHEMALESS;
            DEFINE INDEX IF NOT EXISTS idx_edge_uid ON node_edge FIELDS uid UNIQUE;
            DEFINE INDEX IF NOT EXISTS idx_edge_src ON node_edge FIELDS src_id;
            DEFINE INDEX IF NOT EXISTS idx_edge_dst ON node_edge FIELDS dst_id;
            DEFINE INDEX IF NOT EXISTS idx_edge_src_type ON node_edge FIELDS src_id, type;
            DEFINE INDEX IF NOT EXISTS idx_edge_dst_type ON node_edge FIELDS dst_id, type;
        """)

        await query("""
            DEFINE TABLE IF NOT EXISTS embedding SCHEMALESS;
            DEFINE INDEX IF NOT EXISTS idx_emb_uid ON embedding FIELDS uid UNIQUE;
            DEFINE INDEX IF NOT EXISTS idx_emb_item ON embedding FIELDS item_id;
            DEFINE INDEX IF NOT EXISTS idx_emb_model ON embedding FIELDS model_id;
        """)

        await query("""
            DEFINE TABLE IF NOT EXISTS timeline SCHEMALESS;
            DEFINE INDEX IF NOT EXISTS idx_timeline_uid ON timeline FIELDS uid UNIQUE;
        """)

        await query("""
            DEFINE TABLE IF NOT EXISTS metacontext SCHEMALESS;
            DEFINE INDEX IF NOT EXISTS idx_mc_uid ON metacontext FIELDS uid UNIQUE;
            DEFINE INDEX IF NOT EXISTS idx_mc_status ON metacontext FIELDS status;
        """)

        # Per-graph bookkeeping (reflection counter). Addressed by fixed record
        # id, so it needs no uid index.
        await query("""
            DEFINE TABLE IF NOT EXISTS graph_state SCHEMALESS;
        """)

        # Full-text search over node content and segment text. Last, because
        # every table it indexes has to exist first.
        await query(_ANALYZER_DDL)
        await _define_fts_indexes(query)

    @property
    def db(self) -> AsyncSurreal:
        if self._db is None:
            raise RuntimeError("Not connected. Call connect() first.")
        return self._db

    # --- Documents ---

    async def store_document(self, doc: RawDocument) -> str:
        data = _serialize(doc)
        await self._query(_upsert("document"), {"data": data, "uid": doc.id})
        return doc.id

    async def get_document(self, doc_id: str) -> RawDocument | None:
        rows = await self._query(
            "SELECT * FROM document WHERE uid = $uid LIMIT 1",
            {"uid": doc_id},
        )
        if not rows:
            return None
        return RawDocument.model_validate(_clean_record(rows[0]))

    async def get_document_by_source(self, source: str) -> RawDocument | None:
        rows = await self._query(
            "SELECT * FROM document WHERE source = $source LIMIT 1",
            {"source": source},
        )
        if not rows:
            return None
        return RawDocument.model_validate(_clean_record(rows[0]))

    # --- Segments ---

    async def store_segment(self, segment: Segment) -> str:
        data = _serialize(segment)
        await self._query(_upsert("segment"), {"data": data, "uid": segment.id})
        return segment.id

    async def get_segments_for_document(self, doc_id: str) -> Sequence[Segment]:
        rows = await self._query(
            "SELECT * FROM segment WHERE source_id = $source_id ORDER BY span_start",
            {"source_id": doc_id},
        )
        return [Segment.model_validate(_clean_record(r)) for r in rows]

    async def get_segments(self, segment_ids: Sequence[str]) -> dict[str, Segment]:
        wanted = list(dict.fromkeys(segment_ids))
        found: dict[str, Segment] = {}
        for start in range(0, len(wanted), _NODE_FETCH_CHUNK):
            chunk = wanted[start : start + _NODE_FETCH_CHUNK]
            rows = await self._query(
                "SELECT * FROM segment WHERE uid IN $ids", {"ids": chunk}
            )
            for row in rows or []:
                segment = Segment.model_validate(_clean_record(row))
                found[segment.id] = segment
        return found

    # --- Epistemic Nodes ---

    async def store_node(self, node: EpistemicNode) -> str:
        table = _node_to_table(node)
        data = _serialize(node)
        await self._query(_upsert(table), {"data": data, "uid": node.id})
        return node.id

    async def get_node(self, node_id: str) -> EpistemicNode | None:
        for table in ("topic", "fact", "inference"):
            rows = await self._query(
                f"SELECT * FROM {table} WHERE uid = $uid LIMIT 1",
                {"uid": node_id},
            )
            if rows:
                return _record_to_node(table, rows[0])
        return None

    async def get_nodes(self, node_ids: Sequence[str]) -> dict[str, EpistemicNode]:
        wanted = list(dict.fromkeys(node_ids))
        found: dict[str, EpistemicNode] = {}
        if not wanted:
            return found

        # Three statements per chunk rather than the 1-3 *per id* the
        # single-node form costs: `get_node` cannot know which table holds an
        # id, so it probes topic, then fact, then inference, and pays for every
        # miss on the way. Here the misses are free — a table that holds none of
        # these ids returns no rows.
        for start in range(0, len(wanted), _NODE_FETCH_CHUNK):
            chunk = wanted[start : start + _NODE_FETCH_CHUNK]
            for table in ("topic", "fact", "inference"):
                rows = await self._query(
                    f"SELECT * FROM {table} WHERE uid IN $ids", {"ids": chunk}
                )
                for row in rows or []:
                    node = _record_to_node(table, row)
                    found[node.id] = node
        return found

    async def query_nodes(
        self,
        *,
        node_type: NodeType | None = None,
        status: NodeStatus = NodeStatus.ACTIVE,
        at_time: datetime | None = None,
    ) -> Sequence[EpistemicNode]:
        tables = [_NODE_TYPE_TO_TABLE[node_type]] if node_type else ["topic", "fact", "inference"]
        results = []

        for table in tables:
            if at_time is None:
                rows = await self._query(
                    f"SELECT * FROM {table} WHERE status = $status",
                    {"status": status.value},
                )
            else:
                rows = await self._query(
                    f"SELECT * FROM {table} WHERE created_at <= $at_time "
                    f"AND (superseded_at IS NONE OR superseded_at > $at_time)",
                    {"at_time": at_time.isoformat()},
                )
            results.extend(_record_to_node(table, r) for r in rows)

        return results

    async def get_node_by_content(
        self,
        content: str,
        *,
        node_type: NodeType | None = None,
        status: NodeStatus = NodeStatus.ACTIVE,
    ) -> EpistemicNode | None:
        """First node with exactly this content and status (for exact-name upsert).

        Measured at 3,000 topics against a real server (#48): 4.0 ms with the
        planner's own choice, 4.3 ms with an unused content index, **0.53 ms**
        once the index is named — and the write side pays under 5% for that
        index, inside the run-to-run spread, which was the cost that made this
        an issue rather than a patch. See `CONTENT_LOOKUP`.
        """
        tables = [_NODE_TYPE_TO_TABLE[node_type]] if node_type else ["topic", "fact", "inference"]
        for table in tables:
            rows = await self._query(
                CONTENT_LOOKUP.format(table=table),
                {"content": content, "status": status.value},
            )
            if rows:
                return _record_to_node(table, rows[0])
        return None

    async def query_changes(
        self,
        *,
        start: datetime,
        end: datetime,
        node_type: NodeType | None = None,
    ) -> Sequence[EpistemicNode]:
        tables = [_NODE_TYPE_TO_TABLE[node_type]] if node_type else ["topic", "fact", "inference"]
        results = []

        # Half-open window [start, end): a node matches if it was born
        # (created_at), retired (superseded_at — set for supersede and merge
        # alike), or if any lifecycle episode began or ended inside it. The last
        # clause is what makes a node that retired, returned and retired again
        # reportable in a window over anything but its final retirement.
        for table in tables:
            rows = await self._query(
                f"SELECT * FROM {table} WHERE "
                f"(created_at >= $start AND created_at < $end) "
                f"OR (superseded_at != NONE AND superseded_at >= $start "
                f"AND superseded_at < $end) "
                f"OR {_EPISODE_IN_WINDOW}",
                {"start": start.isoformat(), "end": end.isoformat()},
            )
            results.extend(_record_to_node(table, r) for r in rows)

        return results

    async def relabel_edges(self, old_label: str, new_label: str) -> int:
        """Rewrite the label on user-tier edges (in place; edges are not versioned)."""
        rows = await self._query(
            "UPDATE node_edge SET label = $new WHERE type = $related AND label = $old "
            "RETURN BEFORE",
            {"new": new_label, "old": old_label, "related": EdgeType.RELATED.value},
        )
        return len(rows)

    async def get_relation_kind(self, label: str) -> str | None:
        rows = await self._query(
            "SELECT kind FROM node_edge WHERE type = $related AND label = $label LIMIT 1",
            {"related": EdgeType.RELATED.value, "label": label},
        )
        return rows[0]["kind"] if rows else None

    async def count_nodes_by_type(
        self,
        *,
        status: NodeStatus = NodeStatus.ACTIVE,
    ) -> dict[NodeType, int]:
        counts = {nt: 0 for nt in NodeType}
        for node_type, table in _NODE_TYPE_TO_TABLE.items():
            rows = await self._query(
                f"SELECT count() AS c FROM {table} WHERE status = $status GROUP ALL",
                {"status": status.value},
            )
            counts[node_type] = rows[0]["c"] if rows else 0
        return counts

    # --- Edges ---

    async def store_edge(self, edge: NodeEdge) -> str:
        data = _serialize(edge)
        data["type"] = edge.type.value
        await self._query(_upsert("node_edge"), {"data": data, "uid": edge.id})
        return edge.id

    async def delete_edge(self, edge_id: str) -> None:
        await self._query(
            "DELETE node_edge WHERE uid = $uid",
            {"uid": edge_id},
        )

    async def get_edges_from(
        self, node_id: str, *, edge_type: EdgeType | None = None
    ) -> Sequence[NodeEdge]:
        if edge_type is None:
            rows = await self._query(
                "SELECT * FROM node_edge WHERE src_id = $src_id",
                {"src_id": node_id},
            )
        else:
            rows = await self._query(
                "SELECT * FROM node_edge WHERE src_id = $src_id AND type = $type",
                {"src_id": node_id, "type": edge_type.value},
            )
        return [NodeEdge.model_validate(_clean_record(r)) for r in rows]

    async def get_edges_to(
        self, node_id: str, *, edge_type: EdgeType | None = None
    ) -> Sequence[NodeEdge]:
        if edge_type is None:
            rows = await self._query(
                "SELECT * FROM node_edge WHERE dst_id = $dst_id",
                {"dst_id": node_id},
            )
        else:
            rows = await self._query(
                "SELECT * FROM node_edge WHERE dst_id = $dst_id AND type = $type",
                {"dst_id": node_id, "type": edge_type.value},
            )
        return [NodeEdge.model_validate(_clean_record(r)) for r in rows]

    async def get_edges_for(
        self,
        node_ids: Sequence[str],
        *,
        direction: EdgeDirection,
        edge_type: EdgeType | None = None,
    ) -> dict[str, list[NodeEdge]]:
        wanted = list(dict.fromkeys(node_ids))
        # Pre-seeded so an id with no edges still gets a key. The query returns
        # rows, and a node with none contributes none — without this the caller
        # cannot tell "no edges" from "not asked for".
        found: dict[str, list[NodeEdge]] = {node_id: [] for node_id in wanted}
        if not wanted:
            return found

        field = "src_id" if direction == "from" else "dst_id"
        type_clause = " AND type = $type" if edge_type else ""
        params = {"type": edge_type.value} if edge_type else {}

        def collect(rows) -> None:
            for row in rows or []:
                edge = NodeEdge.model_validate(_clean_record(row))
                # Re-group in Python: one flat result set carries no grouping,
                # and `getattr` here is what preserves the association a
                # per-node query got for free. `found` is pre-seeded with the
                # requested ids, so this also drops any row we did not ask for.
                edges = found.get(getattr(edge, field))
                if edges is not None:
                    edges.append(edge)

        if len(wanted) > _EDGE_IN_PREDICATE_MAX:
            # Past the crossover: read the candidate rows and match here. `IN`
            # is evaluated per row rather than through the index, so asking for
            # a large set costs more than reading the type and discarding what
            # nobody asked for. Reflection always arrives on this branch — it
            # asks about every active node at once.
            collect(await self._query(
                "SELECT * FROM node_edge" + (" WHERE type = $type" if edge_type else ""),
                params,
            ))
            return found

        # One statement: the branch above bounds this list, so there is no
        # chunk seam on the edge path to get wrong.
        collect(await self._query(
            f"SELECT * FROM node_edge WHERE {field} IN $ids{type_clause}",
            {**params, "ids": wanted},
        ))
        return found

    async def count_edges_by_type(self) -> dict[EdgeType, int]:
        counts = {et: 0 for et in EdgeType}
        rows = await self._query(
            "SELECT type, count() AS c FROM node_edge GROUP BY type"
        )
        for row in rows or []:
            raw_type = row.get("type")
            if raw_type is None:
                continue
            counts[EdgeType(raw_type)] = row["c"]
        return counts

    # --- Atomic compound operations ---

    async def _run_transaction(
        self, statements: list[str], params: dict
    ) -> None:
        """Execute statements as one atomic BEGIN…COMMIT batch.

        SurrealDB only treats a single multi-statement query as a transaction;
        BEGIN/COMMIT issued across separate calls is not reliable. A runtime
        error in any statement rolls the whole batch back. `query_raw` raises on
        parse errors and reports runtime errors per statement, so we check both.
        """
        sql = "BEGIN TRANSACTION;\n" + ";\n".join(statements) + ";\nCOMMIT TRANSACTION;"
        resp = await self._query_raw(sql, params)

        if isinstance(resp, dict) and resp.get("error") is not None:
            raise RuntimeError(f"Transaction failed: {resp['error']}")
        for result in (resp.get("result", []) if isinstance(resp, dict) else []):
            if isinstance(result, dict) and result.get("status") not in (None, "OK"):
                raise RuntimeError(f"Transaction failed: {result.get('result')}")

    async def _plan_copied_edges(
        self, old_id: str, new_id: str, status: NodeStatus
    ) -> list[dict]:
        """Rows for the edges a retirement *copies* onto the replacement.

        Planned in Python and read pre-transaction, the same way
        `merge_nodes_tx` plans its re-pointing: the adapter is single-connection
        and already documented as unsafe for concurrent callers, so nothing
        interleaves. Copies are rebuilt rather than cloned, so `uid` and
        `created_at` are the new edge's own.
        """
        copied = {t for t in EdgeType if migration_disposition(t, status) == "copy"}
        if not copied:
            return []

        rows: list[dict] = []
        seen: set[tuple[str, str, str]] = set()
        incident = {e.id: e for e in await self.get_edges_from(old_id)}
        incident |= {e.id: e for e in await self.get_edges_to(old_id)}
        for edge in incident.values():
            if edge.type not in copied:
                continue
            new_src = new_id if edge.src_id == old_id else edge.src_id
            new_dst = new_id if edge.dst_id == old_id else edge.dst_id
            signature = (new_src, new_dst, edge.type.value)
            if new_src == new_dst or signature in seen:
                continue
            seen.add(signature)
            rows.append(_edge_row(NodeEdge(
                **(edge.model_dump(exclude={"id", "created_at"})
                   | {"src_id": new_src, "dst_id": new_dst}),
            )))
        return rows

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
    ) -> None:
        # Which edges follow the replacement depends on *why* the old node is
        # being retired (#54): a correction re-points everything but history,
        # review and judgments (#65); a world-change re-points nothing and
        # copies only the frame and the tags. Both answers come from
        # `migration_disposition`, so this backend cannot develop an opinion of
        # its own.
        moved = [t.value for t in moved_edge_types(status)]
        copied_data = await self._plan_copied_edges(old_node.id, new_node.id, status)

        statements = [
            f"UPDATE {_node_to_table(old_node)} SET status = $status, "
            f"superseded_at = $sup_at, lifecycle = $lifecycle "
            f"WHERE uid = $old_uid",
            f"INSERT INTO {_node_to_table(new_node)} $new_data",
            "INSERT INTO embedding $emb_data",
        ]
        if moved:
            statements += [
                "UPDATE node_edge SET src_id = $new_uid "
                "WHERE src_id = $old_uid AND type IN $moved",
                "UPDATE node_edge SET dst_id = $new_uid "
                "WHERE dst_id = $old_uid AND type IN $moved",
                "DELETE node_edge WHERE src_id = $new_uid AND dst_id = $new_uid",
            ]
        if copied_data:
            statements.append("INSERT INTO node_edge $copied_data")
        statements.append("INSERT INTO node_edge $lineage_data")
        params: dict = {
            "status": status.value,
            "sup_at": superseded_at.isoformat(),
            "old_uid": old_node.id,
            "new_uid": new_node.id,
            "new_data": _serialize(new_node),
            "emb_data": _serialize(new_embedding),
            "lifecycle": _episode_rows(with_retirement(
                old_node.lifecycle, at=superseded_at, because=status,
                counterpart=new_node.id,
            )),
            "moved": moved,
            "copied_data": copied_data,
            "lineage_data": _edge_row(lineage_edge),
        }
        self._append_review_writes(statements, params, evidence_edges, clear_edge_ids)
        await self._run_transaction(statements, params)

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
    ) -> None:
        statements = [
            f"UPDATE {_node_to_table(old_node)} SET status = $status, "
            f"superseded_at = $sup_at, lifecycle = $lifecycle "
            f"WHERE uid = $old_uid",
            "INSERT INTO node_edge $lineage_data",
        ]
        params: dict = {
            "status": status.value,
            "sup_at": superseded_at.isoformat(),
            "old_uid": old_node.id,
            "lineage_data": _edge_row(lineage_edge),
            "lifecycle": _episode_rows(with_retirement(
                old_node.lifecycle, at=superseded_at, because=status,
                counterpart=existing_id,
            )),
        }
        self._append_review_writes(statements, params, evidence_edges, clear_edge_ids)
        await self._run_transaction(statements, params)

    async def set_node_status_tx(
        self,
        nodes: Sequence[EpistemicNode],
        *,
        status: NodeStatus,
        at: datetime,
        edges: Sequence[NodeEdge] = (),
    ) -> None:
        if not nodes:
            return

        # `UPDATE … WHERE uid = $uid` silently matches nothing when the row is
        # absent, so a missing node would flip the rest and report success. The
        # THROW makes the transaction fail the way the in-memory backend's
        # KeyError does — the parity rule is about behaviour, and "partially
        # applied" is exactly the behaviour that must not differ.
        uids = [node.id for node in nodes]
        statements = [
            f"LET $found = (SELECT VALUE uid FROM {_node_tables(None)} "
            "WHERE uid IN $uids)",
            "IF array::len($found) != $expected "
            "{ THROW 'set_node_status_tx: node not found' }",
        ]
        returning = status is NodeStatus.ACTIVE
        params: dict = {
            "uids": uids,
            "expected": len(set(uids)),
            "status": status.value,
            "retired_at": None if returning else at.isoformat(),
        }
        for table in {_node_to_table(node) for node in nodes}:
            statements.append(
                f"UPDATE {table} SET status = $status, "
                "superseded_at = $retired_at WHERE uid IN $uids"
            )
        # The history is per-node, so it takes a statement per node. The status
        # flip above stays a single bulk update: it is the same for all of them,
        # and it is the part that has to be all-or-nothing.
        for i, node in enumerate(nodes):
            episodes = (
                with_return(node.lifecycle, at=at) if returning
                else with_retirement(node.lifecycle, at=at, because=status)
            )
            params[f"lifecycle_{i}"] = _episode_rows(episodes)
            params[f"uid_{i}"] = node.id
            statements.append(
                f"UPDATE {_node_to_table(node)} SET lifecycle = $lifecycle_{i} "
                f"WHERE uid = $uid_{i}"
            )
        # Inside the same transaction, so a reactivated node can never be left
        # ACTIVE without the edge saying what asserted it again (#53 T2).
        if edges:
            statements.append("INSERT INTO node_edge $new_edges")
            params["new_edges"] = [_edge_row(edge) for edge in edges]
        await self._run_transaction(statements, params)

    @staticmethod
    def _append_review_writes(
        statements: list[str],
        params: dict,
        evidence_edges: Sequence[NodeEdge],
        clear_edge_ids: Sequence[str],
    ) -> None:
        """Append optional evidence-edge inserts and candidate-edge deletes."""
        evidence_rows = [_edge_row(edge) for edge in evidence_edges]
        if evidence_rows:
            statements.append("INSERT INTO node_edge $evidence_rows")
            params["evidence_rows"] = evidence_rows
        if clear_edge_ids:
            statements.append("DELETE node_edge WHERE uid IN $clear_ids")
            params["clear_ids"] = list(clear_edge_ids)

    async def merge_nodes_tx(
        self,
        source_nodes: Sequence[EpistemicNode],
        merged_node: EpistemicNode,
        merged_embedding: EmbeddingRecord,
        lineage_edges: Sequence[NodeEdge],
        *,
        merged_at: datetime,
        evidence_edges: Sequence[NodeEdge] = (),
    ) -> None:
        source_ids = {s.id for s in source_nodes}

        # Plan the edge migration in Python: read the sources' migrating edges,
        # re-point them onto the merged node, and drop self-loops and duplicate
        # (src, dst, type) edges, keeping one per group. Planning here keeps it
        # deterministic — in-transaction GROUP BY dedup proved unreliable. The
        # reads are pre-transaction; the adapter is single-connection (already
        # documented as not safe for concurrent callers), so nothing interleaves.
        # Walked in the caller's source order, not `source_ids`' — iterating a
        # set makes which duplicate survives a collapse depend on hash order.
        incident: dict[str, NodeEdge] = {}
        for source in source_nodes:
            for edge in await self.get_edges_from(source.id):
                incident[edge.id] = edge
            for edge in await self.get_edges_to(source.id):
                incident[edge.id] = edge

        old_edge_ids: list[str] = []
        repointed: list[NodeEdge] = []
        survivors: dict[tuple[str, str, str], NodeEdge] = {}
        for edge in incident.values():
            if migration_disposition(edge.type, NodeStatus.MERGED) == "keep":
                continue
            old_edge_ids.append(edge.id)  # every incident edge is deleted...
            new_src = merged_node.id if edge.src_id in source_ids else edge.src_id
            new_dst = merged_node.id if edge.dst_id in source_ids else edge.dst_id
            signature = (new_src, new_dst, edge.type.value)
            survivor = survivors.get(signature)
            if new_src == new_dst or survivor is not None:
                # ...self-loops and duplicates are not recreated, but what a
                # duplicate *asserted* is: collapsing two provenance edges to one
                # document must not lose either one's periods (#53 T1 §2).
                # Rebound rather than appended — `model_copy` shares the list.
                if survivor is not None:
                    survivor.validity = merged_validity(survivor.validity, edge.validity)
                continue
            moved = edge.model_copy(update={"src_id": new_src, "dst_id": new_dst})
            survivors[signature] = moved
            repointed.append(moved)

        repointed_data: list[dict] = []
        for edge in repointed:
            row = _serialize(edge)
            row["type"] = edge.type.value
            repointed_data.append(row)

        lineage_data = []
        for edge in lineage_edges:
            row = _serialize(edge)
            row["type"] = edge.type.value
            lineage_data.append(row)

        statements = [
            f"INSERT INTO {_node_to_table(merged_node)} $merged_data",
            "INSERT INTO embedding $emb_data",
        ]
        if old_edge_ids:
            statements.append("DELETE node_edge WHERE uid IN $old_edge_ids")
        if repointed_data:
            statements.append("INSERT INTO node_edge $repointed_data")
        # One statement per source: the status and instant are shared, but each
        # source carries its own history and writes its own list.
        for i, source in enumerate(source_nodes):
            statements.append(
                f"UPDATE {_node_to_table(source)} SET status = $status, "
                f"superseded_at = $merged_at, lifecycle = $lifecycle_{i} "
                f"WHERE uid = $source_{i}"
            )
        if lineage_data:
            statements.append("INSERT INTO node_edge $lineage_data")

        # The *stored* lifecycle, not the caller's copy. A node object handed in
        # by a caller that loaded it before an earlier merge carries a stale
        # list, and appending to that silently drops every episode since —
        # which is how a second merge/reverse cycle came back looking like the
        # first. `InMemoryStorage` reads the stored node here, so trusting the
        # argument also made the two backends disagree.
        stored = await self.get_nodes([source.id for source in source_nodes])
        source_params: dict = {}
        for i, source in enumerate(source_nodes):
            current = stored.get(source.id, source)
            source_params[f"source_{i}"] = source.id
            source_params[f"lifecycle_{i}"] = _episode_rows(with_retirement(
                current.lifecycle, at=merged_at, because=NodeStatus.MERGED,
                counterpart=merged_node.id,
            ))

        params: dict = {
            **source_params,
            "merged_data": _serialize(merged_node),
            "emb_data": _serialize(merged_embedding),
            "old_edge_ids": old_edge_ids,
            "repointed_data": repointed_data,
            "sources": list(source_ids),
            "status": NodeStatus.MERGED.value,
            "merged_at": merged_at.isoformat(),
            "lineage_data": lineage_data,
        }
        # Appended last, after the migration's DELETE: the flags are anchored to
        # the sources, and `old_edge_ids` names only edges that were incident
        # before this transaction, so nothing here is deleted by it.
        self._append_review_writes(statements, params, evidence_edges, ())

        await self._run_transaction(statements, params)

    async def reverse_merge_tx(
        self,
        survivor: EpistemicNode,
        source_nodes: Sequence[EpistemicNode],
        restored_edges: Sequence[NodeEdge],
        *,
        restored_at: datetime,
        delete_edge_ids: Sequence[str],
    ) -> None:
        """See the protocol. **The only hard delete in this backend**, and it
        must never be reachable from an MCP tool (REVIEW_MODE.md §7.7)."""
        # Read before the transaction, like every other plan here: the adapter
        # is single-connection and documented as unsafe for concurrent callers.
        # The check is the protocol's requirement — a survivor still carrying an
        # edge after the planned deletions means the guard let something
        # through, and deleting the node anyway would take that edge with it.
        remaining = {
            edge.id for edge in await self.get_edges_from(survivor.id)
        } | {
            edge.id for edge in await self.get_edges_to(survivor.id)
        }
        remaining -= set(delete_edge_ids)
        if remaining:
            raise ValueError(
                f"node {survivor.id} still has {len(remaining)} edges and must "
                f"not be deleted: a hard delete would take them with it."
            )

        statements: list[str] = []
        params: dict = {"survivor_uid": survivor.id}

        if delete_edge_ids:
            statements.append("DELETE node_edge WHERE uid IN $delete_edge_ids")
            params["delete_edge_ids"] = list(delete_edge_ids)
        if restored_edges:
            rows = []
            for edge in restored_edges:
                row = _serialize(edge)
                row["type"] = edge.type.value
                rows.append(row)
            statements.append("INSERT INTO node_edge $restored_rows")
            params["restored_rows"] = rows

        # One statement per source: the status and instant are shared, each
        # source's history is its own.
        # Stored rather than passed, for the reason `merge_nodes_tx` states.
        stored = await self.get_nodes([source.id for source in source_nodes])
        for i, source in enumerate(source_nodes):
            current = stored.get(source.id, source)
            params[f"source_{i}"] = source.id
            params[f"lifecycle_{i}"] = _episode_rows(
                with_return(current.lifecycle, at=restored_at)
            )
            statements.append(
                f"UPDATE {_node_to_table(source)} SET status = $status, "
                f"superseded_at = NONE, lifecycle = $lifecycle_{i} "
                f"WHERE uid = $source_{i}"
            )
        params["status"] = NodeStatus.ACTIVE.value

        statements.append(
            f"DELETE {_node_to_table(survivor)} WHERE uid = $survivor_uid"
        )
        # The vector is stored per item, so deleting the node alone would strand
        # an entry the index still returns.
        statements.append("DELETE embedding WHERE item_id = $survivor_uid")

        await self._run_transaction(statements, params)

    async def write_batch_tx(
        self,
        *,
        nodes: Sequence[EpistemicNode] = (),
        edges: Sequence[NodeEdge] = (),
        embeddings: Sequence[EmbeddingRecord] = (),
        timelines: Sequence[Timeline] = (),
    ) -> None:
        statements: list[str] = []
        params: dict = {}

        # Nodes, grouped by their table (topic/fact/inference).
        nodes_by_table: dict[str, list[dict]] = {}
        for node in nodes:
            nodes_by_table.setdefault(_node_to_table(node), []).append(_serialize(node))
        for i, (table, rows) in enumerate(nodes_by_table.items()):
            key = f"nodes_{i}"
            statements.append(f"INSERT INTO {table} ${key}")
            params[key] = rows

        if edges:
            edge_rows = []
            for edge in edges:
                row = _serialize(edge)
                row["type"] = edge.type.value
                edge_rows.append(row)
            statements.append("INSERT INTO node_edge $edge_rows")
            params["edge_rows"] = edge_rows

        # Timelines upsert rather than insert (see the protocol docstring), so
        # they get one statement each: `UPSERT ... WHERE uid` matches a single
        # row and cannot be expressed as a bulk insert of rows.
        for i, timeline in enumerate(timelines):
            statements.append(
                _upsert("timeline", data=f"timeline_{i}", uid=f"timeline_uid_{i}")
            )
            params[f"timeline_{i}"] = _serialize(timeline)
            params[f"timeline_uid_{i}"] = timeline.id

        if embeddings:
            statements.append("INSERT INTO embedding $embedding_rows")
            params["embedding_rows"] = [_serialize(e) for e in embeddings]

        if not statements:
            return
        await self._run_transaction(statements, params)

    # --- Embeddings ---

    async def store_embedding(self, embedding: EmbeddingRecord) -> str:
        data = _serialize(embedding)
        await self._query(_upsert("embedding"), {"data": data, "uid": embedding.id})
        return embedding.id

    async def get_embeddings_for_item(
        self, item_id: str, model_id: str | None = None
    ) -> Sequence[EmbeddingRecord]:
        # `model_id` is filtered here rather than in the query, and that is the
        # whole point of this shape. `WHERE item_id = $i AND model_id = $m` made
        # the planner choose `idx_emb_model` — which matches *every* row for the
        # graph's one model — and filter `item_id` afterwards, so a single-item
        # fetch scanned the entire embedding table. Measured per call: 2.4 ms at
        # 400 embeddings, 6.2 at 1,200, 15.6 at 3,000, dead linear. Asking on
        # `item_id` alone uses `idx_emb_item` and is flat at ~0.6 ms.
        #
        # `idx_emb_model` is not the problem and must stay: `_ranked_items`
        # narrows by model and genuinely needs it. Nor is a composite index the
        # fix — measured, the planner still preferred the unselective one.
        rows = await self._query(
            "SELECT * FROM embedding WHERE item_id = $item_id", {"item_id": item_id}
        )
        records = [EmbeddingRecord.model_validate(_clean_record(r)) for r in rows]
        if model_id is None:
            return records
        return [record for record in records if record.model_id == model_id]

    async def get_embeddings_for_items(
        self, item_ids: Sequence[str], *, model_id: str | None = None
    ) -> dict[str, list[EmbeddingRecord]]:
        wanted = list(dict.fromkeys(item_ids))
        # Pre-seeded for the same reason as `get_edges_for`: an item with no
        # embedding still gets a key, so absence means "not asked".
        found: dict[str, list[EmbeddingRecord]] = {item_id: [] for item_id in wanted}
        if not wanted:
            return found

        # `model_id` stays out of the query here too — the predicate that steers
        # the planner onto `idx_emb_model` is the one this fetch cannot afford.
        for start in range(0, len(wanted), _EMBEDDING_FETCH_CHUNK):
            chunk = wanted[start : start + _EMBEDDING_FETCH_CHUNK]
            rows = await self._query(
                "SELECT * FROM embedding WHERE item_id IN $ids", {"ids": chunk}
            )
            for row in rows or []:
                record = EmbeddingRecord.model_validate(_clean_record(row))
                if model_id is not None and record.model_id != model_id:
                    continue
                records = found.get(record.item_id)
                if records is not None:
                    records.append(record)
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
        """Top-k nodes by cosine similarity, filtered to `statuses`.

        The default is ACTIVE alone, which is the guard this method used to
        enforce by construction: retired nodes do not resurface unless a caller
        names them. `k` counts results the caller can use rather than rows
        examined, so the filter cannot simply be applied to an already-truncated
        ranking.

        Rank first, filter after, over-fetching enough that the filter has
        candidates to keep. Retired nodes are a small minority of a healthy
        graph, so reaching `k × 3` deep almost always leaves `k` survivors after
        one cheap scan and one membership check on ~30 ids. When it does not —
        a graph with a lot of history, or a typed search where the requested
        type is a minority of the embeddings — reach further, and only then pay
        for the exact query. A wider `statuses` only ever makes the over-fetch
        *more* likely to suffice, since fewer candidates are discarded.

        The obvious formulation, filtering inside the ranking query with
        `item_id IN (SELECT ...)`, is the one thing to avoid: SurrealDB re-runs
        that subquery per embedding row. See `_ranked_items_with_status`.

        TODO: When SurrealDB adds native HNSW vector indexes, switch to those.
        For now, brute-force via SurrealQL vector::similarity::cosine().
        """
        for factor in _OVERFETCH_FACTORS:
            limit = k * factor
            candidates = await _ranked_items(self._query, query_vector, model_id, limit)
            keep = set(
                await _ids_with_status(
                    self._query, node_type, statuses,
                    among=[item_id for item_id, _ in candidates],
                )
            )
            active = [(i, score) for i, score in candidates if i in keep]
            # Fewer rows than asked for means the scan reached the end of the
            # embeddings, so a deeper reach cannot find anything more.
            if len(active) >= k or len(candidates) < limit:
                return active[:k]
        return await _ranked_items_with_status(
            self._query, query_vector, model_id, k, node_type, statuses
        )

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
        """Top-k rows by BM25, scored in-engine. See the protocol for the contract.

        One match reference per term, ORed, with their scores summed. A single
        `@@` is **conjunctive** — every token of its argument must be present —
        which is what makes `JIRA-4417` reject `JIRA-4418`, and also what makes
        one absent word in a multi-word query return nothing at all. Separate
        references give the OR the terms need while each stays conjunctive
        within itself (§2.4).

        The status gate and the zero-score truncation both sit *outside* a
        subquery whose `WHERE` holds nothing but match references. That shape is
        load-bearing rather than stylistic — see `_TEXT_SEARCH_OVERFETCH`.

        `verify_containment` obeys the same constraint from the other side: the
        matched text comes back with the rows and the string comparison happens
        here, in Python. As a `WHERE` predicate it would be the same trap in a
        new costume — the index drops, `@@` turns disjunctive, and the near-miss
        returns at a score the zero rule cannot catch (R8, §11.4).
        """
        if not terms:
            return []

        if corpus == "segments":
            table, field = "segment", "text"
            carried, conditions, params = "", [], {}
        else:
            if node_type is None:
                raise ValueError(
                    "text_search(corpus='nodes') requires a node_type: BM25 "
                    "statistics are per node table, so a merged multi-type list "
                    "would sort incomparable scores."
                )
            table, field = _NODE_TYPE_TO_TABLE[node_type], "content"
            carried, conditions = "status, ", ["status IN $statuses"]
            # Bound rather than interpolated, as everywhere else a status set
            # reaches a query: the values are an enum's, but the list is built
            # from a caller's argument and the two facts are unrelated.
            params = {"statuses": sorted(status.value for status in statuses)}

        # Match references are 1-based and each term gets its own. Each score is
        # floored at zero before the sum: the standalone engine already clamps
        # its IDF there, but the older core embedded in the Python SDK returns
        # the negative value instead, and an uninformative term would then drag
        # a document's total *below* the truncation and delete a real hit. The
        # contract says a term more common than half the corpus contributes
        # nothing; on one of these engines that has to be made true here.
        refs = range(1, len(terms) + 1)
        matches = " OR ".join(f"{field} @{ref}@ $term_{ref}" for ref in refs)
        score = " + ".join(f"math::max([search::score({ref}), 0])" for ref in refs)
        params |= {f"term_{ref}": terms[ref - 1] for ref in refs}

        # The zero rule is the engine's job only when nothing may rescue a
        # floored row; with containment it becomes this method's, applied after
        # the partition. The matched text rides along for the same reason.
        if verify_containment:
            fetched, selected = f"{field} AS body, ", "body, "
            reach = k * _CONTAINMENT_OVERFETCH
            limit = reach  # the partition decides what the top k is, not the score
        else:
            fetched = selected = ""
            reach, limit = k * _TEXT_SEARCH_OVERFETCH, k
            conditions.append("score > 0")
        params |= {"reach": reach, "limit": limit}
        gate = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        rows = await self._query(
            f"""
            SELECT uid, {selected}score FROM (
                SELECT uid, {fetched}{carried}{score} AS score
                FROM {table}
                WHERE {matches}
                ORDER BY score DESC
                LIMIT $reach
            )
            {gate}
            ORDER BY score DESC
            LIMIT $limit
            """,
            params,
        )
        candidates = [(row["uid"], row["score"]) for row in rows or []]
        if not verify_containment:
            return candidates
        return containment_first(
            candidates, {row["uid"]: row["body"] for row in rows or []}, terms
        )[:k]

    async def get_nodes_by_source(
        self, source_ids: Sequence[str]
    ) -> dict[str, list[EpistemicNode]]:
        """Three statements per chunk — one per node table — not one per id.

        A node's table is not derivable from its `source_id`, so the segment
        bridge would otherwise probe topic, then fact, then inference for every
        segment a lexical search hit. That is the shape ISSUES.md #14 exists to
        keep out of the read paths.
        """
        wanted = list(dict.fromkeys(source_ids))
        if not wanted:
            return {}

        by_source: dict[str, list[EpistemicNode]] = {sid: [] for sid in wanted}
        for start in range(0, len(wanted), _NODE_FETCH_CHUNK):
            chunk = wanted[start : start + _NODE_FETCH_CHUNK]
            for table in ("topic", "fact", "inference"):
                rows = await self._query(
                    f"SELECT * FROM {table} WHERE source_id IN $ids", {"ids": chunk}
                )
                for row in rows or []:
                    node = _record_to_node(table, row)
                    by_source[node.source_id].append(node)
        return by_source

    # --- Timelines ---

    async def store_timeline(self, timeline: Timeline) -> str:
        data = _serialize(timeline)
        await self._query(_upsert("timeline"), {"data": data, "uid": timeline.id})
        return timeline.id

    async def get_timeline(self, timeline_id: str) -> Timeline | None:
        rows = await self._query(
            "SELECT * FROM timeline WHERE uid = $uid LIMIT 1",
            {"uid": timeline_id},
        )
        if not rows:
            return None
        return Timeline.model_validate(_clean_record(rows[0]))

    async def query_timelines(self) -> Sequence[Timeline]:
        rows = await self._query("SELECT * FROM timeline")
        return [Timeline.model_validate(_clean_record(r)) for r in rows]

    # --- Metacontexts ---

    async def store_metacontext(self, mc: Metacontext) -> str:
        data = _serialize(mc)
        await self._query(_upsert("metacontext"), {"data": data, "uid": mc.id})
        return mc.id

    async def get_metacontext(self, mc_id: str) -> Metacontext | None:
        rows = await self._query(
            "SELECT * FROM metacontext WHERE uid = $uid LIMIT 1",
            {"uid": mc_id},
        )
        if not rows:
            return None
        return Metacontext.model_validate(_clean_record(rows[0]))

    async def query_metacontexts(
        self,
        *,
        status: NodeStatus = NodeStatus.ACTIVE,
    ) -> Sequence[Metacontext]:
        rows = await self._query(
            "SELECT * FROM metacontext WHERE status = $status",
            {"status": status.value},
        )
        return [Metacontext.model_validate(_clean_record(r)) for r in rows]

    # --- Reflection bookkeeping ---

    async def get_reflect_counter(self) -> int:
        rows = await self._query(_REFLECT_GET)
        return _reflect_count(rows)

    async def bump_reflect_counter(self) -> int:
        rows = await self._query(_REFLECT_BUMP)
        return _reflect_count(rows)

    async def reset_reflect_counter(self) -> int:
        rows = await self._query(_REFLECT_RESET)
        return _reflect_count(rows)

    async def get_reflect_threshold_override(self) -> int | None:
        rows = await self._query(_THRESHOLD_GET)
        return _threshold_override(rows)

    async def set_reflect_threshold_override(self, threshold: int | None) -> None:
        await self._query(_THRESHOLD_SET, {"threshold": threshold})

    async def get_merge_overrides(self) -> MergeOverrides:
        rows = await self._query(_MERGE_GET)
        return _merge_overrides(rows)

    async def set_merge_overrides(self, overrides: MergeOverrides) -> None:
        await self._query(
            _MERGE_SET, {"overrides": drop_none_values(overrides.model_dump())},
        )
