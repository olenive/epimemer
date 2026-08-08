import { describe, expect, it } from "vitest";

import {
  LABEL_GAP,
  labelCentre,
  layoutLabels,
  leaderPoints,
  type Bounds,
  type Column,
  type LabelRequest,
  type PlacedLabel,
} from "./timeline-labels";

const BOUNDS: Bounds = { top: 0, bottom: 400 };

const request = (
  id: string,
  anchor: number,
  { height = 20, column = "left" as Column } = {},
): LabelRequest => ({ id, anchor, height, column });

const byId = (placed: readonly PlacedLabel[]): Map<string, PlacedLabel> =>
  new Map(placed.map((p) => [p.id, p]));

/** Every pair in one column clears `gap`, in the order they were placed. */
const noOverlaps = (placed: readonly PlacedLabel[], gap = LABEL_GAP): boolean => {
  const sorted = [...placed].sort((a, b) => a.top - b.top);
  return sorted.every(
    (label, i) =>
      i === 0 || sorted[i - 1].top + sorted[i - 1].height + gap <= label.top + 1e-9,
  );
};

describe("layoutLabels — labels that fit", () => {
  it("leaves well-separated labels on their anchors", () => {
    const placed = layoutLabels(
      [request("a", 50), request("b", 150), request("c", 250)],
      BOUNDS,
    ).placed;

    for (const label of placed) {
      expect(labelCentre(label)).toBeCloseTo(label.anchor);
      expect(label.displaced).toBe(false);
    }
  });

  it("places every request exactly once", () => {
    const placed = layoutLabels(
      [request("a", 50), request("b", 52), request("c", 54)],
      BOUNDS,
    ).placed;
    expect(placed.map((p) => p.id).sort()).toEqual(["a", "b", "c"]);
  });

  it("does not mutate its input", () => {
    const requests = [request("a", 50), request("b", 52)];
    const snapshot = JSON.parse(JSON.stringify(requests));
    layoutLabels(requests, BOUNDS).placed;
    expect(requests).toEqual(snapshot);
  });
});

describe("layoutLabels — collisions", () => {
  it("separates two overlapping labels and marks them displaced", () => {
    const placed = layoutLabels([request("a", 100), request("b", 105)], BOUNDS).placed;

    expect(noOverlaps(placed)).toBe(true);
    expect(placed.every((p) => p.displaced)).toBe(true);
  });

  it("shares the displacement rather than pushing one way", () => {
    // Two labels wanting the same centre should end up straddling it, not with
    // one parked and the other shoved down.
    const placed = byId(layoutLabels([request("a", 100), request("b", 100)], BOUNDS).placed);

    const a = labelCentre(placed.get("a")!);
    const b = labelCentre(placed.get("b")!);
    expect(a).toBeLessThan(100);
    expect(b).toBeGreaterThan(100);
    expect(100 - a).toBeCloseTo(b - 100);
  });

  it("settles a cluster on its members' mean anchor", () => {
    // Distinct anchors, so anchoring the block to its first or last member
    // would land somewhere different from anchoring it to their mean.
    const placed = byId(layoutLabels([request("a", 100), request("b", 108)], BOUNDS).placed);

    const centres = [
      labelCentre(placed.get("a")!),
      labelCentre(placed.get("b")!),
    ];
    expect((centres[0] + centres[1]) / 2).toBeCloseTo(104);
  });

  it("opens a full gap between labels that were merely close", () => {
    // These do not literally overlap — 2px apart at their anchors — but they
    // are closer than the gap, so they still have to be pushed apart.
    const placed = layoutLabels([request("a", 100), request("b", 122)], BOUNDS).placed;

    expect(noOverlaps(placed)).toBe(true);
    expect(placed.every((p) => p.displaced)).toBe(true);
  });

  it("never reorders labels", () => {
    // Crowded and shuffled: a naive "push down from the first collision" pass
    // emits these out of sequence, and the layout must impose the order rather
    // than inherit it from the input.
    const requests = [
      request("c", 102),
      request("e", 104),
      request("a", 100),
      request("d", 103),
      request("b", 101),
    ];
    const placed = layoutLabels(requests, BOUNDS).placed;

    const inAnchorOrder = [...placed].sort((x, y) => x.anchor - y.anchor);
    const inScreenOrder = [...placed].sort((x, y) => x.top - y.top);
    expect(inScreenOrder.map((p) => p.id)).toEqual(inAnchorOrder.map((p) => p.id));
  });

  it("resolves a chain where each label only overlaps its neighbour", () => {
    // No pair is far apart, but no single pass fixes it either: moving one
    // clears its neighbour into the next one along.
    const requests = Array.from({ length: 8 }, (_, i) =>
      request(`n${i}`, 200 + i * 6),
    );
    const placed = layoutLabels(requests, BOUNDS).placed;

    expect(noOverlaps(placed)).toBe(true);
    expect(placed).toHaveLength(8);
  });

  it("respects a custom gap", () => {
    const placed = layoutLabels(
      [request("a", 100), request("b", 104)],
      BOUNDS,
      20,
    ).placed;
    expect(noOverlaps(placed, 20)).toBe(true);
  });
});

