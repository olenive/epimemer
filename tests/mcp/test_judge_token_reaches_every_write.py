"""Every write an agent can make can carry the token that says who made it.

A guard whose reach is an accident of where the code sat is one that fails
open. A tool that resolves a judge but takes no `judge_token` is unreachable by
the agent holding one: its writes fall back to the session binding, which on a
connection shared by two agents is whichever of them claimed last, and the
attribution is wrong in exactly the way the token exists to prevent. Nothing
else would notice, because a missing parameter looks the same as a parameter
nobody passed.

So the population is derived from the source rather than listed: every function
registered as an MCP tool whose body calls `_judge_for_write`. Both halves are
checked, because either alone fails open. Declaring the parameter and not
forwarding it is worse than not declaring it: the tool advertises the feature,
the agent passes its token, and the write is still credited to somebody else.

The other direction too: a tool that takes a `judge_token` and never resolves a
judge is asking for a credential it has no use for, which is either a copied
signature or a write that slipped past the gate.
"""

import ast
import inspect
from pathlib import Path

import pytest

from epimemer.mcp import server


def _tools() -> list[ast.AsyncFunctionDef]:
    """Every function registered as an MCP tool, read from the registration.

    From the decorator rather than from a name convention: nine tools are
    registered as `epimemer_*` and the rest as `memory_*`, and a guard that
    walked one prefix would silently check half the surface.
    """
    tree = ast.parse(Path(server.__file__).read_text())
    return [
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef)
        and any(
            isinstance(decorator, ast.Call) and getattr(decorator.func, "attr", "") == "tool"
            for decorator in node.decorator_list
        )
    ]


def _resolves_a_judge(tool: ast.AsyncFunctionDef) -> list[ast.Call]:
    """The calls this tool makes to the one gate every write goes through."""
    return [
        node
        for node in ast.walk(tool)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_judge_for_write"
    ]


def _parameters(tool: ast.AsyncFunctionDef) -> list[str]:
    return [arg.arg for arg in tool.args.args] + [arg.arg for arg in tool.args.kwonlyargs]


def _forwards_the_token(call: ast.Call) -> bool:
    """Whether this call passes the tool's own `judge_token` through.

    The name is checked as well as the keyword: `judge_token=None` would satisfy
    a keyword-only test while discarding what the agent sent.
    """
    return any(
        keyword.arg == "judge_token"
        and isinstance(keyword.value, ast.Name)
        and keyword.value.id == "judge_token"
        for keyword in call.keywords
    )


WRITE_TOOLS = [tool for tool in _tools() if _resolves_a_judge(tool)]


def test_the_derivation_finds_the_write_tools():
    """The control. A derivation matching nothing makes every assertion over it
    vacuously true, which is the failure mode of deriving rather than listing."""
    assert len(WRITE_TOOLS) > 20
    names = {tool.name for tool in WRITE_TOOLS}
    assert {"memory_store_decomposition", "memory_update", "epimemer_apply_review"} <= names


@pytest.mark.parametrize("tool", WRITE_TOOLS, ids=lambda tool: tool.name)
def test_every_write_tool_takes_a_judge_token(tool):
    assert "judge_token" in _parameters(tool), (
        f"{tool.name} resolves a judge but takes no judge_token, so an agent "
        f"sharing this connection with another cannot say which claim its write "
        f"belongs to. Its writes are credited to whichever agent claimed last."
    )


@pytest.mark.parametrize("tool", WRITE_TOOLS, ids=lambda tool: tool.name)
def test_every_write_tool_forwards_it(tool):
    unforwarded = [call for call in _resolves_a_judge(tool) if not _forwards_the_token(call)]
    assert unforwarded == [], (
        f"{tool.name} takes a judge_token and calls _judge_for_write without it, "
        f"which is worse than not taking one: the agent is told the parameter "
        f"exists, passes its token, and the write is still credited to the last "
        f"claim on the connection."
    )


def test_no_tool_asks_for_a_token_it_does_not_use():
    """A `judge_token` on a tool that resolves no judge is a credential with
    nowhere to go, which means either a copied signature or a write that reaches
    storage without passing the gate."""
    idle = [
        tool.name
        for tool in _tools()
        if "judge_token" in _parameters(tool) and not _resolves_a_judge(tool)
    ]
    assert idle == []


def test_the_gate_takes_the_token_by_keyword():
    """Keyword-only, so the second positional argument stays `expected_graph`.

    Every call site passes the graph positionally, and a token that could be
    passed positionally would let one of them land in the other's place: a graph
    name read as a token refuses every write, and a token read as a graph name
    refuses it as the wrong graph.
    """
    parameters = inspect.signature(server._judge_for_write).parameters
    assert parameters["judge_token"].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameters["judge_token"].default is None
