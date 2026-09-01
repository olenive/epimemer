/**
 * WCAG contrast, for the warning beside a text colour the viewer has picked.
 *
 * A picker over the whole UI can render the UI unusable: dark grey text on dark
 * grey chrome is two valid choices that together say nothing. The ratio is what
 * turns "that looks wrong" into a number the panel can show while the colour is
 * still being dragged.
 *
 * This project already reasons in these numbers rather than by eye: the
 * light-mode darkening pass was decided by computing them. Pure, and its own
 * module, because that is what lets the arithmetic be checked against published
 * values instead of against the page.
 */

/** The AA floor for body text. Larger text is allowed 3:1; nothing here is. */
export const AA_SMALL_TEXT = 4.5;

export type Rgb = readonly [number, number, number];

/** `"#d1d5db"` to its three channels, or null if it is not a six-digit hex. */
export const hexToRgb = (hex: string): Rgb | null => {
  if (!/^#[0-9a-f]{6}$/i.test(hex)) return null;
  return [
    parseInt(hex.slice(1, 3), 16),
    parseInt(hex.slice(3, 5), 16),
    parseInt(hex.slice(5, 7), 16),
  ] as const;
};

/**
 * Relative luminance, as WCAG 2 defines it.
 *
 * The channel curve is the specification's, not sRGB's exact inverse gamma; the
 * two differ slightly and every published ratio is computed with this one, so
 * matching the reference values means matching the specification's arithmetic.
 */
export const relativeLuminance = (rgb: Rgb): number => {
  const [r, g, b] = rgb.map((channel) => {
    const c = channel / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
};

/**
 * The ratio between two colours, from 1 (identical) to 21 (black on white).
 *
 * Symmetric: which colour is the text and which the background changes nothing,
 * which is why the caller may pass them in either order.
 */
export const contrastRatio = (a: string, b: string): number | null => {
  const [first, second] = [hexToRgb(a), hexToRgb(b)];
  if (first === null || second === null) return null;
  const [lighter, darker] = [relativeLuminance(first), relativeLuminance(second)].sort(
    (x, y) => y - x,
  );
  return (lighter + 0.05) / (darker + 0.05);
};

/** Whether a ratio clears the small-text floor. */
export const meetsAA = (ratio: number): boolean => ratio >= AA_SMALL_TEXT;

/** One decimal place, the form a badge shows: `"4.5"`. */
export const formatRatio = (ratio: number): string => ratio.toFixed(1);
