"""How long a deferred archival nominee waits, per graph and per process.

The reflect threshold's pattern: a process default on `ServerConfig`, read from
`EPIMEMER_DEFERRAL_FIRST_WAIT` and `EPIMEMER_DEFERRAL_CAP`; an override per
graph, stored beside the counter; one pure `resolve_deferral_settings`. The
tool sets named fields, clears the ones named in `clear`, and leaves the rest
alone, and it refuses settings that would not resolve to a sensible wait.

The last class drives the whole flow through the MCP boundary, the way an
agent meets it: reflect nominates, apply_reflection defers, and the node is
back after the rounds it was told it would wait.
"""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from fastmcp import FastMCP
from pydantic import ValidationError

from epimemer.core.types import BASE_METACONTEXT_ID, DecisionKind, Metacontext
from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.mcp import tools
from epimemer.mcp.config import ServerConfig, deferral_defaults, load_config
from epimemer.mcp.retrieval_records import new_record_log
from epimemer.mcp.server import mcp as epimemer_mcp
from epimemer.mcp.server import new_connection_state
from epimemer.pipelines.reflection.deferral import deferral_wait
from epimemer.storage.memory import InMemoryStorage
from epimemer.storage.protocol import (
    MAX_DEFERRAL_CAP,
    DeferralOverrides,
    DeferralSettings,
    resolve_deferral_settings,
)

GRAPH = "deferral-scratch"


class TestTheProcessDefault:
    def test_the_built_in_defaults(self, monkeypatch):
        monkeypatch.delenv("EPIMEMER_DEFERRAL_FIRST_WAIT", raising=False)
        monkeypatch.delenv("EPIMEMER_DEFERRAL_CAP", raising=False)

        config = load_config()

        assert (config.deferral_first_wait, config.deferral_cap) == (2, 32)
        assert deferral_defaults(config) == DeferralSettings(first_wait=2, cap=32)

    def test_the_environment_sets_them(self, monkeypatch):
        monkeypatch.setenv("EPIMEMER_DEFERRAL_FIRST_WAIT", "3")
        monkeypatch.setenv("EPIMEMER_DEFERRAL_CAP", "24")

        assert deferral_defaults(load_config()) == DeferralSettings(first_wait=3, cap=24)

    def test_a_first_wait_of_zero_is_refused_at_load(self, monkeypatch):
        monkeypatch.setenv("EPIMEMER_DEFERRAL_FIRST_WAIT", "0")

        with pytest.raises(ValidationError, match="EPIMEMER_DEFERRAL_FIRST_WAIT"):
            load_config()

    def test_a_cap_past_the_ceiling_is_refused_at_load(self, monkeypatch):
        """Eventual review is what a deferral promises, so the process default
        cannot be set to a wait that amounts to never."""
        monkeypatch.setenv("EPIMEMER_DEFERRAL_FIRST_WAIT", "2")
        monkeypatch.setenv("EPIMEMER_DEFERRAL_CAP", "1025")

        with pytest.raises(ValidationError, match="at most 1024"):
            load_config()

    def test_a_cap_below_the_first_wait_is_refused_at_load(self, monkeypatch):
        monkeypatch.setenv("EPIMEMER_DEFERRAL_FIRST_WAIT", "8")
        monkeypatch.setenv("EPIMEMER_DEFERRAL_CAP", "4")

        with pytest.raises(ValidationError, match="EPIMEMER_DEFERRAL_CAP"):
            load_config()


async def _configure(storage, **kwargs) -> dict:
    result, _ = await tools.configure_deferral(storage, **kwargs)
    return result


