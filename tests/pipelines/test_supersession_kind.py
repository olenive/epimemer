"""Supersession records *why* a node was retired (the validity model, first step).

`SUPERSEDED` said a node had been replaced and nothing else, so it covered two
opposite events. **We were wrong** — the claim should never have been believed —
and **the world moved on** — the claim was right, and is still right of its
period. Filing the second as the first is how the graph forgets history: Saint
Petersburg became Leningrad became Saint Petersburg, every name correct in its
turn, and the only thing the model could say was that the earlier one had been
replaced.

This is the floor of the validity model, not the fix. It records the distinction; it does not
give a node a validity interval, so a claim that becomes true *again* still has
nowhere to say so. The full design is in `dev-docs/VALIDITY_DESIGN.md` and
`REVIEW_EPISTEMIC.md` §13.

One behavioural consequence is asserted here rather than left implied: a node
retired because the world changed is **not** an archival candidate. Archiving it
for age would be the same defect one level down — the graph discarding something
true because it is no longer current.
"""

from datetime import UTC, datetime, timedelta

import pytest

from epimemer.core.types import (
    SUPERSEDED_STATUSES,
    EdgeType,
    Fact,
    NodeEdge,
    NodeStatus,
    ValueSignal,
    traversal_excluded,
)
from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.mcp import tools
from epimemer.pipelines.reflection.archival import find_archival_candidates


@pytest.fixture
def embedding_provider() -> MockEmbeddingProvider:
    return MockEmbeddingProvider(model_id="mock-embed", dimension=8)


async def _fact(storage, embedding_provider, content: str) -> Fact:
    from epimemer.core.types import EmbeddingRecord

    node = Fact(content=content, source_id="seg-1", value=ValueSignal())
    await storage.store_node(node)
    vec = (await embedding_provider.embed([content]))[0]
    await storage.store_embedding(
        EmbeddingRecord(
            item_id=node.id,
            model_id=embedding_provider.model_id,
            vector=vec,
        )
    )
    return node


class TestSupersessionKind:
    async def test_a_correction_marks_the_old_node_corrected(self, storage, embedding_provider):
        old = await _fact(storage, embedding_provider, "The capital is Bonn.")

        await tools.update(
            node_id=old.id,
            new_content="The capital is Berlin.",
            because="it_was_wrong",
            storage=storage,
            embedding_provider=embedding_provider,
        )

        assert (await storage.get_node(old.id)).status is NodeStatus.CORRECTED

    async def test_a_world_change_marks_the_old_node_historical(self, storage, embedding_provider):
        """The Saint Petersburg case. The old claim was never wrong."""
        old = await _fact(storage, embedding_provider, "The city is called Leningrad.")

        await tools.update(
            node_id=old.id,
            new_content="The city is called Saint Petersburg.",
            because="the_world_changed",
            storage=storage,
            embedding_provider=embedding_provider,
        )

        assert (await storage.get_node(old.id)).status is NodeStatus.HISTORICAL

    async def test_supersede_by_an_existing_node_records_the_kind_too(
        self, storage, embedding_provider
    ):
        old = await _fact(storage, embedding_provider, "The city is called Leningrad.")
        current = await _fact(storage, embedding_provider, "The city is called Saint Petersburg.")

        await tools.supersede_by(
            old_id=old.id,
            existing_id=current.id,
            because="the_world_changed",
            storage=storage,
        )

        assert (await storage.get_node(old.id)).status is NodeStatus.HISTORICAL

    async def test_the_reason_is_required(self, storage, embedding_provider):
        """No default is safe — choosing one silently mislabels the other case."""
        old = await _fact(storage, embedding_provider, "The city is called Leningrad.")

        with pytest.raises(TypeError):
            await tools.update(
                node_id=old.id,
                new_content="The city is called Saint Petersburg.",
                storage=storage,
                embedding_provider=embedding_provider,
            )

    async def test_an_unrecognised_reason_is_refused(self, storage, embedding_provider):
        old = await _fact(storage, embedding_provider, "The city is called Leningrad.")

        with pytest.raises(ValueError):
            await tools.update(
                node_id=old.id,
                new_content="The city is called Saint Petersburg.",
                because="probably",
                storage=storage,
                embedding_provider=embedding_provider,
            )


