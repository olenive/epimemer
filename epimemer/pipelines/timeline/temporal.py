"""Detect temporal expressions in text, and resolve them only when they resolve.

Extraction proposes timepoints so that content-time mode is not empty on any
graph nobody has hand-curated. The whole design turns on one
asymmetry:

    A missed expression costs a mark on the timeline. An invented one is
    indistinguishable from evidence once it is stored.

So this is deliberately conservative. Everything it resolves, the text states:
"12 March 1997" is a day, "the 1990s" is a decade, "the 19th century" is a
hundred years — wide, but not guessed. Everything else that *reads* as temporal
comes back as a label with no dates, and the panel's undated lane shows it as
what it is. "During the Renaissance" must never become 1500-01-01.

Resolved expressions carry a **half-open interval**: `start` is the first
instant covered, `end` the first instant after. A day, a month, a year and a
century differ only in width, so one shape describes them all, and adjacent
periods meet exactly rather than overlapping by a second.

Deliberately out of scope, because they need a document-level anchor this
function does not have: relative expressions ("three years later", "the
following spring"), clock times, and anything requiring the reader's present.
`Timeline.reference_time` is where such an anchor would come from, and resolving
against it is a separate piece of work.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel


class TemporalExpression(BaseModel):
    """One temporal expression found in a piece of text.

    Either `start` is set (the expression resolved) or `label` is (it did not).
    Never both, and never neither — a match that resolves needs no prose to
    explain it, and one that does not has nothing but its prose.
    """

    text: str
    start: datetime | None = None
    end: datetime | None = None
    label: str | None = None


# Years outside this range are far more often quantities, ids or error codes
# than dates. The bound is a judgment about text, not about history: a document
# that really is about 400 BC will not be served by this detector anyway.
MIN_YEAR = 1000
MAX_YEAR = 2999

# A bare four-digit number is a year only when something frames it as one.
# `of` is excluded on purpose: "the winter of 1897" would be worth having, but
# "a group of 1500 people" is the same shape, and the cost is not symmetric.
#
# The leading lookbehind is load-bearing: without it "versi*on* 2024" reads as
# "on 2024" and ships a date.
_PREPOSITION_WORDS = r"in|on|by|since|from|until|till|during|around|circa|c\.|after|before"
_PREPOSITION = rf"(?<!\w)(?:{_PREPOSITION_WORDS})"

_MONTHS = {
    name: number
    for number, name in enumerate(
        (
            "january february march april may june july august september october november december"
        ).split(),
        start=1,
    )
}
_MONTH_NAMES = "|".join(_MONTHS)

# A number that is part of a longer number, a decimal, or hyphenated into an
# identifier is not a year. This guard sits on both ends of every year match.
# The trailing guard rejects a separator only when a digit follows it, so
# "1897.5" is refused while "signed in 1919." — a year ending a sentence — is
# not. Rejecting every trailing period silently lost the most ordinary case
# there is.
_NOT_A_NUMBER_BEFORE = r"(?<![\d.\-/])"
_NOT_A_NUMBER_AFTER = r"(?!\d)(?![.\-/]\d)"


def _year_group(name: str) -> str:
    """A four-digit year captured as `name`, guarded against longer numbers."""
    return rf"{_NOT_A_NUMBER_BEFORE}(?P<{name}>[12]\d{{3}}){_NOT_A_NUMBER_AFTER}"


def _utc(year: int, month: int = 1, day: int = 1) -> datetime:
    return datetime(year, month, day, tzinfo=UTC)


def _next_day(moment: datetime) -> datetime:
    """The first instant of the following day.

    Exact in UTC: no offset changes, so a day is always 24 hours long.
    """
    return moment + timedelta(days=1)


def _next_month(moment: datetime) -> datetime:
    return _utc(moment.year + 1, 1) if moment.month == 12 else _utc(moment.year, moment.month + 1)


def _in_range(year: int) -> bool:
    return MIN_YEAR <= year <= MAX_YEAR


class _Match(BaseModel):
    """An accepted match and the span it occupies, for overlap rejection."""

    span: tuple[int, int]
    expression: TemporalExpression


def _iso_day(m: re.Match) -> TemporalExpression | None:
    year, month, day = int(m["y"]), int(m["m"]), int(m["d"])
    if not _in_range(year):
        return None
    try:
        start = _utc(year, month, day)
    except ValueError:
        return None  # 2024-02-31 is a typo, not a date.
    return TemporalExpression(text=m.group(0), start=start, end=_next_day(start))


def _iso_month(m: re.Match) -> TemporalExpression | None:
    year, month = int(m["y"]), int(m["m"])
    if not _in_range(year) or not 1 <= month <= 12:
        return None
    start = _utc(year, month)
    return TemporalExpression(text=m.group(0), start=start, end=_next_month(start))


def _named_day(m: re.Match) -> TemporalExpression | None:
    year, month, day = int(m["y"]), _MONTHS[m["month"].lower()], int(m["d"])
    if not _in_range(year):
        return None
    try:
        start = _utc(year, month, day)
    except ValueError:
        return None
    return TemporalExpression(text=m.group("expr"), start=start, end=_next_day(start))


def _named_month(m: re.Match) -> TemporalExpression | None:
    year = int(m["y"])
    if not _in_range(year):
        return None
    start = _utc(year, _MONTHS[m["month"].lower()])
    return TemporalExpression(text=m.group("expr"), start=start, end=_next_month(start))


def _year_range(m: re.Match) -> TemporalExpression | None:
    first, last = int(m["first"]), int(m["last"])
    # A backwards range is a typo or two unrelated years; swapping the ends
    # would be a guess, so the range is simply refused and the endpoints are
    # left to match on their own terms.
    if not (_in_range(first) and _in_range(last)) or last < first:
        return None
    return TemporalExpression(text=m.group("expr"), start=_utc(first), end=_utc(last + 1))


def _decade(m: re.Match) -> TemporalExpression | None:
    decade = int(m["decade"])
    if not _in_range(decade):
        return None
    return TemporalExpression(text=m.group("expr"), start=_utc(decade), end=_utc(decade + 10))


def _century(m: re.Match) -> TemporalExpression | None:
    ordinal = int(m["century"])
    if not 1 <= ordinal <= 30:
        return None
    # The 19th century runs 1801–1900: the nth century ends on the year that
    # divides by 100, which is why the arithmetic looks off by one.
    start = _utc((ordinal - 1) * 100 + 1)
    return TemporalExpression(text=m.group("expr"), start=start, end=_utc(ordinal * 100 + 1))


def _framed_year(m: re.Match) -> TemporalExpression | None:
    year = int(m["year"])
    if not _in_range(year):
        return None
    # The preposition frames the number but is not part of the date, so only
    # the year is reported. Vague matches keep their framing, because without
    # it "the Renaissance" reads as a noun rather than a time.
    return TemporalExpression(text=m["year"], start=_utc(year), end=_utc(year + 1))


def _vague(m: re.Match) -> TemporalExpression:
    return TemporalExpression(text=m.group(0), label=m.group(0))


# Order is priority: the first pattern to claim a span keeps it, so the longest
# and most specific forms come first. "12 March 1997" must not also yield
# "March 1997" and "1997".
_PATTERNS: tuple[tuple[re.Pattern, object], ...] = (
    (
        re.compile(
            rf"(?P<expr>(?P<d>\d{{1,2}})(?:st|nd|rd|th)?\s+(?P<month>{_MONTH_NAMES})"
            rf"\s+{_NOT_A_NUMBER_BEFORE}(?P<y>\d{{4}}){_NOT_A_NUMBER_AFTER})",
            re.IGNORECASE,
        ),
        _named_day,
    ),
    (
        re.compile(
            rf"(?P<expr>(?P<month>{_MONTH_NAMES})\s+(?P<d>\d{{1,2}})(?:st|nd|rd|th)?,?\s+"
            rf"{_NOT_A_NUMBER_BEFORE}(?P<y>\d{{4}}){_NOT_A_NUMBER_AFTER})",
            re.IGNORECASE,
        ),
        _named_day,
    ),
    (
        re.compile(
            rf"{_NOT_A_NUMBER_BEFORE}(?P<y>\d{{4}})-(?P<m>\d{{2}})-(?P<d>\d{{2}})"
            rf"{_NOT_A_NUMBER_AFTER}"
        ),
        _iso_day,
    ),
    (
        re.compile(rf"{_NOT_A_NUMBER_BEFORE}(?P<y>\d{{4}})-(?P<m>\d{{2}}){_NOT_A_NUMBER_AFTER}"),
        _iso_month,
    ),
    (
        re.compile(
            rf"(?P<expr>(?P<month>{_MONTH_NAMES})\s+"
            rf"{_NOT_A_NUMBER_BEFORE}(?P<y>\d{{4}}){_NOT_A_NUMBER_AFTER})",
            re.IGNORECASE,
        ),
        _named_month,
    ),
    (
        re.compile(
            rf"(?P<expr>(?:from|between)\s+{_year_group('first')}"
            rf"\s+(?:to|and)\s+{_year_group('last')})",
            re.IGNORECASE,
        ),
        _year_range,
    ),
    (
        re.compile(
            rf"(?P<expr>{_NOT_A_NUMBER_BEFORE}(?P<first>[12]\d{{3}})\s*[–—]\s*"
            rf"(?P<last>[12]\d{{3}}){_NOT_A_NUMBER_AFTER})"
        ),
        _year_range,
    ),
    (
        re.compile(
            rf"(?P<expr>{_NOT_A_NUMBER_BEFORE}(?P<decade>[12]\d{{2}}0)s){_NOT_A_NUMBER_AFTER}"
        ),
        _decade,
    ),
    (
        re.compile(r"(?P<expr>(?P<century>\d{1,2})(?:st|nd|rd|th)\s+century)", re.IGNORECASE),
        _century,
    ),
    (
        re.compile(rf"{_PREPOSITION}\s+{_year_group('year')}", re.IGNORECASE),
        _framed_year,
    ),
    # Vague markers. Kept short and explicit: an open-ended attempt to
    # recognise "temporal-sounding" prose is how a detector starts inventing.
    # A capitalised noun phrase after a temporal preposition ("during the
    # Renaissance", "in the Middle Ages") is included because the capitals are
    # doing the work — "in the room" does not match.
    (
        re.compile(
            rf"(?<!\w)(?:{_PREPOSITION_WORDS}|throughout)\s+the\s+"
            rf"(?:[A-Z]\w+)(?:\s+(?:[A-Z]\w+|of|the))*"
        ),
        _vague,
    ),
    (
        re.compile(
            r"\b(?:recently|nowadays|long ago|(?:centuries|decades|years) ago|"
            r"in (?:the past|the future|antiquity|ancient times)|"
            r"(?:later|earlier) (?:that|the following) (?:year|month|week|day)|"
            r"(?:later|earlier) that (?:year|month|week|day)|"
            r"the following (?:year|month|week|day))\b",
            re.IGNORECASE,
        ),
        _vague,
    ),
)


def detect_temporal_expressions(text: str) -> list[TemporalExpression]:
    """Find temporal expressions in `text`, in the order they appear.

    Resolved expressions carry `start`/`end`; unresolved ones carry `label`
    only. Overlapping matches are resolved in favour of the more specific
    pattern, and expressions that resolve to the same interval — or to the same
    label — are reported once, since two mentions of 1897 are one point in time.
    """
    accepted: list[_Match] = []
    claimed: list[tuple[int, int]] = []

    for pattern, build in _PATTERNS:
        for m in pattern.finditer(text):
            span = m.span("expr") if "expr" in m.groupdict() else m.span()
            if any(span[0] < end and start < span[1] for start, end in claimed):
                continue
            expression = build(m)
            if expression is None:
                continue
            claimed.append(span)
            accepted.append(_Match(span=span, expression=expression))

    accepted.sort(key=lambda found: found.span)

    seen: set[tuple] = set()
    unique: list[TemporalExpression] = []
    for found in accepted:
        key = (found.expression.start, found.expression.end, found.expression.label)
        if key in seen:
            continue
        seen.add(key)
        unique.append(found.expression)
    return unique
