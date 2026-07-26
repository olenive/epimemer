import { describe, expect, it } from "vitest";

import {
  NO_FILTERS,
  applyFilters,
  facetValues,
  matchesFilters,
  matchesQuery,
  matchesRange,
  parseQuery,
  type FilterableMark,
  type Facets,
} from "./timeline-filter";

const mark = (facets: Facets, start: number | null = 0, end: number | null = null):
  FilterableMark => ({ start, end, facets });

describe("parseQuery", () => {
  it("splits bare terms on whitespace", () => {
    expect(parseQuery("alpha beta")).toEqual([
      { field: null, value: "alpha" },
      { field: null, value: "beta" },
    ]);
  });

  it("recognises a known field prefix", () => {
    expect(parseQuery("source:BBC")).toEqual([{ field: "source", value: "BBC" }]);
  });

  it("is case-insensitive about the field name but not the value", () => {
    expect(parseQuery("Source:BBC")).toEqual([{ field: "source", value: "BBC" }]);
  });

  it("keeps an unknown prefix as literal text", () => {
    // Otherwise a time or a URL silently becomes a search on a made-up field.
    expect(parseQuery("12:30")).toEqual([{ field: null, value: "12:30" }]);
    expect(parseQuery("https://example.com")).toEqual([
      { field: null, value: "https://example.com" },
    ]);
  });

  it("keeps a quoted phrase together", () => {
    expect(parseQuery('"the war" ended')).toEqual([
      { field: null, value: "the war" },
      { field: null, value: "ended" },
    ]);
  });

  it("allows a quoted value after a field prefix", () => {
    expect(parseQuery('source:"BBC News"')).toEqual([
      { field: "source", value: "BBC News" },
    ]);
  });

  it("keeps an unterminated quote as a term rather than dropping it", () => {
    expect(parseQuery('label:"the wa')).toEqual([{ field: "label", value: "the wa" }]);
  });

  it("is empty for blank input", () => {
    expect(parseQuery("")).toEqual([]);
    expect(parseQuery("   ")).toEqual([]);
  });

  it("treats a field prefix with no value as literal text", () => {
    expect(parseQuery("source:")).toEqual([{ field: null, value: "source:" }]);
  });
});

describe("matchesQuery", () => {
  const subject = mark({
    type: ["fact"],
    status: ["active"],
    source: ["BBC News"],
    label: ["the war ended"],
    content: ["Armistice signed"],
  });

  it("matches a bare term against any field", () => {
    expect(matchesQuery(subject, "armistice")).toBe(true);
    expect(matchesQuery(subject, "BBC")).toBe(true);
  });

  it("ignores case", () => {
    expect(matchesQuery(subject, "ARMISTICE")).toBe(true);
  });

  it("matches substrings", () => {
    expect(matchesQuery(subject, "war")).toBe(true);
  });

  it("restricts a field term to that field", () => {
    expect(matchesQuery(subject, "source:BBC")).toBe(true);
    expect(matchesQuery(subject, "source:armistice")).toBe(false);
  });

  it("ANDs multiple terms", () => {
    expect(matchesQuery(subject, "source:BBC war")).toBe(true);
    expect(matchesQuery(subject, "source:BBC absent")).toBe(false);
  });

  it("requires the whole phrase when quoted", () => {
    expect(matchesQuery(subject, '"the war"')).toBe(true);
    expect(matchesQuery(subject, '"war the"')).toBe(false);
  });

  it("passes everything for an empty query", () => {
    expect(matchesQuery(mark({}), "")).toBe(true);
  });
});

