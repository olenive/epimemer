"""Graph bundles: the round trip is the definition of lossless.

Export a rich graph, import it into an empty graph on the *other* backend,
export that, and compare the files byte for byte. Anything a field this module
fails to read would show up as a difference, and anything a backend renders
differently would show up as a difference across the pair — which is why the
round trip runs both directions rather than in-memory to in-memory.

Vectors are the deliberate exception: they are not in the bundle, and import
recomputes them. The test that they come back identical under the same provider
is here too, because "not carried" has to mean "recomputed", not "lost".
"""

import json
from datetime import UTC, datetime, timedelta

import pytest

from epimemer.core.advisories import AdvisoryAction, AdvisoryKind
from epimemer.core.temporal import (
    IntervalBasis,
    NamedInstant,
    PreciseInstant,
    UnboundedInstant,
    ValidityInterval,
)
from epimemer.core.types import (
    Agent,
    AgentDescription,
    ClaimKind,
    DecisionKind,
    DecisionRecord,
    EdgeType,
    EmbeddingRecord,
    Fact,
    Inference,
    JudgeRef,
    LifecycleEpisode,
    Metacontext,
    NodeEdge,
    NodeStatus,
    RawDocument,
    RelationLabel,
    RelationVerdict,
    Segment,
    Timeline,
    Timepoint,
    Topic,
    ValueSignal,
    description_digest,
    retired,
)
from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.pipelines.embedding_text import embedding_text
from epimemer.pipelines.transfer import (
    BUNDLE_FORMAT_VERSION,
    BundleFormatError,
    bundle_bytes,
    export_graph,
    import_graph,
    prune_bundles,
    read_bundle,
    section_counts,
    stale_bundles,
    write_bundle,
)
from epimemer.storage.memory import InMemoryStorage
from epimemer.storage.protocol import MergeOverrides, WarningOverrides
from epimemer.storage.surrealdb_adapter import SurrealDBStorage

AT = datetime(2026, 3, 4, 5, 6, 7, 890123, tzinfo=UTC)
# A whole second, deliberately: Pydantic drops the fractional part on this one
# and keeps it on the other, so a bundle that did not normalize its timestamps
# would round-trip one of these two and not the other.
ON_THE_SECOND = datetime(2026, 3, 4, 5, 6, 8, tzinfo=UTC)

JUDGE = JudgeRef(agent_id="judge-key-1", digest="digest-one")
OTHER_JUDGE = JudgeRef(agent_id="judge-key-2", digest="digest-two")

GRAPH = "bundle-round-trip"

EXPORTED_AT = datetime(2026, 9, 12, 8, 0, 0, tzinfo=UTC)


def _provider() -> MockEmbeddingProvider:
    """The same mock on both sides, so re-embedding is comparable."""
    return MockEmbeddingProvider(model_id="mock-model", dimension=16)


async def _export(storage):
    return await export_graph(
        storage,
        embedding_provider="mock",
        embedding_model_id="mock-model",
        exported_at=EXPORTED_AT,
    )


async def _open(kind: str):
    """One backend, landed on the graph the round trip uses."""
    store = InMemoryStorage() if kind == "memory" else SurrealDBStorage(url="mem://")
    await store.connect()
    return store


