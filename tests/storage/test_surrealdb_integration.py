"""Opt-in integration tests against a real SurrealDB server over ws://.

These exercise things the embedded ``mem://`` backend cannot: real
connection/auth, and transaction atomicity under *genuine* concurrency (separate
ws connections issuing transactions at the same time). They are SKIPPED unless a
server is reachable — they are not a CI gate.

Normally you want ``make test-integration``, which starts a server, waits for
it, runs this module and the durability one, and tears the server down. Add
``SURREAL_PORT=8123`` if it reports port 8000 in use.

To drive it by hand, start a server and point the env var at it:

    # native:
    #   surreal start --user root --pass root memory
    # or Docker:
    #   docker run --rm -p 8000:8000 surrealdb/surrealdb:latest \
    #     start --user root --pass root memory

    EPIMEMER_SURREAL_WS_URL=ws://127.0.0.1:8000/rpc \
        uv run pytest tests/storage/test_surrealdb_integration.py

If ``EPIMEMER_SURREAL_WS_URL`` is unset or the server is unreachable, the whole
module is skipped (no connection is attempted by default) — and pytest reports
skips as success, so check for ``13 passed`` rather than trusting the exit code.
A server that accepts connections without answering (another Docker/Colima
profile holding the port, or a wedged container) reads as unreachable here.
"""

import asyncio
import os
import uuid
from datetime import UTC, datetime

import pytest

from epimemer.core.temporal import (
    IntervalBasis,
    NamedInstant,
    PreciseInstant,
    UnboundedInstant,
    UnknownInstant,
    ValidityInterval,
)
from epimemer.core.types import (
    EdgeType,
    EmbeddingRecord,
    Fact,
    NodeEdge,
    NodeStatus,
    NodeType,
    RawDocument,
    Topic,
)
from epimemer.storage.surrealdb_adapter import SurrealDBStorage

WS_URL = os.environ.get("EPIMEMER_SURREAL_WS_URL")
WS_USER = os.environ.get("EPIMEMER_SURREAL_USER", "root")
WS_PASS = os.environ.get("EPIMEMER_SURREAL_PASS", "root")
WS_NS = os.environ.get("EPIMEMER_SURREAL_NS", "epimemer_it")


