"""The journal, on both backends (REVIEW_MODE.md §4, §10.5).

Attribution on the rows answers *who judged this node*. This table answers the
inverse — *what did this agent judge* — which over fields scattered across
facts, edges, episodes and value signals would be five scans and a reassembly.

Two properties are load-bearing and tested as such. **Append is the only
write**: a reversal, a confirmation and an overturn are all new rows, and the
absence of an update path is what makes that structural rather than a habit.
And **`reviewed` is derived** from a row pointing back, never stored as a flag —
the mutable-copy-in-two-places shape that #54, #55 and #56 all were.

Both backends, because a nullable field one store keeps and the other drops is
the divergence `tests/conftest.py` exists for — and here it would be invisible
until somebody asked who decided something months later.
"""

from datetime import datetime, timedelta, timezone

import pytest

from epimemer.core.types import DecisionKind, DecisionRecord, JudgeRef
from epimemer.storage.memory import InMemoryStorage
from epimemer.storage.protocol import StorageBackend
from epimemer.storage.surrealdb_adapter import SurrealDBStorage


CRITIC = JudgeRef(agent_id="critic", digest="d1")
# The same judge after a re-description: one agent, two description versions.
CRITIC_V2 = JudgeRef(agent_id="critic", digest="d2")
EDITOR = JudgeRef(agent_id="editor", digest="d3")

AT = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)


def _record(**kwargs) -> DecisionRecord:
    fields = {"kind": DecisionKind.INGEST, "subject_ids": ["n1"], "decided_at": AT}
    return DecisionRecord(**{**fields, **kwargs})


class TestOneRowSurvivesTheRoundTrip:
    async def test_every_field_comes_back(self, storage):
        record = _record(
            kind=DecisionKind.MERGE,
            subject_ids=["survivor", "a", "b"],
            judged_by=CRITIC,
            certainty=0.3,
            certainty_basis="the two sources disagree on the date",
            reviews="earlier-record",
            supersedes="earlier-record",
        )

        await storage.record_decision(record)

        assert await storage.get_decision(record.id) == record

    async def test_an_unknown_judge_comes_back_as_unknown(self, storage):
        """Not as an empty string, and not as a dropped row. Blank means
        unknown, and a graph that requires nobody still journals (§3.3.1)."""
        record = _record(judged_by=None)

        await storage.record_decision(record)

        stored = await storage.get_decision(record.id)
        assert stored is not None and stored.judged_by is None

    async def test_unrated_certainty_stays_unrated(self, storage):
        """Deliberately not a rated 0.5 — the #46 ladder's rule, and the reason
        `review()` orders in two tiers rather than one blended number (§6.2)."""
        record = _record()

        await storage.record_decision(record)

        assert (await storage.get_decision(record.id)).certainty is None

    async def test_an_unknown_id_is_none(self, storage):
        assert await storage.get_decision("never-written") is None


class TestWhatDidThisAgentJudge:
    async def test_rows_are_selected_by_the_agent_id(self, storage):
        mine = _record(judged_by=CRITIC)
        theirs = _record(judged_by=EDITOR)
        await storage.record_decision(mine)
        await storage.record_decision(theirs)

        found = await storage.query_decisions(agent_ids=["critic"])

        assert [r.id for r in found] == [mine.id]

    async def test_a_re_described_judge_is_still_the_same_judge(self, storage):
        """The id is assigned by the user and the digest pins the wording, so
        selecting on the pair would partition one agent by its own prose —
        which is exactly what hashing the description to get an id would have
        done (§2.1)."""
        first = _record(judged_by=CRITIC)
        second = _record(judged_by=CRITIC_V2)
        await storage.record_decision(first)
        await storage.record_decision(second)

        found = await storage.query_decisions(agent_ids=["critic"])

        assert {r.id for r in found} == {first.id, second.id}

    async def test_an_unattributed_row_matches_no_agent(self, storage):
        """Unknown is not an id, so it cannot be somebody's work."""
        await storage.record_decision(_record(judged_by=None))

        assert await storage.query_decisions(agent_ids=["critic"]) == []

    async def test_several_ids_are_one_judge(self, storage):
        """A judge is a **set** of keys once two records have been consolidated
        (#78): the survivor takes the other's key as a former id and no journal
        row is rewritten, so *this judge's decisions* is a query over the list.
        """
        early = _record(judged_by=JudgeRef(agent_id="Opus 5 Judge", digest="d1"))
        late = _record(judged_by=CRITIC)
        await storage.record_decision(early)
        await storage.record_decision(late)

        found = await storage.query_decisions(agent_ids=["critic", "Opus 5 Judge"])

        assert {r.id for r in found} == {early.id, late.id}

    async def test_no_ids_at_all_matches_nothing(self, storage):
        """Not everything. An empty list is a caller that named a judge with no
        keys, which is not the same as a caller that named no judge — and the
        two backends have to agree, because a `WHERE … IN []` and a Python `in`
        over an empty set are two implementations of one promise."""
        await storage.record_decision(_record(judged_by=CRITIC))

        assert await storage.query_decisions(agent_ids=[]) == []
        assert len(await storage.query_decisions()) == 1


