"""How many *independent* sources say this — derived at read time (#51).

`ValueSignal` promised two things and #46 split them. *"How well-supported by
evidence"* became the caller-supplied `confidence` prior; *"multiple independent
sources increase confidence"* is this module. The difference between them is
what the number is *about*: `confidence` describes the material, corroboration
describes the **graph**, so it changes whenever the graph changes.

**Never stored.** A stored corroboration count is the trap that removed
`novelty` — an answer frozen at the moment it was taken, against a baseline
nothing records. It is computed on the way out, beside the review labels and the
per-source validity, and it lives here rather than in `pipelines/reflection/`
for that reason: it is a property of a *result set*, not a nomination.

**In-degree is the wrong proxy** and is deliberately not reached for.
`knowledge_in_degree_for` already means something else — archival consumes it as
structural importance — and ten inferences drawn from one document raise it
tenfold while adding no support at all.

**The neighbourhood, not node identity.** One claim is usually many nodes, so
counting sources per node id would count each restatement's source once and
stop. The walk covers ``{node} ∪ {SIMILARITY neighbours}`` instead. Facts can be
deduplicated since #52, but only where an agent judged them one claim *and* the
merge cleared its gate — which is nothing ingested before 2026-08-21, since the
gate reads a `claim_kind` recorded at ingest. So the neighbourhood stays the
right walk, and merging simply moves a pair from this reading to the identity
one. Three reasons it was right not to wait: nothing is destroyed, a wrong
similarity edge overstates a number whose workings come back with it, and it
works today.

**No similarity edge is written anywhere, so today this walk always returns
`{node}`** (#64, measured 2026-08-21: zero on both real graphs). The reads here
are correct and the neighbourhood is the right design; it simply has no input
yet, because the "record `SIMILARITY` and keep both" action every refusal
recommends is reachable only through the generic `link` tool and is therefore
never taken. Two consequences worth knowing before changing anything here: every
count in production is currently the *identity* reading, and the cost measured
in `BENCHMARKS.md` was taken against edges assigned by a dial rather than
produced by judgment. **This is not a reason to collapse the walk** — that
migration was proposed under #52 and declined for exactly this reason, since
removing the neighbourhood would delete the only consumer of the judgment #64
exists to start recording.

**A claim about another period is not a second witness (#62).** "The city is
called Leningrad" (BBC, 1924–1991) and "the city is called Saint Petersburg"
(Reuters, 1991–) are near-identical sentences, so `reflect` pairs them — and
neither exclusion above catches it, correctly: they do not contradict, since the
city really was renamed, and the predecessor is `historical` rather than
`corrected`, which still corroborates by the rule above. So Reuters counted as a
second witness to a sentence it never wrote, and the number rose exactly where
the graph already knew better. `assertions_are_disjoint` settles it, and this
module now asks it before the neighbourhood is formed.

**Nothing is removed from the graph, and nothing is even rejected.** Both claims
are true of their own periods, both stay, and the succession between them is
already recorded three times over — the dates on each provenance edge, the
`temporally_followed_by` link, the predecessor's status. What narrows is one
integer computed on the way out. The look-alike itself comes back named, in
`adjacent_periods`: *someone has a claim about the next stretch of time* is real
information, and where the caller's search returned only one of the pair, this
response is the only place they would learn it.

**It can only fire where the dates provably clear each other.**
`assertions_are_disjoint` answers False for an undated side, for a half-dated
pair, for intervals on different clocks, and for any cross pair that compares
`unknown` (`temporal.py` §6). On today's corpora, where most nodes carry no
intervals, it will rarely fire — the correct amount, not a reason to widen it,
since a check that fired on ignorance would be measuring how diligently the
corpus was dated.

**It does not interact with `confidence`.** Three hedged 0.3 reports from three
publishers score 3, exactly as three 0.9s would. Independence is what is being
counted, not strength, and callers must be told so rather than left to discover
it.
"""

from collections.abc import Sequence

from pydantic import BaseModel, Field

