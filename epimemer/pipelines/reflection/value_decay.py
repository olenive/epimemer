"""Value signal decay for the reflection layer.

Applies multiplicative relevance decay to all active nodes, simulating
the natural fading of unreinforced memories over time.
"""

from epimemer.core.types import EpistemicNode, NodeStatus, ValueSignal
from epimemer.storage.protocol import StorageBackend


async def apply_decay(
    storage: StorageBackend,
    *,
    decay_rate: float = 0.05,
    min_relevance: float = 0.01,
) -> int:
    """Apply relevance decay to all active nodes.

    For each active node, reduces relevance multiplicatively:
        relevance *= (1 - decay_rate)

    The result is clamped to min_relevance so nodes never reach zero.

    Args:
        storage: The storage backend.
        decay_rate: Fraction of relevance lost per decay pass (0.0 to 1.0).
        min_relevance: Floor value for relevance after decay.

    Returns:
        The number of nodes whose relevance was decayed.
    """
    # Get all active nodes (all types)
    active_nodes: list[EpistemicNode] = list(
        await storage.query_nodes(status=NodeStatus.ACTIVE)
    )

    decayed_count = 0
    for node in active_nodes:
        old_relevance = node.value.relevance
        new_relevance = old_relevance * (1.0 - decay_rate)
        new_relevance = max(new_relevance, min_relevance)

        if new_relevance != old_relevance:
            node.value = ValueSignal(
                novelty=node.value.novelty,
                confidence=node.value.confidence,
                relevance=new_relevance,
                last_reinforced=node.value.last_reinforced,
            )
            # For InMemoryStorage the node is already mutated in place,
            # but we store it back for correctness with other backends.
            await storage.store_node(node)
            decayed_count += 1

    return decayed_count
