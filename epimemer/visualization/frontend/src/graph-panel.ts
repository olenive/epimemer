/**
 * Knowledge graph visualization panel using Cytoscape.js.
 *
 * Renders epistemic nodes (topics, facts, inferences) and their edges.
 * Supports live updates via graph events from the WebSocket.
 */

import cytoscape, { type Core, type LayoutOptions } from "cytoscape";
// @ts-expect-error — cytoscape-dagre has no types
import dagre from "cytoscape-dagre";
// @ts-expect-error — cytoscape-fcose has no types
import fcose from "cytoscape-fcose";
import type { EventRouter } from "./events";
import {
  currentTheme,
  currentPalette,
  semanticPaletteFor,
  type Palette,
  type SemanticPalette,
  type Theme,
} from "./theme";
import type {
  EdgeStored,
  EdgeView,
  NodeStatusChanged,
  NodeStored,
  NodeView,
  AnyEvent,
} from "./types";

cytoscape.use(dagre);
cytoscape.use(fcose);

// --- Color scheme by node type ---

// Both tables name a *meaning* in the shared semantic palette rather than a hex
// value, so the graph and the timeline cannot drift apart again (#56) and a
// re-pick happens once, in `theme.ts`.
const NODE_MEANINGS: Record<string, keyof SemanticPalette> = {
  topic: "topic",
  fact: "fact",
  inference: "inference",
  segment: "segment",
  document: "document",
};

// An edge tied to a node kind takes that kind's hue: `supports` runs from a
// fact, `derived_from` from an inference, `subtopic_of` between topics. That is
// also what keeps them apart — three of these used to sit on a hue that, after
// the palette moved, belonged to a *different* kind.
const EDGE_MEANINGS: Record<string, keyof SemanticPalette> = {
  supports: "fact",
  abstracts: "abstracts",
  subtopic_of: "topic",
  similarity: "similarity",
  contradiction: "contradiction",
  derived_from: "inference",
  superseded_by: "lineage",
  merged_into: "lineage",
};

/** Fallback for a kind this build has never heard of: a plain neutral. */
const UNKNOWN_KIND = "#9ca3af";

export const nodeColor = (nodeType: string, theme: Theme): string => {
  const meaning = NODE_MEANINGS[nodeType];
  return meaning ? semanticPaletteFor(theme)[meaning] : UNKNOWN_KIND;
};

export const edgeColor = (edgeType: string, theme: Theme): string => {
  const meaning = EDGE_MEANINGS[edgeType];
  return meaning ? semanticPaletteFor(theme)[meaning] : UNKNOWN_KIND;
};

// Everything that has left the active set fades the same way. The *reason* it
// left (wrong, duplicated, or merely trivial) is not something a node's opacity
// can carry, and an unlisted status falling through to 1.0 would draw a retired
// node as a live one.
//
// So `active` is the only status named, and everything else — including one
// this file has never heard of — fades. Listing the retired statuses instead is
// what caused #55: `NodeStatus` grew `corrected` and `historical` in 666904f,
// the list did not, and two retired states silently drew as live. A status is
// added on the Python side by someone who has no reason to look here, so the
// default is the only part of this that can be relied on to be right.
const ACTIVE_OPACITY = 1.0;
const RETIRED_OPACITY = 0.3;

export const statusOpacity = (status: string): number =>
  status === "active" ? ACTIVE_OPACITY : RETIRED_OPACITY;

// --- Layout configs ---
//
// The graph is wide and shallow: a few ranks (topic → fact → inference) holding
// many peers each. `rankDir` sets the direction ranks advance in, so the *peers*
// spread along the other axis — a vertical rankDir is what puts them across the
// viewport's width. "LR" reads as a single tall column that auto-fit then
// shrinks to nothing.
//
// "BT" over "TB" so edges point upward into what they are about: facts settle
// at the bottom and the topics they support rise to the top. That holds while
// edges run detail → abstraction; a graph wired the other way inverts.

const LAYOUT_CONFIGS: Record<string, object> = {
  dagre: {
    name: "dagre",
    rankDir: "BT",
    rankSep: 90,
    nodeSep: 45,
    animate: true,
    animationDuration: 300,
  },
  fcose: {
    name: "fcose",
    animate: true,
    animationDuration: 300,
    nodeRepulsion: 8000,
    idealEdgeLength: 80,
  },
};

// --- Cytoscape stylesheet ---