from epimemer.core.temporal import ValidityInterval, assertions_are_disjoint
from epimemer.core.types import ATTRIBUTION_KIND, EdgeType, NodeEdge, NodeStatus
from epimemer.storage.protocol import StorageBackend

# The attribution edge `segment` writes for `published_by` (`tools.py`). Read
# here by the same label and kind it is written with, rather than by a second
# spelling that would drift from it.
PUBLISHED_BY_LABEL = "published_by"

# Edges that disqualify a similarity partner from counting as support.
#
# `contradiction` because contradicting pairs are near-maximally similar ("the
# deploy failed" / "the deploy succeeded") and the `similarity` edge written
# before the verdict stays in the graph — so without this a document that
# *contradicts* the claim counts as evidence for it.
#
# `variant_of` because a cross-frame variant is **that frame's** resolution of
# the proposition, not support for this one.
#
# `retracted_similarity` for the first reason exactly, one judgment later: an
# agent that withdraws a `one_claim` verdict leaves the `similarity` edge in
# place, because nothing in this system deletes, so without this the pair goes
# on corroborating after it has been disowned (#68). Reading it here is the
# whole of the retraction — the suppression the pair already had is untouched.
#
# Each member costs a round trip pair (out-edges and in-edges) on a path #51
# measured as the most expensive annotation in retrieval, which is why this list
# grows by argument rather than by convenience.
DISQUALIFYING_EDGE_TYPES: tuple[EdgeType, ...] = (
    EdgeType.CONTRADICTION,
    EdgeType.VARIANT_OF,
    EdgeType.RETRACTED_SIMILARITY,
)

# A neighbour retired as *wrong* is not evidence for anything: `corrected` means
# the claim should never have been believed, and the node is kept for the audit
# trail rather than for its content.
#
# `historical` is deliberately absent. That status means the world moved on —
# the claim was right and is still right of its period — and dropping it here
# would be the same forgetting #53 exists to prevent, one layer along.
DISQUALIFYING_STATUSES: frozenset[NodeStatus] = frozenset({NodeStatus.CORRECTED})


class CorroboratingSource(BaseModel):
    """One independent source, counted once, and what made it count.

    `publisher` is `None` when the documents carried no `published_by`, in which
    case the document stands as its own source. That fallback is honest but not
    free: whether a caller bothered to attribute is an *ingest habit*, and it
    shows up here as a corroboration difference. Naming it in the result is what
    lets a reader discount it.
    """

    source_id: str
    publisher: str | None = None
    document_ids: list[str] = Field(default_factory=list)
    node_ids: list[str] = Field(default_factory=list)


class AdjacentPeriod(BaseModel):
    """A look-alike left out of the count because it speaks to another period.

    Not a rejection. The claim is true of its own stretch of time and stays in
    the graph, linked and dated; it is simply not a *witness to this claim*, any
    more than a 2020 Reuters piece is a witness to what the city was called in
    1970.

    It is reported rather than dropped in silence for the reason the whole
    module is auditable: a count that shrank with no working shown is the one
    thing this design refuses to ship. And where the caller's search returned
    the subject but not this neighbour, these fields are the only place the
    adjacent claim appears at all.

    `publisher` is the first attributed document's, `None` where none of them
    named one — the same fallback `CorroboratingSource` makes, and `document_ids`
    is beside it so a multi-publisher node can be read rather than guessed at.
    """

    node_id: str
    publisher: str | None = None
    document_ids: list[str] = Field(default_factory=list)
    # The neighbour's own periods, not the comparison. What made it adjacent is
    # reproducible from these and the subject's, which `search` already returns.
    periods: list[ValidityInterval] = Field(default_factory=list)


