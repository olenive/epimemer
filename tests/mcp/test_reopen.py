"""A decline can be taken back, and taking it back asserts nothing.

Three nominators suppress what an agent has already answered, and none of the
three suppressions could be withdrawn: the `assessed` edge for a fact pair, a
`RelationVerdict` for a label pair, and a `retention` journal row for a single
node. A pair judged `distinct` in error never came back, however much later
evidence said it should.

`reopen` withdraws one suppression and does nothing else. It never records the
opposite of the earlier verdict: the pair is offered again if it still
qualifies, and the next judge answers it afresh. Everything stays in the
record, so a pair reopened and judged `distinct` a second time carries both
rounds and is suppressed again by the newer one.

What is pinned here: that each of the three suppressions clears, that `reflect`
re-offers the question with the history attached so the next judge can see it
was reopened and why, that the refusals name what was looked for, that every
reopen leaves a `reopened` journal row carrying the reason, and that a fresh
verdict suppresses again.

Both backends, through the `storage` fixture.
"""

import pytest

from epimemer.core.types import (
    BASE_METACONTEXT_ID,
    ClaimKind,
    DecisionKind,
    EdgeType,
    EmbeddingRecord,
    Fact,
    JudgeRef,
    NodeEdge,
    Topic,
    ValueSignal,
    edge_is_live,
    relation_pair_key,
)
from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.mcp import tools
from epimemer.pipelines.reflection.archival import nominate_archival_candidates
from epimemer.pipelines.reflection.relation_consolidation import (
    sweep_similar_relation_pairs,
)
from epimemer.pipelines.reflection.retention import record_retention
from epimemer.pipelines.reflection.similarity_decisions import (
    already_judged_pairs,
    apply_similarity_decision,
)

CRITIC = JudgeRef(agent_id="critic", digest="d1")
EDITOR = JudgeRef(agent_id="editor", digest="d2")

# Two facts an embedding provider cannot tell apart, which is what puts them in
# front of the pair nominator at all.
_TWIN = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]


@pytest.fixture
def embedding_provider() -> MockEmbeddingProvider:
    return MockEmbeddingProvider(model_id="mock-embed", dimension=8)


async def _fact(storage, content: str, *, vector=None) -> Fact:
    fact = Fact(content=content, source_id="seg-1", claim_kind=ClaimKind.STATE)
    await storage.store_node(fact)
    await storage.store_edge(
        NodeEdge(src_id=fact.id, dst_id=BASE_METACONTEXT_ID, type=EdgeType.HAS_METACONTEXT)
    )
    if vector is not None:
        await storage.store_embedding(
            EmbeddingRecord(item_id=fact.id, model_id="mock-embed", vector=list(vector))
        )
    return fact


async def _twins(storage) -> tuple[Fact, Fact]:
    """Two active facts the nominator offers as a pair."""
    return (
        await _fact(storage, "The bridge opened in 1932.", vector=_TWIN),
        await _fact(storage, "The bridge was opened in 1932.", vector=_TWIN),
    )


async def _declined(storage, a: Fact, b: Fact, *, judge=CRITIC) -> None:
    outcome = await apply_similarity_decision(
        storage,
        a_id=a.id,
        b_id=b.id,
        verdict="distinct",
        because="Two bridges, one date.",
        judge=judge,
    )
    assert getattr(outcome, "edges_created", 0) >= 1


async def _assessed_edges(storage, a: Fact, b: Fact) -> list[NodeEdge]:
    found = await storage.get_edges_for([a.id, b.id], direction="from", edge_type=EdgeType.ASSESSED)
    return [edge for edges in found.values() for edge in edges]


async def _reopen_rows(storage):
    return await storage.query_decisions(kinds=[DecisionKind.REOPENED])


async def _contradiction_pairs(storage, embedding_provider) -> list[dict]:
    result, _ = await tools.reflect(storage, embedding_provider)
    return result["contradictions"]


# --- Layer one: a fact pair judged `distinct` ---------------------------------


