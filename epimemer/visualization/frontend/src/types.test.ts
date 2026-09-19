/**
 * The wire shape of a validity interval, checked against a real snapshot edge.
 *
 * `types.ts` is the contract between `snapshot.py` and every panel, and nothing
 * draws validity yet, so a mistake here would sit unnoticed until the first
 * renderer is written against it. Two things are worth pinning now: that the
 * JSON the Python view emits assigns to `EdgeView` unchanged, and that reading
 * an endpoint's kind narrows to exactly the four shapes the model has.
 *
 * The narrowing is enforced by `tsc`, which `npm run build` runs over this
 * file: `describeEndpoint` below ends in a branch that only accepts `never`, so
 * a fifth kind, or a kind spelt wrongly, fails the build rather than a runtime
 * assertion.
 */

import { describe, expect, it } from "vitest";

import type { EdgeView, ImpreciseInstantView, ValidityIntervalView } from "./types";

/**
 * One `sourced_from` edge as `assemble_snapshot` serialises it: two disjoint
 * periods from one source, one dated and witnessed, one the source only named.
 * Written in the shape `ValidityInterval.model_dump(mode="json")` produces.
 */
const SNAPSHOT_EDGE = `{
  "edge_id": "edge-1",
  "src_id": "fact-1",
  "dst_id": "doc-1",
  "edge_type": "sourced_from",
  "weight": 1.0,
  "validity": [
    {
      "start": {"instant_kind": "precise", "at": "1924-01-22T00:00:00Z", "label": null},
      "end": {"instant_kind": "precise", "at": "1924-11-04T00:00:00Z", "label": null},
      "timeline_id": "westminster",
      "witnessed_at": {"instant_kind": "precise", "at": "1924-06-01T00:00:00Z", "label": null},
      "basis": "stated"
    },
    {
      "start": {"instant_kind": "named", "label": "the second Baldwin government"},
      "end": {"instant_kind": "unknown"},
      "timeline_id": null,
      "witnessed_at": null,
      "basis": "inferred"
    }
  ],
  "created_at": "2024-01-01T00:00:00Z",
  "graph": "default",
  "metadata": {}
}`;

const unreachable = (endpoint: never): never => {
  throw new Error(`an endpoint kind nothing handles: ${JSON.stringify(endpoint)}`);
};

/** Every endpoint kind in words, with a final branch only `never` reaches. */
const describeEndpoint = (endpoint: ImpreciseInstantView): string => {
  switch (endpoint.instant_kind) {
    case "precise":
      return endpoint.label === null ? endpoint.at : `${endpoint.at} (${endpoint.label})`;
    case "named":
      return endpoint.label;
    case "unknown":
      return "unknown";
    case "unbounded":
      return "unbounded";
    default:
      return unreachable(endpoint);
  }
};

describe("a sourced_from edge from the snapshot", () => {
  const edge: EdgeView = JSON.parse(SNAPSHOT_EDGE);

  it("keeps both periods the source asserts", () => {
    expect(edge.validity).toHaveLength(2);
    const [dated, named]: ValidityIntervalView[] = edge.validity;
    expect(dated.basis).toBe("stated");
    expect(dated.timeline_id).toBe("westminster");
    expect(named.basis).toBe("inferred");
    expect(named.timeline_id).toBeNull();
    expect(named.witnessed_at).toBeNull();
  });

  it("narrows each endpoint to the kind it declares", () => {
    const [dated, named] = edge.validity;
    expect(describeEndpoint(dated.start)).toBe("1924-01-22T00:00:00Z");
    expect(describeEndpoint(dated.end)).toBe("1924-11-04T00:00:00Z");
    expect(describeEndpoint(named.start)).toBe("the second Baldwin government");
    expect(describeEndpoint(named.end)).toBe("unknown");
  });

  it("carries a resolved endpoint's own words beside its date", () => {
    const founding: ImpreciseInstantView = {
      instant_kind: "precise",
      at: "1703-05-27T00:00:00Z",
      label: "its founding",
    };

    expect(describeEndpoint(founding)).toBe("1703-05-27T00:00:00Z (its founding)");
    expect(describeEndpoint({ instant_kind: "unbounded" })).toBe("unbounded");
  });

  it("survives a round trip through JSON unchanged", () => {
    expect(JSON.parse(JSON.stringify(edge))).toEqual(JSON.parse(SNAPSHOT_EDGE));
  });
});

describe("an edge with no source to assert anything", () => {
  it("carries an empty list rather than a missing key", () => {
    const tagged: EdgeView = {
      edge_id: "edge-2",
      src_id: "fact-1",
      dst_id: "topic-1",
      edge_type: "tagged_with_topic",
      weight: 1,
      validity: [],
      created_at: "2024-01-01T00:00:00Z",
      graph: "default",
      metadata: {},
    };

    expect(tagged.validity).toEqual([]);
  });
});
