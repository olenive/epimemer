"""Who is judging, and who decided that (REVIEW_MODE.md §2, step 2).

The registry has one job that nothing else in this system does: make *"a
different agent reviewed this"* something the graph can show rather than
something an agent asserts. Everything here follows from that, and the tests are
grouped by the claim each one protects rather than by function.

Run against both backends, because approval is per-graph *settings* and a graph
that forgot its approved ids on one backend would refuse every claim on that
backend alone — the exact shape of divergence `tests/conftest.py` exists for.
"""

from datetime import datetime, timezone

from epimemer.core.types import (
    Agent,
    JudgeRef,
    description_digest,
    with_description,
)
from epimemer.mcp import tools


AT = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)
LATER = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)


async def _approved(storage, *ids):
    await storage.set_approved_agent_ids(list(ids))


def _accept(chosen: str | None = None):
    """An approval channel that answers — with `chosen`, or with what was asked."""

    async def approve(proposed: str, description: str) -> str:
        assert description, "the user is shown the description they are approving"
        return chosen or proposed

    return approve


async def _silent(proposed: str, description: str) -> None:
    """A client with no channel to the user: the elicitation-less case."""
    return None


class TestTheIdIsTheUsersToAssign:
    """An agent may propose an id; it may never award itself one.

    This is the whole mechanism. If an agent could admit its own id then
    `reviewed_by == judged_by` is unfalsifiable, self-review is
    indistinguishable from independent review, and the motivating case collapses
    (§2.2).
    """

    async def test_an_unapproved_id_is_refused(self, storage):
        result, _ = await tools.claim_agent(
            storage, agent_id="self-appointed", description="a critic",
            approve_id=_silent, now=AT,
        )

        assert result["status"] == "refused"
        assert await storage.get_agent("self-appointed") is None
        assert await storage.get_approved_agent_ids() == []

    async def test_the_refusal_is_the_prompt(self, storage):
        """No startup handshake exists, so the text is the whole mechanism.

        It has to name a way for the user to act, or an agent that reads it can
        only apologise.
        """
        await _approved(storage, "critic")

        result, _ = await tools.claim_agent(
            storage, agent_id="editor", description="an editor",
            approve_id=_silent, now=AT,
        )

        reason = result["reason"]
        assert "critic" in reason, "an agent that can see the approved ids can ask about them"
        assert "epimemer agents confirm" in reason
        assert "EPIMEMER_APPROVED_AGENTS" in reason
        assert result["approved_agent_ids"] == ["critic"]

    async def test_an_approved_id_needs_no_one_asked(self, storage):
        """The user answered once; a later session must not re-ask."""
        await _approved(storage, "critic")

        result, meta = await tools.claim_agent(
            storage, agent_id="critic", description="a critic", now=AT,
        )

        assert result["status"] == "claimed"
        assert result["agent_id"] == "critic"
        assert meta.nodes_returned == 1

    async def test_the_user_may_hand_back_a_different_id(self, storage):
        """They edit, and what they typed is what gets recorded.

        Recording the *proposal* after the user renamed it would record a claim
        nobody approved — quietly, and under a name the graph then treats as
        approved.
        """
        result, _ = await tools.claim_agent(
            storage, agent_id="claude", description="a critic",
            approve_id=_accept("olegs-critic"), now=AT,
        )

        assert result["agent_id"] == "olegs-critic"
        assert await storage.get_approved_agent_ids() == ["olegs-critic"]
        assert await storage.get_agent("claude") is None
        assert (await storage.get_agent("olegs-critic")) is not None

    async def test_approval_from_the_user_admits_the_id_for_next_time(self, storage):
        await tools.claim_agent(
            storage, agent_id="critic", description="a critic",
            approve_id=_accept(), now=AT,
        )

        # The second claim goes through with no channel to the user at all.
        result, _ = await tools.claim_agent(
            storage, agent_id="critic", description="a critic",
            approve_id=_silent, now=LATER,
        )
        assert result["status"] == "claimed"

    async def test_a_blank_id_or_description_is_refused(self, storage):
        await _approved(storage, "critic")

        blank_id, _ = await tools.claim_agent(
            storage, agent_id="   ", description="a critic", now=AT,
        )
        blank_text, _ = await tools.claim_agent(
            storage, agent_id="critic", description="  ", now=AT,
        )

        assert blank_id["status"] == "refused"
        assert blank_text["status"] == "refused"
        assert await storage.list_agents() == []


