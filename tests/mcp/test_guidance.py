"""The guidance reaches the agent through the server, not through a paste.

Before this, `epimemer_prompts/DEFAULT.md` reached an agent only if someone
copied it into that agent's instructions, and the server's own `instructions`
string was five sentences. Now `RULES.md` is the instructions string, in every
context on connect, and `DEFAULT.md` is the `guide` prompt. The files stay the
single source, so each guard here compares the served text with the file.
"""

from __future__ import annotations

from pathlib import Path

from epimemer.mcp.server import mcp as epimemer_mcp

PROMPTS = Path(__file__).resolve().parents[2] / "epimemer_prompts"

# The instructions string sits in every context window of every client, so it
# has a budget. RULES.md was 2.2 KB when this was set; the full guide is 53 KB,
# and the point of the split is that the two never converge.
RULES_BUDGET_BYTES = 4_000


def test_the_server_instructions_are_the_rules_file():
    assert epimemer_mcp.instructions == (PROMPTS / "RULES.md").read_text(encoding="utf-8")


def test_the_rules_stay_short_enough_to_sit_in_every_context():
    assert len(epimemer_mcp.instructions.encode()) <= RULES_BUDGET_BYTES


def test_the_rules_name_the_prompt_that_holds_the_rest():
    """A reader of the rules has to be told where the full guide is, and the
    name has to be the one the server registers."""
    assert "`guide`" in epimemer_mcp.instructions


async def test_the_guide_prompt_is_the_default_file():
    names = {prompt.name for prompt in await epimemer_mcp.list_prompts()}
    assert "guide" in names
    rendered = await epimemer_mcp.render_prompt("guide")
    assert [message.role for message in rendered.messages] == ["user"]
    assert rendered.messages[0].content.text == (PROMPTS / "DEFAULT.md").read_text(encoding="utf-8")


async def test_the_rules_and_the_guide_agree_on_which_tools_take_no_graph():
    """The one fact both files state outright. The registry is the oracle:
    a tool without `expected_graph` in its schema."""
    names = {
        tool.name
        for tool in await epimemer_mcp.list_tools()
        if "expected_graph" not in tool.parameters.get("properties", {})
    }
    assert names, "every tool takes expected_graph, so the texts are wrong to name any"
    for text in (epimemer_mcp.instructions, (PROMPTS / "DEFAULT.md").read_text()):
        for name in names:
            assert f"`{name}`" in text, f"{name} takes no expected_graph and the text omits it"
