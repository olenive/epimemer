"""Which decisions `review()` is looking at, and how it is narrowed (§6.1, §6.3).

**Three things are separate**: *which* decisions, *what order*, and *whether*
the list is narrowed further. Ordering lives in `difficulty.py`. This module
owns the first and the third.

**A mode names the selection; the arguments narrow whatever it selected.**
`agent_id`, `since` and `until` are available under every mode, so a single
`mode` string still composes with them: `by_agent` and `since` is the ordinary
case. Most modes are the thing that cannot be expressed as a field
filter; `warning` is the exception and is a selection on kind, which is data in
`MODE_KINDS` rather than a branch. One further name exists only to **refuse**.

**`by_agent` and `since` are sugar over a required argument, and the refusal is
the whole value.** `review(mode="all")` with an `agent_id` the caller forgot to
pass returns the entire journal, which reads as *"agent-1 decided everything"*:
a filter that silently returns everything, which is the failure a short
`REVIEW_MODES` exists to avoid. Naming the mode makes the argument mandatory,
so the mistake refuses instead of answering wrongly.

**One designed name is refused rather than admitted.** `between` is not a
second mode: it is `since` with an `until`, and two names for one selection is
two shapes for one question (§6.1).
"""

from epimemer.core.types import DecisionKind, DecisionRecord

# The modes that exist, in the order a tool schema should list them.
REVIEW_MODES: tuple[str, ...] = (
    "all",
    "by_agent",
    "since",
    "unreviewed",
    "warning",
)

# The modes that are a selection on kind, and which kinds. A mode absent from
# this map selects every kind. Data rather than a branch in the tool, for the
# reason `MODE_REQUIRES` is data: adding one is a line, and the tool keeps one
# path through it.
MODE_KINDS: dict[str, list[DecisionKind]] = {
    "warning": [DecisionKind.PROCEEDED_DESPITE_WARNING],
}

# Which argument each mode cannot answer without. A mode absent from this map
# requires nothing; a mode present in it refuses when its argument is blank.
MODE_REQUIRES: dict[str, str] = {
    "by_agent": "agent_id",
    "since": "since",
}

# Designed names that are not modes here, each with the reason in the refusal.
# Kept as data rather than as an `else` branch so the two cases read the same
# way as the unknown-mode case, and so adding one is one line.
UNBUILT_MODES: dict[str, str] = {
    "between": (
        "'between' is 'since' with an end: pass mode='since' with both `since` "
        "and `until`. `until` is exclusive, so adjacent windows neither overlap "
        "nor drop a row on the boundary."
    ),
}


def mode_refusal(mode: str, *, agent_id: str | None, since_given: bool) -> str | None:
    """Why this call cannot be answered as asked, or None.

    `since_given` rather than the datetime itself: the caller has already parsed
    it, and a mode check that re-parsed would be a second place for the format
    to be decided.
    """
    if mode in UNBUILT_MODES:
        return f"'{mode}' is not a mode this server implements. {UNBUILT_MODES[mode]}"
    if mode not in REVIEW_MODES:
        return (
            f"'{mode}' is not a mode. Available: {', '.join(REVIEW_MODES)}. "
            f"Also designed but not modes here: "
            f"{', '.join(sorted(UNBUILT_MODES))}; ask about either by name."
        )
    required = MODE_REQUIRES.get(mode)
    supplied = {"agent_id": agent_id is not None, "since": since_given}
    if required is not None and not supplied[required]:
        return (
            f"mode='{mode}' needs `{required}`, and without it this would "
            f"return the whole journal — which reads as an answer rather than "
            f"as a missing filter."
        )
    return None


def passes_ceiling(record: DecisionRecord, ceiling: float | None) -> bool:
    """§6.3's `certainty_ceiling`, **inclusive**, excluding the unrated.

    Inclusive on `importance_ceiling`'s grounds — *"nomination is a proposal
    rather than a verdict"* — and because the guidance says to **omit** at 0.5,
    so an agent that typed 0.5 anyway was making a point of it.

    Unrated rows are excluded rather than treated as 0.5: blank cannot be
    distinguished from ordinary, and this filter's use is *counting*
    — *"is anything below 0.5 still outstanding?"* — where a blank counted in
    either direction is an invented answer. `unrated_count` is in every
    response so the excluded population stays visible.
    """
    if ceiling is None:
        return True
    return record.certainty is not None and record.certainty <= ceiling
