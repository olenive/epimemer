/**
 * Piecewise-linear time scale for the timeline panel.
 *
 * A row's axis is not one linear mapping but a list of segments separated by
 * *breaks* — places where the data has a gap so much larger than its local
 * spacing that drawing it to scale would crush everything else into a smear.
 * Each break collapses to a fixed-width marker, and the remaining pixels are
 * shared out among the segments in proportion to their own spans.
 *
 * Everything here is pure and DOM-free: the parts most likely to be subtly
 * wrong (where a break goes, where a zoom lands) are the parts worth testing
 * without a browser.
 *
 * Positions are pixels along the axis, measured from its start — the module
 * has no opinion about which way the axis runs. The panel draws them down the
 * screen; nothing here would change if it drew them across.
 *
 * All times are epoch milliseconds.
 */

/** A mark's occupancy in time. `end` is null for an instant. */
export interface Span {
  start: number;
  end: number | null;
}

/** A visible time window. */
export interface Domain {
  t0: number;
  t1: number;
}

/** A stretch of time collapsed out of the axis. */
export interface Gap {
  t0: number;
  t1: number;
}

/** One linear piece of the axis: `[t0, t1]` maps onto `[p0, p1]`. */
export interface Segment {
  t0: number;
  t1: number;
  p0: number;
  p1: number;
}

/** A rendered break marker, sitting between two segments. */
export interface Break {
  /** Index of the segment this break follows. */
  afterSegment: number;
  /** The collapsed time range — fed back in to keep breaks stable under zoom. */
  gap: Gap;
  p0: number;
  p1: number;
}

export interface Scale {
  segments: Segment[];
  breaks: Break[];
  domain: Domain;
  width: number;
}

/**
 * A gap must exceed this multiple of the median gap to be a break candidate.
 * Relative, not absolute: 600 years is unremarkable on a medieval timeline and
 * enormous on one covering 2024–2026.
 */
export const GAP_FACTOR = 10;

/**
 * ...and must also waste at least this fraction of the visible span. Breaking a
 * gap that costs eight pixels buys nothing and adds a distracting marker.
 */
export const GAP_MIN_FRACTION = 0.12;

/** Beyond a handful of breaks the axis stops reading as an axis. */
export const MAX_BREAKS = 5;

/**
 * An existing break survives until its gap falls below this fraction of the
 * entry threshold. Without the deadband a break flickers in and out every few
 * pixels of a zoom drag, which is worse than either state.
 */
export const HYSTERESIS = 0.7;

/** Pixel width of a break marker. */
export const BREAK_PX = 24;

/** Never zoom past this, or the scale divides by ~zero. */
export const MIN_DOMAIN_MS = 1000;

const spanEnd = (span: Span): number => span.end ?? span.start;

/** Full time extent covered by `spans`, or null if there is nothing dated. */
export const extentOf = (spans: readonly Span[]): Domain | null => {
  const dated = spans.filter((s) => Number.isFinite(s.start));
  if (dated.length === 0) return null;
  return {
    t0: Math.min(...dated.map((s) => s.start)),
    t1: Math.max(...dated.map(spanEnd)),
  };
};

const median = (values: readonly number[]): number => {
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 0 ? (sorted[mid - 1] + sorted[mid]) / 2 : sorted[mid];
};

const overlaps = (a: Gap, b: Gap): boolean => a.t0 < b.t1 && b.t0 < a.t1;

/**
 * Interior gaps between consecutive spans, clipped to `domain`.
 *
 * A gap runs from the end of one span to the start of the next, so an interval
 * does not manufacture a gap across its own duration. Leading and trailing
 * empty space is not a gap — breaking there would only shave the margins.
 */
const gapsWithin = (spans: readonly Span[], domain: Domain): Gap[] => {
  const visible = spans
    .filter((s) => spanEnd(s) >= domain.t0 && s.start <= domain.t1)
    .sort((a, b) => a.start - b.start);

  const gaps: Gap[] = [];
  let reach = visible.length > 0 ? spanEnd(visible[0]) : 0;
  for (const span of visible.slice(1)) {
    if (span.start > reach) {
      gaps.push({
        t0: Math.max(reach, domain.t0),
        t1: Math.min(span.start, domain.t1),
      });
    }
    // Overlapping spans must not reopen a gap that an earlier one already covered.
    reach = Math.max(reach, spanEnd(span));
  }
  return gaps.filter((g) => g.t1 > g.t0);
};

/**
 * Choose which gaps to collapse.
 *
 * `previous` are the gaps already broken in the last render; they are held to
 * the relaxed hysteresis threshold so a small zoom does not dissolve them.
 */
