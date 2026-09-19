/**
 * Valid time, turned into something the panel can draw.
 *
 * A `sourced_from` edge carries the periods **that source** says the fact was
 * true (`VALIDITY_DESIGN.md` §2.3). There is no union and never will be: two
 * sources disagreeing about one episode and two sources describing different
 * episodes are the same shape in a merged bar, and nothing in the data says
 * which it is. So the unit here is one source's lane, and the panel draws a
 * strip per period in it.
 *
 * The grammar is `TIMELINE_VISUALISATION.md` §13. Its load-bearing part is that
 * nothing is drawn the data does not assert: an unknown edge dissolves, an
 * unbounded one leaves the panel at full weight, a label nobody has resolved
 * gets no coordinate at all, and the space between two periods stays empty
 * because outside a stated interval is *no assertion* rather than false.
 *
 * Pure: pixels in, pixels out, no DOM. The parts most likely to be wrong are
 * here, where they can be tested without a browser.
 */

import type { Side, SnapshotLike } from "./timeline-model";
import type { EdgeView, ImpreciseInstantView, ValidityIntervalView } from "./types";

const EDGE_SOURCED_FROM = "sourced_from";
const EDGE_TEMPORAL_SUCCESSION = "temporally_followed_by";

/** We were wrong about this one, so it is hidden until somebody asks for it. */
const STATUS_CORRECTED = "corrected";

/**
 * What an endpoint gets drawn as.
 *
 * Five marks for four kinds, because a `precise` endpoint carrying the source's
 * own words is a resolved label rather than a date anybody stated: resolution
 * added a position and left the words standing, so the edge stays soft.
 */
export type EndpointMark = "cap" | "soft" | "fade" | "exit" | "named";

const unhandled = (endpoint: never): never => {
  throw new Error(`an endpoint kind the grammar does not draw: ${JSON.stringify(endpoint)}`);
};

/**
 * The mark for one endpoint.
 *
 * The final branch takes `never`, so a fifth `instant_kind` fails the build
 * here rather than drawing as whatever the last case happened to be.
 */
export const endpointMark = (endpoint: ImpreciseInstantView): EndpointMark => {
  switch (endpoint.instant_kind) {
    case "precise":
      return endpoint.label === null ? "cap" : "soft";
    case "named":
      return "named";
    case "unknown":
      return "fade";
    case "unbounded":
      return "exit";
    default:
      return unhandled(endpoint);
  }
};

/** The instant an endpoint puts on the axis, or null where it places none. */
export const endpointAt = (endpoint: ImpreciseInstantView | null): number | null => {
  if (endpoint === null || endpoint.instant_kind !== "precise") return null;
  const parsed = Date.parse(endpoint.at);
  return Number.isNaN(parsed) ? null : parsed;
};

/** An endpoint in as few words as possible, for a chip. */
export const endpointWords = (endpoint: ImpreciseInstantView): string => {
  switch (endpoint.instant_kind) {
    case "precise":
      return endpoint.at;
    case "named":
      return endpoint.label;
    case "unknown":
      return "unknown";
    case "unbounded":
      return "unbounded";
    default:
      return unhandled(endpoint);
  }
};

/**
 * An endpoint as the tooltip states it: the words for what has no date.
 *
 * Never a date the model does not hold. "Unknown" printed as a timestamp is
 * the collapse the three endpoint states exist to prevent.
 */
export const describeEndpoint = (endpoint: ImpreciseInstantView): string => {
  switch (endpoint.instant_kind) {
    case "precise":
      return endpoint.label === null ? endpoint.at : `${endpoint.at} ("${endpoint.label}")`;
    case "named":
      return `"${endpoint.label}" (named, no date)`;
    case "unknown":
      return "unknown";
    case "unbounded":
      return "unbounded";
    default:
      return unhandled(endpoint);
  }
};

const field = (name: string, value: string): string => `${name.padEnd(9)} ${value}`;

/**
 * The whole `(source, interval)` pair in words.
 *
 * The mark is the summary and this is the record (§13.4), so every field the
 * strip compresses is spelt out: both edges, the basis, the witness, the clock.
 */
