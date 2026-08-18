/**
 * The activity log: what the agent did, filterable, clickable.
 *
 * A rail rather than a division. The dashboard already has two vertical panels,
 * a drawer and a strip, and a fourth column would starve the graph — a log is a
 * narrow list, and the drawer belongs to node detail (EVENT_LOG.md §10). It is
 * hidden until asked for, so the graph keeps its width by default.
 *
 * Everything it holds arrives on the coarse `graph_action_recorded` stream: one
 * entry per transaction, already summarised. Nothing here reads the
 * fine-grained events.
 */

import type { EventRouter } from "./events";
import {
  NO_LOG_FILTERS,
  applyLogFilters,
  entryFromAction,
  rememberEntry,
  verbLabel,
  verbsIn,
  type LogEntry,
  type LogFilters,
} from "./log-store";
import type { AnyEvent, GraphActionRecorded } from "./types";

/** As many acts as the hub's ring holds, so a backfill is never truncated here. */
const LOG_CAPACITY = 512;

export interface LogPanelElements {
  rail: HTMLElement;
  entries: HTMLElement;
  empty: HTMLElement;
  verbs: HTMLElement;
  nodeId: HTMLInputElement;
  text: HTMLInputElement;
  rangeStart: HTMLInputElement;
  rangeEnd: HTMLInputElement;
  clear: HTMLElement;
  count: HTMLElement;
  /** Where a click that could not do what it looked like says so (§7). */
  note: HTMLElement;
}

export interface LogPanelHandle {
  cleanup: () => void;
  /** A different session has its own history — start empty (`main.ts` rule). */
  clearAll: () => void;
  /** Entries belong to a graph; switching graphs starts that graph's log. */
  setViewedGraph: (graph: string) => void;
  /** The other half of bidirectional selection: a node click filters the log. */
  filterToNode: (nodeId: string) => void;
  toggle: () => void;
}

const dateValue = (input: HTMLInputElement): number | null => {
  if (!input.value) return null;
  const t = Date.parse(input.value);
  return Number.isNaN(t) ? null : t;
};

const timeLabel = (at: number): string =>
  new Date(at).toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });

const VERB_CHIP =
  "px-1.5 py-0.5 rounded text-[10px] border transition-colors cursor-pointer";
const CHIP_ON =
  "bg-blue-100 text-blue-700 border-blue-400 dark:bg-blue-900/50 dark:text-blue-300 dark:border-blue-700";
const CHIP_OFF =
  "bg-gray-100 text-gray-600 border-gray-400 dark:bg-gray-800 dark:text-gray-400 dark:border-gray-700";

/**
 * Wire the log rail up.
 *
 * `onSelect` is handed the entry that was clicked and returns what to report —
 * a string when the highlight could not do what the click implied, `null` when
 * it went through. The caller owns the panels; this owns saying so. Keeping the
 * split there is what lets the log be driven without a graph behind it.
 */
