import { describe, expect, it } from "vitest";

import {
  NO_LOG_FILTERS,
  applyLogFilters,
  entryFromAction,
  rememberEntry,
  verbsIn,
  type LogEntry,
} from "./log-store";
import type { GraphActionRecorded } from "./types";

const entry = (over: Partial<LogEntry> = {}): LogEntry => ({
  actionId: "000000000001",
  at: Date.parse("2026-08-18T10:00:00Z"),
  graph: "default",
  verb: "stored",
  subjects: ["node-1"],
  counts: { nodes: 1 },
  summary: "stored (1 node)",
  ...over,
});

// §5: structured filters, not search. Three of the four things you would look
// for are filters over fields, and BM25 over a dozen verbs repeated thousands
// of times is a ranking function returning a constant — every term sits above
// SurrealDB's 50% document-frequency clamp and every match ties at 0.0.
describe("test_log_filters_by_verb_and_substring", () => {
  const entries = [
    entry({ actionId: "001", verb: "stored", summary: "stored (25 nodes, 90 edges)" }),
    entry({
      actionId: "002",
      verb: "corrected",
      subjects: ["node-a", "node-b"],
      summary: "corrected node-a → node-b (2 edges)",
    }),
    entry({ actionId: "003", verb: "world_changed", summary: "world-change: n1 → n2" }),
  ];

  it("keeps only the selected verbs", () => {
    const kept = applyLogFilters(entries, {
      ...NO_LOG_FILTERS,
      verbs: ["corrected", "world_changed"],
    });
    expect(kept.map((e) => e.actionId)).toEqual(["002", "003"]);
  });

  it("treats no verb selection as every verb, not as none", () => {
    expect(applyLogFilters(entries, NO_LOG_FILTERS)).toHaveLength(3);
    expect(applyLogFilters(entries, { ...NO_LOG_FILTERS, verbs: [] })).toHaveLength(3);
  });

  it("matches free text as a plain substring of the summary", () => {
    const kept = applyLogFilters(entries, { ...NO_LOG_FILTERS, text: "edges" });
    expect(kept.map((e) => e.actionId)).toEqual(["001", "002"]);
  });

  it("is case-insensitive about free text", () => {
    expect(
      applyLogFilters(entries, { ...NO_LOG_FILTERS, text: "WORLD-CHANGE" }),
    ).toHaveLength(1);
  });

  it("ANDs verb and text rather than widening", () => {
    const kept = applyLogFilters(entries, {
      ...NO_LOG_FILTERS,
      verbs: ["stored"],
      text: "edges",
    });
    expect(kept.map((e) => e.actionId)).toEqual(["001"]);
  });
});

describe("the node id filter", () => {
  const entries = [
    entry({ actionId: "001", subjects: ["node-1"] }),
    entry({ actionId: "002", subjects: ["node-10", "node-2"] }),
  ];

  it("is exact, because an id is a lookup and not ranked retrieval", () => {
    // §5: ids would score well in BM25, being maximally rare — but for an id
    // you want the one node, and "node-1" must not drag in "node-10".
    const kept = applyLogFilters(entries, { ...NO_LOG_FILTERS, nodeId: "node-1" });
    expect(kept.map((e) => e.actionId)).toEqual(["001"]);
  });

  it("matches any subject, not only the primary one", () => {
    const kept = applyLogFilters(entries, { ...NO_LOG_FILTERS, nodeId: "node-2" });
    expect(kept.map((e) => e.actionId)).toEqual(["002"]);
  });

  it("ignores surrounding whitespace from the text box", () => {
    expect(
      applyLogFilters(entries, { ...NO_LOG_FILTERS, nodeId: "  node-1 " }),
    ).toHaveLength(1);
  });
});

describe("the time range", () => {
  const early = entry({ actionId: "001", at: Date.parse("2026-08-01T00:00:00Z") });
  const late = entry({ actionId: "002", at: Date.parse("2026-08-20T00:00:00Z") });

  it("keeps what falls inside it", () => {
    const kept = applyLogFilters([early, late], {
      ...NO_LOG_FILTERS,
      range: {
        t0: Date.parse("2026-08-10T00:00:00Z"),
        t1: Date.parse("2026-08-30T00:00:00Z"),
      },
    });
    expect(kept.map((e) => e.actionId)).toEqual(["002"]);
  });
});

describe("rememberEntry", () => {
  it("drops a replayed entry it already holds", () => {
    // Backfill on subscribe replays what the ring still has, and a browser that
    // was already connected has some of it. `action_id` is what makes the two
    // recognisable as one act — `seq` differs per connection by design (§4.1).
    const held = [entry({ actionId: "001" })];

    const after = rememberEntry(held, entry({ actionId: "001" }), 10);

    expect(after).toHaveLength(1);
  });

  it("keeps entries in action order however they arrive", () => {
    let entries: LogEntry[] = [];
    for (const id of ["003", "001", "002"]) {
      entries = rememberEntry(entries, entry({ actionId: id }), 10);
    }
    expect(entries.map((e) => e.actionId)).toEqual(["001", "002", "003"]);
  });

  it("is bounded, dropping the oldest", () => {
    let entries: LogEntry[] = [];
    for (const id of ["001", "002", "003"]) {
      entries = rememberEntry(entries, entry({ actionId: id }), 2);
    }
    expect(entries.map((e) => e.actionId)).toEqual(["002", "003"]);
  });
});

describe("entryFromAction", () => {
  it("reads the wire event without re-deriving its summary", () => {
    // §3.1: the line is pre-rendered on the emitting side deliberately. A
    // frontend that assembled it from parts would be a second place where the
    // system's vocabulary is decided.
    const wire = {
      timestamp: "2026-08-18T10:00:00Z",
      category: "graph",
      event_type: "graph_action_recorded",
      graph: "default",
      action_id: "000000000007",
      verb: "corrected",
      subjects: ["a", "b"],
      counts: { edges: 2 },
      summary: "corrected a → b (2 edges)",
    } as GraphActionRecorded;

    expect(entryFromAction(wire)).toEqual({
      actionId: "000000000007",
      at: Date.parse("2026-08-18T10:00:00Z"),
      graph: "default",
      verb: "corrected",
      subjects: ["a", "b"],
      counts: { edges: 2 },
      summary: "corrected a → b (2 edges)",
    });
  });
});

describe("verbsIn", () => {
  it("lists the verbs present, so the chips describe this log", () => {
    const entries = [
      entry({ actionId: "001", verb: "stored" }),
      entry({ actionId: "002", verb: "corrected" }),
      entry({ actionId: "003", verb: "stored" }),
    ];
    expect(verbsIn(entries)).toEqual(["corrected", "stored"]);
  });
});
