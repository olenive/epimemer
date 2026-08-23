"""Do a single MCP client's batched tool calls overlap inside the server?

`ISSUES.md` #16 was deferred on the premise that *"the server is single-client
stdio, so nothing issues concurrent tool calls against the shared connection"*.
Batched parallel tool calls from one client are exactly what that premise
denies, so the question is answerable rather than arguable.

    uv run python scripts/concurrency_probe.py

Prints the entry/exit trace and one word. INTERLEAVED means #16's shared-
connection hazard is reachable with one client, and the `viz_list_*`
switch-and-restore window can overlap a write.
"""

import asyncio
import os
import time

os.environ.setdefault("EPIMEMER_STORAGE_BACKEND", "memory")
os.environ.setdefault("EPIMEMER_EMBEDDING_PROVIDER", "mock")
os.environ.setdefault("EPIMEMER_VIZ_ENABLED", "false")
os.environ.setdefault("EPIMEMER_LOG_LEVEL", "ERROR")

from fastmcp import Client  # noqa: E402

from epimemer.mcp import server as srv  # noqa: E402
from epimemer.mcp import tools  # noqa: E402

HOLD_SECONDS = 0.4


def _traced(name: str, real, trace: list, started: float):
    """Wrap a tool so it holds the server for `HOLD_SECONDS` and says when."""

    async def wrapper(*args, **kwargs):
        trace.append(("enter", name, time.monotonic() - started))
        await asyncio.sleep(HOLD_SECONDS)
        result = await real(*args, **kwargs)
        trace.append(("exit", name, time.monotonic() - started))
        return result

    return wrapper


async def probe() -> bool:
    trace: list[tuple[str, str, float]] = []
    started = time.monotonic()

    tools.graph_stats = _traced("graph_stats", tools.graph_stats, trace, started)
    tools.list_graphs = _traced("list_graphs", tools.list_graphs, trace, started)

    async with Client(srv.mcp) as client:
        await asyncio.gather(
            client.call_tool("graph_stats", {}),
            client.call_tool("list_graphs", {}),
        )

    for kind, name, at in trace:
        print(f"{at * 1000:8.1f} ms  {kind:5s} {name}")

    entries = [name for kind, name, _ in trace if kind == "enter"]
    return len(entries) == 2 and trace[1][0] == "enter"


if __name__ == "__main__":
    overlapped = asyncio.run(probe())
    print("\nINTERLEAVED" if overlapped else "\nSERIALIZED")
