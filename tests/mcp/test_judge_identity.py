"""Three layers of judge identity, against a real store (the judge identity split, stage 2).

`tests/core/test_agent_identity.py` covers the pure resolutions. This covers
what they mean once a graph holds records: that a handle binds to the judge the
user meant, that a rename carries decisions with it, and that consolidating two
records rewrites no journal row.

Both backends, because a judge is graph *state* and the two stores agree about
it only if something checks — the divergence `tests/conftest.py` exists for.
"""

from datetime import UTC, datetime

from epimemer.core.types import (
    Agent,
    DecisionKind,
    DecisionRecord,
    Fact,
    JudgeRef,
    agent_aliases,
    agent_name,
    is_retired,
    live_agents,
    retired_at,
)
from epimemer.mcp import tools
from epimemer.storage.protocol import judge_aliases

AT = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)
LATER = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)


def _accept(chosen: str | None = None):
    async def approve(proposed: str, description: str) -> tools.ApprovalOutcome:
        return tools.ApprovalOutcome(chosen=chosen or proposed)

    return approve


async def _claim(storage, name: str, description: str = "a critic", now=AT) -> dict:
    result, _ = await tools.claim_agent(
        storage,
        agent_id=name,
        description=description,
        approve_id=_accept(),
        now=now,
    )
    assert result["status"] == "claimed", result.get("reason")
    return result


async def _decided(storage, key: str, subject: str = "n1") -> DecisionRecord:
    record = DecisionRecord(
        kind=DecisionKind.MERGE,
        subject_ids=[subject],
        judged_by=JudgeRef(agent_id=key, digest="d1"),
        decided_at=AT,
    )
    await storage.record_decision(record)
    return record


class TestAHandleFindsTheJudgeTheUserMeant:
    async def test_a_second_claim_by_name_joins_rather_than_mints(self, storage):
        first = await _claim(storage, "Opus 5")

        second = await _claim(storage, "opus 5", now=LATER)

        assert second["agent_id"] == first["agent_id"]
        assert second["new_agent"] is False
        assert len(live_agents(await storage.list_agents())) == 1

    async def test_a_claim_by_the_key_handed_back_joins_too(self, storage):
        first = await _claim(storage, "Opus 5")

        second = await _claim(storage, first["agent_id"], now=LATER)

        assert second["agent_id"] == first["agent_id"]
        assert second["name"] == "Opus 5", "the key is not the name"

    async def test_typing_an_existing_name_at_the_new_judge_prompt_joins_it(self, storage):
        # The free-text path is reached by asking for a *new* judge, and typing
        # the name of one that exists is how this graph's own split began.
        first = await _claim(storage, "Opus 5")

        result, _ = await tools.claim_agent(
            storage,
            agent_id="something else",
            description="a critic",
            approve_id=_accept("Opus 5"),
            now=LATER,
        )

        assert result["agent_id"] == first["agent_id"]
        assert result["new_agent"] is False

    async def test_a_seeded_id_is_adopted_as_its_own_key(self, storage):
        # Seeding is the only channel an elicitation-less client has, and the
        # string it seeded *is* the key: minting a second one beside it would
        # orphan the approval the user actually gave.
        await storage.set_approved_agent_ids(["configured"])

        result = await _claim(storage, "configured")

        assert result["agent_id"] == "configured"
        assert await storage.get_approved_agent_ids() == ["configured"]

    async def test_a_record_written_before_the_split_is_named_on_its_next_claim(self, storage):
        await storage.upsert_agent(Agent(id="legacy", authorised_at=AT))
        await storage.set_approved_agent_ids(["legacy"])

        result = await _claim(storage, "legacy", now=LATER)

        assert result["agent_id"] == "legacy", "the key it was recorded under"
        assert result["name"] == "legacy"
        assert (await storage.get_agent("legacy")).name == "legacy"


