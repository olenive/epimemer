"""Who decided this, recorded where the decision landed (REVIEW_MODE.md §3).

Step 2 gave an agent an identity; this is what makes a decision carry it. The
tests are grouped by **carrier** rather than by tool, because that is where the
mistakes live: a judge that reaches seven writers and not the eighth leaves a
hole nobody can see from the response, and one stamped on the wrong record —
the reviewed decision rather than the review — is worse than none.

Both backends, because a nullable field that one store keeps and the other drops
is exactly the divergence `tests/conftest.py` exists for, and this one would be
invisible until somebody asked *who judged this* months later.

**A blank judge means unknown, and nothing else** (§3.3, revised 2026-08-23).
Every write here works without one; the tests that pass `None` are not testing a
degraded mode, they are testing the default.
"""

from datetime import datetime, timezone

import pytest

from epimemer.core.types import (
    BASE_METACONTEXT_ID,
    ClaimKind,
    EdgeType,
    Fact,
    Inference,
    JudgeRef,
    NodeEdge,
    NodeStatus,
    Topic,
    ValidityInterval,
    with_retirement,
    with_return,
)
from epimemer.core.temporal import IntervalBasis, PreciseInstant, UnboundedInstant
from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.mcp import tools


CRITIC = JudgeRef(agent_id="critic", digest="d1")
EDITOR = JudgeRef(agent_id="editor", digest="d2")


@pytest.fixture
def embedder():
    return MockEmbeddingProvider(model_id="mock-embed", dimension=8)


async def _fact(storage, embedder, content: str, *, claim_kind=None, source="doc1"):
    node = Fact(content=content, source_id="seg1", claim_kind=claim_kind)
    await storage.store_node(node)
    # States a frame, as every ingested node has since the frame requirement: absence names none,
    # so two frameless nodes share none and a `one_claim` verdict is refused.
    await storage.store_edge(NodeEdge(
        src_id=node.id, dst_id=BASE_METACONTEXT_ID,
        type=EdgeType.HAS_METACONTEXT,
    ))
    vectors = await embedder.embed([content])
    from epimemer.core.types import EmbeddingRecord

    await storage.store_embedding(EmbeddingRecord(
        item_id=node.id, model_id=embedder.model_id, vector=vectors[0]
    ))
    return node


async def _topic(storage, embedder, content: str):
    node = Topic(content=content, source_id="seg1")
    await storage.store_node(node)
    vectors = await embedder.embed([content])
    from epimemer.core.types import EmbeddingRecord

    await storage.store_embedding(EmbeddingRecord(
        item_id=node.id, model_id=embedder.model_id, vector=vectors[0]
    ))
    return node


async def _edge_between(storage, a_id, b_id, edge_type):
    from epimemer.pipelines.reflection.similarity_decisions import symmetric_edge_between

    return await symmetric_edge_between(a_id, b_id, edge_type, storage)


