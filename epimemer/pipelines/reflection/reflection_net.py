"""Reflection Petri net orchestrating the full reflection pipeline.

Pipeline ordering (sequential where dependencies exist, parallel where independent):

    [ReflectionRequest]
        → run_topic_split → [SplitResult], [PostSplitRequest]
        → run_topic_consolidation → [ConsolidationResult], [PostConsolidationRequest]
        → broadcast_parallel_ops → [DecayInput], [EnrichmentInput], [ContradictionInput]
            → run_value_decay → [DecayResult]
            → run_topic_enrichment → [EnrichmentResult]
            → run_contradiction_detection → [ContradictionResult]

Split must run before consolidation (to decompose broad topics first).
Consolidation must run before enrichment (to settle the topic structure).
Decay and contradiction detection are independent of enrichment.
"""

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

from epimemer.core.types import Topic
from epimemer.embeddings.protocol import EmbeddingProvider
from epimemer.llm.protocol import DecompositionProvider
from epimemer.pipelines.reflection.contradiction_detection import detect_contradictions
from epimemer.pipelines.reflection.topic_consolidation import (
    consolidate_as_hierarchy,
    find_similar_topic_pairs,
    merge_similar_topics,
)
from epimemer.pipelines.reflection.topic_enrichment import enrich_stale_topics
from epimemer.pipelines.reflection.topic_splitting import split_broad_topics
from epimemer.pipelines.reflection.value_decay import apply_decay
from epimemer.storage.protocol import StorageBackend


# --- Token types ---


class ReflectionRequest(BaseModel):
    """Configuration for a reflection pass."""
    similarity_threshold: float = Field(default=0.85)
    contradiction_threshold: float = Field(default=0.80)
    decay_rate: float = Field(default=0.05)
    min_relevance: float = Field(default=0.01)
    material_ratio: float = Field(default=3.0)
    split_ratio: float = Field(default=1.5)
    min_split_items: int = Field(default=4)
    auto_merge: bool = Field(default=True)
    hierarchical: bool = Field(default=True)
    model_id: str | None = Field(default=None)


class SplitResult(BaseModel):
    """Result of topic splitting."""
    topics_checked: int = 0
    topics_split: int = 0
    subtopics_created: list[Topic] = Field(default_factory=list)


class ConsolidationResult(BaseModel):
    """Result of topic consolidation."""
    pairs_found: int = 0
    topics_merged: int = 0
    merged_topics: list[Topic] = Field(default_factory=list)
    parent_topics_created: list[Topic] = Field(default_factory=list)


class EnrichmentResult(BaseModel):
    """Result of topic enrichment."""
    topics_enriched: int = 0
    enriched_topics: list[Topic] = Field(default_factory=list)


class DecayResult(BaseModel):
    """Result of value decay pass."""
    nodes_decayed: int = 0


class ContradictionResult(BaseModel):
    """Result of contradiction detection."""
    pairs_found: int = 0
    contradiction_pairs: list[tuple[str, str, float]] = Field(default_factory=list)


# --- Intermediate types for chaining phases ---


class SplitPhaseOutput(BaseModel):
    """Split result + forwarded request for the next phase."""
    result: SplitResult
    request: ReflectionRequest


class ConsolidationPhaseOutput(BaseModel):
    """Consolidation result + forwarded request for the next phase."""
    result: ConsolidationResult
    request: ReflectionRequest


class ParallelBroadcastResult(BaseModel):
    """Fan-out to three parallel operations."""
    decay_input: ReflectionRequest
    enrichment_input: ReflectionRequest
    contradiction_input: ReflectionRequest


# --- Transition functions ---


async def run_topic_split(
    request: ReflectionRequest,
    storage: StorageBackend,
    embedding_provider: EmbeddingProvider,
    decomposition_provider: DecompositionProvider | None,
) -> SplitPhaseOutput:
    """Check topics for high internal variance and split where needed."""
    if decomposition_provider is None:
        result = SplitResult(topics_checked=0, topics_split=0)
        return SplitPhaseOutput(result=result, request=request)

    splits = await split_broad_topics(
        storage,
        embedding_provider,
        decomposition_provider,
        split_ratio=request.split_ratio,
        min_items=request.min_split_items,
        model_id=request.model_id,
    )

    all_subtopics = [st for _parent, subtopics in splits for st in subtopics]
    result = SplitResult(
        topics_checked=0,  # could count all topics checked, but not critical
        topics_split=len(splits),
        subtopics_created=all_subtopics,
    )
    return SplitPhaseOutput(result=result, request=request)


