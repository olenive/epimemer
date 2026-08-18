/**
 * HTTP client for the visualization hub's read-only API.
 *
 * Every read is scoped to a session: the hub forwards the request to that MCP
 * process, which answers from its own storage. These endpoints never modify MCP
 * state or stored data.
 */

import type {
  EdgeView,
  MetacontextView,
  NodeView,
  ReflectPressure,
  RetrievalRecordWire,
  SessionInfo,
  TimelineView,
} from "./types";

export interface GraphListResponse {
  graphs: string[];
  active_graph: string;
  backend: string;
  /** Pressure on the active graph. Absent from a hub that predates the badge. */
  reflect?: ReflectPressure;
}

export interface SnapshotResponse {
  graph: string;
  nodes: NodeView[];
  edges: EdgeView[];
  /** Both absent from a hub that predates the timeline panel. */
  timelines?: TimelineView[];
  metacontexts?: MetacontextView[];
}

/**
 * Fail with the hub's reason rather than the status code.
 *
 * A 502 here means the session answered badly, or not at all, and the hub puts
 * *why* in the body — `{"error": "sent 1011 (internal error) keepalive ping
 * timeout"}` is the difference between a diagnosis and a shrug. Discarding it
 * left the status bar with nothing to say.
 */
const failure = async (resp: Response, what: string): Promise<Error> => {
  const detail = await resp
    .json()
    .then((body) => (typeof body?.error === "string" ? body.error : ""))
    .catch(() => "");
  return new Error(`${what}: ${detail || resp.status}`);
};

export const fetchSessions = async (): Promise<SessionInfo[]> => {
  const resp = await fetch("/api/sessions");
  if (!resp.ok) throw await failure(resp, "Failed to fetch sessions");
  return resp.json();
};

export const fetchGraphs = async (session: string): Promise<GraphListResponse> => {
  const resp = await fetch(`/api/graphs?session=${encodeURIComponent(session)}`);
  if (!resp.ok) throw await failure(resp, "Failed to fetch graphs");
  return resp.json();
};

export interface RetrievalsResponse {
  records: RetrievalRecordWire[];
}

/**
 * This session's retrieval records, payloads included.
 *
 * Answered by the MCP process itself, which is why it carries the response
 * text even when the hub's own mirror is guarded down to structural metadata —
 * and why it fails once that process exits. The mirror is what survives that;
 * the two routes are complementary (§3.2).
 */
export const fetchRetrievals = async (session: string): Promise<RetrievalsResponse> => {
  const resp = await fetch(`/api/retrievals?session=${encodeURIComponent(session)}`);
  if (!resp.ok) throw await failure(resp, "Failed to fetch retrievals");
  return resp.json();
};

export const fetchSnapshot = async (
  session: string,
  graph: string,
): Promise<SnapshotResponse> => {
  const resp = await fetch(
    `/api/snapshot?session=${encodeURIComponent(session)}&graph=${encodeURIComponent(graph)}`,
  );
  if (!resp.ok) throw await failure(resp, `Failed to fetch snapshot for '${graph}'`);
  return resp.json();
};
