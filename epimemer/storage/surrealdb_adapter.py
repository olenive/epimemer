"""SurrealDB storage backend.

Implements the StorageBackend protocol using SurrealDB.
Supports ws://, http://, and mem:// (embedded) connections.

Note: We use 'uid' as our application-level ID field to avoid conflicts
with SurrealDB's built-in 'id' field (which uses RecordID type).
"""

import asyncio
import contextlib
from datetime import datetime
from typing import Any, Awaitable, Callable, Sequence

from surrealdb import AsyncSurreal
from websockets.exceptions import ConnectionClosed, WebSocketException

from epimemer.core.types import (
    NON_KNOWLEDGE_EDGE_TYPES,
    EdgeType,
    EmbeddingRecord,
    EpistemicNode,
    Fact,
    Inference,
    Metacontext,
    NodeEdge,
    NodeStatus,
    NodeType,
    RawDocument,
    Segment,
    Timeline,
    Topic,
    migration_excluded,
)
from epimemer.storage.protocol import (
    EdgeDirection,
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


async def _active_ids(
    query, node_type: NodeType | None, *, among: list[str] | None = None
) -> list[str]:
    """Uids of active nodes — all of them, or only those in `among`.

    Restricting to `among` is the cheap direction: a handful of candidate ids
    checked against the unique index on `uid`. Passing `among=None` reads every
    active id, which is only worth doing to feed the exact query below.
    """
    tables = _node_tables(node_type)
    if among is None:
        return list(
            await query(f"SELECT VALUE uid FROM {tables} WHERE status = 'active'")
        )
    if not among:
        return []
    return list(
        await query(
            f"SELECT VALUE uid FROM {tables} WHERE status = 'active' AND uid IN $ids",
            {"ids": among},
        )
    )


async def _ranked_active_items(
    query,
    query_vector: list[float],
    model_id: str,
    k: int,
    node_type: NodeType | None,
) -> list[tuple[str, float]]:
    """Exact top-k over active nodes only: rank what survives the filter.

    Two round-trips rather than one query with a subquery. Expressing the filter
    as `item_id IN (SELECT ...)` reads well but makes SurrealDB re-run that
    subquery *per embedding row*, so the cost becomes embeddings × nodes — the
    quadratic term that made search the first operation in this system to fail
    its tool timeout. Fetching the ids first and binding them as a parameter
    turns the per-row work into an array membership test.

    Still linear in (rows × active nodes) in-engine, so this is the fallback
    rather than the usual path — correct at any ratio of retired nodes, and
    bounded, but it grows.

    (It cannot be written as one `LET $active = (...); SELECT ...` call: this
    driver returns the *first* statement's result, so the select's rows would be
    thrown away and `LET`'s `None` returned in their place.)
    """
    active = await _active_ids(query, node_type)
    if not active:
        return []
    rows = await query(
        """
        SELECT
            item_id,
            vector::similarity::cosine(vector, $query_vector) AS score
        FROM embedding
        WHERE model_id = $model_id AND item_id IN $active
        ORDER BY score DESC
        LIMIT $k
        """,
        {
            "query_vector": query_vector,
            "model_id": model_id,
            "k": k,
            "active": active,
        },
    )
    return [(r["item_id"], r["score"]) for r in rows]


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
        """First active node with exactly this content (for exact-name upsert)."""
        tables = [_NODE_TYPE_TO_TABLE[node_type]] if node_type else ["topic", "fact", "inference"]
        for table in tables:
            rows = await self._query(
                f"SELECT * FROM {table} WHERE content = $content AND status = $status LIMIT 1",
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
        # (created_at) or retired (superseded_at — set for supersede and merge
        # alike) within the window. Timestamps are uniform UTC ISO-8601 strings,
        # so the string comparison is chronologically correct.
        for table in tables:
            rows = await self._query(
                f"SELECT * FROM {table} WHERE "
                f"(created_at >= $start AND created_at < $end) "
                f"OR (superseded_at != NONE AND superseded_at >= $start "
                f"AND superseded_at < $end)",
                {"start": start.isoformat(), "end": end.isoformat()},
            )
            results.extend(_record_to_node(table, r) for r in rows)

        return results

    async def update_node_status(
        self,
        node_id: str,
        status: NodeStatus,
        superseded_at: datetime | None = None,
    ) -> None:
        for table in ("topic", "fact", "inference"):
            rows = await self._query(
                f"UPDATE {table} SET status = $status, superseded_at = $superseded_at WHERE uid = $uid",
                {
                    "uid": node_id,
                    "status": status.value,
                    "superseded_at": superseded_at.isoformat() if superseded_at else None,
                },
            )
            if rows:
                return
        raise KeyError(f"Node {node_id} not found")

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
        excluded = [t.value for t in NON_KNOWLEDGE_EDGE_TYPES]

        statements = [
            f"UPDATE {_node_to_table(old_node)} SET status = $status, "
            f"superseded_at = $sup_at WHERE uid = $old_uid",
            f"INSERT INTO {_node_to_table(new_node)} $new_data",
            "INSERT INTO embedding $emb_data",
            "UPDATE node_edge SET src_id = $new_uid "
            "WHERE src_id = $old_uid AND type NOT IN $excluded",
            "UPDATE node_edge SET dst_id = $new_uid "
            "WHERE dst_id = $old_uid AND type NOT IN $excluded",
            "DELETE node_edge WHERE src_id = $new_uid AND dst_id = $new_uid",
            "INSERT INTO node_edge $lineage_data",
        ]
        params: dict = {
            "status": status.value,
            "sup_at": superseded_at.isoformat(),
            "old_uid": old_node.id,
            "new_uid": new_node.id,
            "new_data": _serialize(new_node),
            "emb_data": _serialize(new_embedding),
            "excluded": excluded,
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
            f"superseded_at = $sup_at WHERE uid = $old_uid",
            "INSERT INTO node_edge $lineage_data",
        ]
        params: dict = {
            "status": status.value,
            "sup_at": superseded_at.isoformat(),
            "old_uid": old_node.id,
            "lineage_data": _edge_row(lineage_edge),
        }
        self._append_review_writes(statements, params, evidence_edges, clear_edge_ids)
        await self._run_transaction(statements, params)

    async def set_node_status_tx(
        self,
        nodes: Sequence[EpistemicNode],
        *,
        status: NodeStatus,
        retired_at: datetime | None,
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
        params: dict = {
            "uids": uids,
            "expected": len(set(uids)),
            "status": status.value,
            "retired_at": retired_at.isoformat() if retired_at else None,
        }
        for table in {_node_to_table(node) for node in nodes}:
            statements.append(
                f"UPDATE {table} SET status = $status, "
                "superseded_at = $retired_at WHERE uid IN $uids"
            )
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
    ) -> None:
        source_ids = {s.id for s in source_nodes}

        # Plan the edge migration in Python: read the sources' non-history edges,
        # re-point them onto the merged node, and drop self-loops and duplicate
        # (src, dst, type) edges, keeping one per group. Planning here keeps it
        # deterministic — in-transaction GROUP BY dedup proved unreliable. The
        # reads are pre-transaction; the adapter is single-connection (already
        # documented as not safe for concurrent callers), so nothing interleaves.
        incident: dict[str, NodeEdge] = {}
        for sid in source_ids:
            for edge in await self.get_edges_from(sid):
                incident[edge.id] = edge
            for edge in await self.get_edges_to(sid):
                incident[edge.id] = edge

        old_edge_ids: list[str] = []
        repointed_data: list[dict] = []
        seen: set[tuple[str, str, str]] = set()
        for edge in incident.values():
            if migration_excluded(edge):
                continue
            old_edge_ids.append(edge.id)  # every incident edge is deleted...
            new_src = merged_node.id if edge.src_id in source_ids else edge.src_id
            new_dst = merged_node.id if edge.dst_id in source_ids else edge.dst_id
            signature = (new_src, new_dst, edge.type.value)
            if new_src == new_dst or signature in seen:
                continue  # ...self-loops and duplicates are not recreated
            seen.add(signature)
            row = _serialize(edge.model_copy(update={"src_id": new_src, "dst_id": new_dst}))
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
        statements += [
            "UPDATE topic SET status = $status, superseded_at = $merged_at "
            "WHERE uid IN $sources",
            "UPDATE fact SET status = $status, superseded_at = $merged_at "
            "WHERE uid IN $sources",
            "UPDATE inference SET status = $status, superseded_at = $merged_at "
            "WHERE uid IN $sources",
        ]
        if lineage_data:
            statements.append("INSERT INTO node_edge $lineage_data")

        await self._run_transaction(
            statements,
            {
                "merged_data": _serialize(merged_node),
                "emb_data": _serialize(merged_embedding),
                "old_edge_ids": old_edge_ids,
                "repointed_data": repointed_data,
                "sources": list(source_ids),
                "status": NodeStatus.MERGED.value,
                "merged_at": merged_at.isoformat(),
                "lineage_data": lineage_data,
            },
        )

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
    ) -> Sequence[tuple[str, float]]:
        """Top-k active nodes by cosine similarity.

        Superseded and merged nodes must never resurface here, and `k` counts
        results the caller can use rather than rows examined — so the filter
        cannot simply be applied to an already-truncated ranking.

        Rank first, filter after, over-fetching enough that the filter has
        candidates to keep. Retired nodes are a small minority of a healthy
        graph, so reaching `k × 3` deep almost always leaves `k` survivors after
        one cheap scan and one membership check on ~30 ids. When it does not —
        a graph with a lot of history, or a typed search where the requested
        type is a minority of the embeddings — reach further, and only then pay
        for the exact query.

        The obvious formulation, filtering inside the ranking query with
        `item_id IN (SELECT ...)`, is the one thing to avoid: SurrealDB re-runs
        that subquery per embedding row. See `_ranked_active_items`.

        TODO: When SurrealDB adds native HNSW vector indexes, switch to those.
        For now, brute-force via SurrealQL vector::similarity::cosine().
        """
        for factor in _OVERFETCH_FACTORS:
            limit = k * factor
            candidates = await _ranked_items(self._query, query_vector, model_id, limit)
            keep = set(
                await _active_ids(
                    self._query, node_type, among=[item_id for item_id, _ in candidates]
                )
            )
            active = [(i, score) for i, score in candidates if i in keep]
            # Fewer rows than asked for means the scan reached the end of the
            # embeddings, so a deeper reach cannot find anything more.
            if len(active) >= k or len(candidates) < limit:
                return active[:k]
        return await _ranked_active_items(
            self._query, query_vector, model_id, k, node_type
        )

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