class TestAFactPairComesBack:
    """The `assessed` edge is retired the way a moved `TIMELINK` is: stamped
    with when it stopped counting and by whom, never deleted."""

    async def test_a_declined_pair_is_suppressed_until_it_is_reopened(self, storage):
        a, b = await _twins(storage)
        await _declined(storage, a, b)
        assert frozenset({a.id, b.id}) in await already_judged_pairs([a.id, b.id], storage)

        result, _ = await tools.reopen(
            storage,
            node_ids=[a.id, b.id],
            reason="New source dates the second bridge.",
            judge=EDITOR,
        )

        assert result["reopened"] is True
        assert frozenset({a.id, b.id}) not in await already_judged_pairs([a.id, b.id], storage)

    async def test_the_edge_is_retired_rather_than_deleted(self, storage):
        a, b = await _twins(storage)
        await _declined(storage, a, b)

        await tools.reopen(
            storage, node_ids=[a.id, b.id], reason="Worth another look.", judge=EDITOR
        )

        edges = await _assessed_edges(storage, a, b)
        assert len(edges) == 1, "the record of the first verdict must survive"
        assert not edge_is_live(edges[0])
        assert edges[0].retired_by.agent_id == "editor"
        assert edges[0].metadata["verdict"] == "distinct", "the first verdict is still readable"

    async def test_reflect_offers_the_pair_again_and_says_it_was_reopened(
        self, storage, embedding_provider
    ):
        a, b = await _twins(storage)
        await _declined(storage, a, b)
        assert await _contradiction_pairs(storage, embedding_provider) == []

        await tools.reopen(
            storage,
            node_ids=[a.id, b.id],
            reason="A second source dates the other bridge to 1934.",
            judge=EDITOR,
        )

        offered = await _contradiction_pairs(storage, embedding_provider)
        assert len(offered) == 1
        history = offered[0]["reopened"]
        assert history["reason"] == "A second source dates the other bridge to 1934."
        assert history["judged_by"] == "editor"
        assert history["at"]

    async def test_a_pair_nobody_reopened_carries_no_history(self, storage, embedding_provider):
        await _twins(storage)

        offered = await _contradiction_pairs(storage, embedding_provider)

        assert len(offered) == 1
        assert "reopened" not in offered[0]

    async def test_a_fresh_distinct_suppresses_it_again_and_both_rounds_stand(self, storage):
        a, b = await _twins(storage)
        await _declined(storage, a, b)
        await tools.reopen(storage, node_ids=[a.id, b.id], reason="Look again.", judge=EDITOR)

        await _declined(storage, a, b, judge=EDITOR)

        assert frozenset({a.id, b.id}) in await already_judged_pairs([a.id, b.id], storage)
        edges = await _assessed_edges(storage, a, b)
        assert len(edges) == 2, "both rounds stay in the graph"
        assert sorted(edge_is_live(edge) for edge in edges) == [False, True]

    async def test_a_pair_nothing_suppresses_is_refused(self, storage):
        a, b = await _twins(storage)

        result, _ = await tools.reopen(
            storage, node_ids=[a.id, b.id], reason="Let us see it again.", judge=EDITOR
        )

        assert result["reopened"] is False
        assert "assessed" in result["refused"]

    async def test_a_node_that_does_not_exist_is_refused(self, storage):
        a, _ = await _twins(storage)

        result, _ = await tools.reopen(
            storage, node_ids=[a.id, "no-such-node"], reason="Try it.", judge=EDITOR
        )

        assert result["reopened"] is False
        assert "no-such-node" in result["refused"]

    async def test_a_standing_affirmative_edge_is_named_rather_than_withdrawn(self, storage):
        """`reopen` undoes a decline. A pair that was judged one claim carries a
        `similarity` edge, and withdrawing that would take back a corroboration
        count, which is the opposite verdict rather than the question again."""
        a, b = await _twins(storage)
        await apply_similarity_decision(
            storage,
            a_id=a.id,
            b_id=b.id,
            verdict="one_claim",
            because="One bridge, two phrasings.",
            judge=CRITIC,
        )

        result, _ = await tools.reopen(
            storage, node_ids=[a.id, b.id], reason="Second thoughts.", judge=EDITOR
        )

        assert result["reopened"] is False
        assert "similarity" in result["refused"]


# --- Layer two: a relation label pair ----------------------------------------


class _FixedEmbed:
    """One fixed vector per exact string, so a nomination is not left to a hash."""

    model_id = "fixed"

    def __init__(self, mapping):
        self.mapping = mapping

    async def embed(self, texts):
        return [self.mapping[t] for t in texts]


TWINS = _FixedEmbed({"works_for": [1.0, 0.0], "employed_by": [1.0, 0.0]})


async def _topic(storage, content) -> Topic:
    node = Topic(content=content, source_id="seg-1")
    await storage.store_node(node)
    return node


async def _label(storage, label: str) -> None:
    src = await _topic(storage, f"{label}-src")
    dst = await _topic(storage, f"{label}-dst")
    await tools.link(src.id, dst.id, storage, relation=label, kind="relationship")


async def _label_pair(storage) -> None:
    await _label(storage, "works_for")
    await _label(storage, "employed_by")


