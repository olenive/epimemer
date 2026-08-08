/**
 * Entry point for the Epimemer visualization frontend.
 *
 * The hub serves many MCP sessions at once. The header's session selector
 * chooses which one to view; everything below (graph list, snapshot, live
 * events) is scoped to that session. Disconnected sessions stay listed (greyed)
 * until the hub drops them.
 */

import "./style.css";

import { fetchGraphs, fetchSessions, fetchSnapshot } from "./api";
import { createEventRouter } from "./events";
import { initGraphPanel } from "./graph-panel";
import { initPipelineStrip } from "./pipeline-strip";
import { initSplitPane } from "./split-pane";
import { initTimelinePanel } from "./timeline-panel";
import type { TimelineMark } from "./timeline-model";
import {
  applyTheme,
  currentTheme,
  nextTheme,
  persistTheme,
  storedTheme,
  themeToggleIcon,
  themeToggleTitle,
  type Theme,
} from "./theme";
import {
  applyReflectCounterEvent,
  reflectBadgeClass,
  reflectBadgeLabel,
  reflectBadgeTitle,
  seedReflectState,
  unknownReflectState,
} from "./reflect-badge";
import type {
  AnyEvent,
  GraphSwitched,
  ReflectCounterUpdated,
  SessionInfo,
  SystemMessage,
} from "./types";

// --- DOM element lookup ---

const $ = <T extends HTMLElement>(id: string): T => {
  const el = document.getElementById(id);
  if (!el) throw new Error(`Element #${id} not found`);
  return el as T;
};

// --- State ---

let sessions: SessionInfo[] = [];
let selectedSession: string | null = null;
let viewedGraph = "";
let mcpActiveGraph = "";
let mcpBackend = "";

// --- Initialize ---

const wsUrl = `${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/ws`;

const wsStatus = $("ws-status");
const sessionSelector = $<HTMLSelectElement>("session-selector");
const mcpActiveLabel = $("mcp-active-graph");
const graphSelector = $<HTMLSelectElement>("graph-selector");
const viewModeBadge = $("view-mode-badge");
const reflectBadge = $("reflect-badge");
const btnRefresh = $("btn-refresh");

const router = createEventRouter(wsUrl, (connected) => {
  wsStatus.textContent = connected ? "Connected" : "Disconnected";
  wsStatus.className = connected
    ? "px-2 py-1 text-xs rounded bg-green-100 text-green-700 dark:bg-green-900/50 dark:text-green-400"
    : "px-2 py-1 text-xs rounded bg-red-100 text-red-700 dark:bg-red-900/50 dark:text-red-400";

  // The hub or sessions may have changed while we were away — resync.
  if (connected) {
    refreshSessions();
  }
});

// --- Detail drawer ---
//
// A fixed-height strip that stays in the layout once opened. The timeline is a
// hover target, and a drawer that appeared, grew or shrank with its text would
// re-lay out the panel under the cursor — the mark you were reading moves away
// from the pointer as you read it. Only the close button changes the layout,
// because that is a deliberate act rather than a side effect of looking.

const detailDrawer = $("detail-drawer");
const detailTitle = $("detail-title");
const detailContent = $("detail-content");

const showDetail = (nodeId: string, content: string, nodeType: string): void => {
  detailTitle.textContent = `${nodeType} — ${nodeId.slice(0, 8)}`;
  detailContent.textContent = content;
  detailDrawer.classList.remove("hidden");
};

const clearDetail = (): void => {
  detailTitle.textContent = "";
  detailContent.textContent = "";
};

$("btn-close-detail").addEventListener("click", () => {
  clearDetail();
  detailDrawer.classList.add("hidden");
});

// --- Pipeline strip ---

const pipelineStrip = initPipelineStrip(router, {
  strip: $("pipeline-strip"),
  hint: $("pipeline-strip-hint"),
  detail: $("pipeline-detail"),
  detailTitle: $("pipeline-detail-title"),
  detailSvg: $("pipeline-detail-svg"),
  detailClose: $("pipeline-detail-close"),
});

const stripRow = $("pipeline-strip");
$("btn-toggle-pipeline").addEventListener("click", () => {
  stripRow.classList.toggle("hidden");
});

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") pipelineStrip.closeDetail();
});

// --- Knowledge graph panel ---

const graphPanel = initGraphPanel(
  $("graph-container"),
  router,
  showDetail,
  {
    layoutSelect: $<HTMLSelectElement>("graph-layout"),
    filterSelect: $<HTMLSelectElement>("graph-filter"),
  },
);

// --- Split pane: the graph and the timeline share the width ---

initSplitPane({
  container: $("split-container"),
  left: $("split-left"),
  right: $("split-right"),
  divider: $("split-divider"),
  toggleLeft: $("btn-toggle-graph"),
  toggleRight: $("btn-toggle-timeline"),
});

// --- Timeline panel ---

