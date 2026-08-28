"""Declaring the frame of a graph written before frames were required.

**A user's statement, not a migration.** Nothing here derives the answer from
the content: somebody says that the claims in this graph were always about one
world, and owns having said so. That is why it is a CLI command rather than a
tool — an agent asserting that about its own past writes would be marking its
own homework, the same reasoning that keeps judge approval out of agent reach.

It exists because absence stopped meaning anything. A node carrying no frame
shares a frame with nothing: never compared, never merged, absent from every
scoped search. Before the requirement that state was the whole graph, and
`graph_stats.nodes_without_frame` is how a user watches it reach zero.
"""

import pytest

from epimemer.core.types import (
    BASE_METACONTEXT_ID,
    DecisionKind,
    EdgeType,
    EmbeddingRecord,
    Fact,
    JudgeRef,
    Metacontext,
    NodeEdge,
    QUARANTINE_METACONTEXT_ID,
    Topic,
)
from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.mcp import tools
from epimemer.mcp.config import ServerConfig
from epimemer.pipelines.frames import declare_frames


DECLARER = JudgeRef(agent_id="the-user", digest="d1")


@pytest.fixture
def embedder():
    return MockEmbeddingProvider(model_id="mock-embed", dimension=8)


@pytest.fixture
def config():
    return ServerConfig(storage_backend="memory", embedding_provider="mock")


async def _node(storage, node, *, frames=()):
    await storage.store_node(node)
    for frame in frames:
        await storage.store_edge(NodeEdge(
            src_id=node.id, dst_id=frame, type=EdgeType.HAS_METACONTEXT,
        ))
    return node


async def _frames_of(storage, node_id) -> set[str]:
    edges = await storage.get_edges_from(
        node_id, edge_type=EdgeType.HAS_METACONTEXT
    )
    return {edge.dst_id for edge in edges}


class TestTheSweepStampsWhatNobodySpokeFor:
    async def test_an_unframed_node_is_declared(self, storage):
        legacy = await _node(storage, Topic(content="Vienna", source_id="s1"))

        result = await declare_frames(
            storage, frame=BASE_METACONTEXT_ID, judge=DECLARER
        )

        assert result.declared == 1
        assert await _frames_of(storage, legacy.id) == {BASE_METACONTEXT_ID}

    async def test_a_framed_node_is_left_alone(self, storage):
        """The predicate is *no frames at all*, never *not this frame*. A node
        already framed as fiction must not acquire a second frame from a sweep
        aimed at the ones nobody spoke for — that would assert it in two
        worlds, which is the outcome every gate in this system refuses."""
        fiction, _ = await tools.create_metacontext("The novel", storage)
        framed = await _node(
            storage, Topic(content="the council", source_id="s1"),
            frames=[fiction["metacontext_id"]],
        )

        result = await declare_frames(
            storage, frame=BASE_METACONTEXT_ID, judge=DECLARER
        )

        assert result.declared == 0
        assert result.already_framed == 1
        assert await _frames_of(storage, framed.id) == {
            fiction["metacontext_id"]
        }

    async def test_a_rerun_declares_nothing(self, storage):
        """Idempotent, and that is what lets the command be run without
        checking first — and what makes it naturally dead once no unframed
        node is left, rather than needing deprecation machinery."""
        await _node(storage, Topic(content="Vienna", source_id="s1"))

        await declare_frames(storage, frame=BASE_METACONTEXT_ID, judge=DECLARER)
        again = await declare_frames(
            storage, frame=BASE_METACONTEXT_ID, judge=DECLARER
        )

        assert again.declared == 0
        assert again.already_framed == 1

    async def test_a_frame_that_does_not_exist_is_refused(self, storage):
        """A sweep that minted the frame it was about to stamp would do the one
        thing every other path refuses — an edge pointing at a metacontext
        nobody described — in bulk, on the nodes least able to survive it. Found
        by running this against a real graph that had no `the-real` row."""
        legacy = await _node(storage, Topic(content="Vienna", source_id="s1"))

        with pytest.raises(ValueError, match="no metacontext 'nowhere'"):
            await declare_frames(storage, frame="nowhere", judge=DECLARER)

        assert await _frames_of(storage, legacy.id) == set()

    async def test_an_empty_graph_is_not_an_error(self, storage):
        result = await declare_frames(storage, frame=BASE_METACONTEXT_ID)
        assert result.declared == 0
        assert result.already_framed == 0

    async def test_the_edges_carry_the_declaring_judge(self, storage):
        """Who said so is the entire content of a declaration. Without it the
        stamp is indistinguishable from one the system invented, which is the
        ambiguity the frame requirement exists to end."""
        legacy = await _node(storage, Fact(content="a claim", source_id="s1"))

        await declare_frames(storage, frame=BASE_METACONTEXT_ID, judge=DECLARER)

        edges = await storage.get_edges_from(
            legacy.id, edge_type=EdgeType.HAS_METACONTEXT
        )
        assert [edge.judged_by.agent_id for edge in edges] == ["the-user"]


