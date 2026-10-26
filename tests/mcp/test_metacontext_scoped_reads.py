"""A scoped read must not leak another metacontext through a shared topic node.

One topic node can be pointed at from several worlds: every node tagged with a
name points at the same topic node whatever world it was claimed in, and a
topic node written before anybody named a metacontext stands in none. Either
way it is a bridge: a fact from a novel and a fact about real history are one
hop apart, through a node neither of them is a claim about.

Filtering the node list is not enough on its own. `search` used to return
`query_result.edges` and `query_result.segments` untouched, so a scoped search
handed back edges naming nodes it had just removed and passages from documents
ingested under another metacontext. `query_graph`, `find_nodes` and
`topic_tree` had no scope at all, and `query_graph` did not even say which
metacontext a returned neighbour stood in — the silent case, where the caller
has no way to tell without a second call per node.

So these tests assert the whole response is scoped: nodes, edges, and the
passages behind them.
"""

import pytest
from pydantic import BaseModel

from epimemer.core.types import (
    BASE_METACONTEXT_ID,
    EdgeType,
    EmbeddingRecord,
    Fact,
    Metacontext,
    NodeEdge,
    RawDocument,
    Segment,
    Topic,
)
from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.mcp.tools import (
    find_nodes,
    get_metacontexts_for_node,
    query_graph,
    record_variant,
    search,
    set_metacontext_base,
    topic_tree,
)

REAL_TEXT = "Anarres is the subject of a history seminar."
FICTION_TEXT = "Anarres has no property and no police."
UNDECOMPOSED_TEXT = "Anarres appears in a passage nobody has decomposed."


@pytest.fixture
def embedding_provider() -> MockEmbeddingProvider:
    return MockEmbeddingProvider(model_id="mock-embed", dimension=8)


async def _passage(storage, text: str) -> Segment:
    """One document, one segment, both stored: the passage a search can match."""
    doc = RawDocument(content=text, source=text[:20])
    await storage.store_document(doc)
    segment = Segment(source_id=doc.id, text=text, span_start=0, span_end=len(text))
    await storage.store_segment(segment)
    return segment


async def _fact(storage, model_id, content, vector, *, segment, metacontext_id, topic=None) -> Fact:
    """A fact extracted from `segment`, standing in `metacontext_id`."""
    fact = Fact(content=content, source_id=segment.id)
    await storage.store_node(fact)
    await storage.store_embedding(
        EmbeddingRecord(item_id=fact.id, model_id=model_id, vector=vector)
    )
    await storage.store_edge(
        NodeEdge(src_id=fact.id, dst_id=metacontext_id, type=EdgeType.HAS_METACONTEXT)
    )
    if topic is not None:
        await storage.store_edge(
            NodeEdge(src_id=fact.id, dst_id=topic.id, type=EdgeType.TAGGED_WITH_TOPIC)
        )
    return fact


class TwoWorlds(BaseModel):
    """What `_two_worlds` built, named rather than positional."""

    fiction: Metacontext
    shared: Topic
    real_passage: Segment
    fiction_passage: Segment
    undecomposed: Segment
    real_fact: Fact
    fiction_fact: Fact


async def _two_worlds(storage, embedding_provider, query: str) -> TwoWorlds:
    """Two facts sharing one topic node that stands in no metacontext, one world each.

    Both facts carry the query's own vector, so the vector arm returns both and
    the metacontext filter is the only thing deciding what comes back. A third
    passage has nothing extracted from it, which is the case an unscoped search
    is promised and a scoped one cannot place.
    """
    fiction = Metacontext(content="The novel")
    await storage.store_metacontext(fiction)

    shared = Topic(content="anarres", source_id="tag")
    await storage.store_node(shared)

    vector = (await embedding_provider.embed([query]))[0]
    real_passage = await _passage(storage, REAL_TEXT)
    fiction_passage = await _passage(storage, FICTION_TEXT)
    undecomposed = await _passage(storage, UNDECOMPOSED_TEXT)

    return TwoWorlds(
        fiction=fiction,
        shared=shared,
        real_passage=real_passage,
        fiction_passage=fiction_passage,
        undecomposed=undecomposed,
        real_fact=await _fact(
            storage,
            embedding_provider.model_id,
            "Anarres is named after a real anarchist tradition.",
            vector,
            segment=real_passage,
            metacontext_id=BASE_METACONTEXT_ID,
            topic=shared,
        ),
        fiction_fact=await _fact(
            storage,
            embedding_provider.model_id,
            "Anarres has no property.",
            vector,
            segment=fiction_passage,
            metacontext_id=fiction.id,
            topic=shared,
        ),
    )


