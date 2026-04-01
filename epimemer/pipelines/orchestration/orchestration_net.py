"""Top-level orchestration Petri net.

Routes incoming memory requests to the appropriate sub-pipeline.
Supports auto-reflect: after N ingestions, automatically triggers reflection.

Petri net flow:
    [MemoryRequest] → route_request → [IngestInput | SearchInput | ReflectInput | ...]
    [IngestInput] → run_ingest → [MemoryResult]
    [SearchInput] → run_search → [MemoryResult]
    [ReflectInput] → run_reflect → [MemoryResult]

The orchestration net wraps the tool functions from epimemer.mcp.tools
as transitions, making the full request flow visualizable via Petritype.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field

from petritype import petri_net
from petritype.core.executable_graph_components import (
    ArgumentEdgeToTransition,
    ExecutableGraph,
    ExecutableGraphOperations,
    FunctionTransitionNode,
    ListPlaceNode,
    ReturnedEdgeFromTransition,
)

from epimemer.embeddings.protocol import EmbeddingProvider
from epimemer.llm.protocol import DecompositionProvider
from epimemer.mcp.config import ServerConfig
from epimemer.mcp.tools import (
    ingest as ingest_tool,
    search as search_tool,
    reflect as reflect_tool,
)
from epimemer.mcp.types import ResponseMeta
from epimemer.storage.protocol import StorageBackend


# --- Token types ---


class MemoryRequest(BaseModel):
    """A request to the memory system."""
    action: Literal["ingest", "search", "reflect"]
    payload: dict = Field(default_factory=dict)


class IngestInput(BaseModel):
    """Routed input for the ingest pipeline."""
    content: str
    metadata: dict = Field(default_factory=dict)
    segmentation_strategy: str | None = None
    metacontext_id: str | None = None


class SearchInput(BaseModel):
    """Routed input for the search pipeline."""
    query: str
    k: int = 10
    node_types: list[str] | None = None
    graph_hops: int = 1
    metacontext_id: str | None = None


class ReflectInput(BaseModel):
    """Routed input for the reflection pipeline."""
    similarity_threshold: float = 0.85
    decay_rate: float = 0.05
    auto_merge: bool = True


class MemoryResult(BaseModel):
    """Result from any memory operation."""
    action: str
    result: dict = Field(default_factory=dict)
    meta: dict = Field(default_factory=dict)


class OrchestrationState(BaseModel):
    """Tracks orchestration state for auto-reflect."""
    ingestions_since_reflect: int = 0
    auto_reflect_threshold: int = 10


# --- Routing ---


class RouteResult(BaseModel):
    """Holds the routed input for fan-out to the correct pipeline."""
    ingest_input: IngestInput | None = None
    search_input: SearchInput | None = None
    reflect_input: ReflectInput | None = None


def route_request(request: MemoryRequest) -> RouteResult:
    """Route a MemoryRequest to the appropriate typed input."""
    if request.action == "ingest":
        return RouteResult(ingest_input=IngestInput(**request.payload))
    elif request.action == "search":
        return RouteResult(search_input=SearchInput(**request.payload))
    elif request.action == "reflect":
        return RouteResult(reflect_input=ReflectInput(**request.payload))
    else:
        raise ValueError(f"Unknown action: {request.action}")


def distribute_route(result: RouteResult) -> dict[str, Any]:
    """Route the result to the correct input place."""
    output = {}
    if result.ingest_input is not None:
        output["IngestInput"] = result.ingest_input
    if result.search_input is not None:
        output["SearchInput"] = result.search_input
    if result.reflect_input is not None:
        output["ReflectInput"] = result.reflect_input
    return output


# --- Transition functions ---


async def run_ingest(
    input: IngestInput,
    storage: StorageBackend,
    embedding_provider: EmbeddingProvider,
    decomposition_provider: DecompositionProvider,
    config: ServerConfig,
) -> MemoryResult:
    """Execute the full ingestion pipeline."""
    result, meta = await ingest_tool(
        content=input.content,
        storage=storage,
        embedding_provider=embedding_provider,
        decomposition_provider=decomposition_provider,
        config=config,
        metadata=input.metadata,
        segmentation_strategy=input.segmentation_strategy,
        metacontext_id=input.metacontext_id,
    )
    return MemoryResult(
        action="ingest",
        result=result,
        meta=meta.model_dump(),
    )


async def run_search(
    input: SearchInput,
    storage: StorageBackend,
    embedding_provider: EmbeddingProvider,
) -> MemoryResult:
    """Execute the hybrid retrieval pipeline."""
    result, meta = await search_tool(
        query=input.query,
        storage=storage,
        embedding_provider=embedding_provider,
        k=input.k,
        node_types=input.node_types,
        graph_hops=input.graph_hops,
        metacontext_id=input.metacontext_id,
    )
    return MemoryResult(
        action="search",
        result=result,
        meta=meta.model_dump(),
    )


async def run_reflect(
    input: ReflectInput,
    storage: StorageBackend,
    embedding_provider: EmbeddingProvider,
) -> MemoryResult:
    """Execute the reflection pipeline."""
    result, meta = await reflect_tool(
        storage=storage,
        embedding_provider=embedding_provider,
        similarity_threshold=input.similarity_threshold,
        decay_rate=input.decay_rate,
        auto_merge=input.auto_merge,
    )
    return MemoryResult(
        action="reflect",
        result=result,
        meta=meta.model_dump(),
    )


# --- Petri net factory ---


@petri_net(
    name="orchestration",
    mode="batch",
    description="Top-level orchestration: routes requests to sub-pipelines.",
)
def orchestration_net(
    request: MemoryRequest,
    storage: StorageBackend,
    embedding_provider: EmbeddingProvider,
    decomposition_provider: DecompositionProvider,
    config: ServerConfig,
) -> ExecutableGraph:
    """Build the orchestration Petri net.

    Routes a MemoryRequest to the correct sub-pipeline (ingest, search,
    or reflect) and produces a MemoryResult.
    """
    return ExecutableGraphOperations.construct_graph([
        # Input place
        ListPlaceNode("MemoryRequest", MemoryRequest, [request]),

        # Routed input places (one per pipeline)
        ListPlaceNode("IngestInput", IngestInput),
        ListPlaceNode("SearchInput", SearchInput),
        ListPlaceNode("ReflectInput", ReflectInput),

        # Output place
        ListPlaceNode("MemoryResult", MemoryResult),

        # Transition 0: route request to the correct input place
        FunctionTransitionNode(
            "route_request",
            route_request,
            output_distribution_function=distribute_route,
        ),
        ArgumentEdgeToTransition("MemoryRequest", "route_request", "request"),
        ReturnedEdgeFromTransition("route_request", "IngestInput"),
        ReturnedEdgeFromTransition("route_request", "SearchInput"),
        ReturnedEdgeFromTransition("route_request", "ReflectInput"),

        # Transition 1: run ingest
        FunctionTransitionNode(
            "run_ingest",
            run_ingest,
            kwargs={
                "storage": storage,
                "embedding_provider": embedding_provider,
                "decomposition_provider": decomposition_provider,
                "config": config,
            },
        ),
        ArgumentEdgeToTransition("IngestInput", "run_ingest", "input"),
        ReturnedEdgeFromTransition("run_ingest", "MemoryResult"),

        # Transition 2: run search
        FunctionTransitionNode(
            "run_search",
            run_search,
            kwargs={
                "storage": storage,
                "embedding_provider": embedding_provider,
            },
        ),
        ArgumentEdgeToTransition("SearchInput", "run_search", "input"),
        ReturnedEdgeFromTransition("run_search", "MemoryResult"),

        # Transition 3: run reflect
        FunctionTransitionNode(
            "run_reflect",
            run_reflect,
            kwargs={
                "storage": storage,
                "embedding_provider": embedding_provider,
            },
        ),
        ArgumentEdgeToTransition("ReflectInput", "run_reflect", "input"),
        ReturnedEdgeFromTransition("run_reflect", "MemoryResult"),
    ])


# --- Auto-reflect helper ---


def should_auto_reflect(state: OrchestrationState) -> bool:
    """Check if automatic reflection should be triggered."""
    return state.ingestions_since_reflect >= state.auto_reflect_threshold


async def execute_with_auto_reflect(
    request: MemoryRequest,
    state: OrchestrationState,
    storage: StorageBackend,
    embedding_provider: EmbeddingProvider,
    decomposition_provider: DecompositionProvider,
    config: ServerConfig,
) -> tuple[MemoryResult, OrchestrationState, MemoryResult | None]:
    """Execute a request and optionally trigger auto-reflect.

    Returns:
        (result, updated_state, reflect_result_or_none)
    """
    # Execute the main request
    graph = orchestration_net(request, storage, embedding_provider, decomposition_provider, config)
    graph, _ = await ExecutableGraphOperations.execute_graph(graph, max_transitions=10)
    result: MemoryResult = graph.place_named("MemoryResult").tokens[0]

    # Update state
    reflect_result = None
    if request.action == "ingest":
        state = state.model_copy(update={
            "ingestions_since_reflect": state.ingestions_since_reflect + 1,
        })

        # Check if auto-reflect should fire
        if should_auto_reflect(state):
            reflect_request = MemoryRequest(action="reflect", payload={})
            reflect_graph = orchestration_net(
                reflect_request, storage, embedding_provider, decomposition_provider, config,
            )
            reflect_graph, _ = await ExecutableGraphOperations.execute_graph(reflect_graph, max_transitions=10)
            reflect_result = reflect_graph.place_named("MemoryResult").tokens[0]
            state = state.model_copy(update={"ingestions_since_reflect": 0})

    return result, state, reflect_result
