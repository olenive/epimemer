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
)


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


def _serialize(model) -> dict:
    """Serialize a Pydantic model to a dict suitable for SurrealDB.

    Renames 'id' to 'uid' to avoid conflicting with SurrealDB's built-in id.
    """
    data = model.model_dump(mode="json")
    data["uid"] = data.pop("id")
    return data


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
        await self.db.use(self._namespace, database)
        self._database = database
        await self._setup_schema()

    async def delete_database(self, database: str) -> None:
        """Delete a database from the current namespace."""
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

    @property
    def db(self) -> AsyncSurreal:
        if self._db is None:
            raise RuntimeError("Not connected. Call connect() first.")
        return self._db

    # --- Documents ---

    async def store_document(self, doc: RawDocument) -> str:
        data = _serialize(doc)
        await self.db.query("INSERT INTO document $data", {"data": data})
        return doc.id

    async def get_document(self, doc_id: str) -> RawDocument | None:
        rows = await self.db.query(
            "SELECT * FROM document WHERE uid = $uid LIMIT 1",
            {"uid": doc_id},
        )
        if not rows:
            return None
        return RawDocument.model_validate(_clean_record(rows[0]))

    # --- Segments ---

    async def store_segment(self, segment: Segment) -> str:
        data = _serialize(segment)
        await self.db.query("INSERT INTO segment $data", {"data": data})
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
        await self.db.query(f"INSERT INTO {table} $data", {"data": data})
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

    # --- Edges ---

    async def store_edge(self, edge: NodeEdge) -> str:
        data = _serialize(edge)
        data["type"] = edge.type.value
        await self.db.query("INSERT INTO node_edge $data", {"data": data})
        return edge.id

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

    # --- Embeddings ---

    async def store_embedding(self, embedding: EmbeddingRecord) -> str:
        data = _serialize(embedding)
        await self.db.query("INSERT INTO embedding $data", {"data": data})
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
        if node_type is not None:
            table = _NODE_TYPE_TO_TABLE[node_type]
            type_filter = (
                f"AND item_id IN (SELECT VALUE uid FROM {table} WHERE status = 'active')"
            )
        else:
            type_filter = ""

        rows = await self.db.query(
            f"""
            SELECT
                item_id,
                vector::similarity::cosine(vector, $query_vector) AS score
            FROM embedding
            WHERE model_id = $model_id {type_filter}
            ORDER BY score DESC
            LIMIT $k
            """,
            {"query_vector": query_vector, "model_id": model_id, "k": k},
        )
        return [(r["item_id"], r["score"]) for r in rows]

    # --- Timelines ---

    async def store_timeline(self, timeline: Timeline) -> str:
        data = _serialize(timeline)
        await self.db.query("INSERT INTO timeline $data", {"data": data})
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
        await self.db.query("INSERT INTO metacontext $data", {"data": data})
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
