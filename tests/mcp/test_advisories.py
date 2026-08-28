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
from epimemer.core.types import (
    BASE_METACONTEXT_ID,
    DecisionKind,
    EdgeType,
    Fact,
    JudgeRef,
    Metacontext,
    NodeEdge,
)
from epimemer.mcp import tools
from epimemer.mcp.config import ServerConfig
from epimemer.pipelines.review.modes import MODE_KINDS, REVIEW_MODES
from epimemer.storage.protocol import WarningOverrides, resolve_warning_policy

CRITIC = JudgeRef(agent_id="a-critic", digest="d1")


async def _fact(storage, content, *, frame=BASE_METACONTEXT_ID):
    fact = Fact(content=content, source_id="seg-1")
    await storage.store_node(fact)
    if frame is not None:
        await storage.store_edge(NodeEdge(
            src_id=fact.id, dst_id=frame, type=EdgeType.HAS_METACONTEXT,
        ))
    return fact


async def _elsewhere(storage, fact, label):
    frame = Metacontext(content=label)
    await storage.store_metacontext(frame)
    await storage.store_edge(NodeEdge(
        src_id=fact.id, dst_id=frame.id, type=EdgeType.HAS_METACONTEXT,
    ))
    return frame.id


class TestThePolicyResolvesWithoutASingleton:
    """Settings are a value passed explicitly, exactly as `ServerConfig` is.

    Tests run two backends and many graphs in one process, so a process-wide
    mutable instance makes every test that changes a setting order-dependent with
    every test that reads one — and the policy is per graph, so one instance
    could not answer *what is the policy here* after a `use_graph` anyway.
    """

    def test_a_kind_nobody_named_takes_the_default_action(self):
        policy = WarningPolicy(default_action=AdvisoryAction.FLAG, by_kind={})

        assert resolved_action(policy, AdvisoryKind.DISJOINT_PREMISES) is (
            AdvisoryAction.FLAG
        )

    def test_a_same_frame_contradiction_flags_out_of_the_box(self):
        """Not a preference — a compatibility requirement. `record_contradiction`
        has always notified on a same-frame pair, and a default of `proceed`
        would have kept the key while quietly changing its trigger."""
        assert resolved_action(
            WarningPolicy(), AdvisoryKind.SAME_FRAME_CONTRADICTION
        ) is AdvisoryAction.FLAG

    def test_an_override_of_one_kind_keeps_the_defaults_for_the_others(self):
        """A map override that silently dropped unnamed keys is the same class of
        bug as a field-by-field rebuild forgetting a field."""
        resolved = resolve_warning_policy(
            WarningOverrides(
                by_kind={AdvisoryKind.DISJOINT_PREMISES: AdvisoryAction.FLAG}
            ),
            WarningPolicy(),
        )

        assert resolved.by_kind[AdvisoryKind.DISJOINT_PREMISES] is AdvisoryAction.FLAG
        assert resolved.by_kind[AdvisoryKind.SAME_FRAME_CONTRADICTION] is (
            AdvisoryAction.FLAG
        )

    def test_a_graph_can_quieten_a_kind_the_default_escalates(self):
        resolved = resolve_warning_policy(
            WarningOverrides(by_kind={
                AdvisoryKind.SAME_FRAME_CONTRADICTION: AdvisoryAction.PROCEED
            }),
            WarningPolicy(),
        )

        assert resolved_action(
            resolved, AdvisoryKind.SAME_FRAME_CONTRADICTION
        ) is AdvisoryAction.PROCEED

    def test_no_override_follows_the_default_rather_than_freezing_it(self):
        """`None` means *follow the process default at the time*, so a default
        changed later still reaches a graph that was configured once and cleared."""
        stricter = WarningPolicy(surface=False)

        assert resolve_warning_policy(WarningOverrides(), stricter).surface is False
        assert resolve_warning_policy(None, stricter).surface is False

    def test_two_configs_do_not_share_one_policy(self):
        """A shared mutable default would be a singleton reached by accident."""
        first, second = ServerConfig(), ServerConfig()
        first.warning_policy.by_kind[AdvisoryKind.CROSS_FRAME] = AdvisoryAction.FLAG

        assert AdvisoryKind.CROSS_FRAME not in second.warning_policy.by_kind

    def test_an_explicitly_named_flag_outranks_the_global_mute(self):
        """Specific beats general, the rule every `resolve_*` here keeps.

        Naming a kind in `by_kind` is a stronger statement than a switch that
        names none, so muting the graph does not withdraw an escalation somebody
        asked for by name — and `notify_user: true` with no text to relay would
        be an instruction nobody can follow.
        """
        advisories = [
            Advisory(kind=AdvisoryKind.CROSS_FRAME, message="x"),
            Advisory(kind=AdvisoryKind.SAME_FRAME_CONTRADICTION, message="y"),
        ]

        assert len(surfaced(WarningPolicy(), advisories)) == 2
        muted = surfaced(WarningPolicy(surface=False), advisories)
        assert [a.kind for a in muted] == [AdvisoryKind.SAME_FRAME_CONTRADICTION]
        assert notify_user(WarningPolicy(), advisories) is True

    def test_a_kind_following_the_default_action_is_silenced_by_the_mute(self):
        """The exception is narrow on purpose: a kind nobody named is general,
        however the general default is set."""
        advisories = [Advisory(kind=AdvisoryKind.CROSS_FRAME, message="x")]
        loud = WarningPolicy(
            surface=False, default_action=AdvisoryAction.FLAG, by_kind={}
        )

        assert surfaced(loud, advisories) == []

    def test_setting_a_named_kind_to_proceed_is_how_an_escalation_is_withdrawn(
        self,
    ):
        advisories = [
            Advisory(kind=AdvisoryKind.SAME_FRAME_CONTRADICTION, message="y")
        ]
        quiet = WarningPolicy(surface=False, by_kind={
            AdvisoryKind.SAME_FRAME_CONTRADICTION: AdvisoryAction.PROCEED
        })

        assert surfaced(quiet, advisories) == []
        assert notify_user(quiet, advisories) is False

    def test_reject_is_absent_rather_than_reserved(self):
        """A value nothing can produce is worse than no value at all: a caller
        writes a branch for it and the branch is dead."""
        assert {action.value for action in AdvisoryAction} == {"proceed", "flag"}


