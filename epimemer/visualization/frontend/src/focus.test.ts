import { describe, expect, it } from "vitest";

import { nodeFill, refreshedFill, statusOpacity } from "./graph-panel";
import { desaturate, semanticPaletteFor, type Theme } from "./theme";
import { markFillFor } from "./timeline-panel";

const THEMES: Theme[] = ["light", "dark"];

/**
 * Focus mode dims by desaturation, never by opacity
 * (RETRIEVAL_PROVENANCE.md §4.1).
 *
 * `statusOpacity` already maps retired → faded, and its comment exists because
 * two retired states once drew as live (#55). If focus mode also dimmed by
 * opacity, *retired + retrieved* and *active + not-retrieved* would land at the
 * same alpha — one channel carrying two meanings, decided in two files, and
 * #55 silently re-opened in one mode.
 */
describe("test_focus_mode_leaves_status_opacity_alone", () => {
  it("keeps a retired-and-retrieved node distinguishable from an active-and-not", () => {
    const retiredAndRetrieved = {
      fill: nodeFill("fact", "light", true),
      opacity: statusOpacity("historical"),
    };
    const activeAndNotRetrieved = {
      fill: nodeFill("fact", "light", false),
      opacity: statusOpacity("active"),
    };

    // An opacity-based implementation collapses these two into one appearance.
    expect(retiredAndRetrieved).not.toEqual(activeAndNotRetrieved);
    expect(retiredAndRetrieved.fill).not.toBe(activeAndNotRetrieved.fill);
    expect(retiredAndRetrieved.opacity).not.toBe(activeAndNotRetrieved.opacity);
  });

  it("does not touch opacity at all", () => {
    // The two channels never meet in a caller: focus owns saturation, status
    // owns opacity, and neither function takes the other's argument.
    expect(statusOpacity("active")).toBe(1.0);
    expect(nodeFill.length).toBe(3);
    expect(statusOpacity.length).toBe(1);
  });

  it("draws a focused node in the palette's own colour, unchanged", () => {
    for (const theme of THEMES) {
      expect(nodeFill("fact", theme, true)).toBe(semanticPaletteFor(theme).fact);
    }
  });
});

/**
 * `applyTheme` used to recompute `color` from node type and theme **alone**, so
 * toggling the theme while focus mode was on restored every node to full
 * saturation and silently left the mode — colour decided in two places, which
 * is the #56 failure exactly.
 */
describe("test_theme_toggle_preserves_focus_desaturation", () => {
  it("recomputes a dimmed node dimmed, in either theme", () => {
    for (const theme of THEMES) {
      const recomputed = refreshedFill({ nodeType: "fact", inFocus: false }, theme);
      expect(recomputed).toBe(nodeFill("fact", theme, false));
      expect(recomputed).not.toBe(semanticPaletteFor(theme).fact);
    }
  });

  it("recomputes a focused node at full saturation", () => {
    for (const theme of THEMES) {
      expect(refreshedFill({ nodeType: "fact", inFocus: true }, theme)).toBe(
        semanticPaletteFor(theme).fact,
      );
    }
  });

  it("treats a node with no focus state as in focus", () => {
    // Nothing is dimmed until a record is selected, so the absent case is the
    // normal one and must not read as "not retrieved".
    for (const theme of THEMES) {
      expect(refreshedFill({ nodeType: "fact" }, theme)).toBe(
        semanticPaletteFor(theme).fact,
      );
    }
  });
});

/**
 * §4.2: dim only the graph and the two panels disagree about what came back —
 * the class of bug #56 fixed for colour, one mode later.
 */
describe("test_focus_mode_applies_to_both_panels", () => {
  it("dims a fact the same way in the graph and on the timeline", () => {
    for (const theme of THEMES) {
      expect(markFillFor("fact", theme, false)).toBe(nodeFill("fact", theme, false));
      expect(markFillFor("inference", theme, false)).toBe(
        nodeFill("inference", theme, false),
      );
    }
  });

  it("leaves both at full saturation when nothing is dimmed", () => {
    for (const theme of THEMES) {
      expect(markFillFor("fact", theme, true)).toBe(nodeFill("fact", theme, true));
    }
  });
});

describe("desaturate", () => {
  it("keeps the colour a colour rather than blanking it", () => {
    const dimmed = desaturate("#2a78d6");
    expect(dimmed).toMatch(/^#[0-9a-f]{6}$/);
    expect(dimmed).not.toBe("#2a78d6");
  });

  it("holds lightness, because lightness is opacity's channel", () => {
    // A desaturation that also darkened would be an opacity change wearing a
    // different name, and would collide with status all over again.
    const luma = (hex: string): number => {
      const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
      return 0.2126 * r + 0.7152 * g + 0.0722 * b;
    };
    for (const hex of ["#2a78d6", "#1baf7a", "#9085e9"]) {
      expect(luma(desaturate(hex))).toBeCloseTo(luma(hex), 0);
    }
  });

  it("moves the hue toward grey rather than toward another hue", () => {
    // The property, rather than a fixed output: every channel ends nearer the
    // others than it started. It is deliberately *not* idempotent — dimming is
    // applied once, from the palette colour, and `nodeFill` is the only caller.
    const spread = (hex: string): number => {
      const channels = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
      return Math.max(...channels) - Math.min(...channels);
    };
    for (const hex of ["#2a78d6", "#1baf7a", "#9085e9"]) {
      expect(spread(desaturate(hex))).toBeLessThan(spread(hex));
    }
  });

  it("leaves a grey alone", () => {
    expect(desaturate("#808080")).toBe("#808080");
  });
});
