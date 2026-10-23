"""*Not now, ask again later*: the third answer to a never-retrieved nominee.

A `never_retrieved` nominee had two answers, and neither fitted the common case.
`archivals` sets the node aside, which wants the user's approval. `retained`
keeps it for good, because the nomination names no reason and so nothing that
happens later can be a reason the keep failed to cover. A node not worth the
user's attention today, and perhaps worth it once the graph has grown, either
vanished for ever or came back on every reflect.

What is pinned here:

- **The rule**, without a store: the wait doubles with each deferral up to the
  cap, a node is due once its wait has passed, and a round counter that is
  behind the deferral shows the node rather than hiding it.
- **The read-back**: deferrals count up, and a keep or a one-subject reopen
  clears them.
- **The nominator**: a deferred node is left out while it waits, comes back
  first in its class with what was said, and stops being a nominee for the
  reasons any never-retrieved node does.
- **The tool**: what it accepts, what it refuses and why, the two collision
  rules, and the round it stamps.
- **`reopen`** withdraws a deferral as it withdraws a keep.

Every backend, through the `storage` fixture, except the pure rule.
"""

import pytest

from epimemer.core.types import (
    BASE_METACONTEXT_ID,
    ClaimKind,
    DecisionKind,
    DecisionRecord,
    EdgeType,
    Fact,
    Inference,
    JudgeRef,
    NodeEdge,
    NodeStatus,
    ValueSignal,
)
from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.mcp import tools
from epimemer.pipelines.reflection.archival import (
    candidate_payload,
    never_retrieved_exclusion,
    nominate_archival_candidates,
)
from epimemer.pipelines.reflection.deferral import (
    DeferralState,
    deferral_due,
    deferral_wait,
    deferrals_for,
    record_deferral,
)
from epimemer.pipelines.reflection.retention import confirmed_reasons_for, record_retention
from epimemer.storage.protocol import (
    DeferralOverrides,
    DeferralSettings,
    resolve_deferral_settings,
)

CRITIC = JudgeRef(agent_id="critic", digest="d1")
EDITOR = JudgeRef(agent_id="editor", digest="d2")
DEFAULTS = DeferralSettings(first_wait=2, cap=32)


@pytest.fixture
def embedding_provider() -> MockEmbeddingProvider:
    return MockEmbeddingProvider(model_id="mock-embed", dimension=8)


async def _never_retrieved(storage, content: str = "The parish kept two bridges.", **value):
    """A fact no search has returned, nothing points at, and nobody judged up."""
    node = Fact(
        content=content,
        source_id="seg-1",
        claim_kind=ClaimKind.STATE,
        value=ValueSignal(importance=value.pop("importance", 0.2), **value),
    )
    await storage.store_node(node)
    await storage.store_edge(
        NodeEdge(src_id=node.id, dst_id=BASE_METACONTEXT_ID, type=EdgeType.HAS_METACONTEXT)
    )
    return node


async def _advance(storage, rounds: int) -> None:
    """Age the graph by `rounds` reflects that each counted as a round."""
    await storage.set_reflect_round(await storage.get_reflect_round() + rounds)


async def _defer(storage, node_id: str, because: str = "Not yet.", judge=CRITIC) -> None:
    await record_deferral(
        storage,
        node_id=node_id,
        because=because,
        at_round=await storage.get_reflect_round(),
        judge=judge,
    )


async def _nominated(storage, **kwargs) -> list:
    return [c for c in await nominate_archival_candidates(storage, **kwargs)]


async def _nominated_ids(storage, **kwargs) -> list[str]:
    return [c.node_id for c in await _nominated(storage, **kwargs)]


async def _deferral_rows(storage) -> list[DecisionRecord]:
    return list(await storage.query_decisions(kinds=[DecisionKind.DEFERRAL]))


def _state(count: int, last_round: int) -> DeferralState:
    from datetime import UTC, datetime

    return DeferralState(
        count=count, last_round=last_round, last_at=datetime.now(UTC), because="Not yet."
    )


