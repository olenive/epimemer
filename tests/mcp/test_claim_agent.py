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

    async def approve(proposed: str, description: str) -> tools.ApprovalOutcome:
        assert description, "the user is shown the description they are approving"
        return tools.ApprovalOutcome(chosen=chosen or proposed)

    return approve


async def _silent(proposed: str, description: str) -> tools.ApprovalOutcome:
    """A client with no channel to the user: the elicitation-less case.

    Distinct from `_declined`, and the distinction is the point (#78): this one
    could not put the question, so an id the user approved out of band still
    binds. That one put the question and got *no*.
    """
    return tools.ApprovalOutcome(channel_available=False)


async def _declined(proposed: str, description: str) -> tools.ApprovalOutcome:
    """A user who saw the question and said no."""
    return tools.ApprovalOutcome()


def _asked(record: list[str]):
    """An approval channel that answers yes and records that it was asked."""

    async def approve(proposed: str, description: str) -> tools.ApprovalOutcome:
        record.append(proposed)
        return tools.ApprovalOutcome(chosen=proposed)

    return approve


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

    async def test_the_user_may_hand_back_a_different_name(self, storage):
        """They edit, and what they typed is what the judge is called.

        Recording the *proposal* after the user renamed it would record a claim
        nobody approved — quietly, and under a name the graph then treats as
        approved. Since the three-layer split the name is not the key: a judge
        nothing here knows gets an opaque one (#78), which is what leaves the
        name free to change later.
        """
        result, _ = await tools.claim_agent(
            storage, agent_id="claude", description="a critic",
            approve_id=_accept("olegs-critic"), now=AT,
        )

        assert result["name"] == "olegs-critic"
        assert result["agent_id"] not in ("claude", "olegs-critic")
        assert await storage.get_approved_agent_ids() == [result["agent_id"]]
        assert await storage.get_agent("claude") is None
        agent = await storage.get_agent(result["agent_id"])
        assert agent is not None and agent.name == "olegs-critic"

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


class TestTheGateGuardsAssumingAnIdNotOnlyMintingOne:
    """An approved id does not bind without the user seeing it (#78).

    The gate used to fire only where `agent_id not in approved`, which guarded
    the wrong act. Once an id was approved, any session bound to it with no user
    involvement at all — and `_unapproved_reason` names the approved ids in its
    refusal, so a rejected guess handed back the list of ids that would work.
    Guessing wrong was a directory lookup.
    """

    async def test_an_already_approved_id_is_still_put_to_the_user(self, storage):
        await _approved(storage, "critic")
        asked: list[str] = []

        result, _ = await tools.claim_agent(
            storage, agent_id="critic", description="a critic",
            approve_id=_asked(asked), now=AT,
        )

        assert asked == ["critic"], "an approved id is asked about, not assumed"
        assert result["status"] == "claimed"

    async def test_declining_refuses_an_id_the_user_approved_earlier(self, storage):
        # Declining is the user withdrawing this identity for this bind. A list
        # they added to last week does not overrule the answer they just gave.
        await _approved(storage, "critic")

        result, _ = await tools.claim_agent(
            storage, agent_id="critic", description="a critic",
            approve_id=_declined, now=AT,
        )

        assert result["status"] == "refused"

    async def test_no_channel_still_binds_an_approved_id(self, storage):
        # The EPIMEMER_APPROVED_AGENTS / `epimemer agents confirm` path (§2.3):
        # user involvement that happened earlier rather than none. Refusing it
        # would leave a client that cannot elicit unable to judge at all.
        await _approved(storage, "critic")

        result, _ = await tools.claim_agent(
            storage, agent_id="critic", description="a critic",
            approve_id=_silent, now=AT,
        )

        assert result["status"] == "claimed"

    async def test_no_channel_does_not_admit_an_unapproved_id(self, storage):
        result, _ = await tools.claim_agent(
            storage, agent_id="self-appointed", description="a critic",
            approve_id=_silent, now=AT,
        )

        assert result["status"] == "refused"
        assert await storage.get_approved_agent_ids() == []


