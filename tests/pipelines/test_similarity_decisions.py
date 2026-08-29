"""The verdict that had no writer (`REVIEW_MODE.md` §1, the `assessed` edge).

`reflect` nominated eighteen pairs on `memory` on 2026-08-21. Five merged.
**Thirteen were declined and vanished** — and came back on the next pass, and
the one after, because `already_linked` was built from `SIMILARITY ∪
CONTRADICTION` and both graphs carried zero of either.

The whole design is in the split these tests keep pinned: a decline is **two
populations**, and one edge cannot serve both readers. The nomination sweep
wants every judged pair suppressed; `corroboration.py` wants only restatements
of one claim. Write one edge for both and *"these two are different claims"*
starts corroborating — manufactured support, which is the failure this system
treats as its worst, because a false unification does not lose information, it
inverts the quantity corroboration measures.

So the two properties that matter are asserted against each other, everywhere
below: **both verdicts suppress; only `one_claim` corroborates.**
"""

import pytest

from epimemer.core.types import (
    BASE_METACONTEXT_ID,
    ClaimKind,
    EdgeType,
    EmbeddingRecord,
    Fact,
    Metacontext,
    NodeEdge,
    NodeStatus,
    NodeType,
    RawDocument,
    Topic,
    ValueSignal,
)
from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.mcp import tools
from epimemer.pipelines.query.corroboration import corroboration_for
from epimemer.pipelines.reflection.contradiction_detection import detect_contradictions
from epimemer.pipelines.reflection.similarity_decisions import (
    SimilarityRecorded,
    SimilarityRefused,
    apply_similarity_decision,
)
from epimemer.pipelines.reflection.topic_consolidation import find_similar_topic_pairs


@pytest.fixture
def embedding_provider() -> MockEmbeddingProvider:
    return MockEmbeddingProvider(model_id="mock-embed", dimension=8)


# Supplied rather than derived from the text, for the reason `test_fact_dedup`
# gives: the nomination bar and the verdict have to vary independently, or a
# test about what a verdict records is also a test of how alike two sentences
# happen to hash.
_TWIN = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]


async def _publisher(storage, name: str) -> Topic:
    """Resolve-or-create by name, as ingest's `_upsert_entity_topic` does."""
    existing = await storage.get_node_by_content(name, node_type=NodeType.TOPIC)
    if isinstance(existing, Topic):
        return existing
    entity = Topic(content=name, source_id=None, extraction_method="agent:source")
    await storage.store_node(entity)
    return entity


async def _fact(
    storage,
    embedding_provider,
    content: str,
    *,
    publisher: str | None = None,
    status: NodeStatus = NodeStatus.ACTIVE,
    vector: list[float] | None = None,
) -> Fact:
    """One stored, embedded fact, optionally sourced to a published document.

    The publisher rides on a `published_by` attribution edge from the document,
    exactly as `segment` writes it — corroboration counts publishers, and a test
    that invented its own shape would pass against a walk that never sees a real
    one.
    """
    fact = Fact(
        content=content,
        source_id="seg-1",
        claim_kind=ClaimKind.STATE,
        status=status,
        value=ValueSignal(),
    )
    await storage.store_node(fact)
    # Every fact states a frame, as every ingested one has since the frame requirement — absence
    # names no frame, so two frameless facts share none and nothing here would
    # be nominated at all.
    await storage.store_edge(NodeEdge(
        src_id=fact.id, dst_id=BASE_METACONTEXT_ID,
        type=EdgeType.HAS_METACONTEXT,
    ))
    await storage.store_embedding(EmbeddingRecord(
        item_id=fact.id,
        model_id=embedding_provider.model_id,
        vector=vector or _TWIN,
    ))
    if publisher is not None:
        document = RawDocument(content=f"the text from {publisher}", source=publisher)
        await storage.store_document(document)
        entity = await _publisher(storage, publisher)
        await storage.store_edge(NodeEdge(
            src_id=document.id, dst_id=entity.id, type=EdgeType.RELATED,
            label="published_by", kind="attribution",
        ))
        await storage.store_edge(NodeEdge(
            src_id=fact.id, dst_id=document.id, type=EdgeType.SOURCED_FROM,
        ))
    return fact


