"""*Reviewed, and it stands* — the keep verdict, and what it covers.

`reflect` nominates single nodes for archival the way it nominates pairs for
merging, and the pair half already had a writer for the negative answer: an
`assessed` edge says *somebody judged this and the answer was no action*, and
every sweep reads it. The single-node half had none. An agent that re-read a
flagged inference and concluded it still holds had nowhere to put that, so the
node came back on the next reflect and the one after, to an agent who could not
see the work had been done.

**The workaround this replaces is the reason it is a separate verdict.** The
only way to keep a `never_retrieved` node was to raise its `importance` above
the nomination ceiling — which made one field carry two meanings, *how
consequential this is* and *do not nominate this*. Eight nodes on this project's
own graph were judged upward for no reason but silence. A ranker, a triviality
judgment and a person reading the node all then see a consequence signal the
judge never held.

**The verdict is anchored, not permanent, and that is the difference from the
pair case.** A judged pair's wording is fixed at the moment of judgment, so
suppressing it forever is sound. A node's *neighbourhood* keeps moving: the
premise superseded last week may be superseded again next month by something
new, and a keep verdict that silenced the second change as well as the first
would be a worse defect than the treadmill it replaced.

So a confirmation carries the reasons it covers, one edge each, and a nomination
survives it when the node's current reasons are not all covered:

| Nomination | Anchored to |
|---|---|
| `evidence_stale` | the changed facts named in the label, one edge each |
| `evidence_merged` | the absorbed phrasings named in the label, one edge each |
| `never_retrieved` | the node itself — the nomination names no reason |

`evidence_merged` joined the table on 2026-09-03. It had been the one label
with no writer: the docstrings said *re-read it*, and an agent that did had
nowhere to say so, which put twelve inferences on this project's own graph back
on every reflect for a week — the treadmill this module was written to end,
reintroduced one label over. A re-read is a keep, anchored to the wording that
went away, and a later absorption is a reason nobody covered, exactly as a
later supersession is.

**Archival still never reads `evidence_merged`**, and the two definitions below
are the record of that: `outstanding_reasons` is what a re-read must cover, and
`archival_reasons` is the narrower set the archival nominator proposes on. A
merge gives a premise provenance rather than taking its basis away, so a
nominator that read the label would have every merge propose discarding its own
dependents.

The self-anchor is the degenerate case rather than a second mechanism. Nothing
about *nothing links to this and nothing retrieved it* can change without
removing the node from the set anyway, so there is no later reason for a
confirmation to fail to cover.

**Nothing here retires, archives, or moves a value.** A retention says a node
was looked at. That is all it says, and keeping it to that is what stops it
becoming the next field with two meanings.
"""

from collections.abc import Iterable, Sequence

from epimemer.core.types import EdgeType, JudgeRef, NodeEdge
from epimemer.storage.protocol import StorageBackend


class UnknownAnchors(Exception):
    """A verdict named reasons that are not nodes in this graph.

    Raised rather than returned because every caller has to stop: writing the
    edges anyway produces a keep that covers nothing, which is worse than not
    writing them at all.
    """

    def __init__(self, *, node_id: str, missing: Sequence[str]) -> None:
        self.node_id = node_id
        self.missing = list(missing)
        super().__init__(
            f"{', '.join(self.missing)} names no node here, so an anchor on it "
            f"would cover nothing and {node_id} would be nominated again"
        )


def outstanding_reasons(
    labels: dict[str, list[str]], archived_evidence: Sequence[str]
) -> list[str]:
    """Every reason this node carries, as ids a keep verdict can cover.

    **One definition, read by the worklist and by the tool that refuses an
    uncovered verdict.** Two definitions is how the last defect in this area
    happened: a verdict was written against one notion of *the reasons* and read
    against another, and the call reported success either way.

    The union of the three paths that put an inference in front of a reviewer:
    the facts named by the `evidence_stale` label, the absorbed phrasings named
    by `evidence_merged`, and — where the whole evidence set has been archived —
    the archived facts themselves. Deduplicated and ordered, because it is
    compared as a set but shown to a person as a list.

    Every reason the node *carries*, not every reason still open: subtract what
    a standing retention covers with `uncovered_reasons` before asking a caller
    to name them.
    """
    return list(
        dict.fromkeys(
            [
                *labels.get("evidence_stale", ()),
                *labels.get("evidence_merged", ()),
                *archived_evidence,
            ]
        )
    )


