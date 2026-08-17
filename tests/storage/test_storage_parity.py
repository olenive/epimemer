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
    EdgeType,
    EmbeddingRecord,
    Fact,
    Inference,
    Metacontext,
    NodeEdge,
    NodeStatus,
    NodeType,
    RawDocument,
    Timeline,
    Topic,
    ValueSignal,
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

        topic.value.confidence = 0.123
        await store.store_node(topic)

        got = await store.get_node(topic.id)
        assert got is not None
        assert got.value.confidence == pytest.approx(0.123)

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

    async def test_value_signal_importance_round_trips(self, store):
        """A judged `importance` survives the round trip on every backend.

        Serialization is the plausible divergence: `importance` is a new field
        on a nested model, and a backend that reconstructs `ValueSignal` from
        its own row shape would silently hand back the default instead.
        """
        topic = Topic(
            content="important",
            source_id="s1",
            value=ValueSignal(importance=0.87, confidence=0.31),
        )
        await store.store_node(topic)

        got = await store.get_node(topic.id)
        assert got is not None
        assert got.value.importance == pytest.approx(0.87)
        assert got.value.confidence == pytest.approx(0.31)

    async def test_value_signal_importance_defaults_for_older_rows(self, store):
        """A row written before the field existed reads back at the default.

        Simulated by storing a node whose value payload omits `importance`
        entirely — the "no retroactive repair" carry-over means old graphs must
        keep working rather than be migrated.
        """
        topic = Topic(content="legacy", source_id="s1")
        payload = topic.model_dump(mode="json")
        payload["value"].pop("importance", None)
        await store.store_node(Topic.model_validate(payload))

        got = await store.get_node(topic.id)
        assert got is not None
        assert got.value.importance == pytest.approx(0.5)

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


class TestTimelineReferenceTime:
    """A timeline's `reference_time` is its "now" — and it must survive storage.

    Unset is a distinct state from "set to the moment it was created": a real
    timeline follows the wall clock, and storing a timestamp at creation would
    silently freeze its present at whenever it was first written.
    """

    async def test_reference_time_round_trips(self, store):
        when = datetime(1897, 5, 26, tzinfo=timezone.utc)
        timeline = Timeline(name="Dracula", reference_time=when)
        await store.store_timeline(timeline)

        got = await store.get_timeline(timeline.id)
        assert got is not None
        assert got.reference_time == when

    async def test_reference_time_defaults_to_unset(self, store):
        timeline = Timeline(name="real world")
        await store.store_timeline(timeline)

        got = await store.get_timeline(timeline.id)
        assert got is not None
        assert got.reference_time is None

    async def test_reference_time_can_be_cleared(self, store):
        timeline = Timeline(
            name="was fictional",
            reference_time=datetime(1897, 5, 26, tzinfo=timezone.utc),
        )
        await store.store_timeline(timeline)

        await store.store_timeline(timeline.model_copy(update={"reference_time": None}))

        got = await store.get_timeline(timeline.id)
        assert got is not None
        assert got.reference_time is None


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


