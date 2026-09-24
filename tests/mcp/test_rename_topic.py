"""A topic node's name changes in place (`rename_topic`).

A topic's name is a label rather than a claim, so renaming one is not a
correction and not a change in the world. `update` offers only those two, and
each does harm here: a world-change leaves the topic's facts and inferences on
the retired version, and a correction moves them but records the old name as a
mistake. A rename keeps the node: same id, same edges, same description, and a
trail of the names it used to have.

Both backends via the `storage` fixture.
"""

from epimemer.core.types import (
    DecisionKind,
    EdgeType,
    EmbeddingRecord,
    Fact,
    JudgeRef,
    NodeEdge,
    NodeStatus,
    Topic,
)
from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.mcp import tools

_EMBEDDER = MockEmbeddingProvider(model_id="mock-embed", dimension=8)

CRITIC = JudgeRef(agent_id="critic", digest="d1")


async def _stored(storage, node):
    """`node` stored with an embedding of its content, as ingest would leave it."""
    await storage.store_node(node)
    vector = (await _EMBEDDER.embed([node.content]))[0]
    await storage.store_embedding(
        EmbeddingRecord(item_id=node.id, model_id=_EMBEDDER.model_id, vector=vector)
    )
    return node


async def _described_topic(storage, content="Leningrad city council"):
    return await _stored(
        storage,
        Topic(
            content=content,
            description="The elected body that governs the city on the Neva.",
            metadata={"description_history": [{"replaced": "The city government."}]},
        ),
    )


async def _rename(storage, topic, name, judge=CRITIC):
    result, _ = await tools.rename_topic(topic, storage, _EMBEDDER, name=name, judge=judge)
    return result


class TestTheNodeStaysTheSame:
    async def test_the_id_and_the_edges_stand(self, storage):
        topic = await _described_topic(storage)
        fact = await _stored(storage, Fact(content="The council met on Monday.", source_id="seg-1"))
        tag = await _stored(storage, Topic(content="local-government"))
        await storage.store_edge(
            NodeEdge(src_id=fact.id, dst_id=topic.id, type=EdgeType.EXTRACTED_UNDER_TOPIC)
        )
        await storage.store_edge(
            NodeEdge(src_id=topic.id, dst_id=tag.id, type=EdgeType.TAGGED_WITH_TOPIC)
        )

        result = await _rename(storage, topic.id, "Saint Petersburg city council")

        assert result["status"] == "renamed"
        assert result["topic_id"] == topic.id
        renamed = await storage.get_node(topic.id)
        assert renamed.content == "Saint Petersburg city council"
        assert renamed.status is NodeStatus.ACTIVE
        under = await storage.get_edges_to(topic.id, edge_type=EdgeType.EXTRACTED_UNDER_TOPIC)
        assert [edge.src_id for edge in under] == [fact.id]
        tagged = await storage.get_edges_from(topic.id, edge_type=EdgeType.TAGGED_WITH_TOPIC)
        assert [edge.dst_id for edge in tagged] == [tag.id]

    async def test_the_description_and_its_history_are_kept(self, storage):
        topic = await _described_topic(storage)

        await _rename(storage, topic.id, "Saint Petersburg city council")

        renamed = await storage.get_node(topic.id)
        assert renamed.description == "The elected body that governs the city on the Neva."
        assert renamed.metadata["description_history"] == [{"replaced": "The city government."}]

    async def test_the_previous_name_is_recorded_with_the_judge(self, storage):
        topic = await _described_topic(storage)

        await _rename(storage, topic.id, "Petrograd city council")
        await _rename(storage, topic.id, "Saint Petersburg city council")

        trail = (await storage.get_node(topic.id)).metadata["previous_names"]
        assert [entry["from"] for entry in trail] == [
            "Leningrad city council",
            "Petrograd city council",
        ]
        assert all(entry["judged_by"]["agent_id"] == "critic" for entry in trail)
        assert all(entry["at"] for entry in trail)

    async def test_a_topic_is_found_by_its_old_name_before_the_rename(self, storage):
        topic = await _described_topic(storage)

        result = await _rename(storage, "Leningrad city council", "Saint Petersburg city council")

        assert result["topic_id"] == topic.id