# --- The rule, without a store -----------------------------------------------


class TestTheWaitDoublesUpToTheCap:
    def test_the_first_deferral_waits_the_first_wait(self):
        assert deferral_wait(1, DEFAULTS) == 2

    def test_each_further_deferral_doubles_it(self):
        assert [deferral_wait(n, DEFAULTS) for n in range(1, 8)] == [2, 4, 8, 16, 32, 32, 32]

    def test_the_cap_holds_where_doubling_overshoots_it(self):
        settings = DeferralSettings(first_wait=3, cap=10)
        assert [deferral_wait(n, settings) for n in range(1, 5)] == [3, 6, 10, 10]

    def test_a_hundredth_deferral_still_waits_the_cap(self):
        assert deferral_wait(100, DEFAULTS) == 32

    def test_a_count_below_one_reads_as_the_first(self):
        assert deferral_wait(0, DEFAULTS) == 2


class TestWhenANodeIsDueAgain:
    def test_a_node_nobody_deferred_is_due(self):
        assert deferral_due(None, 0, DEFAULTS)

    def test_a_node_waits_until_its_wait_has_passed(self):
        state = _state(count=1, last_round=5)

        assert not deferral_due(state, 5, DEFAULTS)
        assert not deferral_due(state, 6, DEFAULTS)
        assert deferral_due(state, 7, DEFAULTS)
        assert deferral_due(state, 30, DEFAULTS)

    def test_a_second_deferral_waits_twice_as_long(self):
        state = _state(count=2, last_round=5)

        assert not deferral_due(state, 8, DEFAULTS)
        assert deferral_due(state, 9, DEFAULTS)

    def test_the_cap_bounds_how_long_any_node_is_hidden(self):
        state = _state(count=20, last_round=10)

        assert not deferral_due(state, 41, DEFAULTS)
        assert deferral_due(state, 42, DEFAULTS)

    def test_a_round_behind_the_deferral_shows_the_node(self):
        """A graph whose round came back from an older bundle cannot measure the
        wait, and hiding the node for a wait nobody can measure is the worse
        failure."""
        state = _state(count=3, last_round=12)

        assert deferral_due(state, 4, DEFAULTS)


class TestTheSettingsResolve:
    def test_no_override_is_the_process_default(self):
        assert resolve_deferral_settings(DeferralOverrides(), DEFAULTS) == DEFAULTS
        assert resolve_deferral_settings(None, DEFAULTS) == DEFAULTS

    def test_an_override_wins_field_by_field(self):
        resolved = resolve_deferral_settings(DeferralOverrides(cap=8), DEFAULTS)

        assert resolved == DeferralSettings(first_wait=2, cap=8)

    def test_a_cleared_field_follows_the_default_at_the_time(self):
        later = DeferralSettings(first_wait=5, cap=40)

        assert resolve_deferral_settings(DeferralOverrides(cap=8), later).first_wait == 5


# --- Reading the journal back ------------------------------------------------


