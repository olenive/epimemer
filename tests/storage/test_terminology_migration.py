# terminology-guard: off — this module seeds and asserts the pre-0.2.0 spellings,
# which is what it is for.
"""A graph written when "frame" was the word migrates itself when opened.

Schema version 4 moves every stored string that spelled `frame` to the
metacontext spelling: the two journal kinds, the journal row's own metacontext
field, the renamed advisory kinds wherever they were written down, and the trail
`reassign_metacontext` leaves on a node it moved.

The old shapes are written straight at the connection, since no enum member and
no field produces them any more, which is the point of the migration.

The ws:// copy lives in `test_surrealdb_integration.py` and calls
`assert_a_frame_spelled_graph_migrates_itself` below, so the embedded and remote
paths are checked against one script.
"""

import tempfile
from pathlib import Path

import pytest

from epimemer.core.advisories import AdvisoryAction, AdvisoryKind
from epimemer.core.types import DecisionKind, DecisionRecord, Fact
from epimemer.storage.protocol import WarningOverrides
from epimemer.storage.surrealdb_adapter import SurrealDBStorage

DECLARATION_ROW = "dec-declaration"
REASSIGNMENT_ROW = "dec-reassignment"
ADVISORY_ROW = "dec-advisory"
UNTOUCHED_ROW = "dec-retention"


async def _old_decision(store: SurrealDBStorage, uid: str, row: dict) -> None:
    """One journal row in the shape a 0.1.x graph holds."""
    await store.db.query(
        "CREATE decision CONTENT $data",
        {
            "data": {
                "uid": uid,
                "subject_ids": ["n1"],
                "covers": [],
                "decided_at": "2026-09-01T09:00:00.000000Z",
                **row,
            }
        },
    )


async def _seed_frame_spelled_graph(store: SurrealDBStorage) -> str:
    """A graph holding every stored string the rename touches."""
    await _old_decision(store, DECLARATION_ROW, {"kind": "frame_declaration", "frame": "the-real"})
    await _old_decision(store, REASSIGNMENT_ROW, {"kind": "reframe", "frame": "petersburg-novel"})
    await _old_decision(
        store,
        ADVISORY_ROW,
        {
            "kind": "proceeded_despite_advisory",
            "certainty_basis": (
                "[cross_frame] Two claims about different worlds. "
                "[same_frame_variant] variant_of is meant for a proposition two "
                "worlds resolve differently."
            ),
        },
    )
    # A row the step must not touch, so a rewrite that matched too widely fails.
    await store.record_decision(
        DecisionRecord(
            id=UNTOUCHED_ROW,
            kind=DecisionKind.RETENTION,
            subject_ids=["n1"],
            certainty_basis="re-read; it holds",
        )
    )

    await store.db.query(
        "UPSERT graph_state:reflect SET warning_overrides = $overrides",
        {
            "overrides": {
                "default_action": "proceed",
                "by_kind": {"same_frame_contradiction": "flag", "disjoint_premises": "proceed"},
            }
        },
    )

    moved = Fact(content="the pass was closed", source_id="seg-1")
    await store.store_node(moved)
    await store.db.query(
        "UPDATE fact SET metadata.reframings = $trail WHERE uid = $uid",
        {
            "trail": [{"because": "it is fiction", "withdrew": "the-real", "assigned": "novel"}],
            "uid": moved.id,
        },
    )

    # The marker `_setup_schema` has just stamped, removed: this graph is
    # standing in for one written before the version existed.
    await store.db.query("DELETE schema_version:current")
    return moved.id


async def _schema_version(store: SurrealDBStorage) -> int | None:
    stored = await store.db.query("SELECT VALUE version FROM schema_version:current")
    return int(stored[0]) if stored else None


async def _row(store: SurrealDBStorage, uid: str) -> dict:
    rows = await store.db.query("SELECT * FROM decision WHERE uid = $uid", {"uid": uid})
    assert rows, uid
    return rows[0]


async def _trail(store: SurrealDBStorage, node_id: str) -> dict:
    rows = await store.db.query("SELECT metadata FROM fact WHERE uid = $uid", {"uid": node_id})
    return rows[0]["metadata"]