async def _framed(storage, fact: Fact, label: str) -> str:
    """Move a fact into its own frame, replacing the one `_fact` gave it.

    Replacing rather than adding, because these tests are about a pair standing
    in *disjoint* frames: leaving the base frame on both would give them an
    overlap, and `same_frame` asks about overlap.
    """
    frame = Metacontext(content=label)
    await storage.store_metacontext(frame)
    for edge in await storage.get_edges_from(
        fact.id, edge_type=EdgeType.HAS_METACONTEXT
    ):
        await storage.delete_edge(edge.id)
    await storage.store_edge(NodeEdge(
        src_id=fact.id, dst_id=frame.id, type=EdgeType.HAS_METACONTEXT,
    ))
    return frame.id


async def _decide(storage, a: Fact, b: Fact, verdict: str, because: str = "judged"):
    return await apply_similarity_decision(
        storage, a_id=a.id, b_id=b.id, verdict=verdict, because=because
    )


async def _edge_types_between(storage, a: Fact, b: Fact) -> set[str]:
    """Every edge type joining a and b, in either direction."""
    found = set()
    for src, dst in ((a.id, b.id), (b.id, a.id)):
        for edge in await storage.get_edges_from(src):
            if edge.dst_id == dst:
                found.add(edge.type.value)
    return found


async def _count(storage, fact: Fact) -> int:
    return (await corroboration_for([fact.id], storage))[fact.id].count


class TestBothVerdictsSuppressAndOnlyOneCorroborates:
    """The split, stated four times, because it is the entire design.

    Every other test in this file is a boundary case around these four.
    """

    async def test_one_claim_writes_similarity_and_assessed(
        self, storage, embedding_provider
    ):
        a = await _fact(storage, embedding_provider, "the deploy failed", publisher="BBC")
        b = await _fact(
            storage, embedding_provider, "the deployment failed", publisher="Reuters"
        )

        outcome = await _decide(storage, a, b, "one_claim", "same claim; both events")

        assert isinstance(outcome, SimilarityRecorded)
        assert outcome.edges_created == 2
        assert await _edge_types_between(storage, a, b) == {"similarity", "assessed"}

    async def test_distinct_writes_assessed_only(self, storage, embedding_provider):
        """The load-bearing half. A `similarity` edge here would have the graph
        counting *"these are different claims"* as a second source."""
        a = await _fact(storage, embedding_provider, "the deploy failed", publisher="BBC")
        b = await _fact(
            storage, embedding_provider, "the rollback failed", publisher="Reuters"
        )

        outcome = await _decide(storage, a, b, "distinct", "different runs")

        assert isinstance(outcome, SimilarityRecorded)
        assert outcome.edges_created == 1
        assert await _edge_types_between(storage, a, b) == {"assessed"}

    async def test_one_claim_raises_corroboration_from_one_to_two(
        self, storage, embedding_provider
    ):
        """Two publishers saying one thing is two independent witnesses — the
        number `merge_facts` refusing an event would otherwise have cost."""
        a = await _fact(storage, embedding_provider, "the deploy failed", publisher="BBC")
        b = await _fact(
            storage, embedding_provider, "the deployment failed", publisher="Reuters"
        )
        assert await _count(storage, a) == 1

        await _decide(storage, a, b, "one_claim")

        assert await _count(storage, a) == 2
        assert await _count(storage, b) == 2

    async def test_distinct_leaves_corroboration_alone(
        self, storage, embedding_provider
    ):
        a = await _fact(storage, embedding_provider, "the deploy failed", publisher="BBC")
        b = await _fact(
            storage, embedding_provider, "the rollback failed", publisher="Reuters"
        )

        await _decide(storage, a, b, "distinct")

        assert await _count(storage, a) == 1
        assert await _count(storage, b) == 1


