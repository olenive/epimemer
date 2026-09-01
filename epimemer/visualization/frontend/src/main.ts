/**
 * Entry point for the Epimemer visualization frontend.
 *
 * The hub serves many MCP sessions at once. The header's session selector
 * chooses which one to view; everything below (graph list, snapshot, live
 * events) is scoped to that session. Disconnected sessions stay listed (greyed)
 * until the hub drops them.
 */

import "./style.css";

import { fetchGraphs, fetchRetrievals, fetchSessions, fetchSnapshot } from "./api";
import { initDrawer, notRetrievedMarker } from "./drawer";
import { createEventRouter } from "./events";
import { highlightNote, initGraphPanel } from "./graph-panel";
import { initLogPanel } from "./log-panel";
import { initPipelineStrip } from "./pipeline-strip";
import { initRetrievalSelector } from "./retrieval-selector";
import { responseText, type RecordEntry } from "./retrieval-store";
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
  type StoredPalette,
  applyPalette,
  clearStoredPalette,
  noOverrides,
  persistPalette,
  readStoredPalette,
  resetRequested,
} from "./palette-store";
import { initPalettePicker } from "./palette-picker";
import {
  applyReflectCounterEvent,
  reflectBadgeClass,
  reflectBadgeLabel,
  reflectBadgeTitle,
  seedReflectState,
  unknownReflectState,
} from "./reflect-badge";
import {
  graphSelectorOptions,
  graphSelectorTitle,
  hubErrorText,
  pickDefaultSession,
  sessionLabel,
  type GraphListState,
} from "./session-select";
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
// Sessions the hub lists as connected but which cannot answer — see
// `session-select.ts`. Only asking reveals this, so it accumulates here.
const unreachable = new Set<string>();

// --- Initialize ---

const wsUrl = `${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/ws`;

const wsStatus = $("ws-status");
const sessionSelector = $<HTMLSelectElement>("session-selector");
const mcpActiveLabel = $("mcp-active-graph");
const graphSelector = $<HTMLSelectElement>("graph-selector");
const viewModeBadge = $("view-mode-badge");
const reflectBadge = $("reflect-badge");
const btnRefresh = $("btn-refresh");

