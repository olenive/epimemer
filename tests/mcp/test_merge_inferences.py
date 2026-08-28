"""The merge itself: what the survivor carries, and what the graph records.

`merge_nodes` is type-agnostic and always was — it embeds, migrates and dedupes
edges, and retires sources as MERGED with `merged_into` lineage. Topics reach it
through reflect, facts through `merge_facts`, and until now inferences reached it
through nothing. So almost none of what follows is about merging; it is about the
**gate** and about the advisory, which are the two things that had to be decided.
"""

from datetime import datetime, timezone

import pytest

from epimemer.core.advisories import (
    AdvisoryAction,
    AdvisoryKind,
    WarningPolicy,
)
from epimemer.core.temporal import (
    IntervalBasis,
    PreciseInstant,
    UnknownInstant,
    ValidityInterval,
)
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
    Topic,
)
from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.mcp import tools
from epimemer.storage.protocol import WarningOverrides

_TWIN = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
_STRANGER = [0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]

CRITIC = JudgeRef(agent_id="a-critic", digest="d1")


@pytest.fixture
def embedding_provider() -> MockEmbeddingProvider:
    return MockEmbeddingProvider(model_id="mock-embed", dimension=8)


async def _inference(storage, embedding_provider, content, *, vector=None):
    inference = Inference(content=content, source_id="seg-1")
    await storage.store_node(inference)
    await storage.store_embedding(EmbeddingRecord(
        item_id=inference.id,
        model_id=embedding_provider.model_id,
        vector=vector or _TWIN,
    ))
    await storage.store_edge(NodeEdge(
        src_id=inference.id, dst_id=BASE_METACONTEXT_ID,
        type=EdgeType.HAS_METACONTEXT,
    ))
    return inference


async def _premise(storage, content):
    fact = Fact(content=content, source_id="seg-1", claim_kind=ClaimKind.STATE)
    await storage.store_node(fact)
    return fact


async def _rests_on(storage, inference, premise):
    await storage.store_edge(NodeEdge(
        src_id=inference.id, dst_id=premise.id, type=EdgeType.DERIVED_FROM,
    ))


def _year(value: int) -> PreciseInstant:
    return PreciseInstant(at=datetime(value, 1, 1, tzinfo=timezone.utc))


async def _dated(storage, premise, name, interval):
    document = RawDocument(content=f"contents of {name}", source=name)
    await storage.store_document(document)
    await storage.store_edge(NodeEdge(
        src_id=premise.id, dst_id=document.id,
        type=EdgeType.SOURCED_FROM, validity=[interval],
    ))


async def _disjoint_pair(storage, embedding_provider):
    """Two twin readings whose premises no source puts in one period."""
    one = await _inference(storage, embedding_provider, "The name changed once")
    other = await _inference(storage, embedding_provider, "The name has changed once")
    early = await _premise(storage, "Leningrad is the city's name")
    late = await _premise(storage, "Saint Petersburg is the city's name")
    await _dated(storage, early, "atlas-1970", ValidityInterval(
        start=_year(1924), end=_year(1991), basis=IntervalBasis.STATED
    ))
    await _dated(storage, late, "atlas-2020", ValidityInterval(
        start=_year(1991), end=UnknownInstant(), basis=IntervalBasis.STATED
    ))
    await _rests_on(storage, one, early)
    await _rests_on(storage, other, late)
    return one, other


async def _merge(storage, embedding_provider, sources, content="One reading.", **kw):
    result, meta = await tools.merge_inferences(
        source_ids=[node.id for node in sources],
        content=content,
        storage=storage,
        embedding_provider=embedding_provider,
        **kw,
    )
    return result, meta


