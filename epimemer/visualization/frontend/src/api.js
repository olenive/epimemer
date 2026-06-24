/**
 * HTTP client for the visualization server's read-only API.
 *
 * These endpoints return graph metadata and snapshot data.
 * They never modify MCP state or stored data.
 */
export const fetchGraphs = async () => {
    const resp = await fetch("/api/graphs");
    if (!resp.ok)
        throw new Error(`Failed to fetch graphs: ${resp.status}`);
    return resp.json();
};
export const fetchSnapshot = async (graph) => {
    const resp = await fetch(`/api/snapshot?graph=${encodeURIComponent(graph)}`);
    if (!resp.ok)
        throw new Error(`Failed to fetch snapshot for '${graph}': ${resp.status}`);
    return resp.json();
};
