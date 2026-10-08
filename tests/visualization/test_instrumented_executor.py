"""Tests for the instrumented pipeline executor.

The stream is read from the engine's own event log, so these pin what that
buys: durations measured around the body, and places taken from the tokens a
firing actually consumed and produced.
"""

import asyncio

import pytest
from petritype.core.executable_graph_components import (
    ArgumentEdgeToTransition,
    ExecutableGraph,
    ExecutableGraphOperations,
    FunctionTransitionNode,
    ListPlaceNode,
    ReturnedEdgeFromTransition,
)
from petritype.runtime import ExecutionMode

from epimemer.visualization.event_bus import create_event_bus
from epimemer.visualization.events import (
    Event,
    PipelineCompleted,
    PipelineFailed,
    PipelineStarted,
    TokensUpdated,
    TransitionCompleted,
    TransitionFired,
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

    # Initial marking, then the one firing and the marking after it.
    assert [e.event_type for e in events] == [
        "pipeline_started",
        "tokens_updated",
        "transition_fired",
        "transition_completed",
        "tokens_updated",
        "pipeline_completed",
    ]


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


# --- What the engine's records give the stream ---


async def _sleep_30ms(x: int) -> int:
    await asyncio.sleep(0.03)
    return x


async def _sleep_10ms(x: int) -> int:
    await asyncio.sleep(0.01)
    return x


async def _sleep_60ms(x: int) -> int:
    await asyncio.sleep(0.06)
    return x


def _raise(x: int) -> int:
    raise RuntimeError("transition body blew up")


def _route(a: int, b: int) -> int | str:
    return a + b


def _recorder(bus) -> list[Event]:
    events: list[Event] = []
    bus.subscribe(handler=lambda e: events.append(e))
    return events


def _durations(events: list[Event]) -> dict[str, float]:
    return {e.transition_name: e.duration_ms for e in events if isinstance(e, TransitionCompleted)}


@pytest.mark.asyncio
async def test_duration_is_the_body_wall_time():
    """[A] → double → [B] → sleep → [C]: only the second body takes time."""
    graph = ExecutableGraphOperations.construct_graph(
        [
            ListPlaceNode("A", int, [1]),
            ListPlaceNode("B", int),
            ListPlaceNode("C", int),
            FunctionTransitionNode("double", _double),
            FunctionTransitionNode("sleep", _sleep_30ms),
            ArgumentEdgeToTransition("A", "double", "x"),
            ReturnedEdgeFromTransition("double", "B"),
            ArgumentEdgeToTransition("B", "sleep", "x"),
            ReturnedEdgeFromTransition("sleep", "C"),
        ]
    )
    bus = create_event_bus()
    events = _recorder(bus)

    await execute_with_events(graph, bus, "durations")

    durations = _durations(events)
    assert durations["sleep"] >= 25
    assert durations["double"] < 15


@pytest.mark.asyncio
async def test_overlapping_bodies_each_report_their_own_duration():
    """Two branches run side by side in concurrent mode. The slow one's body
    spans the fast one's completion, so the time between two notifications
    would cut it short; the engine's measurement does not."""
    graph = ExecutableGraphOperations.construct_graph(
        [
            ListPlaceNode("In", int, [1, 2]),
            ListPlaceNode("FastOut", int),
            ListPlaceNode("SlowOut", int),
            FunctionTransitionNode("fast", _sleep_10ms),
            FunctionTransitionNode("slow", _sleep_60ms),
            ArgumentEdgeToTransition("In", "fast", "x"),
            ReturnedEdgeFromTransition("fast", "FastOut"),
            ArgumentEdgeToTransition("In", "slow", "x"),
            ReturnedEdgeFromTransition("slow", "SlowOut"),
        ]
    )
    bus = create_event_bus()
    events = _recorder(bus)

    _graph, fired = await execute_with_events(graph, bus, "overlap", mode=ExecutionMode.CONCURRENT)

    assert fired == 2
    durations = _durations(events)
    assert durations["slow"] >= 55
    assert durations["fast"] < 55


@pytest.mark.asyncio
async def test_places_come_from_the_tokens_moved():
    """`route` reads two places and is wired to two outputs, but an int result
    lands only in the int-typed one. Inputs are reported in argument order and
    outputs name only where a token arrived."""
    graph = ExecutableGraphOperations.construct_graph(
        [
            ListPlaceNode("Zeta", int, [1]),
            ListPlaceNode("Alpha", int, [2]),
            ListPlaceNode("Ints", int),
            ListPlaceNode("Words", str),
            FunctionTransitionNode("route", _route),
            ArgumentEdgeToTransition("Zeta", "route", "a"),
            ArgumentEdgeToTransition("Alpha", "route", "b"),
            ReturnedEdgeFromTransition("route", "Ints"),
            ReturnedEdgeFromTransition("route", "Words"),
        ]
    )
    bus = create_event_bus()
    events = _recorder(bus)

    await execute_with_events(graph, bus, "places")

    [fired] = [e for e in events if isinstance(e, TransitionFired)]
    [completed] = [e for e in events if isinstance(e, TransitionCompleted)]
    assert fired.input_places == ["Zeta", "Alpha"]
    assert completed.output_places == ["Ints"]


@pytest.mark.asyncio
async def test_failure_names_the_raising_transition():
    """[A] → double → [B] → explode → [C]: one good firing, then a raising one."""
    graph = ExecutableGraphOperations.construct_graph(
        [
            ListPlaceNode("A", int, [1]),
            ListPlaceNode("B", int),
            ListPlaceNode("C", int),
            FunctionTransitionNode("double", _double),
            FunctionTransitionNode("explode", _raise),
            ArgumentEdgeToTransition("A", "double", "x"),
            ReturnedEdgeFromTransition("double", "B"),
            ArgumentEdgeToTransition("B", "explode", "x"),
            ReturnedEdgeFromTransition("explode", "C"),
        ]
    )
    bus = create_event_bus()
    events = _recorder(bus)

    with pytest.raises(RuntimeError):
        await execute_with_events(graph, bus, "failing")

    assert isinstance(events[-1], PipelineFailed)
    failed = events[-1]
    assert failed.transition_name == "explode"
    assert failed.transitions_fired == 1
    # The error is the body's own exception, not the engine's wrapper.
    assert "transition body blew up" in failed.error
    assert [e.transition_name for e in events if isinstance(e, TransitionFired)] == ["double"]


@pytest.mark.asyncio
async def test_concurrent_failure_reports_what_completed_beside_it():
    """A concurrent batch can complete good firings and a failing one together,
    with no notification in between. Whatever completed is still reported, so
    the stream agrees with the count `PipelineFailed` carries."""
    width = 6
    components: list = [ListPlaceNode("In", int, list(range(width + 1)))]
    for i in range(width):
        components.extend(
            [
                ListPlaceNode(f"Out{i}", int),
                FunctionTransitionNode(f"ok{i}", _double),
                ArgumentEdgeToTransition("In", f"ok{i}", "x"),
                ReturnedEdgeFromTransition(f"ok{i}", f"Out{i}"),
            ]
        )
    components.extend(
        [
            ListPlaceNode("Never", int),
            FunctionTransitionNode("explode", _raise),
            ArgumentEdgeToTransition("In", "explode", "x"),
            ReturnedEdgeFromTransition("explode", "Never"),
        ]
    )
    graph = ExecutableGraphOperations.construct_graph(components)
    bus = create_event_bus()
    events = _recorder(bus)

    with pytest.raises(RuntimeError):
        await execute_with_events(graph, bus, "fan", mode=ExecutionMode.CONCURRENT)

    failed = events[-1]
    assert isinstance(failed, PipelineFailed)
    assert failed.transition_name == "explode"
    completed = [e for e in events if isinstance(e, TransitionCompleted)]
    assert len(completed) == failed.transitions_fired
    assert sum(graph.fired_counts.values()) == failed.transitions_fired