class TestTheCadenceMemo:
    """Asked once per session, per graph, per identity — and no wider.

    The picker goes up on every bind, so a re-claim of the same judge has to be
    silent or the user is trained to dismiss the question. What the memo must
    never do is widen: *this session confirmed something* would let an agent be
    approved as one judge and then bind as another without a word, which is the
    defect the picker exists to close, rebuilt inside its own fix.
    """

    async def test_a_confirmed_identity_is_not_asked_about_again(self, storage):
        await _approved(storage, "critic")
        asked: list[str] = []

        result, _ = await tools.claim_agent(
            storage, agent_id="critic", description="a critic",
            approve_id=_asked(asked), confirmed_identity="critic", now=AT,
        )

        assert asked == [], "the memo answers for this identity in this graph"
        assert result["status"] == "claimed"

    async def test_the_memo_does_not_cover_an_id_that_is_not_approved(self, storage):
        # A memo is a record of an answer, not a substitute for one. An id the
        # graph does not know cannot have been the identity that was confirmed.
        asked: list[str] = []

        result, _ = await tools.claim_agent(
            storage, agent_id="never-approved", description="a critic",
            approve_id=_asked(asked), confirmed_identity="never-approved", now=AT,
        )

        assert asked == ["never-approved"]
        assert result["status"] == "claimed", "asked, and the user said yes"

    async def test_a_changed_description_is_still_put_to_the_user(self, storage):
        # The memo records an identity, not a wording. A judge that re-describes
        # itself mid-session is making a new claim about what it is.
        await _approved(storage, "critic")
        await tools.claim_agent(
            storage, agent_id="critic", description="a critic",
            approve_id=_accept(), now=AT,
        )
        shown: list[str] = []

        async def confirm(claimed_id: str, text: str) -> bool:
            shown.append(text)
            return True

        result, _ = await tools.claim_agent(
            storage, agent_id="critic", description="a critic, and a reviewer",
            approve_id=_asked([]), confirm_description=confirm,
            confirmed_identity="critic", now=LATER,
        )

        assert shown == ["a critic, and a reviewer"]
        assert result["new_description"] is True


class TestTheResponseSaysWhetherTheJudgeIsNew:
    """`description_versions: 1` implied it; an implication nobody reads is not
    a signal (#78)."""

    async def test_a_first_claim_reports_a_new_judge(self, storage):
        result, _ = await tools.claim_agent(
            storage, agent_id="critic", description="a critic",
            approve_id=_accept(), now=AT,
        )

        assert result["new_agent"] is True
        assert "new judge" in result["message"]

    async def test_a_second_claim_does_not(self, storage):
        await tools.claim_agent(
            storage, agent_id="critic", description="a critic",
            approve_id=_accept(), now=AT,
        )

        result, _ = await tools.claim_agent(
            storage, agent_id="critic", description="a critic",
            approve_id=_accept(), now=LATER,
        )

        assert result["new_agent"] is False
        assert "new judge" not in result["message"]

    async def test_the_message_reads_as_sentences_either_way(self, storage):
        # Assembled from three optional clauses, so the seams are where it
        # stops being a sentence — as it did on the day it shipped, reading
        # "Judging as 'Opus 5' The user confirmed this description."
        first, _ = await tools.claim_agent(
            storage, agent_id="critic", description="a critic",
            approve_id=_accept(), now=AT,
        )
        again, _ = await tools.claim_agent(
            storage, agent_id="critic", description="a critic",
            approve_id=_accept(), now=LATER,
        )

        for message in (first["message"], again["message"]):
            assert "' The" not in message and "' This" not in message
            assert ". " in message

    async def test_the_id_the_user_handed_back_is_the_one_judged(self, storage):
        # The picker may return a judge other than the one proposed, and
        # newness is a fact about *that* judge.
        await _approved(storage, "critic")
        await tools.claim_agent(
            storage, agent_id="critic", description="a critic",
            approve_id=_accept(), now=AT,
        )

        result, _ = await tools.claim_agent(
            storage, agent_id="something-else", description="a critic",
            approve_id=_accept("critic"), now=LATER,
        )

        assert result["agent_id"] == "critic"
        assert result["new_agent"] is False


