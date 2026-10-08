"""Selecting the Postgres backend, and what a server says when it cannot.

The backend needs two things a plain install does not have: a database to
point at and a compiled driver. A server asked for Postgres without either
refuses at startup and names the fix, rather than failing three modules down on
a package or a connection the user never heard of. Nothing here connects.
"""

import sys

import pytest

from epimemer.cli import unreachable_store
from epimemer.mcp.config import ServerConfig, create_storage, load_config

DSN = "postgresql://epimemer:secret@db.example.org:5432/epimemer"


def test_the_dsn_is_read_from_its_variable(monkeypatch):
    monkeypatch.setenv("EPIMEMER_STORAGE_BACKEND", "postgres")
    monkeypatch.setenv("EPIMEMER_POSTGRES_DSN", DSN)

    config = load_config()

    assert (config.storage_backend, config.postgres_dsn) == ("postgres", DSN)


def test_no_dsn_is_refused_naming_the_variable():
    with pytest.raises(RuntimeError, match="EPIMEMER_POSTGRES_DSN"):
        create_storage(ServerConfig(storage_backend="postgres"))


def test_a_missing_extra_is_named_not_stack_traced(monkeypatch):
    # A `None` entry makes the import raise ImportError, and dropping the cached
    # adapter module forces it to re-run that import.
    monkeypatch.setitem(sys.modules, "psycopg", None)
    monkeypatch.delitem(sys.modules, "epimemer.storage.postgres_adapter", raising=False)

    with pytest.raises(RuntimeError) as refused:
        create_storage(ServerConfig(storage_backend="postgres", postgres_dsn=DSN))

    message = str(refused.value)
    assert "epimemer[postgres]" in message
    assert "EPIMEMER_STORAGE_BACKEND" in message
    assert isinstance(refused.value.__cause__, ImportError)


class TestWhichGraphItOpens:
    @pytest.fixture(autouse=True)
    def _driver(self):
        pytest.importorskip("psycopg", reason="the postgres extra is not installed")

    def test_nothing_named_lands_on_the_default_graph(self):
        storage = create_storage(ServerConfig(storage_backend="postgres", postgres_dsn=DSN))

        assert (storage.backend_name, storage.current_database) == ("postgres", "default")

    def test_epimemer_graph_wins(self):
        storage = create_storage(
            ServerConfig(storage_backend="postgres", postgres_dsn=DSN, graph="team")
        )

        assert storage.current_database == "team"

    def test_the_location_names_the_server_and_not_the_password(self):
        storage = create_storage(ServerConfig(storage_backend="postgres", postgres_dsn=DSN))

        assert "db.example.org:5432" in storage.location
        assert "secret" not in storage.location


def test_the_cli_reaches_the_store_the_server_reads():
    """A served database: `epimemer agents confirm` writes where the server reads."""
    assert unreachable_store(ServerConfig(storage_backend="postgres", postgres_dsn=DSN)) is None
