"""One tag, however it is spelled, and however its node was retired.

`_tag_topic` resolves a tag at ingest and `_resolve_node_reference` resolves one for
`find_nodes`. Both matched content exactly, against active nodes only, so a
separator or a retirement made two topic nodes out of one tag and neither path
said so.

`dev-docs/TAG_IDENTITY.md` has the measurement that rules out fixing this with
the similarity bar: `claim-kind` and `claim_kind` score 0.9196 while
`dev-session-2026-09-05` and `dev-session-2026-09-06` score 0.9935.
"""

import pytest

from epimemer.core.types import (
    BASE_METACONTEXT_ID,
    EdgeType,
    EmbeddingRecord,
    Fact,
    JudgeRef,
    NodeEdge,
    NodeStatus,
    NodeType,
    Topic,
)
from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.mcp import tools
from epimemer.mcp.config import ServerConfig
from epimemer.pipelines.metacontexts import TAG_EXTRACTION_METHOD, created_from_tag
from epimemer.pipelines.name_resolution import tag_key

CRITIC = JudgeRef(agent_id="critic", digest="d1")


@pytest.fixture
def embedder():
    return MockEmbeddingProvider(model_id="mock-embed", dimension=8)


@pytest.fixture
def config():
    return ServerConfig(storage_backend="memory", embedding_provider="mock")


def _descriptions(tags) -> dict[str, str]:
    """One line per tag, because a tag this graph has never seen is refused
    without one. Identity is decided by `tag_key` and never by this prose, so a
    single form serves every name here."""
    return {name: f"Everything this graph files under {name}." for name in tags}


async def _ingest(storage, embedder, config, text, *, tags):
    """Ingest one paragraph carrying `tags`, and return the store's response."""
    seg, _ = await tools.segment_text(text, storage, embedder, config)
    stored, _ = await tools.store_decomposition(
        document_id=seg["document_id"],
        segments=[{"segment_id": seg["segments"][0]["segment_id"], "facts": ["a claim"]}],
        storage=storage,
        embedding_provider=embedder,
        metacontext_id=BASE_METACONTEXT_ID,
        tags=tags,
        tag_descriptions=_descriptions(tags),
        judge=CRITIC,
    )
    return stored


async def _tag_topics(storage) -> list[Topic]:
    nodes = await storage.query_nodes(node_type=NodeType.TOPIC)
    return [n for n in nodes if isinstance(n, Topic) and created_from_tag(n)]


async def _named(storage, content: str) -> Topic:
    found = await storage.get_node_by_content(content, node_type=NodeType.TOPIC)
    assert isinstance(found, Topic)
    return found


async def _retire_onto_new_name(storage, loser: Topic, survivor_name: str) -> Topic:
    """Retire `loser` as MERGED onto a fresh tag whose name shares no key with it."""
    survivor = Topic(content=survivor_name, source_id=None, extraction_method=TAG_EXTRACTION_METHOD)
    await storage.store_node(survivor)
    await storage.store_node(loser.model_copy(update={"status": NodeStatus.MERGED}))
    await storage.store_edge(
        NodeEdge(src_id=loser.id, dst_id=survivor.id, type=EdgeType.MERGED_INTO)
    )
    return survivor