describe("layoutLabels — bounds", () => {
  it("keeps a cluster inside the top edge", () => {
    const placed = layoutLabels(
      [request("a", 2), request("b", 4), request("c", 6)],
      BOUNDS,
    ).placed;

    expect(Math.min(...placed.map((p) => p.top))).toBeGreaterThanOrEqual(0);
    expect(noOverlaps(placed)).toBe(true);
  });

  it("keeps a cluster inside the bottom edge", () => {
    const placed = layoutLabels(
      [request("a", 396), request("b", 398), request("c", 399)],
      BOUNDS,
    ).placed;

    expect(Math.max(...placed.map((p) => p.top + p.height))).toBeLessThanOrEqual(400);
    expect(noOverlaps(placed)).toBe(true);
  });

  it("drops what will not fit rather than overflowing its space", () => {
    // Twelve 20px labels plus gaps need ~280px; the range is 100.
    const requests = Array.from({ length: 12 }, (_, i) => request(`n${i}`, 50 + i));
    const { placed, dropped } = layoutLabels(requests, { top: 0, bottom: 100 });

    expect(placed.length + dropped.length).toBe(12);
    expect(placed.length).toBeLessThan(12);
    expect(noOverlaps(placed)).toBe(true);
    expect(Math.min(...placed.map((p) => p.top))).toBeGreaterThanOrEqual(0);
    expect(Math.max(...placed.map((p) => p.top + p.height))).toBeLessThanOrEqual(100);
  });

  it("drops the labels the caller ranked last", () => {
    // Priority is the caller's: whatever it passes first survives. Heights
    // vary, so an admission that quietly reordered by size — taking the small
    // ones because more of them fit — would keep a different set.
    // The caller's order contradicts height order on purpose: admitting the
    // two small labels first fills the space, while an admission that quietly
    // sorted by size would keep the big one and drop a small one instead.
    const requests = [
      request("small-a", 50, { height: 8 }),
      request("small-b", 51, { height: 8 }),
      request("large", 52, { height: 60 }),
    ];
    const { placed, dropped } = layoutLabels(requests, { top: 0, bottom: 80 });

    expect(placed.map((p) => p.id)).toEqual(["small-a", "small-b"]);
    expect(dropped.map((d) => d.id)).toEqual(["large"]);
  });

  it("keeps a big first-ranked label over two smaller ones behind it", () => {
    // The mirror of the case above. Fitting the most labels is *not* the goal:
    // the caller ranked the big one first, so it survives even though two
    // smaller ones would have fitted in its place.
    const requests = [
      request("ranked-first", 50, { height: 30 }),
      request("ranked-second", 51, { height: 10 }),
      request("ranked-third", 52, { height: 25 }),
    ];
    const { placed, dropped } = layoutLabels(requests, { top: 0, bottom: 45 });

    expect(placed.map((p) => p.id)).toEqual(["ranked-first", "ranked-second"]);
    expect(dropped.map((d) => d.id)).toEqual(["ranked-third"]);
  });

  it("does not let a crowded stretch write over a distant one", () => {
    // The case a randomized run found: labels crammed between two blocks used
    // to overflow and land on labels belonging to a different part of the axis.
    const requests = [
      request("block-a", 86, { height: 36, column: "axis" }),
      request("block-b", 111, { height: 22, column: "axis" }),
      request("crammed", 85, { height: 43, column: "right" }),
      request("distant", 158, { height: 9, column: "right" }),
    ];
    const { placed } = layoutLabels(requests, BOUNDS);

    expect(noOverlaps(placed.filter((p) => p.column === "right"))).toBe(true);
  });
});

