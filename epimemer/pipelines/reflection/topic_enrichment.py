"""Topic enrichment for the reflection layer.

Identifies topics whose associated material has grown substantially richer than
what the topic currently says about itself, and gathers that material so the
calling agent can write a description through `apply_reflection`.

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

from epimemer.core.types import (
    EdgeType,
    EmbeddingRecord,
    EpistemicNode,
    Fact,
    Inference,
    JudgeRef,
    Topic,
)
from epimemer.embeddings.protocol import EmbeddingProvider
from epimemer.pipelines.embedding_text import embedding_text
from epimemer.storage.protocol import StorageBackend


async def gather_associated_material_for(
    topics: Sequence[Topic], storage: StorageBackend
) -> dict[str, list[str]]:
    """Text content of the epistemic nodes linked to each topic, keyed by id.

    Gathers content from:
    - Facts via incoming EXTRACTED_UNDER_TOPIC edges (fact → topic)
    - Inferences via incoming ABSTRACTS edges (inference → topic)

    Two queries for the whole topic set rather than two per topic — both
    `reflect` phases that need material walk every active topic, so per-topic
    reads made this one of the larger N+1 sites.
    """
    topic_ids = [topic.id for topic in topics]
    by_edge_type = {
        edge_type: await storage.get_edges_for(topic_ids, direction="to", edge_type=edge_type)
        for edge_type in (EdgeType.EXTRACTED_UNDER_TOPIC, EdgeType.ABSTRACTS)
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

    material: dict[str, list[str]] = {}
    for topic_id in topic_ids:
        contents: list[str] = []
        for by_topic in by_edge_type.values():
            for edge in by_topic[topic_id]:
                node = linked.get(edge.src_id)
                if node is not None and isinstance(node, (Fact, Inference)):
                    contents.append(node.content)
        material[topic_id] = contents
    return material


def _should_enrich(topic: Topic, material: list[str], material_ratio: float) -> bool:
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


def described(topic: Topic, description: str, *, judge: JudgeRef | None) -> Topic:
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
