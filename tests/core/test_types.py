"""Tests for core Pydantic types."""

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
    Provenance,
    RawDocument,
    Segment,
    Tag,
    Timeline,
    Timepoint,
    Topic,
    ValueSignal,
    parse_tag,
    provenance_matches,
    tag_matches,
    union_unique,
)


class TestTagAndProvenanceHelpers:

    def test_node_defaults_empty(self):
        f = Fact(content="x", source_id="s")
        assert f.tags == [] and f.provenance == []

    def test_parse_tag(self):
        assert parse_tag("k=v") == Tag(key="k", value="v")
        assert parse_tag("bare") == Tag(value="bare")
        # Split only on the first '=', so values may contain '='.
        assert parse_tag("url=http://x?a=b") == Tag(key="url", value="http://x?a=b")

    def test_tag_matches_key_value_keyonly_and_bare(self):
        f = Fact(
            content="x", source_id="s",
            tags=[Tag(key="speaker", value="alice"), Tag(value="interesting")],
        )
        assert tag_matches(f, ["speaker=alice"])   # key=value
        assert tag_matches(f, ["speaker="])         # key only, any value
        assert tag_matches(f, ["interesting"])      # bare value, any key
        assert not tag_matches(f, ["speaker=bob"])
        assert tag_matches(f, ["speaker=alice", "interesting"])   # AND of all
        assert not tag_matches(f, ["speaker=alice", "missing"])

    def test_provenance_matches(self):
        f = Fact(
            content="x", source_id="s",
            provenance=[Provenance(source="ISSUES.md", source_type="document")],
        )
        assert provenance_matches(f, source="ISSUES.md")
        assert provenance_matches(f, source_type="document")
        assert not provenance_matches(f, source="README.md")
        assert not provenance_matches(f, source_type="api")

    def test_union_unique_preserves_order_and_dedupes(self):
        a = [Tag(value="x"), Tag(value="y")]
        b = [Tag(value="y"), Tag(value="z")]
        assert union_unique(a, b) == [Tag(value="x"), Tag(value="y"), Tag(value="z")]


class TestValueSignal:

    def test_default_values(self):
        v = ValueSignal()
        assert v.novelty == 1.0
        assert v.confidence == 0.5
        assert v.relevance == 0.5
        assert v.last_reinforced is not None

    def test_custom_values(self):
        v = ValueSignal(novelty=0.3, confidence=0.9, relevance=0.1)
        assert v.novelty == 0.3
        assert v.confidence == 0.9
        assert v.relevance == 0.1

    def test_bounds_validation(self):
        with pytest.raises(Exception):
            ValueSignal(novelty=1.5)
        with pytest.raises(Exception):
            ValueSignal(confidence=-0.1)


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
        assert t.value.novelty == 1.0
        assert t.extraction_method == "llm"

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
        t = Topic(content="test", source_id="s1", value=ValueSignal(novelty=0.7))
        data = t.model_dump(mode="json")
        restored = Topic.model_validate(data)
        assert restored.id == t.id
        assert restored.value.novelty == 0.7


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
        from datetime import datetime, timezone
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
        assert mc.value.novelty == 1.0
        assert mc.value.confidence == 0.5

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
