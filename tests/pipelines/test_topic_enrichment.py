"""What reflect gathers under a topic, and what it calls a change.

`gather_associated_material_for` used to return content strings over two edge
types. Neither was enough. The strings could not say when a node arrived or
whether it had since been retired, which is the whole of the review question;
and the two edge types left out `tagged_with_topic`, so the enrichment scan
could not reach a tag at all, which is why every tag on a real graph is still
embedded on its name alone.

`changed_since` is the review rule itself, and it is pure, so it is asserted
here against hand-built nodes rather than through a reflect.
`dev-docs/DESCRIPTION_REVIEW.md` carries the case.
"""

from datetime import UTC, datetime, timedelta

from epimemer.core.types import (
    EdgeType,
    Fact,
    Inference,
    JudgeRef,
    NodeEdge,
    NodeStatus,
    Topic,
)
from epimemer.pipelines.metacontexts import TAG_EXTRACTION_METHOD
from epimemer.pipelines.reflection.topic_enrichment import (
    changed_since,
    described,
    gather_associated_material_for,
    material_contents,
)

CRITIC = JudgeRef(agent_id="critic", digest="d1")

REVIEWED_AT = datetime(2026, 9, 11, 12, 0, 0, tzinfo=UTC)
BEFORE = REVIEWED_AT - timedelta(hours=1)
AFTER = REVIEWED_AT + timedelta(hours=1)
LATER = REVIEWED_AT + timedelta(hours=2)


def _fact(content: str, *, created_at: datetime, **fields) -> Fact:
    return Fact(content=content, source_id="seg-1", created_at=created_at, **fields)


async def _linked(storage, topic: Topic, node, edge_type: EdgeType) -> None:
    await storage.store_node(node)
    await storage.store_edge(NodeEdge(src_id=node.id, dst_id=topic.id, type=edge_type))


class TestTheMaterialWalkReachesATag:
    """A tag holds neither `extracted_under_topic` nor `abstracts`: everything
    under it hangs off `tagged_with_topic`. Leaving that edge out was why the
    only path to a described tag was an agent calling `apply_reflection` by
    hand."""

    async def test_a_tagged_fact_is_material(self, storage):
        tag = Topic(content="issue-53", source_id=None, extraction_method=TAG_EXTRACTION_METHOD)
        await storage.store_node(tag)
        await _linked(storage, tag, _fact("a claim", created_at=BEFORE), EdgeType.TAGGED_WITH_TOPIC)

        material = await gather_associated_material_for([tag], storage)

        assert material_contents(material[tag.id]) == ["a claim"]

    async def test_the_older_two_edge_types_still_answer(self, storage):
        topic = Topic(content="Validity", source_id="seg-1")
        await storage.store_node(topic)
        await _linked(
            storage, topic, _fact("a claim", created_at=BEFORE), EdgeType.EXTRACTED_UNDER_TOPIC
        )
        await _linked(
            storage,
            topic,
            Inference(content="a conclusion", source_id="seg-1", created_at=BEFORE),
            EdgeType.ABSTRACTS,
        )

        material = await gather_associated_material_for([topic], storage)

        assert sorted(material_contents(material[topic.id])) == ["a claim", "a conclusion"]

    async def test_a_node_reached_twice_is_counted_once(self, storage):
        """A fact extracted under a topic and also tagged with it is one piece
        of material, not two, or the count and the sample both double it."""
        topic = Topic(content="Validity", source_id="seg-1")
        await storage.store_node(topic)
        fact = _fact("a claim", created_at=BEFORE)
        await _linked(storage, topic, fact, EdgeType.EXTRACTED_UNDER_TOPIC)
        await storage.store_edge(
            NodeEdge(src_id=fact.id, dst_id=topic.id, type=EdgeType.TAGGED_WITH_TOPIC)
        )

        material = await gather_associated_material_for([topic], storage)

        assert [node.id for node in material[topic.id]] == [fact.id]

    async def test_the_nodes_come_back_with_their_timestamps(self, storage):
        """The point of returning nodes rather than strings: `changed_since`
        asks when each one arrived, which a string cannot answer."""
        tag = Topic(content="issue-53", source_id=None, extraction_method=TAG_EXTRACTION_METHOD)
        await storage.store_node(tag)
        await _linked(storage, tag, _fact("a claim", created_at=BEFORE), EdgeType.TAGGED_WITH_TOPIC)

        material = await gather_associated_material_for([tag], storage)

        assert material[tag.id][0].created_at == BEFORE
        assert material[tag.id][0].superseded_at is None

    async def test_a_topic_with_nothing_under_it_gets_an_empty_list(self, storage):
        """Keyed for every topic asked about, so a caller never has to tell
        *nothing under it* from *not looked at*."""
        topic = Topic(content="Validity", source_id="seg-1")
        await storage.store_node(topic)

        material = await gather_associated_material_for([topic], storage)

        assert material[topic.id] == []


