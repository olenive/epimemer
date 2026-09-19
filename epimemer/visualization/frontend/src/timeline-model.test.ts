import { describe, expect, it } from "vitest";

import {
  allMarks,
  buildContentRows,
  buildRecordRows,
  buildRows,
  RECORD_ROW_ID,
  sideForTypes,
  type SnapshotLike,
} from "./timeline-model";
import type {
  EdgeView,
  NodeView,
  OccurrenceView,
  RecurrenceView,
  TimelineView,
  TimepointView,
} from "./types";

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

const point = (over: Partial<TimepointView> & { timepoint_id: string }): TimepointView => ({
  start: null,
  end: null,
  label: null,
  kind: over.start ? (over.end ? "interval" : "instant") : "vague",
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

describe("buildContentRows", () => {
  it("makes one row per timeline", () => {
    const snapshot: SnapshotLike = {
      nodes: [],
      edges: [],
      timelines: [
        timeline({ timeline_id: "t1", name: "History of AI" }),
        timeline({ timeline_id: "t2", name: "Renaissance" }),
      ],
    };

    expect(buildContentRows(snapshot).map((r) => r.name)).toEqual([
      "History of AI",
      "Renaissance",
    ]);
  });

  it("separates dated from undated timepoints", () => {
    const snapshot: SnapshotLike = {
      nodes: [],
      edges: [],
      timelines: [
        timeline({
          timeline_id: "t1",
          timepoints: [
            point({ timepoint_id: "p1", start: "2024-01-01T00:00:00Z" }),
            point({ timepoint_id: "p2", label: "during the Renaissance" }),
          ],
        }),
      ],
    };

    const [row] = buildContentRows(snapshot);
    expect(row.dated.map((m) => m.id)).toEqual(["p1"]);
    expect(row.undated.map((m) => m.id)).toEqual(["p2"]);
    expect(row.undated[0].start).toBeNull();
  });

  it("keeps undated timepoints in the order the timeline lists them", () => {
    const snapshot: SnapshotLike = {
      nodes: [],
      edges: [],
      timelines: [
        timeline({
          timeline_id: "t1",
          timepoints: [
            point({ timepoint_id: "p1", label: "first" }),
            point({ timepoint_id: "p2", label: "second" }),
            point({ timepoint_id: "p3", label: "third" }),
          ],
        }),
      ],
    };

    expect(buildContentRows(snapshot)[0].undated.map((m) => m.title)).toEqual([
      "first",
      "second",
      "third",
    ]);
  });

  it("sorts dated timepoints even if the timeline does not", () => {
    const snapshot: SnapshotLike = {
      nodes: [],
      edges: [],
      timelines: [
        timeline({
          timeline_id: "t1",
          timepoints: [
            point({ timepoint_id: "late", start: "2024-06-01T00:00:00Z" }),
            point({ timepoint_id: "early", start: "2024-01-01T00:00:00Z" }),
          ],
        }),
      ],
    };

    expect(buildContentRows(snapshot)[0].dated.map((m) => m.id)).toEqual([
      "early",
      "late",
    ]);
  });

  it("links a timepoint to nodes via TIMELINK edges carrying its id", () => {
    const snapshot: SnapshotLike = {
      nodes: [node({ node_id: "n1", content: "Armistice signed" })],
      edges: [
        edge({ src_id: "n1", dst_id: "t1", metadata: { timepoint_id: "p1" } }),
        // Same timeline, a different timepoint — must not be picked up.
        edge({ src_id: "n2", dst_id: "t1", metadata: { timepoint_id: "p2" } }),
      ],
      timelines: [
        timeline({
          timeline_id: "t1",
          timepoints: [point({ timepoint_id: "p1", start: "2024-01-01T00:00:00Z" })],
        }),
      ],
    };

    const [mark] = buildContentRows(snapshot)[0].dated;
    expect(mark.nodeIds).toEqual(["n1"]);
    expect(mark.facets.content).toEqual(["Armistice signed"]);
    expect(mark.detail).toContain("Armistice signed");
  });

  it("ignores a timelink pointing at another timeline", () => {
    const snapshot: SnapshotLike = {
      nodes: [node({ node_id: "n1" })],
      edges: [edge({ src_id: "n1", dst_id: "other", metadata: { timepoint_id: "p1" } })],
      timelines: [
        timeline({
          timeline_id: "t1",
          timepoints: [point({ timepoint_id: "p1", start: "2024-01-01T00:00:00Z" })],
        }),
      ],
    };

    expect(buildContentRows(snapshot)[0].dated[0].nodeIds).toEqual([]);
  });

  it("names the metacontext when the snapshot carries it", () => {
    const snapshot: SnapshotLike = {
      nodes: [node({ node_id: "n1" })],
      edges: [
        edge({ src_id: "n1", dst_id: "t1", metadata: { timepoint_id: "p1" } }),
        edge({ src_id: "n1", dst_id: "mc1", edge_type: "has_metacontext" }),
      ],
      metacontexts: [
        {
          metacontext_id: "mc1",
          content: "Real historical events",
          description: "",
          graph: "default",
        },
      ],
      timelines: [
        timeline({
          timeline_id: "t1",
          timepoints: [point({ timepoint_id: "p1", start: "2024-01-01T00:00:00Z" })],
        }),
      ],
    };

    expect(buildContentRows(snapshot)[0].dated[0].facets.mc).toEqual([
      "Real historical events",
    ]);
  });

  it("falls back to the metacontext id when it is not in the snapshot", () => {
    // Losing the association entirely would silently drop the mark from a
    // metacontext filter it genuinely belongs to.
    const snapshot: SnapshotLike = {
      nodes: [node({ node_id: "n1" })],
      edges: [
        edge({ src_id: "n1", dst_id: "t1", metadata: { timepoint_id: "p1" } }),
        edge({ src_id: "n1", dst_id: "mc-unknown", edge_type: "has_metacontext" }),
      ],
      timelines: [
        timeline({
          timeline_id: "t1",
          timepoints: [point({ timepoint_id: "p1", start: "2024-01-01T00:00:00Z" })],
        }),
      ],
    };

    expect(buildContentRows(snapshot)[0].dated[0].facets.mc).toEqual(["mc-unknown"]);
  });

  it("takes source names from resolvable provenance edges only", () => {
    const snapshot: SnapshotLike = {
      nodes: [
        node({ node_id: "n1" }),
        node({ node_id: "tag1", node_type: "topic", content: "BBC News" }),
      ],
      edges: [
        edge({ src_id: "n1", dst_id: "t1", metadata: { timepoint_id: "p1" } }),
        edge({ src_id: "n1", dst_id: "tag1", edge_type: "tagged_with_topic" }),
        // A document id, which is not a graph node — a bare uuid helps nobody.
        edge({ src_id: "n1", dst_id: "doc-42", edge_type: "sourced_from" }),
      ],
      timelines: [
        timeline({
          timeline_id: "t1",
          timepoints: [point({ timepoint_id: "p1", start: "2024-01-01T00:00:00Z" })],
        }),
      ],
    };

    expect(buildContentRows(snapshot)[0].dated[0].facets.source).toEqual(["BBC News"]);
  });

  it("gathers the facets of every node on a shared timepoint", () => {
    const snapshot: SnapshotLike = {
      nodes: [
        node({ node_id: "n1", node_type: "fact", status: "active" }),
        node({ node_id: "n2", node_type: "topic", status: "superseded" }),
      ],
      edges: [
        edge({ src_id: "n1", dst_id: "t1", metadata: { timepoint_id: "p1" } }),
        edge({ src_id: "n2", dst_id: "t1", metadata: { timepoint_id: "p1" } }),
      ],
      timelines: [
        timeline({
          timeline_id: "t1",
          timepoints: [point({ timepoint_id: "p1", start: "2024-01-01T00:00:00Z" })],
        }),
      ],
    };

    const [mark] = buildContentRows(snapshot)[0].dated;
    expect(mark.facets.type?.sort()).toEqual(["fact", "topic"]);
    expect(mark.facets.status?.sort()).toEqual(["active", "superseded"]);
  });

  it("titles an unlabelled timepoint from its first linked node", () => {
    const snapshot: SnapshotLike = {
      nodes: [node({ node_id: "n1", content: "Armistice signed" })],
      edges: [edge({ src_id: "n1", dst_id: "t1", metadata: { timepoint_id: "p1" } })],
      timelines: [
        timeline({
          timeline_id: "t1",
          timepoints: [point({ timepoint_id: "p1", start: "2024-01-01T00:00:00Z" })],
        }),
      ],
    };

    expect(buildContentRows(snapshot)[0].dated[0].title).toBe("Armistice signed");
  });

  it("is empty when the hub sent no timelines at all", () => {
    expect(buildContentRows({ nodes: [], edges: [] })).toEqual([]);
  });

  it("reads an interval's end", () => {
    const snapshot: SnapshotLike = {
      nodes: [],
      edges: [],
      timelines: [
        timeline({
          timeline_id: "t1",
          timepoints: [
            point({
              timepoint_id: "p1",
              start: "2024-01-01T00:00:00Z",
              end: "2024-06-01T00:00:00Z",
            }),
          ],
        }),
      ],
    };

    const [mark] = buildContentRows(snapshot)[0].dated;
    expect(mark.end).toBe(Date.parse("2024-06-01T00:00:00Z"));
  });
});

describe("a point the order places", () => {
  const bounded = (over: Partial<TimepointView>): SnapshotLike => ({
    nodes: [],
    edges: [],
    timelines: [
      timeline({
        timeline_id: "t1",
        timepoints: [
          point({ timepoint_id: "p1", label: "the quarrel", ...over }),
        ],
      }),
    ],
  });

  it("leaves the tray for the axis when both bounds are known", () => {
    const [row] = buildContentRows(
      bounded({ earliest: "1890-01-01T00:00:00Z", latest: "1900-01-01T00:00:00Z" }),
    );

    expect(row.undated).toHaveLength(0);
    const [mark] = row.dated;
    expect(mark.band).toEqual({
      earliest: Date.parse("1890-01-01T00:00:00Z"),
      latest: Date.parse("1900-01-01T00:00:00Z"),
    });
    expect(mark.start).toBe(Date.parse("1890-01-01T00:00:00Z"));
    expect(mark.end).toBe(Date.parse("1900-01-01T00:00:00Z"));
  });

  it("takes one bound as a place too", () => {
    const [row] = buildContentRows(bounded({ latest: "1897-05-01T00:00:00Z" }));

    expect(row.undated).toHaveLength(0);
    const [mark] = row.dated;
    expect(mark.band).toEqual({ earliest: null, latest: Date.parse("1897-05-01T00:00:00Z") });
    expect(mark.start).toBe(Date.parse("1897-05-01T00:00:00Z"));
    // No interval: one bound says where an edge is, not how long anything ran.
    expect(mark.end).toBeNull();
  });

  it("keeps the label word for word", () => {
    const [row] = buildContentRows(
      bounded({
        label: "during the Renaissance, or thereabouts, as the chronicler has it",
        earliest: "1400-01-01T00:00:00Z",
      }),
    );

    expect(row.dated[0].title).toBe(
      "during the Renaissance, or thereabouts, as the chronicler has it",
    );
  });

  it("stays in the tray when nothing constrains it", () => {
    const [row] = buildContentRows(bounded({}));

    expect(row.dated).toHaveLength(0);
    expect(row.undated[0].band).toBeNull();
  });

  it("leaves a dated point unbanded", () => {
    const [row] = buildContentRows(
      bounded({ start: "1890-01-01T00:00:00Z", kind: "instant" }),
    );

    expect(row.dated[0].band).toBeNull();
  });

  it("says in the detail that the bounds came from the order", () => {
    const [row] = buildContentRows(
      bounded({ earliest: "1890-01-01T00:00:00Z", latest: "1900-01-01T00:00:00Z" }),
    );

    expect(row.dated[0].detail).toContain("after 1890-01-01T00:00:00Z");
    expect(row.dated[0].detail).toContain("before 1900-01-01T00:00:00Z");
  });
});

describe("a point whose order is disputed", () => {
  const disputed = (over: Partial<TimepointView>): SnapshotLike => ({
    nodes: [],
    edges: [],
    timelines: [
      timeline({
        timeline_id: "t1",
        timepoints: [
          point({
            timepoint_id: "p1",
            label: "the quarrel",
            contested: true,
            temporal_contradiction_id: "c1",
            ...over,
          }),
        ],
      }),
    ],
  });

  it("carries the dispute on a dated point, which keeps its date", () => {
    const [row] = buildContentRows(disputed({ start: "1890-01-01T00:00:00Z", kind: "instant" }));

    const [mark] = row.dated;
    expect(mark.start).toBe(Date.parse("1890-01-01T00:00:00Z"));
    expect(mark.contested).toBe(true);
    expect(mark.contradictionId).toBe("c1");
  });

  it("carries the dispute into the tray on a point with no place", () => {
    const [row] = buildContentRows(disputed({}));

    expect(row.dated).toHaveLength(0);
    expect(row.undated[0].contested).toBe(true);
    expect(row.undated[0].contradictionId).toBe("c1");
  });

  it("leaves an undisputed point saying so", () => {
    const [row] = buildContentRows({
      nodes: [],
      edges: [],
      timelines: [
        timeline({
          timeline_id: "t1",
          timepoints: [point({ timepoint_id: "p1", start: "1890-01-01T00:00:00Z" })],
        }),
      ],
    });

    expect(row.dated[0].contested).toBe(false);
    expect(row.dated[0].contradictionId).toBeNull();
  });
});

describe("what a rule says happens over and over", () => {
  const withRule = (over: Partial<RecurrenceView>): SnapshotLike => ({
    nodes: [],
    edges: [],
    timelines: [
      timeline({
        timeline_id: "t1",
        timepoints: [point({ timepoint_id: "p1", start: "1897-01-01T00:00:00Z" })],
        recurrences: [recurrence({ recurrence_id: "r1", ...over })],
      }),
    ],
  });

  it("makes a spine with a bead per occurrence", () => {
    const [row] = buildContentRows(
      withRule({
        occurrences: [
          occurrence({ occurrence_start: "1897-01-01T00:00:00Z" }),
          occurrence({ occurrence_start: "1897-01-08T00:00:00Z" }),
        ],
      }),
    );

    const [spine] = row.spines;
    expect(spine.id).toBe("r1");
    expect(spine.label).toBe("the weekly service");
    expect(spine.occurrences.map((o) => o.at)).toEqual([
      Date.parse("1897-01-01T00:00:00Z"),
      Date.parse("1897-01-08T00:00:00Z"),
    ]);
  });

  it("names the timepoint a materialised occurrence became", () => {
    const [row] = buildContentRows(
      withRule({
        occurrences: [
          occurrence({ occurrence_start: "1897-01-01T00:00:00Z", materialised_id: "p1" }),
        ],
      }),
    );

    expect(row.spines[0].occurrences[0].materialisedId).toBe("p1");
  });

  it("puts a moved occurrence where it moved to, keeping its identity", () => {
    const [row] = buildContentRows(
      withRule({
        occurrences: [
          occurrence({
            occurrence_start: "1897-01-08T00:00:00Z",
            start: "1897-01-09T00:00:00Z",
            moved_to: "1897-01-09T00:00:00Z",
          }),
        ],
      }),
    );

    const [bead] = row.spines[0].occurrences;
    expect(bead.at).toBe(Date.parse("1897-01-09T00:00:00Z"));
    expect(bead.occurrenceStart).toBe(Date.parse("1897-01-08T00:00:00Z"));
    expect(bead.moved).toBe(true);
  });

  it("makes no spine for a rule with nothing in the window", () => {
    expect(buildContentRows(withRule({ occurrences: [] }))[0].spines).toEqual([]);
  });

  it("gives record time no spines at all", () => {
    expect(buildRecordRows({ nodes: [node({ node_id: "n1" })], edges: [] })[0].spines).toEqual([]);
  });
});

describe("buildRecordRows", () => {
  it("puts every node on one row, with type carried by the side", () => {
    // Node type used to be a row. It is now the side of the axis a mark sits
    // on, so splitting rows as well would only hide nodes.
    const snapshot: SnapshotLike = {
      nodes: [
        node({ node_id: "n1", node_type: "fact" }),
        node({ node_id: "n2", node_type: "topic" }),
        node({ node_id: "n3", node_type: "inference" }),
      ],
      edges: [],
    };

    const rows = buildRecordRows(snapshot);
    expect(rows).toHaveLength(1);
    expect(rows[0].dated.map((m) => m.id).sort()).toEqual(["n1", "n2", "n3"]);
    expect(rows[0].dated.find((m) => m.id === "n3")!.side).toBe("right");
    expect(rows[0].dated.find((m) => m.id === "n1")!.side).toBe("left");
  });

  it("has no row at all when the graph is empty", () => {
    expect(buildRecordRows({ nodes: [], edges: [] })).toEqual([]);
  });

  it("places a node at its creation time", () => {
    const snapshot: SnapshotLike = {
      nodes: [node({ node_id: "n1", created_at: "2024-03-01T00:00:00Z" })],
      edges: [],
    };

    expect(buildRecordRows(snapshot)[0].dated[0].start).toBe(
      Date.parse("2024-03-01T00:00:00Z"),
    );
  });

  it("draws a retrieved node as an interval up to its last retrieval", () => {
    const snapshot: SnapshotLike = {
      nodes: [
        node({
          node_id: "n1",
          created_at: "2024-01-01T00:00:00Z",
          retrieved_at: "2024-06-01T00:00:00Z",
        }),
      ],
      edges: [],
    };

    const [mark] = buildRecordRows(snapshot)[0].dated;
    expect(mark.end).toBe(Date.parse("2024-06-01T00:00:00Z"));
  });

  it("draws a never-retrieved node as an instant", () => {
    // `retrieved_at` is null until a search returns the node — the state the
    // backend used to fake by defaulting the timestamp to creation time.
    const snapshot: SnapshotLike = {
      nodes: [
        node({
          node_id: "n1",
          created_at: "2024-01-01T00:00:00Z",
          retrieved_at: null,
        }),
      ],
      edges: [],
    };

    expect(buildRecordRows(snapshot)[0].dated[0].end).toBeNull();
  });

  it("says so in the detail rather than printing null", () => {
    const snapshot: SnapshotLike = {
      nodes: [
        node({ node_id: "n1", created_at: "2024-01-01T00:00:00Z", retrieved_at: null }),
      ],
      edges: [],
    };

    expect(buildRecordRows(snapshot)[0].dated[0].detail).toContain("never");
  });

  it("shows a dash for an unrated confidence rather than the 0.5 default", () => {
    const snapshot: SnapshotLike = {
      nodes: [node({ node_id: "n1", confidence: null })],
      edges: [],
    };

    const detail = buildRecordRows(snapshot)[0].dated[0].detail;

    expect(detail).toContain("confidence —");
    expect(detail).not.toContain("0.50");
  });

  it("still shows the number when an agent supplied one", () => {
    const snapshot: SnapshotLike = {
      nodes: [node({ node_id: "n1", confidence: 0.3 })],
      edges: [],
    };

    expect(buildRecordRows(snapshot)[0].dated[0].detail).toContain("confidence 0.30");
  });

  it("orders each row by creation time", () => {
    const snapshot: SnapshotLike = {
      nodes: [
        node({ node_id: "late", created_at: "2024-06-01T00:00:00Z" }),
        node({ node_id: "early", created_at: "2024-01-01T00:00:00Z" }),
      ],
      edges: [],
    };

    expect(buildRecordRows(snapshot)[0].dated.map((m) => m.id)).toEqual([
      "early",
      "late",
    ]);
  });

  it("never produces undated marks — every node has a creation time", () => {
    const snapshot: SnapshotLike = { nodes: [node({ node_id: "n1" })], edges: [] };
    expect(buildRecordRows(snapshot)[0].undated).toEqual([]);
  });

  it("carries the node's own facets so filters work in this mode too", () => {
    const snapshot: SnapshotLike = {
      nodes: [node({ node_id: "n1", node_type: "fact", status: "active" })],
      edges: [],
    };

    const [mark] = buildRecordRows(snapshot)[0].dated;
    expect(mark.facets.type).toEqual(["fact"]);
    expect(mark.facets.status).toEqual(["active"]);
    expect(mark.nodeIds).toEqual(["n1"]);
  });
});

describe("buildRows", () => {
  const snapshot: SnapshotLike = {
    nodes: [node({ node_id: "n1" })],
    edges: [],
    timelines: [
      timeline({
        timeline_id: "t1",
        timepoints: [point({ timepoint_id: "p1", start: "2024-01-01T00:00:00Z" })],
      }),
    ],
  };

  it("dispatches on the mode", () => {
    expect(buildRows(snapshot, "content")[0].id).toBe("t1");
    expect(buildRows(snapshot, "record")[0].id).toBe(RECORD_ROW_ID);
  });
});

describe("allMarks", () => {
  it("flattens dated and undated marks across rows", () => {
    const rows = buildContentRows({
      nodes: [],
      edges: [],
      timelines: [
        timeline({
          timeline_id: "t1",
          timepoints: [
            point({ timepoint_id: "p1", start: "2024-01-01T00:00:00Z" }),
            point({ timepoint_id: "p2", label: "vague" }),
          ],
        }),
      ],
    });

    expect(allMarks(rows).map((m) => m.id).sort()).toEqual(["p1", "p2"]);
  });
});

describe("sideForTypes", () => {
  it("puts what the graph was told on the left", () => {
    expect(sideForTypes(["fact"])).toBe("left");
    expect(sideForTypes(["topic"])).toBe("left");
    expect(sideForTypes(["fact", "topic"])).toBe("left");
  });

  it("puts what the graph derived on the right", () => {
    expect(sideForTypes(["inference"])).toBe("right");
    expect(sideForTypes(["inference", "inference"])).toBe("right");
  });

  it("straddles the axis when a timepoint holds both", () => {
    expect(sideForTypes(["fact", "inference"])).toBe("axis");
    expect(sideForTypes(["inference", "topic"])).toBe("axis");
  });

  it("treats an unlinked timepoint as something we were told", () => {
    // A bare authored label is not a derivation, and it has to go somewhere.
    expect(sideForTypes([])).toBe("left");
  });
});