class TestTheLifecycleEpisodeCarriesWhoRetiredIt:
    """Retiring a node is a decision, and the episode is where *when* already
    lives. Putting *who* anywhere else would mean joining two records to answer
    one question."""

    async def test_a_correction_names_its_judge(self, storage, embedder):
        node = await _fact(storage, embedder, "the office is in Leeds")

        await tools.update(
            node.id, "the office is in Manchester", storage, embedder,
            because="it_was_wrong", judge=CRITIC,
        )

        retired = await storage.get_node(node.id)
        assert retired.lifecycle[-1].retired_by == CRITIC

    async def test_a_world_change_names_its_judge(self, storage, embedder):
        node = await _fact(storage, embedder, "Labour is in opposition")

        await tools.update(
            node.id, "Labour is in government", storage, embedder,
            because="the_world_changed", judge=CRITIC,
        )

        retired = await storage.get_node(node.id)
        assert retired.lifecycle[-1].retired_by == CRITIC
        assert retired.status is NodeStatus.HISTORICAL

    async def test_supersede_by_an_existing_node_names_its_judge(
        self, storage, embedder
    ):
        loser = await _fact(storage, embedder, "the figure is 500,000")
        winner = await _fact(storage, embedder, "the figure is 5,000,000")

        await tools.supersede_by(
            loser.id, winner.id, storage, because="it_was_wrong", judge=CRITIC,
        )

        assert (await storage.get_node(loser.id)).lifecycle[-1].retired_by == CRITIC

    async def test_a_merge_names_its_judge_on_every_source(self, storage, embedder):
        a = await _fact(storage, embedder, "the treaty was signed in Vienna",
                        claim_kind=ClaimKind.STATE)
        b = await _fact(storage, embedder, "the treaty was signed in Vienna",
                        claim_kind=ClaimKind.STATE)

        result, _ = await tools.merge_facts(
            [a.id, b.id], "the treaty was signed in Vienna", storage, embedder,
            judge=CRITIC,
        )

        assert result["merged"] is True
        for source_id in (a.id, b.id):
            assert (await storage.get_node(source_id)).lifecycle[-1].retired_by == CRITIC

    async def test_archival_names_its_judge(self, storage, embedder):
        node = await _fact(storage, embedder, "a trivial aside")

        await tools.apply_reflection(
            storage, embedder, archivals=[node.id], judge=CRITIC,
        )

        assert (await storage.get_node(node.id)).lifecycle[-1].retired_by == CRITIC

    async def test_retiring_without_a_judge_is_a_normal_write(
        self, storage, embedder
    ):
        """The default, not a degraded path: `None` means unknown and the write
        goes through exactly as it did before any of this existed."""
        node = await _fact(storage, embedder, "the office is in Leeds")

        await tools.update(
            node.id, "the office is in Manchester", storage, embedder,
            because="it_was_wrong",
        )

        retired = await storage.get_node(node.id)
        assert retired.lifecycle[-1].retired_by is None
        assert retired.status is NodeStatus.CORRECTED


class TestRetiringAndReturningAreTwoDecisions:
    """Often months apart, sometimes two agents. One field could not hold both,
    and the second would silently overwrite the first."""

    async def test_a_return_names_its_own_judge(self, storage, embedder):
        node = await _fact(storage, embedder, "a trivial aside")
        await tools.apply_reflection(
            storage, embedder, archivals=[node.id], judge=CRITIC,
        )

        await tools.restore(storage, node_ids=[node.id], judge=EDITOR)

        episode = (await storage.get_node(node.id)).lifecycle[-1]
        assert episode.retired_by == CRITIC
        assert episode.restored_by == EDITOR

    async def test_a_reverse_merge_names_who_reversed_it(self, storage, embedder):
        a = await _fact(storage, embedder, "one claim", claim_kind=ClaimKind.STATE)
        b = await _fact(storage, embedder, "one claim", claim_kind=ClaimKind.STATE)
        merged, _ = await tools.merge_facts(
            [a.id, b.id], "one claim", storage, embedder, judge=CRITIC,
        )

        result, _ = await tools.reverse_merge(
            merged["fact_id"], storage, judge=EDITOR,
        )

        assert result["reversed"] is True
        episode = (await storage.get_node(a.id)).lifecycle[-1]
        assert episode.retired_by == CRITIC, "the merge's judge stays put"
        assert episode.restored_by == EDITOR

    async def test_a_reactivation_names_its_judge(self, storage, embedder):
        """A historical claim asserted true again is somebody's call."""
        from epimemer.core.types import RawDocument

        doc = RawDocument(content="the 2024 result", source="news")
        await storage.store_document(doc)
        node = await _fact(storage, embedder, "Labour is in government")
        await tools.update(
            node.id, "Labour is in opposition", storage, embedder,
            because="the_world_changed", judge=CRITIC,
        )

        await tools.restore(
            storage, node_ids=[node.id], sourced_from=doc.id, judge=EDITOR,
        )

        episode = (await storage.get_node(node.id)).lifecycle[-1]
        assert episode.retired_by == CRITIC
        assert episode.restored_by == EDITOR

    async def test_the_reactivation_edge_names_the_judge_too(
        self, storage, embedder
    ):
        from epimemer.core.types import RawDocument

        doc = RawDocument(content="the 2024 result", source="news")
        await storage.store_document(doc)
        node = await _fact(storage, embedder, "Labour is in government")
        await tools.update(
            node.id, "Labour is in opposition", storage, embedder,
            because="the_world_changed",
        )

        await tools.restore(
            storage,
            node_ids=[node.id],
            sourced_from=doc.id,
            validity=[ValidityInterval(
                start=PreciseInstant(at=datetime(2024, 7, 5, tzinfo=timezone.utc)),
                end=UnboundedInstant(),
                basis=IntervalBasis.STATED,
            ).model_dump(mode="json")],
            judge=EDITOR,
        )

        edges = await storage.get_edges_from(node.id, edge_type=EdgeType.SOURCED_FROM)
        assert [e.judged_by for e in edges] == [EDITOR]


