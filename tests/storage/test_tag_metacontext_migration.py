"""A graph whose tags stand nowhere is stamped from their uses when opened.

Schema version 5 puts every active topic node created from a tag in the union of
the metacontexts of the nodes tagged with it. Until it ran, a tag was the one
node ingest wrote with no `has_metacontext` edge, so a scoped read dropped it
and every `tagged_with_topic` edge that reached it, and
`graph_stats.nodes_without_metacontext` was nonzero again after the next ingest.

The stamping itself is `stamp_tag_metacontexts`, which is a walk over nodes and
edges rather than over rows: the in-memory backend runs the same function, and
the tests below check the rule there and its arrival on open here.
"""

import tempfile
from pathlib import Path

import pytest

from epimemer.core.types import (
    BASE_METACONTEXT_ID,
    EdgeType,
    Fact,
    Metacontext,
    NodeEdge,
    NodeStatus,
    Topic,
)
from epimemer.pipelines.metacontexts import TAG_EXTRACTION_METHOD, stamp_tag_metacontexts
from epimemer.storage.memory import InMemoryStorage
from epimemer.storage.surrealdb_adapter import SurrealDBStorage

FICTION = "petersburg-novel"


async def _metacontexts(store, node_id: str) -> set[str]:
    edges = await store.get_edges_from(node_id, edge_type=EdgeType.HAS_METACONTEXT)
    return {edge.dst_id for edge in edges}


async def _tag(store, name: str, *, metacontexts=()) -> Topic:
    topic = Topic(content=name, source_id=None, extraction_method=TAG_EXTRACTION_METHOD)
    await store.store_node(topic)
    for metacontext in metacontexts:
        await store.store_edge(
            NodeEdge(src_id=topic.id, dst_id=metacontext, type=EdgeType.HAS_METACONTEXT)
        )
    return topic


async def _tagged_fact(store, content: str, tag: Topic, *, metacontexts, status=NodeStatus.ACTIVE):
    fact = Fact(content=content, source_id="seg-1", status=status)
    await store.store_node(fact)
    await store.store_edge(NodeEdge(src_id=fact.id, dst_id=tag.id, type=EdgeType.TAGGED_WITH_TOPIC))
    for metacontext in metacontexts:
        await store.store_edge(
            NodeEdge(src_id=fact.id, dst_id=metacontext, type=EdgeType.HAS_METACONTEXT)
        )
    return fact


async def _seeded_graph(store):
    """The three tag populations a real graph holds when this first runs.

    A tag nobody ever declared, a tag a declaration sweep stamped `the-real`
    before it was also used from a novel, and a tag that tags nothing.
    """
    for metacontext in (BASE_METACONTEXT_ID, FICTION):
        await store.store_metacontext(Metacontext(id=metacontext, content=metacontext))

    bare = await _tag(store, "design-decisions")
    await _tagged_fact(store, "the rota was rewritten", bare, metacontexts=[BASE_METACONTEXT_ID])
    await _tagged_fact(store, "the council met in winter", bare, metacontexts=[FICTION])

    declared = await _tag(store, "dev-session", metacontexts=[BASE_METACONTEXT_ID])
    await _tagged_fact(store, "the pass was closed", declared, metacontexts=[FICTION])

    unused = await _tag(store, "never-applied")
    return {"bare": bare.id, "declared": declared.id, "unused": unused.id}


@pytest.fixture
async def memory_graph():
    store = InMemoryStorage()
    ids = await _seeded_graph(store)
    return store, ids


