// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { EventRouter } from "./events";
import { paletteFor, semanticPaletteFor } from "./theme";
import type { TimelineMark } from "./timeline-model";
import {
  centredOn,
  extentIncluding,
  initTimelinePanel,
  referenceTimeFor,
  type TimelinePanelControls,
  type TimelinePanelHandle,
} from "./timeline-panel";
import type {
  AnyEvent,
  BoundaryProposalView,
  EdgeView,
  ImpreciseInstantView,
  NodeView,
  OccurrenceView,
  RecurrenceView,
  TimelineView,
  TimepointView,
  ValidityIntervalView,
} from "./types";

/** jsdom has no ResizeObserver, and the panel observes its row container. */
class StubResizeObserver {
  observe(): void {}
  disconnect(): void {}
}
vi.stubGlobal("ResizeObserver", StubResizeObserver);

const MARKUP = `
  <select id="mode"><option value="record">r</option><option value="content">c</option></select>
  <select id="type"><option value="all">all</option><option value="fact">fact</option><option value="topic">topic</option></select>
  <select id="status"><option value="all">all</option><option value="active">active</option><option value="superseded">superseded</option><option value="historical">historical</option><option value="corrected">corrected</option></select>
  <select id="mc"><option value="all">all</option></select>
  <input id="query" type="search" />
  <input id="range-start" type="date" />
  <input id="range-end" type="date" />
  <select id="timeline-select"></select>
  <button id="reset"></button>
  <button id="now"></button>
  <div id="body"></div>
  <div id="undated" class="hidden"></div>
  <div id="empty" class="hidden"></div>
`;

// --- Fixtures ---

const node = (over: Partial<NodeView> & { node_id: string }): NodeView => ({
  node_type: "fact",
  content: "a fact",
  status: "active",
  source_id: "s1",
  extraction_method: "agent",
  confidence: 0.9,
  retrieved_at: "2024-01-01T00:00:00Z",
  created_at: "2024-01-01T00:00:00Z",
  graph: "default",
  metadata: {},
  ...over,
});

const edge = (over: Partial<EdgeView> & { src_id: string; dst_id: string }): EdgeView => ({
  edge_id: `${over.src_id}->${over.dst_id}`,
  edge_type: "timelink",
  weight: 1,
  validity: [],
  created_at: "2024-01-01T00:00:00Z",
  graph: "default",
  metadata: {},
  ...over,
});

const timepoint = (
  id: string,
  start: string | null,
  label: string | null,
  over: Partial<TimepointView> = {},
): TimepointView => ({
  timepoint_id: id,
  start,
  end: null,
  label,
  kind: start === null ? "vague" : "instant",
  earliest: null,
  latest: null,
  contested: false,
  temporal_contradiction_id: null,
  metadata: {},
  ...over,
});

const occurrence = (
  over: Partial<OccurrenceView> & { occurrence_start: string },
): OccurrenceView => ({
  start: over.occurrence_start,
  end: null,
  moved_to: null,
  materialised_id: null,
  ...over,
});

const recurrence = (
  over: Partial<RecurrenceView> & { recurrence_id: string },
): RecurrenceView => ({
  label: "the weekly service",
  rule_kind: "periodic",
  bounds_start: null,
  bounds_end: null,
  window_start: null,
  window_end: null,
  occurrences: [],
  truncated: false,
  ...over,
});

const timeline = (over: Partial<TimelineView> & { timeline_id: string }): TimelineView => ({
  name: "History",
  description: "",
  timepoints: [],
  recurrences: [],
  reference_time: null,
  created_at: "2024-01-01T00:00:00Z",
  graph: "default",
  metadata: {},
  ...over,
});

const graphEvent = (event_type: string, rest: Record<string, unknown>): AnyEvent =>
  ({
    timestamp: "2024-01-01T00:00:00Z",
    category: "graph",
    graph: "default",
    event_type,
    ...rest,
  }) as unknown as AnyEvent;

// --- Harness ---

const el = <T extends HTMLElement>(id: string): T => document.getElementById(id) as T;

const controlsFromMarkup = (): TimelinePanelControls => ({
  body: el("body"),
  empty: el("empty"),
  undated: el("undated"),
  timelineSelect: el("timeline-select"),
  nowButton: el("now"),
  modeSelect: el("mode"),
  typeSelect: el("type"),
  statusSelect: el("status"),
  metacontextSelect: el("mc"),
  queryInput: el("query"),
  rangeStart: el("range-start"),
  rangeEnd: el("range-end"),
  resetButton: el("reset"),
});

let panel: TimelinePanelHandle;
let controls: TimelinePanelControls;
let selected: (TimelineMark | null)[];
let emit: (type: string, event: AnyEvent) => void;

beforeEach(() => {
  document.body.innerHTML = MARKUP;
  // jsdom lays nothing out, and a zero size means the panel draws no axis.
  Object.defineProperty(el("body"), "clientWidth", { value: 600, configurable: true });
  Object.defineProperty(el("body"), "clientHeight", { value: 400, configurable: true });

  const handlers = new Map<string, ((e: AnyEvent) => void)[]>();
  const router = {
    subscribe: (type: string, handler: (e: AnyEvent) => void) => {
      handlers.set(type, [...(handlers.get(type) ?? []), handler]);
      return () => {};
    },
  } as unknown as EventRouter;

  selected = [];
  controls = controlsFromMarkup();
  emit = (type, event) => (handlers.get(type) ?? []).forEach((h) => h(event));
  panel = initTimelinePanel(router, controls, (mark) => selected.push(mark));
});

const marks = (): SVGElement[] => [
  ...document.querySelectorAll<SVGElement>("#body circle, #body rect.cursor-pointer"),
];

/** Side text is drawn as clickable <text>; tick and break labels are not. */
const labels = (): SVGElement[] => [
  ...document.querySelectorAll<SVGElement>("#body text.cursor-pointer"),
];

const change = (element: HTMLElement): void => {
  element.dispatchEvent(new Event("change"));
};

const click = (element: Element): void => {
  element.dispatchEvent(new MouseEvent("click", { bubbles: true }));
};

const useContentMode = (): void => {
  controls.modeSelect.value = "content";
  change(controls.modeSelect);
};

// --- Tests ---

describe("empty states", () => {
  it("shows one before anything is loaded", () => {
    expect(el("empty").classList.contains("hidden")).toBe(false);
  });

  it("names the tool that creates a timeline when content mode is empty", () => {
    // An empty panel that does not say why is indistinguishable from a broken one.
    useContentMode();
    expect(el("empty").textContent).toContain("create_timeline");
  });

  it("clears back to empty", () => {
    panel.loadSnapshot({ nodes: [node({ node_id: "n1" })], edges: [] });
    expect(marks()).toHaveLength(1);

    panel.clear();

    expect(marks()).toHaveLength(0);
    expect(el("empty").classList.contains("hidden")).toBe(false);
  });
});

describe("record mode", () => {
  it("draws every node on one axis, split by side rather than by row", () => {
    panel.loadSnapshot({
      nodes: [
        node({ node_id: "n1", node_type: "fact", content: "a told thing" }),
        node({ node_id: "n2", node_type: "inference", content: "a derived thing" }),
      ],
      edges: [],
    });

    expect(marks()).toHaveLength(2);
    // Facts left of the axis, inferences right of it.
    const axisX = 300;
    const xs = labels().map((t) => Number(t.getAttribute("x")));
    expect(xs.some((x) => x < axisX)).toBe(true);
    expect(xs.some((x) => x > axisX)).toBe(true);
  });

  it("has no timeline to choose between, so the selector is disabled", () => {
    panel.loadSnapshot({ nodes: [node({ node_id: "n1" })], edges: [] });
    expect((el("timeline-select") as HTMLSelectElement).disabled).toBe(true);
  });

  it("draws an axis with tick labels", () => {
    panel.loadSnapshot({
      nodes: [
        node({ node_id: "n1", created_at: "2024-01-01T00:00:00Z" }),
        node({ node_id: "n2", created_at: "2024-06-01T00:00:00Z" }),
      ],
      edges: [],
    });

    expect(document.querySelectorAll("#body line").length).toBeGreaterThan(0);
    expect(document.querySelectorAll("#body text").length).toBeGreaterThan(0);
  });
});