async def _nominated_labels(storage) -> list[dict]:
    sweep = await sweep_similar_relation_pairs(storage, TWINS, similarity_threshold=0.9)
    return sweep.pairs


async def _decline_labels(storage, *, judge=CRITIC) -> None:
    outcome = await tools.apply_reflection(
        storage,
        MockEmbeddingProvider(model_id="mock-embed", dimension=8),
        relation_verdicts=[
            {
                "pair": ["works_for", "employed_by"],
                "kind": "relationship",
                "verdict": "distinct",
                "because": "A servant is not an employee.",
            }
        ],
        judge=judge,
    )
    assert outcome[0]["relation_verdicts_recorded"]


class TestALabelPairComesBack:
    """A new verdict row of a new kind, appended rather than written over: the
    table is append-only, so a reopen is a row like every other."""

    async def test_a_judged_pair_is_suppressed_until_it_is_reopened(self, storage):
        await _label_pair(storage)
        await _decline_labels(storage)
        assert await _nominated_labels(storage) == []

        result, _ = await tools.reopen(
            storage,
            relation_labels=["works_for", "employed_by"],
            reason="The corpus now carries both in one sense.",
            judge=EDITOR,
        )

        assert result["reopened"] is True
        assert len(await _nominated_labels(storage)) == 1

    async def test_the_nomination_carries_the_history(self, storage):
        await _label_pair(storage)
        await _decline_labels(storage)
        await tools.reopen(
            storage,
            relation_labels=["works_for", "employed_by"],
            reason="Both now name the same relation.",
            judge=EDITOR,
        )

        nominated = await _nominated_labels(storage)

        assert nominated[0]["reopened"]["reason"] == "Both now name the same relation."
        assert nominated[0]["reopened"]["judged_by"] == "editor"

    async def test_the_earlier_verdict_is_still_readable(self, storage):
        await _label_pair(storage)
        await _decline_labels(storage)
        await tools.reopen(
            storage,
            relation_labels=["works_for", "employed_by"],
            reason="Look again.",
            judge=EDITOR,
        )

        rows = await storage.query_relation_verdicts()

        assert sorted(row.verdict for row in rows) == ["distinct", "reopened"]

    async def test_a_fresh_verdict_suppresses_it_again(self, storage):
        await _label_pair(storage)
        await _decline_labels(storage)
        await tools.reopen(
            storage,
            relation_labels=["works_for", "employed_by"],
            reason="Look again.",
            judge=EDITOR,
        )

        await _decline_labels(storage, judge=EDITOR)

        assert await _nominated_labels(storage) == []

    async def test_a_pair_nothing_suppresses_is_refused(self, storage):
        await _label_pair(storage)

        result, _ = await tools.reopen(
            storage,
            relation_labels=["works_for", "employed_by"],
            reason="Let us see it.",
            judge=EDITOR,
        )

        assert result["reopened"] is False
        assert "verdict" in result["refused"]

    async def test_a_label_no_edge_carries_is_refused(self, storage):
        await _label_pair(storage)

        result, _ = await tools.reopen(
            storage,
            relation_labels=["works_for", "paid_by"],
            reason="Try it.",
            judge=EDITOR,
        )

        assert result["reopened"] is False
        assert "paid_by" in result["refused"]

    async def test_the_journal_names_the_two_label_records(self, storage):
        await _label_pair(storage)
        await _decline_labels(storage)
        await tools.reopen(
            storage,
            relation_labels=["works_for", "employed_by"],
            reason="Look again.",
            judge=EDITOR,
        )

        labels = {record.name: record.id for record in await storage.query_relation_labels()}
        row = (await _reopen_rows(storage))[0]

        assert row.subject_ids == list(
            relation_pair_key(labels["works_for"], labels["employed_by"])
        )


# --- Layer three: a node kept for its own sake --------------------------------


async def _never_retrieved(storage) -> Fact:
    """A fact no search has returned and nothing points at.

    The `never_retrieved` shape, whose keep verdict covers nothing and so could
    never be outranked by a reason arriving later.
    """
    node = Fact(
        content="The parish kept two bridges.",
        source_id="seg-1",
        claim_kind=ClaimKind.STATE,
        value=ValueSignal(importance=0.2),
    )
    await storage.store_node(node)
    await storage.store_edge(
        NodeEdge(src_id=node.id, dst_id=BASE_METACONTEXT_ID, type=EdgeType.HAS_METACONTEXT)
    )
    return node


async def _nominated_nodes(storage) -> set[str]:
    return {c.node_id for c in await nominate_archival_candidates(storage)}


