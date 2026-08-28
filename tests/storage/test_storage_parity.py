"""Backend-parity tests for the StorageBackend protocol.

Every test here runs against **both** backends. Bugs have survived precisely
because `InMemoryStorage` and `SurrealDBStorage` diverged and only the
in-memory one was exercised: the in-memory store upserts via dict assignment,
while SurrealDB used `INSERT INTO`, which a UNIQUE index on `uid` causes to be
*silently ignored* on re-store.

Anything the protocol promises but the two backends could implement
differently belongs in this file.
"""

from datetime import datetime, timedelta, timezone

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
    Inference,
    Metacontext,
    NodeEdge,
    NodeStatus,
    NodeType,
    RawDocument,
    Segment,
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

    async def test_an_unrated_confidence_round_trips_as_absent(self, store):
        """The confidence prior: absence has to survive the trip, or the field is 0.5 again.

        `confidence` is the one value field where "nobody rated this" is a
        distinct state, and it is expressed by the key being missing. A backend
        that writes `None` as a stored null, or refills the old 0.5 default on
        read, turns the distinction back into the number it replaced.
        """
        topic = Topic(content="unrated", source_id="s1")
        assert topic.value.confidence is None
        await store.store_node(topic)

        got = await store.get_node(topic.id)
        assert got is not None
        assert got.value.confidence is None

    async def test_a_deliberate_middling_confidence_is_not_absence(self, store):
        """The other half: a rated 0.5 must not come back as unrated."""
        topic = Topic(
            content="considered, and middling", source_id="s1",
            value=ValueSignal(confidence=0.5),
        )
        await store.store_node(topic)

        got = await store.get_node(topic.id)
        assert got is not None
        assert got.value.confidence == pytest.approx(0.5)

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


class TestValidityRoundTrips:
    """Validity is a list of nested models on an edge — the shape most likely to
    diverge between a backend that keeps objects and one that serializes them.

    The endpoint union is the specific risk: it round-trips through a
    discriminator, and a backend that lost the key would hand back a different
    endpoint state — turning *we do not know when this started* into *it has no
    start*, which is the one distinction the whole type exists to keep.
    """

    def _leningrad(self) -> list[ValidityInterval]:
        return [
            ValidityInterval(
                start=UnknownInstant(),
                end=PreciseInstant(at=datetime(1991, 9, 6, tzinfo=timezone.utc)),
                witnessed_at=PreciseInstant(at=datetime(1970, 1, 1, tzinfo=timezone.utc)),
                basis=IntervalBasis.INFERRED,
            ),
            ValidityInterval(
                start=NamedInstant(label="during the Renaissance"),
                end=UnboundedInstant(),
                timeline_id="in-universe",
                basis=IntervalBasis.STATED,
            ),
        ]

    async def test_an_edges_intervals_survive_the_round_trip(self, store):
        edge = NodeEdge(
            src_id="fact-1", dst_id="doc-1", type=EdgeType.SOURCED_FROM,
            validity=self._leningrad(),
        )
        await store.store_edge(edge)

        got = await store.get_edges_from("fact-1")
        assert len(got) == 1
        assert got[0].validity == self._leningrad()

    async def test_the_endpoint_states_stay_distinct(self, store):
        """`unknown` must not read back as `unbounded`, or the reverse."""
        edge = NodeEdge(
            src_id="fact-2", dst_id="doc-1", type=EdgeType.SOURCED_FROM,
            validity=self._leningrad(),
        )
        await store.store_edge(edge)

        first, second = (await store.get_edges_from("fact-2"))[0].validity
        assert isinstance(first.start, UnknownInstant)
        assert isinstance(second.end, UnboundedInstant)
        assert isinstance(second.start, NamedInstant)
        assert second.start.label == "during the Renaissance"

    async def test_an_edge_written_before_the_field_existed_reads_back_empty(self, store):
        """Absence is the overwhelming case, and must not become a null."""
        edge = NodeEdge(src_id="fact-3", dst_id="doc-1", type=EdgeType.SOURCED_FROM)
        await store.store_edge(edge)

        got = await store.get_edges_from("fact-3")
        assert got[0].validity == []

    async def test_a_documents_publication_date_survives_the_round_trip(self, store):
        doc = RawDocument(
            content="a 1970 memoir",
            published_at=PreciseInstant(at=datetime(1970, 6, 1, tzinfo=timezone.utc)),
        )
        await store.store_document(doc)

        got = await store.get_document(doc.id)
        assert got is not None
        assert got.published_at == doc.published_at
        assert got.created_at != got.published_at.at

    async def test_an_undated_document_reads_back_undated(self, store):
        doc = RawDocument(content="undated")
        await store.store_document(doc)

        got = await store.get_document(doc.id)
        assert got is not None
        assert got.published_at is None


