"""Shared fixtures.

The `storage` fixture is parameterized over every backend, so any test that
takes it runs twice — once in-memory, once against embedded SurrealDB.

This is deliberate and load-bearing. Storage bugs have survived precisely
because behavioural tests only ever exercised the in-memory backend: the
in-memory store upserts via dict assignment while SurrealDB used `INSERT INTO`,
whose UNIQUE-index violation is *silently ignored*, so "store or update" wrote
nothing on the persistent backend and every in-memory test still passed.
Running the same behaviour against both backends is what makes that class of
divergence visible.

Tests that are specific to one backend's internals belong in
`tests/storage/test_memory_storage.py` or `test_surrealdb_storage.py` and should
construct their own store rather than take this fixture.
"""

import pytest

from epimemer.storage.memory import InMemoryStorage
from epimemer.storage.surrealdb_adapter import SurrealDBStorage


@pytest.fixture(params=["memory", "surrealdb"])
async def storage(request):
    if request.param == "memory":
        yield InMemoryStorage()
    else:
        store = SurrealDBStorage(url="mem://")
        await store.connect()
        yield store
        await store.close()
