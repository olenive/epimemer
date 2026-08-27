"""Three layers of judge identity, against a real store (#78, stage 2).

`tests/core/test_agent_identity.py` covers the pure resolutions. This covers
what they mean once a graph holds records: that a handle binds to the judge the
user meant, that a rename carries decisions with it, and that consolidating two
records rewrites no journal row.

Both backends, because a judge is graph *state* and the two stores agree about
it only if something checks — the divergence `tests/conftest.py` exists for.
"""

from datetime import datetime, timezone

from epimemer.core.types import (
    Agent,
    DecisionKind,
    DecisionRecord,
    JudgeRef,
    agent_name,
    live_agents,
)
from epimemer.mcp import tools
from epimemer.storage.protocol import judge_aliases


AT = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)
LATER = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)


def _accept(chosen: str | None = None):
    async def approve(proposed: str, description: str) -> tools.ApprovalOutcome:
        return tools.ApprovalOutcome(chosen=chosen or proposed)

    return approve


async def _claim(storage, name: str, description: str = "a critic", now=AT) -> dict:
    result, _ = await tools.claim_agent(
        storage, agent_id=name, description=description,
        approve_id=_accept(), now=now,
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

    async def test_typing_an_existing_name_at_the_new_judge_prompt_joins_it(
        self, storage
    ):
        # The free-text path is reached by asking for a *new* judge, and typing
        # the name of one that exists is how this graph's own split began.
        first = await _claim(storage, "Opus 5")

        result, _ = await tools.claim_agent(
            storage, agent_id="something else", description="a critic",
            approve_id=_accept("Opus 5"), now=LATER,
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

    async def test_a_record_written_before_the_split_is_named_on_its_next_claim(
        self, storage
    ):
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

        result = await tools.rename_judge(
            storage, handle="Opus 5", name="Opus 5 reviewer"
        )

        assert result["status"] == "renamed"
        assert result["agent_id"] == claimed["agent_id"]
        assert result["previous_name"] == "Opus 5"

    async def test_the_new_name_finds_the_old_decisions(self, storage):
        claimed = await _claim(storage, "Opus 5")
        await _decided(storage, claimed["agent_id"])
        await tools.rename_judge(storage, handle="Opus 5", name="reviewer")

        found = await storage.query_decisions(
            agent_ids=await judge_aliases(storage, "reviewer")
        )

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
        assert (
            await tools.rename_judge(storage, handle="Opus 5", name="  ")
        )["status"] == "refused"


class TestATakenNameIsAQuestionNotAnError:
    """Two records that should be one is the commonest reason to be renaming at
    all — it is how `Opus 5 Judge` and `Opus 5` came to exist here."""

    async def test_a_collision_asks_rather_than_refusing(self, storage):
        await _claim(storage, "Opus 5 Judge")
        await _claim(storage, "Opus 5", now=LATER)

        result = await tools.rename_judge(
            storage, handle="Opus 5 Judge", name="Opus 5"
        )

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
        assert [agent_name(a) for a in live_agents(await storage.list_agents())] == [
            "Opus 5"
        ]

    async def test_the_absorbed_records_decisions_are_found_under_the_survivor(
        self, storage
    ):
        old = await _claim(storage, "Opus 5 Judge")
        new = await _claim(storage, "Opus 5", now=LATER)
        await _decided(storage, old["agent_id"], subject="n1")
        await _decided(storage, new["agent_id"], subject="n2")

        await tools.rename_judge(
            storage, handle="Opus 5 Judge", name="Opus 5", same_judge=True
        )
        result, _ = await tools.review(storage, mode="by_agent", agent_id="Opus 5")

        assert len(result["decisions"]) == 2
        assert result["judge"]["also_recorded_as"] == [old["agent_id"]]

    async def test_no_journal_row_is_rewritten(self, storage):
        old = await _claim(storage, "Opus 5 Judge")
        new = await _claim(storage, "Opus 5", now=LATER)
        record = await _decided(storage, old["agent_id"])

        await tools.rename_judge(
            storage, handle="Opus 5 Judge", name="Opus 5", same_judge=True
        )

        stored = await storage.get_decision(record.id)
        assert stored.judged_by.agent_id == old["agent_id"]
        assert new["agent_id"] != old["agent_id"]

    async def test_the_absorbed_record_is_kept_and_stops_being_offered(self, storage):
        old = await _claim(storage, "Opus 5 Judge")
        await _claim(storage, "Opus 5", now=LATER)
        await tools.rename_judge(
            storage, handle="Opus 5 Judge", name="Opus 5", same_judge=True
        )

        assert await storage.get_agent(old["agent_id"]) is not None, "not deleted"
        assert [c.name for c in await tools.judge_roster(storage)] == ["Opus 5"]

    async def test_a_claim_under_the_absorbed_name_lands_on_the_survivor(
        self, storage
    ):
        old = await _claim(storage, "Opus 5 Judge")
        new = await _claim(storage, "Opus 5", now=LATER)
        await tools.rename_judge(
            storage, handle="Opus 5 Judge", name="Opus 5", same_judge=True
        )

        # The agent proposes the key it was given a session ago.
        result = await _claim(storage, old["agent_id"], now=LATER)

        assert result["agent_id"] == new["agent_id"]
        assert result["name"] == "Opus 5"


class TestTheRosterAfterAConsolidation:
    async def test_an_approved_key_belonging_to_a_live_judge_is_not_offered_twice(
        self, storage
    ):
        old = await _claim(storage, "Opus 5 Judge")
        await _claim(storage, "Opus 5", now=LATER)
        await tools.rename_judge(
            storage, handle="Opus 5 Judge", name="Opus 5", same_judge=True
        )

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
        await tools.rename_judge(
            storage, handle="Opus 5 Judge", name="Opus 5", same_judge=True
        )
        await storage.set_approved_agent_ids([])

        approved = await tools.seed_approved_judges(storage, [old["agent_id"]])

        assert approved == [new["agent_id"]]

    async def test_a_name_nobody_holds_is_admitted_as_itself(self, storage):
        # Which is exactly the old behaviour, and the only sensible reading of
        # seeding a judge that does not exist yet: its first claim adopts it.
        assert await tools.seed_approved_judges(storage, ["configured"]) == [
            "configured"
        ]

    async def test_a_refusal_names_judges_rather_than_keys(self, storage):
        claimed = await _claim(storage, "Opus 5")

        result, _ = await tools.claim_agent(
            storage, agent_id="stranger", description="a critic",
            approve_id=None, now=LATER,
        )

        assert result["status"] == "refused"
        assert "Opus 5" in result["reason"], "a key in a message for a person is unusable"
        assert claimed["agent_id"] not in result["reason"]
        assert result["approved_judges"] == ["Opus 5"]
