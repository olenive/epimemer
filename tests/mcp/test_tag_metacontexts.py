"""A topic node created from a tag stands in every metacontext it is used from.

A tag used to be the one node `store_decomposition` wrote with no
`has_metacontext` edge, on the grounds that a name asserts nothing. The rest of
the system reads absence as *nobody spoke for this node*: nothing compares it,
nothing merges it, and no scoped read returns it, which made the most-used
topic nodes in a real graph the ones a scoped `search` dropped, along with every
`tagged_with_topic` edge that reached them. `graph_stats.nodes_without_metacontext`
could not stay at zero either, because the next ingest minted another one.

The union is the worst answer available for a claim and the right one for a
name. `dev-session-2026-09-08` standing in `the-real` and in a novel's
metacontext says exactly what is true: the name was used from both worlds.
`dev-docs/TAG_METACONTEXTS.md` carries the argument.
"""

import pytest

from epimemer.core.types import (
    BASE_METACONTEXT_ID,
    EdgeType,
    EmbeddingRecord,
    JudgeRef,
    NodeEdge,
    NodeType,
    Topic,
)
from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.mcp import tools
from epimemer.mcp.config import ServerConfig
from epimemer.pipelines.metacontexts import TAG_EXTRACTION_METHOD, created_from_tag

CRITIC = JudgeRef(agent_id="critic", digest="d1")


@pytest.fixture
def embedder():
    return MockEmbeddingProvider(model_id="mock-embed", dimension=8)


@pytest.fixture
def config():
    return ServerConfig(storage_backend="memory", embedding_provider="mock")


async def _fiction(storage) -> str:
    result, _ = await tools.create_metacontext(
        "The novel's world", storage, description="in-universe"
    )
    return result["metacontext_id"]


async def _ingest(storage, embedder, config, text, *, tags, metacontext_id, per_node_tags=None):
    """Ingest one paragraph stating one fact, carrying `tags`.

    One line per tag, because a name this graph has never seen is refused
    without one. It covers both places a tag can appear, which is the whole
    reason the dict sits at the call level rather than beside each `tags` list.
    """
    seg, _ = await tools.segment_text(text, storage, embedder, config)
    fact = {"content": text, **({"tags": per_node_tags} if per_node_tags else {})}
    stored, _ = await tools.store_decomposition(
        document_id=seg["document_id"],
        segments=[{"segment_id": seg["segments"][0]["segment_id"], "facts": [fact]}],
        storage=storage,
        embedding_provider=embedder,
        metacontext_id=metacontext_id,
        tags=tags,
        tag_descriptions={
            name: f"Everything this graph files under {name}."
            for name in [*tags, *(per_node_tags or [])]
        },
        judge=CRITIC,
    )
    return stored


async def _tag_named(storage, content: str) -> Topic:
    found = await storage.get_node_by_content(content, node_type=NodeType.TOPIC)
    assert isinstance(found, Topic) and created_from_tag(found)
    return found


async def _metacontext_edges(storage, node_id) -> list:
    return list(await storage.get_edges_from(node_id, edge_type=EdgeType.HAS_METACONTEXT))


async def _stands_in(storage, node_id) -> set[str]:
    return {edge.dst_id for edge in await _metacontext_edges(storage, node_id)}


async def _label(storage, metacontext_id: str) -> str:
    """What a read tool prints for a metacontext: its content, not its id."""
    metacontext = await storage.get_metacontext(metacontext_id)
    assert metacontext is not None
    return metacontext.content


async def _tag_topic(storage, embedder, name, *, metacontexts, vector=None) -> Topic:
    """A stored topic node created from a tag, standing where the caller says.

    The vector is supplied so two of them clear the merge bar, which is the only
    way to be sure the metacontext handling is what a merge test observed.
    """
    topic = Topic(content=name, source_id=None, extraction_method=TAG_EXTRACTION_METHOD)
    await storage.store_node(topic)
    vectors = await embedder.embed([name])
    await storage.store_embedding(
        EmbeddingRecord(
            item_id=topic.id,
            model_id=embedder.model_id,
            vector=vector if vector is not None else vectors[0],
        )
    )
    for metacontext in metacontexts:
        await storage.store_edge(
            NodeEdge(
                src_id=topic.id,
                dst_id=metacontext,
                type=EdgeType.HAS_METACONTEXT,
            )
        )
    return topic


