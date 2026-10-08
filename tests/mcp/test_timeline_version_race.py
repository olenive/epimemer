"""Two tool calls on one timeline at once, in one process.

A timeline is one record rewritten whole: every tool that changes it reads it,
changes it and writes it back, and the write lands only if the store still holds
the version that was read (`Timeline.version`). Two calls in flight together can
both read one version. The first write lands; the second must not overwrite it.
A tool that records a decision refuses and tells the agent to read the timeline
again, because what it decided may rest on what changed. Ingest decides nothing
about the points already there, so it proposes again onto the newer record and
both documents' points are kept.

**The interleaving is forced, not hoped for.** The in-memory store never
suspends, so two gathered calls would run one after the other and each would
read the other's write: nothing would race and both tests would pass for the
wrong reason. The store's `get_timeline` is wrapped to yield to the event loop
once after it reads, so each call reads and then lets the other run before it
writes. Both calls therefore hold the same version when they come to write. A
control test runs the same two calls without the wrapper and sees both land.

The calls go through the MCP boundary (`mcp/server.py`), as a client's would:
the graph gate, the guard turn every call takes over the active graph, and the
error a refused call becomes. In-memory storage and the mock embedding provider,
built the way `tests/mcp/test_judge_tokens.py` builds them.
"""

import asyncio
import json
from types import SimpleNamespace

import pytest

from epimemer.core.types import (
    BASE_METACONTEXT_ID,
    EdgeType,
    Metacontext,
    NodeType,
    Timeline,
)
from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.mcp.config import ServerConfig
from epimemer.mcp.retrieval_records import new_record_log
from epimemer.mcp.server import (
    memory_add_timepoint,
    memory_claim_agent,
    memory_segment,
    memory_store_decomposition,
    new_connection_state,
)
from epimemer.storage.memory import InMemoryStorage

GRAPH = "default"
JUDGE = "archivist"


@pytest.fixture
async def storage() -> InMemoryStorage:
    store = InMemoryStorage()
    await store.store_metacontext(
        Metacontext(id=BASE_METACONTEXT_ID, content="The Real", description="The real world.")
    )
    await store.set_approved_agent_ids([JUDGE])
    await store.store_timeline(Timeline(id="tl-parish", name="the parish"))
    return store


@pytest.fixture
def ctx(storage):
    """A stand-in context: the lifespan the boundary reads, and no channel to the user.

    The judge is approved out of band, so a claim binds without asking anybody.
    """
    deps = {
        "storage": storage,
        "embedding_provider": MockEmbeddingProvider(model_id="mock-embed", dimension=8),
        "config": ServerConfig(
            storage_backend="memory", embedding_provider="mock", segmentation_strategy="paragraph"
        ),
        "event_bus": None,
        "retrievals": new_record_log(),
        "version": "9.9.9",
        "connection_state": new_connection_state(),
    }

    async def elicit(message, response_type=None):
        raise RuntimeError("this client cannot put a question to the user")

    return SimpleNamespace(lifespan_context=deps, elicit=elicit)


def yield_after_reading(monkeypatch, storage) -> list[str]:
    """Make `get_timeline` give the event loop a turn after each read.

    Returns the ids read, in order, so a test can see that both calls read
    before either wrote, and that ingest read a second time after a refusal.
    """
    read = storage.get_timeline
    reads: list[str] = []

    async def read_then_yield(timeline_id: str):
        timeline = await read(timeline_id)
        reads.append(timeline_id)
        await asyncio.sleep(0)
        return timeline

    monkeypatch.setattr(storage, "get_timeline", read_then_yield)
    return reads


async def _add_timepoint(ctx, start: str) -> str:
    return await memory_add_timepoint(
        timeline_id="tl-parish", ctx=ctx, start=start, expected_graph=GRAPH
    )


