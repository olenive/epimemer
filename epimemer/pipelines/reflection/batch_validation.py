"""Check an `apply_reflection` batch before any of it applies.

`apply_reflection` applies in ten steps with no transaction across them, and the
step order is load-bearing — the anchoring rule: judgments first, because
later steps retire the nodes those judgments name. So a malformed entry part-way
down a batch cannot be rolled back. Everything above it is already committed,
and the caller receives `{"error": "'pair'"}`: a response that says the call
failed and cannot say what landed. Measured on the in-memory store, a valid
similarity verdict beside a `relation_verdicts` entry with no `pair` left one
`similarity` row written and reported the call as a total failure.

That reading is not merely incomplete, it is actively misleading, because a
similarity verdict is **permanently suppressing**. The obvious next move — fix
the malformed entry, resend the batch — is then the move that meets a refusal
the agent has no reason to expect, on the one entry that did land.

**The fix is not a transaction**, since the step order has to stay. It is that
everything which could raise from inside the loops is settled before the first
write, so a batch either applies or never existed.

**What belongs here, and what does not.** This module covers exactly what would
otherwise raise: a required key the loop reads with `spec["…"]`, an entry that
is not the shape the loop indexes into, and the few values that are parsed or
looked up in a closed vocabulary. It does **not** cover judgments the server can
evaluate and reject — an unknown node id, a stale kind, a verdict already
recorded, a merge below the similarity bar. Those stay per-entry, coming back in
`*_refused` with a reason, and refusing a whole batch because one nominated pair
had already been judged would be a worse answer than the one it replaces.

The line is *could this entry be applied at all*, not *should it be*.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime

from pydantic import BaseModel

from epimemer.core.types import SUPERSESSION_REASONS


class MalformedEntry(BaseModel):
    """One entry that could not be applied at all, and why.

    `index` is the entry's position in the list it was sent in, which is the
    only handle it has: entries carry no ids, and two malformed ones can be
    identical.
    """

    field: str
    index: int
    problem: str


# The keys each loop in `apply_reflection` reads with `spec["…"]`, which raises
# when the key is absent. Declared here rather than checked inline so that
# `tests/mcp/test_apply_reflection_validation.py` can compare this table against
# the function's own subscripts — a key added to a loop and not added here would
# restore the defect quietly, and the guard is what makes that impossible.
REQUIRED_KEYS: dict[str, tuple[str, ...]] = {
    "similarities": ("pair", "verdict"),
    "relation_verdicts": ("pair",),
    "parents": ("children_ids", "content"),
    "splits": ("topic_id", "subtopics"),
    "enrichments": ("topic_id", "new_content"),
    "merges": ("source_ids", "content"),
    "supersessions": ("old_id", "by_id", "because"),
    "judgments": ("node_id", "direction", "reason"),
    "boundaries": ("node_id", "source_id", "endpoint", "at"),
}

# Keys a loop reads but which are **deliberately** not batch-level, listed so
# the drift guard treats each as a decision rather than an omission.
#
# `relation_verdicts.kind` is refused per entry deliberately, and the reason
# is specific: an absent `kind` is an agent who stated none, and the refusal
# says so and asks them to copy it from the nomination. Promoting it here would
# throw away nine good verdicts because the tenth was incomplete — the shape
# this module exists to prevent, applied in the other direction.
DELIBERATELY_PER_ENTRY: dict[str, tuple[str, ...]] = {
    "relation_verdicts": ("kind",),
}

# Values the loops iterate. A non-sequence raises; a bare string is worse, since
# iterating it yields characters and the entry applies against ids nobody sent.
LIST_VALUED: dict[str, tuple[str, ...]] = {
    "similarities": ("pair",),
    "relation_verdicts": ("pair",),
    "parents": ("children_ids",),
    "splits": ("subtopics",),
    "merges": ("source_ids",),
}

# Lists whose entries are bare node ids rather than objects.
ID_VALUED: tuple[str, ...] = ("archivals",)


def _is_list(value: object) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes))


def _entry_problems(field: str, entry: object) -> list[str]:
    """Everything wrong with one entry, or an empty list."""
    required = REQUIRED_KEYS[field]
    if not isinstance(entry, Mapping):
        return [
            f"entry is {type(entry).__name__}, not an object with "
            f"{', '.join(repr(key) for key in required)}"
        ]

    missing = [f"{key!r} is required" for key in required if key not in entry]
    if missing:
        # Return here: every check below indexes a key that has to be present.
        return missing

    problems: list[str] = []
    for key in LIST_VALUED.get(field, ()):
        if not _is_list(entry[key]):
            problems.append(
                f"{key!r} must be a list, not {type(entry[key]).__name__}"
            )
    if field in ("similarities", "relation_verdicts"):
        pair = entry["pair"]
        if _is_list(pair) and len(pair) != 2:
            problems.append(
                f"'pair' names {len(pair)} thing(s); a verdict is about two"
            )
    if field == "supersessions" and entry["because"] not in SUPERSESSION_REASONS:
        problems.append(
            f"'because' is {entry['because']!r}, and the supersession reasons "
            f"are a closed set: {', '.join(sorted(SUPERSESSION_REASONS))}. It "
            f"is a judgment about what happened — if you cannot tell which, "
            f"leave the pair contested rather than guessing."
        )
    if field == "boundaries" and not isinstance(entry["at"], datetime):
        try:
            datetime.fromisoformat(entry["at"])
        except (TypeError, ValueError):
            problems.append(
                f"'at' is neither a datetime nor an ISO-8601 string: "
                f"{entry['at']!r}"
            )
    return problems


def malformed_entries(
    batch: Mapping[str, Sequence[object] | None],
) -> list[MalformedEntry]:
    """Every entry in `batch` that could not be applied at all.

    Pure, and reads no storage: whether an entry *can* be applied is a question
    about the entry, while whether it *should* be is a question about the graph
    and belongs in the step that asks it.

    Every malformed entry is reported, not the first — an agent fixing one and
    resending into the next refusal is the treadmill in miniature.
    """
    found: list[MalformedEntry] = []
    for field in REQUIRED_KEYS:
        for index, entry in enumerate(batch.get(field) or []):
            found.extend(
                MalformedEntry(field=field, index=index, problem=problem)
                for problem in _entry_problems(field, entry)
            )
    for field in ID_VALUED:
        for index, node_id in enumerate(batch.get(field) or []):
            if not isinstance(node_id, str):
                found.append(MalformedEntry(
                    field=field, index=index,
                    problem=(
                        f"entry is {type(node_id).__name__}, not a node id"
                    ),
                ))
    return found


def refusal_message(found: Sequence[MalformedEntry]) -> str:
    """What the agent is told, and it has to lead with what was written."""
    listed = "\n".join(
        f"  {item.field}[{item.index}]: {item.problem}" for item in found
    )
    entries = "entry is" if len(found) == 1 else f"{len(found)} entries are"
    return (
        f"apply_reflection wrote nothing: {entries} malformed.\n{listed}\n\n"
        "The whole batch is checked before the first step applies, so nothing "
        "in it landed and resending the corrected batch meets no half-applied "
        "state. That matters most for `similarities` and `relation_verdicts`, "
        "whose suppressions are permanent: had the valid entries applied, the "
        "resend would be refused as a repeat verdict on exactly those."
    )
