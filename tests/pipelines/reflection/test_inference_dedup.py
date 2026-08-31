"""Two derivations of one conclusion collapse into one node — and only those.

The population this exists for did not exist until facts merged. Collapsing four
near-identical facts onto one survivor migrates the four inferences drawn on them
onto that survivor too, each flagged `evidence_merged`; four near-identical
inferences hanging off one fact is the clearest case for merging them and the one
nothing could see before.

**The one dangerous outcome is computable before the agent decides, which is why
it is a warning and not a rule.** A merge of A resting on `{F1}` and B resting on
`{F2}` leaves a survivor resting on `{F1, F2}`. Where those two are dated and
provably fall clear of each other, the survivor is an inference over premises no
source puts in one period — and it is *genuinely* unsound rather than falsely
flagged, because the agent writes fresh content asserting one claim over both.
The honest answer is usually to narrow the wording or the period, which the agent
does by writing, so a refusal would block a merge it could have fixed. Hence: an
advisory, delivered with the nomination.

The tests below therefore split three ways — what refuses, what warns without
refusing, and what is nominated at all.
"""

from datetime import UTC, datetime

import pytest

from epimemer.core.advisories import AdvisoryKind
from epimemer.core.temporal import (
    IntervalBasis,
    PreciseInstant,
    UnknownInstant,
    ValidityInterval,
)
from epimemer.core.types import (
    BASE_METACONTEXT_ID,
    ClaimKind,
    EdgeType,
    EmbeddingRecord,
    Fact,
    Inference,
    LifecycleEpisode,
    Metacontext,
    NodeEdge,
    NodeStatus,
    RawDocument,
    Topic,
)
from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.pipelines.reflection.inference_dedup import (
    merge_advisories,
    merge_refusal,
    nominate_inference_merges,
)
from epimemer.pipelines.reflection.review import SIMILARITY_NOMINATION_THRESHOLD


@pytest.fixture
def embedding_provider() -> MockEmbeddingProvider:
    return MockEmbeddingProvider(model_id="mock-embed", dimension=8)


# Supplied rather than derived from the text, for `test_fact_dedup`'s reason: a
# test about what the frame rule decides must not also be a test of how alike two
# sentences happen to hash.
_TWIN = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
_STRANGER = [0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]


async def _inference(
    storage,
    embedding_provider,
    content: str,
    *,
    vector: list[float] | None = None,
    status: NodeStatus = NodeStatus.ACTIVE,
    lifecycle: list[LifecycleEpisode] | None = None,
    frame: str | None = BASE_METACONTEXT_ID,
) -> Inference:
    """One stored, embedded, framed inference. Defaults are the mergeable case."""
    inference = Inference(
        content=content,
        source_id="seg-1",
        status=status,
        lifecycle=list(lifecycle or []),
    )
    await storage.store_node(inference)
    await storage.store_embedding(
        EmbeddingRecord(
            item_id=inference.id,
            model_id=embedding_provider.model_id,
            vector=vector or _TWIN,
        )
    )
    if frame is not None:
        await storage.store_edge(
            NodeEdge(
                src_id=inference.id,
                dst_id=frame,
                type=EdgeType.HAS_METACONTEXT,
            )
        )
    return inference


async def _premise(storage, content: str) -> Fact:
    fact = Fact(content=content, source_id="seg-1", claim_kind=ClaimKind.STATE)
    await storage.store_node(fact)
    return fact


async def _rests_on(storage, inference: Inference, premise: Fact) -> None:
    await storage.store_edge(
        NodeEdge(
            src_id=inference.id,
            dst_id=premise.id,
            type=EdgeType.DERIVED_FROM,
        )
    )


async def _dated(storage, premise: Fact, name: str, *intervals) -> str:
    document = RawDocument(content=f"contents of {name}", source=name)
    await storage.store_document(document)
    await storage.store_edge(
        NodeEdge(
            src_id=premise.id,
            dst_id=document.id,
            type=EdgeType.SOURCED_FROM,
            validity=list(intervals),
        )
    )
    return document.id


