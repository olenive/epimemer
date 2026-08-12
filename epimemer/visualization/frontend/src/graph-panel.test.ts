// @vitest-environment jsdom
import { describe, expect, it } from "vitest";

import { statusOpacity } from "./graph-panel";

// The hazard this guards is the gap between a Python enum and a TypeScript
// lookup table: `NodeStatus` grew `corrected` and `historical` in 666904f and
// the table did not, so retired nodes drew at full opacity (#55). The fix is
// the *default*, not two more keys — the next status added must be safe
// without anyone remembering this file exists.
describe("statusOpacity", () => {
  it("draws active nodes at full opacity", () => {
    expect(statusOpacity("active")).toBe(1.0);
  });

  it("fades every status that has left the active set", () => {
    for (const status of [
      "superseded",
      "corrected",
      "historical",
      "merged",
      "archived",
    ]) {
      expect(statusOpacity(status)).toBeLessThan(1.0);
    }
  });

  it("fades a status it has never heard of rather than drawing it as live", () => {
    // The assertion that fails before the fix. A status this table does not
    // know is, by construction, one nobody has checked — and of the two ways
    // to be wrong, drawing a retired node as live is the harmful one.
    expect(statusOpacity("quarantined")).toBeLessThan(1.0);
    expect(statusOpacity("")).toBeLessThan(1.0);
  });
});
