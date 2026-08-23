"""Every decision leaves a row (REVIEW_MODE.md §4, step 5).

Step 3 and step 4 put the judge *on* the thing decided — the episode, the edge,
the node. That answers *who judged this*. This is the inverse: one table so
*what did this agent judge* is a query rather than five scans and a reassembly.

The tests are grouped by **writer**, because the failure this file exists to
catch is a decision path that quietly writes nothing: a graph missing one
writer's rows looks exactly like a graph where that agent never worked.

Two granularity rules are pinned here rather than left to the docstrings, since
both are judgment calls that would otherwise drift. **Ingest is one row per
call** (§4.1) — forty-four facts out of one document is one reading of one
document. And **re-recording a verdict is a review, not a decision**: the row
points back rather than overwriting somebody else's name.
"""

from datetime import datetime, timezone

import pytest

from epimemer.core.types import (
    ClaimKind,
    DecisionKind,
    EdgeType,
    EmbeddingRecord,
    Fact,
    JudgeRef,
    NodeEdge,
    NodeStatus,
    RawDocument,
    Topic,
    ValueSignal,
)
from epimemer.core.temporal import IntervalBasis, ValidityInterval
from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.mcp import tools
from epimemer.mcp.config import ServerConfig


CRITIC = JudgeRef(agent_id="critic", digest="d1")
EDITOR = JudgeRef(agent_id="editor", digest="d2")


@pytest.fixture
def embedder():
    return MockEmbeddingProvider(model_id="mock-embed", dimension=8)


@pytest.fixture
def config():
    return ServerConfig(storage_backend="memory", embedding_provider="mock")


async def _node(storage, embedder, node):
    await storage.store_node(node)
    vectors = await embedder.embed([node.content])
    await storage.store_embedding(EmbeddingRecord(
        item_id=node.id, model_id=embedder.model_id, vector=vectors[0]
    ))
    return node


async def _fact(storage, embedder, content, *, claim_kind=None):
    return await _node(
        storage, embedder,
        Fact(content=content, source_id="seg1", claim_kind=claim_kind),
    )


async def _topic(storage, embedder, content):
    return await _node(storage, embedder, Topic(content=content, source_id="seg1"))


async def _only(storage, kind: DecisionKind):
    """The one row of `kind`, asserting there is exactly one."""
    found = await storage.query_decisions(kinds=[kind])
    assert len(found) == 1, [r.model_dump(mode="json") for r in found]
    return found[0]


class TestIngest:
    async def test_one_row_for_the_call_and_not_one_per_fact(
        self, storage, embedder, config
    ):
        """Forty-four facts out of one document is one reading of one document,
        and a row each would make ingest the journal's dominant writer by orders
        of magnitude while still describing a single act (§4.1)."""
        seg, _ = await tools.segment_text(
            "Three things happened.", storage, embedder, config, judge=CRITIC,
        )
        await tools.store_decomposition(
            document_id=seg["document_id"],
            segments=[{
                "segment_id": seg["segments"][0]["segment_id"],
                "topics": ["a topic"],
                "facts": [
                    {"content": "the first thing", "claim_kind": "event"},
                    {"content": "the second thing", "claim_kind": "event"},
                ],
                "inferences": ["something follows"],
            }],
            storage=storage,
            embedding_provider=embedder,
            judge=CRITIC,
        )

        record = await _only(storage, DecisionKind.INGEST)
        assert record.judged_by == CRITIC

    async def test_the_row_names_every_node_the_call_created(
        self, storage, embedder, config
    ):
        """The per-node judgments ride inside it — `claim_kind`, the two priors
        — and a reviewer opens them from `subject_ids`. That is the honest
        granularity, and it only works if the ids are all there."""
        seg, _ = await tools.segment_text(
            "A report.", storage, embedder, config, judge=CRITIC,
        )
        await tools.store_decomposition(
            document_id=seg["document_id"],
            segments=[{
                "segment_id": seg["segments"][0]["segment_id"],
                "topics": ["a topic"],
                "facts": [{"content": "a fact", "claim_kind": "state"}],
                "inferences": [],
            }],
            storage=storage,
            embedding_provider=embedder,
            judge=CRITIC,
        )

        record = await _only(storage, DecisionKind.INGEST)
        stored = {n.id for n in await storage.query_nodes()}
        assert stored <= set(record.subject_ids)

    async def test_segmenting_writes_no_row(self, storage, embedder, config):
        """Splitting text into paragraphs is not a verdict anybody would
        review. The judgment pass over this document is the second step."""
        await tools.segment_text(
            "A report.", storage, embedder, config,
            published_by="The Gazette", judge=CRITIC,
        )

        assert await storage.query_decisions() == []

    async def test_a_call_that_stored_nothing_journals_nothing(
        self, storage, embedder, config
    ):
        """A row with no subjects is a decision about nothing."""
        seg, _ = await tools.segment_text(
            "A report.", storage, embedder, config,
        )
        await tools.store_decomposition(
            document_id=seg["document_id"],
            segments=[{
                "segment_id": seg["segments"][0]["segment_id"],
                "topics": [], "facts": [], "inferences": [],
            }],
            storage=storage,
            embedding_provider=embedder,
            judge=CRITIC,
        )

        assert await storage.query_decisions(kinds=[DecisionKind.INGEST]) == []

    async def test_an_unattributed_ingest_still_journals(
        self, storage, embedder, config
    ):
        """A graph that requires nobody still wants to know when this was
        decided and whether anyone has checked it (§3.3.1)."""
        seg, _ = await tools.segment_text("A report.", storage, embedder, config)
        await tools.store_decomposition(
            document_id=seg["document_id"],
            segments=[{
                "segment_id": seg["segments"][0]["segment_id"],
                "topics": ["a topic"], "facts": [], "inferences": [],
            }],
            storage=storage,
            embedding_provider=embedder,
        )

        assert (await _only(storage, DecisionKind.INGEST)).judged_by is None