describe("content mode", () => {
  it("draws the selected timeline", () => {
    panel.loadSnapshot({
      nodes: [],
      edges: [],
      timelines: [
        timeline({
          timeline_id: "t1",
          name: "History of AI",
          timepoints: [
            timepoint("p1", "1956-01-01T00:00:00Z", "Dartmouth workshop"),
          ],
        }),
      ],
    });
    useContentMode();

    expect(el("timeline-select").textContent).toContain("History of AI");
    expect(marks()).toHaveLength(1);
  });

  it("shows one timeline at a time and switches on the selector", () => {
    panel.loadSnapshot({
      nodes: [],
      edges: [],
      timelines: [
        timeline({
          timeline_id: "t1",
          name: "History of AI",
          timepoints: [timepoint("p1", "1956-01-01T00:00:00Z", "Dartmouth")],
        }),
        timeline({
          timeline_id: "t2",
          name: "Renaissance",
          timepoints: [
            timepoint("p2", "1450-01-01T00:00:00Z", "printing press"),
            timepoint("p3", "1500-01-01T00:00:00Z", "High Renaissance"),
          ],
        }),
      ],
    });
    useContentMode();

    expect(marks()).toHaveLength(1);

    const select = el("timeline-select") as HTMLSelectElement;
    expect(select.disabled).toBe(false);
    select.value = "t2";
    change(select);

    expect(marks()).toHaveLength(2);
  });

  it("puts vague timepoints in the undated lane, not on the axis", () => {
    panel.loadSnapshot({
      nodes: [],
      edges: [],
      timelines: [
        timeline({
          timeline_id: "t1",
          timepoints: [timepoint("p1", null, "during the Renaissance")],
        }),
      ],
    });
    useContentMode();

    expect(marks()).toHaveLength(0);
    // Its own tray, outside the axis: "below" now means "later", so a chip at
    // the bottom of the axis would read as far-future.
    expect(el("undated").classList.contains("hidden")).toBe(false);
    expect(el("undated").textContent).toContain("during the Renaissance");
  });

  it("draws an interval timepoint as a bar", () => {
    panel.loadSnapshot({
      nodes: [],
      edges: [],
      timelines: [
        timeline({
          timeline_id: "t1",
          timepoints: [
            { ...timepoint("p1", "2024-01-01T00:00:00Z", "a war"), end: "2024-06-01T00:00:00Z" },
            timepoint("p2", "2024-08-01T00:00:00Z", "a treaty"),
          ],
        }),
      ],
    });
    useContentMode();

    expect(document.querySelectorAll("#body rect.cursor-pointer")).toHaveLength(1);
    expect(document.querySelectorAll("#body circle")).toHaveLength(1);
  });
});

describe("a point the order places", () => {
  const band = (): SVGElement | null =>
    document.querySelector<SVGElement>("#body rect.timeline-band");

  const withBounds = (over: Partial<TimepointView>): void => {
    panel.loadSnapshot({
      nodes: [],
      edges: [],
      timelines: [
        timeline({
          timeline_id: "t1",
          timepoints: [
            timepoint("p1", "1890-01-01T00:00:00Z", "the fire"),
            timepoint("p2", "1900-01-01T00:00:00Z", "the flood"),
            timepoint("p3", null, "the quarrel", over),
          ],
        }),
      ],
    });
    useContentMode();
  };

  it("draws a hatched band across the two bounds instead of a chip", () => {
    withBounds({ earliest: "1890-01-01T00:00:00Z", latest: "1900-01-01T00:00:00Z" });

    const drawn = band();
    expect(drawn).not.toBeNull();
    expect(drawn!.getAttribute("fill")).toContain("timeline-hatch");
    expect(Number(drawn!.getAttribute("height"))).toBeGreaterThan(0);
    // Nothing left for the tray: the point has a place now.
    expect(el("undated").classList.contains("hidden")).toBe(true);
  });

  it("writes the label on the band word for word", () => {
    withBounds({
      earliest: "1890-01-01T00:00:00Z",
      latest: "1900-01-01T00:00:00Z",
      label: "the quarrel between the abbot and the miller",
    });

    const label = document.querySelector("#body text.timeline-band-label");
    expect(label?.textContent).toBe("the quarrel between the abbot and the miller");
  });

  it("dissolves toward the later side when only the earliest is known", () => {
    // "After the fire, we do not know when." The edge is in the fog below.
    withBounds({ earliest: "1890-01-01T00:00:00Z" });

    expect(band()!.getAttribute("mask")).toContain("timeline-fade-later");
  });

  it("dissolves toward the earlier side when only the latest is known", () => {
    withBounds({ latest: "1900-01-01T00:00:00Z" });

    expect(band()!.getAttribute("mask")).toContain("timeline-fade-earlier");
  });

  it("leaves a point nothing constrains in the tray", () => {
    withBounds({});

    expect(band()).toBeNull();
    expect(el("undated").textContent).toContain("the quarrel");
  });
});

describe("a point whose order is disputed", () => {
  const load = (over: Partial<TimepointView>): void => {
    panel.loadSnapshot({
      nodes: [],
      edges: [],
      timelines: [
        timeline({
          timeline_id: "t1",
          timepoints: [
            timepoint("p1", "1890-01-01T00:00:00Z", "the fire"),
            timepoint("p2", "1900-01-01T00:00:00Z", "the flood"),
            timepoint("p3", null, "the quarrel", over),
          ],
        }),
      ],
    });
    useContentMode();
  };

  const contestedMarks = (root: string): SVGElement[] => [
    ...document.querySelectorAll<SVGElement>(`${root} .timeline-contested`),
  ];

  const disputed = { contested: true, temporal_contradiction_id: "c1" };

  /** The mark standing for one timepoint, found by the detail on its tooltip. */
  const markTitled = (text: string): SVGElement | undefined =>
    [...document.querySelectorAll<SVGElement>("#body circle")].find((mark) =>
      (mark.textContent ?? "").includes(text),
    );

  it("keeps a dated point where its date puts it and marks it beside", () => {
    load({ start: "1895-01-01T00:00:00Z", kind: "instant" });
    const undisputedY = markTitled("the quarrel")?.getAttribute("cy");
    expect(undisputedY).not.toBeUndefined();

    load({ start: "1895-01-01T00:00:00Z", kind: "instant", ...disputed });

    // The date a source gave is kept. What is in doubt is the order.
    expect(markTitled("the quarrel")?.getAttribute("cy")).toBe(undisputedY);
    expect(contestedMarks("#body")).toHaveLength(1);
  });

  it("marks a point with no place in the tray, where it stays", () => {
    load(disputed);

    expect(el("undated").textContent).toContain("the quarrel");
    expect(contestedMarks("#undated")).toHaveLength(1);
  });

  it("draws the mark in the palette's contradiction hue, minting nothing", () => {
    load({ start: "1895-01-01T00:00:00Z", kind: "instant", ...disputed });

    expect(contestedMarks("#body")[0].getAttribute("stroke")).toBe(
      semanticPaletteFor("light").contradiction,
    );
  });

  it("names the dispute on hover", () => {
    load({ start: "1895-01-01T00:00:00Z", kind: "instant", ...disputed });

    const [glyph] = contestedMarks("#body");
    expect(glyph.textContent ?? "").toContain("c1");
  });

  it("marks nothing when nothing is disputed", () => {
    load({ start: "1895-01-01T00:00:00Z", kind: "instant" });

    expect(contestedMarks("#body")).toHaveLength(0);
  });
});