class TestTheEdgeCarriesWhoAssertedIt:
    """A judgment edge exists *because* somebody judged. Asking who should not
    need a second record."""

    async def test_a_contradiction_names_its_judge(self, storage, embedder):
        a = await _fact(storage, embedder, "the vote passed")
        b = await _fact(storage, embedder, "the vote failed")

        await tools.record_contradiction(a.id, b.id, storage, judge=CRITIC)

        edge = await _edge_between(storage, a.id, b.id, EdgeType.CONTRADICTION)
        assert edge.judged_by == CRITIC

    async def test_a_variant_names_its_judge(self, storage, embedder):
        a = await _fact(storage, embedder, "the city fell in 1453")
        b = await _fact(storage, embedder, "the city never fell")

        await tools.record_variant(a.id, b.id, storage, judge=CRITIC)

        edge = await _edge_between(storage, a.id, b.id, EdgeType.VARIANT_OF)
        assert edge.judged_by == CRITIC

    async def test_both_similarity_verdicts_name_their_judge(
        self, storage, embedder
    ):
        a = await _fact(storage, embedder, "the treaty was signed in Vienna")
        b = await _fact(storage, embedder, "the treaty was signed in Vienna")
        c = await _fact(storage, embedder, "the treaty was ratified in Vienna")
        d = await _fact(storage, embedder, "the treaty was drafted in Vienna")

        await tools.apply_reflection(
            storage, embedder,
            similarities=[
                {"pair": [a.id, b.id], "verdict": "one_claim", "because": "same claim"},
                {"pair": [c.id, d.id], "verdict": "distinct", "because": "two acts"},
            ],
            judge=CRITIC,
        )

        for edge_type, pair in (
            (EdgeType.SIMILARITY, (a.id, b.id)),
            (EdgeType.ASSESSED, (a.id, b.id)),
            (EdgeType.ASSESSED, (c.id, d.id)),
        ):
            edge = await _edge_between(storage, *pair, edge_type)
            assert edge is not None and edge.judged_by == CRITIC, edge_type

    async def test_a_user_relation_names_its_judge(self, storage, embedder):
        a = await _topic(storage, embedder, "the merger")
        b = await _topic(storage, embedder, "the layoffs")

        result, _ = await tools.link(
            a.id, b.id, storage, relation="preceded", judge=CRITIC,
        )

        edges = await storage.get_edges_from(a.id, edge_type=EdgeType.RELATED)
        assert [e.judged_by for e in edges] == [CRITIC]
        assert result["edge_id"] == edges[0].id

    async def test_re_recording_a_pair_does_not_restamp_it(
        self, storage, embedder
    ):
        """A second agent calling the same tool has **confirmed**, not decided.

        Overwriting the name would erase the only record of who made the call,
        and replace it with someone who merely agreed — which is a review, and
        reviews get their own record rather than someone else's field.
        """
        a = await _fact(storage, embedder, "the vote passed")
        b = await _fact(storage, embedder, "the vote failed")
        await tools.record_contradiction(a.id, b.id, storage, judge=CRITIC)

        result, _ = await tools.record_contradiction(
            a.id, b.id, storage, judge=EDITOR,
        )

        assert result["created"] is False
        edge = await _edge_between(storage, a.id, b.id, EdgeType.CONTRADICTION)
        assert edge.judged_by == CRITIC


