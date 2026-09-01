/**
 * Reflect-pressure badge state — pure, no DOM.
 *
 * Shows how close the viewed session's active graph is to a suggested reflect.
 * State arrives two ways: a starting value with the graph listing, then
 * `reflect_counter_updated` events as stores land. Both go through the same
 * reducers so a seeded badge and a live one cannot disagree.
 *
 * "Not yet known" is a real state, distinct from 0: a graph sitting at 7 of 10
 * must not render as `0/10` while the listing is in flight.
 */

import type { ReflectCounterUpdated, ReflectPressure } from "./types";

export interface ReflectBadgeState {
  count: number;
  threshold: number;
  suggested: boolean;
  known: boolean;
}

export const unknownReflectState = (): ReflectBadgeState => ({
  count: 0,
  threshold: 0,
  suggested: false,
  known: false,
});

/** Seed from the graph listing — the value the events then move. */
export const seedReflectState = (
  pressure: ReflectPressure | undefined,
): ReflectBadgeState =>
  pressure === undefined
    ? unknownReflectState()
    : {
        count: pressure.count,
        threshold: pressure.threshold,
        suggested: pressure.suggested,
        known: true,
      };

/**
 * Fold in a counter event.
 *
 * `suggested` is taken from the event rather than recomputed: the inclusive
 * boundary is the server's rule, and a browser re-deriving it is how a badge
 * ends up disagreeing with what the agent was told at ingest time.
 */
export const applyReflectCounterEvent = (
  _state: ReflectBadgeState,
  event: ReflectCounterUpdated,
): ReflectBadgeState => ({
  count: event.count,
  threshold: event.threshold,
  suggested: event.suggested,
  known: true,
});

export const reflectBadgeLabel = (state: ReflectBadgeState): string =>
  state.known ? `reflect ${state.count}/${state.threshold}` : "reflect —";

const BADGE_BASE = "px-1.5 py-0.5 rounded text-xs";

/** Amber once a reflect is due; muted otherwise, and dimmer still when unknown. */
export const reflectBadgeClass = (state: ReflectBadgeState): string => {
  if (!state.known) {
    return `${BADGE_BASE} bg-surface-raised text-content-muted`;
  }
  if (state.suggested) {
    return `${BADGE_BASE} bg-amber-100 text-amber-700 dark:bg-amber-900/50 dark:text-amber-400`;
  }
  return `${BADGE_BASE} bg-surface-raised text-content-secondary`;
};

export const reflectBadgeTitle = (state: ReflectBadgeState): string =>
  state.known
    ? `${state.count} stores since the last reflect; ${state.threshold} suggests one` +
      (state.suggested ? " — reflect is due" : "")
    : "Reflection pressure not yet known for this session";
