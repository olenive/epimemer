// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  PALETTE_STORAGE_KEY,
  PALETTE_VERSION,
  applyPalette,
  clearStoredPalette,
  hasOverrides,
  isHex,
  noOverrides,
  parsePalette,
  persistPalette,
  readStoredPalette,
  resetRequested,
  resolveTokens,
  serializePalette,
  withOverride,
  withoutOverride,
} from "./palette-store";
import { TOKEN_DEFAULTS, currentPalette, invalidatePalette } from "./theme";

beforeEach(() => {
  localStorage.clear();
  document.documentElement.removeAttribute("style");
  document.documentElement.classList.remove("dark");
  invalidatePalette();
});

describe("isHex", () => {
  it("accepts the form the colour input emits and nothing looser", () => {
    expect(isHex("#d1d5db")).toBe(true);
    expect(isHex("#D1D5DB")).toBe(true);
    expect(isHex("#fff")).toBe(false);
    expect(isHex("red")).toBe(false);
    expect(isHex(null)).toBe(false);
    expect(isHex(42)).toBe(false);
  });
});

describe("resolveTokens", () => {
  it("is the defaults when nothing is overridden", () => {
    expect(resolveTokens("light", noOverrides())).toEqual(TOKEN_DEFAULTS.light);
    expect(resolveTokens("dark", noOverrides())).toEqual(TOKEN_DEFAULTS.dark);
  });

  it("merges rather than replaces, so untouched tokens keep their defaults", () => {
    const stored = withOverride(noOverrides(), "light", "--surface-chrome", "#123456");
    const resolved = resolveTokens("light", stored);

    expect(resolved["--surface-chrome"]).toBe("#123456");
    expect(resolved["--text-primary"]).toBe(TOKEN_DEFAULTS.light["--text-primary"]);
    expect(Object.keys(resolved).sort()).toEqual(Object.keys(TOKEN_DEFAULTS.light).sort());
  });
});

describe("overrides are per theme", () => {
  it("does not let a choice made in one theme reach the other", () => {
    // A single shared map would mean picking a dark-mode colour silently
    // destroying light mode, which the viewer would have to notice to repair.
    const stored = withOverride(noOverrides(), "dark", "--surface-page", "#123456");

    expect(resolveTokens("dark", stored)["--surface-page"]).toBe("#123456");
    expect(resolveTokens("light", stored)["--surface-page"]).toBe(
      TOKEN_DEFAULTS.light["--surface-page"],
    );
  });

  it("reports per theme whether there is anything to reset", () => {
    const stored = withOverride(noOverrides(), "dark", "--border", "#123456");
    expect(hasOverrides(stored, "dark")).toBe(true);
    expect(hasOverrides(stored, "light")).toBe(false);
  });
});

describe("withOverride", () => {
  it("refuses a colour it cannot read rather than storing it", () => {
    const stored = withOverride(noOverrides(), "light", "--border", "not a colour");
    expect(stored).toEqual(noOverrides());
  });

  it("leaves the value it was given alone", () => {
    const before = noOverrides();
    withOverride(before, "light", "--border", "#123456");
    expect(before.light).toEqual({});
  });
});

describe("withoutOverride", () => {
  it("restores one token and leaves the rest standing", () => {
    const stored = withOverride(
      withOverride(noOverrides(), "light", "--border", "#111111"),
      "light",
      "--text-muted",
      "#222222",
    );
    const after = withoutOverride(stored, "light", "--border");

    expect(resolveTokens("light", after)["--border"]).toBe(TOKEN_DEFAULTS.light["--border"]);
    expect(after.light["--text-muted"]).toBe("#222222");
  });

  it("is untroubled by a token that was never overridden", () => {
    expect(withoutOverride(noOverrides(), "light", "--border")).toEqual(noOverrides());
  });
});

