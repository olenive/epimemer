import { describe, expect, it } from "vitest";

import {
  clockMatches,
  describeEndpoint,
  describeInterval,
  endpointMark,
  intervalSpan,
  packSlots,
  stripGeometry,
  successionOrder,
  unionEnvelope,
  validityLayout,
  type AxisClock,
  type EndpointMark,
} from "./timeline-validity";
import type {
  BoundaryProposalView,
  EdgeView,
  ImpreciseInstantView,
  NodeView,
  ValidityIntervalView,
} from "./types";

// --- Fixtures ---

const at = (iso: string, label: string | null = null): ImpreciseInstantView => ({
  instant_kind: "precise",
  at: iso,
  label,
});

const UNKNOWN: ImpreciseInstantView = { instant_kind: "unknown" };
const UNBOUNDED: ImpreciseInstantView = { instant_kind: "unbounded" };

/** An edge the source named rather than dated: "the Renaissance". */
const named = (label: string): ImpreciseInstantView => ({ instant_kind: "named", label });

const interval = (over: Partial<ValidityIntervalView> = {}): ValidityIntervalView => ({
  start: at("1997-05-02T00:00:00Z"),
  end: at("2010-05-11T00:00:00Z"),
  timeline_id: null,
  witnessed_at: null,
  basis: "stated",
  ...over,
});

const node = (over: Partial<NodeView> & { node_id: string }): NodeView => ({
  node_type: "fact",
  content: "Labour is in government",
  status: "active",
  source_id: "s1",
  extraction_method: "agent",
  confidence: null,
  retrieved_at: null,
  created_at: "2024-01-01T00:00:00Z",
  graph: "default",
  metadata: {},
  ...over,
});

const edge = (over: Partial<EdgeView> & { src_id: string; dst_id: string }): EdgeView => ({
  edge_id: `${over.src_id}->${over.dst_id}`,
  edge_type: "sourced_from",
  weight: 1,
  validity: [],
  created_at: "2024-01-01T00:00:00Z",
  graph: "default",
  metadata: {},
  ...over,
});

const mark = (id: string, nodeIds: string[], side: "left" | "right" | "axis" = "left") => ({
  id,
  nodeIds,
  side,
});

/** A date reflect read off the claim on the other side of a succession. */
const proposal = (
  over: Partial<BoundaryProposalView> = {},
): BoundaryProposalView => ({
  node_id: "f1",
  source_id: "doc-a",
  endpoint: "end",
  at: "2018-01-01T00:00:00Z",
  timeline_id: null,
  because_id: "f2",
  because_source_id: "doc-b",
  graph: "default",
  ...over,
});

/** No timeline chosen, so the axis is the wall clock the real world keeps. */
const NO_CLOCK: AxisClock = { timelineId: null, referenceTime: null };

/** One pixel per day, with 1997-05-02 at the top of the panel. */
const EPOCH = Date.parse("1997-05-02T00:00:00Z");
const DAY = 86_400_000;
const place = (instant: number): number => (instant - EPOCH) / DAY;
const BOUNDS = { top: -100, bottom: 10_000 };
const FADE = 24;

// --- Endpoint kinds ---

describe("the mark an endpoint gets", () => {
  // The whole grammar turns on these four kinds staying apart (VALIDITY_DESIGN
  // §2.4), so each one is named here rather than checked as a group.
  const EXPECTED: Record<ImpreciseInstantView["instant_kind"], EndpointMark> = {
    precise: "cap",
    named: "named",
    unknown: "fade",
    unbounded: "exit",
  };

  it("gives a stated date a crisp cap", () => {
    expect(endpointMark(at("1991-09-06T00:00:00Z"))).toBe(EXPECTED.precise);
  });

  it("gives a date resolved from a label a soft, hatched edge", () => {
    // Resolution added a position to the words; it did not turn them into a
    // date somebody stated, so the edge stays soft.
    expect(endpointMark(at("1400-01-01T00:00:00Z", "the Renaissance"))).toBe("soft");
  });

  it("gives a named edge a mark of its own, the labelled edge", () => {
    expect(endpointMark({ instant_kind: "named", label: "under the USSR" })).toBe(
      EXPECTED.named,
    );
  });

  it("fades an unknown edge and runs an unbounded one off the panel", () => {
    // §13.2 rule 2: these are different values in the model, so they may never
    // share a treatment.
    expect(endpointMark(UNKNOWN)).toBe(EXPECTED.unknown);
    expect(endpointMark(UNBOUNDED)).toBe(EXPECTED.unbounded);
    expect(endpointMark(UNKNOWN)).not.toBe(endpointMark(UNBOUNDED));
  });

  it("covers every kind the wire type has", () => {
    // `EXPECTED` is keyed by the union itself, so a fifth `instant_kind` fails
    // `tsc` here as well as in `endpointMark`'s own exhaustive switch.
    expect(Object.keys(EXPECTED).sort()).toEqual([
      "named",
      "precise",
      "unbounded",
      "unknown",
    ]);
  });
});