class Corroboration(BaseModel):
    """The count, and everything needed to argue with it.

    The contributing nodes come back deliberately. This design knowingly counts
    over a similarity neighbourhood that will sometimes be wrong, and the whole
    defence of that choice is that an inflated figure stays *visible and
    checkable* — which a bare integer would not be.
    """

    count: int
    sources: list[CorroboratingSource] = Field(default_factory=list)
    # How many counted sources fell back to the document because it named no
    # publisher. Reported rather than hidden, per the entry.
    unattributed_documents: int = 0
    # Look-alikes whose stated periods fall clear of this claim's (#62). Empty
    # rather than absent, so a caller reads one shape for the ordinary case.
    adjacent_periods: list[AdjacentPeriod] = Field(default_factory=list)


async def _partners_by_node(
    node_ids: Sequence[str], storage: StorageBackend, edge_type: EdgeType
) -> dict[str, set[str]]:
    """Nodes joined to each id by `edge_type`, in either direction.

    Direction is not a fact about the epistemics — which way
    `record_contradiction` happened to write the edge does not change whether
    the two claims conflict — so both are read and unioned. Reading one only is
    how an exclusion works in tests and half the time in production.

    Self-loops drop out: a node is not its own corroborating neighbour, and it
    is added to its own set separately and unconditionally.
    """
    outgoing = await storage.get_edges_for(
        node_ids, direction="from", edge_type=edge_type
    )
    incoming = await storage.get_edges_for(
        node_ids, direction="to", edge_type=edge_type
    )
    return {
        node_id: {
            endpoint
            for edge in list(outgoing[node_id]) + list(incoming.get(node_id, []))
            for endpoint in (edge.src_id, edge.dst_id)
            if endpoint != node_id
        }
        for node_id in outgoing
    }


async def _supporters_by_node(
    node_ids: Sequence[str], storage: StorageBackend
) -> dict[str, set[str]]:
    """What points at each node by `supports` or `derived_from`.

    Both edge types record the same relation and are read together, as the
    temporal soundness check already reads them. One hop only: transitive
    support is more faithful and risks counting one document repeatedly along
    several paths, which needs the distinct-set semantics anyway and has not
    been asked for.
    """
    if not node_ids:
        return {}
    supports = await storage.get_edges_for(
        node_ids, direction="to", edge_type=EdgeType.SUPPORTS
    )
    derived = await storage.get_edges_for(
        node_ids, direction="to", edge_type=EdgeType.DERIVED_FROM
    )
    return {
        node_id: {
            edge.src_id
            for edge in list(supports[node_id]) + list(derived.get(node_id, []))
            if edge.src_id != node_id
        }
        for node_id in supports
    }


async def _source_edges_by_node(
    node_ids: Sequence[str], storage: StorageBackend
) -> dict[str, list[NodeEdge]]:
    """The `sourced_from` edges of each node — the document *and* the periods.

    One fetch with two readers. The count needs the document at the end of the
    edge; the disjointness rule needs the intervals riding on it (#53 T1). This
    used to take `dst_id` and discard the rest, which is the whole of #62: the
    dates that would have settled the Leningrad pair were being read and thrown
    away on the same line.
    """
    if not node_ids:
        return {}
    return await storage.get_edges_for(
        node_ids, direction="from", edge_type=EdgeType.SOURCED_FROM
    )


def _documents_from(edges: Sequence[NodeEdge]) -> list[str]:
    """The documents these notes point at, sorted for a stable answer."""
    return sorted({edge.dst_id for edge in edges})


def _intervals_from(edges: Sequence[NodeEdge]) -> list[ValidityInterval]:
    """Every period any source asserts of this node, on one list.

    The existential union `assertions_are_disjoint` asks for — the moments
    *some* source says the claim held. Flattening across sources is the one
    collapse T1 §3 permits, and only because its error direction is safe: a
    sloppy over-wide source can suppress the answer and never manufacture one.
    """
    return [interval for edge in edges for interval in edge.validity]


