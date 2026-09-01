// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";

import tokensCss from "./tokens.css?inline";
import {
  THEME_STORAGE_KEY,
  TOKEN_DEFAULTS,
  TOKEN_NAMES,
  type Theme,
  applyTheme,
  channelsToHex,
  currentPalette,
  invalidatePalette,
  currentTheme,
  nextTheme,
  paletteFor,
  persistTheme,
  resolveTheme,
  storedTheme,
  themeToggleIcon,
  themeToggleTitle,
} from "./theme";

describe("resolveTheme", () => {
  it("honours an explicit stored choice over the system preference", () => {
    expect(resolveTheme("light", true)).toBe("light");
    expect(resolveTheme("dark", false)).toBe("dark");
  });

  it("follows the system when nothing is stored", () => {
    expect(resolveTheme(null, true)).toBe("dark");
    expect(resolveTheme(null, false)).toBe("light");
  });

  it("treats a corrupted stored value as unset rather than erroring", () => {
    expect(resolveTheme("chartreuse", true)).toBe("dark");
    expect(resolveTheme("", false)).toBe("light");
  });
});

describe("nextTheme", () => {
  it("flips", () => {
    expect(nextTheme("dark")).toBe("light");
    expect(nextTheme("light")).toBe("dark");
  });
});

describe("paletteFor", () => {
  it("gives every neutral a value in both themes", () => {
    const dark = paletteFor("dark");
    const light = paletteFor("light");

    expect(Object.keys(dark)).toEqual(Object.keys(light));
    for (const value of [...Object.values(dark), ...Object.values(light)]) {
      expect(value).toMatch(/^#[0-9a-f]{6}$/i);
    }
  });

  it("differs between themes wherever it matters", () => {
    const dark = paletteFor("dark");
    const light = paletteFor("light");
    // The break marker paints over the baseline, so a shared value would leave
    // a dark rectangle sitting on a white page.
    expect(dark.surfaceChrome).not.toBe(light.surfaceChrome);
    expect(dark.nodeLabel).not.toBe(light.nodeLabel);
    expect(dark.axis).not.toBe(light.axis);
  });
});

describe("toggle affordance", () => {
  it("shows where the click leads, not where you are", () => {
    expect(themeToggleIcon("dark")).toBe("☀");
    expect(themeToggleIcon("light")).toBe("☾");
    expect(themeToggleTitle("dark")).toContain("light");
    expect(themeToggleTitle("light")).toContain("dark");
  });
});

describe("applyTheme", () => {
  let root: HTMLElement;

  beforeEach(() => {
    root = document.createElement("html");
  });

  it("adds the dark class and removes it again", () => {
    applyTheme("dark", root);
    expect(root.classList.contains("dark")).toBe(true);

    applyTheme("light", root);
    expect(root.classList.contains("dark")).toBe(false);
  });

  it("sets colorScheme so form controls and scrollbars follow", () => {
    applyTheme("dark", root);
    expect(root.style.colorScheme).toBe("dark");
  });

  it("round-trips through currentTheme", () => {
    applyTheme("dark", root);
    expect(currentTheme(root)).toBe("dark");

    applyTheme("light", root);
    expect(currentTheme(root)).toBe("light");
  });
});

describe("persistence", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("round-trips a choice", () => {
    persistTheme("dark");
    expect(storedTheme()).toBe("dark");
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");
  });

  it("reads null when nothing was ever stored", () => {
    expect(storedTheme()).toBeNull();
  });

  it("survives storage being unavailable", () => {
    // Blocked or private-mode storage throws on access. A theme that does not
    // persist beats a page that does not load.
    const getItem = Storage.prototype.getItem;
    const setItem = Storage.prototype.setItem;
    Storage.prototype.getItem = () => {
      throw new Error("blocked");
    };
    Storage.prototype.setItem = () => {
      throw new Error("blocked");
    };

    try {
      expect(() => persistTheme("dark")).not.toThrow();
      expect(storedTheme()).toBeNull();
    } finally {
      Storage.prototype.getItem = getItem;
      Storage.prototype.setItem = setItem;
    }
  });
});

