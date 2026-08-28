"""Query-related Pydantic models.

These types define the input, output, and metadata for the query pipeline.
They serve as Petri net tokens flowing through the hybrid retrieval net.
"""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from epimemer.core.types import EpistemicNode, NodeEdge, NodeStatus, NodeType


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
    DIRECT = "direct"      # returned unranked: find_nodes, graph_as_of, topic_tree, …


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
    """Input to the query pipeline.

    Two clocks reach this type and they are named apart, on the validity model's
    rule: the
    unmarked name inherits the default reading, and in a knowledge graph the
    default reading of "as of 1980" is *what was true in 1980* — which is the
    valid-time axis. So transaction time is marked too.
    """
    query_text: str
    k: int = 10
    node_types: list[NodeType] | None = None  # filter by type, None = all
    # Transaction time: the graph as it stood then. Not read by the net — the
    # tool that answers this question is `graph_as_of`, which asks storage
    # directly — and kept because it names the axis the field below is not.
    graph_at_time: datetime | None = None
    graph_hops: int = 1  # how many hops for graph expansion
    model_id: str | None = None  # embedding model to use
    # Terms the caller declares load-bearing — identifiers, names, exact
    # phrases. Declared terms are authoritative: each one's best hit survives
    # the top-k cut (R2). Omitted, the lexical arm falls back to the query's own
    # tokens with no such protection (R3).
    terms: list[str] | None = None
    # Which node statuses either seed route may return (T3's reachability). Both
    # arms are asked with this one set, so they cannot disagree about whether a
    # node exists. Built by `reachable_statuses`; the bare default is what a
    # caller gets who never heard of history.
    statuses: frozenset[NodeStatus] = frozenset({NodeStatus.ACTIVE})
    # Valid time: when the claim was true. Supplied, it labels each result
    # `valid` or `unknown` and stops a claim provably valid then from being
    # folded into a later version of itself. Never defaulted to the wall clock —
    # "current" is the *timeline's* reference time, not the viewer's (T3).
    valid_as_of: datetime | None = None
    # The clock `valid_as_of` is read against. `None` is the wall-clock
    # timeline, which is what real-world facts use.
    timeline_id: str | None = None


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
    # Retired versions folded into the result that superseded them, keyed by
    # that result's id and ordered as they ranked. They matched on their own
    # merits and gave up their slot rather than their place in the answer —
    # which is what stops one claim's history from filling a top-10 (T3).
    lineage: dict[str, list[EpistemicNode]] = Field(default_factory=dict)