describe("layoutLabels — columns", () => {
  it("does not let the two side columns collide with each other", () => {
    // Same anchors on both sides: they share no horizontal space, so neither
    // should be pushed off its mark.
    const placed = byId(
      layoutLabels(
        [
          request("l", 100, { column: "left" }),
          request("r", 100, { column: "right" }),
        ],
        BOUNDS,
      ).placed,
    );

    expect(placed.get("l")!.displaced).toBe(false);
    expect(placed.get("r")!.displaced).toBe(false);
    expect(labelCentre(placed.get("l")!)).toBeCloseTo(100);
    expect(labelCentre(placed.get("r")!)).toBeCloseTo(100);
  });

  it("packs each column independently", () => {
    const placed = layoutLabels(
      [
        request("l1", 100, { column: "left" }),
        request("l2", 104, { column: "left" }),
        request("r1", 100, { column: "right" }),
        request("r2", 104, { column: "right" }),
      ],
      BOUNDS,
    ).placed;

    expect(noOverlaps(placed.filter((p) => p.column === "left"))).toBe(true);
    expect(noOverlaps(placed.filter((p) => p.column === "right"))).toBe(true);
  });
});

describe("layoutLabels — straddling marks", () => {
  it("never moves an axis block", () => {
    const placed = byId(
      layoutLabels(
        [
          request("block", 200, { height: 40, column: "axis" }),
          request("a", 200),
          request("b", 205),
        ],
        BOUNDS,
      ).placed,
    );

    const block = placed.get("block")!;
    expect(labelCentre(block)).toBeCloseTo(200);
    expect(block.displaced).toBe(false);
  });

  it("flows side labels around an axis block", () => {
    const block = request("block", 200, { height: 40, column: "axis" });
    const placed = layoutLabels([block, request("a", 200), request("b", 205)], BOUNDS).placed;

    const blockTop = 180;
    const blockBottom = 220;
    for (const label of placed.filter((p) => p.column === "left")) {
      const clearsAbove = label.top + label.height <= blockTop - LABEL_GAP + 1e-9;
      const clearsBelow = label.top >= blockBottom + LABEL_GAP - 1e-9;
      expect(clearsAbove || clearsBelow).toBe(true);
    }
  });

  it("packs each side of a block into the space on that side", () => {
    // A block at 180–220 splits the column. Both labels are clear of it and of
    // each other, so both should keep their anchors — which they cannot if
    // every label is dropped into the first free range.
    const placed = byId(
      layoutLabels(
        [
          request("block", 200, { height: 40, column: "axis" }),
          request("above", 100),
          request("below", 300),
        ],
        BOUNDS,
      ).placed,
    );

    expect(labelCentre(placed.get("above")!)).toBeCloseTo(100);
    expect(labelCentre(placed.get("below")!)).toBeCloseTo(300);
  });

  it("drops side labels when blocks fill the column", () => {
    // Nowhere legal to go. The block is a mark and stays; the side label is
    // annotation, and there is no honest place to put it.
    const { placed, dropped } = layoutLabels(
      [
        request("block", 200, { height: 400, column: "axis" }),
        request("a", 200),
      ],
      BOUNDS,
    );
    expect(placed.map((p) => p.id)).toEqual(["block"]);
    expect(dropped.map((d) => d.id)).toEqual(["a"]);
  });

  it("leaves two overlapping axis blocks where the data puts them", () => {
    const placed = byId(
      layoutLabels(
        [
          request("x", 200, { height: 40, column: "axis" }),
          request("y", 210, { height: 40, column: "axis" }),
        ],
        BOUNDS,
      ).placed,
    );

    expect(labelCentre(placed.get("x")!)).toBeCloseTo(200);
    expect(labelCentre(placed.get("y")!)).toBeCloseTo(210);
  });
});

