/**
 * The colour dropdown: nine swatches, a contrast reading, and a way back.
 *
 * DOM only. Every rule about what a choice *means* lives in `palette-store.ts`,
 * and the arithmetic behind the badge lives in `contrast.ts`, so what is left
 * here is building rows and listening to them.
 *
 * **Live on `input`, persisted on `change`.** Dragging through a gradient fires
 * `input` per frame, and writing to `localStorage` on each one would be a
 * serialize-and-store per pointer move for a value the viewer has not settled
 * on. The page follows the drag; storage records where it stopped.
 *
 * **The way back is not themeable.** *Reset all* carries hard-coded inline
 * colours, because a picker that can paint the whole UI can paint its own
 * escape hatch shut — grey text on the same grey is two valid choices that
 * together say nothing. `?palette=reset` is the second way back, handled before
 * the page draws (`palette-store.ts`).
 */

import { AA_SMALL_TEXT, contrastRatio, formatRatio, meetsAA } from "./contrast";
import {
  type StoredPalette,
  resolveTokens,
  withOverride,
  withoutOverride,
} from "./palette-store";
import { type Theme, type TokenName } from "./theme";

export interface PaletteRow {
  token: TokenName;
  label: string;
}

export interface PaletteGroup {
  title: string;
  rows: PaletteRow[];
}

/**
 * The rows, grouped as the token table is.
 *
 * Names the viewer would use, not the token names: someone darkening the
 * dashboard is looking for *Panels*, not `--surface-chrome`. The C4 group of
 * semantic hues would sit below these; it is deliberately absent, because
 * making two node kinds settable to one colour needs an answer this phase does
 * not have.
 */
export const PALETTE_GROUPS: readonly PaletteGroup[] = [
  {
    title: "Surfaces",
    rows: [
      { token: "--surface-page", label: "Page" },
      { token: "--surface-chrome", label: "Panels" },
      { token: "--surface-raised", label: "Controls" },
      { token: "--surface-raised-hover", label: "Controls hover" },
      { token: "--border", label: "Borders" },
    ],
  },
  {
    title: "Text",
    rows: [
      { token: "--text-strong", label: "Headings" },
      { token: "--text-primary", label: "Body" },
      { token: "--text-secondary", label: "Labels" },
      { token: "--text-muted", label: "Captions" },
    ],
  },
];

/**
 * The surface every text ratio is measured against.
 *
 * One surface rather than a per-token guess: chrome is where the headers,
 * toolbars, drawers and trays put their text, and it is the pairing the
 * light-mode darkening pass was decided on — captions on panels came out at
 * 3.28:1, which is the number that started this.
 */
export const CONTRAST_SURFACE: TokenName = "--surface-chrome";

export const isTextToken = (token: TokenName): boolean => token.startsWith("--text-");

/** What the badge beside a text row says. */
export interface ContrastReading {
  ratio: number;
  passes: boolean;
  label: string;
}

export const readingFor = (
  token: TokenName,
  tokens: Record<TokenName, string>,
): ContrastReading | null => {
  if (!isTextToken(token)) return null;
  const ratio = contrastRatio(tokens[token], tokens[CONTRAST_SURFACE]);
  if (ratio === null) return null;
  const passes = meetsAA(ratio);
  return { ratio, passes, label: `${passes ? "AA" : "⚠"} ${formatRatio(ratio)}` };
};

export interface PalettePickerDeps {
  /** The paintbrush the dropdown hangs from. */
  button: HTMLElement;
  /** The empty container the rows are built into. */
  panel: HTMLElement;
  theme: () => Theme;
  read: () => StoredPalette;
  /** Apply a changed set. `persist` is false while a drag is still in flight. */
  write: (next: StoredPalette, persist: boolean) => void;
  /** Forget every choice, in storage as well as on the page. */
  reset: () => void;
}

export interface PalettePicker {
  render: () => void;
  open: () => void;
  close: () => void;
  isOpen: () => boolean;
}

const ROW_CLASS = "flex items-center gap-2 py-0.5 text-xs";