class TestScopedSearchScopesTheWholeResponse:
    async def test_no_fiction_node_edge_or_passage_in_a_real_search(
        self, storage, embedding_provider
    ):
        """The bleed, in one call. Expansion walks to the shared topic node from
        both facts before the filter runs, so the edge list named the fiction
        fact even though the node list did not, and the fiction document's raw
        passage came back beside it."""
        query = "anarres"
        world = await _two_worlds(storage, embedding_provider, query)

        result, _ = await search(
            query,
            storage,
            embedding_provider,
            k=10,
            graph_hops=1,
            terms=["Anarres"],
            metacontexts=[BASE_METACONTEXT_ID],
        )

        ids = {node["id"] for node in result["nodes"]}
        assert world.real_fact.id in ids
        assert world.fiction_fact.id not in ids

        endpoints = {e["src_id"] for e in result["edges"]} | {e["dst_id"] for e in result["edges"]}
        assert world.fiction_fact.id not in endpoints
        # Nothing an edge names was left out of the node list, so no returned
        # edge is a live reference into a metacontext the caller scoped away.
        assert endpoints <= ids

        segments = {hit["segment_id"] for hit in result["segments"]}
        assert world.real_passage.id in segments
        assert world.fiction_passage.id not in segments

    async def test_a_passage_with_nothing_extracted_is_dropped_when_scoped(
        self, storage, embedding_provider
    ):
        """It says nothing about which world it belongs to, and the caller has
        named the worlds they will read."""
        query = "anarres"
        world = await _two_worlds(storage, embedding_provider, query)

        result, _ = await search(
            query,
            storage,
            embedding_provider,
            k=10,
            graph_hops=1,
            terms=["Anarres"],
            metacontexts=[BASE_METACONTEXT_ID],
        )
        assert world.undecomposed.id not in {hit["segment_id"] for hit in result["segments"]}

    async def test_an_unscoped_search_still_returns_it(self, storage, embedding_provider):
        """*Where did I read that?* is answered for every passage that matched,
        decomposed or not, which is what the unscoped promise says."""
        query = "anarres"
        world = await _two_worlds(storage, embedding_provider, query)

        result, _ = await search(
            query,
            storage,
            embedding_provider,
            k=10,
            graph_hops=1,
            terms=["Anarres"],
        )
        segments = {hit["segment_id"] for hit in result["segments"]}
        assert world.undecomposed.id in segments
        assert {world.real_passage.id, world.fiction_passage.id} <= segments


class TestQueryGraphLabelsAndScopes:
    async def test_unscoped_returns_both_worlds_and_says_which_is_which(
        self, storage, embedding_provider
    ):
        """The silent case. Both facts hang off the shared topic node one hop
        out, and without labels the caller cannot tell the novel's claim from
        the real one without a call per node."""
        world = await _two_worlds(storage, embedding_provider, "anarres")

        result, _ = await query_graph(world.shared.id, storage, hops=1)

        by_id = {node["id"]: node for node in result["nodes"]}
        assert {world.real_fact.id, world.fiction_fact.id} <= by_id.keys()
        assert by_id[world.real_fact.id]["metacontexts"] == ["The Real"]
        assert by_id[world.fiction_fact.id]["metacontexts"] == ["The novel"]
        # The shared topic node stands in no metacontext, so it carries no
        # label rather than an empty one.
        assert "metacontexts" not in by_id[world.shared.id]

    async def test_scoped_keeps_the_seed_and_drops_the_other_world(
        self, storage, embedding_provider
    ):
        """The seed comes back whether or not it stands in a named metacontext,
        because the caller named it — which is what makes a topic node standing
        outside the scope usable as a starting point at all."""
        world = await _two_worlds(storage, embedding_provider, "anarres")

        result, _ = await query_graph(
            world.shared.id, storage, hops=1, metacontexts=[BASE_METACONTEXT_ID]
        )

        ids = {node["id"] for node in result["nodes"]}
        assert ids == {world.shared.id, world.real_fact.id}
        endpoints = {e["src_id"] for e in result["edges"]} | {e["dst_id"] for e in result["edges"]}
        assert endpoints <= ids

    async def test_an_id_that_names_nothing_is_refused(self, storage, embedding_provider):
        world = await _two_worlds(storage, embedding_provider, "anarres")

        with pytest.raises(ValueError, match="does not exist in graph") as refusal:
            await query_graph(world.shared.id, storage, hops=1, metacontexts=["mc-elsewhere"])
        assert BASE_METACONTEXT_ID in str(refusal.value)


