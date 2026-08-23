// @vitest-environment jsdom
import { describe, expect, it } from "vitest";

import {
  edgeColor,
  filterAfterHighlight,
  highlightNote,
  missingFrom,
  statusOpacity,
} from "./graph-panel";
import { semanticPaletteFor, type Theme } from "./theme";

// The hazard this guards is the gap between a Python enum and a TypeScript
// lookup table: `NodeStatus` grew `corrected` and `historical` in 666904f and
// the table did not, so retired nodes drew at full opacity (#55). The fix is
// the *default*, not two more keys — the next status added must be safe
// without anyone remembering this file exists.
describe("statusOpacity", () => {
  it("draws active nodes at full opacity", () => {
    expect(statusOpacity("active")).toBe(1.0);
  });

  it("fades every status that has left the active set", () => {
    for (const status of [
      "superseded",
      "corrected",
      "historical",
      "merged",
      "archived",
    ]) {
      expect(statusOpacity(status)).toBeLessThan(1.0);
    }
  });

  it("fades a status it has never heard of rather than drawing it as live", () => {
    // The assertion that fails before the fix. A status this table does not
    // know is, by construction, one nobody has checked — and of the two ways
    // to be wrong, drawing a retired node as live is the harmful one.
    expect(statusOpacity("quarantined")).toBeLessThan(1.0);
    expect(statusOpacity("")).toBeLessThan(1.0);
  });
});

// The same Python-enum-to-lookup-table gap, one layer over: `EdgeType` grew
// `temporally_followed_by` for #53's world-changes, and an edge kind this table
// has never heard of draws in the unknown-kind neutral rather than as lineage.
describe("edgeColor", () => {
  const THEMES: Theme[] = ["light", "dark"];
  // The neutral `graph-panel` falls through to for a kind it has never heard
  // of. Spelled here rather than imported because the point is to catch the
  // table and the enum drifting apart, and a shared constant would move with
  // whichever side changed.
  const UNKNOWN_KIND = "#9ca3af";

  for (const theme of THEMES) {
    it(`draws a world-change transition as lineage (${theme})`, () => {
      // Same hue as `superseded_by` on purpose: *which* retirement happened is
      // the node's status colour to say, and saying it twice lets the two
      // readings disagree.
      expect(edgeColor("temporally_followed_by", theme))
        .toBe(semanticPaletteFor(theme).lineage);
      expect(edgeColor("temporally_followed_by", theme))
        .toBe(edgeColor("superseded_by", theme));
    });

    it(`draws a judged pair apart from a corroborating one (${theme})`, () => {
      // `assessed` is written for both verdicts — "one claim" and "different
      // claims" alike — while `similarity` is written only for the first, and
      // only the first is counted as support. Two edge types that mean
      // different things must not read as one colour, and neither may fall
      // through to the unknown-kind neutral.
      expect(edgeColor("assessed", theme))
        .toBe(semanticPaletteFor(theme).assessed);
      expect(edgeColor("assessed", theme))
        .not.toBe(edgeColor("similarity", theme));
      expect(edgeColor("assessed", theme)).not.toBe(UNKNOWN_KIND);
    });

    it(`draws a withdrawn similarity apart from a standing one (${theme})`, () => {
      // #68 leaves both edges in the graph — nothing deletes — so both draw,
      // and a withdrawal wearing the similarity hue would show agreement that
      // has been taken back.
      expect(edgeColor("retracted_similarity", theme))
        .not.toBe(edgeColor("similarity", theme));
      expect(edgeColor("retracted_similarity", theme)).not.toBe(UNKNOWN_KIND);
    });

    it(`draws every pair judgment in a hue of its own (${theme})`, () => {
      // The family where colour carries the meaning: each of these says
      // something different about the *same two nodes*, and the panel draws
      // them on top of each other. Grey is a fine default for a structural
      // edge — `sourced_from`, `tagged_with` and eleven others take it — but a
      // judgment that falls through is the #55 failure: an assertion reaching
      // the browser and drawing as an anonymous neutral nobody reads.
      //
      // `variant_of` had been doing exactly that since it was introduced, and
      // was found only because #68 added a row beside it. Spelled out rather
      // than imported for `UNKNOWN_KIND`'s reason: a shared constant moves with
      // whichever side changed, and drift is what is being caught.
      const PAIR_JUDGMENTS = [
        "similarity", "contradiction", "assessed",
        "retracted_similarity", "variant_of",
      ];

      const grey = PAIR_JUDGMENTS.filter(
        (type) => edgeColor(type, theme) === UNKNOWN_KIND,
      );
      expect(grey).toEqual([]);
    });
  }
});

// EVENT_LOG.md §7 / RETRIEVAL_PROVENANCE.md §4.4 — the same two silent failures,
// closed once rather than twice. `highlightNodes` is about to be driven from a
// log entry and from a retrieval record, and in both cases a click that does
// nothing, with nothing said, is indistinguishable from a broken panel.
describe("test_highlight_reports_an_id_absent_from_the_graph", () => {
  it("names the ids the graph does not hold", () => {
    // `cy.getElementById(id)` returns an empty collection and `.addClass` is a
    // no-op, so the failure is entirely silent without this.
    expect(missingFrom(["a", "b"], ["a", "c"])).toEqual(["c"]);
  });

  it("reports nothing when every id is present", () => {
    expect(missingFrom(["a", "b"], ["a", "b"])).toEqual([]);
    expect(highlightNote({ highlighted: ["a"], missing: [], filterCleared: false }))
      .toBeNull();
  });

  it("says so in words, because the colour cannot", () => {
    const note = highlightNote({
      highlighted: ["a"],
      missing: ["c", "d"],
      filterCleared: false,
    });
    expect(note).toContain("2");
    expect(note).toMatch(/not in this graph/i);
  });

  it("distinguishes nothing-highlighted from partly-highlighted", () => {
    const note = highlightNote({
      highlighted: [],
      missing: ["c"],
      filterCleared: false,
    });
    expect(note).toMatch(/not in this graph/i);
  });

  it("mentions a cleared filter, so the panel changing is explained", () => {
    const note = highlightNote({
      highlighted: ["a"],
      missing: [],
      filterCleared: true,
    });
    expect(note).toMatch(/type filter/i);
  });
});

describe("test_highlight_clears_a_conflicting_type_filter", () => {
  it("clears a filter that would hide what is being highlighted", () => {
    // The type filter sets `display: none` (graph-panel.ts, runLayout), so the
    // class lands on something invisible: same symptom, different cause.
    expect(filterAfterHighlight("fact", ["topic"])).toBe("all");
    expect(filterAfterHighlight("fact", ["fact", "inference"])).toBe("all");
  });

  it("leaves a filter that hides none of them", () => {
    // Clearing unconditionally would undo a filter the user set, every time
    // they clicked a log entry about a fact.
    expect(filterAfterHighlight("fact", ["fact"])).toBe("fact");
    expect(filterAfterHighlight("fact", [])).toBe("fact");
  });

  it("has nothing to clear when no filter is set", () => {
    expect(filterAfterHighlight("all", ["topic", "fact"])).toBe("all");
  });
});
