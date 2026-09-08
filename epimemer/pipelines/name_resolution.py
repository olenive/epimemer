"""Resolving a name to the node that answers to it.

A tag is a name, and the graph joins on it twice: `_tag_topic` resolves or
creates one at ingest, `_resolve_node_reference` resolves one for `find_nodes`.
Both
matched the content exactly, against active nodes only, and that failed in two
ways this module exists to close.

**A name written two ways is one name.** `claim_kind` and `claim-kind` differ by
a separator, and an exact match makes them two topic nodes. Cosine cannot make
the call
either: those two score 0.9196 while `dev-session-2026-09-05` and
`dev-session-2026-09-06` score 0.9935, so any threshold that unifies the first
pair unifies the second, which is two different days of work. `tag_key` collapses
the spelling and nothing else. `dev-docs/TAG_IDENTITY.md` carries the
measurements.

**A name whose node was retired still points somewhere.** Enrichment rewrites a
topic's content and retires the old node `CORRECTED`; a merge retires its sources
`MERGED` and names the survivor whatever the agent chose. In both cases the old
name resolved to nothing, so the next document carrying it minted a second topic
node while `find_nodes` returned an empty list for it. `live_successor` follows the
edge the retirement wrote. `dev-docs/TOPIC_DESCRIPTIONS.md` carries the case.

The two are separate questions with one answer each, and both live here because
they are asked together at both call sites: normalise the name, then follow where
the node it names has gone.
"""

import re

from epimemer.core.types import EdgeType, EpistemicNode, NodeStatus, NodeType, Topic
from epimemer.pipelines.metacontexts import created_from_tag
from epimemer.storage.protocol import StorageBackend

# What a retirement writes, and so what says *where this name went now*. A merge
# and a correction are read the same way here and for the same reason: either
# way the name's content moved to the node at the far end.
#
# Deliberately not `LINEAGE_FOLD_EDGE_TYPES`, which excludes `merged_into`
# because a merged node's content lives on the survivor and so never reaches
# search to be folded. That is the same fact answering the opposite question: a
# merged node is exactly the case whose name has somewhere to go.
SUCCESSOR_EDGE_TYPES = (EdgeType.MERGED_INTO, EdgeType.SUPERSEDED_BY)

# The retired statuses a name is looked up under once the active lookup misses,
# in the order a name is most likely to have left. `HISTORICAL` is excluded and
# `ARCHIVED` with it: a historical claim is still right of its period and its
# name has not moved anywhere, and an archived node is reversed by `restore`
# rather than replaced. Only these two say *the content went somewhere else*.
RETIRED_NAME_STATUSES = (NodeStatus.MERGED, NodeStatus.CORRECTED)

# A lineage is short and a cycle is not supposed to be reachable, so this is a
# guard rather than a limit: `temporally_followed_by` legalised cycles for a
# claim that becomes true again, and a successor walk that trusted the data
# would hang rather than answer.
MAX_SUCCESSOR_HOPS = 16


def tag_key(name: str) -> str:
    """A tag's identity, ignoring case and separators.

    `claim_kind` and `claim-kind` are one name written twice, and
    `dev-session-2026-09-05` and `dev-session-2026-09-06` are two names. What
    separates them is the digits, so collapsing case and separators is enough
    and anything more is too much: stemming would fuse `reflect` with
    `reflection`, which name different things here.
    """
    return re.sub(r"[\s_-]+", "", name.strip().lower())


def tag_by_key(name: str, topics: list[Topic]) -> Topic | None:
    """The tag among `topics` whose name is `name` up to spelling.

    Tags only. A statement topic's content is prose, and letting one answer to a
    normalised tag name would hand a tag's topic node to something nobody wrote
    as a tag. Ties go to the first, which is the older node where the caller passed
    them in creation order: the spelling already in the graph wins, so a graph
    settles on one house spelling rather than following whatever was typed last.
    """
    wanted = tag_key(name)
    for topic in topics:
        if created_from_tag(topic) and tag_key(topic.content) == wanted:
            return topic
    return None


async def resolve_name(
    name: str, storage: StorageBackend, *, node_type: NodeType = NodeType.TOPIC
) -> EpistemicNode | None:
    """The node that answers to this exact name, following a retirement forward.

    The active lookup first, which is the common case and keeps the named content
    index. Then the same lookup under the statuses a retirement leaves behind, so
    a name whose node was merged away or corrected resolves to whatever carries
    its content now instead of resolving to nothing.
    """
    found = await storage.get_node_by_content(name, node_type=node_type)
    if found is not None:
        return found
    for status in RETIRED_NAME_STATUSES:
        retired = await storage.get_node_by_content(name, node_type=node_type, status=status)
        if retired is not None:
            return await live_successor(retired, storage)
    return None


async def live_successor(node: EpistemicNode, storage: StorageBackend) -> EpistemicNode:
    """The node that carries this one's content now, or the node itself.

    Walks `merged_into` and `superseded_by` forward until it reaches something
    active. An active node is returned untouched, which makes this safe to call
    on every hit rather than only on a retired one.

    **Returns the node it was given rather than `None` when the trail ends
    somewhere retired.** A dangling lineage is a graph defect and a caller that
    got `None` would create a duplicate topic node, which is the failure this
    exists to
    prevent; returning the retired node keeps the name pointing at the thing it
    named.
    """
    seen = {node.id}
    current = node
    for _ in range(MAX_SUCCESSOR_HOPS):
        if current.status is NodeStatus.ACTIVE:
            return current
        following = None
        for edge_type in SUCCESSOR_EDGE_TYPES:
            for edge in await storage.get_edges_from(current.id, edge_type=edge_type):
                if edge.dst_id not in seen:
                    following = edge.dst_id
                    break
            if following is not None:
                break
        if following is None:
            return current
        seen.add(following)
        successor = await storage.get_node(following)
        if successor is None:
            return current
        current = successor
    return current
