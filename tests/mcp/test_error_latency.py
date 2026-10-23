"""`_meta.latency_ms` on a response that is not a result.

A failed call, a timed-out one, and a write refused before it ran all answer
with an `error` beside `_meta`, and the time they took is as much a fact about
the call as a success's is. Each is measured where the response is built.
"""

import asyncio
import json
import time
from types import SimpleNamespace

from epimemer.mcp import server, tools
from epimemer.mcp.config import ServerConfig
from epimemer.mcp.retrieval_records import new_record_log
from epimemer.mcp.server import new_connection_state
from epimemer.mcp.types import ResponseMeta
from epimemer.storage.memory import InMemoryStorage

# Comfortably below the sleep each test makes, so a slow machine cannot fail
# it and a latency of 0 always does.
_SLEEP_SECONDS = 0.05
_AT_LEAST_MS = 40


def _ctx(timeout_seconds: float = 30.0) -> SimpleNamespace:
    return SimpleNamespace(
        lifespan_context={
            "storage": InMemoryStorage(),
            "config": ServerConfig(
                storage_backend="memory",
                embedding_provider="mock",
                tool_timeout_seconds=timeout_seconds,
            ),
            "event_bus": None,
            "retrievals": new_record_log(),
            "connection_state": new_connection_state(),
        }
    )


async def _run(ctx, coro) -> dict:
    # A tool that names its own graph, so the graph gate is not what answers.
    text = await server._run_with_timeout(
        "epimemer.list_graphs", coro, ctx, "", lambda result, meta: ""
    )
    return json.loads(text)


class TestTheChokePoint:
    async def test_a_failed_call_reports_the_time_it_took(self):
        async def _fails() -> tuple[dict, ResponseMeta]:
            await asyncio.sleep(_SLEEP_SECONDS)
            raise ValueError("Node 'ghost' not found")

        payload = await _run(_ctx(), _fails)

        assert payload["error"] == "Node 'ghost' not found"
        assert payload["_meta"]["latency_ms"] >= _AT_LEAST_MS

    async def test_a_timed_out_call_reports_the_time_it_took(self):
        async def _hangs() -> tuple[dict, ResponseMeta]:
            await asyncio.sleep(10)
            return {}, ResponseMeta()

        payload = await _run(_ctx(timeout_seconds=_SLEEP_SECONDS), _hangs)

        assert "timed out" in payload["error"]
        assert payload["_meta"]["latency_ms"] >= _AT_LEAST_MS


class TestAWriteRefusedBeforeItRan:
    async def test_an_unknown_token_reports_the_time_the_check_took(self, monkeypatch):
        async def _slow_tokens(ctx) -> dict:
            await asyncio.sleep(_SLEEP_SECONDS)
            return {}

        monkeypatch.setattr(server, "_judge_tokens", _slow_tokens)

        judge, refused = await server._judge_for_write(_ctx(), "default", judge_token="not-a-token")

        assert judge is None
        payload = json.loads(refused)
        assert "claim_agent" in payload["error"]
        assert payload["_meta"]["latency_ms"] >= _AT_LEAST_MS

    async def test_a_wrong_graph_refusal_reports_the_time_the_check_took(self, monkeypatch):
        real = tools.wrong_graph

        def _slow_gate(storage, expected_graph):
            time.sleep(_SLEEP_SECONDS)
            return real(storage, expected_graph)

        monkeypatch.setattr(tools, "wrong_graph", _slow_gate)

        judge, refused = await server._judge_for_write(_ctx(), "another-graph")

        assert judge is None
        payload = json.loads(refused)
        assert payload["result"]["expected_graph"] == "another-graph"
        assert payload["_meta"]["latency_ms"] >= _AT_LEAST_MS
