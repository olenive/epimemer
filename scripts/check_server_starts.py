"""Does this install of Epimemer start, and serve its tools, over stdio?

Launches `python -m epimemer.mcp.server` as a client does, completes the MCP
handshake, lists the tools and calls one, then says what happened. A client
that cannot start the server tells the user only that the connection failed,
so this is the check that turns that into a sentence.

Run it against whatever interpreter you want to ask about, which is the point:
the test suite always runs from a development environment, and the install
worth asking about is usually a plainer one.

    python scripts/check_server_starts.py
    python scripts/check_server_starts.py --expect-failure

`--expect-failure` inverts it: the start has to fail **and** the reason has to
reach the log file. That is the shape of the original problem, a venv without
the `sentence-transformers` extra whose default provider is that extra, and
nothing written down anywhere the user could look.

Safe by default and deliberately so: with `EPIMEMER_STORAGE_BACKEND` and
`EPIMEMER_VIZ_ENABLED` unset this uses in-memory storage and publishes no
visualization events, so a diagnostic never writes into somebody's graph. Set them and they are
passed through, along with every other `EPIMEMER_` variable.
"""

import argparse
import asyncio
import os
import re
import sys
import tempfile
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

ROOT = Path(__file__).resolve().parents[1]

# INTEGRATION.md calls itself the canonical list, and `tests/test_docs.py`
# holds that number to the registry. Reading it here rather than writing a
# count into this file keeps one copy of the claim.
DOCUMENTED_TOOL_COUNT = re.compile(r"canonical list of the (\d+) tools")


def expected_tool_count() -> int | None:
    """How many tools the checkout says there are, or None away from one."""
    integration = ROOT / "INTEGRATION.md"
    if not integration.is_file():
        return None
    stated = DOCUMENTED_TOOL_COUNT.search(integration.read_text())
    return int(stated.group(1)) if stated else None


def child_env(log_file: Path) -> dict[str, str]:
    """The environment to launch the server in: this one, with defaults filled."""
    env = dict(os.environ)
    env.setdefault("EPIMEMER_STORAGE_BACKEND", "memory")
    # The mock provider needs no model download, and this check is about
    # whether the process comes up rather than about what it embeds with.
    env.setdefault("EPIMEMER_EMBEDDING_PROVIDER", "mock")
    env.setdefault("EPIMEMER_VIZ_ENABLED", "false")
    env["EPIMEMER_LOG_FILE"] = str(log_file)
    return env


async def start_and_ask(env: dict[str, str], timeout: float) -> tuple[bool, int, str]:
    """Launch the server and ask it what it serves.

    Returns whether it answered, how many tools it named, and what went wrong
    if it did not.
    """
    transport = StdioTransport(command=sys.executable, args=["-m", "epimemer.mcp.server"], env=env)
    try:
        async with asyncio.timeout(timeout):
            async with Client(transport) as client:
                tools = await client.list_tools()
                # A call, because FastMCP builds the lifespan for the work that
                # needs it: listing tools would never reach storage or the
                # embedding provider, which is most of what starting up is.
                await client.call_tool("list_graphs", {})
                return True, len(tools), ""
    except Exception as exc:
        return False, 0, f"{type(exc).__name__}: {exc}"


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--expect-failure",
        action="store_true",
        help="require the start to fail and the log file to say why",
    )
    parser.add_argument(
        "--log-file",
        help="where the server writes; defaults to EPIMEMER_LOG_FILE, else a temporary file",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        help="seconds to wait for the server to answer (default: 60)",
    )
    args = parser.parse_args()

    named = args.log_file or os.environ.get("EPIMEMER_LOG_FILE")
    log_file = Path(named) if named else Path(tempfile.mkdtemp()) / "epimemer.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    if log_file.exists():
        log_file.unlink()

    started, tool_count, failure = await start_and_ask(child_env(log_file), args.timeout)
    written = log_file.read_text() if log_file.is_file() else ""

    if args.expect_failure:
        if started:
            print(f"the server started and served {tool_count} tools; a failure was expected")
            return 1
        if "failed to start" not in written:
            print(f"the server failed ({failure}) and {log_file} does not say why")
            print(written or "(the log file is empty)")
            return 1
        print(f"the server refused to start and {log_file} says why:")
        print(written)
        return 0

    if not started:
        print(f"the server did not answer: {failure}")
        print(written or f"(nothing in {log_file})")
        return 1

    expected = expected_tool_count()
    if expected is not None and tool_count != expected:
        print(f"the server serves {tool_count} tools; INTEGRATION.md says {expected}")
        return 1

    print(f"the server started and serves {tool_count} tools")
    print(written or f"(nothing in {log_file})")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
