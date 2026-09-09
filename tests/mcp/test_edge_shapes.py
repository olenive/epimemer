"""An engine edge type joins the kinds it means, or it is not written.

`link` used to check the edge type against the enum and check that both
endpoints existed, and nothing else. So `link(fact, topic, edge_type="supports")`
was accepted although `supports` is a fact backing an inference, and so were a
`subtopic_of` between two facts and an `extracted_under_topic` onto an
inference. Every reader that assumed a shape had to defend itself:
`dependent_inference_ids` fetches the destinations of `supports` and keeps only
the inferences, precisely because the edge alone did not say. A reader that
forgot was simply wrong about the graph.

The shapes are `EDGE_SHAPES`, beside the enum, and the refusal is outright
rather than an override an agent could record: the wrong kinds are a category
error against what the type means, not a judgment somebody could defend.

Three questions are asked here. That the table covers the enum, so a type added
later cannot slip in unconstrained. That the writers taking endpoints from a
caller ask it. And that the question itself is answerable without storage,
which is what lets every writer ask it with the nodes it has already loaded.
"""

import inspect

import pytest

from epimemer.core.types import (
    EDGE_SHAPES,
    EdgeType,
    EndpointKind,
    Fact,
    Inference,
    JudgeRef,
    Topic,
    describe_edge_shape,
    edge_shape_violation,
    edge_types_joining,
)
from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.mcp import tools
from epimemer.mcp.tools import link
from epimemer.pipelines.reflection.similarity_decisions import (
    SimilarityRefused,
    apply_similarity_decision,
)

CRITIC = JudgeRef(agent_id="critic", digest="d1")


@pytest.fixture
def embedder():
    return MockEmbeddingProvider(model_id="mock-embed", dimension=8)


async def _nodes(storage):
    """One of each kind, stored: the three ends every shape is built from."""
    topic = Topic(content="Viennese coffee houses", source_id="s1")
    fact = Fact(content="the Cafe Central opened in 1876", source_id="s1")
    inference = Inference(content="the tradition predates the empire's fall", source_id="s1")
    for node in (topic, fact, inference):
        await storage.store_node(node)
    return topic, fact, inference


class TestTheTableCoversTheEnum:
    """Total by test as well as by construction, on `ADVISORY_STANCE`'s reasoning.

    A type added without a shape would otherwise take the permissive answer in
    silence, which is the guarantee this whole thing exists to give.
    """

    def test_every_edge_type_declares_a_shape(self):
        assert set(EDGE_SHAPES) == set(EdgeType)

    def test_only_the_open_vocabulary_type_is_unrestricted(self):
        """`related` is open because an agent coins what it joins; nothing else is."""
        open_types = {edge_type for edge_type, shape in EDGE_SHAPES.items() if shape.unrestricted}
        assert open_types == {EdgeType.RELATED}

    def test_a_constrained_type_names_at_least_one_pair(self):
        for edge_type, shape in EDGE_SHAPES.items():
            if not shape.unrestricted:
                assert shape.pairs, f"{edge_type.value} allows nothing at all"


class TestTheCheckIsPure:
    """No storage, and structurally unable to reach any.

    That is what lets `link`, `supersede_by`, `record_contradiction` and the
    similarity verdict all ask the same question with the nodes they have
    already loaded, rather than each carrying its own version of the rule.
    """

    def test_it_takes_kinds_and_nothing_else(self):
        parameters = list(inspect.signature(edge_shape_violation).parameters)
        assert parameters == ["edge_type", "src", "dst"]

    def test_it_cannot_await_a_backend(self):
        assert not inspect.iscoroutinefunction(edge_shape_violation)
        assert not inspect.iscoroutinefunction(edge_types_joining)

    def test_it_answers_the_same_way_every_time(self):
        first = edge_shape_violation(EdgeType.SUPPORTS, EndpointKind.FACT, EndpointKind.TOPIC)
        second = edge_shape_violation(EdgeType.SUPPORTS, EndpointKind.FACT, EndpointKind.TOPIC)
        assert first == second and first is not None