def distribute_split_output(output: SplitPhaseOutput) -> dict[str, SplitResult | ReflectionRequest]:
    return {
        "SplitResult": output.result,
        "PostSplitRequest": output.request,
    }


async def run_topic_consolidation(
    request: ReflectionRequest,
    storage: StorageBackend,
    embedding_provider: EmbeddingProvider,
    decomposition_provider: DecompositionProvider | None,
) -> ConsolidationPhaseOutput:
    """Find and group/merge similar topic pairs."""
    if request.hierarchical and decomposition_provider is not None:
        parent_topics = await consolidate_as_hierarchy(
            storage,
            embedding_provider,
            decomposition_provider,
            similarity_threshold=request.similarity_threshold,
            model_id=request.model_id,
        )
        result = ConsolidationResult(
            pairs_found=len(parent_topics),
            parent_topics_created=parent_topics,
        )
        return ConsolidationPhaseOutput(result=result, request=request)

    # Flat merge (legacy behavior)
    pairs = await find_similar_topic_pairs(
        storage,
        embedding_provider,
        similarity_threshold=request.similarity_threshold,
        model_id=request.model_id,
    )

    merged_topics: list[Topic] = []
    if request.auto_merge:
        for topic_a, topic_b, _score in pairs:
            merged = await merge_similar_topics(topic_a, topic_b, storage)
            merged_topics.append(merged)

    result = ConsolidationResult(
        pairs_found=len(pairs),
        topics_merged=len(merged_topics),
        merged_topics=merged_topics,
    )
    return ConsolidationPhaseOutput(result=result, request=request)


def distribute_consolidation_output(
    output: ConsolidationPhaseOutput,
) -> dict[str, ConsolidationResult | ReflectionRequest]:
    return {
        "ConsolidationResult": output.result,
        "PostConsolidationRequest": output.request,
    }


def broadcast_parallel_ops(
    request: ReflectionRequest,
) -> ParallelBroadcastResult:
    """Fan out request to the three independent parallel operations."""
    return ParallelBroadcastResult(
        decay_input=request.model_copy(),
        enrichment_input=request.model_copy(),
        contradiction_input=request.model_copy(),
    )


def distribute_parallel_broadcast(
    result: ParallelBroadcastResult,
) -> dict[str, ReflectionRequest]:
    return {
        "DecayInput": result.decay_input,
        "EnrichmentInput": result.enrichment_input,
        "ContradictionInput": result.contradiction_input,
    }


async def run_value_decay(
    request: ReflectionRequest,
    storage: StorageBackend,
) -> DecayResult:
    """Apply relevance decay to all active nodes."""
    count = await apply_decay(
        storage,
        decay_rate=request.decay_rate,
        min_relevance=request.min_relevance,
    )
    return DecayResult(nodes_decayed=count)


async def run_topic_enrichment(
    request: ReflectionRequest,
    storage: StorageBackend,
    embedding_provider: EmbeddingProvider,
    decomposition_provider: DecompositionProvider | None,
) -> EnrichmentResult:
    """Enrich topics whose material has outgrown their descriptions."""
    if decomposition_provider is None:
        return EnrichmentResult()
    pairs = await enrich_stale_topics(
        storage,
        embedding_provider,
        decomposition_provider,
        material_ratio=request.material_ratio,
        model_id=request.model_id,
    )
    return EnrichmentResult(
        topics_enriched=len(pairs),
        enriched_topics=[new for _old, new in pairs],
    )


async def run_contradiction_detection(
    request: ReflectionRequest,
    storage: StorageBackend,
    embedding_provider: EmbeddingProvider,
) -> ContradictionResult:
    """Detect potential contradictions among active facts."""
    pairs = await detect_contradictions(
        storage,
        embedding_provider,
        similarity_threshold=request.contradiction_threshold,
        model_id=request.model_id,
    )

    contradiction_pairs = [(a.id, b.id, score) for a, b, score in pairs]

    return ContradictionResult(
        pairs_found=len(pairs),
        contradiction_pairs=contradiction_pairs,
    )


# --- Petri net factory ---


