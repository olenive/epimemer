"""A warning reaches the dashboard, whether or not the agent was shown it.

`WARNINGS_DASHBOARD.md` §2.2: the dashboard is where a person looks at what
the agent was *not* told, so a muted graph publishes its warnings all the same
and `surfaced` says the agent never saw them. That matches the journal, which
already records regardless of the mute.

One event per warning, never one per call, because a call raising two warnings
has two things to say and a reader filters on the kind of each.
"""

from datetime import UTC, datetime

import pytest

from epimemer.core.advisories import (
    Advisory,
    AdvisoryAction,
    AdvisoryKind,
    WarningPolicy,
)
from epimemer.core.temporal import (
    IntervalBasis,
    PreciseInstant,
    UnknownInstant,
    ValidityInterval,
)
from epimemer.core.types import (
    BASE_METACONTEXT_ID,
    ClaimKind,
    EdgeType,
    EmbeddingRecord,
    Fact,
    Inference,
    JudgeRef,
    NodeEdge,
    RawDocument,
)
from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.mcp import tools
from epimemer.visualization.event_bus import create_event_bus
from epimemer.visualization.events import AdvisoryRaised, EventCategory

CRITIC = JudgeRef(agent_id="a-critic", digest="d1")

_TWIN = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]


@pytest.fixture
def bus():
    return create_event_bus()


@pytest.fixture
def embedding_provider() -> MockEmbeddingProvider:
    return MockEmbeddingProvider(model_id="mock-embed", dimension=8)


def _recorder(bus) -> list[AdvisoryRaised]:
    received: list[AdvisoryRaised] = []
    bus.subscribe(AdvisoryRaised, handler=lambda event: received.append(event))
    return received


def _advisory(kind: AdvisoryKind, message: str = "a thing worth knowing") -> Advisory:
    return Advisory(
        kind=kind,
        message=message,
        subjects=["node-a", "node-b"],
        detail={"because": "the periods do not overlap"},
    )


async def _carry(storage, policy, advisories, *, bus=None, judge=CRITIC, tool="record_variant"):
    return await tools.carry_advisories(
        storage,
        policy,
        advisories,
        ["node-a", "node-b"],
        judge=judge,
        tool=tool,
        event_bus=bus,
    )


class TestTheEventSaysWhatTheWarningWasAndWhoSawIt:
    """§3: every field a reader of the log needs, and nothing that would need a
    second lookup to resolve."""

    def test_it_is_a_graph_event_the_frontend_can_route(self):
        event = AdvisoryRaised(
            graph="alpha",
            action_id="000000000007",
            tool="record_contradiction",
            kind=AdvisoryKind.CROSS_METACONTEXT,
            message="wrong tool",
            subjects=["a", "b"],
            detail={},
            action=AdvisoryAction.PROCEED,
            surfaced=True,
            notify_user=False,
            judged_by=None,
        )

        assert event.category is EventCategory.GRAPH
        assert event.event_type == "advisory_raised"

    def test_the_judge_is_optional_because_a_graph_need_not_require_one(self):
        event = AdvisoryRaised(
            graph="alpha",
            action_id="000000000008",
            tool="record_variant",
            kind=AdvisoryKind.SAME_METACONTEXT_VARIANT,
            message="wrong tool",
            action=AdvisoryAction.PROCEED,
            surfaced=True,
            notify_user=False,
        )

        assert event.judged_by is None
        assert event.subjects == []
        assert event.detail == {}