// Cast needed: Cytoscape types don't model data() mappers for numeric fields.
//
// Built per theme rather than declared once: the canvas is drawn, not styled,
// so Tailwind's `dark:` variants cannot reach it. The hues move with the theme
// too (#56), but they are baked into each element's `color` data at add time
// rather than read from here — so `applyTheme` re-writes them.
const stylesheetFor = (palette: Palette) => [
  {
    selector: "node",
    style: {
      label: "data(label)",
      "text-wrap": "ellipsis",
      "text-max-width": "120px",
      "font-size": "10px",
      color: palette.nodeLabel,
      "text-valign": "bottom",
      "text-margin-y": 6,
      "background-color": "data(color)",
      opacity: "data(opacity)",
      width: 24,
      height: 24,
      "border-width": 2,
      "border-color": palette.nodeBorder,
    },
  },
  {
    selector: "node:selected",
    style: {
      "border-color": "#60a5fa",
      "border-width": 3,
    },
  },
  {
    selector: "edge",
    style: {
      width: 1.5,
      "line-color": "data(color)",
      "target-arrow-color": "data(color)",
      "target-arrow-shape": "triangle",
      "curve-style": "bezier",
      opacity: 0.6,
      "arrow-scale": 0.8,
    },
  },
  {
    selector: ".highlighted",
    style: {
      "border-color": "#f472b6",
      "border-width": 3,
      "z-index": 10,
    },
  },
] as unknown as cytoscape.StylesheetStyle[];

// --- Panel state and initialization ---


interface GraphPanelState {
  cy: Core;
  currentLayout: string;
  currentFilter: string;
  pendingLayout: ReturnType<typeof setTimeout> | null;
}

const truncate = (text: string, maxLen: number): string =>
  text.length > maxLen ? text.slice(0, maxLen - 1) + "\u2026" : text;

export interface GraphPanelHandle {
  cleanup: () => void;
  clearGraph: () => void;
  loadSnapshot: (nodes: NodeView[], edges: EdgeView[]) => void;
  /** Mark exactly these nodes; an empty list clears the highlight. */
  highlightNodes: (nodeIds: readonly string[]) => void;
  /** Re-style the canvas for the current theme, preserving layout and selection. */
  applyTheme: () => void;
}

/**
 * Initialize the knowledge graph panel.
 *
 * Returns a handle with cleanup, clearGraph, and loadSnapshot methods.
 */
