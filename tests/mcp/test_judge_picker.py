"""The judge picker's round trips: rename, and bring a retired judge back.

`tests/mcp/test_claim_agent.py` covers what the picker *offers*, which is a read
of graph state. This covers what happens when the user chooses one of the
entries that is not a judge — those answer a different question, so the picker
has to go back up afterwards rather than treat the housekeeping as an answer.

The elicitation channel is faked with a scripted context. Rendering a choice
schema is the client's business and untestable from here; the sequence of
questions and what each answer does to the graph is ours.
"""

from datetime import UTC, datetime

import pytest
from fastmcp.server.elicitation import AcceptedElicitation

from epimemer.core.types import is_retired
from epimemer.mcp import server, tools
from epimemer.storage.memory import InMemoryStorage

AT = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)
LATER = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)


class _Declined:
    """What a client returns when the user says no. Not an `AcceptedElicitation`."""


class _ScriptedContext:
    """A `Context` that answers elicitations from a list, in order.

    Each answer is either a value the user chose or `_Declined()`. Every
    question asked is recorded, because *which questions were put, in what
    order* is most of what these tests are about.
    """

    def __init__(self, storage, answers):
        self.lifespan_context = {"storage": storage}
        self._answers = list(answers)
        self.asked: list[tuple[str, list[str]]] = []

    async def elicit(self, message, response_type=None):
        options = list(response_type) if isinstance(response_type, dict) else []
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
                    self.asked.append((message, []))
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