class TestReadersSeeBothKinds:
    """Every reader that meant "retired by supersession" must still mean it.

    `== NodeStatus.SUPERSEDED` used to be that test and now matches one of three
    values, so each site was moved to `SUPERSEDED_STATUSES`. These are the guards
    against the silent version of that regression, where the code still runs and
    simply stops noticing two thirds of the cases.
    """

    async def test_the_set_covers_every_supersession_status(self):
        assert SUPERSEDED_STATUSES == frozenset(
            {
                NodeStatus.SUPERSEDED,
                NodeStatus.CORRECTED,
                NodeStatus.HISTORICAL,
            }
        )
        assert NodeStatus.ACTIVE not in SUPERSEDED_STATUSES
        assert NodeStatus.MERGED not in SUPERSEDED_STATUSES
        assert NodeStatus.ARCHIVED not in SUPERSEDED_STATUSES

    async def test_a_corrected_node_is_an_archival_candidate_once_old(
        self, storage, embedding_provider
    ):
        old = await _fact(storage, embedding_provider, "The capital is Bonn.")
        await tools.update(
            node_id=old.id,
            new_content="The capital is Berlin.",
            because="it_was_wrong",
            storage=storage,
            embedding_provider=embedding_provider,
        )
        await storage.set_node_status_tx(
            [await storage.get_node(old.id)],
            status=NodeStatus.CORRECTED,
            at=datetime.now(UTC) - timedelta(days=400),
        )

        candidates = await find_archival_candidates(storage, max_age_days=90)

        assert old.id in {n.id for n in candidates}

    async def test_a_historical_node_is_never_an_archival_candidate(
        self, storage, embedding_provider
    ):
        """It was true of its period. Age is not a reason to discard it."""
        old = await _fact(storage, embedding_provider, "The city is called Leningrad.")
        await tools.update(
            node_id=old.id,
            new_content="The city is called Saint Petersburg.",
            because="the_world_changed",
            storage=storage,
            embedding_provider=embedding_provider,
        )
        await storage.set_node_status_tx(
            [await storage.get_node(old.id)],
            status=NodeStatus.HISTORICAL,
            at=datetime.now(UTC) - timedelta(days=400),
        )

        candidates = await find_archival_candidates(storage, max_age_days=90)

        assert old.id not in {n.id for n in candidates}


class TestLegacyGraphsStillLoad:
    def test_a_node_stored_as_superseded_still_loads(self):
        """Old rows do not record which kind they were, and guessing would lie.

        `SUPERSEDED` is kept for exactly this: it means "retired by supersession,
        reason unrecorded". New writes never produce it.
        """
        node = Fact.model_validate(
            {
                "content": "The city is called Leningrad.",
                "source_id": "seg-1",
                "status": "superseded",
            }
        )

        assert node.status is NodeStatus.SUPERSEDED
        assert node.status in SUPERSEDED_STATUSES


