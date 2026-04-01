"""Event types for real-time visualization.

Two categories of events:

1. Graph events — mutations to the knowledge graph (nodes, edges, embeddings)
2. Pipeline events — Petri net execution state (transitions firing, tokens moving)

All events are Pydantic models that serialize cleanly to JSON for WebSocket transport.
The event schema is the contract between producers (storage, pipeline) and consumers
(any visualization frontend). Renderers are decoupled — they only depend on these types.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --- Base ---


class EventCategory(str, Enum):
    GRAPH = "graph"
    PIPELINE = "pipeline"


class Event(BaseModel):
    """Base event. All events carry a timestamp and category for routing."""
    timestamp: datetime = Field(default_factory=_now)
    category: EventCategory


# --- Graph events ---
# Emitted by the storage backend when the knowledge graph is mutated.


class NodeStored(Event):
    """A node was created or updated in storage."""
    category: Literal[EventCategory.GRAPH] = EventCategory.GRAPH
    event_type: Literal["node_stored"] = "node_stored"
    node_id: str
    node_type: str          # "topic", "fact", "inference", "metacontext"
    content: str
    status: str = "active"
    metadata: dict[str, Any] = Field(default_factory=dict)


class NodeStatusChanged(Event):
    """A node's status was updated (e.g., active → superseded)."""
    category: Literal[EventCategory.GRAPH] = EventCategory.GRAPH
    event_type: Literal["node_status_changed"] = "node_status_changed"
    node_id: str
    old_status: str
    new_status: str


class EdgeStored(Event):
    """An edge was created between two nodes."""
    category: Literal[EventCategory.GRAPH] = EventCategory.GRAPH
    event_type: Literal["edge_stored"] = "edge_stored"
    edge_id: str
    src_id: str
    dst_id: str
    edge_type: str          # EdgeType value
    weight: float = 1.0
    metadata: dict[str, Any] = Field(default_factory=dict)


class EmbeddingStored(Event):
    """An embedding was stored (we don't send the vector, just the association)."""
    category: Literal[EventCategory.GRAPH] = EventCategory.GRAPH
    event_type: Literal["embedding_stored"] = "embedding_stored"
    item_id: str
    model_id: str
    dimensions: int         # len(vector) — useful for UI without sending the full vector


class DocumentStored(Event):
    """A raw document was ingested."""
    category: Literal[EventCategory.GRAPH] = EventCategory.GRAPH
    event_type: Literal["document_stored"] = "document_stored"
    document_id: str
    content_preview: str    # first ~200 chars
    metadata: dict[str, Any] = Field(default_factory=dict)


class SegmentStored(Event):
    """A segment was created from a document."""
    category: Literal[EventCategory.GRAPH] = EventCategory.GRAPH
    event_type: Literal["segment_stored"] = "segment_stored"
    segment_id: str
    source_id: str          # document id
    text_preview: str       # first ~200 chars
    span_start: int
    span_end: int


# --- Pipeline events ---
# Emitted during Petri net execution to show process state.


class PipelineTopologyEdge(BaseModel):
    """An edge in the Petri net topology (place→transition or transition→place)."""
    source: str             # place or transition name
    target: str             # place or transition name
    label: str | None = None  # argument name for input edges


class PipelineStarted(Event):
    """A pipeline execution has begun, including full topology for rendering."""
    category: Literal[EventCategory.PIPELINE] = EventCategory.PIPELINE
    event_type: Literal["pipeline_started"] = "pipeline_started"
    pipeline_name: str
    place_names: list[str]          # all places in the net
    transition_names: list[str]     # all transitions in the net
    edges: list[PipelineTopologyEdge] = Field(default_factory=list)


class TransitionEnabled(Event):
    """A transition has become enabled (has sufficient input tokens)."""
    category: Literal[EventCategory.PIPELINE] = EventCategory.PIPELINE
    event_type: Literal["transition_enabled"] = "transition_enabled"
    pipeline_name: str
    transition_name: str


class TransitionFired(Event):
    """A transition has started executing."""
    category: Literal[EventCategory.PIPELINE] = EventCategory.PIPELINE
    event_type: Literal["transition_fired"] = "transition_fired"
    pipeline_name: str
    transition_name: str
    input_places: list[str]         # places that provided tokens


class TransitionCompleted(Event):
    """A transition has finished executing and produced output."""
    category: Literal[EventCategory.PIPELINE] = EventCategory.PIPELINE
    event_type: Literal["transition_completed"] = "transition_completed"
    pipeline_name: str
    transition_name: str
    output_places: list[str]        # places that received tokens
    duration_ms: float              # execution time


class TokensUpdated(Event):
    """Token counts changed in one or more places (batch update)."""
    category: Literal[EventCategory.PIPELINE] = EventCategory.PIPELINE
    event_type: Literal["tokens_updated"] = "tokens_updated"
    pipeline_name: str
    place_token_counts: dict[str, int]  # place_name → current token count


class PipelineCompleted(Event):
    """A pipeline execution has finished."""
    category: Literal[EventCategory.PIPELINE] = EventCategory.PIPELINE
    event_type: Literal["pipeline_completed"] = "pipeline_completed"
    pipeline_name: str
    transitions_fired: int
    duration_ms: float


# --- Union of all concrete event types ---

GraphEvent = NodeStored | NodeStatusChanged | EdgeStored | EmbeddingStored | DocumentStored | SegmentStored
PipelineEvent = PipelineStarted | TransitionEnabled | TransitionFired | TransitionCompleted | TokensUpdated | PipelineCompleted

AnyEvent = GraphEvent | PipelineEvent
