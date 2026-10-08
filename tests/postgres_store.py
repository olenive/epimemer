"""The Postgres backend under test: opt-in, and on a database that starts empty.

The behavioural suite runs against Postgres only when
`EPIMEMER_TEST_POSTGRES_DSN` names a database, so a bare `pytest` stays green
with no server and the skip count says what did not run. `make test-postgres`
starts a throwaway server and sets the variable.

**Point it only at a database you can throw away.** Each test starts by
dropping every graph schema the database holds and ends by dropping them
again, so that a test sees exactly the graphs it made: `list_databases`
answers are asserted whole. The cleaning goes through the backend's own
`delete_database`, which only ever drops a schema holding Epimemer's marker
table, so other schemas in the database are left alone.

The `postgres` extra is optional, so the backend is imported only once a DSN
says it is wanted: an install without the driver still collects every test.
"""

import contextlib
import os
from collections.abc import AsyncIterator
from typing import Any

import pytest

DSN_VARIABLE = "EPIMEMER_TEST_POSTGRES_DSN"
SKIP_REASON = "set EPIMEMER_TEST_POSTGRES_DSN to run the Postgres backend"

# The graph a store stands on while it drops every other one, since no backend
# deletes its active graph. No test uses this name.
SCRATCH = "pytest-scratch"


def postgres_dsn() -> str:
    """The DSN to test against, or a skip naming the variable that would set it."""
    dsn = os.environ.get(DSN_VARIABLE, "")
    if not dsn:
        pytest.skip(SKIP_REASON)
    return dsn


async def open_postgres(database: str = "default") -> Any:
    """A connected `PostgresStorage` on `database`, created if absent."""
    # The skip comes first, so a run with no DSN never imports the driver.
    dsn = postgres_dsn()
    from epimemer.storage.postgres_adapter import PostgresStorage

    store = PostgresStorage(dsn, database=database)
    await store.connect()
    return store


async def drop_other_graphs(store: Any) -> None:
    """Delete every graph `store` lists except the one it is on."""
    for name in await store.list_databases():
        if name != store.current_database:
            await store.delete_database(name)


async def drop_every_graph() -> None:
    """Leave the database holding nothing but an empty scratch graph."""
    cleaner = await open_postgres(SCRATCH)
    try:
        await drop_other_graphs(cleaner)
    finally:
        await cleaner.close()


@contextlib.asynccontextmanager
async def clean_postgres_store() -> AsyncIterator[Any]:
    """A store on a new, empty `default` graph, the only graph in the database.

    Cleaned before as well as after, so a run that was interrupted before its
    teardown cannot leave the next one a graph with data in it. The teardown
    cleans with a store of its own, because the test may have closed or moved
    the one it was given.
    """
    store = await open_postgres(SCRATCH)
    try:
        await drop_other_graphs(store)
        await store.switch_database("default")
        await store.delete_database(SCRATCH)
        yield store
    finally:
        await store.close()
        await drop_every_graph()
