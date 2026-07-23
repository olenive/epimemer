"""Backend-parity tests for the StorageBackend protocol.

Every test here runs against **both** backends. Bugs have survived precisely
because `InMemoryStorage` and `SurrealDBStorage` diverged and only the
in-memory one was exercised: the in-memory store upserts via dict assignment,
while SurrealDB used `INSERT INTO`, which a UNIQUE index on `uid` causes to be
*silently ignored* on re-store.

Anything the protocol promises but the two backends could implement
differently belongs in this file.
"""

from datetime import datetime, timezone

import pytest

from epimemer.core.types import (
    EmbeddingRecord,
    Metacontext,
    RawDocument,
    Timeline,
    Topic,
)
from epimemer.mcp import tools
from epimemer.pipelines.timeline.functions import add_timepoint
from epimemer.storage.memory import InMemoryStorage
from epimemer.storage.surrealdb_adapter import SurrealDBStorage


@pytest.fixture(params=["memory", "surrealdb"])
async def store(request):
    """Yield each backend in turn, so every test in this module runs twice."""
    if request.param == "memory":
        # InMemoryStorage has no connect/close yet.
        yield InMemoryStorage()
    else:
        s = SurrealDBStorage(url="mem://")
        await s.connect()
        yield s
        await s.close()


class TestStoreIsUpsert:
    """`store_*` is upsert-by-id: a re-store updates in place, never duplicates
    and never silently no-ops."""

    async def test_store_node_twice_updates_in_place(self, store):
        topic = Topic(content="hello", source_id="s1")
        await store.store_node(topic)

        topic.value.relevance = 0.123
        await store.store_node(topic)

        got = await store.get_node(topic.id)
        assert got is not None
        assert got.value.relevance == pytest.approx(0.123)

        # ...and exactly one row, not two.
        rows = await store.query_nodes()
        assert [n.id for n in rows] == [topic.id]

    async def test_store_node_twice_updates_content(self, store):
        topic = Topic(content="original", source_id="s1")
        await store.store_node(topic)

        topic.content = "revised"
        await store.store_node(topic)

        got = await store.get_node(topic.id)
        assert got is not None
        assert got.content == "revised"

    async def test_store_timeline_twice_updates_timepoints(self, store):
        timeline = Timeline(name="tl")
        await store.store_timeline(timeline)

        updated, _ = add_timepoint(
            timeline, start=datetime(2024, 1, 1, tzinfo=timezone.utc)
        )
        await store.store_timeline(updated)

        got = await store.get_timeline(timeline.id)
        assert got is not None
        assert len(got.timepoints) == 1

    async def test_store_timeline_accumulates_timepoints(self, store):
        timeline = Timeline(name="tl")
        await store.store_timeline(timeline)

        current = timeline
        for day in (1, 2, 3):
            current, _ = add_timepoint(
                current, start=datetime(2024, 1, day, tzinfo=timezone.utc)
            )
            await store.store_timeline(current)

        got = await store.get_timeline(timeline.id)
        assert got is not None
        assert len(got.timepoints) == 3

    async def test_store_document_twice_updates_in_place(self, store):
        doc = RawDocument(content="first")
        await store.store_document(doc)

        doc.content = "second"
        await store.store_document(doc)

        got = await store.get_document(doc.id)
        assert got is not None
        assert got.content == "second"

    async def test_store_metacontext_twice_updates_in_place(self, store):
        mc = Metacontext(content="frame")
        await store.store_metacontext(mc)

        mc.description = "now described"
        await store.store_metacontext(mc)

        got = await store.get_metacontext(mc.id)
        assert got is not None
        assert got.description == "now described"

    async def test_store_embedding_twice_updates_in_place(self, store):
        emb = EmbeddingRecord(
            item_id="item1", model_id="m", vector=[0.1, 0.2, 0.3]
        )
        await store.store_embedding(emb)

        emb.vector = [0.9, 0.8, 0.7]
        await store.store_embedding(emb)

        got = await store.get_embeddings_for_item("item1")
        assert len(got) == 1
        assert got[0].vector == pytest.approx([0.9, 0.8, 0.7])


PAYLOADS = {
    "unicode": "café → 日本語 🙂 naïve",
    "empty": "",
    "quotes": "he said \"hi\" and 'bye'",
    "backtick_dollar": "a `backtick` and $dollar",
    "whitespace": "line1\nline2\ttab",
    "brackets": "{curly} [square] <angle>",
    "surrealql": "SELECT * FROM node; -- DROP",
}


