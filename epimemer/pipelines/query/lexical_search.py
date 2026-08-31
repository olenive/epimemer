"""Lexical search for the query layer.

BM25 over node content and over segment text, producing *ranked id lists* rather
than one merged scored list. That shape is forced rather than chosen: full-text
indexes are per table, so IDF's N counts one node type, and a score from the
fact table means nothing next to a score from the topic table
(`dev-docs/LEXICAL_SEARCH.md` §10, R5). Ranks are the only currency that crosses
a table boundary, and rank fusion is what consumes them.

Segments are the half that closes the stated gap. `store_decomposition` is
agent-driven, so a fact written as "the deployment ticket was closed" no longer
contains the identifier the source text did — and no search of any kind recovers
it from nodes. The segment kept the raw text, and a node's `source_id` is its
segment, so a segment hit bridges back to what was concluded from that passage
(§1.1).
"""

from collections.abc import Sequence

from pydantic import BaseModel, Field

from epimemer.core.types import Fact, Inference, NodeStatus, NodeType, Topic
from epimemer.pipelines.query.fusion import rrf_scores
from epimemer.pipelines.query.types import SegmentHit
from epimemer.storage.bm25 import analyze
from epimemer.storage.protocol import StorageBackend

_NODE_TYPE_TO_CLASS = {
    NodeType.TOPIC: Topic,
    NodeType.FACT: Fact,
    NodeType.INFERENCE: Inference,
}


class LexicalHits(BaseModel):
    """Everything the lexical arm found, with each part named.

    The parts were briefly a positional tuple whose last element was "the
    bridged ranking, if there was one" — and the caller separated direct hits
    from bridged ones by slicing it off. That is the shape of bug this project
    keeps finding: one rule (which list is which) stated here and re-derived,
    differently, at the only place that reads it. When no segment matched there
    was no last element to slice, and a whole node type's hits were relabelled
    as similarity results. Named fields cannot be counted wrong.
    """

    # One ranked node-id list per node type, plus one for the nodes reached
    # through segment hits. Scores are deliberately absent: they are not
    # comparable between lists, and ranks are all that may cross a table
    # boundary.
    rankings: list[list[str]] = Field(default_factory=list)
    # Node ids a declared term's best hit puts beyond the top-k cut (R2). Empty
    # on the fallback path, which offers no such guarantee.
    protected: list[str] = Field(default_factory=list)
    # The passages that matched, reported in their own right whether or not
    # anything was extracted from them.
    segments: list[SegmentHit] = Field(default_factory=list)
    # Nodes an exact term matched in their own content...
    direct_ids: list[str] = Field(default_factory=list)
    # ...and nodes reached only through the passage they came from.
    bridged_ids: list[str] = Field(default_factory=list)


def query_terms(query_text: str) -> list[str]:
    """The lexical arm's fallback terms: the query's own tokens (R3).

    No invented threshold and no guess about which token matters — the IDF floor
    already answers that, and answers it with the corpus rather than with a
    heuristic. A rare token fires; a common one scores zero and is dropped
    before fusion ever sees it.

    Punctuation-only tokens are left out. They are harmless in the *index*,
    where they appear in nearly every document and clamp to zero, but as
    standalone query terms in a small corpus one could be rare enough to score —
    and "documents containing a hyphen" is not a search anybody asked for.

    Known residue, stated in R3 and repeated here because it is the cost of not
    declaring: a mid-frequency term in a long prose query survives the floor,
    and several of them can out-vote a single rare one under the OR-sum.
    """
    return [
        token for token in analyze(query_text) if any(character.isalnum() for character in token)
    ]


def _merged(per_term: Sequence[Sequence[tuple[str, float]]]) -> list[tuple[str, float]]:
    """One ranked hit list from several per-term ones, merged by rank.

    Scores used to do this. BM25 over several terms is the sum of the per-term
    scores for the same document — verified on both engines — so adding them
    reproduced the ranking of a single combined query while keeping each term's
    own best hit visible, which is what R2 needs and a combined call cannot say.

    R8 ends that. A declared term's list is ordered by containment first and
    score second, so its order is no longer a function of the numbers in it, and
    re-sorting on the sum would send a floored exact match — the hit containment
    just rescued — straight back to the bottom. Rank is what survives crossing
    that boundary, and rank fusion is the merge this module already trusts
    everywhere else. Each score is still reported as the sum, which is what it
    is; it decides nothing here.
    """
    totals: dict[str, float] = {}
    for hits in per_term:
        for item_id, score in hits:
            totals[item_id] = totals.get(item_id, 0.0) + score
    fused = rrf_scores([[item_id for item_id, _ in hits] for hits in per_term])
    return [(item_id, totals[item_id]) for item_id in sorted(fused, key=lambda i: (-fused[i], i))]