class TestTwoAddTimepointCallsAtOnce:
    async def test_one_lands_and_the_other_is_told_to_read_again(self, storage, ctx, monkeypatch):
        reads = yield_after_reading(monkeypatch, storage)

        responses = await asyncio.gather(
            _add_timepoint(ctx, "1897-05-01"),
            _add_timepoint(ctx, "1901-03-01"),
            return_exceptions=True,
        )

        # A refusal reaches the agent as a response, not as an exception.
        assert all(isinstance(response, str) for response in responses), responses
        answers = [json.loads(response) for response in responses]
        landed = [answer["result"] for answer in answers if "result" in answer]
        refused = [answer["error"] for answer in answers if "error" in answer]
        assert len(landed) == 1 and len(refused) == 1, answers
        assert "changed after this call read it" in refused[0]
        assert "Read it again with `query_timeline`" in refused[0]
        # Both read the record before either wrote: the race happened.
        assert reads == ["tl-parish", "tl-parish"]

        stored = await storage.get_timeline("tl-parish")
        assert stored.version == 2
        assert [point.id for point in stored.timepoints] == [landed[0]["timepoint_id"]]

    async def test_without_the_forced_turn_both_land_one_after_the_other(self, storage, ctx):
        """The control: what the wrapper changes. Unforced, the in-memory store
        never suspends, the calls run in turn, and the second reads the first."""
        responses = await asyncio.gather(
            _add_timepoint(ctx, "1897-05-01"),
            _add_timepoint(ctx, "1901-03-01"),
        )

        assert all("result" in json.loads(response) for response in responses)
        stored = await storage.get_timeline("tl-parish")
        assert (stored.version, len(stored.timepoints)) == (3, 2)


async def _segmented(ctx, content: str, token: str) -> tuple[str, str]:
    """One document segmented, returning its id and its one segment's id."""
    response = json.loads(
        await memory_segment(content=content, ctx=ctx, expected_graph=GRAPH, judge_token=token)
    )
    [segment] = response["result"]["segments"]
    return response["result"]["document_id"], segment["segment_id"]


async def _store(ctx, segmented: tuple[str, str], fact: str, token: str) -> str:
    document_id, segment_id = segmented
    return await memory_store_decomposition(
        document_id=document_id,
        segments=[{"segment_id": segment_id, "facts": [fact]}],
        metacontext_id=BASE_METACONTEXT_ID,
        ctx=ctx,
        timeline_id="tl-parish",
        expected_graph=GRAPH,
        judge_token=token,
    )


class TestTwoIngestsAtOnce:
    async def test_both_land_and_the_timeline_holds_both_points(self, storage, ctx, monkeypatch):
        claimed = json.loads(
            await memory_claim_agent(
                agent_id=JUDGE, description="an archivist", ctx=ctx, expected_graph=GRAPH
            )
        )
        token = claimed["result"]["judge_token"]
        siege = await _segmented(ctx, "The siege began in 1897.", token)
        treaty = await _segmented(ctx, "The treaty was signed in 1905.", token)
        reads = yield_after_reading(monkeypatch, storage)

        responses = await asyncio.gather(
            _store(ctx, siege, "The siege began in 1897.", token),
            _store(ctx, treaty, "The treaty was signed in 1905.", token),
            return_exceptions=True,
        )

        assert all(isinstance(response, str) for response in responses), responses
        answers = [json.loads(response) for response in responses]
        assert all("result" in answer for answer in answers), answers
        assert [answer["result"]["timepoints_proposed"] for answer in answers] == [1, 1]
        # Both read version 1; the one refused at the write read again and
        # proposed onto version 2.
        assert reads == ["tl-parish", "tl-parish", "tl-parish"]

        stored = await storage.get_timeline("tl-parish")
        assert stored.version == 3
        assert sorted(point.start.year for point in stored.timepoints) == [1897, 1905]
        year_of = {point.id: point.start.year for point in stored.timepoints}
        links = {
            fact.content: [
                year_of[edge.metadata["timepoint_id"]]
                for edge in await storage.get_edges_from(fact.id, edge_type=EdgeType.TIMELINK)
            ]
            for fact in await storage.query_nodes(node_type=NodeType.FACT)
        }
        assert links == {
            "The siege began in 1897.": [1897],
            "The treaty was signed in 1905.": [1905],
        }
