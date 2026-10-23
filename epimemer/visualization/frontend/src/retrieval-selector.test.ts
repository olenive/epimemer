// @vitest-environment jsdom
/**
 * What the record selector's first option says.
 *
 * The selector lists the viewed graph's retrievals, and a session can hold
 * retrievals on other graphs, so "none here" and "none yet" are different
 * answers.
 */

import { describe, expect, it } from "vitest";

import type { EventRouter } from "./events";
import { initRetrievalSelector } from "./retrieval-selector";
import type { RetrievalRecordWire } from "./types";

const router = {
  subscribe: () => () => {},
} as unknown as EventRouter;

const wire = (over: Partial<RetrievalRecordWire> = {}): RetrievalRecordWire => ({
  record_id: "000000000001",
  at: "2026-08-18T10:00:00Z",
  tool: "epimemer.search",
  query: "deployment rollback",
  graph: "default",
  retrieved: [{ node_id: "n1", provenance: "vector", score: 0.82 }],
  response_text: "{}",
  truncated: false,
  ...over,
});

const selector = () => {
  const select = document.createElement("select");
  const unread = document.createElement("span");
  const handle = initRetrievalSelector({ select, unread }, router, () => {});
  return { select, handle, first: () => select.options[0]?.textContent };
};

describe("the record selector's first option", () => {
  it("says there are no retrievals yet when the session has none", () => {
    const { select, first } = selector();
    expect(first()).toBe("No retrievals yet");
    expect(select.disabled).toBe(true);
  });

  it("says there are none in this graph when the others have some", () => {
    const { handle, first } = selector();
    handle.setViewedGraph("mine");
    handle.merge([wire({ graph: "other" })]);
    expect(first()).toBe("No retrievals in this graph");
  });

  it("offers to leave focus once there is something to focus on", () => {
    const { handle, first } = selector();
    handle.setViewedGraph("mine");
    handle.merge([wire({ graph: "mine" })]);
    expect(first()).toBe("No focus");
  });
});

describe("choosing a record that declares nothing", () => {
  it("says so on the selector", () => {
    const { select, handle } = selector();
    handle.setViewedGraph("mine");
    handle.merge([wire({ graph: "mine", retrieved: null })]);
    select.value = "000000000001";
    select.dispatchEvent(new Event("change"));

    expect(select.title).toMatch(/did not declare/i);
    expect(select.title).toMatch(/nothing is dimmed/i);
  });
});