async def _publisher_by_document(
    document_ids: Sequence[str], storage: StorageBackend
) -> dict[str, tuple[str, str]]:
    """`{document_id: (entity_id, name)}` for documents that name a publisher.

    Two BBC articles are one source, not two, and independence is the whole
    content of the claim — so the publishing entity is what gets counted
    wherever there is one.

    **Publisher identity is name-brittle.** `published_by` entities are resolved
    by exact content match, so "BBC" and "BBC News" are two entities and this
    count inherits the over-split. That is reported in the result rather than
    corrected here, since the fix belongs to entity resolution.

    Documents with several `published_by` edges take the lowest entity id. That
    is arbitrary, and it is arbitrary over a case `segment` cannot produce —
    picking deterministically beats a per-backend edge ordering deciding it.
    """
    if not document_ids:
        return {}
    edges = await storage.get_edges_for(
        document_ids, direction="from", edge_type=EdgeType.RELATED
    )
    entity_by_document = {
        document_id: min(
            (
                edge.dst_id
                for edge in document_edges
                if edge.kind == ATTRIBUTION_KIND and edge.label == PUBLISHED_BY_LABEL
            ),
            default=None,
        )
        for document_id, document_edges in edges.items()
    }
    attributed = {
        document_id: entity_id
        for document_id, entity_id in entity_by_document.items()
        if entity_id is not None
    }
    if not attributed:
        return {}

    entities = await storage.get_nodes(sorted(set(attributed.values())))
    return {
        document_id: (entity_id, entities[entity_id].content)
        for document_id, entity_id in attributed.items()
        if entity_id in entities
    }


def _adjacent_periods(
    node_ids: set[str],
    documents: dict[str, list[str]],
    publishers: dict[str, tuple[str, str]],
    intervals: dict[str, list[ValidityInterval]],
) -> list[AdjacentPeriod]:
    """The uncounted look-alikes, each carrying the periods that uncounted it.

    Sorted by id for the same reason the sources are: one graph, one answer, on
    either backend.
    """
    return [
        AdjacentPeriod(
            node_id=node_id,
            publisher=next(
                (
                    publishers[document_id][1]
                    for document_id in documents.get(node_id, [])
                    if document_id in publishers
                ),
                None,
            ),
            document_ids=documents.get(node_id, []),
            periods=intervals.get(node_id, []),
        )
        for node_id in sorted(node_ids)
    ]


def _assemble(
    subject: str,
    contributing: set[str],
    documents: dict[str, list[str]],
    publishers: dict[str, tuple[str, str]],
    adjacent: list[AdjacentPeriod],
) -> Corroboration | None:
    """Fold the contributing nodes into distinct sources, or `None` for no source.

    The subject's own sources are listed first and the rest sorted by id, so the
    same graph gives the same answer on either backend and the entry a reader
    most expects to see leads.
    """
    names: dict[str, str | None] = {}
    documents_by_source: dict[str, set[str]] = {}
    nodes_by_source: dict[str, set[str]] = {}
    for node_id in sorted(contributing):
        for document_id in documents.get(node_id, []):
            entity = publishers.get(document_id)
            source_id = entity[0] if entity else document_id
            names[source_id] = entity[1] if entity else None
            documents_by_source.setdefault(source_id, set()).add(document_id)
            nodes_by_source.setdefault(source_id, set()).add(node_id)

    if not names:
        return None

    own = {
        (publishers[document_id][0] if document_id in publishers else document_id)
        for document_id in documents.get(subject, [])
    }
    ordered = sorted(names, key=lambda source_id: (source_id not in own, source_id))
    sources = [
        CorroboratingSource(
            source_id=source_id,
            publisher=names[source_id],
            document_ids=sorted(documents_by_source[source_id]),
            node_ids=sorted(nodes_by_source[source_id]),
        )
        for source_id in ordered
    ]
    return Corroboration(
        count=len(sources),
        sources=sources,
        unattributed_documents=sum(1 for s in sources if s.publisher is None),
        adjacent_periods=adjacent,
    )


