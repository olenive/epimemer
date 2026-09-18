import { describe, expect, it } from "vitest";

import {
  NO_LOG_FILTERS,
  applyLogFilters,
  entryFromAction,
  entryFromAdvisory,
  rememberEntry,
  verbLabel,
  verbsIn,
  type LogEntry,
} from "./log-store";
import type { AdvisoryRaised, GraphActionRecorded } from "./types";

const entry = (over: Partial<LogEntry> = {}): LogEntry => ({
  actionId: "000000000001",
  at: Date.parse("2026-08-18T10:00:00Z"),
  graph: "default",
  kind: "act",
  verb: "stored",
  subjects: ["node-1"],
  counts: { nodes: 1 },
  summary: "stored (1 node)",
  surfaced: true,
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
      judged_by: null,
      summary: "corrected a → b (2 edges)",
    } as GraphActionRecorded;

    expect(entryFromAction(wire)).toEqual({
      actionId: "000000000007",
      at: Date.parse("2026-08-18T10:00:00Z"),
      graph: "default",
      kind: "act",
      verb: "corrected",
      subjects: ["a", "b"],
      counts: { edges: 2 },
      summary: "corrected a → b (2 edges)",
      // An act is something the agent did, so there is nothing it was kept
      // from; only a warning can read false here.
      surfaced: true,
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

  it("lists a timeline decision beside the node verbs", () => {
    // A timeline act carries its `DecisionKind` as its verb (EVENT_LOG.md §11),
    // so the chip row grows by whatever the log actually holds rather than by a
    // fixed enum the frontend would have to be kept in step with.
    const entries = [
      entry({ actionId: "001", verb: "stored" }),
      entry({ actionId: "002", verb: "temporal_order" }),
    ];
    expect(verbsIn(entries)).toEqual(["stored", "temporal_order"]);
  });
});

describe("verbLabel", () => {
  it("reads a chip as the summary reads", () => {
    expect(verbLabel("world_changed")).toBe("world-change");
    expect(verbLabel("stored")).toBe("stored");
  });

  it("does not leave a timeline decision underscored", () => {
    expect(verbLabel("recurrence_exception")).toBe("recurrence exception");
  });

  it("leaves a warning's verb alone", () => {
    expect(verbLabel("warned")).toBe("warned");
  });
});

// ADVISORIES_DASHBOARD.md §4: a warning is a line in the story of a session,
// beside the act it accompanied, so it becomes an entry in the same log.
const advisory = (over: Partial<AdvisoryRaised> = {}): AdvisoryRaised =>
  ({
    timestamp: "2026-09-18T11:00:00Z",
    category: "graph",
    event_type: "advisory_raised",
    graph: "default",
    action_id: "000000000009",
    tool: "record_contradiction",
    kind: "cross_metacontext",
    message: "a contradiction recorded across metacontexts, which is the wrong tool.",
    subjects: ["a", "b"],
    detail: {},
    action: "proceed",
    surfaced: true,
    notify_user: false,
    judged_by: null,
    ...over,
  }) as AdvisoryRaised;

const warned = (over: Partial<LogEntry> = {}): LogEntry =>
  entry({
    kind: "warning",
    verb: "warned",
    counts: {},
    summary: "record_variant warned: these facts share a metacontext; the agent proceeded",
    surfaced: true,
    ...over,
  });

describe("entryFromAdvisory", () => {
  it("renders the tool, the message and what the policy decided", () => {
    expect(entryFromAdvisory(advisory()).summary).toBe(
      "record_contradiction warned: a contradiction recorded across " +
        "metacontexts, which is the wrong tool; the agent proceeded",
    );
  });

  it("says when the agent was asked to raise it with the user", () => {
    expect(entryFromAdvisory(advisory({ action: "flag" })).summary).toContain(
      "; flagged to the user",
    );
  });

  it("is a warning entry, with the verb the chip row shows", () => {
    const read = entryFromAdvisory(advisory());
    expect(read.kind).toBe("warning");
    expect(read.verb).toBe("warned");
    expect(read.subjects).toEqual(["a", "b"]);
    expect(read.at).toBe(Date.parse("2026-09-18T11:00:00Z"));
  });

  it("carries whether the agent saw it, which is why it is on the dashboard", () => {
    expect(entryFromAdvisory(advisory({ surfaced: false })).surfaced).toBe(false);
    expect(entryFromAdvisory(advisory()).surfaced).toBe(true);
  });

  it("takes its place in the sequence the acts are numbered in", () => {
    // The ring replays acts and warnings as one stream, so the log sorts and
    // deduplicates both the same way (§3).
    let entries: LogEntry[] = [];
    entries = rememberEntry(entries, entry({ actionId: "002" }), 10);
    entries = rememberEntry(entries, entryFromAdvisory(advisory({ action_id: "001" })), 10);
    entries = rememberEntry(entries, entryFromAdvisory(advisory({ action_id: "001" })), 10);

    expect(entries.map((e) => e.actionId)).toEqual(["001", "002"]);
  });
});

describe("filtering a log that holds warnings", () => {
  it("offers warned beside the act verbs", () => {
    expect(verbsIn([entry({ actionId: "001" }), warned({ actionId: "002" })])).toEqual([
      "stored",
      "warned",
    ]);
  });

  it("can show warnings alone, or leave them out", () => {
    const entries = [entry({ actionId: "001" }), warned({ actionId: "002" })];
    expect(
      applyLogFilters(entries, { ...NO_LOG_FILTERS, verbs: ["warned"] }).map((e) => e.actionId),
    ).toEqual(["002"]);
    expect(
      applyLogFilters(entries, { ...NO_LOG_FILTERS, verbs: ["stored"] }).map((e) => e.actionId),
    ).toEqual(["001"]);
  });

  it("applies the substring, id and time filters as it does to acts", () => {
    const entries = [entry({ actionId: "001" }), warned({ actionId: "002" })];
    expect(
      applyLogFilters(entries, { ...NO_LOG_FILTERS, text: "metacontext" }).map((e) => e.actionId),
    ).toEqual(["002"]);
    expect(applyLogFilters(entries, { ...NO_LOG_FILTERS, nodeId: "node-1" })).toHaveLength(2);
    expect(
      applyLogFilters(entries, {
        ...NO_LOG_FILTERS,
        range: {
          t0: Date.parse("2026-08-01T00:00:00Z"),
          t1: Date.parse("2026-08-19T00:00:00Z"),
        },
      }),
    ).toHaveLength(2);
  });
});