class TestRenamingCarriesTheDecisions:
    """The name is resolved at read time, so a rename that left old rows behind
    would have achieved nothing."""

    async def test_the_key_and_the_history_are_untouched(self, storage):
        claimed = await _claim(storage, "Opus 5")
        await _decided(storage, claimed["agent_id"])

        result = await tools.rename_judge(storage, handle="Opus 5", name="Opus 5 reviewer")

        assert result["status"] == "renamed"
        assert result["agent_id"] == claimed["agent_id"]
        assert result["previous_name"] == "Opus 5"

    async def test_the_new_name_finds_the_old_decisions(self, storage):
        claimed = await _claim(storage, "Opus 5")
        await _decided(storage, claimed["agent_id"])
        await tools.rename_judge(storage, handle="Opus 5", name="reviewer")

        found = await storage.query_decisions(agent_ids=await judge_aliases(storage, "reviewer"))

        assert len(found) == 1

    async def test_the_old_name_finds_nothing_because_it_names_nobody(self, storage):
        claimed = await _claim(storage, "Opus 5")
        await _decided(storage, claimed["agent_id"])
        await tools.rename_judge(storage, handle="Opus 5", name="reviewer")

        result, _ = await tools.review(storage, mode="by_agent", agent_id="Opus 5")

        assert result["decisions"] == []
        # And it says so, rather than leaving an empty page that reads like a
        # judge which decided nothing.
        assert result["judge"]["unknown_here"] is True
        assert result["judge"]["judges_here"] == ["reviewer"]

    async def test_renaming_something_that_is_not_a_judge_is_refused(self, storage):
        await _claim(storage, "Opus 5")

        result = await tools.rename_judge(storage, handle="nobody", name="x")

        assert result["status"] == "refused"
        assert "Opus 5" in result["reason"], "it names the judges that do exist"

    async def test_a_blank_name_is_refused(self, storage):
        await _claim(storage, "Opus 5")
        assert (await tools.rename_judge(storage, handle="Opus 5", name="  "))[
            "status"
        ] == "refused"


class TestATakenNameIsAQuestionNotAnError:
    """Two records that should be one is the commonest reason to be renaming at
    all — it is how `Opus 5 Judge` and `Opus 5` came to exist here."""

    async def test_a_collision_asks_rather_than_refusing(self, storage):
        await _claim(storage, "Opus 5 Judge")
        await _claim(storage, "Opus 5", now=LATER)

        result = await tools.rename_judge(storage, handle="Opus 5 Judge", name="Opus 5")

        assert result["status"] == "same_judge_needed"
        assert "same judge" in result["reason"]
        assert len(live_agents(await storage.list_agents())) == 2, "nothing changed"

    async def test_answering_yes_consolidates(self, storage):
        old = await _claim(storage, "Opus 5 Judge")
        new = await _claim(storage, "Opus 5", now=LATER)

        result = await tools.rename_judge(
            storage, handle="Opus 5 Judge", name="Opus 5", same_judge=True
        )

        assert result["status"] == "consolidated"
        assert result["agent_id"] == new["agent_id"], "the name's holder survives"
        assert old["agent_id"] in result["former_ids"]
        assert [agent_name(a) for a in live_agents(await storage.list_agents())] == ["Opus 5"]

    async def test_the_absorbed_records_decisions_are_found_under_the_survivor(self, storage):
        old = await _claim(storage, "Opus 5 Judge")
        new = await _claim(storage, "Opus 5", now=LATER)
        await _decided(storage, old["agent_id"], subject="n1")
        await _decided(storage, new["agent_id"], subject="n2")

        await tools.rename_judge(storage, handle="Opus 5 Judge", name="Opus 5", same_judge=True)
        result, _ = await tools.review(storage, mode="by_agent", agent_id="Opus 5")

        assert len(result["decisions"]) == 2
        assert result["judge"]["also_recorded_as"] == [old["agent_id"]]

    async def test_no_journal_row_is_rewritten(self, storage):
        old = await _claim(storage, "Opus 5 Judge")
        new = await _claim(storage, "Opus 5", now=LATER)
        record = await _decided(storage, old["agent_id"])

        await tools.rename_judge(storage, handle="Opus 5 Judge", name="Opus 5", same_judge=True)

        stored = await storage.get_decision(record.id)
        assert stored.judged_by.agent_id == old["agent_id"]
        assert new["agent_id"] != old["agent_id"]

    async def test_the_absorbed_record_is_kept_and_stops_being_offered(self, storage):
        old = await _claim(storage, "Opus 5 Judge")
        await _claim(storage, "Opus 5", now=LATER)
        await tools.rename_judge(storage, handle="Opus 5 Judge", name="Opus 5", same_judge=True)

        assert await storage.get_agent(old["agent_id"]) is not None, "not deleted"
        assert [c.name for c in await tools.judge_roster(storage)] == ["Opus 5"]

    async def test_a_claim_under_the_absorbed_name_lands_on_the_survivor(self, storage):
        old = await _claim(storage, "Opus 5 Judge")
        new = await _claim(storage, "Opus 5", now=LATER)
        await tools.rename_judge(storage, handle="Opus 5 Judge", name="Opus 5", same_judge=True)

        # The agent proposes the key it was given a session ago.
        result = await _claim(storage, old["agent_id"], now=LATER)

        assert result["agent_id"] == new["agent_id"]
        assert result["name"] == "Opus 5"


