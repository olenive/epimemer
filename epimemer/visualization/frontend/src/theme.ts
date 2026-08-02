/**
 * Light and dark theme.
 *
 * Tailwind's `dark:` variants handle the page chrome, driven by a `dark` class
 * on `<html>`. What they cannot reach is anything drawn rather than styled —
 * the cytoscape canvas, the timeline SVG, the graphviz DOT — so those read a
 * palette from here instead.
 *
 * The palette is **neutrals only**. Node and edge hues are saturated enough to
 * read on either background and are deliberately left alone, so that "fact
 * green" means the same thing in both themes.
 */

export type Theme = "light" | "dark";

export const THEME_STORAGE_KEY = "epimemer.theme";

/**
 * Which theme to start in.
 *
 * An explicit stored choice wins; otherwise follow the OS. Anything else in
 * storage is treated as unset rather than as an error — a corrupted value
 * should give the user the system default, not a broken page.
 */
export const resolveTheme = (stored: string | null, prefersDark: boolean): Theme => {
  if (stored === "light" || stored === "dark") return stored;
  return prefersDark ? "dark" : "light";
};

export const nextTheme = (theme: Theme): Theme =>
  theme === "dark" ? "light" : "dark";

/** Neutral colours for the drawn panels. */
export interface Palette {
  /** Node label text on the graph canvas. */
  nodeLabel: string;
  nodeBorder: string;
  /** Timeline baseline and tick marks. */
  axis: string;
  tick: string;
  tickLabel: string;
  /** Break marker: it paints over the baseline, so it matches the panel background. */
  breakBackground: string;
  breakSlash: string;
  breakLabel: string;
  /** Pipeline detail graph (graphviz DOT). */
  placeFill: string;
  placeStroke: string;
  placeText: string;
  transitionFill: string;
  transitionStroke: string;
  transitionText: string;
  dotEdge: string;
  dotEdgeLabel: string;
}

const DARK: Palette = {
  nodeLabel: "#d1d5db",
  nodeBorder: "#1f2937",
  axis: "#374151",
  tick: "#4b5563",
  tickLabel: "#6b7280",
  breakBackground: "#111827",
  breakSlash: "#6b7280",
  breakLabel: "#9ca3af",
  placeFill: "#1e293b",
  placeStroke: "#475569",
  placeText: "#94a3b8",
  transitionFill: "#1e3a5f",
  transitionStroke: "#3b82f6",
  transitionText: "#93c5fd",
  dotEdge: "#475569",
  dotEdgeLabel: "#64748b",
};

const LIGHT: Palette = {
  nodeLabel: "#374151",
  nodeBorder: "#9ca3af",
  axis: "#6b7280",
  tick: "#6b7280",
  tickLabel: "#4b5563",
  breakBackground: "#d1d5db",
  breakSlash: "#4b5563",
  breakLabel: "#374151",
  placeFill: "#f1f5f9",
  placeStroke: "#94a3b8",
  placeText: "#475569",
  transitionFill: "#dbeafe",
  transitionStroke: "#3b82f6",
  transitionText: "#1d4ed8",
  dotEdge: "#94a3b8",
  dotEdgeLabel: "#64748b",
};

export const paletteFor = (theme: Theme): Palette => (theme === "dark" ? DARK : LIGHT);

/** Icon for the toggle: show where the click will take you, not where you are. */
export const themeToggleIcon = (theme: Theme): string => (theme === "dark" ? "☀" : "☾");

export const themeToggleTitle = (theme: Theme): string =>
  theme === "dark" ? "Switch to light mode" : "Switch to dark mode";

// --- DOM-facing helpers ---
//
// Kept here so the `dark` class is written and read in exactly one place; a
// second opinion about where the theme lives is how a panel ends up drawing in
// the wrong one.

export const currentTheme = (root: HTMLElement = document.documentElement): Theme =>
  root.classList.contains("dark") ? "dark" : "light";

export const currentPalette = (): Palette => paletteFor(currentTheme());

export const applyTheme = (
  theme: Theme,
  root: HTMLElement = document.documentElement,
): void => {
  root.classList.toggle("dark", theme === "dark");
  // Lets the browser theme form controls and scrollbars to match.
  root.style.colorScheme = theme;
};

/** Read the persisted choice, tolerating storage being unavailable. */
export const storedTheme = (): string | null => {
  try {
    return localStorage.getItem(THEME_STORAGE_KEY);
  } catch {
    // Private browsing and blocked storage both throw here. Not worth failing
    // the page over — the user just gets the system default each visit.
    return null;
  }
};

export const persistTheme = (theme: Theme): void => {
  try {
    localStorage.setItem(THEME_STORAGE_KEY, theme);
  } catch {
    // As above: a theme that does not survive a reload beats a page that does
    // not load.
  }
};

export const prefersDark = (): boolean =>
  typeof matchMedia === "function" && matchMedia("(prefers-color-scheme: dark)").matches;
