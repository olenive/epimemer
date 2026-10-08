"""How a document was cut, as its `metadata["segmentation"]` records it.

Every path that cuts writes this entry, so a reader of a passage can tell
whether its boundaries were a rule's or a judgment's. A re-cut keeps the cut it
replaced in `history`, oldest first (`dev-docs/SEGMENTATION.md` §5.4):

    {"strategy": "paragraph" | "semantic" | "agent",
     "passages": <count>,
     "history": [{"strategy": "paragraph", "passages": 1,
                  "replaced_at": "...", "replaced_by": <judge or null>}]}

No journal row goes with it. A document and its passages carry no judge, and
a cut is not a verdict about the world, so it stays out of `review`.
"""

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from epimemer.core.types import JudgeRef

# The strategy a cut stated by anchors is recorded under.
AGENT_STRATEGY = "agent"


def cut_record(strategy: str, passages: int) -> dict[str, Any]:
    """The entry for a document's first cut, with nothing replaced yet."""
    return {"strategy": strategy, "passages": passages, "history": []}


def recut_record(
    previous: Mapping[str, Any] | None,
    *,
    replaced: int,
    passages: int,
    at: datetime,
    judge: JudgeRef | None,
) -> dict[str, Any]:
    """The entry after the agent re-cuts a document, keeping the cut it replaced.

    `previous` is the entry as stored, or None for a document cut before the
    entry existed; `replaced` is how many passages that cut left, which is the
    count the history keeps when `previous` has none. A strategy nobody recorded
    stays unrecorded rather than being guessed, and a missing judge is None,
    which storage keeps as an absent key.
    """
    before = dict(previous or {})
    replaced_entry = {
        "strategy": before.get("strategy"),
        "passages": before.get("passages", replaced),
        "replaced_at": at.isoformat(),
        "replaced_by": None if judge is None else judge.model_dump(mode="json"),
    }
    return {
        "strategy": AGENT_STRATEGY,
        "passages": passages,
        "history": [*before.get("history", []), replaced_entry],
    }