class TestTheRosterAfterAConsolidation:
    async def test_an_approved_key_belonging_to_a_live_judge_is_not_offered_twice(self, storage):
        old = await _claim(storage, "Opus 5 Judge")
        await _claim(storage, "Opus 5", now=LATER)
        await tools.rename_judge(storage, handle="Opus 5 Judge", name="Opus 5", same_judge=True)

        # Both keys are still approved — approval is a union and withdrawing one
        # is not this operation's business — but they are one judge now.
        assert old["agent_id"] in await storage.get_approved_agent_ids()
        assert len(await tools.judge_roster(storage)) == 1


class TestSeedingApprovalsTheWayAPersonNamesThem:
    """`EPIMEMER_APPROVED_AGENTS` and `epimemer agents confirm` take text a user
    typed, and the approved list holds opaque keys — so a name has to resolve or
    seeding an existing judge by name approves a second, empty identity."""

    async def test_a_name_seeds_the_judge_that_holds_it(self, storage):
        claimed = await _claim(storage, "Opus 5")
        await storage.set_approved_agent_ids([])

        approved = await tools.seed_approved_judges(storage, ["opus 5"])

        assert approved == [claimed["agent_id"]]

    async def test_a_former_key_seeds_the_judge_that_absorbed_it(self, storage):
        old = await _claim(storage, "Opus 5 Judge")
        new = await _claim(storage, "Opus 5", now=LATER)
        await tools.rename_judge(storage, handle="Opus 5 Judge", name="Opus 5", same_judge=True)
        await storage.set_approved_agent_ids([])

        approved = await tools.seed_approved_judges(storage, [old["agent_id"]])

        assert approved == [new["agent_id"]]

    async def test_a_name_nobody_holds_is_admitted_as_itself(self, storage):
        # Which is exactly the old behaviour, and the only sensible reading of
        # seeding a judge that does not exist yet: its first claim adopts it.
        assert await tools.seed_approved_judges(storage, ["configured"]) == ["configured"]

    async def test_a_refusal_names_judges_rather_than_keys(self, storage):
        claimed = await _claim(storage, "Opus 5")

        result, _ = await tools.claim_agent(
            storage,
            agent_id="stranger",
            description="a critic",
            approve_id=None,
            now=LATER,
        )

        assert result["status"] == "refused"
        assert "Opus 5" in result["reason"], "a key in a message for a person is unusable"
        assert claimed["agent_id"] not in result["reason"]
        assert result["approved_judges"] == ["Opus 5"]