HOSTILE_GRAPH_NAMES = [
    "pwn`; REMOVE DATABASE `victim",
    "a;b",
    "x`; REMOVE NAMESPACE epimemer; --",
    "has space",
    "",
    "z" * 65,
]


class TestVectorSearchStatusFilter:
    """Which nodes may be nominated is the caller's to say, and the default is
    the guard this method used to enforce by construction.

    The whole of recurrence hangs off this parameter: until a historical twin
    could be nominated, nobody was ever asked whether a claim had come back, and
    ingest wrote a second node saying what the first one said.
    """

    async def _graph(self, store) -> dict[NodeStatus, Fact]:
        by_status = {}
        for status in (NodeStatus.ACTIVE, NodeStatus.HISTORICAL, NodeStatus.CORRECTED):
            fact = Fact(content=f"a claim, {status.value}", source_id="s1", status=status)
            await store.store_node(fact)
            await store.store_embedding(EmbeddingRecord(
                item_id=fact.id, model_id="m", vector=[1.0, 0.0, 0.0],
            ))
            by_status[status] = fact
        return by_status

    async def test_the_default_returns_only_active_nodes(self, store):
        facts = await self._graph(store)

        hits = await store.vector_search([1.0, 0.0, 0.0], "m", k=10)

        assert [i for i, _ in hits] == [facts[NodeStatus.ACTIVE].id]

    async def test_asking_for_historical_nominates_the_twin(self, store):
        facts = await self._graph(store)

        hits = await store.vector_search(
            [1.0, 0.0, 0.0], "m", k=10,
            statuses=frozenset({NodeStatus.ACTIVE, NodeStatus.HISTORICAL}),
        )

        assert {i for i, _ in hits} == {
            facts[NodeStatus.ACTIVE].id, facts[NodeStatus.HISTORICAL].id
        }

    async def test_a_corrected_claim_stays_out_unless_named(self, store):
        """A claim concluded *wrong* has no route back, so nominating it would
        invite a verdict nothing can record."""
        facts = await self._graph(store)

        hits = await store.vector_search(
            [1.0, 0.0, 0.0], "m", k=10,
            statuses=frozenset({NodeStatus.ACTIVE, NodeStatus.HISTORICAL}),
        )

        assert facts[NodeStatus.CORRECTED].id not in {i for i, _ in hits}

    async def test_an_empty_status_set_returns_nothing(self, store):
        await self._graph(store)

        assert await store.vector_search(
            [1.0, 0.0, 0.0], "m", k=10, statuses=frozenset()
        ) == []

    async def test_k_counts_results_not_rows_examined(self, store):
        """The filter runs before the truncation, on both backends.

        A backend that truncated first would return fewer than `k` usable hits
        whenever a retired node ranked above an active one — which is exactly
        the graph shape recurrence creates.
        """
        for i in range(6):
            fact = Fact(
                content=f"claim {i}", source_id="s1",
                status=NodeStatus.CORRECTED if i < 4 else NodeStatus.ACTIVE,
            )
            await store.store_node(fact)
            await store.store_embedding(EmbeddingRecord(
                item_id=fact.id, model_id="m", vector=[1.0, 0.0, 0.0],
            ))

        hits = await store.vector_search([1.0, 0.0, 0.0], "m", k=2)

        assert len(hits) == 2


