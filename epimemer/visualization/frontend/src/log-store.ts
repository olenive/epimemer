/**
 * What the log holds, and what a filter does to it. No DOM.
 *
 * Filtering is `Array.prototype.filter` over a few hundred entries in memory —
 * no protocol method, no storage schema, no cross-backend parity problem, and
 * deliberately **not** routed through `text_search` (EVENT_LOG.md §5). Log
 * vocabulary is a dozen verbs repeated thousands of times, and SurrealDB's BM25
 * clamps IDF to zero above 50% document frequency, so every verb term would tie
 * at 0.0: a ranking function returning a constant.
 */

import type { TimeRange } from "./timeline-filter";
import type { AdvisoryRaised, GraphActionRecorded } from "./types";

/**
 * What an entry is: something the agent did, or something it was told.
 *
 * A warning is a line in the story of a session, beside the act it accompanied,
 * so it belongs in this list rather than in a panel of its own
 * (ADVISORIES_DASHBOARD.md §2.3). One shape carries both, so the filters, the
 * ordering and the deduplication are written once.
 */
export type LogEntryKind = "act" | "warning";

/** One act or one warning, as the panel holds it. */
export interface LogEntry {
  actionId: string;
  at: number;
  graph: string;
  kind: LogEntryKind;
  verb: string;
  subjects: string[];
  counts: Record<string, number>;
  summary: string;
  /**
   * False only on a warning the agent's response did not carry. An act is
   * something the agent did, so there is nothing it could have been kept from,
   * and it always reads true.
   */
  surfaced: boolean;
}

export interface LogFilters {
  /** Selected verbs. Empty means every verb — a chip row starts unselected. */
  verbs: string[];
  /** Exact match against `subjects`. An id is a lookup, not ranked retrieval. */
  nodeId: string;
  /** Plain substring over `summary`, case-insensitive. */
  text: string;
  range: TimeRange | null;
}

export const NO_LOG_FILTERS: LogFilters = {
  verbs: [],
  nodeId: "",
  text: "",
  range: null,
};

/**
 * Read a wire event into an entry.
 *
 * `summary` is taken as it arrives. It is rendered on the emitting side on
 * purpose: a line the frontend assembled from parts would be a second place
 * where the vocabulary of the system gets decided, and it would drift from the
 * tool responses that use the same words (§3.1).
 */
export const entryFromAction = (event: GraphActionRecorded): LogEntry => ({
  actionId: event.action_id,
  at: Date.parse(event.timestamp),
  graph: event.graph,
  kind: "act",
  verb: event.verb,
  subjects: event.subjects,
  counts: event.counts,
  summary: event.summary,
  surfaced: true,
});

/** The verb every warning row carries, so the chip row needs no special case. */
export const WARNED = "warned";

/** What the policy decided, in the words the row ends on. */
const actionPhrase = (action: string): string =>
  action === "flag" ? "flagged to the user" : "the agent proceeded";

/**
 * The warning's sentence with a closing full stop taken off.
 *
 * The row appends what the policy decided, and an advisory's message is a
 * finished sentence, so joining them raw reads ".; the agent proceeded". The
 * words are untouched: only the punctuation between two clauses moves.
 */
const asClause = (message: string): string => message.replace(/\.\s*$/, "");

/**
 * Read a warning event into an entry.
 *
 * The line is assembled here rather than arriving pre-rendered, unlike an act's
 * summary. The event carries the warning's own message word for word, which is
 * the part that must not be re-worded; the tool prefix and the closing clause
 * are the log's framing of it, and they say what the log knows (§4).
 */
export const entryFromAdvisory = (event: AdvisoryRaised): LogEntry => ({
  actionId: event.action_id,
  at: Date.parse(event.timestamp),
  graph: event.graph,
  kind: "warning",
  verb: WARNED,
  subjects: event.subjects,
  counts: {},
  summary: `${event.tool} warned: ${asClause(event.message)}; ${actionPhrase(event.action)}`,
  surfaced: event.surfaced,
});

const matchesVerb = (entry: LogEntry, verbs: readonly string[]): boolean =>
  verbs.length === 0 || verbs.includes(entry.verb);

const matchesNodeId = (entry: LogEntry, nodeId: string): boolean => {
  const wanted = nodeId.trim();
  return wanted === "" || entry.subjects.includes(wanted);
};

const matchesText = (entry: LogEntry, text: string): boolean =>
  text.trim() === "" ||
  entry.summary.toLowerCase().includes(text.trim().toLowerCase());

const matchesRange = (entry: LogEntry, range: TimeRange | null): boolean =>
  range === null || (entry.at >= range.t0 && entry.at <= range.t1);

/** All filters, ANDed — narrowing, never widening. */
export const matchesLogFilters = (entry: LogEntry, filters: LogFilters): boolean =>
  matchesVerb(entry, filters.verbs) &&
  matchesNodeId(entry, filters.nodeId) &&
  matchesText(entry, filters.text) &&
  matchesRange(entry, filters.range);

export const applyLogFilters = (
  entries: readonly LogEntry[],
  filters: LogFilters,
): LogEntry[] => entries.filter((entry) => matchesLogFilters(entry, filters));

/**
 * Add one entry, keeping the list in action order and bounded.
 *
 * Deduplicated by `action_id`, because backfill on subscribe replays whatever
 * the hub's ring still holds and a browser that was already connected has seen
 * some of it. `seq` cannot do this job: it is assigned per browser connection
 * and restarts on reconnect, so the same act arrives under two numbers (§4.1).
 *
 * Ids are zero-padded at the source, so sorting them as strings is sorting them
 * as positions.
 */
export const rememberEntry = (
  entries: readonly LogEntry[],
  entry: LogEntry,
  capacity: number,
): LogEntry[] => {
  if (entries.some((held) => held.actionId === entry.actionId)) return [...entries];
  const next = [...entries, entry].sort((a, b) =>
    a.actionId < b.actionId ? -1 : a.actionId > b.actionId ? 1 : 0,
  );
  return next.slice(Math.max(0, next.length - capacity));
};

/** The verbs actually present, sorted — the chip row describes this log. */
export const verbsIn = (entries: readonly LogEntry[]): string[] =>
  [...new Set(entries.map((entry) => entry.verb))].sort();

/**
 * "world_changed" → "world-change", so a chip reads as the summary does.
 *
 * Everything else loses its underscores, which is what makes a timeline
 * decision readable: those acts carry the `DecisionKind` the journal recorded
 * them under, so "recurrence_exception" is the word that arrives.
 */
export const verbLabel = (verb: string): string =>
  verb === "world_changed" ? "world-change" : verb.replace(/_/g, " ");