class TestADescriptionIsAClaimNotACredential:
    """Nothing verifies the prose. `confirmed_at` is the only human weight.

    The risk is adoption, not correctness: on five decisions a person eyeballs
    the field; on six hundred thousand somebody builds *"only count facts judged
    by X"* and forgets what the field is made of (§2.4).
    """

    async def test_an_unconfirmed_description_says_so(self, storage):
        await _approved(storage, "critic")

        result, _ = await tools.claim_agent(
            storage, agent_id="critic", description="a rigorous critic", now=AT,
        )

        assert result["description_confirmed"] is False
        agent = await storage.get_agent("critic")
        assert agent.descriptions[-1].confirmed_at is None
        assert "unconfirmed" in result["message"]

    async def test_approving_the_id_confirms_the_description_it_was_shown_with(
        self, storage
    ):
        """The user read that text when they approved the id, so it is theirs."""
        result, _ = await tools.claim_agent(
            storage, agent_id="critic", description="a critic",
            approve_id=_accept(), now=AT,
        )

        assert result["description_confirmed"] is True
        agent = await storage.get_agent("critic")
        assert agent.descriptions[-1].confirmed_at == AT

    async def test_a_new_description_under_a_known_id_is_asked_about(self, storage):
        await _approved(storage, "critic")
        await tools.claim_agent(
            storage, agent_id="critic", description="a critic", now=AT,
        )

        asked: list[str] = []

        async def confirm(agent_id: str, text: str) -> bool:
            asked.append(text)
            return True

        result, _ = await tools.claim_agent(
            storage, agent_id="critic", description="a much stricter critic",
            confirm_description=confirm, now=LATER,
        )

        assert asked == ["a much stricter critic"]
        assert result["description_confirmed"] is True

    async def test_an_unanswered_description_question_still_records_the_version(
        self, storage
    ):
        """Declining costs the confirmation, never the claim.

        *Self-described, unconfirmed* is a real epistemic object, so refusing
        the write here would lose a true record of what the agent said about
        itself to protect a field that is already marked as unverified.
        """
        await _approved(storage, "critic")
        await tools.claim_agent(storage, agent_id="critic", description="a critic", now=AT)

        async def declines(agent_id: str, text: str) -> bool:
            return False

        result, _ = await tools.claim_agent(
            storage, agent_id="critic", description="a stricter critic",
            confirm_description=declines, now=LATER,
        )

        agent = await storage.get_agent("critic")
        assert result["status"] == "claimed"
        assert result["description_confirmed"] is False
        assert [d.text for d in agent.descriptions] == ["a critic", "a stricter critic"]


class TestRedescribingAppendsAndNeverEdits:
    """A decision made last week was made by whatever this agent claimed to be
    last week, and that claim has to stay readable after it changes its mind."""

    async def test_a_changed_description_appends_a_version(self, storage):
        await _approved(storage, "critic")
        await tools.claim_agent(storage, agent_id="critic", description="a critic", now=AT)

        result, _ = await tools.claim_agent(
            storage, agent_id="critic", description="a critic, sceptical of merges",
            now=LATER,
        )

        agent = await storage.get_agent("critic")
        assert result["new_description"] is True
        assert result["description_versions"] == 2
        assert [d.recorded_at for d in agent.descriptions] == [AT, LATER]
        assert agent.descriptions[0].text == "a critic"

    async def test_identical_text_is_not_a_new_version(self, storage):
        """Otherwise the history fills with versions that differ only in date,
        and the digest stops answering *which description was in force*."""
        await _approved(storage, "critic")
        await tools.claim_agent(storage, agent_id="critic", description="a critic", now=AT)

        result, _ = await tools.claim_agent(
            storage, agent_id="critic", description="a critic", now=LATER,
        )

        agent = await storage.get_agent("critic")
        assert result["new_description"] is False
        assert result["description_versions"] == 1
        assert agent.descriptions[0].recorded_at == AT
        assert agent.last_seen_at == LATER, "seen again, even with nothing new to say"

    async def test_the_digest_identifies_the_version_not_the_agent(self, storage):
        """Two agents with identical prose share a digest and stay two agents.

        Hashing the description to get an *id* fails in both directions, which
        is why the digest sits one level down (§2.1).
        """
        await _approved(storage, "critic", "second-critic")
        first, _ = await tools.claim_agent(
            storage, agent_id="critic", description="a critic", now=AT,
        )
        second, _ = await tools.claim_agent(
            storage, agent_id="second-critic", description="a critic", now=AT,
        )

        assert first["digest"] == second["digest"] == description_digest("a critic")
        assert len(await storage.list_agents()) == 2

    async def test_first_seen_survives_a_later_claim(self, storage):
        await _approved(storage, "critic")
        await tools.claim_agent(storage, agent_id="critic", description="a critic", now=AT)

        await tools.claim_agent(
            storage, agent_id="critic", description="a critic", now=LATER,
        )

        agent = await storage.get_agent("critic")
        assert agent.first_seen_at == AT
        assert agent.last_seen_at == LATER


