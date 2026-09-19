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
  // A topic's prose about what it covers; empty on facts and inferences, and
  // absent from a snapshot taken by a server that predates the field.
  description?: string;
  status: string;
  source_id: string;
  extraction_method: string;
  confidence: number | null;     // null until an agent rates the node
  retrieved_at: string | null;   // null until a search has returned it
  created_at: string;
  graph: string;
  metadata: Record<string, unknown>;
}

/**
 * A moment that may be imprecise, as a validity interval's endpoint.
 *
 * Four shapes for three states: a boundary located on the timeline, a boundary
 * the source named but did not locate ("during the Renaissance"), a boundary
 * whose place is unknown, and no boundary at all ("water is H2O" has no start).
 * Unknown and unbounded stay apart on purpose: one says the edge is somewhere,
 * the other says there is no edge, and the marks for them differ.
 *
 * `label` on a located boundary keeps the source's own words where a named
 * endpoint was later resolved to a date, so the phrase behind the date stays
 * readable.
 */
export type ImpreciseInstantView =
  | { instant_kind: "precise"; at: string; label: string | null }
  | { instant_kind: "named"; label: string }
  | { instant_kind: "unknown" }
  | { instant_kind: "unbounded" };

/**
 * A period one source asserts a claim was true, on one clock.
 *
 * Half-open, `[start, end)`: an instant belongs to the period that starts on
 * it. `timeline_id` is the clock, null for the default wall-clock one.
 * `witnessed_at` is the moment the source asserts the interval contains, which
 * is how two undated claims can be shown to overlap. `basis` says whether the
 * dates came from the text (`stated`) or from reading tense and context
 * (`inferred`), so a viewer can keep the two apart.
 *
 * Open world: a moment outside every interval here is unknown rather than
 * false, so a gap must never be drawn as a claim that the fact was untrue.
 */
export interface ValidityIntervalView {
  start: ImpreciseInstantView;
  end: ImpreciseInstantView;
  timeline_id: string | null;
  witnessed_at: ImpreciseInstantView | null;
  basis: "stated" | "inferred";
}

export interface EdgeView {
  edge_id: string;
  src_id: string;
  dst_id: string;
  edge_type: string;
  weight: number;
  /**
   * When the source this edge names asserts the claim was true.
   *
   * One list per `sourced_from` edge, so every period stays attributable to
   * the source that asserted it; empty on every other edge type. Several
   * intervals are several disjoint periods one source claims, never a range to
   * be combined into one.
   */
  validity: ValidityIntervalView[];
  created_at: string;
  graph: string;
  metadata: Record<string, unknown>;
}

/**
 * A point or interval on a timeline.
 *
 * `start` is null for a vague timepoint ("during the Renaissance"). Such a
 * point has no date, but it may still have a place: `earliest` and `latest` are
 * where the order sources stated puts it, and a point with either of them is
 * drawn as a band spanning them. A point with neither has no coordinate at all
 * and must never be placed on the metric axis.
 *
 * Both are derived on read and never stored, so they come and go as the
 * constraints behind them do. They are null for a dated point, which needs no
 * derived position, and for a contested one, whose order cannot be trusted to
 * give it one.
 */
export interface TimepointView {
  timepoint_id: string;
  start: string | null;
  end: string | null;
  label: string | null;
  kind: "instant" | "interval" | "vague";
  earliest: string | null;
  latest: string | null;
  /** The order around this point is disputed. Its date, if it has one, stands. */
  contested: boolean;
  temporal_contradiction_id: string | null;
  metadata: Record<string, unknown>;
}

/**
 * One occurrence of a recurrence rule.
 *
 * `occurrence_start` is the identity, the start the rule gave it before any
 * move; `start` is where it actually is. `materialised_id` names the timepoint
 * somebody turned it into, and such an occurrence is drawn as an ordinary mark
 * rather than as a bead.
 */
