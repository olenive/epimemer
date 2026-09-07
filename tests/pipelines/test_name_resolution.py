"""A name resolves to the node that answers to it, up to spelling and lineage.

Two failures, one call site each, both silent. `claim_kind` written beside
`claim-kind` gave one tag two hubs, and neither the exact match nor the merge
bar could tell that pair from `dev-session-2026-09-05` beside
`dev-session-2026-09-06`, which must stay apart. And a name whose node had been
merged away or rewritten resolved to nothing, so the write path minted a second
hub while the read path returned an empty list.

`dev-docs/TAG_IDENTITY.md` and `dev-docs/TOPIC_DESCRIPTIONS.md` carry the
measurements and the reasoning.
"""

import pytest

from epimemer.core.types import (
    EdgeType,
    JudgeRef,
    NodeEdge,
    NodeStatus,
    NodeType,
    Topic,
)
from epimemer.pipelines.frames import TAG_EXTRACTION_METHOD
from epimemer.pipelines.name_resolution import (
    live_successor,
    resolve_name,
    tag_by_key,
    tag_key,
)

CRITIC = JudgeRef(agent_id="critic", digest="d1")


def _tag(content: str) -> Topic:
    return Topic(content=content, source_id=None, extraction_method=TAG_EXTRACTION_METHOD)


async def _stored(storage, topic: Topic, *, status=NodeStatus.ACTIVE) -> Topic:
    topic = topic.model_copy(update={"status": status})
    await storage.store_node(topic)
    return topic


async def _retire_onto(storage, old: Topic, new: Topic, *, edge_type, status) -> None:
    await storage.store_node(old.model_copy(update={"status": status}))
    await storage.store_edge(NodeEdge(src_id=old.id, dst_id=new.id, type=edge_type))


class TestTagKey:
    """What the normalisation collapses, and what it must leave alone."""

    @pytest.mark.parametrize(
        ("a", "b"),
        [
            ("claim_kind", "claim-kind"),
            ("design-decisions", "design decisions"),
            ("Claim-Kind", "claim_kind"),
            ("  spaced  out  ", "spacedout"),
            ("two__under", "two-under"),
        ],
    )
    def test_spelling_collapses(self, a, b):
        assert tag_key(a) == tag_key(b)

    @pytest.mark.parametrize(
        ("a", "b"),
        [
            # The pair the merge bar scores at 0.9935 and must never fuse.
            ("dev-session-2026-09-05", "dev-session-2026-09-06"),
            # Stemming would fuse these, which is why there is none.
            ("reflect", "reflection"),
            ("provenance", "retrieval-provenance"),
        ],
    )
    def test_meaning_survives(self, a, b):
        assert tag_key(a) != tag_key(b)


class TestTagByKey:
    """Matching a name against candidates, tags only."""

    def test_finds_the_other_spelling(self):
        stored = _tag("claim-kind")
        assert tag_by_key("claim_kind", [stored]) is stored

    def test_a_statement_topic_does_not_answer_to_a_tag_name(self):
        """A tag is a name and a statement topic is prose.

        Letting one answer to a normalised tag name would hand a tag's hub to
        something nobody wrote as a tag.
        """
        prose = Topic(content="claim kind", source_id="seg1", extraction_method="agent")
        assert tag_by_key("claim_kind", [prose]) is None

    def test_the_first_candidate_wins(self):
        """Ties go to the spelling already in the graph, so a house spelling settles."""
        first, second = _tag("claim-kind"), _tag("claim_kind")
        assert tag_by_key("Claim Kind", [first, second]) is first

    def test_no_match_is_none(self):
        assert tag_by_key("nothing-like-it", [_tag("claim-kind")]) is None


class TestFollowingARetirementForward:
    """A name whose node was retired still names what carries its content now."""

    async def test_a_merged_tag_resolves_to_the_survivor(self, storage):
        survivor = await _stored(storage, _tag("claim_kind"))
        loser = await _stored(storage, _tag("claim-kind"))
        await _retire_onto(
            storage,
            loser,
            survivor,
            edge_type=EdgeType.MERGED_INTO,
            status=NodeStatus.MERGED,
        )
        found = await resolve_name("claim-kind", storage)
        assert found is not None
        assert found.id == survivor.id

    async def test_a_corrected_tag_resolves_to_its_replacement(self, storage):
        """The shape of the enrichment incident: a tag's name rewritten away."""
        replacement = await _stored(storage, _tag("the rewritten wording"))
        old = await _stored(storage, _tag("issue-46"))
        await _retire_onto(
            storage,
            old,
            replacement,
            edge_type=EdgeType.SUPERSEDED_BY,
            status=NodeStatus.CORRECTED,
        )
        found = await resolve_name("issue-46", storage)
        assert found is not None
        assert found.id == replacement.id

    async def test_two_hops_reach_the_end_of_the_chain(self, storage):
        end = await _stored(storage, _tag("third"))
        middle = await _stored(storage, _tag("second"))
        start = await _stored(storage, _tag("first"))
        await _retire_onto(
            storage, middle, end, edge_type=EdgeType.MERGED_INTO, status=NodeStatus.MERGED
        )
        await _retire_onto(
            storage, start, middle, edge_type=EdgeType.MERGED_INTO, status=NodeStatus.MERGED
        )
        found = await resolve_name("first", storage)
        assert found is not None
        assert found.id == end.id

    async def test_an_active_name_is_returned_untouched(self, storage):
        live = await _stored(storage, _tag("claim_kind"))
        found = await resolve_name("claim_kind", storage)
        assert found is not None
        assert found.id == live.id

    async def test_a_name_nothing_holds_resolves_to_nothing(self, storage):
        assert await resolve_name("never-written", storage) is None

    async def test_a_historical_name_is_not_followed(self, storage):
        """A historical claim is still right of its period, so its name has not moved.

        Only a merge and a correction say *the content went somewhere else*.
        """
        later = await _stored(storage, _tag("the later claim"))
        earlier = await _stored(storage, _tag("the earlier claim"))
        await _retire_onto(
            storage,
            earlier,
            later,
            edge_type=EdgeType.SUPERSEDED_BY,
            status=NodeStatus.HISTORICAL,
        )
        assert await resolve_name("the earlier claim", storage) is None

    async def test_a_dangling_lineage_returns_the_node_it_was_given(self, storage):
        """A retired node whose successor is gone still names the thing it named.

        Returning nothing would have the caller mint a duplicate hub, which is
        the failure this exists to prevent.
        """
        orphan = await _stored(storage, _tag("orphan"), status=NodeStatus.MERGED)
        assert (await live_successor(orphan, storage)).id == orphan.id

    async def test_a_cycle_terminates(self, storage):
        a = await _stored(storage, _tag("a"), status=NodeStatus.MERGED)
        b = await _stored(storage, _tag("b"), status=NodeStatus.MERGED)
        await storage.store_edge(NodeEdge(src_id=a.id, dst_id=b.id, type=EdgeType.MERGED_INTO))
        await storage.store_edge(NodeEdge(src_id=b.id, dst_id=a.id, type=EdgeType.MERGED_INTO))
        assert (await live_successor(a, storage)).status is not NodeStatus.ACTIVE

    async def test_a_fact_name_is_not_resolved_as_a_topic(self, storage):
        """`resolve_name` is asked for a Topic, so a Fact of the same content is not it."""
        assert await resolve_name("claim_kind", storage, node_type=NodeType.TOPIC) is None