class TestThePairStopsComingBack:
    """The presenting symptom: thirteen declined pairs, re-offered forever."""

    async def _nominations(self, storage, embedding_provider):
        return await detect_contradictions(
            storage, embedding_provider, model_id=embedding_provider.model_id
        )

    async def test_an_unjudged_pair_is_nominated(self, storage, embedding_provider):
        """The control. Without it the two below prove only that something
        broke nomination generally."""
        a = await _fact(storage, embedding_provider, "the deploy failed")
        b = await _fact(storage, embedding_provider, "the deployment failed")

        assert len(await self._nominations(storage, embedding_provider)) == 1

    @pytest.mark.parametrize("verdict", ["one_claim", "distinct"])
    async def test_a_judged_pair_is_not(self, storage, embedding_provider, verdict):
        a = await _fact(storage, embedding_provider, "the deploy failed")
        b = await _fact(storage, embedding_provider, "the deployment failed")

        await _decide(storage, a, b, verdict)

        assert await self._nominations(storage, embedding_provider) == []

    async def test_a_third_unjudged_fact_is_still_nominated(
        self, storage, embedding_provider
    ):
        """Suppression is per pair, not per node. A node that has been judged
        once must not go quiet about everything else it resembles."""
        a = await _fact(storage, embedding_provider, "the deploy failed")
        b = await _fact(storage, embedding_provider, "the deployment failed")
        c = await _fact(storage, embedding_provider, "the release failed")

        await _decide(storage, a, b, "distinct")

        pairs = await self._nominations(storage, embedding_provider)
        assert {frozenset({x.id, y.id}) for x, y, _ in pairs} == {
            frozenset({a.id, c.id}), frozenset({b.id, c.id})
        }

    async def test_a_variant_pair_was_already_suppressed_and_still_is(
        self, storage, embedding_provider
    ):
        """`variant_of` joined the suppression set in the same change. It has
        always been a judgment about a pair; it simply was not read as one."""
        a = await _fact(storage, embedding_provider, "the ring was destroyed")
        b = await _fact(storage, embedding_provider, "the ring was destroyed")
        await storage.store_edge(NodeEdge(
            src_id=a.id, dst_id=b.id, type=EdgeType.VARIANT_OF,
        ))

        assert await self._nominations(storage, embedding_provider) == []


class TestAJudgmentIsAnchoredToTheWordingItWasMadeAgainst:
    """The anchoring rule, extended to the edge the `assessed` edge invents.

    `assessed` is in `REVIEW_EDGE_TYPES`, which is consulted before the status
    branch, so this holds on every retirement rather than on a chosen few.
    """

    @pytest.mark.parametrize("edge_type", [EdgeType.ASSESSED, EdgeType.SIMILARITY])
    def test_it_never_migrates_whatever_the_retirement(self, edge_type):
        from epimemer.core.types import migration_disposition

        for status in (NodeStatus.CORRECTED, NodeStatus.HISTORICAL, NodeStatus.MERGED):
            assert migration_disposition(edge_type, status) == "keep"

    def test_assessed_is_not_traversed_as_knowledge(self):
        """The property `similarity` does *not* share. A suppression index is
        not a relationship to follow out of a node during retrieval."""
        from epimemer.core.types import traversal_excluded

        assert traversal_excluded(
            NodeEdge(src_id="a", dst_id="b", type=EdgeType.ASSESSED)
        )
        assert not traversal_excluded(
            NodeEdge(src_id="a", dst_id="b", type=EdgeType.SIMILARITY)
        )

    async def test_it_is_not_counted_as_structural_support(
        self, storage, embedding_provider
    ):
        """Archival reads in-degree as *what depends on this*. Somebody having
        looked at a pair is not something depending on either node."""
        from epimemer.pipelines.reflection.archival import knowledge_in_degree_for

        a = await _fact(storage, embedding_provider, "the deploy failed")
        b = await _fact(storage, embedding_provider, "the rollback failed")
        await _decide(storage, a, b, "distinct")

        degrees = await knowledge_in_degree_for([a.id, b.id], storage)
        assert degrees[b.id] == 0


