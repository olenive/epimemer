"""One tag, however it is spelled, and however its node was retired.

`_tag_topic` resolves a tag at ingest and `_resolve_hub_id` resolves one for
`find_nodes`. Both matched content exactly, against active nodes only, so a
separator or a retirement made two hubs out of one tag and neither path said so.

`dev-docs/TAG_IDENTITY.md` has the measurement that rules out fixing this with
the similarity bar: `claim-kind` and `claim_kind` score 0.9196 while
`dev-session-2026-09-05` and `dev-session-2026-09-06` score 0.9935.
"""

import pytest

from epimemer.core.types import (
    BASE_METACONTEXT_ID,
    EdgeType,
    EmbeddingRecord,
    JudgeRef,
    NodeEdge,
    NodeStatus,
    NodeType,
    Topic,
)
from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.mcp import tools
from epimemer.mcp.config import ServerConfig
from epimemer.pipelines.frames import TAG_EXTRACTION_METHOD, is_tag_topic

CRITIC = JudgeRef(agent_id="critic", digest="d1")


@pytest.fixture
def embedder():
    return MockEmbeddingProvider(model_id="mock-embed", dimension=8)


@pytest.fixture
def config():
    return ServerConfig(storage_backend="memory", embedding_provider="mock")


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
        judge=CRITIC,
    )
    return stored


async def _tag_topics(storage) -> list[Topic]:
    nodes = await storage.query_nodes(node_type=NodeType.TOPIC)
    return [n for n in nodes if isinstance(n, Topic) and is_tag_topic(n)]


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
        """An extracted topic is prose, and must not answer to a normalised tag name."""
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

    async def test_a_statement_source_is_still_held_to_the_bar(self, storage, embedder, config):
        """The exemption is for tags. One statement among the sources withdraws it."""
        a = await self._tag_with_vector(storage, embedder, "claim-kind", [1.0] + [0.0] * 7)
        b = Topic(content="claim_kind", source_id="seg1", extraction_method="agent")
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
