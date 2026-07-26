import { describe, expect, it } from "vitest";

import {
  BREAK_PX,
  GAP_FACTOR,
  MAX_BREAKS,
  MIN_DOMAIN_MS,
  buildScale,
  domainFromRange,
  extentOf,
  formatSpan,
  isCollapsed,
  panDomain,
  selectBreaks,
  ticksForSegment,
  timeToX,
  xToTime,
  zoomDomain,
  type Domain,
  type Span,
} from "./timeline-scale";

const DAY = 86_400_000;

/** Instants at the given offsets (in days) from an arbitrary epoch. */
const atDays = (...days: number[]): Span[] =>
  days.map((d) => ({ start: d * DAY, end: null }));

const domainOver = (spans: Span[]): Domain => extentOf(spans)!;

describe("extentOf", () => {
  it("spans from the earliest start to the latest end", () => {
    const extent = extentOf([
      { start: 100, end: 500 },
      { start: 200, end: null },
    ]);
    expect(extent).toEqual({ t0: 100, t1: 500 });
  });

  it("is null when nothing is dated", () => {
    expect(extentOf([])).toBeNull();
  });
});

describe("selectBreaks", () => {
  it("breaks a gap that is both relatively and visibly large", () => {
    const spans = atDays(0, 1, 2, 3, 400, 401, 402);
    const breaks = selectBreaks(spans, domainOver(spans));

    expect(breaks).toHaveLength(1);
    expect(breaks[0].t0).toBe(3 * DAY);
    expect(breaks[0].t1).toBe(400 * DAY);
  });

  it("leaves an evenly spaced row alone", () => {
    const spans = atDays(0, 1, 2, 3, 4, 5);
    expect(selectBreaks(spans, domainOver(spans))).toEqual([]);
  });

  it("does not break a gap that is large in absolute terms but not relatively", () => {
    // Every gap is a century; none is anomalous against the others.
    const century = 36_500 * DAY;
    const spans = [0, 1, 2, 3, 4].map((i) => ({ start: i * century, end: null }));

    expect(selectBreaks(spans, domainOver(spans))).toEqual([]);
  });

  it("does not break a relatively huge gap that costs almost no screen", () => {
    // The 200x gap is real, but it is a sliver of a domain dominated by one
    // enormous span, so collapsing it would gain nothing.
    const spans: Span[] = [
      { start: 0, end: null },
      { start: 1, end: null },
      { start: 2, end: null },
      { start: 402, end: null },
      { start: 403, end: null },
      { start: 100 * DAY, end: null },
    ];
    const breaks = selectBreaks(spans, domainOver(spans));

    expect(breaks.every((b) => b.t0 !== 2)).toBe(true);
  });

  it("keeps at most MAX_BREAKS, choosing the largest gaps", () => {
    // Seven tight clusters separated by six gaps that all clear both
    // thresholds, growing slightly so there is a smallest one to discard.
    const spans: Span[] = [];
    let t = 0;
    for (let cluster = 0; cluster < 7; cluster++) {
      spans.push({ start: t, end: null });
      spans.push({ start: t + DAY, end: null });
      spans.push({ start: t + 2 * DAY, end: null });
      t += (1000 + cluster * 10) * DAY;
    }
    const breaks = selectBreaks(spans, domainOver(spans));

    expect(breaks).toHaveLength(MAX_BREAKS);
    // Returned in time order, not size order, so rendering can walk them.
    const starts = breaks.map((b) => b.t0);
    expect([...starts].sort((a, b) => a - b)).toEqual(starts);
    // The discarded one is the smallest gap, which is the first.
    expect(breaks[0].t0).toBeGreaterThan(1000 * DAY);
  });

  it("needs three marks before any gap can be anomalous", () => {
    const spans = atDays(0, 5000);
    expect(selectBreaks(spans, domainOver(spans))).toEqual([]);
  });

  it("ignores the empty space either side of the data", () => {
    const spans = atDays(100, 101, 102);
    const wide: Domain = { t0: 0, t1: 200 * DAY };

    expect(selectBreaks(spans, wide)).toEqual([]);
  });

  it("does not open a gap inside an interval that spans it", () => {
    // A long interval covers the whole row, so the instants sprinkled inside it
    // are never separated by empty time however far apart they look. Tracking
    // only the previous mark's end rather than the furthest reached would
    // invent a gap the interval is sitting on top of.
    const spans: Span[] = [
      { start: 0, end: 1000 * DAY },
      { start: 1 * DAY, end: null },
      { start: 2 * DAY, end: null },
      { start: 3 * DAY, end: null },
      { start: 900 * DAY, end: null },
      { start: 901 * DAY, end: null },
      { start: 902 * DAY, end: null },
    ];
    expect(selectBreaks(spans, domainOver(spans))).toEqual([]);
  });

  describe("hysteresis", () => {
    // A gap sized between the entry and exit thresholds: not big enough to
    // create a break, but big enough to keep one that already exists.
    const borderline = (): { spans: Span[]; domain: Domain } => {
      const unit = DAY;
      const spans: Span[] = [
        { start: 0, end: null },
        { start: unit, end: null },
        { start: 2 * unit, end: null },
        { start: 2 * unit + GAP_FACTOR * 0.85 * unit, end: null },
      ];
      return { spans, domain: domainOver(spans) };
    };

    it("does not create a break below the entry threshold", () => {
      const { spans, domain } = borderline();
      expect(selectBreaks(spans, domain, [])).toEqual([]);
    });

    it("keeps an existing break in the deadband", () => {
      const { spans, domain } = borderline();
      const held = [{ t0: 2 * DAY, t1: 2 * DAY + GAP_FACTOR * 0.85 * DAY }];

      expect(selectBreaks(spans, domain, held)).toHaveLength(1);
    });
  });
});

