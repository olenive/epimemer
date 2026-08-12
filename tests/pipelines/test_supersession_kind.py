"""Supersession records *why* a node was retired (#53, first step).

`SUPERSEDED` said a node had been replaced and nothing else, so it covered two
opposite events. **We were wrong** — the claim should never have been believed —
and **the world moved on** — the claim was right, and is still right of its
period. Filing the second as the first is how the graph forgets history: Saint
Petersburg became Leningrad became Saint Petersburg, every name correct in its
turn, and the only thing the model could say was that the earlier one had been
replaced.

This is the floor of #53, not the fix. It records the distinction; it does not
give a node a validity interval, so a claim that becomes true *again* still has
nowhere to say so. The full design is in `dev-docs/ISSUES.md` #53 and
`REVIEW_EPISTEMIC.md` §13.

One behavioural consequence is asserted here rather than left implied: a node
retired because the world changed is **not** an archival candidate. Archiving it
for age would be the same defect one level down — the graph discarding something
true because it is no longer current.
"""

from datetime import datetime, timedelta, timezone

import pytest

from epimemer.core.types import (
    Fact,
    NodeStatus,
    SUPERSEDED_STATUSES,
    ValueSignal,
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
    await storage.store_embedding(EmbeddingRecord(
        item_id=node.id, model_id=embedding_provider.model_id, vector=vec,
    ))
    return node


class TestSupersessionKind:
    async def test_a_correction_marks_the_old_node_corrected(
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

        assert (await storage.get_node(old.id)).status is NodeStatus.CORRECTED

    async def test_a_world_change_marks_the_old_node_historical(
        self, storage, embedding_provider
    ):
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
        current = await _fact(
            storage, embedding_provider, "The city is called Saint Petersburg."
        )

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

    async def test_an_unrecognised_reason_is_refused(
        self, storage, embedding_provider
    ):
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
        assert SUPERSEDED_STATUSES == frozenset({
            NodeStatus.SUPERSEDED, NodeStatus.CORRECTED, NodeStatus.HISTORICAL,
        })
        assert NodeStatus.ACTIVE not in SUPERSEDED_STATUSES
        assert NodeStatus.MERGED not in SUPERSEDED_STATUSES
        assert NodeStatus.ARCHIVED not in SUPERSEDED_STATUSES

    async def test_a_corrected_node_is_an_archival_candidate_once_old(
        self, storage, embedding_provider
    ):
        old = await _fact(storage, embedding_provider, "The capital is Bonn.")
        await tools.update(
            node_id=old.id, new_content="The capital is Berlin.",
            because="it_was_wrong",
            storage=storage, embedding_provider=embedding_provider,
        )
        await storage.set_node_status_tx(
            [await storage.get_node(old.id)],
            status=NodeStatus.CORRECTED,
            retired_at=datetime.now(timezone.utc) - timedelta(days=400),
        )

        candidates = await find_archival_candidates(storage, max_age_days=90)

        assert old.id in {n.id for n in candidates}

    async def test_a_historical_node_is_never_an_archival_candidate(
        self, storage, embedding_provider
    ):
        """It was true of its period. Age is not a reason to discard it."""
        old = await _fact(storage, embedding_provider, "The city is called Leningrad.")
        await tools.update(
            node_id=old.id, new_content="The city is called Saint Petersburg.",
            because="the_world_changed",
            storage=storage, embedding_provider=embedding_provider,
        )
        await storage.set_node_status_tx(
            [await storage.get_node(old.id)],
            status=NodeStatus.HISTORICAL,
            retired_at=datetime.now(timezone.utc) - timedelta(days=400),
        )

        candidates = await find_archival_candidates(storage, max_age_days=90)

        assert old.id not in {n.id for n in candidates}


class TestLegacyGraphsStillLoad:
    def test_a_node_stored_as_superseded_still_loads(self):
        """Old rows do not record which kind they were, and guessing would lie.

        `SUPERSEDED` is kept for exactly this: it means "retired by supersession,
        reason unrecorded". New writes never produce it.
        """
        node = Fact.model_validate({
            "content": "The city is called Leningrad.",
            "source_id": "seg-1",
            "status": "superseded",
        })

        assert node.status is NodeStatus.SUPERSEDED
        assert node.status in SUPERSEDED_STATUSES
