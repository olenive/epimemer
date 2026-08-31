"""The frame is required at ingest, and reflect stops laundering it away.

**The defect this closes is that absence was not neutral.** A node with no
`has_metacontext` edge is read as a claim about the real world — the one place
in this system where silence becomes a positive assertion, against a rule stated
everywhere else (`confidence` omitted is *unrated*, `judged_by` absent is
*unknown*, `claim_kind` omitted is *unjudged*). Measured on 684 real nodes, no
agent had ever named a frame, so every stored claim was an implicit assertion
about reality that nobody had made.

Requiring the field does **not** prevent a wrong frame: a reflexive `the-real`
on a fiction ingest is exactly as wrong as silence was. What it buys is that the
error is findable — it carries a judge and a journal row — and fixable with
`reframe`. That is the whole pitch, and `TestAStatedFrameLeavesAMark` is where
it is tested.

The second half is the leak that would have made the guarantee false on day one:
`apply_reflection` minted untagged Topics out of framed ones, so reflect
converted framed knowledge into base-reality assertions through a side door.
Splits inherit, synthesis inherits or refuses, and topic merge finally gets the
frame gate facts have had since fact dedup.
"""

import pytest

from epimemer.core.types import (
    BASE_METACONTEXT_ID,
    EdgeType,
    EmbeddingRecord,
    JudgeRef,
    NodeEdge,
    Topic,
)
from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.mcp import tools
from epimemer.mcp.config import ServerConfig
from epimemer.pipelines.reflection.review import frames_of

CRITIC = JudgeRef(agent_id="critic", digest="d1")


@pytest.fixture
def embedder():
    return MockEmbeddingProvider(model_id="mock-embed", dimension=8)


@pytest.fixture
def config():
    return ServerConfig(storage_backend="memory", embedding_provider="mock")


async def _topic(storage, embedder, content, *, vector=None, frames=()):
    """A stored Topic, optionally framed and given an exact embedding.

    `vector` is supplied rather than derived where a test needs two topics to
    clear the merge bar: `all_pairs_above_threshold` reads stored embeddings, so
    identical vectors are the only way to be sure the *frame* gate is what
    refused a merge and not the similarity one.
    """
    topic = Topic(content=content, source_id="seg1")
    await storage.store_node(topic)
    vectors = await embedder.embed([content])
    await storage.store_embedding(
        EmbeddingRecord(
            item_id=topic.id,
            model_id=embedder.model_id,
            vector=vector if vector is not None else vectors[0],
        )
    )
    for frame in frames:
        await storage.store_edge(
            NodeEdge(
                src_id=topic.id,
                dst_id=frame,
                type=EdgeType.HAS_METACONTEXT,
            )
        )
    return topic


async def _frames_stated(storage, node_id) -> set[str]:
    """What the node **says**, not what it is read as — no promotion."""
    edges = await storage.get_edges_from(node_id, edge_type=EdgeType.HAS_METACONTEXT)
    return {edge.dst_id for edge in edges}


async def _fiction(storage):
    result, _ = await tools.create_metacontext(
        "The novel's world", storage, description="in-universe"
    )
    return result["metacontext_id"]


async def _ingest(storage, embedder, config, content, *, metacontext_id):
    seg, _ = await tools.segment_text(content, storage, embedder, config)
    stored, _ = await tools.store_decomposition(
        document_id=seg["document_id"],
        segments=[
            {
                "segment_id": seg["segments"][0]["segment_id"],
                "topics": ["a topic"],
                "facts": ["a claim"],
                "inferences": [],
            }
        ],
        storage=storage,
        embedding_provider=embedder,
        metacontext_id=metacontext_id,
        judge=CRITIC,
    )
    return stored


