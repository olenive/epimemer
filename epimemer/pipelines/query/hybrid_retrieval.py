"""Hybrid retrieval Petri net for the query layer.

Composes vector search and graph expansion into a single pipeline:

    [QueryRequest] -> run_vector_search -> [VectorResults]
    [VectorResults] -> run_graph_expansion -> [ExpandedResults]
    [ExpandedResults] -> build_query_result -> [QueryResult]

The factory function accepts embedding_provider and storage as parameters,
which are passed to transitions via kwargs.
"""

import time

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

from epimemer.core.types import (
    EpistemicNode,
    Fact,
    Inference,
    NodeEdge,
    Topic,
)
from epimemer.embeddings.protocol import EmbeddingProvider
from epimemer.pipelines.query.graph_expansion import expand_via_graph
from epimemer.pipelines.query.types import QueryMetadata, QueryRequest, QueryResult
from epimemer.pipelines.query.vector_search import vector_search
from epimemer.storage.protocol import StorageBackend


# --- Intermediate token types ---


class VectorResults(BaseModel):
    """Intermediate result from vector search, carrying data for the next stage."""
    request: QueryRequest
    scored_nodes: list[tuple[str, float]] = Field(default_factory=list)
    nodes: list[EpistemicNode] = Field(default_factory=list)
    nodes_searched: int = 0
    vector_search_time_ms: float = 0.0


class ExpandedResults(BaseModel):
    """Intermediate result from graph expansion, carrying all data for final assembly."""
    request: QueryRequest
    nodes: list[EpistemicNode] = Field(default_factory=list)
    edges: list[NodeEdge] = Field(default_factory=list)
    nodes_searched: int = 0
    vector_search_time_ms: float = 0.0
    graph_expansion_time_ms: float = 0.0


# --- Transition functions ---


async def run_vector_search(
    request: QueryRequest,
    embedding_provider: EmbeddingProvider,
    storage: StorageBackend,
) -> VectorResults:
    """Execute vector search and return intermediate results."""
    node_type = request.node_types[0] if request.node_types and len(request.node_types) == 1 else None

    start = time.monotonic()
    results = await vector_search(
        query_text=request.query_text,
        embedding_provider=embedding_provider,
        storage=storage,
        k=request.k,
        model_id=request.model_id,
        node_type=node_type,
    )
    elapsed_ms = (time.monotonic() - start) * 1000.0

    nodes = [node for node, _score in results]
    scored = [(node.id, score) for node, score in results]

    # If multiple node_types requested, filter after vector search
    if request.node_types and len(request.node_types) > 1:
        type_classes = set()
        for nt in request.node_types:
            if nt.value == "topic":
                type_classes.add(Topic)
            elif nt.value == "fact":
                type_classes.add(Fact)
            elif nt.value == "inference":
                type_classes.add(Inference)
        nodes = [n for n in nodes if type(n) in type_classes]

    return VectorResults(
        request=request,
        scored_nodes=scored,
        nodes=nodes,
        nodes_searched=len(results),
        vector_search_time_ms=elapsed_ms,
    )


async def run_graph_expansion(
    vector_results: VectorResults,
    storage: StorageBackend,
) -> ExpandedResults:
    """Expand the vector search results via graph traversal."""
    request = vector_results.request

    start = time.monotonic()
    expanded_nodes, edges = await expand_via_graph(
        seed_nodes=vector_results.nodes,
        storage=storage,
        hops=request.graph_hops,
    )
    elapsed_ms = (time.monotonic() - start) * 1000.0

    return ExpandedResults(
        request=request,
        nodes=expanded_nodes,
        edges=edges,
        nodes_searched=vector_results.nodes_searched,
        vector_search_time_ms=vector_results.vector_search_time_ms,
        graph_expansion_time_ms=elapsed_ms,
    )


async def build_query_result(
    expanded_results: ExpandedResults,
) -> QueryResult:
    """Assemble the final QueryResult with metadata."""
    nodes = expanded_results.nodes
    edges = expanded_results.edges

    # Compute type breakdown
    source_types: dict[str, int] = {}
    for node in nodes:
        if isinstance(node, Topic):
            key = "topic"
        elif isinstance(node, Fact):
            key = "fact"
        elif isinstance(node, Inference):
            key = "inference"
        else:
            key = "unknown"
        source_types[key] = source_types.get(key, 0) + 1

    metadata = QueryMetadata(
        nodes_searched=expanded_results.nodes_searched,
        nodes_returned=len(nodes),
        graph_hops=expanded_results.request.graph_hops,
        vector_search_time_ms=expanded_results.vector_search_time_ms,
        graph_expansion_time_ms=expanded_results.graph_expansion_time_ms,
        source_types=source_types,
    )

    return QueryResult(
        nodes=nodes,
        edges=edges,
        metadata=metadata,
    )


# --- Petri net factory ---


@petri_net(
    name="hybrid-retrieval",
    mode="batch",
    description="Hybrid retrieval combining vector search with graph expansion.",
)
def hybrid_retrieval_net(
    request: QueryRequest,
    embedding_provider: EmbeddingProvider,
    storage: StorageBackend,
) -> ExecutableGraph:
    """Build a Petri net for hybrid retrieval.

    Args:
        request: The query request to process.
        embedding_provider: Provider for computing query embeddings.
        storage: Storage backend for vector search and graph traversal.

    Returns:
        An ExecutableGraph ready to execute.
    """
    return ExecutableGraphOperations.construct_graph([
        # Places
        ListPlaceNode("QueryRequest", QueryRequest, [request]),
        ListPlaceNode("VectorResults", VectorResults),
        ListPlaceNode("ExpandedResults", ExpandedResults),
        ListPlaceNode("QueryResult", QueryResult),

        # Transition 1: run_vector_search
        FunctionTransitionNode(
            "run_vector_search",
            run_vector_search,
            kwargs={
                "embedding_provider": embedding_provider,
                "storage": storage,
            },
        ),
        ArgumentEdgeToTransition("QueryRequest", "run_vector_search", "request"),
        ReturnedEdgeFromTransition("run_vector_search", "VectorResults"),

        # Transition 2: run_graph_expansion
        FunctionTransitionNode(
            "run_graph_expansion",
            run_graph_expansion,
            kwargs={"storage": storage},
        ),
        ArgumentEdgeToTransition("VectorResults", "run_graph_expansion", "vector_results"),
        ReturnedEdgeFromTransition("run_graph_expansion", "ExpandedResults"),

        # Transition 3: build_query_result
        FunctionTransitionNode("build_query_result", build_query_result),
        ArgumentEdgeToTransition("ExpandedResults", "build_query_result", "expanded_results"),
        ReturnedEdgeFromTransition("build_query_result", "QueryResult"),
    ], expect_acyclic=True)