async def build_rich_graph(store) -> dict:
    """A graph with something in every section and every awkward field filled.

    Written out rather than driven through the tools on purpose: the point is to
    reach the fields the tools do not routinely write — a closed lifecycle
    episode, a second retirement, a value signal with every clock set, a
    relation label with a description, a timeline with a reference time — since
    those are exactly the ones an export can silently drop.
    """
    await store.store_metacontext(
        Metacontext(
            id="the-real",
            content="The Real",
            description="Claims about the real world.",
            created_at=AT,
            value=ValueSignal(confidence=0.9, importance=0.8, retrieved_at=AT),
            metadata={"note": "seeded"},
        )
    )
    await store.store_metacontext(
        Metacontext(
            id="retired-metacontext",
            content="An abandoned reading",
            status=NodeStatus.ARCHIVED,
            superseded_at=ON_THE_SECOND,
            created_at=AT,
        )
    )

    await store.store_document(
        RawDocument(
            id="doc-1",
            content="It rained on Tuesday. Everything was wet.",
            source="weather.md",
            source_type="document",
            created_at=ON_THE_SECOND,
            metadata={"pages": 2},
        )
    )
    await store.store_segment(
        Segment(
            id="seg-1",
            source_id="doc-1",
            text="It rained on Tuesday.",
            span_start=0,
            span_end=21,
            created_at=AT,
            metadata={"paragraph": 1},
        )
    )
    await store.store_segment(
        Segment(
            id="seg-2",
            source_id="doc-1",
            text="Everything was wet.",
            span_start=22,
            span_end=41,
            created_at=ON_THE_SECOND,
        )
    )

    topic = Topic(
        id="topic-1",
        content="Weather",
        description="Rain, wind, and what followed from them.",
        description_reviewed_at=AT,
        source_id="seg-1",
        created_at=AT,
        judged_by=JUDGE,
        extraction_method="ingest",
        value=ValueSignal(
            confidence=0.75,
            importance=0.6,
            retrieved_at=ON_THE_SECOND,
            importance_judged_at=AT,
            importance_judged_by=OTHER_JUDGE,
        ),
        metadata={"created_from_tag": True},
    )
    undescribed = Topic(id="topic-2", content="Tuesday", created_at=AT)
    fact = Fact(
        id="fact-1",
        content="It rained on Tuesday.",
        source_id="seg-1",
        claim_kind=ClaimKind.EVENT,
        created_at=AT,
        judged_by=JUDGE,
    )
    # Retired, brought back, retired again: the pair of scalars cannot say that,
    # and an export that read only the scalars would lose the first spell.
    recurring = Fact(
        id="fact-2",
        content="The street is flooded.",
        source_id="seg-2",
        claim_kind=ClaimKind.STATE,
        status=NodeStatus.HISTORICAL,
        superseded_at=ON_THE_SECOND + timedelta(days=2),
        created_at=AT,
        lifecycle=[
            LifecycleEpisode(
                retired_at=ON_THE_SECOND,
                because=NodeStatus.HISTORICAL,
                counterpart="fact-1",
                restored_at=ON_THE_SECOND + timedelta(days=1),
                retired_by=JUDGE,
                restored_by=OTHER_JUDGE,
            ),
            LifecycleEpisode(
                retired_at=ON_THE_SECOND + timedelta(days=2),
                because=NodeStatus.HISTORICAL,
                retired_by=OTHER_JUDGE,
            ),
        ],
    )
    archived = Inference(
        id="inference-1",
        content="The drains were blocked.",
        source_id="seg-2",
        status=NodeStatus.ARCHIVED,
        superseded_at=ON_THE_SECOND,
        created_at=AT,
        lifecycle=[
            LifecycleEpisode(retired_at=ON_THE_SECOND, because=NodeStatus.ARCHIVED),
        ],
    )
    corrected = Inference(
        id="inference-2",
        content="It never rains here.",
        source_id="seg-1",
        status=NodeStatus.CORRECTED,
        superseded_at=ON_THE_SECOND,
        created_at=AT,
    )
    for node in (topic, undescribed, fact, recurring, archived, corrected):
        await store.store_node(node)

    edges = [
        NodeEdge(
            id="edge-1",
            src_id=fact.id,
            dst_id="doc-1",
            type=EdgeType.SOURCED_FROM,
            created_at=AT,
            judged_by=JUDGE,
            validity=[
                ValidityInterval(
                    start=PreciseInstant(at=AT),
                    end=UnboundedInstant(),
                    basis=IntervalBasis.STATED,
                ),
                ValidityInterval(
                    start=NamedInstant(label="during the storm"),
                    end=UnboundedInstant(),
                    timeline_id="timeline-1",
                    basis=IntervalBasis.INFERRED,
                ),
            ],
        ),
        NodeEdge(
            id="edge-2",
            src_id=fact.id,
            dst_id=topic.id,
            type=EdgeType.TAGGED_WITH_TOPIC,
            created_at=AT,
        ),
        NodeEdge(
            id="edge-3",
            src_id=fact.id,
            dst_id="the-real",
            type=EdgeType.HAS_METACONTEXT,
            created_at=AT,
            judged_by=JUDGE,
        ),
        NodeEdge(
            id="edge-4",
            src_id=archived.id,
            dst_id=fact.id,
            type=EdgeType.DERIVED_FROM,
            created_at=AT,
            weight=0.25,
        ),
        NodeEdge(
            id="edge-5",
            src_id=recurring.id,
            dst_id=fact.id,
            type=EdgeType.SUPERSEDED_BY,
            created_at=ON_THE_SECOND,
        ),
        NodeEdge(
            id="edge-6",
            src_id=fact.id,
            dst_id=undescribed.id,
            type=EdgeType.RELATED,
            label="reported_by",
            kind="attribution",
            created_at=AT,
            judged_by=OTHER_JUDGE,
            metadata={"coined": True},
        ),
        NodeEdge(
            id="edge-7",
            src_id=fact.id,
            dst_id="timeline-1",
            type=EdgeType.TIMELINK,
            created_at=AT,
            metadata={"timepoint_id": "timepoint-1"},
        ),
        NodeEdge(
            id="edge-8",
            src_id=topic.id,
            dst_id=undescribed.id,
            type=EdgeType.SUBTOPIC_OF,
            created_at=AT,
        ),
    ]
    for edge in edges:
        await store.store_edge(edge)

    await store.store_timeline(
        Timeline(
            id="timeline-1",
            name="The storm",
            description="What happened when.",
            reference_time=AT,
            created_at=AT,
            metadata={"kind": "extracted"},
            timepoints=[
                Timepoint(id="timepoint-1", start=AT, end=ON_THE_SECOND, label="Tuesday"),
                Timepoint(id="timepoint-2", label="afterwards", metadata={"vague": True}),
            ],
        )
    )
    await store.store_timeline(Timeline(id="timeline-2", name="Empty", created_at=ON_THE_SECOND))

    for record in (
        DecisionRecord(
            id="decision-1",
            kind=DecisionKind.INGEST,
            subject_ids=[fact.id, topic.id],
            judged_by=JUDGE,
            decided_at=AT,
            basis="read the document",
        ),
        DecisionRecord(
            id="decision-2",
            kind=DecisionKind.WORLD_CHANGE,
            subject_ids=[recurring.id],
            judged_by=OTHER_JUDGE,
            decided_at=ON_THE_SECOND,
        ),
        DecisionRecord(
            id="decision-3",
            kind=DecisionKind.CONFIRMATION,
            subject_ids=[fact.id],
            reviews="decision-1",
            judged_by=OTHER_JUDGE,
            decided_at=ON_THE_SECOND + timedelta(hours=1),
        ),
        # No judge: unknown is a state, and it has to survive the round trip as
        # unknown rather than as an empty ref.
        DecisionRecord(
            id="decision-4",
            kind=DecisionKind.ARCHIVAL,
            subject_ids=[archived.id],
            decided_at=ON_THE_SECOND,
        ),
    ):
        await store.record_decision(record)

    await store.store_relation_label(
        RelationLabel(
            id="label-1",
            name="reported_by",
            kind="attribution",
            description="Who said it.",
            judged_by=JUDGE,
            created_at=AT,
        )
    )
    await store.store_relation_label(
        RelationLabel(id="label-2", name="caused_by", kind="relationship", created_at=AT)
    )
    await store.record_relation_verdict(
        RelationVerdict(
            id="verdict-1",
            label_ids=["label-1", "label-2"],
            verdict="distinct",
            because="attribution is not causation",
            judged_by=JUDGE,
            decided_at=AT,
        )
    )

    serving = Agent(
        id="judge-key-1",
        name="Opus 5",
        last_seen_at=ON_THE_SECOND,
        descriptions=[
            AgentDescription(
                digest=description_digest("a careful reader"),
                text="a careful reader",
                recorded_at=AT,
                confirmed_at=ON_THE_SECOND,
            )
        ],
    )
    out_of_use = retired(
        Agent(
            id="judge-key-2",
            name="Sonnet 4",
            former_ids=["judge-key-0"],
            last_seen_at=AT,
        ),
        ON_THE_SECOND,
    )
    await store.upsert_agent(serving)
    await store.upsert_agent(out_of_use)
    await store.set_approved_agent_ids(["judge-key-2", "judge-key-1"])

    await store.set_require_judge(True)
    await store.set_reflect_threshold_override(42)
    await store.set_reflect_counter(7)
    await store.set_merge_overrides(MergeOverrides(undo_depth=3, cycle_limit=5))
    await store.set_warning_overrides(
        WarningOverrides(
            surface=False,
            default_action=AdvisoryAction.FLAG,
            by_kind={AdvisoryKind.CROSS_METACONTEXT: AdvisoryAction.PROCEED},
        )
    )
    return {
        "topic": topic,
        "fact": fact,
        "nodes": [topic, undescribed, fact, recurring, archived, corrected],
    }


