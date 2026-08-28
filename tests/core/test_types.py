"""Tests for core Pydantic types."""

from datetime import datetime, timedelta, timezone

import pytest
from petritype.core.executable_graph_components import ListPlaceNode
from pydantic import ValidationError

from epimemer.core.temporal import (
    IntervalBasis,
    NamedInstant,
    PreciseInstant,
    UnknownInstant,
    ValidityInterval,
)
from epimemer.core.types import (
    EdgeType,
    EmbeddingRecord,
    Fact,
    HISTORY_EDGE_TYPES,
    Inference,
    JUDGMENT_EDGE_TYPES,
    LifecycleEpisode,
    Metacontext,
    NodeEdge,
    NodeStatus,
    NON_KNOWLEDGE_EDGE_TYPES,
    completed_merge_cycles,
    RawDocument,
    Segment,
    Timeline,
    Timepoint,
    Topic,
    ValueSignal,
    lineage_edge_type_for,
    merged_value_signal,
    migration_disposition,
    moved_edge_types,
    rated_confidence,
    traversal_excluded,
)


class TestEdgeBehaviour:
    """Traversal and migration are independent questions about an edge.

    Migration gained a third answer with #54: a world-change neither moves an
    edge nor leaves everything behind, so `copy` exists for the two types that
    describe *where a claim sits* rather than asserting it.
    """

    def test_relationship_edge_traversed_and_moved_by_a_correction(self):
        e = NodeEdge(src_id="a", dst_id="b", type=EdgeType.RELATED,
                     label="refuted_in", kind="relationship")
        assert not traversal_excluded(e)
        assert migration_disposition(e.type, NodeStatus.CORRECTED) == "move"

    def test_attribution_edge_not_traversed_but_moved_by_a_correction(self):
        e = NodeEdge(src_id="a", dst_id="b", type=EdgeType.RELATED,
                     label="published_by", kind="attribution")
        assert traversal_excluded(e)
        assert migration_disposition(e.type, NodeStatus.CORRECTED) == "move"

    def test_sourced_from_not_traversed_but_moved_by_a_correction(self):
        e = NodeEdge(src_id="a", dst_id="d", type=EdgeType.SOURCED_FROM)
        assert traversal_excluded(e)
        assert migration_disposition(e.type, NodeStatus.CORRECTED) == "move"

    def test_tagged_with_traversed_and_moved_by_a_correction(self):
        e = NodeEdge(src_id="a", dst_id="t", type=EdgeType.TAGGED_WITH)
        assert not traversal_excluded(e)
        assert migration_disposition(e.type, NodeStatus.CORRECTED) == "move"

    def test_history_edge_neither_traversed_nor_migrated(self):
        e = NodeEdge(src_id="a", dst_id="b", type=EdgeType.SUPERSEDED_BY)
        assert traversal_excluded(e)
        for status in (NodeStatus.CORRECTED, NodeStatus.HISTORICAL, NodeStatus.MERGED):
            assert migration_disposition(e.type, status) == "keep"


class TestWorldChangeMigrationPolicy:
    """#54. The historical node keeps what it asserted; the replacement does not
    inherit assertions it never made."""

    def test_provenance_stays_with_the_claim_its_document_asserted(self):
        assert migration_disposition(
            EdgeType.SOURCED_FROM, NodeStatus.HISTORICAL
        ) == "keep"

    def test_a_judgment_about_the_old_claim_stays_on_it(self):
        for edge_type in (
            EdgeType.CONTRADICTION, EdgeType.VARIANT_OF, EdgeType.RELATED,
            EdgeType.SUPPORTS, EdgeType.DERIVED_FROM,
        ):
            assert migration_disposition(edge_type, NodeStatus.HISTORICAL) == "keep"

    def test_the_frame_and_the_tags_are_copied(self):
        """A frame says which world, a tag says what about — neither asserts the
        claim, and losing the frame would move a fiction claim into base
        reality."""
        for edge_type in (EdgeType.HAS_METACONTEXT, EdgeType.TAGGED_WITH):
            assert migration_disposition(edge_type, NodeStatus.HISTORICAL) == "copy"

    def test_a_world_change_moves_nothing(self):
        assert moved_edge_types(NodeStatus.HISTORICAL) == frozenset()

    def test_a_correction_and_a_merge_still_move_everything_but_bookkeeping(self):
        for status in (NodeStatus.CORRECTED, NodeStatus.MERGED):
            moved = moved_edge_types(status)
            assert EdgeType.SOURCED_FROM in moved
            assert EdgeType.SUPERSEDED_BY not in moved
            assert EdgeType.SUPERSESSION_CANDIDATE not in moved

    def test_a_merge_does_not_move_the_frame(self):
        """A correction's replacement is the same claim, so its frame follows.
        A merge's survivor is *synthesised*, so no source's framing was made
        about that wording — the merging agent re-states it under its own judge
        instead, which is `describe_relation`'s coiner rule one layer up (#76).
        """
        assert EdgeType.HAS_METACONTEXT in moved_edge_types(NodeStatus.CORRECTED)
        assert EdgeType.HAS_METACONTEXT not in moved_edge_types(NodeStatus.MERGED)

    def test_legacy_superseded_rows_behave_as_they_always_did(self):
        """Nothing writes `SUPERSEDED` any more, but old graphs still load and
        must not change meaning under a policy written after them."""
        assert migration_disposition(
            EdgeType.SOURCED_FROM, NodeStatus.SUPERSEDED
        ) == "move"


