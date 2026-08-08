/**
 * Vertical label layout for the timeline panel.
 *
 * Turning the axis vertical buys horizontal room for a mark's text, and
 * immediately creates the problem that room does not solve: two marks a few
 * pixels apart still want to write in the same place. This module decides
 * where each label actually goes, and where a leader line has to run to get
 * back to the tick it belongs to.
 *
 * Three properties matter more than pretty output, and the tests pin all three:
 *
 * 1. **Order is never swapped.** A label may slide off its anchor, but it must
 *    not overtake its neighbour — a timeline whose text reads out of sequence
 *    is worse than one whose text is crowded, because it is wrong rather than
 *    merely ugly.
 * 2. **Displacement is shared.** A cluster settles centred on its members'
 *    mean anchor rather than pushing everything downward from the first
 *    collision, so error is spread instead of accumulating down the column.
 * 3. **Marks that straddle the axis cannot move.** Their block *is* their
 *    label, and its position is the claim about when the thing happened.
 *    Side labels flow around them.
 *
 * When a stretch of axis has more label than room, the labels that do not fit
 * are **dropped and reported**, never crowded or spilled. An earlier draft let
 * an oversized stack overflow its space; a randomized property test found it
 * landing on labels sixty pixels away, in a stretch of axis it had no business
 * in. Overlapping text is unreadable *and* misattributes itself to the wrong
 * mark, which is worse than absent text.
 *
 * Note what is dropped: a **label**, not a mark. The renderer still draws the
 * tick, and can show the count of suppressed labels — density is answered by
 * zooming in, which is exactly when the room appears.
 *
 * **The caller's order is the priority order.** When something has to go, the
 * requests passed last go first. This module holds no opinion about which
 * labels matter; the caller has `importance` and can sort by it.
 *
 * Pure and DOM-free: this is the least certain part of the vertical redesign
 * (dev-docs/TIMELINE_VISUALISATION.md §12.9), so it is built where it can be
 * tested without a browser.
 *
 * All coordinates are pixels down the panel — larger is further into the
 * future, matching §12.1.
 */

/**
 * Which column a label lives in.
 *
 * `axis` is the straddling case of §12.3: a timepoint holding both a fact and
 * an inference is drawn as one block crossing the axis. It occupies room in
 * both side columns and is immovable.
 */
export type Column = "left" | "right" | "axis";

export interface LabelRequest {
  id: string;
  /** Where the label wants its centre: the mark's own position on the axis. */
  anchor: number;
  height: number;
  column: Column;
}

export interface PlacedLabel {
  id: string;
  column: Column;
  /** The position it wanted, kept so a leader line can find the tick again. */
  anchor: number;
  top: number;
  height: number;
  /** True when it could not sit on its anchor, and so needs a leader. */
  displaced: boolean;
}

export interface Bounds {
  top: number;
  bottom: number;
}

export interface LabelLayout {
  placed: PlacedLabel[];
  /** No room for these. In the order they were given, so ties are the caller's. */
  dropped: LabelRequest[];
}

/** Minimum clear space between two stacked labels. */
export const LABEL_GAP = 4;

/**
 * How far a label may sit from its anchor before it counts as displaced.
 *
 * Sub-pixel drift is arithmetic, not movement: drawing a leader line for it
 * would litter the panel with hairlines that connect a label to itself.
 */
const DISPLACEMENT_EPSILON = 0.5;

const sum = (values: readonly number[]): number =>
  values.reduce((total, v) => total + v, 0);

const clamp = (value: number, low: number, high: number): number =>
  Math.min(Math.max(value, low), high);

export const labelCentre = (label: PlacedLabel): number =>
  label.top + label.height / 2;

/**
 * A run of labels that have collided and now move as one block.
 *
 * Members stay in anchor order for the life of the cluster, which is what
 * makes property 1 above hold: nothing here can reorder them.
 */
interface Cluster {
  items: LabelRequest[];
  top: number;
  height: number;
}

const stackHeight = (items: readonly LabelRequest[], gap: number): number =>
  sum(items.map((i) => i.height)) + gap * Math.max(items.length - 1, 0);