class TestTwoReadingsBecomeOne:

    async def test_the_survivor_replaces_both_and_keeps_their_evidence(
        self, storage, embedding_provider
    ):
        premise = await _premise(storage, "The deploy failed")
        other_premise = await _premise(storage, "The queue backed up")
        one = await _inference(storage, embedding_provider, "The release is unsafe")
        other = await _inference(
            storage, embedding_provider, "The release cannot be trusted"
        )
        await _rests_on(storage, one, premise)
        await _rests_on(storage, other, other_premise)

        result, meta = await _merge(
            storage, embedding_provider, [one, other],
            content="The release should not ship.",
        )

        assert result["merged"] is True
        survivor = await storage.get_node(result["inference_id"])
        assert isinstance(survivor, Inference)
        assert survivor.content == "The release should not ship."
        assert survivor.metadata["merged_from"] == [one.id, other.id]
        for source in (one, other):
            assert (await storage.get_node(source.id)).status is NodeStatus.MERGED
        # The union of the premises, which is exactly the combination neither
        # original had — and usually the point of the merge.
        premises = {
            edge.dst_id for edge in await storage.get_edges_from(
                survivor.id, edge_type=EdgeType.DERIVED_FROM
            )
        }
        assert premises == {premise.id, other_premise.id}
        assert meta.source_types == {"inferences": 1}

    async def test_the_survivor_is_searchable_and_framed(
        self, storage, embedding_provider
    ):
        one = await _inference(storage, embedding_provider, "A reading")
        other = await _inference(storage, embedding_provider, "The same reading")

        result, _ = await _merge(storage, embedding_provider, [one, other])

        stored = await storage.get_embeddings_for_items(
            [result["inference_id"]], model_id=embedding_provider.model_id
        )
        assert stored[result["inference_id"]]
        frames = await storage.get_edges_from(
            result["inference_id"], edge_type=EdgeType.HAS_METACONTEXT
        )
        assert [edge.dst_id for edge in frames] == [BASE_METACONTEXT_ID]

    async def test_the_confidence_basis_travels_with_the_confidence(
        self, storage, embedding_provider
    ):
        """`merged_value_signal` keeps the highest confidence of the sources. A
        rebuild that took the number and not the prose behind it would leave a
        prior nobody can review — the exact state the guidance exists to prevent,
        reached by a path nobody chose."""
        one = await _inference(storage, embedding_provider, "A reading")
        other = await _inference(storage, embedding_provider, "The same reading")
        one.value.confidence = 0.3
        one.metadata["confidence_basis"] = "one source, hedged"
        other.value.confidence = 0.9
        other.metadata["confidence_basis"] = "the spec, about its own behaviour"
        for node in (one, other):
            await storage.store_node(node)

        result, _ = await _merge(storage, embedding_provider, [one, other])

        survivor = await storage.get_node(result["inference_id"])
        assert survivor.value.confidence == pytest.approx(0.9)
        assert survivor.metadata["confidence_basis"] == (
            "the spec, about its own behaviour"
        )

    async def test_unrated_sources_owe_no_reason(
        self, storage, embedding_provider
    ):
        one = await _inference(storage, embedding_provider, "A reading")
        other = await _inference(storage, embedding_provider, "The same reading")

        result, _ = await _merge(storage, embedding_provider, [one, other])

        survivor = await storage.get_node(result["inference_id"])
        assert survivor.value.confidence is None
        assert "confidence_basis" not in survivor.metadata

    async def test_the_merge_is_journalled_survivor_first(
        self, storage, embedding_provider
    ):
        one = await _inference(storage, embedding_provider, "A reading")
        other = await _inference(storage, embedding_provider, "The same reading")

        result, _ = await _merge(
            storage, embedding_provider, [one, other], judge=CRITIC
        )

        rows = await storage.query_decisions(kinds=[DecisionKind.MERGE])
        assert len(rows) == 1
        assert rows[0].subject_ids[0] == result["inference_id"]
        assert rows[0].judged_by.agent_id == "a-critic"