class TestWhatIsRefusedRatherThanGuessedAt:
    """Refusals carry a reason and come back in the response. A decision
    silently not recorded is the whole of the `assessed` edge, so this module does not have the
    option of failing that way."""

    async def test_an_unknown_verdict_is_reported_not_defaulted(
        self, storage, embedding_provider
    ):
        """Defaulting would pick one of two writes that differ in exactly the
        way this module exists to keep apart, for an agent that said neither."""
        a = await _fact(storage, embedding_provider, "the deploy failed")
        b = await _fact(storage, embedding_provider, "the rollback failed")

        outcome = await _decide(storage, a, b, "related")

        assert isinstance(outcome, SimilarityRefused)
        assert "one_claim" in outcome.reason and "distinct" in outcome.reason
        assert await _edge_types_between(storage, a, b) == set()

    async def test_a_missing_because_is_refused(self, storage, embedding_provider):
        a = await _fact(storage, embedding_provider, "the deploy failed")
        b = await _fact(storage, embedding_provider, "the rollback failed")

        outcome = await _decide(storage, a, b, "distinct", because="   ")

        assert isinstance(outcome, SimilarityRefused)
        assert "because" in outcome.reason
        assert await _edge_types_between(storage, a, b) == set()

    async def test_a_node_paired_with_itself_is_refused(
        self, storage, embedding_provider
    ):
        a = await _fact(storage, embedding_provider, "the deploy failed")

        outcome = await apply_similarity_decision(
            storage, a_id=a.id, b_id=a.id, verdict="one_claim", because="x"
        )

        assert isinstance(outcome, SimilarityRefused)

    async def test_an_unknown_id_is_refused_by_name(self, storage, embedding_provider):
        a = await _fact(storage, embedding_provider, "the deploy failed")

        outcome = await apply_similarity_decision(
            storage, a_id=a.id, b_id="fact-gone", verdict="distinct", because="x"
        )

        assert isinstance(outcome, SimilarityRefused)
        assert "fact-gone" in outcome.reason


class TestWhichNodesMayCarryAJudgment:
    """`NOMINATED_STATUSES`, and the reasoning is one step long: an `assessed`
    edge earns its place by suppressing a nomination, so it belongs exactly
    where a nomination could have happened."""

    async def test_a_historical_side_is_accepted(self, storage, embedding_provider):
        """The recurrence sweep nominates active/historical pairs. Refusing them
        would leave half the treadmill running — and this is the half where the
        graph is offering a claim beside its own predecessor."""
        a = await _fact(storage, embedding_provider, "the city is Saint Petersburg")
        b = await _fact(
            storage, embedding_provider, "the city is Leningrad",
            status=NodeStatus.HISTORICAL,
        )

        outcome = await _decide(storage, a, b, "distinct", "different periods")

        assert isinstance(outcome, SimilarityRecorded)

    @pytest.mark.parametrize(
        "status", [NodeStatus.CORRECTED, NodeStatus.ARCHIVED, NodeStatus.MERGED]
    )
    async def test_anything_never_nominated_is_refused(
        self, storage, embedding_provider, status
    ):
        """A judgment recorded here suppresses nothing that could have been
        offered — and a `similarity` edge would still be counted as support."""
        a = await _fact(storage, embedding_provider, "the deploy failed")
        b = await _fact(
            storage, embedding_provider, "the deployment failed", status=status
        )

        outcome = await _decide(storage, a, b, "distinct")

        assert isinstance(outcome, SimilarityRefused)
        assert status.value in outcome.reason


