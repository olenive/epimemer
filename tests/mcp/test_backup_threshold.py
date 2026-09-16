"""Backup pressure, and the tool that relieves it.

`test_configure_reflection.py` for the other counter, and the shape is
deliberately the same: process default on `ServerConfig`, a per-graph override
in storage, one pure `resolve_*`, and a count reported by the responses an agent
already reads. Two counters rather than one because they are zeroed by different
acts — reflecting clears one, a successful backup clears the other — so a graph
consolidated weekly and never written out has a small reflect count and a large
backup one.

The tool half is what is new: `backup_graph` takes **no path**. Where a graph
goes is the user's decision, made once in `EPIMEMER_BACKUP_DESTINATION`, so an
agent acting on a prompt can do the thing without also choosing the destination.
With nothing configured it refuses and names the variable.
"""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from fastmcp import FastMCP
from pydantic import ValidationError

from epimemer.core.types import BASE_METACONTEXT_ID, Metacontext
from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.mcp.config import ServerConfig
from epimemer.mcp.retrieval_records import new_record_log
from epimemer.mcp.server import mcp as epimemer_mcp
from epimemer.mcp.tools import (
    backup_graph,
    configure_backup,
    effective_backup_threshold,
    graph_stats,
)
from epimemer.pipelines.transfer import read_bundle
from epimemer.storage.memory import InMemoryStorage
from epimemer.storage.protocol import resolve_backup_threshold


def _graph_with_the_real() -> InMemoryStorage:
    store = InMemoryStorage()
    store._graphs[store._database].metacontexts[BASE_METACONTEXT_ID] = Metacontext(
        id=BASE_METACONTEXT_ID,
        content="The Real",
        description="Claims about the real world.",
    )
    return store


class TestTheCounter:
    """Both backends, per the parity rule."""

    async def test_absent_reads_as_zero(self, storage):
        assert await storage.get_backup_counter() == 0

    async def test_a_bump_counts_one(self, storage):
        assert await storage.bump_backup_counter() == 1
        assert await storage.bump_backup_counter() == 2

    async def test_a_reset_returns_what_it_cleared(self, storage):
        await storage.bump_backup_counter()
        await storage.bump_backup_counter()

        assert await storage.reset_backup_counter() == 2
        assert await storage.get_backup_counter() == 0

    async def test_it_is_a_second_counter_and_not_the_reflect_one(self, storage):
        """The whole reason there are two: reflecting must not say a graph has
        been backed up, and backing up must not discard reflection pressure."""
        await storage.bump_reflect_counter()
        await storage.bump_backup_counter()
        await storage.bump_backup_counter()

        await storage.reset_reflect_counter()

        assert await storage.get_reflect_counter() == 0
        assert await storage.get_backup_counter() == 2

        await storage.bump_reflect_counter()
        await storage.reset_backup_counter()

        assert await storage.get_reflect_counter() == 1
        assert await storage.get_backup_counter() == 0


class TestOverrideStorage:
    async def test_absent_by_default(self, storage):
        assert await storage.get_backup_threshold_override() is None

    async def test_set_then_read_back(self, storage):
        await storage.set_backup_threshold_override(3)

        assert await storage.get_backup_threshold_override() == 3

    async def test_cleared_by_none(self, storage):
        await storage.set_backup_threshold_override(3)
        await storage.set_backup_threshold_override(None)

        assert await storage.get_backup_threshold_override() is None

    async def test_it_does_not_disturb_the_counters(self, storage):
        """Three numbers share one graph-state record; they must not share a value."""
        await storage.bump_backup_counter()
        await storage.bump_reflect_counter()
        await storage.set_reflect_threshold_override(9)

        await storage.set_backup_threshold_override(7)

        assert await storage.get_backup_counter() == 1
        assert await storage.get_reflect_counter() == 1
        assert await storage.get_reflect_threshold_override() == 9

    async def test_it_belongs_to_the_graph_that_set_it(self, storage):
        here = storage.current_database
        await storage.set_backup_threshold_override(3)

        await storage.switch_database("elsewhere")
        assert await storage.get_backup_threshold_override() is None

        await storage.switch_database(here)
        assert await storage.get_backup_threshold_override() == 3