@pytest.fixture(params=[("memory", "surrealdb"), ("surrealdb", "memory")])
async def pair(request):
    """A source backend and a target backend, in both directions.

    Two objects rather than two graphs in one store, because a bundle crossing
    backends is the case that finds a rendering difference — and the two
    directions find different ones.
    """
    source_kind, target_kind = request.param
    source = await _open(source_kind)
    target = await _open(target_kind)
    await source.switch_database(GRAPH)
    yield source, target
    await source.close()
    await target.close()


class TestTheRoundTripIsLossless:
    async def test_the_files_come_back_byte_for_byte(self, pair):
        source, target = pair
        await build_rich_graph(source)

        before = await _export(source)
        await import_graph(before, target, _provider(), graph=GRAPH)
        await target.switch_database(GRAPH)
        after = await _export(target)

        assert bundle_bytes(before) == bundle_bytes(after)

    async def test_two_exports_of_one_graph_are_identical(self, pair):
        """The property the round trip rests on: nothing in an export varies
        run to run except the instant it names."""
        source, _ = pair
        await build_rich_graph(source)

        assert bundle_bytes(await _export(source)) == bundle_bytes(await _export(source))

    async def test_every_section_actually_carries_something(self, pair):
        """The control. Every assertion above passes over an empty graph."""
        source, _ = pair
        await build_rich_graph(source)

        counts = section_counts(await _export(source))

        assert all(count > 0 for count in counts.values()), counts

    async def test_every_protocol_read_agrees_on_the_two_graphs(self, pair):
        source, target = pair
        await build_rich_graph(source)
        await import_graph(await _export(source), target, _provider(), graph=GRAPH)
        await target.switch_database(GRAPH)

        assert await _protocol_reads(source) == await _protocol_reads(target)

    async def test_the_vectors_are_the_ones_the_model_would_compute(self, pair):
        """Not carried has to mean recomputed. The same provider on both sides
        gives the same vectors, which is what makes changing the model the
        migration path rather than a silent loss."""
        source, target = pair
        built = await build_rich_graph(source)
        provider = _provider()
        for node in built["nodes"]:
            vector = (await provider.embed([embedding_text(node)]))[0]
            await source.store_embedding(
                EmbeddingRecord(item_id=node.id, model_id=provider.model_id, vector=vector)
            )

        await import_graph(await _export(source), target, _provider(), graph=GRAPH)
        await target.switch_database(GRAPH)

        for node in built["nodes"]:
            original = await source.get_embeddings_for_item(node.id, model_id=provider.model_id)
            restored = await target.get_embeddings_for_item(node.id, model_id=provider.model_id)
            assert [e.vector for e in original] == [e.vector for e in restored], node.id


