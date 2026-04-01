"""Instrumented pipeline executor that emits Petri net events.

Wraps Petritype's execute_graph with event emission so the pipeline
visualization panel can show transitions firing and tokens moving
in real time.

Usage:
    bus = create_event_bus()
    graph = reflection_net(request, storage, embedding_provider)
    result_graph, steps = await execute_with_events(graph, bus, "reflection")
"""

import time

from petritype.core.executable_graph_components import (
    ArgumentEdgeToTransition,
    ExecutableGraph,
    ExecutableGraphOperations,
    FunctionTransitionNode,
    ListPlaceNode,
    ReturnedEdgeFromTransition,
)

from epimemer.visualization.event_bus import InProcessEventBus
from epimemer.visualization.events import (
    PipelineCompleted,
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
    edges: list[PipelineTopologyEdge] = []
    for edge in graph.argument_edges:
        edges.append(PipelineTopologyEdge(
            source=edge.place_node_name,
            target=edge.transition_node_name,
            label=edge.argument,
        ))
    for edge in graph.return_edges:
        edges.append(PipelineTopologyEdge(
            source=edge.transition_node_name,
            target=edge.place_node_name,
        ))
    return edges


async def execute_with_events(
    graph: ExecutableGraph,
    bus: InProcessEventBus,
    pipeline_name: str,
    *,
    max_iterations: int = 100,
) -> tuple[ExecutableGraph, int]:
    """Execute a Petri net graph step-by-step, emitting pipeline events.

    Fires one transition per step so the visualization can track each
    transition individually. Emits events for:
    - Pipeline start/complete (with full topology for rendering)
    - Each transition enabled/fired/completed
    - Token count updates after each step

    Args:
        graph: The executable Petri net graph.
        bus: Event bus to publish pipeline events on.
        pipeline_name: Name for this pipeline execution (e.g., "reflection").
        max_iterations: Safety limit on total transitions fired.

    Returns:
        (updated_graph, total_transitions_fired)
    """
    pipeline_start = time.monotonic()
    total_fired = 0

    await bus.publish(PipelineStarted(
        pipeline_name=pipeline_name,
        place_names=_place_names(graph),
        transition_names=_transition_names(graph),
        edges=_topology_edges(graph),
    ))

    await bus.publish(TokensUpdated(
        pipeline_name=pipeline_name,
        place_token_counts=_place_token_counts(graph),
    ))

    for _ in range(max_iterations):
        step_start = time.monotonic()

        graph, fired = await ExecutableGraphOperations.execute_graph(
            executable_graph=graph,
            max_transitions=1,
            transition_history_length=1,
            place_history_length=1,
        )

        if fired == 0:
            break

        total_fired += fired
        step_duration_ms = (time.monotonic() - step_start) * 1000

        # Extract what happened from history
        # History is list[list[...]] — one entry per step, each entry is a list of places/transitions
        transition = graph.transition_history[0] if graph.transition_history else None
        input_places = [p.name for p in graph.input_place_history[0]] if graph.input_place_history else []
        output_places = [p.name for p in graph.output_place_history[0]] if graph.output_place_history else []

        if transition is not None:
            await bus.publish(TransitionEnabled(
                pipeline_name=pipeline_name,
                transition_name=transition.name,
            ))

            await bus.publish(TransitionFired(
                pipeline_name=pipeline_name,
                transition_name=transition.name,
                input_places=input_places,
            ))

            await bus.publish(TransitionCompleted(
                pipeline_name=pipeline_name,
                transition_name=transition.name,
                output_places=output_places,
                duration_ms=step_duration_ms,
            ))

        await bus.publish(TokensUpdated(
            pipeline_name=pipeline_name,
            place_token_counts=_place_token_counts(graph),
        ))

    pipeline_duration_ms = (time.monotonic() - pipeline_start) * 1000

    await bus.publish(PipelineCompleted(
        pipeline_name=pipeline_name,
        transitions_fired=total_fired,
        duration_ms=pipeline_duration_ms,
    ))

    return graph, total_fired
