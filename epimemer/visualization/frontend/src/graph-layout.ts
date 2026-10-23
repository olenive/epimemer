/**
 * Which layout the graph panel runs, and with what options.
 *
 * The hierarchy is dagre up to `DAGRE_NODE_LIMIT` nodes and a banded hierarchy
 * above it. Measured on a 2,136-node graph with 9,275 edges, dagre took 38
 * seconds in the browser (54 in Node), almost all of it in its Brandes-Koepf
 * coordinate pass, which is quadratic in the width of a rank. What it produced
 * was a strip 218,673 px wide and 1,026 px tall, so the wait bought nothing:
 * at the panel's minimum zoom a viewport shows under a twentieth of it.
 *
 * The banded hierarchy keeps what the hierarchy is for, every node in the rank
 * dagre's longest-path ranker would give it, edges pointing up into what they
 * are about, and wraps each rank into rows so the whole fits the panel's shape.
 * Its cost grows with the size of the graph rather than the square of a rank:
 * the same 2,136 nodes take about a tenth of a second.
 */

import type { Core, LayoutOptions } from "cytoscape";

/**
 * The largest graph dagre lays out. Dagre took 0.3 s at 200 nodes, 0.6 s at
 * 300 and 1.3 s at 400 on growth-ordered slices of the 2,136-node graph, so
 * 300 keeps a re-layout under about half a second in a browser.
 */
export const DAGRE_NODE_LIMIT = 300;

// The graph is wide and shallow: a few ranks (topic, fact, inference) holding
// many peers each. `rankDir` sets the direction ranks advance in, so the peers
// spread along the other axis: a vertical rankDir is what puts them across the
// viewport's width. "LR" reads as a single tall column that auto-fit then
// shrinks to nothing.
//
// "BT" over "TB" so edges point upward into what they are about: facts settle
// at the bottom and the topics they support rise to the top. That holds while
// edges run detail to abstraction; a graph wired the other way inverts. The
// bands keep the same direction.
const RANK_SEP = 90;
const NODE_SEP = 45;
/** The width and height every node draws at, from the panel's stylesheet. */
const NODE_SIZE = 24;
const NODE_PITCH = NODE_SIZE + NODE_SEP;
const RANK_PITCH = NODE_SIZE + RANK_SEP;
/** The shape the banded layout aims for when the panel has not been measured. */
const DEFAULT_ASPECT = 1.6;

const DAGRE = {
  name: "dagre",
  rankDir: "BT",
  rankSep: RANK_SEP,
  nodeSep: NODE_SEP,
  animate: true,
  animationDuration: 300,
} as LayoutOptions;

const FCOSE = {
  name: "fcose",
  animate: true,
  animationDuration: 300,
  nodeRepulsion: 8000,
  idealEdgeLength: 80,
} as LayoutOptions;

export interface Point {
  x: number;
  y: number;
}

export interface LayoutEdge {
  source: string;
  target: string;
}

/** Whether the hierarchy is drawn in bands rather than by dagre. */
export const drawsBands = (requested: string, nodeCount: number): boolean =>
  requested !== "fcose" && nodeCount > DAGRE_NODE_LIMIT;

/** What the layout picker says about the layout it is showing. */
export const layoutTitle = (requested: string, nodeCount: number): string =>
  drawsBands(requested, nodeCount)
    ? `Hierarchy with each rank wrapped into rows: this graph has ${nodeCount} nodes, and one row per rank is drawn up to ${DAGRE_NODE_LIMIT}`
    : "How the graph is laid out";

/**
 * Each node's rank: the longest path from it to a node with no outgoing edge.
 *
 * The ranks dagre's longest-path ranker gives, numbered from the top: a node
 * sits one rank below the highest of the nodes it points to. An edge that
 * closes a cycle is ignored, found by a depth-first search in input order so
 * the answer does not depend on anything but the graph.
 */
export const ranksOf = (
  ids: readonly string[],
  edges: readonly LayoutEdge[],
): number[] => {
  const index = new Map(ids.map((id, i) => [id, i]));
  const out: number[][] = ids.map(() => []);
  for (const { source, target } of edges) {
    const s = index.get(source);
    const t = index.get(target);
    if (s !== undefined && t !== undefined && s !== t) out[s].push(t);
  }

  const UNSEEN = 0;
  const OPEN = 1;
  const DONE = 2;
  const seen = new Uint8Array(ids.length);
  const rank = new Array<number>(ids.length).fill(0);
  for (let root = 0; root < ids.length; root++) {
    if (seen[root] !== UNSEEN) continue;
    // Iterative, so a long chain cannot overflow the call stack.
    const stack: { node: number; next: number }[] = [{ node: root, next: 0 }];
    seen[root] = OPEN;
    while (stack.length > 0) {
      const frame = stack[stack.length - 1];
      const targets = out[frame.node];
      if (frame.next < targets.length) {
        const w = targets[frame.next++];
        if (seen[w] === UNSEEN) {
          seen[w] = OPEN;
          stack.push({ node: w, next: 0 });
        }
        continue;
      }
      // A target still OPEN is an ancestor on the stack: that edge closes a cycle.
      rank[frame.node] = targets.reduce(
        (r, w) => (seen[w] === DONE ? Math.max(r, rank[w] + 1) : r),
        0,
      );
      seen[frame.node] = DONE;
      stack.pop();
    }
  }
  return rank;
};

