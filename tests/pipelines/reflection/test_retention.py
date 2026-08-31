"""*Reviewed, and it stands* — the keep verdict that had nowhere to go.

Two of `reflect`'s archival reasons nominate a node an agent has already looked
at and decided to keep, and before this neither could read that decision because
there was nothing to write. On 2026-08-29 a reflect over this project's own
`memory` graph nominated six stale-evidence inferences; all six were re-read,
all six still held, and there was nowhere to say so. The same pass judged
**eight** `never_retrieved` nodes upward purely to stop them being nominated —
which is the tell: `importance` had come to carry two meanings, *how
consequential this is* and *do not nominate this*.

The two properties asserted against each other throughout: **a confirmed node
leaves the nomination set, and its importance does not move.** The second is
what makes this a different mechanism from `judgments` rather than a synonym for
it.

And the third, which is why the verdict is anchored rather than permanent: **a
reason that arrives later is not covered.** A judged pair's wording is fixed at
the moment of judgment; a node's neighbourhood keeps moving, and a keep verdict
that silenced the next change too would be worse than the treadmill it replaced.
"""

import pytest

from epimemer.core.types import (
    BASE_METACONTEXT_ID,
    ClaimKind,
    EdgeType,
    Fact,
    Inference,
    NodeEdge,
    NodeStatus,
    ValueSignal,
)
from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.pipelines.reflection.archival import nominate_archival_candidates
from epimemer.pipelines.reflection.retention import (
    confirmed_reasons_for,
    record_retention,
    retention_covers,
)


@pytest.fixture
def embedding_provider() -> MockEmbeddingProvider:
    return MockEmbeddingProvider(model_id="mock-embed", dimension=8)


async def _fact(storage, content: str, *, status=NodeStatus.ACTIVE) -> Fact:
    fact = Fact(
        content=content,
        source_id="seg-1",
        claim_kind=ClaimKind.STATE,
        status=status,
        value=ValueSignal(),
    )
    await storage.store_node(fact)
    await storage.store_edge(
        NodeEdge(
            src_id=fact.id,
            dst_id=BASE_METACONTEXT_ID,
            type=EdgeType.HAS_METACONTEXT,
        )
    )
    return fact


async def _inference_on(storage, content: str, premises) -> Inference:
    """An inference resting on `premises`, flagged stale for each retired one."""
    inference = Inference(content=content, source_id="seg-1", value=ValueSignal())
    await storage.store_node(inference)
    await storage.store_edge(
        NodeEdge(
            src_id=inference.id,
            dst_id=BASE_METACONTEXT_ID,
            type=EdgeType.HAS_METACONTEXT,
        )
    )
    for premise in premises:
        await storage.store_edge(
            NodeEdge(
                src_id=inference.id,
                dst_id=premise.id,
                type=EdgeType.DERIVED_FROM,
            )
        )
        if premise.status is not NodeStatus.ACTIVE:
            await storage.store_edge(
                NodeEdge(
                    src_id=premise.id,
                    dst_id=inference.id,
                    type=EdgeType.EVIDENCE_SUPERSEDED,
                )
            )
    return inference


async def _reasons(storage, node_id: str) -> set[str]:
    return (await confirmed_reasons_for([node_id], storage)).get(node_id, set())


def _nominated(candidates, node_id: str) -> bool:
    return any(c.node_id == node_id for c in candidates)


class TestTheRuleWithoutAStore:
    """`retention_covers` states the design, so it is asserted on its own."""

    def test_a_node_nobody_confirmed_is_not_covered(self):
        assert retention_covers("n", ["a"], {}) is False

    def test_every_current_reason_has_to_be_covered(self):
        assert retention_covers("n", ["a"], {"n": {"a"}}) is True
        assert retention_covers("n", ["a", "b"], {"n": {"a"}}) is False

    def test_a_confirmation_covering_more_than_is_asked_still_covers(self):
        """A premise that was superseded and has since been restored leaves a
        confirmation broader than the question. That is not a reason to re-ask."""
        assert retention_covers("n", ["a"], {"n": {"a", "b"}}) is True

    def test_no_reasons_means_the_node_is_its_own(self):
        """The `never_retrieved` shape. The nomination names no reason, so a
        self-anchored confirmation is what covers it — and an anchor to
        something else does not."""
        assert retention_covers("n", (), {"n": {"n"}}) is True
        assert retention_covers("n", (), {"n": {"other"}}) is False


