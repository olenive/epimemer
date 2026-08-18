"""Query-related Pydantic models.

These types define the input, output, and metadata for the query pipeline.
They serve as Petri net tokens flowing through the hybrid retrieval net.
"""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from epimemer.core.types import EpistemicNode, NodeEdge, NodeType


class SeedProvenance(StrEnum):
    """How a returned node was reached.

    Flattening this away would throw out the most useful thing hybrid retrieval
    produces. *This matched at 0.82; that one was dragged in by an edge from it;
    this third one came back on an exact token match* is the question actually
    being asked when a search disappoints, and a boolean "retrieved" cannot
    answer it.

    A node reached by more than one route gets the **most specific** label, in
    the order below: an exact token match is a rarer and more diagnostic fact
    about a result than similarity, which is the default expectation. So a node
    both arms found is reported as `LEXICAL`.

    `DIRECT` is the fifth member `dev-docs/RETRIEVAL_PROVENANCE.md` §3 reserved
    room for: tools that return nodes without ranking them at all. It never
    appears on a `search` result — the query pipeline always knows which arm
    reached a node — and it carries no score, because showing a blank is honest
    where showing 1.0 would be a lie (§9).
    """
    LEXICAL = "lexical"    # BM25 on node content
    SEGMENT = "segment"    # BM25 on a segment, bridged via source_id
    VECTOR = "vector"      # embedding similarity
    EXPANDED = "expanded"  # pulled in by graph expansion from a seed
    DIRECT = "direct"      # returned unranked: find_nodes, as_of, topic_tree, …


class SegmentHit(BaseModel):
    """A passage the lexical arm matched, reported in its own right.

    A segment is not a graph node and must not be pretended into one. It answers
    a different question — *where did I read that?* rather than *what do I
    believe?* — and it is the half of lexical search that survives an agent
    paraphrasing the identifier out of the fact it wrote.
    """
    segment_id: str
    text: str
    document_id: str
    score: float


class QueryRequest(BaseModel):
    """Input to the query pipeline."""
    query_text: str
    k: int = 10
    node_types: list[NodeType] | None = None  # filter by type, None = all
    at_time: datetime | None = None  # temporal query
    graph_hops: int = 1  # how many hops for graph expansion
    model_id: str | None = None  # embedding model to use
    # Terms the caller declares load-bearing — identifiers, names, exact
    # phrases. Declared terms are authoritative: each one's best hit survives
    # the top-k cut (R2). Omitted, the lexical arm falls back to the query's own
    # tokens with no such protection (R3).
    terms: list[str] | None = None


class QueryMetadata(BaseModel):
    """Metadata about the query execution for logging/feedback."""
    # How deep the *vector* scan reached, not how many nodes either arm looked
    # at. Deliberately unchanged by the lexical arm: frame-scoped search grows
    # its fetch until this number stops rising, which is a statement about the
    # embedding store running out of candidates and would mean nothing if a
    # second arm's hits were added to it.
    nodes_searched: int
    nodes_returned: int
    graph_hops: int
    vector_search_time_ms: float
    graph_expansion_time_ms: float
    lexical_search_time_ms: float = 0.0
    source_types: dict[str, int] = Field(default_factory=dict)  # e.g. {"topic": 2, "fact": 3}


class QueryResult(BaseModel):
    """Output from the query pipeline."""
    nodes: list[EpistemicNode]
    edges: list[NodeEdge]  # edges between returned nodes
    metadata: QueryMetadata
    # How each returned node was reached, keyed by node id.
    provenance: dict[str, SeedProvenance] = Field(default_factory=dict)
    # Passages the lexical arm matched, whether or not they bridged to a node.
    segments: list[SegmentHit] = Field(default_factory=list)