class TestCarryAdvisoriesPublishesOnePerWarning:
    async def test_two_warnings_are_two_events(self, storage, bus):
        received = _recorder(bus)

        await _carry(
            storage,
            WarningPolicy(),
            [
                _advisory(AdvisoryKind.DISJOINT_PREMISES, "one"),
                _advisory(AdvisoryKind.DISJOINT_PREMISES, "two"),
            ],
            bus=bus,
        )

        assert [event.message for event in received] == ["one", "two"]

    async def test_the_event_repeats_the_warning_word_for_word(self, storage, bus):
        received = _recorder(bus)
        advisory = _advisory(AdvisoryKind.CROSS_METACONTEXT, "these are different worlds")

        await _carry(storage, WarningPolicy(), [advisory], bus=bus, tool="record_contradiction")

        assert len(received) == 1
        event = received[0]
        assert event.tool == "record_contradiction"
        assert event.kind is AdvisoryKind.CROSS_METACONTEXT
        assert event.message == advisory.message
        assert event.subjects == advisory.subjects
        assert event.detail == advisory.detail
        assert event.graph == storage.current_database
        assert event.judged_by == "a-critic"

    async def test_a_call_with_nothing_to_warn_about_publishes_nothing(self, storage, bus):
        received = _recorder(bus)

        await _carry(storage, WarningPolicy(), [], bus=bus)

        assert received == []

    async def test_without_a_bus_it_does_the_same_work_and_says_nothing(self, storage):
        """The bus travels as a value, so a server with visualization off is a
        `None` here rather than a branch at every call site."""
        result = await _carry(
            storage, WarningPolicy(), [_advisory(AdvisoryKind.CROSS_METACONTEXT)], bus=None
        )

        assert result["notify_user"] is False
        assert len(result["warnings"]) == 1

    async def test_a_graph_that_requires_no_judge_publishes_without_one(self, storage, bus):
        received = _recorder(bus)

        await _carry(
            storage,
            WarningPolicy(),
            [_advisory(AdvisoryKind.CROSS_METACONTEXT)],
            bus=bus,
            judge=None,
        )

        assert received[0].judged_by is None

    async def test_every_event_takes_its_own_place_in_the_session_stream(self, storage, bus):
        """Warnings and acts share one numbering, which is what lets the log
        replay them in the order they arrived."""
        received = _recorder(bus)

        await _carry(
            storage,
            WarningPolicy(),
            [
                _advisory(AdvisoryKind.DISJOINT_PREMISES, "one"),
                _advisory(AdvisoryKind.DISJOINT_PREMISES, "two"),
            ],
            bus=bus,
        )

        assert received[0].action_id < received[1].action_id


class TestWhatTheAgentSawIsOnTheEvent:
    async def test_an_unmuted_graph_surfaces_and_says_so(self, storage, bus):
        received = _recorder(bus)

        await _carry(storage, WarningPolicy(), [_advisory(AdvisoryKind.CROSS_METACONTEXT)], bus=bus)

        assert received[0].surfaced is True
        assert received[0].action is AdvisoryAction.PROCEED
        assert received[0].notify_user is False

    async def test_a_muted_graph_still_publishes_with_surfaced_false(self, storage, bus):
        """The whole point of §2.2: hiding muted warnings on the dashboard would
        hide exactly what a person opened it to see."""
        received = _recorder(bus)

        result = await _carry(
            storage,
            WarningPolicy(surface=False),
            [_advisory(AdvisoryKind.CROSS_METACONTEXT)],
            bus=bus,
        )

        assert "warnings" not in result
        assert len(received) == 1
        assert received[0].surfaced is False
        assert received[0].notify_user is False

    async def test_a_flagged_kind_publishes_notify_user(self, storage, bus):
        received = _recorder(bus)

        await _carry(
            storage,
            WarningPolicy(),
            [_advisory(AdvisoryKind.SAME_METACONTEXT_CONTRADICTION)],
            bus=bus,
            tool="record_contradiction",
        )

        assert received[0].action is AdvisoryAction.FLAG
        assert received[0].notify_user is True

    async def test_a_named_flag_outranks_the_mute_on_the_event_too(self, storage, bus):
        """`surfaced` follows the same rule the response does, so the dashboard
        and the agent cannot come to disagree about what was shown."""
        received = _recorder(bus)

        await _carry(
            storage,
            WarningPolicy(surface=False),
            [_advisory(AdvisoryKind.SAME_METACONTEXT_CONTRADICTION)],
            bus=bus,
        )

        assert received[0].surfaced is True
        assert received[0].notify_user is True

    async def test_a_muted_flag_free_kind_cannot_be_relayed_to_anyone(self, storage, bus):
        """`notify_user` with nothing to relay is an instruction nobody can
        follow, so a warning the agent never saw never asks for a person."""
        received = _recorder(bus)

        await _carry(
            storage,
            WarningPolicy(
                surface=False,
                by_kind={AdvisoryKind.SAME_METACONTEXT_CONTRADICTION: AdvisoryAction.PROCEED},
            ),
            [_advisory(AdvisoryKind.SAME_METACONTEXT_CONTRADICTION)],
            bus=bus,
        )

        assert received[0].surfaced is False
        assert received[0].notify_user is False
        assert received[0].action is AdvisoryAction.PROCEED


