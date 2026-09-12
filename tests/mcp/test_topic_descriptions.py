"""A topic's name stays put while its description is written beside it.

Enrichment used to replace `content` and retire the original `CORRECTED`. That
moved the name `_tag_topic` resolves at ingest and `_resolve_node_reference`
resolves for `find_nodes`, so a tag split in two: documents tagged before the
enrichment stayed on the first node, documents tagged after minted a second, and
neither knew about the other. The regression test at the bottom of this file is
that failure, run end to end.

The rest asserts what replaced it: the write is in place, the wording it
replaced survives on the node, and only a topic node created from a tag joins
its description into what it is embedded on.
`dev-docs/TOPIC_DESCRIPTIONS.md` carries the case.
"""

import pytest

from epimemer.core.types import (
    BASE_METACONTEXT_ID,
    EdgeType,
    JudgeRef,
    NodeStatus,
    NodeType,
    Topic,
)
from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.mcp import tools
from epimemer.mcp.config import ServerConfig
from epimemer.pipelines.embedding_text import embedding_text
from epimemer.pipelines.metacontexts import TAG_EXTRACTION_METHOD, created_from_tag
from epimemer.pipelines.reflection.topic_enrichment import _should_enrich

CRITIC = JudgeRef(agent_id="critic", digest="d1")
EDITOR = JudgeRef(agent_id="editor", digest="d2")

ISSUE_53 = "Validity intervals: when a claim was true, per source."


@pytest.fixture
def embedder():
    return MockEmbeddingProvider(model_id="mock-embed", dimension=8)


@pytest.fixture
def config():
    return ServerConfig(storage_backend="memory", embedding_provider="mock")


async def _ingest(storage, embedder, config, text, *, tags=(), facts=("a claim",)):
    """One document carrying `tags`.

    The descriptions are what the ingest rule requires of a new tag; the tests
    below then overwrite them through `apply_reflection`, which is the act they
    are about.
    """
    seg, _ = await tools.segment_text(text, storage, embedder, config)
    stored, _ = await tools.store_decomposition(
        document_id=seg["document_id"],
        segments=[{"segment_id": seg["segments"][0]["segment_id"], "facts": list(facts)}],
        storage=storage,
        embedding_provider=embedder,
        metacontext_id=BASE_METACONTEXT_ID,
        tags=list(tags),
        tag_descriptions={name: f"Everything filed under {name}." for name in tags},
        judge=CRITIC,
    )
    return stored


async def _undescribed_topic(storage, embedder, config, content="Validity") -> Topic:
    """A topic with no description, for the tests about what a first one does.

    A statement topic rather than a tag: a tag is refused at ingest without a
    description now, so the only topic that can arrive undescribed is one whose
    `content` already says what it covers.
    """
    seg, _ = await tools.segment_text("A document.", storage, embedder, config)
    await tools.store_decomposition(
        document_id=seg["document_id"],
        segments=[{"segment_id": seg["segments"][0]["segment_id"], "topics": [content]}],
        storage=storage,
        embedding_provider=embedder,
        metacontext_id=BASE_METACONTEXT_ID,
        judge=CRITIC,
    )
    return await _named(storage, content)


async def _tag_topics(storage) -> list[Topic]:
    nodes = await storage.query_nodes(node_type=NodeType.TOPIC)
    return [n for n in nodes if isinstance(n, Topic) and created_from_tag(n)]


async def _named(storage, content: str) -> Topic:
    found = await storage.get_node_by_content(content, node_type=NodeType.TOPIC)
    assert isinstance(found, Topic)
    return found


async def _enrich(storage, embedder, topic_id: str, description: str, *, judge=EDITOR):
    result, _ = await tools.apply_reflection(
        storage,
        embedder,
        enrichments=[{"topic_id": topic_id, "description": description}],
        judge=judge,
    )
    return result


