"""A graph whose keep verdicts were written twice migrates itself when opened.

A `retention` verdict used to land as a journal row whose anchors were prose at
the end of `certainty_basis`, plus one `review_confirmed` edge per anchor. The
row is now the whole verdict and the anchors live in `covers`, so schema version
3 parses the prose, recovers any edge that never got a row of its own, and drops
the edges.

The old shape is written straight at the connection: the enum no longer has a
member that produces those edges, and `record_decision` no longer writes the
prose, which is the point of the migration.

The ws:// copy lives in `test_surrealdb_integration.py` and calls
`assert_a_double_written_graph_migrates_itself` below, so the embedded and
remote paths are checked against one script.
"""

import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from epimemer.core.types import DecisionKind, DecisionRecord, Fact, Inference, JudgeRef
from epimemer.storage.surrealdb_adapter import SurrealDBStorage

REVIEWER = JudgeRef(agent_id="reviewer-1", digest="d1")
EDGE_AT = datetime(2026, 8, 29, 9, 30, tzinfo=UTC)


async def _confirmed_edge(store: SurrealDBStorage, src_id: str, dst_id: str) -> None:
    """One `review_confirmed` edge, under a type nothing can produce any more."""
    await store.db.query(
        "CREATE node_edge CONTENT {"
        "  uid: rand::uuid::v4(), src_id: $src, dst_id: $dst,"
        "  type: 'review_confirmed', judged_by: $judge, created_at: $at"
        "}",
        {
            "src": src_id,
            "dst": dst_id,
            "judge": REVIEWER.model_dump(mode="json"),
            "at": EDGE_AT.isoformat().replace("+00:00", "Z"),
        },
    )


async def _prose_retention(store: SurrealDBStorage, node_id: str, anchors: list[str]) -> str:
    """A retention row in the pre-`covers` shape: anchors spelled into the prose."""
    record = DecisionRecord(
        kind=DecisionKind.RETENTION,
        subject_ids=[node_id],
        judged_by=REVIEWER,
        certainty_basis=f"re-read; it holds [covers: {', '.join(anchors)}]",
    )
    await store.record_decision(record)
    # `covers` is a field of the current model, so the write above stored it
    # empty. A row written before the field existed had no key at all.
    await store.db.query("UPDATE decision UNSET covers WHERE uid = $uid", {"uid": record.id})
    return record.id


async def _seed_double_written_graph(store: SurrealDBStorage) -> dict[str, str]:
    """The three shapes a pre-migration graph holds."""
    stale_a = Fact(content="the deploy failed", source_id="seg-1")
    stale_b = Fact(content="the rollback held", source_id="seg-1")
    anchored = Inference(content="the release was rushed", source_id="seg-1")
    kept_for_itself = Fact(content="the licence file lists MPL-2.0", source_id="seg-1")
    orphaned = Fact(content="five distributions carry no licence metadata", source_id="seg-1")
    for node in (stale_a, stale_b, anchored, kept_for_itself, orphaned):
        await store.store_node(node)

    # (i) A real verdict: prose naming two anchors, and an edge for each.
    anchored_row = await _prose_retention(store, anchored.id, [stale_a.id, stale_b.id])
    await _confirmed_edge(store, stale_a.id, anchored.id)
    await _confirmed_edge(store, stale_b.id, anchored.id)

    # (ii) A node kept for its own sake: the anchor was the node itself, which
    #      rendered as a self-loop edge and as the node's own id in the prose.
    itself_row = await _prose_retention(store, kept_for_itself.id, [kept_for_itself.id])
    await _confirmed_edge(store, kept_for_itself.id, kept_for_itself.id)

    # (iii) The same self-loop with no row at all: the second write is what
    #       suppressed the nomination, so the verdict exists only as an edge.
    await _confirmed_edge(store, orphaned.id, orphaned.id)

    await store.db.query("DELETE schema_version:current")

    return {
        "stale_a": stale_a.id,
        "stale_b": stale_b.id,
        "anchored": anchored.id,
        "anchored_row": anchored_row,
        "kept_for_itself": kept_for_itself.id,
        "itself_row": itself_row,
        "orphaned": orphaned.id,
    }


async def _schema_version(store: SurrealDBStorage) -> int | None:
    stored = await store.db.query("SELECT VALUE version FROM schema_version:current")
    return int(stored[0]) if stored else None


async def _confirmed_edge_count(store: SurrealDBStorage) -> int:
    rows = await store.db.query("SELECT uid FROM node_edge WHERE type = 'review_confirmed'")
    return len(rows or [])


async def _retention_row(store: SurrealDBStorage, node_id: str) -> DecisionRecord:
    rows = await store.query_decisions(kinds=[DecisionKind.RETENTION], subject_ids=[node_id])
    assert len(rows) == 1
    return rows[0]


