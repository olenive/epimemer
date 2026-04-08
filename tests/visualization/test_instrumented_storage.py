"""Tests for InstrumentedStorage multi-graph pass-through."""

import pytest

from epimemer.storage.memory import InMemoryStorage
from epimemer.storage.surrealdb_adapter import SurrealDBStorage
from epimemer.visualization.event_bus import create_event_bus
from epimemer.visualization.instrumented_storage import instrument_storage


@pytest.fixture
def bus():
    return create_event_bus()


class TestMultiGraphPassThrough:
    """Verify InstrumentedStorage correctly passes through multi-graph properties."""

    async def test_wrapping_memory_reports_no_multi_graph(self, bus):
        inner = InMemoryStorage()
        wrapped = instrument_storage(inner, bus)
        assert wrapped.supports_multi_graph is False
        assert wrapped.current_database == "ephemeral"

    async def test_wrapping_memory_list_databases(self, bus):
        inner = InMemoryStorage()
        wrapped = instrument_storage(inner, bus)
        assert await wrapped.list_databases() == ["ephemeral"]

    async def test_wrapping_memory_switch_raises(self, bus):
        inner = InMemoryStorage()
        wrapped = instrument_storage(inner, bus)
        with pytest.raises(NotImplementedError):
            await wrapped.switch_database("other")

    async def test_wrapping_memory_delete_raises(self, bus):
        inner = InMemoryStorage()
        wrapped = instrument_storage(inner, bus)
        with pytest.raises(NotImplementedError):
            await wrapped.delete_database("ephemeral")

    def test_wrapping_surrealdb_reports_multi_graph(self, bus):
        inner = SurrealDBStorage(url="mem://")
        wrapped = instrument_storage(inner, bus)
        assert wrapped.supports_multi_graph is True
