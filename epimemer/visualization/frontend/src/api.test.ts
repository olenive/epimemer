/**
 * Hub read API client.
 *
 * Thin, but two things here are worth pinning: session and graph names go into
 * the query string and must be encoded (graph names allow characters that would
 * otherwise split the query), and a non-2xx response must throw rather than
 * resolve — a caller that renders `undefined` as an empty graph would report a
 * hub outage as "your session has no data".
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fetchGraphs, fetchSessions, fetchSnapshot } from "./api";

let requested: string[] = [];

const respondWith = (body: unknown, ok = true, status = 200): void => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      requested.push(url);
      return { ok, status, json: async () => body };
    }),
  );
};

beforeEach(() => {
  requested = [];
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("fetchSessions", () => {
  it("returns the parsed session list", async () => {
    respondWith([{ session_id: "s1", pid: 42 }]);

    const sessions = await fetchSessions();

    expect(requested).toEqual(["/api/sessions"]);
    expect(sessions).toHaveLength(1);
    expect(sessions[0].session_id).toBe("s1");
  });

  it("throws with the status when the hub rejects the request", async () => {
    respondWith(null, false, 503);

    await expect(fetchSessions()).rejects.toThrow("503");
  });
});

describe("fetchGraphs", () => {
  it("scopes the request to the session", async () => {
    respondWith({ graphs: ["default"], active_graph: "default", backend: "memory" });

    const result = await fetchGraphs("session-a");

    expect(requested).toEqual(["/api/graphs?session=session-a"]);
    expect(result.active_graph).toBe("default");
  });

  it("encodes a session id that would otherwise break the query string", async () => {
    respondWith({ graphs: [], active_graph: "default", backend: "memory" });

    await fetchGraphs("a&b=c");

    expect(requested).toEqual(["/api/graphs?session=a%26b%3Dc"]);
  });

  it("throws with the status on failure", async () => {
    respondWith(null, false, 404);

    await expect(fetchGraphs("session-a")).rejects.toThrow("404");
  });
});

describe("fetchSnapshot", () => {
  it("sends both the session and the graph", async () => {
    respondWith({ graph: "default", nodes: [], edges: [] });

    const snapshot = await fetchSnapshot("session-a", "default");

    expect(requested).toEqual(["/api/snapshot?session=session-a&graph=default"]);
    expect(snapshot.graph).toBe("default");
  });

  it("encodes both parameters", async () => {
    respondWith({ graph: "my graph", nodes: [], edges: [] });

    await fetchSnapshot("session a", "my graph");

    expect(requested).toEqual([
      "/api/snapshot?session=session%20a&graph=my%20graph",
    ]);
  });

  it("names the graph in the error so the failure is attributable", async () => {
    respondWith(null, false, 500);

    await expect(fetchSnapshot("session-a", "archive")).rejects.toThrow(
      "Failed to fetch snapshot for 'archive': 500",
    );
  });
});