class TestTheJournalIsReplayedOldestFirst:
    async def test_a_node_nobody_deferred_is_absent(self, storage):
        node = await _never_retrieved(storage)

        assert await deferrals_for([node.id], storage) == {}

    async def test_each_deferral_counts_and_the_latest_is_described(self, storage):
        node = await _never_retrieved(storage)
        await storage.set_reflect_round(3)
        await _defer(storage, node.id, because="First look.", judge=CRITIC)
        await _advance(storage, 2)
        await _defer(storage, node.id, because="Second look.", judge=EDITOR)

        state = (await deferrals_for([node.id], storage))[node.id]

        assert (state.count, state.last_round) == (2, 5)
        assert (state.because, state.judged_by) == ("Second look.", "editor")

    async def test_a_keep_clears_it(self, storage):
        node = await _never_retrieved(storage)
        await _defer(storage, node.id)

        await record_retention(storage, node_id=node.id, because="Kept.", reasons=[])

        assert await deferrals_for([node.id], storage) == {}

    async def test_a_reopen_clears_it_and_the_next_deferral_starts_again(self, storage):
        node = await _never_retrieved(storage)
        await _defer(storage, node.id)
        await _defer(storage, node.id)
        await tools.reopen(storage, node_ids=[node.id], reason="Ask now.", judge=EDITOR)

        assert await deferrals_for([node.id], storage) == {}

        await _defer(storage, node.id)
        assert (await deferrals_for([node.id], storage))[node.id].count == 1

    async def test_a_reopen_naming_two_subjects_is_not_about_the_node(self, storage):
        node = await _never_retrieved(storage)
        await _defer(storage, node.id)
        await storage.record_decision(
            DecisionRecord(kind=DecisionKind.REOPENED, subject_ids=[node.id, "other"])
        )

        assert (await deferrals_for([node.id], storage))[node.id].count == 1

    async def test_the_keep_index_does_not_read_a_deferral(self, storage):
        """`confirmed_reasons_for` keeps its own meaning: a deferral is not a keep."""
        node = await _never_retrieved(storage)
        await _defer(storage, node.id)

        assert await confirmed_reasons_for([node.id], storage) == {}


# --- The nominator -----------------------------------------------------------


class TestADeferredNodeWaits:
    async def test_it_is_left_out_while_it_waits(self, storage):
        node = await _never_retrieved(storage)
        assert node.id in await _nominated_ids(storage)
        await _defer(storage, node.id)

        assert node.id not in await _nominated_ids(storage)
        await _advance(storage, 1)
        assert node.id not in await _nominated_ids(storage)

    async def test_it_comes_back_once_the_wait_is_over_with_what_was_said(self, storage):
        node = await _never_retrieved(storage)
        await storage.set_reflect_round(4)
        await _defer(storage, node.id, because="The release notes may cite it.")
        await _advance(storage, 3)

        [candidate] = [c for c in await _nominated(storage) if c.node_id == node.id]

        assert candidate.reason == "never_retrieved"
        assert candidate.deferred is not None
        assert candidate.deferred.model_dump() == {
            "count": 1,
            "last_round": 4,
            "rounds_waited": 3,
            "because": "The release notes may cite it.",
            "judged_by": "critic",
            "next_wait": 4,
        }

    async def test_a_node_nobody_deferred_carries_no_deferred_key(self, storage):
        await _never_retrieved(storage)

        [candidate] = await _nominated(storage)

        assert "deferred" not in candidate_payload(candidate)

    async def test_the_payload_of_a_deferred_node_carries_the_block(self, storage):
        node = await _never_retrieved(storage)
        await _defer(storage, node.id)
        await _advance(storage, 2)

        [candidate] = await _nominated(storage)

        assert candidate_payload(candidate)["deferred"]["count"] == 1

    async def test_a_node_due_again_leads_its_class(self, storage):
        others = [await _never_retrieved(storage, f"Bridge number {n}.") for n in range(3)]
        deferred = await _never_retrieved(storage, "The parish kept a ferry.")
        await _defer(storage, deferred.id)
        await _advance(storage, 2)

        nominated = await _nominated_ids(storage)

        assert nominated[0] == deferred.id
        assert set(nominated[1:]) == {node.id for node in others}

    async def test_the_limit_cannot_push_it_out(self, storage):
        for n in range(5):
            await _never_retrieved(storage, f"Bridge number {n}.")
        deferred = await _never_retrieved(storage, "The parish kept a ferry.")
        await _defer(storage, deferred.id)
        await _advance(storage, 2)

        assert await _nominated_ids(storage, limit=1) == [deferred.id]

    async def test_the_graphs_own_settings_set_the_wait(self, storage):
        node = await _never_retrieved(storage)
        await storage.set_deferral_overrides(DeferralOverrides(first_wait=1))
        await _defer(storage, node.id)
        await _advance(storage, 1)

        assert node.id in await _nominated_ids(storage)

    async def test_the_process_default_reaches_the_nominator(self, storage):
        node = await _never_retrieved(storage)
        await _defer(storage, node.id)
        await _advance(storage, 2)

        slower = DeferralSettings(first_wait=5, cap=5)
        assert node.id not in await _nominated_ids(storage, deferral_defaults=slower)
        assert node.id in await _nominated_ids(storage)