async def _frame(storage, node, label: str) -> str:
    frame = Metacontext(content=label)
    await storage.store_metacontext(frame)
    await storage.store_edge(
        NodeEdge(
            src_id=node.id,
            dst_id=frame.id,
            type=EdgeType.HAS_METACONTEXT,
        )
    )
    return frame.id


def _year(value: int) -> PreciseInstant:
    return PreciseInstant(at=datetime(value, 1, 1, tzinfo=UTC))


def _span(start: int, end: int) -> ValidityInterval:
    return ValidityInterval(start=_year(start), end=_year(end), basis=IntervalBasis.STATED)


def _open_from(start: int) -> ValidityInterval:
    return ValidityInterval(start=_year(start), end=UnknownInstant(), basis=IntervalBasis.STATED)


def _completed_cycles(count: int) -> list[LifecycleEpisode]:
    return [
        LifecycleEpisode(
            retired_at=datetime(2026, 1, i + 1, tzinfo=UTC),
            because=NodeStatus.MERGED,
            counterpart=f"survivor-{i}",
            restored_at=datetime(2026, 1, i + 2, tzinfo=UTC),
        )
        for i in range(count)
    ]


async def _refusal(storage, embedding_provider, sources, **kwargs):
    return await merge_refusal(sources, storage, model_id=embedding_provider.model_id, **kwargs)


class TestWhatWillNotMerge:
    """Each rung refuses on doubt rather than resolving it, and says which.

    Ordered permanent-first for `fact_dedup`'s reason: a cross-frame pair will
    never merge however the graph changes, while a pair below the bar may be
    nominable later. Reporting the fixable obstacle while a permanent one also
    stands sends an agent to do work that changes nothing.
    """

    async def test_one_inference_is_already_itself(self, storage, embedding_provider):
        only = await _inference(storage, embedding_provider, "One reading")

        refusal = await _refusal(storage, embedding_provider, [only, only])

        assert refusal is not None and "already itself" in refusal.reason

    async def test_a_retired_derivation_has_been_ruled_on(self, storage, embedding_provider):
        live = await _inference(storage, embedding_provider, "A reading")
        gone = await _inference(
            storage,
            embedding_provider,
            "The same reading",
            status=NodeStatus.CORRECTED,
        )

        refusal = await _refusal(storage, embedding_provider, [live, gone])

        assert refusal is not None
        assert "only active inferences merge" in refusal.reason
        assert "corrected" in refusal.reason

    async def test_two_worlds_do_not_become_one_node(self, storage, embedding_provider):
        """The union problem `fact_dedup` refuses on, unchanged.

        Two perspectives reaching the same conclusion about different worlds are
        two conclusions, and a survivor inheriting both frames asserts in one
        world what was only ever derived in another.
        """
        real = await _inference(storage, embedding_provider, "The pass was closed")
        fictional = await _inference(storage, embedding_provider, "The pass was closed", frame=None)
        await _frame(storage, fictional, "Novel-X")

        refusal = await _refusal(storage, embedding_provider, [real, fictional])

        assert refusal is not None
        assert "same set of frames" in refusal.reason

    async def test_below_the_nomination_bar_is_not_a_pair_anybody_offered(
        self, storage, embedding_provider
    ):
        one = await _inference(storage, embedding_provider, "A reading")
        other = await _inference(
            storage, embedding_provider, "An unrelated reading", vector=_STRANGER
        )

        refusal = await _refusal(storage, embedding_provider, [one, other])

        assert refusal is not None
        assert str(SIMILARITY_NOMINATION_THRESHOLD) in refusal.reason

    async def test_an_unembedded_inference_cannot_clear_a_bar_nobody_measured(
        self, storage, embedding_provider
    ):
        one = await _inference(storage, embedding_provider, "A reading")
        bare = Inference(content="The same reading", source_id="seg-1")
        await storage.store_node(bare)
        await storage.store_edge(
            NodeEdge(
                src_id=bare.id,
                dst_id=BASE_METACONTEXT_ID,
                type=EdgeType.HAS_METACONTEXT,
            )
        )

        refusal = await _refusal(storage, embedding_provider, [one, bare])

        assert refusal is not None and "no stored embedding" in refusal.reason

    async def test_an_oscillation_asks_for_a_person(self, storage, embedding_provider):
        one = await _inference(
            storage, embedding_provider, "A reading", lifecycle=_completed_cycles(2)
        )
        other = await _inference(storage, embedding_provider, "The same reading")

        refusal = await _refusal(storage, embedding_provider, [one, other], cycle_limit=2)

        assert refusal is not None and "Ask the user" in refusal.reason

    async def test_nothing_objects_to_two_twins_in_one_frame(self, storage, embedding_provider):
        one = await _inference(storage, embedding_provider, "A reading")
        other = await _inference(storage, embedding_provider, "The same reading")

        assert await _refusal(storage, embedding_provider, [one, other]) is None

    async def test_there_is_no_claim_kind_rung(self, storage, embedding_provider):
        """An inference carries no `claim_kind`, and that is the design.

        `claim_kind` exists because interval union is mechanically right for a
        state and fabricating for an event. Whether combining premises is
        legitimate is not mechanical — the agent answers it in the text it
        writes — so a stored judgment would freeze what the merge decides.
        """
        one = await _inference(storage, embedding_provider, "A reading")
        other = await _inference(storage, embedding_provider, "The same reading")

        assert not hasattr(one, "claim_kind")
        assert await _refusal(storage, embedding_provider, [one, other]) is None


