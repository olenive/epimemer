"""Opt-in integration tests against a real SurrealDB server over ws://.

These exercise things the embedded ``mem://`` backend cannot: real
connection/auth, and transaction atomicity under *genuine* concurrency (separate
ws connections issuing transactions at the same time). They are SKIPPED unless a
server is reachable — they are not a CI gate.

To run, start a server and point the env var at it:

    # native:
    #   surreal start --user root --pass root memory
    # or Docker:
    #   docker run --rm -p 8000:8000 surrealdb/surrealdb:latest \
    #     start --user root --pass root memory

    EPIMEMER_SURREAL_WS_URL=ws://localhost:8000/rpc \
        uv run pytest tests/storage/test_surrealdb_integration.py

If ``EPIMEMER_SURREAL_WS_URL`` is unset or the server is unreachable, the whole
module is skipped (no connection is attempted by default).
"""

import asyncio
import os
import uuid

import pytest

from epimemer.core.types import (
    EdgeType,
    EmbeddingRecord,
    Fact,
    NodeStatus,
    NodeType,
    NodeEdge,
    Topic,
)
from epimemer.storage.surrealdb_adapter import SurrealDBStorage


WS_URL = os.environ.get("EPIMEMER_SURREAL_WS_URL")
WS_USER = os.environ.get("EPIMEMER_SURREAL_USER", "root")
WS_PASS = os.environ.get("EPIMEMER_SURREAL_PASS", "root")
WS_NS = os.environ.get("EPIMEMER_SURREAL_NS", "epimemer_it")


def _make_store(database: str) -> SurrealDBStorage:
    return SurrealDBStorage(
        url=WS_URL, user=WS_USER, password=WS_PASS,
        namespace=WS_NS, database=database,
    )


def _server_reachable() -> bool:
    """True only if a ws:// server actually accepts a connection.

    Returns False immediately when the env var is unset, so the default (and CI)
    path attempts no connection at all.
    """
    if not WS_URL:
        return False

    async def _probe() -> bool:
        store = _make_store("reachability_probe")
        try:
            await asyncio.wait_for(store.connect(), timeout=3.0)
            await store.close()
            return True
        except Exception:
            return False

    try:
        return asyncio.run(_probe())
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _server_reachable(),
    reason="No reachable SurrealDB ws:// server (set EPIMEMER_SURREAL_WS_URL).",
)


@pytest.fixture
def db_name() -> str:
    """A unique throwaway database name per test (isolates concurrent runs)."""
    return f"it_{uuid.uuid4().hex[:12]}"


@pytest.fixture
async def surreal(db_name):
    """Factory yielding freshly-connected stores on this test's unique database.

    Every store handed out is closed at teardown, and the throwaway database is
    dropped. Open the connections you need *sequentially* (awaiting the factory)
    before firing concurrent work, so transaction concurrency — not schema DDL —
    is what gets probed.
    """
    opened: list[SurrealDBStorage] = []

    async def factory() -> SurrealDBStorage:
        store = _make_store(db_name)
        await store.connect()
        opened.append(store)
        return store

    yield factory

    if opened:
        try:
            await opened[0].delete_database(db_name)
        except Exception:
            pass
    for store in opened:
        try:
            await store.close()
        except Exception:
            pass


# --- Connection / auth ---


async def test_connection_auth_and_roundtrip(surreal):
    """Signin + use namespace/db + schema setup + a CRUD round-trip over ws."""
    store = await surreal()
    topic = Topic(content="hello over websocket", source_id="s1")
    await store.store_node(topic)

    got = await store.get_node(topic.id)
    assert isinstance(got, Topic)
    assert got.content == "hello over websocket"


# --- Transaction atomicity under genuine concurrency ---


