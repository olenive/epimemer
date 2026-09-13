"""The three layers of a judge: the key, the name, and the claim.

`agent_id` used to be all three at once, and the collapse is what made every
defect in the judge identity split unfixable in isolation — naming a judge
badly on first contact was permanent, and splitting one judge's history in two
was a typo away. These are
the pure resolutions that replaced it: no storage, no elicitation, no MCP.

Nothing here writes. Consolidating two records **rewrites nothing and deletes
nothing** — the survivor takes the other's ids as former ids, and lookup
resolves through the list — so *which judge is this* has to be a derivation, and
these are the functions that derive it.
"""

from datetime import UTC, datetime, timedelta

from epimemer.core.types import (
    Agent,
    AgentDescription,
    Fact,
    JudgeRef,
    JudgeUsage,
    LifecycleEpisode,
    NodeStatus,
    ValueSignal,
    absorbed_agent_ids,
    absorbing,
    agent_aliases,
    agent_name,
    description_digest,
    is_retired,
    judge_is_unused,
    live_agents,
    name_holder,
    new_agent_id,
    node_judge_ids,
    reinstated,
    renamed,
    resolve_agent,
    retired,
    retired_agents,
    retired_at,
    serving_agents,
)

AT = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)
LATER = AT + timedelta(days=1)


def _described(text: str, at: datetime) -> AgentDescription:
    return AgentDescription(digest=description_digest(text), text=text, recorded_at=at)


def _agent(key: str, name: str = "", **kw) -> Agent:
    return Agent(id=key, name=name, authorised_at=AT, **kw)


class TestANameIsNotAKey:
    """The key is opaque and permanent; the name is the handle and is not."""

    def test_a_record_written_before_the_split_reads_as_its_own_name(self):
        # No name, because the field did not exist when it was written. It reads
        # as its id, which is what that id was: the name the user typed.
        assert agent_name(_agent("Opus 5")) == "Opus 5"

    def test_a_named_record_reads_as_its_name(self):
        assert agent_name(_agent(new_agent_id(), "Opus 5")) == "Opus 5"

    def test_renaming_touches_the_name_and_nothing_else(self):
        before = _agent("k1", "Opus 5", descriptions=[_described("a critic", AT)])

        after = renamed(before, "  Opus 5 reviewer  ")

        assert after.name == "Opus 5 reviewer", "trimmed"
        assert after.id == before.id, "the key is what every decision recorded"
        assert after.descriptions == before.descriptions

    def test_a_new_key_is_opaque(self):
        # the earlier identity proposal rejected this and was overturned: both its objections were
        # premised on a free-text prompt, and the picker removed them.
        assert new_agent_id() != new_agent_id()
        assert len(new_agent_id()) > 20


class TestResolvingAHandle:
    """A handle is a name, a key, or a key the judge used to be recorded under.

    Precedence is **id, then name, then former id**, and it is not arbitrary: an
    id is exact, and a name is what the user sees and types, so it beats a
    historical alias.
    """

    def test_a_key_resolves(self):
        agents = [_agent("k1", "Opus 5")]
        assert resolve_agent(agents, "k1").id == "k1"

    def test_a_name_resolves(self):
        agents = [_agent("k1", "Opus 5")]
        assert resolve_agent(agents, "Opus 5").id == "k1"

    def test_a_name_resolves_whatever_the_case(self):
        # A picker offering `Opus 5` and `opus 5` as separate judges is the very
        # split this exists to stop.
        agents = [_agent("k1", "Opus 5")]
        assert resolve_agent(agents, "OPUS 5").id == "k1"
        assert resolve_agent(agents, "  opus 5 ").id == "k1"

    def test_a_former_key_resolves_to_whatever_absorbed_it(self):
        agents = [_agent("k1", "Opus 5", former_ids=["Opus 5 Judge"])]
        assert resolve_agent(agents, "Opus 5 Judge").id == "k1"

    def test_a_current_name_beats_another_judges_former_key(self):
        # One judge was once called this; another is called it *now*. The one it
        # names today is the one meant.
        agents = [
            _agent("k1", "reviewer", former_ids=["Opus 5"]),
            _agent("k2", "Opus 5"),
        ]
        assert resolve_agent(agents, "Opus 5").id == "k2"

    def test_a_key_beats_a_name(self):
        agents = [_agent("k1", "k2"), _agent("k2", "elsewhere")]
        assert resolve_agent(agents, "k2").id == "k2"

    def test_nothing_resolves_to_nothing(self):
        assert resolve_agent([_agent("k1", "Opus 5")], "nobody") is None
        assert resolve_agent([], "Opus 5") is None
        assert resolve_agent([_agent("k1", "Opus 5")], "   ") is None