class TestTheNewNameIsTheOneThatResolves:
    async def test_search_finds_the_topic_by_its_new_name(self, storage):
        topic = await _described_topic(storage)
        await _stored(storage, Topic(content="Moscow city council"))

        await _rename(storage, topic.id, "Saint Petersburg city council")

        records = await storage.get_embeddings_for_item(topic.id, model_id=_EMBEDDER.model_id)
        assert len(records) == 1
        expected = (await _EMBEDDER.embed(["Saint Petersburg city council"]))[0]
        assert records[0].vector == expected
        found, _ = await tools.search("Saint Petersburg city council", storage, _EMBEDDER, k=5)
        assert found["nodes"][0]["id"] == topic.id

    async def test_the_old_name_no_longer_resolves(self, storage):
        topic = await _described_topic(storage)

        await _rename(storage, topic.id, "Saint Petersburg city council")

        assert (
            await tools._resolve_node_reference("Leningrad city council", storage)
            == "Leningrad city council"
        )
        assert (
            await tools._resolve_node_reference("Saint Petersburg city council", storage)
            == topic.id
        )

    async def test_the_rename_is_journalled_under_its_own_kind(self, storage):
        topic = await _described_topic(storage)

        await _rename(storage, topic.id, "Saint Petersburg city council")

        rows = await storage.query_decisions(kinds=[DecisionKind.TOPIC_RENAME])
        assert len(rows) == 1
        assert rows[0].subject_ids == [topic.id]
        assert rows[0].judged_by.agent_id == "critic"
        assert await storage.query_decisions(kinds=[DecisionKind.CORRECTION]) == []
        assert await storage.query_decisions(kinds=[DecisionKind.ENRICHMENT]) == []


class TestRefusals:
    """Each refusal writes nothing: the node, its trail and the journal are untouched."""

    async def _assert_untouched(self, storage, topic):
        stored = await storage.get_node(topic.id)
        assert stored.content == topic.content
        assert "previous_names" not in stored.metadata
        assert await storage.query_decisions(kinds=[DecisionKind.TOPIC_RENAME]) == []

    async def test_an_empty_name(self, storage):
        topic = await _described_topic(storage)

        result = await _rename(storage, topic.id, "   ")

        assert result["status"] == "refused"
        assert result["reason"]
        await self._assert_untouched(storage, topic)

    async def test_the_same_name(self, storage):
        topic = await _described_topic(storage)

        result = await _rename(storage, topic.id, "Leningrad city council")

        assert result["status"] == "refused"
        await self._assert_untouched(storage, topic)

    async def test_a_node_that_is_not_a_topic(self, storage):
        fact = await _stored(storage, Fact(content="The council met on Monday.", source_id="seg-1"))

        result = await _rename(storage, fact.id, "Something else")

        assert result["status"] == "refused"
        assert (await storage.get_node(fact.id)).content == "The council met on Monday."
        assert await storage.query_decisions(kinds=[DecisionKind.TOPIC_RENAME]) == []

    async def test_a_reference_that_names_nothing(self, storage):
        result = await _rename(storage, "no-such-topic", "Something else")

        assert result["status"] == "refused"

    async def test_a_topic_that_is_not_active(self, storage):
        topic = await _stored(
            storage, Topic(content="Leningrad city council", status=NodeStatus.HISTORICAL)
        )

        result = await _rename(storage, topic.id, "Saint Petersburg city council")

        assert result["status"] == "refused"
        await self._assert_untouched(storage, topic)

    async def test_a_name_another_active_topic_holds_up_to_spelling(self, storage):
        topic = await _described_topic(storage, content="city_council")
        holder = await _stored(storage, Topic(content="City Council"))

        result = await _rename(storage, topic.id, "city-council")

        assert result["status"] == "refused"
        assert result["holder_id"] == holder.id
        assert "reflect" in result["reason"]
        await self._assert_untouched(storage, topic)
        assert (await storage.get_node(holder.id)).content == "City Council"

    async def test_a_change_of_spelling_alone_is_allowed(self, storage):
        """The topic's own key matches the new name, and it is not another topic."""
        topic = await _described_topic(storage, content="city_council")

        result = await _rename(storage, topic.id, "City Council")

        assert result["status"] == "renamed"
        assert (await storage.get_node(topic.id)).content == "City Council"
