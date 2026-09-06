"""What rests on a fact, once `supports` means one thing.

The extractor no longer writes `supports` from a fact to a topic: that reading
is `extracted_under_topic`. What it does not do is make the shape impossible.
`link` takes any engine edge type between any two nodes it can find, so a
`supports` edge onto a topic is still writable by hand, and
`dependent_inference_ids` still has to check what it reached. These tests pin
the answer to what it was: the same set from a fact carrying both kinds of
dependency edge, and a topic never in it, whichever edge points at it.
"""

from epimemer.core.types import EdgeType, Fact, Inference, NodeEdge, Topic
from epimemer.pipelines.reflection.review import dependent_inference_ids


async def test_both_edge_kinds_count_and_the_topic_does_not(storage):
    fact = Fact(content="the deploy failed", source_id="seg-1")
    supported = Inference(content="deployments have been failing", source_id="seg-1")
    drawn_from = Inference(content="the release process is fragile", source_id="seg-1")
    topic = Topic(content="Deployments", source_id="seg-1")
    for node in (fact, supported, drawn_from, topic):
        await storage.store_node(node)

    await storage.store_edge(NodeEdge(src_id=fact.id, dst_id=supported.id, type=EdgeType.SUPPORTS))
    await storage.store_edge(
        NodeEdge(src_id=drawn_from.id, dst_id=fact.id, type=EdgeType.DERIVED_FROM)
    )
    await storage.store_edge(
        NodeEdge(src_id=fact.id, dst_id=topic.id, type=EdgeType.EXTRACTED_UNDER_TOPIC)
    )

    assert set(await dependent_inference_ids(fact.id, storage)) == {
        supported.id,
        drawn_from.id,
    }


async def test_one_inference_reached_both_ways_is_listed_once(storage):
    fact = Fact(content="the deploy failed", source_id="seg-1")
    inference = Inference(content="deployments have been failing", source_id="seg-1")
    await storage.store_node(fact)
    await storage.store_node(inference)

    await storage.store_edge(NodeEdge(src_id=fact.id, dst_id=inference.id, type=EdgeType.SUPPORTS))
    await storage.store_edge(
        NodeEdge(src_id=inference.id, dst_id=fact.id, type=EdgeType.DERIVED_FROM)
    )

    assert await dependent_inference_ids(fact.id, storage) == [inference.id]


async def test_a_hand_written_supports_edge_onto_a_topic_is_not_a_dependent(storage):
    """The shape `link` will still write, and the reason the kind check stays.

    `link(fact, topic, edge_type="supports")` is accepted: it checks that both
    nodes exist and nothing about what they are. Were the destination taken on
    trust, this topic would be returned as a dependent inference and collect
    inference review labels on every supersession of the fact.
    """
    fact = Fact(content="the deploy failed", source_id="seg-1")
    topic = Topic(content="Deployments", source_id="seg-1")
    inference = Inference(content="deployments have been failing", source_id="seg-1")
    for node in (fact, topic, inference):
        await storage.store_node(node)

    await storage.store_edge(NodeEdge(src_id=fact.id, dst_id=topic.id, type=EdgeType.SUPPORTS))
    await storage.store_edge(NodeEdge(src_id=fact.id, dst_id=inference.id, type=EdgeType.SUPPORTS))

    assert await dependent_inference_ids(fact.id, storage) == [inference.id]


async def test_a_fact_nothing_rests_on_has_no_dependents(storage):
    fact = Fact(content="the deploy failed", source_id="seg-1")
    topic = Topic(content="Deployments", source_id="seg-1")
    await storage.store_node(fact)
    await storage.store_node(topic)
    await storage.store_edge(
        NodeEdge(src_id=fact.id, dst_id=topic.id, type=EdgeType.EXTRACTED_UNDER_TOPIC)
    )

    assert await dependent_inference_ids(fact.id, storage) == []