class TestStatusFlipCanCarryAnEdge:
    """Reactivation writes the flip and its provenance together.

    A node back to ACTIVE with no edge recording *why* is an assertion the graph
    makes and cannot attribute, and two transactions can leave exactly that
    state behind.
    """

    async def test_the_edge_lands_with_the_flip(self, store):
        fact = Fact(content="Labour is in government", source_id="s1",
                    status=NodeStatus.HISTORICAL)
        await store.store_node(fact)

        await store.set_node_status_tx(
            [fact], status=NodeStatus.ACTIVE, at=datetime.now(timezone.utc),
            edges=[NodeEdge(
                src_id=fact.id, dst_id="doc-2026", type=EdgeType.SOURCED_FROM,
                validity=[ValidityInterval(
                    start=PreciseInstant(at=datetime(2024, 7, 5, tzinfo=timezone.utc)),
                    basis=IntervalBasis.STATED,
                )],
            )],
        )

        back = await store.get_node(fact.id)
        assert back.status is NodeStatus.ACTIVE
        edges = await store.get_edges_from(fact.id, edge_type=EdgeType.SOURCED_FROM)
        assert len(edges) == 1
        assert edges[0].validity[0].start.at.year == 2024

    async def test_a_flip_without_edges_creates_nothing(self, store):
        """Every other caller passes none, and archival's guarantee is unchanged."""
        fact = Fact(content="trivial", source_id="s1", status=NodeStatus.ARCHIVED)
        await store.store_node(fact)

        await store.set_node_status_tx(
            [fact], status=NodeStatus.ACTIVE, at=datetime.now(timezone.utc),
        )

        assert await store.get_edges_from(fact.id) == []


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
        await store.set_node_status_tx(
            [topic], status=NodeStatus.SUPERSEDED, at=datetime.now(timezone.utc)
        )

        # An exact match on the query vector: only the status filter can hide it.
        results = await store.vector_search([1.0, 0.0, 0.0], "test", k=5)

        assert all(item_id != topic.id for item_id, _ in results)

    async def test_merged_nodes_never_resurface(self, store):
        topic = await self._stored(
            store, Topic(content="ML", source_id="s1"), [1.0, 0.0, 0.0]
        )
        await store.set_node_status_tx(
            [topic], status=NodeStatus.MERGED, at=datetime.now(timezone.utc)
        )

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
        await store.set_node_status_tx(
            [topic], status=NodeStatus.SUPERSEDED, at=datetime.now(timezone.utc)
        )

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
        await store.set_node_status_tx(
            retired, status=NodeStatus.SUPERSEDED, at=datetime.now(timezone.utc)
        )
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
    """get_edges_for answers many nodes in one call.

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
        """Both sides of the adapter's `IN`-versus-scan seam.

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
    """get_nodes answers many ids in a bounded number of statements.

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
        await store.set_node_status_tx(
            [nodes[1]], status=NodeStatus.ARCHIVED, at=datetime.now(timezone.utc)
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
    """get_embeddings_for_items answers many items in one round-trip.

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

        await store.set_node_status_tx(
            [junk], status=NodeStatus.ACTIVE, at=datetime.now(timezone.utc)
        )

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
            NodeEdge(src_id=old.id, dst_id=new.id,
                     type=EdgeType.TEMPORALLY_FOLLOWED_BY),
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