export interface OccurrenceView {
  occurrence_start: string;
  start: string;
  end: string | null;
  moved_to: string | null;
  materialised_id: string | null;
}

/**
 * A rule that says something happens over and over, with what it produced.
 *
 * Occurrences are computed and never stored, so these are the ones inside the
 * window the snapshot chose: the span of the timeline's dated points widened by
 * one period on each side. `truncated` says the per-rule cap fired.
 */
export interface RecurrenceView {
  recurrence_id: string;
  label: string;
  rule_kind: string;
  bounds_start: string | null;
  bounds_end: string | null;
  window_start: string | null;
  window_end: string | null;
  occurrences: OccurrenceView[];
  truncated: boolean;
}

export interface TimelineView {
  timeline_id: string;
  name: string;
  description: string;
  timepoints: TimepointView[];
  recurrences: RecurrenceView[];
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

/** A metacontext, so a filter can name one rather than show a uuid. */
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
  /**
   * What the act did. A node act uses one of the seven verbs: stored,
   * corrected, world_changed, merged, archived, restored, undetermined. A
   * timeline decision carries instead the kind the journal recorded it under,
   * temporal_order, temporal_verdict, timepoint_merge, recurrence,
   * recurrence_bound, recurrence_exception, so the log and the durable history
   * name it with one word. Kept a free string here: the chip row is built from
   * the verbs a log actually holds, so the frontend never has to be kept in
   * step with an enum.
   */
  verb: string;
  /** Node ids, primary first, or the timeline a decision was about. */
  subjects: string[];
  counts: Record<string, number>;
  summary: string;
  /** The agent id the tool named, null where it named none. */
  judged_by: string | null;
}

/**
 * A warning a tool computed, and whether the agent was shown it.
 *
 * Every warning a call computed arrives here, muted or not: the dashboard is
 * where a person looks at what the agent was *not* told, so `surfaced: false`
 * is the interesting case rather than one to filter out
 * (WARNINGS_DASHBOARD.md §2.2).
 *
 * `action_id` comes from the sequence the acts are numbered in. It is a place
 * in this session's stream, not a link to an act, and it is what lets the log
 * hold warnings and acts in one list.
 */
export interface AdvisoryRaised extends BaseEvent {
  category: "graph";
  event_type: "advisory_raised";
  action_id: string;
  /** The tool the warning came from, e.g. "record_contradiction". */
  tool: string;
  /**
   * What sort of thing was pointed out: disjoint_premises, cross_metacontext,
   * same_metacontext_variant, same_metacontext_contradiction,
   * description_not_written. A free string here for the reason a verb is one:
   * the panel groups by whatever arrives rather than by an enum the frontend
   * would have to be kept in step with.
   */
  kind: string;
  /** The warning's own sentence, word for word. */
  message: string;
  /** The nodes it is about. Clicking the row highlights these. */
  subjects: string[];
  /** Structured evidence, per kind. Rendered by nothing yet. */
  detail: Record<string, unknown>;
  /** proceed | flag: what the policy in force says about this kind here. */
  action: string;
  /** Whether the agent's response carried it. */
  surfaced: boolean;
  /** Whether the agent was told to raise it with the user. */
  notify_user: boolean;
  /** The agent id the call named, null where it named none. */
  judged_by: string | null;
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

/**
 * What a graph does about warnings, as `configure_warnings` reports it.
 *
 * `actions` is every kind with the action in force; `overridden` is the subset
 * this graph answered for itself. A kind missing from `overridden.by_kind` is
 * inherited from the process default, which is a different state from one set
 * explicitly to the same value: the first tracks the default, the second stays
 * put when it changes.
 */
export interface WarningSettings {
  graph: string;
  surface: boolean;
  actions: Record<string, string>;
  overridden: {
    surface?: boolean;
    default_action?: string;
    by_kind?: Record<string, string>;
  };
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
  | AdvisoryRaised
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
