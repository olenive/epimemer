"""Putting back a tag's name that an old enrichment overwrote.

The state under repair is real, found on one graph: five active topic nodes
stamped `agent:tag:enriched`, a sentence where the tag's name should be,
`metadata.enriched_from` pointing at a `CORRECTED` node holding the bare name,
and every `tagged_with_topic` edge on the enriched node.

Enrichment cannot reach a name any more, so nothing creates this state. These
tests build it directly, which is the honest way to test a repair: reproducing
it through the tool would need the defect back.
"""

import pytest

from epimemer.core.types import (
    DecisionKind,
    EdgeType,
    Fact,
    JudgeRef,
    NodeEdge,
    NodeStatus,
    NodeType,
    Topic,
)
from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.mcp import tools
from epimemer.pipelines.embedding_text import embedding_text
from epimemer.pipelines.metacontexts import TAG_EXTRACTION_METHOD, created_from_tag
from epimemer.pipelines.name_resolution import resolve_name
from epimemer.pipelines.reflection.tag_name_repair import (
    ENRICHED_TAG_EXTRACTION_METHOD,
    displaced_tag_names,
    repaired,
)
from epimemer.pipelines.reflection.topic_enrichment import reembedded

CRITIC = JudgeRef(agent_id="critic", digest="d1")
USER = JudgeRef(agent_id="oleg", digest="d9")

SENTENCE = "Validity intervals, the Saint Petersburg Problem: when a claim was true, per source"


@pytest.fixture
def embedder():
    return MockEmbeddingProvider(model_id="mock-embed", dimension=8)


async def _damaged(storage, embedder, *, name="issue-53", tagged=2) -> Topic:
    """One tag whose name an enrichment overwrote, holding all of its edges.

    The original stays `CORRECTED` with a `superseded_by` edge onto the enriched
    node, which is exactly what `supersede_node` wrote at the time.
    """
    original = Topic(
        content=name,
        source_id=None,
        extraction_method=TAG_EXTRACTION_METHOD,
        status=NodeStatus.CORRECTED,
        judged_by=CRITIC,
    )
    enriched = Topic(
        content=SENTENCE,
        source_id=None,
        extraction_method=ENRICHED_TAG_EXTRACTION_METHOD,
        judged_by=CRITIC,
        metadata={"enriched_from": original.id},
    )
    await storage.store_node(original)
    await storage.store_node(enriched)
    await storage.store_edge(
        NodeEdge(src_id=original.id, dst_id=enriched.id, type=EdgeType.SUPERSEDED_BY)
    )
    await storage.store_embedding(await reembedded(enriched, storage, embedder))

    for index in range(tagged):
        fact = Fact(content=f"a claim number {index}", source_id="seg-1")
        await storage.store_node(fact)
        await storage.store_edge(
            NodeEdge(src_id=fact.id, dst_id=enriched.id, type=EdgeType.TAGGED_WITH_TOPIC)
        )
    return enriched


async def _repair_all(storage, embedder, *, judge=USER) -> list[str]:
    """What the CLI does per confirmed candidate, without the prompt."""
    names = []
    for entry in await displaced_tag_names(storage):
        topic = await storage.get_node(entry.topic_id)
        fixed = repaired(topic, entry, judge=judge)
        await storage.store_node(fixed)
        await storage.store_embedding(await reembedded(fixed, storage, embedder))
        await tools.journal(storage, DecisionKind.NAME_RESTORATION, [fixed.id], judge=judge)
        names.append(entry.name)
    return names