async def _protocol_reads(storage) -> dict:
    """Every whole-graph read the protocol offers, as comparable values.

    Ids and timestamps included: this is the second check the design asks for,
    and one that compared only counts would pass over a graph whose contents had
    all been rewritten.
    """

    def dumped(models):
        return sorted(json.dumps(m.model_dump(mode="json"), sort_keys=True) for m in models)

    return {
        "nodes": {
            status.value: dumped(await storage.query_nodes(status=status)) for status in NodeStatus
        },
        "metacontexts": {
            status.value: dumped(await storage.query_metacontexts(status=status))
            for status in NodeStatus
        },
        "edges": dumped(await storage.query_edges()),
        "documents": dumped(await storage.query_documents()),
        "segments": dumped(await storage.query_segments()),
        "timelines": dumped(await storage.query_timelines()),
        "relation_labels": dumped(await storage.query_relation_labels()),
        "relation_verdicts": dumped(await storage.query_relation_verdicts()),
        "decisions": dumped(await storage.query_decisions()),
        "agents": dumped(await storage.list_agents()),
        "judged_relation_pairs": sorted(await storage.judged_relation_pairs()),
        "approved_agent_ids": sorted(await storage.get_approved_agent_ids()),
        "require_judge": await storage.get_require_judge(),
        "reflect_threshold_override": await storage.get_reflect_threshold_override(),
        "reflect_counter": await storage.get_reflect_counter(),
        "merge_overrides": (await storage.get_merge_overrides()).model_dump(mode="json"),
        "warning_overrides": (await storage.get_warning_overrides()).model_dump(mode="json"),
        "nodes_by_type": {
            status.value: {
                node_type.value: count
                for node_type, count in (await storage.count_nodes_by_type(status=status)).items()
            }
            for status in NodeStatus
        },
        "edges_by_type": {
            edge_type.value: count
            for edge_type, count in (await storage.count_edges_by_type()).items()
        },
        "nodes_without_metacontext": await storage.count_nodes_without_metacontext(),
    }


