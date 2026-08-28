"""`reflect` must bound the lists that grow with the square of the graph.

Four of `reflect`'s ten nominee lists are pair lists — topic consolidation,
contradictions, recurrences, relation consolidation — and pairs are quadratic in
the node set while every other list is linear in it. Nothing bounded them: no
limit parameter, no top-k, no size check anywhere on the path, and every
survivor went into the response (#60).

**What this bounds is the response, not the peak allocation.** The scored tuples
upstream in `similar_pairs` are still one per surviving pair; what the cap
removes is the response dicts built from them and the unbounded JSON handed to
the caller. That ordering is deliberate — the measurement that demoted this
issue from urgent (0.0105% real survival, ~3 MB at 10,000 facts, not the ~14 GB
projected) also moved the argument from memory to the response, and a cap that
claimed to bound allocation would be claiming something it does not deliver.

The assertions are on counts the response carries, never on wall-clock or bytes,
for the reason `test_reflect_scaling.py` gives at length: a duration in the suite
is a flake, and the measurement belongs in `make bench`.
"""

import math

import pytest

from epimemer.core.types import (
    BASE_METACONTEXT_ID,
    EdgeType,
    EmbeddingRecord,
    Fact,
    NodeEdge,
    NodeStatus,
    Topic,
)
from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.mcp.tools import CAPPED_KEYS, MAX_NOMINATIONS, reflect


@pytest.fixture
def embedding_provider() -> MockEmbeddingProvider:
    return MockEmbeddingProvider(model_id="mock-embed", dimension=8)


def _fanned_vector(index: int, step: float = 0.02) -> list[float]:
    """A unit vector at `index * step` radians in the first two dimensions.

    Similarity between two of these is `cos((i - j) * step)`, so it falls off
    with index distance. That is what lets a test say *which* pairs a top-k kept
    rather than only how many — with one shared vector every pair ties at 1.0
    and any k survivors are as correct as any other.
    """
    angle = index * step
    return [math.cos(angle), math.sin(angle), 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]


async def _fanned_facts(storage, provider, count: int) -> list[Fact]:
    """Facts whose every pair clears 0.80, with scores graded by index distance.

    At 0.02 radians a 30-fact fan spans 0.58 rad, so the *widest* pair still
    scores 0.836 — above the contradiction threshold — and the closest score
    0.9998. Every pair is a candidate and they are strictly ordered.
    """
    facts = []
    for i in range(count):
        fact = Fact(content=f"Claim number {i}", source_id="s1")
        await storage.store_node(fact)
        # Every node states a frame, as every ingested one has since #76:
        # absence names none, so two frameless nodes share none and the
        # contradiction sweep would skip every pair here.
        await storage.store_edge(NodeEdge(
            src_id=fact.id, dst_id=BASE_METACONTEXT_ID,
            type=EdgeType.HAS_METACONTEXT,
        ))
        await storage.store_embedding(EmbeddingRecord(
            item_id=fact.id, model_id=provider.model_id, vector=_fanned_vector(i),
        ))
        facts.append(fact)
    return facts


async def _identical_topics(storage, provider, count: int) -> list[Topic]:
    """Topics sharing one vector, so every pair clears any threshold."""
    vector = (await provider.embed(["shared"]))[0]
    topics = []
    for i in range(count):
        topic = Topic(content=f"Subject number {i}", source_id="s1")
        await storage.store_node(topic)
        # Every node states a frame, as every ingested one has since #76:
        # absence names none, so two frameless nodes share none and the
        # contradiction sweep would skip every pair here.
        await storage.store_edge(NodeEdge(
            src_id=topic.id, dst_id=BASE_METACONTEXT_ID,
            type=EdgeType.HAS_METACONTEXT,
        ))
        await storage.store_embedding(EmbeddingRecord(
            item_id=topic.id, model_id=provider.model_id, vector=vector,
        ))
        topics.append(topic)
    return topics


class TestThePairListsAreBounded:
    """The defect stated as an assertion: 30 facts are 435 pairs, and every one
    of them was returned."""

    async def test_contradictions_stop_at_the_cap(self, storage, embedding_provider):
        await _fanned_facts(storage, embedding_provider, 30)

        result, _ = await reflect(
            storage, embedding_provider, max_nominations=10
        )

        assert len(result["contradictions"]) == 10

    async def test_topic_pairs_stop_at_the_cap(self, storage, embedding_provider):
        await _identical_topics(storage, embedding_provider, 30)

        result, _ = await reflect(
            storage, embedding_provider, max_nominations=10
        )

        assert len(result["similar_pairs"]) == 10

    async def test_the_cap_holds_as_the_graph_grows(self, storage, embedding_provider):
        """Doubling the facts quadruples the pairs. The response must not move."""
        await _fanned_facts(storage, embedding_provider, 15)
        small, _ = await reflect(storage, embedding_provider, max_nominations=10)

        await _fanned_facts(storage, embedding_provider, 15)
        large, _ = await reflect(storage, embedding_provider, max_nominations=10)

        assert len(small["contradictions"]) == len(large["contradictions"]) == 10


