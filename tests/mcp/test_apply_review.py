"""What a reviewer can write (REVIEW_MODE.md §6.4, §6.5, step 7).

Step 6 made the journal readable, shakiest first. On its own that is a reviewer
who can find every mistake and record nothing about any of them — which is
#64's defect one layer up: *a verdict with no writer*. These are the writers.

**The rule most of this file protects is that neither of them changes the
graph, for opposite reasons.** A confirmation has nothing to change. A dissent
has plenty and does none of it: the undo for a merge is `reverse_merge`, for an
archival `restore`, for a `one_claim` verdict a `distinct` — each with its own
refusals and its own row that sets `supersedes` because it really did supersede
something. A dissent sets only `reviews`, so the journal never claims to have
overturned a decision whose effect still stands.

`rejudge` is the exception that proves it: it *does* write, and what it writes
is a judgment about a claim rather than the claim. Nothing there moves a status,
an edge or a lineage, and the value it replaces is kept.
"""

import pytest

from epimemer.core.types import (
    BASE_METACONTEXT_ID,
    ClaimKind,
    DecisionKind,
    DecisionRecord,
    EmbeddingRecord,
    Fact,
    JudgeRef,
    NodeStatus,
    Topic,
    ValueSignal,
)
from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.mcp import tools
from epimemer.mcp.config import ServerConfig
from epimemer.pipelines.review.apply import (
    ORIGINATING_KINDS,
    REJUDGEABLE_FIELDS,
    Rejudged,
    RejudgeRefused,
    ReviewRecorded,
    ReviewRefused,
    rejudge_node,
    review_decision,
)


CRITIC = JudgeRef(agent_id="critic", digest="d1")
EDITOR = JudgeRef(agent_id="editor", digest="d2")


@pytest.fixture
def embedder():
    return MockEmbeddingProvider(model_id="mock-embed", dimension=8)


@pytest.fixture
def config():
    return ServerConfig(storage_backend="memory", embedding_provider="mock")


async def _decision(storage, **kwargs) -> DecisionRecord:
    record = DecisionRecord(**{"kind": DecisionKind.INGEST, **kwargs})
    await storage.record_decision(record)
    return record


async def _fact(storage, embedder, content: str, **kwargs) -> Fact:
    node = Fact(content=content, source_id="seg1", **kwargs)
    await storage.store_node(node)
    vectors = await embedder.embed([content])
    await storage.store_embedding(EmbeddingRecord(
        item_id=node.id, model_id=embedder.model_id, vector=vectors[0]
    ))
    return node


class TestAConfirmationIsARowPointingBack:
    """§3.4: `reviewed` is derived from existence, never stored as a flag on a
    row that claims to be append-only."""

    async def test_it_writes_a_row_that_reviews_the_decision(self, storage):
        original = await _decision(storage, subject_ids=["a", "b"], judged_by=CRITIC)

        outcome = await review_decision(
            storage, decision_id=original.id, agreed=True,
            because="checked both against the source", judge=EDITOR,
        )

        assert isinstance(outcome, ReviewRecorded)
        written = await storage.get_decision(outcome.record_id)
        assert written.kind is DecisionKind.CONFIRMATION
        assert written.reviews == original.id
        assert written.supersedes is None
        assert written.judged_by.agent_id == "editor"

    async def test_the_decision_it_reviewed_is_untouched(self, storage):
        original = await _decision(storage, subject_ids=["a"], judged_by=CRITIC)
        before = (await storage.get_decision(original.id)).model_dump()

        await review_decision(
            storage, decision_id=original.id, agreed=True, because="fine",
            judge=EDITOR,
        )

        assert (await storage.get_decision(original.id)).model_dump() == before

    async def test_review_then_reports_it_reviewed(self, storage):
        original = await _decision(storage, subject_ids=["a"], judged_by=CRITIC)
        await review_decision(
            storage, decision_id=original.id, agreed=True, because="fine",
            judge=EDITOR,
        )

        result, _ = await tools.review(storage)
        rows = {d["decision_id"]: d for d in result["decisions"]}

        assert rows[original.id]["reviewed"] is True
        assert result["unreviewed_count"] == 1, "the confirmation itself"

    async def test_it_defaults_to_every_subject_the_decision_named(self, storage):
        original = await _decision(storage, subject_ids=["a", "b", "c"])

        outcome = await review_decision(
            storage, decision_id=original.id, agreed=True, because="all three",
        )

        assert outcome.subjects == ["a", "b", "c"]

    async def test_it_can_name_the_subjects_actually_checked(self, storage):
        """§4.1. One pointer at an ingest record covering forty-four facts
        otherwise tells the graph a reviewer checked forty-four."""
        original = await _decision(storage, subject_ids=["a", "b", "c"])

        outcome = await review_decision(
            storage, decision_id=original.id, agreed=True,
            because="checked one of the three", subject_ids=["b"],
        )

        assert outcome.subjects == ["b"]

    async def test_a_subject_the_decision_never_named_is_refused(self, storage):
        original = await _decision(storage, subject_ids=["a"])

        outcome = await review_decision(
            storage, decision_id=original.id, agreed=True, because="x",
            subject_ids=["a", "elsewhere"],
        )

        assert isinstance(outcome, ReviewRefused)
        assert "elsewhere" in outcome.reason

    async def test_it_names_whose_decision_it_reviewed(self, storage):
        """So an agent confirming its own earlier call can see that it did —
        allowed, and a weaker check than an independent one."""
        original = await _decision(storage, subject_ids=["a"], judged_by=CRITIC)

        outcome = await review_decision(
            storage, decision_id=original.id, agreed=True, because="mine",
            judge=CRITIC,
        )

        assert outcome.reviewed_judge == "critic"