/**
 * The two halves of the token table, held together.
 *
 * `tokens.css` is what the browser reads and `TOKEN_DEFAULTS` is what a module
 * with no stylesheet falls back to. They are the same nine colours written
 * twice, which is exactly the arrangement that let the timeline axis drift away
 * from the header it sits under. This is the test that makes the drift
 * impossible rather than merely unlikely.
 */
describe("the token defaults", () => {
  const block = (name: string): Record<string, string> => {
    const match = tokensCss.match(new RegExp(`${name}\\s*\\{([^}]*)\\}`));
    if (match === null) throw new Error(`tokens.css has no ${name} block`);
    return Object.fromEntries(
      [...match[1].matchAll(/(--[a-z-]+):\s*([^;]+);/g)].map(([, token, value]) => [
        token,
        channelsToHex(value.replace(/\/\*.*?\*\//g, "")) ?? value.trim(),
      ]),
    );
  };

  it.each([
    ["light", ":root"],
    ["dark", "\\.dark"],
  ])("matches the stylesheet for %s", (theme, selector) => {
    expect(block(selector)).toEqual(TOKEN_DEFAULTS[theme as Theme]);
  });

  it("declares every token the palette derives from", () => {
    for (const name of TOKEN_NAMES) {
      expect(TOKEN_DEFAULTS.light[name]).toMatch(/^#[0-9a-f]{6}$/);
      expect(TOKEN_DEFAULTS.dark[name]).toMatch(/^#[0-9a-f]{6}$/);
    }
  });
});

describe("channelsToHex", () => {
  it("reads the form a token holds", () => {
    expect(channelsToHex("209 213 219")).toBe("#d1d5db");
    expect(channelsToHex("  3 7 18  ")).toBe("#030712");
    expect(channelsToHex("229, 231, 235")).toBe("#e5e7eb");
  });

  it("refuses anything it cannot read, so the caller falls back", () => {
    // An empty string is the ordinary case: no stylesheet has loaded yet.
    expect(channelsToHex("")).toBeNull();
    expect(channelsToHex("209 213")).toBeNull();
    expect(channelsToHex("209 213 300")).toBeNull();
    expect(channelsToHex("#d1d5db")).toBeNull();
  });
});

describe("currentPalette caching", () => {
  const root = () => document.documentElement;

  beforeEach(() => {
    applyTheme("light");
    invalidatePalette();
  });

  it("reads the custom properties once, not once per render", () => {
    // The timeline re-renders on every frame of a pan. Nine getComputedStyle
    // reads in that loop is a forced reflow per frame, which is the one
    // performance trap this design names.
    const spy = vi.spyOn(window, "getComputedStyle");
    currentPalette();
    const afterFirst = spy.mock.calls.length;
    for (let i = 0; i < 20; i += 1) currentPalette();
    expect(spy.mock.calls.length).toBe(afterFirst);
    spy.mockRestore();
  });

  it("invalidates when the theme changes", () => {
    expect(currentPalette().surfaceChrome).toBe(TOKEN_DEFAULTS.light["--surface-chrome"]);
    applyTheme("dark");
    expect(currentPalette().surfaceChrome).toBe(TOKEN_DEFAULTS.dark["--surface-chrome"]);
  });

  it("picks up an override once told to", () => {
    // What C2 will do: set the variable, then invalidate.
    root().style.setProperty("--surface-chrome", "0 128 0");
    invalidatePalette();
    expect(currentPalette().surfaceChrome).toBe("#008000");
    root().style.removeProperty("--surface-chrome");
    invalidatePalette();
  });

  it("falls back to the defaults where no stylesheet resolves the token", () => {
    // jsdom loads no CSS, so this is the ordinary path here — and the one that
    // keeps the drawn panels coloured before the stylesheet arrives.
    expect(currentPalette().nodeLabel).toBe(TOKEN_DEFAULTS.light["--text-primary"]);
  });
});
