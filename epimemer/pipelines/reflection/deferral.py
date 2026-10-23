"""*Not now, ask again later*: the third answer to a `never_retrieved` nominee.

A `never_retrieved` nominee had two answers. `archivals` sets it aside, which
the user approves. `retained` keeps it, and for this class a keep is permanent:
the nomination names no reason, so the keep covers the node itself, and nothing
that arrives later can be a reason it failed to cover. The keep stands until
`reopen` withdraws it.

That left nothing for the common case in between: a node that is not worth the
user's attention today and may well be next month, once the graph has grown
around it. Answering `retained` for it hides the node for good, and leaving it
unanswered puts it back on every reflect.

**A deferral is a `deferral` journal row, and the wait is counted in reflect
rounds.** Its `at_round` is the graph's reflect round when the row was written.
The node is left out of `archival_candidates` until enough rounds have passed,
and then it comes back carrying what was said about it last time. Each deferral
of the same node doubles the wait, up to a cap, so a node that keeps being put
off is asked about less and less often, and never stops being asked about.

**Rounds rather than days**, because what makes a second look worthwhile is
something new in the graph, and a graph left alone for a month has nothing new.
A round is a reflect that completes with at least one store since the previous
reflect; the backend's `complete_reflect_round` advances it in the same atomic
step that zeroes the reflect counter.

**A deferral lapses on its own, and that is the difference from a keep.** Two
rows clear a node's deferral state before the wait is over: a `retention` for
it, which is a firmer answer replacing a softer one, and a one-subject
`reopened` row naming it, which is `reopen` asking for the question now. A
deferral written after either starts counting again from one.

**The rule is two pure functions and one read.** `deferral_wait` and
`deferral_due` are the rule, stated where a test can check it without a store;
`deferrals_for` replays the journal into the state the rule reads.

**This writer raises where most journal writes swallow their errors**, for
`record_retention`'s reason: the row is the act. A deferral reported as stored
that never landed would put the node back on the next reflect in front of an
agent who was told it would wait.
"""

from collections.abc import Sequence
from datetime import datetime

from pydantic import BaseModel

from epimemer.core.types import DecisionKind, DecisionRecord, JudgeRef
from epimemer.storage.protocol import (
    DeferralSettings,
    StorageBackend,
    resolve_deferral_settings,
)


class DeferralState(BaseModel):
    """What the journal says about one node's standing deferral.

    `count` is how many deferrals stand since the last thing that cleared the
    node's state, so it is 1 after the first. The rest describe the latest of
    them: the round it was written in, when, why, and by whom.
    """

    count: int
    last_round: int
    last_at: datetime
    because: str
    judged_by: str | None = None


def deferral_wait(count: int, settings: DeferralSettings) -> int:
    """How many reflect rounds a node deferred `count` times waits.

    `first_wait` after the first deferral, doubling with each one after it, and
    never more than `cap`. A count below one is read as one, since there is no
    wait without a deferral to measure it from.
    """
    return min(settings.first_wait * 2 ** (max(count, 1) - 1), settings.cap)


def deferral_due(
    state: DeferralState | None, current_round: int, settings: DeferralSettings
) -> bool:
    """Whether a node may be nominated again, given its deferral state.

    True for a node nobody deferred, and for one whose wait is over. True as
    well when the graph's round is *behind* the round the deferral was written
    in. A graph whose round came back from an older bundle, or was reset apart
    from its journal, can show that, and the safe failure is to show the node
    rather than hide it for a wait nobody can measure.
    """
    if state is None:
        return True
    if current_round < state.last_round:
        return True
    return current_round - state.last_round >= deferral_wait(state.count, settings)


def rounds_waited(state: DeferralState, current_round: int) -> int:
    """How many rounds have passed since the latest deferral.

    Zero rather than negative where the round is behind the deferral (a restored
    bundle): the number of rounds since is then unknown, and a negative count
    would read as a wait in the future.
    """
    return max(current_round - state.last_round, 0)


async def deferrals_for(
    node_ids: Sequence[str], storage: StorageBackend
) -> dict[str, DeferralState]:
    """For each node, its standing deferral state. Nodes with none are absent.

    One batched query for the whole set, as `confirmed_reasons_for` reads
    retentions, replayed oldest first: a `deferral` row adds one to the count
    and becomes the latest; a `retention` row for the node, or a one-subject
    `reopened` row naming it, clears the node's entry. A deferral written after
    the clear starts again from one.

    A `reopened` row naming two subjects is about a pair or a declined
    boundary, never about one node, so it is skipped here as it is there.

    A row without `at_round` reads as round 0. Every row this module writes has
    one; the reading exists so a row from elsewhere cannot raise.
    """
    ids = list(node_ids)
    if not ids:
        return {}
    wanted = set(ids)
    rows = await storage.query_decisions(
        kinds=[DecisionKind.DEFERRAL, DecisionKind.RETENTION, DecisionKind.REOPENED],
        subject_ids=ids,
    )
    states: dict[str, DeferralState] = {}
    # `query_decisions` answers newest first; replaying needs oldest first.
    for row in reversed(rows):
        if row.kind is DecisionKind.REOPENED:
            if len(row.subject_ids) == 1 and row.subject_ids[0] in wanted:
                states.pop(row.subject_ids[0], None)
            continue
        for node_id in row.subject_ids:
            if node_id not in wanted:
                continue
            if row.kind is DecisionKind.RETENTION:
                states.pop(node_id, None)
                continue
            previous = states.get(node_id)
            states[node_id] = DeferralState(
                count=1 if previous is None else previous.count + 1,
                last_round=row.at_round or 0,
                last_at=row.decided_at,
                because=row.certainty_basis or "",
                judged_by=None if row.judged_by is None else row.judged_by.agent_id,
            )
    return states


async def deferral_settings_for(
    storage: StorageBackend, defaults: DeferralSettings | None
) -> DeferralSettings:
    """The deferral numbers in force on the active graph.

    `defaults` is the process default from `ServerConfig`; `None` is the
    built-in one, which is what a caller without a config means.
    """
    return resolve_deferral_settings(
        await storage.get_deferral_overrides(),
        defaults if defaults is not None else DeferralSettings(),
    )


async def record_deferral(
    storage: StorageBackend,
    *,
    node_id: str,
    because: str,
    at_round: int,
    judge: JudgeRef | None = None,
) -> DecisionRecord:
    """Write one deferral verdict. Returns the row it wrote.

    One `deferral` row, append-only: `subject_ids` is the node, `at_round` the
    round the wait is measured from, and `because` goes in `certainty_basis`,
    where every journal row carries its prose.

    **A failed write raises**: the row is the deferral, so the caller reports
    the node as skipped rather than as deferred.
    """
    record = DecisionRecord(
        kind=DecisionKind.DEFERRAL,
        subject_ids=[node_id],
        judged_by=judge,
        certainty_basis=because,
        at_round=at_round,
    )
    await storage.record_decision(record)
    return record
