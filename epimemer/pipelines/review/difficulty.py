"""How shaky a decision looks, from properties it never declared (§5, §6.2).

**Two sources of doubt, and they must not be blended into one number.** An agent
may *declare* a `certainty` when it decides; everything here is *derived* after
the fact, from what the decision touched. §5 keeps them apart because they are
different claims — one is somebody's judgment about their own judgment, the
other is an observation about the graph — and an average of the two is neither.

So the ordering is two tiers, never one score. Tier 1 is the declared value,
ascending. Tier 2 is everything unrated, ordered by how many of the signals
below it carries. **Tier 1 comes first, and that is itself a rule**: absence is
not a claim of doubt, so an unrated decision never sorts above one an
agent actually flagged.

**The whole existing corpus is tier 2**, which is why this earns its place at
all: nothing supplies a `certainty` yet, so on today's graph the order is
entirely derived — and it still works. As certainties accumulate tier 1 fills
from the top and the order improves with nothing else changing.

**Unrated confidence is not a signal**, and the first draft of §5 said it was.
The confidence ladder defines absence as *the ordinary case* — "stated plainly, no
specific reason to doubt → omit the field" — and it is the majority state (125
unrated on `memory`). Reading absence as thinness floods the list with ordinary
decisions and re-commits exactly the sin that ladder fixed.

One population these are blind to, recorded rather than papered over: rows
written before 2026-08-19 carry a literal `0.5` confidence and are *genuinely*
unrated, so they pass the thin-source test as rated-ordinary. Nothing can
separate them now.

**There is no similarity band.** §5 cut the first draft's `[0.80, 0.85)` — the
number was invented, and the only two above-bar scores on record sit above it,
so it would have selected neither. Measuring the above-bar distribution is the
precondition for adding it back.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel

from epimemer.core.types import (
    UNRATED_CONFIDENCE,
    DecisionKind,
    DecisionRecord,
    EpistemicNode,
    NodeStatus,
)


class DifficultySignal(str, Enum):
    """§5's four derived signals. **Every member is computed below** — the rule
    `DecisionKind` states, for the same reason: review orders on these, and a
    signal nothing produces is a rank nobody earns."""

    # The material was thin. Read from the subjects' own `value.confidence`,
    # which is the prior the ingesting agent supplied about the record backing
    # them.
    THIN_SOURCE = "thin_source"
    # More sources collapsed into one node is more ways to have been wrong.
    WIDE_MERGE = "wide_merge"
    # Recorded and left standing — by decision or by neglect, which this cannot
    # tell apart and does not claim to.
    OPEN_CONTRADICTION = "open_contradiction"
    # The ground moved under it: something the decision was about has been
    # retired since it was made.
    GROUND_MOVED = "ground_moved"


# Below the ladder's middle rung is *thin*; the rung itself is *ordinary*. The
# same number as `UNRATED_CONFIDENCE` and deliberately derived from it rather
# than typed again: they are one anchor on one ladder, and if that anchor
# ever moves, a copy left behind here would silently disagree about what
# "ordinary" means. A single bar is what this keeps — one constant with
# several declarations pretending to be independent.
THIN_CONFIDENCE_BELOW: float = UNRATED_CONFIDENCE

# How many sources make a merge wide. Three, from §5's table, named here so the
# one place it is read is also the one place it is declared.
WIDE_MERGE_SOURCES: int = 3


def _utc(at: datetime) -> datetime:
    """A timestamp comparable with another one.

    Backends round-trip `datetime` rather than text here, so the string-comparison
    trap does not apply —
    but one of the two sides may still come back naive, and comparing naive with
    aware raises rather than answering wrongly. Assume UTC, which is what every
    writer in this system stores.
    """
    return at.replace(tzinfo=timezone.utc) if at.tzinfo is None else at


def merge_source_count(record: DecisionRecord) -> int:
    """How many sources a merge collapsed.

    `merge_facts` journals `[survivor, *sources]` — the survivor first, so a
    reversal looking for *the merge that made this node* finds it by the id it
    holds. So the source count is one less than the subject count, and reading
    it as the subject count would call every three-source merge wide.
    """
    return max(len(record.subject_ids) - 1, 0)


def difficulty_signals(
    record: DecisionRecord, subjects: Mapping[str, EpistemicNode]
) -> list[DifficultySignal]:
    """§5's signals for one decision, given the nodes it names.

    `subjects` is passed in rather than read here: a page of results shares one
    batched `get_nodes`, and a function that fetched its own would turn an
    ordering pass into one query per subject.

    **An absent subject is not a signal.** A row can name a node that is no
    longer in the graph — a merge survivor a reversal destroyed, or a row read
    beside a graph it was not written in — and reading that as difficulty
    would rank a decision by the reader's position rather than by the decision.
    """
    present = [subjects[sid] for sid in record.subject_ids if sid in subjects]
    signals: list[DifficultySignal] = []

    # `confidence` lives on `ValueSignal`, which every node type carries — the
    # node itself has no such field, and reading it off one is how this first
    # tripped over a `Topic`.
    if any(
        node.value.confidence is not None
        and node.value.confidence < THIN_CONFIDENCE_BELOW
        for node in present
    ):
        signals.append(DifficultySignal.THIN_SOURCE)

    if (
        record.kind is DecisionKind.MERGE
        and merge_source_count(record) >= WIDE_MERGE_SOURCES
    ):
        signals.append(DifficultySignal.WIDE_MERGE)

    # A contradiction resolves by one side being retired — corrected, overtaken
    # by the world, merged away. Both still active means neither happened, which
    # is what `record_contradiction` leaves behind by design: it keeps both and
    # hands the resolution to a later judgment that may never come.
    if (
        record.kind is DecisionKind.CONTRADICTION
        and len(present) == len(record.subject_ids)
        and all(node.status is NodeStatus.ACTIVE for node in present)
    ):
        signals.append(DifficultySignal.OPEN_CONTRADICTION)

    # Strictly *after*, which is what makes this "the ground moved" rather than
    # "this decision retired something". A correction, a merge and an archival
    # sweep all journal their row once the write has landed, so their own
    # subjects carry a retirement instant just before the row's.
    decided_at = _utc(record.decided_at)
    if any(
        node.superseded_at is not None and _utc(node.superseded_at) > decided_at
        for node in present
    ):
        signals.append(DifficultySignal.GROUND_MOVED)

    return signals


class ScoredDecision(BaseModel):
    """One journal row with the doubt attached to it, ready to order."""

    record: DecisionRecord
    signals: list[DifficultySignal]


def review_order(scored: Sequence[ScoredDecision]) -> list[ScoredDecision]:
    """§6.2's ordering: shakiest first, in two tiers that never mix.

    Tier 1 — a declared `certainty` — ascending, so the value an agent flagged
    lowest arrives first. Tier 2 — unrated — by signal count descending.

    Ties break by `decided_at` **newest first**, which is the journal's own
    order, and then by id so the same graph always answers the same way. A page
    that reshuffled between two identical calls would make `truncated` mean a
    different set each time.
    """
    def key(item: ScoredDecision):
        record = item.record
        rated = record.certainty is not None
        return (
            0 if rated else 1,
            record.certainty if rated else 0.0,
            -len(item.signals),
            -_utc(record.decided_at).timestamp(),
            record.id,
        )

    return sorted(scored, key=key)