class TestFindNodesScoping:
    async def test_only_the_metacontext_named_comes_back(self, storage, embedding_provider):
        world = await _two_worlds(storage, embedding_provider, "anarres")

        result, _ = await find_nodes(
            storage,
            tagged_with_topic=world.shared.id,
            metacontexts=[BASE_METACONTEXT_ID],
        )

        assert [node["id"] for node in result["nodes"]] == [world.real_fact.id]
        assert result["nodes"][0]["metacontexts"] == ["The Real"]

    async def test_unscoped_returns_both_with_their_labels(self, storage, embedding_provider):
        world = await _two_worlds(storage, embedding_provider, "anarres")

        result, _ = await find_nodes(storage, tagged_with_topic=world.shared.id)

        labels = {node["id"]: node.get("metacontexts") for node in result["nodes"]}
        assert labels == {
            world.real_fact.id: ["The Real"],
            world.fiction_fact.id: ["The novel"],
        }

    async def test_the_limit_cuts_what_survived_the_filter(self, storage, embedding_provider):
        """A truncation that ran first would answer "nothing here stands in that
        metacontext" whenever the first page happened to be another world's."""
        world = await _two_worlds(storage, embedding_provider, "anarres")
        vector = (await embedding_provider.embed(["anarres"]))[0]
        for i in range(3):
            await _fact(
                storage,
                embedding_provider.model_id,
                f"A further in-world claim {i}.",
                vector,
                segment=world.fiction_passage,
                metacontext_id=world.fiction.id,
                topic=world.shared,
            )

        result, _ = await find_nodes(
            storage,
            tagged_with_topic=world.shared.id,
            metacontexts=[BASE_METACONTEXT_ID],
            limit=1,
        )
        assert [node["id"] for node in result["nodes"]] == [world.real_fact.id]

    async def test_an_id_that_names_nothing_is_refused(self, storage, embedding_provider):
        world = await _two_worlds(storage, embedding_provider, "anarres")

        with pytest.raises(ValueError, match="does not exist in graph") as refusal:
            await find_nodes(
                storage,
                tagged_with_topic=world.shared.id,
                metacontexts=["mc-elsewhere"],
            )
        # The refusal names what does exist, so a caller holding a stale id can
        # see the graph's own list rather than guess again.
        assert BASE_METACONTEXT_ID in str(refusal.value)


async def _hierarchy(storage):
    """A parent topic split into one real subtopic and one from the novel."""
    fiction = Metacontext(content="The novel")
    await storage.store_metacontext(fiction)

    parent = Topic(content="Property", source_id="s1")
    real_child = Topic(content="Property law in the nineteenth century", source_id="s1")
    fiction_child = Topic(content="Property on Anarres", source_id="s1")
    grandchild = Topic(content="Housing allocation on Anarres", source_id="s1")
    for node in (parent, real_child, fiction_child, grandchild):
        await storage.store_node(node)
    for child, mc in (
        (parent, BASE_METACONTEXT_ID),
        (real_child, BASE_METACONTEXT_ID),
        (fiction_child, fiction.id),
        (grandchild, fiction.id),
    ):
        await storage.store_edge(
            NodeEdge(src_id=child.id, dst_id=mc, type=EdgeType.HAS_METACONTEXT)
        )
    for child, its_parent in (
        (real_child, parent),
        (fiction_child, parent),
        (grandchild, fiction_child),
    ):
        await storage.store_edge(
            NodeEdge(src_id=child.id, dst_id=its_parent.id, type=EdgeType.SUBTOPIC_OF)
        )
    return parent, real_child, fiction_child, grandchild