describe("facet filters", () => {
  it("keeps a mark when any linked node passes", () => {
    const mixed = mark({ status: ["active", "superseded"] });
    const filters = { ...NO_FILTERS, statuses: new Set(["active"]) };

    expect(matchesFilters(mixed, filters)).toBe(true);
  });

  it("drops a mark when no linked node passes", () => {
    const retired = mark({ status: ["superseded"] });
    const filters = { ...NO_FILTERS, statuses: new Set(["active"]) };

    expect(matchesFilters(retired, filters)).toBe(false);
  });

  it("does not hide a mark that has nothing to say about the facet", () => {
    // An unlinked timepoint has no node type at all. Excluding it would let a
    // filter delete data it cannot actually speak about.
    const unlinked = mark({ label: ["a lone moment"] });
    const filters = { ...NO_FILTERS, nodeTypes: new Set(["fact"]) };

    expect(matchesFilters(unlinked, filters)).toBe(true);
  });

  it("applies node type, status and metacontext together", () => {
    const subject = mark({
      type: ["fact"],
      status: ["active"],
      mc: ["Real historical events"],
    });

    expect(
      matchesFilters(subject, {
        ...NO_FILTERS,
        nodeTypes: new Set(["fact"]),
        statuses: new Set(["active"]),
        metacontexts: new Set(["Real historical events"]),
      }),
    ).toBe(true);

    expect(
      matchesFilters(subject, {
        ...NO_FILTERS,
        metacontexts: new Set(["World of Darkness"]),
      }),
    ).toBe(false);
  });

  it("passes nothing when the allowed set is empty", () => {
    const subject = mark({ type: ["fact"] });
    expect(matchesFilters(subject, { ...NO_FILTERS, nodeTypes: new Set() })).toBe(false);
  });
});

describe("matchesRange", () => {
  const range = { t0: 100, t1: 200 };

  it("keeps an instant inside the range", () => {
    expect(matchesRange(mark({}, 150), range)).toBe(true);
  });

  it("drops an instant outside it", () => {
    expect(matchesRange(mark({}, 50), range)).toBe(false);
    expect(matchesRange(mark({}, 250), range)).toBe(false);
  });

  it("keeps an interval that merely overlaps", () => {
    expect(matchesRange(mark({}, 50, 150), range)).toBe(true);
    expect(matchesRange(mark({}, 150, 900), range)).toBe(true);
    expect(matchesRange(mark({}, 0, 900), range)).toBe(true);
  });

  it("includes the boundaries", () => {
    expect(matchesRange(mark({}, 100), range)).toBe(true);
    expect(matchesRange(mark({}, 200), range)).toBe(true);
  });

  it("keeps an undated mark, which no date range can speak about", () => {
    expect(matchesRange(mark({ label: ["long ago"] }, null), range)).toBe(true);
  });

  it("passes everything when there is no range", () => {
    expect(matchesRange(mark({}, 999), null)).toBe(true);
  });

  it("is applied by the composed filter, not only on its own", () => {
    const outside = mark({ type: ["fact"] }, 500);
    const filters = { ...NO_FILTERS, nodeTypes: new Set(["fact"]), range };

    expect(matchesFilters(outside, filters)).toBe(false);
    expect(matchesFilters(mark({ type: ["fact"] }, 150), filters)).toBe(true);
  });
});

describe("applyFilters", () => {
  it("narrows a list and preserves order", () => {
    const marks = [
      mark({ type: ["fact"], content: ["one"] }, 1),
      mark({ type: ["topic"], content: ["two"] }, 2),
      mark({ type: ["fact"], content: ["three"] }, 3),
    ];

    const kept = applyFilters(marks, { ...NO_FILTERS, nodeTypes: new Set(["fact"]) });

    expect(kept.map((m) => m.facets.content?.[0])).toEqual(["one", "three"]);
  });

  it("returns everything under NO_FILTERS", () => {
    const marks = [mark({ type: ["fact"] }), mark({ type: ["topic"] })];
    expect(applyFilters(marks, NO_FILTERS)).toHaveLength(2);
  });
});

describe("facetValues", () => {
  it("collects distinct values in sorted order", () => {
    const marks = [
      mark({ mc: ["Fiction", "Real"] }),
      mark({ mc: ["Real"] }),
      mark({}),
    ];
    expect(facetValues(marks, "mc")).toEqual(["Fiction", "Real"]);
  });
});
