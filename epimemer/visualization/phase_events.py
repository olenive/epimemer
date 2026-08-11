"""Publish pipeline events for a process that is not a Petri net.

The pipeline strip renders whatever a `pipeline_started` event describes, and
`events.py` does not care where a topology came from. So an ordinary sequence of
`await`s can appear alongside the real nets by declaring a **synthetic** linear
topology — one transition per phase, one place between each pair — and firing it
by hand.

This exists so `reflect` is watchable without net-ifying it. Net-ification is a
real refactor with its own risks; this is a description of work that already
happens, not a change to how it happens.

The bus is optional throughout: with no bus the phase runner is a plain `await`,
so watching a process cannot change what it computes — the same guarantee
`_run_net` makes for the real nets.

Usage:
    async with phase_pipeline(bus, "reflect", ("consolidation", "review")) as phase:
        pairs = await phase("consolidation", lambda: find_pairs(storage), tokens=len)
        flagged = await phase("review", lambda: gather_pending(storage), tokens=len)
"""

import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from typing import Any

from epimemer.visualization.event_bus import InProcessEventBus
from epimemer.visualization.events import (
    PipelineCompleted,
    PipelineFailed,
    PipelineStarted,
    PipelineTopologyEdge,
    TokensUpdated,
    TransitionCompleted,
    TransitionFired,
)

_INPUT_PLACE = "start"


def _place_before(index: int, phases: Sequence[str]) -> str:
    return _INPUT_PLACE if index == 0 else f"after_{phases[index - 1]}"


def linear_topology(
    phases: Sequence[str],
) -> tuple[list[str], list[str], list[PipelineTopologyEdge]]:
    """A straight chain: start → phase₀ → after_phase₀ → phase₁ → …

    Returns (places, transitions, edges). One more place than transitions, and
    two edges per transition — the shape the strip draws.
    """
    places = [_INPUT_PLACE] + [f"after_{p}" for p in phases]
    edges: list[PipelineTopologyEdge] = []
    for i, phase in enumerate(phases):
        edges.append(
            PipelineTopologyEdge(source=_place_before(i, phases), target=phase, label=None)
        )
        edges.append(
            PipelineTopologyEdge(source=phase, target=f"after_{phase}", label=None)
        )
    return places, list(phases), edges


PhaseRunner = Callable[..., Awaitable[Any]]


@asynccontextmanager
async def phase_pipeline(
    bus: InProcessEventBus | None,
    pipeline_name: str,
    phases: Sequence[str],
) -> AsyncIterator[PhaseRunner]:
    """Announce a synthetic pipeline and yield a runner for its phases.

    The yielded `phase(name, work, *, tokens=None)` awaits `work()` and returns
    its result unchanged. `tokens` is applied to that result to produce the token
    count for the phase's output place — `len` for a list of candidates, `int`
    for a count — so the strip shows what each phase actually found rather than
    only that it ran.

    A phase that raises publishes `PipelineFailed` and re-raises: an unfinished
    stream would leave the strip showing a run that never ends.
    """
    if bus is None:
        async def unwatched(_name: str, work, *, tokens=None):
            return await work()

        yield unwatched
        return

    places, transitions, edges = linear_topology(phases)
    await bus.publish(PipelineStarted(
        pipeline_name=pipeline_name,
        place_names=places,
        transition_names=transitions,
        edges=edges,
    ))

    started = time.perf_counter()
    fired = 0
    # Accumulated so each update carries the whole picture, matching what the
    # net observer sends — the frontend merges per place, but a consumer reading
    # one event in isolation should still see the run so far.
    token_counts: dict[str, int] = {}

    async def phase(name: str, work, *, tokens=None):
        nonlocal fired
        index = transitions.index(name)
        await bus.publish(TransitionFired(
            pipeline_name=pipeline_name,
            transition_name=name,
            input_places=[_place_before(index, transitions)],
        ))
        phase_started = time.perf_counter()
        result = await work()
        fired += 1
        await bus.publish(TransitionCompleted(
            pipeline_name=pipeline_name,
            transition_name=name,
            output_places=[f"after_{name}"],
            duration_ms=(time.perf_counter() - phase_started) * 1000,
        ))
        token_counts[f"after_{name}"] = tokens(result) if tokens is not None else 0
        await bus.publish(TokensUpdated(
            pipeline_name=pipeline_name,
            place_token_counts=dict(token_counts),
        ))
        return result

    try:
        yield phase
    except Exception as exc:
        await bus.publish(PipelineFailed(
            pipeline_name=pipeline_name,
            error=str(exc),
            transitions_fired=fired,
            duration_ms=(time.perf_counter() - started) * 1000,
        ))
        raise

    await bus.publish(PipelineCompleted(
        pipeline_name=pipeline_name,
        transitions_fired=fired,
        duration_ms=(time.perf_counter() - started) * 1000,
    ))