class TestTopicTreeScoping:
    async def test_unscoped_shows_both_branches_with_their_metacontexts(self, storage):
        parent, real_child, fiction_child, _ = await _hierarchy(storage)

        result, _ = await topic_tree(parent.id, storage, depth=2)

        labels = {entry["id"]: entry.get("metacontexts") for entry in result["subtopics"]}
        assert labels == {
            real_child.id: ["The Real"],
            fiction_child.id: ["The novel"],
        }
        assert result["topic"]["metacontexts"] == ["The Real"]

    async def test_scoped_keeps_the_named_topic_and_prunes_the_other_branch(self, storage):
        parent, real_child, fiction_child, grandchild = await _hierarchy(storage)

        result, _ = await topic_tree(
            parent.id, storage, depth=2, metacontexts=[BASE_METACONTEXT_ID]
        )

        assert result["topic"]["id"] == parent.id
        assert [entry["id"] for entry in result["subtopics"]] == [real_child.id]
        # The branch goes with the subtopic that carried it: what hangs below a
        # topic the caller will not be shown is a shape they cannot read.
        rendered = str(result)
        assert fiction_child.id not in rendered
        assert grandchild.id not in rendered

    async def test_the_named_topic_comes_back_even_standing_elsewhere(self, storage):
        """The caller named it, so it is returned the way `query_graph` returns
        its seed. Its ancestors and subtopics are still filtered."""
        parent, real_child, fiction_child, grandchild = await _hierarchy(storage)

        result, _ = await topic_tree(
            fiction_child.id, storage, depth=2, metacontexts=[BASE_METACONTEXT_ID]
        )

        assert result["topic"]["id"] == fiction_child.id
        assert result["subtopics"] == []
        assert [entry["id"] for entry in result["ancestors"]] == [parent.id]

    async def test_an_id_that_names_nothing_is_refused(self, storage):
        parent, _, _, _ = await _hierarchy(storage)

        with pytest.raises(ValueError, match="does not exist in graph") as refusal:
            await topic_tree(parent.id, storage, metacontexts=["mc-elsewhere"])
        assert BASE_METACONTEXT_ID in str(refusal.value)


# --- A metacontext's base ---

IN_1990 = {"instant_kind": "precise", "at": "1990-01-01"}


async def _novel_on_the_real(storage, embedding_provider, query: str) -> TwoWorlds:
    """`_two_worlds` with the novel based on `the-real`, diverging in 1990."""
    world = await _two_worlds(storage, embedding_provider, query)
    await set_metacontext_base(
        world.fiction.id, storage, base_id=BASE_METACONTEXT_ID, diverges=IN_1990
    )
    return world


class TestABaseIsReportedWhereAReaderChoosesWhatToSearch:
    """The relationship is learned from the graph, on the reads that are about
    a metacontext, and nothing from the base comes back unless it is named."""

    async def test_get_metacontexts_carries_each_ones_base(self, storage, embedding_provider):
        world = await _novel_on_the_real(storage, embedding_provider, "anarres")

        fiction, _ = await get_metacontexts_for_node(world.fiction_fact.id, storage)
        real, _ = await get_metacontexts_for_node(world.real_fact.id, storage)

        (stands_in,) = fiction["metacontexts"]
        assert stands_in["based_on"]["metacontext_id"] == BASE_METACONTEXT_ID
        assert stands_in["based_on"]["diverges"]["at"].startswith("1990-01-01")
        (real_world,) = real["metacontexts"]
        assert real_world["based_on"] is None

    async def test_a_search_scoped_to_it_names_the_base_and_returns_nothing_from_it(
        self, storage, embedding_provider
    ):
        world = await _novel_on_the_real(storage, embedding_provider, "anarres")

        result, _ = await search(
            "anarres", storage, embedding_provider, metacontexts=[world.fiction.id]
        )

        (base,) = result["metacontext_bases"].values()
        assert set(result["metacontext_bases"]) == {world.fiction.id}
        assert base["metacontext_id"] == BASE_METACONTEXT_ID
        assert base["content"] == "The Real"
        assert base["diverges"]["at"].startswith("1990-01-01")
        ids = {node["id"] for node in result["nodes"]}
        assert world.fiction_fact.id in ids
        assert world.real_fact.id not in ids

    async def test_a_search_naming_no_metacontext_with_a_base_has_no_such_key(
        self, storage, embedding_provider
    ):
        await _novel_on_the_real(storage, embedding_provider, "anarres")

        scoped, _ = await search(
            "anarres", storage, embedding_provider, metacontexts=[BASE_METACONTEXT_ID]
        )
        unscoped, _ = await search("anarres", storage, embedding_provider)

        assert "metacontext_bases" not in scoped
        assert "metacontext_bases" not in unscoped

    async def test_find_nodes_reports_it_only_when_scoped_to_it(self, storage, embedding_provider):
        world = await _novel_on_the_real(storage, embedding_provider, "anarres")

        scoped, _ = await find_nodes(
            storage, tagged_with_topic=world.shared.id, metacontexts=[world.fiction.id]
        )
        real, _ = await find_nodes(
            storage, tagged_with_topic=world.shared.id, metacontexts=[BASE_METACONTEXT_ID]
        )
        unscoped, _ = await find_nodes(storage, tagged_with_topic=world.shared.id)

        assert set(scoped["metacontext_bases"]) == {world.fiction.id}
        assert "metacontext_bases" not in real
        assert "metacontext_bases" not in unscoped