class TestDisjointPremisesWarnRatherThanRefuse:
    """The whole reason this is a warning: the fix is something the agent writes.

    A refusal would block a merge the agent could have narrowed. So the pair is
    still mergeable, and the advisory rides along with the candidate — before the
    content is written, which is the only moment it can change the answer.
    """

    async def test_premises_that_fall_clear_produce_an_advisory(self, storage, embedding_provider):
        one = await _inference(storage, embedding_provider, "A reading")
        other = await _inference(storage, embedding_provider, "The same reading")
        early = await _premise(storage, "Leningrad is the city's name")
        late = await _premise(storage, "Saint Petersburg is the city's name")
        await _dated(storage, early, "atlas-1970", _span(1924, 1991))
        await _dated(storage, late, "atlas-2020", _open_from(1991))
        await _rests_on(storage, one, early)
        await _rests_on(storage, other, late)

        advisories = await merge_advisories([one, other], storage)

        assert len(advisories) == 1
        assert advisories[0].kind is AdvisoryKind.DISJOINT_PREMISES
        assert set(advisories[0].subjects) == {one.id, other.id}
        assert {advisories[0].detail["a"]["id"], advisories[0].detail["b"]["id"]} == {
            early.id,
            late.id,
        }

    async def test_the_advisory_does_not_refuse_the_merge(self, storage, embedding_provider):
        one = await _inference(storage, embedding_provider, "A reading")
        other = await _inference(storage, embedding_provider, "The same reading")
        early = await _premise(storage, "Leningrad is the city's name")
        late = await _premise(storage, "Saint Petersburg is the city's name")
        await _dated(storage, early, "atlas-1970", _span(1924, 1991))
        await _dated(storage, late, "atlas-2020", _open_from(1991))
        await _rests_on(storage, one, early)
        await _rests_on(storage, other, late)

        assert await merge_advisories([one, other], storage)
        assert await _refusal(storage, embedding_provider, [one, other]) is None

    async def test_overlapping_premises_say_nothing(self, storage, embedding_provider):
        one = await _inference(storage, embedding_provider, "A reading")
        other = await _inference(storage, embedding_provider, "The same reading")
        first = await _premise(storage, "The service was degraded")
        second = await _premise(storage, "The queue was backing up")
        await _dated(storage, first, "report-a", _span(2020, 2024))
        await _dated(storage, second, "report-b", _span(2022, 2026))
        await _rests_on(storage, one, first)
        await _rests_on(storage, other, second)

        assert await merge_advisories([one, other], storage) == []

    async def test_undated_premises_are_ignorance_rather_than_evidence(
        self, storage, embedding_provider
    ):
        """The soundness rule, unchanged: it never fires on `unknown`.

        Most of the graph is undated and always will be. A check that treated
        *cannot be placed* as *falls clear* would be a check on ignorance.
        """
        one = await _inference(storage, embedding_provider, "A reading")
        other = await _inference(storage, embedding_provider, "The same reading")
        first = await _premise(storage, "The service was degraded")
        second = await _premise(storage, "The queue was backing up")
        await _rests_on(storage, one, first)
        await _rests_on(storage, other, second)

        assert await merge_advisories([one, other], storage) == []

    async def test_a_supports_edge_counts_as_a_premise_too(self, storage, embedding_provider):
        """Both directions, exactly as evidence-staleness already counts them."""
        one = await _inference(storage, embedding_provider, "A reading")
        other = await _inference(storage, embedding_provider, "The same reading")
        early = await _premise(storage, "Leningrad is the city's name")
        late = await _premise(storage, "Saint Petersburg is the city's name")
        await _dated(storage, early, "atlas-1970", _span(1924, 1991))
        await _dated(storage, late, "atlas-2020", _open_from(1991))
        await storage.store_edge(
            NodeEdge(
                src_id=early.id,
                dst_id=one.id,
                type=EdgeType.SUPPORTS,
            )
        )
        await _rests_on(storage, other, late)

        assert len(await merge_advisories([one, other], storage)) == 1


