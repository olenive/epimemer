"""Every parameter an agent can pass carries its description in the schema.

A tool description is prose the agent reads once; a parameter description is
what it reads while deciding what to put in that field. FastMCP builds the
second from the Google-style ``Args:`` block, and the block is fragile in a way
nothing announces: a docstring with no ``Args:`` at all, or a paragraph indented
back to the docstring's own margin partway down one, yields a schema where the
parameters are named, typed, and silent. The tool still registers, every test
that calls it still passes, and the only thing lost is the sentence that told
the agent what the argument means.

That is why this is a guard over the registry rather than a review habit. Four
read tools described their arguments in running prose above the signature and
never reached an ``Args:`` block, so ``query_changes`` offered five nameless
windows arguments and ``list_sources``, ``list_relations`` and ``graph_stats``
each offered a nameless ``expected_graph``, the one argument whose whole purpose
is to be passed deliberately.
"""

from __future__ import annotations

from epimemer.mcp.server import mcp as epimemer_mcp


async def test_every_tool_parameter_has_a_description():
    """The schema an agent is shown, asked of the registry rather than of the
    source: a parameter with no description is a parameter it has to guess."""
    undescribed = {
        tool.name: sorted(
            name
            for name, schema in tool.parameters.get("properties", {}).items()
            if not (schema.get("description") or "").strip()
        )
        for tool in await epimemer_mcp.list_tools()
    }
    undescribed = {tool: params for tool, params in undescribed.items() if params}
    assert undescribed == {}, (
        "these parameters reach the agent with no description, which usually "
        "means the docstring has no `Args:` block or a paragraph inside one "
        "broke the parse: "
        + "; ".join(f"{tool}: {', '.join(params)}" for tool, params in sorted(undescribed.items()))
    )