class TestWriteBatchTxTimelines:
    """A `TIMELINK` edge is only meaningful if the timeline it names exists.

    Ingestion is atomic *because* everything goes through `write_batch_tx`. Once
    extraction proposes timepoints, a timeline written outside that batch means
    a mid-document failure can leave `TIMELINK` edges pointing at a timeline
    that was never stored — an edge to nothing, which the viz read path resolves
    to an empty row rather than an error, so it fails silently.

    Timelines are the one **upsert** in an otherwise insert-only batch: a
    timeline is a single record holding a list of timepoints, so appending a
    timepoint is a record replacement. There is no insert-shaped way to say it.
    """

    async def test_timeline_and_timelink_commit_together(self, store):
        node = Fact(content="the siege began", source_id="s1")
        timeline, point = add_timepoint(
            Timeline(name="Extracted"), start=datetime(1897, 5, 1, tzinfo=timezone.utc)
        )
        link = NodeEdge(
            src_id=node.id,
            dst_id=timeline.id,
            type=EdgeType.TIMELINK,
            metadata={"timepoint_id": point.id},
        )

        await store.write_batch_tx(nodes=[node], edges=[link], timelines=[timeline])

        stored = await store.get_timeline(timeline.id)
        assert stored is not None
        assert [tp.id for tp in stored.timepoints] == [point.id]
        assert len(await store.get_edges_from(node.id, edge_type=EdgeType.TIMELINK)) == 1

    async def test_a_failed_batch_leaves_neither(self, store):
        node = Fact(content="the siege began", source_id="s1")
        timeline, point = add_timepoint(
            Timeline(name="Extracted"), start=datetime(1897, 5, 1, tzinfo=timezone.utc)
        )
        link = NodeEdge(
            src_id=node.id,
            dst_id=timeline.id,
            type=EdgeType.TIMELINK,
            metadata={"timepoint_id": point.id},
        )

        def boom_embeddings():
            raise RuntimeError("injected failure")
            yield  # pragma: no cover - generator body, never reached

        with pytest.raises(RuntimeError, match="injected failure"):
            await store.write_batch_tx(
                nodes=[node],
                edges=[link],
                timelines=[timeline],
                embeddings=boom_embeddings(),
            )

        assert await store.get_timeline(timeline.id) is None
        assert await store.get_node(node.id) is None
        assert await store.get_edges_from(node.id) == []

    async def test_appending_to_an_existing_timeline_keeps_the_earlier_points(self, store):
        """The second document into a named timeline must not erase the first."""
        original, first = add_timepoint(
            Timeline(name="Extracted"), start=datetime(1897, 5, 1, tzinfo=timezone.utc)
        )
        await store.store_timeline(original)

        appended, second = add_timepoint(
            original, start=datetime(1897, 9, 1, tzinfo=timezone.utc)
        )
        await store.write_batch_tx(timelines=[appended])

        stored = await store.get_timeline(original.id)
        assert stored is not None
        assert [tp.id for tp in stored.timepoints] == [first.id, second.id]

    async def test_a_failed_batch_leaves_an_existing_timeline_as_it_was(self, store):
        """Rolling back an upsert means restoring the old row, not dropping it."""
        original, first = add_timepoint(
            Timeline(name="Extracted"), start=datetime(1897, 5, 1, tzinfo=timezone.utc)
        )
        await store.store_timeline(original)

        appended, _ = add_timepoint(
            original, start=datetime(1897, 9, 1, tzinfo=timezone.utc)
        )

        def boom_embeddings():
            raise RuntimeError("injected failure")
            yield  # pragma: no cover - generator body, never reached

        with pytest.raises(RuntimeError, match="injected failure"):
            await store.write_batch_tx(
                timelines=[appended], embeddings=boom_embeddings()
            )

        stored = await store.get_timeline(original.id)
        assert stored is not None
        assert [tp.id for tp in stored.timepoints] == [first.id]


class TestVizListTimelines:
    """`viz_list_timelines` reads a named graph without disturbing the active
    connection — the hub asks one session for any of its graphs, so a read that
    switched the connection would leave the MCP session pointed elsewhere."""

    async def test_returns_timelines_with_their_timepoints(self, store):
        timeline, _ = add_timepoint(
            Timeline(name="History"), start=datetime(2024, 1, 1, tzinfo=timezone.utc)
        )
        await store.store_timeline(timeline)

        listed = await store.viz_list_timelines(store.current_database)

        assert [t.name for t in listed] == ["History"]
        assert len(listed[0].timepoints) == 1

    async def test_reads_another_graph_without_switching_the_active_one(self, store):
        # The backends disagree on what their starting graph is called
        # ("default" vs "main"), so ask rather than assume.
        home = store.current_database
        await store.store_timeline(Timeline(name="At home"))
        await store.switch_database("other")
        await store.store_timeline(Timeline(name="In other"))
        await store.switch_database(home)

        listed = await store.viz_list_timelines("other")

        assert [t.name for t in listed] == ["In other"]
        assert store.current_database == home

    async def test_unknown_graph_is_empty_not_an_error(self, store):
        assert list(await store.viz_list_timelines("no-such-graph")) == []


class TestVizListMetacontexts:
    """Metacontexts are named in the dashboard's frame filter, so the viz read
    must reach them the same way it reaches timelines — by graph, without
    moving the active connection."""

    async def test_returns_active_metacontexts(self, store):
        await store.store_metacontext(Metacontext(content="Real historical events"))

        listed = await store.viz_list_metacontexts(store.current_database)

        assert [mc.content for mc in listed] == ["Real historical events"]

    async def test_omits_superseded_metacontexts(self, store):
        await store.store_metacontext(
            Metacontext(content="Retired frame", status=NodeStatus.SUPERSEDED)
        )

        assert list(await store.viz_list_metacontexts(store.current_database)) == []

    async def test_reads_another_graph_without_switching_the_active_one(self, store):
        home = store.current_database
        await store.switch_database("other")
        await store.store_metacontext(Metacontext(content="Elsewhere"))
        await store.switch_database(home)

        listed = await store.viz_list_metacontexts("other")

        assert [mc.content for mc in listed] == ["Elsewhere"]
        assert store.current_database == home

    async def test_unknown_graph_is_empty_not_an_error(self, store):
        assert list(await store.viz_list_metacontexts("no-such-graph")) == []