class TestADeferralGoesMootWithTheNomination:
    """A deferral only answers the never-retrieved nomination, so whatever ends
    that nomination ends the deferral's effect with it."""

    async def test_once_a_search_has_returned_the_node(self, storage):
        from datetime import UTC, datetime

        node = await _never_retrieved(storage)
        await _defer(storage, node.id)
        await storage.store_node(
            node.model_copy(
                update={"value": node.value.model_copy(update={"retrieved_at": datetime.now(UTC)})}
            )
        )
        await _advance(storage, 10)

        assert node.id not in await _nominated_ids(storage)

    async def test_once_something_links_to_it(self, storage):
        node = await _never_retrieved(storage)
        other = await _never_retrieved(storage, "The ferry ran on Sundays.")
        await _defer(storage, node.id)
        await storage.store_edge(NodeEdge(src_id=other.id, dst_id=node.id, type=EdgeType.SUPPORTS))
        await _advance(storage, 10)

        assert node.id not in await _nominated_ids(storage)

    async def test_once_it_is_judged_above_the_ceiling(self, storage):
        node = await _never_retrieved(storage)
        await _defer(storage, node.id)
        await storage.store_node(
            node.model_copy(update={"value": node.value.model_copy(update={"importance": 0.8})})
        )
        await _advance(storage, 10)

        assert node.id not in await _nominated_ids(storage)


class TestOneTestForBothCallers:
    """`never_retrieved_exclusion` is the nominator's test and the tool's, and
    its answer says why a node is not a nominee."""

    def _ask(self, node, **overrides):
        inputs = {
            "in_degree": 0,
            "retained": {},
            "deferral": None,
            "current_round": 0,
            "settings": DEFAULTS,
            **overrides,
        }
        return never_retrieved_exclusion(node, **inputs)

    def test_a_nominee_has_no_exclusion(self):
        assert self._ask(Fact(content="x", source_id="s")) is None

    def test_each_reason_is_named(self):
        from datetime import UTC, datetime

        fact = Fact(content="x", source_id="s")
        assert "inference" in self._ask(Inference(content="x", source_id="s"))
        assert "ceiling" in self._ask(
            fact.model_copy(update={"value": ValueSignal(importance=0.9)})
        )
        retrieved = fact.model_copy(update={"value": ValueSignal(retrieved_at=datetime.now(UTC))})
        assert "search" in self._ask(retrieved)
        assert "links" in self._ask(fact, in_degree=1)
        assert "retained" in self._ask(fact, retained={fact.id: {fact.id}})
        waiting = self._ask(fact, deferral=_state(count=1, last_round=0), current_round=1)
        assert "1 more" in waiting


# --- The tool ----------------------------------------------------------------