class TestEachKindGivesExactlyOneKindOfAdvice:
    """The defect the fourth kind fixed: `SAME_FRAME_CONTRADICTION` was raised
    both where the tool was right and where it was wrong, so one kind carried
    opposite advice — the *field that needs "or" to describe it* tell.

    The classification decides whether a `proceeded_despite_advisory` row is
    written, so a kind added without one would silently take the safer-sounding
    half of a question nobody asked.
    """

    def test_every_kind_is_classified_and_nothing_else_is(self):
        assert set(ADVISORY_STANCE) == set(AdvisoryKind)

    def test_only_the_same_frame_contradiction_endorses_the_call(self):
        endorsing = {
            kind for kind, stance in ADVISORY_STANCE.items()
            if stance is AdvisoryStance.ESCALATES
        }
        assert endorsing == {AdvisoryKind.SAME_FRAME_CONTRADICTION}

    def test_an_escalating_advisory_has_nothing_to_proceed_despite(self):
        assert objects_to_the_call([
            Advisory(kind=AdvisoryKind.SAME_FRAME_CONTRADICTION, message="y")
        ]) is False
        assert objects_to_the_call([
            Advisory(kind=AdvisoryKind.SAME_FRAME_VARIANT, message="y")
        ]) is True

    def test_a_mixed_set_objects_if_anything_in_it_does(self):
        assert objects_to_the_call([
            Advisory(kind=AdvisoryKind.SAME_FRAME_CONTRADICTION, message="y"),
            Advisory(kind=AdvisoryKind.DISJOINT_PREMISES, message="x"),
        ]) is True