class TestAConfirmedInferenceIsNotRenominated:
    async def test_it_is_nominated_before_the_verdict(self, storage):
        """The control. Without it the test below proves only that something
        broke nomination generally."""
        premise = await _fact(storage, "the deploy failed", status=NodeStatus.SUPERSEDED)
        inference = await _inference_on(storage, "the release was rushed", [premise])

        assert _nominated(await nominate_archival_candidates(storage), inference.id)

    async def test_it_is_not_nominated_after(self, storage):
        premise = await _fact(storage, "the deploy failed", status=NodeStatus.SUPERSEDED)
        inference = await _inference_on(storage, "the release was rushed", [premise])

        await record_retention(storage, node_id=inference.id, reasons=[premise.id])

        assert not _nominated(await nominate_archival_candidates(storage), inference.id)

    async def test_a_second_supersession_is_a_reason_nobody_covered(self, storage):
        """The anchoring, and the reason the verdict is not permanent. The keep
        answered one changed premise; a different premise changing later is a
        question nobody has been asked."""
        first = await _fact(storage, "the deploy failed", status=NodeStatus.SUPERSEDED)
        second = await _fact(storage, "the rollback held", status=NodeStatus.ACTIVE)
        inference = await _inference_on(storage, "the release was rushed", [first, second])
        await record_retention(storage, node_id=inference.id, reasons=[first.id])

        assert not _nominated(await nominate_archival_candidates(storage), inference.id)

        # The world moves: the second premise is superseded too.
        second.status = NodeStatus.SUPERSEDED
        await storage.store_node(second)
        await storage.store_edge(
            NodeEdge(
                src_id=second.id,
                dst_id=inference.id,
                type=EdgeType.EVIDENCE_SUPERSEDED,
            )
        )

        assert _nominated(await nominate_archival_candidates(storage), inference.id)


class TestAConfirmedNodeKeepsItsImportance:
    """The point of the whole entry.

    Before this, the only way to keep a `never_retrieved` node was to raise its
    importance above the nomination ceiling, which wrote *do not nominate this*
    into a field meaning *how consequential this is*. Every later reader — a
    ranker, a triviality judgment, a person — then saw a signal the judge never
    held.
    """

    async def test_the_node_leaves_the_set_with_its_value_untouched(self, storage):
        fact = await _fact(storage, "the licence file lists MPL-2.0")
        before = (await storage.get_node(fact.id)).value.importance

        assert _nominated(await nominate_archival_candidates(storage), fact.id)

        await record_retention(storage, node_id=fact.id, reasons=[])

        assert not _nominated(await nominate_archival_candidates(storage), fact.id)
        after = await storage.get_node(fact.id)
        assert after.value.importance == before
        assert after.value.importance_judged_at is None
        assert after.status is NodeStatus.ACTIVE

    async def test_an_unconfirmed_node_is_still_nominated(self, storage):
        """Suppression is per node, as it is per pair. Keeping one node must not
        go quiet about every other node that resembles its situation."""
        kept = await _fact(storage, "the licence file lists MPL-2.0")
        other = await _fact(storage, "five distributions carry no licence metadata")

        await record_retention(storage, node_id=kept.id, reasons=[])

        candidates = await nominate_archival_candidates(storage)
        assert not _nominated(candidates, kept.id)
        assert _nominated(candidates, other.id)


class TestWhatTheEdgeRecords:
    async def test_an_unanchored_verdict_anchors_to_the_node_itself(self, storage):
        fact = await _fact(storage, "the licence file lists MPL-2.0")

        anchors = await record_retention(storage, node_id=fact.id, reasons=[])

        assert anchors == [fact.id]
        assert await _reasons(storage, fact.id) == {fact.id}

    async def test_repeated_anchors_collapse(self, storage):
        """Two sources naming the same changed premise is one reason, not two."""
        premise = await _fact(storage, "the deploy failed", status=NodeStatus.SUPERSEDED)
        inference = await _inference_on(storage, "the release was rushed", [premise])

        anchors = await record_retention(
            storage, node_id=inference.id, reasons=[premise.id, premise.id]
        )

        assert anchors == [premise.id]

    async def test_it_is_not_counted_as_structural_support(self, storage):
        """A retention is a record about a judgment, not a claim about the
        world. Counting it as a dependant would make the act of keeping a node
        change the reason it was nominated."""
        from epimemer.pipelines.reflection.archival import knowledge_in_degree_for

        fact = await _fact(storage, "the licence file lists MPL-2.0")
        await record_retention(storage, node_id=fact.id, reasons=[])

        assert (await knowledge_in_degree_for([fact.id], storage))[fact.id] == 0

    async def test_a_node_with_no_verdict_is_absent_rather_than_empty(self, storage):
        """*Nobody has confirmed this* and *somebody confirmed it against
        nothing* are different answers, and only the second is a self-anchor."""
        fact = await _fact(storage, "the licence file lists MPL-2.0")

        assert await confirmed_reasons_for([fact.id], storage) == {}


