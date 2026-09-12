"""Topic enrichment for the reflection layer.

Gathers the material under each topic so the calling agent can write a
description through `apply_reflection`, and decides which topics to put in front
of it.

**Two tests, and which one applies depends on whether anybody has looked.** A
topic whose `description_reviewed_at` is set is nominated exactly when a node
under it was created, archived or superseded after that moment: the question is
*has anything happened since somebody last stood behind what this says about
itself*, and `changed_since` answers it. A topic nobody has ever reviewed has no
moment to measure from, so it keeps the length ratio in `_should_enrich`, which
asks the first-time question, *is this topic thin beside its material*. The
ratio answered identically on every run, which is why a good description over
forty facts used to be nominated for ever. `dev-docs/DESCRIPTION_REVIEW.md`
carries the case.

**Enrichment writes `description` and never `content`.** The name is the join
key: `_tag_topic` resolves a tag to a topic node by it at ingest, and
`_resolve_node_reference` resolves the same name for `find_nodes`. Enrichment
used to replace it and retire the old node `CORRECTED`, which split a tag in
two: later documents minted a second topic node, earlier ones stayed on the
first, and neither knew about the other. Writing beside the name instead means
the node keeps its id, its status and every edge it holds, and there is nothing
for a guard to refuse. `dev-docs/TOPIC_DESCRIPTIONS.md` carries the case.
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel

from epimemer.core.types import (
    EdgeType,
    EmbeddingRecord,
    EpistemicNode,
    Fact,
    Inference,
    JudgeRef,
    NodeStatus,
    Topic,
)
from epimemer.embeddings.protocol import EmbeddingProvider
from epimemer.pipelines.embedding_text import embedding_text
from epimemer.storage.protocol import StorageBackend

# The three edges that put a claim under a topic. `tagged_with_topic` is the
# one a topic node created from a tag holds, and leaving it out was why the
# enrichment scan could never reach a tag: the only path to a described tag was
# an agent calling `apply_reflection(enrichments=...)` by hand.
MATERIAL_EDGE_TYPES: tuple[EdgeType, ...] = (
    EdgeType.EXTRACTED_UNDER_TOPIC,
    EdgeType.ABSTRACTS,
    EdgeType.TAGGED_WITH_TOPIC,
)


class MaterialChange(BaseModel):
    """One node under a topic that moved since its description was last stood behind.

    `at` is the moment of the change rather than the node's creation, so a list
    of these sorts newest-first on one field whichever kind of change each is.
    """

    node_id: str
    content: str
    change: Literal["created", "archived", "superseded"]
    at: datetime


async def gather_associated_material_for(
    topics: Sequence[Topic], storage: StorageBackend
) -> dict[str, list[EpistemicNode]]:
    """The epistemic nodes linked to each topic, keyed by topic id.

    Gathers material from:
    - Facts via incoming EXTRACTED_UNDER_TOPIC edges (fact → topic)
    - Inferences via incoming ABSTRACTS edges (inference → topic)
    - Anything tagged with the topic, via incoming TAGGED_WITH_TOPIC edges

    **The nodes rather than their content strings**, because `changed_since`
    asks when each one arrived and whether it has since been retired, which a
    string cannot answer. Callers that want the wording read `.content`.

    Three queries for the whole topic set rather than three per topic — both
    `reflect` phases that need material walk every active topic, so per-topic
    reads made this one of the larger N+1 sites.
    """
    topic_ids = [topic.id for topic in topics]
    by_edge_type = {
        edge_type: await storage.get_edges_for(topic_ids, direction="to", edge_type=edge_type)
        for edge_type in MATERIAL_EDGE_TYPES
    }

    # The linked nodes in one read rather than one per edge. This was the
    # largest remaining N+1 in `reflect`: 900 fetches at 1,200 nodes, and on
    # SurrealDB each cost 1-3 round-trips, because a node's table is not known
    # from its id and `get_node` probes all three.
    linked = await storage.get_nodes(
        [
            edge.src_id
            for by_topic in by_edge_type.values()
            for edges in by_topic.values()
            for edge in edges
        ]
    )

    material: dict[str, list[EpistemicNode]] = {}
    for topic_id in topic_ids:
        nodes: list[EpistemicNode] = []
        # Deduplicated: one node carrying a tag it was also extracted under
        # would otherwise be counted, sampled and reported as two.
        seen: set[str] = set()
        for by_topic in by_edge_type.values():
            for edge in by_topic[topic_id]:
                node = linked.get(edge.src_id)
                if isinstance(node, (Fact, Inference)) and node.id not in seen:
                    seen.add(node.id)
                    nodes.append(node)
        material[topic_id] = nodes
    return material


def material_contents(material: Sequence[EpistemicNode]) -> list[str]:
    """Just the wording, for the callers that score or sample text."""
    return [node.content for node in material]


def changed_since(material: Sequence[EpistemicNode], since: datetime) -> list[MaterialChange]:
    """The nodes under a topic that arrived or were retired after `since`, newest first.

    Pure. Three kinds of change count, on `DESCRIPTION_REVIEW.md` §2.2's
    reading: a description written over material that has since been retired is
    as stale as one written before half the material arrived.

    Archival and supersession are told apart by `status`, and both are dated by
    `superseded_at`, which is the field `set_node_status_tx` writes whichever
    retires the node. Where a node both arrived and was retired in the window,
    the retirement is the change reported: it is the later of the two, and it is
    the one that decides what the topic now stands over.
    """
    changes = [change for node in material if (change := _change_to(node, since)) is not None]
    return sorted(changes, key=lambda change: change.at, reverse=True)


def _change_to(node: EpistemicNode, since: datetime) -> MaterialChange | None:
    """What happened to this node after `since`, or `None` if nothing did."""
    retired_at = node.superseded_at
    if retired_at is not None and retired_at > since:
        return MaterialChange(
            node_id=node.id,
            content=node.content,
            change="archived" if node.status is NodeStatus.ARCHIVED else "superseded",
            at=retired_at,
        )
    if node.created_at > since:
        return MaterialChange(
            node_id=node.id, content=node.content, change="created", at=node.created_at
        )
    return None


def _should_enrich(topic: Topic, material: Sequence[str], material_ratio: float) -> bool:
    """Whether what this topic says about itself is thin beside its material.

    Measured against `content` **and** `description` together, because that is
    what the topic now says about itself. Against `content` alone, a tag would be
    nominated on every reflect for ever: its name is a few characters whatever
    anybody writes beside it, so the ratio could never be satisfied and the pair
    would come back after each enrichment that had already answered it.
    """
    if not material:
        return False
    said = len(topic.content) + len(topic.description)
    return sum(len(m) for m in material) >= said * material_ratio


def described(
    topic: Topic, description: str, *, judge: JudgeRef | None, at: datetime | None = None
) -> Topic:
    """`topic` with `description` written on it, keeping the wording it replaced.

    Pure, and the whole of what an enrichment changes: same id, same `content`
    byte for byte, same status, and no lineage edge, so every edge the node holds
    stays pointed at it.

    **The replaced wording goes in `metadata`, not in the journal row.** An
    overwrite with no history would make `description` the one field on a topic
    that can be edited and the one field nothing records, and `describe_relation`
    only gets away with overwriting prose because its second journal row carries
    the new text. Extending the `ENRICHMENT` row would change `journal`'s payload
    for every caller of it; a node's own trail is where a node's own history
    already lives, beside `metacontext_reassignments` and the `rejudge` trail.

    **`description_reviewed_at` moves to `at`**, because whoever wrote this
    sentence has just read the topic's material: that is the same act a
    confirmation records, and reflect measures the next change from it. `at`
    defaults to now, and is an argument so a caller writing several topics in
    one batch can stamp them all with one moment.

    `judged_by` does not move. It records who wrote the *name*, which is
    unchanged, and each trail entry carries whoever wrote that description.
    """
    history = (
        [
            *topic.metadata.get("description_history", []),
            {
                "replaced": topic.description,
                "judged_by": judge.model_dump(mode="json") if judge else None,
            },
        ]
        if topic.description
        else topic.metadata.get("description_history", [])
    )
    return topic.model_copy(
        update={
            "description": description,
            "description_reviewed_at": at if at is not None else datetime.now(UTC),
            "metadata": {
                **topic.metadata,
                **({"description_history": history} if history else {}),
            },
        }
    )


async def reembedded(
    node: EpistemicNode,
    storage: StorageBackend,
    embedding_provider: EmbeddingProvider,
) -> EmbeddingRecord:
    """This node's embedding record, rewritten on the text it now embeds on.

    **Reuses the existing record's id**, which is what makes it a rewrite rather
    than a second vector. Embeddings are keyed by their own id and
    `vector_search` scores every row, so storing a fresh record for a node that
    already had one would leave the stale vector in the ranking beside the new
    one and let a topic match on wording it no longer carries.

    A node that has no record yet gets a new one, which is the ordinary case for
    anything created outside an ingest batch.
    """
    vector = (await embedding_provider.embed([embedding_text(node)]))[0]
    record = EmbeddingRecord(
        item_id=node.id,
        model_id=embedding_provider.model_id,
        vector=vector,
    )
    existing = await storage.get_embeddings_for_item(node.id, model_id=embedding_provider.model_id)
    if existing:
        return record.model_copy(update={"id": existing[0].id})
    return record