class TestResolvingTheThreshold:
    def test_no_override_takes_the_default(self):
        assert resolve_backup_threshold(None, 50) == 50

    def test_an_override_wins(self):
        assert resolve_backup_threshold(5, 50) == 5

    async def test_a_later_default_change_reaches_a_cleared_graph(self, storage):
        """Clearing must not freeze today's default into the graph."""
        await storage.set_backup_threshold_override(3)
        await storage.set_backup_threshold_override(None)

        assert await effective_backup_threshold(storage, 80) == 80


class TestConfigureBackupTool:
    async def test_it_sets_the_override_and_reports_the_effect(self, storage):
        result, _ = await configure_backup(storage, threshold=3, default_threshold=50)

        assert result["backup_threshold"] == 3
        assert result["overridden"] is True
        assert await storage.get_backup_threshold_override() == 3

    async def test_it_clears_the_override(self, storage):
        await storage.set_backup_threshold_override(3)

        result, _ = await configure_backup(storage, threshold=None, default_threshold=50)

        assert result["backup_threshold"] == 50
        assert result["overridden"] is False

    async def test_it_reports_the_current_count_alongside(self, storage):
        await storage.bump_backup_counter()
        await storage.bump_backup_counter()

        result, _ = await configure_backup(storage, threshold=2, default_threshold=50)

        assert result["stores_since_backup"] == 2
        assert result["backup_suggested"] is True

    async def test_it_rejects_a_threshold_below_one(self, storage):
        with pytest.raises(ValueError, match="at least 1"):
            await configure_backup(storage, threshold=0, default_threshold=50)

        assert await storage.get_backup_threshold_override() is None

    async def test_it_does_not_reset_the_counter(self, storage):
        """Raising the threshold is 'not yet'; it must not claim the graph is safe."""
        await storage.bump_backup_counter()

        await configure_backup(storage, threshold=90, default_threshold=50)

        assert await storage.get_backup_counter() == 1


class TestGraphStatsReportsBackupPressure:
    async def test_it_sits_beside_the_reflect_keys(self, storage):
        await storage.bump_backup_counter()

        result, _ = await graph_stats(
            storage, default_reflect_threshold=10, default_backup_threshold=50
        )

        assert result["stores_since_backup"] == 1
        assert result["backup_threshold"] == 50
        assert result["backup_suggested"] is False
        assert result["backup_threshold_overridden"] is False

    async def test_it_reports_the_override(self, storage):
        await storage.set_backup_threshold_override(1)
        await storage.bump_backup_counter()

        result, _ = await graph_stats(
            storage, default_reflect_threshold=10, default_backup_threshold=50
        )

        assert result["backup_threshold"] == 1
        assert result["backup_threshold_overridden"] is True
        assert result["backup_suggested"] is True