class TestApprovalIsPerGraph:
    """Graphs are isolated by design, and approval is a per-graph setting.

    The id is still the user's to reuse across graphs — that is how a human
    correlates two graphs' judges (§2.5) — but reuse is their act, not a
    default the system applies for them.
    """

    async def test_an_id_approved_here_is_not_approved_there(self, storage):
        await _approved(storage, "critic")
        await tools.claim_agent(storage, agent_id="critic", description="a critic", now=AT)

        await storage.switch_database("elsewhere")

        assert await storage.get_approved_agent_ids() == []
        assert await storage.get_agent("critic") is None
        result, _ = await tools.claim_agent(
            storage, agent_id="critic", description="a critic",
            approve_id=_silent, now=AT,
        )
        assert result["status"] == "refused"

    async def test_use_graph_unbinds_a_judge_the_new_graph_never_approved(
        self, storage
    ):
        """The session binds one judge; the approval that admitted it did not
        travel with the switch."""
        await _approved(storage, "critic")
        judge = JudgeRef(agent_id="critic", digest=description_digest("a critic"))

        result, _ = await tools.use_graph(
            "elsewhere", storage, confirm=True, judge=judge,
        )

        assert result["judge_cleared"] == "critic"
        assert "claim_agent again" in result["message"]

    async def test_a_judge_the_new_graph_does_approve_survives(self, storage):
        await storage.switch_database("elsewhere")
        await _approved(storage, "critic")
        await storage.switch_database("default")
        judge = JudgeRef(agent_id="critic", digest="whatever")

        result, _ = await tools.use_graph("elsewhere", storage, judge=judge)

        assert "judge_cleared" not in result
        assert await tools.judge_is_approved(storage, judge) is True

    async def test_a_session_with_no_judge_switches_quietly(self, storage):
        result, _ = await tools.use_graph("elsewhere", storage, confirm=True, judge=None)

        assert "judge_cleared" not in result
        assert result["status"] == "created"


class TestConfiguredApprovalsReachTheGraphYouLandOn:
    """`EPIMEMER_APPROVED_AGENTS` is the only approval channel that reaches an
    embedded store, so a graph switch that skipped it would leave an
    elicitation-less client with no way to admit a judge at all (§10.3)."""

    async def test_seeding_admits_ids_to_a_newly_created_graph(self, storage):
        await tools.use_graph(
            "elsewhere", storage, confirm=True, seed_agent_ids=["critic", "editor"],
        )

        assert await storage.get_approved_agent_ids() == ["critic", "editor"]

    async def test_seeding_runs_before_the_judge_is_rechecked(self, storage):
        """Otherwise configuration would clear a judge it was about to admit."""
        judge = JudgeRef(agent_id="critic", digest="whatever")

        result, _ = await tools.use_graph(
            "elsewhere", storage, confirm=True, judge=judge, seed_agent_ids=["critic"],
        )

        assert "judge_cleared" not in result

    async def test_approving_is_a_union_never_a_replacement(self, storage):
        """Three writers reach this list; the last must not revoke the others."""
        await _approved(storage, "from-the-cli")

        approved = await tools.approve_agent_ids(storage, ["from-config", "from-the-cli"])

        assert approved == ["from-the-cli", "from-config"]
        assert await storage.get_approved_agent_ids() == ["from-the-cli", "from-config"]

    async def test_seeding_nothing_writes_nothing(self, storage):
        await _approved(storage, "critic")

        assert await tools.approve_agent_ids(storage, []) == ["critic"]
        assert await tools.approve_agent_ids(storage, ["critic"]) == ["critic"]


class TestTheRegistryIsNotPartOfTheGraph:
    """Agents are their own table. As nodes they would surface in `search` and
    be swept by `reflect`, and two agents with similar descriptions are not a
    topic to merge (§2.5)."""

    async def test_an_agent_is_not_a_node(self, storage):
        await _approved(storage, "critic")
        await tools.claim_agent(storage, agent_id="critic", description="a critic", now=AT)

        assert await storage.query_nodes() == []
        assert await storage.get_node("critic") is None

    async def test_agents_round_trip_whole(self, storage):
        """Every field, because a partial copy of a model is a bug with a delay
        on it — the carry-forward from `MergedEdge` (§7.9)."""
        agent = Agent(
            id="critic",
            descriptions=with_description(
                with_description([], text="a critic", at=AT, confirmed_at=AT),
                text="a stricter critic",
                at=LATER,
            ),
            authorised_at=AT,
            first_seen_at=AT,
            last_seen_at=LATER,
        )

        await storage.upsert_agent(agent)

        assert await storage.get_agent("critic") == agent
        assert await storage.list_agents() == [agent]

    async def test_upsert_replaces_rather_than_accumulating(self, storage):
        await storage.upsert_agent(Agent(id="critic", authorised_at=AT))
        await storage.upsert_agent(
            Agent(
                id="critic",
                descriptions=with_description([], text="a critic", at=AT),
                authorised_at=AT,
            )
        )

        agents = await storage.list_agents()
        assert len(agents) == 1
        assert len(agents[0].descriptions) == 1

    async def test_storage_hands_back_copies(self, storage):
        """The in-memory backend hands out live objects unless it copies, and a
        caller that edited one would rewrite history in place."""
        await storage.upsert_agent(
            Agent(
                id="critic",
                descriptions=with_description([], text="a critic", at=AT),
                authorised_at=AT,
            )
        )

        got = await storage.get_agent("critic")
        got.descriptions.clear()

        assert len((await storage.get_agent("critic")).descriptions) == 1
