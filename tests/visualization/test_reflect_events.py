"""`reflect` reports its progress to the pipeline strip.

The strip lights up for the four Petri-net pipelines, but `reflect` — the
longest-running and most interesting process in the system — is plain function
phases and never appeared. It is also the operation most worth watching: the
benchmarks put it in seconds at a thousand nodes and minutes beyond that, so a
user staring at a still strip cannot tell work from a hang.

The topology is synthetic: a linear chain named after the phases. `events.py`
does not care where a topology comes from, and the frontend renders whatever
`pipeline_started` describes.

The guarantee these pin hardest is the one `_run_net` already makes — watching
a pipeline must not change what it computes.
"""

import pytest

from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.core.types import Fact, Topic
from epimemer.mcp.tools import reflect
from epimemer.visualization.event_bus import create_event_bus
from epimemer.visualization.events import (
    PipelineCompleted,
    PipelineFailed,
    PipelineStarted,
    TokensUpdated,
    TransitionCompleted,
    TransitionFired,
)


@pytest.fixture
def embedding_provider() -> MockEmbeddingProvider:
    return MockEmbeddingProvider(model_id="mock-embed", dimension=8)


@pytest.fixture
def bus():
    return create_event_bus()


def _recorder(bus) -> list:
    received: list = []
    bus.subscribe(handler=lambda e: received.append(e))
    return received


async def _populate(storage, embedding_provider):
    """A graph with enough in it that every phase has something to look at."""
    from epimemer.core.types import EmbeddingRecord

    for i, content in enumerate(["Cats are mammals", "Dogs are mammals", "Birds fly"]):
        topic = Topic(content=content, source_id=f"s{i}")
        fact = Fact(content=f"{content} — supporting detail", source_id=f"s{i}")
        for node in (topic, fact):
            await storage.store_node(node)
            vector = (await embedding_provider.embed([node.content]))[0]
            await storage.store_embedding(
                EmbeddingRecord(
                    item_id=node.id,
                    model_id=embedding_provider.model_id,
                    vector=vector,
                )
            )


class TestEmission:

    async def test_announces_its_topology_on_entry(self, storage, embedding_provider, bus):
        received = _recorder(bus)
        await _populate(storage, embedding_provider)

        await reflect(storage, embedding_provider, event_bus=bus)

        started = [e for e in received if isinstance(e, PipelineStarted)]
        assert len(started) == 1
        assert started[0].pipeline_name == "reflect"
        # A linear chain: one more place than transitions (the input, then one
        # output per phase), and every transition wired between its neighbours.
        assert len(started[0].place_names) == len(started[0].transition_names) + 1
        assert len(started[0].edges) == 2 * len(started[0].transition_names)

    async def test_every_phase_fires_and_completes_in_order(
        self, storage, embedding_provider, bus
    ):
        received = _recorder(bus)
        await _populate(storage, embedding_provider)

        await reflect(storage, embedding_provider, event_bus=bus)

        started = next(e for e in received if isinstance(e, PipelineStarted))
        fired = [e.transition_name for e in received if isinstance(e, TransitionFired)]
        completed = [
            e.transition_name for e in received if isinstance(e, TransitionCompleted)
        ]

        assert fired == list(started.transition_names)
        assert completed == fired

    async def test_ends_with_completed(self, storage, embedding_provider, bus):
        received = _recorder(bus)
        await _populate(storage, embedding_provider)

        await reflect(storage, embedding_provider, event_bus=bus)

        assert isinstance(received[-1], PipelineCompleted)
        assert received[-1].pipeline_name == "reflect"
        assert received[-1].transitions_fired == len(
            next(e for e in received if isinstance(e, PipelineStarted)).transition_names
        )
        assert received[-1].duration_ms >= 0

    async def test_phases_carry_real_durations(self, storage, embedding_provider, bus):
        received = _recorder(bus)
        await _populate(storage, embedding_provider)

        await reflect(storage, embedding_provider, event_bus=bus)

        durations = [
            e.duration_ms for e in received if isinstance(e, TransitionCompleted)
        ]
        assert durations and all(d >= 0 for d in durations)

    async def test_token_counts_accumulate_across_phases(
        self, storage, embedding_provider, bus
    ):
        """The strip's token badges are what make a phase's *output* visible —
        without them a run is a row of lights with no findings attached."""
        received = _recorder(bus)
        await _populate(storage, embedding_provider)

        await reflect(storage, embedding_provider, event_bus=bus)

        started = next(e for e in received if isinstance(e, PipelineStarted))
        token_events = [e for e in received if isinstance(e, TokensUpdated)]

        assert len(token_events) == len(started.transition_names)
        # Each update carries the whole run so far, not just the phase that
        # produced it, so one event read in isolation still shows the picture.
        assert [len(e.place_token_counts) for e in token_events] == list(
            range(1, len(started.transition_names) + 1)
        )
        assert set(token_events[-1].place_token_counts) == {
            f"after_{name}" for name in started.transition_names
        }

    async def test_a_failing_phase_ends_the_stream_and_re_raises(
        self, storage, embedding_provider, bus, monkeypatch
    ):
        """Without a terminal event the strip shows a pipeline that never ends."""
        received = _recorder(bus)

        async def _boom(*args, **kwargs):
            raise RuntimeError("consolidation exploded")

        monkeypatch.setattr(
            "epimemer.pipelines.reflection.topic_consolidation.find_similar_topic_pairs",
            _boom,
        )

        with pytest.raises(RuntimeError, match="consolidation exploded"):
            await reflect(storage, embedding_provider, event_bus=bus)

        assert isinstance(received[-1], PipelineFailed)
        assert received[-1].pipeline_name == "reflect"
        assert "consolidation exploded" in received[-1].error
        assert not any(isinstance(e, PipelineCompleted) for e in received)


class TestWatchingChangesNothing:

    async def test_no_events_without_a_bus(self, storage, embedding_provider, bus):
        received = _recorder(bus)
        await _populate(storage, embedding_provider)

        await reflect(storage, embedding_provider)

        assert received == []

    async def test_the_result_is_identical_either_way(
        self, storage, embedding_provider, bus
    ):
        await _populate(storage, embedding_provider)

        watched, watched_meta = await reflect(
            storage, embedding_provider, event_bus=bus
        )
        unwatched, unwatched_meta = await reflect(storage, embedding_provider)

        # `reflect` reads and proposes without writing, so two consecutive
        # calls see the same graph and must agree on every key — not merely on
        # the ones the agent acts on, as when decay made each run differ.
        assert watched == unwatched
        assert unwatched_meta.nodes_returned == watched_meta.nodes_returned