class TestBackupGraphTool:
    async def test_it_refuses_when_no_destination_is_configured(self, storage):
        result, _ = await backup_graph(
            storage,
            destination=None,
            backup_keep=None,
            embedding_provider="mock",
            embedding_model_id="mock-embed",
            default_backup_threshold=50,
        )

        assert result["status"] == "refused"
        assert "EPIMEMER_BACKUP_DESTINATION" in result["reason"]

    async def test_a_refusal_leaves_the_counter_alone(self, storage):
        """A counter zeroed by an attempt would say a graph was safe on the
        strength of a write that never happened."""
        await storage.bump_backup_counter()

        await backup_graph(
            storage,
            destination=None,
            backup_keep=None,
            embedding_provider="mock",
            embedding_model_id="mock-embed",
            default_backup_threshold=50,
        )

        assert await storage.get_backup_counter() == 1

    async def test_it_writes_a_bundle_named_for_the_graph_and_the_day(self, storage, tmp_path):
        result, _ = await backup_graph(
            storage,
            destination=str(tmp_path),
            backup_keep=None,
            embedding_provider="mock",
            embedding_model_id="mock-embed",
            default_backup_threshold=50,
        )

        written = Path(result["written_to"])
        assert result["status"] == "written"
        assert written.is_file()
        assert written.name.startswith(f"{storage.current_database}-")
        assert written.name.endswith(".epimemer.tar.gz")

    async def test_the_bundle_it_writes_reads_back(self, storage, tmp_path):
        result, _ = await backup_graph(
            storage,
            destination=str(tmp_path),
            backup_keep=None,
            embedding_provider="mock",
            embedding_model_id="mock-embed",
            default_backup_threshold=50,
        )

        bundle = read_bundle(result["written_to"])

        assert bundle.manifest.graph == storage.current_database
        assert bundle.manifest.embedding_model_id == "mock-embed"
        assert result["counts"] == bundle.manifest.counts

    async def test_a_successful_write_zeroes_the_counter(self, storage, tmp_path):
        await storage.bump_backup_counter()
        await storage.bump_backup_counter()

        result, _ = await backup_graph(
            storage,
            destination=str(tmp_path),
            backup_keep=None,
            embedding_provider="mock",
            embedding_model_id="mock-embed",
            default_backup_threshold=50,
        )

        assert await storage.get_backup_counter() == 0
        assert result["stores_since_backup"] == 0
        assert result["backup_suggested"] is False

    async def test_it_leaves_the_reflect_counter_alone(self, storage, tmp_path):
        """Writing a graph out is not consolidating it."""
        await storage.bump_reflect_counter()

        await backup_graph(
            storage,
            destination=str(tmp_path),
            backup_keep=None,
            embedding_provider="mock",
            embedding_model_id="mock-embed",
            default_backup_threshold=50,
        )

        assert await storage.get_reflect_counter() == 1

    async def test_a_destination_whose_driver_is_missing_names_the_extra(
        self, storage, monkeypatch
    ):
        import fsspec

        monkeypatch.setattr(
            fsspec,
            "get_filesystem_class",
            lambda protocol: (_ for _ in ()).throw(ImportError(protocol)),
        )

        result, _ = await backup_graph(
            storage,
            destination="gs://bucket/backups",
            backup_keep=None,
            embedding_provider="mock",
            embedding_model_id="mock-embed",
            default_backup_threshold=50,
        )

        assert result["status"] == "refused"
        assert "epimemer[gcs]" in result["reason"]


def _dated(destination: Path, graph: str, *dates: str) -> None:
    """Bundles from earlier days, as files a previous backup would have left."""
    for date in dates:
        (destination / f"{graph}-{date}.epimemer.tar.gz").write_bytes(b"an older bundle")


def _bundles(destination: Path, graph: str) -> list[str]:
    return sorted(p.name for p in destination.iterdir() if p.name.startswith(f"{graph}-"))


