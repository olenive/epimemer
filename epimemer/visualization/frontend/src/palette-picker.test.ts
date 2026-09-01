// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  CONTRAST_SURFACE,
  PALETTE_GROUPS,
  initPalettePicker,
  isTextToken,
  readingFor,
} from "./palette-picker";
import {
  PALETTE_STORAGE_KEY,
  type StoredPalette,
  applyPalette,
  clearStoredPalette,
  noOverrides,
  persistPalette,
  readStoredPalette,
  resolveTokens,
} from "./palette-store";
import { TOKEN_DEFAULTS, TOKEN_NAMES, invalidatePalette } from "./theme";

describe("the rows", () => {
  it("covers every token exactly once", () => {
    // A token nobody can reach from the panel is a colour the viewer is stuck
    // with, and the panel gives no hint that it exists.
    const listed = PALETTE_GROUPS.flatMap((group) => group.rows.map((row) => row.token));
    expect([...listed].sort()).toEqual([...TOKEN_NAMES].sort());
  });

  it("names them for a viewer rather than for the stylesheet", () => {
    for (const group of PALETTE_GROUPS) {
      for (const row of group.rows) {
        expect(row.label).not.toContain("--");
      }
    }
  });
});

describe("readingFor", () => {
  const tokens = TOKEN_DEFAULTS.light;

  it("measures text against the panel surface", () => {
    // The pairing the light-mode darkening pass was decided on: captions on
    // panels came out at 3.28:1, which is the number that started this.
    expect(CONTRAST_SURFACE).toBe("--surface-chrome");
    const reading = readingFor("--text-muted", { ...tokens, "--text-muted": "#6b7280" });
    expect(reading?.ratio).toBeCloseTo(3.28, 2);
    expect(reading?.passes).toBe(false);
    expect(reading?.label).toBe("⚠ 3.3");
  });

  it("passes a colour that clears the floor", () => {
    const reading = readingFor("--text-primary", tokens);
    expect(reading?.passes).toBe(true);
    expect(reading?.label.startsWith("AA ")).toBe(true);
  });

  it("says nothing about a surface token", () => {
    expect(readingFor("--surface-page", tokens)).toBeNull();
    expect(isTextToken("--surface-page")).toBe(false);
    expect(isTextToken("--text-muted")).toBe(true);
  });
});