class TestTheTool:
    """The tool half, on every backend."""

    async def test_with_nothing_set_it_reports_the_defaults(self, storage):
        result = await _configure(storage, deferral_defaults=DeferralSettings(first_wait=3, cap=9))

        assert result["deferral_first_wait"] == 3
        assert result["deferral_cap"] == 9
        assert result["overridden"] == {}
        assert (result["default_first_wait"], result["default_cap"]) == (3, 9)
        assert result["reflect_round"] == 0

    async def test_a_named_field_is_set_and_the_other_left_alone(self, storage):
        await _configure(storage, cap=12)

        result = await _configure(storage, first_wait=3)

        assert (result["deferral_first_wait"], result["deferral_cap"]) == (3, 12)
        assert result["overridden"] == {"first_wait": 3, "cap": 12}

    async def test_clear_returns_a_named_field_to_the_default(self, storage):
        await _configure(storage, first_wait=3, cap=12)

        result = await _configure(storage, clear=["first_wait"])

        assert result["overridden"] == {"cap": 12}
        assert result["deferral_first_wait"] == 2
        assert await storage.get_deferral_overrides() == DeferralOverrides(cap=12)

    async def test_clear_can_name_both(self, storage):
        await _configure(storage, first_wait=3, cap=12)

        result = await _configure(storage, clear=["first_wait", "cap"])

        assert result["overridden"] == {}

    async def test_a_cleared_field_follows_the_default_at_the_time(self, storage):
        await _configure(storage, first_wait=3)
        await _configure(storage, clear=["first_wait"])

        later = DeferralSettings(first_wait=5, cap=40)
        result = await _configure(storage, deferral_defaults=later)

        assert result["deferral_first_wait"] == 5

    async def test_clear_and_set_together(self, storage):
        await _configure(storage, first_wait=3)

        result = await _configure(storage, cap=8, clear=["first_wait"])

        assert result["overridden"] == {"cap": 8}

    async def test_an_unknown_name_in_clear_is_refused(self, storage):
        with pytest.raises(ValueError, match="'undo_depth'"):
            await _configure(storage, clear=["undo_depth"])

    async def test_a_field_both_set_and_cleared_is_refused(self, storage):
        with pytest.raises(ValueError, match="both set and cleared"):
            await _configure(storage, first_wait=3, clear=["first_wait"])

    @pytest.mark.parametrize("field", ["first_wait", "cap"])
    async def test_zero_is_refused_and_nothing_changes(self, storage, field):
        await _configure(storage, first_wait=3, cap=12)

        with pytest.raises(ValueError, match="at least 1"):
            await _configure(storage, **{field: 0})

        assert await storage.get_deferral_overrides() == DeferralOverrides(first_wait=3, cap=12)

    async def test_a_cap_below_the_default_first_wait_is_refused(self, storage):
        """Checked as it would resolve: the cap is the only field named, and the
        first wait it falls short of is the process default."""
        with pytest.raises(ValueError, match=r"cap \(1\) must be at least first_wait \(2\)"):
            await _configure(storage, cap=1)

        assert await storage.get_deferral_overrides() == DeferralOverrides()

    async def test_clearing_a_cap_that_would_fall_below_the_override_is_refused(self, storage):
        await _configure(storage, first_wait=40, cap=64)

        with pytest.raises(ValueError, match="must be at least first_wait"):
            await _configure(storage, clear=["cap"])

    async def test_a_read_shows_an_override_that_disagrees_with_the_default(self, storage):
        """A cap stored under one process default, read under a later one whose
        first wait is longer: the read reports it rather than refusing."""
        await storage.set_deferral_overrides(DeferralOverrides(cap=4))
        later = DeferralSettings(first_wait=8, cap=16)

        result = await _configure(storage, deferral_defaults=later)

        assert (result["deferral_first_wait"], result["deferral_cap"]) == (8, 4)
        assert result["overridden"] == {"cap": 4}

    async def test_a_clear_that_changes_nothing_is_not_refused(self, storage):
        await storage.set_deferral_overrides(DeferralOverrides(cap=4))
        later = DeferralSettings(first_wait=8, cap=16)

        result = await _configure(storage, clear=["first_wait"], deferral_defaults=later)

        assert result["overridden"] == {"cap": 4}

    async def test_a_change_under_the_disagreement_is_still_checked(self, storage):
        await storage.set_deferral_overrides(DeferralOverrides(cap=4))
        later = DeferralSettings(first_wait=8, cap=16)

        with pytest.raises(ValueError, match="must be at least first_wait"):
            await _configure(storage, first_wait=6, deferral_defaults=later)

    @pytest.mark.parametrize("field", ["first_wait", "cap"])
    async def test_a_value_past_the_ceiling_is_refused(self, storage, field):
        with pytest.raises(ValueError, match="at most 1024"):
            await _configure(storage, **{field: 1025})

        assert await storage.get_deferral_overrides() == DeferralOverrides()

    async def test_the_ceiling_itself_is_allowed(self, storage):
        result = await _configure(storage, cap=MAX_DEFERRAL_CAP)

        assert result["deferral_cap"] == 1024

    async def test_it_reports_the_round(self, storage):
        await storage.set_reflect_round(7)

        assert (await _configure(storage))["reflect_round"] == 7