describe("buildScale", () => {
  it("is one linear segment when nothing breaks", () => {
    const spans = atDays(0, 1, 2, 3);
    const scale = buildScale(spans, domainOver(spans), 600);

    expect(scale.segments).toHaveLength(1);
    expect(scale.breaks).toEqual([]);
    expect(scale.segments[0].x0).toBe(0);
    expect(scale.segments[0].x1).toBeCloseTo(600);
  });

  it("reserves fixed pixels per break and shares the rest by span", () => {
    const spans = atDays(0, 1, 2, 3, 400, 401, 402);
    const scale = buildScale(spans, domainOver(spans), 600);

    expect(scale.breaks).toHaveLength(1);
    const drawn = scale.segments.reduce((sum, s) => sum + (s.x1 - s.x0), 0);
    expect(drawn).toBeCloseTo(600 - BREAK_PX);
    // Clusters span 3 days and 2 days, so the pixels divide 3:2 — each dense
    // region is drawn to its own scale, not squashed by the sparse one.
    const [first, second] = scale.segments.map((s) => s.x1 - s.x0);
    expect(first / second).toBeCloseTo(3 / 2);
  });

  it("places the break marker between the segments it separates", () => {
    const spans = atDays(0, 1, 2, 3, 400, 401, 402);
    const scale = buildScale(spans, domainOver(spans), 600);

    const [brk] = scale.breaks;
    expect(brk.x0).toBeCloseTo(scale.segments[0].x1);
    expect(brk.x1).toBeCloseTo(scale.segments[1].x0);
    expect(brk.x1 - brk.x0).toBe(BREAK_PX);
  });
});

describe("timeToX / xToTime", () => {
  const spans = atDays(0, 1, 2, 3, 400, 401, 402);
  const scale = buildScale(spans, domainOver(spans), 600);

  it("round-trips a time that is actually on the axis", () => {
    const t = 2 * DAY;
    expect(xToTime(scale, timeToX(scale, t))).toBeCloseTo(t, -1);
  });

  it("maps the domain ends to the axis ends", () => {
    expect(timeToX(scale, scale.domain.t0)).toBeCloseTo(0);
    expect(timeToX(scale, scale.domain.t1)).toBeCloseTo(600);
  });

  it("clamps times outside the domain", () => {
    expect(timeToX(scale, scale.domain.t0 - 10 * DAY)).toBe(0);
    expect(timeToX(scale, scale.domain.t1 + 10 * DAY)).toBeCloseTo(600);
  });

  it("does not spread collapsed time across the axis", () => {
    // Two instants deep inside the collapsed gap must not be drawn apart.
    const a = timeToX(scale, 100 * DAY);
    const b = timeToX(scale, 300 * DAY);
    expect(a).toBeCloseTo(b);
  });

  it("reports which times are collapsed", () => {
    expect(isCollapsed(scale, 200 * DAY)).toBe(true);
    expect(isCollapsed(scale, 1 * DAY)).toBe(false);
  });

  it("resolves a pixel inside a break marker to the gap start", () => {
    const [brk] = scale.breaks;
    expect(xToTime(scale, (brk.x0 + brk.x1) / 2)).toBe(brk.gap.t0);
  });
});