class TestAKeptNodeComesBack:
    """A keep verdict covering nothing can never be outranked by a later reason,
    so it was permanent exactly as a wrong `distinct` was."""

    async def test_a_kept_node_is_suppressed_until_it_is_reopened(self, storage):
        node = await _never_retrieved(storage)
        assert node.id in await _nominated_nodes(storage)
        await record_retention(
            storage, node_id=node.id, because="Still worth having.", reasons=[], judge=CRITIC
        )
        assert node.id not in await _nominated_nodes(storage)

        result, _ = await tools.reopen(
            storage, node_ids=[node.id], reason="Nothing has read it in a year.", judge=EDITOR
        )

        assert result["reopened"] is True
        assert node.id in await _nominated_nodes(storage)

    async def test_reflect_offers_it_again_with_the_history(self, storage, embedding_provider):
        node = await _never_retrieved(storage)
        await record_retention(
            storage, node_id=node.id, because="Still worth having.", reasons=[], judge=CRITIC
        )
        await tools.reopen(
            storage, node_ids=[node.id], reason="Nothing has read it in a year.", judge=EDITOR
        )

        result, _ = await tools.reflect(storage, embedding_provider)
        offered = [c for c in result["archival_candidates"] if c["node_id"] == node.id]

        assert len(offered) == 1
        assert offered[0]["reopened"]["reason"] == "Nothing has read it in a year."
        assert offered[0]["reopened"]["judged_by"] == "editor"

    async def test_a_fresh_keep_suppresses_it_again(self, storage):
        node = await _never_retrieved(storage)
        await record_retention(
            storage, node_id=node.id, because="Still worth having.", reasons=[], judge=CRITIC
        )
        await tools.reopen(storage, node_ids=[node.id], reason="Look again.", judge=EDITOR)

        await record_retention(
            storage, node_id=node.id, because="Read it; it stands.", reasons=[], judge=EDITOR
        )

        assert node.id not in await _nominated_nodes(storage)

    async def test_a_node_nothing_suppresses_is_refused(self, storage):
        node = await _never_retrieved(storage)

        result, _ = await tools.reopen(
            storage, node_ids=[node.id], reason="Let us see it.", judge=EDITOR
        )

        assert result["reopened"] is False
        assert "retention" in result["refused"]

    async def test_a_node_that_does_not_exist_is_refused(self, storage):
        result, _ = await tools.reopen(
            storage, node_ids=["no-such-node"], reason="Try it.", judge=EDITOR
        )

        assert result["reopened"] is False
        assert "no-such-node" in result["refused"]


# --- What every reopen has in common -----------------------------------------


class TestTheCallItself:
    async def test_exactly_one_target(self, storage):
        a, b = await _twins(storage)
        await _declined(storage, a, b)
        await _label_pair(storage)

        both, _ = await tools.reopen(
            storage,
            node_ids=[a.id, b.id],
            relation_labels=["works_for", "employed_by"],
            reason="Both at once.",
            judge=EDITOR,
        )
        neither, _ = await tools.reopen(storage, reason="Nothing at all.", judge=EDITOR)

        assert both["reopened"] is False
        assert neither["reopened"] is False

    async def test_three_node_ids_are_refused(self, storage):
        a, b = await _twins(storage)
        c = await _fact(storage, "A third bridge.")

        result, _ = await tools.reopen(
            storage, node_ids=[a.id, b.id, c.id], reason="All three.", judge=EDITOR
        )

        assert result["reopened"] is False

    async def test_a_reason_is_required(self, storage):
        a, b = await _twins(storage)
        await _declined(storage, a, b)

        result, _ = await tools.reopen(storage, node_ids=[a.id, b.id], reason="   ", judge=EDITOR)

        assert result["reopened"] is False
        assert "reason" in result["refused"]

    async def test_the_journal_row_carries_the_reason_and_no_certainty(self, storage):
        a, b = await _twins(storage)
        await _declined(storage, a, b)

        result, _ = await tools.reopen(
            storage, node_ids=[a.id, b.id], reason="A second source disagrees.", judge=EDITOR
        )

        rows = await _reopen_rows(storage)
        assert len(rows) == 1
        assert rows[0].kind is DecisionKind.REOPENED
        assert rows[0].subject_ids == sorted([a.id, b.id])
        assert rows[0].certainty_basis == "A second source disagrees."
        assert rows[0].certainty is None
        assert rows[0].judged_by.agent_id == "editor"
        assert result["decision_id"] == rows[0].id

    async def test_a_refusal_journals_nothing(self, storage):
        a, b = await _twins(storage)

        await tools.reopen(storage, node_ids=[a.id, b.id], reason="Nothing to undo.", judge=EDITOR)

        assert await _reopen_rows(storage) == []
