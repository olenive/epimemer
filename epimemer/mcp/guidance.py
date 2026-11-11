"""The agent guidance, read from the files that ship in ``epimemer_prompts``.

Two files, three channels. ``RULES.md`` is the server's MCP ``instructions``
string: short, sent to every client on connect, so the rules that must hold on
every call are in every context without anyone copying them. ``DEFAULT.md`` is
the full guide. A person pulls it whole as the MCP prompt ``guide``; an agent
reads it a section at a time through the tool ``guide``, since an agent has no
way to fetch a prompt and seldom needs all of it. The files are the single
source; nothing here restates them.

The server reads each file once, as it starts, and serves that text for as
long as it runs. A package upgraded underneath a running server would
otherwise have it serve guidance written for code it is not running. So the
functions below read or cut the text they are given and keep nothing.
"""

import re
from importlib.resources import files

from pydantic import BaseModel

# A section starts at a third-level heading that ends in its key, written the
# way Pandoc writes a heading id: `### When to reflect (reflect) {#reflect}`.
# The key is in the file because the file is the single source, and a key
# derived from the title would change whenever the title was reworded.
SECTION_HEADING = re.compile(r"^### (?P<title>.+?) \{#(?P<key>[a-z][a-z0-9-]*)\}[ \t]*$", re.M)
ANY_SECTION_HEADING = re.compile(r"^### .*$", re.M)

# What `section` takes to mean the whole guide.
WHOLE_GUIDE = "all"


class GuideSection(BaseModel):
    """One section of the guide: its key, its title, and its text as written."""

    key: str
    title: str
    text: str


class Guide(BaseModel):
    """The guide cut at its section headings.

    `overview` followed by every section's `text`, in order, is the file.
    """

    overview: str
    sections: list[GuideSection]


def guidance(name: str) -> str:
    """The text of one guidance file, by its name in ``epimemer_prompts``."""
    return (files("epimemer_prompts") / name).read_text(encoding="utf-8")


def rules() -> str:
    """What must hold on every call: the server ``instructions``."""
    return guidance("RULES.md")


def guide() -> str:
    """The full guide to using the tools well: the ``guide`` prompt."""
    return guidance("DEFAULT.md")


def cut_guide(text: str) -> Guide:
    """Cut the guide's text at its section headings.

    Raises `ValueError` for a third-level heading with no key, for a key used
    twice, and for the key that means the whole guide. Each would leave a
    section no agent could ask for, so the file is refused rather than served
    with a hole in it.
    """
    headings = list(SECTION_HEADING.finditer(text))
    keyed = {match.start() for match in headings}
    unkeyed = [m.group(0) for m in ANY_SECTION_HEADING.finditer(text) if m.start() not in keyed]
    if unkeyed:
        raise ValueError(f"guide headings without a {{#key}}: {unkeyed}")
    keys = [match.group("key") for match in headings]
    repeated = sorted({key for key in keys if keys.count(key) > 1})
    if repeated:
        raise ValueError(f"guide section keys used more than once: {repeated}")
    if WHOLE_GUIDE in keys:
        raise ValueError(f"'{WHOLE_GUIDE}' means the whole guide and cannot name a section")
    ends = [match.start() for match in headings[1:]] + [len(text)]
    sections = [
        GuideSection(
            key=match.group("key"),
            title=match.group("title"),
            text=text[match.start() : end],
        )
        for match, end in zip(headings, ends, strict=True)
    ]
    overview = text[: headings[0].start()] if headings else text
    return Guide(overview=overview, sections=sections)


def guide_contents(cut: Guide) -> list[dict]:
    """The list of sections an agent chooses from: key, title and size."""
    return [
        {"section": section.key, "title": section.title, "chars": len(section.text)}
        for section in cut.sections
    ]


def guide_response(section: str | None, text: str) -> dict:
    """What the `guide` tool answers for one `section` of the guide's `text`.

    No section: the overview and the list of sections. A section's key: that
    section. `all`: the whole guide. Anything else is refused with the list,
    so the next call can be right.
    """
    cut = cut_guide(text)
    if section is None:
        return {
            "overview": cut.overview.strip(),
            "sections": guide_contents(cut),
            "next": "Call guide(section=<key>) for the section you need.",
        }
    if section == WHOLE_GUIDE:
        return {"section": WHOLE_GUIDE, "text": text}
    for candidate in cut.sections:
        if candidate.key == section:
            return {"section": candidate.key, "text": candidate.text.strip()}
    return {
        "refused": f"The guide has no section '{section}'. Its sections are listed here.",
        "sections": guide_contents(cut),
    }