class TestIngestPutsATagWhereItIsUsedFrom:
    async def test_a_new_tag_stands_in_the_ingests_metacontext(self, storage, embedder, config):
        await _ingest(
            storage,
            embedder,
            config,
            "The rota was rewritten.",
            tags=["design-decisions"],
            metacontext_id=BASE_METACONTEXT_ID,
        )

        tag = await _tag_named(storage, "design-decisions")
        assert await _stands_in(storage, tag.id) == {BASE_METACONTEXT_ID}

    async def test_a_tag_used_from_a_second_metacontext_stands_in_both(
        self, storage, embedder, config
    ):
        """The union grows with use, and one use writes one edge.

        Nothing here is a judgment: the tag stands where the nodes carrying it
        stand, which is a fact about the graph rather than a claim about a world.
        """
        fiction = await _fiction(storage)
        await _ingest(
            storage,
            embedder,
            config,
            "The rota was rewritten.",
            tags=["design-decisions"],
            metacontext_id=BASE_METACONTEXT_ID,
        )
        await _ingest(
            storage,
            embedder,
            config,
            "The council met in winter.",
            tags=["design-decisions"],
            metacontext_id=fiction,
        )

        tag = await _tag_named(storage, "design-decisions")
        edges = await _metacontext_edges(storage, tag.id)
        assert {edge.dst_id for edge in edges} == {BASE_METACONTEXT_ID, fiction}
        assert len(edges) == 2

    async def test_a_second_ingest_from_the_same_metacontext_writes_nothing(
        self, storage, embedder, config
    ):
        """A duplicate edge would make the set the reader derives depend on how
        many times the name was typed."""
        for text in ("The rota was rewritten.", "The rota was rewritten again."):
            await _ingest(
                storage,
                embedder,
                config,
                text,
                tags=["design-decisions"],
                metacontext_id=BASE_METACONTEXT_ID,
            )

        tag = await _tag_named(storage, "design-decisions")
        assert len(await _metacontext_edges(storage, tag.id)) == 1

    async def test_a_tag_repeated_within_one_call_gets_one_edge(self, storage, embedder, config):
        """The document-level tag and the node-level one resolve to a single
        topic node, so the edge is written once per tag per call rather than once
        per `tagged_with_topic` edge."""
        await _ingest(
            storage,
            embedder,
            config,
            "The rota was rewritten.",
            tags=["design-decisions"],
            per_node_tags=["design-decisions"],
            metacontext_id=BASE_METACONTEXT_ID,
        )

        tag = await _tag_named(storage, "design-decisions")
        assert len(await _metacontext_edges(storage, tag.id)) == 1

    async def test_the_edge_carries_the_ingesting_judge(self, storage, embedder, config):
        """Written in the same batch as everything else this call wrote, and
        stamped the same way, so `review(by_agent=…)` reaches it."""
        await _ingest(
            storage,
            embedder,
            config,
            "The rota was rewritten.",
            tags=["design-decisions"],
            metacontext_id=BASE_METACONTEXT_ID,
        )

        tag = await _tag_named(storage, "design-decisions")
        [edge] = await _metacontext_edges(storage, tag.id)
        assert edge.judged_by == CRITIC

    async def test_a_declared_graph_stays_at_zero_nodes_without_a_metacontext(
        self, storage, embedder, config
    ):
        """The readout `epimemer metacontexts declare` is checked with.

        It used to have no steady state meaning *done*: the sweep stamped every
        tag it found, and the next ingest minted one standing in nothing.
        """
        await _ingest(
            storage,
            embedder,
            config,
            "The rota was rewritten.",
            tags=["design-decisions"],
            metacontext_id=BASE_METACONTEXT_ID,
        )

        stats, _ = await tools.graph_stats(storage, default_reflect_threshold=10)
        assert stats["nodes_without_metacontext"] == 0


class TestAScopedReadReturnsTheTagsUsedFromTheScope:
    async def test_search_returns_the_tag_and_the_edge_that_reached_it(
        self, storage, embedder, config
    ):
        """The defect the union fixes: `_in_metacontext_nodes` dropped every tag
        for standing nowhere, and `_edges_among` then dropped each
        `tagged_with_topic` edge for having lost an endpoint."""
        fiction = await _fiction(storage)
        await _ingest(
            storage,
            embedder,
            config,
            "The rota was rewritten.",
            tags=["design-decisions"],
            metacontext_id=BASE_METACONTEXT_ID,
        )
        await _ingest(
            storage,
            embedder,
            config,
            "The council met in winter.",
            tags=["the-council"],
            metacontext_id=fiction,
        )

        real_tag = await _tag_named(storage, "design-decisions")
        fiction_tag = await _tag_named(storage, "the-council")
        found, _ = await tools.search(
            query="The rota was rewritten.",
            storage=storage,
            embedding_provider=embedder,
            k=10,
            metacontexts=[BASE_METACONTEXT_ID],
        )

        returned = {node["id"] for node in found["nodes"]}
        assert real_tag.id in returned
        assert fiction_tag.id not in returned
        assert any(
            edge["dst_id"] == real_tag.id and edge["type"] == EdgeType.TAGGED_WITH_TOPIC.value
            for edge in found["edges"]
        )

    async def test_the_returned_tag_is_labelled_with_its_metacontexts(
        self, storage, embedder, config
    ):
        await _ingest(
            storage,
            embedder,
            config,
            "The rota was rewritten.",
            tags=["design-decisions"],
            metacontext_id=BASE_METACONTEXT_ID,
        )

        tag = await _tag_named(storage, "design-decisions")
        found, _ = await tools.search(
            query="The rota was rewritten.",
            storage=storage,
            embedding_provider=embedder,
            k=10,
        )

        labelled = next(node for node in found["nodes"] if node["id"] == tag.id)
        assert labelled["metacontexts"] == [await _label(storage, BASE_METACONTEXT_ID)]

    async def test_topic_tree_labels_a_tag_like_any_other_entry(self, storage, embedder, config):
        """`topic_tree` used to document the gap rather than close it: a tag
        carried no `metacontexts` key, so a tree of tags said nothing about which
        worlds its branches came from."""
        await _ingest(
            storage,
            embedder,
            config,
            "The rota was rewritten.",
            tags=["design-decisions"],
            metacontext_id=BASE_METACONTEXT_ID,
        )

        tag = await _tag_named(storage, "design-decisions")
        tree, _ = await tools.topic_tree(tag.id, storage)

        assert tree["topic"]["metacontexts"] == [await _label(storage, BASE_METACONTEXT_ID)]


