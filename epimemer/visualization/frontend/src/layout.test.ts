// @vitest-environment jsdom
/**
 * Structural checks on `index.html`.
 *
 * The split pane's behaviour is decided by which elements are its children, and
 * that is expressed in markup rather than in code — so nothing in the module
 * tests can catch it being wrong. It was wrong: the node detail drawer, written
 * as a full-width bottom drawer, ended up as a third *column* of the split.
 * Being a flex item in a row, it took its width from its content, so every
 * hover over a timeline mark resized it and shoved the timeline sideways.
 */
import { beforeAll, describe, expect, it } from "vitest";

// Vite's `?raw` rather than `node:fs`: it keeps this a browser-target module,
// so the frontend needs no Node type dependency to type-check.
import indexHtml from "../index.html?raw";

let doc: Document;

beforeAll(() => {
  doc = new DOMParser().parseFromString(indexHtml, "text/html");
});

const byId = (id: string): HTMLElement => {
  const el = doc.getElementById(id);
  if (el === null) throw new Error(`#${id} is missing from index.html`);
  return el;
};

describe("split pane structure", () => {
  it("has exactly the two halves and the divider as children", () => {
    // Anything else in here becomes a column and competes for the width.
    expect([...byId("split-container").children].map((c) => c.id)).toEqual([
      "split-left",
      "split-divider",
      "split-right",
    ]);
  });

  it("keeps the detail drawer out of the split", () => {
    expect(byId("split-container").contains(byId("detail-drawer"))).toBe(false);
  });

  it("gives the drawer a fixed height so opening it cannot resize on hover", () => {
    // `max-h-*` would let the box grow with its text, re-laying out the panel
    // under the cursor as you read.
    const classes = byId("detail-drawer").className;
    expect(classes).toMatch(/\bh-\d+\b/);
    expect(classes).not.toMatch(/\bmax-h-/);
  });

  it("scrolls long detail inside the drawer rather than growing it", () => {
    expect(byId("detail-content").className).toContain("overflow-auto");
  });
});

/**
 * The same hazard one panel later. EVENT_LOG.md §10 ruled that a fourth
 * vertical column would starve the graph, so the log is a rail: a fixed-width
 * sibling of the split, never a child of it.
 */
describe("the log rail", () => {
  it("is a sibling of the split, not a column inside it", () => {
    expect(byId("split-container").contains(byId("log-rail"))).toBe(false);
    expect(byId("main-row").contains(byId("log-rail"))).toBe(true);
  });

  it("takes a fixed width rather than its content's", () => {
    // Without `shrink-0` a long summary would widen the rail and squeeze the
    // graph — the drawer's failure, rotated ninety degrees.
    const classes = byId("log-rail").className;
    expect(classes).toMatch(/\bw-\d+\b/);
    expect(classes).toContain("shrink-0");
  });

  it("scrolls its entries internally", () => {
    expect(byId("log-entries").className).toContain("overflow-y-auto");
  });

  it("starts hidden, so the graph keeps its width until the log is asked for", () => {
    expect(byId("log-rail").className).toContain("hidden");
  });
});

describe("elements the panels bind to", () => {
  const REQUIRED = [
    "split-container",
    "split-left",
    "split-divider",
    "split-right",
    "btn-toggle-graph",
    "btn-toggle-timeline",
    "timeline-body",
    "timeline-undated",
    "timeline-empty",
    "timeline-select",
    "timeline-mode",
    "timeline-type",
    "timeline-status",
    "timeline-metacontext",
    "timeline-query",
    "timeline-range-start",
    "timeline-range-end",
    "timeline-now",
    "timeline-reset-zoom",
    "graph-container",
    "detail-drawer",
    "detail-title",
    "detail-content",
    "btn-close-detail",
    "main-row",
    "btn-toggle-log",
    "log-rail",
    "log-entries",
    "log-empty",
    "log-verbs",
    "log-node-id",
    "log-text",
    "log-range-start",
    "log-range-end",
    "log-clear",
    "log-count",
    "log-note",
    "record-selector",
    "record-unread",
    "tab-node",
    "tab-response",
    "btn-palette",
    "palette-panel",
  ];

  // `main.ts` throws on a missing id, so a rename here is a blank dashboard.
  it.each(REQUIRED)("has #%s", (id) => {
    expect(doc.getElementById(id)).not.toBeNull();
  });
});

describe("the timeline half", () => {
  it("lets the axis take the height, so the scale has room to measure", () => {
    // `flex-1 min-h-0` is what stops a tall SVG from growing its own container.
    expect(byId("timeline-body").className).toContain("flex-1");
    expect(byId("timeline-body").className).toContain("min-h-0");
  });
});

/**
 * The token migration, guarded.
 *
 * A panel written the old way would still *look* right in both themes, because
 * a `gray-300 dark:gray-900` pair says the same thing the token does. It would
 * ignore the viewer's colour settings entirely, and nothing about its
 * appearance would say so. This is the check that a reviewer cannot forget to
 * make, and it is why the migration was worth finishing rather than leaving
 * half the chrome on raw greys.
 *
 * `white` counts: it was the light half of every form control's pair.
 */
describe("colour tokens", () => {
  // Vite inlines these at build time, so the suite stays a browser target.
  const modules = import.meta.glob("./*.ts", { query: "?raw", import: "default", eager: true }) as Record<
    string,
    string
  >;

  const RAW_COLOUR = /(?:^|\s|")((?:[a-z-]+:)*(?:bg|text|border)-(?:gray-\d+|white))\b/g;

  const offenders = (source: string): string[] => [
    ...new Set([...source.matchAll(RAW_COLOUR)].map((m) => m[1])),
  ];

  it("leaves no raw grey in the markup", () => {
    expect(offenders(indexHtml)).toEqual([]);
  });

  it.each(
    Object.keys(modules).filter((name) => !name.endsWith(".test.ts")),
  )("leaves no raw grey in %s", (name) => {
    expect(offenders(modules[name])).toEqual([]);
  });

  it("finds the modules it claims to check", () => {
    // A glob matching nothing would make every assertion above vacuous, which
    // is the failure mode of deriving a population rather than listing it.
    const checked = Object.keys(modules).filter((n) => !n.endsWith(".test.ts"));
    expect(checked.length).toBeGreaterThan(15);
    expect(checked).toContain("./theme.ts");
    expect(checked).toContain("./timeline-panel.ts");
  });

  it("still catches a raw grey when there is one", () => {
    // The regex is the guard; a typo in it would pass everything silently.
    expect(offenders('class="bg-gray-300 dark:bg-gray-900"')).toEqual([
      "bg-gray-300",
      "dark:bg-gray-900",
    ]);
    expect(offenders('class="bg-white"')).toEqual(["bg-white"]);
    expect(offenders('class="bg-surface-chrome text-content-muted"')).toEqual([]);
  });
});