class TestTheCrossFrameCase:
    """A fiction and a fact are never one claim, and `variant_of` is the
    relation that says so. Corroboration disqualifies partners carrying one —
    which is the difference between the two being kept apart and them being kept
    apart *and counted*."""

    async def test_one_claim_across_frames_is_refused(
        self, storage, embedding_provider
    ):
        a = await _fact(storage, embedding_provider, "the ring was destroyed")
        b = await _fact(storage, embedding_provider, "the ring was destroyed")
        await _framed(storage, a, "Tolkien")
        await _framed(storage, b, "Wagner")

        outcome = await _decide(storage, a, b, "one_claim")

        assert isinstance(outcome, SimilarityRefused)
        assert "record_variant" in outcome.reason
        assert await _edge_types_between(storage, a, b) == set()

    async def test_distinct_across_frames_is_recorded(
        self, storage, embedding_provider
    ):
        """`assessed` corroborates nothing, so there is no reason to make the
        agent choose between recording its judgment and being accurate."""
        a = await _fact(storage, embedding_provider, "the ring was destroyed")
        b = await _fact(storage, embedding_provider, "the ring was destroyed")
        await _framed(storage, a, "Tolkien")
        await _framed(storage, b, "Wagner")

        outcome = await _decide(storage, a, b, "distinct", "different legendaria")

        assert isinstance(outcome, SimilarityRecorded)
        assert await _edge_types_between(storage, a, b) == {"assessed"}


class TestReplayAndReversal:
    """What a second call does — a retried batch, and an agent changing its mind.

    The second used to be refused: nothing could unmake a `one_claim`, and the
    honest refusal was better than writing `assessed` beside a `similarity` edge
    that went on corroborating a pair the agent had disowned. the `one_claim` retraction gave it a
    writer, and the direction it can travel is deliberately one-way.
    """

    async def test_replaying_a_decision_writes_nothing_new(
        self, storage, embedding_provider
    ):
        a = await _fact(storage, embedding_provider, "the deploy failed", publisher="BBC")
        b = await _fact(
            storage, embedding_provider, "the deployment failed", publisher="Reuters"
        )
        await _decide(storage, a, b, "one_claim")

        again = await _decide(storage, a, b, "one_claim")

        assert isinstance(again, SimilarityRecorded)
        assert again.edges_created == 0
        assert await _count(storage, a) == 2      # not 3: one edge, not two

    async def test_distinct_after_one_claim_withdraws_it(
        self, storage, embedding_provider
    ):
        """Nothing in this system deletes, this call included — so the
        `similarity` edge stays and a second edge stops it counting. The
        same shape `contradiction` already has, one judgment along."""
        a = await _fact(storage, embedding_provider, "the deploy failed", publisher="BBC")
        b = await _fact(
            storage, embedding_provider, "the deployment failed", publisher="Reuters"
        )
        await _decide(storage, a, b, "one_claim")

        outcome = await _decide(storage, a, b, "distinct", "on reflection, different")

        assert isinstance(outcome, SimilarityRecorded)
        assert outcome.retracted is True
        assert await _edge_types_between(storage, a, b) == {
            "similarity", "assessed", "retracted_similarity",
        }

    async def test_the_count_comes_back_to_where_it_started(
        self, storage, embedding_provider
    ):
        """The number is the point. A verdict that could not be walked back left
        a count nobody could explain and nothing could correct."""
        a = await _fact(storage, embedding_provider, "the deploy failed", publisher="BBC")
        b = await _fact(
            storage, embedding_provider, "the deployment failed", publisher="Reuters"
        )
        assert await _count(storage, a) == 1

        await _decide(storage, a, b, "one_claim")
        assert await _count(storage, a) == 2

        await _decide(storage, a, b, "distinct", "on reflection, different")

        assert await _count(storage, a) == 1

    async def test_a_withdrawal_leaves_the_pair_suppressed(
        self, storage, embedding_provider
    ):
        """A retraction changes what corroboration counts and nothing else. The
        agent has now judged this pair twice; re-offering it would restart the
        treadmill the `assessed` edge closed."""
        a = await _fact(storage, embedding_provider, "the deploy failed", publisher="BBC")
        b = await _fact(
            storage, embedding_provider, "the deployment failed", publisher="Reuters"
        )
        await _decide(storage, a, b, "one_claim")
        await _decide(storage, a, b, "distinct", "on reflection, different")

        assert "assessed" in await _edge_types_between(storage, a, b)

    async def test_repeating_a_withdrawal_decides_nothing_new(
        self, storage, embedding_provider
    ):
        """Otherwise a retried batch journals a second withdrawal, and the
        record reads as two agents disowning the pair on separate occasions."""
        a = await _fact(storage, embedding_provider, "the deploy failed", publisher="BBC")
        b = await _fact(
            storage, embedding_provider, "the deployment failed", publisher="Reuters"
        )
        await _decide(storage, a, b, "one_claim")
        await _decide(storage, a, b, "distinct", "on reflection, different")

        again = await _decide(storage, a, b, "distinct", "still different")

        assert isinstance(again, SimilarityRecorded)
        assert again.edges_created == 0
        assert again.retracted is False

    async def test_a_withdrawal_is_final(self, storage, embedding_provider):
        """The one-way street, and the asymmetry is the design. Withdrawing a
        `one_claim` costs a count the graph will no longer make; re-asserting
        one over a withdrawal **invents agreement**, which does not lose
        information — it inverts the quantity corroboration measures."""
        a = await _fact(storage, embedding_provider, "the deploy failed", publisher="BBC")
        b = await _fact(
            storage, embedding_provider, "the deployment failed", publisher="Reuters"
        )
        await _decide(storage, a, b, "one_claim")
        await _decide(storage, a, b, "distinct", "on reflection, different")

        outcome = await _decide(storage, a, b, "one_claim", "no, the same after all")

        assert isinstance(outcome, SimilarityRefused)
        assert "was withdrawn" in outcome.reason
        assert "merge_facts" in outcome.reason
        assert await _count(storage, a) == 1

    async def test_one_claim_after_distinct_upgrades_the_pair(
        self, storage, embedding_provider
    ):
        """The direction that *is* additive: `assessed` already says the pair
        was judged, and `similarity` is added beside it. Append-only, so the
        two records never disagree about what happened, only about when."""
        a = await _fact(storage, embedding_provider, "the deploy failed", publisher="BBC")
        b = await _fact(
            storage, embedding_provider, "the deployment failed", publisher="Reuters"
        )
        await _decide(storage, a, b, "distinct")

        outcome = await _decide(storage, a, b, "one_claim", "they are the same run")

        assert isinstance(outcome, SimilarityRecorded)
        assert outcome.edges_created == 1
        assert await _edge_types_between(storage, a, b) == {"similarity", "assessed"}