class TestARefusalComesBackRatherThanRaising:
    """An agent told no has a real alternative — record `SIMILARITY` and keep
    both — and refusing out loud is how it gets to choose it."""

    async def test_a_pair_below_the_bar_is_declined_with_a_reason(
        self, storage, embedding_provider
    ):
        one = await _inference(storage, embedding_provider, "A reading")
        other = await _inference(
            storage, embedding_provider, "Something else", vector=_STRANGER
        )

        result, meta = await _merge(storage, embedding_provider, [one, other])

        assert result["merged"] is False
        assert "similarity" in result["refused"]
        # Declared even on a refusal: the ids are readable in the response.
        assert {r.node_id for r in meta.retrieved} == {one.id, other.id}
        assert (await storage.get_node(one.id)).status is NodeStatus.ACTIVE

    async def test_a_fact_is_not_something_this_tool_merges(
        self, storage, embedding_provider
    ):
        """A malformed request raises; a judgment the graph declines comes back."""
        inference = await _inference(storage, embedding_provider, "A reading")
        fact = await _premise(storage, "The deploy failed")

        with pytest.raises(ValueError, match="only inferences merge here"):
            await _merge(storage, embedding_provider, [inference, fact])

    async def test_a_topic_is_not_either(self, storage, embedding_provider):
        inference = await _inference(storage, embedding_provider, "A reading")
        topic = Topic(content="Deployments", source_id="seg-1")
        await storage.store_node(topic)

        with pytest.raises(ValueError, match="topics consolidate through reflect"):
            await _merge(storage, embedding_provider, [inference, topic])

    async def test_an_id_that_names_nothing_raises(
        self, storage, embedding_provider
    ):
        inference = await _inference(storage, embedding_provider, "A reading")

        with pytest.raises(ValueError, match="not found"):
            await tools.merge_inferences(
                source_ids=[inference.id, "nope"],
                content="One reading.",
                storage=storage,
                embedding_provider=embedding_provider,
            )


class TestTheAdvisoryRidesWithTheMerge:
    """Pre-decision by design, and recorded whether or not it is shown.

    The one dangerous outcome — a survivor resting on premises no source puts in
    one period — is computable before the agent decides, so it belongs in the
    nomination and in the response rather than in a refusal.
    """

    async def test_disjoint_premises_are_reported_and_the_merge_goes_through(
        self, storage, embedding_provider
    ):
        one, other = await _disjoint_pair(storage, embedding_provider)

        result, _ = await _merge(storage, embedding_provider, [one, other])

        assert result["merged"] is True
        assert [w["kind"] for w in result["warnings"]] == [
            AdvisoryKind.DISJOINT_PREMISES.value
        ]
        assert result["warning"] == result["warnings"][0]["message"]
        # `proceed` by default: the agent has already been told, and has written
        # its content in light of it.
        assert result["notify_user"] is False

    async def test_proceeding_past_one_is_journalled_against_the_survivor(
        self, storage, embedding_provider
    ):
        one, other = await _disjoint_pair(storage, embedding_provider)

        result, _ = await _merge(
            storage, embedding_provider, [one, other], judge=CRITIC
        )

        rows = await storage.query_decisions(
            kinds=[DecisionKind.PROCEEDED_DESPITE_ADVISORY]
        )
        assert len(rows) == 1
        assert rows[0].subject_ids[0] == result["inference_id"]
        assert AdvisoryKind.DISJOINT_PREMISES.value in rows[0].certainty_basis
        # Nobody rated this. A row invented at 0.5 would sort above the
        # genuinely unrated ones and read as a judgment nobody made.
        assert rows[0].certainty is None

    async def test_a_clean_merge_writes_no_advisory_row(
        self, storage, embedding_provider
    ):
        premise = await _premise(storage, "The deploy failed")
        one = await _inference(storage, embedding_provider, "A reading")
        other = await _inference(storage, embedding_provider, "The same reading")
        for inference in (one, other):
            await _rests_on(storage, inference, premise)

        result, _ = await _merge(storage, embedding_provider, [one, other])

        assert "warnings" not in result and "warning" not in result
        # Present and false, not absent. *No advisory*, *advisory muted* and
        # *advisory shown but quiet* are one answer to the only question this
        # key asks, and three response shapes for it leak which branch ran.
        assert result["notify_user"] is False
        assert await storage.query_decisions(
            kinds=[DecisionKind.PROCEEDED_DESPITE_ADVISORY]
        ) == []

    async def test_surfacing_off_still_records(self, storage, embedding_provider):
        """The load-bearing separation: a graph whose warnings were off for a
        month should still answer *what was decided while nobody was looking*,
        which is exactly when the question matters most."""
        await storage.set_warning_overrides(WarningOverrides(surface=False))
        one, other = await _disjoint_pair(storage, embedding_provider)

        result, _ = await _merge(storage, embedding_provider, [one, other])

        assert "warnings" not in result and "warning" not in result
        assert result["notify_user"] is False
        assert len(await storage.query_decisions(
            kinds=[DecisionKind.PROCEEDED_DESPITE_ADVISORY]
        )) == 1

    async def test_a_graph_can_escalate_the_kind(self, storage, embedding_provider):
        await storage.set_warning_overrides(WarningOverrides(
            by_kind={AdvisoryKind.DISJOINT_PREMISES: AdvisoryAction.FLAG}
        ))
        one, other = await _disjoint_pair(storage, embedding_provider)

        result, _ = await _merge(storage, embedding_provider, [one, other])

        assert result["notify_user"] is True

    async def test_the_process_default_reaches_the_merge(
        self, storage, embedding_provider
    ):
        """No override, so the policy the server was configured with is the one
        applied — which is what makes the two-layer resolution worth having."""
        one, other = await _disjoint_pair(storage, embedding_provider)

        result, _ = await _merge(
            storage, embedding_provider, [one, other],
            warning_policy=WarningPolicy(
                by_kind={AdvisoryKind.DISJOINT_PREMISES: AdvisoryAction.FLAG}
            ),
        )

        assert result["notify_user"] is True

    async def test_the_advisory_is_computed_before_the_premises_migrate(
        self, storage, embedding_provider
    ):
        """Afterwards the two arguments are one, and the finding is unrecoverable.

        Once `derived_from` has migrated onto the survivor, nothing distinguishes
        *these premises arrived from two inferences* from *this one was drawn on
        both*, so the read has to happen first. This pins that it did.
        """
        one, other = await _disjoint_pair(storage, embedding_provider)

        result, _ = await _merge(storage, embedding_provider, [one, other])

        assert result["warnings"]
        survivor_premises = {
            edge.dst_id for edge in await storage.get_edges_from(
                result["inference_id"], edge_type=EdgeType.DERIVED_FROM
            )
        }
        assert len(survivor_premises) == 2


