"""The judge picker: what its prompt says, and its round trips.

`tests/mcp/test_claim_agent.py` covers what the picker *offers*, which is a read
of graph state. This covers what happens when the user chooses one of the
entries that is not a judge — those answer a different question, so the picker
has to go back up afterwards rather than treat the housekeeping as an answer.

It also covers the words the user reads, since one connection carries several
agents and the prompt is the only place that says which of them is asking.

The elicitation channel is faked with a scripted context. Rendering a choice
schema is the client's business and untestable from here; the sequence of
questions, the text of each, and what each answer does to the graph are ours.
"""

from datetime import UTC, datetime

import pytest
from fastmcp.server.elicitation import AcceptedElicitation

from epimemer.core.types import JudgeRef, is_retired
from epimemer.mcp import server, tools
from epimemer.storage.memory import InMemoryStorage

AT = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)
LATER = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
# The server version the scripted context reports, which every prompt opens
# with. Deliberately not the real one: a test that read the package version
# would pass whether or not the prompt carried it.
VERSION = "9.9.9"


class _Declined:
    """What a client returns when the user says no. Not an `AcceptedElicitation`."""


class _ScriptedContext:
    """A `Context` that answers elicitations from a list, in order.

    Each answer is either a value the user chose or `_Declined()`. Every
    question asked is recorded, because *which questions were put, in what
    order* is most of what these tests are about.
    """

    def __init__(self, storage, answers, bound: str | None = None):
        self.lifespan_context = {"storage": storage, "version": VERSION}
        self._answers = list(answers)
        self.asked: list[tuple[str, dict[str, str]]] = []
        # The judge this connection already holds, where a real `Context` holds
        # it: session state. None is a connection nothing has claimed on yet.
        self._state = {
            server.JUDGE_STATE_KEY: None
            if bound is None
            else JudgeRef(agent_id=bound, digest="a-description-version").model_dump(mode="json")
        }

    async def get_state(self, key):
        return self._state.get(key)

    async def elicit(self, message, response_type=None):
        # The titles as well as the keys: what a line selects and what it says
        # are both the picker's business. A free-text prompt offers neither.
        options = (
            {key: choice["title"] for key, choice in response_type.items()}
            if isinstance(response_type, dict)
            else {}
        )
        self.asked.append((message, options))
        if not self._answers:
            raise AssertionError(f"the picker asked one question too many: {message}")
        answer = self._answers.pop(0)
        if isinstance(answer, _Declined):
            return answer
        return AcceptedElicitation(data=answer)


@pytest.fixture
async def storage():
    return InMemoryStorage()


async def _judge(storage, name: str, *, now=AT) -> str:
    async def approve(proposed, description):
        return tools.ApprovalOutcome(chosen=proposed)

    result, _ = await tools.claim_agent(
        storage,
        agent_id=name,
        description=f"{name}, a critic",
        approve_id=approve,
        now=now,
    )
    return result["agent_id"]


class TestTheRetiredEntryIsOfferedOnlyWhenItLeadsSomewhere:
    async def test_a_graph_with_no_retired_judge_does_not_offer_it(self, storage):
        await _judge(storage, "critic")
        ctx = _ScriptedContext(storage, [f"{tools.JUDGE_CHOICE_PREFIX}whatever"])

        await server._elicit_agent_id(ctx, "critic", "a critic")

        (_, options) = ctx.asked[0]
        assert tools.RETIRED_JUDGE_CHOICE not in options
        assert tools.RENAME_JUDGE_CHOICE in options

    async def test_a_graph_with_one_offers_it_and_leaves_it_off_the_main_list(self, storage):
        await _judge(storage, "critic")
        stale = await _judge(storage, "stale")
        await tools.retire_judge(storage, handle="stale", now=LATER)
        ctx = _ScriptedContext(storage, [_Declined()])

        await server._elicit_agent_id(ctx, "critic", "a critic")

        (_, options) = ctx.asked[0]
        assert tools.RETIRED_JUDGE_CHOICE in options
        assert f"{tools.JUDGE_CHOICE_PREFIX}{stale}" not in options

    async def test_a_graph_whose_every_judge_is_retired_still_gets_a_picker(self, storage):
        """Falling through to free text here would leave the one way back
        unreachable: the user would have to type a name that is refused."""
        await _judge(storage, "stale")
        await tools.retire_judge(storage, handle="stale", now=LATER)
        ctx = _ScriptedContext(storage, [_Declined()])

        await server._elicit_agent_id(ctx, "critic", "a critic")

        (_, options) = ctx.asked[0]
        assert tools.RETIRED_JUDGE_CHOICE in options
        # Nothing to rename, so the entry that would open an empty list is gone.
        assert tools.RENAME_JUDGE_CHOICE not in options