class TestTheToolsPassTheBusThrough:
    """Every tool that raises a warning reaches `carry_advisories`, so each has
    to hand the bus on rather than drop it."""

    async def test_record_contradiction_publishes(self, storage, bus):
        received = _recorder(bus)
        one = await _fact(storage, "The bridge opened in 1932")
        other = await _fact(storage, "The bridge opened in 1934")

        await tools.record_contradiction(one.id, other.id, storage, event_bus=bus)

        assert [event.tool for event in received] == ["record_contradiction"]
        assert received[0].kind is AdvisoryKind.SAME_METACONTEXT_CONTRADICTION

    async def test_record_variant_publishes(self, storage, bus):
        received = _recorder(bus)
        one = await _fact(storage, "The bridge opened in 1932")
        other = await _fact(storage, "The bridge opened in 1934")

        await tools.record_variant(one.id, other.id, storage, event_bus=bus)

        assert [event.tool for event in received] == ["record_variant"]
        assert received[0].kind is AdvisoryKind.SAME_METACONTEXT_VARIANT

    async def test_merge_inferences_publishes(self, storage, bus, embedding_provider):
        received = _recorder(bus)
        one, other = await _disjoint_pair(storage, embedding_provider)

        await tools.merge_inferences(
            source_ids=[one.id, other.id],
            content="One reading.",
            storage=storage,
            embedding_provider=embedding_provider,
            event_bus=bus,
        )

        assert [event.tool for event in received] == ["merge_inferences"]
        assert received[0].kind is AdvisoryKind.DISJOINT_PREMISES


class TestReflectPublishesWhatItAttachedToACandidate:
    """`reflect` writes nothing and so never reaches `carry_advisories`, but a
    candidate's warning is still a warning the agent was shown."""

    async def test_a_candidate_warning_reaches_the_log(self, storage, bus, embedding_provider):
        received = _recorder(bus)
        await _nominated_disjoint_pair(storage, embedding_provider)

        result, _ = await tools.reflect(storage, embedding_provider, event_bus=bus)

        assert result["inference_merge_candidates"][0]["warnings"]
        assert [event.tool for event in received] == ["reflect"]
        assert received[0].kind is AdvisoryKind.DISJOINT_PREMISES

    async def test_it_was_shown_to_the_agent_and_asks_for_nobody(
        self, storage, bus, embedding_provider
    ):
        """An unmuted graph shows a candidate's warning, and `disjoint_premises`
        follows the default action, so it asks for nobody. What the event
        reports is what happened."""
        received = _recorder(bus)
        await _nominated_disjoint_pair(storage, embedding_provider)

        await tools.reflect(storage, embedding_provider, event_bus=bus)

        assert received[0].surfaced is True
        assert received[0].notify_user is False
        assert received[0].action is AdvisoryAction.PROCEED

    async def test_a_muted_graph_publishes_the_warning_it_stripped(
        self, storage, bus, embedding_provider
    ):
        """§2.2 on the reflect path: the warning never reaches the agent, and it
        still reaches the log, because the dashboard is where a person looks at
        what the agent was not told."""
        received = _recorder(bus)
        await _nominated_disjoint_pair(storage, embedding_provider)
        await tools.configure_warnings(storage, surface=False)

        result, _ = await tools.reflect(storage, embedding_provider, event_bus=bus)

        assert result["inference_merge_candidates"][0]["warnings"] == []
        assert [event.tool for event in received] == ["reflect"]
        assert received[0].kind is AdvisoryKind.DISJOINT_PREMISES
        assert received[0].surfaced is False
        assert received[0].notify_user is False
        assert received[0].action is AdvisoryAction.PROCEED

    async def test_a_named_flag_survives_the_mute_and_asks_for_a_person(
        self, storage, bus, embedding_provider
    ):
        received = _recorder(bus)
        await _nominated_disjoint_pair(storage, embedding_provider)
        await tools.configure_warnings(
            storage, surface=False, actions={"disjoint_premises": "flag"}
        )

        result, _ = await tools.reflect(storage, embedding_provider, event_bus=bus)

        assert result["inference_merge_candidates"][0]["warnings"]
        assert received[0].surfaced is True
        assert received[0].notify_user is True
        assert received[0].action is AdvisoryAction.FLAG

    async def test_a_reflect_with_nothing_to_warn_about_publishes_nothing(
        self, storage, bus, embedding_provider
    ):
        received = _recorder(bus)

        await tools.reflect(storage, embedding_provider, event_bus=bus)

        assert received == []