@petri_net(
    name="reflection",
    mode="batch",
    description=(
        "Full reflection pipeline: split → consolidate → "
        "(decay | enrich | contradiction detection)."
    ),
)
def reflection_net(
    request: ReflectionRequest,
    storage: StorageBackend,
    embedding_provider: EmbeddingProvider,
    decomposition_provider: DecompositionProvider | None = None,
) -> ExecutableGraph:
    """Build a Petri net for the reflection pipeline.

    Sequential phases:
        1. Topic splitting (decompose broad topics)
        2. Topic consolidation (group similar topics under parents)

    Parallel phase (after structure is settled):
        3a. Value decay
        3b. Topic enrichment
        3c. Contradiction detection

    Args:
        request: Configuration for the reflection pass.
        storage: The storage backend.
        embedding_provider: Provider for embedding lookups.
        decomposition_provider: LLM provider for synthesis/enrichment.
            Optional for backwards compatibility; required for split/consolidate/enrich.

    Returns:
        An ExecutableGraph ready to execute.
    """
    shared_kwargs = {
        "storage": storage,
        "embedding_provider": embedding_provider,
    }
    llm_kwargs = {
        **shared_kwargs,
        "decomposition_provider": decomposition_provider,
    }

    return ExecutableGraphOperations.construct_graph([
        # === Input ===
        ListPlaceNode("ReflectionRequest", ReflectionRequest, [request]),

        # === Phase 1: Split (sequential) ===
        ListPlaceNode("SplitResult", SplitResult),
        ListPlaceNode("PostSplitRequest", ReflectionRequest),

        FunctionTransitionNode(
            "run_topic_split",
            run_topic_split,
            kwargs=llm_kwargs,
            output_distribution_function=distribute_split_output,
        ),
        ArgumentEdgeToTransition("ReflectionRequest", "run_topic_split", "request"),
        ReturnedEdgeFromTransition("run_topic_split", "SplitResult"),
        ReturnedEdgeFromTransition("run_topic_split", "PostSplitRequest"),

        # === Phase 2: Consolidation (sequential, after split) ===
        ListPlaceNode("ConsolidationResult", ConsolidationResult),
        ListPlaceNode("PostConsolidationRequest", ReflectionRequest),

        FunctionTransitionNode(
            "run_topic_consolidation",
            run_topic_consolidation,
            kwargs=llm_kwargs,
            output_distribution_function=distribute_consolidation_output,
        ),
        ArgumentEdgeToTransition(
            "PostSplitRequest", "run_topic_consolidation", "request"
        ),
        ReturnedEdgeFromTransition("run_topic_consolidation", "ConsolidationResult"),
        ReturnedEdgeFromTransition(
            "run_topic_consolidation", "PostConsolidationRequest"
        ),

        # === Phase 3: Parallel operations (after consolidation) ===
        ListPlaceNode("DecayInput", ReflectionRequest),
        ListPlaceNode("EnrichmentInput", ReflectionRequest),
        ListPlaceNode("ContradictionInput", ReflectionRequest),

        ListPlaceNode("DecayResult", DecayResult),
        ListPlaceNode("EnrichmentResult", EnrichmentResult),
        ListPlaceNode("ContradictionResult", ContradictionResult),

        # Broadcast to three parallel paths
        FunctionTransitionNode(
            "broadcast_parallel_ops",
            broadcast_parallel_ops,
            output_distribution_function=distribute_parallel_broadcast,
        ),
        ArgumentEdgeToTransition(
            "PostConsolidationRequest", "broadcast_parallel_ops", "request"
        ),
        ReturnedEdgeFromTransition("broadcast_parallel_ops", "DecayInput"),
        ReturnedEdgeFromTransition("broadcast_parallel_ops", "EnrichmentInput"),
        ReturnedEdgeFromTransition("broadcast_parallel_ops", "ContradictionInput"),

        # 3a: Value decay
        FunctionTransitionNode(
            "run_value_decay",
            run_value_decay,
            kwargs={"storage": storage},
        ),
        ArgumentEdgeToTransition("DecayInput", "run_value_decay", "request"),
        ReturnedEdgeFromTransition("run_value_decay", "DecayResult"),

        # 3b: Topic enrichment
        FunctionTransitionNode(
            "run_topic_enrichment",
            run_topic_enrichment,
            kwargs=llm_kwargs,
        ),
        ArgumentEdgeToTransition(
            "EnrichmentInput", "run_topic_enrichment", "request"
        ),
        ReturnedEdgeFromTransition("run_topic_enrichment", "EnrichmentResult"),

        # 3c: Contradiction detection
        FunctionTransitionNode(
            "run_contradiction_detection",
            run_contradiction_detection,
            kwargs=shared_kwargs,
        ),
        ArgumentEdgeToTransition(
            "ContradictionInput", "run_contradiction_detection", "request"
        ),
        ReturnedEdgeFromTransition(
            "run_contradiction_detection", "ContradictionResult"
        ),
    ])
