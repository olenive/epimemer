/**
 * The records the selector lists, and what one says. No DOM.
 *
 * A record is **our response**, not the agent's context: what landed in the
 * model's context is the MCP client's rendering of this, possibly truncated by
 * the client, inside a tool-result block we never see. Everything user-facing
 * here says "Response" for that reason — a panel captioned "what the agent saw"
 * would be making a claim the system cannot verify
 * (RETRIEVAL_PROVENANCE.md §3.1).
 */

import type { RetrievalRecorded, RetrievalRecordWire } from "./types";

/** As many as the rings on both sides hold (`ring.py`). */
export const RECORD_CAPACITY = 20;

export interface RecordEntry {
  recordId: string;
  at: number;
  tool: string;
  query: string;
  graph: string;
  /** Ids the response named, in the order it named them. */
  nodeIds: string[];
  provenance: Record<string, string>;
  scores: Record<string, number | null>;
  responseText: string;
  truncated: boolean;
  /**
   * Whether the tool declared its ids at all.
   *
   * `false` is not "returned nothing": it is "never said", and the two must
   * stay distinguishable or a forgotten declaration reads as an empty
   * retrieval and focus mode dims the whole graph on the strength of it (§2.1).
   */
  declared: boolean;
}

export const entryFromRecord = (record: RetrievalRecordWire): RecordEntry => ({
  recordId: record.record_id,
  at: Date.parse(record.at),
  tool: record.tool,
  query: record.query,
  graph: record.graph,
  nodeIds: (record.retrieved ?? []).map((node) => node.node_id),
  provenance: Object.fromEntries(
    (record.retrieved ?? []).map((node) => [node.node_id, node.provenance]),
  ),
  scores: Object.fromEntries(
    (record.retrieved ?? []).map((node) => [node.node_id, node.score]),
  ),
  responseText: record.response_text,
  truncated: record.truncated,
  declared: record.retrieved !== null,
});

export const entryFromEvent = (event: RetrievalRecorded): RecordEntry =>
  entryFromRecord(event.record);

/**
 * Add one record, keeping the list in record order and bounded.
 *
 * Deduplicated by `record_id` for the same reason the log dedups by
 * `action_id`: the hub replays its ring on subscribe, and the RPC hands back
 * the session's, so the same record arrives by two routes on purpose.
 */
export const rememberRecord = (
  entries: readonly RecordEntry[],
  entry: RecordEntry,
  capacity: number = RECORD_CAPACITY,
): RecordEntry[] => {
  const without = entries.filter((held) => held.recordId !== entry.recordId);
  const next = [...without, entry].sort((a, b) =>
    a.recordId < b.recordId ? -1 : a.recordId > b.recordId ? 1 : 0,
  );
  return next.slice(Math.max(0, next.length - capacity));
};

/** Records belong to a graph; one from graph A must not highlight into B (§6). */
export const recordsInGraph = (
  entries: readonly RecordEntry[],
  graph: string,
): RecordEntry[] => entries.filter((entry) => entry.graph === graph);

/** "search · deployment rollback · 5 nodes" — enough to pick one out. */
export const selectorLabel = (entry: RecordEntry): string => {
  const tool = entry.tool.replace(/^epimemer\./, "");
  const count = entry.declared
    ? `${entry.nodeIds.length} node${entry.nodeIds.length === 1 ? "" : "s"}`
    : "not declared";
  const query = entry.query.trim();
  return query ? `${tool} · ${query} · ${count}` : `${tool} · ${count}`;
};

/**
 * How many arrived since the last one you looked at.
 *
 * A retrieval occurring must not move anything but this number (§5.2): the
 * agent fires on the order of ten per task, and a panel that reacted to each
 * would be unreadable.
 */
export const unreadCount = (
  entries: readonly RecordEntry[],
  lastReadId: string | null,
): number => {
  if (lastReadId === null) return entries.length;
  const index = entries.findIndex((entry) => entry.recordId === lastReadId);
  return index === -1 ? entries.length : entries.length - index - 1;
};

/**
 * What the Response tab shows for a record whose payload never arrived.
 *
 * On a non-loopback bind the hub holds structural metadata only, and the
 * session that could serve the payload may be gone. Saying so plainly beats an
 * empty pane that looks like a bug (§3.2).
 */
export const responseText = (entry: RecordEntry): string => {
  if (entry.responseText) {
    return entry.truncated
      ? `${entry.responseText}\n\n[truncated — the record caps what it keeps]`
      : entry.responseText;
  }
  return (
    "No response text for this record.\n\n" +
    "The dashboard is not on a loopback bind, so payloads stayed in the " +
    "session process; that session is no longer answering."
  );
};