export const selectBreaks = (
  spans: readonly Span[],
  domain: Domain,
  previous: readonly Gap[] = [],
): Gap[] => {
  // Fewer than three marks needs no special case: two marks yield one gap,
  // which is its own median, and nothing exceeds GAP_FACTOR times itself.
  const gaps = gapsWithin(spans, domain);
  const positive = gaps.map((g) => g.t1 - g.t0).filter((d) => d > 0);
  if (positive.length === 0) return [];

  const m = median(positive);
  if (m <= 0) return [];

  const visibleSpan = domain.t1 - domain.t0;
  if (visibleSpan <= 0) return [];

  const candidates = gaps.filter((gap) => {
    const size = gap.t1 - gap.t0;
    const held = previous.some((p) => overlaps(p, gap));
    const factor = held ? GAP_FACTOR * HYSTERESIS : GAP_FACTOR;
    return size > factor * m && size / visibleSpan > GAP_MIN_FRACTION;
  });

  // Largest first for the cap, then back into time order for rendering.
  return candidates
    .sort((a, b) => b.t1 - b.t0 - (a.t1 - a.t0))
    .slice(0, MAX_BREAKS)
    .sort((a, b) => a.t0 - b.t0);
};

/**
 * Build the axis for one row.
 *
 * With no breaks this degenerates to an ordinary linear scale, which is the
 * common case and should stay cheap.
 */
export const buildScale = (
  spans: readonly Span[],
  domain: Domain,
  width: number,
  previous: readonly Gap[] = [],
): Scale => {
  const breakGaps = selectBreaks(spans, domain, previous);
  const usable = Math.max(0, width - breakGaps.length * BREAK_PX);

  // Cut the domain at every break, keeping the pieces in between.
  const bounds: Domain[] = [];
  let cursor = domain.t0;
  for (const gap of breakGaps) {
    if (gap.t0 > cursor) bounds.push({ t0: cursor, t1: gap.t0 });
    cursor = gap.t1;
  }
  bounds.push({ t0: cursor, t1: domain.t1 });

  const totalSpan = bounds.reduce((sum, b) => sum + Math.max(0, b.t1 - b.t0), 0);
  const segments: Segment[] = [];
  const breaks: Break[] = [];

  let x = 0;
  bounds.forEach((bound, i) => {
    const span = Math.max(0, bound.t1 - bound.t0);
    const w = totalSpan > 0 ? (span / totalSpan) * usable : usable / bounds.length;
    segments.push({ t0: bound.t0, t1: bound.t1, p0: x, p1: x + w });
    x += w;
    if (i < breakGaps.length) {
      breaks.push({ afterSegment: i, gap: breakGaps[i], p0: x, p1: x + BREAK_PX });
      x += BREAK_PX;
    }
  });

  return { segments, breaks, domain, width };
};

/**
 * Time → pixel. Times inside a collapsed gap land on the break marker's left
 * edge, and times outside the domain clamp to its ends.
 */
export const timeToPos = (scale: Scale, t: number): number => {
  const { segments } = scale;
  if (segments.length === 0) return 0;

  for (let i = 0; i < segments.length; i++) {
    const segment = segments[i];
    // Before this segment starts means inside the gap that precedes it (or
    // before the domain entirely). Either way there is no room to spread it
    // out — pin it to the edge, or a collapsed century draws off the axis.
    if (t < segment.t0) return i > 0 ? segments[i - 1].p1 : segment.p0;
    if (t <= segment.t1) {
      const span = segment.t1 - segment.t0;
      if (span <= 0) return segment.p0;
      return segment.p0 + ((t - segment.t0) / span) * (segment.p1 - segment.p0);
    }
  }
  return segments[segments.length - 1].p1;
};

/** Pixel → time. Inside a break marker, resolves to the gap's start. */
export const posToTime = (scale: Scale, x: number): number => {
  const { segments, breaks } = scale;
  if (segments.length === 0) return scale.domain.t0;
  if (x <= segments[0].p0) return segments[0].t0;

  for (let i = 0; i < segments.length; i++) {
    const segment = segments[i];
    if (x <= segment.p1) {
      const px = segment.p1 - segment.p0;
      if (px <= 0) return segment.t0;
      return segment.t0 + ((x - segment.p0) / px) * (segment.t1 - segment.t0);
    }
    const brk = breaks.find((b) => b.afterSegment === i);
    if (brk && x <= brk.p1) return brk.gap.t0;
  }
  return segments[segments.length - 1].t1;
};

/** True when `t` falls inside a collapsed gap and so has nowhere to be drawn. */
export const isCollapsed = (scale: Scale, t: number): boolean =>
  scale.breaks.some((b) => t > b.gap.t0 && t < b.gap.t1);

const clampToExtent = (domain: Domain, extent: Domain): Domain => {
  const span = Math.min(domain.t1 - domain.t0, extent.t1 - extent.t0 || MIN_DOMAIN_MS);
  let t0 = domain.t0;
  if (t0 < extent.t0) t0 = extent.t0;
  if (t0 + span > extent.t1) t0 = extent.t1 - span;
  return { t0, t1: t0 + span };
};