class TestTheFrameIsRequired:
    async def test_the_tool_has_no_default_to_fall_back_on(self, storage, embedder, config):
        """A required keyword, not a defaulted one. The distinction is the whole
        change: a default would answer the question on behalf of an agent who
        never considered it, which is what absence already did."""
        seg, _ = await tools.segment_text("A document.", storage, embedder, config)
        with pytest.raises(TypeError, match="metacontext_id"):
            await tools.store_decomposition(
                document_id=seg["document_id"],
                segments=[],
                storage=storage,
                embedding_provider=embedder,
            )

    async def test_a_blank_frame_is_refused_and_names_the_ordinary_answer(
        self, storage, embedder, config
    ):
        """The refusal has to carry the answer, because `the-real` is not
        discoverable: no tool enumerates metacontexts, and this is one of two
        places the reserved id is ever named to an agent."""
        seg, _ = await tools.segment_text("A document.", storage, embedder, config)
        with pytest.raises(ValueError, match="the-real"):
            await tools.store_decomposition(
                document_id=seg["document_id"],
                segments=[],
                storage=storage,
                embedding_provider=embedder,
                metacontext_id="   ",
            )

    async def test_the_refusal_no_longer_teaches_omitting_it(self, storage, embedder, config):
        """A wrong id used to be answered with *"or omit metacontext_id"*, which
        is now advice to do the impossible on ingest — and, before the
        requirement, advice to make the silent assertion this issue is about."""
        seg, _ = await tools.segment_text("A document.", storage, embedder, config)
        with pytest.raises(ValueError) as caught:
            await tools.store_decomposition(
                document_id=seg["document_id"],
                segments=[],
                storage=storage,
                embedding_provider=embedder,
                metacontext_id="from-another-graph",
            )
        assert "omit metacontext_id" not in str(caught.value)
        assert BASE_METACONTEXT_ID in str(caught.value)

    async def test_a_frame_from_another_graph_is_still_refused(self, storage, embedder, config):
        """Requiring the field does not weaken the existing check — a stated id
        must still resolve here, since a node framed by nothing shares a frame
        with no other node."""
        seg, _ = await tools.segment_text("A document.", storage, embedder, config)
        with pytest.raises(ValueError, match="does not exist"):
            await tools.store_decomposition(
                document_id=seg["document_id"],
                segments=[],
                storage=storage,
                embedding_provider=embedder,
                metacontext_id="mc-from-another-graph",
            )


class TestAStatedFrameLeavesAMark:
    """Why requiring `the-real` is not ceremony.

    Reading is unchanged — `frames_of` reduces a stated base frame and an
    untagged node to the same single-frame set, so no consumer branches on the
    difference. The difference exists for a **reviewer**: one of these is an
    assertion somebody made, and the other is a question nobody was asked.
    """

    async def test_the_real_is_written_as_an_edge(self, storage, embedder, config):
        await _ingest(
            storage,
            embedder,
            config,
            "A real claim.",
            metacontext_id=BASE_METACONTEXT_ID,
        )
        nodes = await storage.query_nodes()
        assert nodes
        for node in nodes:
            assert await _frames_stated(storage, node.id) == {BASE_METACONTEXT_ID}

    async def test_absence_and_a_stated_frame_are_different_answers(
        self, storage, embedder, config
    ):
        """They used to read the same — an untagged node resolved to the base
        frame, so stating it was a record with no consequence. Absence names no
        frame now, so the two differ everywhere, and a node written before the
        rule is *unspoken for* rather than quietly asserted about the real
        world. `epimemer frames declare` is what ends that state."""
        legacy = await _topic(storage, embedder, "written before the rule")
        await _ingest(
            storage,
            embedder,
            config,
            "written after.",
            metacontext_id=BASE_METACONTEXT_ID,
        )
        stated = next(node for node in await storage.query_nodes() if node.id != legacy.id)

        assert await frames_of(legacy.id, storage) == set()
        assert await frames_of(stated.id, storage) == {BASE_METACONTEXT_ID}

    async def test_the_edge_carries_the_judge(self, storage, embedder, config):
        """What makes the error recoverable rather than merely visible: the
        frame is a judgment, so `review(by_agent=…)` can find every claim this
        agent filed into a world."""
        await _ingest(
            storage,
            embedder,
            config,
            "A real claim.",
            metacontext_id=BASE_METACONTEXT_ID,
        )
        node = (await storage.query_nodes())[0]
        edges = await storage.get_edges_from(node.id, edge_type=EdgeType.HAS_METACONTEXT)
        assert [edge.judged_by.agent_id for edge in edges] == ["critic"]


