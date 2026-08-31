"""How Epimemer drives a Petri net: to quiescence, with observers optional.

Execution used to be capped by a per-call `max_transitions` guess — 10 by
default, 3 for retrieval. A guess is either wrong or redundant: too low and the
net stops mid-pipeline and silently returns a half-filled result; high enough to
be safe and it never fires, which is just quiescence with extra steps. The
Runner runs until nothing is enabled, so the cap is gone and these tests pin
that it stays gone.
"""

import pytest
from petritype.core.executable_graph_components import (
    ArgumentEdgeToTransition,
    ExecutableGraph,
    ExecutableGraphOperations,
    FunctionTransitionNode,
    ListPlaceNode,
    ReturnedEdgeFromTransition,
)

from epimemer.core.types import Fact, Inference, RawDocument, Segment, Topic
from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.mcp import tools
from epimemer.mcp.config import ServerConfig
from epimemer.pipelines.graph_construction.edge_creation import (
    DecomposedSegment,
    edge_creation_net,
)
from epimemer.pipelines.orchestration.orchestration_net import (
    MemoryRequest,
    orchestration_net,
)
from epimemer.pipelines.query.hybrid_retrieval import hybrid_retrieval_net
from epimemer.pipelines.query.types import QueryRequest
from epimemer.pipelines.segmentation.paragraph_split import (
    paragraph_split_segmentation_net,
)
from epimemer.pipelines.segmentation.semantic_similarity import (
    semantic_similarity_segmentation_net,
)
from epimemer.storage.memory import InMemoryStorage
from epimemer.visualization.event_bus import create_event_bus
from epimemer.visualization.events import Event


def _increment(x: int) -> int:
    return x + 1


def _boom(x: int) -> int:
    raise RuntimeError("transition body blew up")


def _chain_graph(length: int) -> ExecutableGraph:
    """A linear net of `length` transitions: [P0] → t0 → [P1] → t1 → ... → [Pn].

    Longer than any cap the old code used, so a net that stops early stops
    visibly: the final place is empty and the fired count is short.
    """
    components: list = [ListPlaceNode("P0", int, [0])]
    for step in range(length):
        components.extend(
            [
                ListPlaceNode(f"P{step + 1}", int),
                FunctionTransitionNode(f"t{step}", _increment),
                ArgumentEdgeToTransition(f"P{step}", f"t{step}", "x"),
                ReturnedEdgeFromTransition(f"t{step}", f"P{step + 1}"),
            ]
        )
    return ExecutableGraphOperations.construct_graph(components)


def _failing_chain_graph() -> ExecutableGraph:
    """[P0]=0 → t0 (increment) → [P1] → t1 (raises) → [P2].

    t0 fires cleanly, t1 raises — so a run gets one good firing and then a
    failure, which is the case the event stream must still close on.
    """
    return ExecutableGraphOperations.construct_graph(
        [
            ListPlaceNode("P0", int, [0]),
            ListPlaceNode("P1", int),
            ListPlaceNode("P2", int),
            FunctionTransitionNode("t0", _increment),
            ArgumentEdgeToTransition("P0", "t0", "x"),
            ReturnedEdgeFromTransition("t0", "P1"),
            FunctionTransitionNode("t1", _boom),
            ArgumentEdgeToTransition("P1", "t1", "x"),
            ReturnedEdgeFromTransition("t1", "P2"),
        ]
    )


def _parallel_fan_graph(width: int) -> ExecutableGraph:
    """`width` independent transitions, all enabled from the start: [Ai]=0 → ti → [Bi].

    In CONCURRENT mode they fire in one batch, so `step_count` jumps by `width`
    on a single observer notification and `last_fired` names only one of them —
    the case the observer must cover by diffing `fired_counts`.
    """
    components: list = []
    for i in range(width):
        components.extend(
            [
                ListPlaceNode(f"A{i}", int, [0]),
                ListPlaceNode(f"B{i}", int),
                FunctionTransitionNode(f"t{i}", _increment),
                ArgumentEdgeToTransition(f"A{i}", f"t{i}", "x"),
                ReturnedEdgeFromTransition(f"t{i}", f"B{i}"),
            ]
        )
    return ExecutableGraphOperations.construct_graph(components)


CHAIN_LENGTH = 12  # deliberately above the old default cap of 10
FAN_WIDTH = 4