/**
 * Zoom about a fixed time, so the mark under the pointer stays under it.
 * `factor` below 1 zooms in.
 */
export const zoomDomain = (
  domain: Domain,
  factor: number,
  anchor: number,
  extent: Domain,
): Domain => {
  const span = domain.t1 - domain.t0;
  // Only the lower bound is enforced here; `clampToExtent` caps the upper one.
  const next = Math.max(span * factor, MIN_DOMAIN_MS);
  // Keep the anchor at the same fractional position across the change.
  const ratio = span > 0 ? (anchor - domain.t0) / span : 0.5;
  return clampToExtent({ t0: anchor - ratio * next, t1: anchor - ratio * next + next }, extent);
};

/** Slide the window without changing its span. */
export const panDomain = (domain: Domain, deltaMs: number, extent: Domain): Domain =>
  clampToExtent({ t0: domain.t0 + deltaMs, t1: domain.t1 + deltaMs }, extent);

/** The window a drag-select produced, floored at the minimum span. */
export const domainFromRange = (a: number, b: number, extent: Domain): Domain => {
  const t0 = Math.min(a, b);
  const t1 = Math.max(a, b);
  if (t1 - t0 < MIN_DOMAIN_MS) {
    const mid = (t0 + t1) / 2;
    return clampToExtent(
      { t0: mid - MIN_DOMAIN_MS / 2, t1: mid + MIN_DOMAIN_MS / 2 },
      extent,
    );
  }
  return clampToExtent({ t0, t1 }, extent);
};

/** A padded starting window, so end marks are not flush against the edges. */
export const paddedExtent = (extent: Domain, fraction = 0.02): Domain => {
  const span = extent.t1 - extent.t0;
  const pad = span > 0 ? span * fraction : MIN_DOMAIN_MS;
  return { t0: extent.t0 - pad, t1: extent.t1 + pad };
};

const MS = {
  second: 1000,
  minute: 60_000,
  hour: 3_600_000,
  day: 86_400_000,
  month: 2_629_800_000, // mean Gregorian month
  year: 31_557_600_000, // mean Julian year
} as const;

/** Human-readable duration, for break markers ("~600 years"). */
export const formatSpan = (ms: number): string => {
  // Abbreviations stay invariant; spelled-out units take a plural.
  const units: [number, string, string][] = [
    [MS.year, "year", "years"],
    [MS.month, "month", "months"],
    [MS.day, "day", "days"],
    [MS.hour, "hour", "hours"],
    [MS.minute, "min", "min"],
    [MS.second, "sec", "sec"],
  ];
  for (const [size, one, many] of units) {
    if (ms >= size) {
      const n = Math.round(ms / size);
      return `~${n} ${n === 1 ? one : many}`;
    }
  }
  return "<1 sec";
};

const TICK_STEPS: number[] = [
  MS.second, 5 * MS.second, 15 * MS.second, 30 * MS.second,
  MS.minute, 5 * MS.minute, 15 * MS.minute, 30 * MS.minute,
  MS.hour, 3 * MS.hour, 6 * MS.hour, 12 * MS.hour,
  MS.day, 2 * MS.day, 7 * MS.day, 14 * MS.day,
  MS.month, 3 * MS.month, 6 * MS.month,
  MS.year, 2 * MS.year, 5 * MS.year, 10 * MS.year,
  25 * MS.year, 50 * MS.year, 100 * MS.year, 250 * MS.year,
  500 * MS.year, 1000 * MS.year,
];

/** Round tick times inside one segment, roughly `target` of them. */
export const ticksForSegment = (segment: Segment, target = 4): number[] => {
  const span = segment.t1 - segment.t0;
  if (span <= 0) return [segment.t0];

  const ideal = span / Math.max(1, target);
  const step = TICK_STEPS.find((s) => s >= ideal) ?? TICK_STEPS[TICK_STEPS.length - 1];

  const ticks: number[] = [];
  for (let t = Math.ceil(segment.t0 / step) * step; t <= segment.t1; t += step) {
    ticks.push(t);
  }
  // A segment narrower than one step still deserves an endpoint to label.
  return ticks.length > 0 ? ticks : [segment.t0];
};

/** Tick label whose precision follows how much time is on screen. */
export const formatTick = (t: number, spanMs: number): string => {
  const date = new Date(t);
  if (spanMs > 20 * MS.year) return String(date.getUTCFullYear());
  if (spanMs > MS.year) {
    return date.toLocaleDateString(undefined, {
      year: "numeric",
      month: "short",
      timeZone: "UTC",
    });
  }
  if (spanMs > 3 * MS.day) {
    return date.toLocaleDateString(undefined, {
      month: "short",
      day: "numeric",
      timeZone: "UTC",
    });
  }
  return date.toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "UTC",
  });
};
