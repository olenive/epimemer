/**
 * The header's record selector: which retrieval the dashboard is looking at.
 *
 * The interaction rule the whole feature hangs on is **ambient signal,
 * deliberate detail** (RETRIEVAL_PROVENANCE.md §5.2), which the pipeline strip
 * already follows. A retrieval occurring adds an entry here and moves the
 * unread count. It engages nothing, opens nothing and steals nothing — the
 * agent fires on the order of ten per task, and a dashboard that reacted to
 * each would be unreadable.
 */

import type { EventRouter } from "./events";
import {
  entryFromEvent,
  entryFromRecord,
  recordsInGraph,
  rememberRecord,
  selectorLabel,
  unreadCount,
  type RecordEntry,
} from "./retrieval-store";
import type { AnyEvent, RetrievalRecorded, RetrievalRecordWire } from "./types";

export interface RetrievalSelectorElements {
  select: HTMLSelectElement;
  unread: HTMLElement;
}

export interface RetrievalSelectorHandle {
  cleanup: () => void;
  /** A different session has its own retrievals — start empty (§6). */
  clearAll: () => void;
  /** Records belong to a graph; switching graphs leaves focus mode (§6). */
  setViewedGraph: (graph: string) => void;
  /** Fold in what the `retrievals` RPC returned, payloads and all. */
  merge: (records: readonly RetrievalRecordWire[]) => void;
  /** The record currently selected, or null when focus mode is off. */
  selected: () => RecordEntry | null;
}

const NO_RECORD = "";

/**
 * Wire the selector up.
 *
 * `onSelect` is handed the chosen record, or `null` when the selection is
 * cleared. Focus mode, the drawer and the panels are the caller's business:
 * this owns the list and the unread count and nothing else.
 */
export const initRetrievalSelector = (
  elements: RetrievalSelectorElements,
  router: EventRouter,
  onSelect: (entry: RecordEntry | null) => void,
): RetrievalSelectorHandle => {
  let entries: RecordEntry[] = [];
  let viewedGraph = "";
  let selectedId: string | null = null;
  let lastReadId: string | null = null;

  const visible = (): RecordEntry[] =>
    viewedGraph ? recordsInGraph(entries, viewedGraph) : entries;

  const render = (): void => {
    const shown = visible();
    elements.select.innerHTML = "";

    const none = document.createElement("option");
    none.value = NO_RECORD;
    none.textContent = shown.length === 0 ? "No retrievals" : "No focus";
    elements.select.appendChild(none);

    // Newest first: the retrieval you came to look at is the one that just
    // happened.
    for (const entry of [...shown].reverse()) {
      const option = document.createElement("option");
      option.value = entry.recordId;
      option.textContent = selectorLabel(entry);
      elements.select.appendChild(option);
    }
    elements.select.value = selectedId ?? NO_RECORD;
    elements.select.disabled = shown.length === 0;

    const unread = unreadCount(shown, lastReadId);
    elements.unread.textContent = unread > 0 ? String(unread) : "";
    elements.unread.classList.toggle("hidden", unread === 0);
    elements.unread.title =
      unread === 1 ? "1 retrieval you have not looked at" : `${unread} retrievals you have not looked at`;
  };

  const choose = (recordId: string): void => {
    const entry = visible().find((e) => e.recordId === recordId) ?? null;
    selectedId = entry?.recordId ?? null;
    if (entry !== null) lastReadId = entry.recordId;
    render();
    onSelect(entry);
  };

  const add = (entry: RecordEntry): void => {
    entries = rememberRecord(entries, entry);
    // The selected record may have been evicted by its own successors; a
    // dangling selection would dim the graph against a record nobody can open.
    if (selectedId !== null && !entries.some((e) => e.recordId === selectedId)) {
      selectedId = null;
      onSelect(null);
    }
    render();
  };

  const handleEvent = (event: AnyEvent): void => {
    add(entryFromEvent(event as RetrievalRecorded));
  };

  const unsubs = [router.subscribe("retrieval_recorded", handleEvent)];

  elements.select.addEventListener("change", () => choose(elements.select.value));

  const clearAll = (): void => {
    entries = [];
    selectedId = null;
    lastReadId = null;
    render();
    onSelect(null);
  };

  const setViewedGraph = (graph: string): void => {
    if (graph === viewedGraph) return;
    viewedGraph = graph;
    // Switching graphs leaves focus mode (§6). The records survive — they are
    // still that graph's history when you switch back.
    selectedId = null;
    render();
    onSelect(null);
  };

  const merge = (records: readonly RetrievalRecordWire[]): void => {
    for (const record of records) {
      entries = rememberRecord(entries, entryFromRecord(record));
    }
    render();
  };

  render();

  return {
    cleanup: () => unsubs.forEach((u) => u()),
    clearAll,
    setViewedGraph,
    merge,
    selected: () => visible().find((e) => e.recordId === selectedId) ?? null,
  };
};
