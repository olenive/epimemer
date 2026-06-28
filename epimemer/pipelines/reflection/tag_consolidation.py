"""Tag consolidation for the reflection layer.

Arbitrary tags are captured freely at ingest with no controlled vocabulary, so
synonyms accumulate (e.g. "billing" / "billings" / "invoicing"). This module
proposes merges of synonymous tags by embedding similarity over their values —
the "organize slow" counterpart to free-text capture. Candidates are surfaced by
reflect and applied via apply_reflection (tag_merges), which rewrites the tags on
affected nodes in place.

Comparison is scoped to tags that share the same key (bare, keyless tags form
their own group): a `speaker` tag and an `author` tag are different dimensions
even if their values look alike.
"""

import math
from collections import defaultdict

from epimemer.embeddings.protocol import EmbeddingProvider
from epimemer.storage.protocol import StorageBackend


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _tag_str(key: str | None, value: str) -> str:
    return f"{key}={value}" if key else value


async def find_similar_tag_pairs(
    storage: StorageBackend,
    embedding_provider: EmbeddingProvider,
    *,
    similarity_threshold: float = 0.9,
) -> list[dict]:
    """Find pairs of likely-synonymous tags among active nodes.

    Distinct tags are collected (with usage counts), grouped by key, and their
    values embedded; pairs within a key group whose value embeddings are at least
    `similarity_threshold` similar are returned, highest similarity first. Each
    pair is {tag_a, tag_b, count_a, count_b, similarity}, where tag_a/tag_b are
    display strings ("key=value" or a bare "value") suitable for apply_reflection
    tag_merges.
    """
    nodes = await storage.query_nodes()  # active nodes

    counts: dict[tuple[str | None, str], int] = {}
    for node in nodes:
        for t in node.tags:
            counts[(t.key, t.value)] = counts.get((t.key, t.value), 0) + 1

    if len(counts) < 2:
        return []

    keys = list(counts.keys())
    vectors = await embedding_provider.embed([value for (_, value) in keys])
    vec_by = dict(zip(keys, vectors))

    groups: dict[str | None, list[tuple[str | None, str]]] = defaultdict(list)
    for key in keys:
        groups[key[0]].append(key)

    pairs: list[dict] = []
    for members in groups.values():
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                a, b = members[i], members[j]
                sim = _cosine_similarity(vec_by[a], vec_by[b])
                if sim >= similarity_threshold:
                    pairs.append({
                        "tag_a": _tag_str(*a),
                        "tag_b": _tag_str(*b),
                        "count_a": counts[a],
                        "count_b": counts[b],
                        "similarity": round(sim, 4),
                    })

    pairs.sort(key=lambda p: p["similarity"], reverse=True)
    return pairs