class TestADissentRecordsTheFindingAndNotTheUndo:
    """The reviewer who most needs this is the one whose undo was **refused**:
    a merge whose survivor has since been contradicted cannot be reversed at
    all (§7), and before this there was nowhere to put the finding."""

    async def test_it_reviews_without_superseding(self, storage):
        original = await _decision(storage, kind=DecisionKind.MERGE,
                                   subject_ids=["survivor", "a", "b"])

        outcome = await review_decision(
            storage, decision_id=original.id, agreed=False,
            because="these are different claims; reverse_merge refuses",
            judge=EDITOR,
        )

        written = await storage.get_decision(outcome.record_id)
        assert written.kind is DecisionKind.DISSENT
        assert written.reviews == original.id
        assert written.supersedes is None, (
            "the merge still stands, and a row claiming otherwise would put the "
            "journal in disagreement with the graph"
        )

    async def test_the_nodes_it_disputes_are_untouched(self, storage, embedder):
        node = await _fact(storage, embedder, "a claim")
        original = await _decision(storage, subject_ids=[node.id])
        before = (await storage.get_node(node.id)).model_dump()

        await review_decision(
            storage, decision_id=original.id, agreed=False, because="wrong",
        )

        assert (await storage.get_node(node.id)).model_dump() == before

    async def test_a_dissent_still_marks_the_decision_reviewed(self, storage):
        """Somebody looked, which is what `unreviewed` asks. The dissent is
        itself an unreviewed decision, so the finding stays visible."""
        original = await _decision(storage, subject_ids=["a"])
        await review_decision(
            storage, decision_id=original.id, agreed=False, because="wrong",
        )

        result, _ = await tools.review(storage, mode="unreviewed")

        ids = [d["decision_id"] for d in result["decisions"]]
        assert original.id not in ids
        assert [d["kind"] for d in result["decisions"]] == ["dissent"]

    async def test_dissent_and_confirmation_are_separable_kinds(self, storage):
        """Two kinds rather than one with a flag: a reviewer asking *what has
        been disputed* does not want the agreements, and a boolean inside a row
        cannot be selected on."""
        agreed = await _decision(storage, subject_ids=["a"])
        disputed = await _decision(storage, subject_ids=["b"])
        await review_decision(storage, decision_id=agreed.id, agreed=True,
                              because="fine")
        await review_decision(storage, decision_id=disputed.id, agreed=False,
                              because="wrong")

        rows = await storage.query_decisions(kinds=[DecisionKind.DISSENT])

        assert [r.reviews for r in rows] == [disputed.id]


