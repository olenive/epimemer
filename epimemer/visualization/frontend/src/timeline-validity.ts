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
 * unbounded one leaves the panel at full weight, an edge the source named
 * wears its word rather than a date, and the space between two periods stays
 * empty because outside a stated interval is *no assertion* rather than false.
 *
 * Pure: pixels in, pixels out, no DOM. The parts most likely to be wrong are
 * here, where they can be tested without a browser.
 */

import type { Side, SnapshotLike } from "./timeline-model";
import type {
  BoundaryProposalView,
  EdgeView,
  ImpreciseInstantView,
  ValidityIntervalView,
} from "./types";

const EDGE_SOURCED_FROM = "sourced_from";
const EDGE_TEMPORAL_SUCCESSION = "temporally_followed_by";

/** We were wrong about this one, so it is hidden until somebody asks for it. */
const STATUS_CORRECTED = "corrected";

/**
 * What an endpoint gets drawn as.
 *
 * Six marks for four kinds. A `precise` endpoint carrying the source's own
 * words is a resolved label rather than a date anybody stated: resolution added
 * a position and left the words standing, so the edge stays soft. And an
 * `unknown` edge reflect has offered a date for wears `proposed` instead of
 * `fade`, so an endpoint still carries one mark (§13.1).
 *
 * Every mark here is drawn, `named` included: it is the labelled edge, a stub
 * of fixed length with the source's word at the end of it (§13.1).
 */
export type EndpointMark = "cap" | "soft" | "fade" | "exit" | "named" | "proposed";

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

/** What a proposed boundary says, in the words the tooltip uses. */
export interface ProposedWords {
  /** The date itself, as this module renders a precise endpoint. */
  label: string;
  /** The claim the date was read from, which a reviewer can go and read. */
  because: string;
}

/**
 * An endpoint as the tooltip states it: the words for what has no date.
 *
 * Never a date the model does not hold. "Unknown" printed as a timestamp is
 * the collapse the three endpoint states exist to prevent. A proposal is said
 * as a proposal, beside the word `unknown` rather than in place of it: the
 * record holds no date for this edge, and holds none until somebody accepts
 * the offer.
 */
export const describeEndpoint = (
  endpoint: ImpreciseInstantView,
  proposed: ProposedWords | null = null,
): string => {
  switch (endpoint.instant_kind) {
    case "precise":
      return endpoint.label === null ? endpoint.at : `${endpoint.at} ("${endpoint.label}")`;
    case "named":
      return `"${endpoint.label}" (named, no date)`;
    case "unknown":
      return proposed === null
        ? "unknown"
        : `unknown (proposed ${proposed.label} from ${proposed.because})`;
    case "unbounded":
      return "unbounded";
    default:
      return unhandled(endpoint);
  }
};

/**
 * The proposal for one endpoint of one period, or null where there is none.
 *
 * Three refusals, each load-bearing. A proposal only ever lands on an edge the
 * record leaves **unknown**, which is the only edge the server offers a date
 * for; an edge that already has one has an answer, and replacing it here would
 * show a date nobody accepted. It must be on the period's **own clock**, since
 * there is no conversion between an in-universe date and a real one. And it
 * must name this **endpoint**: closing a period and opening it are different
 * offers.
 */
export const proposalFor = (
  interval: ValidityIntervalView,
  proposals: readonly BoundaryProposalView[],
  endpoint: "start" | "end",
): BoundaryProposalView | null => {
  if (interval[endpoint].instant_kind !== "unknown") return null;
  return (
    proposals.find(
      (proposal) =>
        proposal.endpoint === endpoint && proposal.timeline_id === interval.timeline_id,
    ) ?? null
  );
};

