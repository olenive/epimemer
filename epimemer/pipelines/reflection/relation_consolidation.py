"""Relation-label consolidation for the reflection layer.

User-tier relationship labels are open vocabulary, so synonyms accumulate
(`authored_by` / `written_by`). This nominates likely-synonymous pairs by
embedding similarity over the labels, scoped to the same behavioural kind (a
`relationship` label is never paired with an `attribution` one).

**Nomination is the whole of what this module does, and judgment is the whole
of what happens next.** `apply_reflection(relation_verdicts=[…])` records
`distinct` or `synonymous` against both label records and nothing is rewritten
— `relation_merges`, which relabelled edges in place, was removed on 2026-08-28
(`RELATION_LABELS.md` §5). A vocabulary converges here through
`describe_relation` rather than through collapse.

A pair already judged — `distinct` or `synonymous`, via
`apply_reflection(relation_verdicts=[…])` — is never nominated again.
That suppression is **permanent by design**, inherited from the fact-pair layer
in as many words rather than by accident, so a wrong `distinct` silences a pair
for good; `RELATION_LABELS.md` §4.2 states it beside its dual, and `ISSUES.md`
`ISSUES.md` is where a retraction would be argued.
"""

import math
from collections import defaultdict

from pydantic import BaseModel

from epimemer.core.types import EdgeType, NodeEdge, relation_pair_key
from epimemer.embeddings.protocol import EmbeddingProvider
from epimemer.storage.protocol import StorageBackend


async def related_edges_of_active_nodes(
    storage: StorageBackend,
) -> list[NodeEdge]:
    """Every user-tier (RELATED) edge with an active node at either end, once.

    The shared read behind label consolidation and `list_relations`, which
    otherwise each grew their own copy of it.

    Scoped to active nodes rather than read straight off the edge table: edges
    survive their endpoints being retired, so an unscoped `GROUP BY label` would
    count labels that only archived and superseded nodes still use — reporting a
    vocabulary the live graph no longer has, and proposing merges between
    labels nobody can reach. That scoping is why this is two queries and not one
    aggregate.

    De-duplicated by edge id, since an edge between two active nodes is found
    from both ends. Node order, then outgoing before incoming: label discovery
    order survives into the proposals, where it decides which of two
    equally-similar synonyms is `label_a`, so it is preserved rather than left
    to whatever order the two queries come back in.
    """
    node_ids = [node.id for node in await storage.query_nodes()]
    outgoing = await storage.get_edges_for(node_ids, direction="from", edge_type=EdgeType.RELATED)
    incoming = await storage.get_edges_for(node_ids, direction="to", edge_type=EdgeType.RELATED)

    by_id: dict[str, NodeEdge] = {}
    for node_id in node_ids:
        for edge in list(outgoing[node_id]) + list(incoming[node_id]):
            by_id.setdefault(edge.id, edge)
    return list(by_id.values())


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class RelationPairSweep(BaseModel):
    """One sweep's nominations, plus what standing verdicts held back.

    `suppressed` counts the judged pairs the sweep skipped — counted where the
    skip happens, before scoring, so it is the number of pairs a verdict took
    off the table rather than the number that would have cleared today's
    threshold. It exists because the suppression is silent by design: without
    it, an empty `pairs` on a well-judged graph is indistinguishable from a
    graph with nothing similar in it, and the agent reading the response
    cannot tell *settled* from *unexamined*.
    """

    pairs: list[dict]
    suppressed: int


async def sweep_similar_relation_pairs(
    storage: StorageBackend,
    embedding_provider: EmbeddingProvider,
    *,
    similarity_threshold: float = 0.9,
) -> RelationPairSweep:
    """Find pairs of likely-synonymous user-tier relationship labels.

    Distinct labels are collected (with usage counts) by scanning the edges of
    active nodes, their strings embedded, and pairs within the same kind whose
    embeddings are at least `similarity_threshold` similar are returned, highest
    first — each as {label_a, label_b, kind, count_a, count_b, similarity}.

    **Pairs an agent has already judged are dropped**. Without this
    the sweep re-derives from scratch every time and a declined pair comes back
    on every `reflect`, for ever, to a fresh agent who cannot see the previous
    refusals — while *accepting* a merge makes one label vanish and is therefore
    self-suppressing. The graph would apply quiet pressure toward the wrong
    answer. Both verdicts suppress; `apply_relation_verdict` writes them.

    **A pair either of whose sides has no record suppresses nothing**, and the
    direction is deliberate. Suppression is keyed on record ids, and a graph
    that predates stage 1 has labels with no records at all — so an unresolvable
    side means *nothing has been judged here yet*, never *the judgment cannot be
    found*. Failing the other way would silence pairs nobody has ever seen, on
    exactly the oldest graphs. It costs nothing in practice: recording a verdict
    creates both records first.
    """
    counts: dict[tuple[str, str], int] = {}
    seen_edges: set[str] = set()
    for edge in await related_edges_of_active_nodes(storage):
        if edge.label and edge.id not in seen_edges:
            seen_edges.add(edge.id)
            counts[(edge.label, edge.kind)] = counts.get((edge.label, edge.kind), 0) + 1

    if len(counts) < 2:
        return RelationPairSweep(pairs=[], suppressed=0)

    keys = list(counts.keys())
    vectors = await embedding_provider.embed([label for (label, _) in keys])
    vec_by = dict(zip(keys, vectors, strict=True))

    groups: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for key in keys:
        groups[key[1]].append(key)

    # One read for the whole sweep rather than a lookup per pair: the candidate
    # pairs are already in memory, and a per-pair query would put the cost on
    # the graph with the most labels — the one this exists for.
    judged = await storage.judged_relation_pairs()
    ids_by = {
        (record.name, record.kind): record.id for record in await storage.query_relation_labels()
    }

    pairs: list[dict] = []
    suppressed = 0
    for members in groups.values():
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                a, b = members[i], members[j]
                id_a, id_b = ids_by.get(a), ids_by.get(b)
                if id_a and id_b and relation_pair_key(id_a, id_b) in judged:
                    suppressed += 1
                    continue
                sim = _cosine_similarity(vec_by[a], vec_by[b])
                if sim >= similarity_threshold:
                    pairs.append(
                        {
                            "label_a": a[0],
                            "label_b": b[0],
                            "kind": a[1],
                            "count_a": counts[a],
                            "count_b": counts[b],
                            "similarity": round(sim, 4),
                        }
                    )

    pairs.sort(key=lambda p: p["similarity"], reverse=True)
    return RelationPairSweep(pairs=pairs, suppressed=suppressed)
