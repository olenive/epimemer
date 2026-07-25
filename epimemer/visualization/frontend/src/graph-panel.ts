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

const NODE_COLORS: Record<string, string> = {
  topic: "#6366f1",      // indigo
  fact: "#22c55e",       // green
  inference: "#f59e0b",  // amber
  segment: "#64748b",    // slate
  document: "#94a3b8",   // light slate
};

const EDGE_COLORS: Record<string, string> = {
  supports: "#4ade80",
  abstracts: "#facc15",
  subtopic_of: "#818cf8",
  similarity: "#38bdf8",
  contradiction: "#ef4444",
  derived_from: "#a78bfa",
  superseded_by: "#6b7280",
  merged_into: "#6b7280",
};

const STATUS_OPACITY: Record<string, number> = {
  active: 1.0,
  superseded: 0.3,
  merged: 0.3,
};

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

// Cast needed: Cytoscape types don't model data() mappers for numeric fields
const STYLESHEET = [
  {
    selector: "node",
    style: {
      label: "data(label)",
      "text-wrap": "ellipsis",
      "text-max-width": "120px",
      "font-size": "10px",
      color: "#d1d5db",
      "text-valign": "bottom",
      "text-margin-y": 6,
      "background-color": "data(color)",
      opacity: "data(opacity)",
      width: 24,
      height: 24,
      "border-width": 2,
      "border-color": "#1f2937",
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
      style: STYLESHEET,
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
      existing.data("opacity", STATUS_OPACITY[n.status] ?? 1.0);
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
        color: NODE_COLORS[n.node_type] ?? "#9ca3af",
        opacity: STATUS_OPACITY[n.status] ?? 1.0,
      },
    });

    scheduleLayout();
  };

  const handleNodeStatusChanged = (event: NodeStatusChanged): void => {
    const node = state.cy.getElementById(event.node_id);
    if (node.length > 0) {
      node.data("status", event.new_status);
      node.data("opacity", STATUS_OPACITY[event.new_status] ?? 1.0);
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
        color: EDGE_COLORS[e.edge_type] ?? "#6b7280",
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
          color: NODE_COLORS[n.node_type] ?? "#9ca3af",
          opacity: STATUS_OPACITY[n.status] ?? 1.0,
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
          color: EDGE_COLORS[e.edge_type] ?? "#6b7280",
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

  const cleanup = (): void => {
    unsubs.forEach((u) => u());
    state.cy.destroy();
  };

  return { cleanup, clearGraph, loadSnapshot };
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
