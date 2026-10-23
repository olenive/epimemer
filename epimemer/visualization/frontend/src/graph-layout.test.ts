import cytoscape, { type Core, type ElementDefinition } from "cytoscape";
// @ts-expect-error: cytoscape-dagre has no types
import dagre from "cytoscape-dagre";
// @ts-expect-error: cytoscape-fcose has no types
import fcose from "cytoscape-fcose";
import { describe, expect, it } from "vitest";

import {
  DAGRE_NODE_LIMIT,
  bandedHierarchy,
  layoutOptionsFor,
  layoutTitle,
  ranksOf,
} from "./graph-layout";

cytoscape.use(dagre);
cytoscape.use(fcose);

/**
 * A graph wired the way an Epimemer graph is: facts tagged with and extracted
 * under topics, inferences supported by facts, topics nested under topics.
 * Seeded, so every run lays out the same graph.
 */
const epimemerLike = (nodeCount: number): ElementDefinition[] => {
  let seed = 7;
  const next = (): number => {
    seed = (seed * 1103515245 + 12345) % 2147483648;
    return seed / 2147483648;
  };
  const pick = (ids: string[]): string => ids[Math.floor(next() * ids.length)];

  const topics = Array.from({ length: Math.round(nodeCount * 0.2) }, (_, i) => `t${i}`);
  const inferences = Array.from({ length: Math.round(nodeCount * 0.23) }, (_, i) => `i${i}`);
  const facts = Array.from(
    { length: nodeCount - topics.length - inferences.length },
    (_, i) => `f${i}`,
  );

  const edges: [string, string][] = [
    ...topics.slice(1).map((t, i): [string, string] => [t, topics[Math.floor(next() * (i + 1))]]),
    ...facts.flatMap((f): [string, string][] => [
      [f, pick(topics)],
      [f, pick(topics)],
      [f, pick(topics)],
      [f, pick(topics)],
      [f, pick(inferences)],
    ]),
    ...inferences.flatMap((i): [string, string][] => [[i, pick(topics)], [i, pick(topics)]]),
  ];

  return [
    ...[...topics, ...inferences, ...facts].map((id) => ({ group: "nodes" as const, data: { id } })),
    ...edges.map(([source, target], k) => ({
      group: "edges" as const,
      data: { id: `e${k}`, source, target },
    })),
  ];
};

const headless = (elements: ElementDefinition[]): Core =>
  cytoscape({
    headless: true,
    styleEnabled: true,
    style: [{ selector: "node", style: { width: 24, height: 24 } }],
    elements,
  });

const timeLayout = (requested: string, cy: Core): number => {
  const started = performance.now();
  cy.layout({ ...layoutOptionsFor(requested, cy), animate: false } as cytoscape.LayoutOptions).run();
  return performance.now() - started;
};

// The hierarchy layout froze the page for 38 seconds on a 2,136-node graph:
// dagre's coordinate pass is quadratic in the width of a rank, and at that size
// a rank holds over a thousand nodes. The default layout has to come back in
// interactive time however large the graph grows.
describe("the default layout on a large graph", () => {
  it("does not hand a graph above the limit to dagre", () => {
    expect(layoutOptionsFor("dagre", headless(epimemerLike(2000))).name).not.toBe("dagre");
  });

  it("lays out two thousand nodes in interactive time", () => {
    const cy = headless(epimemerLike(2000));
    expect(timeLayout("dagre", cy)).toBeLessThan(3000);
  });
});

