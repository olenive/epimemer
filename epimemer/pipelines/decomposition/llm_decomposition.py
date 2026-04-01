"""LLM-based decomposition Petri net.

Extracts Topics, Facts, and Inferences from a Segment using the
DecompositionProvider (LLM abstraction).

Petri net flow:
    [Segment] -> extract_all -> [Topics], [Facts], [Inferences]

A single transition calls all three extraction methods and uses an
output_distribution_function to route each result type to its correct place.
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

from epimemer.core.types import Fact, Inference, Segment, Topic
from epimemer.llm.protocol import DecompositionProvider


# --- Intermediate result type ---


class DecompositionResult(BaseModel):
    """Holds the combined output of all three extraction methods."""
    topics: list[Topic] = Field(default_factory=list)
    facts: list[Fact] = Field(default_factory=list)
    inferences: list[Inference] = Field(default_factory=list)


# --- Transition function ---


async def extract_all(
    segment: Segment,
    decomposition_provider: DecompositionProvider,
) -> DecompositionResult:
    """Extract topics, facts, and inferences from a segment.

    Calls all three DecompositionProvider methods and bundles the results
    into a single DecompositionResult for distribution to typed places.
    """
    topics = await decomposition_provider.extract_topics(segment)
    facts = await decomposition_provider.extract_facts(segment)
    inferences = await decomposition_provider.extract_inferences(segment)
    return DecompositionResult(
        topics=topics,
        facts=facts,
        inferences=inferences,
    )


# --- Output distribution function ---


def distribute_decomposition_result(
    result: DecompositionResult,
) -> dict[str, list]:
    """Route decomposition results to their respective typed places."""
    return {
        "Topics": result.topics,
        "Facts": result.facts,
        "Inferences": result.inferences,
    }


# --- Petri net factory ---


@petri_net(
    name="llm-decomposition",
    mode="batch",
    description="Extract Topics, Facts, and Inferences from a Segment via LLM.",
)
def llm_decomposition_net(
    segment: Segment,
    decomposition_provider: DecompositionProvider,
) -> ExecutableGraph:
    """Build a Petri net for LLM-based segment decomposition.

    Args:
        segment: The segment to decompose.
        decomposition_provider: LLM provider for extraction.

    Returns:
        An ExecutableGraph ready to execute.
    """
    return ExecutableGraphOperations.construct_graph([
        # Places
        ListPlaceNode("Segments", Segment, [segment]),
        ListPlaceNode("Topics", Topic),
        ListPlaceNode("Facts", Fact),
        ListPlaceNode("Inferences", Inference),

        # Transition: extract_all with output distribution
        FunctionTransitionNode(
            "extract_all",
            extract_all,
            kwargs={"decomposition_provider": decomposition_provider},
            output_distribution_function=distribute_decomposition_result,
        ),
        ArgumentEdgeToTransition("Segments", "extract_all", "segment"),
        ReturnedEdgeFromTransition("extract_all", "Topics"),
        ReturnedEdgeFromTransition("extract_all", "Facts"),
        ReturnedEdgeFromTransition("extract_all", "Inferences"),
    ])
