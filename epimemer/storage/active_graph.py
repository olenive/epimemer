"""Keeping the active graph still while a logical operation is using it.

**The active graph is process state on every backend**, not a SurrealDB detail.
`MemoryStorage` resolves `self._graphs[self._database]` on every call and
`SurrealDBStorage` sends `USE ns db` down one shared connection — so in both, a
switch landing between two steps of one operation sends the rest of it
somewhere else. `ISSUES.md` #16 filed this as a connection problem and it is
not; a dedicated second connection would have fixed the smaller half of it, on
one backend.

Two things move the active graph, and they are the whole list:

- **`switch_database`**, permanently — `use_graph`, racing anything batched
  alongside it.
- **the `viz_list_*` reads**, which borrow it and give it back, so that a
  dashboard can snapshot a graph the session is not on. That is a real feature
  (the viewer's *Snapshot* badge), and the borrow is the mechanism the
  wrong-graph incident had, running inside the server where `expected_graph`
  cannot see it: the agent's expectation and `current_database` agree while the
  database on the wire has moved underneath both.

So the invariant is *the active graph does not move while a logical operation
is using it*, and it needs both sides:

    async with storage.graph_guard.using():   # a tool call
        ...
    async with storage.graph_guard.moving():  # a switch, or a snapshot borrow
        ...

Any number of users run together — they do not exclude each other, and the
common case takes an uncontended lock and an integer. A move waits for the last
one to finish and holds everyone else off meanwhile.

**Movers are preferred**, deliberately: a waiting move holds the turnstile, so
users arriving behind it queue rather than overtaking. Without that a steady
trickle of tool calls would starve a snapshot indefinitely, which on a busy
session is exactly when somebody is watching the dashboard.

**Granularity is the logical operation, not the query.** A guard taken per
query would leave the gap it exists to close — most operations issue several,
and the move only has to land between two of them. The boundaries are therefore
the MCP tool call (`mcp/server.py`) and the snapshot RPC (`hub_client.py`), and
the storage methods that move the graph take the mover's side themselves so a
direct caller is safe without knowing any of this.
"""

from __future__ import annotations

import asyncio
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from contextvars import ContextVar
from typing import AsyncIterator, Callable, NamedTuple


class GraphGuard(NamedTuple):
    """The two sides of the invariant, as async context managers.

    A pair of closures rather than an object with methods: the state they share
    is four locals, and nothing outside them may read it.
    """

    using: Callable[[], AbstractAsyncContextManager[None]]
    moving: Callable[[], AbstractAsyncContextManager[None]]


def make_graph_guard() -> GraphGuard:
    """One guard per storage backend, created with it.

    Per backend rather than per process because the backend *is* what holds the
    active graph, and two backends in one process (a test, a CLI beside a
    server) have nothing to say to each other.
    """
    users = 0
    idle = asyncio.Event()
    idle.set()
    # Held by a mover for its whole turn, and by a user only long enough to
    # register. A mover waiting on `idle` therefore blocks arriving users,
    # which is what makes moves win.
    turnstile = asyncio.Lock()

    # Re-entrancy, and the diagnosis for the one ordering that cannot work.
    # Copied into child tasks at creation, which is what `asyncio.wait_for`
    # makes of a coroutine — so a tool's work sees the depth its own boundary
    # set.
    using_depth: ContextVar[int] = ContextVar("epimemer_graph_using", default=0)
    moving_depth: ContextVar[int] = ContextVar("epimemer_graph_moving", default=0)

    @asynccontextmanager
    async def using() -> AsyncIterator[None]:
        """*I am about to work against the active graph; do not move it.*"""
        if moving_depth.get():
            # Already inside a move, which excludes everyone by definition.
            yield
            return

        nonlocal users
        async with turnstile:
            users += 1
            idle.clear()
        token = using_depth.set(using_depth.get() + 1)
        try:
            yield
        finally:
            using_depth.reset(token)
            users -= 1
            if users == 0:
                idle.set()

    @asynccontextmanager
    async def moving() -> AsyncIterator[None]:
        """*I am about to move the active graph; nobody may be using it.*"""
        if moving_depth.get():
            yield
            return

        if using_depth.get():
            raise RuntimeError(
                "cannot move the active graph while holding it — this call "
                "entered using() first, and waiting for itself to finish would "
                "hang. Take moving() at the outermost boundary instead "
                "(mcp/server.py's MOVES_THE_GRAPH is where a tool declares it)."
            )

        async with turnstile:
            await idle.wait()
            token = moving_depth.set(moving_depth.get() + 1)
            try:
                yield
            finally:
                moving_depth.reset(token)

    return GraphGuard(using=using, moving=moving)
