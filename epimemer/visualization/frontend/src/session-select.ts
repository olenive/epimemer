/**
 * Which session to view, and what to say when one cannot answer — pure, no DOM.
 *
 * Two sockets sit behind this header and they fail independently. The browser's
 * socket to the hub is what the status badge reports. The hub's socket to each
 * MCP session is a different thing entirely, and even a live one says nothing
 * about whether that session's *storage* is reachable. A session can therefore
 * be listed, connected, and still unable to answer a single question.
 *
 * "Unreachable" is that third state. It is knowledge the browser accumulates by
 * asking — the hub cannot report it, because from the hub's side the session is
 * connected — so it lives here as a set of session ids that have failed, and is
 * cleared when a session re-registers (a fresh process) or answers again.
 */

import type { SessionInfo } from "./types";

/** What the graph selector knows right now. */
export type GraphListState =
  | { kind: "loading" }
  | { kind: "ready"; graphs: string[]; active: string }
  | { kind: "unavailable"; reason: string };

export interface SelectorOption {
  value: string;
  label: string;
}

/**
 * Rank: answers > listed but not answering > gone. Most recently active first
 * within a rank.
 *
 * The ordering matters more than it looks. Sorting by recency alone picks the
 * session you used last, which is precisely the one you most recently broke —
 * so a single wedged backend blanked the whole UI while a healthy session sat
 * two rows down in the same list.
 */
const rank = (session: SessionInfo, unreachable: ReadonlySet<string>): number => {
  if (!session.connected) return 2;
  return unreachable.has(session.session_id) ? 1 : 0;
};

const lastEventMillis = (session: SessionInfo): number =>
  session.last_event_at ? Date.parse(session.last_event_at) : 0;

export const pickDefaultSession = (
  sessions: readonly SessionInfo[],
  unreachable: ReadonlySet<string>,
): string | null => {
  if (sessions.length === 0) return null;
  const best = [...sessions].sort((a, b) => {
    const byRank = rank(a, unreachable) - rank(b, unreachable);
    return byRank !== 0 ? byRank : lastEventMillis(b) - lastEventMillis(a);
  });
  // Never null past this point: an unreachable session is still worth selecting,
  // because selecting it is how its reason reaches the screen.
  return best[0].session_id;
};

export const graphSelectorOptions = (state: GraphListState): SelectorOption[] => {
  switch (state.kind) {
    case "ready":
      return state.graphs.map((graph) => ({ value: graph, label: graph }));
    case "unavailable":
      return [{ value: "", label: "unavailable" }];
    case "loading":
      return [{ value: "", label: "Loading..." }];
  }
};

export const graphSelectorTitle = (state: GraphListState): string => {
  switch (state.kind) {
    case "ready":
      return "graph to view";
    case "unavailable":
      // The hub's own words. On the failure this was written for they were
      // "sent 1011 (internal error) keepalive ping timeout", which is the entire
      // diagnosis — and it used to reach the console and nowhere else.
      return `this session could not list its graphs: ${state.reason}`;
    case "loading":
      return "loading graphs...";
  }
};

export const sessionLabel = (session: SessionInfo, unreachable: boolean): string => {
  const base = `${session.backend}:${session.active_graph} (pid ${session.pid})`;
  if (!session.connected) return `${base} — disconnected`;
  return unreachable ? `${base} — unreachable` : base;
};

/** Whatever a rejected fetch carried, as something a `title` can show. */
export const hubErrorText = (error: unknown): string => {
  if (error instanceof Error) return error.message;
  if (typeof error === "string" && error !== "") return error;
  return "no reason given";
};
