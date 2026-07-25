/**
 * Petri-net rendering for the pipeline strip and its detail overlay.
 *
 * Both the tile glyph and the expanded detail are the *same* net drawn by the
 * same Graphviz WASM engine — the glyph is a genuine miniature of what a click
 * reveals. `generateDot(topology, { mini })` toggles labels/sizing; `applyState`
 * overlays live run state (firing transition, completed transitions, token-
 * holding places) by recoloring nodes by their stable `id`, so it works
 * unchanged at both sizes. Token-count badges are drawn in detail mode only.
 */

import { Graphviz } from "@hpcc-js/wasm-graphviz";

import type { PipelineStarted } from "./types";
import type { PipelineRunState } from "./pipeline-store";

// One shared WASM instance for glyphs and the detail view.
let _gvPromise: Promise<Graphviz> | null = null;
export const loadGraphviz = (): Promise<Graphviz> => (_gvPromise ??= Graphviz.load());

// --- DOT generation ---

const escapeDot = (s: string): string => s.replace(/"/g, '\\"');

export const generateDot = (
  event: PipelineStarted,
  opts: { mini: boolean },
): string => {
  const mini = opts.mini;
  const lines: string[] = [
    "digraph petri_net {",
    "  rankdir=LR;",
    '  bgcolor="transparent";',
  ];
  if (mini) {
    lines.push("  nodesep=0.12;", "  ranksep=0.22;");
  }
  lines.push(
    `  node [fontname="Inter, system-ui, sans-serif" fontsize=${mini ? 1 : 10}];`,
    `  edge [fontname="Inter, system-ui, sans-serif" fontsize=8];`,
    "  // Places",
  );

  for (const place of event.place_names) {
    const label = mini ? ' label=""' : "";
    const size = mini ? "width=0.14 height=0.14 fixedsize=true" : "width=0.6";
    lines.push(
      `  "${escapeDot(place)}" [shape=circle style=filled fillcolor="#1e293b" ` +
        `color="#475569" fontcolor="#94a3b8" ${size}${label} ` +
        `id="place-${escapeDot(place)}"];`,
    );
  }

  lines.push("  // Transitions");
  for (const transition of event.transition_names) {
    const label = mini ? ' label=""' : "";
    const size = mini ? "width=0.26 height=0.14 fixedsize=true" : "width=1.2 height=0.4";
    lines.push(
      `  "${escapeDot(transition)}" [shape=rect style="filled,rounded" ` +
        `fillcolor="#1e3a5f" color="#3b82f6" fontcolor="#93c5fd" ${size}${label} ` +
        `id="transition-${escapeDot(transition)}"];`,
    );
  }

  lines.push("  // Edges");
  for (const edge of event.edges) {
    const label = !mini && edge.label
      ? ` [label="${escapeDot(edge.label)}" fontcolor="#64748b"]`
      : "";
    const extra = mini ? " arrowsize=0.4 penwidth=0.6" : "";
    lines.push(
      `  "${escapeDot(edge.source)}" -> "${escapeDot(edge.target)}"${label} ` +
        `[color="#475569"${extra}];`,
    );
  }

  lines.push("}");
  return lines.join("\n");
};

// --- Palette (shared with the legacy panel) ---

const ACTIVE_TRANSITION_COLOR = "#ec4899"; // pink-500
const COMPLETED_TRANSITION_COLOR = "#22c55e"; // green-500
const ACTIVE_PLACE_COLOR = "#f59e0b"; // amber-500
const IDLE_TRANSITION_COLOR = "#3b82f6"; // blue-500
const IDLE_PLACE_COLOR = "#475569"; // gray-600

const setSvgNodeColor = (
  svgRoot: SVGElement,
  elementId: string,
  strokeColor: string,
  strokeWidth = "2",
): void => {
  const group = svgRoot.querySelector(`#${CSS.escape(elementId)}`);
  if (!group) return;
  group.querySelectorAll("ellipse, polygon, path, rect").forEach((shape) => {
    (shape as SVGElement).setAttribute("stroke", strokeColor);
    (shape as SVGElement).setAttribute("stroke-width", strokeWidth);
  });
};

const setTokenBadge = (
  svgRoot: SVGElement,
  placeName: string,
  count: number,
): void => {
  const badgeId = `badge-${placeName}`;
  let badge = svgRoot.querySelector(`#${CSS.escape(badgeId)}`) as SVGTextElement | null;
  const group = svgRoot.querySelector(`#${CSS.escape(`place-${placeName}`)}`);
  const ellipse = group?.querySelector("ellipse");
  if (!ellipse) return;

  if (count === 0) {
    badge?.remove();
    return;
  }
  if (!badge) {
    badge = document.createElementNS("http://www.w3.org/2000/svg", "text");
    badge.setAttribute("id", badgeId);
    badge.setAttribute("font-size", "9");
    badge.setAttribute("font-family", "Inter, system-ui, sans-serif");
    badge.setAttribute("text-anchor", "middle");
    badge.setAttribute("dominant-baseline", "central");
    badge.setAttribute("fill", "#fbbf24");
    badge.setAttribute("font-weight", "bold");
    svgRoot.querySelector("g")?.appendChild(badge);
  }
  badge.setAttribute("x", ellipse.getAttribute("cx") ?? "0");
  badge.setAttribute("y", ellipse.getAttribute("cy") ?? "0");
  badge.textContent = String(count);
};

/** Overlay live run state onto a rendered net. Token badges only when asked. */
export const applyState = (
  svgRoot: SVGElement,
  state: PipelineRunState,
  opts: { tokens: boolean },
): void => {
  const topo = state.topology;
  if (!topo) return;

  for (const t of topo.transition_names) {
    setSvgNodeColor(svgRoot, `transition-${t}`, IDLE_TRANSITION_COLOR, "1.5");
  }
  for (const p of topo.place_names) {
    setSvgNodeColor(svgRoot, `place-${p}`, IDLE_PLACE_COLOR, "1.5");
  }
  for (const t of state.completedTransitions) {
    setSvgNodeColor(svgRoot, `transition-${t}`, COMPLETED_TRANSITION_COLOR, "2.5");
  }
  if (state.activeTransition) {
    setSvgNodeColor(svgRoot, `transition-${state.activeTransition}`, ACTIVE_TRANSITION_COLOR, "3");
  }
  for (const [place, count] of Object.entries(state.placeTokens)) {
    if (count > 0) setSvgNodeColor(svgRoot, `place-${place}`, ACTIVE_PLACE_COLOR, "2.5");
    if (opts.tokens) setTokenBadge(svgRoot, place, count);
  }
};

// --- Rendering into a container ---

const makeResponsive = (svgRoot: SVGElement): void => {
  svgRoot.removeAttribute("width");
  svgRoot.removeAttribute("height");
  svgRoot.setAttribute("class", "w-full h-full");
  svgRoot.style.maxWidth = "100%";
  svgRoot.style.maxHeight = "100%";
};

/** Draw `topology` into `el` and return the SVG root for later overlays. */
export const renderNet = (
  gv: Graphviz,
  el: HTMLElement,
  topology: PipelineStarted,
  opts: { mini: boolean },
): SVGElement | null => {
  el.innerHTML = gv.dot(generateDot(topology, opts));
  const svgRoot = el.querySelector("svg");
  if (svgRoot) makeResponsive(svgRoot);
  return svgRoot;
};

/** A stable key for a topology, to avoid re-rendering the SVG when unchanged. */
export const topologyKey = (topology: PipelineStarted | null): string => {
  if (!topology) return "";
  const edges = topology.edges.map((e) => `${e.source}>${e.target}`).join(",");
  return `${topology.place_names.join(",")}|${topology.transition_names.join(",")}|${edges}`;
};