class TestFindingWhatToRepair:
    async def test_it_finds_the_enriched_tag_and_the_name_to_restore(self, storage, embedder):
        enriched = await _damaged(storage, embedder, tagged=3)

        found = await displaced_tag_names(storage)

        assert [entry.topic_id for entry in found] == [enriched.id]
        assert found[0].name == "issue-53"
        assert found[0].displaced_by == SENTENCE
        assert found[0].tagged_nodes == 3

    async def test_an_ordinary_tag_is_not_a_candidate(self, storage, embedder):
        await storage.store_node(
            Topic(content="issue-61", source_id=None, extraction_method=TAG_EXTRACTION_METHOD)
        )

        assert await displaced_tag_names(storage) == []

    async def test_an_enriched_node_with_no_original_left_is_skipped(self, storage, embedder):
        """There is nowhere else the name survives, and inventing one would put a
        name nobody wrote onto edges somebody did."""
        await storage.store_node(
            Topic(
                content=SENTENCE,
                source_id=None,
                extraction_method=ENRICHED_TAG_EXTRACTION_METHOD,
                metadata={"enriched_from": "a-node-that-is-gone"},
            )
        )

        assert await displaced_tag_names(storage) == []

    async def test_an_enriched_node_with_no_pointer_is_skipped(self, storage, embedder):
        await storage.store_node(
            Topic(
                content=SENTENCE,
                source_id=None,
                extraction_method=ENRICHED_TAG_EXTRACTION_METHOD,
            )
        )

        assert await displaced_tag_names(storage) == []


class TestTheRepairIsInPlace:
    async def test_the_name_comes_back_and_the_sentence_becomes_the_description(
        self, storage, embedder
    ):
        enriched = await _damaged(storage, embedder)

        await _repair_all(storage, embedder)

        after = await storage.get_node(enriched.id)
        assert after.content == "issue-53"
        assert after.description == SENTENCE

    async def test_the_node_keeps_its_id_and_every_edge(self, storage, embedder):
        enriched = await _damaged(storage, embedder, tagged=4)
        before = await storage.get_edges_to(enriched.id, edge_type=EdgeType.TAGGED_WITH_TOPIC)

        await _repair_all(storage, embedder)

        after = await storage.get_edges_to(enriched.id, edge_type=EdgeType.TAGGED_WITH_TOPIC)
        assert {edge.id for edge in after} == {edge.id for edge in before}
        assert len(after) == 4

    async def test_it_is_a_tag_again(self, storage, embedder):
        enriched = await _damaged(storage, embedder)

        await _repair_all(storage, embedder)

        after = await storage.get_node(enriched.id)
        assert after.extraction_method == TAG_EXTRACTION_METHOD
        assert created_from_tag(after)

    async def test_the_trail_says_what_it_replaced(self, storage, embedder):
        enriched = await _damaged(storage, embedder)
        original_id = enriched.metadata["enriched_from"]

        await _repair_all(storage, embedder)

        trail = (await storage.get_node(enriched.id)).metadata["name_restored"]
        assert trail[0]["from"] == SENTENCE
        assert trail[0]["original_id"] == original_id
        assert trail[0]["judged_by"]["agent_id"] == "oleg"

    async def test_the_original_is_left_exactly_as_it_was(self, storage, embedder):
        enriched = await _damaged(storage, embedder)
        original_id = enriched.metadata["enriched_from"]

        await _repair_all(storage, embedder)

        original = await storage.get_node(original_id)
        assert original.status is NodeStatus.CORRECTED
        assert original.content == "issue-53"

    async def test_it_re_embeds_on_the_name_and_the_description(self, storage, embedder):
        enriched = await _damaged(storage, embedder)

        await _repair_all(storage, embedder)

        after = await storage.get_node(enriched.id)
        assert embedding_text(after) == f"issue-53. {SENTENCE}"
        expected = (await embedder.embed([embedding_text(after)]))[0]
        stored = await storage.get_embeddings_for_item(enriched.id, model_id=embedder.model_id)
        assert [record.vector for record in stored] == [expected]

    async def test_it_journals_a_name_restoration(self, storage, embedder):
        enriched = await _damaged(storage, embedder)

        await _repair_all(storage, embedder)

        rows = await storage.query_decisions(kinds=[DecisionKind.NAME_RESTORATION])
        assert [row.subject_ids for row in rows] == [[enriched.id]]

    async def test_it_journals_no_enrichment(self, storage, embedder):
        """`ENRICHMENT` is the opposite act: it adds what a topic did not say,
        and this takes back a name the topic never stopped needing."""
        await _damaged(storage, embedder)

        await _repair_all(storage, embedder)

        assert await storage.query_decisions(kinds=[DecisionKind.ENRICHMENT]) == []