describe("the picker", () => {
  let palette: StoredPalette;
  let button: HTMLElement;
  let panel: HTMLElement;

  const build = () => {
    document.body.innerHTML = '<button id="b"></button><div id="p" class="hidden"></div>';
    button = document.getElementById("b") as HTMLElement;
    panel = document.getElementById("p") as HTMLElement;
    return initPalettePicker({
      button,
      panel,
      theme: () => "light",
      read: () => palette,
      write: (next, persist) => {
        palette = next;
        if (persist) persistPalette(next);
        applyPalette("light", next);
      },
      reset: () => {
        clearStoredPalette();
        palette = noOverrides();
        applyPalette("light", palette);
      },
    });
  };

  const swatchFor = (token: string): HTMLInputElement => {
    const row = panel.querySelector(`[data-token="${token}"]`);
    const input = row?.querySelector("input");
    if (!(input instanceof HTMLInputElement)) throw new Error(`no swatch for ${token}`);
    return input;
  };

  const fire = (input: HTMLInputElement, value: string, type: "input" | "change") => {
    input.value = value;
    input.dispatchEvent(new Event(type));
  };

  beforeEach(() => {
    localStorage.clear();
    document.documentElement.removeAttribute("style");
    invalidatePalette();
    palette = noOverrides();
  });

  it("opens and closes from the button", () => {
    const picker = build();
    expect(picker.isOpen()).toBe(false);
    button.click();
    expect(picker.isOpen()).toBe(true);
    button.click();
    expect(picker.isOpen()).toBe(false);
  });

  it("closes on Escape, which is the way out of a dropdown", () => {
    const picker = build();
    picker.open();
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    expect(picker.isOpen()).toBe(false);
  });

  it("shows the resolved value of every token", () => {
    palette = { version: 1, light: { "--border": "#123456" }, dark: {} };
    const picker = build();
    picker.open();

    expect(swatchFor("--border").value).toBe("#123456");
    expect(swatchFor("--text-primary").value).toBe(TOKEN_DEFAULTS.light["--text-primary"]);
  });

  it("writes the custom property on :root when a colour changes", () => {
    const picker = build();
    picker.open();
    fire(swatchFor("--surface-chrome"), "#008000", "change");

    expect(document.documentElement.style.getPropertyValue("--surface-chrome")).toBe("0 128 0");
  });

  it("follows the drag live but only stores where it stopped", () => {
    // `input` fires per frame while a swatch is dragged. Serializing to storage
    // on each one would be a write per pointer move for a value the viewer has
    // not settled on yet.
    const picker = build();
    picker.open();
    const swatch = swatchFor("--surface-page");

    fire(swatch, "#010203", "input");
    expect(resolveTokens("light", palette)["--surface-page"]).toBe("#010203");
    expect(localStorage.getItem(PALETTE_STORAGE_KEY)).toBeNull();

    fire(swatch, "#040506", "change");
    expect(readStoredPalette().light["--surface-page"]).toBe("#040506");
  });

  it("resets one token and leaves the others standing", () => {
    const picker = build();
    picker.open();
    fire(swatchFor("--border"), "#111111", "change");
    fire(swatchFor("--text-muted"), "#222222", "change");

    const revert = panel.querySelector<HTMLElement>('[data-token="--border"] button');
    revert?.click();

    expect(readStoredPalette().light["--border"]).toBeUndefined();
    expect(readStoredPalette().light["--text-muted"]).toBe("#222222");
    expect(swatchFor("--border").value).toBe(TOKEN_DEFAULTS.light["--border"]);
  });

  it("clears storage on reset all", () => {
    const picker = build();
    picker.open();
    fire(swatchFor("--border"), "#111111", "change");
    expect(localStorage.getItem(PALETTE_STORAGE_KEY)).not.toBeNull();

    panel.querySelector<HTMLElement>("#palette-reset-all")?.click();

    expect(localStorage.getItem(PALETTE_STORAGE_KEY)).toBeNull();
    expect(swatchFor("--border").value).toBe(TOKEN_DEFAULTS.light["--border"]);
  });

  it("keeps the reset control off the tokens it can destroy", () => {
    // A picker that can paint the whole UI can paint its own escape hatch shut.
    const picker = build();
    picker.open();
    const resetAll = panel.querySelector<HTMLElement>("#palette-reset-all");

    expect(resetAll?.style.background).not.toBe("");
    expect(resetAll?.className).not.toContain("surface");
    expect(resetAll?.className).not.toContain("content");
  });

  it("repaints every badge when the surface moves, not only the row touched", () => {
    // The ratios are all measured against the panel surface, so changing it
    // moves four readings at once.
    const picker = build();
    picker.open();
    const before = panel.querySelector<HTMLElement>('[data-badge="--text-primary"]')?.textContent;

    fire(swatchFor("--surface-chrome"), "#3a3a3a", "change");
    const after = panel.querySelector<HTMLElement>('[data-badge="--text-primary"]')?.textContent;

    expect(after).not.toBe(before);
  });

  it("warns when a text colour falls below the floor", () => {
    const picker = build();
    picker.open();
    fire(swatchFor("--text-primary"), "#c8cbd0", "change");

    const badge = panel.querySelector<HTMLElement>('[data-badge="--text-primary"]');
    expect(badge?.textContent).toContain("⚠");
    expect(badge?.className).toContain("text-red-600");
  });

  it("gives no badge to a surface row", () => {
    const picker = build();
    picker.open();
    expect(panel.querySelector('[data-token="--surface-page"] [data-badge]')).toBeNull();
  });

  it("renders without touching storage until something is changed", () => {
    const spy = vi.spyOn(Storage.prototype, "setItem");
    const picker = build();
    picker.open();
    expect(spy).not.toHaveBeenCalled();
    spy.mockRestore();
  });
});