class TestSupersession:
    """`because` is carried by the *kind*, rather than by a second field
    repeating it: a correction and a world-change are opposite claims about what
    happened (#53), and a reviewer asking for one does not want the other."""

    async def test_a_correction_is_a_correction(self, storage, embedder):
        node = await _fact(storage, embedder, "the office is in Leeds")

        await tools.update(
            node.id, "the office is in Manchester", storage, embedder,
            because="it_was_wrong", judge=CRITIC,
        )

        record = await _only(storage, DecisionKind.CORRECTION)
        assert record.judged_by == CRITIC
        assert node.id in record.subject_ids

    async def test_a_world_change_is_not_filed_as_an_error(self, storage, embedder):
        node = await _fact(storage, embedder, "Labour is in opposition")

        await tools.update(
            node.id, "Labour is in government", storage, embedder,
            because="the_world_changed", judge=CRITIC,
        )

        await _only(storage, DecisionKind.WORLD_CHANGE)
        assert await storage.query_decisions(kinds=[DecisionKind.CORRECTION]) == []

    async def test_the_row_names_both_versions(self, storage, embedder):
        """The retired node and its replacement: a reviewer wants to read the
        wording that changed, and one id gives them half of it."""
        node = await _fact(storage, embedder, "the figure is 500,000")

        result, _ = await tools.update(
            node.id, "the figure is 5,000,000", storage, embedder,
            because="it_was_wrong", judge=CRITIC,
        )

        record = await _only(storage, DecisionKind.CORRECTION)
        assert set(record.subject_ids) == {result["old_node_id"], result["new_node_id"]}

    async def test_supersede_by_an_existing_node_journals(self, storage, embedder):
        loser = await _fact(storage, embedder, "the figure is 500,000")
        winner = await _fact(storage, embedder, "the figure is 5,000,000")

        await tools.supersede_by(
            loser.id, winner.id, storage, because="the_world_changed", judge=CRITIC,
        )

        record = await _only(storage, DecisionKind.WORLD_CHANGE)
        assert set(record.subject_ids) == {loser.id, winner.id}


