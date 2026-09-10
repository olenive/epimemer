"""Putting a tag's name back on the node that holds its edges.

Enrichment used to replace a topic's `content` and retire the original
`CORRECTED`. Run against a topic node created from a tag, that overwrote the
name the graph joins on with a sentence about what the tag covers, and left the
bare name on a retired node with none of the edges. `find_nodes` stopped
answering for the name, and the next document carrying the tag minted a second
topic node.

Enrichment writes `description` now and cannot reach a name at all, so nothing
creates this state any more. What is left is the graphs that already hold it,
which is what this repairs: on one real graph, five active topic nodes carrying
`extraction_method` `agent:tag:enriched`, a sentence where the name should be,
and up to 107 `tagged_with_topic` edges each.

**The repair is in place, and that is the whole design.** The enriched node is
the one holding the edges, so moving them would be the expensive half of the
work and the half that can go wrong. Putting the name back on it costs nothing
and keeps every edge pointed where it already points. The sentence the
enrichment wrote is good prose about the tag, so it becomes the node's
`description`: the field it should have been written into.

The retired original is left exactly as it is. It carries `superseded_by` to the
repaired node, which is the edge `resolve_name` follows, so the name resolves
through it either way and nothing here has to touch a status.
"""

from collections.abc import Sequence

from pydantic import BaseModel

from epimemer.core.types import EdgeType, JudgeRef, NodeType, Topic
from epimemer.pipelines.metacontexts import TAG_EXTRACTION_METHOD
from epimemer.storage.protocol import StorageBackend

# What the old enrichment stamped on a topic node created from a tag: the
# original method with `:enriched` appended. It is the whole of the candidate
# test, because it is the one mark that says *this node's content was written by
# enrichment over a tag's name*. A repaired node carries `TAG_EXTRACTION_METHOD`
# again and so is never a candidate twice.
ENRICHED_TAG_EXTRACTION_METHOD = f"{TAG_EXTRACTION_METHOD}:enriched"


class DisplacedName(BaseModel):
    """One topic node created from a tag whose name an enrichment overwrote.

    Carries both wordings because a person has to read them side by side before
    agreeing: `name` is what the tag is called and `displaced_by` is the
    sentence standing in its place, which becomes the description. `tagged_nodes`
    is how many nodes hang off the node being repaired, which is the size of
    what a wrong answer would affect.
    """

    topic_id: str
    name: str
    displaced_by: str
    original_id: str
    tagged_nodes: int


async def displaced_tag_names(storage: StorageBackend) -> list[DisplacedName]:
    """Every active topic node whose tag name an old enrichment overwrote.

    A candidate needs all three: the enrichment stamp, an `enriched_from`
    pointing at a node that is still there, and a name on that node to restore.
    A node missing any of them is left alone rather than guessed at: there is
    nowhere else the original name survives, and inventing one would put a name
    nobody wrote onto edges somebody did.
    """
    topics = [
        node
        for node in await storage.query_nodes(node_type=NodeType.TOPIC)
        if isinstance(node, Topic) and node.extraction_method == ENRICHED_TAG_EXTRACTION_METHOD
    ]
    if not topics:
        return []

    tagged = await storage.get_edges_for(
        [topic.id for topic in topics], direction="to", edge_type=EdgeType.TAGGED_WITH_TOPIC
    )

    found: list[DisplacedName] = []
    for topic in topics:
        original_id = topic.metadata.get("enriched_from")
        if not isinstance(original_id, str):
            continue
        original = await storage.get_node(original_id)
        if not isinstance(original, Topic) or not original.content.strip():
            continue
        found.append(
            DisplacedName(
                topic_id=topic.id,
                name=original.content,
                displaced_by=topic.content,
                original_id=original_id,
                tagged_nodes=len(tagged[topic.id]),
            )
        )
    return found


def repaired(topic: Topic, displaced: DisplacedName, *, judge: JudgeRef | None) -> Topic:
    """`topic` with its name back and the sentence it displaced as its description.

    Pure, and it moves no id, no status and no edge. The `name_restored` trail
    is append-only and sits beside `metacontext_reassignments` for the reason
    that one does: it is the only place the wording this replaced survives on the
    node, and a reviewer reading the node later has to be able to see that its
    content was rewritten twice.

    `judged_by` is untouched. It names whoever wrote the enrichment, and the
    person running this is restoring a name rather than authoring one.
    """
    return topic.model_copy(
        update={
            "content": displaced.name,
            "description": displaced.displaced_by,
            "extraction_method": TAG_EXTRACTION_METHOD,
            "metadata": {
                **topic.metadata,
                "name_restored": [
                    *_trail(topic.metadata.get("name_restored")),
                    {
                        "from": displaced.displaced_by,
                        "original_id": displaced.original_id,
                        "judged_by": judge.model_dump(mode="json") if judge else None,
                    },
                ],
            },
        }
    )


def _trail(existing: object) -> Sequence[object]:
    """Whatever is already under the trail key, or nothing.

    A stored graph is data rather than a promise, and a key holding something
    other than a list would otherwise make the repair raise on the one node it
    was run to fix.
    """
    return existing if isinstance(existing, list) else []
