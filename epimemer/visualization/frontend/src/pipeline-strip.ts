/**
 * Pipeline strip — one small tile per Petri net, lighting up as data flows.
 *
 * The knowledge graph is the star; pipelines are ambient awareness. Each tile
 * shows a mini glyph of the net (a genuine miniature of the detail view), the
 * pipeline name, and a status line. Tiles persist across runs and are keyed by
 * `pipeline_name`, so two pipelines firing in quick succession animate
 * independently. Clicking a tile opens the full detail overlay with labels and
 * token badges; Esc / × closes it.
 */

import type { EventRouter } from "./events";
import type { AnyEvent, PipelineEvent } from "./types";
import { applyState, loadGraphviz, renderNet, topologyKey } from "./pipeline-detail";
import { createPipelineStore, type PipelineRunState } from "./pipeline-store";
import type { Graphviz } from "@hpcc-js/wasm-graphviz";

export interface PipelineStripElements {
  strip: HTMLElement;
  hint: HTMLElement;
  detail: HTMLElement;
  detailTitle: HTMLElement;
  detailSvg: HTMLElement;
  detailClose: HTMLElement;
}

export interface PipelineStripHandle {
  /** Drop all pipeline history (e.g. on session switch). */
  clearAll: () => void;
  /** Mark running tiles as possibly-stale after a sequence gap. */
  markStale: () => void;
  closeDetail: () => void;
  /** Regenerate the open detail — its colours are baked in at render time. */
  repaintDetail: () => void;
  cleanup: () => void;
}

interface TileRefs {
  root: HTMLButtonElement;
  glyph: HTMLElement;
  name: HTMLElement;
  status: HTMLElement;
  svgRoot: SVGElement | null;
  topoKey: string;
}

const PIPELINE_EVENTS = [
  "pipeline_started",
  "transition_fired",
  "transition_completed",
  "tokens_updated",
  "pipeline_completed",
  "pipeline_failed",
] as const;

const TILE_BASE =
  "pipeline-tile flex flex-col items-center gap-1 shrink-0 w-24 rounded border p-1.5 " +
  "bg-gray-100 hover:bg-gray-50 dark:bg-gray-900/60 dark:hover:bg-gray-800 " +
  "transition-colors cursor-pointer focus:outline-none";

const borderClass = (state: PipelineRunState): string => {
  if (state.stale) return "border-gray-500 dark:border-gray-600 animate-pulse";
  switch (state.status) {
    case "running":
      return "border-amber-500/70 ring-1 ring-amber-500/40";
    case "completed":
      return "border-green-600/60";
    case "failed":
      return "border-red-600/70";
    default:
      return "border-gray-400 dark:border-gray-700";
  }
};

const statusText = (state: PipelineRunState): string => {
  if (state.stale) return "…";
  switch (state.status) {
    case "running":
      return state.itemsProcessed > 0 ? `▸ ${state.itemsProcessed}` : "running";
    case "completed": {
      const runs = `${state.runsCompleted} run${state.runsCompleted === 1 ? "" : "s"}`;
      return state.lastDurationMs != null
        ? `${runs} · ${Math.round(state.lastDurationMs)}ms`
        : runs;
    }
    case "failed":
      return "failed";
    default:
      return "idle";
  }
};

export const initPipelineStrip = (
  router: EventRouter,
  els: PipelineStripElements,
): PipelineStripHandle => {
  const store = createPipelineStore();
  const tiles = new Map<string, TileRefs>();
  let gv: Graphviz | null = null;

  let detailName: string | null = null;
  let detailSvgRoot: SVGElement | null = null;
  let detailTopoKey = "";

  loadGraphviz().then((loaded) => {
    gv = loaded;
    for (const name of store.names()) renderTile(name);
    if (detailName) updateDetail();
  });

  const updateHint = (): void => {
    els.hint.style.display = store.names().length > 0 ? "none" : "";
  };

  const createTile = (name: string): TileRefs => {
    const root = document.createElement("button");
    root.className = `${TILE_BASE} border-gray-400 dark:border-gray-700`;
    root.title = name;

    const glyph = document.createElement("div");
    glyph.className = "w-full h-10 flex items-center justify-center overflow-hidden";

    const nameEl = document.createElement("div");
    nameEl.className =
      "text-[10px] leading-tight text-gray-700 dark:text-gray-300 truncate w-full text-center";
    nameEl.textContent = name;

    const statusEl = document.createElement("div");
    statusEl.className = "text-[10px] leading-tight text-gray-600 dark:text-gray-500 truncate w-full text-center";

    root.append(glyph, nameEl, statusEl);
    root.addEventListener("click", () => openDetail(name));
    els.strip.appendChild(root);

    const refs: TileRefs = { root, glyph, name: nameEl, status: statusEl, svgRoot: null, topoKey: "" };
    tiles.set(name, refs);
    return refs;
  };

  const renderTile = (name: string): void => {
    const state = store.get(name);
    if (!state) return;
    const refs = tiles.get(name) ?? createTile(name);

    const key = topologyKey(state.topology);
    if (gv && state.topology && refs.topoKey !== key) {
      refs.svgRoot = renderNet(gv, refs.glyph, state.topology, { mini: true });
      refs.topoKey = key;
    }
    if (refs.svgRoot) applyState(refs.svgRoot, state, { tokens: false });

    refs.root.className = `${TILE_BASE} ${borderClass(state)}`;
    refs.status.textContent = statusText(state);
    refs.root.title = state.lastError ? `${name}: ${state.lastError}` : name;
  };

  // --- Detail overlay ---

  const openDetail = (name: string): void => {
    detailName = name;
    detailTopoKey = "";
    els.detailTitle.textContent = name;
    els.detail.classList.remove("hidden");
    updateDetail();
  };

  const updateDetail = (): void => {
    if (!detailName) return;
    const state = store.get(detailName);
    if (!state) return;
    const key = topologyKey(state.topology);
    if (gv && state.topology && detailTopoKey !== key) {
      detailSvgRoot = renderNet(gv, els.detailSvg, state.topology, { mini: false });
      detailTopoKey = key;
    }
    if (detailSvgRoot) applyState(detailSvgRoot, state, { tokens: true });
  };

  /**
   * Redraw the open detail from scratch.
   *
   * The net's colours are baked into the DOT at generation time, so a theme
   * change cannot reach an already-rendered SVG. Clearing the topology key is
   * what forces `updateDetail` to regenerate rather than reuse it.
   */
  const repaintDetail = (): void => {
    if (!detailName) return;
    detailTopoKey = "";
    updateDetail();
  };

  const closeDetail = (): void => {
    detailName = null;
    detailSvgRoot = null;
    detailTopoKey = "";
    els.detail.classList.add("hidden");
    els.detailSvg.innerHTML = "";
  };

  els.detailClose.addEventListener("click", closeDetail);

  // --- Events ---

  const onEvent = (event: AnyEvent): void => {
    const name = store.handleEvent(event as PipelineEvent);
    if (!name) return;
    renderTile(name);
    if (detailName === name) updateDetail();
    updateHint();
  };

  const unsubs = PIPELINE_EVENTS.map((t) => router.subscribe(t, onEvent));

  const clearAll = (): void => {
    store.clear();
    els.strip.innerHTML = "";
    tiles.clear();
    closeDetail();
    updateHint();
  };

  const markStale = (): void => {
    store.markRunningStale();
    for (const name of store.names()) renderTile(name);
    if (detailName) updateDetail();
  };

  const cleanup = (): void => {
    unsubs.forEach((u) => u());
  };

  updateHint();
  return { clearAll, markStale, closeDetail, repaintDetail, cleanup };
};