// Small graphs keep the look they had: the same dagre run with the same
// options, and Force untouched at any size.
describe("small graphs and the force layout", () => {
  const DAGRE_AS_IT_WAS = {
    name: "dagre",
    rankDir: "BT",
    rankSep: 90,
    nodeSep: 45,
    animate: true,
    animationDuration: 300,
  };

  it("runs dagre with its old options up to the limit", () => {
    const cy = headless(epimemerLike(DAGRE_NODE_LIMIT));
    expect(cy.nodes().length).toBe(DAGRE_NODE_LIMIT);
    expect(layoutOptionsFor("dagre", cy)).toEqual(DAGRE_AS_IT_WAS);
  });

  it("falls back to the hierarchy for a layout name it does not know", () => {
    expect(layoutOptionsFor("spiral", headless(epimemerLike(50)))).toEqual(DAGRE_AS_IT_WAS);
  });

  it("leaves Force as it was, large or small", () => {
    const force = {
      name: "fcose",
      animate: true,
      animationDuration: 300,
      nodeRepulsion: 8000,
      idealEdgeLength: 80,
    };
    expect(layoutOptionsFor("fcose", headless(epimemerLike(50)))).toEqual(force);
    expect(layoutOptionsFor("fcose", headless(epimemerLike(2000)))).toEqual(force);
  });

  it("says when the hierarchy is drawn in rows, and why", () => {
    expect(layoutTitle("dagre", DAGRE_NODE_LIMIT)).not.toMatch(/rows/);
    expect(layoutTitle("dagre", 2136)).toMatch(/rows/);
    expect(layoutTitle("dagre", 2136)).toContain("2136");
    expect(layoutTitle("fcose", 2136)).not.toMatch(/rows/);
  });
});

describe("ranksOf", () => {
  it("puts a node one rank below the highest node it points to", () => {
    // f -> i -> t and f -> t: f sits below i, not beside it.
    expect(
      ranksOf(["t", "i", "f"], [
        { source: "i", target: "t" },
        { source: "f", target: "i" },
        { source: "f", target: "t" },
      ]),
    ).toEqual([0, 1, 2]);
  });

  it("ignores the edge that closes a cycle rather than looping", () => {
    const ranks = ranksOf(["a", "b", "c"], [
      { source: "a", target: "b" },
      { source: "b", target: "c" },
      { source: "c", target: "a" },
    ]);
    expect(ranks).toHaveLength(3);
    expect(new Set(ranks).size).toBe(3);
  });

  it("ignores self-loops and edges to nodes it was not given", () => {
    expect(
      ranksOf(["a", "b"], [
        { source: "a", target: "a" },
        { source: "a", target: "missing" },
        { source: "b", target: "a" },
      ]),
    ).toEqual([0, 1]);
  });
});

describe("bandedHierarchy", () => {
  const elements = epimemerLike(2000);
  const ids = elements.filter((e) => e.group === "nodes").map((e) => e.data.id as string);
  const edges = elements
    .filter((e) => e.group === "edges")
    .map((e) => ({ source: e.data.source as string, target: e.data.target as string }));

  it("places every node, each in a place of its own", () => {
    const positions = bandedHierarchy(ids, edges);
    expect(positions.size).toBe(ids.length);
    const places = new Set([...positions.values()].map(({ x, y }) => `${x},${y}`));
    expect(places.size).toBe(ids.length);
  });

  it("keeps edges pointing up into what they are about", () => {
    // The direction the hierarchy has always had: the synthetic graph has no
    // cycles, so every edge must run from lower on the page to higher.
    const positions = bandedHierarchy(ids, edges);
    const downward = edges.filter(
      ({ source, target }) => positions.get(target)!.y >= positions.get(source)!.y,
    );
    expect(downward).toEqual([]);
  });

  it("wraps ranks so the drawing takes the panel's shape, not a strip", () => {
    // Dagre drew the 2,136-node graph 218,673 px wide and 1,026 px tall.
    for (const aspect of [0.8, 1.6, 2.5]) {
      const points = [...bandedHierarchy(ids, edges, aspect).values()];
      const width = Math.max(...points.map((p) => p.x)) - Math.min(...points.map((p) => p.x));
      const height = Math.max(...points.map((p) => p.y)) - Math.min(...points.map((p) => p.y));
      expect(width / height).toBeGreaterThan(aspect / 1.5);
      expect(width / height).toBeLessThan(aspect * 1.5);
    }
  });

  it("gives the same picture every time", () => {
    expect(bandedHierarchy(ids, edges)).toEqual(bandedHierarchy(ids, edges));
  });
});