/**
 * Where a cluster wants to sit: centred on its members' mean anchor.
 *
 * The mean is what spreads the displacement. Anchoring the block to its first
 * or last member instead would push the whole run one way, so a single early
 * collision would drag every later label down the column with it.
 */
const clusterTop = (
  items: readonly LabelRequest[],
  height: number,
  bounds: Bounds,
): number => {
  const meanAnchor = sum(items.map((i) => i.anchor)) / items.length;
  // `admit` has already guaranteed the stack fits, so clamping can satisfy
  // both edges at once and a cluster can never escape its range.
  return clamp(meanAnchor - height / 2, bounds.top, bounds.bottom - height);
};

const asCluster = (
  items: readonly LabelRequest[],
  bounds: Bounds,
  gap: number,
): Cluster => {
  const height = stackHeight(items, gap);
  return { items: [...items], top: clusterTop(items, height, bounds), height };
};

const overlaps = (above: Cluster, below: Cluster, gap: number): boolean =>
  below.top < above.top + above.height + gap - 1e-9;

/**
 * Place one column's labels inside one free range.
 *
 * One downward sweep, merging each label into its predecessor while they
 * overlap and re-centring the merged block each time. A single pass is enough
 * because the merge loop re-checks: a block that moves *up* is compared
 * against its new predecessor immediately, and one that moves *down* meets its
 * successors when their turn comes. An outer repeat-until-stable loop was
 * written first, and a 5,000-case randomized run could not find an input that
 * needed it, so it is gone rather than left as untestable insurance.
 */
/**
 * Split requests into those the range can hold and those it cannot.
 *
 * Taken in the caller's order, so priority is the caller's to express, and cut
 * at the point where the stack would no longer fit. Fitting is what lets
 * `clusterTop` clamp safely, which is what stops a stack from escaping into
 * the next range and writing over labels that belong to a different part of
 * the timeline.
 */
const admit = (
  requests: readonly LabelRequest[],
  bounds: Bounds,
  gap: number,
): { taken: LabelRequest[]; dropped: LabelRequest[] } => {
  const capacity = bounds.bottom - bounds.top;
  const taken: LabelRequest[] = [];
  const dropped: LabelRequest[] = [];
  for (const request of requests) {
    if (stackHeight([...taken, request], gap) <= capacity) taken.push(request);
    else dropped.push(request);
  }
  return { taken, dropped };
};

const packRange = (
  requests: readonly LabelRequest[],
  bounds: Bounds,
  gap: number,
): PlacedLabel[] => {
  const ordered = [...requests].sort((a, b) => a.anchor - b.anchor);
  const settled: Cluster[] = [];

  for (const item of ordered) {
    let current = asCluster([item], bounds, gap);
    while (
      settled.length > 0 &&
      overlaps(settled[settled.length - 1], current, gap)
    ) {
      const previous = settled.pop()!;
      current = asCluster([...previous.items, ...current.items], bounds, gap);
    }
    settled.push(current);
  }

  return settled.flatMap((cluster) => {
    let top = cluster.top;
    return cluster.items.map((item) => {
      const placed: PlacedLabel = {
        id: item.id,
        column: item.column,
        anchor: item.anchor,
        top,
        height: item.height,
        displaced:
          Math.abs(top + item.height / 2 - item.anchor) > DISPLACEMENT_EPSILON,
      };
      top += item.height + gap;
      return placed;
    });
  });
};

/**
 * The stretches of a column left free by the immovable axis blocks.
 *
 * Obstacles are padded by `gap` on both sides so a side label never touches a
 * straddling block.
 */
const freeRanges = (
  obstacles: readonly PlacedLabel[],
  bounds: Bounds,
  gap: number,
): Bounds[] => {
  const blocked = [...obstacles]
    .sort((a, b) => a.top - b.top)
    .map((o) => ({ top: o.top - gap, bottom: o.top + o.height + gap }));

  const ranges: Bounds[] = [];
  let cursor = bounds.top;
  for (const block of blocked) {
    if (block.top > cursor) ranges.push({ top: cursor, bottom: block.top });
    cursor = Math.max(cursor, block.bottom);
  }
  if (cursor < bounds.bottom) ranges.push({ top: cursor, bottom: bounds.bottom });
  return ranges.filter((r) => r.bottom > r.top);
};

