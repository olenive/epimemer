/**
 * WebSocket event router.
 *
 * The router decides what every panel in the UI sees: it splits hub system
 * messages from graph/pipeline events, fans events out by type, detects
 * sequence gaps (the signal that the view is behind), and re-sends the session
 * subscription after a reconnect. Getting the last one wrong is the quiet
 * failure — the socket comes back and the user watches a live session go
 * permanently silent.
 *
 * Routing here is by `event_type`; which *session* a browser sees is decided by
 * the hub, from the subscription frame this module sends. So the session tests
 * below assert that the frame is sent and re-sent, not that events are filtered
 * locally.
 *
 * `WebSocket` is stubbed rather than mocked away, so the tests drive the same
 * onopen/onmessage/onclose surface the browser calls.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createEventRouter } from "./events";
import type { AnyEvent, SystemMessage } from "./types";

class FakeWebSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;

  readyState = FakeWebSocket.CONNECTING;
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((msg: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(readonly url: string) {
    sockets.push(this);
  }

  send(data: string): void {
    this.sent.push(data);
  }

  close(): void {
    this.readyState = FakeWebSocket.CLOSED;
    this.onclose?.();
  }

  /** Complete the handshake, as the browser would. */
  open(): void {
    this.readyState = FakeWebSocket.OPEN;
    this.onopen?.();
  }

  deliver(payload: unknown): void {
    this.onmessage?.({ data: JSON.stringify(payload) });
  }

  deliverRaw(data: string): void {
    this.onmessage?.({ data });
  }
}

let sockets: FakeWebSocket[] = [];
const latest = (): FakeWebSocket => sockets[sockets.length - 1];

const nodeStored = (seq: number, sessionId = "session-a"): AnyEvent & { seq: number } =>
  ({
    timestamp: "2026-07-28T00:00:00Z",
    category: "graph",
    event_type: "node_stored",
    graph: "default",
    session_id: sessionId,
    seq,
    node: { node_id: `n${seq}` },
  }) as unknown as AnyEvent & { seq: number };

