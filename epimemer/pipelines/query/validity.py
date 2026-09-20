"""Reading validity back at retrieval — per source, and bucketed on request.

T1 §3's read surface: *a query answers with `(source, interval)` pairs*. The
intervals were stored on the `sourced_from` edge precisely so a
period is always attributable, and this is where that attribution reaches a
caller. Nothing is collapsed on the way out — union takes one careful source and
one sloppy one and yields a period neither claims, intersection turns two
episodes into "never", and nothing in the data says which case is in front of
you.

`verdict_for` is a collapse, and it is one the caller asked for by naming a
moment. It sits beside the per-source pairs rather than replacing them, which is
the condition §3 sets for any collapse at all.
"""

from collections.abc import Iterable, Sequence
from datetime import datetime

from pydantic import BaseModel, Field

from epimemer.core.temporal import (
    ValidityInterval,
    ValidityVerdict,
    merged_validity,
    validity_at,
)
from epimemer.core.types import EdgeType, NodeEdge
from epimemer.storage.protocol import StorageBackend


class SourceValidity(BaseModel):
    """What one source says about when one claim was true."""

    source_id: str
    intervals: list[ValidityInterval] = Field(default_factory=list)


async def validity_for(
    node_ids: Sequence[str], storage: StorageBackend
) -> dict[str, list[SourceValidity]]:
    """Per-source validity for each node that has any, keyed by node id.

    One batched edge query for the whole set. Nodes with no intervals are absent
    rather than mapped to an empty list, so a caller can filter by membership —
    the same shape `review_labels_for` uses, and the reason a search response can
    carry the field only where it says something.

    Two `sourced_from` edges to one document collapse into a single entry
    through `merged_validity`: that is one source asserting several periods,
    which is what the list was always for, and is not the cross-source union §3
    forbids.
    """
    if not node_ids:
        return {}

    edges = await storage.get_edges_for(
        list(node_ids), direction="from", edge_type=EdgeType.SOURCED_FROM
    )
    return validity_from_edges(edge for node_edges in edges.values() for edge in node_edges)


def validity_from_edges(edges: Iterable[NodeEdge]) -> dict[str, list[SourceValidity]]:
    """The same answer as `validity_for`, over edges a caller already holds.

    The grouping rule is here so it is written once. A visualization snapshot
    has already read every live edge in the graph to draw it, and re-reading
    them through storage would describe a second instant: the strips and the
    boundary proposals beside them have to come from one set of edges.

    Anything that is not a `sourced_from` edge is skipped, so the whole edge
    list can be handed over as it stands.
    """
    by_node: dict[str, dict[str, list[ValidityInterval]]] = {}
    for edge in edges:
        if edge.type is not EdgeType.SOURCED_FROM or not edge.validity:
            continue
        by_source = by_node.setdefault(edge.src_id, {})
        by_source[edge.dst_id] = merged_validity(by_source.get(edge.dst_id, []), edge.validity)
    return {
        node_id: [
            SourceValidity(source_id=source_id, intervals=intervals)
            for source_id, intervals in by_source.items()
        ]
        for node_id, by_source in by_node.items()
    }


def verdict_for(
    sources: Sequence[SourceValidity],
    moment: datetime,
    *,
    timeline_id: str | None = None,
) -> ValidityVerdict:
    """Whether any source puts `moment` inside a period it asserts.

    The bucket T3 asks retrieval to answer with, computed by `validity_at` so
    the rule lives in one place beside the comparison it is a special case of.
    """
    return validity_at(
        [interval for source in sources for interval in source.intervals],
        moment,
        timeline_id=timeline_id,
    )
