"""Reflection pressure reaches the viewer as events.

The counter and its threshold are already readable via `graph_stats`, but that
is a pull: the browser would have to poll to notice that a graph is due for a
reflect. These events make it a push, so the header badge tracks ingest as it
happens.

Every write that can change either number emits — the two counter mutations and
a threshold change — because a badge that updates on stores but not on
`configure_reflection` would show the right count against a stale denominator.
"""

import pytest

from epimemer.storage.memory import InMemoryStorage
from epimemer.visualization.event_bus import create_event_bus
from epimemer.visualization.events import ReflectCounterUpdated
from epimemer.visualization.instrumented_storage import instrument_storage


@pytest.fixture
def bus():
    return create_event_bus()


def _recorder(bus) -> list[ReflectCounterUpdated]:
    received: list[ReflectCounterUpdated] = []
    bus.subscribe(ReflectCounterUpdated, handler=lambda e: received.append(e))
    return received


class TestEmission:

    async def test_bump_emits_the_new_count_and_threshold(self, bus):
        received = _recorder(bus)
        storage = instrument_storage(InMemoryStorage(), bus, default_threshold=10)

        await storage.bump_reflect_counter()

        assert len(received) == 1
        assert received[0].count == 1
        assert received[0].threshold == 10
        assert received[0].suggested is False

    async def test_reaching_the_threshold_flags_the_suggestion(self, bus):
        received = _recorder(bus)
        storage = instrument_storage(InMemoryStorage(), bus, default_threshold=2)

        await storage.bump_reflect_counter()
        await storage.bump_reflect_counter()

        assert [e.suggested for e in received] == [False, True]

    async def test_reset_emits_a_zeroed_count(self, bus):
        storage = instrument_storage(InMemoryStorage(), bus, default_threshold=2)
        await storage.bump_reflect_counter()
        await storage.bump_reflect_counter()

        received = _recorder(bus)
        await storage.reset_reflect_counter()

        assert len(received) == 1
        assert received[0].count == 0
        assert received[0].suggested is False

    async def test_setting_an_override_emits_the_new_threshold(self, bus):
        """Otherwise the badge keeps the old denominator until the next store."""
        storage = instrument_storage(InMemoryStorage(), bus, default_threshold=10)
        await storage.bump_reflect_counter()
        await storage.bump_reflect_counter()

        received = _recorder(bus)
        await storage.set_reflect_threshold_override(2)

        assert len(received) == 1
        assert received[0].count == 2
        assert received[0].threshold == 2
        # The count did not move, but lowering the threshold under it means the
        # graph is now due — the badge has to turn amber on this event alone.
        assert received[0].suggested is True

    async def test_clearing_an_override_emits_the_default(self, bus):
        storage = instrument_storage(InMemoryStorage(), bus, default_threshold=10)
        await storage.set_reflect_threshold_override(2)

        received = _recorder(bus)
        await storage.set_reflect_threshold_override(None)

        assert received[-1].threshold == 10

    async def test_an_override_wins_over_the_default_on_a_bump(self, bus):
        storage = instrument_storage(InMemoryStorage(), bus, default_threshold=10)
        await storage.set_reflect_threshold_override(3)

        received = _recorder(bus)
        await storage.bump_reflect_counter()

        assert received[0].threshold == 3

    async def test_carries_the_active_graph(self, bus):
        storage = instrument_storage(InMemoryStorage(), bus, default_threshold=10)
        await storage.switch_database("other")

        received = _recorder(bus)
        await storage.bump_reflect_counter()

        assert received[0].graph == "other"


class TestPassThrough:
    """Watching must not change what is computed or returned."""

    async def test_counter_values_are_returned_unchanged(self, bus):
        storage = instrument_storage(InMemoryStorage(), bus, default_threshold=10)

        assert await storage.bump_reflect_counter() == 1
        assert await storage.bump_reflect_counter() == 2
        assert await storage.reset_reflect_counter() == 2
        assert await storage.get_reflect_counter() == 0

    async def test_reads_emit_nothing(self, bus):
        storage = instrument_storage(InMemoryStorage(), bus, default_threshold=10)
        received = _recorder(bus)

        await storage.get_reflect_counter()
        await storage.get_reflect_threshold_override()

        assert received == []

    async def test_the_override_round_trips(self, bus):
        storage = instrument_storage(InMemoryStorage(), bus, default_threshold=10)

        await storage.set_reflect_threshold_override(4)
        assert await storage.get_reflect_threshold_override() == 4

        await storage.set_reflect_threshold_override(None)
        assert await storage.get_reflect_threshold_override() is None