async def corroboration_for(
    node_ids: Sequence[str], storage: StorageBackend
) -> dict[str, Corroboration]:
    """Corroboration for each node that has any, keyed by node id.

    Nodes with no provenance at all are absent rather than mapped to a zero —
    the shape `validity_for` and `review_labels_for` already use, so a response
    can carry the field only where it says something. A node that *does* have a
    source scores at least 1: a fact from one document is corroborated once, and
    0 would make the common case look like an error.

    The walk, in order: the similarity neighbourhood of each subject, less
    anything it contradicts or varies from; less anything whose stated periods
    fall clear of the subject's, which is a claim about another stretch of time
    rather than a witness to this one (#62); what supports the survivors; the
    documents all of those were sourced from; and the publishing entity of each
    document, or the document itself where it names none.

    Batched, because this rides on the hottest path in the system and asking per
    node is what made `gather_pending_review` the largest single source of
    round-trips in `reflect` (#14). Thirteen store calls — twelve where nothing
    supports the neighbourhood, since the provenance read is split in two so the
    periods arrive before they are needed and the second half asks only for what
    the supporter hop added. **Constant in the size of the result set** rather
    than per node either way. If that proves too many, the lever is collapsing
    the six typed neighbourhood queries into one untyped `get_edges_for` per
    direction — fewer round-trips against more bytes, which is a trade to make
    on a measurement rather than in advance.
    """
    subjects = list(dict.fromkeys(node_ids))
    if not subjects:
        return {}

    similar = await _partners_by_node(subjects, storage, EdgeType.SIMILARITY)
    disqualified_by_edge = [
        await _partners_by_node(subjects, storage, edge_type)
        for edge_type in DISQUALIFYING_EDGE_TYPES
    ]
    excluded = {
        subject: set().union(*(partners[subject] for partners in disqualified_by_edge))
        for subject in subjects
    }
    # #62, and it has to land *here* — before the supporter hop below. A
    # look-alike that survives to stage 2 walks its own supporters in behind it,
    # and their documents with them, so a comparison made later lets the same
    # publisher back through the side door by a different path.
    candidates = {
        subject: similar[subject] - excluded[subject] for subject in subjects
    }
    source_edges = await _source_edges_by_node(
        sorted(
            set(subjects)
            | {node for members in candidates.values() for node in members}
        ),
        storage,
    )
    intervals = {
        node_id: _intervals_from(edges) for node_id, edges in source_edges.items()
    }
    adjacent = {
        subject: {
            neighbour
            for neighbour in candidates[subject]
            if assertions_are_disjoint(
                intervals.get(subject, []), intervals.get(neighbour, [])
            )
        }
        for subject in subjects
    }
    neighbourhood = {
        subject: {subject} | (candidates[subject] - adjacent[subject])
        for subject in subjects
    }

    supporters = await _supporters_by_node(
        sorted({node for members in neighbourhood.values() for node in members}),
        storage,
    )
    contributing = {
        subject: (
            members | {node for member in members for node in supporters[member]}
        )
        - excluded[subject]
        - adjacent[subject]
        for subject, members in neighbourhood.items()
    }

    # A neighbour retired as wrong stops corroborating; the subject stays in its
    # own set either way, because a caller asking about a corrected node is
    # still owed the source that node came from.
    nodes = await storage.get_nodes(
        sorted({node for members in contributing.values() for node in members})
    )
    retired = {
        node_id
        for node_id, node in nodes.items()
        if node.status in DISQUALIFYING_STATUSES
    }
    contributing = {
        subject: (members - retired) | {subject}
        for subject, members in contributing.items()
    }

    # The early read already covers every subject and every look-alike, so this
    # asks only for what the supporter hop added — and for nothing at all where
    # it added nothing.
    source_edges |= await _source_edges_by_node(
        sorted(
            {node for members in contributing.values() for node in members}
            - set(source_edges)
        ),
        storage,
    )
    documents = {
        node_id: _documents_from(edges) for node_id, edges in source_edges.items()
    }
    publishers = await _publisher_by_document(
        sorted({document for reached in documents.values() for document in reached}),
        storage,
    )

    results = {
        subject: _assemble(
            subject,
            contributing[subject],
            documents,
            publishers,
            _adjacent_periods(adjacent[subject], documents, publishers, intervals),
        )
        for subject in subjects
    }
    return {
        subject: result for subject, result in results.items() if result is not None
    }
