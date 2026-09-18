"""Withdrawing a suppression, and reading back that one was withdrawn.

Three nominators stop offering a question once somebody has answered it, and
each keeps its own index of what has been answered: the `assessed` edge for a
fact pair (`similarity_decisions.py`), a `RelationVerdict` for a label pair
(`RELATION_LABELS.md` §4.2), and a `retention` journal row for a single node
(`retention.py`). Suppression is what makes a sweep worth running twice, and
until this module none of the three could be undone. A pair judged `distinct`
in error was silenced for good, however much later evidence said it should be
looked at again.

**A reopen withdraws the suppression and asserts nothing.** It does not record
the opposite of the earlier verdict, and it never claims the earlier judge was
wrong: it says *ask this again*. What comes back is a nomination, and the next
judge answers it with the same tools as the first. That is the whole scope, and
keeping it there is what makes one tool safe for three layers that fail in
different directions: a wrong `one_claim` manufactures corroboration, while
asking a question twice costs attention.

**Nothing is deleted, on each layer's own terms.** The `assessed` edge is
retired the way a moved `TIMELINK` is, stamped with when it stopped counting
and by whom. The verdict table takes a new row rather than losing one. The
journal, as always, only grows. So a pair reopened and judged `distinct` a
second time carries both rounds, and a reader can see the question was asked
twice.

**The history travels with the nomination.** A judge offered a pair that an
earlier judge declined is owed the fact that it was declined and then reopened,
and why: without it, the second judge re-derives the first one's reasoning from
nothing, which is the treadmill one layer up. `reopen_notes` is the read, and
`reflect` attaches what it finds.
"""

from collections.abc import Callable, Iterable, Sequence
from datetime import datetime

from pydantic import BaseModel

from epimemer.core.types import DecisionKind, DecisionRecord
from epimemer.storage.protocol import StorageBackend


class ReopenNote(BaseModel):
    """That this question was answered once, withdrawn, and is being asked again.

    The judge's prose rather than a flag: *reopened* on its own tells the next
    reader that somebody thought the earlier answer was worth revisiting and
    nothing about why, which leaves them to re-derive it.
    """

    reason: str
    at: datetime
    judged_by: str | None = None


def _note(record: DecisionRecord) -> ReopenNote:
    return ReopenNote(
        # `certainty_basis` is where `reopen` writes the reason, which is where
        # every journal row carries its prose.
        reason=record.certainty_basis or "",
        at=record.decided_at,
        judged_by=None if record.judged_by is None else record.judged_by.agent_id,
    )


def notes_by_target(records: Iterable[DecisionRecord]) -> dict[frozenset[str], ReopenNote]:
    """The newest reopen per target, keyed by the ids it named.

    One key shape for all three layers: a fact pair is two node ids, a label
    pair is two label record ids, and a kept node is one node id. They cannot
    collide, because a label record id is never a node id and a pair is never
    one id.

    Newest wins. A target reopened, judged again and reopened a second time
    shows the second reopen, which is the round the nomination in front of the
    reader belongs to.
    """
    newest: dict[frozenset[str], DecisionRecord] = {}
    for record in sorted(records, key=lambda row: (row.decided_at, row.id)):
        if not record.subject_ids:
            continue
        newest[frozenset(record.subject_ids)] = record
    return {target: _note(record) for target, record in newest.items()}


async def reopen_notes(storage: StorageBackend) -> dict[frozenset[str], ReopenNote]:
    """Every standing reopen in this graph, for `reflect` to attach.

    Unfiltered, and that is affordable because of what a reopen is: one
    deliberate act per suppression somebody wanted undone, written by an agent
    rather than by a sweep. The alternative is three narrowed queries keyed on
    three different id populations, which costs more to get right than the scan
    it saves.
    """
    return notes_by_target(await storage.query_decisions(kinds=[DecisionKind.REOPENED]))


def annotate(
    nominations: Sequence[dict],
    target_of: Callable[[dict], Sequence[str]],
    notes: dict[frozenset[str], ReopenNote],
) -> None:
    """Attach `reopened` to each nomination whose target was reopened.

    `target_of` reads the ids out of one nomination, because the lists `reflect`
    assembles have six shapes and the ids sit in a different place in each.
    Mutates the dicts in place: they are the response being assembled, and a
    parallel structure the caller has to re-join would be one more thing to keep
    in step.

    A nomination nobody reopened carries no key at all, so *never reopened* and
    *reopened, reason unrecorded* stay distinguishable.
    """
    for nomination in nominations:
        ids = target_of(nomination)
        if not ids:
            continue
        note = notes.get(frozenset(ids))
        if note is not None:
            nomination["reopened"] = note.model_dump(mode="json")