def _make_store(database: str) -> SurrealDBStorage:
    return SurrealDBStorage(
        url=WS_URL,
        user=WS_USER,
        password=WS_PASS,
        namespace=WS_NS,
        database=database,
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

    topic_ids = await asyncio.gather(*[writer(store, i) for i, store in enumerate(stores)])

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
        # Blind on purpose: the storage protocol names no exception type, and the
        # two backends refuse with different ones.
        with pytest.raises(Exception):  # noqa: B017
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
        from datetime import datetime

        await store.supersede_node_tx(
            old,
            new,
            emb,
            lineage,
            status=NodeStatus.CORRECTED,
            superseded_at=datetime.now(UTC),
        )
        return new.id

    new_ids = await asyncio.gather(
        *[supersede(store, old) for store, old in zip(stores, olds, strict=True)]
    )

    for old, new_id in zip(olds, new_ids, strict=True):
        assert (await verifier.get_node(old.id)).status == NodeStatus.CORRECTED
        assert await verifier.get_node(new_id) is not None
        lineage = await verifier.get_edges_from(old.id, edge_type=EdgeType.SUPERSEDED_BY)
        assert len(lineage) == 1 and lineage[0].dst_id == new_id


async def test_archival_flip_rolls_back_over_a_real_connection(surreal):
    """The archival flip's guard is SurrealQL that `mem://` alone cannot vouch for.

    A missing row makes `UPDATE ... WHERE uid = $uid` a silent no-op, so the
    transaction throws instead — and that THROW is parsed and executed by the
    server, not the embedded engine. This is the test that says it works where
    it actually runs.
    """
    from datetime import datetime

    store = await surreal()
    verifier = await surreal()

    nodes = [Fact(content=f"trivial-{i}", source_id="s1") for i in range(3)]
    for node in nodes:
        await store.store_node(node)
    missing = Fact(content="never stored", source_id="s1")

    # Blind on purpose: the storage protocol names no exception type, and the
    # two backends refuse with different ones.
    with pytest.raises(Exception):  # noqa: B017
        await store.set_node_status_tx(
            [*nodes, missing],
            status=NodeStatus.ARCHIVED,
            at=datetime.now(UTC),
        )

    for node in nodes:
        assert (await verifier.get_node(node.id)).status == NodeStatus.ACTIVE

    # ...and the same call without the missing node commits every flip.
    await store.set_node_status_tx(nodes, status=NodeStatus.ARCHIVED, at=datetime.now(UTC))
    for node in nodes:
        assert (await verifier.get_node(node.id)).status == NodeStatus.ARCHIVED


async def test_lifecycle_episodes_round_trip_over_a_real_connection(surreal):
    """The episode list survives the server, not only the embedded engine.

    The two are not the same SurrealDB: the embedded one has no `object::`
    functions at all, which is why the history is planned in Python rather than
    appended to in SurrealQL. A nested array of objects going out through one
    connection and coming back through another is the part `mem://` cannot
    vouch for.
    """
    from datetime import datetime

    store = await surreal()
    verifier = await surreal()

    old = Fact(content="the earlier claim", source_id="s1")
    new = Fact(content="what replaced it", source_id="s1")
    await store.store_node(old)
    at = datetime(2026, 6, 15, tzinfo=UTC)
    await store.supersede_node_tx(
        old,
        new,
        EmbeddingRecord(item_id=new.id, model_id="test", vector=[1.0, 0.0, 0.0]),
        NodeEdge(src_id=old.id, dst_id=new.id, type=EdgeType.TEMPORALLY_FOLLOWED_BY),
        status=NodeStatus.HISTORICAL,
        superseded_at=at,
    )

    retired = await verifier.get_node(old.id)
    assert [(e.retired_at, e.because, e.counterpart) for e in retired.lifecycle] == [
        (at, NodeStatus.HISTORICAL, new.id)
    ]

    came_back = datetime(2026, 6, 20, tzinfo=UTC)
    await store.set_node_status_tx(
        [retired],
        status=NodeStatus.ACTIVE,
        at=came_back,
    )

    back = await verifier.get_node(old.id)
    assert back.status is NodeStatus.ACTIVE
    assert back.superseded_at is None
    assert len(back.lifecycle) == 1
    assert back.lifecycle[0].restored_at == came_back

    # The window predicate is SurrealQL the server parses for itself, and the
    # retirement it has to find is no longer in `superseded_at`.
    changed = await verifier.query_changes(
        start=datetime(2026, 6, 14, tzinfo=UTC),
        end=datetime(2026, 6, 16, tzinfo=UTC),
    )
    assert old.id in {n.id for n in changed}


async def test_reactivation_writes_the_flip_and_its_edge_together(surreal):
    """One transaction over a real connection, read back through another.

    The embedded engine cannot vouch for this: the statement that carries the
    provenance edge is appended to the same `BEGIN…COMMIT` batch as the status
    flip and the lifecycle rewrite, and what must never be observable is a node
    back to ACTIVE with no edge saying what asserted it again.
    """
    store = await surreal()
    verifier = await surreal()

    fact = Fact(content="Labour is in government", source_id="s1")
    document = RawDocument(content="the 2024 result", source="almanac")
    await store.store_node(fact)
    await store.store_document(document)
    retired = datetime(2010, 5, 11, tzinfo=UTC)
    await store.set_node_status_tx(
        [fact],
        status=NodeStatus.HISTORICAL,
        at=retired,
    )

    came_back = datetime(2024, 7, 5, tzinfo=UTC)
    await store.set_node_status_tx(
        [await verifier.get_node(fact.id)],
        status=NodeStatus.ACTIVE,
        at=came_back,
        edges=[
            NodeEdge(
                src_id=fact.id,
                dst_id=document.id,
                type=EdgeType.SOURCED_FROM,
                validity=[
                    ValidityInterval(
                        start=PreciseInstant(at=came_back),
                        basis=IntervalBasis.STATED,
                    )
                ],
            )
        ],
    )

    back = await verifier.get_node(fact.id)
    assert back.status is NodeStatus.ACTIVE
    assert back.superseded_at is None
    # Cycles are legal for this claim, so the retirement has to survive its own
    # reversal — a history that forgets it cannot describe the next one.
    assert [(e.because, e.restored_at) for e in back.lifecycle] == [
        (NodeStatus.HISTORICAL, came_back)
    ]

    edges = await verifier.get_edges_from(fact.id, edge_type=EdgeType.SOURCED_FROM)
    assert [e.dst_id for e in edges] == [document.id]
    assert edges[0].validity[0].start.at == came_back


async def test_vector_search_status_filter_over_a_real_connection(surreal):
    """`status IN $statuses` is SurrealQL the server parses for itself.

    The parameter is bound rather than interpolated, and the over-fetch path and
    the exact fallback have to agree about it — a filter that only worked on one
    of the two would show up as candidates appearing or vanishing with `k`.
    """
    store = await surreal()
    verifier = await surreal()

    by_status = {}
    for status in (NodeStatus.ACTIVE, NodeStatus.HISTORICAL, NodeStatus.CORRECTED):
        fact = Fact(content=f"a claim, {status.value}", source_id="s1", status=status)
        await store.store_node(fact)
        await store.store_embedding(
            EmbeddingRecord(
                item_id=fact.id,
                model_id="test",
                vector=[1.0, 0.0, 0.0],
            )
        )
        by_status[status] = fact

    assert [i for i, _ in await verifier.vector_search([1.0, 0.0, 0.0], "test", k=10)] == [
        by_status[NodeStatus.ACTIVE].id
    ]

    nominated = await verifier.vector_search(
        [1.0, 0.0, 0.0],
        "test",
        k=10,
        statuses=frozenset({NodeStatus.ACTIVE, NodeStatus.HISTORICAL}),
    )
    assert {i for i, _ in nominated} == {
        by_status[NodeStatus.ACTIVE].id,
        by_status[NodeStatus.HISTORICAL].id,
    }


async def test_validity_intervals_round_trip_over_a_real_connection(surreal):
    """Nested unions on an edge survive the server, not only the embedded engine.

    Validity is a list of models whose endpoints are a discriminated union, sent
    out through one connection and read back through another. The failure worth
    catching is not loss but *substitution*: a discriminator that did not
    survive would hand back a different endpoint state, turning "we do not know
    when this started" into "it has no start" — the one distinction the type
    exists to keep (the validity model §4).
    """
    store = await surreal()
    verifier = await surreal()

    fact = Fact(content="the city is called Leningrad", source_id="s1")
    document = RawDocument(
        content="a 1970 memoir",
        published_at=PreciseInstant(at=datetime(1970, 6, 1, tzinfo=UTC)),
    )
    await store.store_node(fact)
    await store.store_document(document)
    intervals = [
        ValidityInterval(
            start=UnknownInstant(),
            end=PreciseInstant(at=datetime(1991, 9, 6, tzinfo=UTC)),
            witnessed_at=PreciseInstant(at=datetime(1970, 1, 1, tzinfo=UTC)),
            basis=IntervalBasis.INFERRED,
        ),
        ValidityInterval(
            start=NamedInstant(label="during the Renaissance"),
            end=UnboundedInstant(),
            timeline_id="in-universe",
            basis=IntervalBasis.STATED,
        ),
    ]
    await store.store_edge(
        NodeEdge(
            src_id=fact.id,
            dst_id=document.id,
            type=EdgeType.SOURCED_FROM,
            validity=intervals,
        )
    )

    edges = await verifier.get_edges_from(fact.id, edge_type=EdgeType.SOURCED_FROM)
    assert len(edges) == 1
    assert edges[0].validity == intervals

    stored_document = await verifier.get_document(document.id)
    assert stored_document.published_at == document.published_at
    assert stored_document.created_at.year != 1970


# --- Lexical search ---


async def test_fts_index_is_defined_for_every_searchable_table(surreal):
    """Schema guard, and the one that says which SurrealDB we are talking to.

    The standalone server and the engine embedded in the Python SDK are
    different cores that reject each other's `DEFINE INDEX` syntax outright
    (`FULLTEXT` vs `SEARCH`), so `_setup_schema` negotiates. The unit suite only
    ever exercises the embedded half; without this, shipping the wrong dialect
    would leave every lexical search on a real deployment returning nothing,
    silently, while the whole suite stayed green.
    """
    store = await surreal()

    for table in ("topic", "fact", "inference", "segment"):
        info = await store._query(f"INFO FOR TABLE {table};")
        assert f"idx_{table}_fts" in info["indexes"], f"{table} has no full-text index"


async def test_text_search_discriminates_a_near_miss_over_a_real_connection(surreal):
    """The status gate and conjunctive matching, together, on the real engine.

    Apart is not the same as together. Adding any non-match predicate to a
    `WHERE` that ORs two match references makes 3.0.5 stop using the full-text
    index, and `@@` then matches a document holding *any* token of a term rather
    than all of them — so `JIRA-4417` starts returning `JIRA-4418`, at a
    positive score the zero-rule truncation does not catch. The embedded engine
    does not do this, so the unit suite cannot see it.
    """
    from epimemer.core.types import NodeType

    store = await surreal()
    verifier = await surreal()

    contents = [
        "Ticket JIRA-4417 was closed after the deployment rollback",
        "Ticket JIRA-4418 remains open pending the deployment review",
        "The deployment pipeline was rewritten last quarter",
        "Nothing here concerns tickets at all",
        "A note about the weather this morning",
    ]
    facts = [Fact(content=text, source_id="s1") for text in contents]
    for fact in facts:
        await store.store_node(fact)

    hits = await verifier.text_search(
        ["JIRA-4417", "zzzznotpresent"], corpus="nodes", node_type=NodeType.FACT
    )
    assert [node_id for node_id, _ in hits] == [facts[0].id]

    # The gate itself: a corrected claim holding the identifier stays out.
    await store.set_node_status_tx([facts[0]], status=NodeStatus.CORRECTED, at=datetime.now(UTC))
    assert await verifier.text_search(["JIRA-4417"], corpus="nodes", node_type=NodeType.FACT) == []


async def test_a_status_set_gates_without_losing_the_index(surreal):
    """`status IN $statuses` sits where `status = $status` did, and must behave.

    The gate went plural for history-by-default retrieval, so retrieval can ask
    both arms for history with one set. The predicate is the reason to check it
    on the real engine
    rather than only on the embedded one: this planner drops the full-text index
    when the wrong predicate joins the match references, and `@@` then matches
    any token of a term rather than all of them — which is `JIRA-4417` returning
    `JIRA-4418`, at a score the zero rule does not catch.
    """
    from epimemer.core.types import NodeType

    store = await surreal()
    verifier = await surreal()

    contents = [
        "Ticket JIRA-4417 was closed after the deployment rollback",
        "Ticket JIRA-4418 remains open pending the deployment review",
        "The deployment pipeline was rewritten last quarter",
        "Nothing here concerns tickets at all",
        "A note about the weather this morning",
    ]
    facts = [Fact(content=text, source_id="s1") for text in contents]
    for fact in facts:
        await store.store_node(fact)
    await store.set_node_status_tx([facts[0]], status=NodeStatus.HISTORICAL, at=datetime.now(UTC))

    hits = await verifier.text_search(
        ["JIRA-4417"],
        corpus="nodes",
        node_type=NodeType.FACT,
        statuses=frozenset({NodeStatus.ACTIVE, NodeStatus.HISTORICAL}),
    )
    assert [node_id for node_id, _ in hits] == [facts[0].id]

    # And the default set still refuses it, so the widening is opt-in.
    assert await verifier.text_search(["JIRA-4417"], corpus="nodes", node_type=NodeType.FACT) == []


async def test_containment_keeps_the_index_over_a_real_connection(surreal):
    """R8 on the engine whose planner is the reason R8 is checked in Python.

    The matched text rides back in the projection because a `WHERE` predicate is
    where it must not go: the containment check would disable the full-text
    index exactly as an inlined status gate does, `@@` would turn disjunctive,
    and the near-miss would return at a positive score. So this asserts what
    that failure would break — `JIRA-4418` stays out — as well as what
    containment adds: the document holding the literal identifier outranks one
    that merely holds its tokens, and the longer id is not a match for the
    shorter one.
    """
    from epimemer.core.types import NodeType

    store = await surreal()
    verifier = await surreal()

    contents = [
        "Ticket JIRA-4417 was closed after the deployment rollback",
        "JIRA - 4417 JIRA - 4417 quick note",
        "Ticket JIRA-4418 remains open pending the deployment review",
        "Ticket JIRA-44170 tracks the follow-up work",
        "A note about the weather this morning",
    ]
    facts = [Fact(content=text, source_id="s1") for text in contents]
    for fact in facts:
        await store.store_node(fact)

    hits = await verifier.text_search(
        ["JIRA-4417"],
        corpus="nodes",
        node_type=NodeType.FACT,
        verify_containment=True,
    )

    assert [node_id for node_id, _ in hits] == [facts[0].id, facts[1].id]


# --- The active graph, over a real connection ---


async def test_a_snapshot_borrow_waits_for_a_write_in_flight(surreal, db_name):
    """The deployment the hazard is actually reachable in.

    Embedded `mem://` proves the guard's logic; only a real connection proves
    the thing the guard is about — `USE ns db` is a message on the wire, so a
    borrow that overlapped a write would send that write's statements to another
    database on the server. Here the write completes where it started, and the
    selection is handed back.
    """
    store = await surreal()
    # A real graph to snapshot, schema and all: the borrow is what is under
    # test, not what an absent database answers.
    elsewhere = _make_store(f"{db_name}_elsewhere")
    await elsewhere.connect()

    inside, release = asyncio.Event(), asyncio.Event()
    written = Topic(content="written before the borrow")

    async def in_flight():
        async with store.graph_guard.using():
            inside.set()
            await release.wait()
            await store.store_node(written)

    call = asyncio.create_task(in_flight())
    await inside.wait()
    snapshot = asyncio.create_task(store.viz_list_nodes(f"{db_name}_elsewhere"))
    for _ in range(8):
        await asyncio.sleep(0)

    assert store._selected == db_name, "the connection was re-pointed mid-write"

    release.set()
    await call
    await asyncio.wait_for(snapshot, timeout=10)

    assert store._selected == db_name, "the borrow was not handed back"
    assert await store.get_node_by_content(written.content, node_type=NodeType.TOPIC) is not None

    await elsewhere.delete_database(f"{db_name}_elsewhere")
    await elsewhere.close()
