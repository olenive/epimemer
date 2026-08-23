"""The active graph holds still while a logical operation is using it (#16).

`ISSUES.md` #16 was filed as a SurrealDB connection problem and deferred on the
premise that nothing issues concurrent tool calls. Both halves were wrong: one
client's batched calls overlap (`scripts/concurrency_probe.py`), and the active
graph is process state on *every* backend — `InMemoryStorage` resolves
`self._graphs[self._database]` per call, so a switch landing mid-operation
redirects the rest of it there too.

The tests are written as *what a caller may observe*, never as *which lock is
held*: a guard whose behaviour is right and whose internals are rewritten should
not break them.
"""

import asyncio

import pytest

from epimemer.core.types import NodeType, Topic
from epimemer.storage.active_graph import make_graph_guard
from epimemer.storage.memory import InMemoryStorage
from epimemer.storage.surrealdb_adapter import SurrealDBStorage


async def _settle(ticks: int = 8) -> None:
    """Let every ready task run, without inventing a timeout to wait out."""
    for _ in range(ticks):
        await asyncio.sleep(0)


async def _still_waiting(task: asyncio.Task) -> bool:
    await _settle()
    return not task.done()


class TestUsersDoNotExcludeEachOther:
    """The common case is uncontended, and has to stay that way — a guard that
    serialized every tool call would be a throughput regression sold as a fix."""

    async def test_two_users_hold_it_at_once(self):
        guard = make_graph_guard()
        first_inside, release, second_inside = (
            asyncio.Event(), asyncio.Event(), asyncio.Event(),
        )

        async def first():
            async with guard.using():
                first_inside.set()
                await release.wait()

        async def second():
            async with guard.using():
                second_inside.set()

        held = asyncio.create_task(first())
        await first_inside.wait()

        await asyncio.wait_for(asyncio.create_task(second()), timeout=1)

        assert second_inside.is_set(), "a second user waited on the first"
        release.set()
        await held


class TestAMoveWaitsForTheWorkInFlight:
    async def test_a_move_waits_for_a_user(self):
        guard = make_graph_guard()
        inside, release = asyncio.Event(), asyncio.Event()

        async def user():
            async with guard.using():
                inside.set()
                await release.wait()

        async def mover():
            async with guard.moving():
                return "moved"

        held = asyncio.create_task(user())
        await inside.wait()
        move = asyncio.create_task(mover())

        assert await _still_waiting(move), "the move ran while a call was in flight"

        release.set()
        await held
        assert await asyncio.wait_for(move, timeout=1) == "moved"

    async def test_a_user_arriving_behind_a_waiting_mover_queues(self):
        """Movers are preferred, and this is why: a steady trickle of tool calls
        would otherwise starve a snapshot for as long as the session is busy —
        which is exactly when somebody is watching the dashboard."""
        guard = make_graph_guard()
        inside, release, late_inside = (
            asyncio.Event(), asyncio.Event(), asyncio.Event(),
        )

        async def user(entered: asyncio.Event, hold: asyncio.Event | None = None):
            async with guard.using():
                entered.set()
                if hold is not None:
                    await hold.wait()

        held = asyncio.create_task(user(inside, release))
        await inside.wait()
        move = asyncio.create_task(_mover(guard))
        await _settle()

        late = asyncio.create_task(user(late_inside))

        assert await _still_waiting(late), "a late user overtook a waiting mover"

        release.set()
        await held
        await asyncio.wait_for(move, timeout=1)
        await asyncio.wait_for(late, timeout=1)
        assert late_inside.is_set()

    async def test_a_failed_call_still_releases(self):
        guard = make_graph_guard()

        with pytest.raises(ValueError):
            async with guard.using():
                raise ValueError("the tool failed")

        await asyncio.wait_for(_mover(guard), timeout=1)


async def _mover(guard) -> str:
    async with guard.moving():
        return "moved"