describe("leaderPoints", () => {
  const undisplaced: PlacedLabel = {
    id: "a",
    column: "left",
    anchor: 100,
    top: 90,
    height: 20,
    displaced: false,
  };
  const displaced: PlacedLabel = { ...undisplaced, top: 140, displaced: true };

  it("draws nothing for a label sitting on its anchor", () => {
    expect(leaderPoints(undisplaced, { axisX: 200, labelX: 160 })).toEqual([]);
  });

  it("runs from the tick to the label as an elbow", () => {
    const points = leaderPoints(displaced, { axisX: 200, labelX: 160 });

    expect(points).toHaveLength(4);
    expect(points[0]).toEqual({ x: 200, y: 100 });
    expect(points[3]).toEqual({ x: 160, y: 150 });
    // The bend is one shared vertical, so leaders from a cluster fan rather
    // than cross.
    expect(points[1].x).toBe(180);
    expect(points[2].x).toBe(180);
  });

  it("serves the right column unchanged", () => {
    const points = leaderPoints({ ...displaced, column: "right" }, {
      axisX: 200,
      labelX: 240,
    });

    expect(points[0]).toEqual({ x: 200, y: 100 });
    expect(points[3]).toEqual({ x: 240, y: 150 });
    expect(points[1].x).toBe(220);
  });
});


/**
 * A tiny deterministic PRNG, so a failure is reproducible from its seed.
 * The layout is pure, so random input is fair game and cheap.
 */
const lcg = (seed: number) => () => {
  seed = (seed * 1103515245 + 12345) % 2147483648;
  return seed / 2147483648;
};

describe("layoutLabels — properties over random input", () => {
  it("never overlaps and never reorders, whatever it is handed", () => {
    const random = lcg(20260808);

    for (let trial = 0; trial < 300; trial++) {
      const count = 1 + Math.floor(random() * 14);
      const requests = Array.from({ length: count }, (_, i) =>
        request(`n${i}`, Math.round(random() * 420) - 10, {
          height: 8 + Math.round(random() * 40),
          column: (["left", "right", "axis"] as const)[
            Math.floor(random() * 3)
          ],
        }),
      );

      const { placed, dropped } = layoutLabels(requests, BOUNDS);
      expect(placed.length + dropped.length).toBe(count);
      expect(placed.every((p) => p.top >= BOUNDS.top)).toBe(true);
      expect(
        placed.every((p) => p.column === "axis" || p.top + p.height <= BOUNDS.bottom),
      ).toBe(true);

      for (const column of ["left", "right"] as const) {
        const inColumn = placed.filter((p) => p.column === column);
        expect(noOverlaps(inColumn)).toBe(true);

        // Stated pairwise over *distinct* anchors: two marks at the same
        // instant have no true order, so demanding one would be testing the
        // tie-break rather than the layout.
        for (const a of inColumn) {
          for (const b of inColumn) {
            if (a.anchor < b.anchor) expect(a.top).toBeLessThan(b.top);
          }
        }
      }
    }
  });
});