export const describeInterval = (
  sourceLabel: string,
  interval: ValidityIntervalView,
): string =>
  [
    sourceLabel,
    field("start", describeEndpoint(interval.start)),
    field("end", describeEndpoint(interval.end)),
    field("basis", interval.basis),
    field(
      "witnessed",
      interval.witnessed_at === null ? "none" : describeEndpoint(interval.witnessed_at),
    ),
    field("timeline", interval.timeline_id ?? "default wall clock"),
  ].join("\n");

/** The clock the axis on screen keeps, which decides what may be drawn on it. */
export interface AxisClock {
  /** The timeline selected, null when none is. */
  timelineId: string | null;
  /** Its own present, null when it follows the wall clock. */
  referenceTime: string | null;
}

/**
 * Whether an interval belongs on the axis a given timeline draws.
 *
 * An interval naming this timeline always does. An interval naming no clock is
 * measured against the default wall-clock timeline, which is the timeline that
 * states no present of its own (`VALIDITY_DESIGN.md` §2.5), so it belongs on
 * that axis and on no other: a timeline with a `reference_time` keeps its own
 * clock, and drawing a real-world period against it would assert a mapping
 * between the two that nobody made (§13.2 rule 6). Everything else is the
 * cross-clock case, where comparison is unknown by definition.
 */
export const clockMatches = (
  interval: ValidityIntervalView,
  axis: AxisClock,
): boolean =>
  interval.timeline_id === null
    ? axis.referenceTime === null
    : interval.timeline_id === axis.timelineId;

// --- Geometry ---

export interface StripBounds {
  top: number;
  bottom: number;
}

/**
 * The stretch of time an interval covers, for deciding whether it is in view.
 *
 * Infinite on an unbounded edge, because that edge genuinely has no end, and
 * collapsed onto the nearest placed instant on an unknown one, which is where
 * the geometry puts the body before the fog starts. Null wherever
 * `stripGeometry` would also refuse: the two must agree about what has a place
 * on the axis, or the panel would reserve a lane for a strip it never draws.
 */
export const intervalSpan = (
  interval: ValidityIntervalView,
): { from: number; to: number } | null => {
  const startMark = endpointMark(interval.start);
  const endMark = endpointMark(interval.end);
  if (startMark === "named" || endMark === "named") return null;

  const startAt = endpointAt(interval.start);
  const endAt = endpointAt(interval.end);
  const witnessAt = endpointAt(interval.witnessed_at);
  const from = startAt ?? (startMark === "exit" ? -Infinity : null);
  const to = endAt ?? (endMark === "exit" ? Infinity : null);
  if (from === null && to === null && witnessAt === null) return null;

  return {
    from: from ?? witnessAt ?? to!,
    to: to ?? witnessAt ?? from!,
  };
};

export interface StripGeometry {
  /** The period the source states, in pixels. */
  bodyTop: number;
  bodyBottom: number;
  startMark: EndpointMark;
  endMark: EndpointMark;
  /** How far the fog runs past each edge; zero where that edge does not fade. */
  fadeAbove: number;
  fadeBelow: number;
  /** Where the witness dot goes, or null when the source dated no witness. */
  witnessAt: number | null;
  /** Everything drawn, fades included. What the envelope unions. */
  top: number;
  bottom: number;
}

/**
 * Where one interval is drawn, or null when it has no place on the axis.
 *
 * Null for the two cases §13.2 keeps off the axis: an edge that is only a label
 * (rule 3, resolution adds a position and until then there is none), and an
 * interval nothing anchors at all. Both go to the tray instead, words intact.
 *
 * An unbounded edge is an anchor: "there is no boundary" says where the bar
 * goes, which is off the panel at full weight. An unknown edge is not, which is
 * why the two cannot share a treatment.
 */