class TestReinstatingFromThePickerTakesTwoGestures:
    """A judge somebody deliberately took out of use must not come back and be
    bound by one keystroke. Bringing it back returns to the main picker, which
    now shows it, and choosing it is a separate answer."""

    async def test_the_sub_picker_reinstates_and_the_main_picker_goes_back_up(self, storage):
        key = await _judge(storage, "stale")
        await tools.retire_judge(storage, handle="stale", now=LATER)
        ctx = _ScriptedContext(
            storage,
            [
                tools.RETIRED_JUDGE_CHOICE,
                f"{tools.JUDGE_CHOICE_PREFIX}{key}",
                f"{tools.JUDGE_CHOICE_PREFIX}{key}",
            ],
        )

        outcome = await server._elicit_agent_id(ctx, "stale", "an old judge")

        assert outcome.chosen == key
        assert not is_retired(await storage.get_agent(key))
        assert len(ctx.asked) == 3, "main picker, which retired judge, main picker again"
        # The second round offers it on the main list, which is what makes the
        # second gesture a choice rather than a formality.
        assert f"{tools.JUDGE_CHOICE_PREFIX}{key}" in ctx.asked[2][1]
        assert tools.RETIRED_JUDGE_CHOICE not in ctx.asked[2][1]

    async def test_declining_the_sub_picker_leaves_it_retired_and_returns(self, storage):
        key = await _judge(storage, "stale")
        await _judge(storage, "critic")
        await tools.retire_judge(storage, handle="stale", now=LATER)
        ctx = _ScriptedContext(storage, [tools.RETIRED_JUDGE_CHOICE, _Declined(), _Declined()])

        outcome = await server._elicit_agent_id(ctx, "critic", "a critic")

        assert outcome.chosen is None, "declining the main picker refuses an identity"
        assert is_retired(await storage.get_agent(key))
        assert len(ctx.asked) == 3

    async def test_reinstating_one_of_several_leaves_the_others_alone(self, storage):
        first = await _judge(storage, "first")
        second = await _judge(storage, "second")
        await tools.retire_judge(storage, handle="first", now=AT)
        await tools.retire_judge(storage, handle="second", now=LATER)
        ctx = _ScriptedContext(
            storage,
            [tools.RETIRED_JUDGE_CHOICE, f"{tools.JUDGE_CHOICE_PREFIX}{first}", _Declined()],
        )

        await server._elicit_agent_id(ctx, "first", "a critic")

        assert not is_retired(await storage.get_agent(first))
        assert is_retired(await storage.get_agent(second))
        sub_picker = ctx.asked[1][1]
        assert set(sub_picker) == {
            f"{tools.JUDGE_CHOICE_PREFIX}{first}",
            f"{tools.JUDGE_CHOICE_PREFIX}{second}",
        }

    async def test_a_client_that_cannot_render_the_sub_picker_refuses_nothing(self, storage):
        """Every failure in this flow reads as *no answer*, and no answer here
        means the judge stays retired and the main question is asked again."""

        class _NoSubPicker(_ScriptedContext):
            async def elicit(self, message, response_type=None):
                if "retired judge should be brought back" in message:
                    self.asked.append((message, {}))
                    raise RuntimeError("this client cannot render a choice schema")
                return await super().elicit(message, response_type)

        key = await _judge(storage, "stale")
        await tools.retire_judge(storage, handle="stale", now=LATER)
        ctx = _NoSubPicker(storage, [tools.RETIRED_JUDGE_CHOICE, _Declined()])

        outcome = await server._elicit_agent_id(ctx, "stale", "an old judge")

        assert outcome.chosen is None
        assert is_retired(await storage.get_agent(key))