class TestAJudgmentStaysOnTheWordingItWasMadeAgainst:
    """The anchoring rule. Judgment edges do not migrate, on any retirement.

    The world-change case was already right, and is covered above. What was
    wrong is that a **correction** re-pointed them, and a **merge** carried them
    onto the survivor. `similarity` is the one with teeth, because
    `corroboration.py` walks it: re-pointing one counts the counterpart's
    publisher as backing a wording it was never compared against, which is
    manufactured corroboration — the failure `fact_dedup.py` calls the worst
    available, since a false unification does not lose information, it inverts
    the quantity the count measures.

    `contradiction` carries the same fault with an extra sting: a correction may
    be exactly what resolved the contradiction, so re-pointing it asserts a
    conflict the correction settled.
    """

    async def _correct(self, storage, embedding_provider, old, new_content):
        await tools.update(
            node_id=old.id,
            new_content=new_content,
            because="it_was_wrong",
            storage=storage,
            embedding_provider=embedding_provider,
        )
        lineage = await storage.get_edges_from(old.id, edge_type=EdgeType.SUPERSEDED_BY)
        return lineage[0].dst_id

    @pytest.mark.parametrize(
        "edge_type",
        [
            EdgeType.SIMILARITY,
            EdgeType.CONTRADICTION,
            EdgeType.VARIANT_OF,
        ],
    )
    async def test_a_correction_leaves_the_judgment_behind(
        self, storage, embedding_provider, edge_type
    ):
        old = await _fact(storage, embedding_provider, "The population is 500,000.")
        other = await _fact(storage, embedding_provider, "There are 500,000 people.")
        await storage.store_edge(
            NodeEdge(
                src_id=old.id,
                dst_id=other.id,
                type=edge_type,
            )
        )

        new_id = await self._correct(
            storage,
            embedding_provider,
            old,
            "The population is 5,000,000.",
        )

        assert len(await storage.get_edges_from(old.id, edge_type=edge_type)) == 1
        assert len(await storage.get_edges_from(new_id, edge_type=edge_type)) == 0

    async def test_a_correction_still_moves_provenance(self, storage, embedding_provider):
        """The half of the policy that has not changed, asserted next to the
        half that has: a correction is the same claim, so its sources are
        genuinely its own. Anchoring judgments must not be read as anchoring
        everything."""
        old = await _fact(storage, embedding_provider, "The population is 500,000.")
        await storage.store_edge(
            NodeEdge(
                src_id=old.id,
                dst_id="doc-1",
                type=EdgeType.SOURCED_FROM,
            )
        )

        new_id = await self._correct(
            storage,
            embedding_provider,
            old,
            "The population is 5,000,000.",
        )

        assert len(await storage.get_edges_from(old.id, edge_type=EdgeType.SOURCED_FROM)) == 0
        assert len(await storage.get_edges_from(new_id, edge_type=EdgeType.SOURCED_FROM)) == 1

    @pytest.mark.parametrize(
        "edge_type",
        [
            EdgeType.SIMILARITY,
            EdgeType.CONTRADICTION,
            EdgeType.VARIANT_OF,
        ],
    )
    async def test_a_merge_leaves_the_judgment_on_the_retired_source(
        self, storage, embedding_provider, edge_type
    ):
        """The survivor's content is *synthesised*, so it is not the wording the
        judgment was made against — even though it does carry the source's
        claim."""
        from epimemer.core.types import EmbeddingRecord

        source = await _fact(storage, embedding_provider, "The capital is Bonn.")
        other = await _fact(storage, embedding_provider, "Bonn is the capital.")
        await storage.store_edge(
            NodeEdge(
                src_id=source.id,
                dst_id=other.id,
                type=edge_type,
            )
        )

        survivor = Fact(
            content="Bonn is the capital city.",
            source_id="seg-1",
            value=ValueSignal(),
        )
        vector = (await embedding_provider.embed([survivor.content]))[0]
        await storage.merge_nodes_tx(
            [source],
            survivor,
            EmbeddingRecord(
                item_id=survivor.id,
                model_id=embedding_provider.model_id,
                vector=vector,
            ),
            [
                NodeEdge(
                    src_id=source.id,
                    dst_id=survivor.id,
                    type=EdgeType.MERGED_INTO,
                )
            ],
            merged_at=datetime.now(UTC),
        )

        assert len(await storage.get_edges_from(source.id, edge_type=edge_type)) == 1
        assert len(await storage.get_edges_from(survivor.id, edge_type=edge_type)) == 0