class TestARetryMustNotReadAsASecondOpinion:
    """Two confirmations is exactly the evidence a later reviewer weighs."""

    async def test_the_same_judge_confirming_twice_is_refused(self, storage):
        original = await _decision(storage, subject_ids=["a"])
        first = await review_decision(
            storage, decision_id=original.id, agreed=True, because="fine",
            judge=EDITOR,
        )

        again = await review_decision(
            storage, decision_id=original.id, agreed=True, because="fine",
            judge=EDITOR,
        )

        assert isinstance(again, ReviewRefused)
        assert first.record_id in again.reason

    async def test_a_different_judge_is_the_second_check_this_design_wants(
        self, storage
    ):
        original = await _decision(storage, subject_ids=["a"])
        await review_decision(storage, decision_id=original.id, agreed=True,
                              because="fine", judge=EDITOR)

        second = await review_decision(
            storage, decision_id=original.id, agreed=True, because="agree",
            judge=CRITIC,
        )

        assert isinstance(second, ReviewRecorded)

    async def test_changing_your_mind_is_the_other_verdict_and_is_accepted(
        self, storage
    ):
        original = await _decision(storage, subject_ids=["a"])
        await review_decision(storage, decision_id=original.id, agreed=True,
                              because="fine", judge=EDITOR)

        changed = await review_decision(
            storage, decision_id=original.id, agreed=False,
            because="looked again; the source does not say this", judge=EDITOR,
        )

        assert isinstance(changed, ReviewRecorded)

    async def test_naming_different_subjects_is_new_work(self, storage):
        original = await _decision(storage, subject_ids=["a", "b"])
        await review_decision(storage, decision_id=original.id, agreed=True,
                              because="checked a", subject_ids=["a"],
                              judge=EDITOR)

        more = await review_decision(
            storage, decision_id=original.id, agreed=True, because="checked b",
            subject_ids=["b"], judge=EDITOR,
        )

        assert isinstance(more, ReviewRecorded)

    async def test_a_blank_judge_cannot_be_told_from_a_second_reviewer(
        self, storage
    ):
        """Named rather than hidden: two blanks may be two agents or one retry,
        and refusing on that guess would block a genuine second opinion on every
        graph that does not require a judge."""
        original = await _decision(storage, subject_ids=["a"])
        await review_decision(storage, decision_id=original.id, agreed=True,
                              because="fine")

        again = await review_decision(
            storage, decision_id=original.id, agreed=True, because="fine",
        )

        assert isinstance(again, ReviewRecorded)


class TestTheRefusalsThatComeFirst:
    async def test_a_review_with_no_reason_is_refused(self, storage):
        original = await _decision(storage, subject_ids=["a"])

        outcome = await review_decision(
            storage, decision_id=original.id, agreed=True, because="   ",
        )

        assert isinstance(outcome, ReviewRefused)
        assert "rubber stamp" in outcome.reason

    async def test_a_decision_that_is_not_here_says_the_journal_is_per_graph(
        self, storage
    ):
        outcome = await review_decision(
            storage, decision_id="nope", agreed=True, because="x",
        )

        assert isinstance(outcome, ReviewRefused)
        assert "use_graph" in outcome.reason

    async def test_a_certainty_off_the_ladder_is_refused(self, storage):
        original = await _decision(storage, subject_ids=["a"])

        outcome = await review_decision(
            storage, decision_id=original.id, agreed=True, because="x",
            certainty=1.5,
        )

        assert isinstance(outcome, ReviewRefused)
        assert "0.0" in outcome.reason


class TestTheFirstDeclaredCertainty:
    """Nothing supplied one before this, so the whole corpus was tier 2. §6.2
    designed for exactly that, and this is the tier filling from the top."""

    async def test_a_declared_certainty_reaches_the_row(self, storage):
        original = await _decision(storage, subject_ids=["a"])

        outcome = await review_decision(
            storage, decision_id=original.id, agreed=True,
            because="checked the source", certainty=0.3,
            certainty_basis="the source hedges on this point",
        )

        written = await storage.get_decision(outcome.record_id)
        assert written.certainty == 0.3
        assert written.certainty_basis == "the source hedges on this point"

    async def test_it_sorts_above_an_unrated_row_carrying_more_signals(
        self, storage
    ):
        """Tier 1 before tier 2, even here: absence is not a claim of doubt."""
        loaded = await _decision(
            storage, kind=DecisionKind.MERGE,
            subject_ids=["s", "a", "b", "c", "d"],
        )
        original = await _decision(storage, subject_ids=["x"])
        await review_decision(
            storage, decision_id=original.id, agreed=False,
            because="not sure", certainty=0.3,
        )

        result, _ = await tools.review(storage)

        assert result["decisions"][0]["kind"] == "dissent"
        assert result["decisions"][0]["certainty"] == 0.3
        assert loaded.id in [d["decision_id"] for d in result["decisions"]]

    async def test_omitting_it_stores_unrated_rather_than_a_half(self, storage):
        original = await _decision(storage, subject_ids=["a"])

        outcome = await review_decision(
            storage, decision_id=original.id, agreed=True, because="fine",
        )

        assert (await storage.get_decision(outcome.record_id)).certainty is None


