/**
 * Choosing a session, and saying so when one cannot answer.
 *
 * Written after an hour spent believing the frontend was broken. A restarted
 * SurrealDB had wedged the MCP session the UI happened to default to; every
 * graph listing 502'd, the selector sat on "Loading..." forever, and the only
 * green thing on screen — the hub socket — was genuinely fine. Two separate
 * failures of honesty: the UI picked the worst candidate, then reported nothing.
 */

import { describe, expect, it } from "vitest";

import {
  graphSelectorOptions,
  graphSelectorTitle,
  hubErrorText,
  pickDefaultSession,
  sessionLabel,
} from "./session-select";
import type { SessionInfo } from "./types";

const session = (
  id: string,
  { connected = true, lastEvent = null as string | null } = {},
): SessionInfo => ({
  session_id: id,
  pid: 100,
  backend: "surrealdb",
  active_graph: "main",
  started_at: "2026-08-01T00:00:00Z",
  connected,
  last_event_at: lastEvent,
});

describe("pickDefaultSession", () => {
  it("picks a reachable session over a more recent unreachable one", () => {
    // The exact 2026-08-10 case: the session you used last is the one you just
    // wedged, so "most recent" actively selects the broken one.
    const sessions = [
      session("healthy", { lastEvent: "2026-08-01T00:00:00Z" }),
      session("wedged", { lastEvent: "2026-08-09T00:00:00Z" }),
    ];
    expect(pickDefaultSession(sessions, new Set(["wedged"]))).toBe("healthy");
  });

  it("still prefers the most recent when nothing is known to be unreachable", () => {
    const sessions = [
      session("older", { lastEvent: "2026-08-01T00:00:00Z" }),
      session("newer", { lastEvent: "2026-08-09T00:00:00Z" }),
    ];
    expect(pickDefaultSession(sessions, new Set())).toBe("newer");
  });

  it("prefers a connected session to a disconnected one", () => {
    const sessions = [
      session("gone", { connected: false, lastEvent: "2026-08-09T00:00:00Z" }),
      session("here", { lastEvent: "2026-08-01T00:00:00Z" }),
    ];
    expect(pickDefaultSession(sessions, new Set())).toBe("here");
  });

  it("prefers an unreachable connected session to a disconnected one", () => {
    // Unreachable is a worse bet than reachable, but a better one than gone:
    // it may recover, and selecting it is how the reason reaches the screen.
    const sessions = [
      session("gone", { connected: false }),
      session("wedged"),
    ];
    expect(pickDefaultSession(sessions, new Set(["wedged"]))).toBe("wedged");
  });

  it("still returns a session when every one of them is unreachable", () => {
    // Returning null here would leave the user with a blank UI and no reason.
    const sessions = [session("a"), session("b")];
    expect(pickDefaultSession(sessions, new Set(["a", "b"]))).not.toBeNull();
  });

  it("returns null when there are no sessions at all", () => {
    expect(pickDefaultSession([], new Set())).toBeNull();
  });
});

describe("graphSelectorOptions", () => {
  it("lists the graphs once they arrive", () => {
    const options = graphSelectorOptions({
      kind: "ready",
      graphs: ["default", "notes"],
      active: "notes",
    });
    expect(options.map((o) => o.value)).toEqual(["default", "notes"]);
  });

  it("says unavailable rather than loading when the session cannot answer", () => {
    const options = graphSelectorOptions({
      kind: "unavailable",
      reason: "keepalive ping timeout",
    });
    expect(options).toHaveLength(1);
    expect(options[0].label).not.toMatch(/loading/i);
    expect(options[0].label).toMatch(/unavailable/i);
  });

  it("only claims to be loading while something is actually in flight", () => {
    expect(graphSelectorOptions({ kind: "loading" })[0].label).toMatch(/loading/i);
  });
});

describe("graphSelectorTitle", () => {
  it("carries the hub's reason, which is the whole diagnosis", () => {
    const title = graphSelectorTitle({
      kind: "unavailable",
      reason: "sent 1011 (internal error) keepalive ping timeout",
    });
    expect(title).toContain("keepalive ping timeout");
  });
});

describe("sessionLabel", () => {
  it("distinguishes unreachable from disconnected", () => {
    // The hub reports a session as connected on the strength of its own socket
    // to it, which says nothing about that session's storage.
    const reachable = sessionLabel(session("s"), false);
    const unreachable = sessionLabel(session("s"), true);
    const disconnected = sessionLabel(session("s", { connected: false }), false);

    expect(unreachable).not.toBe(reachable);
    expect(unreachable).not.toBe(disconnected);
    expect(unreachable).toMatch(/unreachable/i);
  });
});

describe("hubErrorText", () => {
  it("unwraps an Error", () => {
    expect(hubErrorText(new Error("boom"))).toBe("boom");
  });

  it("survives something that is not an Error", () => {
    expect(hubErrorText("plain string")).toBe("plain string");
    expect(hubErrorText(undefined)).toBeTruthy();
  });
});