// --- Geometry ---

describe("where a strip is drawn", () => {
  it("spans the two stated dates, with nothing past either end", () => {
    const geometry = stripGeometry(interval(), place, BOUNDS, FADE);

    expect(geometry).not.toBeNull();
    expect(geometry!.bodyTop).toBe(0);
    expect(geometry!.bodyBottom).toBeCloseTo(place(Date.parse("2010-05-11T00:00:00Z")));
    expect(geometry!.fadeAbove).toBe(0);
    expect(geometry!.fadeBelow).toBe(0);
    expect(geometry!.top).toBe(geometry!.bodyTop);
    expect(geometry!.bottom).toBe(geometry!.bodyBottom);
  });

  it("fades past an unknown end rather than stopping at one", () => {
    const geometry = stripGeometry(interval({ end: UNKNOWN }), place, BOUNDS, FADE)!;

    expect(geometry.endMark).toBe("fade");
    expect(geometry.fadeBelow).toBe(FADE);
    expect(geometry.bottom).toBe(geometry.bodyBottom + FADE);
  });

  it("fades above an unknown start", () => {
    const geometry = stripGeometry(interval({ start: UNKNOWN }), place, BOUNDS, FADE)!;

    expect(geometry.startMark).toBe("fade");
    expect(geometry.fadeAbove).toBe(FADE);
    expect(geometry.top).toBe(geometry.bodyTop - FADE);
  });

  it("runs an unbounded end to the edge of the panel at full weight", () => {
    const geometry = stripGeometry(interval({ end: UNBOUNDED }), place, BOUNDS, FADE)!;

    expect(geometry.endMark).toBe("exit");
    expect(geometry.bodyBottom).toBe(BOUNDS.bottom);
    // No fog: there is no edge to be uncertain about.
    expect(geometry.fadeBelow).toBe(0);
  });

  it("runs an unbounded start to the top of the panel", () => {
    const geometry = stripGeometry(interval({ start: UNBOUNDED }), place, BOUNDS, FADE)!;

    expect(geometry.bodyTop).toBe(BOUNDS.top);
    expect(geometry.fadeAbove).toBe(0);
  });

  it("collapses to the witness when neither edge is known, fading both ways", () => {
    const witnessed = interval({
      start: UNKNOWN,
      end: UNKNOWN,
      witnessed_at: at("1970-03-01T00:00:00Z"),
    });

    const geometry = stripGeometry(witnessed, place, BOUNDS, FADE)!;
    const witness = place(Date.parse("1970-03-01T00:00:00Z"));

    expect(geometry.witnessAt).toBeCloseTo(witness);
    expect(geometry.bodyTop).toBeCloseTo(witness);
    expect(geometry.bodyBottom).toBeCloseTo(witness);
    expect(geometry.fadeAbove).toBe(FADE);
    expect(geometry.fadeBelow).toBe(FADE);
  });

  it("places the witness inside a bar that has a stated start", () => {
    const geometry = stripGeometry(
      interval({ end: UNKNOWN, witnessed_at: at("2001-06-07T00:00:00Z") }),
      place,
      BOUNDS,
      FADE,
    )!;

    expect(geometry.witnessAt).toBeGreaterThan(geometry.bodyTop);
    expect(geometry.witnessAt).toBeLessThanOrEqual(geometry.bodyBottom);
  });

  it("leaves a witness nobody dated off the bar", () => {
    const geometry = stripGeometry(
      interval({ witnessed_at: { instant_kind: "named", label: "that summer" } }),
      place,
      BOUNDS,
      FADE,
    )!;

    expect(geometry.witnessAt).toBeNull();
  });

  it("draws a named end as a stub with the source's words at its end", () => {
    // §13.2 rule 3: the date goes where dates go, and the word is drawn where
    // the edge would be. A fade here would say "we do not know", about an edge
    // the source named.
    const geometry = stripGeometry(
      interval({ end: named("the Renaissance") }),
      place,
      BOUNDS,
      FADE,
    )!;

    expect(geometry.endMark).toBe("named");
    expect(geometry.labelBelow).toEqual({ reach: FADE, label: "the Renaissance" });
    expect(geometry.fadeBelow).toBe(0);
    // The dated edge keeps the mark it earned.
    expect(geometry.startMark).toBe("cap");
    expect(geometry.bodyTop).toBe(0);
  });

  it("draws a named start as a stub above the body", () => {
    const geometry = stripGeometry(
      interval({ start: named("the Renaissance") }),
      place,
      BOUNDS,
      FADE,
    )!;

    expect(geometry.startMark).toBe("named");
    expect(geometry.labelAbove).toEqual({ reach: FADE, label: "the Renaissance" });
    expect(geometry.labelBelow).toBeNull();
    expect(geometry.endMark).toBe("cap");
    // Nothing places the start, so the body collapses onto the dated end, the
    // way it does for an unknown edge.
    expect(geometry.bodyTop).toBe(geometry.bodyBottom);
    expect(geometry.bodyBottom).toBeCloseTo(place(Date.parse("2010-05-11T00:00:00Z")));
  });

  it("counts a stub into the strip's extent, so a lane makes room for it", () => {
    const above = stripGeometry(interval({ start: named("then") }), place, BOUNDS, FADE)!;
    const below = stripGeometry(interval({ end: named("then") }), place, BOUNDS, FADE)!;

    expect(above.top).toBe(above.bodyTop - FADE);
    expect(above.bottom).toBe(above.bodyBottom);
    expect(below.bottom).toBe(below.bodyBottom + FADE);
    expect(below.top).toBe(below.bodyTop);
  });

  it("draws a named edge on a bar a witness alone places", () => {
    const geometry = stripGeometry(
      interval({
        start: named("under the USSR"),
        end: UNKNOWN,
        witnessed_at: at("1970-03-01T00:00:00Z"),
      }),
      place,
      BOUNDS,
      FADE,
    )!;
    const witness = place(Date.parse("1970-03-01T00:00:00Z"));

    expect(geometry.bodyTop).toBeCloseTo(witness);
    expect(geometry.labelAbove!.label).toBe("under the USSR");
    // Each endpoint wears its own mark: a stub above, fog below.
    expect(geometry.fadeBelow).toBe(FADE);
    expect(geometry.fadeAbove).toBe(0);
  });

  it("refuses an interval a word is the only thing at either edge of", () => {
    // Two words, or a word beside an edge that places nothing: there is no
    // date anywhere, so there is no honest place to draw the stub.
    for (const nowhere of [
      interval({ start: named("the Renaissance"), end: named("the Enlightenment") }),
      interval({ start: named("the Renaissance"), end: UNKNOWN }),
      interval({ start: named("the Renaissance"), end: UNBOUNDED }),
    ]) {
      expect(stripGeometry(nowhere, place, BOUNDS, FADE)).toBeNull();
    }
  });

  it("refuses to place an interval with no date at either edge and no witness", () => {
    const nothing = interval({ start: UNKNOWN, end: UNKNOWN });

    expect(stripGeometry(nothing, place, BOUNDS, FADE)).toBeNull();
  });

  it("keeps a resolved label on the axis, with soft edges", () => {
    const renaissance = interval({
      start: at("1400-01-01T00:00:00Z", "the Renaissance"),
      end: at("1600-01-01T00:00:00Z", "the Renaissance"),
    });

    const geometry = stripGeometry(renaissance, place, BOUNDS, FADE)!;

    expect(geometry.startMark).toBe("soft");
    expect(geometry.endMark).toBe("soft");
  });
});

