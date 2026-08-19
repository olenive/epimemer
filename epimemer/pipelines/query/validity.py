"""Reading validity back at retrieval — per source, and bucketed on request.

T1 §3's read surface: *a query answers with `(source, interval)` pairs*. The
intervals were stored on the `sourced_from` edge (#53 step 3) precisely so a
period is always attributable, and this is where that attribution reaches a
caller. Nothing is collapsed on the way out — union takes one careful source and
one sloppy one and yields a period neither claims, intersection turns two
episodes into "never", and nothing in the data says which case is in front of
you.

`verdict_for` is a collapse, and it is one the caller asked for by naming a
moment. It sits beside the per-source pairs rather than replacing them, which is
the condition §3 sets for any collapse at all.
"""

from collections.abc import Sequence
from datetime import datetime

from pydantic import BaseModel, Field

from epimemer.core.temporal import (
    ValidityInterval,
    ValidityVerdict,
    merged_validity,
    validity_at,
)
from epimemer.core.types import EdgeType
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

    by_node: dict[str, list[SourceValidity]] = {}
    for node_id, node_edges in edges.items():
        by_source: dict[str, list[ValidityInterval]] = {}
        for edge in node_edges:
            if not edge.validity:
                continue
            by_source[edge.dst_id] = merged_validity(
                by_source.get(edge.dst_id, []), edge.validity
            )
        if by_source:
            by_node[node_id] = [
                SourceValidity(source_id=source_id, intervals=intervals)
                for source_id, intervals in by_source.items()
            ]
    return by_node


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