class TestRunsToQuiescence:
    """`_run_net` fires until nothing is enabled — no iteration cap either path."""

    async def test_without_an_event_bus(self):
        graph, fired = await tools._run_net(_chain_graph(CHAIN_LENGTH), "chain", None)

        assert fired == CHAIN_LENGTH
        final = graph.place_named(f"P{CHAIN_LENGTH}")
        assert final.tokens == [CHAIN_LENGTH]

    async def test_with_an_event_bus(self):
        bus = create_event_bus()
        graph, fired = await tools._run_net(_chain_graph(CHAIN_LENGTH), "chain", bus)

        assert fired == CHAIN_LENGTH
        final = graph.place_named(f"P{CHAIN_LENGTH}")
        assert final.tokens == [CHAIN_LENGTH]

    async def test_both_paths_agree_on_the_result(self):
        """Observing a run must not change it — the bus is a tap, not a valve."""
        bus = create_event_bus()
        observed, observed_fired = await tools._run_net(_chain_graph(CHAIN_LENGTH), "chain", bus)
        plain, plain_fired = await tools._run_net(_chain_graph(CHAIN_LENGTH), "chain", None)

        assert observed_fired == plain_fired
        assert [p.tokens for p in observed.places] == [p.tokens for p in plain.places]


class TestEventsCoverEveryTransition:
    """The visualization must not miss a step of a long run."""

    async def test_every_transition_is_reported(self):
        bus = create_event_bus()
        events: list[Event] = []
        bus.subscribe(handler=lambda e: events.append(e))

        await tools._run_net(_chain_graph(CHAIN_LENGTH), "chain", bus)

        fired = [e.transition_name for e in events if e.event_type == "transition_fired"]
        assert fired == [f"t{step}" for step in range(CHAIN_LENGTH)]

    async def test_completion_reports_the_full_count(self):
        bus = create_event_bus()
        events: list[Event] = []
        bus.subscribe(handler=lambda e: events.append(e))

        await tools._run_net(_chain_graph(CHAIN_LENGTH), "chain", bus)

        completed = [e for e in events if e.event_type == "pipeline_completed"]
        assert len(completed) == 1
        assert completed[0].transitions_fired == CHAIN_LENGTH

    async def test_events_cover_every_transition_in_concurrent_mode(self):
        """A concurrent batch completes several transitions under one notification;
        the observer must report every one, not just `last_fired`."""
        from petritype.runtime import ExecutionMode, RunContext, Runner

        from epimemer.visualization.instrumented_executor import pipeline_observer

        bus = create_event_bus()
        events: list[Event] = []
        bus.subscribe(handler=lambda e: events.append(e))

        graph = await Runner.run_to_completion(
            RunContext(
                graph=_parallel_fan_graph(FAN_WIDTH),
                mode=ExecutionMode.CONCURRENT,
                observers=(pipeline_observer(bus, "fan"),),
            )
        )

        # Sanity: the fan really did fire as one batch (step jumps past 1).
        assert graph.step_count == FAN_WIDTH
        fired = sorted(e.transition_name for e in events if e.event_type == "transition_fired")
        assert fired == sorted(f"t{i}" for i in range(FAN_WIDTH))


class TestFailureTerminatesTheStream:
    """A raising transition must close the event stream, not leave it hanging.

    Without a terminal event a viewer keeps a pipeline marked "running" forever;
    a `pipeline_failed` (distinct from `pipeline_completed`) lets it clear the
    running state and show the error.
    """

    async def test_failed_transition_still_terminates_the_event_stream(self):
        from epimemer.visualization.instrumented_executor import execute_with_events

        bus = create_event_bus()
        events: list[Event] = []
        bus.subscribe(handler=lambda e: events.append(e))

        with pytest.raises(RuntimeError):  # TransitionFailedError subclasses it
            await execute_with_events(_failing_chain_graph(), bus, "chain")

        # The stream ends on a failure, never a (misleading) completion.
        assert [e for e in events if e.event_type == "pipeline_completed"] == []
        failed = [e for e in events if e.event_type == "pipeline_failed"]
        assert len(failed) == 1
        assert failed[0].pipeline_name == "chain"
        assert failed[0].error  # non-empty — carries the cause
        assert failed[0].transitions_fired == 1  # t0 fired; t1 raised, so did not

    async def test_no_bus_path_propagates_without_swallowing(self):
        """The no-bus branch has no terminal event to emit; it must still raise."""
        with pytest.raises(RuntimeError):
            await tools._run_net(_failing_chain_graph(), "chain", None)


