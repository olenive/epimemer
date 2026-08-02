/**
 * The timeline panel: rows of marks on left-to-right axes.
 *
 * Each row owns its domain and its own zoom. There is no shared axis — one
 * graph can hold 1400 AD and last Tuesday, and no single domain renders both.
 *
 * All the arithmetic lives in `timeline-scale` and all the filtering in
 * `timeline-filter`; this module is the DOM around them.
 */

import type { EventRouter } from "./events";
import {
  NO_FILTERS,
  applyFilters,
  facetValues,
  type TimelineFilters,
} from "./timeline-filter";
import {
  allMarks,
  buildRows,
  type DatedMark,
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
  ticksForSegment,
  timeToX,
  xToTime,
  zoomDomain,
  type Domain,
  type Gap,
  type Scale,
} from "./timeline-scale";
import { currentPalette } from "./theme";
import type { AnyEvent, NodeStatusChanged, NodeStored, TimelineStored } from "./types";

const SVG_NS = "http://www.w3.org/2000/svg";

const ROW_HEIGHT = 58;
const AXIS_Y = 26;
const TICK_LABEL_Y = 44;
const MARK_RADIUS = 4.5;
const INTERVAL_HEIGHT = 9;
/** One wheel notch. Below 1 zooms in. */
const WHEEL_STEP = 0.85;

// Mark hues read on either background, so only the neutrals come from the
// palette. "Selected pink" means the same thing in both themes.
const MARK_FILL = "#3b82f6";
const MARK_FILL_SELECTED = "#ec4899";

interface RowZoom {
  domain: Domain;
  /** Gaps broken last render, fed back so a zoom drag does not make them flicker. */
  breaks: Gap[];
}

interface PanelState {
  snapshot: SnapshotLike;
  mode: TimeMode;
  rows: TimelineRow[];
  zoom: Map<string, RowZoom>;
  filters: TimelineFilters;
  selectedMarkId: string | null;
}

export interface TimelinePanelControls {
  rows: HTMLElement;
  empty: HTMLElement;
  modeSelect: HTMLSelectElement;
  typeSelect: HTMLSelectElement;
  statusSelect: HTMLSelectElement;
  metacontextSelect: HTMLSelectElement;
  queryInput: HTMLInputElement;
  rangeStart: HTMLInputElement;
  rangeEnd: HTMLInputElement;
  resetButton: HTMLElement;
}

