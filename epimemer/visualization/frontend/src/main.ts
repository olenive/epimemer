/**
 * Entry point for the Epimemer visualization frontend.
 *
 * Wires together the WebSocket event router, split pane layout,
 * pipeline panel, and knowledge graph panel.
 */

import "./style.css";

import { createEventRouter } from "./events";
import { initGraphPanel } from "./graph-panel";
import { initPipelinePanel } from "./pipeline-panel";
import { initSplitPane } from "./split-pane";

// --- DOM element lookup ---

const $ = <T extends HTMLElement>(id: string): T => {
  const el = document.getElementById(id);
  if (!el) throw new Error(`Element #${id} not found`);
  return el as T;
};

// --- Initialize ---

const wsUrl = `${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/ws`;

const wsStatus = $("ws-status");

const router = createEventRouter(wsUrl, (connected) => {
  wsStatus.textContent = connected ? "Connected" : "Disconnected";
  wsStatus.className = connected
    ? "px-2 py-1 text-xs rounded bg-green-900/50 text-green-400"
    : "px-2 py-1 text-xs rounded bg-red-900/50 text-red-400";
});

// --- Split pane ---

initSplitPane({
  leftPanel: $("panel-pipeline"),
  rightPanel: $("panel-graph"),
  handle: $("resize-handle"),
  toggleLeft: $("btn-toggle-pipeline"),
  toggleRight: $("btn-toggle-graph"),
});

// --- Detail drawer ---

const detailDrawer = $("detail-drawer");
const detailTitle = $("detail-title");
const detailContent = $("detail-content");

const showDetail = (nodeId: string, content: string, nodeType: string): void => {
  detailTitle.textContent = `${nodeType} — ${nodeId.slice(0, 8)}`;
  detailContent.textContent = content;
  detailDrawer.classList.remove("hidden");
};

$("btn-close-detail").addEventListener("click", () => {
  detailDrawer.classList.add("hidden");
});

// --- Pipeline panel ---

const pipelineStatus = $("pipeline-status");

initPipelinePanel(
  $("pipeline-container"),
  router,
  (status) => {
    pipelineStatus.textContent = status;
  },
);

// --- Knowledge graph panel ---

initGraphPanel(
  $("graph-container"),
  router,
  showDetail,
);