class TestAStoredValueAboveTheCeiling:
    """A cap stored before the ceiling existed, or carried in by a bundle, is
    read as stored and behaves as the ceiling: the graph keeps reflecting,
    exporting and importing, and the value can still be cleared."""

    async def _stored(self, storage) -> None:
        await storage.set_deferral_overrides(DeferralOverrides(cap=2000))

    async def test_it_reads_back_as_stored_and_resolves_to_the_ceiling(self, storage):
        await self._stored(storage)

        stored = await storage.get_deferral_overrides()

        assert stored == DeferralOverrides(cap=2000)
        assert resolve_deferral_settings(stored, DeferralSettings()).cap == MAX_DEFERRAL_CAP
        assert deferral_wait(30, resolve_deferral_settings(stored, DeferralSettings())) == 1024

    async def test_reflect_still_runs(self, storage):
        await self._stored(storage)

        result, _ = await tools.reflect(
            storage, MockEmbeddingProvider(model_id="mock-embed", dimension=8)
        )

        assert "archival_candidates" in result

    async def test_the_read_reports_the_clamp_and_writes_nothing(self, storage):
        await self._stored(storage)

        result = await _configure(storage)

        assert result["deferral_cap"] == MAX_DEFERRAL_CAP
        assert result["clamped"] == {"cap": 2000}
        assert result["ceiling"] == MAX_DEFERRAL_CAP
        assert await storage.get_deferral_overrides() == DeferralOverrides(cap=2000)

    async def test_it_can_be_cleared(self, storage):
        await self._stored(storage)

        result = await _configure(storage, clear=["cap"])

        assert result["overridden"] == {}
        assert await storage.get_deferral_overrides() == DeferralOverrides()

    async def test_another_change_stores_it_at_the_ceiling(self, storage):
        await self._stored(storage)

        result = await _configure(storage, first_wait=3)

        assert result["clamped"] == {"cap": 2000}
        assert await storage.get_deferral_overrides() == DeferralOverrides(
            first_wait=3, cap=MAX_DEFERRAL_CAP
        )
        assert "clamped" not in await _configure(storage)

    async def test_it_exports_and_imports_as_stored(self, storage):
        from epimemer.pipelines.transfer import export_graph, import_graph

        await self._stored(storage)
        bundle = await export_graph(
            storage, embedding_provider="mock", embedding_model_id="mock-embed"
        )
        assert bundle.settings.deferral_overrides == DeferralOverrides(cap=2000)

        target = InMemoryStorage()
        await import_graph(
            bundle,
            target,
            MockEmbeddingProvider(model_id="mock-embed", dimension=8),
            graph="clamp-restored",
        )
        await target.switch_database("clamp-restored")

        assert await target.get_deferral_overrides() == DeferralOverrides(cap=2000)
        assert (await _configure(target))["deferral_cap"] == MAX_DEFERRAL_CAP


# --- Through the boundary ----------------------------------------------------


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
        "connection_state": new_connection_state(),
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


async def _call(server: FastMCP, tool: str, **arguments) -> dict:
    response = await server.call_tool(tool, {"expected_graph": GRAPH, **arguments})
    payload = json.loads(response.content[0].text)
    assert "result" in payload, payload
    return payload["result"]


