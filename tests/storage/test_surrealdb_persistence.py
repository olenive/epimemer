"""Opt-in tests for what has to survive a full SurrealDB restart.

Two properties, one setup. **The data** must survive — the trap that once
shipped as a setup script printing "persistent graph" while running ``start ...``
with no storage path, so every restart wiped the graph. **The client** must
survive too (issue #40): the SDK neither reconnects nor admits to being
disconnected, so before the adapter learned to rebuild its connection, a
restarted server wedged every caller for the life of the process.

Unlike ``test_surrealdb_integration.py`` (which connects to an *already running*
server), durability can only be proven by controlling the server lifecycle:
write, **restart the server**, then read back from a fresh connection. So this
test starts, restarts, and removes its own throwaway SurrealDB container backed
by ``rocksdb`` on a named volume.

It is SKIPPED unless explicitly opted in, so a docker-equipped dev box running a
bare ``pytest`` never spins up a container:

    EPIMEMER_SURREAL_PERSIST_TEST=1 \
        uv run pytest tests/storage/test_surrealdb_persistence.py

``make test-integration`` runs it alongside the ws:// suite. This module picks a
free port for its own container, so it is unaffected by anything already holding
8000.
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


def _docker_run(name: str, volume: str, port: int) -> list[str]:
    """A throwaway rocksdb-backed server on its own port and named volume.

    -u 0:0 so the server (non-root by default) can write the named volume, whose
    /data is root-owned on this runtime — the same workaround the setup script
    uses. rocksdb: is the on-disk backend.
    """
    return [
        "docker", "run", "-d", "--name", name, "-u", "0:0",
        "-p", f"{port}:8000", "-v", f"{volume}:/data", IMAGE,
        "start", "--user", "root", "--pass", "root", "rocksdb:/data/epimemer.db",
    ]


async def test_rocksdb_data_survives_server_restart():
    name = f"epimemer-persist-{uuid.uuid4().hex[:8]}"
    volume = f"{name}-data"
    port = _free_port()
    url = f"ws://127.0.0.1:{port}/rpc"
    docker_run = _docker_run(name, volume, port)

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


async def test_storage_recovers_after_server_restart():
    """The *same* store keeps working across a restart — issue #40.

    The test above proves the data survives; this one proves the client does.
    They differ in one line: this one never reconnects by hand, because that is
    the whole point. On 2026-08-10 a restarted container left two long-running
    MCP servers answering every call with `sent 1011 (internal error) keepalive
    ping timeout` until they were killed, and no test could have caught it — the
    default suite never holds a connection long enough to lose one.
    """
    name = f"epimemer-reconnect-{uuid.uuid4().hex[:8]}"
    volume = f"{name}-data"
    port = _free_port()
    url = f"ws://127.0.0.1:{port}/rpc"

    try:
        subprocess.run(_docker_run(name, volume, port), check=True, capture_output=True)
        await _wait_ready(url)

        store = SurrealDBStorage(url=url, database="reconnect")
        await store.connect()
        before = Fact(content="written before the restart", source_id="probe")
        await store.store_node(before)

        subprocess.run(["docker", "restart", name], check=True, capture_output=True)
        await _wait_ready(url)

        # Same store, no reconnect by hand. The first call after the restart is
        # the one that used to fail forever.
        got = await store.get_node(before.id)
        assert got is not None, "the store never recovered from the restart"
        assert got.content == "written before the restart"

        # ...and it is a working connection, not just a lucky read.
        after = Fact(content="written after the restart", source_id="probe")
        await store.store_node(after)
        assert (await store.get_node(after.id)) is not None
        assert store.current_database == "reconnect", "reconnected to the wrong database"

        await store.close()
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        subprocess.run(["docker", "volume", "rm", volume], capture_output=True)