class TestNominationIsScopedToSharedEvidence:
    """Never a global sweep, and the three reasons are not interchangeable.

    It is the case that actually arises; it is cheap; and a global sweep
    nominates nothing — 123 active inferences across both real graphs give 5,053
    pairs and zero at the bar, with the top-scoring pairs sharing vocabulary and
    saying different things.
    """

    async def test_two_readings_of_one_premise_are_nominated(self, storage, embedding_provider):
        premise = await _premise(storage, "The deploy failed")
        one = await _inference(storage, embedding_provider, "The release is unsafe")
        other = await _inference(storage, embedding_provider, "The release cannot be trusted")
        await _rests_on(storage, one, premise)
        await _rests_on(storage, other, premise)

        found = await nominate_inference_merges(storage, embedding_provider)

        assert len(found) == 1
        assert {ref.id for ref in found[0].inferences} == {one.id, other.id}
        assert [ref.id for ref in found[0].shared_premises] == [premise.id]
        assert found[0].similarity >= SIMILARITY_NOMINATION_THRESHOLD

    async def test_twins_sharing_no_premise_are_not_offered(self, storage, embedding_provider):
        """The measured case. Two inferences agreeing may be independent support
        — which is the thing corroboration exists to count — and nothing here
        treats agreement alone as redundancy."""
        one = await _inference(storage, embedding_provider, "The release is unsafe")
        other = await _inference(storage, embedding_provider, "The release cannot be trusted")
        await _rests_on(storage, one, await _premise(storage, "The deploy failed"))
        await _rests_on(storage, other, await _premise(storage, "The queue grew"))

        assert await nominate_inference_merges(storage, embedding_provider) == []

    async def test_a_shared_premise_is_not_enough_on_its_own(self, storage, embedding_provider):
        premise = await _premise(storage, "The deploy failed")
        one = await _inference(storage, embedding_provider, "The release is unsafe")
        other = await _inference(
            storage, embedding_provider, "The rollback was routine", vector=_STRANGER
        )
        await _rests_on(storage, one, premise)
        await _rests_on(storage, other, premise)

        assert await nominate_inference_merges(storage, embedding_provider) == []

    async def test_a_pair_somebody_already_judged_stops_coming_back(
        self, storage, embedding_provider
    ):
        """The treadmill `assessed` closed, on this list too.

        An agent that answered `distinct` and then saw the pair on every
        subsequent reflect is being asked a question that has an answer.
        """
        premise = await _premise(storage, "The deploy failed")
        one = await _inference(storage, embedding_provider, "The release is unsafe")
        other = await _inference(storage, embedding_provider, "The release cannot be trusted")
        await _rests_on(storage, one, premise)
        await _rests_on(storage, other, premise)
        assert await nominate_inference_merges(storage, embedding_provider)

        await storage.store_edge(
            NodeEdge(
                src_id=one.id,
                dst_id=other.id,
                type=EdgeType.ASSESSED,
            )
        )

        assert await nominate_inference_merges(storage, embedding_provider) == []

    async def test_a_cross_frame_pair_is_not_offered_because_it_would_refuse(
        self, storage, embedding_provider
    ):
        """A worklist that cannot be worked is worse than a shorter one."""
        premise = await _premise(storage, "The deploy failed")
        real = await _inference(storage, embedding_provider, "The release is unsafe")
        fictional = await _inference(
            storage, embedding_provider, "The release cannot be trusted", frame=None
        )
        await _frame(storage, fictional, "Novel-X")
        await _rests_on(storage, real, premise)
        await _rests_on(storage, fictional, premise)

        assert await nominate_inference_merges(storage, embedding_provider) == []

    async def test_a_retired_inference_is_out_of_the_sweep(self, storage, embedding_provider):
        premise = await _premise(storage, "The deploy failed")
        live = await _inference(storage, embedding_provider, "The release is unsafe")
        gone = await _inference(
            storage,
            embedding_provider,
            "The release cannot be trusted",
            status=NodeStatus.MERGED,
        )
        await _rests_on(storage, live, premise)
        await _rests_on(storage, gone, premise)

        assert await nominate_inference_merges(storage, embedding_provider) == []

    async def test_the_nomination_carries_its_advisory(self, storage, embedding_provider):
        """Pre-decision, which is the point: the agent is told before it writes.

        Delivering it afterwards would arrive detached from the decision that
        caused it, and a second round trip to hand over information already in
        hand is latency bought for nothing.
        """
        shared = await _premise(storage, "The city was renamed")
        early = await _premise(storage, "Leningrad is the city's name")
        late = await _premise(storage, "Saint Petersburg is the city's name")
        await _dated(storage, early, "atlas-1970", _span(1924, 1991))
        await _dated(storage, late, "atlas-2020", _open_from(1991))
        one = await _inference(storage, embedding_provider, "The name changed once")
        other = await _inference(storage, embedding_provider, "The name has changed once")
        for inference in (one, other):
            await _rests_on(storage, inference, shared)
        await _rests_on(storage, one, early)
        await _rests_on(storage, other, late)

        found = await nominate_inference_merges(storage, embedding_provider)

        assert len(found) == 1
        assert [advisory.kind for advisory in found[0].warnings] == [AdvisoryKind.DISJOINT_PREMISES]

    async def test_facts_and_topics_are_not_swept_here(self, storage, embedding_provider):
        """Facts are `merge_facts`; topics consolidate through reflect."""
        premise = await _premise(storage, "The deploy failed")
        twin = await _premise(storage, "The deployment did not succeed")
        topic = Topic(content="Deployments", source_id="seg-1")
        await storage.store_node(topic)
        for node in (premise, twin, topic):
            await storage.store_embedding(
                EmbeddingRecord(
                    item_id=node.id,
                    model_id=embedding_provider.model_id,
                    vector=_TWIN,
                )
            )

        assert await nominate_inference_merges(storage, embedding_provider) == []

    async def test_candidates_come_back_highest_scoring_first(self, storage, embedding_provider):
        premise = await _premise(storage, "The deploy failed")
        exact = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        near = [0.95, 0.31, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        anchor = await _inference(
            storage, embedding_provider, "The release is unsafe", vector=exact
        )
        twin = await _inference(
            storage, embedding_provider, "The release cannot be trusted", vector=exact
        )
        cousin = await _inference(
            storage, embedding_provider, "The release is questionable", vector=near
        )
        for inference in (anchor, twin, cousin):
            await _rests_on(storage, inference, premise)

        found = await nominate_inference_merges(storage, embedding_provider)

        scores = [candidate.similarity for candidate in found]
        assert scores == sorted(scores, reverse=True)
        assert scores[0] == pytest.approx(1.0)