class TestTheValueSignalCarriesWhoJudgedImportance:
    async def test_the_latest_judgment_names_its_judge(self, storage, embedder):
        node = await _fact(storage, embedder, "the outage lasted four hours")

        await tools.judge_importance(
            node.id, direction="up", reason="cited in the postmortem",
            storage=storage, judge=CRITIC,
        )

        value = (await storage.get_node(node.id)).value
        assert value.importance_judged_by == CRITIC
        assert value.importance_judged_at is not None

    async def test_every_entry_in_the_trail_names_its_own_judge(
        self, storage, embedder
    ):
        """Three judgments by three agents compose into one number, and the
        trail is the only place they stay separable."""
        node = await _fact(storage, embedder, "the outage lasted four hours")

        await tools.judge_importance(
            node.id, direction="up", reason="cited", storage=storage, judge=CRITIC,
        )
        await tools.judge_importance(
            node.id, direction="down", reason="superseded", storage=storage,
            judge=EDITOR,
        )
        await tools.judge_importance(
            node.id, direction="up", reason="cited again", storage=storage,
        )

        trail = (await storage.get_node(node.id)).metadata["reinforcements"]
        # An unknown judge is stored as **absence**, not as a null — the rule
        # every backend applies to metadata, and the same one `confidence`
        # follows. So the third entry has no key at all.
        assert [
            (e.get("judged_by") or {}).get("agent_id") for e in trail
        ] == ["critic", "editor", None]
        # The scalar pair is the latest judgment, not the history.
        assert (await storage.get_node(node.id)).value.importance_judged_by is None


class TestTheNodeCarriesWhoWroteItsWording:
    """Content written during reflect has an author, and it is not whoever
    wrote the version it replaced."""

    async def test_a_correction_credits_the_agent_that_wrote_it(
        self, storage, embedder
    ):
        node = await _fact(storage, embedder, "the office is in Leeds")
        node.judged_by = CRITIC
        await storage.store_node(node)

        result, _ = await tools.update(
            node.id, "the office is in Manchester", storage, embedder,
            because="it_was_wrong", judge=EDITOR,
        )

        replacement = await storage.get_node(result["new_node_id"])
        assert replacement.judged_by == EDITOR, "not inherited from the old wording"
        assert (await storage.get_node(node.id)).judged_by == CRITIC

    async def test_a_synthesised_parent_names_its_judge(self, storage, embedder):
        a = await _topic(storage, embedder, "quarterly revenue")
        b = await _topic(storage, embedder, "quarterly costs")

        await tools.apply_reflection(
            storage, embedder,
            parents=[{"children_ids": [a.id, b.id], "content": "quarterly results"}],
            judge=CRITIC,
        )

        parents = [
            n for n in await storage.query_nodes(node_type=None)
            if isinstance(n, Topic) and n.content == "quarterly results"
        ]
        assert [p.judged_by for p in parents] == [CRITIC]

    async def test_a_merge_survivor_names_its_judge(self, storage, embedder):
        a = await _fact(storage, embedder, "one claim", claim_kind=ClaimKind.STATE)
        b = await _fact(storage, embedder, "one claim", claim_kind=ClaimKind.STATE)

        result, _ = await tools.merge_facts(
            [a.id, b.id], "one claim", storage, embedder, judge=CRITIC,
        )

        assert (await storage.get_node(result["fact_id"])).judged_by == CRITIC


class TestWhatTheAgentSees:
    async def test_an_unknown_judge_is_not_sent_back_as_null(
        self, storage, embedder
    ):
        """True of every node in a graph that records no judge, so repeating it
        per result is noise the agent pays for."""
        node = await _fact(storage, embedder, "a claim")

        result, _ = await tools.query_graph(node.id, storage, hops=0)

        assert "judged_by" not in result["nodes"][0]
        # The contrast: an unrated confidence *is* sent, because it is a caveat
        # about the claim rather than a property of the graph's settings.
        assert result["nodes"][0]["value"]["confidence"] is None

    async def test_a_known_judge_is_sent(self, storage, embedder):
        node = await _fact(storage, embedder, "a claim")
        node.judged_by = CRITIC
        await storage.store_node(node)

        result, _ = await tools.query_graph(node.id, storage, hops=0)

        assert result["nodes"][0]["judged_by"] == {"agent_id": "critic", "digest": "d1"}


