"""Core Pydantic models for the epistemic memory system.

These types serve double duty:
1. Storage schema — serialized to/from the database
2. Petri net tokens — flow through processing pipelines
"""

from datetime import datetime, timezone
from enum import Enum
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
    metadata: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_now)


# Union of all epistemic node types
EpistemicNode = Topic | Fact | Inference


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