class TestBackendName:
    """Every backend exposes a stable, human-readable `backend_name` label,
    surfaced to the visualization UI so a viewer can tell an in-memory store
    from a persistent one."""

    async def test_backend_name_is_a_known_label(self, store):
        assert store.backend_name in {"memory", "surrealdb"}

    async def test_backend_name_matches_implementation(self, store):
        expected = "memory" if isinstance(store, InMemoryStorage) else "surrealdb"
        assert store.backend_name == expected


class TestReflectCounter:
    """The "stores since last reflect" count is graph state, so it must behave
    identically on both backends: absent reads as zero, bumps accumulate, a
    reset reports what it cleared, and graphs never share a count."""

    async def test_unused_graph_reads_zero(self, store):
        assert await store.get_reflect_counter() == 0

    async def test_bump_returns_the_new_count(self, store):
        assert await store.bump_reflect_counter() == 1
        assert await store.bump_reflect_counter() == 2
        assert await store.get_reflect_counter() == 2

    async def test_reset_returns_the_previous_count(self, store):
        await store.bump_reflect_counter()
        await store.bump_reflect_counter()

        assert await store.reset_reflect_counter() == 2
        assert await store.get_reflect_counter() == 0

    async def test_reset_on_an_untouched_graph_is_zero(self, store):
        assert await store.reset_reflect_counter() == 0
        assert await store.get_reflect_counter() == 0

    async def test_counters_are_per_graph(self, store):
        original = store.current_database
        await store.bump_reflect_counter()
        await store.bump_reflect_counter()

        await store.switch_database("other_graph")
        assert await store.get_reflect_counter() == 0
        assert await store.bump_reflect_counter() == 1

        await store.switch_database(original)
        assert await store.get_reflect_counter() == 2


class TestVectorSearchReturnsOnlyActiveNodes:
    """Retired nodes must never resurface through similarity search.

    This is the invariant the status filter exists for, and it is protocol-level:
    both backends promise it, and each implements it differently — in-memory by
    filtering candidates before ranking, SurrealDB inside (or alongside) the
    ranking query. It previously had no parity coverage at all, only
    near-duplicate single-backend tests, which is how an optimisation to one
    implementation could quietly weaken it.

    The last test here is the one with teeth: a backend that filters *after*
    taking the top k satisfies every other assertion while silently returning
    fewer results than asked for.
    """

    async def _stored(self, store, node, vector, *, model_id="test"):
        await store.store_node(node)
        await store.store_embedding(
            EmbeddingRecord(item_id=node.id, model_id=model_id, vector=vector)
        )
        return node

    async def test_superseded_nodes_never_resurface(self, store):
        topic = await self._stored(
            store, Topic(content="ML", source_id="s1"), [1.0, 0.0, 0.0]
        )
        await store.update_node_status(topic.id, NodeStatus.SUPERSEDED)

        # An exact match on the query vector: only the status filter can hide it.
        results = await store.vector_search([1.0, 0.0, 0.0], "test", k=5)

        assert all(item_id != topic.id for item_id, _ in results)

    async def test_merged_nodes_never_resurface(self, store):
        topic = await self._stored(
            store, Topic(content="ML", source_id="s1"), [1.0, 0.0, 0.0]
        )
        await store.update_node_status(topic.id, NodeStatus.MERGED)

        results = await store.vector_search([1.0, 0.0, 0.0], "test", k=5)

        assert all(item_id != topic.id for item_id, _ in results)

    async def test_active_nodes_are_still_returned(self, store):
        """The filter must not be so eager that it hides live nodes."""
        topic = await self._stored(
            store, Topic(content="ML", source_id="s1"), [1.0, 0.0, 0.0]
        )

        results = await store.vector_search([0.9, 0.1, 0.0], "test", k=5)

        assert [item_id for item_id, _ in results] == [topic.id]

    async def test_the_type_filter_also_excludes_inactive(self, store):
        """The typed path is a separate query on both backends."""
        topic = await self._stored(
            store, Topic(content="ML", source_id="s1"), [1.0, 0.0, 0.0]
        )
        live = await self._stored(
            store, Topic(content="AI", source_id="s1"), [0.9, 0.1, 0.0]
        )
        await store.update_node_status(topic.id, NodeStatus.SUPERSEDED)

        results = await store.vector_search(
            [1.0, 0.0, 0.0], "test", k=5, node_type=NodeType.TOPIC
        )

        assert [item_id for item_id, _ in results] == [live.id]

    async def test_inactive_nodes_do_not_consume_the_k_budget(self, store):
        """`k` counts results the caller can use, not rows the backend looked at.

        Six retired nodes score *above* every live one here, so an implementation
        that ranks first and filters second returns nothing while three perfectly
        good matches sit just below the cut. Retrieval would silently go blind on
        any graph with a lot of history — which is exactly the graph that has
        been in use longest.
        """
        retired = [
            await self._stored(
                store,
                Fact(content=f"retired {i}", source_id="s1"),
                [1.0, 0.01 * i, 0.0],
            )
            for i in range(6)
        ]
        for node in retired:
            await store.update_node_status(node.id, NodeStatus.SUPERSEDED)
        live = [
            await self._stored(
                store,
                Fact(content=f"live {i}", source_id="s1"),
                [0.6, 0.8 - 0.01 * i, 0.0],
            )
            for i in range(3)
        ]

        results = await store.vector_search([1.0, 0.0, 0.0], "test", k=3)

        assert sorted(item_id for item_id, _ in results) == sorted(n.id for n in live)