class TestEnrichmentWritesBesideTheNameRatherThanOverIt:
    async def test_the_content_is_byte_identical_afterwards(self, storage, embedder, config):
        await _ingest(storage, embedder, config, "A document.", tags=["issue-53"])
        before = await _named(storage, "issue-53")

        await _enrich(storage, embedder, before.id, ISSUE_53)

        after = await storage.get_node(before.id)
        assert after.content == before.content == "issue-53"
        assert after.description == ISSUE_53

    async def test_the_node_id_does_not_move(self, storage, embedder, config):
        await _ingest(storage, embedder, config, "A document.", tags=["issue-53"])
        before = await _named(storage, "issue-53")

        result = await _enrich(storage, embedder, before.id, ISSUE_53)

        assert result["topics_enriched"] == 1
        assert [t.id for t in await _tag_topics(storage)] == [before.id]

    async def test_nothing_is_retired_and_no_lineage_is_written(self, storage, embedder, config):
        """A `CORRECTED` node is the one status `restore` refuses by design, so
        the old write left the original beyond every designed path back."""
        await _ingest(storage, embedder, config, "A document.", tags=["issue-53"])
        topic = await _named(storage, "issue-53")

        await _enrich(storage, embedder, topic.id, ISSUE_53)

        after = await storage.get_node(topic.id)
        assert after.status is NodeStatus.ACTIVE
        assert await storage.get_edges_from(topic.id, edge_type=EdgeType.SUPERSEDED_BY) == []
        assert not [
            n
            for n in await storage.query_nodes(node_type=NodeType.TOPIC)
            if n.status is NodeStatus.CORRECTED
        ]

    async def test_the_tagged_edges_still_point_at_it(self, storage, embedder, config):
        await _ingest(storage, embedder, config, "A document.", tags=["issue-53"])
        topic = await _named(storage, "issue-53")
        before = await storage.get_edges_to(topic.id, edge_type=EdgeType.TAGGED_WITH_TOPIC)

        await _enrich(storage, embedder, topic.id, ISSUE_53)

        after = await storage.get_edges_to(topic.id, edge_type=EdgeType.TAGGED_WITH_TOPIC)
        assert {e.id for e in after} == {e.id for e in before}
        assert before

    async def test_the_extraction_method_still_says_tag(self, storage, embedder, config):
        """It used to gain `:enriched`, which is what stopped `created_from_tag`
        recognising the node and put it back inside the merge metacontext gate."""
        await _ingest(storage, embedder, config, "A document.", tags=["issue-53"])
        topic = await _named(storage, "issue-53")

        await _enrich(storage, embedder, topic.id, ISSUE_53)

        after = await storage.get_node(topic.id)
        assert after.extraction_method == TAG_EXTRACTION_METHOD
        assert created_from_tag(after)


class TestTheReplacedWordingIsRecoverable:
    """`description` must not be the one field on a topic that can be edited and
    the one field with no history. The trail sits on the node, beside
    `metacontext_reassignments`, rather than in the journal row, whose payload
    shape is shared by every caller of `journal`."""

    async def test_a_first_description_records_no_history(self, storage, embedder, config):
        """There is nothing to keep: an empty description is undescribed, and a
        trail entry saying so would be a revision nobody made."""
        topic = await _undescribed_topic(storage, embedder, config)

        await _enrich(storage, embedder, topic.id, ISSUE_53)

        after = await storage.get_node(topic.id)
        assert "description_history" not in after.metadata

    async def test_a_second_description_keeps_the_first(self, storage, embedder, config):
        topic = await _undescribed_topic(storage, embedder, config)
        await _enrich(storage, embedder, topic.id, ISSUE_53)

        await _enrich(storage, embedder, topic.id, "A sharper sentence.", judge=CRITIC)

        after = await storage.get_node(topic.id)
        assert after.description == "A sharper sentence."
        assert [entry["replaced"] for entry in after.metadata["description_history"]] == [ISSUE_53]

    async def test_the_trail_names_who_wrote_the_replacement(self, storage, embedder, config):
        topic = await _undescribed_topic(storage, embedder, config)
        await _enrich(storage, embedder, topic.id, ISSUE_53, judge=EDITOR)

        await _enrich(storage, embedder, topic.id, "A sharper sentence.", judge=CRITIC)

        after = await storage.get_node(topic.id)
        assert after.metadata["description_history"][0]["judged_by"]["agent_id"] == "critic"

    async def test_the_trail_is_append_only(self, storage, embedder, config):
        topic = await _undescribed_topic(storage, embedder, config)
        for text in (ISSUE_53, "A second.", "A third."):
            await _enrich(storage, embedder, topic.id, text)

        after = await storage.get_node(topic.id)
        assert [entry["replaced"] for entry in after.metadata["description_history"]] == [
            ISSUE_53,
            "A second.",
        ]

    async def test_a_tag_described_at_ingest_keeps_that_wording_too(
        self, storage, embedder, config
    ):
        """The ingest rule writes the first description now, so the first
        enrichment of a tag is already a replacement, and the trail is where
        the wording the creator chose survives."""
        await _ingest(storage, embedder, config, "A document.", tags=["issue-53"])
        topic = await _named(storage, "issue-53")

        await _enrich(storage, embedder, topic.id, ISSUE_53)

        after = await storage.get_node(topic.id)
        assert [entry["replaced"] for entry in after.metadata["description_history"]] == [
            "Everything filed under issue-53."
        ]

    async def test_the_judge_who_wrote_the_name_is_unchanged(self, storage, embedder, config):
        """`judged_by` records who wrote the wording, and the wording is the name."""
        await _ingest(storage, embedder, config, "A document.", tags=["issue-53"])
        topic = await _named(storage, "issue-53")

        await _enrich(storage, embedder, topic.id, ISSUE_53, judge=EDITOR)

        after = await storage.get_node(topic.id)
        assert after.judged_by == topic.judged_by


