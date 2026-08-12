"""Tests for core Pydantic types."""

from datetime import datetime, timedelta, timezone

import pytest
from petritype.core.executable_graph_components import ListPlaceNode

from epimemer.core.types import (
    EdgeType,
    EmbeddingRecord,
    Fact,
    Inference,
    Metacontext,
    NodeEdge,
    NodeStatus,
    RawDocument,
    Segment,
    Timeline,
    Timepoint,
    Topic,
    ValueSignal,
    merged_value_signal,
    migration_disposition,
    moved_edge_types,
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
            assert EdgeType.HAS_METACONTEXT in moved
            assert EdgeType.SUPERSEDED_BY not in moved
            assert EdgeType.SUPERSESSION_CANDIDATE not in moved

    def test_legacy_superseded_rows_behave_as_they_always_did(self):
        """Nothing writes `SUPERSEDED` any more, but old graphs still load and
        must not change meaning under a policy written after them."""
        assert migration_disposition(
            EdgeType.SOURCED_FROM, NodeStatus.SUPERSEDED
        ) == "move"

    def test_topic_source_id_optional(self):
        assert Topic(content="BBC").source_id is None


class TestValueSignal:

    def test_default_values(self):
        v = ValueSignal()
        assert v.confidence == 0.5
        assert v.importance == 0.5

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
        assert t.value.confidence == 0.5
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
        mc = Metacontext(content="test")
        assert mc.value.confidence == 0.5
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