class TestTheEdgeCarriesWhyItWasWritten:
    """Until the decision journal lands (step 5) this is the only record of the
    reasoning, and it is immutable, so §3.4 permits the denormalisation."""

    async def test_the_verdict_and_reason_ride_on_the_edge(
        self, storage, embedding_provider
    ):
        a = await _fact(storage, embedding_provider, "the deploy failed")
        b = await _fact(storage, embedding_provider, "the rollback failed")

        await _decide(storage, a, b, "distinct", "different runs of the pipeline")

        edges = await storage.get_edges_from(a.id, edge_type=EdgeType.ASSESSED)
        assert edges[0].metadata == {
            "verdict": "distinct", "because": "different runs of the pipeline"
        }


class TestTheToolSurface:
    """`apply_reflection` is where the agent already is when it makes these
    judgments — declining becomes an outcome in the same batch rather than a
    separate errand nobody runs."""

    async def test_a_batch_records_and_reports(self, storage, embedding_provider):
        a = await _fact(storage, embedding_provider, "the deploy failed", publisher="BBC")
        b = await _fact(
            storage, embedding_provider, "the deployment failed", publisher="Reuters"
        )
        c = await _fact(storage, embedding_provider, "unrelated")

        result, meta = await tools.apply_reflection(
            storage, embedding_provider,
            similarities=[
                {"pair": [a.id, b.id], "verdict": "one_claim", "because": "same run"},
                {"pair": [a.id, c.id], "verdict": "sort_of", "because": "hmm"},
            ],
        )

        assert result["similarities_recorded"] == 1
        assert result["similarity_edges_written"] == 2
        assert len(result["similarities_refused"]) == 1
        assert result["similarities_refused"][0]["pair"] == [a.id, c.id]
        assert meta.nodes_returned == 1

    async def test_a_refusal_does_not_stop_the_batch(self, storage, embedding_provider):
        """Partial application, as every other argument to this tool does it."""
        a = await _fact(storage, embedding_provider, "the deploy failed")
        b = await _fact(storage, embedding_provider, "the rollback failed")

        result, _ = await tools.apply_reflection(
            storage, embedding_provider,
            similarities=[
                {"pair": [a.id, "nope"], "verdict": "distinct", "because": "x"},
                {"pair": [a.id, b.id], "verdict": "distinct", "because": "different"},
            ],
        )

        assert result["similarities_recorded"] == 1
        assert await _edge_types_between(storage, a, b) == {"assessed"}

    async def test_judgments_are_recorded_before_the_batch_retires_anything(
        self, storage, embedding_provider
    ):
        """The anchoring rule, applied to the order of one call. A judgment is
        about the wording it was made against; a supersession later in the same
        batch would otherwise turn it into a skip."""
        a = await _fact(storage, embedding_provider, "the deploy failed")
        b = await _fact(storage, embedding_provider, "the rollback failed")
        winner = await _fact(storage, embedding_provider, "the deploy was cancelled")

        result, _ = await tools.apply_reflection(
            storage, embedding_provider,
            similarities=[
                {"pair": [a.id, b.id], "verdict": "distinct", "because": "different"},
            ],
            supersessions=[
                {"old_id": a.id, "by_id": winner.id, "because": "it_was_wrong"},
            ],
        )

        assert result["similarities_recorded"] == 1
        assert result["supersessions_applied"] == 1
        assert await _edge_types_between(storage, a, b) == {"assessed"}
        # And the judgment stays on the wording that was judged, rather than
        # arriving on a winner nobody assessed against b.
        assert await _edge_types_between(storage, winner, b) == set()