class TestWorldChangeKeepsTheHistoricalNodesEdges:
    """A world-change migrates per edge type; a correction still moves everything.

    The historical node is kept *because it is still true of its period*, and
    what makes it true of a period is its own provenance — and, once the validity model lands,
    the validity intervals riding on those `sourced_from` edges. Moving them
    onto the replacement leaves the historical node unable to say who asserted
    it or when it held.

    Copying them is not the answer either: a `sourced_from` edge on the
    replacement records the old claim's document asserting the *new* claim,
    which is fabricated attribution. So provenance neither moves nor copies.

    But "migrate nothing" is wrong in the other direction, and dangerously —
    it drops `has_metacontext`, and a fiction-frame claim's replacement would
    land in base reality. A frame says which world a claim belongs to and a tag
    says what it is about; neither asserts the claim, so both are true of the
    replacement too.
    """

    async def _world_change(self, storage, embedding_provider, old):
        await tools.update(
            node_id=old.id,
            new_content="The city is called Saint Petersburg.",
            because="the_world_changed",
            storage=storage,
            embedding_provider=embedding_provider,
        )
        edges = await storage.get_edges_from(old.id, edge_type=EdgeType.TEMPORALLY_FOLLOWED_BY)
        return await storage.get_node(edges[0].dst_id)

    async def test_the_historical_node_keeps_its_source_and_the_replacement_does_not_gain_it(
        self, storage, embedding_provider
    ):
        old = await _fact(storage, embedding_provider, "The city is called Leningrad.")
        await storage.store_edge(
            NodeEdge(
                src_id=old.id,
                dst_id="doc-1953",
                type=EdgeType.SOURCED_FROM,
            )
        )

        new = await self._world_change(storage, embedding_provider, old)

        kept = await storage.get_edges_from(old.id, edge_type=EdgeType.SOURCED_FROM)
        gained = await storage.get_edges_from(new.id, edge_type=EdgeType.SOURCED_FROM)
        assert [e.dst_id for e in kept] == ["doc-1953"]
        assert gained == [] or list(gained) == []

    async def test_the_replacement_stays_in_the_frame_the_old_claim_was_in(
        self, storage, embedding_provider
    ):
        """The assertion that fails under "migrate nothing".

        CLAUDE.md's one hard rule is that fictional and factual information are
        never mixed. A supersession that silently drops the frame breaks it
        without anyone deciding to.
        """
        old = await _fact(storage, embedding_provider, "The city is called Leningrad.")
        await storage.store_edge(
            NodeEdge(
                src_id=old.id,
                dst_id="mc-fiction",
                type=EdgeType.HAS_METACONTEXT,
            )
        )

        new = await self._world_change(storage, embedding_provider, old)

        frames = await storage.get_edges_from(new.id, edge_type=EdgeType.HAS_METACONTEXT)
        assert [e.dst_id for e in frames] == ["mc-fiction"]

    async def test_both_nodes_carry_the_tag(self, storage, embedding_provider):
        """Topics are timeless, so the tag is true of both claims.

        Without this the replacement is unreachable by topic traversal.
        """
        old = await _fact(storage, embedding_provider, "The city is called Leningrad.")
        await storage.store_edge(
            NodeEdge(
                src_id=old.id,
                dst_id="topic-cities",
                type=EdgeType.TAGGED_WITH,
            )
        )

        new = await self._world_change(storage, embedding_provider, old)

        old_tags = await storage.get_edges_from(old.id, edge_type=EdgeType.TAGGED_WITH)
        new_tags = await storage.get_edges_from(new.id, edge_type=EdgeType.TAGGED_WITH)
        assert [e.dst_id for e in old_tags] == ["topic-cities"]
        assert [e.dst_id for e in new_tags] == ["topic-cities"]

    async def test_a_knowledge_edge_stays_with_the_claim_it_was_made_about(
        self, storage, embedding_provider
    ):
        """A contradiction is a claim *about the old claim*. Re-pointing it
        asserts it of a claim nobody assessed."""
        old = await _fact(storage, embedding_provider, "The city is called Leningrad.")
        other = await _fact(storage, embedding_provider, "The city is called Tsaritsyn.")
        await storage.store_edge(
            NodeEdge(
                src_id=old.id,
                dst_id=other.id,
                type=EdgeType.CONTRADICTION,
            )
        )

        new = await self._world_change(storage, embedding_provider, old)

        assert len(await storage.get_edges_from(old.id, edge_type=EdgeType.CONTRADICTION)) == 1
        assert len(await storage.get_edges_from(new.id, edge_type=EdgeType.CONTRADICTION)) == 0

    async def test_a_correction_still_moves_everything(self, storage, embedding_provider):
        """Unchanged behaviour: the corrected node is an audit husk, and the
        replacement is the *same claim*, corrected — so the sources it was
        drawn from are genuinely its own."""
        old = await _fact(storage, embedding_provider, "The capital is Bonn.")
        for dst, edge_type in (
            ("doc-1", EdgeType.SOURCED_FROM),
            ("mc-fiction", EdgeType.HAS_METACONTEXT),
            ("topic-capitals", EdgeType.TAGGED_WITH),
        ):
            await storage.store_edge(NodeEdge(src_id=old.id, dst_id=dst, type=edge_type))

        await tools.update(
            node_id=old.id,
            new_content="The capital is Berlin.",
            because="it_was_wrong",
            storage=storage,
            embedding_provider=embedding_provider,
        )
        lineage = await storage.get_edges_from(old.id, edge_type=EdgeType.SUPERSEDED_BY)
        new_id = lineage[0].dst_id

        for edge_type in (
            EdgeType.SOURCED_FROM,
            EdgeType.HAS_METACONTEXT,
            EdgeType.TAGGED_WITH,
        ):
            assert len(await storage.get_edges_from(old.id, edge_type=edge_type)) == 0
            assert len(await storage.get_edges_from(new_id, edge_type=edge_type)) == 1

    async def test_history_edges_stay_version_anchored_in_both_cases(
        self, storage, embedding_provider
    ):
        """The lineage edge points at the version it was written about. If it
        migrated it would detach from the transition it records."""
        old = await _fact(storage, embedding_provider, "The city is called Leningrad.")

        new = await self._world_change(storage, embedding_provider, old)

        lineage = await storage.get_edges_from(old.id, edge_type=EdgeType.TEMPORALLY_FOLLOWED_BY)
        assert [e.dst_id for e in lineage] == [new.id]
        assert (
            len(await storage.get_edges_from(new.id, edge_type=EdgeType.TEMPORALLY_FOLLOWED_BY))
            == 0
        )