export const stripGeometry = (
  interval: ValidityIntervalView,
  place: (at: number) => number,
  bounds: StripBounds,
  fade: number,
): StripGeometry | null => {
  const startMark = endpointMark(interval.start);
  const endMark = endpointMark(interval.end);
  if (startMark === "named" || endMark === "named") return null;

  const startAt = endpointAt(interval.start);
  const endAt = endpointAt(interval.end);
  const witnessAt = endpointAt(interval.witnessed_at);

  const startAnchor =
    startAt !== null ? place(startAt) : startMark === "exit" ? bounds.top : null;
  const endAnchor =
    endAt !== null ? place(endAt) : endMark === "exit" ? bounds.bottom : null;
  const witness = witnessAt === null ? null : place(witnessAt);
  if (startAnchor === null && endAnchor === null && witness === null) return null;

  // A missing edge falls back to the next thing that is placed, so a bar with
  // only a witness collapses onto it and fades away in both directions.
  const bodyTop = startAnchor ?? witness ?? endAnchor!;
  const bodyBottom = Math.max(bodyTop, endAnchor ?? witness ?? startAnchor!);
  const fadeAbove = startMark === "fade" ? fade : 0;
  const fadeBelow = endMark === "fade" ? fade : 0;

  return {
    bodyTop,
    bodyBottom,
    startMark,
    endMark,
    fadeAbove,
    fadeBelow,
    witnessAt: witness,
    top: bodyTop - fadeAbove,
    bottom: bodyBottom + fadeBelow,
  };
};

/**
 * The extent any source asserts, as an outline.
 *
 * Taken from what is drawn rather than from the dates, so the envelope cannot
 * claim an edge the strips inside it left open. It is the only summary §13.1
 * allows, and it is hollow: a filled union bar would state a period no single
 * source stated.
 */
export const unionEnvelope = (spans: readonly StripBounds[]): StripBounds | null => {
  if (spans.length === 0) return null;
  return {
    top: Math.min(...spans.map((span) => span.top)),
    bottom: Math.max(...spans.map((span) => span.bottom)),
  };
};

/**
 * A lane per span, reusing a lane once the span above it has ended.
 *
 * Column position carries no meaning (§13.3), so lanes can be reused down the
 * axis. `gap` is the room two spans need between them: without it they touch
 * and read as one period, which across the join is a claim nobody made.
 */
export const packSlots = (spans: readonly StripBounds[], gap: number): number[] => {
  const lastBottom: number[] = [];
  return spans.map((span) => {
    const free = lastBottom.findIndex((bottom) => bottom + gap <= span.top);
    const slot = free === -1 ? lastBottom.length : free;
    lastBottom[slot] = span.bottom;
    return slot;
  });
};

// --- Succession ---

export interface Succession {
  from: string;
  to: string;
}

/**
 * The `temporally_followed_by` steps to draw, each exactly once.
 *
 * Chains first, from the claims nothing precedes, so a naming history reads in
 * order. Whatever is left is a cycle, which recurrence makes legal for this
 * edge, and the walk ends there on the step it has already drawn rather than
 * going round again (§13.4).
 */
export const successionOrder = (
  edges: readonly EdgeView[],
  present: ReadonlySet<string>,
): Succession[] => {
  const outgoing = new Map<string, Succession[]>();
  const hasIncoming = new Set<string>();
  for (const edge of edges) {
    if (edge.edge_type !== EDGE_TEMPORAL_SUCCESSION) continue;
    if (!present.has(edge.src_id) || !present.has(edge.dst_id)) continue;
    const step = { from: edge.src_id, to: edge.dst_id };
    const bucket = outgoing.get(step.from);
    if (bucket) bucket.push(step);
    else outgoing.set(step.from, [step]);
    hasIncoming.add(step.to);
  }

  const drawn = new Set<string>();
  const order: Succession[] = [];
  const walk = (start: string): void => {
    const pending = [start];
    while (pending.length > 0) {
      const node = pending.pop()!;
      for (const step of outgoing.get(node) ?? []) {
        const key = `${step.from}|${step.to}`;
        if (drawn.has(key)) continue;
        drawn.add(key);
        order.push(step);
        pending.push(step.to);
      }
    }
  };

  const starts = [...outgoing.keys()];
  for (const node of starts) if (!hasIncoming.has(node)) walk(node);
  for (const node of starts) walk(node);
  return order;
};

// --- The layout ---

/** What the panel needs of a mark to lay its sources out beside it. */
export interface MarkLike {
  id: string;
  nodeIds: readonly string[];
  side: Side;
}

export interface ValidityStrip {
  id: string;
  interval: ValidityIntervalView;
  /** The `(source, interval)` pair in words, for the tooltip. */
  detail: string;
}

