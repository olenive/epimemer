"""The verdict that had no writer (`REVIEW_MODE.md` §1, `ISSUES.md` #64).

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
    frame = Metacontext(content=label)
    await storage.store_metacontext(frame)
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
    """#64's presenting symptom: thirteen declined pairs, re-offered forever."""

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
    """#65's rule, extended to the edge #64 invents.

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
    silently not recorded is the whole of #64, so this module does not have the
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
    """What a second call does. The first case is a retried batch; the second is
    an agent changing its mind, which nothing here can yet perform."""

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

    async def test_distinct_after_one_claim_is_refused_out_loud(
        self, storage, embedding_provider
    ):
        """Nothing in this system deletes, this call included. The standing
        `similarity` edge keeps corroborating a pair the agent has just
        disowned, and saying so beats writing `assessed` beside it and looking
        like the judgment landed (#68)."""
        a = await _fact(storage, embedding_provider, "the deploy failed", publisher="BBC")
        b = await _fact(
            storage, embedding_provider, "the deployment failed", publisher="Reuters"
        )
        await _decide(storage, a, b, "one_claim")

        outcome = await _decide(storage, a, b, "distinct", "on reflection, different")

        assert isinstance(outcome, SimilarityRefused)
        assert "already stands" in outcome.reason
        assert await _count(storage, a) == 2

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
        """#65's anchoring rule, applied to the order of one call. A judgment is
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