/** The range an anchor falls in, else the nearest one. */
const rangeFor = (anchor: number, ranges: readonly Bounds[]): number => {
  let best = 0;
  let bestDistance = Infinity;
  ranges.forEach((range, index) => {
    const distance =
      anchor < range.top
        ? range.top - anchor
        : anchor > range.bottom
          ? anchor - range.bottom
          : 0;
    if (distance < bestDistance) {
      best = index;
      bestDistance = distance;
    }
  });
  return best;
};

const packColumn = (
  requests: readonly LabelRequest[],
  obstacles: readonly PlacedLabel[],
  bounds: Bounds,
  gap: number,
): LabelLayout => {
  if (requests.length === 0) return { placed: [], dropped: [] };

  const ranges = freeRanges(obstacles, bounds, gap);
  // Blocks filling the column leave nowhere legal to go, so every side label
  // is dropped. The marks are still drawn; only their text is suppressed.
  if (ranges.length === 0) return { placed: [], dropped: [...requests] };

  const buckets: LabelRequest[][] = ranges.map(() => []);
  for (const request of requests) {
    buckets[rangeFor(request.anchor, ranges)].push(request);
  }

  const placed: PlacedLabel[] = [];
  const dropped: LabelRequest[] = [];
  ranges.forEach((range, index) => {
    const { taken, dropped: rejected } = admit(buckets[index], range, gap);
    placed.push(...packRange(taken, range, gap));
    dropped.push(...rejected);
  });
  return { placed, dropped };
};

/**
 * Lay out every label, in anchor order within each column.
 *
 * Straddling (`axis`) marks are placed first and never move; the side columns
 * are then packed around them independently, since a left label and a right
 * label cannot collide with each other.
 *
 * Two axis blocks close enough in time to overlap are left overlapping. That
 * is a statement about the data — two mixed timepoints at nearly the same
 * instant — and moving either one would misplace it on the axis, which is the
 * one thing a timeline may not do. Blocks are never dropped for the same
 * reason: they are marks, not annotations.
 */
export const layoutLabels = (
  requests: readonly LabelRequest[],
  bounds: Bounds,
  gap: number = LABEL_GAP,
): LabelLayout => {
  const fixed: PlacedLabel[] = requests
    .filter((r) => r.column === "axis")
    .map((r) => ({
      id: r.id,
      column: r.column,
      anchor: r.anchor,
      top: clamp(
        r.anchor - r.height / 2,
        bounds.top,
        Math.max(bounds.top, bounds.bottom - r.height),
      ),
      height: r.height,
      displaced: false,
    }));

  const side = (column: Column): LabelLayout =>
    packColumn(
      requests.filter((r) => r.column === column),
      fixed,
      bounds,
      gap,
    );

  const left = side("left");
  const right = side("right");
  return {
    placed: [...fixed, ...left.placed, ...right.placed],
    dropped: [...left.dropped, ...right.dropped],
  };
};

export interface Point {
  x: number;
  y: number;
}

export interface LeaderGeometry {
  /** Where the tick sits. */
  axisX: number;
  /** The label's inner edge — the side nearest the axis. */
  labelX: number;
}

/**
 * The polyline from a mark's tick to a displaced label, as an elbow.
 *
 * Empty when the label sits on its anchor: a leader that would be a straight
 * horizontal line adds ink and no information, and at the densities this
 * module exists for, most labels do not move.
 *
 * The elbow bends halfway across, so leaders from a crowded cluster fan out
 * along a shared vertical rather than crossing each other at shallow angles.
 * Symmetric in `axisX` and `labelX`, so it serves either column unchanged.
 */
export const leaderPoints = (
  label: PlacedLabel,
  { axisX, labelX }: LeaderGeometry,
): Point[] => {
  if (!label.displaced) return [];
  const centre = labelCentre(label);
  const bend = (axisX + labelX) / 2;
  return [
    { x: axisX, y: label.anchor },
    { x: bend, y: label.anchor },
    { x: bend, y: centre },
    { x: labelX, y: centre },
  ];
};