# --- fixtures for the tools that raise real warnings ---


async def _fact(storage, content, *, metacontext=BASE_METACONTEXT_ID):
    fact = Fact(content=content, source_id="seg-1")
    await storage.store_node(fact)
    await storage.store_edge(
        NodeEdge(src_id=fact.id, dst_id=metacontext, type=EdgeType.HAS_METACONTEXT)
    )
    return fact


async def _inference(storage, embedding_provider, content):
    inference = Inference(content=content, source_id="seg-1")
    await storage.store_node(inference)
    await storage.store_embedding(
        EmbeddingRecord(item_id=inference.id, model_id=embedding_provider.model_id, vector=_TWIN)
    )
    await storage.store_edge(
        NodeEdge(src_id=inference.id, dst_id=BASE_METACONTEXT_ID, type=EdgeType.HAS_METACONTEXT)
    )
    return inference


async def _premise(storage, content):
    fact = Fact(content=content, source_id="seg-1", claim_kind=ClaimKind.STATE)
    await storage.store_node(fact)
    return fact


async def _rests_on(storage, inference, premise):
    await storage.store_edge(
        NodeEdge(src_id=inference.id, dst_id=premise.id, type=EdgeType.DERIVED_FROM)
    )


def _year(value: int) -> PreciseInstant:
    return PreciseInstant(at=datetime(value, 1, 1, tzinfo=UTC))


async def _dated(storage, premise, name, interval):
    document = RawDocument(content=f"contents of {name}", source=name)
    await storage.store_document(document)
    await storage.store_edge(
        NodeEdge(
            src_id=premise.id,
            dst_id=document.id,
            type=EdgeType.SOURCED_FROM,
            validity=[interval],
        )
    )


async def _periods(storage):
    """Two premises no source puts in one period."""
    early = await _premise(storage, "Leningrad is the city's name")
    late = await _premise(storage, "Saint Petersburg is the city's name")
    await _dated(
        storage,
        early,
        "atlas-1970",
        ValidityInterval(start=_year(1924), end=_year(1991), basis=IntervalBasis.STATED),
    )
    await _dated(
        storage,
        late,
        "atlas-2020",
        ValidityInterval(start=_year(1991), end=UnknownInstant(), basis=IntervalBasis.STATED),
    )
    return early, late


async def _disjoint_pair(storage, embedding_provider):
    """Two readings `merge_inferences` would collapse, one premise each."""
    one = await _inference(storage, embedding_provider, "The name changed once")
    other = await _inference(storage, embedding_provider, "The name has changed once")
    early, late = await _periods(storage)
    await _rests_on(storage, one, early)
    await _rests_on(storage, other, late)
    return one, other


async def _nominated_disjoint_pair(storage, embedding_provider):
    """The same pair, sharing a premise so `reflect` nominates it at all.

    Nomination is scoped to inferences resting on shared evidence, and the
    warning is computed over the *union* of their premises, which is what the
    survivor would rest on.
    """
    one, other = await _disjoint_pair(storage, embedding_provider)
    shared = await _premise(storage, "The city was renamed by decree")
    await _rests_on(storage, one, shared)
    await _rests_on(storage, other, shared)
    return one, other
