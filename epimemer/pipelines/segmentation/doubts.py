"""The server's doubts about a cut it made by rule.

After a programmatic strategy (paragraph or semantic) has cut a document, these
checks look at the passages for structural signs that the cut is poor. They
report and refuse nothing: the passages are stored either way, and the agent
reads the doubts and decides whether the cut needs its attention.

Doubts are their own small vocabulary rather than `Advisory` warnings. A
warning argues with something the agent did and carries a stance and a
`notify_user` flag; a doubt is the server's unease about its own work, wants no
person, and is not a verdict (`dev-docs/SEGMENTATION.md` §6.2).

The thresholds are constants rather than settings. They are starting guesses,
to be moved by measuring how often each doubt fires on real graphs
(`scripts/segmentation_doubts.py`), and a per-graph setting would make the
calibration a property of whichever graph was measured.
"""

import statistics
from collections.abc import Sequence
from enum import Enum

from pydantic import BaseModel

from epimemer.core.types import Segment

# A document longer than this, in characters, left as one passage is doubted.
# A paragraph-sized note left whole is the author's own cut, while a dozen
# sentences with no break are worth a look.
SINGLE_PASSAGE_MIN_CHARS = 1200

# A passage shorter than this, in characters, is a fragment: too short to
# stand on its own as the context of a claim.
FRAGMENT_MAX_CHARS = 40

# Fragments are doubted only in a cut of at least this many passages, so a
# short note of two or three lines is not mistaken for a shredded document.
FRAGMENTS_MIN_PASSAGES = 4

# A passage longer than this multiple of the median passage length is outsized.
OUTSIZED_RATIO = 4

# The median means little with fewer passages than this, so outsized passages
# are only looked for in cuts at least this long.
OUTSIZED_MIN_PASSAGES = 3

# A passage ending in one of these, before any closing quotes or brackets,
# ends a sentence or introduces what follows.
TERMINAL_PUNCTUATION = ".!?:;"

# Closing quotes and brackets that may follow the terminal punctuation.
CLOSERS = "\"')]}”’»"

# A passage of one line shorter than this, in characters, is a title or a
# label, which ends without punctuation and is not an unfinished sentence.
TITLE_MAX_CHARS = 80

# How many characters of a passage a detail quotes.
PREVIEW_CHARS = 40

# How many passages a detail names before summarising the rest as a count.
NAMED_PASSAGES = 5


class DoubtKind(str, Enum):
    """What the server doubts about its own cut."""

    # The whole of a long document is one passage.
    SINGLE_PASSAGE = "single_passage"
    # Most passages are too short to stand on their own.
    FRAGMENTS = "fragments"
    # One passage is several times the median length.
    OUTSIZED = "outsized"
    # A cut falls inside a sentence.
    MID_SENTENCE = "mid_sentence"


class Doubt(BaseModel):
    """One doubt, with `detail` naming the passages it is about by index."""

    kind: DoubtKind
    detail: str


def _flat(text: str) -> str:
    return " ".join(text.split())


def _head(text: str) -> str:
    flat = _flat(text)
    return repr(flat if len(flat) <= PREVIEW_CHARS else flat[:PREVIEW_CHARS] + "...")


def _tail(text: str) -> str:
    flat = _flat(text)
    return repr(flat if len(flat) <= PREVIEW_CHARS else "..." + flat[-PREVIEW_CHARS:])


def _named(entries: list[str]) -> str:
    shown = "; ".join(entries[:NAMED_PASSAGES])
    hidden = len(entries) - NAMED_PASSAGES
    return shown if hidden <= 0 else f"{shown}; and {hidden} more"


def _ends_a_sentence(text: str) -> bool:
    body = text.rstrip().rstrip(CLOSERS)
    return bool(body) and body[-1] in TERMINAL_PUNCTUATION


def _is_title(text: str) -> bool:
    """A markdown heading, or a single line short enough to be a title."""
    body = text.strip()
    return body.startswith("#") or ("\n" not in body and len(body) < TITLE_MAX_CHARS)


def _begins_lower_case(text: str) -> bool:
    body = text.lstrip()
    return bool(body) and body[0].islower()


def _single_passage(content: str, texts: list[str]) -> Doubt | None:
    if len(texts) != 1 or len(content) <= SINGLE_PASSAGE_MIN_CHARS:
        return None
    return Doubt(
        kind=DoubtKind.SINGLE_PASSAGE,
        detail=(
            f"passage 0 holds the whole document, {len(content)} characters: {_head(texts[0])}"
        ),
    )


def _fragments(texts: list[str]) -> Doubt | None:
    if len(texts) < FRAGMENTS_MIN_PASSAGES:
        return None
    short = [(i, t) for i, t in enumerate(texts) if len(t) < FRAGMENT_MAX_CHARS]
    if len(short) * 2 <= len(texts):
        return None
    return Doubt(
        kind=DoubtKind.FRAGMENTS,
        detail=(
            f"{len(short)} of {len(texts)} passages are under {FRAGMENT_MAX_CHARS} "
            f"characters: " + _named([f"passage {i} {_head(t)}" for i, t in short])
        ),
    )


def _outsized(texts: list[str]) -> Doubt | None:
    if len(texts) < OUTSIZED_MIN_PASSAGES:
        return None
    median = statistics.median(len(t) for t in texts)
    big = [(i, t) for i, t in enumerate(texts) if len(t) > OUTSIZED_RATIO * median]
    if not big:
        return None
    return Doubt(
        kind=DoubtKind.OUTSIZED,
        detail=(
            f"median passage is {median:g} characters; "
            + _named([f"passage {i} is {len(t)} characters {_head(t)}" for i, t in big])
        ),
    )


def _mid_sentence(texts: list[str]) -> Doubt | None:
    splits = [
        f"passage {i} ends {_tail(before)} and passage {i + 1} begins {_head(after)}"
        for i, (before, after) in enumerate(zip(texts, texts[1:], strict=False))
        if not _ends_a_sentence(before) and not _is_title(before) and _begins_lower_case(after)
    ]
    if not splits:
        return None
    return Doubt(kind=DoubtKind.MID_SENTENCE, detail=_named(splits))


def doubt_cut(content: str, segments: Sequence[Segment]) -> list[Doubt]:
    """The server's doubts about a programmatic cut of `content` into `segments`.

    At most one doubt of each kind, in the order of `DoubtKind`. Passages are
    numbered in document order, whatever order they are given in.
    """
    texts = [s.text for s in sorted(segments, key=lambda s: s.span_start)]
    found = [
        _single_passage(content, texts),
        _fragments(texts),
        _outsized(texts),
        _mid_sentence(texts),
    ]
    return [doubt for doubt in found if doubt is not None]