async def assert_a_double_written_graph_migrates_itself(open_store) -> None:
    """Seed a pre-`covers` graph, reopen it twice, and check what came back.

    `open_store` is an awaitable factory handing back a freshly connected store
    on one and the same graph, so this reads the same against an embedded file
    and against a ws:// server.
    """
    seeded = await open_store()
    ids = await _seed_double_written_graph(seeded)
    assert await _schema_version(seeded) is None
    assert await _confirmed_edge_count(seeded) == 4
    await seeded.close()

    reopened = await open_store()

    # (i) The prose parsed into `covers`, and the prose itself is untouched.
    anchored = await _retention_row(reopened, ids["anchored"])
    assert anchored.id == ids["anchored_row"]
    assert sorted(anchored.covers) == sorted([ids["stale_a"], ids["stale_b"]])
    assert anchored.certainty_basis.startswith("re-read; it holds [covers:")

    # (ii) The self-anchor became a keep for the node's own sake: nothing
    #      covered, because the nomination named no reason.
    itself = await _retention_row(reopened, ids["kept_for_itself"])
    assert itself.id == ids["itself_row"]
    assert itself.covers == []

    # (iii) The verdict that existed only as an edge is now a row.
    orphaned = await _retention_row(reopened, ids["orphaned"])
    assert orphaned.covers == []
    assert orphaned.judged_by == REVIEWER
    assert orphaned.decided_at == EDGE_AT
    assert "recovered from review_confirmed edges" in orphaned.certainty_basis

    assert await _confirmed_edge_count(reopened) == 0
    assert await _schema_version(reopened) == 4

    # And the nominator reads all three back, which is what the verdict is for.
    from epimemer.pipelines.reflection.retention import confirmed_reasons_for

    covered = await confirmed_reasons_for(
        [ids["anchored"], ids["kept_for_itself"], ids["orphaned"]], reopened
    )
    assert covered == {
        ids["anchored"]: {ids["stale_a"], ids["stale_b"]},
        ids["kept_for_itself"]: {ids["kept_for_itself"]},
        ids["orphaned"]: {ids["orphaned"]},
    }
    await reopened.close()

    # A second open changes nothing: the marker stops the steps running, and
    # they would rewrite nothing if they did.
    again = await open_store()
    assert await _retention_row(again, ids["anchored"]) == anchored
    assert await _retention_row(again, ids["kept_for_itself"]) == itself
    assert await _retention_row(again, ids["orphaned"]) == orphaned
    assert await _confirmed_edge_count(again) == 0
    assert await _schema_version(again) == 4
    await again.close()


@pytest.fixture
def embedded_url():
    """A URL for an embedded store that survives being closed and reopened.

    `mem://` cannot serve here: the graph lives inside the object, so closing it
    is what the migration is supposed to run after.
    """
    with tempfile.TemporaryDirectory() as directory:
        yield f"surrealkv://{Path(directory) / 'graph.db'}"


async def test_an_embedded_graph_migrates_itself_on_open(embedded_url):
    async def open_store() -> SurrealDBStorage:
        store = SurrealDBStorage(url=embedded_url)
        await store.connect()
        return store

    await assert_a_double_written_graph_migrates_itself(open_store)


async def test_a_graph_already_at_version_2_still_gets_this_step(embedded_url):
    """The steps run in order and each is gated on its own version, so a graph
    that has had the edge renames and nothing since is brought the rest of the
    way rather than skipped."""
    store = SurrealDBStorage(url=embedded_url)
    await store.connect()
    ids = await _seed_double_written_graph(store)
    await store.db.query("UPSERT schema_version:current SET version = 2")
    await store.close()

    reopened = SurrealDBStorage(url=embedded_url)
    await reopened.connect()

    assert sorted((await _retention_row(reopened, ids["anchored"])).covers) == sorted(
        [ids["stale_a"], ids["stale_b"]]
    )
    assert (await _retention_row(reopened, ids["orphaned"])).covers == []
    assert await _confirmed_edge_count(reopened) == 0
    assert await _schema_version(reopened) == 4
    await reopened.close()


async def test_a_row_that_already_has_covers_is_left_alone(embedded_url):
    """The half-migrated graph: a second pass must not re-parse a row whose
    anchors are already where they belong, because the prose it would read is
    the wrong side of the same fact."""
    store = SurrealDBStorage(url=embedded_url)
    await store.connect()
    record = DecisionRecord(
        kind=DecisionKind.RETENTION,
        subject_ids=["n1"],
        covers=["kept-anchor"],
        certainty_basis="re-read; it holds [covers: stale-anchor]",
    )
    await store.record_decision(record)
    await store.db.query("DELETE schema_version:current")
    await store.close()

    reopened = SurrealDBStorage(url=embedded_url)
    await reopened.connect()
    assert (await _retention_row(reopened, "n1")).covers == ["kept-anchor"]
    await reopened.close()


async def test_a_fresh_graph_is_stamped_and_left_alone(embedded_url):
    """Nothing to migrate still records the version, so the next open skips it."""
    store = SurrealDBStorage(url=embedded_url)
    await store.connect()

    assert await _schema_version(store) == 4
    assert await store.query_decisions() == []
    assert await _confirmed_edge_count(store) == 0
    await store.close()