class TestReflectOffersTheCandidatesAndDeclaresThem:
    """The nominee list is only useful if `reflect` returns it *and* declares
    what it returned. A nominee whose ids go undeclared is invisible to the
    retrieval record, which is the one place a reader can check what the agent
    was actually shown."""

    async def test_the_pair_arrives_with_its_premise(
        self, storage, embedding_provider
    ):
        premise = await _premise(storage, "The deploy failed")
        one = await _inference(storage, embedding_provider, "The release is unsafe")
        other = await _inference(
            storage, embedding_provider, "The release cannot be trusted"
        )
        for inference in (one, other):
            await _rests_on(storage, inference, premise)

        result, meta = await tools.reflect(storage, embedding_provider)

        candidates = result["inference_merge_candidates"]
        assert len(candidates) == 1
        assert {n["id"] for n in candidates[0]["inferences"]} == {one.id, other.id}
        declared = {record.node_id for record in meta.retrieved}
        assert {one.id, other.id, premise.id} <= declared

    async def test_it_is_capped_like_every_other_pair_built_list(self):
        """Grouping bounds it by inferences-per-premise rather than by the
        graph, which is a real and much lower bound — but *every pair list is
        capped* is a simpler invariant to hold than *capped except where a
        grouping argument says otherwise*, and the bound grows in exactly the
        heavily merged graphs this feature targets."""
        assert "inference_merge_candidates" in tools.CAPPED_KEYS
        assert "inference_merge_nomination" in tools.REFLECT_PHASES

    async def test_the_cap_keeps_the_highest_scoring_and_says_it_cut(
        self, storage, embedding_provider
    ):
        premise = await _premise(storage, "The deploy failed")
        exact = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        near = [0.98, 0.2, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        for index in range(4):
            node = await _inference(
                storage, embedding_provider, f"reading {index}",
                vector=exact if index < 2 else near,
            )
            await _rests_on(storage, node, premise)

        result, _ = await tools.reflect(
            storage, embedding_provider, max_nominations=2
        )

        candidates = result["inference_merge_candidates"]
        assert len(candidates) == 2
        assert "inference_merge_candidates" in result["truncated"]
        assert candidates[0]["similarity"] >= candidates[1]["similarity"]


class TestReversingAnInferenceMerge:
    """`merge_nodes` and `reverse_merge` are type-agnostic by construction, and
    that was the whole argument for expecting this to work. An argument is not a
    run: this is the deepest interaction the inference merge takes part in, and
    it went unexercised until a reviewer ran it by hand."""

    async def test_two_readings_come_back_with_their_premises(
        self, storage, embedding_provider
    ):
        first = await _premise(storage, "The deploy failed")
        second = await _premise(storage, "The queue backed up")
        one = await _inference(storage, embedding_provider, "The release is unsafe")
        other = await _inference(
            storage, embedding_provider, "The release cannot be trusted"
        )
        await _rests_on(storage, one, first)
        await _rests_on(storage, other, second)
        merged, _ = await _merge(storage, embedding_provider, [one, other])

        outcome, _ = await tools.reverse_merge(merged["inference_id"], storage)

        assert outcome["reversed"] is True
        assert set(outcome["restored_ids"]) == {one.id, other.id}
        assert await storage.get_node(merged["inference_id"]) is None
        for source, premise in ((one, first), (other, second)):
            assert (await storage.get_node(source.id)).status is NodeStatus.ACTIVE
            edges = await storage.get_edges_from(
                source.id, edge_type=EdgeType.DERIVED_FROM
            )
            assert [edge.dst_id for edge in edges] == [premise.id]

    async def test_three_readings_reverse_as_cleanly_as_two(
        self, storage, embedding_provider
    ):
        """Everything else here is pairs; the tool takes a list."""
        premise = await _premise(storage, "The deploy failed")
        sources = []
        for index in range(3):
            node = await _inference(
                storage, embedding_provider, f"the release is unsafe ({index})"
            )
            await _rests_on(storage, node, premise)
            sources.append(node)

        merged, _ = await _merge(storage, embedding_provider, sources)
        assert merged["sources_retired"] == 3

        outcome, _ = await tools.reverse_merge(merged["inference_id"], storage)

        assert outcome["reversed"] is True
        assert set(outcome["restored_ids"]) == {node.id for node in sources}
        for node in sources:
            assert (await storage.get_node(node.id)).status is NodeStatus.ACTIVE

    async def test_a_three_way_merge_carries_every_premise(
        self, storage, embedding_provider
    ):
        premises = [await _premise(storage, f"premise {i}") for i in range(3)]
        sources = []
        for index, premise in enumerate(premises):
            node = await _inference(
                storage, embedding_provider, f"the release is unsafe ({index})"
            )
            await _rests_on(storage, node, premise)
            sources.append(node)

        merged, _ = await _merge(storage, embedding_provider, sources)

        carried = {
            edge.dst_id for edge in await storage.get_edges_from(
                merged["inference_id"], edge_type=EdgeType.DERIVED_FROM
            )
        }
        assert carried == {premise.id for premise in premises}


class TestARequiredJudgeReachesEveryWriter:
    """A merge writes through `merge_nodes` and through `journal`, and both
    carry the judge. Worth a run rather than an inspection: the two write in
    different transactions."""

    async def test_the_judge_lands_on_the_survivor_and_the_rows(
        self, storage, embedding_provider
    ):
        premise = await _premise(storage, "The deploy failed")
        one = await _inference(storage, embedding_provider, "A reading")
        other = await _inference(storage, embedding_provider, "The same reading")
        for inference in (one, other):
            await _rests_on(storage, inference, premise)
        await storage.set_require_judge(True)

        result, _ = await _merge(
            storage, embedding_provider, [one, other], judge=CRITIC
        )

        survivor = await storage.get_node(result["inference_id"])
        assert survivor.judged_by.agent_id == "a-critic"
        rows = await storage.query_decisions(kinds=[DecisionKind.MERGE])
        assert [row.judged_by.agent_id for row in rows] == ["a-critic"]
        # The frame the merge re-states is written under the merging judge too,
        # rather than inheriting an edge somebody else wrote.
        frames = await storage.get_edges_from(
            survivor.id, edge_type=EdgeType.HAS_METACONTEXT
        )
        assert [edge.judged_by.agent_id for edge in frames] == ["a-critic"]
