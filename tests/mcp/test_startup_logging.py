"""What the log file says about a server that started, and one that did not.

A client that cannot reach the server shows the user one line: the connection
failed. Everything the process wrote to stderr is gone with it, so the file
named by `EPIMEMER_LOG_FILE` is the only place left to look, and it has to
hold the reason.

In-memory storage and the mock embedding provider throughout: these tests are
about the lifespan's reporting, and a real backend or a model download would
only make them slow.
"""

import json
import logging
import os
import sys

import pytest

from epimemer.mcp.server import app_lifespan, mcp


@pytest.fixture
def isolated_logging():
    """Give the `epimemer` logger back afterwards.

    `setup_logging` clears the logger's handlers and attaches its own, so a
    test that calls it through the lifespan would otherwise leave every later
    test writing into a temporary file that no longer exists.
    """
    logger = logging.getLogger("epimemer")
    handlers, level = logger.handlers[:], logger.level
    yield
    for handler in logger.handlers:
        handler.close()
    logger.handlers[:] = handlers
    logger.setLevel(level)


@pytest.fixture
def server_env(monkeypatch, tmp_path):
    """A process environment with nothing of the developer's in it.

    Every `EPIMEMER_` variable goes, so a shell configured against a real
    SurrealDB cannot make these tests open one. What comes back is the log
    file's path, for a test to read.
    """
    for name in [key for key in os.environ if key.startswith("EPIMEMER_")]:
        monkeypatch.delenv(name, raising=False)
    log_file = tmp_path / "epimemer.log"
    monkeypatch.setenv("EPIMEMER_LOG_FILE", str(log_file))
    monkeypatch.setenv("EPIMEMER_STORAGE_BACKEND", "memory")
    monkeypatch.setenv("EPIMEMER_VIZ_ENABLED", "false")
    return log_file


async def test_a_startup_failure_is_written_to_the_log_file(
    monkeypatch, isolated_logging, server_env
):
    # The default provider is an optional extra; a `None` entry in sys.modules
    # makes its import raise, which is what a venv without the extra does.
    monkeypatch.setenv("EPIMEMER_EMBEDDING_PROVIDER", "sentence-transformers")
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)
    monkeypatch.delitem(sys.modules, "epimemer.embeddings.sentence_transformers", raising=False)

    with pytest.raises(RuntimeError) as refused:
        async with app_lifespan(mcp):
            pass

    written = server_env.read_text()
    assert "failed to start" in written
    assert "epimemer[sentence-transformers]" in written
    assert str(refused.value) in written

    entries = [json.loads(line) for line in written.splitlines() if line.strip()]
    failure = [entry for entry in entries if "failed to start" in entry["message"]]
    assert len(failure) == 1
    assert failure[0]["level"] == "ERROR"
    # The traceback, which is the half that says where the refusal came from.
    assert "create_embedding_provider" in failure[0]["traceback"]


async def test_a_successful_start_logs_the_version_and_backends(
    monkeypatch, isolated_logging, server_env
):
    monkeypatch.setenv("EPIMEMER_EMBEDDING_PROVIDER", "mock")

    async with app_lifespan(mcp) as deps:
        version = deps["version"]

    written = server_env.read_text()
    entries = [json.loads(line) for line in written.splitlines() if line.strip()]
    started = [entry for entry in entries if "started" in entry["message"]]
    assert len(started) == 1
    assert started[0]["level"] == "INFO"

    message = started[0]["message"]
    assert version in message
    assert "memory" in message
    assert "mock" in message
