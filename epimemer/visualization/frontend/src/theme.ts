/**
 * Light and dark theme.
 *
 * Tailwind's `dark:` variants handle the page chrome, driven by a `dark` class
 * on `<html>`. What they cannot reach is anything drawn rather than styled —
 * the cytoscape canvas, the timeline SVG, the graphviz DOT — so those read a
 * palette from here instead.
 *
 * Two palettes live here. `Palette` is the **neutrals** for the drawn panels.
 * `SemanticPalette` is the hues that say *what kind of thing* something is, and
 * it is shared by every panel that draws one — which it had to become: the
 * graph drew facts green and inferences amber while the timeline drew the same
 * two blue and violet, in one split pane (#56).
 *
 * Both vary by theme. The hues used not to, on the reasoning that a saturated
 * colour reads on either background — but that is also what let the two tables
 * drift, since neither had a theme axis forcing anyone to reconcile them.
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
  /**
   * Opaque chrome surface, for anything drawn that has to overwrite what is
   * behind it: the break marker over the baseline, a tick label's plate over
   * the mark column, an expanded card over its neighbours. One value, because
   * three separately-tuned near-background greys would drift.
   *
   * Named for `--surface-chrome`, the token it becomes in VISUALISATION.md C.1.
   */
  surfaceChrome: string;
  breakSlash: string;
  breakLabel: string;
  /** The reference-time rule and its label — chrome, so deliberately neutral. */
  referenceLine: string;
  referenceLabel: string;
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
  // A step brighter than `tick`: the label sits on a plate in the mark column,
  // and gray-500 on gray-900 is only ~3.9:1.
  tickLabel: "#9ca3af",
  surfaceChrome: "#111827",
  breakSlash: "#6b7280",
  breakLabel: "#9ca3af",
  referenceLine: "#6b7280",
  referenceLabel: "#9ca3af",
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
  surfaceChrome: "#d1d5db",
  breakSlash: "#4b5563",
  breakLabel: "#374151",
  referenceLine: "#4b5563",
  referenceLabel: "#374151",
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

// --- Semantic hues ---

/**
 * What kind of thing something is, in colour. One table, every panel.
 *
 * The values come from the valid-time grammar's set (VISUALISATION.md C.6),
 * which was picked against a perceptual check in both themes — lightness band,
 * chroma floor, colour-vision-deficiency separation, contrast on the surface.
 * The graph panel's older hues were not, so the shared set is that one.
 */
export interface SemanticPalette {
  topic: string;
  fact: string;
  inference: string;
  segment: string;
  document: string;
  /** Retired-but-still-true-of-its-period. The timeline desaturates; the graph
   *  uses opacity instead, so this is currently timeline-only. */
  historical: string;
  /** Proposals awaiting review — a reflect-proposed boundary, a soundness flag. */
  pending: string;
  contradiction: string;
  selection: string;
  /** Edge meanings with no node kind of their own. */
  similarity: string;
  abstracts: string;
  /** Lineage rather than knowledge: superseded_by, merged_into. */
  lineage: string;
}

const SEMANTIC_LIGHT: SemanticPalette = {
  topic: "#1baf7a",
  fact: "#2a78d6",
  inference: "#4a3aa7",
  segment: "#64748b",
  document: "#94a3b8",
  historical: "#8095aa",
  pending: "#9a6b00",
  contradiction: "#ef4444",
  selection: "#ec4899",
  similarity: "#38bdf8",
  abstracts: "#facc15",
  lineage: "#6b7280",
};

const SEMANTIC_DARK: SemanticPalette = {
  topic: "#199e70",
  fact: "#3987e5",
  inference: "#9085e9",
  segment: "#64748b",
  document: "#94a3b8",
  historical: "#5d6d7e",
  pending: "#fab219",
  contradiction: "#ef4444",
  selection: "#ec4899",
  similarity: "#38bdf8",
  abstracts: "#facc15",
  lineage: "#6b7280",
};

export const semanticPaletteFor = (theme: Theme): SemanticPalette =>
  theme === "dark" ? SEMANTIC_DARK : SEMANTIC_LIGHT;

/**
 * The same hue, drained of colour — how focus mode dims.
 *
 * Focus owns **saturation**; status owns **opacity** (RETRIEVAL_PROVENANCE.md
 * §4.1). So this mixes each channel toward the grey of the *same luminance*,
 * leaving lightness alone: a desaturation that also darkened would be an
 * opacity change wearing a different name, and retired-and-retrieved would
 * land at the same appearance as active-and-not.
 *
 * It lives here rather than in a panel because both panels dim, and a second
 * implementation is how they would come to disagree — which is #56, exactly.
 */
export const desaturate = (hex: string, amount = 0.85): string => {
  const channels = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
  if (channels.some(Number.isNaN)) return hex;
  const [r, g, b] = channels;
  const grey = 0.2126 * r + 0.7152 * g + 0.0722 * b;
  return (
    "#" +
    channels
      .map((c) => Math.round(c + (grey - c) * amount).toString(16).padStart(2, "0"))
      .join("")
  );
};

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

export const currentSemanticPalette = (): SemanticPalette =>
  semanticPaletteFor(currentTheme());

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