class TestTheRosterIsWhatTheUserPicksFrom:
    """The picker's lines, which are the whole of what a user has to go on.

    Tested here rather than through an elicitation channel: the content is a
    read of graph state and is testable, while the rendering is the client's and
    is not.
    """

    async def test_it_offers_agents_and_approved_ids_alike(self, storage):
        # The two differ in both directions: an id approved by config has no
        # record until something claims it, and an agent whose approval was
        # withdrawn still has a history worth seeing.
        await _approved(storage, "configured-only")
        await tools.claim_agent(
            storage, agent_id="critic", description="a critic",
            approve_id=_accept(), now=AT,
        )

        roster = await tools.judge_roster(storage)

        assert {choice.name for choice in roster} == {"critic", "configured-only"}
        # Keyed on the key, never on the name: what the picker returns is what
        # `claim_agent` resolves, and a name the user is about to change would
        # be a poor thing to return.
        assert {choice.key for choice in roster} == {
            f"use:{choice.agent_id}" for choice in roster
        }

    async def test_a_line_says_what_the_judge_was_for(self, storage):
        await tools.claim_agent(
            storage, agent_id="critic", description="reviews merge decisions",
            approve_id=_accept(), now=AT,
        )

        (choice,) = await tools.judge_roster(storage)
        assert "critic" in choice.title
        assert "reviews merge decisions" in choice.title
        assert "2026-08-22" in choice.title, "last used, so a stale judge is visible"

    async def test_an_approved_id_nothing_has_claimed_says_so(self, storage):
        await _approved(storage, "configured-only")

        (choice,) = await tools.judge_roster(storage)
        assert "never claimed" in choice.title

    async def test_the_most_recently_used_judge_comes_first(self, storage):
        # The cadence asks on every bind, which is affordable only while the
        # answer the user wants is the first one offered.
        await _approved(storage, "older", "newer", "unused")
        await tools.claim_agent(
            storage, agent_id="older", description="a critic",
            approve_id=_accept(), now=AT,
        )
        await tools.claim_agent(
            storage, agent_id="newer", description="a critic",
            approve_id=_accept(), now=LATER,
        )

        order = [choice.agent_id for choice in await tools.judge_roster(storage)]
        assert order == ["newer", "older", "unused"]

    async def test_a_long_description_is_cut_and_the_id_never_is(self, storage):
        await tools.claim_agent(
            storage, agent_id="a-judge-with-a-fairly-long-identifier",
            description="x " * 200, approve_id=_accept(), now=AT,
        )

        (choice,) = await tools.judge_roster(storage)
        assert choice.title.startswith("a-judge-with-a-fairly-long-identifier")
        assert len(choice.title) <= 90
        assert choice.title.endswith("…")

    async def test_a_choice_key_cannot_be_confused_with_the_new_judge_option(
        self, storage
    ):
        # Agent ids are user-assigned free text, validated for emptiness and
        # nothing else, so a bare sentinel would be a string somebody could
        # legitimately be called.
        await _approved(storage, tools.NEW_JUDGE_CHOICE)

        (choice,) = await tools.judge_roster(storage)
        assert choice.key != tools.NEW_JUDGE_CHOICE
        assert tools.selected_judge_id(choice.key) == tools.NEW_JUDGE_CHOICE
        assert tools.selected_judge_id(tools.NEW_JUDGE_CHOICE) is None


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
        agent = await storage.get_agent(result["agent_id"])
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
