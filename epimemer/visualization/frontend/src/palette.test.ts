// @vitest-environment jsdom
import { describe, expect, it } from "vitest";

import { nodeColor } from "./graph-panel";
import { markColor } from "./timeline-panel";
import { semanticPaletteFor, type Theme } from "./theme";

const THEMES: Theme[] = ["light", "dark"];

// #56: the two panels are the halves of one split pane, and they disagreed
// about what colour a fact is — graph green, timeline blue. A user watching a
// fact appear in one and move in the other saw it change kind. These assert the
// agreement rather than the values, so re-picking a hue stays a one-line change
// and drifting apart again does not.
describe("the semantic palette is shared", () => {
  for (const theme of THEMES) {
    it(`gives the graph and the timeline the same fact colour (${theme})`, () => {
      expect(nodeColor("fact", theme)).toBe(markColor("fact", theme));
    });

    it(`gives the graph and the timeline the same inference colour (${theme})`, () => {
      expect(nodeColor("inference", theme)).toBe(markColor("inference", theme));
    });
  }
});

describe("semanticPaletteFor", () => {
  // Not decoration: these hues are how a panel says what kind of thing you are
  // looking at, so two meanings sharing one is the panel saying nothing.
  const MEANINGS = [
    "topic",
    "fact",
    "inference",
    "segment",
    "document",
    "historical",
    "pending",
    "contradiction",
    "selection",
  ] as const;

  for (const theme of THEMES) {
    it(`gives every distinct meaning a distinct hue (${theme})`, () => {
      const palette = semanticPaletteFor(theme);
      const used = MEANINGS.map((meaning) => palette[meaning]);
      expect(new Set(used).size).toBe(MEANINGS.length);
    });
  }

  it("varies the load-bearing hues by theme", () => {
    // The older palette was one value per hue on the grounds that a saturated
    // colour reads on either background. That is what let the two tables drift:
    // neither had a theme axis to reconcile. The set adopted in C.6 was
    // validated per theme, so the type must carry that.
    const light = semanticPaletteFor("light");
    const dark = semanticPaletteFor("dark");
    expect(light.fact).not.toBe(dark.fact);
    expect(light.inference).not.toBe(dark.inference);
    expect(light.topic).not.toBe(dark.topic);
  });
});