class TestTheOtherThreeReads:
    async def test_rows_are_selected_by_kind(self, storage):
        merge = _record(kind=DecisionKind.MERGE)
        await storage.record_decision(merge)
        await storage.record_decision(_record(kind=DecisionKind.INGEST))

        found = await storage.query_decisions(kinds=[DecisionKind.MERGE])

        assert [r.id for r in found] == [merge.id]

    async def test_several_kinds_compose_into_one_query(self, storage):
        await storage.record_decision(_record(kind=DecisionKind.MERGE))
        await storage.record_decision(_record(kind=DecisionKind.CORRECTION))
        await storage.record_decision(_record(kind=DecisionKind.INGEST))

        found = await storage.query_decisions(
            kinds=[DecisionKind.MERGE, DecisionKind.CORRECTION]
        )

        assert len(found) == 2

    async def test_rows_are_selected_by_subject(self, storage):
        """*What has been decided about this node* — W&S §9's `node.notes`,
        derived rather than stored on the node (§9)."""
        about = _record(subject_ids=["target", "other"])
        await storage.record_decision(about)
        await storage.record_decision(_record(subject_ids=["elsewhere"]))

        found = await storage.query_decisions(subject_id="target")

        assert [r.id for r in found] == [about.id]

    async def test_rows_are_selected_by_what_they_review(self, storage):
        original = _record()
        confirmation = _record(reviews=original.id)
        await storage.record_decision(original)
        await storage.record_decision(confirmation)

        found = await storage.query_decisions(reviews=original.id)

        assert [r.id for r in found] == [confirmation.id]

    async def test_filters_compose(self, storage):
        """*Review what agent-1 did yesterday* is two of them, and a method per
        filter would leave the composition to the caller."""
        wanted = _record(kind=DecisionKind.MERGE, judged_by=CRITIC)
        await storage.record_decision(wanted)
        await storage.record_decision(_record(kind=DecisionKind.MERGE, judged_by=EDITOR))
        await storage.record_decision(_record(kind=DecisionKind.INGEST, judged_by=CRITIC))

        found = await storage.query_decisions(
            agent_ids=["critic"], kinds=[DecisionKind.MERGE]
        )

        assert [r.id for r in found] == [wanted.id]


class TestTheWindow:
    async def test_since_is_inclusive_and_until_exclusive(self, storage):
        """The half-open convention `query_changes` already uses, so adjacent
        windows neither overlap nor drop a row on the boundary."""
        on_the_hour = _record(decided_at=AT)
        later = _record(decided_at=AT + timedelta(hours=1))
        await storage.record_decision(on_the_hour)
        await storage.record_decision(later)

        assert {r.id for r in await storage.query_decisions(since=AT)} == {
            on_the_hour.id, later.id
        }
        assert [r.id for r in await storage.query_decisions(until=AT + timedelta(hours=1))] == [
            on_the_hour.id
        ]

    async def test_a_whole_second_is_compared_against_a_fractional_bound(self, storage):
        """The case string comparison gets wrong when one side omits its
        microseconds: `"…41Z"` sorts *after* `"…41.5Z"` because `Z > .`, which
        would put an earlier row past a later bound."""
        whole = _record(decided_at=datetime(2026, 8, 23, 12, 0, 41, tzinfo=timezone.utc))
        await storage.record_decision(whole)

        found = await storage.query_decisions(
            since=datetime(2026, 8, 23, 12, 0, 41, 500000, tzinfo=timezone.utc)
        )

        assert found == []

    async def test_a_window_selects_a_session(self, storage):
        yesterday = _record(decided_at=AT - timedelta(days=1))
        today = _record(decided_at=AT)
        await storage.record_decision(yesterday)
        await storage.record_decision(today)

        found = await storage.query_decisions(
            since=AT - timedelta(hours=1), until=AT + timedelta(hours=1)
        )

        assert [r.id for r in found] == [today.id]


