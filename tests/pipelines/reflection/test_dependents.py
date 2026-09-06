"""What rests on a fact, once `supports` means one thing.

`dependent_inference_ids` used to fetch every `supports` destination and keep
the ones that were inferences, because the edge also ran from a fact to a topic.
That reading is now `extracted_under_topic`, so the filter is gone. These tests
pin the answer to what it was: the same set, from a fact carrying both kinds of
edge, and a topic never in it.
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


async def test_a_fact_nothing_rests_on_has_no_dependents(storage):
    fact = Fact(content="the deploy failed", source_id="seg-1")
    topic = Topic(content="Deployments", source_id="seg-1")
    await storage.store_node(fact)
    await storage.store_node(topic)
    await storage.store_edge(
        NodeEdge(src_id=fact.id, dst_id=topic.id, type=EdgeType.EXTRACTED_UNDER_TOPIC)
    )

    assert await dependent_inference_ids(fact.id, storage) == []