/** One source's claim about one fact: everything that source says about it. */
export interface ValidityLane {
  id: string;
  /** The mark this lane sits beside, for hover and selection. */
  markId: string;
  nodeId: string;
  sourceId: string;
  /** The source's own name where the snapshot holds it, else its id. */
  sourceLabel: string;
  /** The fact's status: historical is muted, corrected is hidden (§13.2 rule 5). */
  status: string;
  side: Side;
  strips: ValidityStrip[];
}

export type OffAxisReason = "unresolved" | "other clock" | "unplaced";

/** An interval with no honest place on this axis, and why it has none. */
export interface ValidityChip {
  id: string;
  label: string;
  reason: OffAxisReason;
  detail: string;
}

export interface ValidityLayout {
  lanes: ValidityLane[];
  chips: ValidityChip[];
}

const reasonFor = (
  interval: ValidityIntervalView,
  axis: AxisClock,
): OffAxisReason | null => {
  if (!clockMatches(interval, axis)) return "other clock";
  if (intervalSpan(interval) !== null) return null;
  const named =
    interval.start.instant_kind === "named" || interval.end.instant_kind === "named";
  return named ? "unresolved" : "unplaced";
};

const chipFor = (
  laneId: string,
  index: number,
  sourceLabel: string,
  interval: ValidityIntervalView,
  reason: OffAxisReason,
): ValidityChip => ({
  id: `${laneId}#${index}`,
  label: `${sourceLabel} · ${endpointWords(interval.start)} → ${endpointWords(interval.end)}`,
  reason,
  detail: describeInterval(sourceLabel, interval),
});

const byLabel = (a: ValidityLane, b: ValidityLane): number => {
  if (a.sourceLabel !== b.sourceLabel) return a.sourceLabel < b.sourceLabel ? -1 : 1;
  return a.id < b.id ? -1 : 1;
};

/**
 * Every source lane and every tray chip the marks on screen call for.
 *
 * One lane per `(fact, source)`, however many marks link that fact: the same
 * source saying the same thing twice is one claim, and a second lane would read
 * as a second source.
 *
 * `includeCorrected` mirrors the retrieval surface's `include_corrected`. Off,
 * a claim we were wrong about is hidden rather than deleted; on, it is drawn
 * struck through, which is how "what did we believe that was wrong?" gets an
 * answer without the mistake sitting among the things still believed.
 */
export const validityLayout = (
  snapshot: SnapshotLike,
  marks: readonly MarkLike[],
  axis: AxisClock,
  includeCorrected: boolean,
): ValidityLayout => {
  const nodes = new Map(snapshot.nodes.map((n) => [n.node_id, n]));
  const markFor = new Map<string, MarkLike>();
  for (const mark of marks) {
    for (const nodeId of mark.nodeIds) if (!markFor.has(nodeId)) markFor.set(nodeId, mark);
  }

  const lanes: ValidityLane[] = [];
  const chips: ValidityChip[] = [];
  for (const edge of snapshot.edges) {
    if (edge.edge_type !== EDGE_SOURCED_FROM) continue;
    const intervals = edge.validity ?? [];
    if (intervals.length === 0) continue;
    const mark = markFor.get(edge.src_id);
    if (mark === undefined) continue;
    const status = nodes.get(edge.src_id)?.status ?? "";
    if (status === STATUS_CORRECTED && !includeCorrected) continue;

    const sourceLabel = nodes.get(edge.dst_id)?.content ?? edge.dst_id;
    const id = `${edge.src_id}|${edge.dst_id}`;
    if (lanes.some((lane) => lane.id === id)) continue;

    const strips: ValidityStrip[] = [];
    intervals.forEach((interval, index) => {
      const reason = reasonFor(interval, axis);
      if (reason !== null) {
        chips.push(chipFor(id, index, sourceLabel, interval, reason));
        return;
      }
      strips.push({
        id: `${id}#${index}`,
        interval,
        detail: describeInterval(sourceLabel, interval),
      });
    });
    if (strips.length === 0) continue;

    lanes.push({
      id,
      markId: mark.id,
      nodeId: edge.src_id,
      sourceId: edge.dst_id,
      sourceLabel,
      status,
      side: mark.side,
      strips,
    });
  }

  return { lanes: lanes.sort(byLabel), chips };
};