class TestLinkRefusesAMisshapenEdge:
    """The four shapes the issue named, each refused with what the type does join."""

    async def test_supports_from_a_fact_to_a_topic(self, storage):
        topic, fact, _ = await _nodes(storage)

        with pytest.raises(ValueError, match="fact -> inference"):
            await link(fact.id, topic.id, storage, edge_type="supports")

        assert await storage.get_edges_from(fact.id) == []

    async def test_the_refusal_names_what_does_join_the_two(self, storage):
        """`tagged_with_topic` and `extracted_under_topic` both take a fact to a
        topic, and the agent is told so rather than left to guess."""
        topic, fact, _ = await _nodes(storage)

        with pytest.raises(ValueError) as refused:
            await link(fact.id, topic.id, storage, edge_type="supports")

        assert "extracted_under_topic" in str(refused.value)
        assert "tagged_with_topic" in str(refused.value)

    async def test_derived_from_out_of_a_topic(self, storage):
        topic, fact, _ = await _nodes(storage)

        with pytest.raises(ValueError, match="inference -> fact"):
            await link(topic.id, fact.id, storage, edge_type="derived_from")

    async def test_subtopic_of_between_two_facts(self, storage):
        _, fact, _ = await _nodes(storage)
        other = Fact(content="the Cafe Landtmann opened in 1873", source_id="s1")
        await storage.store_node(other)

        with pytest.raises(ValueError, match="topic -> topic"):
            await link(fact.id, other.id, storage, edge_type="subtopic_of")

    async def test_extracted_under_topic_onto_an_inference(self, storage):
        _, fact, inference = await _nodes(storage)

        with pytest.raises(ValueError, match="fact -> topic"):
            await link(fact.id, inference.id, storage, edge_type="extracted_under_topic")

    async def test_a_pair_no_engine_type_joins_is_pointed_at_relation(self, storage):
        """Nothing runs from a topic to a fact, so there is no alternative to
        name and the refusal says what to do instead."""
        topic, fact, _ = await _nodes(storage)

        with pytest.raises(ValueError, match="relation"):
            await link(topic.id, fact.id, storage, edge_type="supports")


class TestLinkStillWritesAWellShapedEdge:
    async def test_supports_from_a_fact_to_an_inference(self, storage):
        _, fact, inference = await _nodes(storage)

        result, _ = await link(fact.id, inference.id, storage, edge_type="supports")

        [edge] = await storage.get_edges_from(fact.id)
        assert edge.id == result["edge_id"] and edge.type == EdgeType.SUPPORTS

    async def test_a_user_relation_joins_whatever_the_agent_says(self, storage):
        """`related` is open, so the shapes constrain nothing here: the agent
        coined the word and the word says what it joins."""
        topic, fact, _ = await _nodes(storage)

        result, _ = await link(topic.id, fact.id, storage, relation="discussed_in")

        assert "edge_id" in result


class TestTheOtherWritersAskTheSameQuestion:
    """Every path that takes both endpoints from a caller, checked at its own boundary.

    The rest build their edges out of nodes they made or resolved by kind, and
    cannot get the shape wrong: `update` creates a replacement of the kind it
    retires, `merge_facts` refuses a source that is not a fact, and
    `create_timelink` resolves a timeline through `get_timeline`.
    """

    async def test_supersede_by_refuses_a_replacement_of_another_kind(self, storage):
        topic, fact, _ = await _nodes(storage)

        with pytest.raises(ValueError, match="fact -> fact"):
            await tools.supersede_by(fact.id, topic.id, storage, because="it_was_wrong")

    async def test_record_contradiction_refuses_a_topic(self, storage):
        topic, fact, _ = await _nodes(storage)

        with pytest.raises(ValueError, match="fact -> fact"):
            await tools.record_contradiction(fact.id, topic.id, storage, judge=CRITIC)

    async def test_record_variant_refuses_a_topic(self, storage):
        topic, fact, _ = await _nodes(storage)

        with pytest.raises(ValueError, match="fact -> fact"):
            await tools.record_variant(fact.id, topic.id, storage, judge=CRITIC)

    async def test_a_similarity_verdict_refuses_a_mixed_pair(self, storage):
        """Returned rather than raised, like every other refusal on this path:
        the agent gets the reason back beside the verdicts that were recorded."""
        topic, fact, _ = await _nodes(storage)

        outcome = await apply_similarity_decision(
            storage,
            a_id=fact.id,
            b_id=topic.id,
            verdict="distinct",
            because="they came up together in one search",
            judge=CRITIC,
        )

        assert isinstance(outcome, SimilarityRefused)
        assert "fact -> topic" in outcome.reason

    async def test_a_synthesised_parent_refuses_a_child_that_is_not_a_topic(
        self, storage, embedder
    ):
        topic, fact, _ = await _nodes(storage)

        result, _ = await tools.apply_reflection(
            storage,
            embedder,
            parents=[{"children_ids": [topic.id, fact.id], "content": "coffee house history"}],
            judge=CRITIC,
        )

        assert result["parents_created"] == 0
        [refused] = result["parents_refused"]
        assert fact.id in refused["reason"] and "topics" in refused["reason"]


class TestTheRefusalReadsAsAnInstruction:
    def test_a_shape_is_described_as_the_pairs_it_joins(self):
        assert describe_edge_shape(EdgeType.SUPPORTS) == "fact -> inference"
        assert describe_edge_shape(EdgeType.RELATED) == "any two endpoints"

    def test_the_open_type_is_never_offered_as_an_alternative(self):
        """It fits every pair, so naming it would drown the suggestion that is
        actually about these two."""
        assert EdgeType.RELATED not in edge_types_joining(EndpointKind.FACT, EndpointKind.TOPIC)

    def test_a_well_shaped_pair_has_no_violation(self):
        assert (
            edge_shape_violation(EdgeType.SUPPORTS, EndpointKind.FACT, EndpointKind.INFERENCE)
            is None
        )