class TestWhatCountsAsAChange:
    def test_a_node_created_after_the_review_counts(self):
        changes = changed_since([_fact("new", created_at=AFTER)], REVIEWED_AT)

        assert [(c.content, c.change) for c in changes] == [("new", "created")]

    def test_a_node_created_before_it_does_not(self):
        assert changed_since([_fact("old", created_at=BEFORE)], REVIEWED_AT) == []

    def test_an_archival_after_the_review_counts(self):
        """A description written over material that has since been retired is
        stale, which is the reading the length ratio could never express."""
        archived = _fact(
            "retired", created_at=BEFORE, status=NodeStatus.ARCHIVED, superseded_at=AFTER
        )

        changes = changed_since([archived], REVIEWED_AT)

        assert [(c.content, c.change, c.at) for c in changes] == [("retired", "archived", AFTER)]

    def test_a_supersession_after_the_review_counts(self):
        corrected = _fact(
            "replaced", created_at=BEFORE, status=NodeStatus.CORRECTED, superseded_at=AFTER
        )

        changes = changed_since([corrected], REVIEWED_AT)

        assert [(c.content, c.change) for c in changes] == [("replaced", "superseded")]

    def test_a_retirement_before_the_review_does_not_count(self):
        """The description was written knowing the node was gone."""
        retired = _fact(
            "long gone",
            created_at=BEFORE - timedelta(hours=1),
            status=NodeStatus.ARCHIVED,
            superseded_at=BEFORE,
        )

        assert changed_since([retired], REVIEWED_AT) == []

    def test_a_node_that_arrived_and_was_retired_reports_the_retirement(self):
        """The later of the two, and the one that decides what the topic now
        stands over. Reporting both would count one node twice."""
        both = _fact("brief", created_at=AFTER, status=NodeStatus.ARCHIVED, superseded_at=LATER)

        changes = changed_since([both], REVIEWED_AT)

        assert [(c.change, c.at) for c in changes] == [("archived", LATER)]

    def test_changes_come_back_newest_first(self):
        material = [
            _fact("first", created_at=AFTER),
            _fact("third", created_at=LATER + timedelta(hours=1)),
            _fact("second", created_at=LATER),
        ]

        changes = changed_since(material, REVIEWED_AT)

        assert [c.content for c in changes] == ["third", "second", "first"]

    def test_nothing_under_the_topic_is_no_change(self):
        assert changed_since([], REVIEWED_AT) == []


class TestDescribingATopicRecordsThatSomebodyLooked:
    """Whoever writes the sentence has just read the material, which is the same
    act a confirmation records, so reflect measures the next change from it."""

    def test_it_stamps_the_review_time(self):
        topic = Topic(content="issue-53", source_id=None)

        described_topic = described(topic, "What this covers.", judge=CRITIC, at=REVIEWED_AT)

        assert described_topic.description_reviewed_at == REVIEWED_AT

    def test_it_defaults_to_now(self):
        topic = Topic(content="issue-53", source_id=None)
        before = datetime.now(UTC)

        described_topic = described(topic, "What this covers.", judge=CRITIC)

        assert described_topic.description_reviewed_at is not None
        assert described_topic.description_reviewed_at >= before

    def test_the_wording_it_replaced_is_still_kept(self):
        """The stamp is an addition to `described`, not a replacement for what
        it already did."""
        topic = Topic(content="issue-53", source_id=None, description="First.")

        described_topic = described(topic, "Second.", judge=CRITIC, at=REVIEWED_AT)

        assert described_topic.description == "Second."
        assert [e["replaced"] for e in described_topic.metadata["description_history"]] == [
            "First."
        ]

    def test_the_original_is_untouched(self):
        topic = Topic(content="issue-53", source_id=None)

        described(topic, "What this covers.", judge=CRITIC, at=REVIEWED_AT)

        assert topic.description_reviewed_at is None
        assert topic.description == ""