const timelinePanel = initTimelinePanel(
  router,
  {
    body: $("timeline-body"),
    empty: $("timeline-empty"),
    undated: $("timeline-undated"),
    timelineSelect: $<HTMLSelectElement>("timeline-select"),
    nowButton: $("timeline-now"),
    modeSelect: $<HTMLSelectElement>("timeline-mode"),
    typeSelect: $<HTMLSelectElement>("timeline-type"),
    statusSelect: $<HTMLSelectElement>("timeline-status"),
    metacontextSelect: $<HTMLSelectElement>("timeline-metacontext"),
    queryInput: $<HTMLInputElement>("timeline-query"),
    rangeStart: $<HTMLInputElement>("timeline-range-start"),
    rangeEnd: $<HTMLInputElement>("timeline-range-end"),
    resetButton: $("timeline-reset-zoom"),
  },
  (mark: TimelineMark | null) => {
    // A timepoint is not a graph node, so the bridge is the nodes linked to it.
    graphPanel.highlightNodes(mark?.nodeIds ?? []);
    if (mark) showDetail(mark.id, mark.detail, "timepoint");
  },
);



// --- Theme ---

const themeButton = $("btn-toggle-theme");

/**
 * Put a theme on the page.
 *
 * The class is what Tailwind reads, but the graph canvas and the timeline SVG
 * are drawn rather than styled, so each has to be told separately.
 */
const useTheme = (theme: Theme): void => {
  applyTheme(theme);
  themeButton.textContent = themeToggleIcon(theme);
  themeButton.title = themeToggleTitle(theme);
  graphPanel.applyTheme();
  timelinePanel.refresh();
  pipelineStrip.repaintDetail();
};

// The inline script in index.html already set the class before first paint, so
// read that back rather than resolving a second time and risking disagreement.
useTheme(currentTheme());

themeButton.addEventListener("click", () => {
  const theme = nextTheme(currentTheme());
  persistTheme(theme);
  useTheme(theme);
});

// Follow the OS while the user has expressed no preference of their own.
if (typeof matchMedia === "function") {
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", (e) => {
    if (storedTheme() === null) useTheme(e.matches ? "dark" : "light");
  });
}

// --- Mode badge ---

const updateModeBadge = (): void => {
  const isLive = viewedGraph === mcpActiveGraph;
  viewModeBadge.textContent = isLive ? "Live" : "Snapshot";
  viewModeBadge.className = isLive
    ? "px-1.5 py-0.5 rounded text-xs bg-green-100 text-green-700 dark:bg-green-900/50 dark:text-green-400"
    : "px-1.5 py-0.5 rounded text-xs bg-gray-200 text-gray-600 dark:bg-gray-700 dark:text-gray-400";
};

let reflectState = unknownReflectState();

const renderReflectBadge = (): void => {
  reflectBadge.textContent = reflectBadgeLabel(reflectState);
  reflectBadge.className = reflectBadgeClass(reflectState);
  reflectBadge.title = reflectBadgeTitle(reflectState);
};

const updateMcpLabel = (): void => {
  mcpActiveLabel.textContent = mcpActiveGraph
    ? `${mcpActiveGraph}${mcpBackend ? ` (${mcpBackend})` : ""}`
    : "-";
};

// --- Graph loading ---

const loadGraphSnapshot = async (graph: string): Promise<void> => {
  if (!selectedSession) return;
  try {
    const snapshot = await fetchSnapshot(selectedSession, graph);
    graphPanel.clearGraph();
    // NB: do not clear the pipeline strip here — a snapshot reload must not wipe
    // pipeline history (run counts, glyphs). The strip is cleared only on a
    // session switch.
    graphPanel.loadSnapshot(snapshot.nodes, snapshot.edges);
    timelinePanel.loadSnapshot({
      nodes: snapshot.nodes,
      edges: snapshot.edges,
      timelines: snapshot.timelines,
      metacontexts: snapshot.metacontexts,
    });
    btnRefresh.classList.remove("ring-2", "ring-amber-500");
  } catch (err) {
    console.error("Failed to load snapshot:", err);
  }
};

const switchViewedGraph = async (graph: string): Promise<void> => {
  viewedGraph = graph;
  router.setSessionSubscription({ session: selectedSession, graphs: [graph] });
  updateModeBadge();
  await loadGraphSnapshot(graph);
};

// --- Selectors ---

const populateGraphSelector = (graphs: string[], activeGraph: string): void => {
  graphSelector.innerHTML = "";
  for (const g of graphs) {
    const opt = document.createElement("option");
    opt.value = g;
    opt.textContent = g;
    graphSelector.appendChild(opt);
  }
  graphSelector.value = activeGraph;
};

const sessionLabel = (s: SessionInfo): string => {
  const base = `${s.backend}:${s.active_graph} (pid ${s.pid})`;
  return s.connected ? base : `${base} — disconnected`;
};

