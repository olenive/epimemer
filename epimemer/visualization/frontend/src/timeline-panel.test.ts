// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { EventRouter } from "./events";
import type { TimelineMark } from "./timeline-model";
import {
  initTimelinePanel,
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
  <button id="reset"></button>
  <div id="rows"></div>
  <div id="empty" class="hidden"></div>
`;

// --- Fixtures ---

const node = (over: Partial<NodeView> & { node_id: string }): NodeView => ({
  node_type: "fact",
  content: "a fact",
  status: "active",
  source_id: "s1",
  extraction_method: "agent",
  novelty: 0.5,
  confidence: 0.9,
  relevance: 0.5,
  last_reinforced: "2024-01-01T00:00:00Z",
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
  rows: el("rows"),
  empty: el("empty"),
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
  // jsdom lays nothing out, and a zero width means the panel draws no axis.
  Object.defineProperty(el("rows"), "clientWidth", { value: 600, configurable: true });

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
  ...document.querySelectorAll<SVGElement>("#rows circle, #rows rect.cursor-pointer"),
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
  it("draws a row per node type", () => {
    panel.loadSnapshot({
      nodes: [
        node({ node_id: "n1", node_type: "fact" }),
        node({ node_id: "n2", node_type: "topic" }),
      ],
      edges: [],
    });

    expect(el("rows").textContent).toContain("Facts");
    expect(el("rows").textContent).toContain("Topics");
    expect(marks()).toHaveLength(2);
  });

  it("draws an axis with tick labels", () => {
    panel.loadSnapshot({
      nodes: [
        node({ node_id: "n1", created_at: "2024-01-01T00:00:00Z" }),
        node({ node_id: "n2", created_at: "2024-06-01T00:00:00Z" }),
      ],
      edges: [],
    });

    expect(document.querySelectorAll("#rows line").length).toBeGreaterThan(0);
    expect(document.querySelectorAll("#rows text").length).toBeGreaterThan(0);
  });
});

describe("content mode", () => {
  it("draws a row per timeline", () => {
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

    expect(el("rows").textContent).toContain("History of AI");
    expect(marks()).toHaveLength(1);
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
    expect(el("rows").textContent).toContain("undated");
    expect(el("rows").textContent).toContain("during the Renaissance");
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

    expect(document.querySelectorAll("#rows rect.cursor-pointer")).toHaveLength(1);
    expect(document.querySelectorAll("#rows circle")).toHaveLength(1);
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
    expect(el("rows").textContent).not.toContain("Topics");
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

    click(document.querySelector("#rows button")!);

    expect(selected.at(-1)?.id).toBe("p1");
  });
});

describe("live events", () => {
  it("adds a timeline that arrives after load", () => {
    panel.loadSnapshot({ nodes: [], edges: [], timelines: [] });
    useContentMode();
    expect(el("rows").textContent).not.toContain("Late arrival");

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

    expect(el("rows").textContent).toContain("Late arrival");
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

    expect(document.querySelectorAll("#rows > div")).toHaveLength(1);
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