class TestNetsAreAcyclicByConstruction:
    """Termination rests on every net being acyclic; the net factories now declare
    `expect_acyclic=True`, so an accidental cycle fails at build time — with its
    path named — instead of looping at run time. This pins the mechanism Epimemer
    relies on (part of the Petritype integration contract, like the smoke tests).
    """

    def test_a_cyclic_net_is_rejected_at_construction(self):
        # P0 → t0 → P1 → t1 → P0 is a token-flow cycle: it must not build.
        with pytest.raises(ValueError, match="cycle"):
            ExecutableGraphOperations.construct_graph(
                [
                    ListPlaceNode("P0", int, [0]),
                    ListPlaceNode("P1", int),
                    FunctionTransitionNode("t0", _increment),
                    ArgumentEdgeToTransition("P0", "t0", "x"),
                    ReturnedEdgeFromTransition("t0", "P1"),
                    FunctionTransitionNode("t1", _increment),
                    ArgumentEdgeToTransition("P1", "t1", "x"),
                    ReturnedEdgeFromTransition("t1", "P0"),
                ],
                expect_acyclic=True,
            )


# --- Smoke tests: every real net, driven the way production drives it ---


@pytest.fixture
def embedding_provider() -> MockEmbeddingProvider:
    return MockEmbeddingProvider(model_id="mock-embed", dimension=8)


@pytest.fixture
def storage() -> InMemoryStorage:
    return InMemoryStorage()


@pytest.fixture
def config() -> ServerConfig:
    return ServerConfig(
        storage_backend="memory",
        embedding_provider="mock",
        decomposition_provider="mock",
    )


@pytest.fixture
def decomposed() -> DecomposedSegment:
    segment = Segment(source_id="doc1", text="A segment.", span_start=0, span_end=10)
    return DecomposedSegment(
        segment=segment,
        topics=[Topic(content="A topic", source_id=segment.id)],
        facts=[Fact(content="A fact", source_id=segment.id)],
        inferences=[Inference(content="An inference", source_id=segment.id)],
    )


class TestEveryNetReachesQuiescence:
    """The integration contract with Petritype.

    Epimemer tracks Petritype's main branch, so engine changes land without a
    version bump to notice. Each net here is executed end to end through the
    production entry point with mock providers: if a change to enabling,
    firing, or token distribution breaks a pipeline, one of these fails rather
    than the breakage surfacing as an empty result at runtime.

    Depth belongs in each pipeline's own test module; these only assert the net
    ran to completion and put something in its output place.
    """

    async def test_paragraph_segmentation_net(self):
        document = RawDocument(content="First para.\n\nSecond para.")
        graph, fired = await tools._run_net(
            paragraph_split_segmentation_net(document), "segmentation", None
        )

        assert fired >= 1
        assert len(graph.place_named("Segments").tokens) == 2

    async def test_semantic_similarity_segmentation_net(self, embedding_provider):
        document = RawDocument(
            content="Cats purr. Cats nap. Quantum states superpose. Waves collapse."
        )
        graph, fired = await tools._run_net(
            semantic_similarity_segmentation_net(document, embedding_provider),
            "segmentation",
            None,
        )

        assert fired == 3  # split_sentences, compute_similarities, form_segments
        assert graph.place_named("Segments").tokens

    async def test_edge_creation_net(self, decomposed):
        graph, fired = await tools._run_net(edge_creation_net(decomposed), "edge_creation", None)

        assert fired >= 1
        assert graph.place_named("Edges").tokens

    async def test_hybrid_retrieval_net(self, storage, embedding_provider):
        request = QueryRequest(
            query_text="anything",
            k=5,
            model_id=embedding_provider.model_id,
        )
        graph, fired = await tools._run_net(
            hybrid_retrieval_net(request, embedding_provider, storage),
            "retrieval",
            None,
        )

        # Seven transitions: a fork, two retrieval arms, the fusion that joins
        # them, the lineage collapse that cuts the fused set, expansion,
        # assembly. This net is exactly why the old cap of 3 happened to work
        # back when it had three — and why a cap is the wrong mechanism, since
        # adding an arm silently truncated it.
        assert fired == 7
        assert len(graph.place_named("QueryResult").tokens) == 1

    async def test_orchestration_net(self, storage, embedding_provider, config):
        request = MemoryRequest(action="segment", payload={"content": "Something to remember."})
        graph, fired = await tools._run_net(
            orchestration_net(request, storage, embedding_provider, config),
            "orchestration",
            None,
        )

        assert fired == 2  # route_request, then the routed sub-pipeline
        assert len(graph.place_named("MemoryResult").tokens) == 1
