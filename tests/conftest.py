"""Shared fixtures.

The `storage` fixture is parameterized over every backend, so any test that
takes it runs once per backend: in-memory, embedded SurrealDB, SQLite in a
directory of its own under `tmp_path`, and Postgres, which is skipped unless
`EPIMEMER_TEST_POSTGRES_DSN` names a database to run against
(`tests/postgres_store.py`).

This is deliberate and load-bearing. Storage bugs have survived precisely
because behavioural tests only ever exercised the in-memory backend: the
in-memory store upserts via dict assignment while SurrealDB used `INSERT INTO`,
whose UNIQUE-index violation is *silently ignored*, so "store or update" wrote
nothing on the persistent backend and every in-memory test still passed.
Running the same behaviour against every backend is what makes that class of
divergence visible.

Tests that are specific to one backend's internals belong in
`tests/storage/test_memory_storage.py`, `test_surrealdb_storage.py`,
`test_sqlite_storage.py` or `test_postgres_storage.py` and should construct
their own store rather than take this fixture.
"""

import pytest

from epimemer.core.types import BASE_METACONTEXT_ID, Metacontext
from epimemer.storage.memory import InMemoryStorage
from epimemer.storage.sqlite_adapter import SQLiteStorage
from epimemer.storage.surrealdb_adapter import SurrealDBStorage
from tests.postgres_store import clean_postgres_store


async def _set_up(store):
    """A graph somebody has set up, which is the only kind worth testing against.

    A metacontext is required at ingest and `the-real` is an ordinary
    metacontext, a convention rather than a mechanism, so it exists because
    somebody created it, once, like any other. A fixture that omitted it would
    make every ingest test start by creating it, which tests the fixture rather
    than the behaviour. The tests that are *about* the requirement name
    metacontexts that do not exist, and are unaffected by this one existing.
    """
    await store.store_metacontext(
        Metacontext(
            id=BASE_METACONTEXT_ID,
            content="The Real",
            description="Claims about the real world.",
        )
    )
    return store


@pytest.fixture(params=["memory", "surrealdb", "sqlite", "postgres"])
async def storage(request, tmp_path):
    if request.param == "memory":
        yield await _set_up(InMemoryStorage())
    elif request.param == "surrealdb":
        store = SurrealDBStorage(url="mem://")
        await store.connect()
        yield await _set_up(store)
        await store.close()
    elif request.param == "sqlite":
        store = SQLiteStorage(tmp_path / "graphs")
        await store.connect()
        yield await _set_up(store)
        await store.close()
    else:
        async with clean_postgres_store() as store:
            yield await _set_up(store)
