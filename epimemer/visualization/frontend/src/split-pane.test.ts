// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from "vitest";

import {
  MAX_FRACTION,
  MIN_FRACTION,
  SPLIT_STORAGE_KEY,
  clampFraction,
  initSplitPane,
  parseFraction,
  type SplitPaneElements,
} from "./split-pane";

const MARKUP = `
  <div id="container">
    <div id="left"></div>
    <div id="divider" tabindex="0"></div>
    <div id="right"></div>
  </div>
  <button id="toggle-left"></button>
  <button id="toggle-right"></button>
`;

const el = <T extends HTMLElement>(id: string): T =>
  document.getElementById(id) as T;

const elements = (): SplitPaneElements => ({
  container: el("container"),
  left: el("left"),
  right: el("right"),
  divider: el("divider"),
  toggleLeft: el("toggle-left"),
  toggleRight: el("toggle-right"),
});

const click = (element: Element): void => {
  element.dispatchEvent(new MouseEvent("click", { bubbles: true }));
};

/** jsdom lays nothing out, so the container has to be told how wide it is. */
const widthIs = (width: number): void => {
  el("container").getBoundingClientRect = () =>
    ({ left: 0, width, top: 0, height: 100, right: width, bottom: 100 }) as DOMRect;
};

beforeEach(() => {
  document.body.innerHTML = MARKUP;
  localStorage.clear();
  widthIs(1000);
  // jsdom has no pointer capture on elements.
  Element.prototype.setPointerCapture = () => {};
});

describe("clampFraction", () => {
  it("keeps the divider inside its travel", () => {
    expect(clampFraction(0.5)).toBe(0.5);
    expect(clampFraction(0)).toBe(MIN_FRACTION);
    expect(clampFraction(1)).toBe(MAX_FRACTION);
  });
});

describe("parseFraction", () => {
  it("uses the fallback when nothing is stored", () => {
    expect(parseFraction(null, 0.4)).toBe(0.4);
  });

  it("uses the fallback for a value that is not a number", () => {
    // A corrupt preference should cost the layout, not the page.
    expect(parseFraction("banana", 0.4)).toBe(0.4);
  });

  it("clamps a stored value that is out of range", () => {
    expect(parseFraction("0.99", 0.4)).toBe(MAX_FRACTION);
  });
});

describe("initSplitPane", () => {
  it("gives the left half its default share", () => {
    initSplitPane(elements(), 0.6);
    expect(el("left").style.flex).toBe("0 0 60.00%");
    expect(el("right").style.flex).toBe("1 1 0%");
  });

  it("restores the split from storage", () => {
    localStorage.setItem(SPLIT_STORAGE_KEY, "0.3");
    initSplitPane(elements(), 0.6);
    expect(el("left").style.flex).toBe("0 0 30.00%");
  });

  it("survives storage being unavailable", () => {
    const original = Object.getOwnPropertyDescriptor(window, "localStorage");
    Object.defineProperty(window, "localStorage", {
      configurable: true,
      get() {
        throw new Error("blocked");
      },
    });
    expect(() => initSplitPane(elements(), 0.6)).not.toThrow();
    if (original) Object.defineProperty(window, "localStorage", original);
  });
});

describe("collapsing", () => {
  it("hides a half and gives the other everything", () => {
    initSplitPane(elements());
    click(el("toggle-left"));

    expect(el("left").style.display).toBe("none");
    expect(el("divider").style.display).toBe("none");
    // Cleared, not left carrying the width it had while sharing.
    expect(el("right").style.flex).toBe("");
  });

  it("refuses to collapse the last visible half", () => {
    // Both hidden is an empty window with no way back.
    initSplitPane(elements());
    click(el("toggle-left"));
    click(el("toggle-right"));

    expect(el("right").style.display).not.toBe("none");
  });

  it("restores the divider and the split when a half comes back", () => {
    initSplitPane(elements(), 0.4);
    click(el("toggle-left"));
    click(el("toggle-left"));

    expect(el("left").style.display).not.toBe("none");
    expect(el("left").style.flex).toBe("0 0 40.00%");
    expect(el("divider").style.display).not.toBe("none");
  });

  it("reports which halves are showing", () => {
    initSplitPane(elements());
    expect(el("toggle-left").getAttribute("aria-pressed")).toBe("true");
    click(el("toggle-left"));
    expect(el("toggle-left").getAttribute("aria-pressed")).toBe("false");
  });
});

describe("dragging", () => {
  const drag = (toX: number): void => {
    el("divider").dispatchEvent(
      new PointerEvent("pointerdown", { bubbles: true, pointerId: 1 }),
    );
    el("divider").dispatchEvent(
      new PointerEvent("pointermove", { bubbles: true, pointerId: 1, clientX: toX }),
    );
    el("divider").dispatchEvent(
      new PointerEvent("pointerup", { bubbles: true, pointerId: 1 }),
    );
  };

  it("moves the split to where the pointer is", () => {
    initSplitPane(elements(), 0.5);
    drag(250);
    expect(el("left").style.flex).toBe("0 0 25.00%");
  });

  it("does not follow the pointer before a drag starts", () => {
    initSplitPane(elements(), 0.5);
    el("divider").dispatchEvent(
      new PointerEvent("pointermove", { bubbles: true, pointerId: 1, clientX: 250 }),
    );
    expect(el("left").style.flex).toBe("0 0 50.00%");
  });

  it("keeps the divider inside its travel", () => {
    initSplitPane(elements(), 0.5);
    drag(990);
    expect(el("left").style.flex).toBe(`0 0 ${(MAX_FRACTION * 100).toFixed(2)}%`);
  });

  it("remembers the split for next time", () => {
    initSplitPane(elements(), 0.5);
    drag(300);
    expect(localStorage.getItem(SPLIT_STORAGE_KEY)).toBe("0.3");
  });

  it("keeps the current split when the container has no width yet", () => {
    // A drag before layout would otherwise divide by zero and jump.
    widthIs(0);
    initSplitPane(elements(), 0.5);
    drag(250);
    expect(el("left").style.flex).toBe("0 0 50.00%");
  });
});

describe("keyboard", () => {
  const press = (key: string): void => {
    el("divider").dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true }));
  };

  it("moves the divider with the arrow keys", () => {
    initSplitPane(elements(), 0.5);
    press("ArrowRight");
    expect(el("left").style.flex).toBe("0 0 55.00%");
    press("ArrowLeft");
    expect(el("left").style.flex).toBe("0 0 50.00%");
  });

  it("ignores other keys", () => {
    initSplitPane(elements(), 0.5);
    press("a");
    expect(el("left").style.flex).toBe("0 0 50.00%");
  });
});

describe("cleanup", () => {
  it("stops listening", () => {
    const handle = initSplitPane(elements(), 0.5);
    handle.cleanup();

    click(el("toggle-left"));

    expect(el("left").style.display).not.toBe("none");
  });
});
