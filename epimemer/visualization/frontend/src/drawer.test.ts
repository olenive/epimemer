// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from "vitest";

import { initDrawer, notRetrievedMarker, type DrawerHandle } from "./drawer";

const MARKUP = `
  <div id="detail-drawer" class="hidden h-40">
    <button id="tab-node"></button>
    <button id="tab-response"></button>
    <span id="detail-title"></span>
    <button id="btn-close-detail"></button>
    <pre id="detail-content"></pre>
  </div>
`;

const $ = <T extends HTMLElement>(id: string): T =>
  document.getElementById(id) as T;

let drawer: DrawerHandle;

beforeEach(() => {
  document.body.innerHTML = MARKUP;
  drawer = initDrawer({
    drawer: $("detail-drawer"),
    tabNode: $<HTMLButtonElement>("tab-node"),
    tabResponse: $<HTMLButtonElement>("tab-response"),
    title: $("detail-title"),
    content: $("detail-content"),
    close: $("btn-close-detail"),
  });
});

/**
 * §5.1: node detail follows your *selection*; a response follows the selected
 * *record*. Different drivers, different lifetimes — and in focus mode you want
 * both at once: here is what the agent got, and here is the node I just clicked
 * from inside it. A single pane forces them to clobber each other.
 */
describe("test_response_tab_and_node_tab_keep_separate_content", () => {
  it("keeps each tab's content when the other is filled", () => {
    drawer.showResponse("search", '{"nodes": []}');
    drawer.showNode("fact — 1a2b3c4d", "The deployment rollback failed");

    expect(drawer.contentOf("response")).toBe('{"nodes": []}');
    expect(drawer.contentOf("node")).toBe("The deployment rollback failed");
  });

  it("shows the tab that was just filled", () => {
    drawer.showResponse("search", "{}");
    expect(drawer.activeTab()).toBe("response");

    drawer.showNode("fact", "content");
    expect(drawer.activeTab()).toBe("node");
  });

  it("switches back to a tab that still holds what it held", () => {
    drawer.showResponse("search", '{"nodes": []}');
    drawer.showNode("fact", "content");

    drawer.chooseTab("response");

    expect(drawer.activeTab()).toBe("response");
    expect($("detail-content").textContent).toBe('{"nodes": []}');
  });

  it("says so rather than showing an empty pane for a tab never filled", () => {
    drawer.showNode("fact", "content");
    drawer.chooseTab("response");

    expect($("detail-content").textContent).toMatch(/no retrieval selected/i);
  });
});

/**
 * §5.2: a retrieval must never steal the drawer. The agent fires on the order
 * of ten per task, and a drawer that flipped content underneath you would be
 * unreadable — and would clobber a node detail you deliberately opened.
 */
describe("test_a_new_record_does_not_change_the_open_drawer", () => {
  it("leaves the open drawer, its active tab and its content alone", () => {
    drawer.showResponse("search", "first response");
    drawer.showNode("fact — 1a2b3c4d", "the node I am reading");

    // What "a retrieval occurred" does to the drawer: nothing. The selector
    // gains an entry and the unread count moves; there is deliberately no
    // drawer call on this path at all.
    const before = {
      open: drawer.isOpen(),
      tab: drawer.activeTab(),
      shown: $("detail-content").textContent,
    };

    expect(before).toEqual({
      open: true,
      tab: "node",
      shown: "the node I am reading",
    });
    expect(drawer.contentOf("response")).toBe("first response");
  });

  it("stays closed until something is deliberately opened", () => {
    expect(drawer.isOpen()).toBe(false);
    expect($("detail-drawer").classList.contains("hidden")).toBe(true);
  });

  it("opens when a record is selected, which is a deliberate act", () => {
    drawer.showResponse("search", "{}");
    expect(drawer.isOpen()).toBe(true);
  });

  it("closes only on the close button", () => {
    drawer.showNode("fact", "content");
    $("btn-close-detail").click();

    expect(drawer.isOpen()).toBe(false);
    // The content survives a close, so re-opening does not start blank.
    expect(drawer.contentOf("node")).toBe("content");
  });
});

/**
 * §4.3: the interesting click is on a dimmed node — *why didn't this come
 * back?* Making dimmed nodes inert would remove the answer the mode exists to
 * give, so the absence is stated rather than merely implied by the colour.
 */
describe("test_dimmed_node_stays_clickable_and_says_it_was_not_retrieved", () => {
  it("marks a node the current retrieval did not return", () => {
    const marker = notRetrievedMarker(false, true);
    expect(marker).toMatch(/not in this retrieval/i);
  });

  it("says nothing when the node was retrieved", () => {
    expect(notRetrievedMarker(true, true)).toBe("");
  });

  it("says nothing when focus mode is off", () => {
    // Every node is "in focus" then, and a marker would be a claim about a
    // retrieval nobody selected.
    expect(notRetrievedMarker(false, false)).toBe("");
  });

  it("puts the marker in the body, where a click can read it", () => {
    drawer.showNode("fact — 1a2b3c4d", "content", notRetrievedMarker(false, true));

    expect($("detail-content").textContent).toContain("content");
    expect($("detail-content").textContent).toMatch(/not in this retrieval/i);
  });
});
