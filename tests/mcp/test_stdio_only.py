"""Every entry point serves stdio, which is what lets the judge state be per process.

The judge binding, the tokens and the confirmed-judge memo live in
`connection_state` on the lifespan (`new_connection_state` in `server.py`).
On stdio one process serves one client, so the process's state is the
connection's. A transport that serves several clients from one process would
hand every client the same judge and let one client's token write as another's
judge, so it needs a per-client store first.

Read from the source rather than mocked, so a transport added in either file
fails here before it ships. The transport is named at each call rather than
left to FastMCP's default, which comes from the `FASTMCP_TRANSPORT` environment
variable and so could move a deployment off stdio without a code change.
"""

import ast
from pathlib import Path

import pytest

from epimemer import cli
from epimemer.mcp import server

NEEDS_A_PER_CLIENT_STORE = (
    "a transport that serves several clients from one process needs a per-client "
    "judge store before it is added: plug one in where `connection_state` is "
    "created in the lifespan in epimemer/mcp/server.py"
)


def _mcp_runs(path: Path) -> list[ast.Call]:
    """Every `mcp.run(...)` call in the file."""
    return [
        node
        for node in ast.walk(ast.parse(path.read_text()))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "run"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "mcp"
    ]


@pytest.mark.parametrize("module", [server, cli], ids=["server", "cli"])
def test_every_entry_point_names_stdio(module):
    path = Path(module.__file__)
    runs = _mcp_runs(path)

    assert runs, f"no `mcp.run(` call found in {path.name}; this guard has lost its subject"
    for call in runs:
        where = f"{path.name} line {call.lineno}"
        assert not call.args, (
            f"{where} passes positional arguments to mcp.run(): " + NEEDS_A_PER_CLIENT_STORE
        )
        named = {keyword.arg: keyword.value for keyword in call.keywords}
        assert set(named) == {"transport"}, (
            f"{where} passes {sorted(named)} to mcp.run(), expected only transport: "
            + NEEDS_A_PER_CLIENT_STORE
        )
        transport = named["transport"]
        assert isinstance(transport, ast.Constant) and transport.value == "stdio", (
            f"{where} runs on a transport other than a literal stdio: " + NEEDS_A_PER_CLIENT_STORE
        )
