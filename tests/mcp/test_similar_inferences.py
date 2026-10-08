"""`reflect` offers agreeing inferences, `apply_reflection` records the verdict,
and corroboration reports the agreement without counting it.

The pipeline tests settle which pairs are offered. These follow one pair through
the tools an agent actually calls, because the verdict path, the cap, the
reopen history and the corroboration report all live on that side.
"""

from datetime import UTC, datetime

import pytest

from epimemer.core.types import (
    BASE_METACONTEXT_ID,
    ClaimKind,
    DecisionKind,
    EdgeType,
    EmbeddingRecord,
    Fact,
    Inference,
    JudgeRef,
    NodeEdge,
    NodeStatus,
    RawDocument,
)
from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.mcp import tools
from epimemer.pipelines.query.corroboration import corroboration_for
from epimemer.pipelines.reflection.similarity_decisions import symmetric_edge_between

_TWIN = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]

CRITIC = JudgeRef(agent_id="critic", digest="d1")


@pytest.fixture
def embedding_provider() -> MockEmbeddingProvider:
    return MockEmbeddingProvider(model_id="mock-embed", dimension=8)


async def _sourced(storage, node, name: str) -> None:
    """A document of its own behind `node`, the provenance ingest writes."""
    document = RawDocument(content=f"the text of {name}", source=name)
    await storage.store_document(document)
    await storage.store_edge(
        NodeEdge(src_id=node.id, dst_id=document.id, type=EdgeType.SOURCED_FROM)
    )


async def _inference(storage, embedding_provider, content: str, *, vector=None) -> Inference:
    inference = Inference(content=content, source_id="seg-1")
    await storage.store_node(inference)
    await storage.store_embedding(
        EmbeddingRecord(
            item_id=inference.id,
            model_id=embedding_provider.model_id,
            vector=vector or _TWIN,
        )
    )
    await storage.store_edge(
        NodeEdge(src_id=inference.id, dst_id=BASE_METACONTEXT_ID, type=EdgeType.HAS_METACONTEXT)
    )
    return inference


async def _premise(storage, content: str, document: str) -> Fact:
    fact = Fact(content=content, source_id="seg-1", claim_kind=ClaimKind.STATE)
    await storage.store_node(fact)
    await _sourced(storage, fact, document)
    return fact


async def _agreeing_pair(storage, embedding_provider) -> tuple[Inference, Inference, Fact, Fact]:
    """Two readings of one conclusion from two documents' evidence."""
    one = await _inference(storage, embedding_provider, "The release is unsafe")
    other = await _inference(storage, embedding_provider, "The release cannot be trusted")
    await _sourced(storage, one, "incident-review")
    await _sourced(storage, other, "weekly-report")
    failed = await _premise(storage, "The deploy failed", "incident-review")
    grew = await _premise(storage, "The error queue grew all week", "weekly-report")
    for inference, premise in ((one, failed), (other, grew)):
        await storage.store_edge(
            NodeEdge(src_id=inference.id, dst_id=premise.id, type=EdgeType.DERIVED_FROM)
        )
    return one, other, failed, grew


async def _offered(storage, embedding_provider) -> list[dict]:
    result, _ = await tools.reflect(storage, embedding_provider)
    return result["similar_inferences"]


def _pair_ids(candidate: dict) -> set[str]:
    return {side["id"] for side in candidate["inferences"]}