class TestTheToolRecordsADeferral:
    async def test_it_is_recorded_stamped_and_credited(self, storage, embedding_provider):
        node = await _never_retrieved(storage)
        await storage.set_reflect_round(9)

        result, _ = await tools.apply_reflection(
            storage,
            embedding_provider,
            deferred=[{"node_id": node.id, "because": "Ask once the docs land."}],
            judge=CRITIC,
        )

        assert result["deferrals_recorded"] == [
            {"node_id": node.id, "count": 1, "due_after_rounds": 2}
        ]
        assert result["deferred_skipped"] == []
        [row] = await _deferral_rows(storage)
        assert row.subject_ids == [node.id]
        assert row.at_round == 9
        assert row.certainty_basis == "Ask once the docs land."
        assert row.judged_by.agent_id == "critic"

    async def test_a_second_deferral_counts_two_and_waits_longer(self, storage, embedding_provider):
        node = await _never_retrieved(storage)
        entry = {"node_id": node.id, "because": "Not yet."}
        await tools.apply_reflection(storage, embedding_provider, deferred=[entry])
        await _advance(storage, 2)

        result, _ = await tools.apply_reflection(storage, embedding_provider, deferred=[entry])

        assert result["deferrals_recorded"] == [
            {"node_id": node.id, "count": 2, "due_after_rounds": 4}
        ]

    async def test_the_graphs_settings_set_the_reported_wait(self, storage, embedding_provider):
        node = await _never_retrieved(storage)
        await storage.set_deferral_overrides(DeferralOverrides(first_wait=3))

        result, _ = await tools.apply_reflection(
            storage, embedding_provider, deferred=[{"node_id": node.id, "because": "Later."}]
        )

        assert result["deferrals_recorded"][0]["due_after_rounds"] == 3

    async def test_both_lists_are_always_in_the_response(self, storage, embedding_provider):
        result, _ = await tools.apply_reflection(storage, embedding_provider)

        assert result["deferrals_recorded"] == []
        assert result["deferred_skipped"] == []

    @pytest.mark.parametrize("because", [None, "", "   "])
    async def test_because_is_required(self, storage, embedding_provider, because):
        node = await _never_retrieved(storage)
        entry = (
            {"node_id": node.id} if because is None else {"node_id": node.id, "because": because}
        )

        result, _ = await tools.apply_reflection(storage, embedding_provider, deferred=[entry])

        assert result["deferrals_recorded"] == []
        assert [row["node_id"] for row in result["deferred_skipped"]] == [node.id]
        assert "`because` is required" in result["deferred_skipped"][0]["why"]
        assert await _deferral_rows(storage) == []

    async def test_a_node_named_twice_is_deferred_once(self, storage, embedding_provider):
        node = await _never_retrieved(storage)
        entry = {"node_id": node.id, "because": "Not yet."}

        result, _ = await tools.apply_reflection(
            storage, embedding_provider, deferred=[entry, entry]
        )

        assert len(result["deferrals_recorded"]) == 1
        assert "twice" in result["deferred_skipped"][0]["why"]
        assert len(await _deferral_rows(storage)) == 1

    @pytest.mark.parametrize("entry", ["not-an-object", {"because": "no id"}, {"node_id": 7}])
    async def test_a_malformed_entry_is_skipped(self, storage, embedding_provider, entry):
        result, _ = await tools.apply_reflection(storage, embedding_provider, deferred=[entry])

        assert result["deferrals_recorded"] == []
        assert len(result["deferred_skipped"]) == 1

    async def test_a_store_failure_skips_the_node_and_says_so(self, storage, embedding_provider):
        node = await _never_retrieved(storage)

        async def _refuse(record):
            raise RuntimeError("the journal is unavailable")

        storage.record_decision = _refuse

        result, _ = await tools.apply_reflection(
            storage, embedding_provider, deferred=[{"node_id": node.id, "because": "Later."}]
        )

        assert result["deferrals_recorded"] == []
        assert "not stored" in result["deferred_skipped"][0]["why"]