class TestBothBackendsKeepIt:
    """A nullable field one store keeps and the other drops is invisible until
    somebody asks *who judged this* months later."""

    async def test_an_episode_round_trips_both_judges(self, storage, embedder):
        node = await _fact(storage, embedder, "a claim")
        at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        node.lifecycle = with_return(
            with_retirement(
                [], at=at, because=NodeStatus.ARCHIVED, judge=CRITIC,
            ),
            at=at, judge=EDITOR,
        )
        await storage.store_node(node)

        episode = (await storage.get_node(node.id)).lifecycle[0]
        assert episode.retired_by == CRITIC
        assert episode.restored_by == EDITOR

    async def test_an_edge_round_trips_its_judge(self, storage, embedder):
        a = await _fact(storage, embedder, "a")
        b = await _fact(storage, embedder, "b")
        edge = NodeEdge(
            src_id=a.id, dst_id=b.id, type=EdgeType.SIMILARITY, judged_by=CRITIC,
        )

        await storage.store_edge(edge)

        # By type: `_fact` also writes the node's frame edge, which carries no
        # judge because nothing here claimed to have framed it.
        stored = await storage.get_edges_from(a.id, edge_type=EdgeType.SIMILARITY)
        assert [e.judged_by for e in stored] == [CRITIC]

    async def test_a_node_round_trips_its_judge(self, storage, embedder):
        node = Inference(content="a derivation", source_id="seg1", judged_by=CRITIC)

        await storage.store_node(node)

        assert (await storage.get_node(node.id)).judged_by == CRITIC

    async def test_an_archived_node_carries_its_judges_through_the_blob(
        self, storage, embedder
    ):
        """The archive is the record; a judge lost on export is lost for good."""
        node = await _fact(storage, embedder, "a trivial aside")
        node.judged_by = CRITIC
        await storage.store_node(node)

        result, _ = await tools.apply_reflection(
            storage, embedder, archivals=[node.id], judge=EDITOR,
        )

        [archived] = result["archive_data"]["nodes"]
        assert archived["judged_by"] == {"agent_id": "critic", "digest": "d1"}


class TestResolvingTheJudgeAtTheBoundary:
    """`_bound_judge` is the one place a write learns who is calling.

    It is checked here with a stand-in context because the real one needs an MCP
    session, and the behaviours that matter — no session, and an id the graph no
    longer approves — are exactly the ones a session-carrying test cannot reach.
    """

    class _Ctx:
        """The two things `_bound_judge` touches, and nothing else."""

        def __init__(self, storage, stored, raises=False):
            self.lifespan_context = {"storage": storage}
            self._stored = stored
            self._raises = raises

        async def get_state(self, key):
            if self._raises:
                raise RuntimeError("no session exists")
            return self._stored

    async def test_no_session_is_no_judge_rather_than_an_error(self, storage):
        from epimemer.mcp.server import _bound_judge

        ctx = self._Ctx(storage, None, raises=True)

        assert await _bound_judge(ctx) is None

    async def test_an_approved_judge_comes_back(self, storage):
        from epimemer.mcp.server import _bound_judge

        await storage.set_approved_agent_ids(["critic"])
        ctx = self._Ctx(storage, CRITIC.model_dump(mode="json"))

        assert await _bound_judge(ctx) == CRITIC

    async def test_a_revoked_id_records_as_unknown(self, storage):
        """Approval is re-checked on every write, so the `use_graph` check is
        not a single point of failure.

        Unknown rather than an error: recording the name would assert an
        approval that no longer exists, and refusing is the graph's policy
        talking, which this function does not hold.
        """
        from epimemer.mcp.server import _bound_judge

        await storage.set_approved_agent_ids(["someone-else"])
        ctx = self._Ctx(storage, CRITIC.model_dump(mode="json"))

        assert await _bound_judge(ctx) is None

    async def test_a_graph_that_approved_nobody_records_as_unknown(self, storage):
        from epimemer.mcp.server import _bound_judge

        ctx = self._Ctx(storage, CRITIC.model_dump(mode="json"))

        assert await _bound_judge(ctx) is None