class TestBackupRetention:
    """`EPIMEMER_BACKUP_KEEP`: how many bundles a destination holds.

    Unset keeps everything, and that is the default — a retention policy nobody
    set should never be the reason a backup is gone.
    """

    def test_the_default_keeps_everything(self):
        assert ServerConfig().backup_keep is None

    def test_a_count_below_one_is_refused_at_load(self):
        """Zero would delete the bundle the backup just wrote."""
        with pytest.raises(ValidationError, match="at least 1"):
            ServerConfig(backup_keep=0)

    async def test_unset_removes_nothing(self, storage, tmp_path):
        graph = storage.current_database
        _dated(tmp_path, graph, "2026-01-01", "2026-02-01", "2026-03-01")

        result, _ = await backup_graph(
            storage,
            destination=str(tmp_path),
            backup_keep=None,
            embedding_provider="mock",
            embedding_model_id="mock-embed",
            default_backup_threshold=50,
        )

        assert len(_bundles(tmp_path, graph)) == 4
        assert "removed" not in result
        assert "kept" not in result

    async def test_it_keeps_the_newest_and_counts_todays_among_them(self, storage, tmp_path):
        graph = storage.current_database
        _dated(tmp_path, graph, "2026-01-01", "2026-02-01", "2026-03-01", "2026-04-01")

        result, _ = await backup_graph(
            storage,
            destination=str(tmp_path),
            backup_keep=2,
            embedding_provider="mock",
            embedding_model_id="mock-embed",
            default_backup_threshold=50,
        )

        today = Path(result["written_to"]).name
        assert result["kept"] == 2
        assert result["removed"] == [
            f"{graph}-2026-01-01.epimemer.tar.gz",
            f"{graph}-2026-02-01.epimemer.tar.gz",
            f"{graph}-2026-03-01.epimemer.tar.gz",
        ]
        assert _bundles(tmp_path, graph) == sorted([f"{graph}-2026-04-01.epimemer.tar.gz", today])

    async def test_a_failed_write_prunes_nothing(self, storage, tmp_path, monkeypatch):
        """The old bundles are all a graph has while the new one does not exist."""
        graph = storage.current_database
        _dated(tmp_path, graph, "2026-01-01", "2026-02-01", "2026-03-01")

        from epimemer.pipelines import transfer

        def _refuse(*args, **kwargs):
            raise OSError("the destination went away")

        monkeypatch.setattr(transfer, "write_bundle", _refuse)

        with pytest.raises(OSError):
            await backup_graph(
                storage,
                destination=str(tmp_path),
                backup_keep=1,
                embedding_provider="mock",
                embedding_model_id="mock-embed",
                default_backup_threshold=50,
            )

        assert len(_bundles(tmp_path, graph)) == 3

    async def test_it_leaves_another_graph_alone(self, storage, tmp_path):
        """One destination holds every graph this server opens."""
        graph = storage.current_database
        _dated(tmp_path, f"{graph}-archive", "2026-01-01", "2026-02-01")

        await backup_graph(
            storage,
            destination=str(tmp_path),
            backup_keep=1,
            embedding_provider="mock",
            embedding_model_id="mock-embed",
            default_backup_threshold=50,
        )

        assert len(_bundles(tmp_path, f"{graph}-archive")) == 2


# --- Through the MCP server: the counter an agent actually sees ---


@asynccontextmanager
async def _session(storage, config: ServerConfig) -> AsyncIterator[FastMCP]:
    original = epimemer_mcp._lifespan
    deps = {
        "storage": storage,
        "embedding_provider": MockEmbeddingProvider(model_id="mock-embed", dimension=8),
        "config": config,
        "event_bus": None,
        "viz_session": None,
        "viz_hub_url": None,
        "retrievals": new_record_log(),
    }

    @asynccontextmanager
    async def _lifespan(_server):
        yield deps

    epimemer_mcp._lifespan = _lifespan
    async with _lifespan(epimemer_mcp) as ctx:
        epimemer_mcp._lifespan_result = ctx
        try:
            yield epimemer_mcp
        finally:
            epimemer_mcp._lifespan_result = None
    epimemer_mcp._lifespan = original


def _result(tool_result) -> dict:
    return json.loads(tool_result.content[0].text)["result"]


async def _ingest(server: FastMCP, content: str) -> dict:
    seg = _result(
        await server.call_tool("segment", {"expected_graph": "default", "content": content})
    )
    return _result(
        await server.call_tool(
            "store_decomposition",
            {
                "expected_graph": "default",
                "metacontext_id": "the-real",
                "document_id": seg["document_id"],
                "segments": [
                    {"segment_id": s["segment_id"], "topics": [f"Topic {s['segment_id']}"]}
                    for s in seg["segments"]
                ],
            },
        )
    )


