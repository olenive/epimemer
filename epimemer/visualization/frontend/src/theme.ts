/**
 * Light and dark theme.
 *
 * Tailwind's `dark:` variants handle the page chrome, driven by a `dark` class
 * on `<html>`. What they cannot reach is anything drawn rather than styled —
 * the cytoscape canvas, the timeline SVG, the graphviz DOT — so those read a
 * palette from here instead.
 *
 * Since the token migration the neutrals are not written here twice. The
 * chrome-adjacent fields of `Palette` read the same custom properties the
 * Tailwind classes do (`src/tokens.css`), which is what stops the two drifting:
 * the light-mode darkening pass previously had to fix the timeline axis apart
 * from the header it sits under. Only the genuinely draw-only fields, the
 * graphviz set, still carry literals.
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

/**
 * The nine neutral tokens, and the defaults `src/tokens.css` declares.
 *
 * Written twice, in two languages, because a stylesheet cannot be imported by a
 * module that has to run without one — jsdom loads no CSS, and the drawn panels
 * must still have colours before the stylesheet arrives. `theme.test.ts` parses
 * `tokens.css` and asserts these agree, so the pair cannot drift the way the
 * values they replaced did.
 */
export const TOKEN_NAMES = [
  "--surface-page",
  "--surface-chrome",
  "--surface-raised",
  "--surface-raised-hover",
  "--border",
  "--text-strong",
  "--text-primary",
  "--text-secondary",
  "--text-muted",
] as const;

export type TokenName = (typeof TOKEN_NAMES)[number];

export const TOKEN_DEFAULTS: Record<Theme, Record<TokenName, string>> = {
  light: {
    "--surface-page": "#e5e7eb",
    "--surface-chrome": "#d1d5db",
    "--surface-raised": "#f3f4f6",
    "--surface-raised-hover": "#f9fafb",
    "--border": "#9ca3af",
    "--text-strong": "#1f2937",
    "--text-primary": "#374151",
    "--text-secondary": "#4b5563",
    "--text-muted": "#4b5563",
  },
  dark: {
    "--surface-page": "#030712",
    "--surface-chrome": "#111827",
    "--surface-raised": "#1f2937",
    "--surface-raised-hover": "#374151",
    "--border": "#374151",
    "--text-strong": "#e5e7eb",
    "--text-primary": "#d1d5db",
    "--text-secondary": "#9ca3af",
    "--text-muted": "#6b7280",
  },
};

/** `"209 213 219"` — the form a token holds — to `"#d1d5db"`. */
export const channelsToHex = (channels: string): string | null => {
  const parts = channels.trim().split(/[\s,]+/);
  if (parts.length !== 3) return null;
  const bytes = parts.map((part) => Number(part));
  if (bytes.some((b) => !Number.isInteger(b) || b < 0 || b > 255)) return null;
  return "#" + bytes.map((b) => b.toString(16).padStart(2, "0")).join("");
};

/** Fields that are the same colour as the chrome, and which token each takes. */
const DERIVED: Record<string, TokenName> = {
  nodeLabel: "--text-primary",
  nodeBorder: "--border",
  axis: "--border",
  tick: "--text-muted",
  // A step brighter than `tick`: the label sits on a plate in the mark column,
  // and the muted token on the chrome is only ~3.9:1.
  tickLabel: "--text-secondary",
  surfaceChrome: "--surface-chrome",
  breakSlash: "--text-muted",
  breakLabel: "--text-secondary",
  referenceLine: "--text-muted",
  referenceLabel: "--text-secondary",
};

/** The graphviz set: drawn only, with no counterpart in the chrome. */
const DARK_DRAWN = {
  placeFill: "#1e293b",
  placeStroke: "#475569",
  placeText: "#94a3b8",
  transitionFill: "#1e3a5f",
  transitionStroke: "#3b82f6",
  transitionText: "#93c5fd",
  dotEdge: "#475569",
  dotEdgeLabel: "#64748b",
};

const LIGHT_DRAWN = {
  placeFill: "#f1f5f9",
  placeStroke: "#94a3b8",
  placeText: "#475569",
  transitionFill: "#dbeafe",
  transitionStroke: "#3b82f6",
  transitionText: "#1d4ed8",
  dotEdge: "#94a3b8",
  dotEdgeLabel: "#64748b",
};

/** Build a palette from a set of token values plus the theme's drawn literals. */
const assemble = (theme: Theme, tokens: Record<TokenName, string>): Palette => {
  const derived = Object.fromEntries(
    Object.entries(DERIVED).map(([field, token]) => [field, tokens[token]]),
  );
  return { ...derived, ...(theme === "dark" ? DARK_DRAWN : LIGHT_DRAWN) } as Palette;
};

/**
 * The palette a theme has with nothing overridden.
 *
 * Pure, and the answer callers want when there is no document to read: the
 * defaults rather than whatever the viewer has since chosen.
 */
export const paletteFor = (theme: Theme): Palette => assemble(theme, TOKEN_DEFAULTS[theme]);

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
  /** A pair somebody judged. The similarity hue drained of saturation, which
   *  is the relationship: same subject, no assertion of support. `assessed` is
   *  written for both verdicts — including "these are different claims" — so
   *  drawing it *as* similarity would show agreement where a decline was
   *  recorded, and drawing it in an unrelated hue would hide the connection. */
  assessed: string;
  /** Lineage rather than knowledge: superseded_by, temporally_followed_by,
   *  merged_into. */
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
  assessed: "#7ba7bd",
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
  assessed: "#6b93a8",
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

/**
 * The palette as the document currently resolves it, cached.
 *
 * **Never read the custom properties per render.** `currentPalette()` is called
 * on every frame of a timeline pan, and nine `getComputedStyle` reads in that
 * loop is a forced reflow per frame. The values only change when the theme
 * changes or the viewer overrides one, so they are read once and held until
 * something says otherwise.
 *
 * A token the document does not resolve falls back to its default, which is
 * what happens wherever no stylesheet is loaded.
 */
let cached: { theme: Theme; palette: Palette } | null = null;

/** Drop the cached palette. Call after anything that changes a token's value. */
export const invalidatePalette = (): void => {
  cached = null;
};

const readTokens = (theme: Theme, root: HTMLElement): Record<TokenName, string> => {
  const computed = getComputedStyle(root);
  const entries = TOKEN_NAMES.map((name) => {
    const hex = channelsToHex(computed.getPropertyValue(name));
    return [name, hex ?? TOKEN_DEFAULTS[theme][name]];
  });
  return Object.fromEntries(entries) as Record<TokenName, string>;
};

export const currentPalette = (root: HTMLElement = document.documentElement): Palette => {
  const theme = currentTheme(root);
  if (cached !== null && cached.theme === theme) return cached.palette;
  const palette = assemble(theme, readTokens(theme, root));
  cached = { theme, palette };
  return palette;
};

export const currentSemanticPalette = (): SemanticPalette =>
  semanticPaletteFor(currentTheme());

export const applyTheme = (
  theme: Theme,
  root: HTMLElement = document.documentElement,
): void => {
  root.classList.toggle("dark", theme === "dark");
  // Lets the browser theme form controls and scrollbars to match.
  root.style.colorScheme = theme;
  // The tokens now resolve to the other theme's values, so anything drawn from
  // the cache would be a frame behind.
  invalidatePalette();
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