class TestReflectStopsMintingUntaggedNodes:
    """The leak that would have made the requirement false on day one.

    Both paths created a `Topic` with no frame edge out of nodes that had one,
    and `frames_for` promotes that to base reality — reflect converting framed
    knowledge into an assertion about the real world, with no agent involved.
    """

    async def test_a_split_inherits_the_parents_frame(self, storage, embedder):
        fiction = await _fiction(storage)
        parent = await _topic(storage, embedder, "the novel's politics", frames=[fiction])

        await tools.apply_reflection(
            storage,
            embedder,
            splits=[{"topic_id": parent.id, "subtopics": ["the council", "the war"]}],
            judge=CRITIC,
        )

        children = [
            node
            for node in await storage.query_nodes()
            if node.metadata.get("split_from") == parent.id
        ]
        assert len(children) == 2
        for child in children:
            assert await _frames_stated(storage, child.id) == {fiction}

    async def test_a_split_of_an_unspoken_for_topic_invents_no_frame(self, storage, embedder):
        """A subtopic inherits what its parent states, and a parent written
        before the rule states nothing — so the split says nothing either. It
        used to mint an explicit `the-real` here, back when absence resolved to
        that frame and the child was only writing down what was already true.
        Inventing one now would put words in a nobody's mouth; declaring the
        graph is what a person does instead."""
        parent = await _topic(storage, embedder, "European history")

        await tools.apply_reflection(
            storage,
            embedder,
            splits=[{"topic_id": parent.id, "subtopics": ["the Congress"]}],
            judge=CRITIC,
        )

        child = next(
            node
            for node in await storage.query_nodes()
            if node.metadata.get("split_from") == parent.id
        )
        assert await _frames_stated(storage, child.id) == set()
        assert await _frames_stated(storage, parent.id) == set()

    async def test_a_synthesised_parent_inherits_the_shared_frame(self, storage, embedder):
        fiction = await _fiction(storage)
        a = await _topic(storage, embedder, "the council", frames=[fiction])
        b = await _topic(storage, embedder, "the war", frames=[fiction])

        result, _ = await tools.apply_reflection(
            storage,
            embedder,
            parents=[{"children_ids": [a.id, b.id], "content": "the novel's politics"}],
            judge=CRITIC,
        )

        assert result["parents_created"] == 1
        parent = next(
            node for node in await storage.query_nodes() if node.metadata.get("synthesized_from")
        )
        assert await _frames_stated(storage, parent.id) == {fiction}

    async def test_a_synthesis_across_frames_is_refused(self, storage, embedder):
        """Not a union. A topic drawn from a fiction claim and a real one would
        assert in both worlds, which `fact_dedup` calls the worst outcome
        available — this is that gate one tier up."""
        fiction = await _fiction(storage)
        invented = await _topic(storage, embedder, "the council", frames=[fiction])
        real = await _topic(storage, embedder, "the Congress", frames=[BASE_METACONTEXT_ID])

        result, _ = await tools.apply_reflection(
            storage,
            embedder,
            parents=[
                {
                    "children_ids": [invented.id, real.id],
                    "content": "assemblies",
                }
            ],
            judge=CRITIC,
        )

        assert result["parents_created"] == 0
        assert len(result["parents_refused"]) == 1
        assert result["parents_refused"][0]["children_ids"] == [invented.id, real.id]
        assert "frames" in result["parents_refused"][0]["reason"]
        assert not [
            node for node in await storage.query_nodes() if node.metadata.get("synthesized_from")
        ]

    async def test_an_unspoken_for_child_and_a_stated_one_are_refused(self, storage, embedder):
        """This used to synthesise, because absence resolved to the base frame
        and the two compared equal. It is refused now, and that is the cost the
        declaration sweep exists to pay: combining them would put a claim nobody
        framed into a frame somebody named."""
        legacy = await _topic(storage, embedder, "Vienna")
        stated = await _topic(storage, embedder, "Salzburg", frames=[BASE_METACONTEXT_ID])

        result, _ = await tools.apply_reflection(
            storage,
            embedder,
            parents=[
                {
                    "children_ids": [legacy.id, stated.id],
                    "content": "Austrian cities",
                }
            ],
            judge=CRITIC,
        )

        assert result["parents_created"] == 0
        assert len(result["parents_refused"]) == 1

    async def test_two_unspoken_for_children_still_synthesise(self, storage, embedder):
        """Neither says anything, so the sets are equal and the parent inherits
        nothing. `same_frame` answers the *overlap* question the other way for
        the same pair — both are right, and the difference is only visible on a
        graph nobody has declared."""
        a = await _topic(storage, embedder, "Vienna")
        b = await _topic(storage, embedder, "Salzburg")

        result, _ = await tools.apply_reflection(
            storage,
            embedder,
            parents=[{"children_ids": [a.id, b.id], "content": "Austrian cities"}],
            judge=CRITIC,
        )

        assert result["parents_created"] == 1
        parent = next(
            node for node in await storage.query_nodes() if node.metadata.get("synthesized_from")
        )
        assert await _frames_stated(storage, parent.id) == set()


