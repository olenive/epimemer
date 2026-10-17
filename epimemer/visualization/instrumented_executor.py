"""Turns Petri net execution into the event stream the visualization consumes.

Petritype's `Runner` drives a net to quiescence and hands observers the live
graph after every state change. With `record_events` on, the engine also keeps
its own log of what happened: one `Fired` record per completed firing, naming
the tokens it consumed and produced and the wall time of its body.
`pipeline_observer` reads which transitions are in flight and drains that log
at each notification, and translates both into `PipelineEvent`s; it is the
only thing in this module that knows the wire format.

What follows from reading the engine's own records:

- **Lossless in both modes.** A concurrent notification can cover several
  completions, and each left its own record, so each is reported once.
- **Real durations.** `duration_ms` is the body's wall time as the engine
  measured it around the call.
- **Places from the net and its tokens.** `output_places` name the places a
  firing deposited tokens into. `input_places` are the places the net's
  argument edges declare for the transition at launch, or the places its
  consumed tokens came from when a firing is only heard of at completion.
- **A real working state.** `TransitionFired` is published when the runner
  reports the transition in flight, in both modes, so a tile can show the
  transition working while its body runs. In sequential mode,
  through Petritype 0.3.1's launch notification, subscribers have it before
  the body starts. In concurrent mode the runner starts the body's task before
  notifying, and publishing yields to the loop, so the body runs up to its
  first await before subscribers have it. In either mode a body that holds the
  event loop delays delivery of anything the hub client has queued, which is
  why `SentenceTransformersProvider.embed` encodes in a worker thread.
- **No iteration cap.** The net stops when nothing is fireable. A cap that is
  too low truncates the pipeline and returns a half-filled result with no
  error; one that is high enough to be safe never fires.
- **Observing does not change the run.** The observer switches recording on and
  reads; tokens flow the same way with or without it. The bus is a tap: see
  `tests/pipelines/test_net_execution.py`.
- **Every run ends the stream.** Success emits `PipelineCompleted`; a raising
  transition emits `PipelineFailed`, naming that transition, and then
  re-raises. The two are distinct so a consumer can tell a finished pipeline
  from a failed one.

Usage:
    bus = create_event_bus()
    graph = semantic_similarity_segmentation_net(document, embedding_provider)
    result_graph, fired = await execute_with_events(graph, bus, "segmentation")
"""

import time

from petritype.core import events as engine_events
from petritype.core.events import PlaceTokenId
from petritype.core.executable_graph_components import (
    ExecutableGraph,
    TransitionFailedError,
)
from petritype.runtime import ExecutionMode, Observer, RunContext, Runner

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


def _place_token_counts(graph: ExecutableGraph) -> dict[str, int]:
    """Extract current token counts from all places in the graph."""
    return {p.name: len(p.tokens) for p in graph.places}


def _place_names(graph: ExecutableGraph) -> list[str]:
    return [p.name for p in graph.places]


def _transition_names(graph: ExecutableGraph) -> list[str]:
    return [t.name for t in graph.transitions]


def _fired_total(graph: ExecutableGraph) -> int:
    return sum(graph.fired_counts.values())


def _topology_edges(graph: ExecutableGraph) -> list[PipelineTopologyEdge]:
    """Extract the Petri net topology as a list of directed edges."""
    return [
        *(
            PipelineTopologyEdge(
                source=edge.place_node_name,
                target=edge.transition_node_name,
                label=edge.argument,
            )
            for edge in graph.argument_edges
        ),
        *(
            PipelineTopologyEdge(
                source=edge.transition_node_name,
                target=edge.place_node_name,
            )
            for edge in graph.return_edges
        ),
    ]


def _places_of(tokens: tuple[PlaceTokenId, ...]) -> list[str]:
    """The places a firing's tokens came from or went to, each once, in first-seen order."""
    return list(dict.fromkeys(place for place, _token_id in tokens))


def _declared_inputs(graph: ExecutableGraph, transition_name: str) -> list[str]:
    """The places a transition's argument edges read from, each once, in declaration order."""
    return list(
        dict.fromkeys(
            edge.place_node_name
            for edge in graph.argument_edges
            if edge.transition_node_name == transition_name
        )
    )


async def _announce_launches(
    bus: InProcessEventBus, pipeline_name: str, graph: ExecutableGraph, announced: set[str]
) -> None:
    """Publish `TransitionFired` for each transition in flight not yet announced.

    The tokens a launching firing consumed are out of their places and not yet
    recorded anywhere, so its inputs are the places the net declares for it.
    Transitions are announced in the net's declaration order, and each is added
    to `announced`, so its completion publishes no second `TransitionFired`.
    """
    for transition in graph.transitions:
        if transition.name in graph.in_flight and transition.name not in announced:
            await bus.publish(
                TransitionFired(
                    pipeline_name=pipeline_name,
                    transition_name=transition.name,
                    input_places=_declared_inputs(graph, transition.name),
                )
            )
            announced.add(transition.name)