// "Hub", not "Connected": this badge reports the browser's socket to the hub and
// nothing else. Green here while every panel sits empty is exactly what made the
// 2026-08-10 failure read as a frontend bug.
const router = createEventRouter(wsUrl, (connected) => {
  wsStatus.textContent = connected ? "Hub connected" : "Hub disconnected";
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

const drawer = initDrawer({
  drawer: $("detail-drawer"),
  tabNode: $<HTMLButtonElement>("tab-node"),
  tabResponse: $<HTMLButtonElement>("tab-response"),
  title: $("detail-title"),
  content: $("detail-content"),
  close: $("btn-close-detail"),
});

/**
 * Open a node's detail, saying so when the current retrieval did not return it.
 *
 * Dimmed nodes stay clickable on purpose: the interesting click is on one that
 * did *not* come back, and the marker states the absence rather than leaving it
 * implied by the colour (§4.3).
 */
const showDetail = (nodeId: string, content: string, nodeType: string): void => {
  drawer.showNode(
    `${nodeType} — ${nodeId.slice(0, 8)}`,
    content,
    notRetrievedMarker(graphPanel.isInFocus(nodeId), recordSelector.selected() !== null),
  );
};

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
  // Selection is bidirectional (§7): a log entry highlights its nodes, and a
  // node narrows the log to what was done to it. The rail is not revealed —
  // ambient signal, deliberate detail — so the filter is simply there when you
  // open it. `logPanel` is initialised below and only read at click time.
  (nodeId, content, nodeType) => {
    showDetail(nodeId, content, nodeType);
    logPanel.filterToNode(nodeId);
  },
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



// --- Activity log ---

const logPanel = initLogPanel(
  {
    rail: $("log-rail"),
    entries: $("log-entries"),
    empty: $("log-empty"),
    verbs: $("log-verbs"),
    nodeId: $<HTMLInputElement>("log-node-id"),
    text: $<HTMLInputElement>("log-text"),
    rangeStart: $<HTMLInputElement>("log-range-start"),
    rangeEnd: $<HTMLInputElement>("log-range-end"),
    clear: $("log-clear"),
    count: $("log-count"),
    note: $("log-note"),
  },
  router,
  // Click an entry, highlight what it acted on. `highlightNodes` reports what
  // it could not do — an id this graph does not hold, a type filter it had to
  // clear — and the rail says so, because a highlight that lands on nothing
  // looks exactly like a broken panel (§7).
  (entry) => highlightNote(graphPanel.highlightNodes(entry.subjects)),
);

$("btn-toggle-log").addEventListener("click", () => logPanel.toggle());

// --- Retrieval provenance: focus mode ---

/**
 * Dim everything the chosen retrieval did not return, in **both** panels.
 *
 * Timeline marks are the same nodes, so dimming only the graph would leave the
 * two panels disagreeing about what came back — the class of bug #56 fixed for
 * colour. Focus state therefore lives here, above both, like the theme does.
 */
const applyFocus = (entry: RecordEntry | null): void => {
  const ids = entry === null ? null : entry.nodeIds;
  graphPanel.setFocus(ids);
  timelinePanel.setFocus(ids);
  // Selecting a record is a deliberate act, so this is the one path allowed to
  // open the drawer and change its tab (§5.2). A record merely *arriving* never
  // reaches here.
  if (entry !== null) {
    drawer.showResponse(
      `Response — ${entry.tool.replace(/^epimemer\./, "")}`,
      responseText(entry),
    );
  }
};

const recordSelector = initRetrievalSelector(
  { select: $<HTMLSelectElement>("record-selector"), unread: $("record-unread") },
  router,
  applyFocus,
);

// --- Theme ---

const themeButton = $("btn-toggle-theme");

// The escape hatch, taken before anything is drawn: a palette that has painted
// its own reset control shut is unreachable from inside the page.
if (resetRequested(location.search)) clearStoredPalette();

let palette = readStoredPalette();

/**
 * Put a theme on the page.
 *
 * The class is what Tailwind reads, but the graph canvas and the timeline SVG
 * are drawn rather than styled, so each has to be told separately.
 *
 * The viewer's overrides are re-applied on every switch because they are stored
 * per theme: the colours light mode resolves to are not the ones dark mode
 * does, and the inline properties left behind would otherwise be the previous
 * theme's.
 */
const useTheme = (theme: Theme): void => {
  applyTheme(theme);
  applyPalette(theme, palette);
  themeButton.textContent = themeToggleIcon(theme);
  themeButton.title = themeToggleTitle(theme);
  graphPanel.applyTheme();
  timelinePanel.refresh();
  pipelineStrip.repaintDetail();
};

// --- Colours ---

/**
 * Take a changed set of overrides.
 *
 * `persist` is false while a swatch is still being dragged: the page follows
 * every frame, storage records where the drag stopped.
 */
const usePalette = (next: StoredPalette, persist: boolean): void => {
  palette = next;
  if (persist) persistPalette(next);
  useTheme(currentTheme());
};

const palettePicker = initPalettePicker({
  button: $("btn-palette"),
  panel: $("palette-panel"),
  theme: () => currentTheme(),
  read: () => palette,
  write: usePalette,
  reset: () => {
    clearStoredPalette();
    usePalette(noOverrides(), false);
  },
});

/**
 * Switch theme, and rebuild the picker if it is open.
 *
 * The swatches show the colours of the theme being edited, and the overrides
 * behind them are stored per theme, so an open panel left alone across a switch
 * would offer the other theme's values as this one's.
 */
const switchTheme = (theme: Theme): void => {
  useTheme(theme);
  if (palettePicker.isOpen()) palettePicker.render();
};

// The inline script in index.html already set the class before first paint, so
// read that back rather than resolving a second time and risking disagreement.
useTheme(currentTheme());

themeButton.addEventListener("click", () => {
  const theme = nextTheme(currentTheme());
  persistTheme(theme);
  switchTheme(theme);
});

// Follow the OS while the user has expressed no preference of their own.
if (typeof matchMedia === "function") {
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", (e) => {
    if (storedTheme() === null) switchTheme(e.matches ? "dark" : "light");
  });
}

// --- Mode badge ---

const updateModeBadge = (): void => {
  const isLive = viewedGraph === mcpActiveGraph;
  viewModeBadge.textContent = isLive ? "Live" : "Snapshot";
  viewModeBadge.className = isLive
    ? "px-1.5 py-0.5 rounded text-xs bg-green-100 text-green-700 dark:bg-green-900/50 dark:text-green-400"
    : "px-1.5 py-0.5 rounded text-xs bg-surface-raised text-content-secondary";
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
    unreachable.delete(selectedSession);
  } catch (err) {
    console.error("Failed to load snapshot:", err);
    // Empty panels with no explanation is the failure this issue is about, so a
    // session that cannot produce a snapshot is labelled like one that cannot
    // list its graphs.
    unreachable.add(selectedSession);
    populateSessionSelector();
  }
};

