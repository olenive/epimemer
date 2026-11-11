"""Fixtures shared by the MCP server's tests."""

import logging
import os
from collections.abc import AsyncIterator

import pytest

from epimemer.mcp.server import app_lifespan, mcp


@pytest.fixture
async def started_deps(monkeypatch, tmp_path) -> AsyncIterator[dict]:
    """What the real lifespan yields: the values a server reads once, at startup.

    In-memory storage, the mock embedding provider and visualization off, with
    every `EPIMEMER_` variable of the developer's shell removed first. The
    `epimemer` logger is given back afterwards, because `setup_logging` swaps
    its handlers for one writing into `tmp_path`.
    """
    for name in [key for key in os.environ if key.startswith("EPIMEMER_")]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("EPIMEMER_LOG_FILE", str(tmp_path / "epimemer.log"))
    monkeypatch.setenv("EPIMEMER_STORAGE_BACKEND", "memory")
    monkeypatch.setenv("EPIMEMER_EMBEDDING_PROVIDER", "mock")
    monkeypatch.setenv("EPIMEMER_VIZ_ENABLED", "false")
    logger = logging.getLogger("epimemer")
    handlers, level = logger.handlers[:], logger.level
    try:
        async with app_lifespan(mcp) as deps:
            yield deps
    finally:
        for handler in logger.handlers:
            handler.close()
        logger.handlers[:] = handlers
        logger.setLevel(level)