const populateSessionSelector = (): void => {
  sessionSelector.innerHTML = "";
  if (sessions.length === 0) {
    const opt = document.createElement("option");
    opt.value = "";
    opt.textContent = "No sessions";
    sessionSelector.appendChild(opt);
    return;
  }
  for (const s of sessions) {
    const opt = document.createElement("option");
    opt.value = s.session_id;
    opt.textContent = sessionLabel(s);
    opt.disabled = !s.connected;
    if (!s.connected) opt.className = "text-gray-400 dark:text-gray-600";
    sessionSelector.appendChild(opt);
  }
  if (selectedSession) sessionSelector.value = selectedSession;
};

const pickDefaultSession = (): string | null => {
  const connected = sessions.filter((s) => s.connected);
  const pool = connected.length > 0 ? connected : sessions;
  if (pool.length === 0) return null;
  // Most recently active first.
  const sorted = [...pool].sort((a, b) => {
    const ta = a.last_event_at ? Date.parse(a.last_event_at) : 0;
    const tb = b.last_event_at ? Date.parse(b.last_event_at) : 0;
    return tb - ta;
  });
  return sorted[0].session_id;
};

// --- Session selection ---

const selectSession = async (sessionId: string): Promise<void> => {
  // A different session has its own pipelines — start its strip fresh.
  if (sessionId !== selectedSession) pipelineStrip.clearAll();
  selectedSession = sessionId;
  sessionSelector.value = sessionId;
  try {
    const { graphs, active_graph, backend, reflect } = await fetchGraphs(sessionId);
    mcpActiveGraph = active_graph;
    mcpBackend = backend;
    updateMcpLabel();
    // Seed from the listing so the badge is right on arrival; events move it
    // from here.
    reflectState = seedReflectState(reflect);
    renderReflectBadge();
    populateGraphSelector(graphs, active_graph);
    await switchViewedGraph(active_graph);
  } catch (err) {
    console.error("Failed to select session:", err);
  }
};

const upsertSession = (info: SessionInfo): void => {
  const idx = sessions.findIndex((s) => s.session_id === info.session_id);
  if (idx >= 0) sessions[idx] = info;
  else sessions.push(info);
};

const refreshSessions = async (): Promise<void> => {
  try {
    sessions = await fetchSessions();
  } catch (err) {
    console.error("Failed to fetch sessions:", err);
    return;
  }
  // Keep the current selection if it still exists; otherwise pick a default.
  if (!selectedSession || !sessions.some((s) => s.session_id === selectedSession)) {
    const next = pickDefaultSession();
    populateSessionSelector();
    if (next) await selectSession(next);
  } else {
    populateSessionSelector();
    // Reload the selected session's snapshot in case we missed events while away.
    if (viewedGraph) await loadGraphSnapshot(viewedGraph);
  }
};

// --- Event handlers ---

sessionSelector.addEventListener("change", () => {
  if (sessionSelector.value) selectSession(sessionSelector.value);
});

graphSelector.addEventListener("change", () => {
  switchViewedGraph(graphSelector.value);
});

btnRefresh.addEventListener("click", () => {
  if (viewedGraph) loadGraphSnapshot(viewedGraph);
});

router.onGapDetected(() => {
  btnRefresh.classList.add("ring-2", "ring-amber-500");
  pipelineStrip.markStale();
});

// Hub system messages — keep the session selector current.
router.onSystemMessage((msg: SystemMessage) => {
  if (msg.type === "session_connected") {
    upsertSession(msg.session);
    if (!selectedSession) {
      populateSessionSelector();
      selectSession(msg.session.session_id);
    } else {
      populateSessionSelector();
    }
  } else if (msg.type === "session_disconnected") {
    const s = sessions.find((x) => x.session_id === msg.session_id);
    if (s) s.connected = false;
    populateSessionSelector();
  } else if (msg.type === "session_dropped") {
    sessions = sessions.filter((x) => x.session_id !== msg.session_id);
    if (selectedSession === msg.session_id) {
      selectedSession = null;
      const next = pickDefaultSession();
      populateSessionSelector();
      if (next) selectSession(next);
    } else {
      populateSessionSelector();
    }
  }
});

// ReflectCounterUpdated — the viewed session's pressure moved.
router.subscribe("reflect_counter_updated", (event: AnyEvent) => {
  const e = event as ReflectCounterUpdated;
  if (e.session_id && e.session_id !== selectedSession) return;
  reflectState = applyReflectCounterEvent(reflectState, e);
  renderReflectBadge();
});

// GraphSwitched — MCP changed its active graph. Only for the viewed session.
router.subscribe("graph_switched", (event: AnyEvent) => {
  const e = event as GraphSwitched;
  if (e.session_id && e.session_id !== selectedSession) return;
  mcpActiveGraph = e.new_graph;
  updateMcpLabel();
  updateModeBadge();

  if (!selectedSession) return;
  fetchGraphs(selectedSession)
    .then(({ graphs, reflect }) => {
      populateGraphSelector(graphs, viewedGraph);
      // The counter belongs to the graph, so a switch changes it — re-seed
      // rather than leaving the old graph's number on screen.
      reflectState = seedReflectState(reflect);
      renderReflectBadge();
    })
    .catch(console.error);
});

// --- Initial load ---

const init = async (): Promise<void> => {
  await refreshSessions();
};

init();