class TestTheWorklistDropsWhatWasKept:
    async def test_a_confirmed_inference_leaves_pending_review(self, storage):
        """The other nominator. `reflect`'s worklist is what is *outstanding*,
        and re-offering a resolved item is the treadmill this verdict ends."""
        from epimemer.pipelines.reflection.review import gather_pending_review

        premise = await _fact(storage, "the deploy failed", status=NodeStatus.SUPERSEDED)
        inference = await _inference_on(storage, "the release was rushed", [premise])

        listed = [node.id for node, _ in await gather_pending_review(storage)]
        assert inference.id in listed

        await record_retention(storage, node_id=inference.id, reasons=[premise.id])

        listed = [node.id for node, _ in await gather_pending_review(storage)]
        assert inference.id not in listed

    async def test_the_label_itself_stays_true(self, storage):
        """Being true and being outstanding are different questions. The
        inference does still rest on evidence that changed, and a caller reading
        a search result is entitled to know it."""
        from epimemer.pipelines.reflection.review import review_labels_for

        premise = await _fact(storage, "the deploy failed", status=NodeStatus.SUPERSEDED)
        inference = await _inference_on(storage, "the release was rushed", [premise])
        await record_retention(storage, node_id=inference.id, reasons=[premise.id])

        node = await storage.get_node(inference.id)
        labels = await review_labels_for([node], storage)
        assert "evidence_stale" in labels[inference.id]


class TestTheToolPath:
    async def test_apply_reflection_records_and_journals_it(self, storage, embedding_provider):
        from epimemer.core.types import DecisionKind
        from epimemer.mcp import tools

        fact = await _fact(storage, "the licence file lists MPL-2.0")

        result, _ = await tools.apply_reflection(
            storage,
            embedding_provider,
            retained=[{"node_id": fact.id, "because": "still the licence position"}],
        )

        assert result["retentions_recorded"] == 1
        assert await _reasons(storage, fact.id) == {fact.id}

        rows = await storage.query_decisions(kinds=[DecisionKind.RETENTION])
        assert [row.subject_ids for row in rows] == [[fact.id]]

    @pytest.mark.parametrize(
        "entry",
        [
            {"node_id": "", "because": "no id"},
            {"node_id": "missing-node", "because": "unknown id"},
        ],
    )
    async def test_a_malformed_or_unknown_entry_is_skipped(
        self, storage, embedding_provider, entry
    ):
        """Skipped as archivals and supersessions are, rather than refusing the
        whole batch: one bad row must not discard the verdicts beside it."""
        from epimemer.mcp import tools

        result, _ = await tools.apply_reflection(storage, embedding_provider, retained=[entry])

        assert result["retentions_recorded"] == 0

    async def test_a_verdict_with_no_reason_is_skipped(self, storage, embedding_provider):
        """`because` is required for the reason it is required on a review
        confirmation: a keep with no reason marks the node reviewed, so the next
        reviewer skips it having learned nothing."""
        from epimemer.mcp import tools

        fact = await _fact(storage, "the licence file lists MPL-2.0")

        result, _ = await tools.apply_reflection(
            storage, embedding_provider, retained=[{"node_id": fact.id, "because": "  "}]
        )

        assert result["retentions_recorded"] == 0
        assert await confirmed_reasons_for([fact.id], storage) == {}


