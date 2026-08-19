"""Vector search for the query layer.

Embeds query text and searches for semantically similar epistemic nodes
in the storage backend. Returns (node, score) pairs sorted by similarity.
"""

from epimemer.core.types import EpistemicNode, NodeStatus, NodeType
from epimemer.embeddings.protocol import EmbeddingProvider
from epimemer.storage.protocol import StorageBackend


async def vector_search(
    query_text: str,
    embedding_provider: EmbeddingProvider,
    storage: StorageBackend,
    *,
    k: int = 10,
    model_id: str | None = None,
    node_type: NodeType | None = None,
    statuses: frozenset[NodeStatus] = frozenset({NodeStatus.ACTIVE}),
) -> list[tuple[EpistemicNode, float]]:
    """Embed query text, search for similar nodes. Returns (node, score) pairs.

    `statuses` is passed straight through; the default keeps a retired claim out
    of any caller that has not asked for one. Its counterpart on the lexical arm
    takes the same set, so the two seed routes cannot disagree about whether a
    node exists.
    """
    resolved_model_id = model_id or embedding_provider.model_id

    # Embed the query text
    vectors = await embedding_provider.embed([query_text])
    query_vector = vectors[0]

    # Search for similar items in storage
    search_results = await storage.vector_search(
        query_vector,
        resolved_model_id,
        k=k,
        node_type=node_type,
        statuses=statuses,
    )

    # Fetch the actual nodes for each result
    results: list[tuple[EpistemicNode, float]] = []
    for item_id, score in search_results:
        node = await storage.get_node(item_id)
        if node is not None:
            results.append((node, score))

    return results