class TestOneRowForTheSweep:
    async def test_the_sweep_journals_once_naming_what_it_stamped(self, storage):
        """Archival-sweep granularity: one act of judgment applied to whatever
        it found, not N independent verdicts. A row per node would make a
        declaration the journal's dominant writer and say nothing extra."""
        a = await _node(storage, Topic(content="Vienna", source_id="s1"))
        b = await _node(storage, Topic(content="Salzburg", source_id="s1"))

        await declare_frames(storage, frame=BASE_METACONTEXT_ID, judge=DECLARER)

        rows = await storage.query_decisions(
            kinds=[DecisionKind.FRAME_DECLARATION]
        )
        assert len(rows) == 1
        assert set(rows[0].subject_ids) == {a.id, b.id}
        assert rows[0].judged_by.agent_id == "the-user"

    async def test_a_sweep_that_stamped_nothing_journals_nothing(self, storage):
        """A row with no subjects is a decision about nothing — the rule
        `store_decomposition` already follows for a call that stored nothing."""
        await declare_frames(storage, frame=BASE_METACONTEXT_ID, judge=DECLARER)

        assert await storage.query_decisions(
            kinds=[DecisionKind.FRAME_DECLARATION]
        ) == []


class TestTheQuarantineFrameIsWriteOnlyForTheSweep:
    async def test_the_sweep_may_stamp_it(self, storage):
        """For a graph nobody can vouch for: the nodes are marked as exactly
        that, rather than left carrying no frame — which would leave them
        isolated from everything, including each other."""
        legacy = await _node(storage, Topic(content="something", source_id="s1"))
        await storage.store_metacontext(Metacontext(
            id=QUARANTINE_METACONTEXT_ID,
            content="Unvouched",
            description="Nobody has vouched for these.",
        ))

        await declare_frames(
            storage, frame=QUARANTINE_METACONTEXT_ID, judge=DECLARER
        )

        assert await _frames_of(storage, legacy.id) == {
            QUARANTINE_METACONTEXT_ID
        }

    async def test_an_agent_may_not_ingest_into_it(
        self, storage, embedder, config
    ):
        """A frame an agent can assert into stops meaning *nobody vouched for
        this* and becomes untagged again under a new name."""
        await tools.create_metacontext(
            "unvouched", storage, description="quarantine"
        )
        seg, _ = await tools.segment_text("A doc.", storage, embedder, config)

        with pytest.raises(ValueError, match="not a frame anything may be"):
            await tools.store_decomposition(
                document_id=seg["document_id"],
                segments=[],
                storage=storage,
                embedding_provider=embedder,
                metacontext_id=QUARANTINE_METACONTEXT_ID,
            )

    async def test_searching_for_it_is_allowed(self, storage):
        """Asking what nobody has vouched for is a reasonable question — the
        rule is about assertion, not about looking. So the refusal is on the
        write side only, which is why `require_metacontext` takes `writing`."""
        await storage.store_metacontext(Metacontext(
            id=QUARANTINE_METACONTEXT_ID,
            content="Unvouched",
            description="Nobody has vouched for these.",
        ))

        await tools.require_metacontext(QUARANTINE_METACONTEXT_ID, storage)

        with pytest.raises(ValueError, match="not a frame anything may be"):
            await tools.require_metacontext(
                QUARANTINE_METACONTEXT_ID, storage, writing=True
            )


class TestGraphStatsIsTheCompletenessCheck:
    async def test_it_counts_what_names_no_frame(self, storage):
        await _node(storage, Topic(content="Vienna", source_id="s1"))
        await _node(storage, Topic(content="Salzburg", source_id="s1"))

        stats, _ = await tools.graph_stats(storage, default_reflect_threshold=5)

        assert stats["nodes_without_frame"] == 2

    async def test_it_reaches_zero_when_the_sweep_has_run(self, storage):
        """The check the migration is finished, and the reason this number is
        on `graph_stats` rather than printed once by the command: a user has to
        be able to ask again later."""
        await _node(storage, Topic(content="Vienna", source_id="s1"))

        await declare_frames(storage, frame=BASE_METACONTEXT_ID, judge=DECLARER)
        stats, _ = await tools.graph_stats(storage, default_reflect_threshold=5)

        assert stats["nodes_without_frame"] == 0

    async def test_a_framed_node_never_counts(self, storage):
        fiction, _ = await tools.create_metacontext("The novel", storage)
        await _node(
            storage, Topic(content="the council", source_id="s1"),
            frames=[fiction["metacontext_id"]],
        )

        stats, _ = await tools.graph_stats(storage, default_reflect_threshold=5)

        assert stats["nodes_without_frame"] == 0
