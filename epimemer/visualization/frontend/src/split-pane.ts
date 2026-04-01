/**
 * Resizable split pane with collapsible panels.
 *
 * Handles mouse drag on the resize handle and toggle buttons
 * for showing/hiding each panel.
 */

export interface SplitPaneElements {
  leftPanel: HTMLElement;
  rightPanel: HTMLElement;
  handle: HTMLElement;
  toggleLeft: HTMLElement;
  toggleRight: HTMLElement;
}

export const initSplitPane = (elements: SplitPaneElements): void => {
  const { leftPanel, rightPanel, handle, toggleLeft, toggleRight } = elements;

  let isDragging = false;
  let leftVisible = true;
  let rightVisible = true;

  const updatePanelVisibility = (): void => {
    leftPanel.style.display = leftVisible ? "flex" : "none";
    rightPanel.style.display = rightVisible ? "flex" : "none";
    handle.style.display = leftVisible && rightVisible ? "block" : "none";

    // Update toggle button active states
    toggleLeft.classList.toggle("bg-blue-900/50", leftVisible);
    toggleLeft.classList.toggle("text-blue-400", leftVisible);
    toggleRight.classList.toggle("bg-blue-900/50", rightVisible);
    toggleRight.classList.toggle("text-blue-400", rightVisible);

    // If only one panel visible, give it full width
    if (leftVisible && !rightVisible) {
      leftPanel.style.flex = "1 1 100%";
    } else if (!leftVisible && rightVisible) {
      rightPanel.style.flex = "1 1 100%";
    } else {
      leftPanel.style.flex = "";
      rightPanel.style.flex = "";
    }
  };

  // --- Drag resize ---

  handle.addEventListener("mousedown", (e) => {
    isDragging = true;
    e.preventDefault();
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  });

  document.addEventListener("mousemove", (e) => {
    if (!isDragging) return;

    const parent = leftPanel.parentElement;
    if (!parent) return;

    const parentRect = parent.getBoundingClientRect();
    const ratio = (e.clientX - parentRect.left) / parentRect.width;
    const clamped = Math.max(0.15, Math.min(0.85, ratio));

    leftPanel.style.flex = `0 0 ${clamped * 100}%`;
    rightPanel.style.flex = `1 1 0%`;
  });

  document.addEventListener("mouseup", () => {
    if (!isDragging) return;
    isDragging = false;
    document.body.style.cursor = "";
    document.body.style.userSelect = "";
  });

  // --- Toggle buttons ---

  toggleLeft.addEventListener("click", () => {
    leftVisible = !leftVisible;
    if (!leftVisible && !rightVisible) rightVisible = true;
    updatePanelVisibility();
  });

  toggleRight.addEventListener("click", () => {
    rightVisible = !rightVisible;
    if (!leftVisible && !rightVisible) leftVisible = true;
    updatePanelVisibility();
  });
};
