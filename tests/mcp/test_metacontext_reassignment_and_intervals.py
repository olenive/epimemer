"""Revising the two ingest judgments that had no way back.

Both are `rejudge`'s category — *the claim is fine, the judgment about it was
wrong* — and both are their own tool for a **structural** reason rather than a
tidiness one. `rejudge` is addressed by `node_id` and promises that no status,
edge or lineage moves. A metacontext revision moves an edge and changes what retrieval
does; an interval belongs to a **(node, source) pair** rather than to a node, so
folding it in would grow a `source_id` that applies to one field of five.

What the metacontext half is really guarding is that **untagged is not neutral**.
Base-reality knowledge is inherited by every metacontext, so withdrawing a node's last
metacontext promotes the claim from asserted-in-one-world to asserted-in-all — which is
why the paradigm repair is a *move*, and why a bare withdrawal has to say it
means the promotion.

Both backends via the `storage` fixture.
"""

from datetime import UTC, datetime

from epimemer.core.temporal import (
    IntervalBasis,
    PreciseInstant,
    UnknownInstant,
    ValidityInterval,
)
from epimemer.core.types import (
    BASE_METACONTEXT_ID,
    DecisionKind,
    EdgeType,
    Fact,
    JudgeRef,
    Metacontext,
    NodeEdge,
    RawDocument,
)
from epimemer.mcp import tools

CRITIC = JudgeRef(agent_id="critic", digest="d1")
EDITOR = JudgeRef(agent_id="editor", digest="d2")


async def _fact(storage, content="Le Guin published The Dispossessed in 1974"):
    node = Fact(content=content, source_id="seg1")
    await storage.store_node(node)
    return node


async def _metacontext(storage, content="The world of Anarres"):
    mc = Metacontext(content=content)
    await storage.store_metacontext(mc)
    return mc


async def _in_metacontext(storage, node, *metacontexts):
    for mc in metacontexts:
        await storage.store_edge(
            NodeEdge(
                src_id=node.id,
                dst_id=mc.id,
                type=EdgeType.HAS_METACONTEXT,
                judged_by=CRITIC,
            )
        )


async def _metacontexts_of(storage, node_id):
    from epimemer.pipelines.reflection.review import metacontexts_of

    return await metacontexts_of(node_id, storage)


# --- The metacontext half ---


class TestMovingAMetacontextIsOneCall:
    async def test_the_node_lands_in_the_new_metacontext_and_leaves_the_old(self, storage):
        node = await _fact(storage)
        novel, real_history = await _metacontext(storage), await _metacontext(storage, "History")
        await _in_metacontext(storage, node, novel)

        result, _ = await tools.reassign_metacontext(
            node.id,
            storage,
            withdraw=novel.id,
            assign=real_history.id,
            because="a fact about the author, not about Anarres",
            judge=EDITOR,
        )

        assert result["reassigned"] is True
        assert result["metacontexts_now"] == [real_history.id]
        assert await _metacontexts_of(storage, node.id) == {real_history.id}

    async def test_a_move_never_asks_the_last_metacontext_question(self, storage):
        """The point of `assign`. Withdraw-then-link passes through untagged —
        asserted in **every** metacontext — and strands the node there if the second
        call never happens. A move never reaches that state, so it needs no
        acknowledgment."""
        node = await _fact(storage)
        novel, history = await _metacontext(storage), await _metacontext(storage, "History")
        await _in_metacontext(storage, node, novel)

        result, _ = await tools.reassign_metacontext(
            node.id,
            storage,
            withdraw=novel.id,
            assign=history.id,
            because="mis-filed",
        )

    async def test_assigning_a_metacontext_this_graph_does_not_have_is_refused(self, storage):
        """Metacontext ids are per graph. One carried over from another names nothing
        here, and a node standing in nothing shares a metacontext with no other node."""
        node = await _fact(storage)
        novel = await _metacontext(storage)
        await _in_metacontext(storage, node, novel)

        result, _ = await tools.reassign_metacontext(
            node.id,
            storage,
            withdraw=novel.id,
            assign="from-another-graph",
            because="mis-filed",
        )

        assert result["reassigned"] is False
        assert "create_metacontext" in result["refused"]
        assert await _metacontexts_of(storage, node.id) == {novel.id}

    async def test_withdrawing_one_of_several_leaves_the_rest(self, storage):
        node = await _fact(storage)
        novel, history = await _metacontext(storage), await _metacontext(storage, "History")
        await _in_metacontext(storage, node, novel, history)

        result, _ = await tools.reassign_metacontext(
            node.id,
            storage,
            withdraw=novel.id,
            because="only ever real history",
        )

        assert result["metacontexts_now"] == [history.id]