def _episode(because: NodeStatus, *, restored: bool) -> LifecycleEpisode:
    return LifecycleEpisode(
        retired_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        because=because,
        counterpart="other",
        restored_at=datetime(2026, 1, 2, tzinfo=timezone.utc) if restored else None,
    )


class TestCountingMergeCycles:
    """One completed cycle is one *closed* `merged` episode (REVIEW_MODE.md
    §7.8). The signal needs no new storage — the lifecycle already records it,
    append-only and never trimmed."""

    def test_a_closed_merge_episode_is_one_cycle(self):
        node = Fact(content="c", source_id="s", lifecycle=[
            _episode(NodeStatus.MERGED, restored=True),
            _episode(NodeStatus.MERGED, restored=True),
        ])
        assert completed_merge_cycles(node) == 2

    def test_an_open_merge_episode_is_not_a_cycle(self):
        """The node is still merged. Counting it would refuse on the strength of
        the merge currently being reversed."""
        node = Fact(content="c", source_id="s", lifecycle=[
            _episode(NodeStatus.MERGED, restored=False),
        ])
        assert completed_merge_cycles(node) == 0

    def test_a_recurrence_is_not_an_oscillation(self):
        """A claim that stepped aside for its period and came back is #53's
        recurrence — the case the lifecycle was built for, and not this one."""
        node = Fact(content="c", source_id="s", lifecycle=[
            _episode(NodeStatus.HISTORICAL, restored=True),
            _episode(NodeStatus.CORRECTED, restored=True),
        ])
        assert completed_merge_cycles(node) == 0

    def test_a_node_that_never_left_has_no_cycles(self):
        assert completed_merge_cycles(Fact(content="c", source_id="s")) == 0


class TestAJudgmentIsAnchoredWhateverTheRetirement:
    """#65. A judgment was made against a wording, and no retirement leaves that
    wording in place — so none of them re-point one.

    The correction case is the one with teeth. `migration_disposition` holds
    that a correction preserves the *claim*, which is why the sources follow it;
    but "the population is 500,000" corrected to "5,000,000" leaves a
    counterpart judged *one claim* against a number that is no longer there, and
    carrying the edge would count that counterpart's publisher as backing the
    new figure. A merge is the same shape reached differently: the survivor's
    content is synthesised, so it is nobody's judged wording either.

    Anchoring costs one re-nomination, and that cost is correct — the
    replacement against the same counterpart is a pair nobody has judged.
    """

    def test_no_status_moves_a_judgment(self):
        for edge_type in JUDGMENT_EDGE_TYPES:
            for status in (
                NodeStatus.CORRECTED, NodeStatus.HISTORICAL,
                NodeStatus.MERGED, NodeStatus.SUPERSEDED,
            ):
                assert migration_disposition(edge_type, status) == "keep"

    def test_the_correction_and_merge_paths_agree_with_the_policy(self):
        """The backends filter by `moved_edge_types`, so the two must not be
        derivable to different answers."""
        for status in (NodeStatus.CORRECTED, NodeStatus.MERGED):
            assert not (JUDGMENT_EDGE_TYPES & moved_edge_types(status))

    def test_a_judgment_is_still_traversed_as_knowledge(self):
        """Migration and traversal are separate questions, and these types
        answer them differently. Anchoring them must not quietly remove them
        from search, which is what putting them in `NON_KNOWLEDGE_EDGE_TYPES`
        would have done."""
        assert not (JUDGMENT_EDGE_TYPES & NON_KNOWLEDGE_EDGE_TYPES)
        for edge_type in JUDGMENT_EDGE_TYPES:
            e = NodeEdge(src_id="a", dst_id="b", type=edge_type)
            assert not traversal_excluded(e)