const switchViewedGraph = async (graph: string): Promise<void> => {
  viewedGraph = graph;
  // Before the subscription changes, so the backfill the hub replays lands in
  // an empty log rather than behind the previous graph's entries.
  logPanel.setViewedGraph(graph);
  recordSelector.setViewedGraph(graph);
  router.setSessionSubscription({ session: selectedSession, graphs: [graph] });
  updateModeBadge();
  await loadGraphSnapshot(graph);
};

/**
 * Fold in the payloads the hub's mirror may not hold.
 *
 * Best-effort by design: the RPC fails once the session exits, and the mirror
 * is exactly what covers that case. A failure here means "no payloads", not
 * "no records" (§3.2).
 */
const loadRetrievals = async (session: string): Promise<void> => {
  try {
    recordSelector.merge((await fetchRetrievals(session)).records);
  } catch (err) {
    console.debug("Retrieval payloads unavailable from the session:", err);
  }
};

// --- Selectors ---

const renderGraphList = (state: GraphListState): void => {
  graphSelector.innerHTML = "";
  for (const { value, label } of graphSelectorOptions(state)) {
    const opt = document.createElement("option");
    opt.value = value;
    opt.textContent = label;
    graphSelector.appendChild(opt);
  }
  graphSelector.value = state.kind === "ready" ? state.active : "";
  graphSelector.title = graphSelectorTitle(state);
  graphSelector.disabled = state.kind !== "ready";
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
    opt.textContent = sessionLabel(s, unreachable.has(s.session_id));
    // A disconnected session has nothing to say. An unreachable one stays
    // selectable: it may recover, and picking it is how its reason is shown.
    opt.disabled = !s.connected;
    if (!s.connected) opt.className = "text-content-muted";
    else if (unreachable.has(s.session_id)) {
      opt.className = "text-amber-700 dark:text-amber-500";
    }
    sessionSelector.appendChild(opt);
  }
  if (selectedSession) sessionSelector.value = selectedSession;
};

// --- Session selection ---

/** Show a session, reporting `false` if it could not answer. */
const selectSession = async (sessionId: string): Promise<boolean> => {
  // A different session has its own pipelines and its own history — start both
  // fresh. Entries from one session must never read as another's.
  if (sessionId !== selectedSession) {
    pipelineStrip.clearAll();
    logPanel.clearAll();
    recordSelector.clearAll();
  }
  selectedSession = sessionId;
  sessionSelector.value = sessionId;
  renderGraphList({ kind: "loading" });
  try {
    const { graphs, active_graph, backend, reflect } = await fetchGraphs(sessionId);
    unreachable.delete(sessionId);
    mcpActiveGraph = active_graph;
    mcpBackend = backend;
    updateMcpLabel();
    // Seed from the listing so the badge is right on arrival; events move it
    // from here.
    reflectState = seedReflectState(reflect);
    renderReflectBadge();
    renderGraphList({ kind: "ready", graphs, active: active_graph });
    await switchViewedGraph(active_graph);
    await loadRetrievals(sessionId);
    return true;
  } catch (err) {
    // Say so on screen. The console is where this used to stop, which is why a
    // dead backend was indistinguishable from a slow one for an hour.
    console.error("Failed to select session:", err);
    unreachable.add(sessionId);
    viewedGraph = "";
    mcpActiveGraph = "";
    mcpBackend = "";
    updateMcpLabel();
    reflectState = unknownReflectState();
    renderReflectBadge();
    renderGraphList({ kind: "unavailable", reason: hubErrorText(err) });
    populateSessionSelector();
    return false;
  }
};

/**
 * Select the best session that will actually answer.
 *
 * Each failure demotes its session, so the next round picks a different one;
 * `tried` guarantees termination when every session is equally broken, and the
 * last attempt stays selected so its reason is on screen.
 */
const selectFirstWorkingSession = async (): Promise<void> => {
  const tried = new Set<string>();
  for (;;) {
    const next = pickDefaultSession(sessions, unreachable);
    if (next === null || tried.has(next)) return;
    tried.add(next);
    if (await selectSession(next)) return;
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
    populateSessionSelector();
    await selectFirstWorkingSession();
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
    // A re-register is a fresh process, or one that reconnected its storage —
    // either way, whatever we learned about it failing no longer applies.
    unreachable.delete(msg.session.session_id);
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
    unreachable.delete(msg.session_id);
    if (selectedSession === msg.session_id) {
      selectedSession = null;
      populateSessionSelector();
      selectFirstWorkingSession();
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
      renderGraphList({ kind: "ready", graphs, active: viewedGraph });
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
