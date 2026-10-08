"""Inferences that agree without sharing a premise are offered, and only those.

The merge sweep offers near-identical inferences resting on a common premise.
This one offers the rest of the similar pairs, so the two lists partition the
pairs by premise overlap and no pair is asked about twice. What the agent
records about a pair here is a `similarity` verdict, and corroboration reports
that edge rather than counting it (`SIMILAR_INFERENCES.md`).
"""

import pytest

from epimemer.core.types import (
    BASE_METACONTEXT_ID,
    ClaimKind,
    EdgeType,
    EmbeddingRecord,
    Fact,
    Inference,
    Metacontext,
    NodeEdge,
    NodeStatus,
)
from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.pipelines.reflection.inference_dedup import nominate_inference_merges
from epimemer.pipelines.reflection.review import SIMILARITY_NOMINATION_THRESHOLD
from epimemer.pipelines.reflection.similar_inferences import nominate_similar_inferences
from epimemer.pipelines.reflection.similarity_decisions import apply_similarity_decision


@pytest.fixture
def embedding_provider() -> MockEmbeddingProvider:
    return MockEmbeddingProvider(model_id="mock-embed", dimension=8)


# Supplied rather than derived from the text, so a test about which pairs are
# offered is not also a test of how alike two sentences happen to hash.
_TWIN = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
_STRANGER = [0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]


async def _inference(
    storage,
    embedding_provider,
    content: str,
    *,
    vector: list[float] | None = None,
    status: NodeStatus = NodeStatus.ACTIVE,
    metacontext: str | None = BASE_METACONTEXT_ID,
) -> Inference:
    """One stored, embedded inference. Defaults are the case that is offered."""
    inference = Inference(content=content, source_id="seg-1", status=status)
    await storage.store_node(inference)
    await storage.store_embedding(
        EmbeddingRecord(
            item_id=inference.id,
            model_id=embedding_provider.model_id,
            vector=vector or _TWIN,
        )
    )
    if metacontext is not None:
        await storage.store_edge(
            NodeEdge(src_id=inference.id, dst_id=metacontext, type=EdgeType.HAS_METACONTEXT)
        )
    return inference


async def _premise(storage, content: str) -> Fact:
    fact = Fact(content=content, source_id="seg-1", claim_kind=ClaimKind.STATE)
    await storage.store_node(fact)
    return fact


async def _rests_on(storage, inference: Inference, premise: Fact) -> None:
    await storage.store_edge(
        NodeEdge(src_id=inference.id, dst_id=premise.id, type=EdgeType.DERIVED_FROM)
    )


async def _agreeing_pair(storage, embedding_provider) -> tuple[Inference, Inference, Fact, Fact]:
    """Two readings of one conclusion, each drawn from its own evidence."""
    one = await _inference(storage, embedding_provider, "The release is unsafe")
    other = await _inference(storage, embedding_provider, "The release cannot be trusted")
    failed = await _premise(storage, "The deploy failed")
    grew = await _premise(storage, "The error queue grew all week")
    await _rests_on(storage, one, failed)
    await _rests_on(storage, other, grew)
    return one, other, failed, grew


