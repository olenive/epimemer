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

**Every description is empty today**, because nothing writes one yet. So every
vector this produces is byte-identical to the one the same node got before this
module existed, and the branch below is dead until enrichment starts writing
descriptions. Whether it stays that way is a measurement rather than a guess:
adding a description to a stored topic moves it away from the same topic
arriving undescribed at ingest, which is exactly where topic similarity is meant
to catch a duplicate, and that number has not been taken.
`dev-docs/TOPIC_DESCRIPTIONS.md` holds the case and the measurement it waits on.
"""

from epimemer.core.types import EpistemicNode, Topic


def embedding_text(node: EpistemicNode) -> str:
    """The text this node is embedded on: its wording, plus its description.

    A described topic reads as one sentence followed by another, which is what
    the embedding model was trained on and keeps the name at the front where it
    dominates. An undescribed topic returns its `content` unchanged, so it
    embeds exactly as it always has.

    Facts and inferences return `content`. A claim's wording is the claim, and
    they carry no second field to join.
    """
    if isinstance(node, Topic) and node.description:
        return f"{node.content}. {node.description}"
    return node.content
