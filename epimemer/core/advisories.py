"""What the system tells an agent about an operation it is *about* to make.

An advisory is not an error and not a refusal. It is the thing the graph knows
and the agent cannot compute for itself, handed over at the moment the agent is
choosing what to write — which is the only moment at which it can still change
the answer. `dev-docs/WARNINGS_AND_SETTINGS.md` is the record; the two
load-bearing decisions are worth restating where the code is:

**Pre-decision, not post-hoc.** A merge whose premises never held together is a
computable outcome, so it rides along with the candidate pair rather than
arriving as a rejection the agent has to re-propose past. An extra round trip to
deliver information already in hand is latency bought for nothing.

**A warning rather than a rule.** The honest response to *these premises never
held together* is often to narrow the merged claim's wording or period — which
the agent does by writing content, which is what it is already doing. A refusal
blocks a merge the agent could have fixed.

Named `Advisory` rather than `Warning` for one reason: `Warning` is a Python
builtin, and a model shadowing it makes every module importing both read
ambiguously. **The wire format keeps the user's word** — tool responses carry
`warnings: [...]` — so only the Python class differs, and this paragraph is the
whole of the translation.
"""

from enum import Enum

from pydantic import BaseModel, Field


class AdvisoryKind(str, Enum):
    """What sort of thing is being pointed out.

    A closed vocabulary rather than free prose, because the reviewing agent
    groups and sorts these, and re-parsing sentences is how that rots. Every
    member has a writer, on `DecisionKind`'s rule: a kind nothing produces is a
    filter that returns nothing and reads as a clean graph.
    """

    # Two premises of a proposed merge whose asserted periods provably fall
    # clear of each other, so the survivor would rest on a combination no source
    # puts in one period. Produced by inference-merge nomination and by
    # `merge_inferences` itself.
    DISJOINT_PREMISES = "disjoint_premises"
    # A contradiction recorded across frames. Two claims about different worlds
    # do not conflict, so the tool reached for was the wrong one.
    CROSS_FRAME = "cross_frame"
    # A variant recorded within one frame. `variant_of` is for a proposition one
    # world resolves differently from another, so same-frame is again the wrong
    # tool — and what is being described is probably a contradiction.
    SAME_FRAME_VARIANT = "same_frame_variant"
    # A contradiction recorded within one frame. **The only kind here that says
    # the call was right**: the conflict is real rather than a divergence of
    # worlds, which is exactly what makes it worth putting to a person.
    SAME_FRAME_CONTRADICTION = "same_frame_contradiction"


class AdvisoryStance(str, Enum):
    """Whether an advisory argues with the operation or reports on it.

    **Two situations wore one kind until 2026-08-29, and they gave opposite
    advice.** `SAME_FRAME_CONTRADICTION` was raised both by
    `record_contradiction` — where it means *you are right, and a person should
    see this* — and by `record_variant`, where it means *this is the wrong
    tool*. One field needing "or" to describe it is the tell this codebase has
    caught several times; the fix was a fourth kind, and this is what the split
    bought.

    The distinction is not cosmetic: it decides whether a
    `proceeded_despite_advisory` row is written. *Despite* is meaningful only
    where there was something to proceed against, and a row for every correct
    call degrades the review the kind exists for.
    """

    # The operation may be the wrong one. Proceeding is worth recording.
    OBJECTS = "objects"
    # The operation was right; the finding it turned up wants a person. Nothing
    # was proceeded against, so nothing is journalled.
    ESCALATES = "escalates"


# Every kind, classified. A **total** map rather than a set of objecting kinds,
# because a set makes absence mean *escalates* — and silence quietly becoming a
# claim is the failure the frame requirement exists to prevent. A test asserts
# both directions, so a kind added without a stance fails rather than defaulting
# to the safer-sounding half.
ADVISORY_STANCE: dict["AdvisoryKind", AdvisoryStance] = {
    AdvisoryKind.DISJOINT_PREMISES: AdvisoryStance.OBJECTS,
    AdvisoryKind.CROSS_FRAME: AdvisoryStance.OBJECTS,
    AdvisoryKind.SAME_FRAME_VARIANT: AdvisoryStance.OBJECTS,
    AdvisoryKind.SAME_FRAME_CONTRADICTION: AdvisoryStance.ESCALATES,
}