class TestLifecycleComesFromTheStoredRow:
    """A retirement appends to the history **in the database**, not the caller's.

    Every transaction here takes node objects, and each one has to decide
    whether the argument is a *request* — which nodes to retire — or a
    *snapshot* of their state. It is a request: a caller holding a node it
    loaded before an earlier retirement carries a stale `lifecycle`, and
    appending to that silently drops every episode since.

    Parity rather than a SurrealDB test because the two backends answered
    differently: `InMemoryStorage` always re-read the stored node, so the same
    call produced two different histories depending on where it ran. The failure
    is invisible from behaviour — the node ends up with the right status either
    way, and only the record of how it got there is short.
    """

    RETIRED = datetime(2026, 1, 1, tzinfo=timezone.utc)
    CAME_BACK = datetime(2026, 2, 1, tzinfo=timezone.utc)
    AGAIN = datetime(2026, 3, 1, tzinfo=timezone.utc)

    async def _out_and_back(self, store, content: str):
        """A node that has left the active set once and returned.

        Returns it with a copy of itself as it was *before* any of that — the
        object a caller that loaded early would still be holding.
        """
        node = Fact(content=content, source_id="s1")
        await store.store_node(node)
        stale = node.model_copy(deep=True)
        await store.set_node_status_tx(
            [node], status=NodeStatus.ARCHIVED, at=self.RETIRED
        )
        await store.set_node_status_tx(
            [node], status=NodeStatus.ACTIVE, at=self.CAME_BACK
        )
        return node, stale

    async def test_supersede_appends_to_the_stored_history(self, store):
        old, stale = await self._out_and_back(store, "the earlier claim")
        new = Fact(content="the corrected claim", source_id="s2")

        await store.supersede_node_tx(
            stale,
            new,
            EmbeddingRecord(item_id=new.id, model_id="test", vector=[0.0, 1.0, 0.0]),
            NodeEdge(src_id=old.id, dst_id=new.id, type=EdgeType.SUPERSEDED_BY),
            status=NodeStatus.CORRECTED,
            superseded_at=self.AGAIN,
        )

        stored = await store.get_node(old.id)
        assert [(e.retired_at, e.restored_at) for e in stored.lifecycle] == [
            (self.RETIRED, self.CAME_BACK),
            (self.AGAIN, None),
        ]
        assert stored.lifecycle[-1].counterpart == new.id

    async def test_supersede_by_existing_appends_to_the_stored_history(self, store):
        old, stale = await self._out_and_back(store, "the earlier claim")
        keeper = Fact(content="the claim already there", source_id="s2")
        await store.store_node(keeper)

        await store.supersede_by_existing_tx(
            stale,
            keeper.id,
            NodeEdge(src_id=old.id, dst_id=keeper.id, type=EdgeType.SUPERSEDED_BY),
            status=NodeStatus.CORRECTED,
            superseded_at=self.AGAIN,
        )

        stored = await store.get_node(old.id)
        assert [(e.retired_at, e.restored_at) for e in stored.lifecycle] == [
            (self.RETIRED, self.CAME_BACK),
            (self.AGAIN, None),
        ]
        assert stored.lifecycle[-1].counterpart == keeper.id

    async def test_archiving_appends_to_the_stored_history(self, store):
        node, stale = await self._out_and_back(store, "trivial")

        await store.set_node_status_tx(
            [stale], status=NodeStatus.ARCHIVED, at=self.AGAIN
        )

        stored = await store.get_node(node.id)
        assert [(e.retired_at, e.restored_at) for e in stored.lifecycle] == [
            (self.RETIRED, self.CAME_BACK),
            (self.AGAIN, None),
        ]

    async def test_restoring_closes_the_stored_open_episode(self, store):
        """The return branch fails the other way: it writes *less*, not more.

        `with_return` is a no-op on a history with nothing open, so a caller's
        pre-archival copy would have written an empty list straight over the
        episode that was there — losing the retirement as well as its return.
        """
        node = Fact(content="trivial", source_id="s1")
        await store.store_node(node)
        stale = node.model_copy(deep=True)
        await store.set_node_status_tx(
            [node], status=NodeStatus.ARCHIVED, at=self.RETIRED
        )

        await store.set_node_status_tx(
            [stale], status=NodeStatus.ACTIVE, at=self.CAME_BACK
        )

        stored = await store.get_node(node.id)
        assert [(e.retired_at, e.restored_at) for e in stored.lifecycle] == [
            (self.RETIRED, self.CAME_BACK)
        ]