class TestThePickerStillPutsItselfBackUpAfterARename:
    """The round trip reinstating copies. Kept here so that a change to one is
    measured against the other."""

    async def test_renaming_returns_to_the_picker_showing_the_new_name(self, storage):
        key = await _judge(storage, "Opus 5 Judge")
        ctx = _ScriptedContext(
            storage,
            [
                tools.RENAME_JUDGE_CHOICE,
                f"{tools.JUDGE_CHOICE_PREFIX}{key}",
                "Opus 5",
                f"{tools.JUDGE_CHOICE_PREFIX}{key}",
            ],
        )

        outcome = await server._elicit_agent_id(ctx, "Opus 5 Judge", "a critic")

        assert outcome.chosen == key
        assert (await storage.get_agent(key)).name == "Opus 5"
        assert len(ctx.asked) == 4, "main picker, which judge, what name, main picker again"


class TestThePickerSaysWhoIsAsking:
    """One connection carries several agents, a subagent beside the agent that
    spawned it, and each of them claims. The picker has to say which one is
    asking, or the user is assigning an identity to somebody they cannot name.
    """

    async def test_the_picker_opens_with_the_proposed_name(self, storage):
        await _judge(storage, "critic")
        ctx = _ScriptedContext(storage, [_Declined()])

        await server._elicit_agent_id(ctx, "Opus 5", "a critic")

        (message, _) = ctx.asked[0]
        assert message.splitlines()[0] == (
            f"Epimemer {VERSION}. An agent proposing 'Opus 5' asks which judge "
            f"it is, in graph '{storage.current_database}'."
        )

    async def test_the_picker_says_when_the_connection_already_has_a_different_judge(self, storage):
        bound = await _judge(storage, "critic")
        await _judge(storage, "Opus 5")
        ctx = _ScriptedContext(storage, [_Declined()], bound=bound)

        await server._elicit_agent_id(ctx, "Opus 5", "a second agent")

        (message, _) = ctx.asked[0]
        assert message.splitlines()[1] == (
            "This connection already judges as 'critic', so this is most "
            "likely a second agent beside it."
        )
        assert bound not in message, "the name the user reads, never the opaque key"

    async def test_the_picker_says_when_the_claim_repeats_the_bound_judge(self, storage):
        bound = await _judge(storage, "critic")
        ctx = _ScriptedContext(storage, [_Declined()], bound=bound)

        await server._elicit_agent_id(ctx, "critic", "a critic")

        (message, _) = ctx.asked[0]
        assert message.splitlines()[1] == (
            "This connection already judges as 'critic', so this is a re-claim of the same judge."
        )

    async def test_a_re_claim_proposing_the_key_reads_as_the_same_judge(self, storage):
        """A claim hands back a key and an agent may propose that key straight
        back, so the key and the name have to resolve to one judge here too."""
        bound = await _judge(storage, "critic")
        ctx = _ScriptedContext(storage, [_Declined()], bound=bound)

        await server._elicit_agent_id(ctx, bound, "a critic")

        (message, _) = ctx.asked[0]
        assert "so this is a re-claim of the same judge." in message

    async def test_the_picker_says_nothing_about_a_binding_when_none_exists(self, storage):
        await _judge(storage, "critic")
        ctx = _ScriptedContext(storage, [_Declined()])

        await server._elicit_agent_id(ctx, "critic", "a critic")

        (message, _) = ctx.asked[0]
        assert "This connection already judges" not in message

    async def test_the_self_description_is_still_last(self, storage):
        bound = await _judge(storage, "critic")
        ctx = _ScriptedContext(storage, [_Declined()], bound=bound)

        await server._elicit_agent_id(ctx, "Opus 5", "an agent that reads code")

        (message, _) = ctx.asked[0]
        assert message.endswith("It describes itself as: an agent that reads code")
        assert message.index("Decline to refuse it an identity") < message.index(
            "an agent that reads code"
        )

    async def test_the_free_text_fallback_says_it_too(self, storage):
        """The picker degrades to free text where a client cannot render a
        choice schema, and the two paths have to agree about who is asking."""
        bound = await _judge(storage, "critic")
        ctx = _ScriptedContext(storage, ["Opus 5"], bound=bound)

        await server._elicit_new_judge_name(ctx, "Opus 5", "a second agent")

        (message, _) = ctx.asked[0]
        assert message.splitlines()[1] == (
            "This connection already judges as 'critic', so this is most "
            "likely a second agent beside it."
        )