class TestTheLineageEdgeSplitsWithTheStatus:
    """#53 T2. The status split landed first and the edge did not, which left a
    world-change writing `superseded_by` onto a node marked `HISTORICAL` — an
    edge saying *replaced* about a claim the status calls still true of its
    period. The two now split together.

    `temporally_followed_by` states temporal order rather than replacement, and
    that is what makes recurrence expressible: the Saint Petersburg claim
    followed the Leningrad one in 1991, and Leningrad becoming current again
    would not make that false. `superseded_by` keeps meaning replacement and
    stays terminal.
    """

    def test_a_world_change_records_temporal_order(self):
        assert lineage_edge_type_for(
            NodeStatus.HISTORICAL
        ) is EdgeType.TEMPORALLY_FOLLOWED_BY

    def test_a_correction_records_replacement(self):
        assert lineage_edge_type_for(NodeStatus.CORRECTED) is EdgeType.SUPERSEDED_BY

    def test_legacy_rows_read_as_replacement(self):
        """A pre-split row does not say which event it was, and `superseded_by`
        is what was actually written at the time. Reading it as the new edge
        would claim a distinction nobody recorded."""
        assert lineage_edge_type_for(NodeStatus.SUPERSEDED) is EdgeType.SUPERSEDED_BY

    def test_a_status_that_is_not_a_retirement_is_refused(self):
        """Same discipline as `superseded_status_for`: a merge writes
        `merged_into` through its own path, and answering for it here would
        hand a caller a plausible edge for an event that did not happen."""
        for status in (NodeStatus.ACTIVE, NodeStatus.MERGED, NodeStatus.ARCHIVED):
            with pytest.raises(ValueError):
                lineage_edge_type_for(status)

    def test_the_new_edge_is_lineage_rather_than_knowledge(self):
        """It joins `HISTORY_EDGE_TYPES`, which buys three behaviours at once:
        not traversed by default, not migrated on a later retirement, and
        anchored to the version it was written about. Migrating it would
        detach it from the transition it records."""
        e = NodeEdge(src_id="a", dst_id="b", type=EdgeType.TEMPORALLY_FOLLOWED_BY)

        assert EdgeType.TEMPORALLY_FOLLOWED_BY in HISTORY_EDGE_TYPES
        assert traversal_excluded(e)
        for status in (NodeStatus.CORRECTED, NodeStatus.HISTORICAL, NodeStatus.MERGED):
            assert migration_disposition(e.type, status) == "keep"
            assert EdgeType.TEMPORALLY_FOLLOWED_BY not in moved_edge_types(status)

    def test_topic_source_id_optional(self):
        assert Topic(content="BBC").source_id is None


