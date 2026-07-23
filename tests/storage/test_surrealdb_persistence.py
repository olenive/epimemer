"""Opt-in durability test: rocksdb-backed data survives a full server restart.

This guards the exact property the in-memory backend silently lacked — the trap
that once shipped as a setup script printing "persistent graph" while running
``start ... `` with no storage path, so every restart wiped the graph.

Unlike ``test_surrealdb_integration.py`` (which connects to an *already running*
server), durability can only be proven by controlling the server lifecycle:
write, **restart the server**, then read back from a fresh connection. So this
test starts, restarts, and removes its own throwaway SurrealDB container backed
by ``rocksdb`` on a named volume.

It is SKIPPED unless explicitly opted in, so a docker-equipped dev box running a
bare ``pytest`` never spins up a container:

    EPIMEMER_SURREAL_PERSIST_TEST=1 \
        uv run pytest tests/storage/test_surrealdb_persistence.py
"""

import asyncio
import os
import shutil
import socket
import subprocess
import time
import uuid

import pytest

from epimemer.core.types import Fact
from epimemer.storage.surrealdb_adapter import SurrealDBStorage

IMAGE = os.environ.get("EPIMEMER_SURREAL_IMAGE", "surrealdb/surrealdb:latest")
ENABLED = bool(os.environ.get("EPIMEMER_SURREAL_PERSIST_TEST"))
DOCKER = shutil.which("docker")

pytestmark = pytest.mark.skipif(
    not (ENABLED and DOCKER),
    reason="Set EPIMEMER_SURREAL_PERSIST_TEST=1 (needs docker) to run the "
    "restart-durability test.",
)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _wait_ready(url: str, timeout: float = 45.0) -> None:
    """Poll until a fresh connection succeeds, or fail loudly."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        probe = SurrealDBStorage(url=url, database="ready_probe")
        try:
            await asyncio.wait_for(probe.connect(), timeout=2.0)
            await probe.close()
            return
        except Exception:
            await asyncio.sleep(0.5)
    raise RuntimeError(f"SurrealDB at {url} did not become ready within {timeout}s")


async def test_rocksdb_data_survives_server_restart():
    name = f"epimemer-persist-{uuid.uuid4().hex[:8]}"
    volume = f"{name}-data"
    port = _free_port()
    url = f"ws://127.0.0.1:{port}/rpc"

    # -u 0:0 so the server (non-root by default) can write the named volume,
    # whose /data is root-owned on this runtime — the same workaround the setup
    # script uses. rocksdb: is the on-disk backend under test.
    docker_run = [
        "docker", "run", "-d", "--name", name, "-u", "0:0",
        "-p", f"{port}:8000", "-v", f"{volume}:/data", IMAGE,
        "start", "--user", "root", "--pass", "root", "rocksdb:/data/epimemer.db",
    ]

    try:
        subprocess.run(docker_run, check=True, capture_output=True)
        await _wait_ready(url)

        # Write through the real adapter, then drop the connection.
        writer = SurrealDBStorage(url=url, database="persist")
        await writer.connect()
        node = Fact(content="survives restart", source_id="probe")
        await writer.store_node(node)
        await writer.close()

        # The crux: a full server restart (kills the process, reopens the store).
        subprocess.run(["docker", "restart", name], check=True, capture_output=True)
        await _wait_ready(url)

        # A brand-new connection must still find the node.
        reader = SurrealDBStorage(url=url, database="persist")
        await reader.connect()
        got = await reader.get_node(node.id)
        await reader.close()

        assert got is not None, "node was lost across restart — storage is not durable"
        assert isinstance(got, Fact)
        assert got.content == "survives restart"
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        subprocess.run(["docker", "volume", "rm", volume], capture_output=True)