class TestTheToolBatchesAndRefusesPerEntry:
    """`apply_reflection`'s shape rather than one transaction: each entry is an
    independent judgment about an unrelated decision that happens to be
    batched."""

    async def test_one_bad_entry_does_not_lose_the_good_ones(self, storage):
        good = await _decision(storage, subject_ids=["a"])

        result, _ = await tools.apply_review(
            storage,
            confirmations=[
                {"decision_id": good.id, "because": "fine"},
                {"decision_id": "nope", "because": "fine"},
            ],
            judge=EDITOR,
        )

        assert result["confirmations"] == 1
        assert len(result["refused"]) == 1

    async def test_it_counts_the_two_kinds_separately(self, storage):
        a = await _decision(storage, subject_ids=["a"])
        b = await _decision(storage, subject_ids=["b"])

        result, _ = await tools.apply_review(
            storage,
            confirmations=[{"decision_id": a.id, "because": "fine"}],
            dissents=[{"decision_id": b.id, "because": "wrong"}],
            judge=EDITOR,
        )

        assert (result["confirmations"], result["dissents"]) == (1, 1)

    async def test_it_names_the_graph_it_wrote_in(self, storage):
        result, _ = await tools.apply_review(storage)
        assert result["graph"] == storage.current_database

    async def test_nothing_supplied_is_an_answer_rather_than_an_error(
        self, storage
    ):
        result, meta = await tools.apply_review(storage)
        assert result["recorded"] == [] and meta.nodes_returned == 0

    async def test_the_subjects_reviewed_are_declared(self, storage, embedder):
        """`retrieved` drives focus in the viewer, so a reviewer's attention
        follows the same path a search's does."""
        node = await _fact(storage, embedder, "a claim")
        original = await _decision(storage, subject_ids=[node.id])

        _, meta = await tools.apply_review(
            storage, confirmations=[{"decision_id": original.id, "because": "ok"}],
        )

        assert [r.node_id for r in meta.retrieved] == [node.id]


class TestRejudgeRevisesAJudgmentAndNotAClaim:
    async def test_it_moves_a_claim_kind_without_retiring_anything(
        self, storage, embedder
    ):
        node = await _fact(storage, embedder, "Labour won the election",
                           claim_kind=ClaimKind.STATE)

        outcome = await rejudge_node(
            storage, node_id=node.id, claim_kind=ClaimKind.EVENT,
            because="an occasion, not a condition that holds", judge=EDITOR,
        )

        assert isinstance(outcome, Rejudged)
        after = await storage.get_node(node.id)
        assert after.claim_kind is ClaimKind.EVENT
        assert after.status is NodeStatus.ACTIVE
        assert after.superseded_at is None
        assert after.lifecycle == []

    async def test_the_wording_and_its_author_are_untouched(
        self, storage, embedder
    ):
        """`judged_by` records who wrote the wording, which is unchanged — a new
        version would be a new node, and this is not one."""
        node = await _fact(storage, embedder, "a claim", claim_kind=ClaimKind.STATE)
        node.judged_by = CRITIC
        await storage.store_node(node)

        await rejudge_node(
            storage, node_id=node.id, claim_kind=ClaimKind.EVENT,
            because="wrong kind", judge=EDITOR,
        )

        after = await storage.get_node(node.id)
        assert after.content == "a claim"
        assert after.judged_by.agent_id == "critic"

    async def test_the_prior_value_is_kept_rather_than_overwritten(
        self, storage, embedder
    ):
        """Without the trail this would be the one call in the system that
        destroys a judgment rather than superseding it."""
        node = await _fact(storage, embedder, "a claim",
                           value=ValueSignal(confidence=0.9))

        await rejudge_node(
            storage, node_id=node.id, confidence=0.3,
            because="the source hedges", judge=EDITOR,
        )

        trail = (await storage.get_node(node.id)).metadata["rejudgments"]
        assert len(trail) == 1
        assert trail[0]["was"] == {"confidence": 0.9}
        assert trail[0]["now"] == {"confidence": 0.3}
        assert trail[0]["because"] == "the source hedges"
        assert trail[0]["judged_by"]["agent_id"] == "editor"

    async def test_the_trail_is_append_only(self, storage, embedder):
        node = await _fact(storage, embedder, "a claim",
                           value=ValueSignal(confidence=0.9))
        await rejudge_node(storage, node_id=node.id, confidence=0.5, because="a")
        await rejudge_node(storage, node_id=node.id, confidence=0.3, because="b")

        trail = (await storage.get_node(node.id)).metadata["rejudgments"]
        assert [entry["because"] for entry in trail] == ["a", "b"]

    async def test_a_basis_lands_beside_the_signal_not_on_it(
        self, storage, embedder
    ):
        """Where ingest already puts it: the basis is prose about one judgment,
        and `ValueSignal` is the numbers every ranker reads."""
        node = await _fact(storage, embedder, "a claim")

        await rejudge_node(
            storage, node_id=node.id, confidence=0.7,
            confidence_basis="the spec, about its own behaviour", because="x",
        )

        after = await storage.get_node(node.id)
        assert after.metadata["confidence_basis"] == (
            "the spec, about its own behaviour"
        )

    async def test_it_reports_only_what_actually_changed(
        self, storage, embedder
    ):
        node = await _fact(storage, embedder, "a claim",
                           claim_kind=ClaimKind.EVENT,
                           value=ValueSignal(confidence=0.9))

        outcome = await rejudge_node(
            storage, node_id=node.id, claim_kind=ClaimKind.EVENT,
            confidence=0.3, because="only the confidence was wrong",
        )

        assert outcome.changed == {"confidence": 0.3}