class TestValueSignal:

    def test_default_values(self):
        v = ValueSignal()
        assert v.confidence is None
        assert v.importance == 0.5

    def test_an_unrated_node_stores_absence_not_the_default_number(self):
        """#46 amendment 1, signed off 2026-08-19.

        A stored 0.5 cannot say which of two things happened: an agent read
        the material and judged it middling, or no agent considered the
        question at all. That is the same trap `retrieved_at` and
        `importance_judged_at` were pulled out of, and it matters more here —
        the merge rule and any audit of how much of a graph has actually been
        judged both need to tell those apart.
        """
        assert "confidence" not in ValueSignal().model_dump(exclude_none=True)
        assert ValueSignal(confidence=0.5).confidence == 0.5

    def test_the_unrated_case_reads_as_the_documented_default(self):
        """Absence is the *storage* answer; every consumer still sees a number."""
        assert rated_confidence(None) == pytest.approx(0.5)
        assert rated_confidence(0.9) == pytest.approx(0.9)
        assert rated_confidence(0.0) == pytest.approx(0.0)

    def test_a_node_stored_before_the_field_went_nullable_still_loads(self):
        """The migration case, and it needs no migration.

        Every node written before 2026-08-19 carries `confidence: 0.5` — the
        old default, written by nothing else. Those rows load as a *rated* 0.5
        rather than as unrated, which overstates what was considered but is the
        only honest reading available: the row does not record whether anyone
        looked. Absence only starts meaning something for nodes written after
        the change, which is why nothing sweeps the old ones.
        """
        v = ValueSignal.model_validate({"confidence": 0.5, "importance": 0.5})
        assert v.confidence == 0.5

    def test_a_node_stored_with_relevance_still_loads(self):
        """The migration case for #44's removal, and the whole risk of it.

        `relevance` was written into every node of every existing graph. It is
        gone from the model, so loading one of those rows now hands Pydantic a
        key it does not declare — and a model configured `extra="forbid"` would
        raise, taking out every pre-existing graph on read. Nothing recoverable
        is lost by ignoring it: the field had no reader, which is why it went.
        """
        v = ValueSignal.model_validate(
            {"novelty": 0.3, "confidence": 0.9, "relevance": 0.37, "importance": 0.8}
        )

        assert not hasattr(v, "relevance")
        assert v.confidence == 0.9
        assert v.importance == 0.8

    def test_a_reloaded_signal_no_longer_carries_relevance(self):
        """It is dropped on the way out too, so a re-store cleans the row."""
        restored = ValueSignal.model_validate(
            ValueSignal.model_validate({"confidence": 0.9, "relevance": 0.37})
            .model_dump(mode="json")
        )
        assert "relevance" not in restored.model_dump(mode="json")

    def test_a_node_stored_with_novelty_still_loads(self):
        """The migration case for #46's removal, and the wider one of the two.

        `relevance` was at least written by something. `novelty` was written by
        nothing after creation: every node in every existing graph carries it at
        exactly 1.0, because the default was the only writer. So the key is in
        more rows than `relevance` ever was, and a model configured
        `extra="forbid"` would raise on all of them.

        Nothing recoverable is lost. What the field reached for — how unlike the
        rest of the graph a node is — is not a property of the node at all: the
        same content scores differently arriving into an empty graph and into a
        mature one, so a stored answer records ingest order and then freezes.
        Asked at read time against the graph as it stands, the question is
        well-posed, and `vector_search` already answers it.
        """
        v = ValueSignal.model_validate(
            {"novelty": 0.3, "confidence": 0.9, "importance": 0.8}
        )

        assert not hasattr(v, "novelty")
        assert v.confidence == 0.9
        assert v.importance == 0.8

    def test_a_reloaded_signal_no_longer_carries_novelty(self):
        """Dropped on the way out too, so a re-store cleans the row."""
        restored = ValueSignal.model_validate(
            ValueSignal.model_validate({"confidence": 0.9, "novelty": 0.3})
            .model_dump(mode="json")
        )
        assert "novelty" not in restored.model_dump(mode="json")

    def test_both_clocks_start_unset(self):
        """"Never retrieved" and "never judged" are real states, not fictions.

        Defaulting these to `now` would make a node that nothing has ever
        touched indistinguishable from one touched a moment ago — which is what
        forced archival nomination to compare timestamps with a one-second
        tolerance and call the result "never used".
        """
        v = ValueSignal()
        assert v.retrieved_at is None
        assert v.importance_judged_at is None

    def test_a_record_written_before_these_fields_existed_reads_as_unset(self):
        """The migration case, and the reason `None` is the right default.

        `ValueSignal` is persisted inside its node as JSON, so a record written
        earlier simply lacks both keys. Defaulting to `now` would load every
        pre-existing node as freshly retrieved, silently exempting whole graphs
        from cleanup; `None` loads them as never touched, which merely proposes
        them for review.
        """
        v = ValueSignal.model_validate(
            {"novelty": 1.0, "confidence": 0.5, "importance": 0.5}
        )
        assert v.retrieved_at is None
        assert v.importance_judged_at is None

    def test_custom_values(self):
        v = ValueSignal(importance=0.3, confidence=0.9)
        assert v.importance == 0.3
        assert v.confidence == 0.9

    def test_bounds_validation(self):
        with pytest.raises(Exception):
            ValueSignal(importance=1.5)
        with pytest.raises(Exception):
            ValueSignal(confidence=-0.1)