class TestWithdrawingTheLastMetacontextIsRefused:
    """It used to be allowed behind `to_base_reality=True`, because absence
    meant base reality and the withdrawal was a *promotion* worth stating on
    purpose. Absence means nothing now: a node without a metacontext shares one
    with nothing, so it is never compared, never merged, and returned by no
    scoped search. There is nothing left to authorise, so the flag is gone
    rather than renamed — the paradigm case revisable ingest judgments was built for is `assign`.
    """

    async def test_a_bare_last_withdrawal_is_refused(self, storage):
        node = await _fact(storage)
        novel = await _metacontext(storage)
        await _in_metacontext(storage, node, novel)

        result, _ = await tools.reassign_metacontext(
            node.id,
            storage,
            withdraw=novel.id,
            because="a real publication fact",
        )

        assert result["reassigned"] is False
        assert "shares a metacontext with nothing" in result["refused"]
        assert await _metacontexts_of(storage, node.id) == {novel.id}

    async def test_the_refusal_names_the_move_that_works(self, storage):
        """The Le Guin case is still fixable in one call — it just has to say
        where the claim goes, which is the metacontext holding real-world claims."""
        node = await _fact(storage)
        novel = await _metacontext(storage)
        await _in_metacontext(storage, node, novel)

        refusal, _ = await tools.reassign_metacontext(
            node.id,
            storage,
            withdraw=novel.id,
            because="a fact about the author",
        )
        assert "assign=" in refusal["refused"]

        real = await _metacontext(storage, "The Real")
        result, _ = await tools.reassign_metacontext(
            node.id,
            storage,
            withdraw=novel.id,
            assign=real.id,
            because="a fact about the author, not about Anarres",
        )

        assert result["reassigned"] is True
        assert result["metacontexts_now"] == [real.id]
        assert await _metacontexts_of(storage, node.id) == {real.id}

    async def test_withdrawing_one_of_several_is_untouched(self, storage):
        """The refusal is about stranding a node, not about withdrawal."""
        node = await _fact(storage)
        novel, history = await _metacontext(storage), await _metacontext(storage, "History")
        await _in_metacontext(storage, node, novel, history)

        result, _ = await tools.reassign_metacontext(
            node.id,
            storage,
            withdraw=novel.id,
            because="only ever real history",
        )

        assert result["reassigned"] is True
        assert result["metacontexts_now"] == [history.id]


class TestWhatReframingRefuses:
    async def test_a_blank_because(self, storage):
        node = await _fact(storage)
        novel = await _metacontext(storage)
        await _in_metacontext(storage, node, novel)

        result, _ = await tools.reassign_metacontext(
            node.id,
            storage,
            withdraw=novel.id,
            because="   ",
        )

        assert result["reassigned"] is False
        assert await _metacontexts_of(storage, node.id) == {novel.id}

    async def test_a_metacontext_the_node_does_not_hold(self, storage):
        node = await _fact(storage)
        novel, history = await _metacontext(storage), await _metacontext(storage, "History")
        await _in_metacontext(storage, node, novel)

        result, _ = await tools.reassign_metacontext(
            node.id,
            storage,
            withdraw=history.id,
            because="mis-filed",
        )

        assert result["reassigned"] is False

    async def test_a_node_stating_no_metacontext_has_nothing_to_withdraw(self, storage):
        """It is not in base reality either — it is a node nobody has spoken
        for, and `epimemer metacontexts declare` is what ends that."""
        node = await _fact(storage)

        result, _ = await tools.reassign_metacontext(
            node.id,
            storage,
            withdraw=BASE_METACONTEXT_ID,
            because="mis-filed",
        )

        assert result["reassigned"] is False
        assert "no metacontexts at all" in result["refused"]

    async def test_no_such_node(self, storage):
        result, _ = await tools.reassign_metacontext(
            "nope",
            storage,
            withdraw="anything",
            because="mis-filed",
        )

        assert result["reassigned"] is False


