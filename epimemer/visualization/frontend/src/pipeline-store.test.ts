/**
 * Pipeline run-state reduction.
 *
 * These are the frontend's only non-rendering logic: every event the hub
 * forwards is folded into per-pipeline state here, and the strip and detail
 * panels render whatever comes out. A wrong fold shows the user a pipeline that
 * is finished when it is running, or silently loses a run from the counters —
 * neither of which type-checking can catch.
 *
 * The hub gives no ordering or completeness guarantee across a reconnect, so
 * the out-of-order cases below are real traffic, not hypotheticals.
 */

import { describe, expect, it } from "vitest";

import {
  applyPipelineEvent,
  applyTokensUpdate,
  createPipelineStore,
  emptyRunState,
  markStale,
} from "./pipeline-store";
import type {
  PipelineCompleted,
  PipelineFailed,
  PipelineStarted,
  TokensUpdated,
  TransitionCompleted,
  TransitionFired,
} from "./types";

const base = {
  timestamp: "2026-07-28T00:00:00Z",
  category: "pipeline" as const,
  graph: "default",
};

const started = (name = "ingest"): PipelineStarted => ({
  ...base,
  event_type: "pipeline_started",
  pipeline_name: name,
  place_names: ["in", "out"],
  transition_names: ["extract", "store"],
  edges: [{ source: "in", target: "extract", label: null }],
});

const fired = (transition: string, name = "ingest"): TransitionFired => ({
  ...base,
  event_type: "transition_fired",
  pipeline_name: name,
  transition_name: transition,
  input_places: ["in"],
});

const completedTransition = (
  transition: string,
  name = "ingest",
): TransitionCompleted => ({
  ...base,
  event_type: "transition_completed",
  pipeline_name: name,
  transition_name: transition,
  output_places: ["out"],
  duration_ms: 12,
});

const tokens = (
  counts: Record<string, number>,
  name = "ingest",
): TokensUpdated => ({
  ...base,
  event_type: "tokens_updated",
  pipeline_name: name,
  place_token_counts: counts,
});

const finished = (name = "ingest"): PipelineCompleted => ({
  ...base,
  event_type: "pipeline_completed",
  pipeline_name: name,
  transitions_fired: 2,
  duration_ms: 340,
});

const failed = (name = "ingest"): PipelineFailed => ({
  ...base,
  event_type: "pipeline_failed",
  pipeline_name: name,
  error: "extraction timed out",
  transitions_fired: 1,
  duration_ms: 90,
});

describe("applyPipelineEvent — a full run", () => {
  it("tracks status and transitions from start to completion", () => {
    let state = emptyRunState("ingest");
    expect(state.status).toBe("idle");

    state = applyPipelineEvent(state, started());
    expect(state.status).toBe("running");
    expect(state.topology?.transition_names).toEqual(["extract", "store"]);
    expect(state.activeTransition).toBeNull();

    state = applyPipelineEvent(state, fired("extract"));
    expect(state.activeTransition).toBe("extract");
    expect(state.completedTransitions).toEqual([]);

    state = applyPipelineEvent(state, completedTransition("extract"));
    expect(state.activeTransition).toBeNull();
    expect(state.completedTransitions).toEqual(["extract"]);

    state = applyPipelineEvent(state, fired("store"));
    state = applyPipelineEvent(state, completedTransition("store"));
    expect(state.completedTransitions).toEqual(["extract", "store"]);

    state = applyPipelineEvent(state, finished());
    expect(state.status).toBe("completed");
    expect(state.activeTransition).toBeNull();
    expect(state.runsCompleted).toBe(1);
    expect(state.lastDurationMs).toBe(340);
    expect(state.lastTransitionsFired).toBe(2);
  });

  it("records the error and keeps the run counter untouched on failure", () => {
    let state = applyPipelineEvent(emptyRunState("ingest"), started());
    state = applyPipelineEvent(state, fired("extract"));
    state = applyPipelineEvent(state, failed());

    expect(state.status).toBe("failed");
    expect(state.lastError).toBe("extraction timed out");
    expect(state.activeTransition).toBeNull();
    // A failed run is not a completed one — the counter must not move.
    expect(state.runsCompleted).toBe(0);
    expect(state.lastDurationMs).toBe(90);
  });

  it("resets per-run fields but keeps session counters on a second run", () => {
    let state = applyPipelineEvent(emptyRunState("ingest"), started());
    state = applyPipelineEvent(state, completedTransition("extract"));
    state = applyPipelineEvent(state, tokens({ out: 3 }));
    state = applyPipelineEvent(state, finished());

    state = applyPipelineEvent(state, started());

    expect(state.status).toBe("running");
    expect(state.completedTransitions).toEqual([]);
    expect(state.placeTokens).toEqual({});
    expect(state.lastError).toBeNull();
    // Session-lifetime counters survive the reset — that is what makes the
    // strip's totals meaningful across a working session.
    expect(state.runsCompleted).toBe(1);
    expect(state.itemsProcessed).toBe(3);
  });
});