class TestWhatADescribedTopicIsEmbeddedOn:
    async def test_a_described_tag_re_embeds_on_content_and_description(
        self, storage, embedder, config
    ):
        await _ingest(storage, embedder, config, "A document.", tags=["issue-53"])
        topic = await _named(storage, "issue-53")

        await _enrich(storage, embedder, topic.id, ISSUE_53)

        expected = (await embedder.embed([f"issue-53. {ISSUE_53}"]))[0]
        stored = await storage.get_embeddings_for_item(topic.id, model_id=embedder.model_id)
        assert [record.vector for record in stored] == [expected]

    async def test_the_rewrite_leaves_no_second_vector(self, storage, embedder, config):
        """`vector_search` scores every embedding row, so a second record for one
        node would rank it twice and once on wording it no longer carries."""
        await _ingest(storage, embedder, config, "A document.", tags=["issue-53"])
        topic = await _named(storage, "issue-53")

        await _enrich(storage, embedder, topic.id, ISSUE_53)
        await _enrich(storage, embedder, topic.id, "A sharper sentence.")

        stored = await storage.get_embeddings_for_item(topic.id, model_id=embedder.model_id)
        assert len(stored) == 1

    async def test_a_described_statement_topic_embeds_on_content_alone(
        self, storage, embedder, config
    ):
        """A statement topic's content is already the prose a description
        restates, so joining one would only move it away from the same topic
        arriving undescribed at ingest."""
        seg, _ = await tools.segment_text("A document.", storage, embedder, config)
        await tools.store_decomposition(
            document_id=seg["document_id"],
            segments=[
                {
                    "segment_id": seg["segments"][0]["segment_id"],
                    "topics": [
                        {
                            "content": "How the graph consolidates itself",
                            "description": "Covers reflect, merge and archival.",
                        }
                    ],
                }
            ],
            storage=storage,
            embedding_provider=embedder,
            metacontext_id=BASE_METACONTEXT_ID,
            judge=CRITIC,
        )
        topic = await _named(storage, "How the graph consolidates itself")

        assert topic.description == "Covers reflect, merge and archival."
        assert embedding_text(topic) == topic.content
        expected = (await embedder.embed([topic.content]))[0]
        stored = await storage.get_embeddings_for_item(topic.id, model_id=embedder.model_id)
        assert [record.vector for record in stored] == [expected]


class TestADescriptionAtIngest:
    async def test_it_lands_on_the_topic(self, storage, embedder, config):
        seg, _ = await tools.segment_text("A document.", storage, embedder, config)
        await tools.store_decomposition(
            document_id=seg["document_id"],
            segments=[
                {
                    "segment_id": seg["segments"][0]["segment_id"],
                    "topics": [{"content": "Consolidation", "description": "What reflect does."}],
                }
            ],
            storage=storage,
            embedding_provider=embedder,
            metacontext_id=BASE_METACONTEXT_ID,
            judge=CRITIC,
        )

        assert (await _named(storage, "Consolidation")).description == "What reflect does."

    async def test_an_undescribed_topic_reads_back_empty(self, storage, embedder, config):
        seg, _ = await tools.segment_text("A document.", storage, embedder, config)
        await tools.store_decomposition(
            document_id=seg["document_id"],
            segments=[
                {"segment_id": seg["segments"][0]["segment_id"], "topics": ["Consolidation"]}
            ],
            storage=storage,
            embedding_provider=embedder,
            metacontext_id=BASE_METACONTEXT_ID,
            judge=CRITIC,
        )

        assert (await _named(storage, "Consolidation")).description == ""

    @pytest.mark.parametrize("kind", ["facts", "inferences"])
    async def test_it_is_refused_on_a_claim(self, storage, embedder, config, kind):
        """Refused rather than dropped, on `claim_kind`'s grounds: prose written
        into a field that does not exist is a judgment the agent believes it
        made. A claim's wording *is* the claim."""
        seg, _ = await tools.segment_text("A document.", storage, embedder, config)

        with pytest.raises(ValueError) as caught:
            await tools.store_decomposition(
                document_id=seg["document_id"],
                segments=[
                    {
                        "segment_id": seg["segments"][0]["segment_id"],
                        kind: [{"content": "A claim.", "description": "softer wording"}],
                    }
                ],
                storage=storage,
                embedding_provider=embedder,
                metacontext_id=BASE_METACONTEXT_ID,
                judge=CRITIC,
            )

        assert "description was supplied on a" in str(caught.value)