class TestTheTwoForms:
    async def test_plain_and_compressed_read_back_the_same(self, tmp_path):
        store = await _open("memory")
        await store.switch_database(GRAPH)
        await build_rich_graph(store)
        bundle = await _export(store)

        write_bundle(bundle, tmp_path / "graph.epimemer.tar.gz")
        write_bundle(bundle, tmp_path / "plain", plain=True)

        from_archive = read_bundle(tmp_path / "graph.epimemer.tar.gz")
        from_directory = read_bundle(tmp_path / "plain")

        assert bundle_bytes(from_archive) == bundle_bytes(bundle)
        assert bundle_bytes(from_directory) == bundle_bytes(bundle)

    async def test_the_plain_form_is_one_file_per_section(self, tmp_path):
        store = await _open("memory")
        await build_rich_graph(store)

        write_bundle(await _export(store), tmp_path / "plain", plain=True)

        written = {path.name for path in (tmp_path / "plain").iterdir()}
        assert "manifest.json" in written
        assert "nodes.jsonl" in written
        assert "settings.jsonl" in written

    async def test_an_archive_is_written_the_same_way_twice(self, tmp_path):
        """Not required by the format, but an archive whose bytes move on their
        own is one nobody can check by hand."""
        store = await _open("memory")
        await build_rich_graph(store)
        bundle = await _export(store)

        write_bundle(bundle, tmp_path / "one.tar.gz")
        write_bundle(bundle, tmp_path / "two.tar.gz")

        assert (tmp_path / "one.tar.gz").read_bytes() == (tmp_path / "two.tar.gz").read_bytes()


class TestWhatImportRefuses:
    async def test_a_graph_that_exists_is_refused(self, tmp_path):
        store = await _open("memory")
        await store.switch_database(GRAPH)
        await build_rich_graph(store)
        bundle = await _export(store)
        await store.switch_database("somewhere-else")

        with pytest.raises(ValueError, match="already exists"):
            await import_graph(bundle, store, _provider(), graph=GRAPH)

    async def test_the_existing_graph_is_left_exactly_as_it_was(self, tmp_path):
        store = await _open("memory")
        await store.switch_database(GRAPH)
        await build_rich_graph(store)
        bundle = await _export(store)
        await store.switch_database("somewhere-else")

        with pytest.raises(ValueError):
            await import_graph(bundle, store, _provider(), graph=GRAPH)

        await store.switch_database(GRAPH)
        assert bundle_bytes(await _export(store)) == bundle_bytes(bundle)

    async def test_a_newer_format_is_refused_rather_than_guessed_at(self, tmp_path):
        store = await _open("memory")
        bundle = await _export(store)
        newer = bundle.model_copy(
            update={
                "manifest": bundle.manifest.model_copy(
                    update={"format_version": BUNDLE_FORMAT_VERSION + 1}
                )
            }
        )
        write_bundle(newer, tmp_path / "newer.tar.gz")

        with pytest.raises(BundleFormatError, match="format version"):
            read_bundle(tmp_path / "newer.tar.gz")

    async def test_an_older_format_imports_and_says_so(self):
        """Missing fields take model defaults, which is what an old storage row
        already does. The report is how a caller learns it happened."""
        store = await _open("memory")
        await build_rich_graph(store)
        bundle = await _export(store)
        older = bundle.model_copy(
            update={"manifest": bundle.manifest.model_copy(update={"format_version": 0})}
        )

        target = await _open("memory")
        report = await import_graph(older, target, _provider(), graph=GRAPH)

        assert report.older_format
        assert report.format_version == 0

    async def test_a_bundle_missing_a_section_is_refused(self, tmp_path):
        store = await _open("memory")
        write_bundle(await _export(store), tmp_path / "plain", plain=True)
        (tmp_path / "plain" / "edges.jsonl").unlink()

        with pytest.raises(BundleFormatError, match="edges.jsonl"):
            read_bundle(tmp_path / "plain")

    async def test_a_bundle_with_no_manifest_is_refused(self, tmp_path):
        store = await _open("memory")
        write_bundle(await _export(store), tmp_path / "plain", plain=True)
        (tmp_path / "plain" / "manifest.json").unlink()

        with pytest.raises(BundleFormatError, match="manifest"):
            read_bundle(tmp_path / "plain")