async def test_concurrent_write_batches_all_commit(surreal):
    """Separate connections committing batches at once: no lost or torn writes."""
    n = 8
    verifier = await surreal()
    stores = [await surreal() for _ in range(n)]  # sequential connects (no DDL race)

    async def writer(store: SurrealDBStorage, i: int) -> str:
        topic = Topic(content=f"node-{i}", source_id="s1")
        fact = Fact(content=f"fact-{i}", source_id="s1")
        edge = NodeEdge(src_id=fact.id, dst_id=topic.id, type=EdgeType.SUPPORTS)
        emb = EmbeddingRecord(item_id=topic.id, model_id="m", vector=[float(i), 1.0])
        await store.write_batch_tx(nodes=[topic, fact], edges=[edge], embeddings=[emb])
        return topic.id

    topic_ids = await asyncio.gather(
        *[writer(store, i) for i, store in enumerate(stores)]
    )

    # Every batch landed in full.
    for tid in topic_ids:
        assert await verifier.get_node(tid) is not None
        assert len(await verifier.get_edges_to(tid, edge_type=EdgeType.SUPPORTS)) == 1
        assert len(await verifier.get_embeddings_for_item(tid)) == 1
    topics = await verifier.query_nodes(node_type=NodeType.TOPIC)
    assert len(topics) == n


async def test_failed_batch_rolls_back_under_concurrency(surreal):
    """A colliding batch aborts fully while concurrent good batches all commit."""
    verifier = await surreal()
    # Pre-squat a uid so one concurrent batch hits a unique-index collision.
    squatter = Topic(content="squatter", source_id="s1")
    await verifier.store_node(squatter)

    good_stores = [await surreal() for _ in range(3)]
    bad_store = await surreal()

    async def good(store: SurrealDBStorage, i: int) -> str:
        topic = Topic(content=f"good-{i}", source_id="s1")
        await store.write_batch_tx(nodes=[topic])
        return topic.id

    async def bad() -> str:
        fresh = Topic(content="fresh-partner", source_id="s1")
        collide = Topic(id=squatter.id, content="dupe", source_id="s1")
        with pytest.raises(Exception):
            await bad_store.write_batch_tx(nodes=[fresh, collide])
        return fresh.id

    *good_ids, bad_fresh_id = await asyncio.gather(
        good(good_stores[0], 0),
        good(good_stores[1], 1),
        good(good_stores[2], 2),
        bad(),
    )

    # The good batches committed...
    for gid in good_ids:
        assert await verifier.get_node(gid) is not None
    # ...and the failed batch left nothing behind (full rollback of its partner).
    assert await verifier.get_node(bad_fresh_id) is None


async def test_concurrent_supersede_distinct_nodes(surreal):
    """Concurrent supersede transactions on distinct nodes all apply cleanly."""
    n = 6
    verifier = await surreal()

    # Seed the old nodes sequentially.
    olds = [Topic(content=f"old-{i}", source_id="s1") for i in range(n)]
    for old in olds:
        await verifier.store_node(old)

    stores = [await surreal() for _ in range(n)]

    async def supersede(store: SurrealDBStorage, old: Topic) -> str:
        new = Topic(content=f"new-for-{old.content}", source_id="s1")
        emb = EmbeddingRecord(item_id=new.id, model_id="m", vector=[1.0, 0.0])
        lineage = NodeEdge(src_id=old.id, dst_id=new.id, type=EdgeType.SUPERSEDED_BY)
        from datetime import datetime, timezone
        await store.supersede_node_tx(
            old, new, emb, lineage, superseded_at=datetime.now(timezone.utc)
        )
        return new.id

    new_ids = await asyncio.gather(
        *[supersede(store, old) for store, old in zip(stores, olds)]
    )

    for old, new_id in zip(olds, new_ids):
        assert (await verifier.get_node(old.id)).status == NodeStatus.SUPERSEDED
        assert await verifier.get_node(new_id) is not None
        lineage = await verifier.get_edges_from(old.id, edge_type=EdgeType.SUPERSEDED_BY)
        assert len(lineage) == 1 and lineage[0].dst_id == new_id