class TestTheToolRefusesWhatIsNotANominee:
    async def _refusal(self, storage, embedding_provider, node_id: str) -> str:
        result, _ = await tools.apply_reflection(
            storage, embedding_provider, deferred=[{"node_id": node_id, "because": "Later."}]
        )
        assert result["deferrals_recorded"] == []
        [skipped] = result["deferred_skipped"]
        assert skipped["node_id"] == node_id
        assert await _deferral_rows(storage) == []
        return skipped["why"]

    async def test_an_unknown_node(self, storage, embedding_provider):
        assert "unknown" in await self._refusal(storage, embedding_provider, "no-such-node")

    async def test_a_node_a_search_has_returned(self, storage, embedding_provider):
        from datetime import UTC, datetime

        node = await _never_retrieved(storage, retrieved_at=datetime.now(UTC))

        why = await self._refusal(storage, embedding_provider, node.id)

        assert "not a never_retrieved nominee" in why
        assert "search" in why

    async def test_an_inference(self, storage, embedding_provider):
        inference = Inference(content="The parish was prosperous.", source_id="seg-1")
        await storage.store_node(inference)

        assert "inference" in await self._refusal(storage, embedding_provider, inference.id)

    async def test_a_node_something_links_to(self, storage, embedding_provider):
        node = await _never_retrieved(storage)
        other = await _never_retrieved(storage, "The ferry ran on Sundays.")
        await storage.store_edge(NodeEdge(src_id=other.id, dst_id=node.id, type=EdgeType.SUPPORTS))

        assert "links" in await self._refusal(storage, embedding_provider, node.id)

    async def test_a_node_a_keep_already_holds(self, storage, embedding_provider):
        node = await _never_retrieved(storage)
        await record_retention(storage, node_id=node.id, because="Kept.", reasons=[])

        assert "retained" in await self._refusal(storage, embedding_provider, node.id)

    async def test_a_node_still_waiting(self, storage, embedding_provider):
        node = await _never_retrieved(storage)
        await _defer(storage, node.id)
        rows_before = len(await _deferral_rows(storage))

        result, _ = await tools.apply_reflection(
            storage, embedding_provider, deferred=[{"node_id": node.id, "because": "Again."}]
        )

        assert result["deferrals_recorded"] == []
        assert "already deferred" in result["deferred_skipped"][0]["why"]
        assert len(await _deferral_rows(storage)) == rows_before


class TestTheTwoCollisions:
    async def test_archival_wins_over_a_deferral(self, storage, embedding_provider):
        node = await _never_retrieved(storage)

        result, _ = await tools.apply_reflection(
            storage,
            embedding_provider,
            archivals=[node.id],
            deferred=[{"node_id": node.id, "because": "Later."}],
        )

        assert result["nodes_archived"] == 1
        assert result["deferrals_recorded"] == []
        assert "archivals" in result["deferred_skipped"][0]["why"]
        assert await _deferral_rows(storage) == []

    async def test_a_keep_and_a_deferral_are_refused_in_both(self, storage, embedding_provider):
        node = await _never_retrieved(storage)

        result, _ = await tools.apply_reflection(
            storage,
            embedding_provider,
            retained=[{"node_id": node.id, "because": "Keep it."}],
            deferred=[{"node_id": node.id, "because": "Later."}],
        )

        assert result["retentions_recorded"] == 0
        assert result["deferrals_recorded"] == []
        assert "`deferred`" in result["retained_skipped"][0]["why"]
        assert "`retained`" in result["deferred_skipped"][0]["why"]
        assert await confirmed_reasons_for([node.id], storage) == {}
        assert await _deferral_rows(storage) == []

    async def test_other_nodes_in_the_batch_still_land(self, storage, embedding_provider):
        clash = await _never_retrieved(storage)
        kept = await _never_retrieved(storage, "The ferry ran on Sundays.")
        later = await _never_retrieved(storage, "The mill closed in winter.")

        result, _ = await tools.apply_reflection(
            storage,
            embedding_provider,
            retained=[
                {"node_id": clash.id, "because": "Keep it."},
                {"node_id": kept.id, "because": "Keep it."},
            ],
            deferred=[
                {"node_id": clash.id, "because": "Later."},
                {"node_id": later.id, "because": "Later."},
            ],
        )

        assert result["retentions_recorded"] == 1
        assert [entry["node_id"] for entry in result["deferrals_recorded"]] == [later.id]


# --- Reopen ------------------------------------------------------------------