class TestAnAllTagMergeTakesTheUnion:
    async def test_the_survivor_stands_where_both_sources_stood(self, storage, embedder):
        """The exact-set-equality gate stays exempt for an all-tag merge, and the
        reason is now the answer rather than the absence of one: names take the
        union, so the survivor is used from every world its sources were."""
        fiction = await _fiction(storage)
        twin = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        a = await _tag_topic(
            storage,
            embedder,
            "design-decision",
            metacontexts=[BASE_METACONTEXT_ID],
            vector=twin,
        )
        b = await _tag_topic(
            storage,
            embedder,
            "design-decisions",
            metacontexts=[BASE_METACONTEXT_ID, fiction],
            vector=twin,
        )

        result, _ = await tools.apply_reflection(
            storage,
            embedder,
            merges=[{"source_ids": [a.id, b.id], "content": "design-decisions"}],
            judge=CRITIC,
        )

        assert result["topics_merged"] == 1
        survivor = await storage.get_node_by_content("design-decisions", node_type=NodeType.TOPIC)
        assert survivor is not None and survivor.id not in (a.id, b.id)
        assert await _stands_in(storage, survivor.id) == {BASE_METACONTEXT_ID, fiction}


class TestAnAllTagParentTakesTheUnion:
    """Parent synthesis used to gate an all-tag child set the way topic merge
    used to: `shared_metacontext_set` over children used from different worlds
    refused the parent. A parent over names gathers names, and a name stands in
    every metacontext it is used from, so the parent takes the union."""

    async def test_a_parent_over_tags_stands_where_any_child_stood(self, storage, embedder):
        fiction = await _fiction(storage)
        a = await _tag_topic(storage, embedder, "issue-53", metacontexts=[BASE_METACONTEXT_ID])
        b = await _tag_topic(
            storage, embedder, "validity", metacontexts=[BASE_METACONTEXT_ID, fiction]
        )

        result, _ = await tools.apply_reflection(
            storage,
            embedder,
            parents=[{"children_ids": [a.id, b.id], "content": "temporal validity"}],
            judge=CRITIC,
        )

        assert result["parents_created"] == 1
        assert result["parents_refused"] == []
        parent = next(
            node for node in await storage.query_nodes() if node.metadata.get("synthesized_from")
        )
        assert await _stands_in(storage, parent.id) == {BASE_METACONTEXT_ID, fiction}

    async def test_one_statement_child_keeps_the_equality_gate(self, storage, embedder):
        """The union is the answer for names only. A statement among the
        children is a claim, and a claim combined across worlds would assert in
        both, so the gate that refuses it stays."""
        fiction = await _fiction(storage)
        tag = await _tag_topic(storage, embedder, "issue-53", metacontexts=[BASE_METACONTEXT_ID])
        statement = Topic(
            content="The council rules by decree.",
            source_id=None,
            extraction_method="agent",
        )
        await storage.store_node(statement)
        await storage.store_embedding(
            EmbeddingRecord(
                item_id=statement.id,
                model_id=embedder.model_id,
                vector=(await embedder.embed([statement.content]))[0],
            )
        )
        await storage.store_edge(
            NodeEdge(src_id=statement.id, dst_id=fiction, type=EdgeType.HAS_METACONTEXT)
        )

        result, _ = await tools.apply_reflection(
            storage,
            embedder,
            parents=[{"children_ids": [tag.id, statement.id], "content": "governance"}],
            judge=CRITIC,
        )

        assert result["parents_created"] == 0
        assert len(result["parents_refused"]) == 1
        assert "metacontexts" in result["parents_refused"][0]["reason"]
