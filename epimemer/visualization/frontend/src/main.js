/**
 * Entry point for the Epimemer visualization frontend.
 *
 * Wires together the WebSocket event router, split pane layout,
 * pipeline panel, knowledge graph panel, and graph selector.
 */
import "./style.css";
import { fetchGraphs, fetchSnapshot } from "./api";
import { createEventRouter } from "./events";
import { initGraphPanel } from "./graph-panel";
import { initPipelinePanel } from "./pipeline-panel";
import { initSplitPane } from "./split-pane";
// --- DOM element lookup ---
const $ = (id) => {
    const el = document.getElementById(id);
    if (!el)
        throw new Error(`Element #${id} not found`);
    return el;
};
// --- State ---
let viewedGraph = "";
let mcpActiveGraph = "";
// --- Initialize ---
const wsUrl = `${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/ws`;
const wsStatus = $("ws-status");
const mcpActiveLabel = $("mcp-active-graph");
const graphSelector = $("graph-selector");
const viewModeBadge = $("view-mode-badge");
const btnRefresh = $("btn-refresh");
const router = createEventRouter(wsUrl, (connected) => {
    wsStatus.textContent = connected ? "Connected" : "Disconnected";
    wsStatus.className = connected
        ? "px-2 py-1 text-xs rounded bg-green-900/50 text-green-400"
        : "px-2 py-1 text-xs rounded bg-red-900/50 text-red-400";
    // Auto-refresh snapshot on reconnect
    if (connected && viewedGraph) {
        loadGraphSnapshot(viewedGraph);
    }
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
const showDetail = (nodeId, content, nodeType) => {
    detailTitle.textContent = `${nodeType} — ${nodeId.slice(0, 8)}`;
    detailContent.textContent = content;
    detailDrawer.classList.remove("hidden");
};
$("btn-close-detail").addEventListener("click", () => {
    detailDrawer.classList.add("hidden");
});
// --- Pipeline panel ---
const pipelineStatus = $("pipeline-status");
const pipelinePanel = initPipelinePanel($("pipeline-container"), router, (status) => {
    pipelineStatus.textContent = status;
});
// --- Knowledge graph panel ---
const graphPanel = initGraphPanel($("graph-container"), router, showDetail, {
    layoutSelect: $("graph-layout"),
    filterSelect: $("graph-filter"),
});
// --- Mode badge ---
const updateModeBadge = () => {
    const isLive = viewedGraph === mcpActiveGraph;
    viewModeBadge.textContent = isLive ? "Live" : "Snapshot";
    viewModeBadge.className = isLive
        ? "px-1.5 py-0.5 rounded text-xs bg-green-900/50 text-green-400"
        : "px-1.5 py-0.5 rounded text-xs bg-gray-700 text-gray-400";
};
// --- Graph loading ---
const loadGraphSnapshot = async (graph) => {
    try {
        const snapshot = await fetchSnapshot(graph);
        graphPanel.clearGraph();
        pipelinePanel.clearPipeline();
        graphPanel.loadSnapshot(snapshot.nodes, snapshot.edges);
        btnRefresh.classList.remove("ring-2", "ring-amber-500");
    }
    catch (err) {
        console.error("Failed to load snapshot:", err);
    }
};
const switchViewedGraph = async (graph) => {
    viewedGraph = graph;
    router.setGraphSubscription([graph]);
    updateModeBadge();
    await loadGraphSnapshot(graph);
};
// --- Graph selector population ---
const populateGraphSelector = (graphs, activeGraph) => {
    graphSelector.innerHTML = "";
    for (const g of graphs) {
        const opt = document.createElement("option");
        opt.value = g;
        opt.textContent = g;
        graphSelector.appendChild(opt);
    }
    graphSelector.value = activeGraph;
};
// --- Event handlers ---
graphSelector.addEventListener("change", () => {
    switchViewedGraph(graphSelector.value);
});
btnRefresh.addEventListener("click", () => {
    if (viewedGraph) {
        loadGraphSnapshot(viewedGraph);
    }
});
// Gap detection — highlight Refresh button
router.onGapDetected(() => {
    btnRefresh.classList.add("ring-2", "ring-amber-500");
});
// GraphSwitched event — MCP changed its active graph
router.subscribe("graph_switched", (event) => {
    const e = event;
    mcpActiveGraph = e.new_graph;
    mcpActiveLabel.textContent = mcpActiveGraph;
    updateModeBadge();
    // Re-fetch graph list (new graphs may have been created)
    fetchGraphs().then(({ graphs }) => {
        populateGraphSelector(graphs, viewedGraph);
    }).catch(console.error);
});
// --- Initial load ---
const init = async () => {
    try {
        const { graphs, active_graph } = await fetchGraphs();
        mcpActiveGraph = active_graph;
        mcpActiveLabel.textContent = mcpActiveGraph;
        populateGraphSelector(graphs, active_graph);
        await switchViewedGraph(active_graph);
    }
    catch (err) {
        console.error("Failed to initialize graph selector:", err);
    }
};
init();
