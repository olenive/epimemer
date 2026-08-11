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
import type { EdgeView, NodeView, TimelineView, TimepointView } from "./types";

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

const point = (over: Partial<TimepointView> & { timepoint_id: string }): TimepointView => ({
  start: null,
  end: null,
  label: null,
  metadata: {},
  ...over,
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
    // frame filter it genuinely belongs to.
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
        edge({ src_id: "n1", dst_id: "tag1", edge_type: "tagged_with" }),
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