class TestTheLineageEdgeRecordsWhichEventHappened:
    """The edge split, the half the status split left behind.

    Retiring a node writes two things: a status on the node and an edge to its
    successor. The status learned the difference between *we were wrong* and
    *the world moved* and the edge did not, so every world-change produced a
    node marked `HISTORICAL` — still true of its period — reached by an edge
    that said it had been replaced. One of the two was lying and it was always
    the edge.

    `temporally_followed_by` says only that one claim came after another, which
    is what makes recurrence expressible later: Leningrad becoming Saint
    Petersburg in 1991 stays true even when a claim becomes current again.
    """

    async def test_a_world_change_writes_temporal_order_and_not_replacement(
        self, storage, embedding_provider
    ):
        old = await _fact(storage, embedding_provider, "The city is called Leningrad.")

        await tools.update(
            node_id=old.id,
            new_content="The city is called Saint Petersburg.",
            because="the_world_changed",
            storage=storage,
            embedding_provider=embedding_provider,
        )

        followed = await storage.get_edges_from(old.id, edge_type=EdgeType.TEMPORALLY_FOLLOWED_BY)
        assert len(followed) == 1
        assert len(await storage.get_edges_from(old.id, edge_type=EdgeType.SUPERSEDED_BY)) == 0

    async def test_a_correction_writes_replacement_and_not_temporal_order(
        self, storage, embedding_provider
    ):
        old = await _fact(storage, embedding_provider, "The capital is Bonn.")

        await tools.update(
            node_id=old.id,
            new_content="The capital is Berlin.",
            because="it_was_wrong",
            storage=storage,
            embedding_provider=embedding_provider,
        )

        assert len(await storage.get_edges_from(old.id, edge_type=EdgeType.SUPERSEDED_BY)) == 1
        assert (
            len(await storage.get_edges_from(old.id, edge_type=EdgeType.TEMPORALLY_FOLLOWED_BY))
            == 0
        )

    async def test_the_same_split_applies_when_the_successor_already_exists(
        self, storage, embedding_provider
    ):
        """`supersede_by` is the other writer, and a split honoured on one path
        and not the other is worse than no split: the edge would then depend on
        how the claim happened to arrive."""
        old = await _fact(storage, embedding_provider, "The city is called Leningrad.")
        existing = await _fact(storage, embedding_provider, "The city is called Saint Petersburg.")
        wrong = await _fact(storage, embedding_provider, "The capital is Bonn.")
        right = await _fact(storage, embedding_provider, "The capital is Berlin.")

        await tools.supersede_by(
            old_id=old.id,
            existing_id=existing.id,
            because="the_world_changed",
            storage=storage,
        )
        await tools.supersede_by(
            old_id=wrong.id,
            existing_id=right.id,
            because="it_was_wrong",
            storage=storage,
        )

        assert [
            e.dst_id
            for e in await storage.get_edges_from(old.id, edge_type=EdgeType.TEMPORALLY_FOLLOWED_BY)
        ] == [existing.id]
        assert [
            e.dst_id
            for e in await storage.get_edges_from(wrong.id, edge_type=EdgeType.SUPERSEDED_BY)
        ] == [right.id]

    async def test_the_transition_edge_is_not_traversed_as_knowledge(
        self, storage, embedding_provider
    ):
        """It joins `HISTORY_EDGE_TYPES`, so retrieval does not walk from a
        historical claim into its successor as though the two were related by
        content. Making historical claims *reachable* is T3's job, through
        status recall rather than through this edge."""
        old = await _fact(storage, embedding_provider, "The city is called Leningrad.")

        new = await self._world_change(storage, embedding_provider, old)
        [edge] = await storage.get_edges_from(old.id, edge_type=EdgeType.TEMPORALLY_FOLLOWED_BY)

        assert traversal_excluded(edge)
        assert edge.dst_id == new.id

    async def _world_change(self, storage, embedding_provider, old):
        await tools.update(
            node_id=old.id,
            new_content="The city is called Saint Petersburg.",
            because="the_world_changed",
            storage=storage,
            embedding_provider=embedding_provider,
        )
        edges = await storage.get_edges_from(old.id, edge_type=EdgeType.TEMPORALLY_FOLLOWED_BY)
        return await storage.get_node(edges[0].dst_id)