class TestLexicalSearch:
    """`text_search` is the first protocol method where the backends genuinely
    differ: SurrealDB scores in-engine with its own analyzer, the in-memory
    backend scores in Python (`storage/bm25.py`). Exact score parity is not
    achievable — the stemmers alone disagree — so what is asserted here is what
    is achievable and what callers actually depend on: the same *set* of hits
    for an unambiguous term, the same order where scores are unambiguous, and
    the same answers to the three rules that decide membership.

    Corpora here are five documents wide on purpose. IDF is `log((N - n + 0.5) /
    (n + 0.5))`, so nothing can score above zero until a term is in fewer than
    half the corpus: a two-document fixture makes every result empty and reads
    as a broken implementation.
    """

    TICKETS = (
        "Ticket JIRA-4417 was closed after the deployment rollback",
        "Ticket JIRA-4418 remains open pending the deployment review",
        "The deployment pipeline was rewritten last quarter",
        "Nothing here concerns tickets at all",
        "A note about the weather this morning",
    )

    async def _facts(self, store, contents=None) -> list[Fact]:
        facts = [
            Fact(content=content, source_id="s1")
            for content in (contents if contents is not None else self.TICKETS)
        ]
        for fact in facts:
            await store.store_node(fact)
        return facts

    async def test_text_search_agrees_across_backends_on_a_rare_term(self, store):
        """Set parity, §4: the rare identifier finds its node and only its node.

        The near-miss shares every token of `JIRA-4417` except `4417`, so a
        backend whose match is disjunctive rather than conjunctive returns both
        and fails here.
        """
        facts = await self._facts(store)

        hits = await store.text_search(
            ["JIRA-4417"], corpus="nodes", node_type=NodeType.FACT
        )

        assert [node_id for node_id, _ in hits] == [facts[0].id]

    async def test_text_search_is_or_across_terms(self, store):
        """§2.4's trap, asserted against `text_search` directly.

        Never route this through `search`: the vector arm supplies results even
        when a conjunctive lexical arm returns nothing, so the fused version of
        this test passes without testing anything.

        Both halves matter. A single `@@` over both terms returns `[]` — the
        multi-word prose query that silently degrades to vector-only. And the
        scores must be *unchanged* by the absent term, or "contributes nothing"
        would mean "contributes something negative".
        """
        await self._facts(store)

        both = await store.text_search(
            ["rollback", "zzzznotpresent"], corpus="nodes", node_type=NodeType.FACT
        )
        alone = await store.text_search(
            ["rollback"], corpus="nodes", node_type=NodeType.FACT
        )

        assert [node_id for node_id, _ in both] == [node_id for node_id, _ in alone]
        assert both
        for (_, with_absent), (_, without) in zip(both, alone):
            assert with_absent == pytest.approx(without, rel=1e-9)

    async def test_zero_scored_matches_never_reach_fusion(self, store):
        """R1: the IDF floor zeroes the score, and the hit list drops the row.

        `deployment` is in three of five facts. It still *matches* them — the
        clamp is on the score, not on membership — and a zero-scored row
        surviving into rank fusion would arrive at an arbitrary tie rank and
        fuse almost as strongly as the best real hit.
        """
        await self._facts(store)

        assert await store.text_search(
            ["deployment"], corpus="nodes", node_type=NodeType.FACT
        ) == []

    async def test_a_rare_term_outranks_a_less_rare_one(self, store):
        """Order parity where the scores are unambiguous, §4. Within one list
        only — there is no order across node types to assert.

        The tokens are invented rather than English because one backend stems
        and the other does not: an ordinary word here would be asserting that
        Snowball and this test agree about its root, which is the one thing §4
        says not to depend on.
        """
        await self._facts(store, [
            "zqxx wobble",
            "wobble frunk",
            "grelt frunk",
            "plink grelt",
            "snorf plink",
        ])

        hits = await store.text_search(
            ["zqxx", "wobble"], corpus="nodes", node_type=NodeType.FACT
        )

        # `zqxx` is in one of five and `wobble` in two, so the fact holding both
        # outranks the fact holding only the commoner one.
        assert len(hits) == 2
        assert hits[0][1] > hits[1][1]

    async def test_same_term_clamps_in_one_table_and_scores_in_another(self, store):
        """R5/R6: BM25's corpus is one node table, not the graph.

        `orbit` is in three of five facts and one of five topics. It says
        nothing about which fact you want and quite a lot about which topic —
        which is the honest reading, not an artefact. A backend that scored one
        merged corpus gets a single answer for both and fails here.
        """
        await self._facts(store, [
            "orbit correction burn scheduled",
            "orbit decay measured again",
            "orbit insertion completed",
            "unrelated ground station note",
            "another unrelated note entirely",
        ])
        for content in (
            "orbit",
            "ground stations",
            "telemetry",
            "propulsion",
            "mission planning",
        ):
            await store.store_node(Topic(content=content, source_id="s1"))

        facts = await store.text_search(
            ["orbit"], corpus="nodes", node_type=NodeType.FACT
        )
        topics = await store.text_search(
            ["orbit"], corpus="nodes", node_type=NodeType.TOPIC
        )

        assert facts == []
        assert len(topics) == 1 and topics[0][1] > 0

    async def test_text_search_requires_a_node_type_for_the_node_corpus(self, store):
        """R5, as a contract rather than a convention: there is no cross-table
        score with which to merge two node types into one ranked list."""
        with pytest.raises(ValueError):
            await store.text_search(["anything"], corpus="nodes")

    async def test_a_corrected_node_is_not_a_lexical_seed(self, store):
        """R7: the index matches every row whatever its status; the gate does not.

        A CORRECTED node is a claim concluded *wrong*. Without a status gate it
        comes back as a lexical seed, ranked highest exactly when it holds the
        rare identifier the caller searched for.
        """
        facts = await self._facts(store)
        await store.set_node_status_tx(
            [facts[0]], status=NodeStatus.CORRECTED, at=datetime.now(timezone.utc)
        )

        assert await store.text_search(
            ["JIRA-4417"], corpus="nodes", node_type=NodeType.FACT
        ) == []
        # ...and it is reachable when explicitly asked for, exactly as
        # `query_nodes` treats status.
        corrected = await store.text_search(
            ["JIRA-4417"],
            corpus="nodes",
            node_type=NodeType.FACT,
            statuses=frozenset({NodeStatus.CORRECTED}),
        )
        assert [node_id for node_id, _ in corrected] == [facts[0].id]

    async def test_both_seed_routes_take_the_same_status_set(self, store):
        """History-by-default retrieval: a hybrid search asks one set of both arms, so both take a set.

        The asymmetry this closes was real and one-sided — `vector_search` went
        plural for recurrence while this stayed singular — and it would have
        surfaced as a node reachable only by a rare identifier going missing
        exactly when the caller asked to see history.
        """
        facts = await self._facts(store)
        await store.set_node_status_tx(
            [facts[0]], status=NodeStatus.HISTORICAL, at=datetime.now(timezone.utc)
        )

        both = await store.text_search(
            ["JIRA-4417", "rollback"],
            corpus="nodes",
            node_type=NodeType.FACT,
            statuses=frozenset({NodeStatus.ACTIVE, NodeStatus.HISTORICAL}),
        )
        assert facts[0].id in {node_id for node_id, _ in both}

    async def test_retiring_a_node_does_not_change_what_the_rest_score(self, store):
        """The statistics note in R7, pinned so nobody chases it as a bug.

        `WHERE` exclusion does not change the index's corpus counts, so IDF is
        computed over every row in the table however many are retired. A backend
        that scored only what it was about to return would compute different
        numbers from the same graph.
        """
        facts = await self._facts(store)
        before = await store.text_search(
            ["rollback"], corpus="nodes", node_type=NodeType.FACT
        )

        await store.set_node_status_tx(
            [facts[2]], status=NodeStatus.CORRECTED, at=datetime.now(timezone.utc)
        )
        after = await store.text_search(
            ["rollback"], corpus="nodes", node_type=NodeType.FACT
        )

        assert [node_id for node_id, _ in before] == [node_id for node_id, _ in after]
        assert before[0][1] == pytest.approx(after[0][1], rel=1e-9)

    async def test_text_search_finds_an_identifier_in_a_segment(self, store):
        """The other corpus, §1.1. Segments keep the raw text a paraphrased fact
        may have dropped, and they have no status to gate on."""
        segments = [
            Segment(source_id="d1", text=text, span_start=0, span_end=len(text))
            for text in self.TICKETS
        ]
        for segment in segments:
            await store.store_segment(segment)

        hits = await store.text_search(["JIRA-4417"], corpus="segments")

        assert [segment_id for segment_id, _ in hits] == [segments[0].id]

    async def test_exact_containment_outranks_scattered_cooccurrence(self, store):
        """R8: adjacency is evidence, and BM25 cannot see it.

        A conjunctive token match asks only whether all three of `jira`, `-` and
        `4417` are somewhere in the document — "JIRA - 4417" twice in a short
        note satisfies it, scores *higher* than the real ticket (more term
        frequency, fewer words to normalise by), and the full-text index has no
        phrase query to say otherwise. Containment of the original string is the
        only adjacency signal available, so it decides the order and the score
        decides ties within each half.
        """
        facts = await self._facts(store, [
            "Ticket JIRA-4417 was closed after the deployment rollback",
            "JIRA - 4417 JIRA - 4417 quick note",
            "The kitchen tap on floor two has been fixed",
            "Quarterly revenue exceeded the forecast",
            "A note about the weather this morning",
        ])

        plain = dict(await store.text_search(
            ["JIRA-4417"], corpus="nodes", node_type=NodeType.FACT
        ))
        verified = await store.text_search(
            ["JIRA-4417"],
            corpus="nodes",
            node_type=NodeType.FACT,
            verify_containment=True,
        )

        # Both are token matches, and on score alone the scattered one wins.
        assert plain[facts[1].id] > plain[facts[0].id]
        assert [node_id for node_id, _ in verified] == [facts[0].id, facts[1].id]

    async def test_a_longer_identifier_is_not_a_containment_match(self, store):
        """The other half of R8: token boundaries exclude what containment cannot.

        `JIRA-44170` *contains* the string `JIRA-4417`, so a containment scan on
        its own would return the wrong ticket. It is not a candidate: digit runs
        tokenize whole, so `44170` is not `4417` and the token match never offers
        it for checking. The two filters run in this order for that reason.
        """
        facts = await self._facts(store, [
            "Ticket JIRA-4417 was closed after the deployment rollback",
            "Ticket JIRA-44170 tracks the follow-up work",
            "The kitchen tap on floor two has been fixed",
            "Quarterly revenue exceeded the forecast",
            "A note about the weather this morning",
        ])

        hits = await store.text_search(
            ["JIRA-4417"],
            corpus="nodes",
            node_type=NodeType.FACT,
            verify_containment=True,
        )

        assert [node_id for node_id, _ in hits] == [facts[0].id]

    async def test_text_search_without_terms_returns_nothing(self, store):
        await self._facts(store)
        assert await store.text_search(
            [], corpus="nodes", node_type=NodeType.FACT
        ) == []

    async def test_text_search_honours_k(self, store):
        await self._facts(store, [
            "alpha rollback one",
            "alpha rollback two",
            "beta note three",
            "gamma note four",
            "delta note five",
        ])

        hits = await store.text_search(
            ["rollback"], corpus="nodes", node_type=NodeType.FACT, k=1
        )

        assert len(hits) == 1