/**
 * Order each rank so a node sits near the nodes it is joined to.
 *
 * Barycentric sweeps, as dagre's ordering pass does, down the ranks and back
 * up: each node moves to the mean position of its neighbours in other ranks,
 * and a node with none keeps its place.
 */
const orderRanks = (
  ranks: readonly number[],
  edges: readonly [number, number][],
): number[][] => {
  const neighbours: number[][] = ranks.map(() => []);
  for (const [s, t] of edges) {
    if (ranks[s] === ranks[t]) continue;
    neighbours[s].push(t);
    neighbours[t].push(s);
  }

  const rankCount = ranks.reduce((m, r) => Math.max(m, r + 1), 0);
  const bands: number[][] = Array.from({ length: rankCount }, () => []);
  ranks.forEach((r, node) => bands[r].push(node));

  // Where each node sits in its band, from 0 to 1.
  const place = new Float64Array(ranks.length);
  const settle = (band: number[]): void =>
    band.forEach((node, i) => {
      place[node] = (i + 0.5) / band.length;
    });
  bands.forEach(settle);

  const sweep = (order: readonly number[]): void => {
    for (const r of order) {
      const centre = (node: number): number =>
        neighbours[node].length === 0
          ? place[node]
          : neighbours[node].reduce((sum, n) => sum + place[n], 0) / neighbours[node].length;
      const keyed = bands[r].map((node) => ({ node, key: centre(node) }));
      keyed.sort((a, b) => a.key - b.key);
      bands[r] = keyed.map(({ node }) => node);
      settle(bands[r]);
    }
  };
  const down = bands.map((_, r) => r);
  const up = [...down].reverse();
  for (let pass = 0; pass < 2; pass++) {
    sweep(down);
    sweep(up);
  }
  return bands;
};

/** Rows per band: whatever brings the drawing closest to `aspect`. */
const rowLength = (bandSizes: readonly number[], aspect: number): number => {
  const widest = Math.max(1, ...bandSizes);
  const mismatch = (perRow: number): number => {
    const rows = bandSizes.reduce((sum, n) => sum + Math.max(0, Math.ceil(n / perRow) - 1), 0);
    const width = (perRow - 1) * NODE_PITCH + NODE_SIZE;
    const height = rows * NODE_PITCH + (bandSizes.length - 1) * RANK_PITCH + NODE_SIZE;
    return Math.abs(Math.log(width / height / aspect));
  };
  let best = 1;
  for (let perRow = 2; perRow <= widest; perRow++) {
    if (mismatch(perRow) < mismatch(best)) best = perRow;
  }
  return best;
};

/**
 * Positions for the banded hierarchy.
 *
 * Ranks run top to bottom from the nodes with nothing above them, each rank
 * wrapped into rows of one shared length and centred, with dagre's spacing:
 * `NODE_SEP` between neighbours and `RANK_SEP` between ranks.
 */
export const bandedHierarchy = (
  ids: readonly string[],
  edges: readonly LayoutEdge[],
  aspect: number = DEFAULT_ASPECT,
): Map<string, Point> => {
  const index = new Map(ids.map((id, i) => [id, i]));
  const joined = edges.flatMap(({ source, target }): [number, number][] => {
    const s = index.get(source);
    const t = index.get(target);
    return s !== undefined && t !== undefined && s !== t ? [[s, t]] : [];
  });
  const bands = orderRanks(ranksOf(ids, edges), joined);
  const perRow = rowLength(
    bands.map((band) => band.length),
    aspect > 0 && Number.isFinite(aspect) ? aspect : DEFAULT_ASPECT,
  );

  const positions = new Map<string, Point>();
  let top = 0;
  for (const band of bands) {
    for (let start = 0; start < band.length; start += perRow) {
      const row = band.slice(start, start + perRow);
      row.forEach((node, i) => {
        positions.set(ids[node], { x: (i - (row.length - 1) / 2) * NODE_PITCH, y: top });
      });
      top += NODE_PITCH;
    }
    top += RANK_PITCH - NODE_PITCH;
  }
  return positions;
};

/** The panel's shape, or the default when it has none to measure. */
const aspectOf = (cy: Core): number => {
  const width = cy.width();
  const height = cy.height();
  // Headless reports 1 x 1 and a hidden panel 0 x 0: neither is a shape to aim for.
  return width >= NODE_SIZE && height >= NODE_SIZE ? width / height : DEFAULT_ASPECT;
};

/**
 * The options to lay `cy` out with.
 *
 * Counts every node, including ones a type filter hides: the layout positions
 * those too, so they are part of its cost.
 */
export const layoutOptionsFor = (requested: string, cy: Core): LayoutOptions => {
  if (requested === "fcose") return FCOSE;
  const nodes = cy.nodes();
  if (!drawsBands(requested, nodes.length)) return DAGRE;

  const positions = bandedHierarchy(
    nodes.map((n) => n.id()),
    cy.edges().map((e) => ({ source: e.source().id(), target: e.target().id() })),
    aspectOf(cy),
  );
  return {
    name: "preset",
    positions: Object.fromEntries(positions),
    animate: true,
    animationDuration: 300,
    fit: true,
    padding: 30,
  };
};
