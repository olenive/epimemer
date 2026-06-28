"""Core Pydantic models for the epistemic memory system.

These types serve double duty:
1. Storage schema — serialized to/from the database
2. Petri net tokens — flow through processing pipelines
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return str(uuid4())


# --- Enums ---


class NodeType(str, Enum):
    TOPIC = "topic"
    FACT = "fact"
    INFERENCE = "inference"


class NodeStatus(str, Enum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    MERGED = "merged"


class EdgeType(str, Enum):
    # Segment anchoring
    ABOUT = "about"                  # segment → topic
    CONTAINS = "contains"            # segment → fact
    IMPLIES = "implies"              # segment → inference

    # Semantic hierarchy
    SUPPORTS = "supports"            # fact → topic, fact → inference
    ABSTRACTS = "abstracts"          # inference → topic
    DERIVED_FROM = "derived_from"    # inference → fact

    # Cross-node linking
    SIMILARITY = "similarity"        # topic ↔ topic, fact ↔ fact
    CONTRADICTION = "contradiction"  # fact ↔ fact

    # Topic hierarchy (DAG — multiple parents allowed, cycles forbidden)
    SUBTOPIC_OF = "subtopic_of"      # topic → parent topic

    # History
    SUPERSEDED_BY = "superseded_by"  # node → node (update)
    MERGED_INTO = "merged_into"      # node → node (merge)

    # Temporal
    TIMELINK = "timelink"                        # node → timeline (with timepoint_id in metadata)
    ASSOCIATED_TIMELINE = "associated_timeline"  # topic → timeline

    # Epistemic framing
    HAS_METACONTEXT = "has_metacontext"          # node → metacontext

    # Epistemic review (see REVIEW_EPISTEMIC.md)
    SUPERSESSION_CANDIDATE = "supersession_candidate"  # newer fact → older fact
    EVIDENCE_SUPERSEDED = "evidence_superseded"        # superseded fact → dependent inference
    VARIANT_OF = "variant_of"                          # fact ↔ fact, across frames
    BASED_ON = "based_on"                              # metacontext → metacontext (association)


# Edges that record version history rather than knowledge. They are anchored to
# a specific node version and are excluded from edge migration on supersession /
# merge, and from default graph traversal.
HISTORY_EDGE_TYPES: frozenset[EdgeType] = frozenset(
    {EdgeType.SUPERSEDED_BY, EdgeType.MERGED_INTO}
)

# Edges that flag a node for epistemic review. They are computed into retrieval
# labels (superseded_candidate / evidence_stale) rather than traversed as
# knowledge, and are anchored to a node version (not migrated on supersession).
REVIEW_EDGE_TYPES: frozenset[EdgeType] = frozenset(
    {EdgeType.SUPERSESSION_CANDIDATE, EdgeType.EVIDENCE_SUPERSEDED}
)

# Metadata / signal edges (history + review): excluded from edge migration on
# supersession/merge and from default graph traversal. Knowledge relationships
# such as `contradiction` and `variant_of` are NOT in this set — they are real
# edges to follow.
NON_KNOWLEDGE_EDGE_TYPES: frozenset[EdgeType] = HISTORY_EDGE_TYPES | REVIEW_EDGE_TYPES

# Reserved id for the canonical base-reality frame ("The Real"). Matched by id,
# never by content, so a fiction frame that internally mentions "reality" is
# never confused with it. Untagged nodes are implicitly in this frame.
BASE_METACONTEXT_ID = "the-real"


# --- Value Signal ---


class ValueSignal(BaseModel):
    """Multi-dimensional value signal attached to every node."""
    novelty: float = Field(default=1.0, ge=0.0, le=1.0)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    relevance: float = Field(default=0.5, ge=0.0, le=1.0)
    last_reinforced: datetime = Field(default_factory=_now)


# --- Documents and Segments ---


class RawDocument(BaseModel):
    """Input text before any processing."""
    id: str = Field(default_factory=_new_id)
    content: str
    source: str | None = None         # human-meaningful origin, e.g. "ISSUES.md"
    source_type: str | None = None    # free string; suggested: document|api|chat
    metadata: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_now)


class Segment(BaseModel):
    """A non-overlapping section of a document aligned to semantic boundaries."""
    id: str = Field(default_factory=_new_id)
    source_id: str                    # RawDocument.id
    text: str
    span_start: int                   # character offset in source
    span_end: int                     # character offset in source
    metadata: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_now)


# --- Provenance & tags ---


class Provenance(BaseModel):
    """Where a piece of knowledge came from.

    System-stamped on every node at ingest and queryable, so "which nodes came
    from X" is answerable. Lightly structured (system-owned keys, free values).
    A node carries a list: a fresh node has one entry; a merged node carries the
    union of its sources'.
    """
    source: str                       # "ISSUES.md", "stripe-api", "chat#4012"
    source_type: str = "document"     # free string; suggested: document|api|chat
    source_id: str | None = None      # RawDocument id (or external id) if applicable
    ingested_at: datetime = Field(default_factory=_now)
    metadata: dict = Field(default_factory=dict)   # url, line range, author, ...


class Tag(BaseModel):
    """A free-text label for filtering. Optional `key` gives a dimension.

    No controlled vocabulary: keys and values are both free. Dimensioned tags
    (key set) support "filter by key" queries; bare tags (key None) are casual
    labels. Sprawl is handled by later consolidation, not upfront rules.
    """
    key: str | None = None
    value: str


# --- Epistemic Nodes ---


class Topic(BaseModel):
    """Paragraph-length semantic summary of a theme.

    Acts as a soft ontological node — embeds well, supports clustering,
    and can evolve over time.
    """
    id: str = Field(default_factory=_new_id)
    content: str                      # paragraph-level description
    source_id: str                    # Segment.id that generated this
    status: NodeStatus = NodeStatus.ACTIVE
    superseded_at: datetime | None = None
    value: ValueSignal = Field(default_factory=ValueSignal)
    extraction_method: str = "llm"
    provenance: list[Provenance] = Field(default_factory=list)
    tags: list[Tag] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_now)


class Fact(BaseModel):
    """Atomic, verifiable, grounded statement.

    Tied to source material with minimal ambiguity.
    """
    id: str = Field(default_factory=_new_id)
    content: str
    source_id: str                    # Segment.id that generated this
    status: NodeStatus = NodeStatus.ACTIVE
    superseded_at: datetime | None = None
    value: ValueSignal = Field(default_factory=ValueSignal)
    extraction_method: str = "llm"
    provenance: list[Provenance] = Field(default_factory=list)
    tags: list[Tag] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_now)


class Inference(BaseModel):
    """Higher-level interpretive derivation from facts and context.

    Explicitly provisional and revisable. Multiple competing inferences
    from the same evidence are permitted to coexist.
    """
    id: str = Field(default_factory=_new_id)
    content: str
    source_id: str                    # Segment.id that generated this
    status: NodeStatus = NodeStatus.ACTIVE
    superseded_at: datetime | None = None
    value: ValueSignal = Field(default_factory=ValueSignal)
    extraction_method: str = "llm"
    provenance: list[Provenance] = Field(default_factory=list)
    tags: list[Tag] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_now)


# Union of all epistemic node types
EpistemicNode = Topic | Fact | Inference


class NodeChangeEvent(BaseModel):
    """A lifecycle event on a node that falls inside a queried time window.

    Emitted by temporal change queries: `created` when the node was born in the
    window; `superseded`/`merged` when the node was retired in the window (the
    kind mirrors the node's terminal status). A node both born and retired inside
    one window yields two events.
    """
    kind: Literal["created", "superseded", "merged"]
    at: datetime


# --- Tag / provenance parsing + matching ---


def union_unique(*lists):
    """Concatenate lists, dropping later duplicates (by value), order-preserving.

    Used to carry provenance/tags forward across supersession, merge, and
    parent/split derivation so a node keeps its predecessors' sources and labels.
    """
    out: list = []
    for items in lists:
        for it in items:
            if it not in out:
                out.append(it)
    return out


def parse_tag(spec: str) -> Tag:
    """Build a Tag from "key=value" (split on first '=') or bare "value"."""
    if "=" in spec:
        k, v = spec.split("=", 1)
        return Tag(key=k or None, value=v)
    return Tag(value=spec)


def _parse_tag_filter(spec: str) -> tuple[str | None, str | None]:
    """Parse a tag filter into (key, value) criteria, where None means "any".

    "key=value" -> (key, value); "key=" -> (key, None); bare "value" -> (None, value).
    """
    if "=" in spec:
        k, v = spec.split("=", 1)
        return (k or None, v or None)
    return (None, spec)


def tag_satisfies(tag: Tag, spec: str) -> bool:
    """True when a single tag matches a filter spec (key=value / key= / bare value)."""
    k, v = _parse_tag_filter(spec)
    return (k is None or tag.key == k) and (v is None or tag.value == v)


def tag_matches(node: EpistemicNode, filters: list[str]) -> bool:
    """True when the node's tags satisfy every filter spec (AND)."""
    return all(
        any(tag_satisfies(t, spec) for t in node.tags) for spec in filters
    )


def provenance_matches(
    node: EpistemicNode,
    *,
    source: str | None = None,
    source_type: str | None = None,
) -> bool:
    """True when the node has a provenance entry matching the given criteria."""
    if source is not None and not any(p.source == source for p in node.provenance):
        return False
    if source_type is not None and not any(
        p.source_type == source_type for p in node.provenance
    ):
        return False
    return True


# --- Edges ---


class NodeEdge(BaseModel):
    """A typed, weighted, directed edge between two nodes."""
    id: str = Field(default_factory=_new_id)
    src_id: str
    dst_id: str
    type: EdgeType
    weight: float = Field(default=1.0, ge=0.0)
    metadata: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_now)