class TestOneTagHoweverItIsSpelled:
    async def test_a_second_spelling_reuses_the_first_tag(self, storage, embedder, config):
        await _ingest(storage, embedder, config, "First document.", tags=["claim-kind"])
        await _ingest(storage, embedder, config, "Second document.", tags=["claim_kind"])
        tags = await _tag_topics(storage)
        assert [t.content for t in tags] == ["claim-kind"]

    async def test_the_response_says_which_spelling_it_resolved_to(self, storage, embedder, config):
        """Said rather than done silently, so the caller learns the house spelling."""
        await _ingest(storage, embedder, config, "First document.", tags=["claim-kind"])
        stored = await _ingest(storage, embedder, config, "Second.", tags=["claim_kind"])
        assert stored["tags_resolved_to"] == {"claim_kind": "claim-kind"}

    async def test_a_spelling_that_matches_reports_nothing(self, storage, embedder, config):
        stored = await _ingest(storage, embedder, config, "First.", tags=["claim-kind"])
        assert "tags_resolved_to" not in stored

    async def test_one_document_naming_both_spellings_creates_one_tag(
        self, storage, embedder, config
    ):
        """The cache answers before the store does, so it has to key the same way.

        Keyed by the raw name, both spellings miss the cache, both miss storage,
        and the call mints the pair this resolution exists to prevent.
        """
        await _ingest(storage, embedder, config, "One document.", tags=["claim_kind", "claim-kind"])
        assert len(await _tag_topics(storage)) == 1

    async def test_dated_session_tags_stay_apart(self, storage, embedder, config):
        """The pair the similarity bar cannot separate from the one above."""
        await _ingest(storage, embedder, config, "Monday.", tags=["dev-session-2026-09-05"])
        await _ingest(storage, embedder, config, "Tuesday.", tags=["dev-session-2026-09-06"])
        assert len(await _tag_topics(storage)) == 2

    async def test_an_unseen_name_still_creates_one_tag(self, storage, embedder, config):
        await _ingest(storage, embedder, config, "A document.", tags=["brand-new"])
        assert [t.content for t in await _tag_topics(storage)] == ["brand-new"]

    async def test_a_statement_topic_does_not_capture_a_tag_name(self, storage, embedder, config):
        """An extracted topic with a space in it is prose, and must not answer to
        a normalised tag name: only a tag-shaped name is one name with a tag."""
        prose = Topic(content="claim kind", source_id="seg1", extraction_method="agent")
        await storage.store_node(prose)
        await _ingest(storage, embedder, config, "A document.", tags=["claim_kind"])
        assert [t.content for t in await _tag_topics(storage)] == ["claim_kind"]


class TestATagOutlivesItsNode:
    async def test_a_merged_tag_name_reuses_the_survivor(self, storage, embedder, config):
        await _ingest(storage, embedder, config, "First.", tags=["issue-46"])
        loser = await _named(storage, "issue-46")
        survivor = await _retire_onto_new_name(storage, loser, "confidence-as-a-prior")

        await _ingest(storage, embedder, config, "Second.", tags=["issue-46"])
        assert [t.id for t in await _tag_topics(storage)] == [survivor.id]

    async def test_find_nodes_reaches_the_survivor_by_the_retired_name(
        self, storage, embedder, config
    ):
        """The read half of the same failure: it returned an empty list, not an error."""
        await _ingest(storage, embedder, config, "First.", tags=["issue-46"])
        loser = await _named(storage, "issue-46")
        survivor = await _retire_onto_new_name(storage, loser, "confidence-as-a-prior")
        for edge in await storage.get_edges_to(loser.id, edge_type=EdgeType.TAGGED_WITH_TOPIC):
            await storage.store_edge(
                NodeEdge(src_id=edge.src_id, dst_id=survivor.id, type=EdgeType.TAGGED_WITH_TOPIC)
            )

        found, _ = await tools.find_nodes(storage, tagged_with_topic="issue-46")
        assert found["nodes"]

    async def test_find_nodes_reaches_a_tag_by_another_spelling(self, storage, embedder, config):
        await _ingest(storage, embedder, config, "First.", tags=["claim-kind"])
        found, _ = await tools.find_nodes(storage, tagged_with_topic="claim_kind")
        assert found["nodes"]

    async def test_a_document_source_name_is_not_normalised(self, storage, embedder, config):
        """`ISSUES.md` must not answer to `issuesmd`: only the Topic branch normalises."""
        seg, _ = await tools.segment_text(
            "A document.", storage, embedder, config, source="ISSUES.md"
        )
        await tools.store_decomposition(
            document_id=seg["document_id"],
            segments=[{"segment_id": seg["segments"][0]["segment_id"], "facts": ["a claim"]}],
            storage=storage,
            embedding_provider=embedder,
            metacontext_id=BASE_METACONTEXT_ID,
            judge=CRITIC,
        )
        exact, _ = await tools.find_nodes(storage, sourced_from="ISSUES.md")
        assert exact["nodes"], "the exact source name must still resolve"

        normalised, _ = await tools.find_nodes(storage, sourced_from="issuesmd")
        assert normalised["nodes"] == []


