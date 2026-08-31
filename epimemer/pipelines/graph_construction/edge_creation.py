"""Edge creation Petri net for graph construction.

Given a decomposed segment (segment + its extracted nodes), creates typed
edges between all related nodes.

Edge rules:
- segment -> topic:     EdgeType.ABOUT
- segment -> fact:      EdgeType.CONTAINS
- segment -> inference: EdgeType.IMPLIES
- fact -> topic:        EdgeType.SUPPORTS     (same source segment)
- inference -> topic:   EdgeType.ABSTRACTS    (same source segment)
- fact -> inference:    EdgeType.SUPPORTS     (same source segment)

Petri net flow:
    [DecomposedSegment] -> create_edges -> [list[NodeEdge]]
"""

from petritype import petri_net
from petritype.core.executable_graph_components import (
    ArgumentEdgeToTransition,
    ExecutableGraph,
    ExecutableGraphOperations,
    FunctionTransitionNode,
    ListPlaceNode,
    ReturnedEdgeFromTransition,
)
from pydantic import BaseModel, Field

from epimemer.core.types import (
    EdgeType,
    Fact,
    Inference,
    NodeEdge,
    Segment,
    Topic,
)


class DecomposedSegment(BaseModel):
    """A segment bundled with all nodes extracted from it."""

    segment: Segment
    topics: list[Topic] = Field(default_factory=list)
    facts: list[Fact] = Field(default_factory=list)
    inferences: list[Inference] = Field(default_factory=list)


def create_edges(decomposed: DecomposedSegment) -> list[NodeEdge]:
    """Create all typed edges for a decomposed segment.

    Produces edges between the segment and its extracted nodes,
    and cross-edges between the extracted nodes themselves.
    """
    edges: list[NodeEdge] = []
    segment = decomposed.segment

    # segment -> topic: ABOUT
    for topic in decomposed.topics:
        edges.append(
            NodeEdge(
                src_id=segment.id,
                dst_id=topic.id,
                type=EdgeType.ABOUT,
            )
        )

    # segment -> fact: CONTAINS
    for fact in decomposed.facts:
        edges.append(
            NodeEdge(
                src_id=segment.id,
                dst_id=fact.id,
                type=EdgeType.CONTAINS,
            )
        )

    # segment -> inference: IMPLIES
    for inference in decomposed.inferences:
        edges.append(
            NodeEdge(
                src_id=segment.id,
                dst_id=inference.id,
                type=EdgeType.IMPLIES,
            )
        )

    # fact -> topic: SUPPORTS (same source segment)
    for fact in decomposed.facts:
        for topic in decomposed.topics:
            edges.append(
                NodeEdge(
                    src_id=fact.id,
                    dst_id=topic.id,
                    type=EdgeType.SUPPORTS,
                )
            )

    # inference -> topic: ABSTRACTS (same source segment)
    for inference in decomposed.inferences:
        for topic in decomposed.topics:
            edges.append(
                NodeEdge(
                    src_id=inference.id,
                    dst_id=topic.id,
                    type=EdgeType.ABSTRACTS,
                )
            )

    # fact -> inference: SUPPORTS (same source segment)
    for fact in decomposed.facts:
        for inference in decomposed.inferences:
            edges.append(
                NodeEdge(
                    src_id=fact.id,
                    dst_id=inference.id,
                    type=EdgeType.SUPPORTS,
                )
            )

    return edges


@petri_net(
    name="edge-creation",
    mode="batch",
    description="Create typed edges between nodes extracted from a segment.",
)
def edge_creation_net(
    decomposed: DecomposedSegment,
) -> ExecutableGraph:
    """Build a Petri net for edge creation.

    Args:
        decomposed: A segment bundled with its extracted nodes.

    Returns:
        An ExecutableGraph ready to execute.
    """
    return ExecutableGraphOperations.construct_graph(
        [
            # Places
            ListPlaceNode("DecomposedSegments", DecomposedSegment, [decomposed]),
            ListPlaceNode("Edges", NodeEdge),
            # Transition
            FunctionTransitionNode("create_edges", create_edges),
            ArgumentEdgeToTransition("DecomposedSegments", "create_edges", "decomposed"),
            ReturnedEdgeFromTransition("create_edges", "Edges"),
        ],
        expect_acyclic=True,
    )