class TestTheDocumentedFlowActuallyWorks:
    """End to end through the tool, which is where the verdict was broken.

    Every assertion above exercised `record_retention` or the pure predicate
    directly. `apply_reflection` read an undocumented `covers` key, so an agent
    following the documented shape wrote a self-anchor on a node with real
    reasons: the edges went in, `retentions_recorded` said 1, the journal said
    success, and the node came back on the next reflect. A verdict whose writer
    and reader disagree — the third time in this codebase, and the second in a
    fortnight.

    The motivating case is the one that could not work, which is why these two
    tests exist at the tool boundary rather than one layer down.
    """

    async def test_a_verdict_with_covers_suppresses_the_next_nomination(
        self, storage, embedding_provider
    ):
        from epimemer.mcp import tools

        premise = await _fact(storage, "the deploy failed", status=NodeStatus.SUPERSEDED)
        inference = await _inference_on(storage, "the release was rushed", [premise])

        result, _ = await tools.apply_reflection(
            storage,
            embedding_provider,
            retained=[
                {
                    "node_id": inference.id,
                    "because": "re-read against the superseding fact; it holds",
                    "covers": [premise.id],
                }
            ],
        )

        assert result["retentions_recorded"] == 1
        assert result["retained_skipped"] == []
        assert not _nominated(await nominate_archival_candidates(storage), inference.id)

    async def test_a_verdict_without_covers_is_refused_and_names_the_reasons(
        self, storage, embedding_provider
    ):
        """Refused rather than silently ineffective, and refused rather than
        auto-filled: filling the ids in would record the agent as having
        re-read a supersession it may never have seen."""
        from epimemer.mcp import tools

        premise = await _fact(storage, "the deploy failed", status=NodeStatus.SUPERSEDED)
        inference = await _inference_on(storage, "the release was rushed", [premise])

        result, _ = await tools.apply_reflection(
            storage,
            embedding_provider,
            retained=[{"node_id": inference.id, "because": "looks fine to me"}],
        )

        assert result["retentions_recorded"] == 0
        assert [row["node_id"] for row in result["retained_skipped"]] == [inference.id]
        assert premise.id in result["retained_skipped"][0]["why"]
        # And the node is still outstanding, which is the whole point.
        assert _nominated(await nominate_archival_candidates(storage), inference.id)
        assert await confirmed_reasons_for([inference.id], storage) == {}

    async def test_an_anchor_naming_nothing_is_refused(self, storage, embedding_provider):
        """A typo'd id writes an edge that permanently fails to cover — the
        failure this verdict exists to end, reintroduced through its own write
        path."""
        from epimemer.mcp import tools

        premise = await _fact(storage, "the deploy failed", status=NodeStatus.SUPERSEDED)
        inference = await _inference_on(storage, "the release was rushed", [premise])

        result, _ = await tools.apply_reflection(
            storage,
            embedding_provider,
            retained=[
                {
                    "node_id": inference.id,
                    "because": "re-read",
                    "covers": ["typo-id"],
                }
            ],
        )

        assert result["retentions_recorded"] == 0
        assert await confirmed_reasons_for([inference.id], storage) == {}

    async def test_a_node_in_both_lists_is_archived_and_the_collision_reported(
        self, storage, embedding_provider
    ):
        """Archival wins because it is reversible and visible, while a silent
        keep that quietly won an argument is neither. Saying so is the part that
        was missing."""
        from epimemer.mcp import tools

        fact = await _fact(storage, "the licence file lists MPL-2.0")

        result, _ = await tools.apply_reflection(
            storage,
            embedding_provider,
            archivals=[fact.id],
            retained=[{"node_id": fact.id, "because": "worth keeping"}],
        )

        assert result["nodes_archived"] == 1
        assert result["retentions_recorded"] == 0
        assert [row["node_id"] for row in result["retained_skipped"]] == [fact.id]
        assert "archivals" in result["retained_skipped"][0]["why"]


class TestArchivedEvidenceIsAnchoredToo:
    """Seam 5, taken rather than filed.

    An inference whose whole evidence set was archived used to fall through to
    the self-anchor, on the argument that no later change could affect that
    nomination. The argument is wrong: `restore` is the designed reversal, so
    the cycle that bites is restore-then-re-archive — the node re-enters the set
    carrying a verdict that never saw the second archival.
    """

    async def test_the_verdict_anchors_to_the_archived_facts(self, storage):
        from epimemer.pipelines.reflection.archival import evidence_gone_for

        premise = await _fact(storage, "the deploy failed", status=NodeStatus.ARCHIVED)
        inference = await _inference_on(storage, "the release was rushed", [premise])

        gone = await evidence_gone_for([inference], storage)
        assert gone[inference.id] == [premise.id]

        await record_retention(storage, node_id=inference.id, reasons=[premise.id])
        assert not _nominated(await nominate_archival_candidates(storage), inference.id)

    async def test_a_second_archived_premise_is_a_reason_nobody_covered(self, storage):
        first = await _fact(storage, "the deploy failed", status=NodeStatus.ARCHIVED)
        inference = await _inference_on(storage, "the release was rushed", [first])
        await record_retention(storage, node_id=inference.id, reasons=[first.id])

        second = await _fact(storage, "the rollback held", status=NodeStatus.ARCHIVED)
        await storage.store_edge(
            NodeEdge(
                src_id=inference.id,
                dst_id=second.id,
                type=EdgeType.DERIVED_FROM,
            )
        )

        assert _nominated(await nominate_archival_candidates(storage), inference.id)