class TestBatchedEdgeFetch:
    """`get_edges_for` answers many nodes in one call (#14 step 1).

    The oracle throughout is the pair it batches: whatever
    `get_edges_from`/`get_edges_to` say about a node one at a time,
    `get_edges_for` must say about that node in bulk. Only the number of
    round-trips is meant to change.

    Two things a batched implementation can get wrong that a per-node one
    cannot: losing the *association* between a node and its edges (SurrealDB
    returns one flat result set that has to be re-grouped in Python), and
    dropping nodes that have no edges (a `GROUP BY`-shaped answer has no row for
    them, so the caller cannot tell "no edges" from "never asked").
    """

    async def _triangle(self, store):
        """a → b, b → c, and a self-loop on c, with distinguishable types."""
        a = Fact(content="a", source_id="s1")
        b = Fact(content="b", source_id="s1")
        c = Fact(content="c", source_id="s1")
        for node in (a, b, c):
            await store.store_node(node)
        edges = [
            NodeEdge(src_id=a.id, dst_id=b.id, type=EdgeType.SUPPORTS),
            NodeEdge(src_id=b.id, dst_id=c.id, type=EdgeType.SUPPORTS),
            NodeEdge(src_id=a.id, dst_id=c.id, type=EdgeType.CONTRADICTION),
            NodeEdge(src_id=c.id, dst_id=c.id, type=EdgeType.RELATED, label="self"),
        ]
        for edge in edges:
            await store.store_edge(edge)
        return a, b, c

    async def _agrees_with_singles(self, store, ids, *, edge_type=None):
        """Assert both directions match the per-node methods for every id."""
        for direction, single in (
            ("from", store.get_edges_from),
            ("to", store.get_edges_to),
        ):
            batched = await store.get_edges_for(
                ids, direction=direction, edge_type=edge_type
            )
            for node_id in ids:
                expected = await single(node_id, edge_type=edge_type)
                assert {e.id for e in batched[node_id]} == {e.id for e in expected}, (
                    f"direction={direction} disagreed for {node_id}"
                )

    async def test_it_agrees_with_the_per_node_methods(self, store):
        a, b, c = await self._triangle(store)
        await self._agrees_with_singles(store, [a.id, b.id, c.id])

    async def test_the_type_filter_agrees_too(self, store):
        a, b, c = await self._triangle(store)
        await self._agrees_with_singles(
            store, [a.id, b.id, c.id], edge_type=EdgeType.SUPPORTS
        )

    async def test_every_requested_id_gets_a_key(self, store):
        """Including ids with no matching edges, and ids that are not nodes.

        Absence must mean "you did not ask", never "there were none" — callers
        iterate the map and a missing key would silently skip a node.
        """
        a, b, c = await self._triangle(store)
        result = await store.get_edges_for(
            [a.id, c.id, "no-such-node"], direction="to"
        )

        assert set(result) == {a.id, c.id, "no-such-node"}
        assert result[a.id] == []  # nothing points at `a`
        assert result["no-such-node"] == []
        assert len(result[c.id]) == 3  # b→c, a→c, and c's self-loop

    async def test_a_self_loop_appears_at_both_of_its_endpoints(self, store):
        _, _, c = await self._triangle(store)

        outgoing = await store.get_edges_for([c.id], direction="from")
        incoming = await store.get_edges_for([c.id], direction="to")

        loop = {e.id for e in outgoing[c.id]} & {e.id for e in incoming[c.id]}
        assert len(loop) == 1

    async def test_repeated_ids_collapse_to_one_entry(self, store):
        a, b, _ = await self._triangle(store)
        result = await store.get_edges_for([a.id, a.id, b.id], direction="from")

        assert set(result) == {a.id, b.id}
        assert len(result[a.id]) == 2

    async def test_no_ids_is_no_work_and_an_empty_map(self, store):
        await self._triangle(store)
        assert await store.get_edges_for([], direction="from") == {}
        assert await store.get_edges_for([], direction="to") == {}

    async def test_edges_come_back_whole(self, store):
        """The batched path must not return a projection.

        Callers read `label`, `kind`, `weight` and `metadata` off these edges —
        `list_relations` groups by label+kind — so a `SELECT src_id, dst_id`
        would satisfy every other test here and still break them.
        """
        src = Fact(content="src", source_id="s1")
        dst = Fact(content="dst", source_id="s1")
        await store.store_node(src)
        await store.store_node(dst)
        edge = NodeEdge(
            src_id=src.id,
            dst_id=dst.id,
            type=EdgeType.RELATED,
            label="published_by",
            kind="attribution",
            weight=0.25,
            metadata={"note": "kept"},
        )
        await store.store_edge(edge)

        got = (await store.get_edges_for([src.id], direction="from"))[src.id][0]

        assert got.id == edge.id
        assert got.label == "published_by"
        assert got.kind == "attribution"
        assert got.weight == 0.25
        assert got.metadata == {"note": "kept"}

    async def _hub_and_spokes(self, store, count: int):
        hub = Topic(content="hub", source_id="s1")
        await store.store_node(hub)
        facts = [Fact(content=f"f{i}", source_id="s1") for i in range(count)]
        for fact in facts:
            await store.store_node(fact)
            await store.store_edge(
                NodeEdge(src_id=fact.id, dst_id=hub.id, type=EdgeType.SUPPORTS)
            )
        return hub, facts

    @pytest.mark.parametrize("count", [40, 250])
    async def test_a_large_request_answers_the_same_as_a_small_one(self, store, count):
        """Both sides of the adapter's `IN`-versus-scan seam (#14 step 4).

        SurrealDB evaluates `IN` per row instead of through the index, so past
        a measured size the adapter stops asking for the ids and reads the
        candidate rows instead. That is a second code path, and a second code
        path is where a batched fetch quietly starts dropping rows — so the
        cheap branch and the expensive one are asserted to agree. 40 is below
        the crossover and 250 above it, both small enough for the default suite.
        """
        hub, facts = await self._hub_and_spokes(store, count)

        result = await store.get_edges_for(
            [f.id for f in facts], direction="from", edge_type=EdgeType.SUPPORTS
        )

        assert len(result) == count
        assert all(len(result[f.id]) == 1 for f in facts)
        assert {result[f.id][0].dst_id for f in facts} == {hub.id}

    async def test_the_large_path_returns_only_what_was_asked_for(self, store):
        """The scan branch reads rows nobody asked about; none may leak out.

        It fetches by type rather than by id, so every edge of that type comes
        back and the ids are matched here. A node outside the request must not
        appear in the map, and its edges must not be attributed to one inside.
        """
        hub, facts = await self._hub_and_spokes(store, 150)
        stranger = Fact(content="stranger", source_id="s2")
        await store.store_node(stranger)
        await store.store_edge(
            NodeEdge(src_id=stranger.id, dst_id=hub.id, type=EdgeType.SUPPORTS)
        )

        asked = [f.id for f in facts]
        result = await store.get_edges_for(
            asked, direction="from", edge_type=EdgeType.SUPPORTS
        )

        assert set(result) == set(asked)
        assert stranger.id not in result
        assert all(len(result[node_id]) == 1 for node_id in asked)

    async def test_the_large_path_still_honours_no_type_filter(self, store):
        """Without a type the scan reads the whole edge table — same contract."""
        hub, facts = await self._hub_and_spokes(store, 150)
        extra = NodeEdge(
            src_id=facts[0].id, dst_id=hub.id, type=EdgeType.RELATED, label="also"
        )
        await store.store_edge(extra)

        result = await store.get_edges_for([f.id for f in facts], direction="from")

        assert len(result[facts[0].id]) == 2
        assert all(len(result[f.id]) == 1 for f in facts[1:])