class TestMergedValueSignal:
    """The one place a merge decides what a combined signal says (#45)."""

    def _at(self, days_ago: int) -> datetime:
        return datetime.now(timezone.utc) - timedelta(days=days_ago)

    def test_a_real_timestamp_beats_never(self):
        """`None` means never happened, so it loses to anything that did."""
        judged, retrieved = self._at(200), self._at(5)
        merged = merged_value_signal([
            ValueSignal(importance_judged_at=judged, retrieved_at=retrieved),
            ValueSignal(),
        ])
        assert merged.importance_judged_at == judged
        assert merged.retrieved_at == retrieved

    def test_the_later_of_two_real_timestamps_wins(self):
        older, newer = self._at(200), self._at(10)
        merged = merged_value_signal([
            ValueSignal(importance_judged_at=older),
            ValueSignal(importance_judged_at=newer),
        ])
        assert merged.importance_judged_at == newer

    def test_order_does_not_matter(self):
        older, newer = self._at(200), self._at(10)
        a = ValueSignal(importance=0.9, importance_judged_at=older)
        b = ValueSignal(importance=0.2, importance_judged_at=newer)
        assert merged_value_signal([a, b]) == merged_value_signal([b, a])

    def test_untouched_sources_produce_an_untouched_signal(self):
        """A merge invents no history: nothing happened, so both clocks stay unset."""
        merged = merged_value_signal([ValueSignal(), ValueSignal()])
        assert merged.importance_judged_at is None
        assert merged.retrieved_at is None

    def test_scalars_keep_the_behaviour_both_sites_already_had(self):
        merged = merged_value_signal([
            ValueSignal(confidence=0.8, importance=0.9),
            ValueSignal(confidence=0.4, importance=0.2),
        ])
        assert merged.confidence == pytest.approx(0.8)   # max
        assert merged.importance == pytest.approx(0.9)   # max

    def test_a_rated_confidence_beats_an_unrated_one(self):
        """Same rule the clocks use: `None` is "nobody said", so it loses.

        Reading the unrated side as 0.5 first would let an absence outrank a
        deliberate 0.3 — a judgment nobody made beating one somebody did.
        """
        merged = merged_value_signal([ValueSignal(confidence=0.3), ValueSignal()])
        assert merged.confidence == pytest.approx(0.3)

    def test_merging_unrated_signals_produces_an_unrated_one(self):
        """A merge invents no judgment, exactly as it invents no history."""
        assert merged_value_signal([ValueSignal(), ValueSignal()]).confidence is None

    def test_merging_nothing_is_refused(self):
        """Silently returning defaults would look like a merge that lost everything."""
        with pytest.raises(ValueError):
            merged_value_signal([])


class TestRawDocument:

    def test_creation(self):
        doc = RawDocument(content="Hello world")
        assert doc.content == "Hello world"
        assert doc.id is not None
        assert doc.metadata == {}

    def test_with_metadata(self):
        doc = RawDocument(content="test", metadata={"source": "web"})
        assert doc.metadata["source"] == "web"


class TestSegment:

    def test_creation(self):
        seg = Segment(source_id="doc1", text="Some text", span_start=0, span_end=9)
        assert seg.source_id == "doc1"
        assert seg.text == "Some text"
        assert seg.span_start == 0
        assert seg.span_end == 9

    def test_serialization_roundtrip(self):
        seg = Segment(source_id="doc1", text="Some text", span_start=0, span_end=9)
        data = seg.model_dump(mode="json")
        restored = Segment.model_validate(data)
        assert restored.id == seg.id
        assert restored.text == seg.text


class TestEpistemicNodes:

    def test_topic_defaults(self):
        t = Topic(content="A topic about ML", source_id="seg1")
        assert t.status == NodeStatus.ACTIVE
        assert t.superseded_at is None
        assert t.value.confidence is None       # unrated until an agent says
        assert t.extraction_method == "unspecified"

    def test_fact_defaults(self):
        f = Fact(content="Water boils at 100C", source_id="seg1")
        assert f.status == NodeStatus.ACTIVE

    def test_inference_defaults(self):
        i = Inference(content="This implies X", source_id="seg1")
        assert i.status == NodeStatus.ACTIVE

    def test_unique_ids(self):
        t1 = Topic(content="A", source_id="s1")
        t2 = Topic(content="B", source_id="s1")
        assert t1.id != t2.id

    def test_serialization_roundtrip(self):
        t = Topic(content="test", source_id="s1", value=ValueSignal(confidence=0.7))
        data = t.model_dump(mode="json")
        restored = Topic.model_validate(data)
        assert restored.id == t.id
        assert restored.value.confidence == 0.7