class TestMergeAndItsUndo:
    async def _merged(self, storage, embedder, *, judge=CRITIC):
        a = await _fact(storage, embedder, "the treaty was signed in Vienna",
                        claim_kind=ClaimKind.STATE)
        b = await _fact(storage, embedder, "the treaty was signed in Vienna",
                        claim_kind=ClaimKind.STATE)
        result, _ = await tools.merge_facts(
            [a.id, b.id], "the treaty was signed in Vienna", storage, embedder,
            judge=judge,
        )
        assert result["merged"] is True
        return result["fact_id"], [a.id, b.id]

    async def test_a_merge_journals_the_survivor_first(self, storage, embedder):
        """So a reversal looking for *the merge that made this node* finds it by
        the id it holds."""
        survivor, sources = await self._merged(storage, embedder)

        record = await _only(storage, DecisionKind.MERGE)
        assert record.subject_ids[0] == survivor
        assert set(record.subject_ids) == {survivor, *sources}

    async def test_a_reversal_both_reviews_and_supersedes_the_merge(
        self, storage, embedder
    ):
        """The one case where the two fields are set together (§4): agent 2
        overturning agent 1's merge has checked it *and* replaced it."""
        survivor, _ = await self._merged(storage, embedder)
        merge = await _only(storage, DecisionKind.MERGE)

        result, _ = await tools.reverse_merge(survivor, storage, judge=EDITOR)

        assert result["reversed"] is True
        reversal = await _only(storage, DecisionKind.REVERSAL)
        assert reversal.reviews == merge.id
        assert reversal.supersedes == merge.id
        assert reversal.judged_by == EDITOR

    async def test_the_reversal_names_the_node_it_destroyed(self, storage, embedder):
        """That id is now the only place the graph says the node existed —
        everything else about it is gone, which is what makes this the system's
        one destructive act."""
        survivor, _ = await self._merged(storage, embedder)

        await tools.reverse_merge(survivor, storage, judge=EDITOR)

        assert survivor in (await _only(storage, DecisionKind.REVERSAL)).subject_ids
        assert await storage.get_node(survivor) is None

    async def test_reversing_a_merge_older_than_the_journal_cites_nothing(
        self, storage, embedder
    ):
        """The journal cannot point at a row that does not exist, and inventing
        a target would be worse than the blank."""
        from epimemer.pipelines.graph_construction.versioning import merge_nodes

        a = await _fact(storage, embedder, "the treaty was signed in Vienna",
                        claim_kind=ClaimKind.STATE)
        b = await _fact(storage, embedder, "the treaty was signed in Vienna",
                        claim_kind=ClaimKind.STATE)
        survivor = Fact(
            content="the treaty was signed in Vienna", source_id="seg1",
            claim_kind=ClaimKind.STATE, extraction_method="agent:merge",
            metadata={"merged_from": [a.id, b.id]},
        )
        await merge_nodes([a, b], survivor, storage, embedder)

        result, _ = await tools.reverse_merge(survivor.id, storage, judge=EDITOR)

        assert result["reversed"] is True
        reversal = await _only(storage, DecisionKind.REVERSAL)
        assert reversal.reviews is None and reversal.supersedes is None

    async def test_a_refused_reversal_journals_nothing(self, storage, embedder):
        """The row is written after the decision lands, so a refusal leaves no
        trace of a decision the graph never made."""
        node = await _fact(storage, embedder, "not a merge survivor")

        result, _ = await tools.reverse_merge(node.id, storage, judge=EDITOR)

        assert result["reversed"] is False
        assert await storage.query_decisions(kinds=[DecisionKind.REVERSAL]) == []