class TestBatchedNodeFetch:
    """`get_nodes` answers many ids in a bounded number of statements (#14 step 4).

    The oracle is `get_node`, one id at a time. On SurrealDB the single-node
    form cannot know which table holds an id, so it probes topic, then fact,
    then inference and pays for each miss — 2,104 round-trips for 900 nodes in
    one `reflect`. Only that cost is meant to change.

    Unlike `get_edges_for`, an id that is not a node is *absent* from the map
    rather than present with an empty value: a list has an empty value and a
    node does not, so `result.get(id)` matches `await get_node(id)`.
    """

    async def _mixed(self, store):
        """One node of each type, since the adapter reads them from three tables."""
        nodes = [
            Topic(content="a topic", source_id="s1"),
            Fact(content="a fact", source_id="s1"),
            Inference(content="an inference", source_id="s1"),
        ]
        for node in nodes:
            await store.store_node(node)
        return nodes

    async def test_it_agrees_with_the_single_node_method(self, store):
        nodes = await self._mixed(store)

        batched = await store.get_nodes([n.id for n in nodes])

        for node in nodes:
            expected = await store.get_node(node.id)
            assert batched[node.id].id == expected.id
            assert batched[node.id].content == expected.content
            assert type(batched[node.id]) is type(expected)

    async def test_an_id_that_is_not_a_node_is_absent(self, store):
        nodes = await self._mixed(store)

        result = await store.get_nodes([nodes[0].id, "no-such-node"])

        assert set(result) == {nodes[0].id}
        assert result.get("no-such-node") is None

    async def test_repeated_ids_collapse_to_one_entry(self, store):
        nodes = await self._mixed(store)
        result = await store.get_nodes([nodes[0].id, nodes[0].id, nodes[1].id])
        assert set(result) == {nodes[0].id, nodes[1].id}

    async def test_no_ids_is_no_work_and_an_empty_map(self, store):
        await self._mixed(store)
        assert await store.get_nodes([]) == {}

    async def test_retired_nodes_come_back_too(self, store):
        """`get_node` ignores status, so this must as well.

        Callers reach for a node by id when they already hold a reference to
        it — a lineage edge, an archived candidate — and filtering by status
        here would turn a batched read into a silently different question.
        """
        nodes = await self._mixed(store)
        await store.update_node_status(
            nodes[1].id, NodeStatus.ARCHIVED, datetime.now(timezone.utc)
        )

        result = await store.get_nodes([n.id for n in nodes])

        assert set(result) == {n.id for n in nodes}
        assert result[nodes[1].id].status is NodeStatus.ARCHIVED

    async def test_nodes_come_back_whole(self, store):
        """Callers read content, value signals and metadata off these."""
        fact = Fact(
            content="whole", source_id="s1",
            value=ValueSignal(importance=0.9), metadata={"note": "kept"},
        )
        await store.store_node(fact)

        got = (await store.get_nodes([fact.id]))[fact.id]

        assert got.content == "whole"
        assert got.value.importance == 0.9
        assert got.metadata == {"note": "kept"}

    async def test_more_ids_than_fit_in_one_statement(self, store):
        """Past the adapter's chunk size, where a batched fetch loses rows."""
        facts = [Fact(content=f"f{i}", source_id="s1") for i in range(300)]
        for fact in facts:
            await store.store_node(fact)

        result = await store.get_nodes([f.id for f in facts])

        assert len(result) == 300
        assert {result[f.id].content for f in facts} == {f"f{i}" for i in range(300)}


