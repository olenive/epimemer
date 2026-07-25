/**
 * Reflect-pressure badge state.
 *
 * The badge is the only place reflection pressure is visible while working, so
 * the states that matter are: not yet known (must not look like zero), below
 * the threshold, and due. The last one is the whole point — it is the prompt to
 * run a reflect.
 */

import { describe, expect, it } from "vitest";

import {
  applyReflectCounterEvent,
  reflectBadgeClass,
  reflectBadgeLabel,
  reflectBadgeTitle,
  seedReflectState,
  unknownReflectState,
} from "./reflect-badge";
import type { ReflectCounterUpdated } from "./types";

const event = (
  count: number,
  threshold: number,
  suggested: boolean,
): ReflectCounterUpdated => ({
  timestamp: "2026-07-29T00:00:00Z",
  category: "graph",
  event_type: "reflect_counter_updated",
  graph: "default",
  count,
  threshold,
  suggested,
});

describe("seedReflectState", () => {
  it("stays unknown when the listing carries no pressure", () => {
    // An older hub, or a session that could not answer — better a dash than a
    // confident zero.
    expect(seedReflectState(undefined).known).toBe(false);
  });

  it("takes the listing's values", () => {
    const state = seedReflectState({ count: 7, threshold: 10, suggested: false });

    expect(state).toEqual({
      count: 7,
      threshold: 10,
      suggested: false,
      known: true,
    });
  });

  it("carries a suggestion that was already true on connect", () => {
    const state = seedReflectState({ count: 12, threshold: 10, suggested: true });

    expect(state.suggested).toBe(true);
    expect(reflectBadgeLabel(state)).toBe("reflect 12/10");
  });
});

describe("applyReflectCounterEvent", () => {
  it("replaces the state wholesale", () => {
    const state = applyReflectCounterEvent(unknownReflectState(), event(1, 10, false));

    expect(state).toEqual({
      count: 1,
      threshold: 10,
      suggested: false,
      known: true,
    });
  });

  it("follows a threshold that changed under a steady count", () => {
    // configure_reflection lowering the threshold: the count did not move, but
    // the graph is now due.
    let state = applyReflectCounterEvent(unknownReflectState(), event(3, 10, false));
    state = applyReflectCounterEvent(state, event(3, 2, true));

    expect(state.threshold).toBe(2);
    expect(state.suggested).toBe(true);
  });

  it("returns to not-due after a reflect zeroes the count", () => {
    let state = applyReflectCounterEvent(unknownReflectState(), event(10, 10, true));
    state = applyReflectCounterEvent(state, event(0, 10, false));

    expect(state.count).toBe(0);
    expect(state.suggested).toBe(false);
  });

  it("trusts the event's suggestion rather than recomputing it", () => {
    // The server owns the boundary rule. If the browser re-derived it, a
    // change there would silently desync the badge from the agent's prompt.
    const state = applyReflectCounterEvent(unknownReflectState(), event(4, 10, true));

    expect(state.suggested).toBe(true);
  });
});

describe("rendering", () => {
  it("shows a dash and a muted style until anything is known", () => {
    const state = unknownReflectState();

    expect(reflectBadgeLabel(state)).toBe("reflect —");
    expect(reflectBadgeClass(state)).toContain("text-gray-600");
    expect(reflectBadgeTitle(state)).toContain("not yet known");
  });

  it("reads count over threshold once known", () => {
    const state = seedReflectState({ count: 7, threshold: 10, suggested: false });

    expect(reflectBadgeLabel(state)).toBe("reflect 7/10");
    expect(reflectBadgeClass(state)).toContain("bg-gray-700");
    expect(reflectBadgeClass(state)).not.toContain("amber");
  });

  it("turns amber when a reflect is due", () => {
    const state = seedReflectState({ count: 10, threshold: 10, suggested: true });

    expect(reflectBadgeClass(state)).toContain("amber");
    expect(reflectBadgeTitle(state)).toContain("due");
  });
});
