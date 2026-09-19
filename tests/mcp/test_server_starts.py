"""The server as a client launches it: `python -m epimemer.mcp.server`.

Every other test here builds the FastMCP app in-process, which proves the
tools work and proves nothing about the process a client starts. Running the
module is its own environment: `__name__` is "__main__", the entry point is
`mcp.run()` rather than a fixture, and a venv can be missing an optional
extra. A client that hits any of that reports one thing, a connection that
failed, so these tests start the real process and read what it left behind.

In-memory storage, the mock embedding provider and visualization off: the
subject is startup, and a model download or a port to dial would only make
these slow and entangled.
"""

import asyncio
import json
import os
import sys

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from epimemer.mcp.server import mcp as epimemer_mcp

# A subprocess that never answers would otherwise hang the suite. Generous,
# because this is a cold interpreter start on a loaded CI runner, and a
# timeout that fires under load is a flake rather than a finding.
STARTUP_TIMEOUT_SECONDS = 60


def _child_env(log_file, **overrides) -> dict[str, str]:
    """An environment with none of the developer's Epimemer settings in it.

    Every `EPIMEMER_` variable is dropped before ours go in, so a shell
    pointed at a real SurrealDB and a real graph cannot make these tests open
    one. The rest of the environment is inherited: the child needs the same
    interpreter, path and virtualenv as the test that spawned it.
    """
    env = {key: value for key, value in os.environ.items() if not key.startswith("EPIMEMER_")}
    env |= {
        "EPIMEMER_STORAGE_BACKEND": "memory",
        "EPIMEMER_EMBEDDING_PROVIDER": "mock",
        "EPIMEMER_VIZ_ENABLED": "false",
        "EPIMEMER_LOG_FILE": str(log_file),
    }
    env |= overrides
    return env


def _transport(env: dict[str, str]) -> StdioTransport:
    return StdioTransport(command=sys.executable, args=["-m", "epimemer.mcp.server"], env=env)


def _entries(log_file) -> list[dict]:
    """The log file as records. Each line is one JSON object."""
    text = log_file.read_text()
    return [json.loads(line) for line in text.splitlines() if line.strip()]


async def test_the_launched_server_serves_every_registered_tool(tmp_path):
    """What the process serves, against what the module registers.

    The count alone would pass while the wrong tools were served; the names
    are the claim worth making. `tests/test_docs.py` is what ties that same
    registry to the number INTEGRATION.md calls canonical, so between the two
    a tool cannot be added without both the document and this process knowing.
    """
    log_file = tmp_path / "epimemer.log"
    async with asyncio.timeout(STARTUP_TIMEOUT_SECONDS):
        async with Client(_transport(_child_env(log_file))) as client:
            served = {tool.name for tool in await client.list_tools()}
            # A call, because FastMCP builds the lifespan for the work that
            # needs it: listing tools alone would never touch storage or the
            # embedding provider, which is most of what startup does.
            await client.call_tool("list_graphs", {})

    registered = {tool.name for tool in await epimemer_mcp.list_tools()}
    assert served == registered
    assert served


async def test_a_launched_server_logs_the_version_and_backends(tmp_path):
    """The line that says a start succeeded, from the process that started.

    In-process this passes on a logger named after the module. Launched with
    `-m` that name is "__main__", which is outside the `epimemer` logger and
    so outside the handler pointed at this file, and the line silently goes
    nowhere. Only a real launch can tell the difference.
    """
    log_file = tmp_path / "epimemer.log"
    async with asyncio.timeout(STARTUP_TIMEOUT_SECONDS):
        async with Client(_transport(_child_env(log_file))) as client:
            await client.call_tool("list_graphs", {})

    started = [entry for entry in _entries(log_file) if "started" in entry["message"]]
    assert len(started) == 1, log_file.read_text()
    assert started[0]["level"] == "INFO"
    assert "memory" in started[0]["message"]
    assert "mock" in started[0]["message"]


async def test_a_launched_server_that_cannot_start_writes_why(tmp_path):
    """A venv without the `sentence-transformers` extra, which is how this began.

    The default provider is that extra, so a plain install plus a plain
    `EPIMEMER_EMBEDDING_PROVIDER` refuses at startup. A stub earlier on the
    child's path makes the import fail whether or not the extra is installed
    here, so the test says the same thing on a developer's machine as on a CI
    runner that never installed it.
    """
    stub = tmp_path / "stub"
    stub.mkdir()
    (stub / "sentence_transformers.py").write_text(
        'raise ImportError("not installed in this environment")\n'
    )
    log_file = tmp_path / "epimemer.log"
    env = _child_env(
        log_file,
        EPIMEMER_EMBEDDING_PROVIDER="sentence-transformers",
        PYTHONPATH=str(stub),
    )

    failed = False
    try:
        async with asyncio.timeout(STARTUP_TIMEOUT_SECONDS):
            async with Client(_transport(env)) as client:
                await client.call_tool("list_graphs", {})
    except Exception:
        # Which exception a broken session raises is the client library's
        # business and changes with it. What this test is about is the file.
        failed = True

    assert failed, "the server answered a call although its provider could not be built"

    written = log_file.read_text()
    assert "failed to start" in written
    assert "epimemer[sentence-transformers]" in written

    refusal = [entry for entry in _entries(log_file) if "failed to start" in entry["message"]]
    assert refusal and refusal[0]["level"] == "ERROR"
    assert "create_embedding_provider" in refusal[0]["traceback"]