class TestTheWithdrawnMetacontextSurvives:
    async def test_the_trail_names_what_was_withdrawn_and_why(self, storage):
        """Every search and corroboration answer given while the metacontext was wrong
        was wrong. This entry, with the journal row's timestamp beside it, is
        the only thing that bounds which answers those were."""
        node = await _fact(storage)
        novel, history = await _metacontext(storage), await _metacontext(storage, "History")
        await _in_metacontext(storage, node, novel)

        await tools.reassign_metacontext(
            node.id,
            storage,
            withdraw=novel.id,
            assign=history.id,
            because="a fact about the author",
            judge=EDITOR,
        )

        stored = await storage.get_node(node.id)
        [entry] = stored.metadata["metacontext_reassignments"]
        assert entry["withdrew"] == novel.id
        assert entry["assigned"] == history.id
        assert entry["because"] == "a fact about the author"
        assert entry["judged_by"]["agent_id"] == "editor"

    async def test_the_trail_is_append_only(self, storage):
        node = await _fact(storage)
        a, b, c = (
            await _metacontext(storage, "A"),
            await _metacontext(storage, "B"),
            await _metacontext(storage, "C"),
        )
        await _in_metacontext(storage, node, a)

        await tools.reassign_metacontext(
            node.id, storage, withdraw=a.id, assign=b.id, because="first"
        )
        await tools.reassign_metacontext(
            node.id, storage, withdraw=b.id, assign=c.id, because="second"
        )

        stored = await storage.get_node(node.id)
        assert [e["because"] for e in stored.metadata["metacontext_reassignments"]] == [
            "first",
            "second",
        ]

    async def test_it_journals_its_own_kind(self, storage):
        node = await _fact(storage)
        novel = await _metacontext(storage)
        await _in_metacontext(storage, node, novel)

        history = await _metacontext(storage, "History")
        result, _ = await tools.reassign_metacontext(
            node.id,
            storage,
            withdraw=novel.id,
            assign=history.id,
            because="a real publication fact",
            judge=EDITOR,
        )

        [row] = await storage.query_decisions(kinds=[DecisionKind.METACONTEXT_REASSIGNMENT])
        assert row.subject_ids == [node.id]
        assert row.judged_by == EDITOR
        assert row.id == result["decision_id"]

    async def test_nothing_is_retired_and_no_lineage_moves(self, storage):
        node = await _fact(storage)
        novel = await _metacontext(storage)
        await _in_metacontext(storage, node, novel)
        before = await storage.get_node(node.id)

        history = await _metacontext(storage, "History")
        await tools.reassign_metacontext(
            node.id, storage, withdraw=novel.id, assign=history.id, because="mis-filed"
        )

        after = await storage.get_node(node.id)
        assert after.status == before.status
        assert after.content == before.content
        assert await storage.query_decisions(kinds=[DecisionKind.CORRECTION]) == []


# --- The interval half ---


def _interval(start_year, end_year=None, basis=IntervalBasis.STATED):
    return ValidityInterval(
        start=PreciseInstant(at=datetime(start_year, 1, 1, tzinfo=UTC)),
        end=(
            PreciseInstant(at=datetime(end_year, 1, 1, tzinfo=UTC))
            if end_year
            else UnknownInstant()
        ),
        basis=basis,
    )


async def _sourced(storage, *intervals, content="Blair is Prime Minister"):
    doc = RawDocument(content="a 1997 article", source="article")
    await storage.store_document(doc)
    node = Fact(content=content, source_id="seg1")
    await storage.store_node(node)
    await storage.store_edge(
        NodeEdge(
            src_id=node.id,
            dst_id=doc.id,
            type=EdgeType.SOURCED_FROM,
            validity=list(intervals),
            judged_by=CRITIC,
        )
    )
    return node, doc


