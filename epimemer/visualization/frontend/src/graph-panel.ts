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
  desaturate,
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
  // Lineage too, and deliberately the same hue: which of the two events
  // happened is carried by the *node's* status colour, so tinting the edge
  // would say it twice and leave the two readings free to disagree. Missing
  // here it would fall through to `UNKNOWN_KIND` grey — the #55 failure, where
  // a status the backend had grown drew as a kind the frontend never heard of.
  temporally_followed_by: "lineage",
  merged_into: "lineage",
  // A judgment about a pair, written whichever way the verdict went (#64). It
  // is here for the reason the two above are: the backend grew an edge type,
  // and a type this table has never heard of draws as `UNKNOWN_KIND` grey —
  // which is the #55 failure, one layer over.
  assessed: "assessed",
  // An earlier `one_claim` verdict withdrawn (#68). The `similarity` edge stays
  // in the graph — nothing deletes — so both draw, and they must not draw
  // alike: `similarity` in its own hue would show agreement that has been
  // taken back. It takes `contradiction`'s, which is the other edge that stops
  // a similarity partner counting, for the same reason and by the same route.
  retracted_similarity: "contradiction",
  // Cross-frame variants. Found missing while adding the row above — it has
  // been drawing as `UNKNOWN_KIND` grey since it was introduced, which is
  // exactly the failure the two comments above warn about, live.
  variant_of: "similarity",
};

/** Fallback for a kind this build has never heard of: a plain neutral. */
const UNKNOWN_KIND = "#9ca3af";

export const nodeColor = (nodeType: string, theme: Theme): string => {
  const meaning = NODE_MEANINGS[nodeType];
  return meaning ? semanticPaletteFor(theme)[meaning] : UNKNOWN_KIND;
};

/**
 * The colour a node draws in. **Every caller goes through here** — including
 * `applyTheme`, which is where focus state was previously lost.
 *
 * Cytoscape has no saturation property: nodes draw as
 * `background-color: data(color)`, so desaturation is a *computed colour*
 * written into `data("color")` rather than a channel the renderer blends for
 * us. That is why focus is an argument to the colour rather than a later
 * mutation of it — `applyTheme` recomputing from type and theme alone would
 * restore every node to full saturation and silently exit the mode
 * (RETRIEVAL_PROVENANCE.md §4.1).
 *
 * `statusOpacity` is untouched. The two channels never meet in a caller.
 */
export const nodeFill = (nodeType: string, theme: Theme, inFocus: boolean): string =>
  inFocus ? nodeColor(nodeType, theme) : desaturate(nodeColor(nodeType, theme));

/** What a node whose data says this should draw as, after a theme change. */
export const refreshedFill = (
  data: { nodeType?: string; inFocus?: boolean },
  theme: Theme,
): string => nodeFill(data.nodeType ?? "", theme, data.inFocus ?? true);

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

// --- Highlighting, and its two silent failures ---
//
// `highlightNodes` is driven from elsewhere — a timepoint on the timeline, a
// log entry, a retrieval record — and it could fail in two ways that looked
// identical from the outside and said nothing: an id this graph does not hold
// (`getElementById` returns an empty collection, `.addClass` is a no-op), and a
// node the type filter has set `display: none` on. Click, nothing happens, no
// explanation. Both are closed here rather than at each caller
// (EVENT_LOG.md §7, RETRIEVAL_PROVENANCE.md §4.4).

export interface HighlightReport {
  highlighted: string[];
  /** Requested ids this graph does not hold. */
  missing: string[];
  /** A type filter was cleared so the highlight would be visible. */
  filterCleared: boolean;
}

/** Which of `wanted` the graph does not hold, in the order asked for. */
export const missingFrom = (
  present: readonly string[],
  wanted: readonly string[],
): string[] => {
  const held = new Set(present);
  return wanted.filter((id) => !held.has(id));
};

/**
 * The type filter to leave in place so every highlighted node is visible.
 *
 * Cleared only when it would actually hide one of them. Clearing
 * unconditionally would undo a filter the user set, every time they clicked an
 * entry about a node that filter already showed.
 */
export const filterAfterHighlight = (
  currentFilter: string,
  types: readonly string[],
): string =>
  currentFilter === "all" || types.every((type) => type === currentFilter)
    ? currentFilter
    : "all";