class TestAnEqualKeyClearsTheSimilarityBar:
    async def _tag_with_vector(self, storage, embedder, content, vector):
        topic = Topic(content=content, source_id=None, extraction_method=TAG_EXTRACTION_METHOD)
        await storage.store_node(topic)
        await storage.store_embedding(
            EmbeddingRecord(item_id=topic.id, model_id=embedder.model_id, vector=vector)
        )
        return topic

    async def test_two_spellings_merge_below_the_bar(self, storage, embedder, config):
        """0.9196 is under 0.92, and an equal key is the stronger identity proof."""
        a = await self._tag_with_vector(storage, embedder, "claim-kind", [1.0] + [0.0] * 7)
        b = await self._tag_with_vector(storage, embedder, "claim_kind", [0.0, 1.0] + [0.0] * 6)
        result, _ = await tools.apply_reflection(
            storage,
            embedder,
            merges=[{"source_ids": [a.id, b.id], "content": "claim_kind"}],
            judge=CRITIC,
        )
        assert result["topics_merged"] == 1
        assert result["merges_rejected"] == 0

    async def test_different_keys_are_still_held_to_the_bar(self, storage, embedder, config):
        """Two days of work share no key, so nothing here lets them fuse."""
        a = await self._tag_with_vector(
            storage, embedder, "dev-session-2026-09-05", [1.0] + [0.0] * 7
        )
        b = await self._tag_with_vector(
            storage, embedder, "dev-session-2026-09-06", [0.0, 1.0] + [0.0] * 6
        )
        result, _ = await tools.apply_reflection(
            storage,
            embedder,
            merges=[{"source_ids": [a.id, b.id], "content": "dev-session"}],
            judge=CRITIC,
        )
        assert result["topics_merged"] == 0
        assert result["merges_rejected"] == 1

    async def test_a_prose_statement_source_is_still_held_to_the_bar(
        self, storage, embedder, config
    ):
        """A statement topic written as prose shares the key and is still not one
        name with the tag, so the bar decides."""
        a = await self._tag_with_vector(storage, embedder, "claim-kind", [1.0] + [0.0] * 7)
        b = Topic(content="claim kind", source_id="seg1", extraction_method="agent")
        await storage.store_node(b)
        await storage.store_embedding(
            EmbeddingRecord(item_id=b.id, model_id=embedder.model_id, vector=[0.0, 1.0] + [0.0] * 6)
        )
        result, _ = await tools.apply_reflection(
            storage,
            embedder,
            merges=[{"source_ids": [a.id, b.id], "content": "claim_kind"}],
            judge=CRITIC,
        )
        assert result["topics_merged"] == 0
        assert result["merges_rejected"] == 1


async def _decompose(storage, embedder, config, text, entries, *, tags, tag_descriptions=None):
    """Store one decomposition of `text`, one entry of `entries` per segment."""
    seg, _ = await tools.segment_text(text, storage, embedder, config)
    assert len(seg["segments"]) == len(entries)
    stored, _ = await tools.store_decomposition(
        document_id=seg["document_id"],
        segments=[
            {"segment_id": segment["segment_id"], **entry}
            for segment, entry in zip(seg["segments"], entries, strict=True)
        ],
        storage=storage,
        embedding_provider=embedder,
        metacontext_id=BASE_METACONTEXT_ID,
        tags=tags,
        tag_descriptions=_descriptions(tags) if tag_descriptions is None else tag_descriptions,
        judge=CRITIC,
    )
    return stored


