"""Nominating inferences that agree without sharing a premise (`SIMILAR_INFERENCES.md`).

`inference_dedup` offers near-identical inferences resting on a common premise,
as merge candidates. This sweep offers the other pairs, two inferences that say
nearly the same thing from evidence they do not share, so the agreement between
them has a way into the graph. The agent answers each one through
`apply_reflection(similarities=[...])`: `one_claim` writes a `similarity` edge,
`distinct` writes `assessed` alone, and either verdict stops the pair coming
back.

**The two lists partition the pairs by premise overlap.** A pair sharing any
premise belongs to `inference_merge_candidates`, and leaving it out here is
what keeps one pair from being asked about twice under two framings. The
verdict index is shared, so a pair judged on either list is suppressed on
both, including a pair that moves lists when a fact merge brings two premises
together.

**The sweep is global, unlike the merge sweep**, because there is no shared
premise to group by: absence of overlap is the condition, and nothing narrows
a search for it. The scoring is the batched matrix product the fact sweep
uses, and every later read is bounded by the pairs that cleared the bar rather
than by the graph.

**The bar is the one nomination bar.** The merge sweep's header records that
a global sweep over 123 real inferences found nothing at it (maximum 0.66),
and that the best-scoring pairs below it shared vocabulary and said different
things. Lowering the bar for this list would fill it with exactly those. On
graphs like the measured ones the list is empty, and finding that out costs a
read of the active inferences and their embeddings and one matrix product.

**A `similarity` edge between inferences is reported by corroboration, not
counted.** `corroboration.py` lists the partner under `agreeing_inferences`
beside the count, so recording agreement cannot raise a number callers read as
independent support.
"""

from pydantic import BaseModel, Field

from epimemer.core.types import Inference, NodeStatus, NodeType
from epimemer.embeddings.protocol import EmbeddingProvider
from epimemer.pipelines.reflection.pair_scoring import similar_pairs, stack_uniform_width
from epimemer.pipelines.reflection.review import (
    SIMILARITY_NOMINATION_THRESHOLD,
    NodeRef,
    metacontexts_for,
)
from epimemer.pipelines.reflection.similarity_decisions import already_judged_pairs
from epimemer.pipelines.reflection.soundness import premise_ids_for
from epimemer.storage.protocol import StorageBackend


class InferenceWithPremises(BaseModel):
    """One side of a nominated pair, with the premises it was drawn from.

    The premises come with it because they are what the agent weighs: two
    conclusions reached from separate evidence is the case this list exists
    for, and the agent can only see that it is separate evidence by reading it.
    """

    id: str
    content: str
    premises: list[NodeRef] = Field(default_factory=list)


class SimilarInferencePair(BaseModel):
    """Two active inferences that say nearly the same thing and share no premise.

    A proposal, never a verdict. Whether they draw the same conclusion is the
    agent's judgment, recorded as `one_claim` or `distinct`.
    """

    inferences: list[InferenceWithPremises]
    similarity: float


async def nominate_similar_inferences(
    storage: StorageBackend,
    embedding_provider: EmbeddingProvider,
    *,
    similarity_threshold: float = SIMILARITY_NOMINATION_THRESHOLD,
    model_id: str | None = None,
) -> list[SimilarInferencePair]:
    """Similar active inference pairs with no premise in common, highest scoring first.

    Reads only. Pairs somebody already judged are left out, on
    `already_judged_pairs`' terms, and so is a pair standing in no common
    metacontext: `one_claim` refuses one, so offering it would put a pair on the
    worklist that can only ever be declined.
    """
    effective_model_id = model_id or embedding_provider.model_id

    inferences = [
        node
        for node in await storage.query_nodes(
            node_type=NodeType.INFERENCE, status=NodeStatus.ACTIVE
        )
        if isinstance(node, Inference)
    ]
    if len(inferences) < 2:
        return []

    by_id = {node.id: node for node in inferences}
    stored = await storage.get_embeddings_for_items(list(by_id), model_id=effective_model_id)
    vectors = {node_id: stored[node_id][0].vector for node_id in by_id if stored.get(node_id)}
    kept, matrix = stack_uniform_width(list(by_id), vectors)
    if not kept:
        return []

    scored = [
        (frozenset({kept[i], kept[j]}), score)
        for i, j, score in similar_pairs(matrix, similarity_threshold)
    ]
    if not scored:
        return []

    # Every read from here is asked only about the inferences in a scored pair,
    # which on a real graph is a handful out of hundreds.
    involved = list(dict.fromkeys(node_id for pair, _ in scored for node_id in sorted(pair)))
    judged = await already_judged_pairs(involved, storage)
    premises = await premise_ids_for(involved, storage)
    unshared = [
        (pair, score)
        for pair, score in scored
        if pair not in judged
        and not set.intersection(*(set(premises[node_id]) for node_id in pair))
    ]
    if not unshared:
        return []

    surviving = list(dict.fromkeys(node_id for pair, _ in unshared for node_id in sorted(pair)))
    metacontexts = await metacontexts_for(surviving, storage)
    offered = [
        (pair, score)
        for pair, score in unshared
        if set.intersection(*(metacontexts[node_id] for node_id in pair))
    ]
    if not offered:
        return []

    premise_nodes = await storage.get_nodes(
        list(
            dict.fromkeys(
                premise_id
                for pair, _ in offered
                for node_id in sorted(pair)
                for premise_id in premises[node_id]
            )
        )
    )

    candidates = [
        SimilarInferencePair(
            inferences=[
                InferenceWithPremises(
                    id=node_id,
                    content=by_id[node_id].content,
                    premises=[
                        NodeRef(id=premise_id, content=premise_nodes[premise_id].content)
                        for premise_id in premises[node_id]
                        if premise_id in premise_nodes
                    ],
                )
                for node_id in sorted(pair)
            ],
            similarity=round(score, 4),
        )
        for pair, score in offered
    ]
    # Stable, so tied pairs keep the order `similar_pairs` produced them in, as
    # every other nominee list does.
    candidates.sort(key=lambda candidate: candidate.similarity, reverse=True)
    return candidates