class TestItSaysWhenItCut:
    """A silently shortened list is worse than a long one — the caller cannot
    tell an exhausted graph from a truncated answer."""

    async def test_a_truncated_list_is_named(self, storage, embedding_provider):
        await _fanned_facts(storage, embedding_provider, 30)

        result, _ = await reflect(
            storage, embedding_provider, max_nominations=10
        )

        assert result["truncated"] == ["contradictions"]

    async def test_an_untruncated_response_names_nothing(
        self, storage, embedding_provider
    ):
        await _fanned_facts(storage, embedding_provider, 3)

        result, _ = await reflect(storage, embedding_provider)

        assert result["truncated"] == []

    async def test_a_list_exactly_at_the_cap_is_not_truncated(
        self, storage, embedding_provider
    ):
        """Off-by-one in the direction that lies: reporting a cut that did not
        happen sends a caller looking for nominees that do not exist."""
        await _fanned_facts(storage, embedding_provider, 3)  # exactly 3 pairs

        result, _ = await reflect(storage, embedding_provider, max_nominations=3)

        assert len(result["contradictions"]) == 3
        assert result["truncated"] == []

    async def test_every_capped_key_is_a_key_of_the_response(
        self, storage, embedding_provider
    ):
        """`CAPPED_KEYS` naming a list that no longer exists would cap nothing
        and say nothing, silently."""
        result, _ = await reflect(storage, embedding_provider)

        assert set(CAPPED_KEYS) <= set(result)


class TestTheSurvivorsAreTheHighestScoring:
    """Top-k, not first-k. A cap that kept an arbitrary slice would hand the
    agent the weakest candidates as readily as the strongest."""

    async def test_the_kept_pairs_are_the_closest_ones(
        self, storage, embedding_provider
    ):
        await _fanned_facts(storage, embedding_provider, 30)

        result, _ = await reflect(
            storage, embedding_provider, max_nominations=10
        )

        kept = [pair["similarity"] for pair in result["contradictions"]]
        assert kept == sorted(kept, reverse=True), "the response is not score-ordered"
        # 29 adjacent pairs score 0.9998; anything further apart scores less. A
        # top-10 that reached past them took a weaker pair over a stronger one.
        assert min(kept) == pytest.approx(0.9998, abs=1e-4)


class TestRecurrencesAreCappedOnTheirOwn:
    """Contradictions and recurrences are partitioned out of one scored set, so
    a cap applied before the split would let the larger half starve the other —
    and recurrence is the safety net under an opt-in detector (#53 T2)."""

    async def test_a_recurrence_survives_a_full_contradiction_list(
        self, storage, embedding_provider
    ):
        from datetime import datetime, timezone

        facts = await _fanned_facts(storage, embedding_provider, 30)
        await storage.set_node_status_tx(
            [facts[0]], status=NodeStatus.HISTORICAL, at=datetime.now(timezone.utc)
        )

        result, _ = await reflect(
            storage, embedding_provider, max_nominations=5
        )

        assert len(result["contradictions"]) == 5
        assert result["recurrences"], (
            "the contradiction list filled the cap and the recurrences vanished "
            "with it; each list is capped after the partition, not before"
        )


class TestTheDefaultDoesNotFireOnAnOrdinaryGraph:
    """The cap is insurance, not a working limit. The measured real-corpus rate
    is 0.0105% — 4 surviving pairs out of 38,226 — so a default that trimmed a
    normal graph would be losing nominees to guard against nothing."""

    async def test_a_small_graph_is_returned_whole(self, storage, embedding_provider):
        await _fanned_facts(storage, embedding_provider, 12)

        result, _ = await reflect(storage, embedding_provider)

        assert len(result["contradictions"]) == 66  # 12 facts, every pair
        assert result["truncated"] == []

    def test_the_default_sits_above_what_a_real_graph_returns(self):
        assert MAX_NOMINATIONS >= 100


class TestTheMetaCountsWhatWasReturned:

    async def test_nodes_returned_follows_the_capped_lists(
        self, storage, embedding_provider
    ):
        """`retrieved` is what the response carried, never what reflect looked
        at — the rule §2 already states for this tool."""
        await _fanned_facts(storage, embedding_provider, 30)

        result, meta = await reflect(
            storage, embedding_provider, max_nominations=10
        )

        assert meta.nodes_returned == sum(
            len(value)
            for key, value in result.items()
            # Neither is a nominee list: `truncated` names cut lists, and
            # `relation_pairs_suppressed` counts pairs standing verdicts held
            # out of similar_relations.
            if key not in ("truncated", "relation_pairs_suppressed")
        )
