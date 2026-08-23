"""Reading the journal back, shakiest first (REVIEW_MODE.md §5, §6, step 6).

Step 5 made *what did this agent judge* one query. This is what turns that query
into a review: an order that puts the calls most worth a second look at the top,
so a reviewer can stop reading when it stops repaying the attention.

**The rule these tests exist to protect is the one that looks like a detail.**
Declared doubt and derived doubt are two tiers that never mix, and tier 1 comes
first *even when a tier-2 row carries more signals* — because a blank
`certainty` means **unrated**, not doubtful (#46), and letting an unrated
decision outrank one an agent actually flagged re-commits the sin #46 fixed.

Nothing supplies a `certainty` yet, so the whole live corpus is tier 2. That is
why the derived half is what step 6 ships: it needs no attribution and works on
the graph as it stands. The tier-1 rows below are built by hand, which is the
only place they exist until step 7's `apply_review`.
"""

from datetime import datetime, timedelta, timezone

import pytest

from epimemer.core.types import (
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
from epimemer.pipelines.review.difficulty import (
    DifficultySignal,
    ScoredDecision,
    difficulty_signals,
    merge_source_count,
    review_order,
)


CRITIC = JudgeRef(agent_id="critic", digest="d1")
NOON = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def embedder():
    return MockEmbeddingProvider(model_id="mock-embed", dimension=8)


@pytest.fixture
def config():
    return ServerConfig(storage_backend="memory", embedding_provider="mock")


def _fact(content: str, **kwargs) -> Fact:
    return Fact(content=content, source_id="seg1", **kwargs)


def _row(kind: DecisionKind, subjects: list[str], **kwargs) -> DecisionRecord:
    return DecisionRecord(kind=kind, subject_ids=subjects, decided_at=NOON, **kwargs)


def _by_id(*nodes) -> dict:
    return {node.id: node for node in nodes}


class TestTheDerivedSignals:
    """§5's four, each computed from what the decision touched rather than from
    anything the deciding agent said about it."""

    def test_a_thin_source_is_below_the_rung_not_at_it(self):
        thin = _fact("a shaky claim", value=ValueSignal(confidence=0.3))
        ordinary = _fact("a plain claim", value=ValueSignal(confidence=0.5))
        record = _row(DecisionKind.INGEST, [thin.id, ordinary.id])

        assert DifficultySignal.THIN_SOURCE in difficulty_signals(
            record, _by_id(thin, ordinary)
        )
        assert DifficultySignal.THIN_SOURCE not in difficulty_signals(
            _row(DecisionKind.INGEST, [ordinary.id]), _by_id(ordinary)
        )

    def test_unrated_confidence_is_not_a_signal(self):
        """The majority state, and §5's own correction to its first draft:
        absence is *the ordinary case* on the #46 ladder, so reading it as
        thinness floods the list with ordinary decisions."""
        unrated = _fact("a plain claim")
        assert unrated.value.confidence is None

        assert difficulty_signals(
            _row(DecisionKind.INGEST, [unrated.id]), _by_id(unrated)
        ) == []

    def test_the_merge_source_count_skips_the_survivor(self):
        """`merge_facts` journals `[survivor, *sources]`, so reading the subject
        count as the source count calls every two-source merge wide."""
        assert merge_source_count(_row(DecisionKind.MERGE, ["s", "a", "b"])) == 2
        assert merge_source_count(_row(DecisionKind.MERGE, ["s", "a", "b", "c"])) == 3

    def test_a_wide_merge_is_three_sources(self):
        nodes = [_fact(f"claim {i}") for i in range(5)]
        subjects = _by_id(*nodes)
        ids = [n.id for n in nodes]

        narrow = _row(DecisionKind.MERGE, ids[:3])       # survivor + two
        wide = _row(DecisionKind.MERGE, ids[:4])         # survivor + three

        assert DifficultySignal.WIDE_MERGE not in difficulty_signals(narrow, subjects)
        assert DifficultySignal.WIDE_MERGE in difficulty_signals(wide, subjects)

    def test_a_contradiction_with_both_sides_active_is_open(self):
        a, b = _fact("the deploy failed"), _fact("the deploy succeeded")
        record = _row(DecisionKind.CONTRADICTION, [a.id, b.id])

        assert DifficultySignal.OPEN_CONTRADICTION in difficulty_signals(
            record, _by_id(a, b)
        )

    def test_a_contradiction_one_side_of_which_was_retired_is_not(self):
        """Something was done about it. Which is all this can tell — whether the
        retirement *resolved* the conflict is a judgment, not an observation."""
        a = _fact("the deploy failed")
        b = _fact("the deploy succeeded", status=NodeStatus.CORRECTED)
        record = _row(DecisionKind.CONTRADICTION, [a.id, b.id])

        assert DifficultySignal.OPEN_CONTRADICTION not in difficulty_signals(
            record, _by_id(a, b)
        )

    def test_ground_moved_means_retired_after_the_decision(self):
        later = _fact("the population is 500,000", superseded_at=NOON + timedelta(days=1))
        record = _row(DecisionKind.INGEST, [later.id])

        assert DifficultySignal.GROUND_MOVED in difficulty_signals(
            record, _by_id(later)
        )

    def test_a_decision_that_did_the_retiring_has_not_moved_under_itself(self):
        """A correction, a merge and an archival sweep all journal once the
        write has landed, so their own subjects carry a retirement instant just
        *before* the row's. Reading that as difficulty would flag every one."""
        retired = _fact("the old figure", superseded_at=NOON - timedelta(seconds=1))
        record = _row(DecisionKind.CORRECTION, [retired.id])

        assert DifficultySignal.GROUND_MOVED not in difficulty_signals(
            record, _by_id(retired)
        )

    def test_a_naive_timestamp_is_compared_rather_than_raising(self):
        """Backends round-trip datetimes here rather than text, so this is not
        #70 — but one side coming back naive would raise instead of answering,
        and a review that dies on one row answers nothing."""
        naive = _fact("a claim", superseded_at=datetime(2026, 8, 24, 12, 0))
        record = _row(DecisionKind.INGEST, [naive.id])

        assert DifficultySignal.GROUND_MOVED in difficulty_signals(
            record, _by_id(naive)
        )

    def test_a_subject_that_is_gone_contributes_nothing(self):
        """A reversal destroys its survivor, and a row can be read beside a
        graph it was not written in (#72). Ranking by the reader's position
        rather than by the decision would be worse than ranking it low."""
        present = _fact("a claim")
        record = _row(DecisionKind.REVERSAL, [present.id, "a-node-that-is-gone"])

        assert difficulty_signals(record, _by_id(present)) == []


class TestTheOrderIsTwoTiersThatNeverMix:
    def test_a_flagged_decision_outranks_an_unrated_one_with_more_signals(self):
        """The load-bearing rule. Absence is not a claim of doubt, so no amount
        of derived evidence lets an unrated row overtake a declared one."""
        flagged = ScoredDecision(
            record=_row(DecisionKind.MERGE, ["a"], certainty=0.9), signals=[]
        )
        shaky = ScoredDecision(
            record=_row(DecisionKind.MERGE, ["b"]),
            signals=[
                DifficultySignal.THIN_SOURCE,
                DifficultySignal.WIDE_MERGE,
                DifficultySignal.GROUND_MOVED,
            ],
        )

        assert review_order([shaky, flagged]) == [flagged, shaky]

    def test_tier_one_runs_least_certain_first(self):
        low = ScoredDecision(record=_row(DecisionKind.MERGE, ["a"], certainty=0.3),
                             signals=[])
        high = ScoredDecision(record=_row(DecisionKind.MERGE, ["b"], certainty=0.9),
                              signals=[])

        assert review_order([high, low]) == [low, high]

    def test_tier_two_runs_most_signals_first(self):
        one = ScoredDecision(record=_row(DecisionKind.INGEST, ["a"]),
                             signals=[DifficultySignal.THIN_SOURCE])
        two = ScoredDecision(
            record=_row(DecisionKind.INGEST, ["b"]),
            signals=[DifficultySignal.THIN_SOURCE, DifficultySignal.GROUND_MOVED],
        )

        assert review_order([one, two]) == [two, one]

    def test_the_same_graph_answers_the_same_way_twice(self):
        """A page that reshuffled between two identical calls would make
        `truncated` name a different set each time."""
        rows = [
            ScoredDecision(record=_row(DecisionKind.INGEST, [f"n{i}"]), signals=[])
            for i in range(6)
        ]

        assert [d.record.id for d in review_order(rows)] == [
            d.record.id for d in review_order(list(reversed(rows)))
        ]


class TestTheToolOverARealGraph:
    async def test_it_puts_the_shaky_decision_first(
        self, storage, embedder, config
    ):
        thin = _fact("a shaky claim", value=ValueSignal(confidence=0.3),
                     claim_kind=ClaimKind.STATE)
        plain = _fact("a plain claim", claim_kind=ClaimKind.STATE)
        for node in (thin, plain):
            await storage.store_node(node)
            vectors = await embedder.embed([node.content])
            await storage.store_embedding(EmbeddingRecord(
                item_id=node.id, model_id=embedder.model_id, vector=vectors[0],
            ))

        await storage.record_decision(_row(DecisionKind.INGEST, [plain.id]))
        await storage.record_decision(_row(DecisionKind.INGEST, [thin.id]))

        result, meta = await tools.review(storage)

        assert [d["subjects"][0]["id"] for d in result["decisions"]] == [
            thin.id, plain.id,
        ]
        assert result["decisions"][0]["difficulty_signals"] == ["thin_source"]
        assert meta.nodes_returned == 2

    async def test_it_names_the_graph_it_answered_from(self, storage):
        """#72: the journal is per graph, so an answer that does not say which
        reads as the whole story."""
        result, _ = await tools.review(storage)

        assert result["graph"] == storage.current_database

    async def test_the_counts_are_over_everything_scanned(self, storage):
        """Three shaky rows out of four hundred unrated is not the same answer
        as three out of four, and a count over the page cannot tell them
        apart."""
        for i in range(5):
            await storage.record_decision(_row(DecisionKind.INGEST, [f"n{i}"]))
        await storage.record_decision(
            _row(DecisionKind.INGEST, ["n5"], certainty=0.3, judged_by=CRITIC)
        )

        result, _ = await tools.review(storage, max_results=2)

        assert len(result["decisions"]) == 2
        assert result["decisions_scanned"] == 6
        assert result["unrated_count"] == 5
        assert result["unattributed_count"] == 5

    async def test_a_cut_list_says_so(self, storage):
        for i in range(4):
            await storage.record_decision(_row(DecisionKind.INGEST, [f"n{i}"]))

        cut, _ = await tools.review(storage, max_results=2)
        whole, _ = await tools.review(storage, max_results=10)

        assert cut["truncated"] is True
        assert whole["truncated"] is False

    async def test_it_reports_whether_anyone_has_checked_a_row(self, storage):
        """Derived from a row pointing back rather than stored (§3.4) — the
        property step 7's `unreviewed` mode filters on."""
        original = _row(DecisionKind.MERGE, ["a", "b"])
        await storage.record_decision(original)
        await storage.record_decision(
            _row(DecisionKind.REVERSAL, ["a"], reviews=original.id)
        )

        result, _ = await tools.review(storage)
        by_id = {d["decision_id"]: d for d in result["decisions"]}

        assert by_id[original.id]["reviewed"] is True

    async def test_a_subject_that_is_gone_comes_back_named(self, storage):
        """A null preview is information — the merge survivor a reversal
        destroyed — so the id stays rather than the row losing its subject."""
        await storage.record_decision(_row(DecisionKind.REVERSAL, ["gone"]))

        result, _ = await tools.review(storage)
        subject = result["decisions"][0]["subjects"][0]

        assert subject == {"id": "gone", "content_preview": None, "status": None}

    async def test_an_unbuilt_mode_is_refused_by_name(self, storage):
        """A mode the list admitted but nothing implemented would be a filter
        that silently returned everything."""
        result, _ = await tools.review(storage, mode="by_agent")

        assert "by_agent" in result["refused"]
        assert result["modes"] == ["all"]
        assert "decisions" not in result

    async def test_it_writes_nothing(self, storage, embedder, config):
        """Read-only, like `reflect`, and for the same reason: it nominates, and
        every change goes through the decision tools that already exist."""
        node = _fact("a claim")
        await storage.store_node(node)
        await storage.record_decision(_row(DecisionKind.INGEST, [node.id]))
        before = await storage.query_decisions()

        await tools.review(storage)

        assert await storage.query_decisions() == before
        assert (await storage.get_node(node.id)).model_dump() == node.model_dump()


class TestItWorksOnTheCorpusAsItStands:
    """Step 6 ships before anything supplies a `certainty` and before most rows
    have a judge, which is the point rather than a caveat."""

    async def test_a_journal_written_by_the_ordinary_tools_orders_usefully(
        self, storage, embedder, config
    ):
        seg, _ = await tools.segment_text(
            "The deploy failed.\n\nThe deploy succeeded.",
            storage, embedder, config,
        )
        await tools.store_decomposition(
            document_id=seg["document_id"],
            segments=[{
                "segment_id": s["segment_id"],
                "topics": [],
                "facts": [{
                    "content": f"The deploy {outcome}",
                    "claim_kind": "event",
                    "confidence": 0.3,
                }],
                "inferences": [],
            } for s, outcome in zip(seg["segments"], ("failed", "succeeded"))],
            storage=storage,
            embedding_provider=embedder,
        )
        facts = await storage.query_nodes(node_type=None)
        pair = [n.id for n in facts if n.content.startswith("The deploy")]
        assert len(pair) == 2
        await tools.record_contradiction(pair[0], pair[1], storage)

        result, _ = await tools.review(storage)

        kinds = [d["kind"] for d in result["decisions"]]
        assert set(kinds) == {"ingest", "contradiction"}
        assert result["unattributed_count"] == len(result["decisions"]), (
            "no judge was claimed, and the review still answers"
        )
        assert any(d["difficulty_signals"] for d in result["decisions"])

    async def test_an_empty_journal_is_an_answer_rather_than_an_error(
        self, storage
    ):
        result, meta = await tools.review(storage)

        assert result["decisions"] == []
        assert result["decisions_scanned"] == 0
        assert result["truncated"] is False
        assert meta.nodes_returned == 0