describe("zoomDomain", () => {
  const extent: Domain = { t0: 0, t1: 1000 * DAY };

  it("keeps the anchor time under the pointer", () => {
    const domain: Domain = { t0: 0, t1: 1000 * DAY };
    const anchor = 250 * DAY;
    const before = (anchor - domain.t0) / (domain.t1 - domain.t0);

    const zoomed = zoomDomain(domain, 0.5, anchor, extent);
    const after = (anchor - zoomed.t0) / (zoomed.t1 - zoomed.t0);

    expect(after).toBeCloseTo(before, 6);
  });

  it("narrows the window when zooming in", () => {
    const domain: Domain = { t0: 0, t1: 1000 * DAY };
    const zoomed = zoomDomain(domain, 0.5, 500 * DAY, extent);
    expect(zoomed.t1 - zoomed.t0).toBeCloseTo(500 * DAY);
  });

  it("never zooms out past the data extent", () => {
    const domain: Domain = { t0: 0, t1: 1000 * DAY };
    const zoomed = zoomDomain(domain, 10, 500 * DAY, extent);

    expect(zoomed.t0).toBeGreaterThanOrEqual(extent.t0);
    expect(zoomed.t1).toBeLessThanOrEqual(extent.t1);
  });

  it("never zooms in past the minimum span", () => {
    const domain: Domain = { t0: 0, t1: MIN_DOMAIN_MS };
    const zoomed = zoomDomain(domain, 0.0001, 0, extent);
    expect(zoomed.t1 - zoomed.t0).toBeGreaterThanOrEqual(MIN_DOMAIN_MS);
  });

  it("shifts the window rather than shrinking it when it hits the edge", () => {
    const domain: Domain = { t0: 900 * DAY, t1: 1000 * DAY };
    const zoomed = zoomDomain(domain, 1.5, 1000 * DAY, extent);

    expect(zoomed.t1).toBeCloseTo(extent.t1);
    expect(zoomed.t1 - zoomed.t0).toBeCloseTo(150 * DAY);
  });
});

describe("panDomain", () => {
  const extent: Domain = { t0: 0, t1: 1000 * DAY };

  it("slides without changing the span", () => {
    const domain: Domain = { t0: 100 * DAY, t1: 200 * DAY };
    const panned = panDomain(domain, 50 * DAY, extent);

    expect(panned.t0).toBeCloseTo(150 * DAY);
    expect(panned.t1 - panned.t0).toBeCloseTo(100 * DAY);
  });

  it("stops at the extent instead of running off it", () => {
    const domain: Domain = { t0: 900 * DAY, t1: 1000 * DAY };
    const panned = panDomain(domain, 500 * DAY, extent);

    expect(panned.t1).toBeCloseTo(extent.t1);
    expect(panned.t1 - panned.t0).toBeCloseTo(100 * DAY);
  });
});

describe("domainFromRange", () => {
  const extent: Domain = { t0: 0, t1: 1000 * DAY };

  it("orders the ends of a backwards drag", () => {
    const domain = domainFromRange(300 * DAY, 100 * DAY, extent);
    expect(domain.t0).toBeCloseTo(100 * DAY);
    expect(domain.t1).toBeCloseTo(300 * DAY);
  });

  it("widens a click-sized selection to the minimum span", () => {
    const domain = domainFromRange(500 * DAY, 500 * DAY, extent);
    expect(domain.t1 - domain.t0).toBeGreaterThanOrEqual(MIN_DOMAIN_MS);
  });
});

describe("ticksForSegment", () => {
  it("produces round times inside the segment", () => {
    const ticks = ticksForSegment({ t0: 0, t1: 30 * DAY, x0: 0, x1: 600 }, 4);

    expect(ticks.length).toBeGreaterThan(0);
    expect(ticks.every((t) => t >= 0 && t <= 30 * DAY)).toBe(true);
  });

  it("still labels a segment narrower than one step", () => {
    expect(ticksForSegment({ t0: 5, t1: 6, x0: 0, x1: 10 })).toHaveLength(1);
  });
});

describe("formatSpan", () => {
  it("uses the largest unit that fits", () => {
    expect(formatSpan(600 * 365.25 * DAY)).toBe("~600 years");
    expect(formatSpan(3 * DAY)).toBe("~3 days");
    expect(formatSpan(2 * 3_600_000)).toBe("~2 hours");
  });

  it("does not pluralise a single unit", () => {
    expect(formatSpan(365.25 * DAY)).toBe("~1 year");
  });
});
