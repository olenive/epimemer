/**
 * TypeScript types mirroring the Python event schema.
 *
 * These types are the contract between the WebSocket server and the frontend.
 * If the Python events change, these must be updated to match.
 */

// --- Base ---

export type EventCategory = "graph" | "pipeline";

export interface BaseEvent {
  timestamp: string;
  category: EventCategory;
  event_type: string;
  graph: string;
  /** Injected by the hub — which session produced this event. */
  session_id?: string;
}

// --- Sessions (hub) ---

export interface SessionInfo {
  session_id: string;
  pid: number;
  backend: string;
  active_graph: string;
  started_at: string;
  connected: boolean;
  last_event_at: string | null;
}

export interface SessionConnected {
  type: "session_connected";
  session: SessionInfo;
  seq: number;
}

export interface SessionDisconnected {
  type: "session_disconnected";
  session_id: string;
  seq: number;
}

export interface SessionDropped {
  type: "session_dropped";
  session_id: string;
  seq: number;
}

export type SystemMessage = SessionConnected | SessionDisconnected | SessionDropped;

// --- Shared view models ---

export interface NodeView {
  node_id: string;
  node_type: string;
  content: string;
  status: string;
  source_id: string;
  extraction_method: string;
  confidence: number | null;     // null until an agent rates the node
  retrieved_at: string | null;   // null until a search has returned it
  created_at: string;
  graph: string;
  metadata: Record<string, unknown>;
}

export interface EdgeView {
  edge_id: string;
  src_id: string;
  dst_id: string;
  edge_type: string;
  weight: number;
  created_at: string;
  graph: string;
  metadata: Record<string, unknown>;
}

/**
 * A point or interval on a timeline.
 *
 * `start` is null for a vague timepoint ("during the Renaissance"). Such a
 * point has no coordinate and must never be placed on the metric axis.
 */
export interface TimepointView {
  timepoint_id: string;
  start: string | null;
  end: string | null;
  label: string | null;
  metadata: Record<string, unknown>;
}

export interface TimelineView {
  timeline_id: string;
  name: string;
  description: string;
  timepoints: TimepointView[];
  /**
   * The timeline's own "now" — what the view centres on and measures past and
   * future against. `null` means follow the wall clock; resolve it at render
   * time rather than substituting a fixed instant on arrival.
   */
  reference_time: string | null;
  created_at: string;
  graph: string;
  metadata: Record<string, unknown>;
}

/** An epistemic frame, so a filter can name one rather than show a uuid. */
export interface MetacontextView {
  metacontext_id: string;
  content: string;
  description: string;
  graph: string;
}

/**
 * One entry in this graph's relationship vocabulary, with what it means here.
 *
 * An edge carries its label as a bare string, so a viewer reading edges alone
 * can show what a relation is called and nothing about what this graph means
 * by it. Absent for a label nobody has recorded, which is the ordinary state
 * of a graph that predates the record.
 */
export interface RelationLabelView {
  relation_label_id: string;
  name: string;
  kind: string;
  description: string;
  graph: string;
}

// --- Graph events ---

export interface NodeStored extends BaseEvent {
  category: "graph";
  event_type: "node_stored";
  node: NodeView;
}

export interface NodeStatusChanged extends BaseEvent {
  category: "graph";
  event_type: "node_status_changed";
  node_id: string;
  old_status: string;
  new_status: string;
  /** The node that replaced, followed or absorbed this one. Null where nothing
   *  did — archival retires a node without a successor. */
  counterpart: string | null;
}

/**
 * One human-meaningful act, at the transaction boundary that performed it.
 *
 * The fine-grained events keep flowing and the graph panel keeps reading them;
 * the log reads only this. `summary` arrives pre-rendered — see `log-store.ts`
 * for why the frontend must not assemble it. `action_id` is assigned by the
 * session that emitted the act, so it is a position in a stream, which `seq`
 * (per browser connection, reset on reconnect) is not.
 */
export interface GraphActionRecorded extends BaseEvent {
  category: "graph";
  event_type: "graph_action_recorded";
  action_id: string;
  /** stored | corrected | world_changed | merged | archived | restored | retired */
  verb: string;
  /** Node ids, primary first. */
  subjects: string[];
  counts: Record<string, number>;
  summary: string;
}

/** One node a response named, and how it was reached. */
export interface RetrievedNodeWire {
  node_id: string;
  /** vector | lexical | segment | expanded | direct */
  provenance: string;
  /** Similarity or BM25 where the tool has one; null where it ranks nothing. */
  score: number | null;
}