class TestAnAbsorbedRecordIsNoLongerAJudge:
    """It is kept — nothing here hard-deletes — so *absorbed* is derived."""

    def test_a_record_another_claims_as_a_former_key_is_not_live(self):
        agents = [_agent("k1", "Opus 5", former_ids=["old"]), _agent("old", "Opus 5 Judge")]

        assert absorbed_agent_ids(agents) == {"old"}
        assert [a.id for a in live_agents(agents)] == ["k1"]

    def test_a_record_naming_itself_is_still_live(self):
        # Nothing should write this, and a self-reference must not make a judge
        # disappear from its own graph if something does.
        agents = [_agent("k1", "Opus 5", former_ids=["k1"])]
        assert [a.id for a in live_agents(agents)] == ["k1"]
        assert agent_aliases(agents[0]) == ["k1"]

    def test_an_absorbed_record_cannot_be_resolved_directly(self):
        agents = [_agent("k1", "Opus 5", former_ids=["old"]), _agent("old", "old name")]

        assert resolve_agent(agents, "old").id == "k1"
        assert resolve_agent(agents, "old name") is None, "it is not a judge any more"


class TestTheIdsAJudgesRowsMayCarry:
    def test_a_judge_that_was_never_consolidated_has_one(self):
        assert agent_aliases(_agent("k1", "Opus 5")) == ["k1"]

    def test_the_current_key_comes_first(self):
        agent = _agent("k1", "Opus 5", former_ids=["a", "b"])
        assert agent_aliases(agent) == ["k1", "a", "b"]


class TestNamesAreUniquePerGraph:
    """Or `by_agent` stops being answerable and the picker shows two identical
    lines. Enforced where a name is set, the only place it can be."""

    def test_a_taken_name_names_its_holder(self):
        agents = [_agent("k1", "Opus 5"), _agent("k2", "editor")]
        assert name_holder(agents, "opus 5").id == "k1"

    def test_the_judge_being_renamed_is_not_its_own_collision(self):
        agents = [_agent("k1", "Opus 5")]
        assert name_holder(agents, "Opus 5", excluding="k1") is None

    def test_an_absorbed_record_does_not_hold_a_name(self):
        agents = [_agent("k1", "reviewer", former_ids=["old"]), _agent("old", "Opus 5")]
        assert name_holder(agents, "Opus 5") is None


class TestConsolidatingTwoRecordsThatWereAlwaysOneJudge:
    """The repair for `Opus 5 Judge` and `Opus 5`, measured on this repository's
    own graph. Nothing is rewritten and nothing is deleted; what changes is only
    where a lookup lands."""

    def test_the_survivor_answers_for_both_sets_of_keys(self):
        survivor = _agent("k1", "Opus 5")
        absorbed = _agent("k2", "Opus 5 Judge", former_ids=["ancient"])

        merged = absorbing(survivor, absorbed)

        assert merged.id == "k1", "the survivor's key is untouched"
        assert merged.former_ids == ["k2", "ancient"]
        assert agent_aliases(merged) == ["k1", "k2", "ancient"]

    def test_both_description_histories_survive(self):
        # A decision records `(key, digest)`, so dropping the absorbed history
        # would leave its own old decisions unreadable through the record that
        # now answers for them.
        survivor = _agent("k1", "Opus 5", descriptions=[_described("a critic", LATER)])
        absorbed = _agent("k2", "Opus 5 Judge", descriptions=[_described("a judge", AT)])

        merged = absorbing(survivor, absorbed)

        assert [d.text for d in merged.descriptions] == ["a judge", "a critic"]
        assert merged.descriptions[-1].text == "a critic", "the latest claim, still last"

    def test_identical_wording_is_one_version(self):
        survivor = _agent("k1", "a", descriptions=[_described("a critic", LATER)])
        absorbed = _agent("k2", "b", descriptions=[_described("a critic", AT)])

        merged = absorbing(survivor, absorbed)

        assert len(merged.descriptions) == 1
        assert merged.descriptions[0].recorded_at == AT, "the earlier claim of it"

    def test_the_dates_span_both(self):
        survivor = _agent("k1", "a", first_seen_at=LATER, last_seen_at=LATER)
        absorbed = _agent("k2", "b", first_seen_at=AT, last_seen_at=AT)

        merged = absorbing(survivor, absorbed)

        assert merged.first_seen_at == AT
        assert merged.last_seen_at == LATER

    def test_a_judge_never_seen_does_not_erase_the_others_dates(self):
        survivor = _agent("k1", "a", first_seen_at=AT, last_seen_at=LATER)
        absorbed = _agent("k2", "b")

        merged = absorbing(survivor, absorbed)

        assert merged.first_seen_at == AT
        assert merged.last_seen_at == LATER

    def test_consolidating_twice_adds_no_duplicates(self):
        survivor = _agent("k1", "a", former_ids=["k2"])
        merged = absorbing(survivor, _agent("k2", "b"))
        assert merged.former_ids == ["k2"]