class TestCoversIsExactlyTheReasons:
    """Both directions refuse, and each closes a way of reporting success while
    suppressing the wrong amount.

    The rule shipped as `reasons ⊆ covers`, which is one-sided. A reviewer
    probed the open side against the branch and found both corners live: extra
    anchors pre-covered a change nobody had seen, and anchors on a reasonless
    node wrote a verdict `retention_covers` would never ask about. The read side
    stays subset-based — an old verdict whose reason set later shrank does still
    cover — and only the *write* is exact.
    """

    async def test_surplus_anchors_do_not_pre_cover_the_future(self, storage, embedding_provider):
        """The corner that matters most. Naming every premise 'to be safe' is
        what a hurried caller does, and it would silence the next supersession
        before anyone read it — the one thing anchoring exists to prevent."""
        from epimemer.mcp import tools

        superseded = await _fact(storage, "the deploy failed", status=NodeStatus.SUPERSEDED)
        still_active = await _fact(storage, "the rollback held")
        inference = await _inference_on(
            storage, "the release was rushed", [superseded, still_active]
        )

        result, _ = await tools.apply_reflection(
            storage,
            embedding_provider,
            retained=[
                {
                    "node_id": inference.id,
                    "because": "re-read",
                    "covers": [superseded.id, still_active.id],
                }
            ],
        )

        assert result["retentions_recorded"] == 0
        assert still_active.id in result["retained_skipped"][0]["why"]
        assert await confirmed_reasons_for([inference.id], storage) == {}

        # And the future it would have pre-covered still arrives as a nomination.
        still_active.status = NodeStatus.SUPERSEDED
        await storage.store_node(still_active)
        await storage.store_edge(
            NodeEdge(
                src_id=still_active.id,
                dst_id=inference.id,
                type=EdgeType.EVIDENCE_SUPERSEDED,
            )
        )
        assert _nominated(await nominate_archival_candidates(storage), inference.id)

    async def test_a_reasonless_node_takes_no_covers_at_all(self, storage, embedding_provider):
        """The self-anchor is implied and never spelled. Anchoring a
        `never_retrieved` node to some real id writes an edge `retention_covers`
        will never ask about — success reported, nothing suppressed."""
        from epimemer.mcp import tools

        fact = await _fact(storage, "the licence file lists MPL-2.0")
        other = await _fact(storage, "five distributions carry no licence metadata")

        result, _ = await tools.apply_reflection(
            storage,
            embedding_provider,
            retained=[
                {
                    "node_id": fact.id,
                    "because": "worth keeping",
                    "covers": [other.id],
                }
            ],
        )

        assert result["retentions_recorded"] == 0
        assert other.id in result["retained_skipped"][0]["why"]
        assert _nominated(await nominate_archival_candidates(storage), fact.id)

    async def test_a_stale_covers_from_before_a_restore_is_refused(
        self, storage, embedding_provider
    ):
        """The mid-flight race, which the exact rule settles rather than
        special-cases: the premise came back between `reflect` and this call, so
        the caller's ids exceed the shrunk reasons. Refusing is right — an
        anchor on the restored fact is the restore-then-re-archive hole with a
        head start."""
        from epimemer.mcp import tools

        premise = await _fact(storage, "the deploy failed", status=NodeStatus.SUPERSEDED)
        inference = await _inference_on(storage, "the release was rushed", [premise])

        # The world moves between the nomination and the verdict.
        premise.status = NodeStatus.ACTIVE
        await storage.store_node(premise)
        for edge in await storage.get_edges_from(premise.id):
            if edge.type is EdgeType.EVIDENCE_SUPERSEDED:
                await storage.delete_edge(edge.id)

        result, _ = await tools.apply_reflection(
            storage,
            embedding_provider,
            retained=[
                {
                    "node_id": inference.id,
                    "because": "re-read",
                    "covers": [premise.id],
                }
            ],
        )

        assert result["retentions_recorded"] == 0
        assert await confirmed_reasons_for([inference.id], storage) == {}
