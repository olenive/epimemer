"""Relation-label consolidation for the reflection layer.

User-tier relationship labels are open vocabulary, so synonyms accumulate
(`authored_by` / `written_by`). This proposes merges by embedding similarity over
the labels, scoped to the same behavioural kind (never merge a `relationship`
label into an `attribution` one). Applied via apply_reflection relation_merges,
which relabels the edges in place (edges are not versioned).
"""

import math
from collections import defaultdict

from epimemer.core.types import EdgeType, NodeEdge
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
    aggregate (ISSUES.md #14 step 2).

    De-duplicated by edge id, since an edge between two active nodes is found
    from both ends. Node order, then outgoing before incoming: label discovery
    order survives into the proposals, where it decides which of two
    equally-similar synonyms is `label_a`, so it is preserved rather than left
    to whatever order the two queries come back in.
    """
    node_ids = [node.id for node in await storage.query_nodes()]
    outgoing = await storage.get_edges_for(
        node_ids, direction="from", edge_type=EdgeType.RELATED
    )
    incoming = await storage.get_edges_for(
        node_ids, direction="to", edge_type=EdgeType.RELATED
    )

    by_id: dict[str, NodeEdge] = {}
    for node_id in node_ids:
        for edge in list(outgoing[node_id]) + list(incoming[node_id]):
            by_id.setdefault(edge.id, edge)
    return list(by_id.values())


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


async def find_similar_relation_pairs(
    storage: StorageBackend,
    embedding_provider: EmbeddingProvider,
    *,
    similarity_threshold: float = 0.9,
) -> list[dict]:
    """Find pairs of likely-synonymous user-tier relationship labels.

    Distinct labels are collected (with usage counts) by scanning the edges of
    active nodes, their strings embedded, and pairs within the same kind whose
    embeddings are at least `similarity_threshold` similar are returned, highest
    first — each as {label_a, label_b, kind, count_a, count_b, similarity}.
    """
    counts: dict[tuple[str, str], int] = {}
    seen_edges: set[str] = set()
    for edge in await related_edges_of_active_nodes(storage):
        if edge.label and edge.id not in seen_edges:
            seen_edges.add(edge.id)
            counts[(edge.label, edge.kind)] = counts.get((edge.label, edge.kind), 0) + 1

    if len(counts) < 2:
        return []

    keys = list(counts.keys())
    vectors = await embedding_provider.embed([label for (label, _) in keys])
    vec_by = dict(zip(keys, vectors))

    groups: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for key in keys:
        groups[key[1]].append(key)

    pairs: list[dict] = []
    for members in groups.values():
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                a, b = members[i], members[j]
                sim = _cosine_similarity(vec_by[a], vec_by[b])
                if sim >= similarity_threshold:
                    pairs.append({
                        "label_a": a[0],
                        "label_b": b[0],
                        "kind": a[1],
                        "count_a": counts[a],
                        "count_b": counts[b],
                        "similarity": round(sim, 4),
                    })

    pairs.sort(key=lambda p: p["similarity"], reverse=True)
    return pairs