async def _topics_keyed(storage, key: str) -> list[Topic]:
    """Every active topic node whose name normalises to `key`."""
    nodes = await storage.query_nodes(node_type=NodeType.TOPIC)
    return [n for n in nodes if isinstance(n, Topic) and tag_key(n.content) == key]


def _joined(stored) -> list[dict]:
    return [w for w in stored.get("warnings", []) if w["kind"] == "tag_joined_topic"]


TABLE_SESSIONS = "Running a tabletop game from a session, one folder per playthrough."

# Two unit vectors whose cosine is 0.7, about what the `table-sessions` twins scored.
APART_A = [1.0] + [0.0] * 7
APART_B = [0.7, (1 - 0.7**2) ** 0.5] + [0.0] * 6


class TestATagAndAnExtractedTopicOfOneName:
    """A tag-shaped name is one name, whichever of the two wrote it first.

    One call that tagged `table-sessions` and extracted a topic of that name
    minted two topic nodes, a statement topic and a tag, and the merge bar kept
    them apart. The tag now files under the topic, and the response says which
    topic and what it is described as, so the caller sees the meaning it joined.
    """

    async def test_a_tag_and_an_extracted_topic_of_one_name_are_one_node(
        self, storage, embedder, config
    ):
        stored = await _decompose(
            storage,
            embedder,
            config,
            "A table session.",
            [
                {
                    "topics": [{"content": "foo-bar", "description": TABLE_SESSIONS}],
                    "facts": ["a claim"],
                }
            ],
            tags=["foo-bar"],
        )

        (topic,) = await _topics_keyed(storage, "foobar")
        assert topic.content == "foo-bar"
        assert not created_from_tag(topic)
        assert stored["tags_created"] == 0
        (warning,) = _joined(stored)
        assert warning["detail"] == {
            "tag": "foo-bar",
            "topic": "foo-bar",
            "description": TABLE_SESSIONS,
        }
        assert warning["subjects"] == [topic.id]
        assert "'foo-bar'" in warning["message"] and TABLE_SESSIONS in warning["message"]

    async def test_the_tagged_fact_hangs_off_the_topic_and_the_topic_not_off_itself(
        self, storage, embedder, config
    ):
        await _decompose(
            storage,
            embedder,
            config,
            "A table session.",
            [{"topics": [{"content": "foo-bar"}], "facts": ["a claim"]}],
            tags=["foo-bar"],
        )

        (topic,) = await _topics_keyed(storage, "foobar")
        tagged = await storage.get_edges_to(topic.id, edge_type=EdgeType.TAGGED_WITH_TOPIC)
        fact = await storage.get_node_by_content("a claim", node_type=NodeType.FACT)
        assert {edge.src_id for edge in tagged} == {fact.id}

    async def test_an_undescribed_topic_takes_the_tag_description(self, storage, embedder, config):
        """The caller described the name once, under `tag_descriptions`, and a
        topic with no description of its own takes it, as an undescribed tag does."""
        await _decompose(
            storage,
            embedder,
            config,
            "A table session.",
            [{"topics": [{"content": "foo-bar"}], "facts": ["a claim"]}],
            tags=["foo-bar"],
            tag_descriptions={"foo-bar": TABLE_SESSIONS},
        )

        (topic,) = await _topics_keyed(storage, "foobar")
        assert topic.description == TABLE_SESSIONS

    async def test_a_tag_resolves_to_a_topic_a_later_segment_extracts(
        self, storage, embedder, config
    ):
        """Every node of the call is built before any tag is applied, so a
        document-level tag on the first segment reaches a topic in the second,
        and needs no description of its own."""
        stored = await _decompose(
            storage,
            embedder,
            config,
            "First paragraph here.\n\nSecond paragraph there.",
            [
                {"facts": ["a claim"]},
                {"topics": [{"content": "foo-bar", "description": TABLE_SESSIONS}]},
            ],
            tags=["foo-bar"],
            tag_descriptions={},
        )

        (topic,) = await _topics_keyed(storage, "foobar")
        assert not created_from_tag(topic)
        assert len(_joined(stored)) == 1

    async def test_a_stored_topic_spelled_another_way_takes_the_tag(
        self, storage, embedder, config
    ):
        await _decompose(
            storage,
            embedder,
            config,
            "The first session.",
            [{"topics": [{"content": "foo_bar", "description": TABLE_SESSIONS}]}],
            tags=[],
        )

        stored = await _decompose(
            storage,
            embedder,
            config,
            "The second session.",
            [{"facts": ["a claim"]}],
            tags=["foo-bar"],
        )

        (topic,) = await _topics_keyed(storage, "foobar")
        assert topic.content == "foo_bar"
        assert stored["tags_resolved_to"] == {"foo-bar": "foo_bar"}
        (warning,) = _joined(stored)
        assert warning["detail"]["topic"] == "foo_bar"
        assert warning["detail"]["description"] == TABLE_SESSIONS
        fact = await storage.get_node_by_content("a claim", node_type=NodeType.FACT)
        tagged = await storage.get_edges_from(fact.id, edge_type=EdgeType.TAGGED_WITH_TOPIC)
        assert [edge.dst_id for edge in tagged] == [topic.id]

    async def test_the_described_topic_keeps_its_description(self, storage, embedder, config):
        """The tag's sentence is not written over the topic's own."""
        await _decompose(
            storage,
            embedder,
            config,
            "The first session.",
            [{"topics": [{"content": "foo_bar", "description": TABLE_SESSIONS}]}],
            tags=[],
        )

        stored = await _decompose(
            storage,
            embedder,
            config,
            "The second session.",
            [{"facts": ["a claim"]}],
            tags=["foo-bar"],
            tag_descriptions={"foo-bar": "Something else entirely."},
        )

        (topic,) = await _topics_keyed(storage, "foobar")
        assert topic.description == TABLE_SESSIONS
        assert "description_not_written" in [w["kind"] for w in stored["warnings"]]

    async def test_a_tag_node_still_wins_over_an_extracted_topic(self, storage, embedder, config):
        """Where a tag of the name exists already, the tag answers, as before."""
        await _ingest(storage, embedder, config, "First.", tags=["foo_bar"])
        tag = await _named(storage, "foo_bar")

        stored = await _decompose(
            storage,
            embedder,
            config,
            "Second.",
            [{"topics": [{"content": "Foo-Bar"}], "facts": ["another claim"]}],
            tags=["foo-bar"],
        )

        fact = await storage.get_node_by_content("another claim", node_type=NodeType.FACT)
        tagged = await storage.get_edges_from(fact.id, edge_type=EdgeType.TAGGED_WITH_TOPIC)
        assert [edge.dst_id for edge in tagged] == [tag.id]
        assert _joined(stored) == []

    async def test_find_nodes_reaches_the_topic_by_the_tag_spelling(
        self, storage, embedder, config
    ):
        """The read path resolves the way ingest files: a name reaches a topic
        node of one name, tag or not."""
        topic = Topic(content="foo_bar", source_id="seg1", extraction_method="agent")
        fact = Fact(content="a claim", source_id="seg1")
        await storage.store_node(topic)
        await storage.store_node(fact)
        await storage.store_edge(
            NodeEdge(src_id=fact.id, dst_id=topic.id, type=EdgeType.TAGGED_WITH_TOPIC)
        )

        found, _ = await tools.find_nodes(storage, tagged_with_topic="foo-bar")
        assert [node["content"] for node in found["nodes"]] == ["a claim"]