export const initGraphPanel = (
  container: HTMLElement,
  router: EventRouter,
  onNodeSelect: (nodeId: string, content: string, nodeType: string) => void,
  controls?: { layoutSelect: HTMLSelectElement; filterSelect: HTMLSelectElement },
): GraphPanelHandle => {
  const state: GraphPanelState = {
    cy: cytoscape({
      container,
      style: stylesheetFor(currentPalette()),
      layout: { name: "preset" },
      minZoom: 0.2,
      maxZoom: 4,
      wheelSensitivity: 0.3,
    }),
    currentLayout: "dagre",
    currentFilter: "all",
    pendingLayout: null,
  };

  if (controls) {
    bindGraphControls(controls.layoutSelect, controls.filterSelect, state.cy, state);
  }

  // --- Event handlers ---

  const scheduleLayout = (): void => {
    if (state.pendingLayout) clearTimeout(state.pendingLayout);
    state.pendingLayout = setTimeout(() => {
      state.pendingLayout = null;
      runLayout(state);
    }, 200);
  };

  const handleNodeStored = (event: NodeStored): void => {
    const n = event.node;
    const existing = state.cy.getElementById(n.node_id);
    if (existing.length > 0) {
      existing.data("label", truncate(n.content, 40));
      existing.data("content", n.content);
      existing.data("status", n.status);
      existing.data("opacity", statusOpacity(n.status));
      return;
    }

    state.cy.add({
      group: "nodes",
      data: {
        id: n.node_id,
        label: truncate(n.content, 40),
        content: n.content,
        nodeType: n.node_type,
        status: n.status,
        color: nodeColor(n.node_type, currentTheme()),
        opacity: statusOpacity(n.status),
      },
    });

    scheduleLayout();
  };

  const handleNodeStatusChanged = (event: NodeStatusChanged): void => {
    const node = state.cy.getElementById(event.node_id);
    if (node.length > 0) {
      node.data("status", event.new_status);
      node.data("opacity", statusOpacity(event.new_status));
    }
  };

  const handleEdgeStored = (event: EdgeStored): void => {
    const e = event.edge;
    if (state.cy.getElementById(e.edge_id).length > 0) return;

    // Only add edge if both endpoints exist
    if (
      state.cy.getElementById(e.src_id).length === 0 ||
      state.cy.getElementById(e.dst_id).length === 0
    ) {
      return;
    }

    state.cy.add({
      group: "edges",
      data: {
        id: e.edge_id,
        source: e.src_id,
        target: e.dst_id,
        edgeType: e.edge_type,
        color: edgeColor(e.edge_type, currentTheme()),
        weight: e.weight,
      },
    });

    scheduleLayout();
  };

  const handleEvent = (event: AnyEvent): void => {
    switch (event.event_type) {
      case "node_stored":
        handleNodeStored(event as NodeStored);
        break;
      case "node_status_changed":
        handleNodeStatusChanged(event as NodeStatusChanged);
        break;
      case "edge_stored":
        handleEdgeStored(event as EdgeStored);
        break;
    }
  };

  // --- Node selection ---

  state.cy.on("tap", "node", (evt) => {
    const node = evt.target;
    onNodeSelect(
      node.data("id"),
      node.data("content") ?? "",
      node.data("nodeType") ?? "",
    );
  });

  // --- Clear and snapshot ---

  const clearGraph = (): void => {
    state.cy.elements().remove();
  };

  const loadSnapshot = (nodes: NodeView[], edges: EdgeView[]): void => {
    clearGraph();

    for (const n of nodes) {
      state.cy.add({
        group: "nodes",
        data: {
          id: n.node_id,
          label: truncate(n.content, 40),
          content: n.content,
          nodeType: n.node_type,
          status: n.status,
          color: nodeColor(n.node_type, currentTheme()),
          opacity: statusOpacity(n.status),
        },
      });
    }

    for (const e of edges) {
      // Only add edge if both endpoints exist
      if (
        state.cy.getElementById(e.src_id).length === 0 ||
        state.cy.getElementById(e.dst_id).length === 0
      ) {
        continue;
      }

      state.cy.add({
        group: "edges",
        data: {
          id: e.edge_id,
          source: e.src_id,
          target: e.dst_id,
          edgeType: e.edge_type,
          color: edgeColor(e.edge_type, currentTheme()),
          weight: e.weight,
        },
      });
    }

    if (nodes.length > 0) {
      runLayout(state);
    }
  };

  // --- Subscribe to events ---

  const unsubs = [
    router.subscribe("node_stored", handleEvent),
    router.subscribe("node_status_changed", handleEvent),
    router.subscribe("edge_stored", handleEvent),
  ];

  /**
   * Highlight a set of nodes selected elsewhere — currently a timepoint on the
   * timeline panel, whose linked nodes these are. Replaces any previous
   * highlight rather than adding to it, so selection reads as one thing.
   */
  const highlightNodes = (nodeIds: readonly string[]): void => {
    state.cy.nodes().removeClass("highlighted");
    for (const id of nodeIds) {
      state.cy.getElementById(id).addClass("highlighted");
    }
  };

  /**
   * Swap the stylesheet for the current theme.
   *
   * Restyling rather than reloading: cytoscape keeps positions and selection
   * across a style change, so toggling the theme must not scatter a layout the
   * user has already read.
   */
  const applyTheme = (): void => {
    // Neutrals live in the stylesheet; hues live in each element's data, so a
    // theme switch has to touch both or half the canvas keeps the old theme.
    const theme = currentTheme();
    state.cy.nodes().forEach((n) => {
      n.data("color", nodeColor(n.data("nodeType") ?? "", theme));
    });
    state.cy.edges().forEach((e) => {
      e.data("color", edgeColor(e.data("edgeType") ?? "", theme));
    });
    state.cy.style(stylesheetFor(currentPalette()));
  };

  const cleanup = (): void => {
    unsubs.forEach((u) => u());
    state.cy.destroy();
  };

  return { cleanup, clearGraph, loadSnapshot, highlightNodes, applyTheme };
};

// --- Layout ---

const runLayout = (state: GraphPanelState): void => {
  const config = LAYOUT_CONFIGS[state.currentLayout] ?? LAYOUT_CONFIGS.dagre;

  // Apply filter
  if (state.currentFilter !== "all") {
    state.cy.nodes().forEach((n) => {
      const visible = n.data("nodeType") === state.currentFilter;
      if (visible) n.style("display", "element");
      else n.style("display", "none");
    });
  } else {
    state.cy.nodes().style("display", "element");
  }

  const layout = state.cy.layout(config as LayoutOptions);
  layout.run();
};

/**
 * Bind layout/filter controls to the graph panel.
 */
export const bindGraphControls = (
  layoutSelect: HTMLSelectElement,
  filterSelect: HTMLSelectElement,
  cy: Core,
  state: { currentLayout: string; currentFilter: string },
): void => {
  layoutSelect.addEventListener("change", () => {
    state.currentLayout = layoutSelect.value;
    runLayout({ ...state, cy, pendingLayout: null });
  });

  filterSelect.addEventListener("change", () => {
    state.currentFilter = filterSelect.value;
    runLayout({ ...state, cy, pendingLayout: null });
  });
};
