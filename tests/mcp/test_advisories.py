"""Advisories, the policy that governs them, and the record they leave.

Three things that are only useful together: the system telling an agent what is
wrong with what it is about to do *before* it does it; a way for a user to turn
that surfacing on and off, globally and per kind, without a singleton; and what
persists when an agent proceeds past one.

**Recording is not a setting.** `surface` gates the response and never the
journal row, because a graph whose warnings were switched off for a month should
still answer *what was decided while nobody was looking* — which is exactly when
that question is worth asking.
"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from epimemer.core.advisories import (
    ADVISORY_STANCE,
    Advisory,
    AdvisoryAction,
    AdvisoryKind,
    AdvisoryStance,
    WarningPolicy,
    notify_user,
    objects_to_the_call,
    resolved_action,
    surfaced,
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
    DecisionKind,
    EdgeType,
    EmbeddingRecord,
    Fact,
    Inference,
    JudgeRef,
    Metacontext,
    NodeEdge,
    RawDocument,
)
from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.mcp import tools
from epimemer.mcp.config import ServerConfig
from epimemer.pipelines.review.modes import MODE_KINDS, REVIEW_MODES
from epimemer.storage.protocol import WarningOverrides, resolve_warning_policy
from epimemer.visualization.event_bus import create_event_bus

CRITIC = JudgeRef(agent_id="a-critic", digest="d1")

# Two inferences an embedding provider cannot tell apart, which is what puts
# them in front of the merge nominator at all.
_TWIN = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]


@pytest.fixture
def embedding_provider() -> MockEmbeddingProvider:
    return MockEmbeddingProvider(model_id="mock-embed", dimension=8)


async def _fact(storage, content, *, metacontext=BASE_METACONTEXT_ID):
    fact = Fact(content=content, source_id="seg-1")
    await storage.store_node(fact)
    if metacontext is not None:
        await storage.store_edge(
            NodeEdge(
                src_id=fact.id,
                dst_id=metacontext,
                type=EdgeType.HAS_METACONTEXT,
            )
        )
    return fact


async def _elsewhere(storage, fact, label):
    metacontext = Metacontext(content=label)
    await storage.store_metacontext(metacontext)
    await storage.store_edge(
        NodeEdge(
            src_id=fact.id,
            dst_id=metacontext.id,
            type=EdgeType.HAS_METACONTEXT,
        )
    )
    return metacontext.id


class TestThePolicyResolvesWithoutASingleton:
    """Settings are a value passed explicitly, exactly as `ServerConfig` is.

    Tests run two backends and many graphs in one process, so a process-wide
    mutable instance makes every test that changes a setting order-dependent with
    every test that reads one — and the policy is per graph, so one instance
    could not answer *what is the policy here* after a `use_graph` anyway.
    """

    def test_a_kind_nobody_named_takes_the_default_action(self):
        policy = WarningPolicy(default_action=AdvisoryAction.FLAG, by_kind={})

        assert resolved_action(policy, AdvisoryKind.DISJOINT_PREMISES) is (AdvisoryAction.FLAG)

    def test_a_same_metacontext_contradiction_flags_out_of_the_box(self):
        """Not a preference — a compatibility requirement. `record_contradiction`
        has always notified on a same-metacontext pair, and a default of `proceed`
        would have kept the key while quietly changing its trigger."""
        assert (
            resolved_action(WarningPolicy(), AdvisoryKind.SAME_METACONTEXT_CONTRADICTION)
            is AdvisoryAction.FLAG
        )

    def test_an_override_of_one_kind_keeps_the_defaults_for_the_others(self):
        """A map override that silently dropped unnamed keys is the same class of
        bug as a field-by-field rebuild forgetting a field."""
        resolved = resolve_warning_policy(
            WarningOverrides(by_kind={AdvisoryKind.DISJOINT_PREMISES: AdvisoryAction.FLAG}),
            WarningPolicy(),
        )

        assert resolved.by_kind[AdvisoryKind.DISJOINT_PREMISES] is AdvisoryAction.FLAG
        assert resolved.by_kind[AdvisoryKind.SAME_METACONTEXT_CONTRADICTION] is (
            AdvisoryAction.FLAG
        )

    def test_a_graph_can_quieten_a_kind_the_default_escalates(self):
        resolved = resolve_warning_policy(
            WarningOverrides(
                by_kind={AdvisoryKind.SAME_METACONTEXT_CONTRADICTION: AdvisoryAction.PROCEED}
            ),
            WarningPolicy(),
        )

        assert (
            resolved_action(resolved, AdvisoryKind.SAME_METACONTEXT_CONTRADICTION)
            is AdvisoryAction.PROCEED
        )

    def test_no_override_follows_the_default_rather_than_freezing_it(self):
        """`None` means *follow the process default at the time*, so a default
        changed later still reaches a graph that was configured once and cleared."""
        stricter = WarningPolicy(surface=False)

        assert resolve_warning_policy(WarningOverrides(), stricter).surface is False
        assert resolve_warning_policy(None, stricter).surface is False

    def test_two_configs_do_not_share_one_policy(self):
        """A shared mutable default would be a singleton reached by accident."""
        first, second = ServerConfig(), ServerConfig()
        first.warning_policy.by_kind[AdvisoryKind.CROSS_METACONTEXT] = AdvisoryAction.FLAG

        assert AdvisoryKind.CROSS_METACONTEXT not in second.warning_policy.by_kind

    def test_an_explicitly_named_flag_outranks_the_global_mute(self):
        """Specific beats general, the rule every `resolve_*` here keeps.

        Naming a kind in `by_kind` is a stronger statement than a switch that
        names none, so muting the graph does not withdraw an escalation somebody
        asked for by name — and `notify_user: true` with no text to relay would
        be an instruction nobody can follow.
        """
        advisories = [
            Advisory(kind=AdvisoryKind.CROSS_METACONTEXT, message="x"),
            Advisory(kind=AdvisoryKind.SAME_METACONTEXT_CONTRADICTION, message="y"),
        ]

        assert len(surfaced(WarningPolicy(), advisories)) == 2
        muted = surfaced(WarningPolicy(surface=False), advisories)
        assert [a.kind for a in muted] == [AdvisoryKind.SAME_METACONTEXT_CONTRADICTION]
        assert notify_user(WarningPolicy(), advisories) is True

    def test_a_kind_following_the_default_action_is_silenced_by_the_mute(self):
        """The exception is narrow on purpose: a kind nobody named is general,
        however the general default is set."""
        advisories = [Advisory(kind=AdvisoryKind.CROSS_METACONTEXT, message="x")]
        loud = WarningPolicy(surface=False, default_action=AdvisoryAction.FLAG, by_kind={})

        assert surfaced(loud, advisories) == []

    def test_setting_a_named_kind_to_proceed_is_how_an_escalation_is_withdrawn(
        self,
    ):
        advisories = [Advisory(kind=AdvisoryKind.SAME_METACONTEXT_CONTRADICTION, message="y")]
        quiet = WarningPolicy(
            surface=False,
            by_kind={AdvisoryKind.SAME_METACONTEXT_CONTRADICTION: AdvisoryAction.PROCEED},
        )

        assert surfaced(quiet, advisories) == []
        assert notify_user(quiet, advisories) is False

    def test_reject_is_absent_rather_than_reserved(self):
        """A value nothing can produce is worse than no value at all: a caller
        writes a branch for it and the branch is dead."""
        assert {action.value for action in AdvisoryAction} == {"proceed", "flag"}


class TestEachKindGivesExactlyOneKindOfAdvice:
    """The defect the fourth kind fixed: `SAME_METACONTEXT_CONTRADICTION` was raised
    both where the tool was right and where it was wrong, so one kind carried
    opposite advice — the *field that needs "or" to describe it* tell.

    The classification decides whether a `proceeded_despite_warning` row is
    written, so a kind added without one would silently take the safer-sounding
    half of a question nobody asked.
    """

    def test_every_kind_is_classified_and_nothing_else_is(self):
        assert set(ADVISORY_STANCE) == set(AdvisoryKind)

    def test_the_kinds_that_endorse_the_call_are_named_one_by_one(self):
        """An inventory rather than a rule, so a kind cannot join the
        non-journalling half by being added: a same-metacontext contradiction
        is a right call that found something, and a description not written is
        a right call the graph declined one write inside."""
        endorsing = {
            kind for kind, stance in ADVISORY_STANCE.items() if stance is AdvisoryStance.ESCALATES
        }
        assert endorsing == {
            AdvisoryKind.SAME_METACONTEXT_CONTRADICTION,
            AdvisoryKind.DESCRIPTION_NOT_WRITTEN,
        }

    def test_an_escalating_advisory_has_nothing_to_proceed_despite(self):
        assert (
            objects_to_the_call(
                [Advisory(kind=AdvisoryKind.SAME_METACONTEXT_CONTRADICTION, message="y")]
            )
            is False
        )
        assert (
            objects_to_the_call([Advisory(kind=AdvisoryKind.SAME_METACONTEXT_VARIANT, message="y")])
            is True
        )

    def test_a_mixed_set_objects_if_anything_in_it_does(self):
        assert (
            objects_to_the_call(
                [
                    Advisory(kind=AdvisoryKind.SAME_METACONTEXT_CONTRADICTION, message="y"),
                    Advisory(kind=AdvisoryKind.DISJOINT_PREMISES, message="x"),
                ]
            )
            is True
        )


class TestConfigureWarnings:
    async def test_it_reports_what_is_in_force_without_changing_anything(self, storage):
        result, _ = await tools.configure_warnings(storage)

        assert result["surface"] is True
        assert result["actions"]["same_metacontext_contradiction"] == "flag"
        assert result["actions"]["disjoint_premises"] == "proceed"
        assert result["overridden"] == {}

    async def test_setting_one_kind_leaves_the_rest_inherited(self, storage):
        await tools.configure_warnings(storage, actions={"disjoint_premises": "flag"})

        result, _ = await tools.configure_warnings(storage)

        assert result["actions"]["disjoint_premises"] == "flag"
        assert result["actions"]["same_metacontext_contradiction"] == "flag"
        # Which answers this graph gave, as opposed to inherited — the two are
        # different, because only the second tracks a changed default.
        assert result["overridden"] == {"by_kind": {"disjoint_premises": "flag"}}

    async def test_a_second_call_merges_rather_than_replacing(self, storage):
        await tools.configure_warnings(storage, actions={"disjoint_premises": "flag"})
        await tools.configure_warnings(storage, actions={"cross_metacontext": "flag"})

        result, _ = await tools.configure_warnings(storage)

        assert result["overridden"]["by_kind"] == {
            "disjoint_premises": "flag",
            "cross_metacontext": "flag",
        }

    async def test_clear_goes_back_to_the_default_at_the_time(self, storage):
        await tools.configure_warnings(
            storage, surface=False, actions={"disjoint_premises": "flag"}
        )
        await tools.configure_warnings(storage, clear=True)

        result, _ = await tools.configure_warnings(
            storage,
            default_warning_policy=WarningPolicy(default_action=AdvisoryAction.FLAG),
        )

        assert result["surface"] is True
        assert result["overridden"] == {}
        assert result["actions"]["disjoint_premises"] == "flag"

    async def test_an_unknown_kind_is_refused_by_name(self, storage):
        with pytest.raises(ValueError, match="is not a warning kind"):
            await tools.configure_warnings(storage, actions={"nonsense": "flag"})

    async def test_reject_is_refused_and_says_why(self, storage):
        with pytest.raises(ValueError, match="'reject' does not exist"):
            await tools.configure_warnings(storage, actions={"disjoint_premises": "reject"})

    async def test_the_setting_is_per_graph_state_that_survives_a_read(self, storage):
        await tools.configure_warnings(storage, surface=False)

        assert (await storage.get_warning_overrides()).surface is False


class TestTheTwoExistingWarningsBecameAdvisories:
    """`record_contradiction` and `record_variant` each returned an ad-hoc string
    plus, in one case, a boolean. The right idiom and the wrong plumbing: no
    kind, no subjects, nothing a setting could address. The response keys stay."""

    async def test_a_same_metacontext_contradiction_still_notifies(self, storage):
        a = await _fact(storage, "X is true")
        b = await _fact(storage, "X is false")

        result, _ = await tools.record_contradiction(a.id, b.id, storage)

        assert result["same_metacontext"] is True
        assert result["notify_user"] is True
        assert result["warnings"][0]["kind"] == "same_metacontext_contradiction"

    async def test_a_cross_metacontext_contradiction_still_says_use_record_variant(self, storage):
        a = await _fact(storage, "real")
        b = await _fact(storage, "fictional", metacontext=None)
        await _elsewhere(storage, b, "Fiction")

        result, _ = await tools.record_contradiction(a.id, b.id, storage)

        assert result["same_metacontext"] is False
        assert result["notify_user"] is False
        assert "record_variant" in result["warning"]
        assert result["warnings"][0]["kind"] == "cross_metacontext"

    async def test_a_cross_metacontext_variant_is_the_correct_use_and_says_nothing(self, storage):
        a = await _fact(storage, "Napoleon lost at Waterloo")
        b = await _fact(storage, "Napoleon won at Waterloo", metacontext=None)
        await _elsewhere(storage, b, "Novel-X")

        result, _ = await tools.record_variant(a.id, b.id, storage)

        assert result["same_metacontext"] is False
        assert "warning" not in result and "warnings" not in result
        assert result["notify_user"] is False

    async def test_a_same_metacontext_variant_is_advised_against_and_stays_quiet(self, storage):
        """It keeps the whisper it always had — but as a policy rather than a
        hard-coding, so a graph that wants it louder can name it."""
        a = await _fact(storage, "a")
        b = await _fact(storage, "b")

        result, _ = await tools.record_variant(a.id, b.id, storage)

        assert "record_contradiction" in result["warning"]
        assert result["warnings"][0]["kind"] == "same_metacontext_variant"
        assert result["notify_user"] is False

    async def test_a_same_metacontext_variant_can_be_escalated_by_a_graph(self, storage):
        await tools.configure_warnings(storage, actions={"same_metacontext_variant": "flag"})
        a = await _fact(storage, "a")
        b = await _fact(storage, "b")

        result, _ = await tools.record_variant(a.id, b.id, storage)

        assert result["notify_user"] is True

    async def test_the_wrong_tool_is_recorded_and_the_right_one_is_not(self, storage):
        """The whole of the stance split, at the two call sites that motivated
        it. A same-metacontext variant used the wrong tool and the graph says so; a
        same-metacontext contradiction used the right one and had nothing to proceed
        against."""
        a = await _fact(storage, "a")
        b = await _fact(storage, "b")
        await tools.record_variant(a.id, b.id, storage)
        objections = await storage.query_decisions(kinds=[DecisionKind.PROCEEDED_DESPITE_WARNING])
        assert len(objections) == 1

        c = await _fact(storage, "X is true")
        d = await _fact(storage, "X is false")
        await tools.record_contradiction(c.id, d.id, storage)

        assert (
            len(await storage.query_decisions(kinds=[DecisionKind.PROCEEDED_DESPITE_WARNING])) == 1
        )

    async def test_a_graph_can_turn_the_notification_off_as_a_decision(self, storage):
        """It stays possible; it just becomes something somebody chose rather
        than a side effect of a representation change."""
        await tools.configure_warnings(
            storage, actions={"same_metacontext_contradiction": "proceed"}
        )
        a = await _fact(storage, "X is true")
        b = await _fact(storage, "X is false")

        result, _ = await tools.record_contradiction(a.id, b.id, storage)

        assert result["notify_user"] is False
        assert result["warnings"][0]["kind"] == "same_metacontext_contradiction"

    async def test_a_mute_does_not_withdraw_the_contradiction_escalation(self, storage):
        """It is named `flag` by default, and a switch that names no kind is the
        more general statement. Setting it to `proceed` is how it goes quiet."""
        await tools.configure_warnings(storage, surface=False)
        a = await _fact(storage, "X is true")
        b = await _fact(storage, "X is false")

        result, _ = await tools.record_contradiction(a.id, b.id, storage)

        assert result["notify_user"] is True
        assert result["warnings"][0]["kind"] == "same_metacontext_contradiction"

    async def test_a_mute_does_hide_a_kind_that_only_objects(self, storage):
        await tools.configure_warnings(storage, surface=False)
        a = await _fact(storage, "a")
        b = await _fact(storage, "b")

        result, _ = await tools.record_variant(a.id, b.id, storage)

        assert "warning" not in result and "warnings" not in result
        assert result["notify_user"] is False
        # Muted, and recorded anyway: that separation is the load-bearing part.
        assert (
            len(await storage.query_decisions(kinds=[DecisionKind.PROCEEDED_DESPITE_WARNING])) == 1
        )


class TestTheMuteGovernsReflectsCandidateWarnings:
    """`reflect` attaches a warning to a merge candidate, and the same rule
    governs it as governs every tool that writes.

    One graph answering *no warnings* from `record_contradiction` and *here is a
    warning* from `reflect` on the same run made the setting unreliable rather
    than merely incomplete. The candidate still arrives either way: what the
    mute takes away is the warning on it, so nothing the agent can act on is
    lost.
    """

    async def test_a_muted_graph_gets_no_candidate_warnings_from_reflect(
        self, storage, embedding_provider
    ):
        await _nominated_disjoint_pair(storage, embedding_provider)
        await tools.configure_warnings(storage, surface=False)

        result, _ = await tools.reflect(storage, embedding_provider)

        assert result["inference_merge_candidates"], "the candidate itself still arrives"
        assert all(
            candidate["warnings"] == [] for candidate in result["inference_merge_candidates"]
        )

    async def test_a_named_flag_still_reaches_reflect(self, storage, embedding_provider):
        """Naming a kind is the more specific statement, here as everywhere
        else: muting the graph does not withdraw an escalation somebody asked
        for by name."""
        await _nominated_disjoint_pair(storage, embedding_provider)
        await tools.configure_warnings(
            storage, surface=False, actions={"disjoint_premises": "flag"}
        )

        result, _ = await tools.reflect(storage, embedding_provider)

        candidate = result["inference_merge_candidates"][0]
        assert [warning["kind"] for warning in candidate["warnings"]] == ["disjoint_premises"]


class TestTheRecordIsReadBackByReview:
    """One review machine, not two. A `NodeNote` would have been a second
    review-state store with a second *what has nobody looked at* scan, which an
    agent proceeding past an advisory would have written into as well."""

    async def test_the_mode_selects_only_warning_rows(self, storage):
        a = await _fact(storage, "real")
        b = await _fact(storage, "fictional", metacontext=None)
        await _elsewhere(storage, b, "Fiction")
        await tools.record_contradiction(a.id, b.id, storage, judge=CRITIC)

        every, _ = await tools.review(storage)
        only, _ = await tools.review(storage, mode="warning")

        assert {d["kind"] for d in every["decisions"]} == {
            "contradiction",
            "proceeded_despite_warning",
        }
        assert {d["kind"] for d in only["decisions"]} == {"proceeded_despite_warning"}

    async def test_the_commonest_path_does_not_double_the_journal(self, storage):
        """A same-metacontext contradiction is the ordinary, correct use of the tool.
        A row for every one of them would swamp the review this mode exists for
        — the selectivity argument that keeps `DecisionKind` fine-grained,
        turned on the kind itself."""
        a = await _fact(storage, "X is true")
        b = await _fact(storage, "X is false")

        await tools.record_contradiction(a.id, b.id, storage, judge=CRITIC)

        every, _ = await tools.review(storage)
        assert {d["kind"] for d in every["decisions"]} == {"contradiction"}
        assert (await tools.review(storage, mode="warning"))[0]["decisions"] == []

    async def test_the_row_carries_what_the_decider_was_told(self, storage):
        a = await _fact(storage, "real")
        b = await _fact(storage, "fictional", metacontext=None)
        await _elsewhere(storage, b, "Fiction")
        await tools.record_contradiction(a.id, b.id, storage, judge=CRITIC)

        result, _ = await tools.review(storage, mode="warning")

        row = result["decisions"][0]
        assert row["judged_by"] == "a-critic"
        assert "cross_metacontext" in row["certainty_basis"]
        assert {s["id"] for s in row["subjects"]} == {a.id, b.id}

    async def test_an_unwritten_kind_would_have_read_as_a_clean_graph(self):
        """Why the mode was refused until now, kept as the guard on the pair."""
        assert "warning" in REVIEW_MODES
        assert MODE_KINDS["warning"] == [DecisionKind.PROCEEDED_DESPITE_WARNING]

    async def test_nothing_contested_is_an_empty_list_rather_than_a_refusal(self, storage):
        result, _ = await tools.review(storage, mode="warning")

        assert result["decisions"] == []
        assert "refused" not in result


class TestTheNameTheModeUsedToHave:
    """`advisory` was the mode's name until the word became `warning`, and it
    was answered under both spellings for one release. That release has
    shipped, so a call still using it is refused and shown the modes: an
    unknown name answered as though it were `warning` would go on teaching a
    spelling the schema has dropped."""

    async def test_the_old_spelling_is_refused_and_the_modes_are_listed(self, storage):
        a = await _fact(storage, "real")
        b = await _fact(storage, "fictional", metacontext=None)
        await _elsewhere(storage, b, "Fiction")
        await tools.record_contradiction(a.id, b.id, storage, judge=CRITIC)

        result, _ = await tools.review(storage, mode="advisory")

        assert "'advisory' is not a mode" in result["refused"]
        assert result["modes"] == list(REVIEW_MODES)
        assert "decisions" not in result


class TestAStoredOverrideFromANewerBuild:
    """A graph written by a build that knows a kind this one does not.

    Failing loudly is right — silently dropping a policy somebody set is worse
    than an error — but the blast radius is worth pinning rather than
    discovering: `get_warning_overrides` sits on the path of every tool that can
    raise an advisory, so an unreadable override does not merely fail to apply.
    It takes `record_contradiction`, `record_variant` and `merge_inferences`
    down with it.
    """

    def test_an_unknown_kind_raises_rather_than_being_dropped(self):
        with pytest.raises(ValidationError):
            WarningOverrides.model_validate({"by_kind": {"a_kind_from_the_future": "flag"}})

    def test_an_unknown_action_raises_too(self):
        with pytest.raises(ValidationError):
            WarningOverrides.model_validate({"by_kind": {"cross_metacontext": "reject"}})

    async def test_the_failure_reaches_the_tools_that_read_the_policy(self, storage):
        """Not a defect to fix here — a consequence to know about. Recovering
        would mean either dropping the unreadable entry, which loses a setting
        silently, or refusing the write that made it, which no older build can
        do. Loud on read is the honest remaining option."""
        a = await _fact(storage, "X is true")
        b = await _fact(storage, "X is false")

        async def unreadable() -> WarningOverrides:
            return WarningOverrides.model_validate({"by_kind": {"a_kind_from_the_future": "flag"}})

        storage.get_warning_overrides = unreadable

        with pytest.raises(ValidationError):
            await tools.record_contradiction(a.id, b.id, storage)


# The keys advisories put on a response, and the only ones this work could have
# touched. `edge_id` and `created` are left out because both tools are
# idempotent: a repeat reports `created: false` for that reason, not this one.
ADVISORY_KEYS = ("warning", "warnings", "notify_user")


def _advisory_answer(result: dict) -> dict:
    return {key: result[key] for key in ADVISORY_KEYS if key in result}


class TestWatchingChangesNothingTheAgentIsTold:
    """A dashboard that altered a tool response would be observing by changing
    the thing observed.

    The event carries what the agent saw; it never decides it. So the same call
    with a bus behind it and without one hands back the same keys and the same
    advisories, on the muted path as well as the plain one.
    """

    async def test_record_contradiction_answers_the_same(self, storage):
        a = await _fact(storage, "X is true")
        b = await _fact(storage, "X is false")

        without, _ = await tools.record_contradiction(a.id, b.id, storage)
        watched, _ = await tools.record_contradiction(
            a.id, b.id, storage, event_bus=create_event_bus()
        )

        assert set(watched) == set(without)
        assert _advisory_answer(watched) == _advisory_answer(without)
        assert watched["notify_user"] is True

    async def test_record_variant_answers_the_same(self, storage):
        a = await _fact(storage, "a")
        b = await _fact(storage, "b")

        without, _ = await tools.record_variant(a.id, b.id, storage)
        watched, _ = await tools.record_variant(a.id, b.id, storage, event_bus=create_event_bus())

        assert set(watched) == set(without)
        assert _advisory_answer(watched) == _advisory_answer(without)

    async def test_a_muted_graph_answers_the_same(self, storage):
        """The path where a warning is published and the agent is told nothing,
        which is the one a mistake here would show up on.

        `record_variant` rather than `record_contradiction`, because the
        contradiction kind is named `flag` by default and a named flag outranks
        the mute: the agent would still see it, so it would not be this path.
        """
        await tools.configure_warnings(storage, surface=False)
        a = await _fact(storage, "a")
        b = await _fact(storage, "b")

        without, _ = await tools.record_variant(a.id, b.id, storage)
        watched, _ = await tools.record_variant(a.id, b.id, storage, event_bus=create_event_bus())

        assert set(watched) == set(without)
        assert _advisory_answer(watched) == _advisory_answer(without)
        assert "warnings" not in watched


# --- a reflect candidate that carries a warning ---


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


def _year(value: int) -> PreciseInstant:
    return PreciseInstant(at=datetime(value, 1, 1, tzinfo=UTC))


async def _nominated_disjoint_pair(storage, embedding_provider):
    """Two readings resting on a shared premise, over periods no source joins.

    The shared premise is what makes `reflect` look at the pair at all;
    the two dated premises are what makes the merge worth warning about,
    since the survivor would rest on a combination nothing puts in one period.
    """
    one = await _inference(storage, embedding_provider, "The name changed once")
    other = await _inference(storage, embedding_provider, "The name has changed once")
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
    await _rests_on(storage, one, early)
    await _rests_on(storage, other, late)
    shared = await _premise(storage, "The city was renamed by decree")
    await _rests_on(storage, one, shared)
    await _rests_on(storage, other, shared)
    return one, other