describe("the stretch of time an interval covers", () => {
  it("runs from one stated date to the other", () => {
    expect(intervalSpan(interval())).toEqual({
      from: Date.parse("1997-05-02T00:00:00Z"),
      to: Date.parse("2010-05-11T00:00:00Z"),
    });
  });

  it("runs for ever past an unbounded edge", () => {
    expect(intervalSpan(interval({ end: UNBOUNDED })!)!.to).toBe(Infinity);
    expect(intervalSpan(interval({ start: UNBOUNDED })!)!.from).toBe(-Infinity);
  });

  it("collapses an unknown edge onto the nearest dated instant", () => {
    // Which is where the body stops and the fog starts, so the span says what
    // is drawn rather than claiming the fog as asserted time.
    const span = intervalSpan(interval({ end: UNKNOWN }))!;

    expect(span.to).toBe(Date.parse("1997-05-02T00:00:00Z"));
  });

  it("collapses a named edge onto the date that places the interval", () => {
    // The stub is a fixed length in pixels, so it claims no time: the span
    // stops at the date, exactly as it does past an unknown edge.
    const span = intervalSpan(interval({ end: named("the Renaissance") }))!;

    expect(span.from).toBe(Date.parse("1997-05-02T00:00:00Z"));
    expect(span.to).toBe(Date.parse("1997-05-02T00:00:00Z"));
  });

  it("places an interval a witness alone dates, whatever its edges say", () => {
    const span = intervalSpan(
      interval({
        start: named("under the USSR"),
        end: UNKNOWN,
        witnessed_at: at("1970-03-01T00:00:00Z"),
      }),
    )!;

    expect(span.from).toBe(Date.parse("1970-03-01T00:00:00Z"));
  });

  it("agrees with the geometry about what has no place at all", () => {
    // The panel reserves a lane for whatever the span accepts, so the two
    // refusing different things would leave an empty column beside the axis.
    for (const nowhere of [
      interval({ start: named("the Renaissance"), end: named("the Enlightenment") }),
      interval({ start: named("the Renaissance"), end: UNKNOWN }),
      interval({ start: named("the Renaissance"), end: UNBOUNDED }),
      interval({ start: UNBOUNDED, end: named("the Renaissance") }),
      interval({ start: UNKNOWN, end: UNKNOWN }),
    ]) {
      expect(intervalSpan(nowhere)).toBeNull();
      expect(stripGeometry(nowhere, place, BOUNDS, FADE)).toBeNull();
    }
  });

  it("agrees with the geometry about what a word at one edge still places", () => {
    for (const drawn of [
      interval({ start: named("the Renaissance") }),
      interval({ end: named("the Renaissance") }),
      interval({
        start: named("the Renaissance"),
        end: UNKNOWN,
        witnessed_at: at("1970-03-01T00:00:00Z"),
      }),
    ]) {
      expect(intervalSpan(drawn)).not.toBeNull();
      expect(stripGeometry(drawn, place, BOUNDS, FADE)).not.toBeNull();
    }
  });
});

