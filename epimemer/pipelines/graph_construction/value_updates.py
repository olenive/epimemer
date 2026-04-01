"""Value signal update functions.

Plain async functions for updating value signals when new data arrives
that relates to existing nodes.
"""

from datetime import datetime, timezone

from epimemer.core.types import EpistemicNode, ValueSignal
from epimemer.storage.protocol import StorageBackend


async def update_value_on_supporting_evidence(
    node: EpistemicNode,
    storage: StorageBackend,
) -> None:
    """Increase confidence and update recency when supporting evidence arrives.

    Bumps confidence by 0.1 (clamped to 1.0) and refreshes last_reinforced
    to the current time.
    """
    now = datetime.now(timezone.utc)
    node.value = ValueSignal(
        novelty=node.value.novelty,
        confidence=min(1.0, node.value.confidence + 0.1),
        relevance=node.value.relevance,
        last_reinforced=now,
    )
    await storage.store_node(node)


async def update_value_on_contradiction(
    node: EpistemicNode,
    contradicting_node: EpistemicNode,
    storage: StorageBackend,
) -> None:
    """Increase novelty on both nodes when contradiction detected.

    Bumps novelty by 0.2 (clamped to 1.0) on both the original node
    and the contradicting node, then stores both.
    """
    node.value = ValueSignal(
        novelty=min(1.0, node.value.novelty + 0.2),
        confidence=node.value.confidence,
        relevance=node.value.relevance,
        last_reinforced=node.value.last_reinforced,
    )
    contradicting_node.value = ValueSignal(
        novelty=min(1.0, contradicting_node.value.novelty + 0.2),
        confidence=contradicting_node.value.confidence,
        relevance=contradicting_node.value.relevance,
        last_reinforced=contradicting_node.value.last_reinforced,
    )
    await storage.store_node(node)
    await storage.store_node(contradicting_node)


async def reinforce_node(
    node: EpistemicNode,
    storage: StorageBackend,
) -> None:
    """Update last_reinforced timestamp and boost relevance.

    Sets last_reinforced to the current time and bumps relevance
    by 0.1 (clamped to 1.0).
    """
    now = datetime.now(timezone.utc)
    node.value = ValueSignal(
        novelty=node.value.novelty,
        confidence=node.value.confidence,
        relevance=min(1.0, node.value.relevance + 0.1),
        last_reinforced=now,
    )
    await storage.store_node(node)