class TestTheProposalIsShownByName:
    """A claim hands back an opaque key and an agent may propose that key on its
    next claim, so the handle reaching these prompts is a name or a UUID. A user
    shown a UUID has nothing to recognise a judge by, and one of them picked the
    wrong judge reading exactly that. Every prompt that names the proposal
    resolves it first, and they all resolve it the same way.
    """

    A_STRANGE_KEY = "5124f64a-38d7-4d81-a006-59aa02715f00"

    async def test_a_proposal_given_as_a_key_is_shown_by_name(self, storage):
        key = await _judge(storage, "critic")
        ctx = _ScriptedContext(storage, [_Declined()])

        await server._elicit_agent_id(ctx, key, "a critic")

        (message, _) = ctx.asked[0]
        assert message.splitlines()[0] == (
            f"Epimemer {VERSION}. An agent proposing 'critic' asks which judge "
            f"it is, in graph '{storage.current_database}'."
        )
        assert key not in message, "the name the user reads, never the opaque key"

    async def test_a_proposal_given_as_a_former_key_is_shown_by_name(self, storage):
        """Consolidating two records leaves the absorbed key answering for the
        survivor, and a returning agent may still be holding that key."""
        absorbed = await _judge(storage, "Opus 5 Judge")
        await _judge(storage, "Opus 5", now=LATER)
        await tools.rename_judge(storage, handle="Opus 5 Judge", name="Opus 5", same_judge=True)
        ctx = _ScriptedContext(storage, [_Declined()])

        await server._elicit_agent_id(ctx, absorbed, "a critic")

        (message, _) = ctx.asked[0]
        assert message.splitlines()[0].startswith(
            f"Epimemer {VERSION}. An agent proposing 'Opus 5' asks"
        )
        assert absorbed not in message

    async def test_an_unresolved_proposal_is_shown_as_given(self, storage):
        """A handle this graph has never seen has no name to show instead, so it
        is shown as it came, key-shaped or not."""
        await _judge(storage, "critic")
        ctx = _ScriptedContext(storage, [_Declined()])

        await server._elicit_agent_id(ctx, self.A_STRANGE_KEY, "a critic")

        (message, options) = ctx.asked[0]
        assert message.splitlines()[0].startswith(
            f"Epimemer {VERSION}. An agent proposing '{self.A_STRANGE_KEY}' asks"
        )
        assert options[tools.PROPOSED_JUDGE_CHOICE] == f"A new judge: '{self.A_STRANGE_KEY}'"

    async def test_the_free_text_fallback_shows_the_name_too(self, storage):
        key = await _judge(storage, "critic")
        ctx = _ScriptedContext(storage, [_Declined()])

        await server._elicit_new_judge_name(ctx, key, "a critic")

        (message, _) = ctx.asked[0]
        assert "Accept to use 'critic'" in message
        assert key not in message

    async def test_the_new_judge_choice_never_presents_an_existing_name_as_new(self, storage):
        """The judge the proposal resolves to is already on the list as itself,
        so offering its name as new is an invitation to split its history."""
        key = await _judge(storage, "critic")
        ctx = _ScriptedContext(storage, [_Declined()])

        await server._elicit_agent_id(ctx, key, "a critic")

        (_, options) = ctx.asked[0]
        assert options[tools.NEW_JUDGE_CHOICE] == "A new judge, with a name you type"
        assert "critic" not in options[tools.NEW_JUDGE_CHOICE]


class _NoChoices(_ScriptedContext):
    """A client that cannot render a choice schema, which is the only reason
    the free-text fallback exists. Every choice prompt raises; free text is
    answered from the script as usual."""

    async def elicit(self, message, response_type=None):
        if isinstance(response_type, dict):
            self.asked.append((message, {}))
            raise RuntimeError("this client cannot render a choice schema")
        return await super().elicit(message, response_type)