class TestRetirementIsAnAppendOnlyTrail:
    """A judge can be taken out of use, brought back, and taken out again.

    A `retired_at` scalar cannot say that: clearing it on the return erases the
    first spell, and keeping it reports a serving judge as retired. Same problem
    and same answer as `LifecycleEpisode` on a node.
    """

    def test_a_fresh_judge_is_not_retired(self):
        agent = _agent("k1", "Opus 5")
        assert not is_retired(agent)
        assert retired_at(agent) is None
        assert agent.retirements == []

    def test_retiring_opens_an_episode(self):
        agent = retired(_agent("k1", "Opus 5"), AT)

        assert is_retired(agent)
        assert retired_at(agent) == AT
        assert agent.retirements[-1].reinstated_at is None

    def test_reinstating_closes_it_rather_than_clearing_it(self):
        agent = reinstated(retired(_agent("k1", "Opus 5"), AT), LATER)

        assert not is_retired(agent)
        assert retired_at(agent) is None
        assert agent.retirements[-1].retired_at == AT, "the spell out of use is kept"
        assert agent.retirements[-1].reinstated_at == LATER

    def test_a_second_retirement_appends_and_dates_from_the_later_one(self):
        later_still = LATER + timedelta(days=7)
        agent = retired(reinstated(retired(_agent("k1", "Opus 5"), AT), LATER), later_still)

        assert [ep.retired_at for ep in agent.retirements] == [AT, later_still]
        assert retired_at(agent) == later_still, "the spell it is in now, not the first"

    def test_retiring_an_already_retired_judge_changes_nothing(self):
        once = retired(_agent("k1", "Opus 5"), AT)
        assert retired(once, LATER).retirements == once.retirements

    def test_reinstating_a_serving_judge_changes_nothing(self):
        assert reinstated(_agent("k1", "Opus 5"), AT).retirements == []

    def test_nothing_else_about_the_record_moves(self):
        agent = _agent("k1", "Opus 5", former_ids=["old"], descriptions=[_described("a", AT)])

        out = retired(agent, AT)

        assert out.id == agent.id
        assert agent_name(out) == "Opus 5"
        assert out.former_ids == ["old"]
        assert out.descriptions == agent.descriptions


class TestRetiredIsNotAbsorbed:
    """*Live* means *not absorbed*, and a retired judge is still a judge.

    Its decisions have to stay readable and `review(mode="by_agent")` has to
    keep answering for it, so it must not drop out of `live_agents` or become
    unresolvable. Only *may this be claimed* turns on retirement.
    """

    def test_a_retired_judge_is_still_live_and_still_resolves(self):
        agents = [retired(_agent("k1", "Opus 5"), AT)]

        assert [a.id for a in live_agents(agents)] == ["k1"]
        assert resolve_agent(agents, "Opus 5").id == "k1"
        assert resolve_agent(agents, "k1").id == "k1"

    def test_the_live_set_splits_into_serving_and_retired(self):
        agents = [_agent("k1", "Serving"), retired(_agent("k2", "Put away"), AT)]

        assert [a.id for a in serving_agents(agents)] == ["k1"]
        assert [a.id for a in retired_agents(agents)] == ["k2"]

    def test_an_absorbed_record_is_in_neither_however_it_was_left(self):
        agents = [
            _agent("k1", "Opus 5", former_ids=["old"]),
            retired(_agent("old", "old name"), AT),
        ]

        assert [a.id for a in serving_agents(agents)] == ["k1"]
        assert retired_agents(agents) == [], "absorbed first, so not a judge to retire"


class TestWhereAJudgeKeyCanSitOnANode:
    """`node_judge_ids` is the one enumeration, and deletion depends on it.

    A `JudgeRef` field added to a node and missed there would let
    `epimemer agents delete` remove a judge some node still points at.
    """

    def test_a_node_nobody_judged_names_nobody(self):
        assert node_judge_ids(Fact(content="x", source_id="s1")) == set()

    def test_the_writer_the_importance_judge_and_both_ends_of_an_episode(self):
        node = Fact(
            content="x",
            source_id="s1",
            judged_by=JudgeRef(agent_id="writer", digest="d"),
            value=ValueSignal(importance_judged_by=JudgeRef(agent_id="rater", digest="d")),
            lifecycle=[
                LifecycleEpisode(
                    retired_at=AT,
                    because=NodeStatus.ARCHIVED,
                    retired_by=JudgeRef(agent_id="retirer", digest="d"),
                    restored_at=LATER,
                    restored_by=JudgeRef(agent_id="restorer", digest="d"),
                )
            ],
        )

        assert node_judge_ids(node) == {"writer", "rater", "retirer", "restorer"}


class TestNothingNamingAJudgeMeansItsRecordCanGo:
    def test_all_zero_is_unused(self):
        assert judge_is_unused(JudgeUsage())

    def test_any_one_count_is_enough_to_keep_it(self):
        for field in ("decisions", "nodes", "edges", "relations"):
            assert not judge_is_unused(JudgeUsage(**{field: 1})), field