async def _ranked(
    storage: StorageBackend,
    terms: Sequence[str],
    *,
    corpus: str,
    node_type: NodeType | None,
    k: int,
    statuses: frozenset[NodeStatus],
    declared: bool,
) -> tuple[list[tuple[str, float]], list[str]]:
    """One partition's ranked hits, and the ids its declared terms protect.

    The fallback path asks for all the terms at once — cheaper, and it owes no
    protection. The declared path asks per term, because knowing *which* term
    found a document is the whole of what R2 needs.

    Only declared terms are checked for containment (R8). A fallback term is a
    token the query happened to contain, not a string the caller vouched for,
    and "the document contains this word literally" is no evidence at all when
    the word was cut out of a sentence by the analyzer.

    Each declared term protects its own best hit **in this partition** — its top
    containing hit where there is one, which is why the protected id is read off
    the front of a list the backend has already ordered that way. There is no
    cross-table score with which to pick one winner across partitions, so the
    design does not pretend to: the bound is one node per term per list, which
    is small and stays small.
    """
    if not declared:
        hits = await storage.text_search(
            terms, corpus=corpus, node_type=node_type, k=k, statuses=statuses
        )
        return list(hits), []

    per_term = [
        list(
            await storage.text_search(
                [term],
                corpus=corpus,
                node_type=node_type,
                k=k,
                statuses=statuses,
                verify_containment=True,
            )
        )
        for term in terms
    ]
    return _merged(per_term), [hits[0][0] for hits in per_term if hits]


async def lexical_search(
    storage: StorageBackend,
    terms: Sequence[str],
    *,
    k: int = 10,
    node_types: Sequence[NodeType] | None = None,
    declared: bool = False,
    statuses: frozenset[NodeStatus] = frozenset({NodeStatus.ACTIVE}),
) -> LexicalHits:
    """Everything BM25 found, over node content and over segment text.

    See `LexicalHits` for what each part is.

    `statuses` gates both routes. Applying it to direct seeds and not to bridged
    ones would make the bridge a side door around the gate, and a CORRECTED node
    — a claim concluded *wrong* — would come back through it (R7).
    """
    if not terms:
        return LexicalHits()

    types = list(node_types) if node_types else list(NodeType)
    rankings: list[list[str]] = []
    protected: list[str] = []

    for node_type in types:
        hits, guarded = await _ranked(
            storage,
            terms,
            corpus="nodes",
            node_type=node_type,
            k=k,
            statuses=statuses,
            declared=declared,
        )
        rankings.append([node_id for node_id, _ in hits])
        protected.extend(guarded)

    direct_ids = [node_id for ranking in rankings for node_id in ranking]

    segment_hits, guarded_segments = await _ranked(
        storage,
        terms,
        corpus="segments",
        node_type=None,
        k=k,
        statuses=statuses,
        declared=declared,
    )
    if not segment_hits:
        return LexicalHits(rankings=rankings, protected=protected, direct_ids=direct_ids)

    segment_ids = [segment_id for segment_id, _ in segment_hits]
    bodies = await storage.get_segments(segment_ids)
    extracted = await storage.get_nodes_by_source(segment_ids)
    wanted = tuple(_NODE_TYPE_TO_CLASS[node_type] for node_type in types)

    bridged = {
        segment_id: [
            node.id
            for node in extracted[segment_id]
            if node.status in statuses and isinstance(node, wanted)
        ]
        for segment_id in segment_ids
    }
    bridged_ids = [node_id for segment_id in segment_ids for node_id in bridged[segment_id]]
    rankings.append(bridged_ids)
    protected.extend(
        bridged[segment_id][0] for segment_id in guarded_segments if bridged.get(segment_id)
    )

    return LexicalHits(
        rankings=rankings,
        protected=protected,
        segments=[
            SegmentHit(
                segment_id=segment_id,
                text=bodies[segment_id].text,
                document_id=bodies[segment_id].source_id,
                score=score,
            )
            for segment_id, score in segment_hits
            if segment_id in bodies
        ],
        direct_ids=direct_ids,
        bridged_ids=bridged_ids,
    )