class TestNodesBySource:
    """The bridge a segment hit crosses to reach the graph.

    A node's `source_id` is the id of the segment it was extracted from, and
    until now nothing could walk that edge in bulk. The map's shape follows
    `get_edges_for` rather than `get_nodes`: a segment with no extracted nodes
    has an empty list, and callers iterate the map, so a missing key would
    silently skip a segment.
    """

    async def test_returns_the_nodes_extracted_from_each_segment(self, store):
        first = Segment(source_id="d1", text="one", span_start=0, span_end=3)
        second = Segment(source_id="d1", text="two", span_start=3, span_end=6)
        for segment in (first, second):
            await store.store_segment(segment)

        topic = Topic(content="a topic", source_id=first.id)
        fact = Fact(content="a fact", source_id=first.id)
        inference = Inference(content="an inference", source_id=second.id)
        for node in (topic, fact, inference):
            await store.store_node(node)

        by_source = await store.get_nodes_by_source([first.id, second.id])

        assert {n.id for n in by_source[first.id]} == {topic.id, fact.id}
        assert [n.id for n in by_source[second.id]] == [inference.id]

    async def test_every_requested_id_is_a_key(self, store):
        segment = Segment(source_id="d1", text="one", span_start=0, span_end=3)
        await store.store_segment(segment)

        by_source = await store.get_nodes_by_source([segment.id, "not-a-segment"])

        assert by_source[segment.id] == []
        assert by_source["not-a-segment"] == []

    async def test_repeated_ids_collapse_and_an_empty_request_is_empty(self, store):
        segment = Segment(source_id="d1", text="one", span_start=0, span_end=3)
        await store.store_segment(segment)
        fact = Fact(content="a fact", source_id=segment.id)
        await store.store_node(fact)

        assert await store.get_nodes_by_source([]) == {}
        repeated = await store.get_nodes_by_source([segment.id, segment.id])
        assert [n.id for n in repeated[segment.id]] == [fact.id]

    async def test_nodes_come_back_at_any_status(self, store):
        """The gate belongs to the caller, not here.

        R7 filters bridged nodes exactly as it filters direct lexical seeds — but
        it filters them *there*, so that the one rule lives in one place. A
        bridge that pre-filtered would make `status=CORRECTED` unreachable
        through it while the direct route still honoured it.
        """
        segment = Segment(source_id="d1", text="one", span_start=0, span_end=3)
        await store.store_segment(segment)
        retired = Fact(content="a retired fact", source_id=segment.id)
        await store.store_node(retired)
        await store.set_node_status_tx(
            [retired], status=NodeStatus.CORRECTED, at=datetime.now(timezone.utc)
        )

        by_source = await store.get_nodes_by_source([segment.id])

        assert [n.id for n in by_source[segment.id]] == [retired.id]
        assert by_source[segment.id][0].status is NodeStatus.CORRECTED