export const initLogPanel = (
  elements: LogPanelElements,
  router: EventRouter,
  onSelect: (entry: LogEntry) => string | null,
): LogPanelHandle => {
  let entries: LogEntry[] = [];
  let filters: LogFilters = { ...NO_LOG_FILTERS };
  let viewedGraph = "";
  let selectedId: string | null = null;

  const readFilters = (): LogFilters => {
    const t0 = dateValue(elements.rangeStart);
    const t1 = dateValue(elements.rangeEnd);
    return {
      verbs: filters.verbs,
      nodeId: elements.nodeId.value,
      text: elements.text.value,
      // A half-open range is still a range, as on the timeline panel.
      range:
        t0 === null && t1 === null
          ? null
          : { t0: t0 ?? -Infinity, t1: t1 ?? Infinity },
    };
  };

  const setNote = (note: string | null): void => {
    elements.note.textContent = note ?? "";
    elements.note.classList.toggle("hidden", note === null);
  };

  const renderVerbs = (): void => {
    elements.verbs.innerHTML = "";
    for (const verb of verbsIn(entries)) {
      const chip = document.createElement("button");
      const on = filters.verbs.includes(verb);
      chip.className = `${VERB_CHIP} ${on ? CHIP_ON : CHIP_OFF}`;
      chip.textContent = verbLabel(verb);
      chip.title = on ? `Stop showing ${verb} only` : `Show ${verb}`;
      chip.addEventListener("click", () => {
        filters = {
          ...filters,
          verbs: on
            ? filters.verbs.filter((v) => v !== verb)
            : [...filters.verbs, verb],
        };
        render();
      });
      elements.verbs.appendChild(chip);
    }
  };

  const renderEntries = (): void => {
    const shown = applyLogFilters(entries, readFilters());
    elements.entries.innerHTML = "";
    elements.count.textContent =
      shown.length === entries.length
        ? `${entries.length}`
        : `${shown.length} / ${entries.length}`;
    elements.empty.classList.toggle("hidden", shown.length > 0);
    elements.empty.textContent =
      entries.length === 0
        ? "Nothing yet — the agent has not changed this graph."
        : "No entries match these filters.";

    // Newest first: the thing you came to look at is the thing that just
    // happened. The store keeps action order; only the reading is reversed.
    for (const entry of [...shown].reverse()) {
      const row = document.createElement("button");
      row.className =
        "w-full text-left px-2 py-1 rounded text-xs transition-colors " +
        (entry.actionId === selectedId
          ? "bg-blue-100 dark:bg-blue-900/40"
          : "hover:bg-gray-200 dark:hover:bg-gray-800");
      row.title = entry.subjects.join("\n") || "no nodes";

      const line = document.createElement("div");
      line.className = "text-gray-700 dark:text-gray-300 break-words";
      line.textContent = entry.summary;

      const meta = document.createElement("div");
      meta.className = "text-[10px] text-gray-600 dark:text-gray-500";
      meta.textContent = timeLabel(entry.at);

      row.append(line, meta);
      row.addEventListener("click", () => {
        selectedId = entry.actionId;
        setNote(onSelect(entry));
        renderEntries();
      });
      elements.entries.appendChild(row);
    }
  };

  const render = (): void => {
    renderVerbs();
    renderEntries();
  };

  const handleAction = (event: AnyEvent): void => {
    const entry = entryFromAction(event as GraphActionRecorded);
    // An entry from graph A must never highlight into graph B (§6). The hub's
    // subscription already scopes this; the check stays because the panel is
    // what would show the mistake.
    if (viewedGraph && entry.graph && entry.graph !== viewedGraph) return;
    entries = rememberEntry(entries, entry, LOG_CAPACITY);
    render();
  };

  const unsubs = [router.subscribe("graph_action_recorded", handleAction)];

  for (const input of [elements.nodeId, elements.text]) {
    input.addEventListener("input", renderEntries);
  }
  for (const input of [elements.rangeStart, elements.rangeEnd]) {
    input.addEventListener("change", renderEntries);
  }
  elements.clear.addEventListener("click", () => {
    filters = { ...NO_LOG_FILTERS };
    elements.nodeId.value = "";
    elements.text.value = "";
    elements.rangeStart.value = "";
    elements.rangeEnd.value = "";
    render();
  });

  const clearAll = (): void => {
    entries = [];
    selectedId = null;
    setNote(null);
    render();
  };

  const setViewedGraph = (graph: string): void => {
    if (graph === viewedGraph) return;
    viewedGraph = graph;
    clearAll();
  };

  /**
   * The other direction of selection: a node click narrows the log to that node.
   *
   * Deliberately does **not** reveal the rail. The codebase's rule is ambient
   * signal, deliberate detail — a panel that opened itself on every node click
   * would be the drawer-stealing behaviour RETRIEVAL_PROVENANCE.md §5.2 rules
   * out. The filter is simply there when you open the log.
   */
  const filterToNode = (nodeId: string): void => {
    elements.nodeId.value = nodeId;
    renderEntries();
  };

  const toggle = (): void => {
    elements.rail.classList.toggle("hidden");
  };

  render();

  return {
    cleanup: () => unsubs.forEach((u) => u()),
    clearAll,
    setViewedGraph,
    filterToNode,
    toggle,
  };
};