def archival_reasons(labels: dict[str, list[str]], archived_evidence: Sequence[str]) -> list[str]:
    """The reasons the archival nominator proposes on: `outstanding_reasons`
    without `evidence_merged`.

    A separate name rather than a flag, so the exclusion reads as a decision at
    the call site. A premise that absorbed another claim gained provenance
    rather than losing its basis; nominating on it would have every merge
    propose discarding its own dependents.
    """
    return list(dict.fromkeys([*labels.get("evidence_stale", ()), *archived_evidence]))


def uncovered_reasons(
    node_id: str, reasons: Iterable[str], covered: dict[str, set[str]]
) -> list[str]:
    """The reasons still open on this node: `reasons` less what a standing
    retention already anchors to.

    What the worklist shows and what a keep must cover are the same set, and
    this is where it is computed. Measuring a verdict against every reason the
    node carries instead asked a caller to re-name a premise somebody had
    already re-read, and complying wrote a second anchor that said nothing new.
    """
    confirmed = covered.get(node_id, set())
    return [reason for reason in reasons if reason not in confirmed]


async def confirmed_reasons_for(
    node_ids: Sequence[str], storage: StorageBackend
) -> dict[str, set[str]]:
    """For each node, the reasons a retention already covers.

    One batched query for the whole set, on `already_judged_pairs`' terms: the
    edge type is part of the query rather than a filter over every edge each
    node has, and a nominator walks its entire candidate population.

    A node with no retention is absent rather than mapped to an empty set, so a
    caller can filter by membership — *nobody has confirmed this* and *somebody
    confirmed it against nothing* are different answers, and only the second is
    a self-anchor.
    """
    ids = list(node_ids)
    if not ids:
        return {}
    found = await storage.get_edges_for(ids, direction="to", edge_type=EdgeType.REVIEW_CONFIRMED)
    covered: dict[str, set[str]] = {}
    for node_id, edges in found.items():
        if edges:
            covered[node_id] = {edge.src_id for edge in edges}
    return covered


def retention_covers(node_id: str, reasons: Iterable[str], covered: dict[str, set[str]]) -> bool:
    """Whether a standing retention answers every reason this node has *now*.

    Pure, and separate from the read for the reason the pair suppression keeps
    its own predicate: the rule *a new reason outranks an old verdict* is the
    whole design, and it belongs somewhere a test can state it without a store.

    An empty `reasons` is the `never_retrieved` shape: the nomination names none,
    so the node's own id is the reason, and a self-anchored confirmation covers
    it.
    """
    confirmed = covered.get(node_id)
    if confirmed is None:
        return False
    wanted = set(reasons) or {node_id}
    return wanted <= confirmed


async def record_retention(
    storage: StorageBackend,
    *,
    node_id: str,
    reasons: Sequence[str],
    judge: JudgeRef | None = None,
) -> list[str]:
    """Write the keep verdict for one node. Returns the anchors it now covers.

    Append-only and immutable, as `assessed` is, which is what lets a nominator
    read these edges instead of querying the decision journal: an edge that is
    never edited cannot drift from the row that records the same act.

    `reasons` empty means the node is its own anchor. The caller decides that,
    rather than this function inferring it from the node type, because *which
    reasons a nomination named* is knowledge the nominator has and the store
    does not.

    **An anchor that names nothing is refused rather than written.** A typo'd id
    writes an edge that permanently fails to cover anything, and the node then
    comes back on every reflect while the call that was supposed to keep it
    reported success — the failure this verdict exists to end, reintroduced
    through its own write path.
    """
    anchors = list(dict.fromkeys(reasons)) or [node_id]
    if anchors != [node_id]:
        found = await storage.get_nodes(anchors)
        missing = [anchor for anchor in anchors if anchor not in found]
        if missing:
            raise UnknownAnchors(node_id=node_id, missing=missing)
    for anchor in anchors:
        await storage.store_edge(
            NodeEdge(
                src_id=anchor,
                dst_id=node_id,
                type=EdgeType.REVIEW_CONFIRMED,
                judged_by=judge,
            )
        )
    return anchors
