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
  novelty: number;
  confidence: number;
  relevance: number;
  last_reinforced: string;
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
}

export interface EdgeStored extends BaseEvent {
  category: "graph";
  event_type: "edge_stored";
  edge: EdgeView;
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

export type GraphEvent =
  | NodeStored
  | NodeStatusChanged
  | EdgeStored
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