class TestReopenWithdrawsADeferral:
    async def test_the_node_is_offered_again_at_once(self, storage):
        node = await _never_retrieved(storage)
        await _defer(storage, node.id)
        assert node.id not in await _nominated_ids(storage)

        result, _ = await tools.reopen(
            storage, node_ids=[node.id], reason="The docs have landed.", judge=EDITOR
        )

        assert result["reopened"] is True
        assert result["layer"] == "node"
        [candidate] = [c for c in await _nominated(storage) if c.node_id == node.id]
        assert candidate.deferred is None

    async def test_reflect_shows_the_reopen_on_the_nomination(self, storage, embedding_provider):
        node = await _never_retrieved(storage)
        await _defer(storage, node.id)
        await tools.reopen(storage, node_ids=[node.id], reason="Ask now.", judge=EDITOR)

        result, _ = await tools.reflect(storage, embedding_provider)
        [offered] = [c for c in result["archival_candidates"] if c["node_id"] == node.id]

        assert offered["reopened"]["reason"] == "Ask now."
        assert "deferred" not in offered

    async def test_a_node_nothing_holds_back_is_refused_naming_both(self, storage):
        node = await _never_retrieved(storage)

        result, _ = await tools.reopen(storage, node_ids=[node.id], reason="Look.", judge=EDITOR)

        assert result["reopened"] is False
        assert "retention" in result["refused"]
        assert "deferral" in result["refused"]


# --- The nominator and the tool agree ----------------------------------------


class TestTheNominatorAndTheToolAgree:
    """The two callers of `never_retrieved_exclusion`, run over one population.

    Each state below either is a never_retrieved nominee or is not, and the
    assertion is that the nominator offering it and the tool accepting a
    deferral of it are the same answer, node for node.
    """

    async def _population(self, storage) -> list[str]:
        from datetime import UTC, datetime

        await storage.set_reflect_round(10)
        plain = await _never_retrieved(storage, "A plain nominee.")
        retrieved = await _never_retrieved(
            storage, "Returned by a search.", retrieved_at=datetime.now(UTC)
        )
        important = await _never_retrieved(storage, "Judged to matter.", importance=0.8)
        linked = await _never_retrieved(storage, "Something links here.")
        await storage.store_edge(
            NodeEdge(src_id=plain.id, dst_id=linked.id, type=EdgeType.SUPPORTS)
        )
        kept = await _never_retrieved(storage, "Kept for good.")
        await record_retention(storage, node_id=kept.id, because="Kept.", reasons=[])
        waiting = await _never_retrieved(storage, "Deferred and waiting.")
        await _defer(storage, waiting.id)
        due = await _never_retrieved(storage, "Deferred and due.")
        await record_deferral(storage, node_id=due.id, because="Earlier.", at_round=7)
        ahead = await _never_retrieved(storage, "Deferred at a round still to come.")
        await record_deferral(storage, node_id=ahead.id, because="Later.", at_round=40)
        reopened = await _never_retrieved(storage, "Kept, then reopened.")
        await record_retention(storage, node_id=reopened.id, because="Kept.", reasons=[])
        await tools.reopen(storage, node_ids=[reopened.id], reason="Ask again.")
        inference = Inference(content="An inference.", source_id="seg-1")
        await storage.store_node(inference)
        return [
            node.id
            for node in (
                plain,
                retrieved,
                important,
                linked,
                kept,
                waiting,
                due,
                ahead,
                reopened,
                inference,
            )
        ]

    async def test_offered_and_accepted_are_the_same_nodes(self, storage, embedding_provider):
        population = await self._population(storage)

        offered = {c.node_id for c in await _nominated(storage) if c.reason == "never_retrieved"}
        result, _ = await tools.apply_reflection(
            storage,
            embedding_provider,
            deferred=[{"node_id": node_id, "because": "Check."} for node_id in population],
        )
        accepted = {entry["node_id"] for entry in result["deferrals_recorded"]}

        assert accepted == offered
        assert len(offered) == 4  # plain, due, ahead and reopened
        assert {entry["node_id"] for entry in result["deferred_skipped"]} == (
            set(population) - offered
        )


# --- Further cases -----------------------------------------------------------