describe("what a rule says happens over and over", () => {
  const beads = (): SVGElement[] => [
    ...document.querySelectorAll<SVGElement>("#body circle.timeline-bead"),
  ];
  const spine = (): SVGElement | null =>
    document.querySelector<SVGElement>("#body line.timeline-spine");

  /** Two dated points give the axis a span; the rule fills it. */
  const withRule = (
    occurrences: OccurrenceView[],
    timepoints = [
      timepoint("p1", "1897-01-01T00:00:00Z", "the first service"),
      timepoint("p2", "1897-02-01T00:00:00Z", "the last service"),
    ],
  ): void => {
    panel.loadSnapshot({
      nodes: [],
      edges: [],
      timelines: [
        timeline({
          timeline_id: "t1",
          timepoints,
          recurrences: [recurrence({ recurrence_id: "r1", occurrences })],
        }),
      ],
    });
    useContentMode();
  };

  it("threads a bead onto a dotted spine for each occurrence", () => {
    withRule([
      occurrence({ occurrence_start: "1897-01-08T00:00:00Z" }),
      occurrence({ occurrence_start: "1897-01-15T00:00:00Z" }),
      occurrence({ occurrence_start: "1897-01-22T00:00:00Z" }),
    ]);

    expect(beads()).toHaveLength(3);
    // Dotted, because the spine says "same rule" and asserts nothing between.
    expect(spine()?.getAttribute("stroke-dasharray")).not.toBeNull();
  });

  it("spans the spine from the first occurrence to the last", () => {
    withRule([
      occurrence({ occurrence_start: "1897-01-08T00:00:00Z" }),
      occurrence({ occurrence_start: "1897-01-15T00:00:00Z" }),
      occurrence({ occurrence_start: "1897-01-22T00:00:00Z" }),
    ]);

    const drawn = beads().map((bead) => Number(bead.getAttribute("cy")));
    expect(Number(spine()!.getAttribute("y1"))).toBeCloseTo(Math.min(...drawn), 5);
    expect(Number(spine()!.getAttribute("y2"))).toBeCloseTo(Math.max(...drawn), 5);
  });

  it("leaves a materialised occurrence to its own mark rather than a bead", () => {
    // Materialising turns an occurrence into an ordinary point, and an ordinary
    // point is already drawn. A bead beside it would be one date drawn twice.
    withRule(
      [
        occurrence({ occurrence_start: "1897-01-08T00:00:00Z", materialised_id: "p3" }),
        occurrence({ occurrence_start: "1897-01-15T00:00:00Z" }),
      ],
      [
        timepoint("p1", "1897-01-01T00:00:00Z", "the first service"),
        timepoint("p2", "1897-02-01T00:00:00Z", "the last service"),
        timepoint("p3", "1897-01-08T00:00:00Z", "the weekly service"),
      ],
    );

    expect(beads()).toHaveLength(1);
    const marked = [
      ...document.querySelectorAll<SVGElement>("#body circle:not(.timeline-bead)"),
    ].filter((c) => (c.textContent ?? "").includes("the weekly service"));
    expect(marked).toHaveLength(1);
  });

  it("puts a moved occurrence's bead on the date it moved to", () => {
    withRule(
      [
        occurrence({
          occurrence_start: "1897-01-08T00:00:00Z",
          start: "1897-01-09T00:00:00Z",
          moved_to: "1897-01-09T00:00:00Z",
        }),
      ],
      [
        timepoint("p1", "1897-01-01T00:00:00Z", "the first service"),
        timepoint("p2", "1897-02-01T00:00:00Z", "the last service"),
        timepoint("p3", "1897-01-09T00:00:00Z", "the ninth"),
      ],
    );

    const ninth = [...document.querySelectorAll<SVGElement>("#body circle")].find((c) =>
      (c.textContent ?? "").includes("the ninth"),
    );
    expect(Number(beads()[0].getAttribute("cy"))).toBeCloseTo(
      Number(ninth!.getAttribute("cy")),
      5,
    );
  });

  it("draws nothing for a rule with no occurrences in the window", () => {
    withRule([]);

    expect(beads()).toHaveLength(0);
    expect(spine()).toBeNull();
  });
});

describe("theming", () => {
  // The axis is drawn, not styled, so Tailwind's dark: variants cannot reach
  // it — the palette has to be read at render time or the timeline stays dark
  // on a white page.
  const axisStroke = (): string | null =>
    document.querySelector("#body line")?.getAttribute("stroke") ?? null;

  const twoNodes = {
    nodes: [
      node({ node_id: "n1", created_at: "2024-01-01T00:00:00Z" }),
      node({ node_id: "n2", created_at: "2024-06-01T00:00:00Z" }),
    ],
    edges: [],
  };

  it("draws the axis in the light palette by default", () => {
    panel.loadSnapshot(twoNodes);
    expect(axisStroke()).toBe(paletteFor("light").axis);
  });

  it("draws the axis in the dark palette when the dark class is set", () => {
    document.documentElement.classList.add("dark");
    try {
      panel.loadSnapshot(twoNodes);
      expect(axisStroke()).toBe(paletteFor("dark").axis);
    } finally {
      document.documentElement.classList.remove("dark");
    }
  });

  it("repaints on refresh when the theme changed underneath it", () => {
    panel.loadSnapshot(twoNodes);
    expect(axisStroke()).toBe(paletteFor("light").axis);

    document.documentElement.classList.add("dark");
    try {
      panel.refresh();
      expect(axisStroke()).toBe(paletteFor("dark").axis);
    } finally {
      document.documentElement.classList.remove("dark");
    }
  });
});

describe("tick labels", () => {
  // The axis column is where every mark is drawn, so a tick label sharing that
  // column is competing with data for the same pixels. Two things keep it
  // readable: it is painted last, and it carries its own background.
  const spanning = {
    nodes: [
      node({ node_id: "n1", created_at: "2024-01-01T00:00:00Z" }),
      node({ node_id: "n2", created_at: "2024-06-01T00:00:00Z" }),
    ],
    edges: [],
  };

  /** Every element under the body, in document order — which is paint order. */
  const painted = (): Element[] => [...el("body").querySelectorAll("*")];

  const ticks = (): SVGElement[] => [
    ...document.querySelectorAll<SVGElement>("#body text.tick-label"),
  ];

  it("paints tick labels after every mark, so the axis column cannot bury them", () => {
    panel.loadSnapshot(spanning);
    const order = painted();

    const lastMark = Math.max(...marks().map((m) => order.indexOf(m)));
    const firstTick = Math.min(...ticks().map((t) => order.indexOf(t)));

    expect(ticks().length).toBeGreaterThan(0);
    expect(firstTick).toBeGreaterThan(lastMark);
  });

  it("backs each tick label with a plate wide enough to sit behind it", () => {
    panel.loadSnapshot(spanning);
    const order = painted();
    const plates = [...document.querySelectorAll<SVGElement>("#body rect.tick-plate")];

    expect(plates).toHaveLength(ticks().length);
    for (const tick of ticks()) {
      const centre = Number(tick.getAttribute("x"));
      const text = tick.textContent ?? "";
      const plate = plates.find(
        (p) =>
          Number(p.getAttribute("x")) < centre &&
          Number(p.getAttribute("x")) + Number(p.getAttribute("width")) > centre,
      );
      expect(plate, `no plate behind ${text}`).toBeDefined();
      // Behind, not over: the plate has to be the earlier sibling.
      expect(order.indexOf(plate as Element)).toBeLessThan(order.indexOf(tick));
      expect(Number(plate?.getAttribute("width"))).toBeGreaterThan(text.length * 5.6);
    }
  });

  it("fills the plate from the same chrome colour the break marker uses", () => {
    // One value, two users. Two would drift.
    panel.loadSnapshot(spanning);
    const plate = document.querySelector("#body rect.tick-plate");
    expect(plate).not.toBeNull();
    expect(paletteFor("light").surfaceChrome).toBeTypeOf("string");
    expect(plate?.getAttribute("fill")).toBe(paletteFor("light").surfaceChrome);
  });
});