class TestNodeEdge:

    def test_creation(self):
        e = NodeEdge(src_id="a", dst_id="b", type=EdgeType.SUPPORTS)
        assert e.src_id == "a"
        assert e.dst_id == "b"
        assert e.type == EdgeType.SUPPORTS
        assert e.weight == 1.0

    def test_history_edge_types(self):
        e1 = NodeEdge(src_id="a", dst_id="b", type=EdgeType.SUPERSEDED_BY)
        e2 = NodeEdge(src_id="a", dst_id="c", type=EdgeType.MERGED_INTO)
        assert e1.type == EdgeType.SUPERSEDED_BY
        assert e2.type == EdgeType.MERGED_INTO


class TestEmbeddingRecord:

    def test_creation(self):
        emb = EmbeddingRecord(item_id="node1", model_id="all-MiniLM-L6-v2", vector=[0.1, 0.2, 0.3])
        assert emb.item_id == "node1"
        assert emb.model_id == "all-MiniLM-L6-v2"
        assert len(emb.vector) == 3


class TestPetritypeIntegration:
    """Verify our types work as Petri net tokens."""

    def test_topic_as_token(self):
        t = Topic(content="test", source_id="s1")
        place = ListPlaceNode("Topics", Topic, [t])
        assert len(place.tokens) == 1
        assert place.tokens[0].content == "test"

    def test_fact_as_token(self):
        f = Fact(content="a fact", source_id="s1")
        place = ListPlaceNode("Facts", Fact, [f])
        assert len(place.tokens) == 1

    def test_inference_as_token(self):
        i = Inference(content="an inference", source_id="s1")
        place = ListPlaceNode("Inferences", Inference, [i])
        assert len(place.tokens) == 1

    def test_segment_as_token(self):
        s = Segment(source_id="d1", text="text", span_start=0, span_end=4)
        place = ListPlaceNode("Segments", Segment, [s])
        assert len(place.tokens) == 1

    def test_type_mismatch_rejected(self):
        t = Topic(content="test", source_id="s1")
        with pytest.raises(TypeError):
            ListPlaceNode("Facts", Fact, [t])


class TestTimepoint:

    def test_creation_with_concrete_dates(self):
        tp = Timepoint(
            start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end=datetime(2024, 12, 31, tzinfo=timezone.utc),
            label="Year 2024",
        )
        assert tp.id
        assert tp.start.year == 2024
        assert tp.label == "Year 2024"

    def test_creation_vague(self):
        tp = Timepoint(label="during the Renaissance")
        assert tp.id
        assert tp.start is None
        assert tp.end is None
        assert tp.label == "during the Renaissance"

    def test_unique_ids(self):
        tp1 = Timepoint(label="a")
        tp2 = Timepoint(label="b")
        assert tp1.id != tp2.id


class TestTimeline:

    def test_creation(self):
        tl = Timeline(name="History of AI", description="Key events in AI")
        assert tl.id
        assert tl.name == "History of AI"
        assert tl.timepoints == []

    def test_serialization_roundtrip(self):
        tp = Timepoint(label="event")
        tl = Timeline(name="test", timepoints=[tp])
        data = tl.model_dump(mode="json")
        restored = Timeline.model_validate(data)
        assert restored.name == "test"
        assert len(restored.timepoints) == 1
        assert restored.timepoints[0].id == tp.id


class TestMetacontext:

    def test_creation(self):
        mc = Metacontext(content="Real historical events")
        assert mc.id
        assert mc.content == "Real historical events"
        assert mc.status == NodeStatus.ACTIVE

    def test_has_value_signals(self):
        """A frame is created by a tool call, not extracted from material, so
        nobody is in a position to rate it — which is what unrated means."""
        mc = Metacontext(content="test")
        assert mc.value.confidence is None
        assert mc.value.importance == 0.5

    def test_serialization_roundtrip(self):
        mc = Metacontext(content="Fiction", description="Fictional setting")
        data = mc.model_dump(mode="json")
        restored = Metacontext.model_validate(data)
        assert restored.content == "Fiction"
        assert restored.description == "Fictional setting"