class AdvisoryAction(str, Enum):
    """What a policy says should happen when an advisory is raised.

    Two members, and `reject` is deliberately absent rather than reserved: a
    value nothing can produce is worse than no value at all, because a caller
    writes a branch for it and the branch is dead. It lands when something wants
    it, with the refusal path and its tests.
    """

    # The operation applies; the advisory is recorded and surfaced.
    PROCEED = "proceed"
    # The operation applies, and the agent is expected to raise it with the
    # user. This is what `notify_user` reports.
    FLAG = "flag"


class Advisory(BaseModel):
    """One thing worth knowing about an operation, in a shape a reviewer can group."""

    kind: AdvisoryKind
    # One sentence, for a human or an agent. Rendered into a response's
    # `warning` key, so it has to stand alone.
    message: str
    # The nodes the advisory is about.
    subjects: list[str] = Field(default_factory=list)
    # Structured evidence, per kind — the premise ids and their periods for
    # `DISJOINT_PREMISES`. Structured rather than folded into `message` because
    # the reviewer sorts on it.
    detail: dict = Field(default_factory=dict)


# The one kind named by default, and it is a compatibility requirement rather
# than a preference: `record_contradiction` has always returned `notify_user`
# for a same-frame pair, and a policy defaulting it to `proceed` would keep the
# key while quietly changing its trigger. Turning the notification off stays
# available — it just has to be somebody's decision rather than a side effect.
#
# `SAME_FRAME_VARIANT` is deliberately **not** here. `record_variant` returned a
# quiet note for a same-frame pair and still does; making it escalate would have
# been an unasked-for change to a tool nobody was reviewing. A graph that wants
# it louder names it.
DEFAULT_BY_KIND: dict[AdvisoryKind, AdvisoryAction] = {
    AdvisoryKind.SAME_FRAME_CONTRADICTION: AdvisoryAction.FLAG,
}


class WarningPolicy(BaseModel):
    """What to do about advisories on this graph, and whether to say so."""

    # The global switch. False stops advisories being *surfaced*; it never stops
    # them being recorded. That separation is the load-bearing part: a graph
    # whose warnings were off for a month should still answer *what was decided
    # while nobody was looking*, which is exactly when the question matters.
    surface: bool = True
    default_action: AdvisoryAction = AdvisoryAction.PROCEED
    by_kind: dict[AdvisoryKind, AdvisoryAction] = Field(
        default_factory=lambda: dict(DEFAULT_BY_KIND)
    )


def resolved_action(policy: WarningPolicy, kind: AdvisoryKind) -> AdvisoryAction:
    """The action for one kind. Pure, and the only place the fallback lives."""
    return policy.by_kind.get(kind, policy.default_action)


def notify_user(policy: WarningPolicy, advisories: list[Advisory]) -> bool:
    """Whether any of these is escalated to the person.

    Read by the tools that already return a `notify_user` key, so the key keeps
    its name and gains a policy behind it. Callers pass the **surfaced** set: an
    advisory the agent was never shown cannot be relayed, and `notify_user: true`
    with no text to relay is an instruction nobody can follow.
    """
    return any(
        resolved_action(policy, advisory.kind) is AdvisoryAction.FLAG
        for advisory in advisories
    )


def surfaced(policy: WarningPolicy, advisories: list[Advisory]) -> list[Advisory]:
    """The advisories this graph shows the agent.

    `surface` is the global mute, and **an explicitly named `flag` outranks
    it**. That is the same specific-beats-general rule the `resolve_*` functions
    keep everywhere else: naming a kind in `by_kind` is a stronger statement
    than a switch that names none, so muting the graph does not withdraw an
    escalation somebody asked for by name. A kind following `default_action`
    is not named and is silenced, which is what makes the exception narrow.

    To quieten a named kind, set it to `proceed` — the escalation then stops
    being somebody's standing instruction, which is the only honest way to
    withdraw one.
    """
    if policy.surface:
        return list(advisories)
    return [
        advisory for advisory in advisories
        if policy.by_kind.get(advisory.kind) is AdvisoryAction.FLAG
    ]


def objects_to_the_call(advisories: list[Advisory]) -> bool:
    """Whether any of these argues the operation may be wrong.

    What decides a `proceeded_despite_advisory` row. Read over **every**
    advisory rather than the surfaced ones, because recording is unconditional:
    a graph with warnings muted should still answer *what was decided while
    nobody was looking*.
    """
    return any(
        ADVISORY_STANCE[advisory.kind] is AdvisoryStance.OBJECTS
        for advisory in advisories
    )
