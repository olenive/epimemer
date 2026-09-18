"""Building one `GraphActionRecorded` — the verb, the id and the rendered line.

`instrumented_storage` sits exactly at the transaction boundaries and already
holds every id involved, so it is where the act is emitted. What it should
*say* lives here: kept out of the wrapper so the vocabulary is decided once, and
so it can be read and tested without a storage backend behind it
(EVENT_LOG.md §3.1).
"""

from collections.abc import Iterator, Mapping, Sequence
from itertools import count

from epimemer.core.types import DecisionKind, JudgeRef, NodeStatus
from epimemer.visualization.events import ActionVerb, GraphActionRecorded

# One sequence per process, which is one sequence per session: `session_id` is a
# fresh uuid4 per MCP process, so a restart is a different session with its own
# ring and its own numbering. Zero-padded so the lexical order a JSON consumer
# gets for free is the numeric one (§4.1).
_ACTION_IDS: Iterator[int] = count(1)


def next_action_id() -> str:
    """The next action id for this process."""
    return f"{next(_ACTION_IDS):012d}"


# A status flip reads as the act that caused it. `SUPERSEDED` is the legacy
# value kept for rows that predate the split; it maps to `UNDETERMINED`
# rather than to a guess between the two acts it might have been.
#
# That entry sunsets with the enum member, not on its own: delete it in the same
# change that removes `NodeStatus.SUPERSEDED` from `core/types.py`, since nothing
# in `epimemer/` writes the status and the member is its whole remaining supply
# (EVENT_LOG.md §11.1). The verb does not go with it — the fall-through below
# answers for statuses that do not exist yet, so it is never spent.
_STATUS_VERBS: Mapping[NodeStatus, ActionVerb] = {
    NodeStatus.ACTIVE: ActionVerb.RESTORED,
    NodeStatus.CORRECTED: ActionVerb.CORRECTED,
    NodeStatus.HISTORICAL: ActionVerb.WORLD_CHANGED,
    NodeStatus.MERGED: ActionVerb.MERGED,
    NodeStatus.ARCHIVED: ActionVerb.ARCHIVED,
    NodeStatus.SUPERSEDED: ActionVerb.UNDETERMINED,
}


def verb_for_status(status: NodeStatus) -> ActionVerb:
    """The verb a flip to `status` reads as.

    Falls through to `UNDETERMINED` for a status this module has never heard
    of, because a status is added by someone who has no cause to look here.
    The frontend's `statusOpacity` defaults the other way — an unlisted status
    *fades* — and that asymmetry is deliberate: fading a live node is a
    cosmetic error, while a log line saying a node was retired when it was not
    is a false statement about what the agent did.
    """
    return _STATUS_VERBS.get(status, ActionVerb.UNDETERMINED)


def _short(node_id: str) -> str:
    """Enough of an id to recognise, not enough to fill the line."""
    return node_id[:8]


def _swept(counts: Mapping[str, int]) -> str:
    """ "3 edges, 1 node" — what the act carried along, largest kind first."""
    parts = [
        f"{n} {kind if n != 1 else kind.removesuffix('s')}"
        for kind, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        if n
    ]
    return ", ".join(parts)


# What each timeline decision reads as. The verb set stays closed, so a
# timeline act carries its `DecisionKind`; a kind is a noun and a log line needs
# a verb phrase, which is what these supply (EVENT_LOG.md §11).
_DECISION_LEADS: Mapping[DecisionKind, str] = {
    DecisionKind.TEMPORAL_ORDER: "ordered timepoints on",
    DecisionKind.TEMPORAL_VERDICT: "answered a temporal contradiction on",
    DecisionKind.TIMEPOINT_MERGE: "merged timepoints on",
    DecisionKind.RECURRENCE: "recorded a recurrence on",
    DecisionKind.RECURRENCE_BOUND: "ended a recurrence on",
    DecisionKind.RECURRENCE_EXCEPTION: "recorded a recurrence exception on",
    # Not a timeline decision, and here for the same reason they are: the verb
    # set stays closed, so a reopen carries its kind and needs a verb phrase.
    # No trailing "on": a reopen names what it put back, not a record it
    # changed.
    DecisionKind.REOPENED: "reopened",
}


def _decision_lead(kind: DecisionKind, subjects: Sequence[str]) -> str:
    """ "merged timepoints on a1b2c3d4": the subject is the timeline, not a node.

    A kind with no entry falls back to its own words rather than to the node
    phrasing above, because a line saying "N nodes" about a timeline would state
    something false about what was touched.
    """
    lead = _DECISION_LEADS.get(kind, f"{kind.value.replace('_', ' ')} on")
    return f"{lead} {_short(subjects[0])}" if subjects else lead


def summarise(
    verb: ActionVerb | DecisionKind,
    subjects: Sequence[str],
    counts: Mapping[str, int],
) -> str:
    """The one line a person reads, rendered here rather than in the frontend.

    Short ids, because the line is scanned rather than copied; the full ids ride
    on `subjects`, which is what the log's id filter and click-to-highlight use.
    """
    swept = _swept(counts)
    if isinstance(verb, DecisionKind):
        lead = _decision_lead(verb, subjects)
        return f"{lead} ({swept})" if swept else lead
    match verb:
        case ActionVerb.CORRECTED | ActionVerb.WORLD_CHANGED if len(subjects) >= 2:
            lead = (
                f"corrected {_short(subjects[0])} → {_short(subjects[1])}"
                if verb is ActionVerb.CORRECTED
                else f"world-change: {_short(subjects[0])} → {_short(subjects[1])}"
            )
        case ActionVerb.MERGED if subjects:
            others = len(subjects) - 1
            lead = f"merged {others} node{'' if others == 1 else 's'} into {_short(subjects[0])}"
        case ActionVerb.STORED:
            lead = "stored"
        case ActionVerb.UNDETERMINED:
            # The verb names a state rather than an act, so it cannot carry the
            # line on its own: "undetermined 1 node" says nothing happened to
            # anything. What is known is that the status moved and that we
            # cannot say to what effect.
            lead = f"status undetermined: {len(subjects)} node" + (
                "" if len(subjects) == 1 else "s"
            )
        case _:
            lead = f"{verb.value.replace('_', '-')} {len(subjects)} node" + (
                "" if len(subjects) == 1 else "s"
            )
    return f"{lead} ({swept})" if swept else lead


def graph_action(
    *,
    graph: str,
    verb: ActionVerb | DecisionKind,
    subjects: Sequence[str],
    counts: Mapping[str, int],
    judged_by: JudgeRef | None = None,
) -> GraphActionRecorded:
    """One act, ready to publish. Zero counts are dropped rather than rendered."""
    kept = {kind: n for kind, n in counts.items() if n}
    return GraphActionRecorded(
        graph=graph,
        action_id=next_action_id(),
        verb=verb,
        subjects=list(subjects),
        counts=kept,
        summary=summarise(verb, subjects, kept),
        judged_by=None if judged_by is None else judged_by.agent_id,
    )