class TestWhatIsOffered:
    async def test_agreement_from_separate_evidence_is_offered(self, storage, embedding_provider):
        one, other, failed, grew = await _agreeing_pair(storage, embedding_provider)

        found = await nominate_similar_inferences(storage, embedding_provider)

        assert len(found) == 1
        assert {side.id for side in found[0].inferences} == {one.id, other.id}
        assert found[0].similarity >= SIMILARITY_NOMINATION_THRESHOLD

    async def test_each_side_carries_what_it_was_drawn_from(self, storage, embedding_provider):
        """The premises are what tell the agent this is agreement between two
        bodies of evidence, so each side shows its own."""
        one, other, failed, grew = await _agreeing_pair(storage, embedding_provider)

        [found] = await nominate_similar_inferences(storage, embedding_provider)

        drawn_from = {side.id: [ref.id for ref in side.premises] for side in found.inferences}
        assert drawn_from == {one.id: [failed.id], other.id: [grew.id]}

    async def test_a_premise_reached_by_supports_is_shared_too(self, storage, embedding_provider):
        """`supports` and `derived_from` record one relation, as the soundness
        check and the merge sweep both read it."""
        one = await _inference(storage, embedding_provider, "The release is unsafe")
        other = await _inference(storage, embedding_provider, "The release cannot be trusted")
        shared = await _premise(storage, "The deploy failed")
        await _rests_on(storage, one, shared)
        await storage.store_edge(
            NodeEdge(src_id=shared.id, dst_id=other.id, type=EdgeType.SUPPORTS)
        )

        assert await nominate_similar_inferences(storage, embedding_provider) == []

    async def test_two_inferences_resting_on_nothing_share_nothing(
        self, storage, embedding_provider
    ):
        await _inference(storage, embedding_provider, "The release is unsafe")
        await _inference(storage, embedding_provider, "The release cannot be trusted")

        assert len(await nominate_similar_inferences(storage, embedding_provider)) == 1

    async def test_below_the_bar_is_not_offered(self, storage, embedding_provider):
        await _inference(storage, embedding_provider, "The release is unsafe")
        await _inference(storage, embedding_provider, "The rollback was routine", vector=_STRANGER)

        assert await nominate_similar_inferences(storage, embedding_provider) == []

    async def test_highest_scoring_first(self, storage, embedding_provider):
        near = [0.95, 0.31, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        await _inference(storage, embedding_provider, "The release is unsafe")
        await _inference(storage, embedding_provider, "The release cannot be trusted")
        await _inference(storage, embedding_provider, "The release is doubtful", vector=near)

        found = await nominate_similar_inferences(storage, embedding_provider)

        scores = [candidate.similarity for candidate in found]
        assert len(scores) == 3
        assert scores == sorted(scores, reverse=True)
        assert scores[0] == pytest.approx(1.0)


class TestTheListsDoNotOverlap:
    """A pair sharing a premise is the merge sweep's, and offering it here too
    would ask one question twice under two framings."""

    async def test_a_shared_premise_keeps_the_pair_out(self, storage, embedding_provider):
        one = await _inference(storage, embedding_provider, "The release is unsafe")
        other = await _inference(storage, embedding_provider, "The release cannot be trusted")
        shared = await _premise(storage, "The deploy failed")
        await _rests_on(storage, one, shared)
        await _rests_on(storage, other, shared)

        assert await nominate_similar_inferences(storage, embedding_provider) == []
        assert len(await nominate_inference_merges(storage, embedding_provider)) == 1

    async def test_one_shared_premise_among_several_is_enough(self, storage, embedding_provider):
        one = await _inference(storage, embedding_provider, "The release is unsafe")
        other = await _inference(storage, embedding_provider, "The release cannot be trusted")
        shared = await _premise(storage, "The deploy failed")
        await _rests_on(storage, one, shared)
        await _rests_on(storage, other, shared)
        await _rests_on(storage, one, await _premise(storage, "The error queue grew"))
        await _rests_on(storage, other, await _premise(storage, "The pager fired twice"))

        assert await nominate_similar_inferences(storage, embedding_provider) == []

    async def test_separate_evidence_is_not_a_merge_candidate(self, storage, embedding_provider):
        await _agreeing_pair(storage, embedding_provider)

        assert await nominate_inference_merges(storage, embedding_provider) == []
        assert len(await nominate_similar_inferences(storage, embedding_provider)) == 1


class TestAJudgedPairStaysJudged:
    """Every verdict stops the pair, and only that pair."""

    @pytest.mark.parametrize("verdict", ["one_claim", "distinct"])
    async def test_a_verdict_stops_the_pair_coming_back(self, storage, embedding_provider, verdict):
        one, other, _, _ = await _agreeing_pair(storage, embedding_provider)
        assert await nominate_similar_inferences(storage, embedding_provider)

        recorded = await apply_similarity_decision(
            storage, a_id=one.id, b_id=other.id, verdict=verdict, because="read both"
        )

        assert recorded.verdict == verdict
        assert await nominate_similar_inferences(storage, embedding_provider) == []

    async def test_an_unjudged_pair_comes_back(self, storage, embedding_provider):
        await _agreeing_pair(storage, embedding_provider)

        first = await nominate_similar_inferences(storage, embedding_provider)
        second = await nominate_similar_inferences(storage, embedding_provider)

        assert first == second
        assert len(second) == 1

    async def test_a_third_unjudged_inference_is_still_offered(self, storage, embedding_provider):
        one, other, _, _ = await _agreeing_pair(storage, embedding_provider)
        third = await _inference(storage, embedding_provider, "The release is doubtful")
        await apply_similarity_decision(
            storage, a_id=one.id, b_id=other.id, verdict="distinct", because="read both"
        )

        found = await nominate_similar_inferences(storage, embedding_provider)

        assert {frozenset(side.id for side in pair.inferences) for pair in found} == {
            frozenset({one.id, third.id}),
            frozenset({other.id, third.id}),
        }


class TestWhatIsLeftOut:
    async def test_a_pair_in_no_common_metacontext(self, storage, embedding_provider):
        """`one_claim` refuses it, so offering it would put a pair on the
        worklist that can only ever be declined."""
        real = await _inference(storage, embedding_provider, "The release is unsafe")
        novel = Metacontext(content="Novel-X")
        await storage.store_metacontext(novel)
        fictional = await _inference(
            storage, embedding_provider, "The release cannot be trusted", metacontext=novel.id
        )
        await _rests_on(storage, real, await _premise(storage, "The deploy failed"))
        await _rests_on(storage, fictional, await _premise(storage, "The queue grew"))

        assert await nominate_similar_inferences(storage, embedding_provider) == []

    async def test_a_retired_inference(self, storage, embedding_provider):
        await _inference(storage, embedding_provider, "The release is unsafe")
        await _inference(
            storage,
            embedding_provider,
            "The release cannot be trusted",
            status=NodeStatus.MERGED,
        )

        assert await nominate_similar_inferences(storage, embedding_provider) == []

    async def test_facts_are_not_swept_here(self, storage, embedding_provider):
        for content in ("The deploy failed", "The deployment did not succeed"):
            fact = await _premise(storage, content)
            await storage.store_embedding(
                EmbeddingRecord(item_id=fact.id, model_id=embedding_provider.model_id, vector=_TWIN)
            )

        assert await nominate_similar_inferences(storage, embedding_provider) == []