async def _extracted(storage, embedder, config, content: str) -> Topic:
    """Store a topic extracted under `content`, with no tag in the call."""
    await _decompose(
        storage,
        embedder,
        config,
        f"A document about {content}.",
        [{"topics": [{"content": content, "description": TABLE_SESSIONS}]}],
        tags=[],
    )
    return await _named(storage, content)


async def _merged(storage, embedder, sources: list[Topic], content: str) -> Topic:
    result, _ = await tools.apply_reflection(
        storage,
        embedder,
        merges=[{"source_ids": [source.id for source in sources], "content": content}],
        judge=CRITIC,
    )
    assert result["topics_merged"] == 1, result
    survivor = await _named(storage, content)
    assert not created_from_tag(survivor)
    return survivor


async def _tag_merged_into_topic(storage, embedder, config, survivor: str = "foo-bar") -> Topic:
    """A tag `foo-bar` and a topic extracted as `foo_bar`, merged by a judge."""
    await _ingest(storage, embedder, config, "First.", tags=["foo-bar"])
    tag = await _named(storage, "foo-bar")
    topic = await _extracted(storage, embedder, config, "foo_bar")
    return await _merged(storage, embedder, [tag, topic], survivor)


async def _tagged_target(storage, fact: str) -> str:
    node = await storage.get_node_by_content(fact, node_type=NodeType.FACT)
    [edge] = await storage.get_edges_from(node.id, edge_type=EdgeType.TAGGED_WITH_TOPIC)
    return edge.dst_id