export const initPalettePicker = (deps: PalettePickerDeps): PalettePicker => {
  const { button, panel } = deps;

  const isOpen = (): boolean => !panel.classList.contains("hidden");
  const close = (): void => panel.classList.add("hidden");

  const render = (): void => {
    const theme = deps.theme();
    const tokens = resolveTokens(theme, deps.read());
    panel.replaceChildren();

    const heading = document.createElement("div");
    heading.className = "flex items-center justify-between pb-1 mb-1 border-b border-line";
    const title = document.createElement("span");
    title.className = "text-xs font-semibold text-content-strong";
    title.textContent = "Colours";
    const resetAll = document.createElement("button");
    resetAll.id = "palette-reset-all";
    resetAll.textContent = "Reset all";
    resetAll.className = "text-xs rounded px-1.5 py-0.5";
    // Deliberately not a token: this control has to stay legible whatever the
    // viewer has done to the rest of the page.
    resetAll.style.cssText = "background:#ffffff;color:#111827;border:1px solid #6b7280";
    resetAll.addEventListener("click", () => {
      deps.reset();
      render();
    });
    heading.append(title, resetAll);
    panel.appendChild(heading);

    for (const group of PALETTE_GROUPS) {
      const groupTitle = document.createElement("div");
      groupTitle.className = "text-[10px] uppercase tracking-wider text-content-muted pt-1";
      groupTitle.textContent = group.title;
      panel.appendChild(groupTitle);

      for (const { token, label } of group.rows) {
        const row = document.createElement("div");
        row.className = ROW_CLASS;
        row.dataset.token = token;

        const name = document.createElement("span");
        name.className = "w-28 shrink-0 text-content-secondary";
        name.textContent = label;

        const swatch = document.createElement("input");
        swatch.type = "color";
        swatch.value = tokens[token];
        swatch.className = "w-6 h-5 shrink-0 cursor-pointer bg-transparent";
        swatch.setAttribute("aria-label", label);

        const hex = document.createElement("span");
        hex.className = "w-16 shrink-0 font-mono text-content-muted";
        hex.textContent = tokens[token];

        const revert = document.createElement("button");
        revert.className = "shrink-0 text-content-muted hover:text-content-strong";
        revert.textContent = "⟲";
        revert.title = `Reset ${label}`;
        revert.setAttribute("aria-label", `Reset ${label}`);
        revert.addEventListener("click", () => {
          deps.write(withoutOverride(deps.read(), theme, token), true);
          render();
        });

        const apply = (persist: boolean) => {
          deps.write(withOverride(deps.read(), theme, token, swatch.value), persist);
          hex.textContent = swatch.value;
          repaintBadges();
        };
        swatch.addEventListener("input", () => apply(false));
        swatch.addEventListener("change", () => apply(true));

        row.append(name, swatch, hex, revert);

        if (isTextToken(token)) {
          const badge = document.createElement("span");
          badge.className = "ml-auto shrink-0 font-mono";
          badge.dataset.badge = token;
          row.appendChild(badge);
        }
        panel.appendChild(row);
      }
    }
    repaintBadges();
  };

  /**
   * Refresh every badge, not only the row that changed.
   *
   * Changing *Panels* moves the surface all four text ratios are measured
   * against, so a badge repainted only beside the row the viewer touched would
   * leave the other three reporting a contrast against a colour that is no
   * longer there.
   */
  const repaintBadges = (): void => {
    const tokens = resolveTokens(deps.theme(), deps.read());
    for (const element of panel.querySelectorAll<HTMLElement>("[data-badge]")) {
      const reading = readingFor(element.dataset.badge as TokenName, tokens);
      if (reading === null) continue;
      element.textContent = reading.label;
      element.className = `ml-auto shrink-0 font-mono ${
        reading.passes ? "text-content-muted" : "text-red-600 dark:text-red-400"
      }`;
      element.title = reading.passes
        ? `Contrast with the panel surface, above the ${AA_SMALL_TEXT}:1 floor for small text`
        : `Contrast with the panel surface, below the ${AA_SMALL_TEXT}:1 floor for small text`;
    }
  };

  const open = (): void => {
    render();
    panel.classList.remove("hidden");
  };

  button.addEventListener("click", (event) => {
    event.stopPropagation();
    if (isOpen()) close();
    else open();
  });

  // Clicking the page or pressing Escape closes it, as a dropdown should.
  document.addEventListener("click", (event) => {
    if (isOpen() && !panel.contains(event.target as Node)) close();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") close();
  });

  return { render, open, close, isOpen };
};