class TestWhatAFailureLeavesBehind:
    async def test_a_partial_import_drops_the_graph(self):
        """A retry must never be blocked by the wreckage of the last attempt."""
        store = await _open("memory")
        await build_rich_graph(store)
        bundle = await _export(store)

        target = await _open("memory")
        broken = bundle.model_copy(
            update={
                "manifest": bundle.manifest.model_copy(
                    update={"counts": {**bundle.manifest.counts, "nodes": 999}}
                )
            }
        )

        with pytest.raises(ValueError, match="does not match the manifest"):
            await import_graph(broken, target, _provider(), graph=GRAPH)

        assert GRAPH not in await target.list_databases()

    async def test_a_failing_embedding_provider_drops_the_graph(self):
        class Refuses:
            model_id = "mock-model"
            dimension = 16

            async def embed(self, texts):
                raise RuntimeError("no model here")

        store = await _open("memory")
        await build_rich_graph(store)
        bundle = await _export(store)
        target = await _open("memory")

        with pytest.raises(RuntimeError, match="no model here"):
            await import_graph(bundle, target, Refuses(), graph=GRAPH)

        assert GRAPH not in await target.list_databases()

    async def test_the_caller_is_left_on_the_graph_it_started_on(self):
        store = await _open("memory")
        await build_rich_graph(store)
        bundle = await _export(store)

        target = await _open("memory")
        await target.switch_database("where-i-was")
        await import_graph(bundle, target, _provider(), graph=GRAPH)

        assert target.current_database == "where-i-was"


class TestTheManifest:
    async def test_it_records_what_the_graph_was_embedded_with(self):
        store = await _open("memory")
        bundle = await export_graph(
            store,
            embedding_provider="sentence-transformers",
            embedding_model_id="all-MiniLM-L6-v2",
            exported_at=EXPORTED_AT,
        )

        assert bundle.manifest.embedding_provider == "sentence-transformers"
        assert bundle.manifest.embedding_model_id == "all-MiniLM-L6-v2"

    async def test_a_restore_onto_another_model_says_so(self):
        store = await _open("memory")
        await build_rich_graph(store)
        bundle = await export_graph(
            store,
            embedding_provider="sentence-transformers",
            embedding_model_id="all-MiniLM-L6-v2",
            exported_at=EXPORTED_AT,
        )

        target = await _open("memory")
        report = await import_graph(bundle, target, _provider(), graph=GRAPH)

        assert report.reembedded_with_a_different_model
        assert report.embedding_model_id == "mock-model"

    async def test_the_counts_are_the_sections_counts(self):
        store = await _open("memory")
        await build_rich_graph(store)

        bundle = await _export(store)

        assert bundle.manifest.counts == section_counts(bundle)


class TestAFileThatIsNotABundle:
    def test_a_truncated_or_unrelated_file_is_refused_plainly(self, tmp_path):
        """A half-finished download and a file that was never a bundle fail the
        same way, and the reader's next move is the same either way."""
        not_a_bundle = tmp_path / "notes.epimemer.tar.gz"
        not_a_bundle.write_bytes(b"this was never an archive")

        with pytest.raises(BundleFormatError, match="not a readable Epimemer bundle"):
            read_bundle(not_a_bundle)

    def test_a_path_with_nothing_at_it_says_so(self, tmp_path):
        with pytest.raises(BundleFormatError, match="No bundle at"):
            read_bundle(tmp_path / "missing.tar.gz")


def _name(graph: str, date: str) -> str:
    return f"{graph}-{date}.epimemer.tar.gz"