class TestATagMergedIntoATopicOfItsName:
    """A judge who merged a tag into a topic has decided the two are one.

    The survivor of a mixed merge is not stamped as a tag, so it is a topic
    node not created from a tag. Its merge history says a tag of the same key
    was among its sources, and that is what stops `tag_joined_topic` raising
    on every later use of the tag. The history is the survivor's own
    `merged_from`, so a graph that already holds such a survivor needs no
    repair.
    """

    async def test_the_tag_used_after_the_merge_raises_no_warning(self, storage, embedder, config):
        survivor = await _tag_merged_into_topic(storage, embedder, config)

        stored = await _decompose(
            storage, embedder, config, "Later.", [{"facts": ["a claim"]}], tags=["foo-bar"]
        )

        assert _joined(stored) == []
        assert await _tagged_target(storage, "a claim") == survivor.id

    async def test_another_spelling_of_the_tag_raises_none_either(self, storage, embedder, config):
        survivor = await _tag_merged_into_topic(storage, embedder, config)

        stored = await _decompose(
            storage, embedder, config, "Later.", [{"facts": ["a claim"]}], tags=["Foo_Bar"]
        )

        assert _joined(stored) == []
        assert await _tagged_target(storage, "a claim") == survivor.id

    async def test_a_survivor_named_otherwise_is_reached_without_a_warning(
        self, storage, embedder, config
    ):
        """The tag reaches the survivor through the merge, by its retired name."""
        survivor = await _tag_merged_into_topic(
            storage, embedder, config, survivor="table-sessions"
        )

        stored = await _decompose(
            storage, embedder, config, "Later.", [{"facts": ["a claim"]}], tags=["foo-bar"]
        )

        assert _joined(stored) == []
        assert await _tagged_target(storage, "a claim") == survivor.id

    async def test_a_tag_of_another_key_still_warns(self, storage, embedder, config):
        """The survivor's own name was never a tag, so a tag of that name joins it."""
        survivor = await _tag_merged_into_topic(
            storage, embedder, config, survivor="table-sessions"
        )

        stored = await _decompose(
            storage,
            embedder,
            config,
            "Later.",
            [{"facts": ["a claim"]}],
            tags=["table-sessions"],
        )

        (warning,) = _joined(stored)
        assert warning["subjects"] == [survivor.id]

    async def test_a_survivor_merged_again_raises_none(self, storage, embedder, config):
        """The history is followed back through a survivor among the sources."""
        first = await _tag_merged_into_topic(storage, embedder, config)
        another = await _extracted(storage, embedder, config, "FOO-BAR")
        survivor = await _merged(storage, embedder, [first, another], "foo-bar")

        stored = await _decompose(
            storage, embedder, config, "Later.", [{"facts": ["a claim"]}], tags=["foo-bar"]
        )

        assert _joined(stored) == []
        assert await _tagged_target(storage, "a claim") == survivor.id

    async def test_a_merge_of_topics_alone_still_warns(self, storage, embedder, config):
        first = await _extracted(storage, embedder, config, "foo_bar")
        second = await _extracted(storage, embedder, config, "Foo-Bar")
        survivor = await _merged(storage, embedder, [first, second], "foo-bar")

        stored = await _decompose(
            storage, embedder, config, "Later.", [{"facts": ["a claim"]}], tags=["foo-bar"]
        )

        (warning,) = _joined(stored)
        assert warning["subjects"] == [survivor.id]

    async def test_a_topic_never_merged_with_a_tag_warns_every_time(
        self, storage, embedder, config
    ):
        topic = await _extracted(storage, embedder, config, "foo_bar")

        for text in ("Monday.", "Tuesday."):
            stored = await _decompose(
                storage, embedder, config, text, [{"facts": [f"{text} a claim"]}], tags=["foo-bar"]
            )
            (warning,) = _joined(stored)
            assert warning["subjects"] == [topic.id]


