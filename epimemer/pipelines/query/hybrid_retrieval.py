"""Hybrid retrieval Petri net for the query layer.

Two independent retrieval arms meeting at a fusion step, then graph expansion:

    [QueryRequest] -> plan_retrieval -> [VectorQuery], [LexicalQuery]
    [VectorQuery]  -> run_vector_search  -> [VectorResults]
    [LexicalQuery] -> run_lexical_search -> [LexicalResults]
    [VectorResults] + [LexicalResults] -> fuse_seeds -> [Seeds]
    [Seeds] -> collapse_lineage -> [CollapsedSeeds]
    [CollapsedSeeds] -> run_graph_expansion -> [ExpandedResults]
    [ExpandedResults] -> build_query_result -> [QueryResult]

"Hybrid" used to mean vector plus graph expansion. It now means what the name
says. The two arms fail in opposite directions — cosine similarity has no notion
of term rarity, BM25 has none of meaning — which is the whole argument for
running both, and neither can change what the other computes.

The lexical arm is a transition rather than a call inside another one on
purpose: the pipeline strip renders net topology, so a fusion hidden inside a
function would make the process invisible in exactly the tool built to show it.

`plan_retrieval` exists because a place's token is consumed by whichever
transition takes it, so two arms cannot both read one place. It also has real
work to do — resolving which terms the lexical arm searches for, and recording
whether the caller declared them, which is what decides whether R2's survival
guarantee applies.

`collapse_lineage` sits between fusion and expansion because that is where the
top-k cut has to be. A retired claim competes with its own replacement for a
slot before anything can fold it into one, so fusion over-fetches and the cut
happens after the fold — and it happens before expansion, so no hop is spent
walking out of a node that was about to be folded away.
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

from epimemer.core.temporal import ValidityVerdict
from epimemer.core.types import (
    EpistemicNode,
    Fact,
    Inference,
    NodeEdge,
    NodeStatus,
    SUPERSEDED_STATUSES,
    Topic,
)
from epimemer.embeddings.protocol import EmbeddingProvider
from epimemer.pipelines.query.fusion import fuse
from epimemer.pipelines.query.graph_expansion import expand_via_graph
from epimemer.pipelines.query.lexical_search import lexical_search, query_terms
from epimemer.pipelines.query.lineage import fold_lineage
from epimemer.pipelines.query.types import (
    QueryMetadata,
    QueryRequest,
    QueryResult,
    SeedProvenance,
    SegmentHit,
)
from epimemer.pipelines.query.validity import validity_for, verdict_for
from epimemer.pipelines.query.vector_search import vector_search
from epimemer.storage.protocol import StorageBackend


# --- Intermediate token types ---


class LexicalQuery(BaseModel):
    """What the lexical arm searches for, and on whose authority.

    `declared` is not a detail: declared terms are a statement of intent and
    earn R2's survival guarantee, while fallback tokens are an inference from
    the query text and earn nothing. Carrying the distinction as data keeps the
    two paths from being told apart by a heuristic further down.
    """
    request: QueryRequest
    terms: list[str] = Field(default_factory=list)
    declared: bool = False


class VectorResults(BaseModel):
    """Intermediate result from vector search, carrying data for the next stage."""
    request: QueryRequest
    scored_nodes: list[tuple[str, float]] = Field(default_factory=list)
    nodes: list[EpistemicNode] = Field(default_factory=list)
    nodes_searched: int = 0
    vector_search_time_ms: float = 0.0


class LexicalResults(BaseModel):
    """Intermediate result from lexical search.

    Ids rather than nodes, and several lists rather than one: BM25 scores are
    per index, so the lists are mutually incomparable and only their ranks may
    be fused. Resolving ids to nodes happens once, after fusion has decided
    which ones survive.
    """
    request: QueryRequest
    rankings: list[list[str]] = Field(default_factory=list)
    protected: list[str] = Field(default_factory=list)
    direct_ids: list[str] = Field(default_factory=list)
    bridged_ids: list[str] = Field(default_factory=list)
    segments: list[SegmentHit] = Field(default_factory=list)
    lexical_search_time_ms: float = 0.0


class Seeds(BaseModel):
    """The fused starting set, before graph expansion."""
    request: QueryRequest
    nodes: list[EpistemicNode] = Field(default_factory=list)
    provenance: dict[str, SeedProvenance] = Field(default_factory=dict)
    segments: list[SegmentHit] = Field(default_factory=list)
    # Ids a declared term's best hit protects (R2), carried past fusion because
    # the top-k cut moves to `collapse_lineage` when history is reachable and
    # the guarantee has to move with it.
    protected: list[str] = Field(default_factory=list)
    # Filled by `collapse_lineage`: retired versions folded into the result that
    # replaced them, keyed by that result's id.
    lineage: dict[str, list[EpistemicNode]] = Field(default_factory=dict)
    nodes_searched: int = 0
    vector_search_time_ms: float = 0.0
    lexical_search_time_ms: float = 0.0


class ExpandedResults(BaseModel):
    """Intermediate result from graph expansion, carrying all data for final assembly."""
    request: QueryRequest
    nodes: list[EpistemicNode] = Field(default_factory=list)
    edges: list[NodeEdge] = Field(default_factory=list)
    provenance: dict[str, SeedProvenance] = Field(default_factory=dict)
    segments: list[SegmentHit] = Field(default_factory=list)
    lineage: dict[str, list[EpistemicNode]] = Field(default_factory=dict)
    nodes_searched: int = 0
    vector_search_time_ms: float = 0.0
    lexical_search_time_ms: float = 0.0
    graph_expansion_time_ms: float = 0.0


# --- Transition functions ---


async def plan_retrieval(request: QueryRequest) -> tuple[QueryRequest, LexicalQuery]:
    """Fork the request into the two arms, resolving the lexical terms.

    Declared terms are taken as given. Omitted, the arm falls back to the
    query's own tokens (R3) — rare ones fire, common ones score zero and are
    dropped before fusion, so no threshold has to be invented.
    """
    declared = bool(request.terms)
    terms = list(request.terms) if declared else query_terms(request.query_text)
    return request, LexicalQuery(request=request, terms=terms, declared=declared)


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
        statuses=request.statuses,
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


async def run_lexical_search(
    lexical_query: LexicalQuery,
    storage: StorageBackend,
) -> LexicalResults:
    """Execute BM25 over node content and segment text."""
    request = lexical_query.request

    start = time.monotonic()
    hits = await lexical_search(
        storage,
        lexical_query.terms,
        k=request.k,
        node_types=request.node_types,
        declared=lexical_query.declared,
        statuses=request.statuses,
    )
    elapsed_ms = (time.monotonic() - start) * 1000.0

    return LexicalResults(
        request=request,
        rankings=hits.rankings,
        protected=hits.protected,
        # Direct and bridged are kept apart so the result can say *the
        # identifier was in the passage, not in the claim*.
        direct_ids=hits.direct_ids,
        bridged_ids=hits.bridged_ids,
        segments=hits.segments,
        lexical_search_time_ms=elapsed_ms,
    )


def seed_provenance(
    node_id: str,
    *,
    direct: set[str],
    bridged: set[str],
) -> SeedProvenance:
    """The label a seed carries, most specific first.

    A node both arms found is reported as `LEXICAL`: an exact token match is the
    rarer and more diagnostic fact, and being a vector hit is the default
    expectation of anything a search returns. `SEGMENT` outranks `VECTOR` for
    the same reason and sits below `LEXICAL` because the identifier was in the
    passage rather than in the claim itself.
    """
    if node_id in direct:
        return SeedProvenance.LEXICAL
    if node_id in bridged:
        return SeedProvenance.SEGMENT
    return SeedProvenance.VECTOR


# How far past `k` fusion reaches when a claim's retired versions can be seeds.
# T3's worked case is a claim with four predecessors filling half a top-10, and
# a factor of three absorbs that with room over. Not unbounded: every extra id
# is a node fetched and an edge lookup, and the point is to protect the top of
# the result rather than to guarantee a full `k` under any amount of history.
_LINEAGE_OVERFETCH = 3


def _fusion_limit(request: QueryRequest) -> int:
    """How many fused ids to keep, before lineage collapse cuts back to `k`.

    Widened only when a retired node can reach the seed set at all. With the
    default `{ACTIVE}` nothing can fold — a predecessor is retired by definition
    — so the over-fetch would buy a longer node fetch and nothing else.
    """
    if request.statuses <= {NodeStatus.ACTIVE}:
        return request.k
    return request.k * _LINEAGE_OVERFETCH


async def fuse_seeds(
    vector_results: VectorResults,
    lexical_results: LexicalResults,
    storage: StorageBackend,
) -> Seeds:
    """Merge the two arms by rank and resolve the survivors to nodes.

    Reciprocal Rank Fusion, because the arms' scores are on incomparable scales
    and any weighted sum of them is a magic number that gets re-tuned forever.
    Declared terms' best hits are carried past the top-k cut (R2).

    Vector results arrive as nodes already; lexical ones arrive as ids, and only
    those that survive fusion are fetched. Ids that no longer resolve are
    dropped rather than reported as gaps — a node deleted between the two halves
    of one search is not a result.

    **The cut is wider than `k` when retired nodes can be seeds**, and
    `collapse_lineage` narrows it back. A claim's retired versions occupy slots
    here — they are ordinary hits until something identifies them as versions of
    one thing — so cutting at `k` first would leave the fold rearranging a
    top-10 that had already lost the results it was meant to save.
    """
    request = vector_results.request
    vector_ranking = [node_id for node_id, _ in vector_results.scored_nodes]

    order = fuse(
        [vector_ranking, *lexical_results.rankings],
        limit=_fusion_limit(request),
        protected=lexical_results.protected,
    )

    known = {node.id: node for node in vector_results.nodes}
    unresolved = [node_id for node_id in order if node_id not in known]
    if unresolved:
        known |= await storage.get_nodes(unresolved)

    direct = set(lexical_results.direct_ids)
    bridged = set(lexical_results.bridged_ids)
    nodes = [known[node_id] for node_id in order if node_id in known]

    return Seeds(
        request=request,
        nodes=nodes,
        provenance={
            node.id: seed_provenance(node.id, direct=direct, bridged=bridged)
            for node in nodes
        },
        segments=lexical_results.segments,
        protected=list(lexical_results.protected),
        nodes_searched=vector_results.nodes_searched,
        vector_search_time_ms=vector_results.vector_search_time_ms,
        lexical_search_time_ms=lexical_results.lexical_search_time_ms,
    )


async def collapse_lineage(seeds: Seeds, storage: StorageBackend) -> Seeds:
    """Fold each matched claim's retired versions into it, then cut to `k`.

    The condition under which returning `HISTORICAL` by default is not a ranking
    regression: a historical claim and its replacement are near-identical text,
    so both score near the top, and without this a claim with four predecessors
    fills half a top-10 with versions of one thing. See `lineage.fold_lineage`
    for the fold rule and why the walk is cycle-safe.

    **A moment asked about protects the claims that answer it.** When the
    request names `valid_as_of`, the seeds' validity is read first and anything
    provably valid then keeps its slot. Otherwise a search for what the city was
    called in 1980 would return the 1991 answer at the top with the 1980 one
    tucked underneath it — the asked-for answer hidden beneath the wrong one.

    That read is per-source and covers seeds only; `search` reads validity again
    for the final node set, which expansion has since added to. It is also
    skipped unless some seed is actually retired — with nothing foldable there
    is nothing for the protection to protect, and a search whose results are all
    current is the common case rather than a corner of it.

    Declared terms' hits are protected from folding as well as from the cut. R2
    promises the caller's own identifier comes back as a result, and a result
    hanging off another node's history is not what was promised.
    """
    request = seeds.request
    unfoldable = list(seeds.protected)
    foldable = any(node.status in SUPERSEDED_STATUSES for node in seeds.nodes)

    if request.valid_as_of is not None and foldable:
        validity = await validity_for([node.id for node in seeds.nodes], storage)
        unfoldable += [
            node.id
            for node in seeds.nodes
            if verdict_for(
                validity.get(node.id, []),
                request.valid_as_of,
                timeline_id=request.timeline_id,
            )
            is ValidityVerdict.VALID
        ]

    kept, lineage = await fold_lineage(seeds.nodes, storage, unfoldable=unfoldable)

    # Same rule as `fuse`'s: the cut takes the best `k`, and a protected id that
    # falls outside extends the result rather than displacing anything.
    protected_ids = set(seeds.protected)
    survivors = kept[: request.k]
    rescued = [node for node in kept[request.k :] if node.id in protected_ids]
    nodes = survivors + rescued

    kept_ids = {node.id for node in nodes}
    return seeds.model_copy(update={
        "nodes": nodes,
        "provenance": {
            node_id: label
            for node_id, label in seeds.provenance.items()
            if node_id in kept_ids
        },
        # A fold whose host did not survive the cut is dropped with it: the
        # history of a claim nobody is being shown is not an answer to anything.
        "lineage": {
            host_id: folded
            for host_id, folded in lineage.items()
            if host_id in kept_ids
        },
    })


async def run_graph_expansion(
    seeds: Seeds,
    storage: StorageBackend,
) -> ExpandedResults:
    """Expand the fused seed set via graph traversal."""
    request = seeds.request

    start = time.monotonic()
    expanded_nodes, edges = await expand_via_graph(
        seed_nodes=seeds.nodes,
        storage=storage,
        hops=request.graph_hops,
    )
    elapsed_ms = (time.monotonic() - start) * 1000.0

    # Everything expansion added arrived by an edge from a seed, not by
    # matching anything — which is the distinction the focus panel exists to
    # draw, and the reason this is an enum rather than a boolean.
    provenance = dict(seeds.provenance)
    for node in expanded_nodes:
        provenance.setdefault(node.id, SeedProvenance.EXPANDED)

    return ExpandedResults(
        request=request,
        nodes=expanded_nodes,
        edges=edges,
        provenance=provenance,
        segments=seeds.segments,
        lineage=seeds.lineage,
        nodes_searched=seeds.nodes_searched,
        vector_search_time_ms=seeds.vector_search_time_ms,
        lexical_search_time_ms=seeds.lexical_search_time_ms,
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
        lexical_search_time_ms=expanded_results.lexical_search_time_ms,
        graph_expansion_time_ms=expanded_results.graph_expansion_time_ms,
        source_types=source_types,
    )

    return QueryResult(
        nodes=nodes,
        edges=edges,
        metadata=metadata,
        provenance={
            node.id: expanded_results.provenance[node.id]
            for node in nodes
            if node.id in expanded_results.provenance
        },
        segments=expanded_results.segments,
        lineage=expanded_results.lineage,
    )


# --- Petri net factory ---


@petri_net(
    name="hybrid-retrieval",
    mode="batch",
    description="Hybrid retrieval fusing vector and lexical search, then graph expansion.",
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
        storage: Storage backend for both searches and graph traversal.

    Returns:
        An ExecutableGraph ready to execute.
    """
    return ExecutableGraphOperations.construct_graph([
        # Places
        ListPlaceNode("QueryRequest", QueryRequest, [request]),
        ListPlaceNode("VectorQuery", QueryRequest),
        ListPlaceNode("LexicalQuery", LexicalQuery),
        ListPlaceNode("VectorResults", VectorResults),
        ListPlaceNode("LexicalResults", LexicalResults),
        ListPlaceNode("Seeds", Seeds),
        ListPlaceNode("CollapsedSeeds", Seeds),
        ListPlaceNode("ExpandedResults", ExpandedResults),
        ListPlaceNode("QueryResult", QueryResult),

        # Transition 0: fork the request into the two arms
        FunctionTransitionNode("plan_retrieval", plan_retrieval),
        ArgumentEdgeToTransition("QueryRequest", "plan_retrieval", "request"),
        ReturnedEdgeFromTransition("plan_retrieval", "VectorQuery", return_index=0),
        ReturnedEdgeFromTransition("plan_retrieval", "LexicalQuery", return_index=1),

        # Transition 1a: run_vector_search
        FunctionTransitionNode(
            "run_vector_search",
            run_vector_search,
            kwargs={
                "embedding_provider": embedding_provider,
                "storage": storage,
            },
        ),
        ArgumentEdgeToTransition("VectorQuery", "run_vector_search", "request"),
        ReturnedEdgeFromTransition("run_vector_search", "VectorResults"),

        # Transition 1b: run_lexical_search
        FunctionTransitionNode(
            "run_lexical_search",
            run_lexical_search,
            kwargs={"storage": storage},
        ),
        ArgumentEdgeToTransition("LexicalQuery", "run_lexical_search", "lexical_query"),
        ReturnedEdgeFromTransition("run_lexical_search", "LexicalResults"),

        # Transition 2: fuse_seeds — the join. Both arms must have finished.
        FunctionTransitionNode(
            "fuse_seeds",
            fuse_seeds,
            kwargs={"storage": storage},
        ),
        ArgumentEdgeToTransition("VectorResults", "fuse_seeds", "vector_results"),
        ArgumentEdgeToTransition("LexicalResults", "fuse_seeds", "lexical_results"),
        ReturnedEdgeFromTransition("fuse_seeds", "Seeds"),

        # Transition 3: collapse_lineage — the top-k cut, after the fold.
        FunctionTransitionNode(
            "collapse_lineage",
            collapse_lineage,
            kwargs={"storage": storage},
        ),
        ArgumentEdgeToTransition("Seeds", "collapse_lineage", "seeds"),
        ReturnedEdgeFromTransition("collapse_lineage", "CollapsedSeeds"),

        # Transition 4: run_graph_expansion
        FunctionTransitionNode(
            "run_graph_expansion",
            run_graph_expansion,
            kwargs={"storage": storage},
        ),
        ArgumentEdgeToTransition("CollapsedSeeds", "run_graph_expansion", "seeds"),
        ReturnedEdgeFromTransition("run_graph_expansion", "ExpandedResults"),

        # Transition 5: build_query_result
        FunctionTransitionNode("build_query_result", build_query_result),
        ArgumentEdgeToTransition("ExpandedResults", "build_query_result", "expanded_results"),
        ReturnedEdgeFromTransition("build_query_result", "QueryResult"),
    ], expect_acyclic=True)