class TestNewEdgeTypes:

    def test_timelink_edge(self):
        e = NodeEdge(
            src_id="fact-1",
            dst_id="timeline-1",
            type=EdgeType.TIMELINK,
            metadata={"timepoint_id": "tp-1"},
        )
        assert e.type == EdgeType.TIMELINK
        assert e.metadata["timepoint_id"] == "tp-1"

    def test_associated_timeline_edge(self):
        e = NodeEdge(
            src_id="topic-1",
            dst_id="timeline-1",
            type=EdgeType.ASSOCIATED_TIMELINE,
        )
        assert e.type == EdgeType.ASSOCIATED_TIMELINE

    def test_has_metacontext_edge(self):
        e = NodeEdge(
            src_id="fact-1",
            dst_id="metacontext-1",
            type=EdgeType.HAS_METACONTEXT,
        )
        assert e.type == EdgeType.HAS_METACONTEXT


class TestValidityBelongsToASource:
    """Intervals hang off the provenance edge, and nowhere else (#53 T1 §2).

    Per source rather than per node is the decision the whole model turns on: a
    node-level set has to union what its sources assert, and union takes one
    careful source and one sloppy one and produces a period neither claims.
    Anywhere but `sourced_from` is that node-level set reached by accident —
    a period attributed to nobody, which nothing can check or migrate.
    """

    def _span(self) -> ValidityInterval:
        return ValidityInterval(
            start=PreciseInstant(at=datetime(1997, 5, 2, tzinfo=timezone.utc)),
            end=PreciseInstant(at=datetime(2010, 5, 11, tzinfo=timezone.utc)),
            basis=IntervalBasis.STATED,
        )

    def test_a_provenance_edge_carries_what_its_source_asserts(self):
        edge = NodeEdge(
            src_id="fact-1", dst_id="doc-1", type=EdgeType.SOURCED_FROM,
            validity=[self._span()],
        )

        assert edge.validity[0].basis is IntervalBasis.STATED

    def test_one_source_may_assert_several_separate_periods(self):
        """A party in government over two spans is one claim, not two."""
        edge = NodeEdge(
            src_id="fact-1", dst_id="doc-1", type=EdgeType.SOURCED_FROM,
            validity=[
                self._span(),
                ValidityInterval(
                    start=PreciseInstant(at=datetime(2024, 7, 5, tzinfo=timezone.utc)),
                    basis=IntervalBasis.STATED,
                ),
            ],
        )

        assert len(edge.validity) == 2
        assert isinstance(edge.validity[1].end, UnknownInstant)

    @pytest.mark.parametrize(
        "edge_type",
        [
            EdgeType.SIMILARITY,
            EdgeType.TAGGED_WITH,
            EdgeType.SUPPORTS,
            EdgeType.HAS_METACONTEXT,
        ],
    )
    def test_any_other_edge_refuses_them(self, edge_type):
        with pytest.raises(ValidationError, match="cannot carry validity"):
            NodeEdge(
                src_id="fact-1", dst_id="other-1", type=edge_type,
                validity=[self._span()],
            )

    def test_an_edge_without_intervals_is_unaffected(self):
        """Absence is the overwhelming case and must stay free of ceremony."""
        edge = NodeEdge(src_id="fact-1", dst_id="topic-1", type=EdgeType.SUPPORTS)

        assert edge.validity == []


class TestDocumentsCarryTheirPublicationDate:
    """`created_at` is ingest time and cannot stand in for it (#53 T1 §7).

    A 1970 memoir read today carries `created_at = 2026`. Using that as evidence
    about when a claim held is transaction time wearing valid time's clothes.
    """

    def test_a_publication_date_is_kept_apart_from_the_ingest_time(self):
        doc = RawDocument(
            content="the city is called Leningrad",
            published_at=PreciseInstant(at=datetime(1970, 6, 1, tzinfo=timezone.utc)),
        )

        assert doc.published_at.at.year == 1970
        assert doc.created_at.year != 1970

    def test_an_undated_document_stays_undated(self):
        """No fallback: a default would have every undated document claim its
        facts were witnessed on the day it happened to be ingested."""
        doc = RawDocument(content="undated")

        assert doc.published_at is None

    def test_a_publication_date_may_be_a_phrase_the_text_gives(self):
        doc = RawDocument(
            content="spring edition", published_at=NamedInstant(label="spring 1970"),
        )

        assert doc.published_at.label == "spring 1970"