export interface TimelinePanelHandle {
  cleanup: () => void;
  clear: () => void;
  loadSnapshot: (snapshot: SnapshotLike) => void;
  /** Re-measure and redraw — call when the panel is shown or resized. */
  refresh: () => void;
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

export const initTimelinePanel = (
  router: EventRouter,
  controls: TimelinePanelControls,
  onSelect: (mark: TimelineMark | null) => void,
): TimelinePanelHandle => {
  const state: PanelState = {
    snapshot: { nodes: [], edges: [] },
    mode: "record",
    rows: [],
    zoom: new Map(),
    filters: { ...NO_FILTERS },
    selectedMarkId: null,
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
   * markup; frames are open-ended and only the graph knows them.
   */
  const populateMetacontexts = (): void => {
    const present = facetValues(allMarks(state.rows), "mc");
    const previous = controls.metacontextSelect.value;
    controls.metacontextSelect.innerHTML = "";

    const all = document.createElement("option");
    all.value = "all";
    all.textContent = "All frames";
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

  // --- Zoom ---

  const extentFor = (row: TimelineRow): Domain | null => {
    const extent = extentOf(row.dated);
    return extent === null ? null : paddedExtent(extent);
  };

  const zoomFor = (row: TimelineRow): RowZoom | null => {
    const existing = state.zoom.get(row.id);
    if (existing) return existing;
    const extent = extentFor(row);
    if (extent === null) return null;
    const fresh: RowZoom = { domain: extent, breaks: [] };
    state.zoom.set(row.id, fresh);
    return fresh;
  };

  const applyZoom = (
    rowId: string,
    change: (current: Domain, extent: Domain) => Domain,
    everyRow: boolean,
  ): void => {
    const targets = everyRow ? state.rows : state.rows.filter((r) => r.id === rowId);
    for (const row of targets) {
      const extent = extentFor(row);
      const current = zoomFor(row);
      if (extent === null || current === null) continue;
      state.zoom.set(row.id, {
        domain: change(current.domain, extent),
        breaks: current.breaks,
      });
    }
    render();
  };

  // --- Rendering ---

  const renderMark = (group: SVGGElement, scale: Scale, mark: DatedMark): void => {
    const x = timeToX(scale, mark.start);
    const isSelected = mark.id === state.selectedMarkId;
    const fill = isSelected ? MARK_FILL_SELECTED : MARK_FILL;

    const shape =
      mark.end !== null
        ? svg("rect", {
            x,
            y: AXIS_Y - INTERVAL_HEIGHT / 2,
            width: Math.max(2, timeToX(scale, mark.end) - x),
            height: INTERVAL_HEIGHT,
            rx: 2,
            fill,
            "fill-opacity": isSelected ? 0.95 : 0.65,
          })
        : svg("circle", {
            cx: x,
            cy: AXIS_Y,
            r: isSelected ? MARK_RADIUS + 1.5 : MARK_RADIUS,
            fill,
            "fill-opacity": isSelected ? 1 : 0.85,
          });

    shape.setAttribute("class", "cursor-pointer");
    shape.addEventListener("mouseenter", () => onSelect(mark));
    shape.addEventListener("click", (e) => {
      e.stopPropagation();
      state.selectedMarkId = state.selectedMarkId === mark.id ? null : mark.id;
      onSelect(state.selectedMarkId === null ? null : mark);
      render();
    });

    const title = svg("title", {});
    title.textContent = mark.detail;
    shape.appendChild(title);
    group.appendChild(shape);
  };

  const renderAxis = (group: SVGGElement, scale: Scale, width: number): void => {
    const palette = currentPalette();
    group.appendChild(
      svg("line", {
        x1: 0,
        y1: AXIS_Y,
        x2: width,
        y2: AXIS_Y,
        stroke: palette.axis,
        "stroke-width": 1,
      }),
    );

    const visibleSpan = scale.domain.t1 - scale.domain.t0;
    for (const segment of scale.segments) {
      for (const tick of ticksForSegment(segment)) {
        const x = timeToX(scale, tick);
        group.appendChild(
          svg("line", {
            x1: x,
            y1: AXIS_Y,
            x2: x,
            y2: AXIS_Y + 5,
            stroke: palette.tick,
            "stroke-width": 1,
          }),
        );
        const label = svg("text", {
          x,
          y: TICK_LABEL_Y,
          fill: palette.tickLabel,
          "font-size": 9,
          "text-anchor": "middle",
        });
        label.textContent = formatTick(tick, visibleSpan);
        group.appendChild(label);
      }
    }

    for (const brk of scale.breaks) {
      group.appendChild(
        svg("rect", {
          x: brk.x0,
          y: AXIS_Y - 10,
          width: BREAK_PX,
          height: 20,
          fill: palette.breakBackground,
        }),
      );
      // Two slashes, the conventional mark for a collapsed axis.
      for (const offset of [7, 13]) {
        group.appendChild(
          svg("line", {
            x1: brk.x0 + offset - 3,
            y1: AXIS_Y + 7,
            x2: brk.x0 + offset + 3,
            y2: AXIS_Y - 7,
            stroke: palette.breakSlash,
            "stroke-width": 1.5,
          }),
        );
      }
      const label = svg("text", {
        x: brk.x0 + BREAK_PX / 2,
        y: AXIS_Y - 14,
        fill: palette.breakLabel,
        "font-size": 9,
        "text-anchor": "middle",
      });
      label.textContent = formatSpan(brk.gap.t1 - brk.gap.t0);
      group.appendChild(label);
    }
  };

  /** Wheel / drag handling for one row's axis. */
  const bindRowInteraction = (
    element: SVGSVGElement,
    row: TimelineRow,
    scaleOf: () => Scale,
  ): void => {
    element.addEventListener(
      "wheel",
      (e: WheelEvent) => {
        e.preventDefault();
        const anchor = xToTime(scaleOf(), e.offsetX);
        const factor = e.deltaY < 0 ? WHEEL_STEP : 1 / WHEEL_STEP;
        applyZoom(row.id, (d, extent) => zoomDomain(d, factor, anchor, extent), e.altKey);
      },
      { passive: false },
    );

    let dragFrom: { x: number; time: number; shift: boolean } | null = null;

    element.addEventListener("pointerdown", (e: PointerEvent) => {
      element.setPointerCapture(e.pointerId);
      dragFrom = { x: e.offsetX, time: xToTime(scaleOf(), e.offsetX), shift: e.shiftKey };
    });

    element.addEventListener("pointermove", (e: PointerEvent) => {
      if (dragFrom === null || dragFrom.shift) return;
      const scale = scaleOf();
      // Convert the pixel delta into time at the current scale, then slide.
      const delta = dragFrom.time - xToTime(scale, e.offsetX);
      if (delta === 0) return;
      applyZoom(row.id, (d, extent) => panDomain(d, delta, extent), e.altKey);
      dragFrom = { ...dragFrom, x: e.offsetX, time: xToTime(scaleOf(), e.offsetX) };
    });

    const endDrag = (e: PointerEvent): void => {
      if (dragFrom !== null && dragFrom.shift && Math.abs(e.offsetX - dragFrom.x) > 3) {
        const to = xToTime(scaleOf(), e.offsetX);
        applyZoom(row.id, (_, extent) => domainFromRange(dragFrom!.time, to, extent), e.altKey);
      }
      dragFrom = null;
    };

    element.addEventListener("pointerup", endDrag);
    element.addEventListener("pointercancel", () => {
      dragFrom = null;
    });
  };

  const renderUndated = (marks: readonly TimelineMark[]): HTMLElement => {
    const lane = document.createElement("div");
    lane.className = "flex flex-wrap items-center gap-1 pl-1 pb-1.5";

    const caption = document.createElement("span");
    caption.className =
      "text-[10px] uppercase tracking-wider text-gray-600 pr-1";
    caption.textContent = "undated";
    lane.appendChild(caption);

    for (const mark of marks) {
      const chip = document.createElement("button");
      chip.className =
        mark.id === state.selectedMarkId
          ? "px-1.5 py-0.5 text-[10px] rounded border bg-pink-100 text-pink-800 border-pink-300 " +
            "dark:bg-pink-900/60 dark:text-pink-200 dark:border-pink-700"
          : "px-1.5 py-0.5 text-[10px] rounded border bg-gray-100 text-gray-600 border-gray-400 hover:bg-gray-50 " +
            "dark:bg-gray-800 dark:text-gray-400 dark:border-gray-700 dark:hover:bg-gray-700";
      chip.textContent = truncate(mark.title, 34);
      chip.title = mark.detail;
      chip.addEventListener("mouseenter", () => onSelect(mark));
      chip.addEventListener("click", () => {
        state.selectedMarkId = state.selectedMarkId === mark.id ? null : mark.id;
        onSelect(state.selectedMarkId === null ? null : mark);
        render();
      });
      lane.appendChild(chip);
    }
    return lane;
  };

  const renderRow = (row: TimelineRow, width: number): HTMLElement => {
    const wrapper = document.createElement("div");
    wrapper.className = "border-b border-gray-400 dark:border-gray-800/60";

    const head = document.createElement("div");
    head.className = "flex items-baseline gap-2 px-2 pt-1.5";
    const name = document.createElement("span");
    name.className = "text-xs font-medium text-gray-600 dark:text-gray-400";
    name.textContent = row.name;
    const count = document.createElement("span");
    count.className = "text-[10px] text-gray-600";
    count.textContent = `${row.dated.length + row.undated.length} marks`;
    head.append(name, count);
    wrapper.appendChild(head);

    const zoom = zoomFor(row);
    if (zoom !== null && width > 0) {
      const scale = buildScale(row.dated, zoom.domain, width, zoom.breaks);
      // Remember what broke, so the next render's hysteresis has a reference.
      state.zoom.set(row.id, { domain: zoom.domain, breaks: scale.breaks.map((b) => b.gap) });

      const element = document.createElementNS(SVG_NS, "svg");
      element.setAttribute("width", String(width));
      element.setAttribute("height", String(ROW_HEIGHT));
      element.setAttribute("class", "block touch-none select-none cursor-grab");

      const group = svg("g", {});
      renderAxis(group, scale, width);
      for (const mark of row.dated) renderMark(group, scale, mark);
      element.appendChild(group);
      bindRowInteraction(element, row, () => scale);
      wrapper.appendChild(element);
    }

    if (row.undated.length > 0) wrapper.appendChild(renderUndated(row.undated));
    return wrapper;
  };

  const render = (): void => {
    const width = controls.rows.clientWidth;
    controls.rows.innerHTML = "";

    const visible = state.rows
      .map((row) => ({
        ...row,
        dated: applyFilters(row.dated, state.filters),
        undated: applyFilters(row.undated, state.filters),
      }))
      .filter((row) => row.dated.length > 0 || row.undated.length > 0);

    if (visible.length === 0) {
      controls.empty.classList.remove("hidden");
      controls.empty.textContent =
        state.mode === "content"
          ? "No timelines in this graph. An agent creates one with create_timeline, then add_timepoint."
          : "No nodes in this graph yet.";
      return;
    }
    controls.empty.classList.add("hidden");

    for (const row of visible) controls.rows.appendChild(renderRow(row, width));
  };

  const rebuild = (): void => {
    state.rows = buildRows(state.snapshot, state.mode);
    // Zoom belongs to a row's data; rebuilding may have changed the extent.
    state.zoom.clear();
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
    rebuild();
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
    state.zoom.clear();
    render();
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

  // A row's pixel width is its scale, so a resize is a re-render.
  const observer = new ResizeObserver(() => render());
  observer.observe(controls.rows);

  // Draw once now, so the panel explains itself before the first snapshot
  // rather than sitting blank in an undefined state.
  rebuild();

  const cleanup = (): void => {
    unsubs.forEach((u) => u());
    observer.disconnect();
  };

  return { cleanup, clear, loadSnapshot, refresh: render };
};
