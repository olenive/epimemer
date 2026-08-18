import { describe, expect, it } from "vitest";

import {
  entryFromRecord,
  recordsInGraph,
  rememberRecord,
  responseText,
  selectorLabel,
  unreadCount,
  type RecordEntry,
} from "./retrieval-store";
import type { RetrievalRecordWire } from "./types";

const wire = (over: Partial<RetrievalRecordWire> = {}): RetrievalRecordWire => ({
  record_id: "000000000001",
  at: "2026-08-18T10:00:00Z",
  tool: "epimemer.search",
  query: "deployment rollback",
  graph: "default",
  retrieved: [
    { node_id: "n1", provenance: "vector", score: 0.82 },
    { node_id: "n2", provenance: "lexical", score: 3.1 },
  ],
  response_text: '{"result": {}}',
  truncated: false,
  ...over,
});

const entry = (over: Partial<RecordEntry> = {}): RecordEntry => ({
  ...entryFromRecord(wire()),
  ...over,
});

describe("entryFromRecord", () => {
  it("keeps how each node was reached, not just that it was", () => {
    // §3: a flat "retrieved" set throws away the most useful thing the feature
    // produces. *This matched at 0.82; that one came back on an exact token
    // match* is the question you are actually asking when a search disappoints.
    const read = entryFromRecord(wire());

    expect(read.nodeIds).toEqual(["n1", "n2"]);
    expect(read.provenance).toEqual({ n1: "vector", n2: "lexical" });
    expect(read.scores).toEqual({ n1: 0.82, n2: 3.1 });
  });

  it("distinguishes a tool that declared nothing from one that never declared", () => {
    expect(entryFromRecord(wire({ retrieved: [] })).declared).toBe(true);
    expect(entryFromRecord(wire({ retrieved: null })).declared).toBe(false);
  });

  it("gives an undeclared record an empty id list to work with", () => {
    expect(entryFromRecord(wire({ retrieved: null })).nodeIds).toEqual([]);
  });
});

describe("rememberRecord", () => {
  it("drops a record it already holds, however it arrived", () => {
    // The hub replays its ring on subscribe and the RPC hands back the
    // session's, so the same record arrives twice on purpose.
    const held = [entry({ recordId: "001" })];

    expect(rememberRecord(held, entry({ recordId: "001" }))).toHaveLength(1);
  });

  it("prefers the newer copy of a record it already holds", () => {
    // The RPC copy carries the payload; the mirrored one may not.
    const held = [entry({ recordId: "001", responseText: "" })];

    const after = rememberRecord(held, entry({ recordId: "001", responseText: "{}" }));

    expect(after[0].responseText).toBe("{}");
  });

  it("keeps record order however they arrive", () => {
    let entries: RecordEntry[] = [];
    for (const id of ["003", "001", "002"]) {
      entries = rememberRecord(entries, entry({ recordId: id }));
    }
    expect(entries.map((e) => e.recordId)).toEqual(["001", "002", "003"]);
  });

  it("is bounded", () => {
    let entries: RecordEntry[] = [];
    for (const id of ["001", "002", "003"]) {
      entries = rememberRecord(entries, entry({ recordId: id }), 2);
    }
    expect(entries.map((e) => e.recordId)).toEqual(["002", "003"]);
  });
});

describe("recordsInGraph", () => {
  it("keeps a record from another graph out of the selector", () => {
    // §6: a record from graph A must not highlight into graph B.
    const entries = [
      entry({ recordId: "001", graph: "alpha" }),
      entry({ recordId: "002", graph: "beta" }),
    ];

    expect(recordsInGraph(entries, "beta").map((e) => e.recordId)).toEqual(["002"]);
  });
});

describe("selectorLabel", () => {
  it("names the tool, the query and how much came back", () => {
    expect(selectorLabel(entry())).toBe("search · deployment rollback · 2 nodes");
  });

  it("says so when a tool never declared, rather than showing zero", () => {
    const undeclared = entryFromRecord(wire({ retrieved: null }));
    expect(selectorLabel(undeclared)).toContain("not declared");
  });

  it("drops the query when there is none to show", () => {
    expect(selectorLabel(entry({ query: "  " }))).toBe("search · 2 nodes");
  });
});

describe("unreadCount", () => {
  const entries = ["001", "002", "003"].map((id) => entry({ recordId: id }));

  it("counts everything before anything has been read", () => {
    expect(unreadCount(entries, null)).toBe(3);
  });

  it("counts what arrived after the one last selected", () => {
    expect(unreadCount(entries, "001")).toBe(2);
    expect(unreadCount(entries, "003")).toBe(0);
  });

  it("falls back to everything when the last read one has been evicted", () => {
    expect(unreadCount(entries, "000")).toBe(3);
  });
});

describe("responseText", () => {
  it("says plainly when a payload never arrived", () => {
    // §3.2: the Response tab for a guarded record must say so, not sit empty
    // and look broken.
    const text = responseText(entry({ responseText: "" }));
    expect(text).toMatch(/no response text/i);
    expect(text).toMatch(/loopback/i);
  });

  it("marks a payload the cap bit into", () => {
    expect(responseText(entry({ responseText: "{}", truncated: true }))).toContain(
      "truncated",
    );
  });

  it("hands back an untruncated payload unchanged", () => {
    expect(responseText(entry({ responseText: "{}" }))).toBe("{}");
  });
});
