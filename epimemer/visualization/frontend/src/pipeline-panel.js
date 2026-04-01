/**
 * Pipeline visualization panel using Graphviz SVG + JS state overlay.
 *
 * On PipelineStarted, generates a DOT description of the Petri net topology,
 * renders it to SVG via @hpcc-js/wasm-graphviz, then overlays live state
 * (active transitions, token counts) via CSS class manipulation on SVG elements.
 */
import { Graphviz } from "@hpcc-js/wasm-graphviz";
// --- DOT generation from topology ---
const escapeDot = (s) => s.replace(/"/g, '\\"');
const generateDot = (event) => {
    const lines = [
        "digraph petri_net {",
        '  rankdir=LR;',
        '  bgcolor="transparent";',
        '  node [fontname="Inter, system-ui, sans-serif" fontsize=10];',
        '  edge [fontname="Inter, system-ui, sans-serif" fontsize=8];',
        "",
        "  // Places",
    ];
    for (const place of event.place_names) {
        lines.push(`  "${escapeDot(place)}" [` +
            `shape=circle ` +
            `style=filled ` +
            `fillcolor="#1e293b" ` +
            `color="#475569" ` +
            `fontcolor="#94a3b8" ` +
            `width=0.6 ` +
            `id="place-${escapeDot(place)}"` +
            `];`);
    }
    lines.push("", "  // Transitions");
    for (const transition of event.transition_names) {
        lines.push(`  "${escapeDot(transition)}" [` +
            `shape=rect ` +
            `style="filled,rounded" ` +
            `fillcolor="#1e3a5f" ` +
            `color="#3b82f6" ` +
            `fontcolor="#93c5fd" ` +
            `width=1.2 ` +
            `height=0.4 ` +
            `id="transition-${escapeDot(transition)}"` +
            `];`);
    }
    lines.push("", "  // Edges");
    for (const edge of event.edges) {
        const label = edge.label ? ` [label="${escapeDot(edge.label)}" fontcolor="#64748b"]` : "";
        lines.push(`  "${escapeDot(edge.source)}" -> "${escapeDot(edge.target)}"${label} [color="#475569"];`);
    }
    lines.push("}");
    return lines.join("\n");
};
// --- SVG state overlay ---
const ACTIVE_TRANSITION_COLOR = "#ec4899"; // pink-500
const COMPLETED_TRANSITION_COLOR = "#22c55e"; // green-500
const ACTIVE_PLACE_COLOR = "#f59e0b"; // amber-500
const IDLE_TRANSITION_COLOR = "#3b82f6"; // blue-500
const IDLE_PLACE_COLOR = "#475569"; // gray-600
const setSvgNodeColor = (svgRoot, elementId, strokeColor, strokeWidth = "2") => {
    const group = svgRoot.querySelector(`#${CSS.escape(elementId)}`);
    if (!group)
        return;
    // Graphviz renders nodes as <g> containing shapes (ellipse, polygon, path)
    const shapes = group.querySelectorAll("ellipse, polygon, path, rect");
    shapes.forEach((shape) => {
        shape.setAttribute("stroke", strokeColor);
        shape.setAttribute("stroke-width", strokeWidth);
    });
};
const setTokenCount = (svgRoot, container, placeName, count) => {
    // Add/update a token count badge near the place node
    const badgeId = `badge-${placeName}`;
    let badge = container.querySelector(`#${CSS.escape(badgeId)}`);
    const group = svgRoot.querySelector(`#${CSS.escape(`place-${placeName}`)}`);
    if (!group)
        return;
    // Get position from the SVG element
    const ellipse = group.querySelector("ellipse");
    if (!ellipse)
        return;
    const cx = parseFloat(ellipse.getAttribute("cx") ?? "0");
    const cy = parseFloat(ellipse.getAttribute("cy") ?? "0");
    if (count === 0 && badge) {
        badge.remove();
        return;
    }
    if (count === 0)
        return;
    if (!badge) {
        badge = document.createElementNS("http://www.w3.org/2000/svg", "text");
        badge.setAttribute("id", badgeId);
        badge.setAttribute("font-size", "9");
        badge.setAttribute("font-family", "Inter, system-ui, sans-serif");
        badge.setAttribute("text-anchor", "middle");
        badge.setAttribute("dominant-baseline", "central");
        badge.setAttribute("fill", "#fbbf24");
        badge.setAttribute("font-weight", "bold");
        // Append to the SVG root's first <g> (the graph group)
        const graphGroup = svgRoot.querySelector("g");
        graphGroup?.appendChild(badge);
    }
    badge.setAttribute("x", String(cx));
    badge.setAttribute("y", String(cy));
    badge.textContent = String(count);
};
/**
 * Initialize the pipeline visualization panel.
 *
 * Returns a cleanup function and a status update callback.
 */
export const initPipelinePanel = (container, router, onStatusChange) => {
    const state = {
        svgRoot: null,
        graphviz: null,
        activeTransition: null,
    };
    // Initialize Graphviz WASM
    Graphviz.load().then((gv) => {
        state.graphviz = gv;
    });
    const handlePipelineStarted = async (event) => {
        if (!state.graphviz) {
            state.graphviz = await Graphviz.load();
        }
        const dot = generateDot(event);
        const svgString = state.graphviz.dot(dot);
        container.innerHTML = svgString;
        state.svgRoot = container.querySelector("svg");
        if (state.svgRoot) {
            // Make SVG responsive
            state.svgRoot.removeAttribute("width");
            state.svgRoot.removeAttribute("height");
            state.svgRoot.setAttribute("class", "w-full h-full");
            state.svgRoot.style.maxWidth = "100%";
            state.svgRoot.style.maxHeight = "100%";
        }
        onStatusChange(`Running: ${event.pipeline_name}`);
    };
    const handleTransitionFired = (event) => {
        if (!state.svgRoot)
            return;
        // Reset previous active transition
        if (state.activeTransition) {
            setSvgNodeColor(state.svgRoot, `transition-${state.activeTransition}`, IDLE_TRANSITION_COLOR);
        }
        // Highlight current transition
        setSvgNodeColor(state.svgRoot, `transition-${event.transition_name}`, ACTIVE_TRANSITION_COLOR, "3");
        state.activeTransition = event.transition_name;
        // Highlight input places
        for (const place of event.input_places) {
            setSvgNodeColor(state.svgRoot, `place-${place}`, ACTIVE_PLACE_COLOR, "2.5");
        }
    };
    const handleTransitionCompleted = (event) => {
        if (!state.svgRoot)
            return;
        // Mark transition as completed
        setSvgNodeColor(state.svgRoot, `transition-${event.transition_name}`, COMPLETED_TRANSITION_COLOR, "2.5");
        // Highlight output places
        for (const place of event.output_places) {
            setSvgNodeColor(state.svgRoot, `place-${place}`, ACTIVE_PLACE_COLOR, "2.5");
        }
        // Fade input places back after a moment
        setTimeout(() => {
            if (!state.svgRoot)
                return;
            // Reset all places to idle, then token update will re-highlight non-empty ones
        }, 500);
    };
    const handleTokensUpdated = (event) => {
        if (!state.svgRoot)
            return;
        for (const [placeName, count] of Object.entries(event.place_token_counts)) {
            setTokenCount(state.svgRoot, container, placeName, count);
            // Color non-empty places differently
            const color = count > 0 ? ACTIVE_PLACE_COLOR : IDLE_PLACE_COLOR;
            setSvgNodeColor(state.svgRoot, `place-${placeName}`, color);
        }
    };
    const handlePipelineCompleted = (event) => {
        state.activeTransition = null;
        onStatusChange(`Done: ${event.transitions_fired} transitions in ${event.duration_ms.toFixed(0)}ms`);
    };
    const handleEvent = (event) => {
        switch (event.event_type) {
            case "pipeline_started":
                handlePipelineStarted(event);
                break;
            case "transition_fired":
                handleTransitionFired(event);
                break;
            case "transition_completed":
                handleTransitionCompleted(event);
                break;
            case "tokens_updated":
                handleTokensUpdated(event);
                break;
            case "pipeline_completed":
                handlePipelineCompleted(event);
                break;
        }
    };
    const unsubs = [
        router.subscribe("pipeline_started", handleEvent),
        router.subscribe("transition_fired", handleEvent),
        router.subscribe("transition_completed", handleEvent),
        router.subscribe("tokens_updated", handleEvent),
        router.subscribe("pipeline_completed", handleEvent),
    ];
    return () => {
        unsubs.forEach((u) => u());
    };
};