class TestRecordingAPairVerdictTwiceIsAReview:
    """`ATTRIBUTION.md`'s rule, now with somewhere to put it: a second agent
    recording a verdict the pair already carries has **confirmed**, not decided.
    Overwriting the edge's `judged_by` would take the decision from whoever made
    it; writing nothing lets a third agent do the work a fourth time."""

    async def _pair(self, storage, embedder):
        a = await _fact(storage, embedder, "the vote passed")
        b = await _fact(storage, embedder, "the vote failed")
        return a, b

    async def test_the_first_contradiction_is_a_decision(self, storage, embedder):
        a, b = await self._pair(storage, embedder)

        await tools.record_contradiction(a.id, b.id, storage, judge=CRITIC)

        record = await _only(storage, DecisionKind.CONTRADICTION)
        assert record.reviews is None
        assert set(record.subject_ids) == {a.id, b.id}

    async def test_the_second_points_back_at_the_first(self, storage, embedder):
        a, b = await self._pair(storage, embedder)
        await tools.record_contradiction(a.id, b.id, storage, judge=CRITIC)
        original = await _only(storage, DecisionKind.CONTRADICTION)

        result, _ = await tools.record_contradiction(
            a.id, b.id, storage, judge=EDITOR
        )

        assert result["created"] is False, "the edge is untouched"
        rows = await storage.query_decisions(kinds=[DecisionKind.CONTRADICTION])
        [confirmation] = [r for r in rows if r.id != original.id]
        assert confirmation.reviews == original.id
        assert confirmation.judged_by == EDITOR

    async def test_the_pair_is_matched_in_either_direction(self, storage, embedder):
        """`_ensure_symmetric_edge` already treats the pair as unordered, and a
        journal that did not would file the mirror call as a fresh decision."""
        a, b = await self._pair(storage, embedder)
        await tools.record_contradiction(a.id, b.id, storage, judge=CRITIC)
        original = await _only(storage, DecisionKind.CONTRADICTION)

        await tools.record_contradiction(b.id, a.id, storage, judge=EDITOR)

        rows = await storage.query_decisions(kinds=[DecisionKind.CONTRADICTION])
        [confirmation] = [r for r in rows if r.id != original.id]
        assert confirmation.reviews == original.id

    async def test_a_third_agent_confirms_the_original_and_not_the_second(
        self, storage, embedder
    ):
        """A confirmation of a confirmation buries the decision it was about."""
        a, b = await self._pair(storage, embedder)
        await tools.record_contradiction(a.id, b.id, storage, judge=CRITIC)
        original = await _only(storage, DecisionKind.CONTRADICTION)
        await tools.record_contradiction(a.id, b.id, storage, judge=EDITOR)

        await tools.record_contradiction(
            a.id, b.id, storage, judge=JudgeRef(agent_id="third", digest="d3")
        )

        rows = await storage.query_decisions(kinds=[DecisionKind.CONTRADICTION])
        assert [r.reviews for r in rows if r.judged_by.agent_id == "third"] == [
            original.id
        ]

    async def test_an_edge_older_than_the_journal_leaves_the_pointer_blank(
        self, storage, embedder
    ):
        a, b = await self._pair(storage, embedder)
        await tools._ensure_symmetric_edge(
            a.id, b.id, EdgeType.CONTRADICTION, storage
        )

        await tools.record_contradiction(a.id, b.id, storage, judge=EDITOR)

        assert (await _only(storage, DecisionKind.CONTRADICTION)).reviews is None

    async def test_a_variant_follows_the_same_rule(self, storage, embedder):
        a, b = await self._pair(storage, embedder)
        await tools.record_variant(a.id, b.id, storage, judge=CRITIC)
        original = await _only(storage, DecisionKind.VARIANT)

        await tools.record_variant(a.id, b.id, storage, judge=EDITOR)

        rows = await storage.query_decisions(kinds=[DecisionKind.VARIANT])
        [confirmation] = [r for r in rows if r.id != original.id]
        assert confirmation.reviews == original.id


class TestTheSmallerWriters:
    async def test_link_journals_a_relation(self, storage, embedder):
        a = await _topic(storage, embedder, "Vienna")
        b = await _topic(storage, embedder, "Austria")

        await tools.link(a.id, b.id, storage, relation="capital_of", judge=CRITIC)

        record = await _only(storage, DecisionKind.RELATION)
        assert set(record.subject_ids) == {a.id, b.id}

    async def test_judging_importance_journals_once(self, storage, embedder):
        node = await _fact(storage, embedder, "a claim worth keeping")

        await tools.judge_importance(
            node.id, "up", "cited again", storage, judge=CRITIC
        )

        record = await _only(storage, DecisionKind.IMPORTANCE)
        assert record.subject_ids == [node.id]

    async def test_restoring_journals_a_reactivation(self, storage, embedder):
        node = await _fact(storage, embedder, "a trivial aside")
        await tools.apply_reflection(
            storage, embedder, archivals=[node.id], judge=CRITIC
        )

        await tools.restore(
            storage, archive_data={"nodes": [], "edges": []},
            node_ids=None, judge=EDITOR,
        )
        # Nothing came back, so nothing was decided.
        assert await storage.query_decisions(kinds=[DecisionKind.REACTIVATION]) == []

        await tools.restore(
            storage,
            archive_data={
                "nodes": [tools._node_to_dict(await storage.get_node(node.id))],
                "edges": [],
            },
            judge=EDITOR,
        )

        record = await _only(storage, DecisionKind.REACTIVATION)
        assert record.subject_ids == [node.id]
        assert record.judged_by == EDITOR


