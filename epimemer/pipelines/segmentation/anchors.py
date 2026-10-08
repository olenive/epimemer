"""The agent's own cut of a document, stated as anchors.

An anchor is the text a passage begins with, copied exactly from the document.
The agent already holds the whole document, so naming where each passage
starts costs it a few words per passage, and the server can check every one of
them against the text. Offsets were rejected because a wrong offset is silent:
the passage exists, and it is the wrong stretch. Full passage texts were
rejected because they echo the whole document back for the same check
(`dev-docs/SEGMENTATION.md` §4).

The walk is deterministic and needs no notion of ambiguity: each anchor is
found at its **first occurrence** after the end of the one before, so an agent
that means a later occurrence lengthens the anchor until it is the first.

A malformed list refuses the whole call and names every problem at once, so an
agent fixing one does not resend into the next. The refusal is prose, as
`BoundaryRefused` is: nothing branches on it.
"""

import re
import unicodedata
from collections.abc import Sequence

from pydantic import BaseModel

from epimemer.core.types import RawDocument, Segment

# An anchor needs at least this many words, or the characters below: a
# two-word anchor matches too early too often. Whichever the agent finds easier
# to satisfy is enough.
ANCHOR_MIN_WORDS = 3

# Characters of the anchor once leading and trailing whitespace is set aside,
# so padding cannot make a short anchor long.
ANCHOR_MIN_CHARS = 12

# How many characters of an anchor or of skipped text a problem quotes.
QUOTED_CHARS = 60


class AnchorsRefused(BaseModel):
    """Why a list of anchors cannot be turned into passages: every problem found."""

    problems: list[str]


# A quote folds runs of ASCII whitespace into one space, so a line break reads
# as a space. Every other space is left in place for `repr` to escape: a
# non-breaking space where the document has a plain one is the usual way a copy
# fails to match while looking identical, and the escape is how the agent sees it.
_ASCII_WHITESPACE = re.compile(r"[ \t\n\r\f\v]+")


def _quoted(text: str) -> str:
    flat = _ASCII_WHITESPACE.sub(" ", text).strip(" ")
    return repr(flat if len(flat) <= QUOTED_CHARS else flat[:QUOTED_CHARS] + "...")


def _normalisation_hint(content: str, anchor: str, cursor: int) -> str:
    """Why an anchor not found from `cursor` failed, when the reason is Unicode
    normalisation: the anchor in NFC or NFD is found there. Otherwise "".

    A decomposed accent prints exactly like a composed one, so escaping cannot
    show this difference in a quote, and the hint names it instead.
    """
    for form in ("NFC", "NFD"):
        normalised = unicodedata.normalize(form, anchor)
        if normalised != anchor and content.find(normalised, cursor) >= 0:
            return (
                ": it differs from the document's text only in Unicode normalisation, "
                f"and the document has it in {form}"
            )
    return ""


def _too_short(anchor: str) -> bool:
    return len(anchor.split()) < ANCHOR_MIN_WORDS and len(anchor.strip()) < ANCHOR_MIN_CHARS


def _passage(document: RawDocument, start: int, end: int) -> Segment | None:
    """The stretch `[start, end)` trimmed of surrounding whitespace, or None if
    nothing is left. Spans are kept accurate after the trim, as the paragraph
    rule keeps them."""
    raw = document.content[start:end]
    text = raw.strip()
    if not text:
        return None
    span_start = start + raw.index(text)
    return Segment(
        source_id=document.id,
        text=text,
        span_start=span_start,
        span_end=span_start + len(text),
    )


def segments_from_anchors(
    document: RawDocument, anchors: Sequence[str]
) -> list[Segment] | AnchorsRefused:
    """The passages `anchors` cut `document` into, or every reason they cannot.

    The first anchor is found from the start of the document and every later
    one from the end of the anchor before it. Each passage runs from its
    anchor's start to the next anchor's start, the last to the end of the
    document. Text before the first anchor is refused rather than dropped,
    unless it is whitespace. One anchor at the start is a valid cut: the agent
    is saying the document is one passage.

    Pure: reads nothing but its arguments, and the passages carry fresh ids.
    """
    if not anchors:
        return AnchorsRefused(
            problems=["no cuts were given: name at least the text the document begins with"]
        )

    content = document.content
    problems: list[str] = []
    starts: list[int | None] = []
    cursor = 0
    for i, anchor in enumerate(anchors):
        if _too_short(anchor):
            problems.append(
                f"cuts[{i}] {_quoted(anchor)} is too short: an anchor needs at least "
                f"{ANCHOR_MIN_WORDS} words or {ANCHOR_MIN_CHARS} characters"
            )
        found = content.find(anchor, cursor)
        if found < 0:
            where = "in the document" if i == 0 else f"after the end of cuts[{i - 1}]"
            hint = _normalisation_hint(content, anchor, cursor)
            problems.append(f"cuts[{i}] {_quoted(anchor)} is not found {where}{hint}")
            starts.append(None)
            continue
        starts.append(found)
        cursor = found + len(anchor)

    first = starts[0]
    if first is not None and content[:first].strip():
        problems.append(
            f"the text before cuts[0] is not covered by any passage: "
            f"{_quoted(content[:first])}; the first anchor must begin the document"
        )

    # Passages run between the anchors that were found. One that was not found
    # already has its problem, and a passage measured from a guessed position
    # would report a second problem that is not real.
    located = [(i, start) for i, start in enumerate(starts) if start is not None]
    ends = [start for _, start in located[1:]] + ([len(content)] if located else [])
    segments: list[Segment] = []
    for (i, start), end in zip(located, ends, strict=True):
        passage = _passage(document, start, end)
        if passage is None:
            problems.append(
                f"the passage cuts[{i}] {_quoted(anchors[i])} begins is empty: the next "
                f"cut follows it with nothing but whitespace between"
            )
        else:
            segments.append(passage)

    if problems:
        return AnchorsRefused(problems=problems)
    return segments


def refusal_message(refused: AnchorsRefused, *, tool: str) -> str:
    """What the agent is told, leading with what was written: nothing."""
    listed = "\n".join(f"  - {problem}" for problem in refused.problems)
    count = "a problem" if len(refused.problems) == 1 else f"{len(refused.problems)} problems"
    return (
        f"{tool} wrote nothing: the cuts have {count}.\n{listed}\n\n"
        "Each cut is an anchor, the text a passage begins with, copied exactly "
        f"from the document and at least {ANCHOR_MIN_WORDS} words or "
        f"{ANCHOR_MIN_CHARS} characters long. They are read in order, each "
        "found at its first occurrence after the end of the one before, so "
        "lengthen an anchor to reach a later occurrence. The first anchor must "
        "begin the document."
    )
