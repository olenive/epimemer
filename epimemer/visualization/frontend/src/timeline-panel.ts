/**
 * The timeline panel: one timeline on a vertical axis, read like a chat log.
 *
 * Past at the top, future below (dev-docs/TIMELINE_VISUALISATION.md §12.1), so
 * position along the axis increases with time exactly as the scale computes it
 * and nothing has to invert. Time gets the scroll direction, which is
 * unbounded; text gets the width, which is what it was short of.
 *
 * Facts and topics sit left of the line, inferences right, and a timepoint
 * holding both straddles it (§12.3). One timeline at a time, chosen from the
 * selector — comparing timelines is a different feature (§12.2).
 *
 * The arithmetic lives elsewhere and is tested without a browser: positions in
 * `timeline-scale`, label placement in `timeline-labels`, predicates in
 * `timeline-filter`. This module is the DOM around them.
 */

import type { EventRouter } from "./events";
import {
  NO_FILTERS,
  applyFilters,
  facetValues,
  type TimelineFilters,
} from "./timeline-filter";
import {
  LABEL_GAP,
  labelCentre,
  layoutLabels,
  leaderPoints,
  wrapText,
  type LabelRequest,
  type PlacedLabel,
} from "./timeline-labels";
import {
  allMarks,
  buildRows,
  type DatedMark,
  type RecurrenceSpine,
  type SnapshotLike,
  type TimeMode,
  type TimelineMark,
  type TimelineRow,
} from "./timeline-model";
import {
  BREAK_PX,
  buildScale,
  domainFromRange,
  extentOf,
  formatSpan,
  formatTick,
  paddedExtent,
  panDomain,
  posToTime,
  ticksForSegment,
  timeToPos,
  zoomDomain,
  type Domain,
  type Gap,
  type Scale,
} from "./timeline-scale";
import {
  intervalSpan,
  packSlots,
  stripGeometry,
  successionOrder,
  unionEnvelope,
  validityLayout,
  type AxisClock,
  type StripBounds,
  type StripGeometry,
  type ValidityChip,
  type ValidityLane,
  type ValidityLayout,
  type ValidityStrip,
} from "./timeline-validity";
import {
  currentPalette,
  currentTheme,
  desaturate,
  semanticPaletteFor,
  stripHue,
  type Theme,
} from "./theme";
import type { AnyEvent, NodeStatusChanged, NodeStored, TimelineStored } from "./types";

const SVG_NS = "http://www.w3.org/2000/svg";

/** Room above and below the axis, so the first and last marks are not flush. */
const AXIS_PADDING = 18;
/** Gap between the axis and the nearest edge of a side label. */
const LABEL_INSET = 26;
const MARK_RADIUS = 4.5;
const INTERVAL_WIDTH = 9;
const LABEL_HEIGHT = 15;
/** Straddling blocks carry their own text, so they need more room. */
const BLOCK_HEIGHT = 22;
/** Rough width of a character at the label font size, for budgeting text. */
const CHAR_WIDTH = 5.6;
/** How far above its tick a label sits, and the plate that backs it. */
const TICK_LABEL_OFFSET = 5;
const TICK_PLATE_HEIGHT = 12;
const TICK_PLATE_PADDING = 3;
/** How many lines a selected mark's card may run to. */
const CARD_LINES = 5;
const CARD_LINE_HEIGHT = 12;
const CARD_PADDING = 5;
/** Selected cards are drawn in the left column when the mark straddles the axis. */
const CARD_SUFFIX = ":card";
/** How wide a derived band is drawn, and the least height it may collapse to. */
const BAND_WIDTH = 15;
const BAND_MIN_HEIGHT = 10;
/**
 * How far an open edge takes to dissolve, and how long a one-sided band runs.
 *
 * §13.1: an unknown endpoint is a bar fading to nothing over roughly 24px, "the
 * edge is somewhere in this fog". A one-sided band is all fog past its one
 * known edge, so the fade is the whole of it.
 */
const FADE_PX = 24;
/**
 * Per-source validity lanes (§13): how far the first sits from the axis, and
 * the pitch from one to the next, which is a strip plus room for its name.
 *
 * Lanes are reused down the axis, so the pitch buys a column rather than a
 * source: a side runs out of room at `STRIP_SLOTS` lanes overlapping in time,
 * and the strips past that are counted rather than stacked into the labels.
 */
const STRIP_INSET = 13;
const STRIP_PITCH = 16;
const STRIP_WIDTH = 5;
const STRIP_SLOTS = 3;
/** Room two strips sharing a lane need, so a gap never reads as one bar. */
const STRIP_SLOT_GAP = 6;
/** Half the width of an endpoint cap, and the witness dot with its halo. */
const CAP_REACH = 4;
const WITNESS_RADIUS = 3;
const WITNESS_HALO = 5.5;
/** The terminal dot on a succession elbow, and how far its curve bows. */
const ELBOW_DOT = 2.5;
const ELBOW_BOW = 14;
/** A bead is a small mark: an occurrence a rule computed, not a point recorded. */
const BEAD_RADIUS = 2.6;
/** How far the first spine sits from the axis, and how far apart two spines are. */
const SPINE_INSET = 22;
const SPINE_GAP = 9;
/** One wheel notch while zooming. Below 1 zooms in. */
const WHEEL_STEP = 0.85;
/** One wheel notch while panning, as a fraction of the visible span. */
const PAN_STEP = 0.12;

/**
 * Mark hues, from the palette the graph panel also draws from (#56).
 *
 * Facts and inferences used to be named here, and differently — the two panels
 * sit side by side and disagreed about what colour a fact is. `kind` is a node
 * type, so anything that is not an inference is drawn as a claim.
 */
export const markColor = (kind: string, theme: Theme): string => {
  const palette = semanticPaletteFor(theme);
  return kind === "inference" ? palette.inference : palette.fact;
};

/**
 * A mark's fill, focus included — the timeline's half of `nodeFill`.
 *
 * Dim only the graph and the two panels disagree about what came back, which
 * is the class of bug #56 fixed for colour (RETRIEVAL_PROVENANCE.md §4.2). Both
 * sides desaturate through `theme.ts`, so there is one dimming rule rather than
 * two that drift.
 */
export const markFillFor = (kind: string, theme: Theme, inFocus: boolean): string =>
  inFocus ? markColor(kind, theme) : desaturate(markColor(kind, theme));

export const selectedMarkColor = (theme: Theme): string =>
  semanticPaletteFor(theme).selection;

export const contestedColor = (theme: Theme): string =>
  semanticPaletteFor(theme).contradiction;

/**
 * The contested glyph: a kink in the axis direction.
 *
 * Red is the contradiction hue in both panels, and a disputed order is a
 * contradiction. The shape is what keeps it from reading as "this date is
 * wrong": the stroke runs along the axis, which is the direction time runs in,
 * and doubles back on itself. What is tangled is the sequence. A cross or a
 * ring over the mark would put the doubt on the date instead, and the date is
 * exactly what a dispute about order leaves standing.
 */
const CONTESTED_PATH = "M 0 -5 L 4 -2 L -4 2 L 0 5";
const CONTESTED_OFFSET = 11;

/** What the glyph says when the pointer rests on it. */
export const contestedDetail = (contradictionId: string | null): string =>
  `order disputed: contradiction ${contradictionId ?? "unnamed"}`;

interface View {
  domain: Domain;
  /** Gaps broken last render, fed back so a zoom drag does not make them flicker. */
  breaks: Gap[];
}

interface PanelState {
  snapshot: SnapshotLike;
  mode: TimeMode;
  rows: TimelineRow[];
  /** Which timeline is on screen. Null means "whichever is first". */
  timelineId: string | null;
  view: Map<string, View>;
  filters: TimelineFilters;
  selectedMarkId: string | null;
}

export interface TimelinePanelControls {
  body: HTMLElement;
  empty: HTMLElement;
  undated: HTMLElement;
  modeSelect: HTMLSelectElement;
  timelineSelect: HTMLSelectElement;
  typeSelect: HTMLSelectElement;
  statusSelect: HTMLSelectElement;
  metacontextSelect: HTMLSelectElement;
  queryInput: HTMLInputElement;
  rangeStart: HTMLInputElement;
  rangeEnd: HTMLInputElement;
  resetButton: HTMLElement;
  nowButton: HTMLElement;
}