/**
 * What one tool call handed the agent.
 *
 * `retrieved` is `null` when the tool never declared its ids — which is a
 * different thing from `[]`, "declared and returned nothing". Keeping them
 * apart is what makes a forgotten declaration visible rather than silent.
 *
 * `query` and `response_text` are empty on a record the hub holds under a
 * non-loopback bind: payloads stay in the session process there, reachable
 * only over the `retrievals` RPC while that session lives.
 */
export interface RetrievalRecordWire {
  record_id: string;
  at: string;
  tool: string;
  query: string;
  graph: string;
  retrieved: RetrievedNodeWire[] | null;
  response_text: string;
  truncated: boolean;
}

export interface RetrievalRecorded extends BaseEvent {
  category: "graph";
  event_type: "retrieval_recorded";
  record: RetrievalRecordWire;
}

export interface EdgeStored extends BaseEvent {
  category: "graph";
  event_type: "edge_stored";
  edge: EdgeView;
}

/**
 * A timeline was created or extended.
 *
 * Carries the whole timeline, not a delta: adding a timepoint re-stores the
 * timeline, so a receiver replaces its copy rather than merging.
 */
export interface TimelineStored extends BaseEvent {
  category: "graph";
  event_type: "timeline_stored";
  timeline: TimelineView;
}

export interface EmbeddingStored extends BaseEvent {
  category: "graph";
  event_type: "embedding_stored";
  item_id: string;
  model_id: string;
  dimensions: number;
}

export interface DocumentStored extends BaseEvent {
  category: "graph";
  event_type: "document_stored";
  document_id: string;
  content_preview: string;
  metadata: Record<string, unknown>;
}

export interface SegmentStored extends BaseEvent {
  category: "graph";
  event_type: "segment_stored";
  segment_id: string;
  source_id: string;
  text_preview: string;
  span_start: number;
  span_end: number;
}

export interface GraphSwitched extends BaseEvent {
  category: "graph";
  event_type: "graph_switched";
  previous_graph: string;
  new_graph: string;
}

/** How close a graph is to a suggested reflect. */
export interface ReflectPressure {
  count: number;
  threshold: number;
  suggested: boolean;
}

export interface ReflectCounterUpdated extends BaseEvent, ReflectPressure {
  category: "graph";
  event_type: "reflect_counter_updated";
}

export type GraphEvent =
  | NodeStored
  | ReflectCounterUpdated
  | NodeStatusChanged
  | GraphActionRecorded
  | RetrievalRecorded
  | EdgeStored
  | TimelineStored
  | EmbeddingStored
  | DocumentStored
  | SegmentStored
  | GraphSwitched;

// --- Pipeline events ---

export interface PipelineTopologyEdge {
  source: string;
  target: string;
  label: string | null;
}

export interface PipelineStarted extends BaseEvent {
  category: "pipeline";
  event_type: "pipeline_started";
  pipeline_name: string;
  place_names: string[];
  transition_names: string[];
  edges: PipelineTopologyEdge[];
}

export interface TransitionEnabled extends BaseEvent {
  category: "pipeline";
  event_type: "transition_enabled";
  pipeline_name: string;
  transition_name: string;
}

export interface TransitionFired extends BaseEvent {
  category: "pipeline";
  event_type: "transition_fired";
  pipeline_name: string;
  transition_name: string;
  input_places: string[];
}

export interface TransitionCompleted extends BaseEvent {
  category: "pipeline";
  event_type: "transition_completed";
  pipeline_name: string;
  transition_name: string;
  output_places: string[];
  duration_ms: number;
}

export interface TokensUpdated extends BaseEvent {
  category: "pipeline";
  event_type: "tokens_updated";
  pipeline_name: string;
  place_token_counts: Record<string, number>;
}

export interface PipelineCompleted extends BaseEvent {
  category: "pipeline";
  event_type: "pipeline_completed";
  pipeline_name: string;
  transitions_fired: number;
  duration_ms: number;
}

export interface PipelineFailed extends BaseEvent {
  category: "pipeline";
  event_type: "pipeline_failed";
  pipeline_name: string;
  error: string;
  transitions_fired: number;
  duration_ms: number;
}

export type PipelineEvent =
  | PipelineStarted
  | TransitionEnabled
  | TransitionFired
  | TransitionCompleted
  | TokensUpdated
  | PipelineCompleted
  | PipelineFailed;

export type AnyEvent = GraphEvent | PipelineEvent;

/** Wire message shape — AnyEvent + per-connection sequence number. */
export type WireEvent = AnyEvent & { seq: number };