class TestTheStampingRule:
    async def test_a_tag_standing_nowhere_gains_the_union_of_its_uses(self, memory_graph):
        store, ids = memory_graph

        await stamp_tag_metacontexts(store)

        assert await _metacontexts(store, ids["bare"]) == {BASE_METACONTEXT_ID, FICTION}

    async def test_a_declared_tag_keeps_what_it_was_given_and_gains_the_rest(self, memory_graph):
        """Add-only, so a person's declaration survives a derivation.

        The sweep stamped this one `the-real` when nothing was tagged with it
        from anywhere; its one use is from the novel, and both now stand.
        """
        store, ids = memory_graph

        await stamp_tag_metacontexts(store)

        assert await _metacontexts(store, ids["declared"]) == {BASE_METACONTEXT_ID, FICTION}

    async def test_a_tag_that_tags_nothing_is_left_alone(self, memory_graph):
        """It is used from nowhere, so the union is empty and there is nothing to
        say about it until it is next used."""
        store, ids = memory_graph

        await stamp_tag_metacontexts(store)

        assert await _metacontexts(store, ids["unused"]) == set()

    async def test_a_rerun_writes_nothing(self, memory_graph):
        store, ids = memory_graph

        first = await stamp_tag_metacontexts(store)
        again = await stamp_tag_metacontexts(store)

        assert first.edges_written == 3
        assert again.edges_written == 0
        assert again.topics_seen == first.topics_seen
        assert await _metacontexts(store, ids["bare"]) == {BASE_METACONTEXT_ID, FICTION}

    async def test_a_statement_topic_is_not_touched(self):
        """The scan matches topic nodes created from a tag only. A statement
        topic is a claim about a world, and where it stands is a judgment
        somebody made rather than something to derive from its neighbours."""
        store = InMemoryStorage()
        prose = Topic(content="how the rota is decided", source_id="seg-1")
        await store.store_node(prose)
        await store.store_edge(
            NodeEdge(
                src_id=Fact(content="a claim", source_id="seg-1").id,
                dst_id=prose.id,
                type=EdgeType.TAGGED_WITH_TOPIC,
            )
        )

        result = await stamp_tag_metacontexts(store)

        assert result.topics_seen == 0
        assert await _metacontexts(store, prose.id) == set()


@pytest.fixture
def embedded_url():
    """A URL for an embedded store that survives being closed and reopened.

    `mem://` cannot serve here: the graph lives inside the object, so closing it
    is what the migration is supposed to run after.
    """
    with tempfile.TemporaryDirectory() as directory:
        yield f"surrealkv://{Path(directory) / 'graph.db'}"


async def test_an_embedded_graph_stamps_its_tags_on_open(embedded_url):
    """The readout `epimemer metacontexts declare` is checked with reaches zero
    on open, and stays there because ingest now writes the edge itself."""
    store = SurrealDBStorage(url=embedded_url)
    await store.connect()
    ids = await _seeded_graph(store)
    # The marker `_setup_schema` has just stamped, removed: this graph is
    # standing in for one written before the version existed.
    await store.db.query("DELETE schema_version:current")
    assert await store.count_nodes_without_metacontext() == 2
    await store.close()

    reopened = SurrealDBStorage(url=embedded_url)
    await reopened.connect()

    assert await _metacontexts(reopened, ids["bare"]) == {BASE_METACONTEXT_ID, FICTION}
    assert await _metacontexts(reopened, ids["declared"]) == {BASE_METACONTEXT_ID, FICTION}
    assert await _metacontexts(reopened, ids["unused"]) == set()
    # The tag tagging nothing is the one node left, and it is the one the rule
    # says nothing about: a name used from nowhere.
    assert await reopened.count_nodes_without_metacontext() == 1
    stored = await reopened.db.query("SELECT VALUE version FROM schema_version:current")
    assert int(stored[0]) == 5
    await reopened.close()

    # A second open changes nothing: the marker stops the step running, and it
    # would write nothing if it did.
    again = SurrealDBStorage(url=embedded_url)
    await again.connect()
    assert await _metacontexts(again, ids["bare"]) == {BASE_METACONTEXT_ID, FICTION}
    assert await _metacontexts(again, ids["declared"]) == {BASE_METACONTEXT_ID, FICTION}
    await again.close()