class TestRejudgePointsAtTheDecisionItRevises:
    async def test_it_reviews_the_ingest_that_made_the_judgment(
        self, storage, embedder, config
    ):
        seg, _ = await tools.segment_text("A claim.", storage, embedder, config)
        await tools.store_decomposition(
            document_id=seg["document_id"],
            segments=[{
                "segment_id": seg["segments"][0]["segment_id"],
                "topics": [], "inferences": [],
                "facts": [{"content": "A claim", "claim_kind": "state"}],
            }],
            storage=storage, embedding_provider=embedder,
            metacontext_id=BASE_METACONTEXT_ID,
        )
        ingest = (await storage.query_decisions(kinds=[DecisionKind.INGEST]))[0]
        fact = next(
            n for n in await storage.query_nodes(node_type=None)
            if n.content == "A claim"
        )

        result, _ = await tools.rejudge(
            fact.id, storage, because="an occasion", claim_kind="event",
        )

        assert result["reviews"] == ingest.id
        row = await storage.get_decision(result["decision_id"])
        assert row.kind is DecisionKind.REJUDGMENT
        assert row.reviews == ingest.id

    async def test_it_points_at_the_decision_not_a_later_confirmation(
        self, storage, embedder
    ):
        """§10.5's rule for pair verdicts, applied here: the oldest originating
        row is the decision; anything after it is a confirmation of one."""
        node = await _fact(storage, embedder, "a claim")
        first = await _decision(storage, subject_ids=[node.id])
        await _decision(storage, subject_ids=[node.id])

        outcome = await rejudge_node(
            storage, node_id=node.id, confidence=0.3, because="x",
        )

        assert outcome.reviews == first.id

    async def test_a_node_older_than_the_journal_leaves_the_pointer_blank(
        self, storage, embedder
    ):
        """The journal cannot cite a row that does not exist, and does not
        pretend to."""
        node = await _fact(storage, embedder, "a claim")

        outcome = await rejudge_node(
            storage, node_id=node.id, confidence=0.3, because="x",
        )

        assert outcome.reviews is None

    async def test_every_originating_kind_is_one_the_journal_writes(self):
        assert set(ORIGINATING_KINDS) <= set(DecisionKind)


