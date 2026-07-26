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

export const fetchSessions = async (): Promise<SessionInfo[]> => {
  const resp = await fetch("/api/sessions");
  if (!resp.ok) throw new Error(`Failed to fetch sessions: ${resp.status}`);
  return resp.json();
};

export const fetchGraphs = async (session: string): Promise<GraphListResponse> => {
  const resp = await fetch(`/api/graphs?session=${encodeURIComponent(session)}`);
  if (!resp.ok) throw new Error(`Failed to fetch graphs: ${resp.status}`);
  return resp.json();
};

export const fetchSnapshot = async (
  session: string,
  graph: string,
): Promise<SnapshotResponse> => {
  const resp = await fetch(
    `/api/snapshot?session=${encodeURIComponent(session)}&graph=${encodeURIComponent(graph)}`,
  );
  if (!resp.ok) throw new Error(`Failed to fetch snapshot for '${graph}': ${resp.status}`);
  return resp.json();
};