beforeEach(() => {
  sockets = [];
  vi.stubGlobal("WebSocket", FakeWebSocket);
  vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("connection lifecycle", () => {
  it("dials the given url and reports connection status", () => {
    const status: boolean[] = [];
    createEventRouter("ws://hub/events", (connected) => status.push(connected));

    expect(latest().url).toBe("ws://hub/events");
    expect(status).toEqual([]);

    latest().open();
    expect(status).toEqual([true]);

    latest().close();
    expect(status).toEqual([true, false]);
  });

  it("reconnects after a drop", () => {
    vi.useFakeTimers();
    createEventRouter("ws://hub/events", () => {});
    latest().open();

    latest().close();
    expect(sockets).toHaveLength(1);

    vi.advanceTimersByTime(1000);
    expect(sockets).toHaveLength(2);
    expect(latest().url).toBe("ws://hub/events");
  });
});

describe("event routing", () => {
  it("delivers an event only to handlers for its type", () => {
    const router = createEventRouter("ws://hub/events", () => {});
    latest().open();

    const nodes: AnyEvent[] = [];
    const edges: AnyEvent[] = [];
    router.subscribe("node_stored", (e) => nodes.push(e));
    router.subscribe("edge_stored", (e) => edges.push(e));

    latest().deliver(nodeStored(1));

    expect(nodes).toHaveLength(1);
    expect(edges).toEqual([]);
  });

  it("delivers every event to a subscribeAll handler", () => {
    const router = createEventRouter("ws://hub/events", () => {});
    latest().open();

    const seen: string[] = [];
    router.subscribeAll((e) => seen.push(e.event_type));

    latest().deliver(nodeStored(1));
    latest().deliver({ ...nodeStored(2), event_type: "edge_stored" });

    expect(seen).toEqual(["node_stored", "edge_stored"]);
  });

  it("stops delivering once unsubscribed", () => {
    const router = createEventRouter("ws://hub/events", () => {});
    latest().open();

    const seen: AnyEvent[] = [];
    const unsubscribe = router.subscribe("node_stored", (e) => seen.push(e));

    latest().deliver(nodeStored(1));
    unsubscribe();
    latest().deliver(nodeStored(2));

    expect(seen).toHaveLength(1);
  });

  it("keeps delivering to other handlers when one throws", () => {
    // One broken panel must not silence the rest of the UI.
    const router = createEventRouter("ws://hub/events", () => {});
    latest().open();

    const seen: AnyEvent[] = [];
    router.subscribe("node_stored", () => {
      throw new Error("render failed");
    });
    router.subscribe("node_stored", (e) => seen.push(e));

    expect(() => latest().deliver(nodeStored(1))).not.toThrow();
    expect(seen).toHaveLength(1);
  });

  it("survives a malformed frame", () => {
    const router = createEventRouter("ws://hub/events", () => {});
    latest().open();
    const seen: AnyEvent[] = [];
    router.subscribeAll((e) => seen.push(e));

    expect(() => latest().deliverRaw("not json")).not.toThrow();

    latest().deliver(nodeStored(2));
    expect(seen).toHaveLength(1);
  });
});

describe("system messages", () => {
  it("routes them to the system handler, not to event subscribers", () => {
    const router = createEventRouter("ws://hub/events", () => {});
    latest().open();

    const system: SystemMessage[] = [];
    const events: AnyEvent[] = [];
    router.onSystemMessage((m) => system.push(m));
    router.subscribeAll((e) => events.push(e));

    latest().deliver({ type: "session_disconnected", session_id: "session-a", seq: 1 });

    expect(system).toHaveLength(1);
    expect(system[0].type).toBe("session_disconnected");
    expect(events).toEqual([]);
  });

  it("passes normal events through untouched", () => {
    const router = createEventRouter("ws://hub/events", () => {});
    latest().open();

    const system: SystemMessage[] = [];
    const events: AnyEvent[] = [];
    router.onSystemMessage((m) => system.push(m));
    router.subscribeAll((e) => events.push(e));

    latest().deliver(nodeStored(1));

    expect(system).toEqual([]);
    expect(events).toHaveLength(1);
  });
});

describe("sequence gap detection", () => {
  it("stays quiet while sequence numbers are contiguous", () => {
    const router = createEventRouter("ws://hub/events", () => {});
    latest().open();
    const onGap = vi.fn();
    router.onGapDetected(onGap);

    latest().deliver(nodeStored(1));
    latest().deliver(nodeStored(2));
    latest().deliver(nodeStored(3));

    expect(onGap).not.toHaveBeenCalled();
  });

  it("fires when the sequence skips", () => {
    const router = createEventRouter("ws://hub/events", () => {});
    latest().open();
    const onGap = vi.fn();
    router.onGapDetected(onGap);

    latest().deliver(nodeStored(1));
    latest().deliver(nodeStored(4));

    expect(onGap).toHaveBeenCalledTimes(1);
  });

  it("does not treat the first frame of a connection as a gap", () => {
    // Sequence numbers are per-connection and the hub's counter is wherever it
    // happens to be; only a jump *within* a connection means missed events.
    vi.useFakeTimers();
    const router = createEventRouter("ws://hub/events", () => {});
    latest().open();
    const onGap = vi.fn();
    router.onGapDetected(onGap);

    latest().deliver(nodeStored(97));
    expect(onGap).not.toHaveBeenCalled();

    latest().close();
    vi.advanceTimersByTime(1000);
    latest().open();
    latest().deliver(nodeStored(1));

    expect(onGap).not.toHaveBeenCalled();
  });
});

describe("session subscription", () => {
  it("sends the subscription frame on an open socket", () => {
    const router = createEventRouter("ws://hub/events", () => {});
    latest().open();

    router.setSessionSubscription({ session: "session-a", graphs: null });

    expect(latest().sent).toEqual([
      JSON.stringify({ subscribe: { session: "session-a", graphs: null } }),
    ]);
  });

  it("holds the subscription until the socket opens", () => {
    const router = createEventRouter("ws://hub/events", () => {});

    router.setSessionSubscription({ session: "session-a", graphs: ["default"] });
    expect(latest().sent).toEqual([]);

    latest().open();
    expect(latest().sent).toEqual([
      JSON.stringify({ subscribe: { session: "session-a", graphs: ["default"] } }),
    ]);
  });

  it("re-sends it after a reconnect", () => {
    // Without this the new socket is subscribed to nothing and the session goes
    // silent while still appearing connected.
    vi.useFakeTimers();
    const router = createEventRouter("ws://hub/events", () => {});
    latest().open();
    router.setSessionSubscription({ session: "session-a", graphs: null });

    latest().close();
    vi.advanceTimersByTime(1000);
    latest().open();

    expect(sockets).toHaveLength(2);
    expect(latest().sent).toEqual([
      JSON.stringify({ subscribe: { session: "session-a", graphs: null } }),
    ]);
  });

  it("sends only the latest subscription after switching sessions", () => {
    const router = createEventRouter("ws://hub/events", () => {});
    latest().open();

    router.setSessionSubscription({ session: "session-a", graphs: null });
    router.setSessionSubscription({ session: "session-b", graphs: null });

    expect(latest().sent).toHaveLength(2);
    expect(JSON.parse(latest().sent[1])).toEqual({
      subscribe: { session: "session-b", graphs: null },
    });
  });
});