class TestStaleBundles:
    """Which bundles retention removes, decided from the names alone."""

    def test_the_newest_keep_survive(self):
        names = [_name("default", d) for d in ("2026-01-01", "2026-02-01", "2026-03-01")]

        assert stale_bundles(names, "default", 1) == names[:2]

    def test_the_date_in_the_name_orders_them_not_the_listing(self):
        """A listing arrives in whatever order the store felt like."""
        names = [
            _name("default", "2026-03-01"),
            _name("default", "2026-01-01"),
            _name("default", "2026-02-01"),
        ]

        assert stale_bundles(names, "default", 1) == [
            _name("default", "2026-01-01"),
            _name("default", "2026-02-01"),
        ]

    def test_dates_across_a_year_and_a_month_boundary_still_order(self):
        names = [
            _name("default", "2025-12-31"),
            _name("default", "2026-01-01"),
            _name("default", "2026-09-02"),
            _name("default", "2026-09-10"),
        ]

        assert stale_bundles(names, "default", 2) == names[:2]

    def test_keep_larger_than_the_count_removes_nothing(self):
        names = [_name("default", "2026-01-01"), _name("default", "2026-02-01")]

        assert stale_bundles(names, "default", 5) == []
        assert stale_bundles([], "default", 5) == []

    def test_a_graph_whose_name_is_a_prefix_of_another_is_untouched(self):
        """`notes` must never claim the backups of `notes-archive`, which is
        what a bare prefix match would do."""
        names = [
            _name("notes", "2026-01-01"),
            _name("notes-archive", "2026-01-01"),
            _name("notes-archive", "2026-02-01"),
        ]

        assert stale_bundles(names, "notes", 1) == []
        assert stale_bundles(names, "notes-archive", 1) == [_name("notes-archive", "2026-01-01")]

    def test_a_plain_directory_bundle_is_never_matched(self):
        """`--plain` writes a folder, and retention removes tarballs."""
        names = [
            "default-2026-01-01.epimemer",
            "default-2026-01-01.epimemer/",
            _name("default", "2026-02-01"),
            _name("default", "2026-03-01"),
        ]

        assert stale_bundles(names, "default", 1) == [_name("default", "2026-02-01")]

    def test_anything_that_is_not_a_dated_bundle_is_left_alone(self):
        names = [
            "default.epimemer.tar.gz",
            "default-latest.epimemer.tar.gz",
            "default-2026-01.epimemer.tar.gz",
            "default-2026-01-01.tar.gz",
            "notes.txt",
        ]

        assert stale_bundles(names, "default", 1) == []

    def test_full_paths_come_back_as_they_went_in(self):
        """The caller deletes by the same string it listed."""
        names = [f"bucket/backups/{_name('default', d)}" for d in ("2026-01-01", "2026-02-01")]

        assert stale_bundles(names, "default", 1) == [names[0]]

    def test_keeping_none_is_refused(self):
        """Zero would delete the bundle the backup just wrote."""
        with pytest.raises(ValueError, match="at least 1"):
            stale_bundles([_name("default", "2026-01-01")], "default", 0)


class TestPruneBundles:
    def test_it_deletes_the_stale_ones_and_reports_what_is_left(self, tmp_path):
        for date in ("2026-01-01", "2026-02-01", "2026-03-01"):
            (tmp_path / _name("default", date)).write_bytes(b"a bundle")
        (tmp_path / "notes.txt").write_text("not a bundle")

        report = prune_bundles(str(tmp_path), "default", 1)

        assert report.kept == 1
        assert report.removed == [_name("default", "2026-01-01"), _name("default", "2026-02-01")]
        assert report.failed == {}
        assert (tmp_path / _name("default", "2026-03-01")).is_file()
        assert (tmp_path / "notes.txt").is_file()

    def test_a_destination_with_nothing_in_it_yet(self, tmp_path):
        report = prune_bundles(str(tmp_path / "not-written-to"), "default", 3)

        assert report.kept == 0
        assert report.removed == []

    def test_a_delete_that_fails_is_reported_rather_than_raised(self, tmp_path, monkeypatch):
        """The graph is already written out by the time this runs, so a bundle
        nobody could remove is untidy rather than dangerous."""
        for date in ("2026-01-01", "2026-02-01"):
            (tmp_path / _name("default", date)).write_bytes(b"a bundle")

        import fsspec

        local = fsspec.filesystem("file")
        monkeypatch.setattr(
            type(local),
            "rm",
            lambda *args, **kwargs: (_ for _ in ()).throw(PermissionError("read-only")),
        )

        report = prune_bundles(str(tmp_path), "default", 1)

        assert report.removed == []
        assert report.failed == {_name("default", "2026-01-01"): "read-only"}
        # Still there, so still counted.
        assert report.kept == 2
