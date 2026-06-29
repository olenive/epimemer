"""Relation-label consolidation for the reflection layer.

User-tier relationship labels are open vocabulary, so synonyms accumulate
(`authored_by` / `written_by`). This proposes merges by embedding similarity over
the labels, scoped to the same behavioural kind (never merge a `relationship`
label into an `attribution` one). Applied via apply_reflection relation_merges,
which relabels the edges in place (edges are not versioned).
"""

import math
from collections import defaultdict

from epimemer.core.types import EdgeType
from epimemer.embeddings.protocol import EmbeddingProvider
from epimemer.storage.protocol import StorageBackend


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
    for node in await storage.query_nodes():
        edges = list(await storage.get_edges_from(node.id)) + list(
            await storage.get_edges_to(node.id)
        )
        for e in edges:
            if e.type == EdgeType.RELATED and e.label and e.id not in seen_edges:
                seen_edges.add(e.id)
                counts[(e.label, e.kind)] = counts.get((e.label, e.kind), 0) + 1

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