class TestPayloadFidelity:
    """Content must survive the round trip byte-for-byte on every backend.

    SurrealDB reaches its store through query serialization, so characters that
    are syntax to SurrealQL — backticks, `$`, quotes, semicolons — take a path
    that plain dict assignment never exercises.
    """

    @pytest.mark.parametrize("content", PAYLOADS.values(), ids=list(PAYLOADS))
    async def test_node_content_round_trips(self, store, content):
        topic = Topic(content=content, source_id="s1")
        await store.store_node(topic)

        got = await store.get_node(topic.id)
        assert got is not None
        assert got.content == content

    async def test_optional_field_stays_none(self, store):
        """A nullable field set to None reads back as None.

        Passes on SurrealDB for a non-obvious reason: the key is not stored at
        all (a NONE value is omitted from the row), and Pydantic refills the
        declared `= None` default on read. It therefore only holds while every
        nullable field defaults to None — a field declared
        `x: str | None = "unknown"` would read back "unknown" here.
        """
        topic = Topic(content="no source", source_id=None)
        await store.store_node(topic)

        got = await store.get_node(topic.id)
        assert got is not None
        assert got.source_id is None

    async def test_nested_metadata_round_trips(self, store):
        metadata = {"k": "v", "nested": {"a": [1, 2]}, "empty": {}}
        topic = Topic(content="meta", source_id="s1", metadata=metadata)
        await store.store_node(topic)

        got = await store.get_node(topic.id)
        assert got is not None
        assert got.metadata == metadata

    async def test_none_valued_metadata_key_is_dropped(self, store):
        """Every backend drops a None-valued metadata key — the same way.

        SurrealDB cannot store one (the driver encodes `None` as `NONE`, which
        stores no key), so rather than let the backends disagree, all of them
        normalize to absence on write. This asserts the *agreement*, which is
        what makes the backends interchangeable; that it drops rather than
        keeps is the documented trade.
        """
        topic = Topic(content="meta", source_id="s1",
                      metadata={"note": None, "keep": 1})
        await store.store_node(topic)

        got = await store.get_node(topic.id)
        assert got is not None
        assert got.metadata == {"keep": 1}

    async def test_none_dropped_from_nested_metadata(self, store):
        topic = Topic(content="meta", source_id="s1",
                      metadata={"outer": {"note": None, "keep": 1}})
        await store.store_node(topic)

        got = await store.get_node(topic.id)
        assert got is not None
        assert got.metadata == {"outer": {"keep": 1}}

    async def test_none_preserved_inside_metadata_list(self, store):
        """A None *element* survives — dropping it would shift every index."""
        topic = Topic(content="meta", source_id="s1",
                      metadata={"xs": [1, None, 2]})
        await store.store_node(topic)

        got = await store.get_node(topic.id)
        assert got is not None
        assert got.metadata == {"xs": [1, None, 2]}

    async def test_storing_does_not_mutate_the_caller_object(self, store):
        """Normalizing must not reach back into the object the caller passed."""
        metadata = {"note": None, "keep": 1}
        topic = Topic(content="meta", source_id="s1", metadata=metadata)
        await store.store_node(topic)

        assert topic.metadata == {"note": None, "keep": 1}
        assert metadata == {"note": None, "keep": 1}

    async def test_float_precision_preserved(self, store):
        vector = [0.1234567890123456, -1e-12, 3.0, 0.0]
        emb = EmbeddingRecord(item_id="item1", model_id="m", vector=vector)
        await store.store_embedding(emb)

        got = await store.get_embeddings_for_item("item1")
        assert got[0].vector == vector


HOSTILE_GRAPH_NAMES = [
    "pwn`; REMOVE DATABASE `victim",
    "a;b",
    "x`; REMOVE NAMESPACE epimemer; --",
    "has space",
    "",
    "z" * 65,
]


class TestGraphNameValidation:
    """Backends must agree on what a legal graph name is, so a name accepted by
    one is not a name the other would interpolate into SurrealQL."""

    @pytest.mark.parametrize("name", HOSTILE_GRAPH_NAMES)
    async def test_switch_database_rejects_illegal_name(self, store, name):
        with pytest.raises(ValueError):
            await store.switch_database(name)

    @pytest.mark.parametrize("name", HOSTILE_GRAPH_NAMES)
    async def test_delete_database_rejects_illegal_name(self, store, name):
        with pytest.raises(ValueError):
            await store.delete_database(name)

    @pytest.mark.parametrize("name", ["default", "my_graph", "my-graph", "g1", "A" * 64])
    async def test_legal_names_accepted(self, store, name):
        await store.switch_database(name)
        assert store.current_database == name


class TestTimelineToolsPersist:
    """`add_timeline_timepoint` re-stores the whole timeline, so an insert-only
    backend drops every timepoint after the first — and `create_timelink` then
    cannot find the timepoint."""

    async def test_add_timepoint_persists_on_storage(self, store):
        created, _ = await tools.create_timeline("tl", store)
        timeline_id = created["timeline_id"]

        first, _ = await tools.add_timeline_timepoint(
            timeline_id, store, start=datetime(2024, 1, 1, tzinfo=timezone.utc)
        )
        second, _ = await tools.add_timeline_timepoint(
            timeline_id, store, start=datetime(2024, 6, 1, tzinfo=timezone.utc)
        )

        assert second["timepoints_count"] == 2

        # `timepoints_count` is computed from the in-memory object, so it reads
        # correctly even when the write was dropped. Re-reading through
        # `query_timeline` is what actually catches the data loss.
        queried, _ = await tools.query_timeline(
            timeline_id,
            store,
            range_start=datetime(2023, 1, 1, tzinfo=timezone.utc),
            range_end=datetime(2025, 1, 1, tzinfo=timezone.utc),
        )
        returned_ids = {tp["id"] for tp in queried["timepoints"]}
        assert returned_ids == {first["timepoint_id"], second["timepoint_id"]}