async def _topic(storage, embedding_provider, content: str,
                 *, vector: list[float] | None = None) -> Topic:
    """One stored, embedded topic. Vector supplied for the reason `_fact` gives."""
    topic = Topic(content=content, source_id="seg-1", value=ValueSignal())
    await storage.store_node(topic)
    await storage.store_edge(NodeEdge(
        src_id=topic.id, dst_id=BASE_METACONTEXT_ID, type=EdgeType.HAS_METACONTEXT,
    ))
    await storage.store_embedding(EmbeddingRecord(
        item_id=topic.id, model_id=embedding_provider.model_id,
        vector=vector or _TWIN,
    ))
    return topic


class TestATopicPairStopsComingBackToo:
    """The same defect, one sweep along, and it survived the original fix.

    `apply_reflection(similarities=…)` has always accepted a topic pair, written
    the `assessed` edge and reported it recorded. The topic sweep never read it,
    so the pair returned on every reflect regardless — for ever, to an agent who
    could not see it had already been answered.

    **Worse than the fact-pair case it mirrors**, because the call reported
    success: a verdict recorded nowhere and a verdict recorded where nothing
    reads are indistinguishable from the caller, except that the second one
    says it worked. Found 2026-08-29 by judging three topic pairs on `memory`
    and watching all three come back on the next pass.
    """

    async def _nominations(self, storage, embedding_provider):
        return await find_similar_topic_pairs(
            storage, embedding_provider, model_id=embedding_provider.model_id,
            similarity_threshold=0.85,
        )

    async def test_an_unjudged_topic_pair_is_nominated(
        self, storage, embedding_provider
    ):
        """The control, for the reason the fact-pair control exists."""
        await _topic(storage, embedding_provider, "design-decisions")
        await _topic(storage, embedding_provider, "design decisions")

        assert len(await self._nominations(storage, embedding_provider)) == 1

    @pytest.mark.parametrize("verdict", ["one_claim", "distinct"])
    async def test_a_judged_topic_pair_is_not(
        self, storage, embedding_provider, verdict
    ):
        a = await _topic(storage, embedding_provider, "design-decisions")
        b = await _topic(storage, embedding_provider, "design decisions")

        await apply_similarity_decision(
            storage, a_id=a.id, b_id=b.id, verdict=verdict, because="judged"
        )

        assert await self._nominations(storage, embedding_provider) == []

    async def test_a_third_unjudged_topic_is_still_nominated(
        self, storage, embedding_provider
    ):
        """Suppression is per pair here as well. A tag judged against one
        spelling must not go quiet about every other topic it resembles."""
        a = await _topic(storage, embedding_provider, "design-decisions")
        b = await _topic(storage, embedding_provider, "design decisions")
        c = await _topic(storage, embedding_provider, "design notes")

        await apply_similarity_decision(
            storage, a_id=a.id, b_id=b.id, verdict="distinct", because="judged"
        )

        pairs = await self._nominations(storage, embedding_provider)
        assert {frozenset({x.id, y.id}) for x, y, _ in pairs} == {
            frozenset({a.id, c.id}), frozenset({b.id, c.id})
        }