class TestTheNameResolvesToTheRepairedNode:
    async def test_resolve_name_lands_on_the_node_holding_the_edges(self, storage, embedder):
        enriched = await _damaged(storage, embedder)

        await _repair_all(storage, embedder)

        found = await resolve_name("issue-53", storage)
        assert found.id == enriched.id

    async def test_find_nodes_returns_the_tagged_nodes(self, storage, embedder):
        await _damaged(storage, embedder, tagged=3)

        await _repair_all(storage, embedder)

        result, _ = await tools.find_nodes(storage, tagged_with_topic="issue-53")
        assert len(result["nodes"]) == 3

    async def test_before_the_repair_the_name_lands_on_a_node_called_something_else(
        self, storage, embedder
    ):
        """What is left of the damage once name resolution follows a retirement.

        Following `superseded_by` forward already gets a reader from the name to
        the node holding the edges, so `find_nodes` answers either way. What it
        cannot fix is that the node it lands on is not called `issue-53`: the
        name exists only on a retired husk, so `topic_tree`, `search` and the
        panel all show a sentence where a reader expects a tag. That is what the
        repair ends, and it is why the repair is worth running on a graph where
        nothing looks broken.
        """
        await _damaged(storage, embedder, tagged=3)

        result, _ = await tools.find_nodes(storage, tagged_with_topic="issue-53")
        found = await resolve_name("issue-53", storage)

        assert len(result["nodes"]) == 3
        assert found.content == SENTENCE


class TestTheRepairIsIdempotent:
    async def test_a_rerun_finds_nothing(self, storage, embedder):
        await _damaged(storage, embedder)

        assert await _repair_all(storage, embedder) == ["issue-53"]
        assert await displaced_tag_names(storage) == []
        assert await _repair_all(storage, embedder) == []

    async def test_a_rerun_writes_no_second_trail_entry(self, storage, embedder):
        enriched = await _damaged(storage, embedder)
        await _repair_all(storage, embedder)

        await _repair_all(storage, embedder)

        trail = (await storage.get_node(enriched.id)).metadata["name_restored"]
        assert len(trail) == 1

    async def test_a_rerun_journals_nothing_further(self, storage, embedder):
        await _damaged(storage, embedder)
        await _repair_all(storage, embedder)

        await _repair_all(storage, embedder)

        rows = await storage.query_decisions(kinds=[DecisionKind.NAME_RESTORATION])
        assert len(rows) == 1


class TestSeveralAtOnce:
    async def test_every_damaged_tag_is_found_and_repaired(self, storage, embedder):
        """Five is what the real graph holds."""
        names = [f"issue-{n}" for n in (16, 52, 53, 61, 62)]
        for name in names:
            await _damaged(storage, embedder, name=name)

        assert sorted(await _repair_all(storage, embedder)) == sorted(names)

        restored = [
            node.content
            for node in await storage.query_nodes(node_type=NodeType.TOPIC)
            if node.status is NodeStatus.ACTIVE
        ]
        assert sorted(restored) == sorted(names)


class TestTheRepairedNodeIsPure:
    """`repaired` is the whole of what changes, and it is a function of a node."""

    def test_it_moves_no_id_no_status_and_no_judge(self, storage):
        from epimemer.pipelines.reflection.tag_name_repair import DisplacedName

        topic = Topic(
            content=SENTENCE,
            source_id=None,
            extraction_method=ENRICHED_TAG_EXTRACTION_METHOD,
            judged_by=CRITIC,
            metadata={"enriched_from": "orig"},
        )
        entry = DisplacedName(
            topic_id=topic.id,
            name="issue-53",
            displaced_by=SENTENCE,
            original_id="orig",
            tagged_nodes=7,
        )

        fixed = repaired(topic, entry, judge=USER)

        assert fixed.id == topic.id
        assert fixed.status is topic.status
        assert fixed.judged_by == CRITIC
        assert fixed.metadata["enriched_from"] == "orig"