class TestRepeatedTransitionsBetweenOnePair:
    """Two transitions the same way round are two facts about the world, not a
    duplicate row (the edge split).

    *"Labour is in government"* gives way to *"the Conservatives are in
    government"* in 1951, and again in 1970, and again in 1979, and again in
    2010. Each is a separate observed transition between the same pair of
    claims. Collapsing them by `(src, dst, type)` would leave the graph
    asserting that the change happened once.

    Reactivating a `HISTORICAL` node is not built yet — that is the `recurs`
    verdict, and it needs validity intervals to be worth anything — so this
    asserts the storage guarantee the mechanism will stand on rather than
    driving it through `update`. The migration path is safe by construction:
    `migration_disposition` answers `keep` for every history edge, so the one
    signature-dedup site never sees these.
    """

    async def test_parallel_transitions_survive_as_separate_edges(self, storage):
        for _ in range(2):
            await storage.store_edge(
                NodeEdge(
                    src_id="labour-in-government",
                    dst_id="conservatives-in-government",
                    type=EdgeType.TEMPORALLY_FOLLOWED_BY,
                )
            )

        edges = await storage.get_edges_from(
            "labour-in-government", edge_type=EdgeType.TEMPORALLY_FOLLOWED_BY
        )
        assert len(edges) == 2
        assert len({e.id for e in edges}) == 2

    async def test_a_transition_cycle_is_legal(self, storage):
        """The chain returns to its own node — Saint Petersburg to Leningrad
        and back — so any future walker over these edges has to be cycle-safe.
        Recorded here because the edge that made recurrence expressible is the
        same edge that made the graph cyclic."""
        for src, dst in (("spb", "leningrad"), ("leningrad", "spb")):
            await storage.store_edge(
                NodeEdge(
                    src_id=src,
                    dst_id=dst,
                    type=EdgeType.TEMPORALLY_FOLLOWED_BY,
                )
            )

        assert (
            len(await storage.get_edges_from("spb", edge_type=EdgeType.TEMPORALLY_FOLLOWED_BY)) == 1
        )
        assert (
            len(
                await storage.get_edges_from("leningrad", edge_type=EdgeType.TEMPORALLY_FOLLOWED_BY)
            )
            == 1
        )
