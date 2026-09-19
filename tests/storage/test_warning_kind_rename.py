"""A journal row written before the rename still reads back.

The note a tool attaches to a call is a **warning** on every surface an agent or
a person reads, so `proceeded_despite_advisory` became
`proceeded_despite_warning`. The value was already on disk in three places that
do not migrate together:

- a SurrealDB graph, which rewrites the stored string when it is next opened
  (schema version 6);
- the in-memory store, which has no version marker and so normalises whatever it
  is handed;
- a bundle, which is a file on somebody's disk and is never migrated at all.

`DecisionKind._missing_` covers the second and third, and this checks all three
plus the value a fresh row is written under.
"""

import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from epimemer.core.types import DecisionKind, DecisionRecord, Fact
from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.pipelines.transfer import export_graph, import_graph, read_bundle, write_bundle
from epimemer.storage.memory import InMemoryStorage
from epimemer.storage.surrealdb_adapter import SurrealDBStorage

OLD_KIND = "proceeded_despite_advisory"
NEW_KIND = "proceeded_despite_warning"

WROTE_ANYWAY = "[cross_metacontext] Two claims about different worlds."

EXPORTED_AT = datetime(2026, 9, 12, 8, 0, 0, tzinfo=UTC)


def _row(uid: str) -> dict:
    """One journal row in the shape a graph written before the rename holds."""
    return {
        "uid": uid,
        "kind": OLD_KIND,
        "subject_ids": ["n1", "n2"],
        "covers": [],
        "decided_at": "2026-09-01T09:00:00.000000Z",
        "certainty_basis": WROTE_ANYWAY,
    }


class TestTheNewValueIsWhatAFreshRowCarries:
    async def test_the_enum_spells_it_the_new_way(self):
        assert DecisionKind.PROCEEDED_DESPITE_WARNING.value == NEW_KIND

    async def test_a_row_written_now_is_stored_under_it(self):
        store = SurrealDBStorage(url="mem://")
        await store.connect()

        await store.record_decision(
            DecisionRecord(
                id="dec-fresh",
                kind=DecisionKind.PROCEEDED_DESPITE_WARNING,
                subject_ids=["n1"],
                certainty_basis=WROTE_ANYWAY,
            )
        )

        stored = await store.db.query("SELECT kind FROM decision WHERE uid = 'dec-fresh'")
        assert stored[0]["kind"] == NEW_KIND
        await store.close()


class TestAnOldRowReadsBackAsTheNewKind:
    """Both backends answer the same question the same way, by different routes:
    SurrealDB rewrites the string, and the in-memory store normalises on read."""

    @pytest.fixture
    def embedded_url(self):
        """An embedded store that survives being closed and reopened, since the
        migration is what running on the second open is being checked."""
        with tempfile.TemporaryDirectory() as directory:
            yield f"surrealkv://{Path(directory) / 'graph.db'}"

    async def test_surrealdb_rewrites_it_when_the_graph_is_opened(self, embedded_url):
        seeded = SurrealDBStorage(url=embedded_url)
        await seeded.connect()
        await seeded.db.query("CREATE decision CONTENT $data", {"data": _row("dec-old")})
        # The marker the setup has just stamped, removed: this graph stands in
        # for one written before version 6 existed.
        await seeded.db.query("DELETE schema_version:current")
        await seeded.close()

        reopened = SurrealDBStorage(url=embedded_url)
        await reopened.connect()

        stored = await reopened.db.query("SELECT kind FROM decision WHERE uid = 'dec-old'")
        assert stored[0]["kind"] == NEW_KIND

        # The point of the rewrite: selecting on the kind reaches the row again.
        selected = await reopened.query_decisions(kinds=[DecisionKind.PROCEEDED_DESPITE_WARNING])
        assert [record.id for record in selected] == ["dec-old"]
        assert selected[0].certainty_basis == WROTE_ANYWAY
        await reopened.close()

    async def test_the_in_memory_store_normalises_what_it_is_handed(self):
        """No version marker and nothing to migrate, so the old value has to be
        readable rather than rewritten. A bundle import is the route it arrives
        by, and it arrives as a parsed record."""
        store = InMemoryStorage()
        await store.connect()

        await store.record_decision(
            DecisionRecord.model_validate(_row("dec-old") | {"id": "dec-old"})
        )

        selected = await store.query_decisions(kinds=[DecisionKind.PROCEEDED_DESPITE_WARNING])
        assert [record.id for record in selected] == ["dec-old"]
        await store.close()

    async def test_a_typo_is_still_an_error(self):
        """The normalisation is a short list of retired names, not a fallback:
        a kind nothing ever wrote has to fail rather than resolve."""
        with pytest.raises(ValueError):
            DecisionKind("proceeded_despite_nothing")


class TestABundleExportedBeforeTheRenameImports:
    """A bundle is a file, so nothing migrates it. It has to read."""

    async def test_the_kind_arrives_as_the_new_one(self, tmp_path):
        source = InMemoryStorage()
        await source.connect()
        await source.switch_database("before-the-rename")
        await source.store_node(Fact(id="n1", content="the pass was closed", source_id="seg-1"))
        await source.record_decision(
            DecisionRecord(
                id="dec-old",
                kind=DecisionKind.PROCEEDED_DESPITE_WARNING,
                subject_ids=["n1"],
                certainty_basis=WROTE_ANYWAY,
            )
        )
        exported = await export_graph(
            source,
            embedding_provider="mock",
            embedding_model_id="mock-model",
            exported_at=EXPORTED_AT,
        )
        write_bundle(exported, tmp_path / "plain", plain=True)
        await source.close()

        # Age the bundle back to what the exporter of the day would have
        # written. Rewriting the file is the only honest way to have one: no
        # code here spells the old value any more.
        decisions = tmp_path / "plain" / "decisions.jsonl"
        decisions.write_text(decisions.read_text().replace(NEW_KIND, OLD_KIND))
        assert OLD_KIND in decisions.read_text()

        target = InMemoryStorage()
        await target.connect()
        await import_graph(
            read_bundle(tmp_path / "plain"),
            target,
            MockEmbeddingProvider(model_id="mock-model", dimension=16),
            graph="after-the-rename",
        )
        await target.switch_database("after-the-rename")

        selected = await target.query_decisions(kinds=[DecisionKind.PROCEEDED_DESPITE_WARNING])
        assert [record.id for record in selected] == ["dec-old"]
        assert selected[0].certainty_basis == WROTE_ANYWAY
        await target.close()