async def assert_a_frame_spelled_graph_migrates_itself(open_store) -> None:
    """Seed a 0.1.x-spelled graph, reopen it twice, and check what came back.

    `open_store` is an awaitable factory handing back a freshly connected store
    on one and the same graph, so this reads the same against an embedded file
    and against a ws:// server.
    """
    seeded = await open_store()
    node_id = await _seed_frame_spelled_graph(seeded)
    assert await _schema_version(seeded) is None
    await seeded.close()

    reopened = await open_store()

    # (i) The two journal kinds are the ones the enum now has, so `review` and
    #     `query_decisions` reach the rows again.
    declaration = await _row(reopened, DECLARATION_ROW)
    assert declaration["kind"] == DecisionKind.METACONTEXT_DECLARATION.value
    reassignment = await _row(reopened, REASSIGNMENT_ROW)
    assert reassignment["kind"] == DecisionKind.METACONTEXT_REASSIGNMENT.value

    # (ii) The row's own field moved with the model field it serializes, so the
    #      value survives rather than being dropped on the next read.
    assert declaration["metacontext"] == "the-real"
    assert "frame" not in declaration
    assert reassignment["metacontext"] == "petersburg-novel"

    stored = await reopened.query_decisions(kinds=[DecisionKind.METACONTEXT_DECLARATION])
    assert [row.metacontext for row in stored] == ["the-real"]

    # (iii) The advisory kinds inside the row's prose, and nothing else in it.
    advisory = await _row(reopened, ADVISORY_ROW)
    assert advisory["certainty_basis"].startswith("[cross_metacontext] ")
    assert "[same_metacontext_variant] variant_of is meant for" in advisory["certainty_basis"]
    assert "frame" not in advisory["certainty_basis"]

    # (iv) A row of another kind is untouched.
    assert (await _row(reopened, UNTOUCHED_ROW))["certainty_basis"] == "re-read; it holds"

    # (v) The graph's own warning policy, whose kinds are map keys.
    overrides = await reopened.get_warning_overrides()
    assert overrides == WarningOverrides(
        default_action=AdvisoryAction.PROCEED,
        by_kind={
            AdvisoryKind.SAME_METACONTEXT_CONTRADICTION: AdvisoryAction.FLAG,
            AdvisoryKind.DISJOINT_PREMISES: AdvisoryAction.PROCEED,
        },
    )

    # (vi) The trail on the node, the only place a withdrawn metacontext lives.
    trail = await _trail(reopened, node_id)
    assert "reframings" not in trail
    assert [entry["because"] for entry in trail["metacontext_reassignments"]] == ["it is fiction"]

    assert await _schema_version(reopened) == 4
    await reopened.close()

    # A second open changes nothing: the marker stops the steps running, and
    # they would rewrite nothing if they did.
    again = await open_store()
    assert await _row(again, DECLARATION_ROW) == declaration
    assert await _row(again, REASSIGNMENT_ROW) == reassignment
    assert await _row(again, ADVISORY_ROW) == advisory
    assert await again.get_warning_overrides() == overrides
    assert await _trail(again, node_id) == trail
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

    await assert_a_frame_spelled_graph_migrates_itself(open_store)


async def test_a_graph_already_at_version_3_still_gets_this_step(embedded_url):
    """The steps run in order and each is gated on its own version, so a graph
    that has had the keep-verdict move and nothing since is brought the rest of
    the way rather than skipped."""
    store = SurrealDBStorage(url=embedded_url)
    await store.connect()
    node_id = await _seed_frame_spelled_graph(store)
    await store.db.query("UPSERT schema_version:current SET version = 3")
    await store.close()

    reopened = SurrealDBStorage(url=embedded_url)
    await reopened.connect()

    assert (await _row(reopened, DECLARATION_ROW))["kind"] == "metacontext_declaration"
    assert "metacontext_reassignments" in await _trail(reopened, node_id)
    assert await _schema_version(reopened) == 4
    await reopened.close()


async def test_a_fresh_graph_is_stamped_and_left_alone(embedded_url):
    """Nothing to migrate still records the version, so the next open skips it."""
    store = SurrealDBStorage(url=embedded_url)
    await store.connect()

    assert await _schema_version(store) == 4
    assert await store.query_decisions() == []
    assert await store.get_warning_overrides() == WarningOverrides()
    await store.close()