describe("parsePalette", () => {
  it("round-trips what was serialized", () => {
    const stored = withOverride(noOverrides(), "dark", "--surface-chrome", "#abcdef");
    expect(parsePalette(serializePalette(stored))).toEqual(stored);
  });

  it("falls back to the defaults on anything it cannot read", () => {
    // Each of these is a different way of arriving at the same answer, and the
    // defaults are the one answer that is never wrong.
    expect(parsePalette(null)).toEqual(noOverrides());
    expect(parsePalette("{not json")).toEqual(noOverrides());
    expect(parsePalette("[]")).toEqual(noOverrides());
    expect(parsePalette("null")).toEqual(noOverrides());
    expect(parsePalette('"a string"')).toEqual(noOverrides());
  });

  it("discards a version it does not know rather than reading it", () => {
    // Reading a shape written by different rules is how a stored preference
    // turns into a page painted from a misinterpretation.
    const future = JSON.stringify({ version: PALETTE_VERSION + 1, light: { "--border": "#123456" } });
    expect(parsePalette(future)).toEqual(noOverrides());
    expect(parsePalette(JSON.stringify({ light: { "--border": "#123456" } }))).toEqual(
      noOverrides(),
    );
  });

  it("drops an unknown token and a malformed colour, keeping the rest", () => {
    const raw = JSON.stringify({
      version: PALETTE_VERSION,
      light: { "--border": "#123456", "--not-a-token": "#000000", "--text-muted": "chartreuse" },
      dark: {},
    });
    expect(parsePalette(raw).light).toEqual({ "--border": "#123456" });
  });
});

describe("storage", () => {
  it("persists and reads back", () => {
    const stored = withOverride(noOverrides(), "light", "--border", "#123456");
    persistPalette(stored);

    expect(localStorage.getItem(PALETTE_STORAGE_KEY)).toBe(serializePalette(stored));
    expect(readStoredPalette()).toEqual(stored);
  });

  it("clears", () => {
    persistPalette(withOverride(noOverrides(), "light", "--border", "#123456"));
    clearStoredPalette();

    expect(localStorage.getItem(PALETTE_STORAGE_KEY)).toBeNull();
    expect(readStoredPalette()).toEqual(noOverrides());
  });

  it("gives the defaults when storage throws rather than failing the page", () => {
    const spy = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(readStoredPalette()).toEqual(noOverrides());
    spy.mockRestore();
  });

  it("swallows a write that throws", () => {
    const spy = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("full");
    });
    expect(() => persistPalette(noOverrides())).not.toThrow();
    spy.mockRestore();
  });
});

describe("applyPalette", () => {
  const root = () => document.documentElement;

  it("writes every token as channels, not only the overridden ones", () => {
    // Writing only the overrides would leave a cleared one standing as an
    // inline property that outranks the stylesheet.
    applyPalette("light", noOverrides());

    expect(root().style.getPropertyValue("--surface-chrome")).toBe("209 213 219");
    expect(root().style.getPropertyValue("--text-muted")).toBe("75 85 99");
  });

  it("puts an override on the document and into the palette the panels read", () => {
    applyPalette("light", withOverride(noOverrides(), "light", "--surface-chrome", "#008000"));

    expect(root().style.getPropertyValue("--surface-chrome")).toBe("0 128 0");
    expect(currentPalette().surfaceChrome).toBe("#008000");
  });

  it("replaces the previous theme's values on a switch", () => {
    applyPalette("light", noOverrides());
    applyPalette("dark", noOverrides());

    expect(root().style.getPropertyValue("--surface-page")).toBe("3 7 18");
  });
});

describe("resetRequested", () => {
  it("recognises the way back", () => {
    // The escape hatch for a palette that has painted its own reset shut.
    expect(resetRequested("?palette=reset")).toBe(true);
    expect(resetRequested("?session=3&palette=reset")).toBe(true);
    expect(resetRequested("?palette=green")).toBe(false);
    expect(resetRequested("")).toBe(false);
  });
});