class TestBatchedEmbeddingFetch:
    """`get_embeddings_for_items` answers many items in one round-trip (#14 step 4).

    The oracle is `get_embeddings_for_item`. Vectors are the heaviest rows in
    the store and every phase of `reflect` that compares them was reading them
    one item at a time.
    """

    async def _embedded(self, store, count: int, model_id: str = "model-a"):
        facts = [Fact(content=f"f{i}", source_id="s1") for i in range(count)]
        for i, fact in enumerate(facts):
            await store.store_node(fact)
            await store.store_embedding(EmbeddingRecord(
                item_id=fact.id, model_id=model_id, vector=[float(i), 0.5]
            ))
        return facts

    async def test_it_agrees_with_the_single_item_method(self, store):
        facts = await self._embedded(store, 3)

        batched = await store.get_embeddings_for_items([f.id for f in facts])

        for fact in facts:
            expected = await store.get_embeddings_for_item(fact.id)
            assert [e.vector for e in batched[fact.id]] == [e.vector for e in expected]

    async def test_the_model_filter_agrees_too(self, store):
        fact = (await self._embedded(store, 1, model_id="model-a"))[0]
        await store.store_embedding(EmbeddingRecord(
            item_id=fact.id, model_id="model-b", vector=[9.0, 9.0]
        ))

        batched = await store.get_embeddings_for_items([fact.id], model_id="model-a")

        assert len(batched[fact.id]) == 1
        assert batched[fact.id][0].model_id == "model-a"
        assert len(await store.get_embeddings_for_item(fact.id)) == 2

    async def test_every_requested_id_gets_a_key(self, store):
        """Including items with no embedding, and ids that are not items."""
        facts = await self._embedded(store, 1)
        bare = Fact(content="unembedded", source_id="s1")
        await store.store_node(bare)

        result = await store.get_embeddings_for_items(
            [facts[0].id, bare.id, "no-such-item"]
        )

        assert set(result) == {facts[0].id, bare.id, "no-such-item"}
        assert result[bare.id] == []
        assert result["no-such-item"] == []
        assert len(result[facts[0].id]) == 1

    async def test_repeated_ids_collapse_to_one_entry(self, store):
        facts = await self._embedded(store, 2)
        result = await store.get_embeddings_for_items(
            [facts[0].id, facts[0].id, facts[1].id]
        )
        assert set(result) == {facts[0].id, facts[1].id}
        assert len(result[facts[0].id]) == 1

    async def test_no_ids_is_no_work_and_an_empty_map(self, store):
        await self._embedded(store, 1)
        assert await store.get_embeddings_for_items([]) == {}

    async def test_vectors_come_back_whole(self, store):
        """A projection would satisfy every other test here and break ranking."""
        fact = Fact(content="v", source_id="s1")
        await store.store_node(fact)
        await store.store_embedding(EmbeddingRecord(
            item_id=fact.id, model_id="model-a", vector=[0.1, 0.2, 0.3]
        ))

        got = (await store.get_embeddings_for_items([fact.id]))[fact.id][0]

        assert got.vector == [0.1, 0.2, 0.3]
        assert got.model_id == "model-a"
        assert got.item_id == fact.id

    async def test_more_ids_than_fit_in_one_statement(self, store):
        """Past the adapter's chunk size, where a batched fetch loses rows."""
        facts = await self._embedded(store, 300)

        result = await store.get_embeddings_for_items([f.id for f in facts])

        assert len(result) == 300
        assert all(len(result[f.id]) == 1 for f in facts)
        assert {result[f.id][0].vector[0] for f in facts} == {float(i) for i in range(300)}


