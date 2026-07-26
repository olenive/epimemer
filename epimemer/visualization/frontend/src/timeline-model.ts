/**
 * Turning a graph snapshot into timeline rows.
 *
 * Two modes produce rows of the same shape, so the panel renders one thing:
 *
 * - **content time** — `Timeline`s and their timepoints: when the described
 *   events happened. Rows are timelines.
 * - **record time** — node lifetimes: when the graph learned something. Rows
 *   are node types.
 *
 * Pure, and deliberately separate from rendering: which marks exist and what
 * they can be filtered by is the part worth testing without a DOM.
 */

import type { Facets, FilterableMark } from "./timeline-filter";
import type {
  EdgeView,
  MetacontextView,
  NodeView,
  TimelineView,
  TimepointView,
} from "./types";

export type TimeMode = "content" | "record";

export interface TimelineMark extends FilterableMark {
  id: string;
  /** Primary label, drawn beside the mark when there is room. */
  title: string;
  /** Long form for the detail drawer. */
  detail: string;
  /** Nodes this mark stands for — the bridge to the graph panel. */
  nodeIds: string[];
}

/**
 * A mark that has somewhere to be drawn.
 *
 * The narrowing is in the type because it is what lets a mark be handed to the
 * scale at all — an undated mark has no coordinate and belongs off the axis.
 */
export interface DatedMark extends TimelineMark {
  start: number;
}

export interface TimelineRow {
  id: string;
  name: string;
  /** Marks with a real coordinate, in time order. */
  dated: DatedMark[];
  /** Marks with no date, in the order the timeline lists them. */
  undated: TimelineMark[];
}

const isDated = (mark: TimelineMark): mark is DatedMark => mark.start !== null;

export interface SnapshotLike {
  nodes: NodeView[];
  edges: EdgeView[];
  timelines?: TimelineView[];
  metacontexts?: MetacontextView[];
}

const EDGE_TIMELINK = "timelink";
const EDGE_METACONTEXT = "has_metacontext";
/** Both answer "where did this come from?", so they feed one `source` facet. */
const PROVENANCE_EDGES = new Set(["sourced_from", "tagged_with"]);

const parseTime = (iso: string | null): number | null => {
  if (!iso) return null;
  const t = Date.parse(iso);
  return Number.isNaN(t) ? null : t;
};

const unique = (values: readonly string[]): string[] => [...new Set(values)];

/** Group edges by their source node, so a node's outgoing edges are one lookup. */
const bySource = (edges: readonly EdgeView[]): Map<string, EdgeView[]> => {
  const index = new Map<string, EdgeView[]>();
  for (const edge of edges) {
    const bucket = index.get(edge.src_id);
    if (bucket) bucket.push(edge);
    else index.set(edge.src_id, [edge]);
  }
  return index;
};

interface Resolver {
  nodes: Map<string, NodeView>;
  outgoing: Map<string, EdgeView[]>;
  metacontexts: Map<string, string>;
}

const makeResolver = (snapshot: SnapshotLike): Resolver => ({
  nodes: new Map(snapshot.nodes.map((n) => [n.node_id, n])),
  outgoing: bySource(snapshot.edges),
  metacontexts: new Map(
    (snapshot.metacontexts ?? []).map((mc) => [mc.metacontext_id, mc.content]),
  ),
});

/**
 * Frames a node sits in. Falls back to the raw id when the metacontext is not
 * in the snapshot — an unhelpful label still filters correctly, where dropping
 * it would quietly lose the association.
 */
const metacontextsOf = (resolver: Resolver, nodeId: string): string[] =>
  (resolver.outgoing.get(nodeId) ?? [])
    .filter((e) => e.edge_type === EDGE_METACONTEXT)
    .map((e) => resolver.metacontexts.get(e.dst_id) ?? e.dst_id);

/**
 * Where a node came from — documents and tags alike.
 *
 * Only resolvable endpoints are kept: `sourced_from` may point at a document
 * that is not a graph node, and a bare uuid in a "source" dropdown is noise.
 */
const sourcesOf = (resolver: Resolver, nodeId: string): string[] =>
  (resolver.outgoing.get(nodeId) ?? [])
    .filter((e) => PROVENANCE_EDGES.has(e.edge_type))
    .map((e) => resolver.nodes.get(e.dst_id)?.content)
    .filter((content): content is string => content !== undefined);

/** Facets contributed by the nodes attached to a mark. */
const facetsFromNodes = (
  resolver: Resolver,
  nodes: readonly NodeView[],
  extraLabels: readonly string[] = [],
): Facets => ({
  type: unique(nodes.map((n) => n.node_type)),
  status: unique(nodes.map((n) => n.status)),
  content: unique(nodes.map((n) => n.content)),
  mc: unique(nodes.flatMap((n) => metacontextsOf(resolver, n.node_id))),
  source: unique(nodes.flatMap((n) => sourcesOf(resolver, n.node_id))),
  label: unique(extraLabels.filter((l) => l !== "")),
});

