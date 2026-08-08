/**
 * Resizable split pane with collapsible halves.
 *
 * The graph and the timeline are both primary views that want the whole
 * window, and which one that is changes minute to minute. So neither is a
 * drawer: the divider slides, and either half can be collapsed to give the
 * other everything.
 *
 * The split is stored as the left half's fraction of the width rather than a
 * pixel count, so a window resize keeps the proportion the user chose instead
 * of stranding one panel.
 *
 * Restored from an earlier version of this dashboard (commit `c94e5b5`), with
 * the ratio state, keyboard resizing and persistence added.
 */

export interface SplitPaneElements {
  container: HTMLElement;
  left: HTMLElement;
  right: HTMLElement;
  divider: HTMLElement;
  toggleLeft: HTMLElement;
  toggleRight: HTMLElement;
}

export interface SplitPaneHandle {
  cleanup: () => void;
}

export const SPLIT_STORAGE_KEY = "epimemer.split";

/**
 * How far the divider may travel.
 *
 * A panel squeezed below this is unreadable but still costs a scrollbar and a
 * re-render on every frame of the drag; collapsing is the honest way to get
 * rid of one, and that is what the toggles are for.
 */
export const MIN_FRACTION = 0.15;
export const MAX_FRACTION = 0.85;

/** Keyboard resize step, as a fraction of the width. */
const KEY_STEP = 0.05;

export const clampFraction = (fraction: number): number =>
  Math.min(Math.max(fraction, MIN_FRACTION), MAX_FRACTION);

/**
 * Read a persisted split, tolerating storage being unavailable or corrupt.
 *
 * Anything unparseable gives the default rather than an error: a bad value in
 * storage should cost the user their layout preference, not the page.
 */
export const parseFraction = (stored: string | null, fallback: number): number => {
  if (stored === null) return fallback;
  const value = Number.parseFloat(stored);
  return Number.isFinite(value) ? clampFraction(value) : fallback;
};

interface Visibility {
  left: boolean;
  right: boolean;
}

const ACTIVE_TOGGLE = ["bg-blue-100", "text-blue-700", "dark:bg-blue-900/50", "dark:text-blue-300"];

export const initSplitPane = (
  els: SplitPaneElements,
  defaultFraction = 0.55,
): SplitPaneHandle => {
  let fraction = parseFraction(readStored(), defaultFraction);
  const visible: Visibility = { left: true, right: true };

  function readStored(): string | null {
    try {
      return localStorage.getItem(SPLIT_STORAGE_KEY);
    } catch {
      return null;
    }
  }

  const persist = (): void => {
    try {
      localStorage.setItem(SPLIT_STORAGE_KEY, String(fraction));
    } catch {
      // A split that does not survive a reload beats a page that does not load.
    }
  };

  const apply = (): void => {
    els.left.style.display = visible.left ? "" : "none";
    els.right.style.display = visible.right ? "" : "none";
    els.divider.style.display = visible.left && visible.right ? "" : "none";

    if (visible.left && visible.right) {
      els.left.style.flex = `0 0 ${(fraction * 100).toFixed(2)}%`;
      els.right.style.flex = "1 1 0%";
    } else {
      // The survivor takes everything; `flex` is cleared so it is not still
      // carrying the width it had when it was sharing.
      els.left.style.flex = "";
      els.right.style.flex = "";
    }

    els.divider.setAttribute("aria-valuenow", String(Math.round(fraction * 100)));
    for (const [toggle, shown] of [
      [els.toggleLeft, visible.left],
      [els.toggleRight, visible.right],
    ] as const) {
      toggle.setAttribute("aria-pressed", String(shown));
      for (const cls of ACTIVE_TOGGLE) toggle.classList.toggle(cls, shown);
    }
  };

  // --- Drag ---

  let dragging = false;

  const positionFrom = (clientX: number): number => {
    const rect = els.container.getBoundingClientRect();
    if (rect.width === 0) return fraction;
    return clampFraction((clientX - rect.left) / rect.width);
  };

  const onPointerDown = (e: PointerEvent): void => {
    dragging = true;
    els.divider.setPointerCapture(e.pointerId);
    e.preventDefault();
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  };

  const onPointerMove = (e: PointerEvent): void => {
    if (!dragging) return;
    fraction = positionFrom(e.clientX);
    apply();
  };

  const onPointerUp = (): void => {
    if (!dragging) return;
    dragging = false;
    document.body.style.cursor = "";
    document.body.style.userSelect = "";
    persist();
  };

  const onKeyDown = (e: KeyboardEvent): void => {
    const step = e.key === "ArrowLeft" ? -KEY_STEP : e.key === "ArrowRight" ? KEY_STEP : 0;
    if (step === 0) return;
    e.preventDefault();
    fraction = clampFraction(fraction + step);
    apply();
    persist();
  };

  els.divider.addEventListener("pointerdown", onPointerDown);
  els.divider.addEventListener("pointermove", onPointerMove);
  els.divider.addEventListener("pointerup", onPointerUp);
  els.divider.addEventListener("pointercancel", onPointerUp);
  els.divider.addEventListener("keydown", onKeyDown);

  // --- Toggles ---

  /** Collapsing the last visible half would leave an empty window. */
  const toggle = (side: "left" | "right") => (): void => {
    const other = side === "left" ? "right" : "left";
    if (visible[side] && !visible[other]) return;
    visible[side] = !visible[side];
    apply();
  };

  const onToggleLeft = toggle("left");
  const onToggleRight = toggle("right");
  els.toggleLeft.addEventListener("click", onToggleLeft);
  els.toggleRight.addEventListener("click", onToggleRight);

  apply();

  return {
    cleanup: () => {
      els.divider.removeEventListener("pointerdown", onPointerDown);
      els.divider.removeEventListener("pointermove", onPointerMove);
      els.divider.removeEventListener("pointerup", onPointerUp);
      els.divider.removeEventListener("pointercancel", onPointerUp);
      els.divider.removeEventListener("keydown", onKeyDown);
      els.toggleLeft.removeEventListener("click", onToggleLeft);
      els.toggleRight.removeEventListener("click", onToggleRight);
    },
  };
};