// --- The envelope ---

describe("the union envelope", () => {
  it("covers every strip and claims nothing beyond them", () => {
    const envelope = unionEnvelope([
      { top: 40, bottom: 120 },
      { top: 10, bottom: 60 },
    ]);

    expect(envelope).toEqual({ top: 10, bottom: 120 });
  });

  it("is nothing at all when no source placed anything", () => {
    expect(unionEnvelope([])).toBeNull();
  });
});

// --- Lane packing ---

describe("packing lanes", () => {
  it("gives overlapping strips lanes of their own", () => {
    expect(packSlots([{ top: 0, bottom: 50 }, { top: 20, bottom: 70 }], 6)).toEqual([0, 1]);
  });

  it("reuses a lane once the strip above it has finished", () => {
    expect(
      packSlots([{ top: 0, bottom: 50 }, { top: 60, bottom: 90 }], 6),
    ).toEqual([0, 0]);
  });

  it("keeps a gap between two strips sharing a lane", () => {
    // Touching bars in one lane read as one bar, which would draw a period
    // nobody asserted across the join.
    expect(packSlots([{ top: 0, bottom: 50 }, { top: 53, bottom: 90 }], 6)).toEqual([0, 1]);
  });

  it("packs three strips into two lanes when the first has ended", () => {
    expect(
      packSlots(
        [
          { top: 0, bottom: 50 },
          { top: 10, bottom: 40 },
          { top: 80, bottom: 120 },
        ],
        6,
      ),
    ).toEqual([0, 1, 0]);
  });
});

// --- Succession ---

describe("temporally_followed_by connectors", () => {
  const link = (from: string, to: string): EdgeView =>
    edge({ src_id: from, dst_id: to, edge_type: "temporally_followed_by" });

  it("follows a chain from the claim nothing precedes", () => {
    const order = successionOrder(
      [link("petrograd", "leningrad"), link("stpetersburg", "petrograd")],
      new Set(["stpetersburg", "petrograd", "leningrad"]),
    );

    expect(order).toEqual([
      { from: "stpetersburg", to: "petrograd" },
      { from: "petrograd", to: "leningrad" },
    ]);
  });

  it("draws every step of a cycle once and stops", () => {
    // Recurrence makes a cycle legal for this edge (§13.4), so the walk has to
    // end on a revisited step rather than run for ever.
    const order = successionOrder(
      [link("a", "b"), link("b", "c"), link("c", "a")],
      new Set(["a", "b", "c"]),
    );

    expect(order).toHaveLength(3);
    expect(new Set(order.map((step) => `${step.from}|${step.to}`)).size).toBe(3);
  });

  it("draws a claim that follows itself once", () => {
    expect(successionOrder([link("a", "a")], new Set(["a"]))).toEqual([
      { from: "a", to: "a" },
    ]);
  });

  it("leaves out a step whose other end is not on screen", () => {
    expect(successionOrder([link("a", "b")], new Set(["a"]))).toEqual([]);
  });

  it("ignores every other kind of edge", () => {
    expect(
      successionOrder([edge({ src_id: "a", dst_id: "b" })], new Set(["a", "b"])),
    ).toEqual([]);
  });
});

