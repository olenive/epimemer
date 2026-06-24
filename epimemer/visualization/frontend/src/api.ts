/**
 * HTTP client for the visualization server's read-only API.
 *
 * These endpoints return graph metadata and snapshot data.
 * They never modify MCP state or stored data.
 */

import type { NodeView, EdgeView } from "./types";

export interface GraphListResponse {
  graphs: string[];
  active_graph: string;
}

export interface SnapshotResponse {
  graph: string;
  nodes: NodeView[];
  edges: EdgeView[];
}

export const fetchGraphs = async (): Promise<GraphListResponse> => {
  const resp = await fetch("/api/graphs");
  if (!resp.ok) throw new Error(`Failed to fetch graphs: ${resp.status}`);
  return resp.json();
};

export const fetchSnapshot = async (graph: string): Promise<SnapshotResponse> => {
  const resp = await fetch(`/api/snapshot?graph=${encodeURIComponent(graph)}`);
  if (!resp.ok) throw new Error(`Failed to fetch snapshot for '${graph}': ${resp.status}`);
  return resp.json();
};