async def _ingest(server: FastMCP, content: str) -> None:
    seg = await _call(server, "segment", content=content)
    await _call(
        server,
        "store_decomposition",
        metacontext_id=BASE_METACONTEXT_ID,
        document_id=seg["document_id"],
        segments=[{"segment_id": s["segment_id"], "topics": [content]} for s in seg["segments"]],
    )


async def _never_retrieved_ids(server: FastMCP) -> dict[str, dict]:
    reflected = await _call(server, "reflect")
    return {
        c["node_id"]: c
        for c in reflected["archival_candidates"]
        if c["reason"] == "never_retrieved"
    }


def _graph() -> InMemoryStorage:
    store = InMemoryStorage(database=GRAPH)
    store._graphs[GRAPH].metacontexts[BASE_METACONTEXT_ID] = Metacontext(
        id=BASE_METACONTEXT_ID, content="The Real", description="Claims about the real world."
    )
    return store


class TestTheFlowAnAgentMeets:
    @pytest.fixture
    def config(self) -> ServerConfig:
        return ServerConfig(storage_backend="memory", embedding_provider="mock")

    async def test_a_deferred_node_waits_its_rounds_and_comes_back(self, config):
        storage = _graph()

        async with _session(storage, config) as server:
            await _ingest(server, "Harbour tolls")
            nominated = await _never_retrieved_ids(server)
            node_id = next(iter(nominated))
            assert "deferred" not in nominated[node_id]

            applied = await _call(
                server,
                "apply_reflection",
                deferred=[{"node_id": node_id, "because": "Wait for the ledger."}],
            )
            assert applied["deferrals_recorded"] == [
                {"node_id": node_id, "count": 1, "due_after_rounds": 2}
            ]
            [row] = await storage.query_decisions(
                subject_ids=[node_id], kinds=[DecisionKind.DEFERRAL]
            )
            assert row.at_round == 1

            # Nothing stored since: the round stands still and the node waits.
            assert node_id not in await _never_retrieved_ids(server)
            assert await storage.get_reflect_round() == 1

            await _ingest(server, "Ledger entries")
            assert node_id not in await _never_retrieved_ids(server)
            await _ingest(server, "Customs house")
            assert node_id not in await _never_retrieved_ids(server)
            await _ingest(server, "Quay repairs")
            back = await _never_retrieved_ids(server)

        assert back[node_id]["deferred"] == {
            "count": 1,
            "last_round": 1,
            "rounds_waited": 2,
            "because": "Wait for the ledger.",
            "judged_by": None,
            "next_wait": 4,
        }

    async def test_the_tool_sets_and_clears_through_the_boundary(self, config):
        storage = _graph()
        config = config.model_copy(update={"deferral_first_wait": 4, "deferral_cap": 16})

        async with _session(storage, config) as server:
            set_ = await _call(server, "configure_deferral", first_wait=1)
            cleared = await _call(server, "configure_deferral", clear=["first_wait"])
            refused = json.loads(
                (await server.call_tool("configure_deferral", {"expected_graph": GRAPH, "cap": 2}))
                .content[0]
                .text
            )

        assert (set_["deferral_first_wait"], set_["deferral_cap"]) == (1, 16)
        assert (cleared["deferral_first_wait"], cleared["overridden"]) == (4, {})
        assert "must be at least first_wait (4)" in refused["error"]

    async def test_the_process_default_reaches_apply_reflection(self, config):
        storage = _graph()
        config = config.model_copy(update={"deferral_first_wait": 5, "deferral_cap": 5})

        async with _session(storage, config) as server:
            await _ingest(server, "Harbour tolls")
            node_id = next(iter(await _never_retrieved_ids(server)))
            applied = await _call(
                server, "apply_reflection", deferred=[{"node_id": node_id, "because": "Later."}]
            )

        assert applied["deferrals_recorded"][0]["due_after_rounds"] == 5