async def _publish_recorded(
    bus: InProcessEventBus, pipeline_name: str, graph: ExecutableGraph, announced: set[str]
) -> list[engine_events.Event]:
    """Drain the graph's event log and publish it, then the current token counts.

    Each `Fired` record becomes a `TransitionCompleted`, oldest first, and its
    transition leaves `announced`, as a `Failed` one's does. A firing that was
    not announced at launch, which is what a firing inside an inner net or from
    a `Fire` command gives, gets its `TransitionFired` first, with the places
    its consumed tokens came from. The other records (tokens added from outside
    a firing, mutations, toggles) have no counterpart on the wire. Returns the
    drained records so the failure path can find the `Failed` one.
    """
    records = graph.drain_events()
    for record in records:
        if isinstance(record, engine_events.Failed):
            announced.discard(record.transition)
        if isinstance(record, engine_events.Fired):
            if record.transition in announced:
                announced.discard(record.transition)
            else:
                await bus.publish(
                    TransitionFired(
                        pipeline_name=pipeline_name,
                        transition_name=record.transition,
                        input_places=_places_of(record.consumed),
                    )
                )
            await bus.publish(
                TransitionCompleted(
                    pipeline_name=pipeline_name,
                    transition_name=record.transition,
                    output_places=_places_of(record.produced),
                    duration_ms=record.duration_ms,
                )
            )
    await bus.publish(
        TokensUpdated(
            pipeline_name=pipeline_name,
            place_token_counts=_place_token_counts(graph),
        )
    )
    return records


def _failure(records: list[engine_events.Event], exc: Exception) -> tuple[str | None, str]:
    """The failed transition's name and the error to show for it.

    The engine's `Failed` record names both, and its error is the body's own
    exception. Without one, the exception is all there is.
    """
    failed = next((r for r in records if isinstance(r, engine_events.Failed)), None)
    if failed is not None:
        return failed.transition, failed.error
    if isinstance(exc, TransitionFailedError):
        return exc.transition_name, str(exc)
    return None, str(exc)


def pipeline_observer(
    bus: InProcessEventBus, pipeline_name: str, announced: set[str] | None = None
) -> Observer:
    """Build an observer that republishes the engine's event log as pipeline events.

    Each notification first announces the transitions newly in flight, then
    drains the log, so every firing since the previous one is reported exactly
    once, however many a concurrent batch completed. The observer switches
    recording on itself: the runner's first notification is the initial
    marking, before anything fires, so a run loses nothing. Draining empties the
    log, so this should be the graph's only reader of it.

    `announced` holds the transitions published as fired and not yet as
    completed. Pass a set to share it with whoever drains the log after the
    run, as the failure path in `execute_with_events` does.
    """
    announced = set() if announced is None else announced

    async def observe(graph: ExecutableGraph) -> None:
        graph.record_events = True
        await _announce_launches(bus, pipeline_name, graph, announced)
        await _publish_recorded(bus, pipeline_name, graph, announced)

    return observe


async def execute_with_events(
    graph: ExecutableGraph,
    bus: InProcessEventBus,
    pipeline_name: str,
    *,
    mode: ExecutionMode = ExecutionMode.SEQUENTIAL,
) -> tuple[ExecutableGraph, int]:
    """Run a Petri net to quiescence, publishing the pipeline's event stream.

    Args:
        graph: The executable Petri net graph.
        bus: Event bus to publish pipeline events on.
        pipeline_name: Name for this pipeline execution (e.g., "segmentation").
        mode: The runner's execution mode; the stream is complete in either.

    Returns:
        (updated_graph, transitions_fired)
    """
    started_at = time.monotonic()
    fired_before = _fired_total(graph)
    # Records left from before this run belong to no stream of ours.
    graph.drain_events()
    graph.record_events = True

    await bus.publish(
        PipelineStarted(
            pipeline_name=pipeline_name,
            place_names=_place_names(graph),
            transition_names=_transition_names(graph),
            edges=_topology_edges(graph),
        )
    )

    # Shared with the observer, so a firing it announced at launch and that
    # completed beside a failure is not announced a second time below.
    announced: set[str] = set()
    observer = pipeline_observer(bus, pipeline_name, announced)
    ctx = RunContext(graph=graph, mode=mode, observers=(observer,))
    try:
        summary = await Runner.run(ctx)
    except Exception as exc:
        # The runner raises without notifying observers, so the log still holds
        # the failure and anything that completed in the same batch. Publish it,
        # close the stream with `PipelineFailed`, and re-raise for the caller to
        # turn into an error response. Without a terminal event the frontend
        # would show a pipeline that never ends.
        records = await _publish_recorded(bus, pipeline_name, graph, announced)
        transition_name, error = _failure(records, exc)
        await bus.publish(
            PipelineFailed(
                pipeline_name=pipeline_name,
                error=error,
                transition_name=transition_name,
                transitions_fired=_fired_total(graph) - fired_before,
                duration_ms=(time.monotonic() - started_at) * 1000,
            )
        )
        raise

    await bus.publish(
        PipelineCompleted(
            pipeline_name=pipeline_name,
            transitions_fired=summary.n_fired,
            duration_ms=(time.monotonic() - started_at) * 1000,
        )
    )
    return ctx.graph, summary.n_fired