class TestRejudgeRefusals:
    async def test_a_claim_kind_on_a_topic_is_refused_rather_than_dropped(
        self, storage, embedder
    ):
        """The refusal ingest already makes: a judgment written into a field
        that does not exist is one the agent believes it made."""
        topic = Topic(content="a theme")
        await storage.store_node(topic)

        outcome = await rejudge_node(
            storage, node_id=topic.id, claim_kind=ClaimKind.EVENT, because="x",
        )

        assert isinstance(outcome, RejudgeRefused)
        assert "facts alone" in outcome.reason

    async def test_nothing_supplied_is_refused_and_names_the_fields(
        self, storage, embedder
    ):
        node = await _fact(storage, embedder, "a claim")

        outcome = await rejudge_node(storage, node_id=node.id, because="x")

        assert isinstance(outcome, RejudgeRefused)
        for field in REJUDGEABLE_FIELDS:
            assert field in outcome.reason

    async def test_importance_is_sent_to_its_own_writer(
        self, storage, embedder
    ):
        """Two writers for one field is how a value ends up depending on which
        tool ran last."""
        node = await _fact(storage, embedder, "a claim")

        outcome = await rejudge_node(storage, node_id=node.id, because="x")

        assert "judge_importance" in outcome.reason
        assert "importance" not in REJUDGEABLE_FIELDS

    async def test_restating_what_the_node_already_says_is_sent_to_apply_review(
        self, storage, embedder
    ):
        node = await _fact(storage, embedder, "a claim",
                           claim_kind=ClaimKind.STATE)

        outcome = await rejudge_node(
            storage, node_id=node.id, claim_kind=ClaimKind.STATE, because="x",
        )

        assert isinstance(outcome, RejudgeRefused)
        assert "apply_review" in outcome.reason

    async def test_no_reason_is_refused(self, storage, embedder):
        node = await _fact(storage, embedder, "a claim")

        outcome = await rejudge_node(
            storage, node_id=node.id, confidence=0.3, because="",
        )

        assert isinstance(outcome, RejudgeRefused)

    async def test_a_missing_node_is_refused(self, storage):
        outcome = await rejudge_node(
            storage, node_id="nope", confidence=0.3, because="x",
        )

        assert isinstance(outcome, RejudgeRefused)

    async def test_a_confidence_off_the_ladder_is_refused(
        self, storage, embedder
    ):
        node = await _fact(storage, embedder, "a claim")

        outcome = await rejudge_node(
            storage, node_id=node.id, confidence=2.0, because="x",
        )

        assert isinstance(outcome, RejudgeRefused)

    async def test_a_refusal_leaves_the_node_alone(self, storage, embedder):
        node = await _fact(storage, embedder, "a claim",
                           value=ValueSignal(confidence=0.9))
        before = (await storage.get_node(node.id)).model_dump()

        await rejudge_node(storage, node_id=node.id, confidence=2.0, because="x")

        assert (await storage.get_node(node.id)).model_dump() == before

    async def test_an_unknown_claim_kind_is_refused_by_the_tool(
        self, storage, embedder
    ):
        node = await _fact(storage, embedder, "a claim")

        result, _ = await tools.rejudge(
            node.id, storage, because="x", claim_kind="occurrence",
        )

        assert result["rejudged"] is False
        assert "state" in result["refused"] and "event" in result["refused"]


class TestRejudgeDeclaresAndJournals:
    async def test_the_row_carries_the_reviewers_certainty(
        self, storage, embedder
    ):
        node = await _fact(storage, embedder, "a claim")

        result, _ = await tools.rejudge(
            node.id, storage, because="the source hedges", confidence=0.3,
            certainty=0.7, certainty_basis="the passage is unambiguous",
        )

        row = await storage.get_decision(result["decision_id"])
        assert row.certainty == 0.7
        assert row.certainty_basis == "the passage is unambiguous"

    async def test_the_two_numbers_land_in_different_places(
        self, storage, embedder
    ):
        """`confidence` is about the material and `certainty` about the act of
        re-judging — this is the one call that takes both."""
        node = await _fact(storage, embedder, "a claim")

        result, _ = await tools.rejudge(
            node.id, storage, because="x", confidence=0.3, certainty=0.9,
        )

        assert (await storage.get_node(node.id)).value.confidence == 0.3
        assert (await storage.get_decision(result["decision_id"])).certainty == 0.9

    async def test_the_node_is_declared(self, storage, embedder):
        node = await _fact(storage, embedder, "a claim")

        _, meta = await tools.rejudge(node.id, storage, because="x", confidence=0.3)

        assert [r.node_id for r in meta.retrieved] == [node.id]

    async def test_a_rejudgment_shows_up_in_review(self, storage, embedder):
        node = await _fact(storage, embedder, "a claim")
        await tools.rejudge(node.id, storage, because="x", confidence=0.3)

        result, _ = await tools.review(storage)

        assert [d["kind"] for d in result["decisions"]] == ["rejudgment"]
