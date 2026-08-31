"""Tests for the instrumented pipeline executor."""

import pytest
from petritype.core.executable_graph_components import (
    ArgumentEdgeToTransition,
    ExecutableGraph,
    ExecutableGraphOperations,
    FunctionTransitionNode,
    ListPlaceNode,
    ReturnedEdgeFromTransition,
)

from epimemer.visualization.event_bus import create_event_bus
from epimemer.visualization.events import (
    Event,
    PipelineCompleted,
    PipelineStarted,
    TokensUpdated,
)
from epimemer.visualization.instrumented_executor import execute_with_events


def _double(x: int) -> int:
    return x * 2


def _build_simple_graph() -> ExecutableGraph:
    """A trivial Petri net: [Input] → double → [Output]."""
    return ExecutableGraphOperations.construct_graph(
        [
            ListPlaceNode("Input", int, [5]),
            ListPlaceNode("Output", int),
            FunctionTransitionNode("double", _double),
            ArgumentEdgeToTransition("Input", "double", "x"),
            ReturnedEdgeFromTransition("double", "Output"),
        ]
    )


@pytest.mark.asyncio
async def test_emits_pipeline_lifecycle_events():
    bus = create_event_bus()
    events: list[Event] = []
    bus.subscribe(handler=lambda e: events.append(e))

    graph = _build_simple_graph()
    result_graph, total = await execute_with_events(graph, bus, "test-pipeline")

    assert total == 1

    # Check output token
    output_place = next(p for p in result_graph.places if p.name == "Output")
    assert output_place.tokens == [10]

    # Check event sequence
    event_types = [e.event_type for e in events]
    assert event_types[0] == "pipeline_started"
    assert "tokens_updated" in event_types
    assert "transition_enabled" in event_types
    assert "transition_fired" in event_types
    assert "transition_completed" in event_types
    assert event_types[-1] == "pipeline_completed"


@pytest.mark.asyncio
async def test_pipeline_started_includes_topology():
    bus = create_event_bus()
    started_events: list[PipelineStarted] = []
    bus.subscribe(PipelineStarted, handler=lambda e: started_events.append(e))

    graph = _build_simple_graph()
    await execute_with_events(graph, bus, "topo-test")

    assert len(started_events) == 1
    started = started_events[0]

    assert "Input" in started.place_names
    assert "Output" in started.place_names
    assert "double" in started.transition_names

    # Check edges
    edge_pairs = [(e.source, e.target) for e in started.edges]
    assert ("Input", "double") in edge_pairs
    assert ("double", "Output") in edge_pairs


@pytest.mark.asyncio
async def test_pipeline_completed_reports_duration():
    bus = create_event_bus()
    completed: list[PipelineCompleted] = []
    bus.subscribe(PipelineCompleted, handler=lambda e: completed.append(e))

    graph = _build_simple_graph()
    await execute_with_events(graph, bus, "dur-test")

    assert len(completed) == 1
    assert completed[0].transitions_fired == 1
    assert completed[0].duration_ms >= 0


@pytest.mark.asyncio
async def test_token_counts_updated_after_each_step():
    bus = create_event_bus()
    token_events: list[TokensUpdated] = []
    bus.subscribe(TokensUpdated, handler=lambda e: token_events.append(e))

    graph = _build_simple_graph()
    await execute_with_events(graph, bus, "token-test")

    # Should have initial + after-step token updates
    assert len(token_events) >= 2

    # Initial: Input has 1 token, Output has 0
    initial = token_events[0]
    assert initial.place_token_counts["Input"] == 1
    assert initial.place_token_counts["Output"] == 0

    # Final: Input has 0, Output has 1
    final = token_events[-1]
    assert final.place_token_counts["Input"] == 0
    assert final.place_token_counts["Output"] == 1