class TestReflectionAppliesManyDecisionsAndJournalsEachOne:
    """One row per decision here, not one per call — the opposite of ingest, and
    for the opposite reason: these are independent verdicts about unrelated
    nodes that happen to be batched into one request."""

    async def test_a_similarity_verdict_journals(self, storage, embedder):
        a = await _fact(storage, embedder, "the treaty was signed in Vienna")
        b = await _fact(storage, embedder, "the treaty was signed at Vienna")

        result, _ = await tools.apply_reflection(
            storage, embedder,
            similarities=[{
                "pair": [a.id, b.id], "verdict": "distinct",
                "because": "different treaties",
            }],
            judge=CRITIC,
        )

        assert result["similarities_recorded"] == 1
        record = await _only(storage, DecisionKind.SIMILARITY)
        assert set(record.subject_ids) == {a.id, b.id}

    async def test_a_refused_similarity_journals_nothing(self, storage, embedder):
        a = await _fact(storage, embedder, "the treaty was signed in Vienna")
        b = await _fact(storage, embedder, "the treaty was signed at Vienna")

        result, _ = await tools.apply_reflection(
            storage, embedder,
            similarities=[{"pair": [a.id, b.id], "verdict": "distinct", "because": ""}],
            judge=CRITIC,
        )

        assert result["similarities_refused"]
        assert await storage.query_decisions(kinds=[DecisionKind.SIMILARITY]) == []

    async def test_a_synthesised_parent_journals(self, storage, embedder):
        a = await _topic(storage, embedder, "Vienna")
        b = await _topic(storage, embedder, "Salzburg")

        await tools.apply_reflection(
            storage, embedder,
            parents=[{"children_ids": [a.id, b.id], "content": "Austrian cities"}],
            judge=CRITIC,
        )

        record = await _only(storage, DecisionKind.SYNTHESIS)
        assert {a.id, b.id} <= set(record.subject_ids)

    async def test_a_split_journals_the_parent_and_its_parts(self, storage, embedder):
        parent = await _topic(storage, embedder, "European history")

        await tools.apply_reflection(
            storage, embedder,
            splits=[{"topic_id": parent.id, "subtopics": ["the Congress", "the wars"]}],
            judge=CRITIC,
        )

        record = await _only(storage, DecisionKind.SPLIT)
        assert record.subject_ids[0] == parent.id
        assert len(record.subject_ids) == 3

    async def test_an_enrichment_journals(self, storage, embedder):
        topic = await _topic(storage, embedder, "Vienna")

        await tools.apply_reflection(
            storage, embedder,
            enrichments=[{"topic_id": topic.id, "new_content": "Vienna, in Austria"}],
            judge=CRITIC,
        )

        record = await _only(storage, DecisionKind.ENRICHMENT)
        assert topic.id in record.subject_ids

    async def test_an_archival_sweep_is_one_row(self, storage, embedder):
        """Approving a batch of trivial nodes is a single pass over a single
        nomination list, not twelve independent verdicts."""
        nodes = [
            await _fact(storage, embedder, f"a trivial aside {n}") for n in range(3)
        ]

        await tools.apply_reflection(
            storage, embedder, archivals=[n.id for n in nodes], judge=CRITIC,
        )

        record = await _only(storage, DecisionKind.ARCHIVAL)
        assert set(record.subject_ids) == {n.id for n in nodes}

    async def test_a_reflection_supersession_journals_its_reason(
        self, storage, embedder
    ):
        loser = await _fact(storage, embedder, "the figure is 500,000")
        winner = await _fact(storage, embedder, "the figure is 5,000,000")

        await tools.apply_reflection(
            storage, embedder,
            supersessions=[{
                "old_id": loser.id, "by_id": winner.id, "because": "it_was_wrong",
            }],
            judge=CRITIC,
        )

        record = await _only(storage, DecisionKind.CORRECTION)
        assert set(record.subject_ids) == {loser.id, winner.id}

    async def test_an_importance_judgment_journals_once_not_twice(
        self, storage, embedder
    ):
        """It is reached through `judge_importance`, which writes its own row.
        One act, one row, whichever way it was called."""
        node = await _fact(storage, embedder, "a claim worth keeping")

        await tools.apply_reflection(
            storage, embedder,
            judgments=[{"node_id": node.id, "direction": "down", "reason": "minor"}],
            judge=CRITIC,
        )

        await _only(storage, DecisionKind.IMPORTANCE)

    async def test_merging_relation_labels_still_journals_nothing(
        self, storage, embedder
    ):
        """Deliberate, and the reason is the field rather than the effort: a
        journal subject is a node id and this judgment's subjects are *labels*.
        Pinned so the gap is a decision somebody can find, not an oversight."""
        a = await _topic(storage, embedder, "Vienna")
        b = await _topic(storage, embedder, "Austria")
        await tools.link(a.id, b.id, storage, relation="capital_of")

        result, _ = await tools.apply_reflection(
            storage, embedder,
            relation_merges=[{"labels": ["capital_of"], "into": "is_capital_of"}],
            judge=CRITIC,
        )

        assert result["edges_relabeled"] == 1
        assert await storage.query_decisions(
            kinds=[DecisionKind.RELATION_MERGE]
        ) == []


