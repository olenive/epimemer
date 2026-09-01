import { describe, expect, it } from "vitest";

import {
  AA_SMALL_TEXT,
  contrastRatio,
  formatRatio,
  hexToRgb,
  meetsAA,
  relativeLuminance,
} from "./contrast";

describe("hexToRgb", () => {
  it("reads a six-digit hex in either case", () => {
    expect(hexToRgb("#d1d5db")).toEqual([209, 213, 219]);
    expect(hexToRgb("#D1D5DB")).toEqual([209, 213, 219]);
    expect(hexToRgb("#000000")).toEqual([0, 0, 0]);
  });

  it("refuses anything else, rather than guessing", () => {
    // A shorthand is a plausible thing to be handed and is not what the picker
    // emits; reading it as a colour would be inventing three channels.
    expect(hexToRgb("#fff")).toBeNull();
    expect(hexToRgb("d1d5db")).toBeNull();
    expect(hexToRgb("rgb(1,2,3)")).toBeNull();
    expect(hexToRgb("")).toBeNull();
  });
});

describe("relativeLuminance", () => {
  it("puts black at 0 and white at 1", () => {
    expect(relativeLuminance([0, 0, 0])).toBe(0);
    expect(relativeLuminance([255, 255, 255])).toBeCloseTo(1, 10);
  });

  it("uses the linear branch below the specification's knee", () => {
    // Channels at or under 0.03928 are divided rather than raised to a power,
    // and getting that boundary wrong shows up only in very dark colours.
    expect(relativeLuminance([10, 10, 10])).toBeCloseTo(0.0030352, 6);
  });
});

describe("contrastRatio", () => {
  it("gives 21 for black on white, the largest there is", () => {
    expect(contrastRatio("#000000", "#ffffff")).toBeCloseTo(21, 10);
  });

  it("gives 1 for a colour on itself", () => {
    expect(contrastRatio("#4b5563", "#4b5563")).toBeCloseTo(1, 10);
  });

  it("is symmetric, so the caller need not know which is the text", () => {
    const a = contrastRatio("#6b7280", "#d1d5db");
    const b = contrastRatio("#d1d5db", "#6b7280");
    expect(a).not.toBeNull();
    expect(a).toBeCloseTo(b as number, 12);
  });

  it("matches the published value for the classic AA grey", () => {
    // #767676 on white is 4.54:1, the darkest grey that passes small-text AA,
    // and it is the value to check the arithmetic against: an implementation
    // using sRGB's own inverse gamma rather than the specification's curve
    // lands near here but not on it.
    expect(contrastRatio("#767676", "#ffffff")).toBeCloseTo(4.54, 2);
  });

  it("returns null when either colour is unreadable", () => {
    expect(contrastRatio("#fff", "#000000")).toBeNull();
    expect(contrastRatio("#ffffff", "not a colour")).toBeNull();
  });
});

describe("meetsAA", () => {
  it("treats the floor itself as passing", () => {
    // 4.5 is stated as a minimum, so the boundary belongs on the passing side.
    expect(meetsAA(AA_SMALL_TEXT)).toBe(true);
    expect(meetsAA(4.4999)).toBe(false);
    expect(meetsAA(21)).toBe(true);
  });
});

describe("formatRatio", () => {
  it("shows one decimal, the form the badge has room for", () => {
    expect(formatRatio(4.54)).toBe("4.5");
    expect(formatRatio(21)).toBe("21.0");
  });
});
