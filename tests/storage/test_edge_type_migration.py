"""A graph written before the edge-type split migrates itself when opened.

`_setup_schema` runs on `connect()` and on every `switch_database()`, so it is
the one place that sees every graph exactly when it is opened. It renames the
two edge types that were split apart, once, gated on a `schema_version` marker
so a graph already at the current version pays nothing.

The old-named rows are written straight at the connection: the enum no longer
has members that produce them, which is the whole point of the migration.

The ws:// copy of this lives in `test_surrealdb_integration.py` and calls
`assert_a_pre_split_graph_migrates_itself` below, so the embedded and remote
paths are checked against one script.
"""

import tempfile
from pathlib import Path

import pytest

from epimemer.core.types import EdgeType, Fact, Inference, NodeEdge, Topic
from epimemer.storage.surrealdb_adapter import SurrealDBStorage


async def _rename_type(store: SurrealDBStorage, edge: NodeEdge, old_name: str) -> None:
    """Put one stored edge back under the name it carried before the split."""
    await store.db.query(
        "UPDATE node_edge SET type = $type WHERE uid = $uid",
        {"type": old_name, "uid": edge.id},
    )


async def _seed_pre_split_graph(store: SurrealDBStorage) -> dict[str, str]:
    """A graph holding one edge of each shape, under the pre-split names."""
    topic = Topic(content="Billing", source_id="seg-1")
    fact = Fact(content="the invoice was reissued", source_id="seg-1")
    inference = Inference(content="billing runs are being corrected", source_id="seg-1")
    for node in (topic, fact, inference):
        await store.store_node(node)

    tagged = NodeEdge(src_id=fact.id, dst_id=topic.id, type=EdgeType.TAGGED_WITH_TOPIC)
    extracted = NodeEdge(src_id=fact.id, dst_id=topic.id, type=EdgeType.EXTRACTED_UNDER_TOPIC)
    supports = NodeEdge(src_id=fact.id, dst_id=inference.id, type=EdgeType.SUPPORTS)
    for edge in (tagged, extracted, supports):
        await store.store_edge(edge)

    await _rename_type(store, tagged, "tagged_with")
    await _rename_type(store, extracted, "supports")

    # The marker `_setup_schema` has just stamped, removed: this graph is
    # standing in for one written before the version existed.
    await store.db.query("DELETE schema_version:current")

    return {
        "topic": topic.id,
        "fact": fact.id,
        "inference": inference.id,
        "tagged": tagged.id,
        "extracted": extracted.id,
        "supports": supports.id,
    }


async def _types_by_uid(store: SurrealDBStorage) -> dict[str, str]:
    rows = await store.db.query("SELECT uid, type FROM node_edge")
    return {row["uid"]: row["type"] for row in rows}


async def _schema_version(store: SurrealDBStorage) -> int | None:
    stored = await store.db.query("SELECT VALUE version FROM schema_version:current")
    return int(stored[0]) if stored else None


async def assert_a_pre_split_graph_migrates_itself(open_store) -> None:
    """Seed a pre-split graph, reopen it twice, and check what came back.

    `open_store` is an awaitable factory handing back a freshly connected store
    on one and the same graph, so this reads the same against an embedded file
    and against a ws:// server.
    """
    seeded = await open_store()
    ids = await _seed_pre_split_graph(seeded)
    assert await _schema_version(seeded) is None
    await seeded.close()

    reopened = await open_store()
    types = await _types_by_uid(reopened)
    assert types[ids["tagged"]] == "tagged_with_topic"
    assert types[ids["extracted"]] == "extracted_under_topic"
    # Evidential support is untouched: its destination is an inference.
    assert types[ids["supports"]] == "supports"
    assert await _schema_version(reopened) == 5

    # The edges are reachable under the new names, not merely relabelled in place.
    tagged_edges = await reopened.get_edges_from(ids["fact"], edge_type=EdgeType.TAGGED_WITH_TOPIC)
    assert [e.id for e in tagged_edges] == [ids["tagged"]]
    extracted_edges = await reopened.get_edges_to(
        ids["topic"], edge_type=EdgeType.EXTRACTED_UNDER_TOPIC
    )
    assert [e.id for e in extracted_edges] == [ids["extracted"]]
    await reopened.close()

    # A second open changes nothing: the marker stops the statements running,
    # and they would rewrite nothing if they did.
    again = await open_store()
    assert await _types_by_uid(again) == types
    assert await _schema_version(again) == 5
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

    await assert_a_pre_split_graph_migrates_itself(open_store)


async def test_switching_to_a_pre_split_graph_migrates_it(embedded_url):
    """`switch_database` opens a graph too, and runs the same schema setup."""
    store = SurrealDBStorage(url=embedded_url, database="first")
    await store.connect()
    await store.switch_database("second")
    ids = await _seed_pre_split_graph(store)

    await store.switch_database("first")
    await store.switch_database("second")

    types = await _types_by_uid(store)
    assert types[ids["tagged"]] == "tagged_with_topic"
    assert types[ids["extracted"]] == "extracted_under_topic"
    assert types[ids["supports"]] == "supports"
    assert await _schema_version(store) == 5
    await store.close()


async def test_a_fresh_graph_is_stamped_and_left_alone(embedded_url):
    """Nothing to migrate still records the version, so the next open skips it."""
    store = SurrealDBStorage(url=embedded_url)
    await store.connect()
    assert await _schema_version(store) == 5
    assert await _types_by_uid(store) == {}
    await store.close()