// --- Clocks ---

describe("which clock an interval is measured on", () => {
  /** A timeline that follows the wall clock, which is what no present means. */
  const wallClock: AxisClock = { timelineId: "westminster", referenceTime: null };
  /** A timeline with a present of its own, so a clock of its own. */
  const ownClock: AxisClock = {
    timelineId: "in-universe",
    referenceTime: "0300-01-01T00:00:00Z",
  };

  it("draws an interval that names this timeline, whatever clock it keeps", () => {
    expect(clockMatches(interval({ timeline_id: "westminster" }), wallClock)).toBe(true);
    expect(clockMatches(interval({ timeline_id: "in-universe" }), ownClock)).toBe(true);
  });

  it("draws a real-world interval on a timeline that follows the wall clock", () => {
    // A null clock is the default wall-clock timeline, and a timeline that
    // states no present is that timeline (VALIDITY_DESIGN §2.5).
    expect(clockMatches(interval(), wallClock)).toBe(true);
  });

  it("draws a real-world interval when no timeline is selected", () => {
    expect(clockMatches(interval(), { timelineId: null, referenceTime: null })).toBe(
      true,
    );
  });

  it("keeps a real-world interval off a timeline with a present of its own", () => {
    // The in-universe axis and the CE axis are two clocks, and comparison
    // across them is unknown by definition (§13.2 rule 6).
    expect(clockMatches(interval(), ownClock)).toBe(false);
  });

  it("keeps an interval measured on another named clock off this axis", () => {
    expect(clockMatches(interval({ timeline_id: "in-universe" }), wallClock)).toBe(
      false,
    );
  });
});

// --- The record behind the mark ---

describe("what the tooltip says", () => {
  it("names each endpoint kind by its word rather than as a date", () => {
    expect(describeEndpoint(UNKNOWN)).toBe("unknown");
    expect(describeEndpoint(UNBOUNDED)).toBe("unbounded");
    expect(describeEndpoint({ instant_kind: "named", label: "the Renaissance" })).toBe(
      '"the Renaissance" (named, no date)',
    );
  });

  it("keeps a resolved endpoint's own words beside its date", () => {
    expect(describeEndpoint(at("1400-01-01T00:00:00Z", "the Renaissance"))).toBe(
      '1400-01-01T00:00:00Z ("the Renaissance")',
    );
  });

  it("gives the source, both edges, the basis, the witness and the clock", () => {
    const text = describeInterval(
      "almanac, 2011",
      interval({
        timeline_id: "westminster",
        witnessed_at: at("2011-03-01T00:00:00Z"),
        basis: "inferred",
      }),
    );

    expect(text).toContain("almanac, 2011");
    expect(text).toContain("1997-05-02T00:00:00Z");
    expect(text).toContain("2010-05-11T00:00:00Z");
    expect(text).toContain("inferred");
    expect(text).toContain("2011-03-01T00:00:00Z");
    expect(text).toContain("westminster");
  });

  it("says so when there is no witness and no clock", () => {
    const text = describeInterval("a blog", interval());

    expect(text).toContain("witnessed none");
    expect(text).toContain("default wall clock");
  });
});


