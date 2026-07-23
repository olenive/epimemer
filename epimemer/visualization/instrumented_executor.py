"""Turns Petri net execution into the event stream the visualization consumes.

Petritype's `Runner` already drives a net to quiescence and hands observers the
live graph after every state change. All that is missing is a translation: graph
state → `PipelineEvent`. That translation is `pipeline_observer`, and it is the
only thing in this module that knows about the wire format.

Two consequences worth stating, because they were not true of the hand-rolled
loop this replaces:

- **No iteration cap.** The net stops when nothing is enabled, not when a
  guessed budget runs out. A cap that is too low truncates the pipeline and
  returns a half-filled result with no error; one that is high enough to be safe
  never fires.
- **Observing does not change the run.** Events are published from a graph the
  runner owns; the observer only reads. The bus is a tap, not a valve — see
  `tests/pipelines/test_net_execution.py`.
- **Every run ends the stream.** Success emits `PipelineCompleted`; a raising
  transition emits `PipelineFailed` and then re-raises. The two are distinct so a
  consumer can tell a finished pipeline from a failed one.

Usage:
    bus = create_event_bus()
    graph = reflection_net(request, storage, embedding_provider)
    result_graph, fired = await execute_with_events(graph, bus, "reflection")
"""

import time

from petritype.core.executable_graph_components import ExecutableGraph
from petritype.runtime import Observer, RunContext, Runner

from epimemer.visualization.event_bus import InProcessEventBus
from epimemer.visualization.events import (
    PipelineCompleted,
    PipelineFailed,
    PipelineStarted,
    PipelineTopologyEdge,
    TokensUpdated,
    TransitionCompleted,
    TransitionEnabled,
    TransitionFired,
)


def _place_token_counts(graph: ExecutableGraph) -> dict[str, int]:
    """Extract current token counts from all places in the graph."""
    return {p.name: len(p.tokens) for p in graph.places}


def _place_names(graph: ExecutableGraph) -> list[str]:
    return [p.name for p in graph.places]


def _transition_names(graph: ExecutableGraph) -> list[str]:
    return [t.name for t in graph.transitions]


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


def _input_place_names(graph: ExecutableGraph, transition_name: str) -> list[str]:
    """Places wired as inputs to a transition, including non-consuming read edges.

    Taken from the topology rather than from the engine's fire history: the
    history is capped for memory and its retention is configurable, so it is not
    a dependable source. The two agree for any transition whose input arcs all
    carry a token when it fires, which is every net here.
    """
    return [
        edge.place_node_name
        for edge in (*graph.argument_edges, *graph.snapshot_edges, *graph.mutate_edges)
        if edge.transition_node_name == transition_name
    ]


def _output_place_names(graph: ExecutableGraph, transition_name: str) -> list[str]:
    """Places a transition deposits into, from the topology. See `_input_place_names`."""
    return [
        edge.place_node_name
        for edge in graph.return_edges
        if edge.transition_node_name == transition_name
    ]


def pipeline_observer(bus: InProcessEventBus, pipeline_name: str) -> Observer:
    """Build an observer that republishes graph state changes as pipeline events.

    The runner calls this once for the initial marking and once after every
    change. What fired since the last call is read by diffing `fired_counts` — a
    per-transition tally the engine keeps, monotonic and never trimmed — not from
    `last_fired`. That distinction is load-bearing in `CONCURRENT` mode: a single
    notification can cover a whole batch of completions, `step_count` jumps by
    more than one, and `last_fired` names only the last of them. Diffing the
    counts catches every firing in the batch; reading `last_fired` would drop all
    but one. In `SEQUENTIAL` mode each firing gets its own notification, so the
    diff is +1 on one transition and the two readings agree.

    `TransitionEnabled` / `Fired` / `Completed` are emitted together, after the
    fact — one triple per firing. The runner fires atomically from an observer's
    point of view (no callback between "enabled" and "done"), so the three
    describe one completed firing rather than three moments; they stay separate
    events because the frontend consumes them that way. Within one batch the
    triples are ordered by net definition, not completion order — concurrent
    completion order is only partial anyway.
    """
    state: dict = {"fired_counts": None, "since": time.monotonic()}

    async def observe(graph: ExecutableGraph) -> None:
        now = time.monotonic()
        previous = state["fired_counts"]
        current = dict(graph.fired_counts)
        state["fired_counts"] = current

        if previous is not None:
            for transition_name in _transition_names(graph):
                fired = current.get(transition_name, 0) - previous.get(transition_name, 0)
                for _ in range(fired):
                    await bus.publish(TransitionEnabled(
                        pipeline_name=pipeline_name,
                        transition_name=transition_name,
                    ))
                    await bus.publish(TransitionFired(
                        pipeline_name=pipeline_name,
                        transition_name=transition_name,
                        input_places=_input_place_names(graph, transition_name),
                    ))
                    await bus.publish(TransitionCompleted(
                        pipeline_name=pipeline_name,
                        transition_name=transition_name,
                        output_places=_output_place_names(graph, transition_name),
                        duration_ms=(now - state["since"]) * 1000,
                    ))

        state["since"] = now
        await bus.publish(TokensUpdated(
            pipeline_name=pipeline_name,
            place_token_counts=_place_token_counts(graph),
        ))

    return observe


async def execute_with_events(
    graph: ExecutableGraph,
    bus: InProcessEventBus,
    pipeline_name: str,
) -> tuple[ExecutableGraph, int]:
    """Run a Petri net to quiescence, publishing the pipeline's event stream.

    Args:
        graph: The executable Petri net graph.
        bus: Event bus to publish pipeline events on.
        pipeline_name: Name for this pipeline execution (e.g., "reflection").

    Returns:
        (updated_graph, transitions_fired)
    """
    started_at = time.monotonic()
    steps_before = graph.step_count

    await bus.publish(PipelineStarted(
        pipeline_name=pipeline_name,
        place_names=_place_names(graph),
        transition_names=_transition_names(graph),
        edges=_topology_edges(graph),
    ))

    # A raising transition must still close the stream. The runner mutates this
    # graph in place, so `step_count` reflects what fired before the failure even
    # after the exception unwinds. Publish a terminal `PipelineFailed` and
    # re-raise — the caller (`_run_with_timeout`) turns it into an error response;
    # without this the frontend would show a pipeline that never ends.
    try:
        graph = await Runner.run_to_completion(
            RunContext(graph=graph, observers=(pipeline_observer(bus, pipeline_name),))
        )
    except Exception as exc:
        await bus.publish(PipelineFailed(
            pipeline_name=pipeline_name,
            error=str(exc),
            transitions_fired=graph.step_count - steps_before,
            duration_ms=(time.monotonic() - started_at) * 1000,
        ))
        raise

    fired = graph.step_count - steps_before
    await bus.publish(PipelineCompleted(
        pipeline_name=pipeline_name,
        transitions_fired=fired,
        duration_ms=(time.monotonic() - started_at) * 1000,
    ))

    return graph, fired
