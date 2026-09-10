"""The text a node is embedded on.

A topic carries two strings: `content`, the name the graph joins on, and
`description`, the prose saying what it covers. Which of them a vector is built
from is one decision, and every site that writes an embedding has to make it the
same way, so it is made here and nowhere else.

Writing the join out at each site would be the two-definitions failure this
project has hit before: the sites are spread across ingest, merge, supersession,
topic splits and parent synthesis, and a second spelling of the join would show
up as a topic that quietly fails to match itself. One function, one definition,
and a test that asserts no other module joins the two fields.

**The join is narrow, and the narrowness is the design.** Only a topic node
created from a tag joins its description; every other node embeds on `content`
alone. `dev-docs/TOPIC_DESCRIPTIONS.md` holds the case.
"""

from epimemer.core.types import EpistemicNode, Topic
from epimemer.pipelines.metacontexts import created_from_tag


def embedding_text(node: EpistemicNode) -> str:
    """The text this node is embedded on.

    A topic node created from a tag joins its description to its name, because a
    bare tag name is the one wording that says nothing about what the topic
    means: it is invisible to a search on meaning, and it scores against its
    neighbours on how alike the strings are rather than on what they cover. A
    statement topic's `content` is already the prose a description would restate,
    so joining one there would only move a stored topic away from the same topic
    arriving undescribed at ingest, which is exactly where topic similarity is
    meant to catch a duplicate.

    Everything else returns `content`: an undescribed topic, a described
    statement topic, and every fact and inference, whose wording is the claim and
    which carry no second field to join.
    """
    if isinstance(node, Topic) and node.description and created_from_tag(node):
        return f"{node.content}. {node.description}"
    return node.content