class TestArchivalStatus:
    """`ARCHIVED` is how an *active* node leaves the active set.

    Everything else that retires a node (supersede, merge) says the node was
    wrong or duplicated. Archival says it was trivial — the hygiene arm of the
    review loop — and it is the first status flip applied to nodes that are
    otherwise perfectly good, so the exclusion has to hold on both backends
    rather than only where it was implemented.
    """

    async def _stored(self, store, node, vector):
        await store.store_node(node)
        await store.store_embedding(
            EmbeddingRecord(item_id=node.id, model_id="test", vector=vector)
        )
        return node

    async def test_archived_nodes_excluded_from_queries(self, store):
        keep = await self._stored(
            store, Fact(content="kept", source_id="s1"), [1.0, 0.0, 0.0]
        )
        junk = await self._stored(
            store, Fact(content="trivial", source_id="s1"), [1.0, 0.0, 0.0]
        )
        at = datetime.now(timezone.utc)

        await store.set_node_status_tx(
            [junk], status=NodeStatus.ARCHIVED, at=at
        )

        active_ids = {n.id for n in await store.query_nodes(status=NodeStatus.ACTIVE)}
        assert keep.id in active_ids
        assert junk.id not in active_ids

        # An exact match on the query vector: only the status filter can hide it.
        found = {item_id for item_id, _ in await store.vector_search(
            [1.0, 0.0, 0.0], "test", k=5
        )}
        assert keep.id in found
        assert junk.id not in found

        archived = await store.query_nodes(status=NodeStatus.ARCHIVED)
        assert [n.id for n in archived] == [junk.id]
        assert archived[0].superseded_at is not None

    async def test_restore_returns_an_archived_node_to_active(self, store):
        junk = await self._stored(
            store, Fact(content="trivial", source_id="s1"), [1.0, 0.0, 0.0]
        )
        await store.set_node_status_tx(
            [junk], status=NodeStatus.ARCHIVED, at=datetime.now(timezone.utc)
        )

        await store.update_node_status(junk.id, NodeStatus.ACTIVE)

        active_ids = {n.id for n in await store.query_nodes(status=NodeStatus.ACTIVE)}
        assert junk.id in active_ids

    async def test_lifecycle_episodes_round_trip(self, store):
        """The episode list is storage state, so both backends must keep it.

        Written as parity rather than as an in-memory test because it is a
        protocol field: a backend that accepts the write and loses the list on
        read makes the durable history silently backend-dependent.
        """
        old = await self._stored(
            store, Fact(content="the earlier claim", source_id="s1"), [1.0, 0.0, 0.0]
        )
        new = Fact(content="what replaced it", source_id="s1")
        at = datetime(2026, 6, 15, tzinfo=timezone.utc)
        await store.supersede_node_tx(
            old, new,
            EmbeddingRecord(item_id=new.id, model_id="test", vector=[1.0, 0.0, 0.0]),
            NodeEdge(src_id=old.id, dst_id=new.id, type=EdgeType.SUPERSEDED_BY),
            status=NodeStatus.HISTORICAL, superseded_at=at,
        )

        retired = await store.get_node(old.id)
        assert len(retired.lifecycle) == 1
        episode = retired.lifecycle[0]
        assert episode.retired_at == at
        assert episode.because is NodeStatus.HISTORICAL
        assert episode.counterpart == new.id
        assert episode.restored_at is None

        came_back = datetime(2026, 6, 20, tzinfo=timezone.utc)
        await store.set_node_status_tx(
            [retired], status=NodeStatus.ACTIVE, at=came_back,
        )

        back = await store.get_node(old.id)
        assert back.status is NodeStatus.ACTIVE
        assert back.superseded_at is None
        # Append-only: the retirement is still there, now with its end.
        assert len(back.lifecycle) == 1
        assert back.lifecycle[0].retired_at == at
        assert back.lifecycle[0].restored_at == came_back

    async def test_archival_appends_an_episode_without_a_counterpart(self, store):
        """Archival retires a node too, and nothing superseded it. The episode
        records the retirement; `counterpart` is honestly empty rather than
        borrowed from somewhere."""
        junk = await self._stored(
            store, Fact(content="trivial", source_id="s1"), [1.0, 0.0, 0.0]
        )
        at = datetime(2026, 6, 15, tzinfo=timezone.utc)
        await store.set_node_status_tx(
            [junk], status=NodeStatus.ARCHIVED, at=at,
        )

        archived = await store.get_node(junk.id)
        assert [(e.retired_at, e.because, e.counterpart) for e in archived.lifecycle] == [
            (at, NodeStatus.ARCHIVED, None)
        ]

    async def test_archival_flip_is_atomic(self, store):
        """A failure part-way through leaves *every* node active.

        Half an approved archival batch is worse than none of it: the agent is
        told what was archived, and a partial flip makes that report a lie.
        """
        nodes = [
            await self._stored(
                store, Fact(content=f"junk {i}", source_id="s1"), [1.0, 0.0, 0.0]
            )
            for i in range(3)
        ]
        missing = Fact(content="never stored", source_id="s1")

        with pytest.raises(Exception):
            await store.set_node_status_tx(
                [*nodes, missing],
                status=NodeStatus.ARCHIVED,
                at=datetime.now(timezone.utc),
            )

        active_ids = {n.id for n in await store.query_nodes(status=NodeStatus.ACTIVE)}
        assert {n.id for n in nodes} <= active_ids