class TestBatchedSegmentFetch:
    """Segments by their own id — the only thing a lexical hit knows about them.

    Every other route into the segment table goes through the document that
    contains it, which a hit over the segment corpus does not have.
    """

    async def test_returns_the_requested_segments(self, store):
        segments = [
            Segment(source_id="d1", text=f"segment {i}", span_start=i, span_end=i + 1)
            for i in range(3)
        ]
        for segment in segments:
            await store.store_segment(segment)

        found = await store.get_segments([segments[0].id, segments[2].id])

        assert set(found) == {segments[0].id, segments[2].id}
        assert found[segments[0].id].text == "segment 0"
        assert found[segments[2].id].source_id == "d1"

    async def test_unknown_ids_are_absent_rather_than_none(self, store):
        segment = Segment(source_id="d1", text="only", span_start=0, span_end=4)
        await store.store_segment(segment)

        found = await store.get_segments([segment.id, "not-a-segment"])

        assert set(found) == {segment.id}
        assert found.get("not-a-segment") is None

    async def test_repeated_ids_collapse_and_an_empty_request_is_empty(self, store):
        segment = Segment(source_id="d1", text="only", span_start=0, span_end=4)
        await store.store_segment(segment)

        assert await store.get_segments([]) == {}
        assert set(await store.get_segments([segment.id, segment.id])) == {segment.id}