class TestNesting:
    async def test_a_move_inside_a_move_is_one_turn(self):
        """`assemble_snapshot` takes a turn for its four reads so the snapshot is
        of one instant, and each read takes one of its own so a direct caller is
        safe without knowing that."""
        guard = make_graph_guard()

        async with guard.moving():
            async with guard.moving():
                pass

        await asyncio.wait_for(_mover(guard), timeout=1)

    async def test_using_inside_a_move_is_free(self):
        """A mover already excludes everyone; making it queue behind itself
        would be a deadlock dressed as caution."""
        guard = make_graph_guard()

        async with guard.moving():
            async with guard.using():
                pass

    async def test_moving_inside_using_says_what_to_do_instead(self):
        """The one ordering that cannot work — the call would wait for itself to
        finish. It raises rather than hanging, because a hung tool call reports
        nothing and a raised one names the fix."""
        guard = make_graph_guard()

        with pytest.raises(RuntimeError) as exc:
            async with guard.using():
                async with guard.moving():
                    pass

        assert "MOVES_THE_GRAPH" in str(exc.value)


class TestTheBackendsTakeTheirOwnTurn:
    """Taken by `switch_database` itself, not by its callers — so a test, the
    CLI, or a tool nobody has written yet is safe without knowing about #16."""

    async def test_a_switch_waits_for_a_call_in_flight(self, storage):
        started_on = storage.current_database
        guard = storage.graph_guard
        inside, release = asyncio.Event(), asyncio.Event()

        async def in_flight():
            async with guard.using():
                inside.set()
                await release.wait()
                # The write lands *after* the switch was requested, which is the
                # whole failure: without the guard it goes to the new graph.
                await storage.store_node(Topic(content="written where it started"))

        call = asyncio.create_task(in_flight())
        await inside.wait()
        switch = asyncio.create_task(storage.switch_database("elsewhere"))

        assert await _still_waiting(switch), "the switch cut in front of a live call"

        release.set()
        await call
        await asyncio.wait_for(switch, timeout=5)

        assert storage.current_database == "elsewhere"
        assert await storage.get_node_by_content(
            "written where it started", node_type=NodeType.TOPIC
        ) is None, "the write followed the switch"

        await storage.switch_database(started_on)
        assert await storage.get_node_by_content(
            "written where it started", node_type=NodeType.TOPIC
        ) is not None


class TestOnlyTheBorrowerPaysForTheBorrow:
    """The two backends read another graph by different means, and only one of
    them touches the active graph to do it. The asymmetry is deliberate, and
    testing it is what stops somebody 'tidying' the backends into agreement."""

    async def test_surrealdb_defers_a_snapshot_to_the_call_in_flight(self):
        store = SurrealDBStorage(url="mem://")
        await store.connect()
        try:
            guard = store.graph_guard
            inside, release = asyncio.Event(), asyncio.Event()

            async def in_flight():
                async with guard.using():
                    inside.set()
                    await release.wait()

            call = asyncio.create_task(in_flight())
            await inside.wait()
            snapshot = asyncio.create_task(store.viz_list_nodes("elsewhere"))
            await _settle()

            # The observable here is the database selected **on the wire**, and
            # nothing public reports it: `current_database` answers where the
            # caller believes it is, and during a borrow those two disagree —
            # which is precisely why `expected_graph` cannot see this bug.
            assert store._selected == store.current_database, (
                "the connection was re-pointed while a call was using it"
            )

            release.set()
            await call
            await asyncio.wait_for(snapshot, timeout=5)
            assert store._selected == store.current_database, "the borrow was not returned"
        finally:
            await store.close()

    async def test_memory_answers_a_snapshot_without_waiting(self):
        """A dict lookup goes nowhere near the active graph, so there is nothing
        to borrow and no reason to make the dashboard queue."""
        store = InMemoryStorage()
        guard = store.graph_guard
        inside, release = asyncio.Event(), asyncio.Event()

        async def in_flight():
            async with guard.using():
                inside.set()
                await release.wait()

        call = asyncio.create_task(in_flight())
        await inside.wait()

        assert await asyncio.wait_for(store.viz_list_nodes("elsewhere"), timeout=1) == []

        release.set()
        await call