class TestRetiringAJudge:
    """A judge that should not be chosen again leaves the picker, and keeps
    everything else. The alternative users had was renaming it to something
    warning-shaped, which is a warning nothing enforces."""

    async def test_it_is_recorded_as_retired_and_nothing_else_changes(self, storage):
        claimed = await _claim(storage, "Opus 5")
        record = await _decided(storage, claimed["agent_id"])

        result = await tools.retire_judge(storage, handle="Opus 5", now=LATER)

        assert result["status"] == "retired"
        stored = await storage.get_agent(claimed["agent_id"])
        assert is_retired(stored)
        assert retired_at(stored) == LATER
        assert agent_name(stored) == "Opus 5"
        assert len(stored.descriptions) == 1
        # The decision is still there, still readable, and still reviewable —
        # `judged_by` holds a key, and the key still resolves to a name.
        found = await storage.query_decisions(agent_ids=agent_aliases(stored))
        assert [r.id for r in found] == [record.id]

    async def test_it_says_how_to_bring_it_back_and_what_happens_to_sessions(self, storage):
        await _claim(storage, "Opus 5")

        result = await tools.retire_judge(storage, handle="Opus 5", now=LATER)

        assert "epimemer agents reinstate Opus 5" in result["message"]
        assert "continue until they reconnect" in result["message"]

    async def test_a_key_or_a_former_key_names_it_too(self, storage):
        old = await _claim(storage, "Opus 5 Judge")
        await _claim(storage, "Opus 5", now=LATER)
        await tools.rename_judge(storage, handle="Opus 5 Judge", name="Opus 5", same_judge=True)

        result = await tools.retire_judge(storage, handle=old["agent_id"], now=LATER)

        assert result["status"] == "retired"
        assert is_retired(await storage.get_agent(result["agent_id"]))

    async def test_retiring_one_already_retired_is_refused_with_the_date(self, storage):
        await _claim(storage, "Opus 5")
        await tools.retire_judge(storage, handle="Opus 5", now=AT)

        result = await tools.retire_judge(storage, handle="Opus 5", now=LATER)

        assert result["status"] == "refused"
        assert "already retired on 2026-08-22" in result["reason"]

    async def test_a_handle_nothing_answers_to_is_refused(self, storage):
        await _claim(storage, "Opus 5")

        result = await tools.retire_judge(storage, handle="nobody", now=LATER)

        assert result["status"] == "refused"
        assert "No judge here answers to 'nobody'" in result["reason"]
        assert "Opus 5" in result["reason"]

    async def test_an_absorbed_record_cannot_be_retired_on_its_own(self, storage):
        """It is not a judge in its own right. Its old name stops naming
        anything, and its key resolves to the judge that absorbed it — which is
        the one a user retiring it means."""
        old = await _claim(storage, "Opus 5 Judge")
        new = await _claim(storage, "Opus 5", now=LATER)
        await tools.rename_judge(storage, handle="Opus 5 Judge", name="Opus 5", same_judge=True)

        by_old_name = await tools.retire_judge(storage, handle="Opus 5 Judge", now=LATER)
        assert by_old_name["status"] == "refused"

        by_key = await tools.retire_judge(storage, handle=old["agent_id"], now=LATER)
        assert by_key["agent_id"] == new["agent_id"]
        assert not is_retired(await storage.get_agent(old["agent_id"]))


class TestReinstatingAJudge:
    async def test_it_can_be_claimed_again(self, storage):
        await _claim(storage, "Opus 5")
        await tools.retire_judge(storage, handle="Opus 5", now=AT)

        result = await tools.reinstate_judge(storage, handle="Opus 5", now=LATER)

        assert result["status"] == "reinstated"
        claimed, _ = await tools.claim_agent(
            storage, agent_id="Opus 5", description="a critic", approve_id=_accept(), now=LATER
        )
        assert claimed["status"] == "claimed"

    async def test_the_spell_it_spent_retired_stays_on_the_record(self, storage):
        await _claim(storage, "Opus 5")
        await tools.retire_judge(storage, handle="Opus 5", now=AT)

        result = await tools.reinstate_judge(storage, handle="Opus 5", now=LATER)

        stored = await storage.get_agent(result["agent_id"])
        assert [(e.retired_at, e.reinstated_at) for e in stored.retirements] == [(AT, LATER)]

    async def test_reinstating_a_serving_judge_is_refused(self, storage):
        await _claim(storage, "Opus 5")

        result = await tools.reinstate_judge(storage, handle="Opus 5", now=LATER)

        assert result["status"] == "refused"
        assert "is not retired" in result["reason"]

    async def test_a_handle_nothing_answers_to_is_refused(self, storage):
        result = await tools.reinstate_judge(storage, handle="nobody", now=LATER)

        assert result["status"] == "refused"
        assert "No judge here answers to 'nobody'" in result["reason"]

    async def test_the_refusal_lists_retired_judges_and_marks_them(self, storage):
        """The judge a user is most likely to be naming at `reinstate` is a
        retired one, so *not found* has to say which those are."""
        await _claim(storage, "Opus 5")
        await tools.retire_judge(storage, handle="Opus 5", now=AT)

        result = await tools.reinstate_judge(storage, handle="opus five", now=LATER)

        assert "Opus 5 (retired)" in result["reason"]


