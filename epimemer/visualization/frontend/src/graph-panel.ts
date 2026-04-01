/**
 * Knowledge graph visualization panel using Cytoscape.js.
 *
 * Renders epistemic nodes (topics, facts, inferences) and their edges.
 * Supports live updates via graph events from the WebSocket.
 */

import cytoscape, { type Core, type LayoutOptions } from "cytoscape";
// @ts-expect-error — cytoscape-dagre has no types
import dagre from "cytoscape-dagre";
import type { EventRouter } from "./events";
import type {
  EdgeStored,
  NodeStatusChanged,
  NodeStored,
  AnyEvent,
} from "./types";

cytoscape.use(dagre);

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

const LAYOUT_CONFIGS: Record<string, object> = {
  dagre: {
    name: "dagre",
    rankDir: "TB",
    rankSep: 60,
    nodeSep: 30,
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

/**
 * Initialize the knowledge graph panel.
 *
 * Returns a cleanup function that removes event subscriptions.
 */
export const initGraphPanel = (
  container: HTMLElement,
  router: EventRouter,
  onNodeSelect: (nodeId: string, content: string, nodeType: string) => void,
): (() => void) => {
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

  // --- Event handlers ---

  const scheduleLayout = (): void => {
    if (state.pendingLayout) clearTimeout(state.pendingLayout);
    state.pendingLayout = setTimeout(() => {
      state.pendingLayout = null;
      runLayout(state);
    }, 200);
  };

  const handleNodeStored = (event: NodeStored): void => {
    const existing = state.cy.getElementById(event.node_id);
    if (existing.length > 0) {
      existing.data("label", truncate(event.content, 40));
      existing.data("content", event.content);
      existing.data("status", event.status);
      existing.data("opacity", STATUS_OPACITY[event.status] ?? 1.0);
      return;
    }

    state.cy.add({
      group: "nodes",
      data: {
        id: event.node_id,
        label: truncate(event.content, 40),
        content: event.content,
        nodeType: event.node_type,
        status: event.status,
        color: NODE_COLORS[event.node_type] ?? "#9ca3af",
        opacity: STATUS_OPACITY[event.status] ?? 1.0,
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
    const edgeId = event.edge_id;
    if (state.cy.getElementById(edgeId).length > 0) return;

    // Only add edge if both endpoints exist
    if (
      state.cy.getElementById(event.src_id).length === 0 ||
      state.cy.getElementById(event.dst_id).length === 0
    ) {
      return;
    }

    state.cy.add({
      group: "edges",
      data: {
        id: edgeId,
        source: event.src_id,
        target: event.dst_id,
        edgeType: event.edge_type,
        color: EDGE_COLORS[event.edge_type] ?? "#6b7280",
        weight: event.weight,
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

  // --- Subscribe to events ---

  const unsubs = [
    router.subscribe("node_stored", handleEvent),
    router.subscribe("node_status_changed", handleEvent),
    router.subscribe("edge_stored", handleEvent),
  ];

  return () => {
    unsubs.forEach((u) => u());
    state.cy.destroy();
  };
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
