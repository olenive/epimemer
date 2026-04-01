"""Query-related Pydantic models.

These types define the input, output, and metadata for the query pipeline.
They serve as Petri net tokens flowing through the hybrid retrieval net.
"""

from datetime import datetime

from pydantic import BaseModel, Field

from epimemer.core.types import EpistemicNode, NodeEdge, NodeType


class QueryRequest(BaseModel):
    """Input to the query pipeline."""
    query_text: str
    k: int = 10
    node_types: list[NodeType] | None = None  # filter by type, None = all
    at_time: datetime | None = None  # temporal query
    graph_hops: int = 1  # how many hops for graph expansion
    model_id: str | None = None  # embedding model to use


class QueryMetadata(BaseModel):
    """Metadata about the query execution for logging/feedback."""
    nodes_searched: int
    nodes_returned: int
    graph_hops: int
    vector_search_time_ms: float
    graph_expansion_time_ms: float
    source_types: dict[str, int] = Field(default_factory=dict)  # e.g. {"topic": 2, "fact": 3}


class QueryResult(BaseModel):
    """Output from the query pipeline."""
    nodes: list[EpistemicNode]
    edges: list[NodeEdge]  # edges between returned nodes
    metadata: QueryMetadata
