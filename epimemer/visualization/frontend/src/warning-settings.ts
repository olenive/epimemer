/**
 * What this graph does about advisories, shown and never changed.
 *
 * Read-only on purpose (ADVISORIES_DASHBOARD.md §2.4). A write from the browser
 * would be the first write into a graph with no author: every change today is
 * journalled against a session and a judge, and a browser has neither. That
 * identity question is parked under "Sharing a graph between users" in
 * `PROPOSED_FEATURES.md`, and this panel must not settle it by accident.
 *
 * The settings arrive in `configure_warnings`'s own response shape, over the
 * same request path the graph listing uses, so the panel and the tool cannot
 * come to disagree about what a graph is set to.
 */

import type { EventRouter } from "./events";
import type { AnyEvent, GraphSwitched, WarningSettings } from "./types";

export interface WarningSettingsElements {
  panel: HTMLElement;
  title: HTMLElement;
  mute: HTMLElement;
  rows: HTMLElement;
}

export interface WarningSettingsHandle {
  cleanup: () => void;
  /** Watch this session, and read its settings now. Null clears the panel. */
  setSession: (session: string | null) => void;
  toggle: () => void;
}

/** Where a kind's action came from. The two are genuinely different states. */
export type SettingOrigin = "inherited" | "set on this graph";

export interface WarningSettingRow {
  kind: string;
  action: string;
  origin: SettingOrigin;
}

/** The graph is in the title because the setting is per graph, always. */
export const settingsTitle = (settings: WarningSettings | null): string =>
  settings === null ? "Warnings" : `Warnings on ${settings.graph}`;

/**
 * The mute, labelled for what it does.
 *
 * It governs surfacing only and never recording: a graph with it off still
 * journals every operation that went ahead against an objecting warning. So it
 * is not "warnings off", and saying so would misdescribe the graph.
 */
export const muteLine = (settings: WarningSettings): string =>
  settings.surface ? "warnings shown to the agent" : "warnings muted for the agent";

/**
 * One row per kind: the action in force, and where that answer came from.
 *
 * A kind absent from `overridden.by_kind` is inherited. A kind set explicitly
 * to the value it would have inherited anyway is *not* the same thing: the
 * first tracks the process default when it changes, the second stays put.
 *
 * The order is the response's, which is the kinds sorted by name. Sorting again
 * here would be a second opinion about an order that already has one.
 */
export const settingsRows = (settings: WarningSettings): WarningSettingRow[] => {
  const named = settings.overridden.by_kind ?? {};
  return Object.entries(settings.actions).map(([kind, action]) => ({
    kind,
    action,
    origin: kind in named ? "set on this graph" : "inherited",
  }));
};

const ROW = "flex items-baseline gap-2 text-[10px]";

const rowElement = (row: WarningSettingRow): HTMLElement => {
  const line = document.createElement("div");
  line.className = ROW;

  const kind = document.createElement("span");
  kind.className = "text-content-secondary truncate";
  kind.textContent = row.kind;

  const action = document.createElement("span");
  action.className = "ml-auto text-content-primary";
  action.textContent = row.action;

  const origin = document.createElement("span");
  origin.className = "text-content-muted w-28 text-right shrink-0";
  origin.textContent = row.origin;

  line.append(kind, action, origin);
  return line;
};

/**
 * Wire the panel up.
 *
 * `read` is the fetch, injected rather than imported so the panel can be driven
 * without a socket or a server behind it. It re-reads on `graph_switched` for
 * the session being watched, as the reflect badge does, because the settings
 * belong to the graph and a switch changes them.
 */
export const initWarningSettings = (
  elements: WarningSettingsElements,
  router: EventRouter,
  read: (session: string) => Promise<WarningSettings>,
): WarningSettingsHandle => {
  let session: string | null = null;

  const renderFailure = (reason: string): void => {
    elements.title.textContent = settingsTitle(null);
    // Say what went wrong rather than leave the previous graph's answers up:
    // stale settings shown as current are worse than no settings at all.
    elements.mute.textContent = reason;
    elements.rows.innerHTML = "";
  };

  const renderSettings = (settings: WarningSettings): void => {
    elements.title.textContent = settingsTitle(settings);
    elements.mute.textContent = muteLine(settings);
    elements.rows.innerHTML = "";
    for (const row of settingsRows(settings)) elements.rows.appendChild(rowElement(row));
  };

  const refresh = (): void => {
    if (session === null) {
      renderFailure("No session selected.");
      return;
    }
    const asked = session;
    read(asked)
      .then((settings) => {
        // A session switch during the read wins: what came back describes a
        // graph nobody is looking at any more.
        if (asked === session) renderSettings(settings);
      })
      .catch((err) => {
        if (asked === session) renderFailure(String(err?.message ?? err));
      });
  };

  const unsubs = [
    router.subscribe("graph_switched", (event: AnyEvent) => {
      const switched = event as GraphSwitched;
      if (session !== null && switched.session_id === session) refresh();
    }),
  ];

  const setSession = (next: string | null): void => {
    session = next;
    refresh();
  };

  return {
    cleanup: () => unsubs.forEach((unsub) => unsub()),
    setSession,
    toggle: () => elements.panel.classList.toggle("hidden"),
  };
};