class TestTimestampsAtAWholeSecond:
    """The divergence this fixture exists to catch, and nearly missed.

    SurrealDB stores timestamps as ISO strings. Comparing them as strings is
    chronologically correct only while every rendering has the same shape — and
    Pydantic omits the fractional part when it is exactly zero, so a row written
    at `…:41Z` sorted *after* a bound at `…:41.500000Z`, because `"Z" > "."`,
    and dropped out of a window it belonged in.

    **It survived because `datetime.now()` essentially never lands on a whole
    second**, so every other timestamp in this file is constructed by accident
    rather than on purpose. Fixed by comparing instants instead of spellings —
    `surrealdb_adapter.instant()` — and pinned here, because the next person to
    write a timestamp comparison will reach for `>=` first.
    """

    WHOLE = datetime(2026, 8, 23, 12, 0, 41, tzinfo=timezone.utc)
    HALF_PAST = datetime(2026, 8, 23, 12, 0, 41, 500000, tzinfo=timezone.utc)

    async def test_a_node_created_on_a_whole_second_existed_a_moment_later(
        self, store
    ):
        node = Fact(content="x", source_id="s1", created_at=self.WHOLE)
        await store.store_node(node)

        found = await store.query_nodes(at_time=self.HALF_PAST)

        assert [n.id for n in found] == [node.id]

    async def test_a_node_retired_on_a_whole_second_is_gone_a_moment_later(
        self, store
    ):
        """The other direction, and the one that reads as *history was rewritten*
        rather than *a node is missing*."""
        node = Fact(
            content="x", source_id="s1",
            created_at=self.WHOLE - timedelta(days=1),
        )
        await store.store_node(node)
        await store.set_node_status_tx(
            [node], status=NodeStatus.CORRECTED, at=self.WHOLE
        )

        found = await store.query_nodes(at_time=self.HALF_PAST)

        assert found == []

    async def test_a_lifecycle_window_sees_a_whole_second_retirement(self, store):
        """`query_changes` reads the episodes rather than `superseded_at`, and
        compares the same way."""
        node = Fact(
            content="x", source_id="s1",
            created_at=self.WHOLE - timedelta(days=1),
        )
        await store.store_node(node)
        await store.set_node_status_tx(
            [node], status=NodeStatus.CORRECTED, at=self.WHOLE
        )

        changed = await store.query_changes(
            start=self.WHOLE - timedelta(seconds=1),
            end=self.WHOLE + timedelta(seconds=1),
        )

        assert [n.id for n in changed] == [node.id]

    async def test_a_fractional_timestamp_is_unaffected(self, store):
        """The control. Nothing was ever wrong with the comparison itself — only
        with two renderings of it, which is why this looked fine for months."""
        node = Fact(
            content="x", source_id="s1",
            created_at=self.WHOLE.replace(microsecond=1),
        )
        await store.store_node(node)

        found = await store.query_nodes(at_time=self.HALF_PAST)

        assert [n.id for n in found] == [node.id]