describe("filtering", () => {
  it("narrows by linked node type", () => {
    panel.loadSnapshot({
      nodes: [
        node({ node_id: "n1", node_type: "fact" }),
        node({ node_id: "n2", node_type: "topic" }),
      ],
      edges: [],
    });
    expect(marks()).toHaveLength(2);

    controls.typeSelect.value = "fact";
    change(controls.typeSelect);

    expect(marks()).toHaveLength(1);
  });

  it("narrows by free text", () => {
    panel.loadSnapshot({
      nodes: [
        node({ node_id: "n1", content: "Armistice signed" }),
        node({ node_id: "n2", content: "Treaty ratified" }),
      ],
      edges: [],
    });

    controls.queryInput.value = "armistice";
    controls.queryInput.dispatchEvent(new Event("input"));

    expect(marks()).toHaveLength(1);
  });

  it("narrows by date range", () => {
    panel.loadSnapshot({
      nodes: [
        node({ node_id: "n1", created_at: "2024-01-15T00:00:00Z" }),
        node({ node_id: "n2", created_at: "2025-01-15T00:00:00Z" }),
      ],
      edges: [],
    });

    controls.rangeStart.value = "2024-01-01";
    controls.rangeEnd.value = "2024-12-31";
    change(controls.rangeStart);

    expect(marks()).toHaveLength(1);
  });

  it("accepts a range with only one end set", () => {
    panel.loadSnapshot({
      nodes: [
        node({ node_id: "n1", created_at: "2024-01-15T00:00:00Z" }),
        node({ node_id: "n2", created_at: "2025-01-15T00:00:00Z" }),
      ],
      edges: [],
    });

    controls.rangeStart.value = "2025-01-01";
    change(controls.rangeStart);

    expect(marks()).toHaveLength(1);
  });

  it("offers the metacontexts present in the data as options", () => {
    panel.loadSnapshot({
      nodes: [node({ node_id: "n1" })],
      edges: [edge({ src_id: "n1", dst_id: "mc1", edge_type: "has_metacontext" })],
      metacontexts: [
        {
          metacontext_id: "mc1",
          content: "Real historical events",
          description: "",
          graph: "default",
        },
      ],
    });

    const options = [...controls.metacontextSelect.options].map((o) => o.value);
    expect(options).toEqual(["all", "Real historical events"]);
  });
});

describe("selection", () => {
  it("reports the clicked mark's linked nodes, for the graph panel to highlight", () => {
    panel.loadSnapshot({ nodes: [node({ node_id: "n1" })], edges: [] });

    click(marks()[0]);

    expect(selected.at(-1)?.nodeIds).toEqual(["n1"]);
  });

  it("clears the selection when the same mark is clicked again", () => {
    panel.loadSnapshot({ nodes: [node({ node_id: "n1" })], edges: [] });

    click(marks()[0]);
    click(marks()[0]);

    expect(selected.at(-1)).toBeNull();
  });

  it("selects an undated chip too", () => {
    panel.loadSnapshot({
      nodes: [],
      edges: [],
      timelines: [
        timeline({
          timeline_id: "t1",
          timepoints: [timepoint("p1", null, "long ago")],
        }),
      ],
    });
    useContentMode();

    click(document.querySelector("#undated button")!);

    expect(selected.at(-1)?.id).toBe("p1");
  });
});

describe("live events", () => {
  it("adds a timeline that arrives after load", () => {
    panel.loadSnapshot({ nodes: [], edges: [], timelines: [] });
    useContentMode();
    expect(el("timeline-select").textContent).not.toContain("Late arrival");

    emit(
      "timeline_stored",
      graphEvent("timeline_stored", {
        timeline: timeline({
          timeline_id: "t9",
          name: "Late arrival",
          timepoints: [timepoint("p1", "2024-01-01T00:00:00Z", "first")],
        }),
      }),
    );

    expect(el("timeline-select").textContent).toContain("Late arrival");
  });

  it("replaces a re-stored timeline rather than duplicating it", () => {
    const withPoints = (count: number): TimelineView =>
      timeline({
        timeline_id: "t1",
        name: "History",
        timepoints: Array.from({ length: count }, (_, i) =>
          timepoint(`p${i}`, `2024-0${i + 1}-01T00:00:00Z`, `point ${i}`),
        ),
      });

    panel.loadSnapshot({ nodes: [], edges: [], timelines: [withPoints(1)] });
    useContentMode();

    emit("timeline_stored", graphEvent("timeline_stored", { timeline: withPoints(2) }));

    expect(el("timeline-select").querySelectorAll("option")).toHaveLength(1);
    expect(marks()).toHaveLength(2);
  });

  it("applies a status change so a retired node leaves an active-only view", () => {
    panel.loadSnapshot({ nodes: [node({ node_id: "n1", status: "active" })], edges: [] });
    controls.statusSelect.value = "active";
    change(controls.statusSelect);
    expect(marks()).toHaveLength(1);

    emit(
      "node_status_changed",
      graphEvent("node_status_changed", {
        node_id: "n1",
        old_status: "active",
        new_status: "superseded",
      }),
    );

    expect(marks()).toHaveLength(0);
  });

  it("adds a node that arrives after load", () => {
    panel.loadSnapshot({ nodes: [], edges: [] });

    emit("node_stored", graphEvent("node_stored", { node: node({ node_id: "n1" }) }));

    expect(marks()).toHaveLength(1);
  });
});

describe("reference time", () => {
  const withReference = (reference: string | null): void => {
    panel.loadSnapshot({
      nodes: [],
      edges: [],
      timelines: [
        timeline({
          timeline_id: "t1",
          name: "Dracula",
          reference_time: reference,
          timepoints: [
            timepoint("p1", "1897-05-26T00:00:00Z", "the journal opens"),
            timepoint("p2", "1897-11-06T00:00:00Z", "the pursuit ends"),
          ],
        }),
      ],
    });
    useContentMode();
  };

  it("draws a rule when the timeline's own now is in view", () => {
    withReference("1897-08-01T00:00:00Z");
    expect(el("body").textContent).toContain("now");
  });

  it("does not draw the rule when real time is nowhere near the data", () => {
    // Unset means "follow the wall clock", which for an 1897 timeline is far
    // below the visible window. Clamping it to an edge would put the present
    // somewhere it is not.
    withReference(null);
    expect(el("body").textContent).not.toContain("now");
  });
});

describe("referenceTimeFor", () => {
  const snapshot = {
    nodes: [],
    edges: [],
    timelines: [
      timeline({ timeline_id: "t1", reference_time: "1897-05-26T00:00:00Z" }),
      timeline({ timeline_id: "t2", reference_time: null }),
    ],
  };

  it("uses the timeline's stated present in content mode", () => {
    expect(referenceTimeFor(snapshot, "content", "t1", 999)).toBe(
      Date.parse("1897-05-26T00:00:00Z"),
    );
  });

  it("follows the clock when the timeline states nothing", () => {
    expect(referenceTimeFor(snapshot, "content", "t2", 999)).toBe(999);
  });

  it("ignores a fictional present in record mode", () => {
    // Record time is wall-clock: `created_at` is when the graph learned it, so
    // an 1897 anchor would be measuring against the wrong thing entirely.
    expect(referenceTimeFor(snapshot, "record", "t1", 999)).toBe(999);
  });

  it("falls back to the clock on an unparseable timestamp", () => {
    const broken = {
      nodes: [],
      edges: [],
      timelines: [timeline({ timeline_id: "t1", reference_time: "not a date" })],
    };
    expect(referenceTimeFor(broken, "content", "t1", 999)).toBe(999);
  });
});

describe("extentIncluding", () => {
  it("widens an extent that does not reach the reference time", () => {
    // Otherwise centring on "now" is impossible and the view settles at an
    // edge without saying so.
    expect(extentIncluding({ t0: 0, t1: 100 }, 500)).toEqual({ t0: 0, t1: 500 });
    expect(extentIncluding({ t0: 0, t1: 100 }, -500)).toEqual({ t0: -500, t1: 100 });
  });

  it("leaves an extent that already contains it alone", () => {
    expect(extentIncluding({ t0: 0, t1: 100 }, 50)).toEqual({ t0: 0, t1: 100 });
  });
});

describe("centredOn", () => {
  const extent = { t0: 0, t1: 1000 };

  it("centres the window on the instant, keeping its span", () => {
    expect(centredOn({ t0: 0, t1: 100 }, 500, extent)).toEqual({ t0: 450, t1: 550 });
  });

  it("stops at the extent rather than scrolling past the data", () => {
    expect(centredOn({ t0: 0, t1: 100 }, 0, extent)).toEqual({ t0: 0, t1: 100 });
    expect(centredOn({ t0: 0, t1: 100 }, 1000, extent)).toEqual({ t0: 900, t1: 1000 });
  });

  it("keeps a window wider than the extent pinned to its start", () => {
    expect(centredOn({ t0: 0, t1: 5000 }, 500, extent)).toEqual({ t0: 0, t1: 5000 });
  });
});

