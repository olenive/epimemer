/**
 * The detail drawer: two tabs, one fixed-height strip.
 *
 * The drawer used to serve one thing, the selected node's detail. A response is
 * a second thing with a **different driver and a different lifetime** — node
 * detail follows your selection, a response follows the selected record — and
 * in focus mode you want both at once (RETRIEVAL_PROVENANCE.md §5.1).
 *
 * The rule it enforces is §5.2's: **a retrieval must never steal the drawer.**
 * Nothing here is called when a record merely *arrives*; only selecting one
 * opens the Response tab, and only clicking a node fills the Node tab. The
 * agent fires on the order of ten retrievals per task, and content flipping
 * underneath you would be unreadable.
 *
 * Height stays fixed for the reason already recorded in the markup: a drawer
 * that grew with its text would re-lay out the panel under the cursor.
 */

export type DrawerTab = "node" | "response";

export interface DrawerElements {
  drawer: HTMLElement;
  tabNode: HTMLButtonElement;
  tabResponse: HTMLButtonElement;
  title: HTMLElement;
  content: HTMLElement;
  close: HTMLElement;
}

export interface DrawerHandle {
  /** Fill the Node tab and show it. `marker` is prepended when non-empty. */
  showNode: (title: string, body: string, marker?: string) => void;
  /** Fill the Response tab, show it, and open the drawer if it was hidden. */
  showResponse: (title: string, body: string) => void;
  chooseTab: (tab: DrawerTab) => void;
  activeTab: () => DrawerTab;
  isOpen: () => boolean;
  /** What a tab holds, whether or not it is the one on screen. */
  contentOf: (tab: DrawerTab) => string;
  close: () => void;
}

const EMPTY: Record<DrawerTab, string> = {
  node: "Click a node to see its detail.",
  response: "No retrieval selected — pick one from the header.",
};

const TAB_ON =
  "px-2 py-0.5 text-xs rounded-t border-b-2 border-blue-500 text-gray-800 dark:text-gray-200";
const TAB_OFF =
  "px-2 py-0.5 text-xs rounded-t border-b-2 border-transparent text-gray-600 dark:text-gray-500 hover:text-gray-800 dark:hover:text-gray-300";

/**
 * The line a dimmed node's detail carries.
 *
 * Empty unless focus mode is on *and* this node was not in the retrieval:
 * outside focus mode there is no retrieval to be absent from, and a marker
 * would be a claim about one nobody selected.
 */
export const notRetrievedMarker = (inFocus: boolean, focusOn: boolean): string =>
  focusOn && !inFocus ? "⊘ Not in this retrieval." : "";

export const initDrawer = (elements: DrawerElements): DrawerHandle => {
  const held: Record<DrawerTab, { title: string; body: string } | null> = {
    node: null,
    response: null,
  };
  let active: DrawerTab = "node";

  const render = (): void => {
    const content = held[active];
    elements.title.textContent = content?.title ?? "";
    elements.content.textContent = content?.body ?? EMPTY[active];
    elements.tabNode.className = active === "node" ? TAB_ON : TAB_OFF;
    elements.tabResponse.className = active === "response" ? TAB_ON : TAB_OFF;
  };

  const fill = (tab: DrawerTab, title: string, body: string): void => {
    held[tab] = { title, body };
    active = tab;
    elements.drawer.classList.remove("hidden");
    render();
  };

  elements.tabNode.addEventListener("click", () => chooseTab("node"));
  elements.tabResponse.addEventListener("click", () => chooseTab("response"));
  elements.close.addEventListener("click", () => close());

  const chooseTab = (tab: DrawerTab): void => {
    active = tab;
    render();
  };

  const close = (): void => {
    // Content survives, so re-opening does not start blank — closing is a
    // deliberate act about the layout, not about forgetting what you read.
    elements.drawer.classList.add("hidden");
    render();
  };

  render();

  return {
    showNode: (title, body, marker = "") =>
      fill("node", title, marker ? `${marker}\n\n${body}` : body),
    showResponse: (title, body) => fill("response", title, body),
    chooseTab,
    activeTab: () => active,
    isOpen: () => !elements.drawer.classList.contains("hidden"),
    contentOf: (tab) => held[tab]?.body ?? "",
    close,
  };
};