class TestEveryPromptSaysWhichServerIsAsking:
    """The user asked to see which Epimemer they are talking to while they are
    choosing a judge, and the version is the first thing on every prompt: it is
    short, and the terminal cuts the end of a long message rather than the
    start.

    All of them, because a version on some prompts and not others reads as a
    difference between the prompts rather than as one server answering.
    """

    async def test_every_claim_prompt_opens_with_the_version(self, storage):
        messages: list[str] = []

        # A graph with no judges: the two-choice picker, then free text.
        empty = _ScriptedContext(storage, [tools.NEW_JUDGE_CHOICE, "Opus 5"])
        await server._elicit_agent_id(empty, "Opus 5", "a critic")
        messages += [message for message, _ in empty.asked]

        # The main picker, and the two prompts a rename opens under it.
        key = await _judge(storage, "critic")
        renaming = _ScriptedContext(
            storage,
            [
                tools.RENAME_JUDGE_CHOICE,
                f"{tools.JUDGE_CHOICE_PREFIX}{key}",
                "Opus 5 Judge",
                _Declined(),
            ],
        )
        await server._elicit_agent_id(renaming, "critic", "a critic")
        messages += [message for message, _ in renaming.asked]

        # The main picker again, and the prompt that brings a retired judge back.
        await tools.retire_judge(storage, handle=key, now=LATER)
        reinstating = _ScriptedContext(
            storage,
            [tools.RETIRED_JUDGE_CHOICE, f"{tools.JUDGE_CHOICE_PREFIX}{key}", _Declined()],
        )
        await server._elicit_agent_id(reinstating, "Opus 5 Judge", "a critic")
        messages += [message for message, _ in reinstating.asked]

        # The free-text fallback, for a client that renders no choices.
        fallback = _ScriptedContext(storage, [_Declined()])
        await server._elicit_new_judge_name(fallback, "Opus 5", "a critic")
        messages += [message for message, _ in fallback.asked]

        # And the description confirmation, which is a second prompt on a claim
        # the user has already answered once.
        describing = _ScriptedContext(storage, [_Declined()])
        await server._elicit_description_confirmation(describing, "critic", "a critic")
        messages += [message for message, _ in describing.asked]

        assert len(messages) == 11, "every prompt in the flows above was captured"
        for message in messages:
            assert message.startswith(f"Epimemer {VERSION}. "), message


class TestAGraphWithNoJudgesOffersTheProposal:
    """A required text field cannot express *accept the proposal*.

    The free-text prompt said `Accept to use 'X', or type another name`, and a
    client that renders a plain string as a required field answers `This field
    is required`: the prompt promised something the client refused. So the
    first judge is chosen the same way every later one is, from a list.
    """

    async def test_an_empty_graph_offers_the_proposal_as_a_choice(self, storage):
        ctx = _ScriptedContext(storage, [_Declined()])

        await server._elicit_agent_id(ctx, "Opus 5", "a critic")

        (message, options) = ctx.asked[0]
        assert len(options) == 2, "the proposal, and a name the user types"
        assert options[tools.PROPOSED_JUDGE_CHOICE] == "Use 'Opus 5'"
        assert options[tools.NEW_JUDGE_CHOICE] == "Type another name"
        assert message.endswith("It describes itself as: a critic")

    async def test_choosing_the_proposal_binds_it_without_typing(self, storage):
        ctx = _ScriptedContext(storage, [tools.PROPOSED_JUDGE_CHOICE])

        outcome = await server._elicit_agent_id(ctx, "Opus 5", "a critic")

        assert outcome.chosen == "Opus 5"
        assert len(ctx.asked) == 1, "accepting the proposal asks nothing further"

    async def test_typing_another_name_follows_the_second_choice(self, storage):
        ctx = _ScriptedContext(storage, [tools.NEW_JUDGE_CHOICE, "Fable 5.1"])

        outcome = await server._elicit_agent_id(ctx, "Opus 5", "a critic")

        assert outcome.chosen == "Fable 5.1"
        (message, options) = ctx.asked[1]
        assert options == {}, "free text, since the user asked to type a name"
        assert message.splitlines()[0] == (f"Epimemer {VERSION}. Type the name for the new judge.")

    async def test_an_empty_typed_name_is_a_decline(self, storage):
        """Having chosen to type a name, typing none is a refusal to name one.
        Reading it as the proposal would bind a judge the user just declined to
        accept one prompt earlier."""
        ctx = _ScriptedContext(storage, [tools.NEW_JUDGE_CHOICE, "   "])

        outcome = await server._elicit_agent_id(ctx, "Opus 5", "a critic")

        assert outcome.chosen is None
        assert outcome.channel_available, "a refusal, not a client that cannot ask"

    async def test_declining_the_two_choice_picker_refuses_an_identity(self, storage):
        ctx = _ScriptedContext(storage, [_Declined()])

        outcome = await server._elicit_agent_id(ctx, "Opus 5", "a critic")

        assert outcome.chosen is None
        assert outcome.channel_available

    async def test_the_picker_fallback_still_reads_an_empty_accept_as_the_proposal(self, storage):
        """The fallback exists only because the choices did not render, so its
        Accept is the one gesture left that can mean *yes, that name*."""
        ctx = _NoChoices(storage, [""])

        outcome = await server._elicit_agent_id(ctx, "Opus 5", "a critic")

        assert outcome.chosen == "Opus 5"
        assert "Accept to use 'Opus 5'" in ctx.asked[1][0]