export interface TimelinePanelHandle {
  cleanup: () => void;
  clear: () => void;
  loadSnapshot: (snapshot: SnapshotLike) => void;
  /** Re-measure and redraw — call when the panel is shown or resized. */
  refresh: () => void;
  /**
   * Dim every mark this retrieval did not return; `null` leaves focus mode.
   *
   * A mark can stand for several nodes, and it is in focus if any of them came
   * back — the mark says "something here was retrieved", which is the honest
   * reading of a mark that is not a node.
   */
  setFocus: (nodeIds: readonly string[] | null) => void;
}

const svg = <K extends keyof SVGElementTagNameMap>(
  tag: K,
  attrs: Record<string, string | number>,
): SVGElementTagNameMap[K] => {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [name, value] of Object.entries(attrs)) {
    el.setAttribute(name, String(value));
  }
  return el;
};

const truncate = (text: string, max: number): string =>
  text.length > max ? `${text.slice(0, max - 1)}…` : text;

/** A select's value, or null for the "all" sentinel. */
const selected = (select: HTMLSelectElement): ReadonlySet<string> | null =>
  select.value === "all" ? null : new Set([select.value]);

const dateValue = (input: HTMLInputElement): number | null => {
  if (!input.value) return null;
  const t = Date.parse(input.value);
  return Number.isNaN(t) ? null : t;
};

/**
 * The present this timeline *claims*, or null if it claims none.
 *
 * Only content mode can have one: record time is wall-clock (`created_at`), so
 * a fictional anchor would be measuring against the wrong thing entirely.
 *
 * The distinction between "stated" and "resolved" matters for where the view
 * opens. A stated present is a fact about the material — "the novel opens in
 * May 1897" — and is worth scrolling to. The wall clock is not a claim about
 * anything, and centring an 1897 timeline on today would open it on an empty
 * present with every mark off-screen.
 */
export const statedReferenceTime = (
  snapshot: SnapshotLike,
  mode: TimeMode,
  timelineId: string | null,
): number | null => {
  if (mode !== "content") return null;
  const timeline = (snapshot.timelines ?? []).find(
    (t) => t.timeline_id === timelineId,
  );
  const stated = timeline?.reference_time ?? null;
  if (stated === null) return null;
  const parsed = Date.parse(stated);
  return Number.isNaN(parsed) ? null : parsed;
};

/**
 * The instant the "now" rule is drawn at, and where the "now" button goes.
 *
 * Resolved rather than stated: an unset `reference_time` means follow the wall
 * clock, and it is resolved *here* rather than when the snapshot arrived —
 * otherwise a long-lived session would pin the present to whenever the browser
 * happened to connect.
 */
export const referenceTimeFor = (
  snapshot: SnapshotLike,
  mode: TimeMode,
  timelineId: string | null,
  now: number = Date.now(),
): number => statedReferenceTime(snapshot, mode, timelineId) ?? now;

/**
 * Widen an extent so a given instant falls inside it.
 *
 * Without this the reference time cannot be centred on when it lies outside
 * the data — a timeline whose events are all in the past, say — and the view
 * would silently settle at the nearest edge instead.
 */
export const extentIncluding = (extent: Domain, at: number): Domain => ({
  t0: Math.min(extent.t0, at),
  t1: Math.max(extent.t1, at),
});

/** Recentre a domain on an instant, keeping its span, staying inside the extent. */
export const centredOn = (domain: Domain, at: number, extent: Domain): Domain => {
  const span = domain.t1 - domain.t0;
  const latestStart = Math.max(extent.t1 - span, extent.t0);
  const t0 = Math.min(Math.max(at - span / 2, extent.t0), latestStart);
  return { t0, t1: t0 + span };
};

