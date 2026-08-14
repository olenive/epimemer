// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { EventRouter } from "./events";
import { paletteFor } from "./theme";
import type { TimelineMark } from "./timeline-model";
import {
  centredOn,
  extentIncluding,
  initTimelinePanel,
  referenceTimeFor,
  type TimelinePanelControls,
  type TimelinePanelHandle,
} from "./timeline-panel";
import type { AnyEvent, EdgeView, NodeView, TimelineView } from "./types";

/** jsdom has no ResizeObserver, and the panel observes its row container. */
class StubResizeObserver {
  observe(): void {}
  disconnect(): void {}
}
vi.stubGlobal("ResizeObserver", StubResizeObserver);

const MARKUP = `
  <select id="mode"><option value="record">r</option><option value="content">c</option></select>
  <select id="type"><option value="all">all</option><option value="fact">fact</option><option value="topic">topic</option></select>
  <select id="status"><option value="all">all</option><option value="active">active</option><option value="superseded">superseded</option></select>
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
  created_at: "2024-01-01T00:00:00Z",
  graph: "default",
  metadata: {},
  ...over,
});

const timepoint = (id: string, start: string | null, label: string | null) => ({
  timepoint_id: id,
  start,
  end: null,
  label,
  metadata: {},
});

const timeline = (over: Partial<TimelineView> & { timeline_id: string }): TimelineView => ({
  name: "History",
  description: "",
  timepoints: [],
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

  it("offers the frames present in the data as options", () => {
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