class TestConfigureWarnings:

    async def test_it_reports_what_is_in_force_without_changing_anything(
        self, storage
    ):
        result, _ = await tools.configure_warnings(storage)

        assert result["surface"] is True
        assert result["actions"]["same_frame_contradiction"] == "flag"
        assert result["actions"]["disjoint_premises"] == "proceed"
        assert result["overridden"] == {}

    async def test_setting_one_kind_leaves_the_rest_inherited(self, storage):
        await tools.configure_warnings(
            storage, actions={"disjoint_premises": "flag"}
        )

        result, _ = await tools.configure_warnings(storage)

        assert result["actions"]["disjoint_premises"] == "flag"
        assert result["actions"]["same_frame_contradiction"] == "flag"
        # Which answers this graph gave, as opposed to inherited — the two are
        # different, because only the second tracks a changed default.
        assert result["overridden"] == {"by_kind": {"disjoint_premises": "flag"}}

    async def test_a_second_call_merges_rather_than_replacing(self, storage):
        await tools.configure_warnings(
            storage, actions={"disjoint_premises": "flag"}
        )
        await tools.configure_warnings(
            storage, actions={"cross_frame": "flag"}
        )

        result, _ = await tools.configure_warnings(storage)

        assert result["overridden"]["by_kind"] == {
            "disjoint_premises": "flag", "cross_frame": "flag",
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
        with pytest.raises(ValueError, match="is not an advisory kind"):
            await tools.configure_warnings(storage, actions={"nonsense": "flag"})

    async def test_reject_is_refused_and_says_why(self, storage):
        with pytest.raises(ValueError, match="'reject' does not exist"):
            await tools.configure_warnings(
                storage, actions={"disjoint_premises": "reject"}
            )

    async def test_the_setting_is_per_graph_state_that_survives_a_read(
        self, storage
    ):
        await tools.configure_warnings(storage, surface=False)

        assert (await storage.get_warning_overrides()).surface is False


class TestTheTwoExistingWarningsBecameAdvisories:
    """`record_contradiction` and `record_variant` each returned an ad-hoc string
    plus, in one case, a boolean. The right idiom and the wrong plumbing: no
    kind, no subjects, nothing a setting could address. The response keys stay."""

    async def test_a_same_frame_contradiction_still_notifies(self, storage):
        a = await _fact(storage, "X is true")
        b = await _fact(storage, "X is false")

        result, _ = await tools.record_contradiction(a.id, b.id, storage)

        assert result["same_frame"] is True
        assert result["notify_user"] is True
        assert result["warnings"][0]["kind"] == "same_frame_contradiction"

    async def test_a_cross_frame_contradiction_still_says_use_record_variant(
        self, storage
    ):
        a = await _fact(storage, "real")
        b = await _fact(storage, "fictional", frame=None)
        await _elsewhere(storage, b, "Fiction")

        result, _ = await tools.record_contradiction(a.id, b.id, storage)

        assert result["same_frame"] is False
        assert result["notify_user"] is False
        assert "record_variant" in result["warning"]
        assert result["warnings"][0]["kind"] == "cross_frame"

    async def test_a_cross_frame_variant_is_the_correct_use_and_says_nothing(
        self, storage
    ):
        a = await _fact(storage, "Napoleon lost at Waterloo")
        b = await _fact(storage, "Napoleon won at Waterloo", frame=None)
        await _elsewhere(storage, b, "Novel-X")

        result, _ = await tools.record_variant(a.id, b.id, storage)

        assert result["same_frame"] is False
        assert "warning" not in result and "warnings" not in result

    async def test_a_same_frame_variant_is_advised_against_and_stays_quiet(
        self, storage
    ):
        """It keeps the whisper it always had — but as a policy rather than a
        hard-coding, so a graph that wants it louder can name it."""
        a = await _fact(storage, "a")
        b = await _fact(storage, "b")

        result, _ = await tools.record_variant(a.id, b.id, storage)

        assert "record_contradiction" in result["warning"]
        assert result["warnings"][0]["kind"] == "same_frame_variant"
        assert result["notify_user"] is False

    async def test_a_same_frame_variant_can_be_escalated_by_a_graph(self, storage):
        await tools.configure_warnings(
            storage, actions={"same_frame_variant": "flag"}
        )
        a = await _fact(storage, "a")
        b = await _fact(storage, "b")

        result, _ = await tools.record_variant(a.id, b.id, storage)

        assert result["notify_user"] is True

    async def test_the_wrong_tool_is_recorded_and_the_right_one_is_not(
        self, storage
    ):
        """The whole of the stance split, at the two call sites that motivated
        it. A same-frame variant used the wrong tool and the graph says so; a
        same-frame contradiction used the right one and had nothing to proceed
        against."""
        a = await _fact(storage, "a")
        b = await _fact(storage, "b")
        await tools.record_variant(a.id, b.id, storage)
        objections = await storage.query_decisions(
            kinds=[DecisionKind.PROCEEDED_DESPITE_ADVISORY]
        )
        assert len(objections) == 1

        c = await _fact(storage, "X is true")
        d = await _fact(storage, "X is false")
        await tools.record_contradiction(c.id, d.id, storage)

        assert len(await storage.query_decisions(
            kinds=[DecisionKind.PROCEEDED_DESPITE_ADVISORY]
        )) == 1

    async def test_a_graph_can_turn_the_notification_off_as_a_decision(
        self, storage
    ):
        """It stays possible; it just becomes something somebody chose rather
        than a side effect of a representation change."""
        await tools.configure_warnings(
            storage, actions={"same_frame_contradiction": "proceed"}
        )
        a = await _fact(storage, "X is true")
        b = await _fact(storage, "X is false")

        result, _ = await tools.record_contradiction(a.id, b.id, storage)

        assert result["notify_user"] is False
        assert result["warnings"][0]["kind"] == "same_frame_contradiction"

    async def test_a_mute_does_not_withdraw_the_contradiction_escalation(
        self, storage
    ):
        """It is named `flag` by default, and a switch that names no kind is the
        more general statement. Setting it to `proceed` is how it goes quiet."""
        await tools.configure_warnings(storage, surface=False)
        a = await _fact(storage, "X is true")
        b = await _fact(storage, "X is false")

        result, _ = await tools.record_contradiction(a.id, b.id, storage)

        assert result["notify_user"] is True
        assert result["warnings"][0]["kind"] == "same_frame_contradiction"

    async def test_a_mute_does_hide_a_kind_that_only_objects(self, storage):
        await tools.configure_warnings(storage, surface=False)
        a = await _fact(storage, "a")
        b = await _fact(storage, "b")

        result, _ = await tools.record_variant(a.id, b.id, storage)

        assert "warning" not in result and "warnings" not in result
        assert result["notify_user"] is False
        # Muted, and recorded anyway: that separation is the load-bearing part.
        assert len(await storage.query_decisions(
            kinds=[DecisionKind.PROCEEDED_DESPITE_ADVISORY]
        )) == 1


class TestTheRecordIsReadBackByReview:
    """One review machine, not two. A `NodeNote` would have been a second
    review-state store with a second *what has nobody looked at* scan, which an
    agent proceeding past an advisory would have written into as well."""

    async def test_the_mode_selects_only_advisory_rows(self, storage):
        a = await _fact(storage, "real")
        b = await _fact(storage, "fictional", frame=None)
        await _elsewhere(storage, b, "Fiction")
        await tools.record_contradiction(a.id, b.id, storage, judge=CRITIC)

        every, _ = await tools.review(storage)
        only, _ = await tools.review(storage, mode="advisory")

        assert {d["kind"] for d in every["decisions"]} == {
            "contradiction", "proceeded_despite_advisory",
        }
        assert {d["kind"] for d in only["decisions"]} == {
            "proceeded_despite_advisory"
        }

    async def test_the_commonest_path_does_not_double_the_journal(self, storage):
        """A same-frame contradiction is the ordinary, correct use of the tool.
        A row for every one of them would swamp the review this mode exists for
        — the selectivity argument that keeps `DecisionKind` fine-grained,
        turned on the kind itself."""
        a = await _fact(storage, "X is true")
        b = await _fact(storage, "X is false")

        await tools.record_contradiction(a.id, b.id, storage, judge=CRITIC)

        every, _ = await tools.review(storage)
        assert {d["kind"] for d in every["decisions"]} == {"contradiction"}
        assert (await tools.review(storage, mode="advisory"))[0]["decisions"] == []

    async def test_the_row_carries_what_the_decider_was_told(self, storage):
        a = await _fact(storage, "real")
        b = await _fact(storage, "fictional", frame=None)
        await _elsewhere(storage, b, "Fiction")
        await tools.record_contradiction(a.id, b.id, storage, judge=CRITIC)

        result, _ = await tools.review(storage, mode="advisory")

        row = result["decisions"][0]
        assert row["judged_by"] == "a-critic"
        assert "cross_frame" in row["certainty_basis"]
        assert {s["id"] for s in row["subjects"]} == {a.id, b.id}

    async def test_an_unwritten_kind_would_have_read_as_a_clean_graph(self):
        """Why the mode was refused until now, kept as the guard on the pair."""
        assert "advisory" in REVIEW_MODES
        assert MODE_KINDS["advisory"] == [DecisionKind.PROCEEDED_DESPITE_ADVISORY]

    async def test_nothing_contested_is_an_empty_list_rather_than_a_refusal(
        self, storage
    ):
        result, _ = await tools.review(storage, mode="advisory")

        assert result["decisions"] == []
        assert "refused" not in result


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
            WarningOverrides.model_validate(
                {"by_kind": {"a_kind_from_the_future": "flag"}}
            )

    def test_an_unknown_action_raises_too(self):
        with pytest.raises(ValidationError):
            WarningOverrides.model_validate(
                {"by_kind": {"cross_frame": "reject"}}
            )

    async def test_the_failure_reaches_the_tools_that_read_the_policy(
        self, storage
    ):
        """Not a defect to fix here — a consequence to know about. Recovering
        would mean either dropping the unreadable entry, which loses a setting
        silently, or refusing the write that made it, which no older build can
        do. Loud on read is the honest remaining option."""
        a = await _fact(storage, "X is true")
        b = await _fact(storage, "X is false")

        async def unreadable() -> WarningOverrides:
            return WarningOverrides.model_validate(
                {"by_kind": {"a_kind_from_the_future": "flag"}}
            )

        storage.get_warning_overrides = unreadable

        with pytest.raises(ValidationError):
            await tools.record_contradiction(a.id, b.id, storage)
