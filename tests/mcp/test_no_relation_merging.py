"""Relation labels are never rewritten (`RELATION_LABELS.md` §5, the label record).

`apply_reflection(relation_merges=…)` used to relabel every user-tier edge
carrying a listed label, in place. Edges are not versioned, so the old wording
was gone: **a lossy, irreversible bulk rewrite in a system whose whole design is
append-only and reversible.** `reverse_merge` undoes a node merge, `restore` an
archival, `reframe` a frame, and `apply_review` records a dissent against any of
them. This was the one operation with no undo, and it operated on the least
valuable thing in the graph — a word, which affects no retrieval (§1.2).

**It was removed on 2026-08-28 rather than replaced.** Stage 4's deprecation is
the shape that would replace it — `status` and `alias_of` on the label record,
existing edges keeping their own wording, `list_relations` folding aliases
underneath — and it is not built, because dropping is reversible and building is
not. §5 has the full argument.

These tests are the guard on that decision, and they exist because the
reintroduction is a natural-looking one line: an agent looking at two synonymous
labels wants to fuse them, and the mechanism is a single UPDATE. So the absence
is pinned at both layers — the tool cannot be asked, and no backend can do it.

**What replaced it is not nothing**, and the last test says so. The nominations
still arrive, and `relation_verdicts` is their destination: a pair is judged
`distinct` or `synonymous`, the judgment is recorded against both label records,
and the pair never comes back. That ordering was the constraint on removing
merging at all — stage 3 had to land first, or `similar_relations` would
nominate into a void, which is FC1's treadmill running to no destination.
"""

import inspect

import pytest

from epimemer.core.types import JudgeRef, Topic
from epimemer.mcp import tools
from epimemer.storage.memory import InMemoryStorage
from epimemer.storage.protocol import StorageBackend
from epimemer.storage.surrealdb_adapter import SurrealDBStorage
from epimemer.visualization.instrumented_storage import InstrumentedStorage

CRITIC = JudgeRef(agent_id="critic", digest="d1")


class _FixedEmbed:
    model_id = "fixed"

    async def embed(self, texts):
        return [{"works_for": [1.0, 0.0], "employed_by": [1.0, 0.0]}[t] for t in texts]


TWINS = _FixedEmbed()


class TestTheToolCannotBeAsked:
    async def test_apply_reflection_takes_no_relation_merges(self, storage):
        with pytest.raises(TypeError):
            await tools.apply_reflection(
                storage,
                TWINS,
                relation_merges=[{"labels": ["works_for"], "into": "employed_by"}],
            )

    def test_the_parameter_is_gone_from_both_layers(self):
        """The MCP signature too — an argument the server accepts is one an
        agent will send, whatever the tool underneath does with it."""
        from epimemer.mcp import server

        for fn in (tools.apply_reflection, server.memory_apply_reflection):
            assert "relation_merges" not in inspect.signature(fn).parameters

    async def test_no_response_key_promises_a_rewrite(self, storage):
        result, _ = await tools.apply_reflection(storage, TWINS)
        for key in ("relations_consolidated", "edges_relabeled", "relation_descriptions_orphaned"):
            assert key not in result


class TestNoBackendCanDoIt:
    """The mechanism, not just the caller.

    `relabel_edges` was the only write in the system that mutated an existing
    edge's content, and leaving it in the protocol would leave the capability
    one line from returning. Deprecation, if it is ever built, rewrites nothing
    — so nothing needs this back.
    """

    @pytest.mark.parametrize(
        "backend",
        [StorageBackend, InMemoryStorage, SurrealDBStorage, InstrumentedStorage],
        ids=["protocol", "memory", "surrealdb", "instrumented"],
    )
    def test_relabel_edges_is_absent(self, backend):
        assert not hasattr(backend, "relabel_edges")


class TestTheNominationsStillHaveSomewhereToGo:
    """Removal was safe only because stage 3 landed first."""

    async def test_a_nominated_pair_is_judged_and_stops_coming_back(self, storage):
        from epimemer.pipelines.reflection.relation_consolidation import (
            sweep_similar_relation_pairs,
        )

        for label in ("works_for", "employed_by"):
            src = Topic(content=f"{label}-src", source_id="s")
            dst = Topic(content=f"{label}-dst", source_id="s")
            await storage.store_node(src)
            await storage.store_node(dst)
            await tools.link(src.id, dst.id, storage, relation=label, judge=CRITIC)

        nominated = await sweep_similar_relation_pairs(storage, TWINS, similarity_threshold=0.9)
        assert len(nominated.pairs) == 1, "the pair has to be offered first"

        result, _ = await tools.apply_reflection(
            storage,
            TWINS,
            relation_verdicts=[
                {
                    "pair": ["works_for", "employed_by"],
                    "kind": "relationship",
                    "verdict": "synonymous",
                    "because": "one relationship written two ways",
                }
            ],
            judge=CRITIC,
        )
        assert result["relation_verdicts_recorded"] == 1

        after = await sweep_similar_relation_pairs(storage, TWINS, similarity_threshold=0.9)
        assert after.pairs == []
        assert after.suppressed == 1

    async def test_synonymous_rewrites_nothing(self, storage):
        """The verdict that would once have been a merge. Both labels survive."""
        for label in ("works_for", "employed_by"):
            src = Topic(content=f"{label}-src", source_id="s")
            dst = Topic(content=f"{label}-dst", source_id="s")
            await storage.store_node(src)
            await storage.store_node(dst)
            await tools.link(src.id, dst.id, storage, relation=label, judge=CRITIC)

        await tools.apply_reflection(
            storage,
            TWINS,
            relation_verdicts=[
                {
                    "pair": ["works_for", "employed_by"],
                    "kind": "relationship",
                    "verdict": "synonymous",
                    "because": "one relationship written two ways",
                }
            ],
            judge=CRITIC,
        )

        labels = {record.name for record in await storage.query_relation_labels()}
        assert {"works_for", "employed_by"} <= labels
