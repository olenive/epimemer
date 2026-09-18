// @vitest-environment jsdom
/**
 * What a warning row looks like in the log rail.
 *
 * The rest of the rail is covered by `log-store.test.ts`, which owns the
 * entries and the filters. What needs a DOM is the three things
 * `ADVISORIES_DASHBOARD.md` §4 asks for: the row text, the dimmer draw for a
 * warning the agent never saw, and the click that highlights its subjects.
 */

import { beforeEach, describe, expect, it } from "vitest";

import type { EventHandler, EventRouter } from "./events";
import { initLogPanel, type LogPanelElements } from "./log-panel";
import type { LogEntry } from "./log-store";
import { semanticPaletteFor } from "./theme";
import type { AdvisoryRaised, AnyEvent, GraphActionRecorded } from "./types";

/** A router the test drives by hand, with no socket behind it. */
const fakeRouter = () => {
  const handlers = new Map<string, EventHandler[]>();
  const router: EventRouter = {
    subscribe: (eventType, handler) => {
      handlers.set(eventType, [...(handlers.get(eventType) ?? []), handler]);
      return () => handlers.delete(eventType);
    },
    subscribeAll: () => () => {},
    setSessionSubscription: () => {},
    onSystemMessage: () => {},
    onGapDetected: () => {},
  };
  const emit = (event: AnyEvent): void => {
    for (const handler of handlers.get(event.event_type) ?? []) handler(event);
  };
  return { router, emit };
};

const input = (): HTMLInputElement => document.createElement("input");

const elements = (): LogPanelElements => ({
  rail: document.createElement("div"),
  entries: document.createElement("div"),
  empty: document.createElement("div"),
  verbs: document.createElement("div"),
  nodeId: input(),
  text: input(),
  rangeStart: input(),
  rangeEnd: input(),
  clear: document.createElement("button"),
  count: document.createElement("span"),
  note: document.createElement("div"),
});

const act = (over: Partial<GraphActionRecorded> = {}): GraphActionRecorded =>
  ({
    timestamp: "2026-09-18T10:00:00Z",
    category: "graph",
    event_type: "graph_action_recorded",
    graph: "default",
    action_id: "000000000001",
    verb: "stored",
    subjects: ["node-1"],
    counts: { nodes: 1 },
    summary: "stored (1 node)",
    judged_by: null,
    ...over,
  }) as GraphActionRecorded;

const advisory = (over: Partial<AdvisoryRaised> = {}): AdvisoryRaised =>
  ({
    timestamp: "2026-09-18T10:00:01Z",
    category: "graph",
    event_type: "advisory_raised",
    graph: "default",
    action_id: "000000000002",
    tool: "record_contradiction",
    kind: "cross_metacontext",
    message: "these facts are in different metacontexts.",
    subjects: ["node-1", "node-2"],
    detail: {},
    action: "proceed",
    surfaced: true,
    notify_user: false,
    judged_by: null,
    ...over,
  }) as AdvisoryRaised;

const rows = (host: HTMLElement): HTMLButtonElement[] =>
  [...host.querySelectorAll("button")] as HTMLButtonElement[];

/** jsdom reports an inline colour back as `rgb(r, g, b)`. */
const hexToRgb = (hex: string): string => {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
  return `rgb(${r}, ${g}, ${b})`;
};

beforeEach(() => {
  document.documentElement.className = "";
});

describe("a warning in the log rail", () => {
  it("reads as the tool, the message and what the policy decided", () => {
    const els = elements();
    const { router, emit } = fakeRouter();
    initLogPanel(els, router, () => null);

    emit(advisory());

    expect(rows(els.entries)[0].textContent).toContain(
      "record_contradiction warned: these facts are in different " +
        "metacontexts; the agent proceeded",
    );
  });

  it("sits in the log beside the act it followed", () => {
    const els = elements();
    const { router, emit } = fakeRouter();
    initLogPanel(els, router, () => null);

    emit(act());
    emit(advisory());

    // Newest first, which is how the rail reads.
    expect(rows(els.entries).map((row) => row.textContent)).toHaveLength(2);
    expect(rows(els.entries)[0].textContent).toContain("warned");
    expect(rows(els.entries)[1].textContent).toContain("stored");
  });

  it("marks the row in the pending hue rather than a colour of its own", () => {
    const els = elements();
    const { router, emit } = fakeRouter();
    initLogPanel(els, router, () => null);

    emit(advisory());

    const marker = els.entries.querySelector("[data-warning-marker]") as HTMLElement;
    expect(marker.style.backgroundColor).toBe(hexToRgb(semanticPaletteFor("light").pending));
  });

  it("offers a warned chip beside the act verbs", () => {
    const els = elements();
    const { router, emit } = fakeRouter();
    initLogPanel(els, router, () => null);

    emit(act());
    emit(advisory());

    expect(rows(els.verbs).map((chip) => chip.textContent)).toEqual(["stored", "warned"]);
  });

  it("draws dimmer and says so when the agent was never shown it", () => {
    const els = elements();
    const { router, emit } = fakeRouter();
    initLogPanel(els, router, () => null);

    emit(advisory({ surfaced: false }));

    const row = rows(els.entries)[0];
    expect(row.className).toContain("opacity-");
    expect(row.title).toContain("not shown to the agent");
  });

  it("says nothing of the kind about a warning the agent read", () => {
    const els = elements();
    const { router, emit } = fakeRouter();
    initLogPanel(els, router, () => null);

    emit(advisory());

    const row = rows(els.entries)[0];
    expect(row.className).not.toContain("opacity-");
    expect(row.title).not.toContain("not shown to the agent");
  });

  it("highlights the nodes the warning was about when clicked", () => {
    const els = elements();
    const { router, emit } = fakeRouter();
    const clicked: LogEntry[] = [];
    initLogPanel(els, router, (entry) => {
      clicked.push(entry);
      return null;
    });

    emit(advisory());
    rows(els.entries)[0].click();

    expect(clicked.map((entry) => entry.subjects)).toEqual([["node-1", "node-2"]]);
  });

  it("keeps a warning from another graph out of the viewed one", () => {
    const els = elements();
    const { router, emit } = fakeRouter();
    const panel = initLogPanel(els, router, () => null);
    panel.setViewedGraph("beta");

    emit(advisory({ graph: "alpha" }));

    expect(rows(els.entries)).toHaveLength(0);
  });
});