class TestVariantsIn:
    """Where a world departs from the one it builds on, as a traversal."""

    async def test_returns_each_variant_pair_with_the_base_fact(self, storage, embedding_provider):
        world = await _novel_on_the_real(storage, embedding_provider, "anarres")
        await record_variant(world.fiction_fact.id, world.real_fact.id, storage)

        result, meta = await find_nodes(storage, variants_in=world.fiction.id)

        (found,) = result["nodes"]
        assert found["id"] == world.fiction_fact.id
        assert found["metacontexts"] == ["The novel"]
        assert found["varies_from"] == {
            "id": world.real_fact.id,
            "content": world.real_fact.content,
            "metacontexts": ["The Real"],
        }
        assert {node.node_id for node in meta.retrieved} == {
            world.fiction_fact.id,
            world.real_fact.id,
        }

    async def test_a_variant_recorded_from_the_base_side_is_found_too(
        self, storage, embedding_provider
    ):
        world = await _novel_on_the_real(storage, embedding_provider, "anarres")
        await record_variant(world.real_fact.id, world.fiction_fact.id, storage)

        result, _ = await find_nodes(storage, variants_in=world.fiction.id)

        assert [node["varies_from"]["id"] for node in result["nodes"]] == [world.real_fact.id]

    async def test_a_variant_in_a_metacontext_that_is_not_the_base_is_left_out(
        self, storage, embedding_provider
    ):
        world = await _novel_on_the_real(storage, embedding_provider, "anarres")
        sequel = Metacontext(content="The sequel")
        await storage.store_metacontext(sequel)
        vector = (await embedding_provider.embed(["anarres"]))[0]
        elsewhere = await _fact(
            storage,
            embedding_provider.model_id,
            "Anarres has a police force.",
            vector,
            segment=world.fiction_passage,
            metacontext_id=sequel.id,
        )
        await record_variant(world.fiction_fact.id, elsewhere.id, storage)

        result, _ = await find_nodes(storage, variants_in=world.fiction.id)

        assert result["nodes"] == []

    async def test_a_metacontext_with_no_base_is_refused(self, storage, embedding_provider):
        world = await _two_worlds(storage, embedding_provider, "anarres")

        with pytest.raises(ValueError, match="has no base"):
            await find_nodes(storage, variants_in=world.fiction.id)

    async def test_it_does_not_combine_with_sourced_from(self, storage, embedding_provider):
        world = await _novel_on_the_real(storage, embedding_provider, "anarres")

        with pytest.raises(ValueError, match="exactly one") as refusal:
            await find_nodes(
                storage, variants_in=world.fiction.id, sourced_from=world.fiction_passage.id
            )
        assert "sourced_from, variants_in" in str(refusal.value)

    async def test_node_types_and_metacontexts_narrow_it(self, storage, embedding_provider):
        world = await _novel_on_the_real(storage, embedding_provider, "anarres")
        await record_variant(world.fiction_fact.id, world.real_fact.id, storage)

        topics_only, _ = await find_nodes(
            storage, variants_in=world.fiction.id, node_types=["topic"]
        )
        scoped_away, _ = await find_nodes(
            storage, variants_in=world.fiction.id, metacontexts=[BASE_METACONTEXT_ID]
        )
        scoped_to_it, _ = await find_nodes(
            storage, variants_in=world.fiction.id, metacontexts=[world.fiction.id]
        )

        assert topics_only["nodes"] == []
        assert scoped_away["nodes"] == []
        assert [node["id"] for node in scoped_to_it["nodes"]] == [world.fiction_fact.id]
        assert set(scoped_to_it["metacontext_bases"]) == {world.fiction.id}