def _reflection_sources() -> dict[str, str]:
    """Every module in the reflection package, by name."""
    from pathlib import Path

    import epimemer.pipelines.reflection as package

    return {
        path.stem: path.read_text()
        for path in Path(package.__file__).parent.glob("*.py")
        if path.stem != "__init__"
    }


def _pair_nominating_sweeps() -> dict[str, str]:
    """The sweeps that score pairs, **derived rather than listed**.

    A sweep asks `pair_scoring` for every pair over a bar; that is what makes it
    a sweep, and it is the one thing a new one cannot avoid doing — the module
    exists because several reflect phases ask the same question. Deriving the
    set is the whole value of the guard below: a hand-kept tuple of the three
    that exist today would never contain the fourth, so the test would pass by
    never looking at it.

    That is not hypothetical. This guard was first written with exactly such a
    tuple, and a reviewer pointed out that its stated purpose — catching a
    sweep written later — was the one thing it could not do.
    """
    return {
        name: source for name, source in _reflection_sources().items()
        if "pair_scoring" in source
    }


class TestEverySweepReadsTheOneSuppression:
    """The guard on the omission rather than on this instance of it.

    Three sweeps nominate pairs and each used to decide for itself what counted
    as judged. Two of the three read the suppression; the third had never been
    given it, and nothing said so — which is how it went unnoticed through the
    change that added the second reader.

    **The behavioural tests above are the real guard**; this one is a tripwire
    for a sweep nobody has written yet, which is the case no behavioural test
    can cover because there is nothing to call.
    """

    def test_the_derivation_finds_the_sweeps_that_exist(self):
        """The control. A derivation that matched nothing would make every
        assertion below vacuously true — which is the failure mode of deriving
        rather than listing, and the reason it is checked."""
        assert set(_pair_nominating_sweeps()) == {
            "contradiction_detection", "inference_dedup", "topic_consolidation"
        }

    def test_every_one_of_them_calls_the_shared_read(self):
        missing = [
            name for name, source in _pair_nominating_sweeps().items()
            if "already_judged_pairs" not in source
        ]
        assert missing == [], (
            f"{', '.join(missing)} nominates pairs without reading the "
            f"suppression every verdict writes, so a judged pair comes back on "
            f"the next reflect — for ever, to an agent who cannot see it was "
            f"already answered"
        )

    def test_the_edge_list_has_one_home(self):
        """A second list of what counts as judged would drift, and the drift
        would be silent — the reason the read was extracted rather than copied
        a third time. Scans the whole package, not the sweeps: a copy is just as
        harmful wherever it is put."""
        defining = sorted(
            name for name, source in _reflection_sources().items()
            if "ALREADY_JUDGED_EDGE_TYPES: tuple" in source
        )
        assert defining == ["similarity_decisions"]
