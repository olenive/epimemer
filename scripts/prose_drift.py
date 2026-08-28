#!/usr/bin/env python
"""Find prose that restates something the code enumerates.

**Not a test, deliberately, and the reason is the defect it catches.** Every
drift this has found had one shape: a document carrying a live count of a
code-enumerated list — the size of `CAPPED_KEYS`, the number of reflect phases,
the kinds `apply_reflection` accepts. Pinning those numbers in the suite would
detect the drift and *institutionalise the duplication that causes it*: every
legitimate change to a list would fail a doc test whose fix is bumping a number,
which trains the update-without-rereading habit that lets the argument around
the number go stale while the number stays fresh.

So the real fix is to stop writing the counts, and this is the tool for finding
the ones already written. Run it before asking for a review, while the prose and
the code are both in your head — a lint at that moment is worth more than a
suite member firing months later at whoever happens to touch the list.

A **dated measurement is not a live count** and is never reported: *0.0105% of
fact pairs clear the bar*, *5,053 pairs, zero nominations*, *seven when this was
written*. Those are evidence, and evidence does not drift — it ages, which is
what the date is for.

    uv run python scripts/prose_drift.py

Exits non-zero when anything is found, so it can be chained, but nothing runs it
automatically.
"""

import re
import sys
from pathlib import Path

from epimemer.core.advisories import ADVISORY_STANCE, AdvisoryAction, AdvisoryKind
from epimemer.core.types import DecisionKind
from epimemer.mcp import tools
from epimemer.pipelines.reflection.batch_validation import ID_VALUED, REQUIRED_KEYS
from epimemer.pipelines.review.modes import REVIEW_MODES

NUMBER_WORDS = {
    "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}

# What the code enumerates, the phrase a sentence would count it with, and a
# word that has to appear nearby to confirm the subject.
#
# **The phrases are narrow on purpose, and two looser versions of this proved
# why.** Matching a number against a plausible noun — "two keys", "three
# layers" — reported thirty findings with two real ones. Adding bare digits and
# member names made it worse: section numbers, dates and confidence values all
# matched. What every genuine drift actually looked like was a *number word*
# followed closely by a phrase that names the collection and nothing else. A
# tool nobody runs twice is worth less than no tool, so this errs at missing
# things rather than at reporting them.
SUBJECTS: dict[str, tuple[int, str, str]] = {
    "REFLECT_PHASES": (
        len(tools.REFLECT_PHASES),
        r"phases|nominee lists|worklists|keys\b",
        r"reflect",
    ),
    "CAPPED_KEYS": (
        len(tools.CAPPED_KEYS),
        r"pair[- ]built lists|pair lists|capped lists|quadratic lists"
        r"|(?:of them |of the \w+ )?are built out of pairs",
        r"cap|truncated|nomination",
    ),
    "apply_reflection arguments": (
        len(REQUIRED_KEYS) + len(ID_VALUED),
        r"kinds of decision|steps share|steps are applied|decision kinds",
        r"apply_reflection|reflect",
    ),
    "AdvisoryKind": (
        len(AdvisoryKind), r"advisory kinds|kinds of advisory", r"advisor"
    ),
    "AdvisoryAction": (len(AdvisoryAction), r"advisory actions", r"advisor"),
    "AdvisoryStance": (
        len({stance for stance in ADVISORY_STANCE.values()}),
        r"advisory stances", r"advisor",
    ),
    "DecisionKind": (
        len(DecisionKind), r"journal kinds|kinds of journal row", r"journal|review"
    ),
    "REVIEW_MODES": (len(REVIEW_MODES), r"review modes|modes of review", r"review"),
}

# How far around the count to look for the confirming word. One paragraph,
# roughly — the subject is usually named in the sentence before.
WINDOW = 240

# `dev-docs/` is deliberately out of scope. Those documents are dated records of
# what was decided when, so a number in one is evidence of the state at that
# date rather than a claim about the state now — the same reason a measurement
# is never reported here.
DOCS = ("*.md", "docs/*.md", "epimemer_prompts/*.md")
SOURCE = ("epimemer/core/*.py", "epimemer/mcp/*.py", "epimemer/pipelines/**/*.py",
          "epimemer/storage/*.py")


def _files(root: Path):
    for pattern in DOCS + SOURCE:
        yield from sorted(root.glob(pattern))


def _findings(root: Path) -> list[str]:
    found: list[str] = []
    counts = "|".join(NUMBER_WORDS)
    for path in _files(root):
        text = path.read_text(encoding="utf-8")
        for name, (size, phrase, confirm) in SUBJECTS.items():
            pattern = re.compile(
                rf"\b({counts})\b(?:[^.\n]{{0,40}}?)\b(?:{phrase})",
                flags=re.IGNORECASE,
            )
            for match in pattern.finditer(text):
                window = text[
                    max(0, match.start() - WINDOW) : match.end() + WINDOW
                ]
                if not re.search(confirm, window, flags=re.IGNORECASE):
                    continue
                stated = NUMBER_WORDS[match.group(1).lower()]
                line = text[:match.start()].count("\n") + 1
                verdict = "STALE" if stated != size else "live count"
                clause = re.sub(r"\s+", " ", match.group(0)).strip()
                found.append(
                    f"{path.relative_to(root)}:{line}: {verdict} — {clause!r} "
                    f"counts {name} (currently {size}). "
                    f"Name the code instead of counting it."
                )
    return found


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    found = _findings(root)
    for line in found:
        print(line)
    if not found:
        print("No prose found restating a code-enumerated list.")
        return 0
    print(f"\n{len(found)} to look at. A `live count` is right today and will "
          f"not stay right; a `STALE` one is already wrong.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
