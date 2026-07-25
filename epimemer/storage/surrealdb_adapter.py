"""SurrealDB storage backend.

Implements the StorageBackend protocol using SurrealDB.
Supports ws://, http://, and mem:// (embedded) connections.

Note: We use 'uid' as our application-level ID field to avoid conflicts
with SurrealDB's built-in 'id' field (which uses RecordID type).
"""

from datetime import datetime
from typing import Sequence

from surrealdb import AsyncSurreal

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
from epimemer.storage.protocol import drop_none_values, validate_graph_name


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


def _upsert(table: str) -> str:
    """SurrealQL to upsert a row keyed on the application id (``uid``).

    `INSERT INTO` is *silently ignored* when the UNIQUE index on `uid` is
    violated — no error, no update — which makes every re-store a no-op.
    `UPSERT ... WHERE` inserts when nothing matches and replaces the content
    when a row does, keeping the generated record id stable so records written
    before this changed keep working.
    """
    return f"UPSERT {table} CONTENT $data WHERE uid = $uid"


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


def _reflect_count(rows) -> int:
    """Read the counter out of a reflect-state row set.

    A graph that has never stored anything yields no row (SELECT) or a null one
    (RETURN BEFORE on the create path); both mean zero.
    """
    if not rows or rows[0] is None:
        return 0
    return int(rows[0].get(_REFLECT_FIELD) or 0)


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

    async def connect(self) -> None:
        self._db = AsyncSurreal(self._url)
        await self._db.connect(self._url)
        if not self._url.startswith("mem://"):
            await self._db.signin({"username": self._user, "password": self._password})
        await self._db.use(self._namespace, self._database)
        await self._setup_schema()

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
        result = await self.db.query("INFO FOR NS;")
        # SurrealDB returns a dict with "databases" key
        databases = result.get("databases", {}) if isinstance(result, dict) else {}
        return sorted(databases.keys())

    async def switch_database(self, database: str) -> None:
        """Switch to a different database and set up its schema."""
        validate_graph_name(database)
        await self.db.use(self._namespace, database)
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
        await self.db.query(f"REMOVE DATABASE IF EXISTS `{database}`;")

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
        original_db = self._database
        try:
            await self.db.use(self._namespace, database)
            results = []
            for table in ("topic", "fact", "inference"):
                rows = await self.db.query(
                    f"SELECT * FROM {table} WHERE status = $status",
                    {"status": historical_status.value},
                )
                results.extend(_record_to_node(table, r) for r in rows)
            return results
        finally:
            await self.db.use(self._namespace, original_db)

    async def viz_list_edges(
        self,
        database: str,
    ) -> Sequence[NodeEdge]:
        """List all edges in a graph for visualization snapshot."""
        original_db = self._database
        try:
            await self.db.use(self._namespace, database)
            rows = await self.db.query("SELECT * FROM node_edge")
            return [NodeEdge.model_validate(_clean_record(r)) for r in rows]
        finally:
            await self.db.use(self._namespace, original_db)

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def _setup_schema(self) -> None:
        """Define tables and indexes. Idempotent."""
        await self.db.query("""
            DEFINE TABLE IF NOT EXISTS document SCHEMALESS;
            DEFINE INDEX IF NOT EXISTS idx_document_uid ON document FIELDS uid UNIQUE;
        """)
        await self.db.query("""
            DEFINE TABLE IF NOT EXISTS segment SCHEMALESS;
            DEFINE INDEX IF NOT EXISTS idx_segment_uid ON segment FIELDS uid UNIQUE;
            DEFINE INDEX IF NOT EXISTS idx_segment_source ON segment FIELDS source_id;
        """)

        for table in ("topic", "fact", "inference"):
            await self.db.query(f"""
                DEFINE TABLE IF NOT EXISTS {table} SCHEMALESS;
                DEFINE INDEX IF NOT EXISTS idx_{table}_uid ON {table} FIELDS uid UNIQUE;
                DEFINE INDEX IF NOT EXISTS idx_{table}_status ON {table} FIELDS status;
            """)

        await self.db.query("""
            DEFINE TABLE IF NOT EXISTS node_edge SCHEMALESS;
            DEFINE INDEX IF NOT EXISTS idx_edge_uid ON node_edge FIELDS uid UNIQUE;
            DEFINE INDEX IF NOT EXISTS idx_edge_src ON node_edge FIELDS src_id;
            DEFINE INDEX IF NOT EXISTS idx_edge_dst ON node_edge FIELDS dst_id;
            DEFINE INDEX IF NOT EXISTS idx_edge_src_type ON node_edge FIELDS src_id, type;
            DEFINE INDEX IF NOT EXISTS idx_edge_dst_type ON node_edge FIELDS dst_id, type;
        """)

        await self.db.query("""
            DEFINE TABLE IF NOT EXISTS embedding SCHEMALESS;
            DEFINE INDEX IF NOT EXISTS idx_emb_uid ON embedding FIELDS uid UNIQUE;
            DEFINE INDEX IF NOT EXISTS idx_emb_item ON embedding FIELDS item_id;
            DEFINE INDEX IF NOT EXISTS idx_emb_model ON embedding FIELDS model_id;
        """)

        await self.db.query("""
            DEFINE TABLE IF NOT EXISTS timeline SCHEMALESS;
            DEFINE INDEX IF NOT EXISTS idx_timeline_uid ON timeline FIELDS uid UNIQUE;
        """)

        await self.db.query("""
            DEFINE TABLE IF NOT EXISTS metacontext SCHEMALESS;
            DEFINE INDEX IF NOT EXISTS idx_mc_uid ON metacontext FIELDS uid UNIQUE;
            DEFINE INDEX IF NOT EXISTS idx_mc_status ON metacontext FIELDS status;
        """)

        # Per-graph bookkeeping (reflection counter). Addressed by fixed record
        # id, so it needs no uid index.
        await self.db.query("""
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
        await self.db.query(_upsert("document"), {"data": data, "uid": doc.id})
        return doc.id

    async def get_document(self, doc_id: str) -> RawDocument | None:
        rows = await self.db.query(
            "SELECT * FROM document WHERE uid = $uid LIMIT 1",
            {"uid": doc_id},
        )
        if not rows:
            return None
        return RawDocument.model_validate(_clean_record(rows[0]))

    async def get_document_by_source(self, source: str) -> RawDocument | None:
        rows = await self.db.query(
            "SELECT * FROM document WHERE source = $source LIMIT 1",
            {"source": source},
        )
        if not rows:
            return None
        return RawDocument.model_validate(_clean_record(rows[0]))

    # --- Segments ---

    async def store_segment(self, segment: Segment) -> str:
        data = _serialize(segment)
        await self.db.query(_upsert("segment"), {"data": data, "uid": segment.id})
        return segment.id

    async def get_segments_for_document(self, doc_id: str) -> Sequence[Segment]:
        rows = await self.db.query(
            "SELECT * FROM segment WHERE source_id = $source_id ORDER BY span_start",
            {"source_id": doc_id},
        )
        return [Segment.model_validate(_clean_record(r)) for r in rows]

    # --- Epistemic Nodes ---

    async def store_node(self, node: EpistemicNode) -> str:
        table = _node_to_table(node)
        data = _serialize(node)
        await self.db.query(_upsert(table), {"data": data, "uid": node.id})
        return node.id

    async def get_node(self, node_id: str) -> EpistemicNode | None:
        for table in ("topic", "fact", "inference"):
            rows = await self.db.query(
                f"SELECT * FROM {table} WHERE uid = $uid LIMIT 1",
                {"uid": node_id},
            )
            if rows:
                return _record_to_node(table, rows[0])
        return None

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
                rows = await self.db.query(
                    f"SELECT * FROM {table} WHERE status = $status",
                    {"status": status.value},
                )
            else:
                rows = await self.db.query(
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
            rows = await self.db.query(
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
            rows = await self.db.query(
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
            rows = await self.db.query(
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
        rows = await self.db.query(
            "UPDATE node_edge SET label = $new WHERE type = $related AND label = $old "
            "RETURN BEFORE",
            {"new": new_label, "old": old_label, "related": EdgeType.RELATED.value},
        )
        return len(rows)

    async def get_relation_kind(self, label: str) -> str | None:
        rows = await self.db.query(
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
            rows = await self.db.query(
                f"SELECT count() AS c FROM {table} WHERE status = $status GROUP ALL",
                {"status": status.value},
            )
            counts[node_type] = rows[0]["c"] if rows else 0
        return counts

    # --- Edges ---

    async def store_edge(self, edge: NodeEdge) -> str:
        data = _serialize(edge)
        data["type"] = edge.type.value
        await self.db.query(_upsert("node_edge"), {"data": data, "uid": edge.id})
        return edge.id

    async def delete_edge(self, edge_id: str) -> None:
        await self.db.query(
            "DELETE node_edge WHERE uid = $uid",
            {"uid": edge_id},
        )

    async def get_edges_from(
        self, node_id: str, *, edge_type: EdgeType | None = None
    ) -> Sequence[NodeEdge]:
        if edge_type is None:
            rows = await self.db.query(
                "SELECT * FROM node_edge WHERE src_id = $src_id",
                {"src_id": node_id},
            )
        else:
            rows = await self.db.query(
                "SELECT * FROM node_edge WHERE src_id = $src_id AND type = $type",
                {"src_id": node_id, "type": edge_type.value},
            )
        return [NodeEdge.model_validate(_clean_record(r)) for r in rows]

    async def get_edges_to(
        self, node_id: str, *, edge_type: EdgeType | None = None
    ) -> Sequence[NodeEdge]:
        if edge_type is None:
            rows = await self.db.query(
                "SELECT * FROM node_edge WHERE dst_id = $dst_id",
                {"dst_id": node_id},
            )
        else:
            rows = await self.db.query(
                "SELECT * FROM node_edge WHERE dst_id = $dst_id AND type = $type",
                {"dst_id": node_id, "type": edge_type.value},
            )
        return [NodeEdge.model_validate(_clean_record(r)) for r in rows]

    async def count_edges_by_type(self) -> dict[EdgeType, int]:
        counts = {et: 0 for et in EdgeType}
        rows = await self.db.query(
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
        resp = await self.db.query_raw(sql, params)

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
            "status": NodeStatus.SUPERSEDED.value,
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
            "status": NodeStatus.SUPERSEDED.value,
            "sup_at": superseded_at.isoformat(),
            "old_uid": old_node.id,
            "lineage_data": _edge_row(lineage_edge),
        }
        self._append_review_writes(statements, params, evidence_edges, clear_edge_ids)
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

        if embeddings:
            statements.append("INSERT INTO embedding $embedding_rows")
            params["embedding_rows"] = [_serialize(e) for e in embeddings]

        if not statements:
            return
        await self._run_transaction(statements, params)

    # --- Embeddings ---

    async def store_embedding(self, embedding: EmbeddingRecord) -> str:
        data = _serialize(embedding)
        await self.db.query(_upsert("embedding"), {"data": data, "uid": embedding.id})
        return embedding.id

    async def get_embeddings_for_item(
        self, item_id: str, model_id: str | None = None
    ) -> Sequence[EmbeddingRecord]:
        if model_id is None:
            rows = await self.db.query(
                "SELECT * FROM embedding WHERE item_id = $item_id",
                {"item_id": item_id},
            )
        else:
            rows = await self.db.query(
                "SELECT * FROM embedding WHERE item_id = $item_id AND model_id = $model_id",
                {"item_id": item_id, "model_id": model_id},
            )
        return [EmbeddingRecord.model_validate(_clean_record(r)) for r in rows]

    async def vector_search(
        self,
        query_vector: list[float],
        model_id: str,
        *,
        k: int = 10,
        node_type: NodeType | None = None,
    ) -> Sequence[tuple[str, float]]:
        # TODO: When SurrealDB adds native HNSW vector indexes, switch to those.
        # For now, brute-force via SurrealQL vector::similarity::cosine().
        #
        # Both paths restrict results to *active* nodes: superseded/merged nodes
        # must never resurface via vector search. The typed path scopes to one
        # node table; the untyped path spans all three.
        if node_type is not None:
            table = _NODE_TYPE_TO_TABLE[node_type]
            active_filter = (
                f"AND item_id IN (SELECT VALUE uid FROM {table} WHERE status = 'active')"
            )
        else:
            active_filter = (
                "AND item_id IN "
                "(SELECT VALUE uid FROM topic, fact, inference WHERE status = 'active')"
            )

        rows = await self.db.query(
            f"""
            SELECT
                item_id,
                vector::similarity::cosine(vector, $query_vector) AS score
            FROM embedding
            WHERE model_id = $model_id {active_filter}
            ORDER BY score DESC
            LIMIT $k
            """,
            {"query_vector": query_vector, "model_id": model_id, "k": k},
        )
        return [(r["item_id"], r["score"]) for r in rows]

    # --- Timelines ---

    async def store_timeline(self, timeline: Timeline) -> str:
        data = _serialize(timeline)
        await self.db.query(_upsert("timeline"), {"data": data, "uid": timeline.id})
        return timeline.id

    async def get_timeline(self, timeline_id: str) -> Timeline | None:
        rows = await self.db.query(
            "SELECT * FROM timeline WHERE uid = $uid LIMIT 1",
            {"uid": timeline_id},
        )
        if not rows:
            return None
        return Timeline.model_validate(_clean_record(rows[0]))

    async def query_timelines(self) -> Sequence[Timeline]:
        rows = await self.db.query("SELECT * FROM timeline")
        return [Timeline.model_validate(_clean_record(r)) for r in rows]

    # --- Metacontexts ---

    async def store_metacontext(self, mc: Metacontext) -> str:
        data = _serialize(mc)
        await self.db.query(_upsert("metacontext"), {"data": data, "uid": mc.id})
        return mc.id

    async def get_metacontext(self, mc_id: str) -> Metacontext | None:
        rows = await self.db.query(
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
        rows = await self.db.query(
            "SELECT * FROM metacontext WHERE status = $status",
            {"status": status.value},
        )
        return [Metacontext.model_validate(_clean_record(r)) for r in rows]

    # --- Reflection bookkeeping ---

    async def get_reflect_counter(self) -> int:
        rows = await self.db.query(_REFLECT_GET)
        return _reflect_count(rows)

    async def bump_reflect_counter(self) -> int:
        rows = await self.db.query(_REFLECT_BUMP)
        return _reflect_count(rows)

    async def reset_reflect_counter(self) -> int:
        rows = await self.db.query(_REFLECT_RESET)
        return _reflect_count(rows)