export const initTimelinePanel = (
  router: EventRouter,
  controls: TimelinePanelControls,
  onSelect: (mark: TimelineMark | null) => void,
): TimelinePanelHandle => {
  const state: PanelState = {
    snapshot: { nodes: [], edges: [] },
    mode: "record",
    rows: [],
    timelineId: null,
    view: new Map(),
    filters: { ...NO_FILTERS },
    selectedMarkId: null,
  };

  // --- What is on screen ---

  const currentRow = (): TimelineRow | null =>
    state.rows.find((r) => r.id === state.timelineId) ?? state.rows[0] ?? null;

  const referenceTime = (): number =>
    referenceTimeFor(state.snapshot, state.mode, currentRow()?.id ?? null);

  const statedReference = (): number | null =>
    statedReferenceTime(state.snapshot, state.mode, currentRow()?.id ?? null);

  const extentFor = (row: TimelineRow): Domain | null => {
    const extent = extentOf(row.dated);
    if (extent === null) return null;
    // Only a *stated* present widens the extent. Stretching an 1897 timeline
    // out to today to accommodate the wall clock would bury the data.
    const stated = statedReference();
    return paddedExtent(stated === null ? extent : extentIncluding(extent, stated));
  };

  const viewFor = (row: TimelineRow): View | null => {
    const existing = state.view.get(row.id);
    if (existing) return existing;
    const extent = extentFor(row);
    if (extent === null) return null;
    // Open centred on a stated present — a timeline holding future events has
    // no meaningful edge to start at. With none stated, fit the data instead.
    const stated = statedReference();
    const fresh: View = {
      domain: stated === null ? extent : centredOn(extent, stated, extent),
      breaks: [],
    };
    state.view.set(row.id, fresh);
    return fresh;
  };

  const applyView = (change: (current: Domain, extent: Domain) => Domain): void => {
    const row = currentRow();
    if (row === null) return;
    const extent = extentFor(row);
    const current = viewFor(row);
    if (extent === null || current === null) return;
    state.view.set(row.id, {
      domain: change(current.domain, extent),
      breaks: current.breaks,
    });
    render();
  };

  // --- Focus mode (RETRIEVAL_PROVENANCE.md §4.2) ---
  //
  // Held here rather than in the filters: a filter removes marks, and focus
  // must leave them drawn. *"What did the agent miss?"* is half the question,
  // and a filtered-away mark cannot answer it.

  let focused: ReadonlySet<string> | null = null;

  const markInFocus = (mark: TimelineMark): boolean =>
    focused === null || mark.nodeIds.some((id) => focused!.has(id));

  const setFocus = (nodeIds: readonly string[] | null): void => {
    focused = nodeIds === null ? null : new Set(nodeIds);
    render();
  };

  // --- Filters ---

  const readFilters = (): TimelineFilters => {
    const t0 = dateValue(controls.rangeStart);
    const t1 = dateValue(controls.rangeEnd);
    return {
      nodeTypes: selected(controls.typeSelect),
      statuses: selected(controls.statusSelect),
      metacontexts: selected(controls.metacontextSelect),
      // A half-open range is still a range: the missing end becomes unbounded.
      range:
        t0 === null && t1 === null
          ? null
          : { t0: t0 ?? -Infinity, t1: t1 ?? Infinity },
      query: controls.queryInput.value,
    };
  };

  /**
   * Refill the metacontext select from the data.
   *
   * Node type and status are fixed vocabularies and stay as authored in the
   * markup; metacontexts are open-ended and only the graph knows them.
   */
  const populateMetacontexts = (): void => {
    const present = facetValues(allMarks(state.rows), "mc");
    const previous = controls.metacontextSelect.value;
    controls.metacontextSelect.innerHTML = "";

    const all = document.createElement("option");
    all.value = "all";
    all.textContent = "All metacontexts";
    controls.metacontextSelect.appendChild(all);

    for (const value of present) {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = truncate(value, 28);
      controls.metacontextSelect.appendChild(option);
    }
    // Keep the user's choice across a reload if it still exists.
    controls.metacontextSelect.value = present.includes(previous) ? previous : "all";
  };

  /** One timeline is on screen at a time, so the selector is how you switch. */
  const populateTimelines = (): void => {
    const previous = state.timelineId;
    controls.timelineSelect.innerHTML = "";
    for (const row of state.rows) {
      const option = document.createElement("option");
      option.value = row.id;
      option.textContent = truncate(row.name, 32);
      controls.timelineSelect.appendChild(option);
    }
    state.timelineId = state.rows.some((r) => r.id === previous)
      ? previous
      : (state.rows[0]?.id ?? null);
    if (state.timelineId !== null) controls.timelineSelect.value = state.timelineId;
    // With one timeline there is nothing to choose between.
    controls.timelineSelect.disabled = state.rows.length < 2;
  };

  // --- Rendering ---

  const markFill = (mark: DatedMark): string => {
    const theme = currentTheme();
    return mark.id === state.selectedMarkId
      ? selectedMarkColor(theme)
      : markFillFor(
          mark.side === "right" ? "inference" : "fact",
          theme,
          markInFocus(mark),
        );
  };

  /**
   * Make an element stand for a mark: hover, click, and the detail on hover.
   *
   * `tooltip` is off where the element's own text is the thing being read. A
   * `<title>` is a child node, so it lands inside `textContent` and would make
   * a label that has to be verbatim read as the label plus its own tooltip.
   */
  const bindMark = (
    element: SVGElement,
    mark: TimelineMark,
    { tooltip = true }: { tooltip?: boolean } = {},
  ): void => {
    element.setAttribute("class", "cursor-pointer");
    element.addEventListener("mouseenter", () => onSelect(mark));
    element.addEventListener("click", (e) => {
      e.stopPropagation();
      state.selectedMarkId = state.selectedMarkId === mark.id ? null : mark.id;
      onSelect(state.selectedMarkId === null ? null : mark);
      render();
    });
    if (!tooltip) return;
    const title = svg("title", {});
    title.textContent = mark.detail;
    element.appendChild(title);
  };

  /**
   * The fills a band needs: 45° hatching per side, and the two fade masks.
   *
   * Hatching is the §13.1 mark for "resolved vague label", a span the graph
   * derived rather than a date a source gave, and it is per side because a
   * pattern's colour is fixed where it is defined, not where it is used. The
   * masks are how an unknown endpoint dissolves: white is kept, transparent is
   * dropped, so the bar loses weight toward the open side instead of stopping
   * at an edge nobody asserted.
   */
  const renderDefs = (parent: SVGSVGElement): void => {
    const theme = currentTheme();
    const defs = svg("defs", {});

    for (const kind of ["fact", "inference"] as const) {
      const pattern = svg("pattern", {
        id: `timeline-hatch-${kind}`,
        width: 5,
        height: 5,
        patternUnits: "userSpaceOnUse",
        patternTransform: "rotate(45)",
      });
      pattern.appendChild(
        svg("path", {
          d: "M 0 0 L 0 5",
          stroke: markColor(kind, theme),
          "stroke-width": 2,
          "stroke-opacity": 0.75,
        }),
      );
      defs.appendChild(pattern);
    }

    // One hatch per lane, in that lane's hue: a soft edge is a date resolved
    // from the source's own words, and it stays soft (§13.1).
    for (let slot = 0; slot < STRIP_SLOTS; slot++) {
      const pattern = svg("pattern", {
        id: `timeline-strip-hatch-${slot}`,
        width: 4,
        height: 4,
        patternUnits: "userSpaceOnUse",
        patternTransform: "rotate(45)",
      });
      pattern.appendChild(
        svg("path", {
          d: "M 0 0 L 0 4",
          stroke: stripHue(theme, slot),
          "stroke-width": 1.6,
          "stroke-opacity": 0.75,
        }),
      );
      defs.appendChild(pattern);
    }

    for (const [name, from, to] of [
      ["timeline-fade-later", 1, 0],
      ["timeline-fade-earlier", 0, 1],
    ] as const) {
      const gradient = svg("linearGradient", {
        id: `${name}-gradient`,
        x1: 0,
        y1: 0,
        x2: 0,
        y2: 1,
      });
      for (const [offset, opacity] of [
        [0, from],
        [1, to],
      ] as const) {
        gradient.appendChild(
          svg("stop", {
            offset,
            "stop-color": "#ffffff",
            "stop-opacity": opacity,
          }),
        );
      }
      defs.appendChild(gradient);

      const mask = svg("mask", { id: name, maskContentUnits: "objectBoundingBox" });
      mask.appendChild(
        svg("rect", { x: 0, y: 0, width: 1, height: 1, fill: `url(#${name}-gradient)` }),
      );
      defs.appendChild(mask);
    }

    parent.appendChild(defs);
  };

  /**
   * A point the order placed: a hatched band from `earliest` to `latest`.
   *
   * With one bound known the band runs a fixed distance into the unknown and
   * dissolves, which is §13.1's unknown endpoint: "after the fire, we do not
   * know when" is fog below a known edge, not a bar ending somewhere.
   *
   * The label goes on the band verbatim (§13.2's rule 3): resolving a vague
   * label adds a position, it never replaces the words.
   */
  const renderBand = (
    group: SVGGElement,
    scale: Scale,
    mark: DatedMark,
    band: NonNullable<DatedMark["band"]>,
    axisX: number,
  ): void => {
    const palette = currentPalette();
    const kind = mark.side === "right" ? "inference" : "fact";
    const isSelected = mark.id === state.selectedMarkId;

    const known =
      band.earliest !== null ? timeToPos(scale, band.earliest) : timeToPos(scale, band.latest!);
    const top = band.earliest !== null ? known : known - FADE_PX;
    const bottom = band.latest !== null ? timeToPos(scale, band.latest) : known + FADE_PX;
    const height = Math.max(BAND_MIN_HEIGHT, bottom - top);

    const shape = svg("rect", {
      x: axisX - BAND_WIDTH / 2,
      y: top,
      width: BAND_WIDTH,
      height,
      fill: `url(#timeline-hatch-${kind})`,
      "fill-opacity": markInFocus(mark) ? 1 : 0.35,
      ...(isSelected
        ? { stroke: selectedMarkColor(currentTheme()), "stroke-width": 1 }
        : {}),
    });
    if (band.earliest === null) shape.setAttribute("mask", "url(#timeline-fade-earlier)");
    if (band.latest === null) shape.setAttribute("mask", "url(#timeline-fade-later)");
    bindMark(shape, mark);
    shape.setAttribute("class", "timeline-band cursor-pointer");
    group.appendChild(shape);

    const isLeft = mark.side !== "right";
    const label = svg("text", {
      class: "timeline-band-label cursor-pointer",
      x: isLeft ? axisX - BAND_WIDTH : axisX + BAND_WIDTH,
      y: top + height / 2 + 3.5,
      fill: palette.nodeLabel,
      "font-size": 10,
      "text-anchor": isLeft ? "end" : "start",
    });
    // Room is what the column has; the words are what the source wrote, and
    // truncating them here would leave the panel showing a label no document
    // contains. A label too long for the column runs off it.
    label.textContent = mark.title;
    bindMark(label, mark, { tooltip: false });
    label.setAttribute("class", "timeline-band-label cursor-pointer");
    group.appendChild(label);
  };

  /** The glyph itself, wherever it is drawn: on the axis or inside a chip. */
  const contestedGlyph = (mark: TimelineMark, x: number, y: number): SVGPathElement => {
    const glyph = svg("path", {
      class: "timeline-contested",
      d: CONTESTED_PATH,
      transform: `translate(${x}, ${y})`,
      fill: "none",
      stroke: contestedColor(currentTheme()),
      "stroke-width": 1.6,
      "stroke-linecap": "round",
      "stroke-linejoin": "round",
    });
    const title = svg("title", {});
    title.textContent = contestedDetail(mark.contradictionId);
    glyph.appendChild(title);
    return glyph;
  };

  /**
   * The dispute mark beside a mark on the axis.
   *
   * Beside, never instead: the point keeps whatever place it had, because a
   * dispute about order is not a reason to move a date or to take a derived
   * band away that the server already withheld.
   */
  const renderContested = (
    group: SVGGElement,
    scale: Scale,
    mark: DatedMark,
    axisX: number,
  ): void => {
    if (!mark.contested) return;
    const side = mark.side === "right" ? 1 : -1;
    group.appendChild(
      contestedGlyph(mark, axisX + side * CONTESTED_OFFSET, timeToPos(scale, mark.start)),
    );
  };

  const renderMark = (
    group: SVGGElement,
    scale: Scale,
    mark: DatedMark,
    axisX: number,
  ): void => {
    renderContested(group, scale, mark, axisX);
    if (mark.band !== null) {
      renderBand(group, scale, mark, mark.band, axisX);
      return;
    }
    const y = timeToPos(scale, mark.start);
    const isSelected = mark.id === state.selectedMarkId;
    const fill = markFill(mark);

    if (mark.side === "axis") {
      // Straddles the line, because the timepoint holds both what we were told
      // and what was derived from it. Splitting it would invent a second mark.
      const span = mark.end === null ? 0 : timeToPos(scale, mark.end) - y;
      const height = Math.max(BLOCK_HEIGHT, span);
      const block = svg("rect", {
        x: axisX - INTERVAL_WIDTH * 1.6,
        y: y - BLOCK_HEIGHT / 2,
        width: INTERVAL_WIDTH * 3.2,
        height,
        rx: 3,
        fill,
        "fill-opacity": isSelected ? 0.95 : 0.7,
      });
      bindMark(block, mark);
      group.appendChild(block);
      return;
    }

    const shape =
      mark.end !== null
        ? svg("rect", {
            x: axisX - INTERVAL_WIDTH / 2,
            y,
            width: INTERVAL_WIDTH,
            height: Math.max(2, timeToPos(scale, mark.end) - y),
            rx: 2,
            fill,
            "fill-opacity": isSelected ? 0.95 : 0.65,
          })
        : svg("circle", {
            cx: axisX,
            cy: y,
            r: isSelected ? MARK_RADIUS + 1.5 : MARK_RADIUS,
            fill,
            "fill-opacity": isSelected ? 1 : 0.85,
          });
    bindMark(shape, mark);
    group.appendChild(shape);
  };

  // --- Valid time: what each source says about when (§13) ---

  /** One period one source asserts, placed. */
  interface PlacedStrip {
    strip: ValidityStrip;
    geometry: StripGeometry;
  }

  /** One source's lane, with the column it was given and everything in it. */
  interface PlacedLane {
    lane: ValidityLane;
    slot: number;
    strips: PlacedStrip[];
    span: StripBounds;
  }

  interface PlacedValidity {
    lanes: PlacedLane[];
    chips: ValidityChip[];
    /** Strips there was no column left for. */
    hidden: number;
    /** Columns each side is using, which is how far its labels have to move. */
    slots: { left: number; right: number };
  }

  /** A lane sits on its mark's side; a straddling mark's lanes go left. */
  const columnFor = (side: TimelineMark["side"]): "left" | "right" =>
    side === "right" ? "right" : "left";

  /**
   * Where every source's periods go, in columns beside the axis.
   *
   * **Content mode only.** These are periods the world was in, and the record
   * axis measures when the graph was told things: a 1924 interval plotted
   * against `created_at` would be a date on the wrong clock entirely.
   *
   * A claim we were wrong about is left out unless the status filter asks for
   * it by name, which is this panel's version of `include_corrected` (§13.2
   * rule 5).
   */
  /**
   * The clock the axis keeps: this timeline, and whether it has a present.
   *
   * The raw field rather than the resolved instant, because what matters here
   * is whether the timeline states one at all. A stated present makes it a
   * clock of its own; without one it follows the wall clock, which is the
   * timeline real-world periods are measured against.
   */
  const axisClock = (): AxisClock => {
    const timelineId = currentRow()?.id ?? null;
    const timeline = (state.snapshot.timelines ?? []).find(
      (t) => t.timeline_id === timelineId,
    );
    return { timelineId, referenceTime: timeline?.reference_time ?? null };
  };

  const layoutValidity = (marks: readonly DatedMark[]): ValidityLayout => {
    if (state.mode !== "content") return { lanes: [], chips: [] };
    return validityLayout(
      state.snapshot,
      marks,
      axisClock(),
      // The panel's `include_corrected`: naming the status is how a mistake is
      // summoned, and nothing else brings it back.
      state.filters.statuses?.has("corrected") ?? false,
    );
  };

  const placeValidity = (
    { lanes, chips }: ValidityLayout,
    scale: Scale,
    height: number,
  ): PlacedValidity => {
    const bounds = { top: 0, bottom: height };
    const placed = lanes.flatMap((lane) => {
      const strips = lane.strips.flatMap((strip) => {
        const span = intervalSpan(strip.interval);
        // Off-screen periods take no column: `timeToPos` pins anything outside
        // the domain to an edge, so keeping them would pile flat strips there.
        if (span === null || span.to < scale.domain.t0 || span.from > scale.domain.t1) {
          return [];
        }
        const geometry = stripGeometry(
          strip.interval,
          (at) => timeToPos(scale, at),
          bounds,
          FADE_PX,
        );
        return geometry === null ? [] : [{ strip, geometry }];
      });
      const span = unionEnvelope(strips.map((s) => s.geometry));
      return span === null ? [] : [{ lane, strips, span }];
    });

    const slots = { left: 0, right: 0 };
    const drawn: PlacedLane[] = [];
    let hidden = 0;
    for (const column of ["left", "right"] as const) {
      const here = placed
        .filter((p) => columnFor(p.lane.side) === column)
        .sort((a, b) => a.span.top - b.span.top);
      const assigned = packSlots(
        here.map((p) => p.span),
        STRIP_SLOT_GAP,
      );
      here.forEach((p, index) => {
        const slot = assigned[index];
        if (slot >= STRIP_SLOTS) {
          hidden += p.strips.length;
          return;
        }
        slots[column] = Math.max(slots[column], slot + 1);
        drawn.push({ ...p, slot });
      });
    }
    return { lanes: drawn, chips, hidden, slots };
  };

  const laneX = (placed: PlacedLane, axisX: number): number =>
    axisX +
    (columnFor(placed.lane.side) === "right" ? 1 : -1) *
      (STRIP_INSET + placed.slot * STRIP_PITCH);

  /** Hover and click reach the mark; the tooltip carries the interval itself. */
  const bindStrip = (
    element: SVGElement,
    className: string,
    detail: string,
    markId: string,
  ): void => {
    element.setAttribute("class", `${className} cursor-pointer`);
    const title = svg("title", {});
    title.textContent = detail;
    element.appendChild(title);
    const mark = currentRow()?.dated.find((m) => m.id === markId);
    if (mark === undefined) return;
    element.addEventListener("mouseenter", () => onSelect(mark));
    element.addEventListener("click", (e) => {
      e.stopPropagation();
      state.selectedMarkId = state.selectedMarkId === mark.id ? null : mark.id;
      onSelect(state.selectedMarkId === null ? null : mark);
      render();
    });
  };

  /**
   * One period, with a mark per endpoint kind.
   *
   * Solid for `stated` and a soft tint under a dashed outline for `inferred`,
   * so squinting performs the stated-only filter the API offers. A stated date
   * gets a crisp cap; an unknown edge dissolves over `FADE_PX`, past the
   * now-line if that is where it falls, because stopping at the rule would
   * assert an endpoint nobody stated; an unbounded edge keeps full weight and
   * leaves the panel; a date resolved from the source's words keeps a hatched,
   * soft edge.
   */
  const renderStrip = (
    group: SVGGElement,
    placed: PlacedLane,
    { strip, geometry }: PlacedStrip,
    x: number,
    height: number,
  ): void => {
    // Focus owns saturation and status owns opacity (RETRIEVAL_PROVENANCE.md
    // §4.1), so a strip the retrieval missed and a strip that is history stay
    // distinguishable from each other.
    const lit = focused === null || focused.has(placed.lane.nodeId);
    const plain = stripHue(currentTheme(), placed.slot);
    const hue = lit ? plain : desaturate(plain);
    const muted = placed.lane.status === "historical";
    const inferred = strip.interval.basis === "inferred";
    const alpha = muted ? 0.45 : 1;
    const left = x - STRIP_WIDTH / 2;
    const bodyHeight = Math.max(1, geometry.bodyBottom - geometry.bodyTop);

    const body = svg("rect", {
      x: left,
      y: geometry.bodyTop,
      width: STRIP_WIDTH,
      height: bodyHeight,
      rx: 1.5,
      fill: hue,
      "fill-opacity": (inferred ? 0.2 : 0.85) * alpha,
      ...(inferred
        ? {
            stroke: hue,
            "stroke-width": 1,
            "stroke-dasharray": "3 2",
            "stroke-opacity": 0.9 * alpha,
          }
        : {}),
    });
    bindStrip(
      body,
      `timeline-strip${inferred ? " timeline-strip-inferred" : ""}${
        muted ? " timeline-strip-historical" : ""
      }`,
      strip.detail,
      placed.lane.markId,
    );
    group.appendChild(body);

    for (const [endpoint, y, direction] of [
      [geometry.startMark, geometry.bodyTop, -1],
      [geometry.endMark, geometry.bodyBottom, 1],
    ] as const) {
      if (endpoint === "cap") {
        group.appendChild(
          svg("line", {
            class: "timeline-strip-cap",
            x1: x - CAP_REACH,
            y1: y,
            x2: x + CAP_REACH,
            y2: y,
            stroke: hue,
            "stroke-width": 2,
            "stroke-opacity": alpha,
          }),
        );
      }
      if (endpoint === "soft") {
        const reach = Math.min(FADE_PX, bodyHeight);
        group.appendChild(
          svg("rect", {
            class: "timeline-strip-soft",
            x: left,
            y: direction === -1 ? y : y - reach,
            width: STRIP_WIDTH,
            height: reach,
            fill: `url(#timeline-strip-hatch-${placed.slot})`,
            "fill-opacity": alpha,
          }),
        );
      }
      if (endpoint === "fade") {
        const reach = direction === -1 ? geometry.fadeAbove : geometry.fadeBelow;
        group.appendChild(
          svg("rect", {
            class: "timeline-strip-fade",
            x: left,
            y: direction === -1 ? y - reach : y,
            width: STRIP_WIDTH,
            height: reach,
            fill: hue,
            "fill-opacity": alpha,
            mask: `url(#timeline-fade-${direction === -1 ? "earlier" : "later"})`,
          }),
        );
      }
      if (endpoint === "exit") {
        // The bar already runs to the edge; the head says it keeps going.
        const rim = direction === -1 ? 0 : height;
        const tip = rim + direction * 5;
        group.appendChild(
          svg("path", {
            class: "timeline-strip-exit",
            d: `M ${left - 2} ${rim} L ${left + STRIP_WIDTH + 2} ${rim} L ${x} ${tip} Z`,
            fill: hue,
            "fill-opacity": alpha,
          }),
        );
      }
    }

    if (geometry.witnessAt !== null) {
      // The one moment a source actually stood behind, so the one part of the
      // mark drawn at full confidence.
      group.appendChild(
        svg("circle", {
          class: "timeline-witness-halo",
          cx: x,
          cy: geometry.witnessAt,
          r: WITNESS_HALO,
          fill: "none",
          stroke: hue,
          "stroke-width": 2,
          "stroke-opacity": 0.35 * alpha,
        }),
      );
      const dot = svg("circle", {
        class: "timeline-witness",
        cx: x,
        cy: geometry.witnessAt,
        r: WITNESS_RADIUS,
        fill: hue,
        "fill-opacity": alpha,
      });
      bindStrip(dot, "timeline-witness", strip.detail, placed.lane.markId);
      group.appendChild(dot);
    }

    if (placed.lane.status === "corrected") {
      // Summoned rather than believed: struck through, the way the retrieval
      // surface shows what we thought and had to take back.
      group.appendChild(
        svg("line", {
          class: "timeline-strip-struck",
          x1: x,
          y1: geometry.bodyTop,
          x2: x,
          y2: geometry.bodyBottom,
          stroke: currentPalette().surfaceChrome,
          "stroke-width": 1.5,
        }),
      );
    }
  };

  /**
   * One source's lane: its periods, the hairline between them, and its name.
   *
   * The hairline is the only thing drawn between two periods. Outside a stated
   * interval is *no assertion* (§13.2 rule 1), so a shaded or coloured gap
   * would draw a claim nobody made; a dotted spine says "same claim, nothing
   * asserted here" and no more.
   *
   * The name is rotated to read outward from the axis, which is where a lane
   * has room. Direct labelling is what lets a strip's hue mean nothing (§13.3).
   */
  const renderLane = (
    group: SVGGElement,
    placed: PlacedLane,
    axisX: number,
    height: number,
  ): void => {
    const palette = currentPalette();
    const x = laneX(placed, axisX);

    if (placed.strips.length > 1) {
      group.appendChild(
        svg("line", {
          class: "timeline-claim-spine",
          x1: x,
          y1: placed.span.top,
          x2: x,
          y2: placed.span.bottom,
          stroke: palette.tick,
          "stroke-width": 1,
          "stroke-dasharray": "1 3",
        }),
      );
    }

    for (const strip of placed.strips) renderStrip(group, placed, strip, x, height);

    const reach = placed.span.bottom - placed.span.top;
    if (reach < 20) return;
    const outward = columnFor(placed.lane.side) === "right" ? 1 : -1;
    const label = svg("text", {
      class: "timeline-strip-label",
      transform: `translate(${x + outward * (STRIP_WIDTH / 2 + 3)}, ${
        outward === 1 ? placed.span.top : placed.span.bottom
      }) rotate(${outward * 90})`,
      fill: palette.nodeLabel,
      "font-size": 9,
      "text-anchor": "start",
    });
    label.textContent = truncate(placed.lane.sourceLabel, Math.floor(reach / CHAR_WIDTH));
    group.appendChild(label);
  };

  /**
   * The summary the selected fact's sources add up to, as an outline.
   *
   * Hollow and dashed, never filled (§13.2 rule 4): "any source asserts" is a
   * reading made here, and a solid bar would show it as a period somebody
   * stated. Drawn only for a selected fact with more than one source, where
   * there is a disagreement to summarise.
   */
  const renderEnvelope = (
    group: SVGGElement,
    lanes: readonly PlacedLane[],
    axisX: number,
  ): void => {
    const byNode = new Map<string, PlacedLane[]>();
    for (const placed of lanes) {
      if (placed.lane.markId !== state.selectedMarkId) continue;
      const bucket = byNode.get(placed.lane.nodeId);
      if (bucket) bucket.push(placed);
      else byNode.set(placed.lane.nodeId, [placed]);
    }

    for (const stack of byNode.values()) {
      if (stack.length < 2) continue;
      const span = unionEnvelope(stack.map((p) => p.span))!;
      const xs = stack.map((p) => laneX(p, axisX));
      const left = Math.min(...xs) - STRIP_WIDTH;
      const right = Math.max(...xs) + STRIP_WIDTH;
      const outline = svg("rect", {
        class: "timeline-envelope",
        x: left,
        y: span.top - 3,
        width: right - left,
        height: span.bottom - span.top + 6,
        rx: 3,
        fill: "none",
        stroke: currentPalette().tick,
        "stroke-width": 1.2,
        "stroke-dasharray": "1 3",
      });
      const title = svg("title", {});
      title.textContent = "any source asserts";
      outline.appendChild(title);
      group.appendChild(outline);
    }
  };

  /**
   * `temporally_followed_by`, as an elbow from one claim's end to the next's
   * start.
   *
   * Order, not replacement, so it is a thin connector rather than anything that
   * reads as a correction. Recurrence makes a cycle legal for this edge, and
   * `successionOrder` draws each step once, so a chain that returns to its own
   * lane is drawn calmly instead of hanging the renderer.
   */
  const renderSuccession = (
    group: SVGGElement,
    lanes: readonly PlacedLane[],
    axisX: number,
  ): void => {
    const extent = new Map<string, { x: number; top: number; bottom: number }>();
    for (const placed of lanes) {
      const x = laneX(placed, axisX);
      const known = extent.get(placed.lane.nodeId);
      extent.set(placed.lane.nodeId, {
        // The lane nearest the axis speaks for the claim, so a fact with
        // several sources still gets one connector rather than one per source.
        x: known === undefined ? x : Math.abs(x - axisX) < Math.abs(known.x - axisX) ? x : known.x,
        top: Math.min(known?.top ?? Infinity, placed.span.top),
        bottom: Math.max(known?.bottom ?? -Infinity, placed.span.bottom),
      });
    }

    const palette = currentPalette();
    for (const step of successionOrder(state.snapshot.edges, new Set(extent.keys()))) {
      const from = extent.get(step.from)!;
      const to = extent.get(step.to)!;
      group.appendChild(
        svg("path", {
          class: "timeline-succession",
          d:
            `M ${from.x} ${from.bottom} ` +
            `C ${from.x} ${from.bottom + ELBOW_BOW}, ${to.x} ${to.top - ELBOW_BOW}, ` +
            `${to.x} ${to.top}`,
          fill: "none",
          stroke: palette.tick,
          "stroke-width": 1.2,
        }),
      );
      group.appendChild(
        svg("circle", {
          class: "timeline-succession-dot",
          cx: to.x,
          cy: to.top,
          r: ELBOW_DOT,
          fill: palette.tick,
        }),
      );
    }
  };

  /**
   * One rule's occurrences: beads on a hairline dotted spine.
   *
   * The spine means "same rule, nothing asserted in between" (§13.1), which is
   * why it is dotted rather than drawn: an occurrence is what the rule
   * produces, and between two of them the graph has said nothing at all.
   *
   * A materialised occurrence gets no bead. Materialising turns it into an
   * ordinary timepoint, and that point is already on the axis as a full mark,
   * so a bead as well would draw one date twice.
   *
   * Spines sit in lanes left of the axis, one lane per rule. Left is where what
   * the graph was told goes, and a rule is a source saying a thing recurs.
   */
  const renderSpine = (
    group: SVGGElement,
    scale: Scale,
    spine: RecurrenceSpine,
    lane: number,
    axisX: number,
    inset: number,
  ): void => {
    const palette = currentPalette();
    const theme = currentTheme();
    const x = axisX - inset - lane * SPINE_GAP;
    const inside = spine.occurrences.filter(
      (occurrence) =>
        occurrence.at >= scale.domain.t0 && occurrence.at <= scale.domain.t1,
    );
    if (inside.length === 0) return;

    const positions = inside.map((occurrence) => timeToPos(scale, occurrence.at));
    group.appendChild(
      svg("line", {
        class: "timeline-spine",
        x1: x,
        y1: Math.min(...positions),
        x2: x,
        y2: Math.max(...positions),
        stroke: palette.tick,
        "stroke-width": 1,
        "stroke-dasharray": "1 3",
      }),
    );

    inside.forEach((occurrence, index) => {
      if (occurrence.materialisedId !== null) return;
      const bead = svg("circle", {
        class: "timeline-bead",
        cx: x,
        cy: positions[index],
        r: BEAD_RADIUS,
        fill: markColor("fact", theme),
        "fill-opacity": 0.8,
      });
      const title = svg("title", {});
      title.textContent = [
        spine.label,
        occurrence.moved
          ? `moved to ${new Date(occurrence.at).toISOString()}`
          : new Date(occurrence.at).toISOString(),
      ].join("\n");
      bead.appendChild(title);
      group.appendChild(bead);
    });
  };

  /**
   * The axis line, its ticks and its breaks.
   *
   * Returns the tick labels in a group of their own rather than appending them,
   * because SVG has no z-index and every mark is drawn in this same column: a
   * label appended here is painted over by the first mark that shares its time.
   * The caller appends the returned group last. Each label carries a plate for
   * the same reason the break marker does — it has to overwrite the marks it
   * lands on to stay readable.
   */
  const renderAxis = (
    group: SVGGElement,
    scale: Scale,
    axisX: number,
    height: number,
  ): SVGGElement => {
    const palette = currentPalette();
    group.appendChild(
      svg("line", {
        x1: axisX,
        y1: 0,
        x2: axisX,
        y2: height,
        stroke: palette.axis,
        "stroke-width": 1,
      }),
    );

    const visibleSpan = scale.domain.t1 - scale.domain.t0;
    const tickLabels = svg("g", {});
    for (const segment of scale.segments) {
      for (const tick of ticksForSegment(segment)) {
        const y = timeToPos(scale, tick);
        group.appendChild(
          svg("line", {
            x1: axisX - 4,
            y1: y,
            x2: axisX + 4,
            y2: y,
            stroke: palette.tick,
            "stroke-width": 1,
          }),
        );
        const text = formatTick(tick, visibleSpan);
        // Sized off the same character budget the side labels use, and kept as
        // tight as legibility allows: the plate is opaque, so every pixel of it
        // is a mark the reader cannot see.
        const plateWidth = text.length * CHAR_WIDTH + TICK_PLATE_PADDING * 2;
        tickLabels.appendChild(
          svg("rect", {
            class: "tick-plate",
            x: axisX - plateWidth / 2,
            y: y - TICK_LABEL_OFFSET - TICK_PLATE_HEIGHT + 3,
            width: plateWidth,
            height: TICK_PLATE_HEIGHT,
            // Fully round ends at this height, so it reads as a pill rather
            // than a box cut out of the column.
            rx: TICK_PLATE_HEIGHT / 2,
            fill: palette.surfaceChrome,
          }),
        );
        const label = svg("text", {
          class: "tick-label",
          x: axisX,
          y: y - TICK_LABEL_OFFSET,
          fill: palette.tickLabel,
          "font-size": 9,
          "text-anchor": "middle",
        });
        label.textContent = text;
        tickLabels.appendChild(label);
      }
    }

    for (const brk of scale.breaks) {
      group.appendChild(
        svg("rect", {
          x: axisX - 10,
          y: brk.p0,
          width: 20,
          height: BREAK_PX,
          fill: palette.surfaceChrome,
        }),
      );
      // Two slashes, the conventional mark for a collapsed axis.
      for (const offset of [7, 13]) {
        group.appendChild(
          svg("line", {
            x1: axisX - 7,
            y1: brk.p0 + offset + 3,
            x2: axisX + 7,
            y2: brk.p0 + offset - 3,
            stroke: palette.breakSlash,
            "stroke-width": 1.5,
          }),
        );
      }
      const label = svg("text", {
        x: axisX + 14,
        y: brk.p0 + BREAK_PX / 2 + 3,
        fill: palette.breakLabel,
        "font-size": 9,
      });
      label.textContent = formatSpan(brk.gap.t1 - brk.gap.t0);
      group.appendChild(label);
    }

    return tickLabels;
  };

  /**
   * The reference-time rule: a labelled line across the axis.
   *
   * Only drawn when it is actually in view. Clamping it to an edge so it is
   * always visible would assert that the present is somewhere it is not; the
   * "now" button is how you get back to it.
   */
  const renderReferenceRule = (
    group: SVGGElement,
    scale: Scale,
    width: number,
    at: number,
  ): void => {
    if (at < scale.domain.t0 || at > scale.domain.t1) return;
    const y = timeToPos(scale, at);
    // Neutral, not amber: the rule is chrome — an annotation on the axis — and
    // a semantic hue here competes with the things that are data (#56).
    const palette = currentPalette();
    group.appendChild(
      svg("line", {
        x1: 0,
        y1: y,
        x2: width,
        y2: y,
        stroke: palette.referenceLine,
        "stroke-width": 1,
        "stroke-dasharray": "4 3",
        "stroke-opacity": 0.8,
      }),
    );
    const label = svg("text", {
      x: width - 4,
      y: y - 3,
      fill: palette.referenceLabel,
      "font-size": 9,
      "text-anchor": "end",
    });
    label.textContent = "now";
    group.appendChild(label);
  };

  /**
   * How far a column's text starts from the axis.
   *
   * Source lanes take the gutter first, so the text steps out past however many
   * of them the side is using. With no validity to draw, nothing moves.
   */
  type Insets = { left: number; right: number };

  const insetsFor = (slots: Insets): Insets => ({
    left: LABEL_INSET + slots.left * STRIP_PITCH,
    right: LABEL_INSET + slots.right * STRIP_PITCH,
  });

  /** Pixels available for a label in the given column, measured from its edge. */
  const roomFor = (
    column: "left" | "right",
    axisX: number,
    width: number,
    inset: number,
  ): number => (column === "left" ? axisX - inset : width - axisX - inset - 4);

  /** Characters that fit in one line of a label in the given column. */
  const charBudget = (
    column: "left" | "right",
    axisX: number,
    width: number,
    inset: number,
  ): number =>
    // The card's padding comes out of the text budget, so a full-width line
    // plus its border still fits inside the panel.
    Math.max(
      8,
      Math.floor((roomFor(column, axisX, width, inset) - CARD_PADDING * 2) / CHAR_WIDTH),
    );

  /**
   * Place and draw the side text. Returns how many labels there was no room for.
   *
   * The selected mark's text expands in place: it asks the layout for a taller
   * box holding several wrapped lines, and the layout slides its *neighbours'
   * labels* out of the way. No mark moves. That distinction is the whole reason
   * the expansion happens here rather than on the axis — position on the axis
   * means time, so making room there would put marks where their timestamps do
   * not, and quietly move "now" relative to them.
   *
   * The expanded card is passed first, which makes it the highest priority
   * (`timeline-labels` treats the caller's order as priority) — so the one
   * label the reader deliberately asked for is never the one dropped.
   */
  const renderLabels = (
    group: SVGGElement,
    scale: Scale,
    marks: readonly DatedMark[],
    axisX: number,
    width: number,
    height: number,
    insets: Insets,
  ): number => {
    const palette = currentPalette();
    const byRequest = new Map<string, DatedMark>();
    const cards = new Map<string, string[]>();
    const expanded: LabelRequest[] = [];
    const plain: LabelRequest[] = [];

    for (const mark of marks) {
      // A band carries its label on itself, verbatim. A second, truncated copy
      // in the side column would be the same point twice, said differently.
      if (mark.band !== null) continue;
      const anchor = timeToPos(scale, mark.start);
      const isSelected = mark.id === state.selectedMarkId;

      if (mark.side === "axis") {
        // The block itself is immovable and carries no side text of its own.
        plain.push({ id: mark.id, anchor, height: BLOCK_HEIGHT, column: "axis" });
        if (!isSelected) continue;
      }

      // A straddling mark's card goes in the left column; it needs a distinct
      // id so the block and the card are two requests, not one.
      const column = mark.side === "axis" ? "left" : mark.side;
      const id = mark.side === "axis" ? `${mark.id}${CARD_SUFFIX}` : mark.id;
      byRequest.set(id, mark);

      if (!isSelected) {
        plain.push({ id, anchor, height: LABEL_HEIGHT, column });
        continue;
      }
      const lines = wrapText(
        mark.detail,
        charBudget(column, axisX, width, insets[column]),
        CARD_LINES,
      );
      cards.set(id, lines);
      expanded.push({
        id,
        anchor,
        height: lines.length * CARD_LINE_HEIGHT + CARD_PADDING * 2,
        column,
      });
    }

    const { placed, dropped } = layoutLabels(
      [...expanded, ...plain],
      { top: 0, bottom: height },
      LABEL_GAP,
    );

    for (const label of placed) {
      const mark = byRequest.get(label.id);
      if (mark === undefined || label.column === "axis") continue;

      const isLeft = label.column === "left";
      const inset = insets[label.column];
      const labelX = isLeft ? axisX - inset : axisX + inset;

      const leader = leaderPoints(label, { axisX, labelX });
      if (leader.length > 0) {
        group.appendChild(
          svg("polyline", {
            points: leader.map((p) => `${p.x},${p.y}`).join(" "),
            fill: "none",
            stroke: palette.tick,
            "stroke-width": 1,
            "stroke-opacity": 0.6,
          }),
        );
      }

      const lines = cards.get(label.id);
      if (lines !== undefined) {
        renderCard(
          group,
          label,
          mark,
          lines,
          labelX,
          isLeft,
          roomFor(label.column, axisX, width, inset),
        );
        continue;
      }

      const text = svg("text", {
        x: labelX,
        y: labelCentre(label) + 3.5,
        fill: palette.nodeLabel,
        "font-size": 10,
        "text-anchor": isLeft ? "end" : "start",
      });
      text.textContent = truncate(
        mark.title,
        charBudget(label.column, axisX, width, inset),
      );
      bindMark(text, mark);
      group.appendChild(text);
    }
    return dropped.length;
  };

  /** The expanded panel for the selected mark: its dates and text, in place. */
  const renderCard = (
    group: SVGGElement,
    label: PlacedLabel,
    mark: DatedMark,
    lines: readonly string[],
    labelX: number,
    isLeft: boolean,
    room: number,
  ): void => {
    const palette = currentPalette();
    const longest = lines.reduce((n, line) => Math.max(n, line.length), 0);
    const cardWidth = Math.min(room, longest * CHAR_WIDTH + CARD_PADDING * 2);

    const card = svg("rect", {
      x: isLeft ? labelX - cardWidth : labelX,
      y: label.top,
      width: cardWidth,
      height: label.height,
      rx: 3,
      fill: palette.surfaceChrome,
      stroke: selectedMarkColor(currentTheme()),
      "stroke-width": 1,
      "stroke-opacity": 0.7,
    });
    group.appendChild(card);

    const text = svg("text", {
      x: isLeft ? labelX - CARD_PADDING : labelX + CARD_PADDING,
      y: label.top + CARD_PADDING + CARD_LINE_HEIGHT - 3,
      fill: palette.nodeLabel,
      "font-size": 10,
      "text-anchor": isLeft ? "end" : "start",
    });
    lines.forEach((line, index) => {
      const span = svg("tspan", {
        x: isLeft ? labelX - CARD_PADDING : labelX + CARD_PADDING,
        dy: index === 0 ? 0 : CARD_LINE_HEIGHT,
      });
      span.textContent = line;
      text.appendChild(span);
    });
    bindMark(text, mark);
    group.appendChild(text);
  };

  /**
   * Wheel pans; ⌘/ctrl-wheel zooms. Drag pans, shift-drag zooms to a range.
   *
   * Wheel-to-pan is what "scrolling" means once the axis is vertical, and it
   * costs the wheel-to-zoom binding the horizontal panel had. The viewport
   * stays virtual rather than becoming a tall scrolling SVG: breaks are
   * recomputed from the visible domain and zoom is a domain transform, so a
   * native scrollbar would have to be reconciled with both.
   */
  const bindInteraction = (element: SVGSVGElement, scaleOf: () => Scale): void => {
    element.addEventListener(
      "wheel",
      (e: WheelEvent) => {
        e.preventDefault();
        if (e.ctrlKey || e.metaKey) {
          const anchor = posToTime(scaleOf(), e.offsetY - AXIS_PADDING);
          const factor = e.deltaY < 0 ? WHEEL_STEP : 1 / WHEEL_STEP;
          applyView((d, extent) => zoomDomain(d, factor, anchor, extent));
          return;
        }
        const scale = scaleOf();
        const span = scale.domain.t1 - scale.domain.t0;
        applyView((d, extent) =>
          panDomain(d, Math.sign(e.deltaY) * span * PAN_STEP, extent),
        );
      },
      { passive: false },
    );

    const timeAt = (offsetY: number): number =>
      posToTime(scaleOf(), offsetY - AXIS_PADDING);

    let dragFrom: { y: number; time: number; shift: boolean } | null = null;

    element.addEventListener("pointerdown", (e: PointerEvent) => {
      element.setPointerCapture(e.pointerId);
      dragFrom = { y: e.offsetY, time: timeAt(e.offsetY), shift: e.shiftKey };
    });

    element.addEventListener("pointermove", (e: PointerEvent) => {
      if (dragFrom === null || dragFrom.shift) return;
      const delta = dragFrom.time - timeAt(e.offsetY);
      if (delta === 0) return;
      applyView((d, extent) => panDomain(d, delta, extent));
      dragFrom = { ...dragFrom, y: e.offsetY, time: timeAt(e.offsetY) };
    });

    element.addEventListener("pointerup", (e: PointerEvent) => {
      if (dragFrom !== null && dragFrom.shift && Math.abs(e.offsetY - dragFrom.y) > 3) {
        const to = timeAt(e.offsetY);
        applyView((_, extent) => domainFromRange(dragFrom!.time, to, extent));
      }
      dragFrom = null;
    });
    element.addEventListener("pointercancel", () => {
      dragFrom = null;
    });
  };

  /**
   * Undated timepoints, in a tray of their own.
   *
   * They cannot be placed on a metric axis without asserting something false.
   * The tray sits outside the axis rather than below it, because "below" now
   * means "later" — chips at the bottom would read as far-future.
   */
  const renderUndated = (
    marks: readonly TimelineMark[],
    chips: readonly ValidityChip[] = [],
  ): void => {
    controls.undated.innerHTML = "";
    controls.undated.classList.toggle("hidden", marks.length + chips.length === 0);
    if (marks.length + chips.length === 0) return;

    const caption = document.createElement("span");
    caption.className =
      "text-[10px] uppercase tracking-wider text-content-muted pr-1";
    caption.textContent = "undated";
    controls.undated.appendChild(caption);

    for (const mark of marks) {
      const chip = document.createElement("button");
      chip.className =
        mark.id === state.selectedMarkId
          ? "px-1.5 py-0.5 text-[10px] rounded border bg-pink-100 text-pink-800 border-pink-300 " +
            "dark:bg-pink-900/60 dark:text-pink-200 dark:border-pink-700"
          : "px-1.5 py-0.5 text-[10px] rounded border bg-surface-raised text-content-secondary " +
            "border-line hover:bg-surface-raised-hover";
      chip.classList.add("inline-flex", "items-center", "gap-1");
      const text = document.createElement("span");
      text.textContent = truncate(mark.title, 34);
      chip.appendChild(text);
      if (mark.contested) {
        // A chip cannot carry the glyph beside it the way an axis mark can, so
        // it carries it inside. Same glyph, same hue: a tray full of chips has
        // to say which of them are disputed.
        const badge = svg("svg", {
          width: 9,
          height: 11,
          viewBox: "-5 -6 10 12",
          "aria-hidden": "true",
        });
        badge.appendChild(contestedGlyph(mark, 0, 0));
        chip.appendChild(badge);
      }
      chip.title = mark.detail;
      chip.addEventListener("mouseenter", () => onSelect(mark));
      chip.addEventListener("click", () => {
        state.selectedMarkId = state.selectedMarkId === mark.id ? null : mark.id;
        onSelect(state.selectedMarkId === null ? null : mark);
        render();
      });
      controls.undated.appendChild(chip);
    }

    // A period with no honest place on the axis keeps its words instead: a
    // label nobody has resolved, and a claim measured on another clock, which
    // comparison leaves unknown by definition (§13.2 rules 3 and 6).
    for (const chip of chips) {
      const el = document.createElement("span");
      el.className =
        "timeline-validity-chip px-1.5 py-0.5 text-[10px] rounded border border-dashed " +
        "bg-surface-raised text-content-secondary border-line inline-flex items-center gap-1";
      const text = document.createElement("span");
      text.textContent = truncate(chip.label, 40);
      el.appendChild(text);
      const badge = document.createElement("span");
      badge.className = "text-content-muted";
      badge.textContent = chip.reason === "other clock" ? "⧗" : "?";
      el.appendChild(badge);
      el.title = chip.detail;
      controls.undated.appendChild(el);
    }
  };

  const render = (): void => {
    controls.body.innerHTML = "";
    const row = currentRow();
    const filtered =
      row === null
        ? null
        : {
            ...row,
            dated: applyFilters(row.dated, state.filters),
            undated: applyFilters(row.undated, state.filters),
          };

    if (
      filtered === null ||
      (filtered.dated.length === 0 && filtered.undated.length === 0)
    ) {
      controls.empty.classList.remove("hidden");
      controls.empty.textContent =
        state.mode === "content"
          ? "No timelines in this graph. An agent creates one with create_timeline, then add_timepoint."
          : "No nodes in this graph yet.";
      renderUndated([]);
      return;
    }
    controls.empty.classList.add("hidden");
    const layout = layoutValidity(filtered.dated);
    renderUndated(filtered.undated, layout.chips);

    const width = controls.body.clientWidth;
    const height = controls.body.clientHeight;
    const view = viewFor(row!);
    if (view === null || width <= 0 || height <= 0) return;

    const usable = Math.max(1, height - AXIS_PADDING * 2);
    const scale = buildScale(filtered.dated, view.domain, usable, view.breaks);
    // Remember what broke, so the next render's hysteresis has a reference.
    state.view.set(row!.id, {
      domain: view.domain,
      breaks: scale.breaks.map((b) => b.gap),
    });

    const element = document.createElementNS(SVG_NS, "svg");
    element.setAttribute("width", String(width));
    element.setAttribute("height", String(height));
    element.setAttribute("class", "block touch-none select-none cursor-grab");

    renderDefs(element);
    const group = svg("g", { transform: `translate(0, ${AXIS_PADDING})` });
    const axisX = Math.round(width / 2);

    const validity = placeValidity(layout, scale, usable);
    // Source lanes take the gutter beside the axis, so the recurrence spines
    // and the side text both step out past them.
    const insets = insetsFor(validity.slots);

    const tickLabels = renderAxis(group, scale, axisX, usable);
    renderReferenceRule(group, scale, width, referenceTime());
    filtered.spines.forEach((spine, lane) =>
      renderSpine(
        group,
        scale,
        spine,
        lane,
        axisX,
        SPINE_INSET + validity.slots.left * STRIP_PITCH,
      ),
    );
    renderSuccession(group, validity.lanes, axisX);
    renderEnvelope(group, validity.lanes, axisX);
    for (const placed of validity.lanes) renderLane(group, placed, axisX, usable);
    for (const mark of filtered.dated) renderMark(group, scale, mark, axisX);
    const hidden = renderLabels(group, scale, filtered.dated, axisX, width, usable, insets);
    // Last, so the marks sharing the axis column cannot bury them.
    group.appendChild(tickLabels);

    const notes = [
      hidden > 0 ? `+${hidden} label${hidden === 1 ? "" : "s"} hidden` : null,
      validity.hidden > 0
        ? `+${validity.hidden} source strip${validity.hidden === 1 ? "" : "s"} hidden`
        : null,
    ].filter((note): note is string => note !== null);
    if (notes.length > 0) {
      const note = svg("text", {
        x: 4,
        y: usable - 2,
        fill: currentPalette().tickLabel,
        "font-size": 9,
      });
      note.textContent = `${notes.join(", ")} — zoom in`;
      group.appendChild(note);
    }

    element.appendChild(group);
    bindInteraction(element, () => scale);
    controls.body.appendChild(element);
  };

  const rebuild = (): void => {
    state.rows = buildRows(state.snapshot, state.mode);
    // A view belongs to a row's data; rebuilding may have changed the extent.
    state.view.clear();
    populateTimelines();
    populateMetacontexts();
    state.filters = readFilters();
    render();
  };

  // --- Public surface ---

  const loadSnapshot = (snapshot: SnapshotLike): void => {
    state.snapshot = snapshot;
    state.selectedMarkId = null;
    rebuild();
  };

  const clear = (): void => {
    state.snapshot = { nodes: [], edges: [] };
    state.selectedMarkId = null;
    state.timelineId = null;
    rebuild();
  };

  // --- Controls ---

  const onFilterChange = (): void => {
    state.filters = readFilters();
    render();
  };

  controls.modeSelect.addEventListener("change", () => {
    state.mode = controls.modeSelect.value === "content" ? "content" : "record";
    state.selectedMarkId = null;
    state.timelineId = null;
    rebuild();
  });

  controls.timelineSelect.addEventListener("change", () => {
    state.timelineId = controls.timelineSelect.value;
    state.selectedMarkId = null;
    render();
  });

  for (const control of [
    controls.typeSelect,
    controls.statusSelect,
    controls.metacontextSelect,
    controls.rangeStart,
    controls.rangeEnd,
  ]) {
    control.addEventListener("change", onFilterChange);
  }
  controls.queryInput.addEventListener("input", onFilterChange);

  controls.resetButton.addEventListener("click", () => {
    state.view.clear();
    render();
  });

  controls.nowButton.addEventListener("click", () => {
    applyView((domain, extent) => centredOn(domain, referenceTime(), extent));
  });

  // --- Live events ---

  const onTimelineStored = (event: AnyEvent): void => {
    const { timeline } = event as TimelineStored;
    const timelines = state.snapshot.timelines ?? [];
    // The event carries the timeline entire, so replace rather than merge.
    const next = timelines.some((t) => t.timeline_id === timeline.timeline_id)
      ? timelines.map((t) => (t.timeline_id === timeline.timeline_id ? timeline : t))
      : [...timelines, timeline];
    state.snapshot = { ...state.snapshot, timelines: next };
    if (state.mode === "content") rebuild();
  };

  const onNodeStored = (event: AnyEvent): void => {
    const { node } = event as NodeStored;
    const nodes = state.snapshot.nodes;
    state.snapshot = {
      ...state.snapshot,
      nodes: nodes.some((n) => n.node_id === node.node_id)
        ? nodes.map((n) => (n.node_id === node.node_id ? node : n))
        : [...nodes, node],
    };
    if (state.mode === "record") rebuild();
  };

  const onNodeStatusChanged = (event: AnyEvent): void => {
    const e = event as NodeStatusChanged;
    const nodes = state.snapshot.nodes;
    if (!nodes.some((n) => n.node_id === e.node_id)) return;
    // Status drives a filter, so a stale one would leave retired nodes showing
    // under "active only" for the rest of the session.
    state.snapshot = {
      ...state.snapshot,
      nodes: nodes.map((n) =>
        n.node_id === e.node_id ? { ...n, status: e.new_status } : n,
      ),
    };
    rebuild();
  };

  const unsubs = [
    router.subscribe("timeline_stored", onTimelineStored),
    router.subscribe("node_stored", onNodeStored),
    router.subscribe("node_status_changed", onNodeStatusChanged),
  ];

  // The panel's pixel height is its scale, so a resize is a re-render.
  const observer = new ResizeObserver(() => render());
  observer.observe(controls.body);

  // Draw once now, so the panel explains itself before the first snapshot
  // rather than sitting blank in an undefined state.
  rebuild();

  const cleanup = (): void => {
    unsubs.forEach((u) => u());
    observer.disconnect();
  };

  return { cleanup, clear, loadSnapshot, refresh: render, setFocus };
};
