"""*Reviewed, and it stands* — the keep verdict, and what it covers.

`reflect` nominates single nodes for archival the way it nominates pairs for
merging, and the pair half already had a writer for the negative answer: an
`assessed` edge says *somebody judged this and the answer was no action*, and
every sweep reads it. The single-node half had none. An agent that re-read a
flagged inference and concluded it still holds had nowhere to put that, so the
node came back on the next reflect and the one after, to an agent who could not
see the work had been done.

**The verdict is a `retention` row in the decision journal, and nothing else.**
Its `covers` field holds the reasons it answers, so the row a reviewer reads and
the index a nominator reads are one object rather than two that can drift.

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

So a confirmation carries the reasons it covers, and a nomination survives it
when the node's current reasons are not all covered:

| Nomination | Anchored to |
|---|---|
| `evidence_stale` | the changed facts named in the label |
| `evidence_merged` | the absorbed phrasings named in the label |
| `never_retrieved` | nothing: `covers` is empty |

`evidence_merged` joined the table on 2026-09-05. It had been the one label
with no writer: the docstrings said *re-read it*, and an agent that did had
nowhere to say so, so the treadmill this module was written to end came back
one label over. A re-read is a keep, anchored to the wording that went away,
and a later absorption is a reason nobody covered, exactly as a later
supersession is.

**Archival still never reads `evidence_merged`**, and the two definitions below
are the record of that: `outstanding_reasons` is what a re-read must cover, and
`archival_reasons` is the narrower set the archival nominator proposes on. A
merge gives a premise provenance rather than taking its basis away, so a
nominator that read the label would have every merge propose discarding its own
dependents.

**This writer raises where every other journal write swallows its errors, and
the difference is what the row is.** Elsewhere the row records a graph write
that already happened, so losing it costs the journal an entry and nothing else.
Here the row *is* the act: a keep that failed to store and was reported as a
success would put the node back on every reflect, in front of an agent told the
work was done. So the exception propagates, and the caller reports the node as
skipped.

**Nothing here retires, archives, or moves a value.** A retention says a node
was looked at. That is all it says, and keeping it to that is what stops it
becoming the next field with two meanings.
"""

from collections.abc import Iterable, Sequence

from epimemer.core.types import DecisionKind, DecisionRecord, JudgeRef
from epimemer.storage.protocol import StorageBackend


class UnknownAnchors(Exception):
    """A verdict named reasons that are not nodes in this graph.

    Raised rather than returned because every caller has to stop: writing the
    row anyway produces a keep that covers nothing, which is worse than not
    writing it at all.
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
    by `evidence_merged`, and, where the whole evidence set has been archived,
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
    kind is part of the query rather than a filter over every row each node has,
    and a nominator walks its entire candidate population.

    A node with no retention is absent rather than mapped to an empty set, so a
    caller can filter by membership — *nobody has confirmed this* and *somebody
    confirmed it against nothing* are different answers, and only the second is
    a keep for the node's own sake.

    A row with empty `covers` is that second answer, and it is read back as the
    node's own id: the pure predicates below then compare like with like, and
    `retention_covers` needs no branch for the shape with no reasons.
    """
    ids = list(node_ids)
    if not ids:
        return {}
    wanted = set(ids)
    rows = await storage.query_decisions(kinds=[DecisionKind.RETENTION], subject_ids=ids)
    covered: dict[str, set[str]] = {}
    for row in rows:
        for node_id in row.subject_ids:
            if node_id not in wanted:
                continue
            covered.setdefault(node_id, set()).update(row.covers or [node_id])
    return covered


def retention_covers(node_id: str, reasons: Iterable[str], covered: dict[str, set[str]]) -> bool:
    """Whether a standing retention answers every reason this node has *now*.

    Pure, and separate from the read for the reason the pair suppression keeps
    its own predicate: the rule *a new reason outranks an old verdict* is the
    whole design, and it belongs somewhere a test can state it without a store.

    An empty `reasons` is the `never_retrieved` shape: the nomination names none,
    so the node's own id is the reason, and a verdict that covered nothing else
    covers it.
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
    because: str,
    reasons: Sequence[str],
    judge: JudgeRef | None = None,
) -> list[str]:
    """Write the keep verdict for one node. Returns the anchors it now covers.

    One `retention` row, append-only and never edited, whose `covers` field is
    the anchors. `because` is the agent's prose and goes in `certainty_basis`,
    which is where a reviewer reads why the node was kept.

    `reasons` empty means the node was kept for its own sake, and the row's
    `covers` is then empty: the node's own id is never written into it, because
    a verdict covering a node with itself is a claim about the node rather than
    about a question. The caller decides which case this is, rather than this
    function inferring it from the node type, because *which reasons a
    nomination named* is knowledge the nominator has and the store does not.

    **An anchor that names nothing is refused rather than written.** A typo'd id
    writes a verdict that permanently fails to cover anything, and the node then
    comes back on every reflect while the call that was supposed to keep it
    reported success — the failure this verdict exists to end, reintroduced
    through its own write path.

    **A failed write raises**, unlike every other journal write in this
    codebase: the module docstring has the argument, and it comes down to the
    row being the act rather than a note about one.
    """
    anchors = list(dict.fromkeys(reasons)) or [node_id]
    covers = [] if anchors == [node_id] else anchors
    if covers:
        found = await storage.get_nodes(covers)
        missing = [anchor for anchor in covers if anchor not in found]
        if missing:
            raise UnknownAnchors(node_id=node_id, missing=missing)
    await storage.record_decision(
        DecisionRecord(
            kind=DecisionKind.RETENTION,
            subject_ids=[node_id],
            covers=covers,
            judged_by=judge,
            certainty_basis=because,
        )
    )
    return anchors