describe("gestures", () => {
  const loadTwoYears = (): void => {
    panel.loadSnapshot({
      nodes: [
        node({ node_id: "n1", created_at: "2024-01-01T00:00:00Z" }),
        node({ node_id: "n2", created_at: "2026-01-01T00:00:00Z" }),
      ],
      edges: [],
    });
  };

  const wheel = (init: WheelEventInit): void => {
    document.querySelector("#body svg")!.dispatchEvent(
      new WheelEvent("wheel", { bubbles: true, cancelable: true, ...init }),
    );
  };

  /** Where the first mark sits, as a proxy for the visible window. */
  const firstMarkY = (): number => Number(marks()[0].getAttribute("cy"));

  it("pans on a bare wheel", () => {
    loadTwoYears();
    // Zoom in first: with the whole extent on screen there is nowhere to pan.
    wheel({ deltaY: -120, metaKey: true });
    const before = firstMarkY();

    wheel({ deltaY: 120 });

    expect(firstMarkY()).not.toBe(before);
  });

  it("zooms on ⌘-wheel, not pans", () => {
    // The two are distinguishable: zooming in spreads the marks apart, panning
    // slides them together.
    loadTwoYears();
    const spreadBefore = Number(marks()[1].getAttribute("cy")) - firstMarkY();

    wheel({ deltaY: -120, metaKey: true });

    const spreadAfter = Number(marks()[1].getAttribute("cy")) - firstMarkY();
    expect(spreadAfter).toBeGreaterThan(spreadBefore);
  });

  it("returns to the reference time on demand", () => {
    loadTwoYears();
    wheel({ deltaY: -120, metaKey: true });
    wheel({ deltaY: -600 });
    const panned = firstMarkY();

    click(el("now"));

    // Real "now" is past the last node, so this scrolls to the recent end.
    expect(firstMarkY()).not.toBe(panned);
  });
});

describe("expand on select", () => {
  const loadOne = (): void => {
    panel.loadSnapshot({
      nodes: [
        node({
          node_id: "n1",
          content:
            "The SurrealDB container was recreated with an on-disk rocksdb " +
            "backend on a named Docker volume, so its data survives a restart.",
        }),
        node({ node_id: "n2", created_at: "2024-02-01T00:00:00Z" }),
      ],
      edges: [],
    });
  };

  const card = (): SVGTextElement | null =>
    document.querySelector<SVGTextElement>("#body text.cursor-pointer tspan")
      ?.parentElement as unknown as SVGTextElement | null;

  it("shows one line per mark until something is selected", () => {
    loadOne();
    expect(document.querySelectorAll("#body tspan")).toHaveLength(0);
  });

  it("expands the selected mark's text in place", () => {
    loadOne();
    click(marks()[0]);

    const lines = document.querySelectorAll("#body tspan");
    expect(lines.length).toBeGreaterThan(1);
    // The dates come with it — that is what the drawer was being opened for.
    expect(card()?.textContent).toContain("created");
  });

  it("does not move any mark to make room", () => {
    // The whole reason the card lives in the label column: position on the
    // axis means time, and expanding there would put marks where their
    // timestamps do not.
    loadOne();
    const before = marks().map((m) => m.getAttribute("cy") ?? m.getAttribute("y"));

    click(marks()[0]);

    expect(marks().map((m) => m.getAttribute("cy") ?? m.getAttribute("y"))).toEqual(
      before,
    );
  });

  it("collapses again when the same mark is clicked", () => {
    loadOne();
    click(marks()[0]);
    expect(document.querySelectorAll("#body tspan").length).toBeGreaterThan(1);

    click(marks()[0]);

    expect(document.querySelectorAll("#body tspan")).toHaveLength(0);
  });

  it("keeps the selected card even when the column is full", () => {
    // The card is passed to the layout first, which makes it the highest
    // priority — the one label the reader asked for is never the one dropped.
    panel.loadSnapshot({
      nodes: Array.from({ length: 60 }, (_, i) =>
        node({
          node_id: `n${i}`,
          created_at: new Date(Date.UTC(2024, 0, 1, 0, i)).toISOString(),
          content: `node number ${i} with a reasonably long description`,
        }),
      ),
      edges: [],
    });
    click(marks()[30]);

    expect(document.querySelectorAll("#body tspan").length).toBeGreaterThan(1);
    expect(el("body").textContent).toContain("labels hidden");
  });
});

describe("the expanded card's geometry", () => {
  it("stays inside the panel on either side", () => {
    panel.loadSnapshot({
      nodes: [
        node({
          node_id: "fact",
          node_type: "fact",
          content: "x".repeat(400),
          created_at: "2024-01-01T00:00:00Z",
        }),
        node({
          node_id: "inference",
          node_type: "inference",
          content: "y".repeat(400),
          created_at: "2024-06-01T00:00:00Z",
        }),
      ],
      edges: [],
    });

    for (const mark of [marks()[0], marks()[1]]) {
      click(mark);
      const card = document.querySelector<SVGRectElement>("#body rect[rx='3']")!;
      const x = Number(card.getAttribute("x"));
      expect(x).toBeGreaterThanOrEqual(0);
      expect(x + Number(card.getAttribute("width"))).toBeLessThanOrEqual(600);
      click(mark);
    }
  });
});

// --- Valid time (§13) ---

const CLOCK = "tl-westminster";

const stated = (iso: string, label: string | null = null): ImpreciseInstantView => ({
  instant_kind: "precise",
  at: iso,
  label,
});

const UNKNOWN_EDGE: ImpreciseInstantView = { instant_kind: "unknown" };
const NO_EDGE: ImpreciseInstantView = { instant_kind: "unbounded" };
const NAMED_EDGE: ImpreciseInstantView = {
  instant_kind: "named",
  label: "the Renaissance",
};

const period = (over: Partial<ValidityIntervalView> = {}): ValidityIntervalView => ({
  start: stated("1997-05-02T00:00:00Z"),
  end: stated("2010-05-11T00:00:00Z"),
  timeline_id: null,
  witnessed_at: null,
  basis: "stated",
  ...over,
});

const sourced = (src: string, dst: string, validity: ValidityIntervalView[]): EdgeView =>
  edge({ src_id: src, dst_id: dst, edge_type: "sourced_from", validity });