class TestReflectOffersThePair:
    async def test_the_pair_arrives_and_is_declared(self, storage, embedding_provider):
        """A nominee whose ids go undeclared is invisible to the retrieval
        record, which is where a reader checks what the agent was shown."""
        one, other, failed, grew = await _agreeing_pair(storage, embedding_provider)

        result, meta = await tools.reflect(storage, embedding_provider)

        [candidate] = result["similar_inferences"]
        assert _pair_ids(candidate) == {one.id, other.id}
        assert result["inference_merge_candidates"] == []
        declared = {record.node_id for record in meta.retrieved}
        assert {one.id, other.id, failed.id, grew.id} <= declared

    async def test_it_has_a_phase_and_a_cap(self):
        assert "similar_inferences" in tools.CAPPED_KEYS
        assert "similar_inference_nomination" in tools.REFLECT_PHASES

    async def test_the_cap_keeps_the_highest_scoring_and_says_it_cut(
        self, storage, embedding_provider
    ):
        near = [0.98, 0.2, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        for index in range(4):
            await _inference(
                storage,
                embedding_provider,
                f"reading {index}",
                vector=_TWIN if index < 2 else near,
            )

        result, _ = await tools.reflect(storage, embedding_provider, max_nominations=2)

        candidates = result["similar_inferences"]
        assert len(candidates) == 2
        assert "similar_inferences" in result["truncated"]
        assert candidates[0]["similarity"] == pytest.approx(1.0)
        assert candidates[0]["similarity"] >= candidates[1]["similarity"]

    async def test_an_unjudged_pair_comes_back(self, storage, embedding_provider):
        await _agreeing_pair(storage, embedding_provider)

        assert len(await _offered(storage, embedding_provider)) == 1
        assert len(await _offered(storage, embedding_provider)) == 1


class TestTheVerdictPath:
    """`similarities` already takes any pair of like nodes, so this list is
    judged through it rather than through a verdict of its own."""

    async def test_one_claim_writes_the_similarity_edge(self, storage, embedding_provider):
        one, other, _, _ = await _agreeing_pair(storage, embedding_provider)

        result, _ = await tools.apply_reflection(
            storage,
            embedding_provider,
            similarities=[
                {
                    "pair": [one.id, other.id],
                    "verdict": "one_claim",
                    "because": "Both conclude the release is unsafe, from different evidence.",
                }
            ],
            judge=CRITIC,
        )

        assert result["similarities_recorded"] == 1
        assert result["similarity_edges_written"] == 1
        assert result["similarities_refused"] == []
        edge = await symmetric_edge_between(one.id, other.id, EdgeType.SIMILARITY, storage)
        assert edge is not None
        assert edge.judged_by.agent_id == "critic"
        rows = await storage.query_decisions(kinds=[DecisionKind.SIMILARITY])
        assert [set(row.subject_ids) for row in rows] == [{one.id, other.id}]
        assert await _offered(storage, embedding_provider) == []

    async def test_distinct_writes_no_similarity_and_still_stops_the_pair(
        self, storage, embedding_provider
    ):
        one, other, _, _ = await _agreeing_pair(storage, embedding_provider)

        result, _ = await tools.apply_reflection(
            storage,
            embedding_provider,
            similarities=[
                {
                    "pair": [one.id, other.id],
                    "verdict": "distinct",
                    "because": "One is about this release, the other about the next.",
                }
            ],
            judge=CRITIC,
        )

        assert result["similarity_edges_written"] == 0
        assert await symmetric_edge_between(one.id, other.id, EdgeType.SIMILARITY, storage) is None
        assert await _offered(storage, embedding_provider) == []

    async def test_a_reopened_decline_comes_back_carrying_its_history(
        self, storage, embedding_provider
    ):
        one, other, _, _ = await _agreeing_pair(storage, embedding_provider)
        await tools.apply_reflection(
            storage,
            embedding_provider,
            similarities=[{"pair": [one.id, other.id], "verdict": "distinct", "because": "no"}],
            judge=CRITIC,
        )

        await tools.reopen(
            storage, node_ids=[one.id, other.id], reason="A later report links them.", judge=CRITIC
        )

        [candidate] = await _offered(storage, embedding_provider)
        assert candidate["reopened"]["reason"] == "A later report links them."


async def _retire(storage, node, status: NodeStatus) -> None:
    await storage.set_node_status_tx([node], status=status, at=datetime.now(UTC))


async def _same_metacontext_facts(storage) -> tuple[Fact, Fact]:
    facts = (
        Fact(content="The deploy failed", source_id="seg-1"),
        Fact(content="The deployment did not succeed", source_id="seg-1"),
    )
    for fact in facts:
        await storage.store_node(fact)
        await storage.store_edge(
            NodeEdge(src_id=fact.id, dst_id=BASE_METACONTEXT_ID, type=EdgeType.HAS_METACONTEXT)
        )
    return facts


class TestAPairRetiredBeforeItsVerdict:
    """`reflect` offered the pair, and before `apply_reflection` arrived one
    side was retired by another call. The verdict is refused into
    `similarities_refused` and the rest of the batch goes on, exactly as for a
    pair of facts in the same position."""

    @pytest.mark.parametrize(
        "status", [NodeStatus.ARCHIVED, NodeStatus.CORRECTED, NodeStatus.MERGED]
    )
    async def test_the_refusal_has_the_shape_of_every_other_pair_list(
        self, storage, embedding_provider, status
    ):
        one, other, _, _ = await _agreeing_pair(storage, embedding_provider)
        [candidate] = await _offered(storage, embedding_provider)
        assert _pair_ids(candidate) == {one.id, other.id}
        fact, retired_fact = await _same_metacontext_facts(storage)

        await _retire(storage, other, status)
        await _retire(storage, retired_fact, status)
        result, _ = await tools.apply_reflection(
            storage,
            embedding_provider,
            similarities=[
                {"pair": [one.id, other.id], "verdict": "one_claim", "because": "agree"},
                {"pair": [fact.id, retired_fact.id], "verdict": "one_claim", "because": "same"},
            ],
            relation_verdicts=[{"pair": ["rl-a", "rl-b"], "verdict": "distinct", "because": "x"}],
            judge=CRITIC,
        )

        inference_refusal, fact_refusal = result["similarities_refused"]
        [relation_refusal] = result["relation_verdicts_refused"]
        assert set(inference_refusal) == set(fact_refusal) == set(relation_refusal)
        assert set(inference_refusal) == {"pair", "reason"}
        assert inference_refusal["pair"] == [one.id, other.id]
        assert f"{other.id} is {status.value}" in inference_refusal["reason"]
        assert inference_refusal["reason"].replace(other.id, "<retired>") == fact_refusal[
            "reason"
        ].replace(retired_fact.id, "<retired>")

        assert result["similarities_recorded"] == 0
        for edge_type in (EdgeType.SIMILARITY, EdgeType.ASSESSED):
            assert await symmetric_edge_between(one.id, other.id, edge_type, storage) is None
        assert await storage.query_decisions(kinds=[DecisionKind.SIMILARITY]) == []

    async def test_a_side_retired_as_historical_is_still_judged(self, storage, embedding_provider):
        """Pinned, not chosen. This list nominates active inferences only, but
        the verdict path accepts anything `reflect` nominates on any list, and
        the recurrence sweep nominates historical claims. So a verdict on a pair
        whose other side became historical since is recorded, and `one_claim`
        writes its `similarity` edge to the historical inference."""
        one, other, _, _ = await _agreeing_pair(storage, embedding_provider)
        await _offered(storage, embedding_provider)

        await _retire(storage, other, NodeStatus.HISTORICAL)
        result, _ = await tools.apply_reflection(
            storage,
            embedding_provider,
            similarities=[{"pair": [one.id, other.id], "verdict": "one_claim", "because": "agree"}],
            judge=CRITIC,
        )

        assert result["similarities_refused"] == []
        assert result["similarities_recorded"] == 1
        assert await symmetric_edge_between(one.id, other.id, EdgeType.SIMILARITY, storage)


class TestCorroborationReportsTheAgreement:
    """Recording the agreement must not raise the number callers read as
    independent support, and must not hide the agreement either."""

    async def test_one_claim_leaves_both_counts_where_they_were(self, storage, embedding_provider):
        one, other, _, _ = await _agreeing_pair(storage, embedding_provider)
        before = await corroboration_for([one.id, other.id], storage)

        await tools.apply_reflection(
            storage,
            embedding_provider,
            similarities=[{"pair": [one.id, other.id], "verdict": "one_claim", "because": "agree"}],
            judge=CRITIC,
        )
        after = await corroboration_for([one.id, other.id], storage)

        assert {node: result.count for node, result in after.items()} == {
            node: result.count for node, result in before.items()
        }
        assert [agreeing.node_id for agreeing in after[one.id].agreeing_inferences] == [other.id]
        assert [agreeing.node_id for agreeing in after[other.id].agreeing_inferences] == [one.id]
        assert after[one.id].agreeing_inferences[0].content_preview == other.content