class TestAGraphRestoredBehindItsDeferrals:
    """A round behind the deferral's shows the node, through both callers."""

    async def _restored(self, storage):
        node = await _never_retrieved(storage)
        await storage.set_reflect_round(10)
        await _defer(storage, node.id)
        # The round comes back from an older bundle, behind the deferral row.
        await storage.set_reflect_round(3)
        return node

    async def test_the_nominator_offers_it(self, storage):
        node = await self._restored(storage)

        [candidate] = [c for c in await _nominated(storage) if c.node_id == node.id]

        assert candidate.deferred.last_round == 10
        assert candidate.deferred.rounds_waited == 0

    async def test_the_tool_accepts_a_deferral_of_it(self, storage, embedding_provider):
        node = await self._restored(storage)

        result, _ = await tools.apply_reflection(
            storage, embedding_provider, deferred=[{"node_id": node.id, "because": "Still not."}]
        )

        assert result["deferrals_recorded"] == [
            {"node_id": node.id, "count": 2, "due_after_rounds": 4}
        ]
        rows = await _deferral_rows(storage)
        assert sorted(row.at_round for row in rows) == [3, 10]


class TestHigherClassesAndTheLimit:
    async def _retired(self, storage, n: int) -> None:
        from datetime import UTC, datetime, timedelta

        for i in range(n):
            await storage.store_node(
                Fact(
                    content=f"Retired claim {i}.",
                    source_id="seg-1",
                    status=NodeStatus.SUPERSEDED,
                    superseded_at=datetime.now(UTC) - timedelta(days=120),
                    value=ValueSignal(importance=0.2),
                )
            )

    async def test_a_due_node_still_leads_the_never_retrieved_that_fit(self, storage):
        await self._retired(storage, 19)
        for n in range(3):
            await _never_retrieved(storage, f"Bridge number {n}.")
        deferred = await _never_retrieved(storage, "The parish kept a ferry.")
        await _defer(storage, deferred.id)
        await _advance(storage, 2)

        candidates = await _nominated(storage)

        assert len(candidates) == 20
        assert [c.reason for c in candidates[:19]] == ["retired"] * 19
        assert candidates[19].node_id == deferred.id

    async def test_higher_classes_that_fill_the_limit_leave_no_room(self, storage):
        """What the guide's wording has to allow for: the placement is first among
        the never_retrieved nominees, not first in the list."""
        await self._retired(storage, 20)
        deferred = await _never_retrieved(storage, "The parish kept a ferry.")
        await _defer(storage, deferred.id)
        await _advance(storage, 2)

        assert deferred.id not in await _nominated_ids(storage)


class TestAKeepAndADeferralInOneQuery:
    async def test_each_index_reads_only_its_own_verdict(self, storage):
        kept = await _never_retrieved(storage, "Kept for good.")
        deferred = await _never_retrieved(storage, "Put off for now.")
        await record_retention(storage, node_id=kept.id, because="Kept.", reasons=[])
        await _defer(storage, deferred.id)

        ids = [kept.id, deferred.id]
        states = await deferrals_for(ids, storage)
        keeps = await confirmed_reasons_for(ids, storage)

        assert set(states) == {deferred.id}
        assert states[deferred.id].count == 1
        assert keeps == {kept.id: {kept.id}}
        assert not set(ids) & set(await _nominated_ids(storage))


class TestReopenOfADueDeferral:
    async def test_it_is_refused_and_the_count_stands(self, storage):
        """A deferral whose wait is over keeps nothing out, so there is nothing
        to withdraw, and withdrawing it anyway would reset the count."""
        node = await _never_retrieved(storage)
        await _defer(storage, node.id)
        await _advance(storage, 2)
        assert node.id in await _nominated_ids(storage)

        result, _ = await tools.reopen(storage, node_ids=[node.id], reason="Ask now.")

        assert result["reopened"] is False
        assert "nothing to reopen" in result["refused"]
        assert (await deferrals_for([node.id], storage))[node.id].count == 1
        rows = await storage.query_decisions(kinds=[DecisionKind.REOPENED])
        assert list(rows) == []

    async def test_the_process_default_decides_whether_it_is_due(self, storage):
        node = await _never_retrieved(storage)
        await _defer(storage, node.id)
        await _advance(storage, 2)

        result, _ = await tools.reopen(
            storage,
            node_ids=[node.id],
            reason="Ask now.",
            deferral_defaults=DeferralSettings(first_wait=5, cap=5),
        )

        assert result["reopened"] is True