/** Nodes linked to one timepoint, via TIMELINK edges carrying its id. */
const nodesForTimepoint = (
  resolver: Resolver,
  edges: readonly EdgeView[],
  timelineId: string,
  timepointId: string,
): NodeView[] =>
  edges
    .filter(
      (e) =>
        e.edge_type === EDGE_TIMELINK &&
        e.dst_id === timelineId &&
        e.metadata?.timepoint_id === timepointId,
    )
    .map((e) => resolver.nodes.get(e.src_id))
    .filter((n): n is NodeView => n !== undefined);

const describeTimepoint = (
  point: TimepointView,
  timeline: TimelineView,
  linked: readonly NodeView[],
): string => {
  const when =
    point.start === null
      ? "undated"
      : point.end
        ? `${point.start} → ${point.end}`
        : point.start;
  const lines = [`${timeline.name} — ${when}`];
  if (point.label) lines.push(point.label);
  if (linked.length > 0) {
    lines.push("", ...linked.map((n) => `[${n.node_type}] ${n.content}`));
  }
  return lines.join("\n");
};

const timepointTitle = (point: TimepointView, linked: readonly NodeView[]): string =>
  point.label ?? linked[0]?.content ?? "(untitled)";

const markForTimepoint = (
  resolver: Resolver,
  edges: readonly EdgeView[],
  timeline: TimelineView,
  point: TimepointView,
): TimelineMark => {
  const linked = nodesForTimepoint(resolver, edges, timeline.timeline_id, point.timepoint_id);
  return {
    id: point.timepoint_id,
    start: parseTime(point.start),
    end: parseTime(point.end),
    title: timepointTitle(point, linked),
    detail: describeTimepoint(point, timeline, linked),
    nodeIds: linked.map((n) => n.node_id),
    facets: facetsFromNodes(resolver, linked, [point.label ?? "", timeline.name]),
  };
};

/**
 * Content-time rows: one per timeline.
 *
 * Undated timepoints keep the order the timeline lists them in, which is the
 * order `reorder_timepoints` establishes on the backend — the panel does not
 * get to invent a second answer to "what order is this timeline in?".
 */
export const buildContentRows = (snapshot: SnapshotLike): TimelineRow[] => {
  const resolver = makeResolver(snapshot);
  return (snapshot.timelines ?? []).map((timeline) => {
    const marks = timeline.timepoints.map((point) =>
      markForTimepoint(resolver, snapshot.edges, timeline, point),
    );
    return {
      id: timeline.timeline_id,
      name: timeline.name,
      dated: marks.filter(isDated).sort((a, b) => a.start - b.start),
      undated: marks.filter((m) => !isDated(m)),
    };
  });
};

const RECORD_ROWS: { id: string; name: string }[] = [
  { id: "topic", name: "Topics" },
  { id: "fact", name: "Facts" },
  { id: "inference", name: "Inferences" },
];

const describeNode = (node: NodeView): string =>
  [
    `[${node.node_type}] ${node.status}`,
    node.content,
    "",
    `created   ${node.created_at}`,
    `reinforced ${node.last_reinforced}`,
    `confidence ${node.confidence.toFixed(2)}  novelty ${node.novelty.toFixed(2)}`,
  ].join("\n");

/**
 * Record-time mark for one node: born at `created_at`, drawn as an interval
 * out to `last_reinforced` so the span over which it stayed relevant is
 * visible. A node never reinforced since creation is a plain point.
 */
const markForNode = (resolver: Resolver, node: NodeView): DatedMark => {
  const created = parseTime(node.created_at) ?? 0;
  const reinforced = parseTime(node.last_reinforced);
  return {
    id: node.node_id,
    start: created,
    end: reinforced !== null && reinforced > created ? reinforced : null,
    title: node.content,
    detail: describeNode(node),
    nodeIds: [node.node_id],
    facets: facetsFromNodes(resolver, [node]),
  };
};

/**
 * Record-time rows: one per node type.
 *
 * Only nodes present in the snapshot appear, and the snapshot carries active
 * nodes — so this shows what the graph currently holds and when it arrived,
 * not a full audit trail including retirements.
 */
export const buildRecordRows = (snapshot: SnapshotLike): TimelineRow[] => {
  const resolver = makeResolver(snapshot);
  return RECORD_ROWS.map(({ id, name }) => ({
    id,
    name,
    dated: snapshot.nodes
      .filter((n) => n.node_type === id)
      .map((n) => markForNode(resolver, n))
      .sort((a, b) => a.start - b.start),
    undated: [],
  })).filter((row) => row.dated.length > 0);
};

export const buildRows = (snapshot: SnapshotLike, mode: TimeMode): TimelineRow[] =>
  mode === "content" ? buildContentRows(snapshot) : buildRecordRows(snapshot);

/** Every mark in every row — for populating filter selects. */
export const allMarks = (rows: readonly TimelineRow[]): TimelineMark[] =>
  rows.flatMap((row) => [...row.dated, ...row.undated]);