class TestReflectNominatesOneNameByName:
    """A pair that is one name is offered whatever its cosine.

    The two `table-sessions` nodes scored 0.7053, under the 0.85 nomination bar,
    because one embeds on its name and description and the other on its name
    alone, so reflect never showed the duplicate.
    """

    async def _seeded(self, storage, embedder, content, vector, *, tag: bool):
        topic = Topic(
            content=content,
            source_id=None if tag else "seg1",
            extraction_method=TAG_EXTRACTION_METHOD if tag else "agent",
        )
        await storage.store_node(topic)
        await storage.store_edge(
            NodeEdge(src_id=topic.id, dst_id=BASE_METACONTEXT_ID, type=EdgeType.HAS_METACONTEXT)
        )
        await storage.store_embedding(
            EmbeddingRecord(item_id=topic.id, model_id=embedder.model_id, vector=vector)
        )
        return topic

    async def _pairs(self, storage, embedder) -> list[dict]:
        result, _ = await tools.reflect(storage, embedder)
        return result["similar_pairs"]

    async def test_a_one_name_pair_below_the_bar_is_nominated_and_marked(self, storage, embedder):
        a = await self._seeded(storage, embedder, "foo-bar", APART_A, tag=True)
        b = await self._seeded(storage, embedder, "foo_bar", APART_B, tag=False)

        (pair,) = await self._pairs(storage, embedder)

        assert {pair["topic_a"]["id"], pair["topic_b"]["id"]} == {a.id, b.id}
        assert pair["same_name"] is True
        assert pair["similarity"] == pytest.approx(0.7, abs=1e-4)

    async def test_a_pair_of_different_names_below_the_bar_is_not(self, storage, embedder):
        await self._seeded(storage, embedder, "foo-bar", APART_A, tag=True)
        await self._seeded(storage, embedder, "foo-baz", APART_B, tag=False)

        assert await self._pairs(storage, embedder) == []

    async def test_a_cosine_pair_carries_no_marker(self, storage, embedder):
        await self._seeded(storage, embedder, "foo-bar", APART_A, tag=True)
        await self._seeded(storage, embedder, "foo-baz", APART_A, tag=False)

        (pair,) = await self._pairs(storage, embedder)
        assert "same_name" not in pair

    async def test_a_recorded_verdict_stops_the_pair_coming_back(self, storage, embedder):
        a = await self._seeded(storage, embedder, "foo-bar", APART_A, tag=True)
        b = await self._seeded(storage, embedder, "foo_bar", APART_B, tag=False)
        assert len(await self._pairs(storage, embedder)) == 1

        recorded, _ = await tools.apply_reflection(
            storage,
            embedder,
            similarities=[
                {"pair": [a.id, b.id], "verdict": "distinct", "because": "the tag means more"}
            ],
            judge=CRITIC,
        )

        assert recorded["similarities_recorded"] == 1
        assert await self._pairs(storage, embedder) == []