class TestTheMainPickerOffersTheProposalToo:
    """The same gap, on the picker a graph with judges draws.

    The main picker rolled *take the proposed name* and *type a name* into one
    entry, and the free-text prompt behind it said `Accept to use 'X'`. Claude
    Code draws a bare string as a required field, so that Accept was not a
    gesture the user could make and taking the proposed name meant retyping it
    character for character. The proposal gets its own line here as it does in
    an empty graph, and the entry that leads to typing promises nothing about
    Accept.
    """

    async def test_the_main_picker_offers_the_proposal_as_its_own_line(self, storage):
        await _judge(storage, "critic")
        ctx = _ScriptedContext(storage, [_Declined()])

        await server._elicit_agent_id(ctx, "Opus 5", "a critic")

        (_, options) = ctx.asked[0]
        assert options[tools.PROPOSED_JUDGE_CHOICE] == "A new judge: 'Opus 5'"
        assert options[tools.NEW_JUDGE_CHOICE] == "A new judge, with a name you type"

    async def test_choosing_the_proposed_line_binds_it_without_typing(self, storage):
        await _judge(storage, "critic")
        ctx = _ScriptedContext(storage, [tools.PROPOSED_JUDGE_CHOICE])

        outcome = await server._elicit_agent_id(ctx, "Opus 5", "a critic")

        assert outcome.chosen == "Opus 5"
        assert len(ctx.asked) == 1, "taking the proposed name asks nothing further"

    async def test_the_main_picker_hides_the_proposed_line_when_the_proposal_is_an_existing_judge(
        self, storage
    ):
        """That judge is on the roster already, so a second line offering its
        name as new is an invitation to split its history."""
        key = await _judge(storage, "critic")
        ctx = _ScriptedContext(storage, [_Declined()])

        await server._elicit_agent_id(ctx, key, "a critic")

        (_, options) = ctx.asked[0]
        assert tools.PROPOSED_JUDGE_CHOICE not in options
        assert options[tools.NEW_JUDGE_CHOICE] == "A new judge, with a name you type"

    async def test_the_typed_line_from_the_main_picker_treats_an_empty_name_as_a_decline(
        self, storage
    ):
        """The proposal was on the screen a moment ago and the user passed over
        it, so binding it on an empty answer would bind the name they declined."""
        await _judge(storage, "critic")
        ctx = _ScriptedContext(storage, [tools.NEW_JUDGE_CHOICE, "   "])

        outcome = await server._elicit_agent_id(ctx, "Opus 5", "a critic")

        assert outcome.chosen is None
        assert outcome.channel_available, "a refusal, not a client that cannot ask"
        (message, options) = ctx.asked[1]
        assert options == {}, "free text, since the user asked to type a name"
        assert message.splitlines()[0] == (f"Epimemer {VERSION}. Type the name for the new judge.")

    async def test_the_free_text_fallback_is_reached_only_when_no_picker_can_be_drawn(
        self, storage
    ):
        """Where the choices did render, nothing leads to the prompt whose
        Accept means the proposal. Where they did not, a bare Accept is the one
        gesture the client has left that can mean *yes, that name*."""
        await _judge(storage, "critic")
        ctx = _NoChoices(storage, [""])

        outcome = await server._elicit_agent_id(ctx, "Opus 5", "a critic")

        assert outcome.chosen == "Opus 5"
        assert ctx.asked[0][1] == {}, "the picker was attempted and could not be drawn"
        assert "Accept to use 'Opus 5'" in ctx.asked[1][0]