class TestTheOldShapeIsRefusedByName:
    async def test_an_enrichment_sending_new_content_is_told_the_new_key(
        self, storage, embedder, config
    ):
        await _ingest(storage, embedder, config, "A document.", tags=["issue-53"])
        topic = await _named(storage, "issue-53")

        with pytest.raises(ValueError) as caught:
            await tools.apply_reflection(
                storage,
                embedder,
                enrichments=[{"topic_id": topic.id, "new_content": ISSUE_53}],
                judge=EDITOR,
            )

        assert "description" in str(caught.value)
        assert (await storage.get_node(topic.id)).content == "issue-53"


class TestADescribedTopicIsNotNominatedForEver:
    """`_should_enrich` compared material against `content` alone. A short name
    stays short whatever anybody writes beside it, so the ratio could never be
    satisfied and a described topic came back on every reflect, for ever."""

    # Enough material to nominate a bare `issue-53` three times over, and far
    # short of nominating the same name once it carries a sentence.
    MATERIAL = ["a claim about when this was true"]

    def _tag(self, **fields) -> Topic:
        return Topic(
            content="issue-53", source_id=None, extraction_method=TAG_EXTRACTION_METHOD, **fields
        )

    def test_a_described_tag_with_modest_material_is_not_nominated(self):
        assert not _should_enrich(self._tag(description=ISSUE_53), self.MATERIAL, 3.0)

    def test_the_same_tag_undescribed_is_nominated(self):
        assert _should_enrich(self._tag(), self.MATERIAL, 3.0)

    def test_material_that_dwarfs_the_description_still_nominates(self):
        material = ["a claim about when this was true " * 20]

        assert _should_enrich(self._tag(description=ISSUE_53), material, 3.0)

    async def test_a_nomination_carries_the_description_it_would_replace(
        self, storage, embedder, config
    ):
        """The agent cannot tell a first description from an overwrite of prose
        somebody judged, out of a nomination that shows only the name.

        A tag, and a second document under it, because a described topic is
        nominated only when its material moves: the enrichment below is what
        stamps `description_reviewed_at`, and the second ingest is what changes
        something after it.
        """
        await _ingest(storage, embedder, config, "First document.", tags=["issue-53"])
        topic = await _named(storage, "issue-53")
        await _enrich(storage, embedder, topic.id, "Short.")

        await _ingest(
            storage,
            embedder,
            config,
            "Second document.",
            tags=["issue-53"],
            facts=["a later claim"],
        )
        result, _ = await tools.reflect(storage, embedder)

        nominated = {c["topic_id"]: c for c in result["enrichment_candidates"]}
        assert nominated[topic.id]["current_description"] == "Short."


class TestTopicTreePreviewsADescription:
    async def test_a_described_topic_previews_it(self, storage, embedder, config):
        await _ingest(storage, embedder, config, "A document.", tags=["issue-53"])
        topic = await _named(storage, "issue-53")
        await _enrich(storage, embedder, topic.id, ISSUE_53)

        result, _ = await tools.topic_tree(topic.id, storage)

        assert result["topic"]["content_preview"] == "issue-53"
        assert result["topic"]["description_preview"] == ISSUE_53

    async def test_an_undescribed_topic_carries_no_such_key(self, storage, embedder, config):
        """Absent rather than empty, so *undescribed* reads as the absence it is."""
        topic = await _undescribed_topic(storage, embedder, config)

        result, _ = await tools.topic_tree(topic.id, storage)

        assert "description_preview" not in result["topic"]


class TestTheFailureThatStartedIt:
    """Tag a document, enrich the tag, tag another document with the same name.

    Before the change this ended with two topic nodes: the enrichment moved the
    name, so the second ingest found no active topic called `issue-53` and minted
    one, while `find_nodes(tagged_with_topic="issue-53")` returned nothing at all.
    """

    async def test_tagging_after_an_enrichment_mints_no_second_topic_node(
        self, storage, embedder, config
    ):
        await _ingest(storage, embedder, config, "First document.", tags=["issue-53"])
        topic = await _named(storage, "issue-53")
        await _enrich(storage, embedder, topic.id, ISSUE_53)

        await _ingest(storage, embedder, config, "Second document.", tags=["issue-53"])

        tags = await _tag_topics(storage)
        assert [t.id for t in tags] == [topic.id]

    async def test_both_documents_nodes_hang_off_the_one_topic(self, storage, embedder, config):
        await _ingest(storage, embedder, config, "First document.", tags=["issue-53"])
        topic = await _named(storage, "issue-53")
        await _enrich(storage, embedder, topic.id, ISSUE_53)
        await _ingest(storage, embedder, config, "Second document.", tags=["issue-53"])

        result, _ = await tools.find_nodes(storage, tagged_with_topic="issue-53")

        assert len(result["nodes"]) >= 2