@pytest.fixture
def config() -> ServerConfig:
    return ServerConfig(
        storage_backend="memory",
        embedding_provider="mock",
        backup_threshold=2,
    )


async def test_store_decomposition_reports_backup_pressure(config):
    """The response an agent acts on, so the keys have to reach it."""
    storage = _graph_with_the_real()

    async with _session(storage, config) as server:
        first = await _ingest(server, "Cats are mammals.")
        assert first["stores_since_backup"] == 1
        assert first["backup_threshold"] == 2
        assert first["backup_suggested"] is False

        second = await _ingest(server, "Dogs are mammals.")
        assert second["stores_since_backup"] == 2
        assert second["backup_suggested"] is True


async def test_reflecting_does_not_clear_the_backup_counter(config):
    """The distinction the second counter exists for, end to end."""
    storage = _graph_with_the_real()

    async with _session(storage, config) as server:
        await _ingest(server, "Cats are mammals.")
        await _ingest(server, "Dogs are mammals.")
        await server.call_tool("reflect", {"expected_graph": "default"})

        stats = _result(await server.call_tool("graph_stats", {"expected_graph": "default"}))

    assert stats["stores_since_reflect"] == 0
    assert stats["stores_since_backup"] == 2
    assert stats["backup_suggested"] is True


async def test_apply_reflection_reports_backup_pressure(config):
    """Reported, not moved: applying a reflection is the moment an agent is
    already thinking about the graph, and the count is of stores."""
    storage = _graph_with_the_real()

    async with _session(storage, config) as server:
        await _ingest(server, "Cats are mammals.")
        applied = _result(await server.call_tool("apply_reflection", {"expected_graph": "default"}))

    assert applied["stores_since_backup"] == 1
    assert applied["backup_threshold"] == 2
    assert applied["backup_suggested"] is False


async def test_the_backup_tool_needs_no_judge(config, tmp_path):
    """A backup asserts nothing about the graph, so requiring a judge would put
    a gate in front of the one act that protects the data. No id is approved in
    this session, which would refuse any write tool."""
    storage = _graph_with_the_real()
    config = config.model_copy(update={"backup_destination": str(tmp_path)})

    async with _session(storage, config) as server:
        await _ingest(server, "Cats are mammals.")
        written = _result(await server.call_tool("backup_graph", {"expected_graph": "default"}))

    assert written["status"] == "written"
    assert Path(written["written_to"]).is_file()


async def test_the_backup_tool_clears_the_prompt(config, tmp_path):
    storage = _graph_with_the_real()
    config = config.model_copy(update={"backup_destination": str(tmp_path)})

    async with _session(storage, config) as server:
        await _ingest(server, "Cats are mammals.")
        await _ingest(server, "Dogs are mammals.")
        await server.call_tool("backup_graph", {"expected_graph": "default"})
        stats = _result(await server.call_tool("graph_stats", {"expected_graph": "default"}))

    assert stats["stores_since_backup"] == 0
    assert stats["backup_suggested"] is False


async def test_the_backup_tool_refuses_with_no_destination(config):
    storage = _graph_with_the_real()

    async with _session(storage, config) as server:
        refused = _result(await server.call_tool("backup_graph", {"expected_graph": "default"}))

    assert refused["status"] == "refused"
    assert "EPIMEMER_BACKUP_DESTINATION" in refused["reason"]


async def test_configure_backup_persists_the_override(config):
    """The override outlives the process that set it — the point of storing it."""
    storage = _graph_with_the_real()

    async with _session(storage, config) as server:
        await server.call_tool("configure_backup", {"expected_graph": "default", "threshold": 5})

    async with _session(storage, config) as server:
        stats = _result(await server.call_tool("graph_stats", {"expected_graph": "default"}))

    assert stats["backup_threshold"] == 5
    assert stats["backup_threshold_overridden"] is True