const wordsFor = (proposal: BoundaryProposalView | null): ProposedWords | null =>
  proposal === null ? null : { label: proposal.at, because: proposal.because_id };

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
  proposals: readonly BoundaryProposalView[] = [],
): string =>
  [
    sourceLabel,
    field(
      "start",
      describeEndpoint(interval.start, wordsFor(proposalFor(interval, proposals, "start"))),
    ),
    field(
      "end",
      describeEndpoint(interval.end, wordsFor(proposalFor(interval, proposals, "end"))),
    ),
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

/** What an interval offers the axis: the mark each edge wears, and the instants. */
interface Anchors {
  startMark: EndpointMark;
  endMark: EndpointMark;
  startAt: number | null;
  endAt: number | null;
  witnessAt: number | null;
}

const anchorsOf = (interval: ValidityIntervalView): Anchors => ({
  startMark: endpointMark(interval.start),
  endMark: endpointMark(interval.end),
  startAt: endpointAt(interval.start),
  endAt: endpointAt(interval.end),
  witnessAt: endpointAt(interval.witnessed_at),
});

/**
 * Whether anything gives this interval a place on the axis.
 *
 * A date at either edge or on the witness does, and each endpoint then wears
 * its own mark, however soft the other one is (§13.2 rule 3). An unbounded edge
 * does too, on its own: "there is no boundary" says where the bar goes, which
 * is off the panel at full weight.
 *
 * A word does not, and an unknown edge does not. So a word beside one of those
 * leaves no date anywhere, and the interval goes to the tray whole: the stub is
 * drawn where the edge would be, and nothing says where that is.
 *
 * Both `intervalSpan` and `stripGeometry` start here. The panel reserves a lane
 * for every interval the first accepts and draws what the second returns, so
 * the two have to refuse the same things.
 */
const isPlaced = ({
  startMark,
  endMark,
  startAt,
  endAt,
  witnessAt,
}: Anchors): boolean => {
  if (startAt !== null || endAt !== null || witnessAt !== null) return true;
  if (startMark === "named" || endMark === "named") return false;
  return startMark === "exit" || endMark === "exit";
};

/**
 * The stretch of time an interval covers, for deciding whether it is in view.
 *
 * Infinite on an unbounded edge, because that edge genuinely has no end, and
 * collapsed onto the nearest placed instant on an unknown or a named one. That
 * is where the geometry puts the body before the fog or the stub starts, and
 * both of those are fixed pixel lengths that claim no time.
 */
export const intervalSpan = (
  interval: ValidityIntervalView,
): { from: number; to: number } | null => {
  const anchors = anchorsOf(interval);
  if (!isPlaced(anchors)) return null;

  const { startMark, endMark, startAt, endAt, witnessAt } = anchors;
  const from = startAt ?? (startMark === "exit" ? -Infinity : null);
  const to = endAt ?? (endMark === "exit" ? Infinity : null);

  return {
    from: from ?? witnessAt ?? to!,
    to: to ?? witnessAt ?? from!,
  };
};

/**
 * The labelled edge: a stub of fixed length wearing the source's own words.
 *
 * `reach` is pixels and asserts no extent, the way a fade asserts none. The
 * source named this edge rather than dating it, so the bar keeps full weight
 * that far and the word is drawn at the end of it, verbatim (§13.1).
 */
export interface LabelledEdge {
  reach: number;
  label: string;
}

const labelledEdge = (
  endpoint: ImpreciseInstantView,
  fade: number,
): LabelledEdge | null =>
  endpoint.instant_kind === "named" ? { reach: fade, label: endpoint.label } : null;

/**
 * Where a proposed boundary falls, and what it says.
 *
 * `at` is a pixel on the axis, unlike the fixed reaches a fade and a stub use:
 * this one is a date, read from a document on the other side of a succession,
 * so it has a real place. What it does not have is the record's agreement,
 * which is why it is drawn hollow and in the pending colour rather than as
 * more bar (§13.2 rule 9).
 */
export interface ProposedEdge extends ProposedWords {
  at: number;
}

const proposedEdge = (
  interval: ValidityIntervalView,
  proposals: readonly BoundaryProposalView[],
  endpoint: "start" | "end",
  place: (at: number) => number,
): ProposedEdge | null => {
  const proposal = proposalFor(interval, proposals, endpoint);
  if (proposal === null) return null;
  const at = Date.parse(proposal.at);
  if (Number.isNaN(at)) return null;
  return { at: place(at), label: proposal.at, because: proposal.because_id };
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
  /**
   * The stub and words for a named edge, above the body and below it.
   *
   * Null where that edge is not named, and a fade and a stub never share an
   * edge: an endpoint wears one mark.
   */
  labelAbove: LabelledEdge | null;
  labelBelow: LabelledEdge | null;
  /**
   * The boundary reflect offers above the body and below it, where there is
   * one.
   *
   * Null on an edge nobody has offered a date for, and null on an edge that has
   * one already. A proposal replaces the fade on its side rather than joining
   * it, so an endpoint still wears a single mark.
   */
  proposedAbove: ProposedEdge | null;
  proposedBelow: ProposedEdge | null;
  /** Where the witness dot goes, or null when the source dated no witness. */
  witnessAt: number | null;
  /** Everything drawn, fades and stubs included. What the envelope unions. */
  top: number;
  bottom: number;
}

/**
 * Where one interval is drawn, or null when it has no place on the axis.
 *
 * Null for what §13.2 keeps off the axis: an interval nothing dates anywhere,
 * whether its edges are unknown, or words, or a word beside "no boundary".
 * Those go to the tray instead, words intact.
 *
 * A date at either edge or on the witness puts the interval on the axis, and
 * each endpoint then wears its own mark: a word at the other edge is drawn as
 * a stub with the word at its end, since a fade would say "we do not know"
 * about an edge the source named. An unbounded edge is an anchor of its own:
 * "there is no boundary" says where the bar goes, which is off the panel at
 * full weight. An unknown edge is not, which is why the two cannot share a
 * treatment.
 */
export const stripGeometry = (
  interval: ValidityIntervalView,
  place: (at: number) => number,
  bounds: StripBounds,
  fade: number,
  proposals: readonly BoundaryProposalView[] = [],
): StripGeometry | null => {
  const anchors = anchorsOf(interval);
  // A proposal places nothing. An interval nothing dates goes to the tray with
  // or without one, because the graph holds no date for it yet and an offer is
  // not a record (§13.4).
  if (!isPlaced(anchors)) return null;

  const { startAt, endAt, witnessAt } = anchors;
  const proposedAbove = proposedEdge(interval, proposals, "start", place);
  const proposedBelow = proposedEdge(interval, proposals, "end", place);
  // The proposal takes the edge's mark over from the fade, which is the mark an
  // unknown edge wears when nobody has offered a date for it.
  const startMark: EndpointMark = proposedAbove === null ? anchors.startMark : "proposed";
  const endMark: EndpointMark = proposedBelow === null ? anchors.endMark : "proposed";
  const startAnchor =
    startAt !== null ? place(startAt) : startMark === "exit" ? bounds.top : null;
  const endAnchor =
    endAt !== null ? place(endAt) : endMark === "exit" ? bounds.bottom : null;
  const witness = witnessAt === null ? null : place(witnessAt);

  // A missing edge falls back to the next thing that is placed, so a bar with
  // only a witness collapses onto it and fades away in both directions. The
  // body is the record's own dates and a proposal never moves it (§13.2 rule 9).
  const bodyTop = startAnchor ?? witness ?? endAnchor!;
  const bodyBottom = Math.max(bodyTop, endAnchor ?? witness ?? startAnchor!);
  const fadeAbove = startMark === "fade" ? fade : 0;
  const fadeBelow = endMark === "fade" ? fade : 0;
  const labelAbove = labelledEdge(interval.start, fade);
  const labelBelow = labelledEdge(interval.end, fade);

  return {
    bodyTop,
    bodyBottom,
    startMark,
    endMark,
    fadeAbove,
    fadeBelow,
    labelAbove,
    labelBelow,
    proposedAbove,
    proposedBelow,
    witnessAt: witness,
    // The stub counts towards the extent, so lane packing and the envelope
    // leave room for it, and so does the extension out to a proposed date.
    top: Math.min(bodyTop - fadeAbove - (labelAbove?.reach ?? 0), proposedAbove?.at ?? Infinity),
    bottom: Math.max(
      bodyBottom + fadeBelow + (labelBelow?.reach ?? 0),
      proposedBelow?.at ?? -Infinity,
    ),
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
  /**
   * The boundaries reflect offers for this `(claim, source)` pair.
   *
   * Carried this far unfiltered by clock and endpoint, because the geometry is
   * where those refusals belong: it is the thing that knows which edge is open.
   */
  proposals: BoundaryProposalView[];
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
  /**
   * Reflect has offered a date for one of this period's open edges.
   *
   * The chip stays in the tray either way: an offer is not a date the graph
   * holds, so it cannot place what the record leaves unplaced. The badge is
   * how the tray says there is something here to review (§13.4).
   */
  proposed: boolean;
}

export interface ValidityLayout {
  lanes: ValidityLane[];
  chips: ValidityChip[];
}

/**
 * Why an interval is a tray chip rather than a strip, or null when it is drawn.
 *
 * An interval a date places is drawn, whatever words its other edge carries.
 * What is left is `unresolved` where a word is the closest thing to a position
 * the interval has, and `unplaced` where it has no words either.
 */
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
  proposals: readonly BoundaryProposalView[],
): ValidityChip => ({
  id: `${laneId}#${index}`,
  label: `${sourceLabel} · ${endpointWords(interval.start)} → ${endpointWords(interval.end)}`,
  reason,
  detail: describeInterval(sourceLabel, interval, proposals),
  proposed:
    proposalFor(interval, proposals, "start") !== null ||
    proposalFor(interval, proposals, "end") !== null,
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
  // Keyed by the pair a proposal names, which is the pair a lane is: a claim
  // with two sources has two periods, and an offer touches exactly one of them.
  const proposalsFor = new Map<string, BoundaryProposalView[]>();
  for (const proposal of snapshot.boundary_proposals ?? []) {
    const key = `${proposal.node_id}|${proposal.source_id}`;
    const bucket = proposalsFor.get(key);
    if (bucket) bucket.push(proposal);
    else proposalsFor.set(key, [proposal]);
  }
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

    const proposals = proposalsFor.get(id) ?? [];
    const strips: ValidityStrip[] = [];
    intervals.forEach((interval, index) => {
      const reason = reasonFor(interval, axis);
      if (reason !== null) {
        chips.push(chipFor(id, index, sourceLabel, interval, reason, proposals));
        return;
      }
      strips.push({
        id: `${id}#${index}`,
        interval,
        proposals,
        detail: describeInterval(sourceLabel, interval, proposals),
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