describe("applyPipelineEvent — events that arrive out of order", () => {
  it("ignores an unknown event type instead of throwing", () => {
    const state = applyPipelineEvent(emptyRunState("ingest"), started());
    const unknown = {
      ...base,
      event_type: "transition_enabled",
      pipeline_name: "ingest",
      transition_name: "extract",
    } as unknown as Parameters<typeof applyPipelineEvent>[1];

    const after = applyPipelineEvent(state, unknown);

    expect(after).toEqual(state);
  });

  it("accepts a completion for a transition it never saw fire", () => {
    // A reconnect mid-run means the `transition_fired` may be on the far side
    // of the gap; the completion still has to land.
    const state = applyPipelineEvent(
      emptyRunState("ingest"),
      completedTransition("store"),
    );

    expect(state.completedTransitions).toEqual(["store"]);
    expect(state.activeTransition).toBeNull();
    expect(state.status).toBe("idle");
  });

  it("does not record the same completed transition twice", () => {
    let state = applyPipelineEvent(emptyRunState("ingest"), started());
    state = applyPipelineEvent(state, completedTransition("extract"));
    state = applyPipelineEvent(state, completedTransition("extract"));

    expect(state.completedTransitions).toEqual(["extract"]);
  });

  it("leaves a different active transition alone when one completes", () => {
    let state = applyPipelineEvent(emptyRunState("ingest"), started());
    state = applyPipelineEvent(state, fired("store"));
    state = applyPipelineEvent(state, completedTransition("extract"));

    expect(state.activeTransition).toBe("store");
  });

  it("handles events for a run it never saw start", () => {
    const state = applyPipelineEvent(emptyRunState("ingest"), fired("extract"));

    expect(state.status).toBe("running");
    expect(state.topology).toBeNull();
    expect(state.activeTransition).toBe("extract");
  });
});

describe("applyTokensUpdate", () => {
  it("counts only the positive deltas towards items processed", () => {
    let state = applyTokensUpdate(emptyRunState("ingest"), tokens({ in: 5 }));
    expect(state.itemsProcessed).toBe(5);

    // Tokens moving on: `in` drains, `out` fills. Only the rise counts, so a
    // token is not counted again as it leaves the place it arrived in.
    state = applyTokensUpdate(state, tokens({ in: 0, out: 5 }));
    expect(state.placeTokens).toEqual({ in: 0, out: 5 });
    expect(state.itemsProcessed).toBe(10);

    // A drain on its own adds nothing...
    state = applyTokensUpdate(state, tokens({ out: 2 }));
    expect(state.placeTokens.out).toBe(2);
    expect(state.itemsProcessed).toBe(10);

    // ...and a repeated snapshot of an unchanged place is not a new arrival.
    // These are absolute counts, not deltas, and the hub resends freely.
    state = applyTokensUpdate(state, tokens({ out: 2 }));
    expect(state.itemsProcessed).toBe(10);
  });

  it("leaves places it was not told about untouched", () => {
    let state = applyTokensUpdate(emptyRunState("ingest"), tokens({ in: 2 }));
    state = applyTokensUpdate(state, tokens({ out: 1 }));

    expect(state.placeTokens).toEqual({ in: 2, out: 1 });
  });

  it("does not mutate the state it was given", () => {
    const before = emptyRunState("ingest");
    applyTokensUpdate(before, tokens({ in: 4 }));

    expect(before.placeTokens).toEqual({});
    expect(before.itemsProcessed).toBe(0);
  });
});

describe("markStale", () => {
  it("flags a running pipeline", () => {
    const running = applyPipelineEvent(emptyRunState("ingest"), started());

    expect(markStale(running).stale).toBe(true);
  });

  it("leaves a settled pipeline alone", () => {
    // A completed run's state is not behind — it is simply over, and showing
    // it as stale would tell the user to expect an update that never comes.
    const done = applyPipelineEvent(
      applyPipelineEvent(emptyRunState("ingest"), started()),
      finished(),
    );

    expect(markStale(done).stale).toBe(false);
    expect(markStale(emptyRunState("ingest")).stale).toBe(false);
  });

  it("clears once the next event arrives", () => {
    const stale = markStale(applyPipelineEvent(emptyRunState("ingest"), started()));

    expect(applyPipelineEvent(stale, fired("extract")).stale).toBe(false);
  });
});

describe("createPipelineStore", () => {
  it("keeps one run state per pipeline name", () => {
    const store = createPipelineStore();

    expect(store.handleEvent(started("ingest"))).toBe("ingest");
    expect(store.handleEvent(started("reflect"))).toBe("reflect");
    store.handleEvent(finished("ingest"));

    expect(store.names().sort()).toEqual(["ingest", "reflect"]);
    expect(store.get("ingest")?.status).toBe("completed");
    expect(store.get("reflect")?.status).toBe("running");
  });

  it("returns null for an event carrying no pipeline name", () => {
    const store = createPipelineStore();
    const nameless = { ...base, event_type: "pipeline_started" } as PipelineStarted;

    expect(store.handleEvent(nameless)).toBeNull();
    expect(store.names()).toEqual([]);
  });

  it("marks only running pipelines stale", () => {
    const store = createPipelineStore();
    store.handleEvent(started("ingest"));
    store.handleEvent(started("reflect"));
    store.handleEvent(finished("reflect"));

    store.markRunningStale();

    expect(store.get("ingest")?.stale).toBe(true);
    expect(store.get("reflect")?.stale).toBe(false);
  });

  it("forgets everything on clear", () => {
    const store = createPipelineStore();
    store.handleEvent(started("ingest"));

    store.clear();

    expect(store.names()).toEqual([]);
    expect(store.get("ingest")).toBeUndefined();
  });
});