class TestDeletingAJudgeThatHasNeverJudged:
    """The journal is append-only and `judged_by` holds a key, so a record can
    only go where nothing carries it. Everything else is retired."""

    async def test_an_unused_judge_is_scanned_clean_and_deleted(self, storage):
        claimed = await _claim(storage, "Opus 5")

        scan = await tools.judge_deletion_scan(storage, handle="Opus 5")
        assert scan["status"] == "deletable"
        assert scan["usage"] == {"decisions": 0, "nodes": 0, "edges": 0, "relations": 0}

        result = await tools.delete_judge(storage, handle="Opus 5")

        assert result["status"] == "deleted"
        assert await storage.get_agent(claimed["agent_id"]) is None

    async def test_the_approval_goes_with_the_record(self, storage):
        """An approved key with no judge behind it would be offered by the
        picker as a bare id and mint the record again."""
        claimed = await _claim(storage, "Opus 5")
        assert claimed["agent_id"] in await storage.get_approved_agent_ids()

        await tools.delete_judge(storage, handle="Opus 5")

        assert await storage.get_approved_agent_ids() == []

    async def test_a_judge_that_decided_something_is_refused_with_the_counts(self, storage):
        claimed = await _claim(storage, "Opus 5")
        await _decided(storage, claimed["agent_id"])

        result = await tools.delete_judge(storage, handle="Opus 5")

        assert result["status"] == "refused"
        assert "1 journal row(s)" in result["reason"]
        assert "epimemer agents retire Opus 5" in result["reason"]
        assert await storage.get_agent(claimed["agent_id"]) is not None

    async def test_a_judge_that_wrote_a_node_is_refused(self, storage):
        claimed = await _claim(storage, "Opus 5")
        await storage.store_node(
            Fact(
                content="x",
                source_id="s1",
                judged_by=JudgeRef(agent_id=claimed["agent_id"], digest="d1"),
            )
        )

        result = await tools.delete_judge(storage, handle="Opus 5")

        assert result["status"] == "refused"
        assert "1 node(s)" in result["reason"]

    async def test_a_judge_that_absorbed_another_is_refused(self, storage):
        """Rows written under the absorbed key resolve through this record, so
        deleting it strands them even where it has judged nothing itself."""
        await _claim(storage, "Opus 5 Judge")
        new = await _claim(storage, "Opus 5", now=LATER)
        await tools.rename_judge(storage, handle="Opus 5 Judge", name="Opus 5", same_judge=True)

        result = await tools.delete_judge(storage, handle="Opus 5")

        assert result["status"] == "refused"
        assert "consolidated into it" in result["reason"]
        assert await storage.get_agent(new["agent_id"]) is not None

    async def test_a_handle_nothing_answers_to_is_refused(self, storage):
        result = await tools.delete_judge(storage, handle="nobody")

        assert result["status"] == "refused"
        assert "No judge here answers to 'nobody'" in result["reason"]

    async def test_a_retired_judge_that_never_judged_can_still_be_deleted(self, storage):
        """Retiring is not a state that protects a record; what protects one is
        something naming it."""
        claimed = await _claim(storage, "Opus 5")
        await tools.retire_judge(storage, handle="Opus 5", now=LATER)

        result = await tools.delete_judge(storage, handle="Opus 5")

        assert result["status"] == "deleted"
        assert await storage.get_agent(claimed["agent_id"]) is None
