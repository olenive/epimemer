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

from epimemer.core.types import (
    EpistemicNode,
    Fact,
    Inference,
    Metacontext,
    NodeEdge,
    Timeline,
    Timepoint,
    Topic,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --- Shared view models ---
# Used by both events and HTTP snapshot endpoints. These are the canonical
# shape that the frontend receives — a single contract for all node/edge data.


class NodeView(BaseModel):
    """Unified node representation for events and snapshots."""
    node_id: str
    node_type: str            # "topic" | "fact" | "inference"
    content: str
    status: str               # "active" | "superseded" | "merged"
    source_id: str | None = None  # tag/entity topics have no segment origin
    extraction_method: str
    novelty: float
    confidence: float
    relevance: float
    last_reinforced: datetime
    created_at: datetime
    graph: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class EdgeView(BaseModel):
    """Unified edge representation for events and snapshots."""
    edge_id: str
    src_id: str
    dst_id: str
    edge_type: str
    weight: float = 1.0
    created_at: datetime
    graph: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class TimepointView(BaseModel):
    """A point or interval on a timeline.

    `start` is absent for a vague timepoint ("during the Renaissance"), which
    the frontend must place off the metric axis rather than guess a date for.
    """
    timepoint_id: str
    start: datetime | None = None
    end: datetime | None = None
    label: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class MetacontextView(BaseModel):
    """An epistemic frame, so the dashboard can name one rather than show a uuid."""
    metacontext_id: str
    content: str
    description: str = ""
    graph: str


class TimelineView(BaseModel):
    """Unified timeline representation for events and snapshots.

    Timepoints travel embedded, matching how they are stored — a timeline is
    one record, and its points are not graph nodes.
    """
    timeline_id: str
    name: str
    description: str = ""
    timepoints: list[TimepointView] = Field(default_factory=list)
    created_at: datetime
    graph: str
    metadata: dict[str, Any] = Field(default_factory=dict)


# --- Conversion helpers ---


def _node_type_label(node: EpistemicNode) -> str:
    match node:
        case Topic():
            return "topic"
        case Fact():
            return "fact"
        case Inference():
            return "inference"
        case _:
            return "unknown"


def node_to_view(node: EpistemicNode, graph: str) -> NodeView:
    """Convert a storage EpistemicNode to a NodeView for the frontend."""
    return NodeView(
        node_id=node.id,
        node_type=_node_type_label(node),
        content=node.content,
        status=node.status.value,
        source_id=node.source_id,
        extraction_method=node.extraction_method,
        novelty=node.value.novelty,
        confidence=node.value.confidence,
        relevance=node.value.relevance,
        last_reinforced=node.value.last_reinforced,
        created_at=node.created_at,
        graph=graph,
        metadata=node.metadata,
    )


def edge_to_view(edge: NodeEdge, graph: str) -> EdgeView:
    """Convert a storage NodeEdge to an EdgeView for the frontend."""
    return EdgeView(
        edge_id=edge.id,
        src_id=edge.src_id,
        dst_id=edge.dst_id,
        edge_type=edge.type.value,
        weight=edge.weight,
        created_at=edge.created_at,
        graph=graph,
        metadata=edge.metadata,
    )


def metacontext_to_view(mc: Metacontext, graph: str) -> MetacontextView:
    """Convert a storage Metacontext to a MetacontextView for the frontend."""
    return MetacontextView(
        metacontext_id=mc.id,
        content=mc.content,
        description=mc.description,
        graph=graph,
    )


def _timepoint_to_view(timepoint: Timepoint) -> TimepointView:
    return TimepointView(
        timepoint_id=timepoint.id,
        start=timepoint.start,
        end=timepoint.end,
        label=timepoint.label,
        metadata=timepoint.metadata,
    )


def timeline_to_view(timeline: Timeline, graph: str) -> TimelineView:
    """Convert a storage Timeline to a TimelineView for the frontend."""
    return TimelineView(
        timeline_id=timeline.id,
        name=timeline.name,
        description=timeline.description,
        timepoints=[_timepoint_to_view(tp) for tp in timeline.timepoints],
        created_at=timeline.created_at,
        graph=graph,
        metadata=timeline.metadata,
    )


# --- Base ---


class EventCategory(str, Enum):
    GRAPH = "graph"
    PIPELINE = "pipeline"


class Event(BaseModel):
    """Base event. All events carry a timestamp, category, and graph for routing."""
    timestamp: datetime = Field(default_factory=_now)
    category: EventCategory
    graph: str = ""


# --- Graph events ---
# Emitted by the storage backend when the knowledge graph is mutated.


class NodeStored(Event):
    """A node was created or updated in storage."""
    category: Literal[EventCategory.GRAPH] = EventCategory.GRAPH
    event_type: Literal["node_stored"] = "node_stored"
    node: NodeView


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
    edge: EdgeView


class TimelineStored(Event):
    """A timeline was created or updated.

    One event carrying the whole timeline rather than separate created/appended
    events: `store_timeline` is an upsert and the only write path — adding a
    timepoint re-stores the timeline — so telling creation from extension would
    mean reading before every write to learn something the viewer can see for
    itself. Timelines are small; sending the current state is cheaper and cannot
    drift from storage.
    """
    category: Literal[EventCategory.GRAPH] = EventCategory.GRAPH
    event_type: Literal["timeline_stored"] = "timeline_stored"
    timeline: TimelineView


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
    """A pipeline execution has finished successfully."""
    category: Literal[EventCategory.PIPELINE] = EventCategory.PIPELINE
    event_type: Literal["pipeline_completed"] = "pipeline_completed"
    pipeline_name: str
    transitions_fired: int
    duration_ms: float


class PipelineFailed(Event):
    """A pipeline execution ended by raising — terminal, like PipelineCompleted.

    Kept distinct from `PipelineCompleted` so a consumer can clear the "running"
    state *and* surface the error, rather than mistaking a failure for a success.
    `transitions_fired` counts the transitions that fired before the raising one
    (which did not fire).
    """
    category: Literal[EventCategory.PIPELINE] = EventCategory.PIPELINE
    event_type: Literal["pipeline_failed"] = "pipeline_failed"
    pipeline_name: str
    error: str
    transitions_fired: int
    duration_ms: float


# --- System events ---
# Emitted on graph management operations.


class GraphSwitched(Event):
    """The MCP active graph was changed."""
    category: Literal[EventCategory.GRAPH] = EventCategory.GRAPH
    event_type: Literal["graph_switched"] = "graph_switched"
    previous_graph: str
    new_graph: str


class ReflectCounterUpdated(Event):
    """The graph's reflection pressure changed — its count or its threshold.

    Carries `suggested` rather than leaving the viewer to compare, so the
    boundary rule (inclusive) lives in one place and a badge cannot disagree
    with what `store_decomposition` tells the agent.
    """
    category: Literal[EventCategory.GRAPH] = EventCategory.GRAPH
    event_type: Literal["reflect_counter_updated"] = "reflect_counter_updated"
    count: int
    threshold: int
    suggested: bool


# --- Union of all concrete event types ---

GraphEvent = NodeStored | NodeStatusChanged | EdgeStored | TimelineStored | EmbeddingStored | DocumentStored | SegmentStored | GraphSwitched | ReflectCounterUpdated
PipelineEvent = PipelineStarted | TransitionEnabled | TransitionFired | TransitionCompleted | TokensUpdated | PipelineCompleted | PipelineFailed

AnyEvent = GraphEvent | PipelineEvent