describe("a boundary reflect proposes", () => {
  const PROPOSED = Date.parse("2018-01-01T00:00:00Z");

  it("replaces the fade below an open end and reaches out to the date", () => {
    const open = interval({ end: UNKNOWN });

    const geometry = stripGeometry(open, place, BOUNDS, FADE, [proposal()])!;

    expect(geometry.endMark).toBe("proposed");
    // One mark per endpoint: the fog goes when the offer arrives.
    expect(geometry.fadeBelow).toBe(0);
    expect(geometry.proposedBelow).not.toBeNull();
    expect(geometry.proposedBelow!.at).toBeCloseTo(place(PROPOSED));
    expect(geometry.proposedBelow!.label).toBe("2018-01-01T00:00:00Z");
    expect(geometry.proposedBelow!.because).toBe("f2");
    // Counted into the extent, so lanes and the envelope leave room for it.
    expect(geometry.bottom).toBeCloseTo(place(PROPOSED));
  });

  it("does the same above an open start", () => {
    const open = interval({ start: UNKNOWN });
    const earlier = proposal({ endpoint: "start", at: "1990-02-03T00:00:00Z" });

    const geometry = stripGeometry(open, place, BOUNDS, FADE, [earlier])!;

    expect(geometry.startMark).toBe("proposed");
    expect(geometry.fadeAbove).toBe(0);
    expect(geometry.proposedAbove!.at).toBeCloseTo(place(Date.parse("1990-02-03T00:00:00Z")));
    expect(geometry.top).toBeCloseTo(place(Date.parse("1990-02-03T00:00:00Z")));
  });

  it("leaves the solid body exactly where the record's own dates put it", () => {
    // §13.2 rule 9: a proposal never thickens the body. The solid part stops at
    // the last date the source gave, whatever is offered past it.
    const open = interval({ end: UNKNOWN });
    const bare = stripGeometry(open, place, BOUNDS, FADE)!;

    const offered = stripGeometry(open, place, BOUNDS, FADE, [proposal()])!;

    expect(offered.bodyTop).toBe(bare.bodyTop);
    expect(offered.bodyBottom).toBe(bare.bodyBottom);
  });

  it("ignores a proposal measured on another clock", () => {
    // There is no conversion between an in-universe date and a real one, so a
    // proposal from another clock places nothing here.
    const open = interval({ end: UNKNOWN });

    const geometry = stripGeometry(open, place, BOUNDS, FADE, [
      proposal({ timeline_id: "in-universe" }),
    ])!;

    expect(geometry.endMark).toBe("fade");
    expect(geometry.proposedBelow).toBeNull();
    expect(geometry.fadeBelow).toBe(FADE);
  });

  it("ignores a proposal for an edge that already has a date", () => {
    // Defensive: the server proposes only against an open edge. Drawing one
    // here would show a date nobody accepted over a date somebody stated.
    const geometry = stripGeometry(interval(), place, BOUNDS, FADE, [proposal()])!;

    expect(geometry.endMark).toBe("cap");
    expect(geometry.proposedBelow).toBeNull();
    expect(geometry.bottom).toBe(geometry.bodyBottom);
  });

  it("ignores a proposal for the other endpoint", () => {
    const open = interval({ end: UNKNOWN });

    const geometry = stripGeometry(open, place, BOUNDS, FADE, [
      proposal({ endpoint: "start" }),
    ])!;

    expect(geometry.endMark).toBe("fade");
    expect(geometry.proposedAbove).toBeNull();
    expect(geometry.proposedBelow).toBeNull();
  });

  it("leaves an interval nothing dates in the tray, with the offer flagged", () => {
    // An offer is not a date the graph holds, so it cannot place what the
    // record leaves unplaced (§13.4).
    const unplaced = { ...interval({ start: UNKNOWN, end: UNKNOWN }), witnessed_at: null };
    const snapshot = {
      nodes: [
        node({ node_id: "f1" }),
        node({ node_id: "doc-a", node_type: "document", content: "almanac, 2011" }),
      ],
      edges: [edge({ src_id: "f1", dst_id: "doc-a", validity: [unplaced] })],
      boundary_proposals: [proposal()],
    };

    const { lanes, chips } = validityLayout(snapshot, [mark("tp1", ["f1"])], NO_CLOCK, false);

    expect(lanes).toHaveLength(0);
    expect(chips).toHaveLength(1);
    expect(chips[0].reason).toBe("unplaced");
    expect(chips[0].proposed).toBe(true);
  });

  it("flags no chip when nothing has been offered", () => {
    const unplaced = { ...interval({ start: UNKNOWN, end: UNKNOWN }), witnessed_at: null };
    const snapshot = {
      nodes: [
        node({ node_id: "f1" }),
        node({ node_id: "doc-a", node_type: "document", content: "almanac, 2011" }),
      ],
      edges: [edge({ src_id: "f1", dst_id: "doc-a", validity: [unplaced] })],
    };

    const { chips } = validityLayout(snapshot, [mark("tp1", ["f1"])], NO_CLOCK, false);

    expect(chips[0].proposed).toBe(false);
  });

  it("hands a strip only the offers made about its own claim and source", () => {
    const snapshot = {
      nodes: [
        node({ node_id: "f1" }),
        node({ node_id: "doc-a", node_type: "document", content: "almanac, 2011" }),
        node({ node_id: "doc-b", node_type: "document", content: "a blog" }),
      ],
      edges: [
        edge({ src_id: "f1", dst_id: "doc-a", validity: [interval({ end: UNKNOWN })] }),
        edge({ src_id: "f1", dst_id: "doc-b", validity: [interval({ end: UNKNOWN })] }),
      ],
      boundary_proposals: [proposal({ source_id: "doc-b" })],
    };

    const { lanes } = validityLayout(snapshot, [mark("tp1", ["f1"])], NO_CLOCK, false);

    const [blog, almanac] = lanes;
    expect(blog.sourceLabel).toBe("a blog");
    expect(blog.strips[0].proposals).toHaveLength(1);
    expect(almanac.strips[0].proposals).toHaveLength(0);
  });

  it("says in the tooltip what was offered and where it was read from", () => {
    const text = describeInterval("almanac, 2011", interval({ end: UNKNOWN }), [proposal()]);

    expect(text).toContain("unknown (proposed 2018-01-01T00:00:00Z from f2)");
  });

  it("still says plain unknown for an edge nobody has offered a date for", () => {
    expect(describeInterval("almanac, 2011", interval({ end: UNKNOWN }))).toContain(
      "end       unknown",
    );
  });
});

