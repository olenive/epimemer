// @vitest-environment jsdom
/**
 * The read-only warning settings panel.
 *
 * `ADVISORIES_DASHBOARD.md` §5: it shows the mute and, per kind, the action in
 * force and whether that answer is inherited from the process default or set on
 * this graph. Nothing here writes a setting, because a write from the browser
 * would be the first write into a graph with no author.
 */

import { describe, expect, it, vi } from "vitest";

import type { EventHandler, EventRouter } from "./events";
import type { AnyEvent, GraphSwitched, WarningSettings } from "./types";
import {
  initWarningSettings,
  muteLine,
  settingsRows,
  settingsTitle,
  type WarningSettingsElements,
} from "./warning-settings";

const settings = (over: Partial<WarningSettings> = {}): WarningSettings => ({
  graph: "memory",
  surface: true,
  actions: {
    cross_metacontext: "proceed",
    description_not_written: "proceed",
    disjoint_premises: "proceed",
    same_metacontext_contradiction: "flag",
    same_metacontext_variant: "proceed",
  },
  overridden: {},
  ...over,
});

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

const elements = (): WarningSettingsElements => ({
  panel: document.createElement("div"),
  title: document.createElement("div"),
  mute: document.createElement("div"),
  rows: document.createElement("div"),
});

const switched = (session: string): GraphSwitched =>
  ({
    timestamp: "2026-09-18T12:00:00Z",
    category: "graph",
    event_type: "graph_switched",
    graph: "beta",
    session_id: session,
    previous_graph: "memory",
    new_graph: "beta",
  }) as GraphSwitched;

const settled = () => new Promise((resolve) => setTimeout(resolve, 0));

describe("what the panel says", () => {
  it("names the graph, because the setting is per graph", () => {
    expect(settingsTitle(settings({ graph: "notes" }))).toBe("Warnings on notes");
  });

  it("says nothing about a graph it has not been told about", () => {
    expect(settingsTitle(null)).toBe("Warnings");
  });

  it("labels the mute for what it does, never as turning warnings off", () => {
    expect(muteLine(settings())).toBe("warnings shown to the agent");
    expect(muteLine(settings({ surface: false }))).toBe("warnings muted for the agent");
  });

  it("reports a kind absent from the overrides as inherited", () => {
    const rows = settingsRows(settings());
    expect(rows).toHaveLength(5);
    expect(rows.every((row) => row.origin === "inherited")).toBe(true);
  });

  it("reports a kind this graph named as set here, even at the same value", () => {
    // The distinction is the one `configure_warnings` already makes: a kind set
    // explicitly stays put when the default moves, an inherited one follows it.
    const rows = settingsRows(
      settings({ overridden: { by_kind: { cross_metacontext: "proceed" } } }),
    );

    const named = rows.find((row) => row.kind === "cross_metacontext");
    expect(named?.origin).toBe("set on this graph");
    expect(named?.action).toBe("proceed");
    expect(rows.filter((row) => row.origin === "set on this graph")).toHaveLength(1);
  });

  it("lists the kinds in the order the response gives them", () => {
    expect(settingsRows(settings()).map((row) => row.kind)).toEqual(
      Object.keys(settings().actions),
    );
  });
});

describe("the panel on screen", () => {
  it("renders the title, the mute and a row per kind", async () => {
    const els = elements();
    const { router } = fakeRouter();
    const fetchSettings = vi.fn(async () => settings());
    const panel = initWarningSettings(els, router, fetchSettings);

    panel.setSession("s-a");
    await settled();

    expect(els.title.textContent).toBe("Warnings on memory");
    expect(els.mute.textContent).toBe("warnings shown to the agent");
    expect(els.rows.children).toHaveLength(5);
    expect(els.rows.textContent).toContain("same_metacontext_contradiction");
    expect(els.rows.textContent).toContain("flag");
    expect(els.rows.textContent).toContain("inherited");
  });

  it("re-asks when the session moves to another graph", async () => {
    const els = elements();
    const { router, emit } = fakeRouter();
    const fetchSettings = vi.fn(async () => settings());
    const panel = initWarningSettings(els, router, fetchSettings);

    panel.setSession("s-a");
    await settled();
    emit(switched("s-a"));
    await settled();

    expect(fetchSettings).toHaveBeenCalledTimes(2);
    expect(fetchSettings).toHaveBeenLastCalledWith("s-a");
  });

  it("ignores a graph switch in a session nobody is watching", async () => {
    const els = elements();
    const { router, emit } = fakeRouter();
    const fetchSettings = vi.fn(async () => settings());
    const panel = initWarningSettings(els, router, fetchSettings);

    panel.setSession("s-a");
    await settled();
    emit(switched("s-b"));
    await settled();

    expect(fetchSettings).toHaveBeenCalledTimes(1);
  });

  it("says so rather than showing a stale graph's settings when the read fails", async () => {
    const els = elements();
    const { router } = fakeRouter();
    const panel = initWarningSettings(els, router, async () => {
      throw new Error("session RPC timed out");
    });

    panel.setSession("s-a");
    await settled();

    expect(els.title.textContent).toBe("Warnings");
    expect(els.mute.textContent).toContain("session RPC timed out");
    expect(els.rows.children).toHaveLength(0);
  });
});