/** What to tell the user, or null when nothing needs saying. */
export const highlightNote = (report: HighlightReport): string | null => {
  const parts: string[] = [];
  if (report.missing.length > 0) {
    const n = report.missing.length;
    parts.push(`${n} node${n === 1 ? " is" : "s are"} not in this graph`);
  }
  if (report.filterCleared) parts.push("type filter cleared to show them");
  return parts.length > 0 ? parts.join("; ") : null;
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
  /**
   * Mark exactly these nodes; an empty list clears the highlight.
   *
   * Reports what it could not do, so a caller can say so rather than leaving a
   * click that appears to have been ignored.
   */
  highlightNodes: (nodeIds: readonly string[]) => HighlightReport;
  /** Re-style the canvas for the current theme, preserving layout and selection. */
  applyTheme: () => void;
  /**
   * Dim everything this retrieval did not return; `null` leaves focus mode.
   *
   * Dimmed nodes stay drawn, hoverable and clickable — the interesting click
   * is on a dimmed node (*why didn't this come back?*), and making them inert
   * would remove the answer the mode exists to give (§4.3).
   */
  setFocus: (nodeIds: readonly string[] | null) => void;
  /** Whether a node is in the current retrieval; true when focus is off. */
  isInFocus: (nodeId: string) => boolean;
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

  // The retrieval a record returned, or null when focus mode is off. Null is
  // *not* an empty set: nothing is dimmed until a record is selected, and an
  // empty retrieval dims everything, which is the honest picture of a search
  // that came back with nothing.
  let focused: ReadonlySet<string> | null = null;
  const inFocus = (nodeId: string): boolean =>
    focused === null || focused.has(nodeId);

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
        inFocus: inFocus(n.node_id),
        color: nodeFill(n.node_type, currentTheme(), inFocus(n.node_id)),
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
          inFocus: inFocus(n.node_id),
          color: nodeFill(n.node_type, currentTheme(), inFocus(n.node_id)),
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
   * Highlight a set of nodes selected elsewhere — a timepoint on the timeline,
   * an entry in the log, a record in the retrieval selector. Replaces any
   * previous highlight rather than adding to it, so selection reads as one
   * thing.
   *
   * Clears a type filter that would hide what is being highlighted, and returns
   * the ids this graph does not hold. Both were silent before: the class landed
   * on an empty collection, or on something with `display: none`.
   */
  const highlightNodes = (nodeIds: readonly string[]): HighlightReport => {
    state.cy.nodes().removeClass("highlighted");
    const present = nodeIds.filter((id) => state.cy.getElementById(id).length > 0);
    const types = present.map(
      (id) => (state.cy.getElementById(id).data("nodeType") as string) ?? "",
    );

    const filter = filterAfterHighlight(state.currentFilter, types);
    const filterCleared = filter !== state.currentFilter;
    if (filterCleared) {
      state.currentFilter = filter;
      if (controls) controls.filterSelect.value = filter;
      runLayout(state);
    }

    for (const id of present) {
      state.cy.getElementById(id).addClass("highlighted");
    }
    return {
      highlighted: [...present],
      missing: missingFrom(present, nodeIds),
      filterCleared,
    };
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
    //
    // Through `refreshedFill`, so focus survives. Recomputing from node type
    // and theme alone — which this did — restored every node to full
    // saturation and silently left focus mode.
    const theme = currentTheme();
    state.cy.nodes().forEach((n) => {
      n.data("color", refreshedFill(n.data(), theme));
    });
    state.cy.edges().forEach((e) => {
      e.data("color", edgeColor(e.data("edgeType") ?? "", theme));
    });
    state.cy.style(stylesheetFor(currentPalette()));
  };

  const setFocus = (nodeIds: readonly string[] | null): void => {
    focused = nodeIds === null ? null : new Set(nodeIds);
    const theme = currentTheme();
    state.cy.nodes().forEach((n) => {
      const focus = inFocus(n.data("id"));
      n.data("inFocus", focus);
      n.data("color", nodeFill(n.data("nodeType") ?? "", theme, focus));
    });
  };

  const cleanup = (): void => {
    unsubs.forEach((u) => u());
    state.cy.destroy();
  };

  return {
    cleanup,
    clearGraph,
    loadSnapshot,
    highlightNodes,
    applyTheme,
    setFocus,
    isInFocus: inFocus,
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
