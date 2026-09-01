/**
 * The viewer's colour choices: what is stored, and how it reaches the page.
 *
 * **In `localStorage`, keyed per theme, and deliberately not in the graph.**
 * This is the opposite call from `reference_time`, which is a fact about the
 * material and belongs where every client can see it. A colour preference is a
 * property of the viewer: two people reading one graph are entitled to disagree
 * about it, and one of them changing it must not rewrite what the other sees.
 *
 * **Per theme, because a single shared map would mean choosing a colour in dark
 * mode silently destroying light mode** — the viewer would have to notice,
 * switch, and repair it. Only overridden tokens are written, so a default that
 * changes in a later release still reaches everyone who never touched it, and
 * `version` is there so a rename can migrate rather than misread.
 *
 * Everything above `readStoredPalette` is pure. Storage and the document are
 * the last two functions, kept apart so the merge rules can be tested without
 * either.
 */

import { hexToRgb } from "./contrast";
import {
  TOKEN_DEFAULTS,
  TOKEN_NAMES,
  type Theme,
  type TokenName,
  invalidatePalette,
} from "./theme";

export const PALETTE_STORAGE_KEY = "epimemer.palette";

/** Bumped only when the stored shape changes in a way a reader cannot infer. */
export const PALETTE_VERSION = 1;

export type Overrides = Partial<Record<TokenName, string>>;

export interface StoredPalette {
  version: number;
  light: Overrides;
  dark: Overrides;
}

/** Nothing overridden: the state a first visit and a full reset both produce. */
export const noOverrides = (): StoredPalette => ({
  version: PALETTE_VERSION,
  light: {},
  dark: {},
});

const isToken = (name: string): name is TokenName =>
  (TOKEN_NAMES as readonly string[]).includes(name);

/** Six-digit hex only. `<input type="color">` emits exactly this form. */
export const isHex = (value: unknown): value is string =>
  typeof value === "string" && /^#[0-9a-f]{6}$/i.test(value);

const cleanOverrides = (value: unknown): Overrides => {
  if (typeof value !== "object" || value === null) return {};
  return Object.fromEntries(
    Object.entries(value as Record<string, unknown>).filter(
      ([token, hex]) => isToken(token) && isHex(hex),
    ),
  ) as Overrides;
};

/**
 * Read what was stored, keeping only what is still meaningful.
 *
 * **A version this code does not know is discarded rather than read**, because
 * the alternative is interpreting a shape written by different rules and
 * painting the page with the result. Unparseable text, a wrong version, an
 * unknown token and a malformed colour all end at the same place: the defaults,
 * which is the one outcome that is never wrong.
 */
export const parsePalette = (raw: string | null): StoredPalette => {
  if (raw === null) return noOverrides();
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return noOverrides();
  }
  if (typeof parsed !== "object" || parsed === null) return noOverrides();
  const stored = parsed as Partial<StoredPalette>;
  if (stored.version !== PALETTE_VERSION) return noOverrides();
  return {
    version: PALETTE_VERSION,
    light: cleanOverrides(stored.light),
    dark: cleanOverrides(stored.dark),
  };
};

export const serializePalette = (stored: StoredPalette): string => JSON.stringify(stored);

/** The token values a theme resolves to: the defaults, with overrides on top. */
export const resolveTokens = (
  theme: Theme,
  stored: StoredPalette,
): Record<TokenName, string> => ({ ...TOKEN_DEFAULTS[theme], ...stored[theme] });

/**
 * One token overridden in one theme. Returns a new value; the caller persists.
 *
 * A colour equal to the default is recorded as an override anyway. It is what
 * the viewer chose, and treating it as "unset" would silently re-point it at a
 * default that may change under them.
 */
export const withOverride = (
  stored: StoredPalette,
  theme: Theme,
  token: TokenName,
  hex: string,
): StoredPalette => {
  if (!isHex(hex)) return stored;
  return { ...stored, [theme]: { ...stored[theme], [token]: hex } };
};

/** One token back to its default, leaving every other choice standing. */
export const withoutOverride = (
  stored: StoredPalette,
  theme: Theme,
  token: TokenName,
): StoredPalette => {
  const remaining = { ...stored[theme] };
  delete remaining[token];
  return { ...stored, [theme]: remaining };
};

/** Whether this theme has any choice worth resetting. */
export const hasOverrides = (stored: StoredPalette, theme: Theme): boolean =>
  Object.keys(stored[theme]).length > 0;

// --- Storage and the document ---

/** Read the stored choices, tolerating storage being unavailable. */
export const readStoredPalette = (): StoredPalette => {
  try {
    return parsePalette(localStorage.getItem(PALETTE_STORAGE_KEY));
  } catch {
    // Private browsing and blocked storage both throw. The viewer gets the
    // defaults, which is worse than their colours and much better than no page.
    return noOverrides();
  }
};

export const persistPalette = (stored: StoredPalette): void => {
  try {
    localStorage.setItem(PALETTE_STORAGE_KEY, serializePalette(stored));
  } catch {
    // As above: colours that do not survive a reload beat a page that does not
    // load.
  }
};

export const clearStoredPalette = (): void => {
  try {
    localStorage.removeItem(PALETTE_STORAGE_KEY);
  } catch {
    // Nothing to do, and nothing worth failing over.
  }
};

/**
 * Write this theme's resolved tokens onto the document, and drop the cache.
 *
 * Every token is written, not only the overridden ones, so that switching
 * theme or clearing one override cannot leave the previous value standing as an
 * inline property that outranks the stylesheet.
 *
 * The values go on as channels because that is what the tokens hold: Tailwind
 * composes them with an alpha, and a hex custom property cannot be given one.
 */
export const applyPalette = (
  theme: Theme,
  stored: StoredPalette,
  root: HTMLElement = document.documentElement,
): void => {
  const tokens = resolveTokens(theme, stored);
  for (const name of TOKEN_NAMES) {
    const rgb = hexToRgb(tokens[name]);
    if (rgb === null) continue;
    root.style.setProperty(name, rgb.join(" "));
  }
  invalidatePalette();
};

/**
 * The second way back, for a page whose reset button has been painted shut.
 *
 * `?palette=reset` clears the stored choices before anything is drawn. A colour
 * picker over the whole UI has to have an escape hatch that does not depend on
 * the UI being legible.
 */
export const resetRequested = (search: string): boolean =>
  new URLSearchParams(search).get("palette") === "reset";
