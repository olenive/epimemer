"""Top-level orchestration Petri net.

Routes incoming memory requests to the appropriate sub-pipeline.
Supports auto-reflect: after N store_decomposition calls, automatically triggers reflection.

Petri net flow:
    [MemoryRequest] → route_request → [SegmentInput | StoreInput | SearchInput | ReflectInput | ...]
    [SegmentInput] → run_segment → [MemoryResult]
    [StoreInput] → run_store_decomposition → [MemoryResult]
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
from petritype.runtime import RunContext, Runner

from epimemer.embeddings.protocol import EmbeddingProvider
from epimemer.mcp.config import ServerConfig
from epimemer.mcp.tools import (
    segment_text as segment_tool,
    store_decomposition as store_decomposition_tool,
    search as search_tool,
    reflect as reflect_tool,
)
from epimemer.mcp.types import ResponseMeta
from epimemer.storage.protocol import StorageBackend


# --- Token types ---


class MemoryRequest(BaseModel):
    """A request to the memory system."""
    action: Literal["segment", "store_decomposition", "search", "reflect"]
    payload: dict = Field(default_factory=dict)


class SegmentInput(BaseModel):
    """Routed input for the segmentation pipeline (step 1 of ingest)."""
    content: str
    metadata: dict = Field(default_factory=dict)
    segmentation_strategy: str | None = None


class StoreDecompositionInput(BaseModel):
    """Routed input for storing agent-provided decomposition (step 2 of ingest)."""
    document_id: str
    segments: list[dict] = Field(default_factory=list)
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


class MemoryResult(BaseModel):
    """Result from any memory operation."""
    action: str
    result: dict = Field(default_factory=dict)
    meta: dict = Field(default_factory=dict)


class OrchestrationState(BaseModel):
    """Tracks orchestration state for auto-reflect."""
    stores_since_reflect: int = 0
    auto_reflect_threshold: int = 10


# --- Routing ---


class RouteResult(BaseModel):
    """Holds the routed input for fan-out to the correct pipeline."""
    segment_input: SegmentInput | None = None
    store_input: StoreDecompositionInput | None = None
    search_input: SearchInput | None = None
    reflect_input: ReflectInput | None = None


def route_request(request: MemoryRequest) -> RouteResult:
    """Route a MemoryRequest to the appropriate typed input."""
    if request.action == "segment":
        return RouteResult(segment_input=SegmentInput(**request.payload))
    elif request.action == "store_decomposition":
        return RouteResult(store_input=StoreDecompositionInput(**request.payload))
    elif request.action == "search":
        return RouteResult(search_input=SearchInput(**request.payload))
    elif request.action == "reflect":
        return RouteResult(reflect_input=ReflectInput(**request.payload))
    else:
        raise ValueError(f"Unknown action: {request.action}")


def distribute_route(result: RouteResult) -> dict[str, Any]:
    """Route the result to the correct input place."""
    output = {}
    if result.segment_input is not None:
        output["SegmentInput"] = result.segment_input
    if result.store_input is not None:
        output["StoreInput"] = result.store_input
    if result.search_input is not None:
        output["SearchInput"] = result.search_input
    if result.reflect_input is not None:
        output["ReflectInput"] = result.reflect_input
    return output


# --- Transition functions ---


async def run_segment(
    input: SegmentInput,
    storage: StorageBackend,
    embedding_provider: EmbeddingProvider,
    config: ServerConfig,
) -> MemoryResult:
    """Execute the segmentation pipeline (step 1 of ingest)."""
    result, meta = await segment_tool(
        content=input.content,
        storage=storage,
        embedding_provider=embedding_provider,
        config=config,
        metadata=input.metadata,
        segmentation_strategy=input.segmentation_strategy,
    )
    return MemoryResult(
        action="segment",
        result=result,
        meta=meta.model_dump(),
    )


async def run_store_decomposition(
    input: StoreDecompositionInput,
    storage: StorageBackend,
    embedding_provider: EmbeddingProvider,
) -> MemoryResult:
    """Store agent-provided decomposition (step 2 of ingest)."""
    result, meta = await store_decomposition_tool(
        document_id=input.document_id,
        segments=input.segments,
        storage=storage,
        embedding_provider=embedding_provider,
        metacontext_id=input.metacontext_id,
    )
    return MemoryResult(
        action="store_decomposition",
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
    config: ServerConfig,
) -> ExecutableGraph:
    """Build the orchestration Petri net.

    Routes a MemoryRequest to the correct sub-pipeline (segment,
    store_decomposition, search, or reflect) and produces a MemoryResult.
    """
    return ExecutableGraphOperations.construct_graph([
        # Input place
        ListPlaceNode("MemoryRequest", MemoryRequest, [request]),

        # Routed input places (one per pipeline)
        ListPlaceNode("SegmentInput", SegmentInput),
        ListPlaceNode("StoreInput", StoreDecompositionInput),
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
        ReturnedEdgeFromTransition("route_request", "SegmentInput"),
        ReturnedEdgeFromTransition("route_request", "StoreInput"),
        ReturnedEdgeFromTransition("route_request", "SearchInput"),
        ReturnedEdgeFromTransition("route_request", "ReflectInput"),

        # Transition 1: run segment
        FunctionTransitionNode(
            "run_segment",
            run_segment,
            kwargs={
                "storage": storage,
                "embedding_provider": embedding_provider,
                "config": config,
            },
        ),
        ArgumentEdgeToTransition("SegmentInput", "run_segment", "input"),
        ReturnedEdgeFromTransition("run_segment", "MemoryResult"),

        # Transition 2: run store_decomposition
        FunctionTransitionNode(
            "run_store_decomposition",
            run_store_decomposition,
            kwargs={
                "storage": storage,
                "embedding_provider": embedding_provider,
            },
        ),
        ArgumentEdgeToTransition("StoreInput", "run_store_decomposition", "input"),
        ReturnedEdgeFromTransition("run_store_decomposition", "MemoryResult"),

        # Transition 3: run search
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

        # Transition 4: run reflect
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
    ], expect_acyclic=True)


# --- Auto-reflect helper ---


def should_auto_reflect(state: OrchestrationState) -> bool:
    """Check if automatic reflection should be triggered."""
    return state.stores_since_reflect >= state.auto_reflect_threshold


async def execute_with_auto_reflect(
    request: MemoryRequest,
    state: OrchestrationState,
    storage: StorageBackend,
    embedding_provider: EmbeddingProvider,
    config: ServerConfig,
) -> tuple[MemoryResult, OrchestrationState, MemoryResult | None]:
    """Execute a request and optionally trigger auto-reflect.

    Returns:
        (result, updated_state, reflect_result_or_none)
    """
    graph = orchestration_net(request, storage, embedding_provider, config)
    graph = await Runner.run_to_completion(RunContext(graph=graph))
    result: MemoryResult = graph.place_named("MemoryResult").tokens[0]

    reflect_result = None
    if request.action == "store_decomposition":
        state = state.model_copy(update={
            "stores_since_reflect": state.stores_since_reflect + 1,
        })

        if should_auto_reflect(state):
            reflect_request = MemoryRequest(action="reflect", payload={})
            reflect_graph = orchestration_net(
                reflect_request, storage, embedding_provider, config,
            )
            reflect_graph = await Runner.run_to_completion(RunContext(graph=reflect_graph))
            reflect_result = reflect_graph.place_named("MemoryResult").tokens[0]
            state = state.model_copy(update={"stores_since_reflect": 0})

    return result, state, reflect_result