class TestTopicMergeGetsTheGateFactsAlreadyHad:
    """`merge_nodes` migrates every source's edges onto the survivor,
    `has_metacontext` among them — so a cross-frame topic merge left one topic
    asserted in two worlds. The equality check has lived in `fact_dedup` since
    fact dedup and covered facts alone.
    """

    async def test_a_cross_frame_merge_is_refused(self, storage, embedder):
        fiction = await _fiction(storage)
        twin = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        a = await _topic(storage, embedder, "the council", vector=twin, frames=[fiction])
        b = await _topic(
            storage,
            embedder,
            "the councils",
            vector=twin,
            frames=[BASE_METACONTEXT_ID],
        )

        result, _ = await tools.apply_reflection(
            storage,
            embedder,
            merges=[{"source_ids": [a.id, b.id], "content": "councils"}],
            judge=CRITIC,
        )

        assert result["topics_merged"] == 0
        assert result["merges_rejected"] == 0, "the similarity bar was cleared"
        assert len(result["topic_merges_refused"]) == 1
        assert result["topic_merges_refused"][0]["source_ids"] == [a.id, b.id]
        assert "frames" in result["topic_merges_refused"][0]["reason"]

    async def test_a_refused_merge_retires_nothing(self, storage, embedder):
        fiction = await _fiction(storage)
        twin = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        a = await _topic(storage, embedder, "the council", vector=twin, frames=[fiction])
        b = await _topic(
            storage,
            embedder,
            "the councils",
            vector=twin,
            frames=[BASE_METACONTEXT_ID],
        )

        await tools.apply_reflection(
            storage,
            embedder,
            merges=[{"source_ids": [a.id, b.id], "content": "councils"}],
            judge=CRITIC,
        )

        assert (await storage.get_node(a.id)).status.value == "active"
        assert (await storage.get_node(b.id)).status.value == "active"

    async def test_a_same_frame_merge_still_applies(self, storage, embedder):
        fiction = await _fiction(storage)
        twin = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        a = await _topic(storage, embedder, "the council", vector=twin, frames=[fiction])
        b = await _topic(storage, embedder, "the councils", vector=twin, frames=[fiction])

        result, _ = await tools.apply_reflection(
            storage,
            embedder,
            merges=[{"source_ids": [a.id, b.id], "content": "councils"}],
            judge=CRITIC,
        )

        assert result["topics_merged"] == 1
        assert result["topic_merges_refused"] == []


class TestTheReadSideStaysOptional:
    async def test_search_without_a_frame_answers_across_all_of_them(
        self, storage, embedder, config
    ):
        """Deliberately asymmetric. An omitted filter on the read side is a
        coherent question — *anything about this, wherever it was claimed* —
        while an omitted frame on the write side was an unstated assumption.
        Requiring it here would make cross-frame search impossible."""
        fiction = await _fiction(storage)
        await _ingest(
            storage,
            embedder,
            config,
            "The council met in winter.",
            metacontext_id=fiction,
        )

        found, _ = await tools.search(
            query="council",
            storage=storage,
            embedding_provider=embedder,
            k=5,
        )

        assert found["nodes"]


class TestTheLegacyPopulationIsLeftAlone:
    async def test_an_ingest_does_not_backfill_older_nodes(self, storage, embedder, config):
        """Writing `the-real` onto nodes nobody asked would manufacture exactly
        the deliberate-looking assertions this issue exists to end — the confidence prior's
        treatment of the legacy `0.5` confidences, which were sized and dated
        rather than rewritten."""
        legacy = await _topic(storage, embedder, "written before the rule")

        await _ingest(
            storage,
            embedder,
            config,
            "written after.",
            metacontext_id=BASE_METACONTEXT_ID,
        )

        assert await _frames_stated(storage, legacy.id) == set()

    async def test_nothing_dates_the_rule_into_the_graph(self):
        """There is no before-and-after to date. Absence meant base reality
        until the promotion went, and it means nothing now — in every graph, of
        every node, whenever it was written. The boundary that needed recording
        was an artefact of the promotion rule, and went with it."""
        assert not hasattr(tools, "FRAME_REQUIRED_SINCE")
        assert not hasattr(tools, "ensure_base_metacontext")