class TestCorrectingAPeriodThatIsPresentAndWrong:
    async def test_the_replacement_list_is_what_the_edge_carries(self, storage):
        node, doc = await _sourced(storage, _interval(1979, 1999))

        result, _ = await tools.correct_interval(
            node.id,
            storage,
            source_id=doc.id,
            intervals=[_interval(1997, 1999).model_dump(mode="json")],
            because="1979 was the republication date, misread as the original",
            judge=EDITOR,
        )

        assert result["corrected"] is True
        [edge] = await storage.get_edges_from(node.id, edge_type=EdgeType.SOURCED_FROM)
        assert edge.validity[0].start.at.year == 1997

    async def test_an_empty_list_removes_a_period_that_was_invented(self, storage):
        """Refusing this would leave a fabricated interval unremovable, which is
        revisable ingest judgments's own shape a second time."""
        node, doc = await _sourced(storage, _interval(1979, 1999))

        result, _ = await tools.correct_interval(
            node.id,
            storage,
            source_id=doc.id,
            intervals=[],
            because="the article gives no dates; this was invented",
        )

        assert result["corrected"] is True
        [edge] = await storage.get_edges_from(node.id, edge_type=EdgeType.SOURCED_FROM)
        assert list(edge.validity) == []

    async def test_the_edge_keeps_its_id_and_its_judge(self, storage):
        node, doc = await _sourced(storage, _interval(1979, 1999))
        [before] = await storage.get_edges_from(node.id, edge_type=EdgeType.SOURCED_FROM)

        await tools.correct_interval(
            node.id,
            storage,
            source_id=doc.id,
            intervals=[_interval(1997, 1999).model_dump(mode="json")],
            because="misread",
            judge=EDITOR,
        )

        [after] = await storage.get_edges_from(node.id, edge_type=EdgeType.SOURCED_FROM)
        assert after.id == before.id
        assert after.judged_by == CRITIC, "who recorded the provenance is unchanged"

    async def test_the_prior_periods_survive_on_the_edge(self, storage):
        node, doc = await _sourced(storage, _interval(1979, 1999))

        await tools.correct_interval(
            node.id,
            storage,
            source_id=doc.id,
            intervals=[_interval(1997, 1999).model_dump(mode="json")],
            because="misread the republication date",
            judge=EDITOR,
        )

        [edge] = await storage.get_edges_from(node.id, edge_type=EdgeType.SOURCED_FROM)
        [entry] = edge.metadata["interval_corrections"]
        assert entry["was"][0]["start"]["at"].startswith("1979")
        assert entry["because"] == "misread the republication date"
        assert entry["judged_by"]["agent_id"] == "editor"

    async def test_it_journals_its_own_kind(self, storage):
        node, doc = await _sourced(storage, _interval(1979, 1999))

        result, _ = await tools.correct_interval(
            node.id,
            storage,
            source_id=doc.id,
            intervals=[_interval(1997, 1999).model_dump(mode="json")],
            because="misread",
            judge=EDITOR,
        )

        [row] = await storage.query_decisions(kinds=[DecisionKind.INTERVAL_CORRECTION])
        assert row.subject_ids == [node.id]
        assert row.id == result["decision_id"]

    async def test_the_basis_is_the_callers_to_state(self, storage):
        """Unlike `apply_boundary`, which forces `inferred` because that is what
        a derived endpoint is. A correction is often restoring what the document
        actually said, and calling that inferred would understate it."""
        node, doc = await _sourced(storage, _interval(1979, 1999))

        await tools.correct_interval(
            node.id,
            storage,
            source_id=doc.id,
            intervals=[_interval(1997, 1999, basis=IntervalBasis.STATED).model_dump(mode="json")],
            because="the article states 1997",
        )

        [edge] = await storage.get_edges_from(node.id, edge_type=EdgeType.SOURCED_FROM)
        assert edge.validity[0].basis == IntervalBasis.STATED


class TestWhatCorrectingRefuses:
    async def test_a_blank_because(self, storage):
        node, doc = await _sourced(storage, _interval(1979, 1999))

        result, _ = await tools.correct_interval(
            node.id,
            storage,
            source_id=doc.id,
            intervals=[_interval(1997, 1999).model_dump(mode="json")],
            because="  ",
        )

        assert result["corrected"] is False

    async def test_a_source_no_edge_names(self, storage):
        node, _doc = await _sourced(storage, _interval(1979, 1999))

        result, _ = await tools.correct_interval(
            node.id,
            storage,
            source_id="not-a-document",
            intervals=[],
            because="misread",
        )

        assert result["corrected"] is False
        assert "0 provenance edges" in result["refused"]

    async def test_restating_what_is_already_there(self, storage):
        """A restatement is not a revision, matching `rejudge`."""
        node, doc = await _sourced(storage, _interval(1979, 1999))

        result, _ = await tools.correct_interval(
            node.id,
            storage,
            source_id=doc.id,
            intervals=[_interval(1979, 1999).model_dump(mode="json")],
            because="checking",
        )

        assert result["corrected"] is False
        assert "nothing to revise" in result["refused"]

    async def test_an_interval_with_no_basis(self, storage):
        """`basis` has no default on purpose: the agent is not a source, so
        every period says whether it was copied or read."""
        node, doc = await _sourced(storage, _interval(1979, 1999))

        result, _ = await tools.correct_interval(
            node.id,
            storage,
            source_id=doc.id,
            intervals=[{"timeline_id": None}],
            because="misread",
        )

        assert result["corrected"] is False
        assert "basis" in result["refused"]

    async def test_a_refusal_journals_nothing(self, storage):
        node, _doc = await _sourced(storage, _interval(1979, 1999))

        await tools.correct_interval(
            node.id,
            storage,
            source_id="not-a-document",
            intervals=[],
            because="misread",
        )

        assert await storage.query_decisions(kinds=[DecisionKind.INTERVAL_CORRECTION]) == []


class TestRejudgeSendsYouToTheRightTool:
    async def test_its_refusal_names_both_siblings(self, storage):
        """The only real argument for folding all three into one tool was that
        an agent looks in the obvious place. So the obvious place points on —
        otherwise it reaches for `supersede_by` and files truth as error."""
        node = await _fact(storage)

        result, _ = await tools.rejudge(node.id, storage, because="nothing supplied")

        assert result["rejudged"] is False
        assert "`reassign_metacontext`" in result["refused"]
        assert "`correct_interval`" in result["refused"]
        assert "supersede_by" in result["refused"]