/** A date reflect read off the claim on the other side of a succession. */
const offered = (over: Partial<BoundaryProposalView> = {}): BoundaryProposalView => ({
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

const linked = (src: string, timepointId: string): EdgeView =>
  edge({
    src_id: src,
    dst_id: CLOCK,
    edge_type: "timelink",
    metadata: { timepoint_id: timepointId },
  });

const follows = (from: string, to: string): EdgeView =>
  edge({ src_id: from, dst_id: to, edge_type: "temporally_followed_by" });

/**
 * A timeline running 1990 to 2026, so the domain holds the periods below
 * whatever the filters do to the marks.
 */
const showValidity = (
  edges: EdgeView[],
  facts: NodeView[] = [node({ node_id: "f1", content: "Labour is in government" })],
  over: Partial<TimelineView> = {},
  proposals: BoundaryProposalView[] = [],
): void => {
  useContentMode();
  panel.loadSnapshot({
    nodes: [
      ...facts,
      node({ node_id: "doc-a", node_type: "document", content: "almanac, 2011" }),
      node({ node_id: "doc-b", node_type: "document", content: "a blog" }),
    ],
    edges,
    boundary_proposals: proposals,
    timelines: [
      timeline({
        timeline_id: CLOCK,
        name: "Westminster",
        timepoints: [
          timepoint("tp-early", "1990-01-01T00:00:00Z", "the record opens"),
          timepoint("tp-late", "2026-01-01T00:00:00Z", "the record closes"),
        ],
        ...over,
      }),
    ],
  });
};

const found = <T extends SVGElement>(selector: string): T[] => [
  ...document.querySelectorAll<T>(`#body ${selector}`),
];

const strips = (): SVGRectElement[] => found<SVGRectElement>("rect.timeline-strip");

const box = (element: SVGElement): { top: number; bottom: number } => ({
  top: Number(element.getAttribute("y")),
  bottom: Number(element.getAttribute("y")) + Number(element.getAttribute("height")),
});

/** Where a rotated label is pinned, read back out of its transform. */
const anchorOf = (element: SVGElement): { x: number; y: number } => {
  const at = /translate\(([-\d.]+),\s*([-\d.]+)\)/.exec(
    element.getAttribute("transform") ?? "",
  );
  return { x: Number(at?.[1]), y: Number(at?.[2]) };
};

const anchorX = (element: SVGElement): number => anchorOf(element).x;
const anchorY = (element: SVGElement): number => anchorOf(element).y;

describe("per-source validity strips", () => {
  it("draws a strip for every source and period, and no union bar", () => {
    showValidity([
      linked("f1", "tp-early"),
      sourced("f1", "doc-a", [period()]),
      sourced("f1", "doc-b", [period({ start: stated("1995-05-01T00:00:00Z") })]),
    ]);

    expect(strips()).toHaveLength(2);
    // Two sources, two lanes: the disagreement about where this episode began
    // is what a merged bar would erase.
    expect(new Set(strips().map((s) => s.getAttribute("x"))).size).toBe(2);
    expect(found("rect.timeline-envelope")).toHaveLength(0);
  });

  it("keeps several periods from one source in that source's lane", () => {
    showValidity([
      linked("f1", "tp-early"),
      sourced("f1", "doc-a", [
        period(),
        period({ start: stated("2024-07-05T00:00:00Z"), end: UNKNOWN_EDGE }),
      ]),
    ]);

    expect(strips()).toHaveLength(2);
    expect(new Set(strips().map((s) => s.getAttribute("x"))).size).toBe(1);
  });

  it("names each lane after its source, on the strip itself", () => {
    showValidity([
      linked("f1", "tp-early"),
      sourced("f1", "doc-a", [period()]),
      sourced("f1", "doc-b", [period()]),
    ]);

    const names = found("text.timeline-strip-label").map((t) => t.textContent);
    expect(names).toContain("almanac, 2011");
    expect(names).toContain("a blog");
  });

  it("steps the side text out past the lanes", () => {
    showValidity([linked("f1", "tp-early")]);
    const bare = Math.min(...labels().map((t) => Number(t.getAttribute("x"))));

    showValidity([linked("f1", "tp-early"), sourced("f1", "doc-a", [period()])]);
    const beside = Math.min(...labels().map((t) => Number(t.getAttribute("x"))));

    expect(beside).toBeLessThan(bare);
  });

  it("counts the strips a side has no lane left for", () => {
    showValidity([
      linked("f1", "tp-early"),
      ...["doc-a", "doc-b", "doc-c", "doc-d"].map((doc) =>
        sourced("f1", doc, [period()]),
      ),
    ]);

    expect(strips()).toHaveLength(3);
    expect(el("body").textContent).toContain("1 source strip hidden");
  });

  it("draws nothing in record time, which measures a different clock", () => {
    showValidity([linked("f1", "tp-early"), sourced("f1", "doc-a", [period()])]);
    controls.modeSelect.value = "record";
    change(controls.modeSelect);

    expect(strips()).toHaveLength(0);
  });
});

describe("the mark each endpoint kind gets", () => {
  const withPeriod = (over: Partial<ValidityIntervalView>): void =>
    showValidity([linked("f1", "tp-early"), sourced("f1", "doc-a", [period(over)])]);

  it("caps a stated edge", () => {
    withPeriod({});

    expect(found("line.timeline-strip-cap")).toHaveLength(2);
    expect(found("rect.timeline-strip-fade")).toHaveLength(0);
  });

  it("dissolves an unknown edge", () => {
    withPeriod({ end: UNKNOWN_EDGE });

    const fade = found<SVGRectElement>("rect.timeline-strip-fade");
    expect(fade).toHaveLength(1);
    expect(fade[0].getAttribute("mask")).toBe("url(#timeline-fade-later)");
    expect(found("path.timeline-strip-exit")).toHaveLength(0);
  });

  it("runs an unbounded edge off the panel at full weight", () => {
    withPeriod({ end: NO_EDGE });

    expect(found("path.timeline-strip-exit")).toHaveLength(1);
    // The distinction the endpoint type exists to keep: no fog where there is
    // no edge to be uncertain about (§13.2 rule 2).
    expect(found("rect.timeline-strip-fade")).toHaveLength(0);
  });

  it("hatches an edge resolved from the source's own words", () => {
    withPeriod({ start: stated("1997-05-02T00:00:00Z", "the second Blair ministry") });

    const soft = found<SVGRectElement>("rect.timeline-strip-soft");
    expect(soft).toHaveLength(1);
    expect(soft[0].getAttribute("fill")).toBe("url(#timeline-strip-hatch-0)");
  });

  it("draws a named edge as a stub at full weight, with no tick and no fog", () => {
    // §13.1: the edge is a word, and the word is drawn where the edge would be.
    withPeriod({ end: NAMED_EDGE });

    const stub = found<SVGRectElement>("rect.timeline-strip-named");
    expect(stub).toHaveLength(1);
    expect(stub[0].getAttribute("fill-opacity")).toBe(
      strips()[0].getAttribute("fill-opacity"),
    );
    expect(stub[0].getAttribute("mask")).toBeNull();
    // Square: the stub ends where it ends, without a cap saying a date is there.
    expect(stub[0].getAttribute("rx")).toBeNull();
    expect(found("rect.timeline-strip-fade")).toHaveLength(0);
    expect(found("line.timeline-strip-cap")).toHaveLength(1);
  });

  it("writes the stored words at the end of the stub, verbatim and in mono", () => {
    withPeriod({ end: NAMED_EDGE });

    const stub = box(found<SVGRectElement>("rect.timeline-strip-named")[0]);
    const label = found<SVGTextElement>("text.timeline-strip-named-label");
    expect(label).toHaveLength(1);
    expect(label[0].textContent).toBe("the Renaissance");
    expect(label[0].getAttribute("font-family")).toContain("mono");
    expect(anchorY(label[0])).toBeCloseTo(stub.bottom);
  });

  it("puts the stub above the body when the start is the named edge", () => {
    withPeriod({ start: NAMED_EDGE });

    const body = box(strips()[0]);
    const stub = box(found<SVGRectElement>("rect.timeline-strip-named")[0]);
    expect(stub.bottom).toBeCloseTo(body.top);
    expect(anchorY(found<SVGTextElement>("text.timeline-strip-named-label")[0])).toBeCloseTo(
      stub.top,
    );
  });

  it("keeps the stub inside its own lane, clear of the next one's strip", () => {
    showValidity([
      linked("f1", "tp-early"),
      sourced("f1", "doc-a", [period({ end: NAMED_EDGE })]),
      sourced("f1", "doc-b", [period()]),
    ]);

    const label = found<SVGTextElement>("text.timeline-strip-named-label")[0];
    const lane = Number(
      found<SVGRectElement>("rect.timeline-strip-named")[0].getAttribute("x"),
    );
    const neighbour = strips()
      .map((s) => Number(s.getAttribute("x")))
      .filter((x) => x !== lane);

    // Rotated into its lane's own column, the way a lane wears its source name.
    expect(label.getAttribute("transform")).toContain("rotate(");
    for (const x of neighbour) expect(Math.abs(anchorX(label) - x)).toBeGreaterThan(9);
  });

  it("marks a witnessed moment with a dot and a halo", () => {
    withPeriod({ witnessed_at: stated("2001-06-07T00:00:00Z") });

    const dot = found("circle.timeline-witness");
    const halo = found("circle.timeline-witness-halo");
    expect(dot).toHaveLength(1);
    expect(halo).toHaveLength(1);
    expect(dot[0].getAttribute("cy")).toBe(halo[0].getAttribute("cy"));
  });

  it("collapses onto the witness when neither edge is known", () => {
    withPeriod({
      start: UNKNOWN_EDGE,
      end: UNKNOWN_EDGE,
      witnessed_at: stated("2001-06-07T00:00:00Z"),
    });

    const fades = found<SVGRectElement>("rect.timeline-strip-fade");
    expect(fades).toHaveLength(2);
    const dot = Number(found("circle.timeline-witness")[0].getAttribute("cy"));
    expect(box(fades[0]).bottom).toBeCloseTo(dot);
    expect(box(fades[1]).top).toBeCloseTo(dot);
  });

  it("draws a stated period solid and an inferred one hollow and dashed", () => {
    showValidity([
      linked("f1", "tp-early"),
      sourced("f1", "doc-a", [period()]),
      sourced("f1", "doc-b", [period({ basis: "inferred" })]),
    ]);

    const [hollow, solid] = [
      strips().find((s) => s.classList.contains("timeline-strip-inferred"))!,
      strips().find((s) => !s.classList.contains("timeline-strip-inferred"))!,
    ];
    expect(hollow.getAttribute("stroke-dasharray")).toBe("3 2");
    expect(solid.getAttribute("stroke-dasharray")).toBeNull();
    expect(Number(hollow.getAttribute("fill-opacity"))).toBeLessThan(
      Number(solid.getAttribute("fill-opacity")),
    );
  });
});

describe("the rules a reasonable rendering would break", () => {
  it("leaves the gap between two periods empty", () => {
    // Open world: outside a stated interval is no assertion, so anything drawn
    // across the gap would be a claim nobody made (§13.2 rule 1).
    showValidity([
      linked("f1", "tp-early"),
      sourced("f1", "doc-a", [
        period(),
        period({ start: stated("2024-07-05T00:00:00Z"), end: UNKNOWN_EDGE }),
      ]),
    ]);

    const [first, second] = strips().map(box);
    const midGap = (first.bottom + second.top) / 2;
    // Every filled shape the grammar draws: two bodies and the fog past the
    // open end of the second period, and nothing else.
    const drawn = found<SVGRectElement>("rect").filter((r) =>
      [...r.classList].some((name) => name.startsWith("timeline-strip")),
    );
    expect(drawn).toHaveLength(3);
    expect(drawn.some((r) => box(r).top <= midGap && box(r).bottom >= midGap)).toBe(
      false,
    );

    const spine = found("line.timeline-claim-spine");
    expect(spine).toHaveLength(1);
    expect(spine[0].getAttribute("stroke-dasharray")).toBe("1 3");
  });

  it("fades through the now-line rather than stopping at it", () => {
    showValidity(
      [
        linked("f1", "tp-early"),
        sourced("f1", "doc-a", [
          period({
            start: stated("2024-01-01T00:00:00Z"),
            end: UNKNOWN_EDGE,
            // The timeline states a present, so it keeps its own clock and a
            // period drawn on it has to name that clock.
            timeline_id: CLOCK,
          }),
        ]),
      ],
      undefined,
      { reference_time: "2024-06-01T00:00:00Z" },
    );

    const rule = found<SVGLineElement>("line").find(
      (l) => l.getAttribute("stroke-dasharray") === "4 3",
    )!;
    const now = Number(rule.getAttribute("y1"));
    const fade = box(found("rect.timeline-strip-fade")[0]);

    expect(fade.top).toBeLessThan(now);
    expect(fade.bottom).toBeGreaterThan(now);
  });

  it("drains a strip the retrieval did not return, and keeps it drawn", () => {
    // Focus owns saturation, status owns opacity, so "not returned" and "no
    // longer current" never arrive at the same appearance.
    showValidity([linked("f1", "tp-early"), sourced("f1", "doc-a", [period()])]);
    const lit = strips()[0].getAttribute("fill");

    panel.setFocus(["somebody-else"]);

    expect(strips()).toHaveLength(1);
    expect(strips()[0].getAttribute("fill")).not.toBe(lit);
    expect(strips()[0].getAttribute("fill-opacity")).toBe("0.85");
  });

  it("keeps a historical claim on the panel, at lower volume", () => {
    showValidity(
      [linked("f1", "tp-early"), sourced("f1", "doc-a", [period()])],
      [node({ node_id: "f1", status: "historical" })],
    );

    expect(strips()).toHaveLength(1);
    expect(strips()[0].classList.contains("timeline-strip-historical")).toBe(true);
    expect(Number(strips()[0].getAttribute("fill-opacity"))).toBeLessThan(0.85);
  });

  it("hides a corrected claim until the status filter asks for it", () => {
    showValidity(
      [
        linked("f1", "tp-early"),
        linked("f1", "tp-late"),
        sourced("f1", "doc-a", [period()]),
      ],
      [node({ node_id: "f1", status: "corrected" })],
    );

    expect(strips()).toHaveLength(0);

    controls.statusSelect.value = "corrected";
    change(controls.statusSelect);

    expect(strips()).toHaveLength(1);
    // Summoned, not believed.
    expect(found("line.timeline-strip-struck")).toHaveLength(1);
  });

  it("puts no flag on a premise", () => {
    // A soundness flag belongs on the inference that drew the conclusion, not
    // on the facts it drew it from (§13.2 rule 8).
    showValidity([linked("f1", "tp-early"), sourced("f1", "doc-a", [period()])]);

    const pending = semanticPaletteFor("light").pending;
    const flagged = found("*").some(
      (e) => e.getAttribute("fill") === pending || e.getAttribute("stroke") === pending,
    );
    expect(flagged).toBe(false);
  });
});

describe("periods with no place on this axis", () => {
  it("sends a label nothing else places to the tray, words intact", () => {
    showValidity([
      linked("f1", "tp-early"),
      sourced("f1", "doc-a", [
        period({
          start: { instant_kind: "named", label: "under the USSR" },
          end: UNKNOWN_EDGE,
        }),
      ]),
    ]);

    expect(strips()).toHaveLength(0);
    const chips = [...document.querySelectorAll("#undated .timeline-validity-chip")];
    expect(chips).toHaveLength(1);
    expect(chips[0].textContent).toContain("under the USSR");
  });

  it("draws an interval a date places and leaves the tray empty", () => {
    showValidity([
      linked("f1", "tp-early"),
      sourced("f1", "doc-a", [period({ start: NAMED_EDGE })]),
    ]);

    expect(strips()).toHaveLength(1);
    expect(found("rect.timeline-strip-named")).toHaveLength(1);
    expect(
      [...document.querySelectorAll("#undated .timeline-validity-chip")],
    ).toHaveLength(0);
  });

  it("sends a claim measured on another clock to the tray, with a clock badge", () => {
    showValidity([
      linked("f1", "tp-early"),
      sourced("f1", "doc-a", [period({ timeline_id: "in-universe" })]),
    ]);

    const chips = [...document.querySelectorAll("#undated .timeline-validity-chip")];
    expect(strips()).toHaveLength(0);
    expect(chips).toHaveLength(1);
    expect(chips[0].textContent).toContain("⧗");
  });

  it("draws a claim that names this timeline's own clock", () => {
    showValidity([
      linked("f1", "tp-early"),
      sourced("f1", "doc-a", [period({ timeline_id: CLOCK })]),
    ]);

    expect(strips()).toHaveLength(1);
  });

  it("sends a real-world claim to the tray on a timeline with its own present", () => {
    // A timeline that states a present keeps its own clock, so a period
    // measured against the wall clock asserts a mapping nobody made.
    showValidity(
      [linked("f1", "tp-early"), sourced("f1", "doc-a", [period()])],
      undefined,
      { reference_time: "1850-01-01T00:00:00Z" },
    );

    const chips = [...document.querySelectorAll("#undated .timeline-validity-chip")];
    expect(strips()).toHaveLength(0);
    expect(chips).toHaveLength(1);
    expect(chips[0].textContent).toContain("⧗");
  });

  it("draws a claim that names that timeline, present or no present", () => {
    showValidity(
      [linked("f1", "tp-early"), sourced("f1", "doc-a", [period({ timeline_id: CLOCK })])],
      undefined,
      { reference_time: "1850-01-01T00:00:00Z" },
    );

    expect(strips()).toHaveLength(1);
  });
});


describe("a boundary reflect proposes", () => {
  const open = (): EdgeView[] => [
    linked("f1", "tp-early"),
    sourced("f1", "doc-a", [period({ end: UNKNOWN_EDGE })]),
  ];

  it("runs a hollow dashed extension out to a dashed cap, with a chip", () => {
    showValidity(open(), undefined, {}, [offered()]);

    const extension = found<SVGRectElement>("rect.timeline-strip-proposed");
    expect(extension).toHaveLength(1);
    // Hollow and dashed: the reading it would get once accepted, since an
    // accepted boundary makes the interval inferred (§13.2 rule 9).
    expect(extension[0].getAttribute("stroke-dasharray")).toBe("4 3");
    expect(extension[0].getAttribute("stroke")).toBe(
      semanticPaletteFor("light").pending,
    );
    expect(extension[0].getAttribute("fill")).toBe(
      semanticPaletteFor("light").pendingSurface,
    );
    expect(found("line.timeline-strip-proposed-cap")).toHaveLength(1);
    const chip = found<SVGGElement>("g.timeline-strip-proposed-chip");
    expect(chip).toHaveLength(1);
    expect(chip[0].querySelector("text")!.textContent).toBe("proposed · review");
  });

  it("replaces the fade that edge would otherwise wear", () => {
    // One mark per endpoint: fog and an offer never share an edge.
    showValidity(open(), undefined, {}, [offered()]);

    expect(found("rect.timeline-strip-fade")).toHaveLength(0);
  });

  it("leaves the solid body where the record's own dates put it", () => {
    showValidity(open());
    const bare = box(strips()[0]);

    showValidity(open(), undefined, {}, [offered()]);

    expect(box(strips()[0])).toEqual(bare);
  });

  it("ends the extension at the proposed date, past the body", () => {
    // A dated witness gives the body real height, so the joint between the two
    // is a coordinate rather than the one-pixel floor a collapsed bar gets.
    showValidity(
      [
        linked("f1", "tp-early"),
        sourced("f1", "doc-a", [
          period({ end: UNKNOWN_EDGE, witnessed_at: stated("2005-06-01T00:00:00Z") }),
        ]),
      ],
      undefined,
      {},
      [offered()],
    );

    const body = box(strips()[0]);
    const extension = box(found<SVGRectElement>("rect.timeline-strip-proposed")[0]);
    const cap = Number(
      found<SVGLineElement>("line.timeline-strip-proposed-cap")[0].getAttribute("y1"),
    );

    expect(extension.top).toBeCloseTo(body.bottom);
    expect(extension.bottom).toBeGreaterThan(body.bottom);
    expect(cap).toBeCloseTo(extension.bottom);
  });

  it("draws none of it when nothing has been offered", () => {
    showValidity(open());

    expect(found("rect.timeline-strip-proposed")).toHaveLength(0);
    expect(found("line.timeline-strip-proposed-cap")).toHaveLength(0);
    expect(found("g.timeline-strip-proposed-chip")).toHaveLength(0);
    expect(found("rect.timeline-strip-fade")).toHaveLength(1);
  });

  it("carries the record behind the offer, the claim it was read from included", () => {
    showValidity(open(), undefined, {}, [offered()]);

    const detail = found("rect.timeline-strip-proposed")[0].querySelector("title")!
      .textContent!;
    expect(detail).toContain("proposed 2018-01-01T00:00:00Z from f2");
  });

  it("makes the envelope reach the proposed date", () => {
    // The envelope is taken from what is drawn, so an extension past the body
    // has to move it: a summary that stopped short would claim an edge the
    // strips inside it left open.
    showValidity(
      [
        linked("f1", "tp-early"),
        sourced("f1", "doc-a", [period({ end: UNKNOWN_EDGE })]),
        sourced("f1", "doc-b", [period()]),
      ],
      undefined,
      {},
      [offered()],
    );
    click(strips()[0]);

    const envelope = box(found<SVGRectElement>("rect.timeline-envelope")[0]);
    const cap = Number(
      found<SVGLineElement>("line.timeline-strip-proposed-cap")[0].getAttribute("y1"),
    );
    expect(envelope.bottom).toBeGreaterThanOrEqual(cap);
  });

  it("badges a tray chip whose period the offer cannot place", () => {
    // An offer is not a date the graph holds, so the period stays in the tray
    // and the badge says there is something there to review (§13.4).
    showValidity(
      [
        linked("f1", "tp-early"),
        sourced("f1", "doc-a", [period({ start: UNKNOWN_EDGE, end: UNKNOWN_EDGE })]),
      ],
      undefined,
      {},
      [offered()],
    );

    const chips = [...document.querySelectorAll("#undated .timeline-validity-chip")];
    expect(strips()).toHaveLength(0);
    expect(chips).toHaveLength(1);
    expect(chips[0].querySelector(".timeline-validity-chip-proposed")!.textContent).toBe(
      "proposed",
    );
  });

  it("leaves an unoffered tray chip unbadged", () => {
    showValidity([
      linked("f1", "tp-early"),
      sourced("f1", "doc-a", [period({ start: UNKNOWN_EDGE, end: UNKNOWN_EDGE })]),
    ]);

    const chips = [...document.querySelectorAll("#undated .timeline-validity-chip")];
    expect(chips).toHaveLength(1);
    expect(chips[0].querySelector(".timeline-validity-chip-proposed")).toBeNull();
  });
});

describe("the record behind a strip", () => {
  it("says the source, both edges, the basis, the witness and the clock", () => {
    showValidity([
      linked("f1", "tp-early"),
      sourced("f1", "doc-a", [
        period({
          end: UNKNOWN_EDGE,
          witnessed_at: stated("2001-06-07T00:00:00Z"),
          basis: "inferred",
        }),
      ]),
    ]);

    const detail = strips()[0].querySelector("title")!.textContent!;
    expect(detail).toContain("almanac, 2011");
    expect(detail).toContain("1997-05-02T00:00:00Z");
    // The word, never a date invented for it.
    expect(detail).toContain("unknown");
    expect(detail).toContain("inferred");
    expect(detail).toContain("2001-06-07T00:00:00Z");
    expect(detail).toContain("default wall clock");
  });

  it("summarises a fact's sources as a hollow envelope, on demand", () => {
    showValidity([
      linked("f1", "tp-early"),
      sourced("f1", "doc-a", [period()]),
      sourced("f1", "doc-b", [period({ start: stated("1995-05-01T00:00:00Z") })]),
    ]);
    click(strips()[0]);

    const envelope = found<SVGRectElement>("rect.timeline-envelope");
    expect(envelope).toHaveLength(1);
    // Outline only, so it can never be read as a period somebody stated.
    expect(envelope[0].getAttribute("fill")).toBe("none");
    expect(envelope[0].querySelector("title")!.textContent).toBe("any source asserts");
  });
});

describe("temporally_followed_by", () => {
  const naming = (): EdgeView[] => [
    linked("f1", "tp-early"),
    linked("f2", "tp-late"),
    sourced("f1", "doc-a", [period()]),
    sourced("f2", "doc-b", [
      period({ start: stated("2010-05-11T00:00:00Z"), end: UNKNOWN_EDGE }),
    ]),
  ];

  const two = (): NodeView[] => [
    node({ node_id: "f1", content: "Labour is in government" }),
    node({ node_id: "f2", content: "the Coalition is in government" }),
  ];

  it("elbows from one claim's end to the next one's start", () => {
    showValidity([...naming(), follows("f1", "f2")], two());

    expect(found("path.timeline-succession")).toHaveLength(1);
    expect(found("circle.timeline-succession-dot")).toHaveLength(1);
  });

  it("draws each step of a cycle once and stops", () => {
    // Recurrence makes a cycle legal for this edge, so the walk has to end.
    showValidity(
      [
        linked("f1", "tp-early"),
        linked("f2", "tp-early"),
        linked("f3", "tp-late"),
        sourced("f1", "doc-a", [period()]),
        sourced("f2", "doc-b", [period({ start: stated("2011-01-01T00:00:00Z") })]),
        sourced("f3", "doc-a", [period({ start: stated("2015-01-01T00:00:00Z") })]),
        follows("f1", "f2"),
        follows("f2", "f3"),
        follows("f3", "f1"),
      ],
      [...two(), node({ node_id: "f3", content: "a third turn" })],
    );

    expect(found("path.timeline-succession")).toHaveLength(3);
  });

  it("leaves out a step whose other end has nothing drawn", () => {
    showValidity([...naming().slice(0, 3), follows("f1", "f2")], two());

    expect(found("path.timeline-succession")).toHaveLength(0);
  });
});

describe("recurrence beads beside the strips", () => {
  it("keeps drawing a rule's occurrences", () => {
    showValidity(
      [linked("f1", "tp-early"), sourced("f1", "doc-a", [period()])],
      undefined,
      {
        recurrences: [
          recurrence({
            recurrence_id: "r1",
            occurrences: [
              occurrence({ occurrence_start: "2000-01-01T00:00:00Z" }),
              occurrence({ occurrence_start: "2001-01-01T00:00:00Z" }),
            ],
          }),
        ],
      },
    );

    expect(found("circle.timeline-bead")).toHaveLength(2);
    expect(strips()).toHaveLength(1);
  });
});