class TestOrder:
    async def test_newest_first(self, storage):
        old = _record(decided_at=AT - timedelta(days=1))
        new = _record(decided_at=AT)
        await storage.record_decision(old)
        await storage.record_decision(new)

        assert [r.id for r in await storage.query_decisions()] == [new.id, old.id]

    async def test_a_batch_sharing_one_timestamp_is_ordered_the_same_way_by_both(
        self, storage
    ):
        """Rows written in one call share a timestamp to the microsecond. The
        tiebreak is arbitrary — the point is that it is the *same* arbitrary on
        both backends, or a parity test passes or fails on the clock."""
        records = [_record(decided_at=AT) for _ in range(5)]
        for record in records:
            await storage.record_decision(record)

        found = await storage.query_decisions()

        assert [r.id for r in found] == sorted(
            (r.id for r in records), reverse=True
        )

    async def test_limit_truncates_in_journal_order(self, storage):
        old = _record(decided_at=AT - timedelta(days=1))
        new = _record(decided_at=AT)
        await storage.record_decision(old)
        await storage.record_decision(new)

        assert [r.id for r in await storage.query_decisions(limit=1)] == [new.id]


class TestReviewedIsDerived:
    async def test_a_row_nothing_points_at_is_unreviewed(self, storage):
        original = _record()
        await storage.record_decision(original)

        assert await storage.reviewed_decision_ids([original.id]) == set()

    async def test_a_row_something_points_at_is_reviewed(self, storage):
        original = _record()
        await storage.record_decision(original)
        await storage.record_decision(_record(reviews=original.id))

        assert await storage.reviewed_decision_ids([original.id]) == {original.id}

    async def test_a_page_is_answered_in_one_query(self, storage):
        reviewed = _record()
        unreviewed = _record()
        await storage.record_decision(reviewed)
        await storage.record_decision(unreviewed)
        await storage.record_decision(_record(reviews=reviewed.id))

        answer = await storage.reviewed_decision_ids([reviewed.id, unreviewed.id])

        assert answer == {reviewed.id}

    async def test_asking_about_nothing_answers_nothing(self, storage):
        await storage.record_decision(_record(reviews="somewhere-else"))

        assert await storage.reviewed_decision_ids([]) == set()


class TestAppendIsTheOnlyWrite:
    """Stated in §4 and enforced by the surface rather than by discipline: a
    backend that offered an edit would let review state become mutable in one
    place and derived in another."""

    @pytest.mark.parametrize(
        "backend", [InMemoryStorage, SurrealDBStorage, StorageBackend]
    )
    def test_no_backend_can_change_or_remove_a_row(self, backend):
        forbidden = [
            name for name in dir(backend)
            if "decision" in name.lower()
            and name.split("_")[0] in {"update", "delete", "remove", "set", "edit"}
        ]
        assert forbidden == []

    async def test_two_rows_about_one_subject_both_survive(self, storage):
        """The shape a mutable flag would have collapsed: the decision and the
        review of it are two rows, and reading the second must not lose the
        first."""
        original = _record(judged_by=CRITIC)
        review = _record(judged_by=EDITOR, reviews=original.id)
        await storage.record_decision(original)
        await storage.record_decision(review)

        found = await storage.query_decisions(subject_id="n1")

        assert {r.id for r in found} == {original.id, review.id}


class TestTheJournalIsPerGraph:
    async def test_a_second_graph_starts_empty(self, storage):
        await storage.record_decision(_record())

        await storage.switch_database("elsewhere")

        assert await storage.query_decisions() == []

    async def test_switching_back_finds_the_rows(self, storage):
        record = _record()
        await storage.record_decision(record)
        first = storage.current_database

        await storage.switch_database("elsewhere")
        await storage.switch_database(first)

        assert [r.id for r in await storage.query_decisions()] == [record.id]