# --- Embeddings ---


class EmbeddingRecord(BaseModel):
    """An embedding vector associated with a specific item and model."""
    id: str = Field(default_factory=_new_id)
    item_id: str                      # node or segment id
    model_id: str                     # e.g. "all-mpnet-base-v2"
    vector: list[float]
    created_at: datetime = Field(default_factory=_now)


# --- Timelines ---


class Timepoint(BaseModel):
    """A point or interval on a timeline.

    Can be concrete (with start/end datetimes), vague (label only),
    or a mix (concrete start with descriptive label).
    """
    id: str = Field(default_factory=_new_id)
    start: datetime | None = None     # concrete start (optional)
    end: datetime | None = None       # concrete end (optional, for intervals)
    label: str | None = None          # free-text (e.g., "during the Renaissance")
    metadata: dict = Field(default_factory=dict)


class Timeline(BaseModel):
    """An ordered container of timepoints.

    Timepoints are embedded within the timeline (not separate graph nodes).
    Other nodes link to specific timepoints via TIMELINK edges that carry
    a timepoint_id in metadata.
    """
    id: str = Field(default_factory=_new_id)
    name: str
    description: str = ""
    timepoints: list[Timepoint] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_now)


# --- Metacontext ---


class Metacontext(BaseModel):
    """Epistemic frame for disambiguation.

    Metacontexts distinguish different takes, sources, or interpretations
    of the same information. For example, "Real historical events" vs.
    "World of Darkness fictional universe" vs. "Reporting by the BBC".

    Has value signals and status like epistemic nodes — supports
    consolidation/merge during reflection.
    """
    id: str = Field(default_factory=_new_id)
    content: str                      # e.g., "Real historical events"
    description: str = ""             # longer explanation
    status: NodeStatus = NodeStatus.ACTIVE
    superseded_at: datetime | None = None
    value: ValueSignal = Field(default_factory=ValueSignal)
    metadata: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_now)
