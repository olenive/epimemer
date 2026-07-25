/**
 * Pipeline run state — pure, no DOM.
 *
 * One `PipelineRunState` per `pipeline_name`, updated by pure event-application
 * functions (event in → new state out) so the logic is testable by inspection
 * and separate from rendering. Counters (`runsCompleted`, `itemsProcessed`) are
 * session-lifetime and survive snapshot reloads; per-run fields reset on each
 * `pipeline_started`.
 */

import type {
  PipelineCompleted,
  PipelineEvent,
  PipelineFailed,
  PipelineStarted,
  TokensUpdated,
  TransitionCompleted,
  TransitionFired,
} from "./types";

export type PipelineStatus = "idle" | "running" | "completed" | "failed";

export interface PipelineRunState {
  name: string;
  topology: PipelineStarted | null;
  status: PipelineStatus;
  activeTransition: string | null;
  completedTransitions: string[];
  placeTokens: Record<string, number>;
  runsCompleted: number;
  lastDurationMs: number | null;
  lastTransitionsFired: number | null;
  itemsProcessed: number;
  lastError: string | null;
  /** A gap was detected mid-run — state may be behind until the next event. */
  stale: boolean;
}

export const emptyRunState = (name: string): PipelineRunState => ({
  name,
  topology: null,
  status: "idle",
  activeTransition: null,
  completedTransitions: [],
  placeTokens: {},
  runsCompleted: 0,
  lastDurationMs: null,
  lastTransitionsFired: null,
  itemsProcessed: 0,
  lastError: null,
  stale: false,
});

/**
 * Fold a token update into the run. `itemsProcessed` accumulates the sum of
 * positive per-place deltas — a backend-agnostic proxy for "tokens pushed
 * through" that needs no knowledge of which places are terminal.
 */
export const applyTokensUpdate = (
  state: PipelineRunState,
  event: TokensUpdated,
): PipelineRunState => {
  let added = 0;
  const placeTokens = { ...state.placeTokens };
  for (const [place, count] of Object.entries(event.place_token_counts)) {
    const prev = placeTokens[place] ?? 0;
    if (count > prev) added += count - prev;
    placeTokens[place] = count;
  }
  return { ...state, placeTokens, itemsProcessed: state.itemsProcessed + added };
};

export const applyPipelineEvent = (
  state: PipelineRunState,
  event: PipelineEvent,
): PipelineRunState => {
  switch (event.event_type) {
    case "pipeline_started": {
      const e = event as PipelineStarted;
      // New run: reset per-run fields, keep session-lifetime counters.
      return {
        ...state,
        topology: e,
        status: "running",
        activeTransition: null,
        completedTransitions: [],
        placeTokens: {},
        lastError: null,
        stale: false,
      };
    }
    case "transition_fired": {
      const e = event as TransitionFired;
      return { ...state, status: "running", activeTransition: e.transition_name, stale: false };
    }
    case "transition_completed": {
      const e = event as TransitionCompleted;
      const completed = state.completedTransitions.includes(e.transition_name)
        ? state.completedTransitions
        : [...state.completedTransitions, e.transition_name];
      return {
        ...state,
        completedTransitions: completed,
        activeTransition:
          state.activeTransition === e.transition_name ? null : state.activeTransition,
      };
    }
    case "tokens_updated":
      return applyTokensUpdate(state, event as TokensUpdated);
    case "pipeline_completed": {
      const e = event as PipelineCompleted;
      return {
        ...state,
        status: "completed",
        activeTransition: null,
        stale: false,
        runsCompleted: state.runsCompleted + 1,
        lastDurationMs: e.duration_ms,
        lastTransitionsFired: e.transitions_fired,
      };
    }
    case "pipeline_failed": {
      const e = event as PipelineFailed;
      return {
        ...state,
        status: "failed",
        activeTransition: null,
        stale: false,
        lastError: e.error,
        lastDurationMs: e.duration_ms,
        lastTransitionsFired: e.transitions_fired,
      };
    }
    default:
      return state;
  }
};

export const markStale = (state: PipelineRunState): PipelineRunState =>
  state.status === "running" ? { ...state, stale: true } : state;

// --- Thin stateful store over the pure reducers ---

export interface PipelineStore {
  /** Apply an event; returns the affected pipeline name, or null. */
  handleEvent: (event: PipelineEvent) => string | null;
  markRunningStale: () => void;
  clear: () => void;
  get: (name: string) => PipelineRunState | undefined;
  names: () => string[];
}

export const createPipelineStore = (): PipelineStore => {
  const runs = new Map<string, PipelineRunState>();

  const handleEvent = (event: PipelineEvent): string | null => {
    const name = event.pipeline_name;
    if (!name) return null;
    const prev = runs.get(name) ?? emptyRunState(name);
    runs.set(name, applyPipelineEvent(prev, event));
    return name;
  };

  const markRunningStale = (): void => {
    for (const [k, v] of runs) runs.set(k, markStale(v));
  };

  return {
    handleEvent,
    markRunningStale,
    clear: () => runs.clear(),
    get: (name: string) => runs.get(name),
    names: () => [...runs.keys()],
  };
};
