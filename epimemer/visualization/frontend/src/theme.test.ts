// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from "vitest";

import {
  THEME_STORAGE_KEY,
  applyTheme,
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
