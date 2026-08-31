"""Guards on the instructions, because a wrong README is a defect users hit first.

The README once documented ``surreal start --user root --pass root`` under a
heading promising persistence. That command is silently in-memory: ``[PATH]``
defaults to ``memory``, so the container starts, accepts writes, and drops the
whole graph on restart without an error. The script and the durability test had
both been corrected; only the README was left re-deriving the flags, wrongly.

The tool-inventory guards below are the same defect in a second place.
``INTEGRATION.md`` calls itself *"the canonical list of the N tools — other docs
should link here rather than restate the count"*, and it said 32 while 34 were
registered. Its own table carried all 34 rows; only the sentence had rotted. The
single source of truth was the thing that drifted.

So the registry is asked rather than any document, and each guard fails for its
own reason: the sentence, the canonical table, and the other documents that
enumerate names. ``tests/mcp/test_retrieval_declaration.py`` already does this
for a test-side enumeration — *"the oracle is only an oracle if it is over all
tools"* — and the documents deserve the same treatment.
"""

from __future__ import annotations

import re
from pathlib import Path

from epimemer.mcp.server import mcp as epimemer_mcp

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
INTEGRATION = ROOT / "INTEGRATION.md"
SUMMARY = ROOT / "SUMMARY.md"

# The sentence INTEGRATION.md designates as the one home for the count.
DOCUMENTED_TOOL_COUNT = re.compile(r"canonical list of the (\d+) tools")

# A tool row in one of INTEGRATION.md's tables: a backticked lowercase name in
# the first cell. Environment variables are the other backticked first column in
# that file and are upper-case, so they do not collide.
TOOL_TABLE_ROW = re.compile(r"^\|\s*`([a-z_]+)`\s*\|", re.MULTILINE)

# Documents that enumerate tool names. The *count* is single-sourced by design;
# the names genuinely earn their place in each of these (README groups them for
# orientation, SUMMARY for architecture, INTEGRATION for reference), so a guard
# keeps the duplication honest instead of a rule nobody can enforce.
DOCUMENTS_LISTING_TOOLS = (INTEGRATION, README, SUMMARY)

# A shell invocation of the SurrealDB server, with whatever follows the
# credentials. Line continuations are folded first, so a command split across
# several lines is matched whole.
SURREAL_START = re.compile(r"start\s+--user\s+root\s+--pass\s+root([^\n]*)")


def _folded(text: str) -> str:
    return text.replace("\\\n", " ")


def test_every_documented_surrealdb_start_names_an_on_disk_path():
    commands = SURREAL_START.findall(_folded(README.read_text()))

    assert commands, "expected the README to document starting SurrealDB"
    for tail in commands:
        assert "rocksdb:" in tail, (
            "a documented SurrealDB start has no storage path, so it runs "
            f"in-memory and loses the graph on restart: ...{tail.strip()!r}"
        )


async def _registered_tool_names() -> set[str]:
    """What the server actually serves — the only authority on this question.

    Counting the documented table instead would check a document against itself,
    and a tool absent from both the sentence and the table would pass.
    """
    return {tool.name for tool in await epimemer_mcp.list_tools()}


async def test_the_documented_tool_count_matches_the_registry():
    """The sentence, which is the half that rotted.

    Every other document is told to link here rather than restate the number, so
    this one claim is load-bearing for all of them.
    """
    stated = DOCUMENTED_TOOL_COUNT.search(INTEGRATION.read_text())

    assert stated, (
        "expected INTEGRATION.md to state the tool count it calls itself "
        "canonical for; if the wording changed, update DOCUMENTED_TOOL_COUNT "
        "rather than dropping the guarantee"
    )
    registered = len(await _registered_tool_names())
    assert int(stated.group(1)) == registered, (
        f"INTEGRATION.md says {stated.group(1)} tools; {registered} are registered in server.py"
    )


async def test_the_canonical_tool_table_lists_exactly_the_registered_tools():
    """The rows, checked in both directions.

    A missing row hides a tool from the only complete reference there is. A
    surplus row is worse: it documents something an agent cannot call, and the
    failure lands on the user as a tool that does not exist.
    """
    documented = set(TOOL_TABLE_ROW.findall(INTEGRATION.read_text()))
    registered = await _registered_tool_names()

    assert not registered - documented, (
        f"registered but absent from INTEGRATION.md's tables: {sorted(registered - documented)}"
    )
    assert not documented - registered, (
        f"listed in INTEGRATION.md but not registered: {sorted(documented - registered)}"
    )


async def test_every_registered_tool_is_named_where_the_docs_list_tools():
    """The looser half, over every document that enumerates names.

    Presence of the backticked name anywhere in the file, which proves the
    document knows the tool exists — not that it sits in the right group. That
    is deliberate: SUMMARY.md's grouping went months without
    ``judge_importance``, ``set_reference_time`` or ``viz_status``, and total
    absence is the failure worth a test. Section-accurate placement would mean
    parsing three different layouts to catch a tidier class of mistake.
    """
    registered = await _registered_tool_names()

    for path in DOCUMENTS_LISTING_TOOLS:
        text = path.read_text()
        missing = sorted(name for name in registered if f"`{name}`" not in text)
        assert not missing, (
            f"{path.name} never mentions {missing} — registered tools it has no record of"
        )