// --- The layout ---

describe("laying out what the sources assert", () => {
  const snapshot = {
    nodes: [
      node({ node_id: "f1" }),
      node({ node_id: "doc-a", node_type: "document", content: "almanac, 2011" }),
      node({ node_id: "doc-b", node_type: "document", content: "a blog" }),
    ],
    edges: [
      edge({ src_id: "f1", dst_id: "doc-a", validity: [interval()] }),
      edge({
        src_id: "f1",
        dst_id: "doc-b",
        validity: [interval({ start: at("1995-05-01T00:00:00Z") })],
      }),
    ],
  };

  it("gives each source its own lane, named after the source", () => {
    const { lanes } = validityLayout(snapshot, [mark("tp1", ["f1"])], NO_CLOCK, false);

    expect(lanes).toHaveLength(2);
    expect(lanes.map((lane) => lane.sourceLabel)).toEqual(["a blog", "almanac, 2011"]);
    expect(lanes.every((lane) => lane.strips.length === 1)).toBe(true);
  });

  it("keeps several periods from one source in that source's lane", () => {
    const twice = {
      ...snapshot,
      edges: [
        edge({
          src_id: "f1",
          dst_id: "doc-a",
          validity: [interval(), interval({ start: at("2024-07-05T00:00:00Z"), end: UNKNOWN })],
        }),
      ],
    };

    const { lanes } = validityLayout(twice, [mark("tp1", ["f1"])], NO_CLOCK, false);

    expect(lanes).toHaveLength(1);
    expect(lanes[0].strips).toHaveLength(2);
  });

  it("names a source the snapshot does not hold by its id", () => {
    const unresolvable = {
      nodes: [node({ node_id: "f1" })],
      edges: [edge({ src_id: "f1", dst_id: "doc-z", validity: [interval()] })],
    };

    const { lanes } = validityLayout(unresolvable, [mark("tp1", ["f1"])], NO_CLOCK, false);

    expect(lanes[0].sourceLabel).toBe("doc-z");
  });

  it("puts an inference's lanes on the inference side", () => {
    const { lanes } = validityLayout(snapshot, [mark("tp1", ["f1"], "right")], NO_CLOCK, false);

    expect(lanes.every((lane) => lane.side === "right")).toBe(true);
  });

  it("reads validity off sourced_from edges and no others", () => {
    const tagged = {
      nodes: [node({ node_id: "f1" })],
      edges: [
        edge({
          src_id: "f1",
          dst_id: "topic-1",
          edge_type: "tagged_with_topic",
          validity: [interval()],
        }),
      ],
    };

    expect(validityLayout(tagged, [mark("tp1", ["f1"])], NO_CLOCK, false).lanes).toEqual([]);
  });

  it("keeps a historical claim, which is still true of its period", () => {
    const retired = {
      ...snapshot,
      nodes: snapshot.nodes.map((n) => (n.node_id === "f1" ? { ...n, status: "historical" } : n)),
    };

    const { lanes } = validityLayout(retired, [mark("tp1", ["f1"])], NO_CLOCK, false);

    expect(lanes).toHaveLength(2);
    expect(lanes.every((lane) => lane.status === "historical")).toBe(true);
  });

  it("hides a corrected claim until it is asked for", () => {
    const wrong = {
      ...snapshot,
      nodes: snapshot.nodes.map((n) => (n.node_id === "f1" ? { ...n, status: "corrected" } : n)),
    };

    expect(validityLayout(wrong, [mark("tp1", ["f1"])], NO_CLOCK, false).lanes).toEqual([]);
    expect(validityLayout(wrong, [mark("tp1", ["f1"])], NO_CLOCK, true).lanes).toHaveLength(2);
  });

  const oneInterval = (over: Partial<ValidityIntervalView>) => ({
    nodes: [node({ node_id: "f1" })],
    edges: [edge({ src_id: "f1", dst_id: "doc-a", validity: [interval(over)] })],
  });

  it("sends a label nothing else places to the tray with its words intact", () => {
    const { lanes, chips } = validityLayout(
      oneInterval({ start: named("under the USSR"), end: UNKNOWN }),
      [mark("tp1", ["f1"])],
      NO_CLOCK,
      false,
    );

    expect(lanes).toEqual([]);
    expect(chips).toHaveLength(1);
    expect(chips[0].reason).toBe("unresolved");
    expect(chips[0].label).toContain("under the USSR");
  });

  it("sends a label beside an unbounded edge to the tray", () => {
    // "No boundary" places the bar at the edge of the panel and says nothing
    // about where the named edge is, so no date is left anywhere.
    const { lanes, chips } = validityLayout(
      oneInterval({ start: named("under the USSR"), end: UNBOUNDED }),
      [mark("tp1", ["f1"])],
      NO_CLOCK,
      false,
    );

    expect(lanes).toEqual([]);
    expect(chips.map((chip) => chip.reason)).toEqual(["unresolved"]);
  });

  it("sends an interval named at both edges to the tray", () => {
    const { lanes, chips } = validityLayout(
      oneInterval({ start: named("the Renaissance"), end: named("the Enlightenment") }),
      [mark("tp1", ["f1"])],
      NO_CLOCK,
      false,
    );

    expect(lanes).toEqual([]);
    expect(chips.map((chip) => chip.reason)).toEqual(["unresolved"]);
  });

  it("draws an interval a date places, whichever edge the word is at", () => {
    for (const over of [
      { start: named("under the USSR") },
      { end: named("under the USSR") },
      {
        start: named("under the USSR"),
        end: UNKNOWN,
        witnessed_at: at("1970-03-01T00:00:00Z"),
      },
    ]) {
      const { lanes, chips } = validityLayout(
        oneInterval(over),
        [mark("tp1", ["f1"])],
        NO_CLOCK,
        false,
      );

      expect(lanes).toHaveLength(1);
      expect(lanes[0].strips).toHaveLength(1);
      expect(chips).toEqual([]);
    }
  });

  it("still says the word is a word in the record behind the mark", () => {
    const { lanes } = validityLayout(
      oneInterval({ start: named("under the USSR") }),
      [mark("tp1", ["f1"])],
      NO_CLOCK,
      false,
    );

    expect(lanes[0].strips[0].detail).toContain('"under the USSR" (named, no date)');
  });

  it("sends a claim measured on another clock to the tray", () => {
    const elsewhere = {
      nodes: [node({ node_id: "f1" })],
      edges: [
        edge({
          src_id: "f1",
          dst_id: "doc-a",
          validity: [interval({ timeline_id: "in-universe" })],
        }),
      ],
    };

    const { lanes, chips } = validityLayout(
      elsewhere,
      [mark("tp1", ["f1"])],
      { timelineId: "westminster", referenceTime: null },
      false,
    );

    expect(lanes).toEqual([]);
    expect(chips.map((chip) => chip.reason)).toEqual(["other clock"]);
  });

  it("sends a claim with no date anywhere to the tray", () => {
    const nowhere = {
      nodes: [node({ node_id: "f1" })],
      edges: [
        edge({
          src_id: "f1",
          dst_id: "doc-a",
          validity: [interval({ start: UNKNOWN, end: UNKNOWN })],
        }),
      ],
    };

    const { chips } = validityLayout(nowhere, [mark("tp1", ["f1"])], NO_CLOCK, false);

    expect(chips.map((chip) => chip.reason)).toEqual(["unplaced"]);
  });

  it("draws one lane for a fact two marks both link to", () => {
    // The same source saying the same thing twice is one claim, and a second
    // lane would read as a second source.
    const { lanes } = validityLayout(
      snapshot,
      [mark("tp1", ["f1"]), mark("tp2", ["f1"])],
      NO_CLOCK,
      false,
    );

    expect(lanes).toHaveLength(2);
  });
});