class TestAcceptedBoundaries:
    """The other gap `ATTRIBUTION.md` named. Accepting a boundary edits an
    existing `sourced_from` edge, so stamping it inline would take the edge from
    whoever ingested it — and both of its subjects are nodes, so the journal
    fits it exactly."""

    async def _renaming(self, storage):
        older = RawDocument(content="A 1970 gazetteer", source="doc-1970")
        newer = RawDocument(content="A 2000 gazetteer", source="doc-2000")
        for document in (older, newer):
            await storage.store_document(document)

        def period(start: int) -> ValidityInterval:
            return ValidityInterval(
                start={
                    "instant_kind": "precise",
                    "at": datetime(start, 1, 1, tzinfo=timezone.utc).isoformat(),
                },
                end={"instant_kind": "unknown"},
                basis=IntervalBasis.STATED,
            )

        leningrad = Fact(content="the city is called Leningrad",
                         source_id="s1", value=ValueSignal())
        petersburg = Fact(content="the city is called Saint Petersburg",
                          source_id="s1", value=ValueSignal())
        for node, document, span in (
            (leningrad, older, period(1924)),
            (petersburg, newer, period(1991)),
        ):
            await storage.store_node(node)
            await storage.store_edge(NodeEdge(
                src_id=node.id, dst_id=document.id,
                type=EdgeType.SOURCED_FROM, validity=[span],
            ))
        await storage.set_node_status_tx(
            [leningrad], status=NodeStatus.HISTORICAL,
            at=datetime.now(timezone.utc),
        )
        await storage.store_edge(NodeEdge(
            src_id=leningrad.id, dst_id=petersburg.id,
            type=EdgeType.TEMPORALLY_FOLLOWED_BY,
        ))
        return leningrad, older

    async def test_accepting_one_journals_the_claim_and_the_source(
        self, storage, embedder
    ):
        leningrad, older = await self._renaming(storage)

        result, _ = await tools.apply_reflection(
            storage, embedder,
            boundaries=[{
                "node_id": leningrad.id, "source_id": older.id,
                "endpoint": "end", "at": "1991-01-01T00:00:00+00:00",
            }],
            judge=CRITIC,
        )

        assert result["boundaries_applied"] == 1
        record = await _only(storage, DecisionKind.BOUNDARY)
        assert set(record.subject_ids) == {leningrad.id, older.id}
        assert record.judged_by == CRITIC

    async def test_a_refused_boundary_journals_nothing(self, storage, embedder):
        leningrad, older = await self._renaming(storage)

        result, _ = await tools.apply_reflection(
            storage, embedder,
            boundaries=[{
                "node_id": leningrad.id, "source_id": older.id,
                "endpoint": "end", "at": "1900-01-01T00:00:00+00:00",
            }],
            judge=CRITIC,
        )

        assert result["boundaries_refused"]
        assert await storage.query_decisions(kinds=[DecisionKind.BOUNDARY]) == []


class TestNoKindGoesUnwritten:
    """The drift guard. A kind added to the enum with nothing writing it is a
    review filter that silently returns nothing, and a writer added without a
    kind is a decision that leaves no row — neither shows up in a passing suite
    unless something reads both lists.

    The two exceptions are named here rather than being an empty result: an
    exception nobody has to justify is how the list becomes decoration.
    """

    UNWRITTEN = {
        # Advisories are not built; W&S §9's node note is folded into this enum
        # so that when they are, there is one review machine rather than two.
        DecisionKind.PROCEEDED_DESPITE_ADVISORY,
        # Its subjects are labels, not node ids. See `apply_reflection` step 9.
        DecisionKind.RELATION_MERGE,
    }

    def test_every_kind_has_a_writer_or_a_stated_reason(self):
        import inspect

        from epimemer.mcp import tools as tools_module

        source = inspect.getsource(tools_module)
        # `supersession_kind` maps a status to one of the two supersession
        # kinds, so those are written by name in `types.py` rather than here.
        indirect = {DecisionKind.CORRECTION, DecisionKind.WORLD_CHANGE}
        unwritten = {
            kind for kind in DecisionKind
            if f"DecisionKind.{kind.name}" not in source
        } - indirect

        assert unwritten == self.UNWRITTEN

    async def test_the_stated_exceptions_are_still_selectable(self, storage):
        """Nothing writes them, but the filter has to work the day something
        does — and a query for a kind with no rows must answer empty rather than
        fail."""
        for kind in self.UNWRITTEN:
            assert await storage.query_decisions(kinds=[kind]) == []