class TestTheIngestRowNamesTheFrame:
    """What turns the recoverability argument into a supported read.

    Requiring the frame was justified by *a wrong one is findable and fixable*.
    Until the row carried it, finding meant walking from an agent's ingest rows
    out to the edges of every node they named — two hops and no query. One
    frame per call means one value per row.
    """

    async def test_the_row_carries_it(self, storage, embedder, config):
        from epimemer.core.types import DecisionKind

        fiction = await _fiction(storage)
        await _ingest(
            storage,
            embedder,
            config,
            "The council met.",
            metacontext_id=fiction,
        )

        [row] = await storage.query_decisions(kinds=[DecisionKind.INGEST])
        assert row.frame == fiction

    async def test_a_declaration_sweep_carries_it_too(self, storage, embedder):
        from epimemer.core.types import DecisionKind
        from epimemer.pipelines.frames import declare_frames

        await _topic(storage, embedder, "written before the rule")

        await declare_frames(storage, frame=BASE_METACONTEXT_ID, judge=CRITIC)

        [row] = await storage.query_decisions(kinds=[DecisionKind.FRAME_DECLARATION])
        assert row.frame == BASE_METACONTEXT_ID

    async def test_a_decision_that_names_no_frame_leaves_it_blank(self, storage, embedder):
        """Most kinds do not apply one, and a blank is the honest answer — not
        a default, which is the mistake this whole issue is about."""
        from epimemer.core.types import DecisionKind

        parent = await _topic(storage, embedder, "European history", frames=[BASE_METACONTEXT_ID])
        await tools.apply_reflection(
            storage,
            embedder,
            splits=[{"topic_id": parent.id, "subtopics": ["the Congress"]}],
            judge=CRITIC,
        )

        [row] = await storage.query_decisions(kinds=[DecisionKind.SPLIT])
        assert row.frame is None


class TestAMergeRestatesTheFrame:
    """Merging is not coining, one layer up from `describe_relation`.

    A survivor's content is *synthesised*, so no source's framing was ever made
    about that wording. Migrating the edge would answer *which world is this
    about* on the merging agent's behalf while crediting somebody else.
    """

    async def test_the_survivor_carries_the_merging_judge(self, storage, embedder):
        twin = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        ingester = JudgeRef(agent_id="ingester", digest="d9")
        a = await _topic(
            storage,
            embedder,
            "the council",
            vector=twin,
            frames=[BASE_METACONTEXT_ID],
        )
        b = await _topic(
            storage,
            embedder,
            "the councils",
            vector=twin,
            frames=[BASE_METACONTEXT_ID],
        )
        for node in (a, b):
            for edge in await storage.get_edges_from(node.id, edge_type=EdgeType.HAS_METACONTEXT):
                await storage.store_edge(edge.model_copy(update={"judged_by": ingester}))

        await tools.apply_reflection(
            storage,
            embedder,
            merges=[{"source_ids": [a.id, b.id], "content": "councils"}],
            judge=CRITIC,
        )

        survivor = next(
            node for node in await storage.query_nodes() if node.metadata.get("merged_from")
        )
        edges = await storage.get_edges_from(survivor.id, edge_type=EdgeType.HAS_METACONTEXT)
        assert [(e.dst_id, e.judged_by.agent_id) for e in edges] == [
            (BASE_METACONTEXT_ID, "critic")
        ]

    async def test_the_sources_keep_their_own(self, storage, embedder):
        """A merge does not move the frame, so the retired sources still say
        which world they were about — which is what a reversal restores to."""
        twin = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        a = await _topic(
            storage,
            embedder,
            "the council",
            vector=twin,
            frames=[BASE_METACONTEXT_ID],
        )
        b = await _topic(
            storage,
            embedder,
            "the councils",
            vector=twin,
            frames=[BASE_METACONTEXT_ID],
        )

        await tools.apply_reflection(
            storage,
            embedder,
            merges=[{"source_ids": [a.id, b.id], "content": "councils"}],
            judge=CRITIC,
        )

        for node in (a, b):
            assert await _frames_stated(storage, node.id) == {BASE_METACONTEXT_ID}
