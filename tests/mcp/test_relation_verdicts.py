"""A declined label pair stays declined (#74 stage 3, FC1).

The live defect this closes: `sweep_similar_relation_pairs` re-derives from
scratch on every `reflect` and recorded nothing about declines, so a pair an
agent considered and rejected came back on every pass, for ever, to a fresh
agent who could not see the previous refusals. Worse than wasted attention —
**accepting a merge makes one label vanish and therefore suppresses itself,
while declining did not**, so the graph applied quiet pressure toward the wrong
answer.

#64 closed exactly this for fact pairs with the `assessed` edge. Relation labels
could not have one: that edge runs between two *nodes*, and `works_for` and
`employed_by` are not nodes. Stage 1 gave them records; this is what those
records were for, and the journal row naming both of them is where `ISSUES.md`
#69 finally resolves.

What is pinned here beyond the regression itself: that both verdicts suppress,
that suppression is keyed on ids and so survives a description or a re-use, that
it is **permanent by design** rather than by oversight, and that none of it
needs the CLI — the backfill refuses embedded backends, which is the default
development configuration.

Both backends via the `storage` fixture.
"""

import pytest

from epimemer.core.types import (
    DecisionKind,
    EdgeType,
    JudgeRef,
    NodeEdge,
    Topic,
    relation_pair_key,
)
from epimemer.mcp import tools
from epimemer.pipelines.reflection.relation_consolidation import (
    sweep_similar_relation_pairs,
)


CRITIC = JudgeRef(agent_id="critic", digest="d1")
EDITOR = JudgeRef(agent_id="editor", digest="d2")


class _FixedEmbed:
    """One fixed vector per exact string, so a nomination is not left to a hash."""

    model_id = "fixed"

    def __init__(self, mapping):
        self.mapping = mapping

    async def embed(self, texts):
        return [self.mapping[t] for t in texts]


# `works_for` and `employed_by` collide; `funded_by` is the control that must
# never be nominated with either.
TWINS = _FixedEmbed({
    "works_for": [1.0, 0.0],
    "employed_by": [1.0, 0.0],
    "funded_by": [0.0, 1.0],
})


async def _node(storage, content):
    node = Topic(content=content, source_id="seg1")
    await storage.store_node(node)
    return node


async def _edge(storage, label, *, kind="relationship", judge=None):
    """Use `label` on a fresh edge, which is what coins it."""
    src = await _node(storage, f"{label}-src")
    dst = await _node(storage, f"{label}-dst")
    await tools.link(src.id, dst.id, storage, relation=label, kind=kind, judge=judge)
    return src, dst


async def _bare_edge(storage, label, *, kind="relationship"):
    """An edge written straight to the store — a label with **no record**.

    What a graph written before stage 1 looks like, and the state the
    suppression read has to fail safe on.
    """
    src = await _node(storage, f"{label}-src")
    dst = await _node(storage, f"{label}-dst")
    await storage.store_edge(
        NodeEdge(
            src_id=src.id, dst_id=dst.id, type=EdgeType.RELATED,
            label=label, kind=kind,
        )
    )
    return src, dst


async def _twin_labels(storage, *, bare=False):
    write = _bare_edge if bare else _edge
    await write(storage, "works_for")
    await write(storage, "employed_by")
    await write(storage, "funded_by")


async def _nominated(storage):
    pairs = (await sweep_similar_relation_pairs(storage, TWINS, similarity_threshold=0.9)).pairs
    return {frozenset((p["label_a"], p["label_b"])) for p in pairs}


async def _judge_pair(
    storage, verdict="distinct", *, because="Different relationships.",
    judge=CRITIC, kind="relationship", pair=("works_for", "employed_by"),
):
    result, _ = await tools.apply_reflection(
        storage,
        TWINS,
        relation_verdicts=[
            {"pair": list(pair), "kind": kind, "verdict": verdict, "because": because}
        ],
        judge=judge,
    )
    return result


class TestTheTreadmillStops:
    """FC1's regression test, and the one that must be shown to fail without
    the filter — which the second test here does, by removing the only thing
    that suppresses."""

    async def test_a_declined_pair_is_not_nominated_again(self, storage):
        await _twin_labels(storage)
        assert frozenset(("works_for", "employed_by")) in await _nominated(storage)

        await _judge_pair(storage, "distinct")

        assert await _nominated(storage) == set()

    async def test_the_same_pair_is_nominated_without_a_verdict(self, storage):
        """The control for the test above. Everything is identical except that
        nothing was judged — so a green regression test cannot be green because
        the nomination stopped happening for some other reason."""
        await _twin_labels(storage)

        assert frozenset(("works_for", "employed_by")) in await _nominated(storage)
        assert frozenset(("works_for", "employed_by")) in await _nominated(storage)

    async def test_synonymous_suppresses_too(self, storage):
        """Both verdicts suppress. `synonymous` acts on nothing today, and
        recording it is still a judgment — leaving it unrecordable would be the
        same treadmill for the affirmative answer."""
        await _twin_labels(storage)

        await _judge_pair(storage, "synonymous", because="One relationship, two words.")

        assert await _nominated(storage) == set()

    async def test_an_unjudged_pair_still_comes_back(self, storage):
        """Suppression is per pair, not per sweep."""
        await _twin_labels(storage)
        await _edge(storage, "retained_by")
        embed = _FixedEmbed({
            "works_for": [1.0, 0.0],
            "employed_by": [1.0, 0.0],
            "retained_by": [0.99, 0.14],
            "funded_by": [0.0, 1.0],
        })

        await _judge_pair(storage, "distinct")

        pairs = (await sweep_similar_relation_pairs(storage, embed, similarity_threshold=0.9)).pairs
        got = {frozenset((p["label_a"], p["label_b"])) for p in pairs}
        assert frozenset(("works_for", "employed_by")) not in got
        assert frozenset(("works_for", "retained_by")) in got


class TestAPairIsOnePairWhicheverWayRoundItIsJudged:
    async def test_judging_b_a_suppresses_a_b(self, storage):
        """`relation_pair_key` sorts, so one pair is one row. A pair keyed two
        ways is a suppression index that silently suppresses half of what it
        should — and the sweep's `label_a`/`label_b` order comes from label
        discovery order, which nobody controls."""
        await _twin_labels(storage)

        await _judge_pair(storage, pair=("employed_by", "works_for"))

        assert await _nominated(storage) == set()

    async def test_the_stored_row_holds_the_ids_sorted(self, storage):
        await _twin_labels(storage)

        await _judge_pair(storage, pair=("employed_by", "works_for"))

        pairs = await storage.judged_relation_pairs()
        assert len(pairs) == 1
        ids = next(iter(pairs))
        assert list(ids) == sorted(ids)


class TestALabelWithNoRecordGetsOne:
    """The refusal an earlier draft wrote here was a dead end: the CLI backfill
    refuses embedded backends — the default development configuration — and an
    agent cannot run it in any case. *A remedy the agent cannot issue, on a
    backend where it refuses, is not a remedy.*"""

    async def test_a_verdict_creates_the_records_and_suppresses(self, storage):
        await _twin_labels(storage, bare=True)
        assert await storage.get_relation_label("works_for", "relationship") is None
        assert frozenset(("works_for", "employed_by")) in await _nominated(storage)

        result = await _judge_pair(storage)

        assert result["relation_verdicts_recorded"] == 1
        assert await storage.get_relation_label("works_for", "relationship") is not None
        assert await storage.get_relation_label("employed_by", "relationship") is not None
        assert await _nominated(storage) == set()

    async def test_the_created_record_carries_no_judge(self, storage):
        """`judged_by` is the coiner and never the judger. Judging a label
        against another is not a claim to have introduced the word."""
        await _twin_labels(storage, bare=True)

        await _judge_pair(storage, judge=CRITIC)

        record = await storage.get_relation_label("works_for", "relationship")
        assert record.judged_by is None

    async def test_a_coiners_record_is_not_restamped(self, storage):
        await _twin_labels(storage)

        await _judge_pair(storage, judge=EDITOR)

        record = await storage.get_relation_label("works_for", "relationship")
        assert record.judged_by is None  # `_edge` coined without a judge

    async def test_an_existing_coiner_survives_a_verdict(self, storage):
        await _edge(storage, "works_for", judge=CRITIC)
        await _edge(storage, "employed_by", judge=CRITIC)

        await _judge_pair(storage, judge=EDITOR)

        record = await storage.get_relation_label("works_for", "relationship")
        assert record.judged_by is not None and record.judged_by.agent_id == "critic"


class TestSuppressionIsFailSafe:
    async def test_a_pair_with_one_unrecorded_side_is_still_nominated(self, storage):
        """Suppression is keyed on record ids, so an unresolvable side means
        *nothing has been judged here yet* — never *the judgment cannot be
        found*. Failing the other way would silence pairs nobody has ever seen,
        on exactly the oldest graphs."""
        await _bare_edge(storage, "works_for")
        await _edge(storage, "employed_by")
        await _edge(storage, "funded_by")

        assert frozenset(("works_for", "employed_by")) in await _nominated(storage)


class TestBecauseIsRequired:
    async def test_a_blank_reason_is_refused(self, storage):
        await _twin_labels(storage)

        result = await _judge_pair(storage, because="   ")

        assert result["relation_verdicts_recorded"] == 0
        assert "`because` is required" in result["relation_verdicts_refused"][0]["reason"]

    async def test_a_refused_entry_does_not_suppress(self, storage):
        await _twin_labels(storage)

        await _judge_pair(storage, because="")

        assert frozenset(("works_for", "employed_by")) in await _nominated(storage)

    async def test_the_rest_of_the_batch_is_applied(self, storage):
        """Refused per entry, in the shape `similarities_refused` already uses.
        One malformed entry must not cost the graph the judgments beside it."""
        await _twin_labels(storage)
        await _edge(storage, "paid_by")
        embed = _FixedEmbed({
            "works_for": [1.0, 0.0],
            "employed_by": [1.0, 0.0],
            "funded_by": [0.0, 1.0],
            "paid_by": [0.0, 1.0],
        })

        result, _ = await tools.apply_reflection(
            storage,
            embed,
            relation_verdicts=[
                {"pair": ["works_for", "employed_by"], "kind": "relationship",
                 "verdict": "distinct", "because": ""},
                {"pair": ["funded_by", "paid_by"], "kind": "relationship",
                 "verdict": "distinct", "because": "Grant, not salary."},
            ],
            judge=CRITIC,
        )

        assert result["relation_verdicts_recorded"] == 1
        assert len(result["relation_verdicts_refused"]) == 1
        pairs = (await sweep_similar_relation_pairs(storage, embed, similarity_threshold=0.9)).pairs
        got = {frozenset((p["label_a"], p["label_b"])) for p in pairs}
        assert got == {frozenset(("works_for", "employed_by"))}


class TestOtherRefusals:
    async def test_a_label_no_edge_carries_is_refused(self, storage):
        await _twin_labels(storage)

        result = await _judge_pair(storage, pair=("works_for", "never_used"))

        assert result["relation_verdicts_recorded"] == 0
        assert "no edge in this graph carries" in (
            result["relation_verdicts_refused"][0]["reason"]
        )

    async def test_a_cross_kind_pair_is_refused(self, storage):
        """The kind decides whether retrieval follows the edge, so two labels of
        different kinds are never one relationship — and the sweep never
        nominates them as a pair."""
        await _edge(storage, "works_for")
        await _edge(storage, "cited_in", kind="attribution")

        result = await _judge_pair(storage, pair=("works_for", "cited_in"))

        assert result["relation_verdicts_recorded"] == 0
        assert "never one relationship" in (
            result["relation_verdicts_refused"][0]["reason"]
        )

    async def test_a_stale_kind_on_the_nomination_is_refused(self, storage):
        await _edge(storage, "cited_in", kind="attribution")
        await _edge(storage, "quoted_in", kind="attribution")

        result = await _judge_pair(
            storage, pair=("cited_in", "quoted_in"), kind="relationship"
        )

        assert result["relation_verdicts_recorded"] == 0
        assert "is 'attribution' in this graph" in (
            result["relation_verdicts_refused"][0]["reason"]
        )

    async def test_a_label_paired_with_itself_is_refused(self, storage):
        await _twin_labels(storage)

        result = await _judge_pair(storage, pair=("works_for", "works_for"))

        assert result["relation_verdicts_recorded"] == 0
        assert "already itself" in result["relation_verdicts_refused"][0]["reason"]

    async def test_an_unknown_verdict_is_refused(self, storage):
        """Rejected rather than defaulted: a default would pick one of two
        judgments on behalf of an agent that made neither."""
        await _twin_labels(storage)

        result = await _judge_pair(storage, verdict="compatible")

        assert result["relation_verdicts_recorded"] == 0
        assert "not a verdict about a label pair" in (
            result["relation_verdicts_refused"][0]["reason"]
        )


class TestARetryIsNotASecondOpinion:
    async def test_the_same_judge_repeating_itself_is_refused(self, storage):
        await _twin_labels(storage)
        await _judge_pair(storage, judge=CRITIC)

        result = await _judge_pair(storage, judge=CRITIC)

        assert result["relation_verdicts_recorded"] == 0
        assert result["relation_verdicts_confirmed"] == 0
        assert "a retry is not a second opinion" in (
            result["relation_verdicts_refused"][0]["reason"]
        )

    async def test_a_retry_writes_no_second_row(self, storage):
        await _twin_labels(storage)
        await _judge_pair(storage, judge=CRITIC)

        await _judge_pair(storage, judge=CRITIC)

        ids = list(await storage.judged_relation_pairs())[0]
        assert len(await storage.relation_verdicts_for(list(ids))) == 1

    async def test_two_unnamed_judges_compare_equal(self, storage):
        """Where a graph does not require a judge, an anonymous repeat is
        indistinguishable from a replayed batch — and the two want opposite
        treatments. Refusing costs an unnamed agent a confirmation the journal's
        first row already records; accepting would manufacture agreement out of
        nobody."""
        await _twin_labels(storage)
        await _judge_pair(storage, judge=None)

        result = await _judge_pair(storage, judge=None)

        assert result["relation_verdicts_recorded"] == 0
        assert len(result["relation_verdicts_refused"]) == 1


class TestASecondJudgeConfirms:
    async def test_a_different_judge_agreeing_is_a_confirmation(self, storage):
        await _twin_labels(storage)
        await _judge_pair(storage, judge=CRITIC)

        result = await _judge_pair(storage, judge=EDITOR)

        assert result["relation_verdicts_confirmed"] == 1
        assert result["relation_verdicts_recorded"] == 0
        assert result["relation_verdicts_refused"] == []

    async def test_the_confirmation_cites_the_original(self, storage):
        """`_journal_pair_judgment(created=False)` — the established shape, not
        a new one. It is what stops a third agent doing the work a fourth
        time."""
        await _twin_labels(storage)
        await _judge_pair(storage, judge=CRITIC)
        await _judge_pair(storage, judge=EDITOR)

        rows = await storage.query_decisions(kinds=[DecisionKind.RELATION_VERDICT])
        by_judge = {r.judged_by.agent_id: r for r in rows}
        assert by_judge["editor"].reviews == by_judge["critic"].id
        assert by_judge["critic"].reviews is None

    async def test_a_confirmation_writes_no_second_verdict_row(self, storage):
        await _twin_labels(storage)
        await _judge_pair(storage, judge=CRITIC)

        await _judge_pair(storage, judge=EDITOR)

        ids = list(await storage.judged_relation_pairs())[0]
        standing = await storage.relation_verdicts_for(list(ids))
        assert len(standing) == 1
        assert standing[0].judged_by.agent_id == "critic"

    async def test_a_second_judge_disagreeing_records_a_second_verdict(self, storage):
        """Append-only: both rows survive, with their judges and their reasons.
        Nothing is withdrawn and nothing is overruled — since both verdicts
        suppress, a disagreement changes nothing operationally. It is made
        visible rather than resolved (`ISSUES.md` #80)."""
        await _twin_labels(storage)
        await _judge_pair(storage, "distinct", judge=CRITIC)

        result = await _judge_pair(
            storage, "synonymous", because="One relationship.", judge=EDITOR
        )

        assert result["relation_verdicts_recorded"] == 1
        ids = list(await storage.judged_relation_pairs())[0]
        standing = await storage.relation_verdicts_for(list(ids))
        assert {v.verdict for v in standing} == {"distinct", "synonymous"}


class TestTheJournalRow:
    """Where #69 resolves. The question was unanswerable while a label had no
    id: the alternatives were a second namespace inside `subject_ids`, or the
    endpoint nodes of edges the decision was not about."""

    async def test_the_row_names_both_label_ids(self, storage):
        await _twin_labels(storage)

        await _judge_pair(storage, judge=CRITIC)

        row = (await storage.query_decisions(
            kinds=[DecisionKind.RELATION_VERDICT]
        ))[0]
        a = await storage.get_relation_label("works_for", "relationship")
        b = await storage.get_relation_label("employed_by", "relationship")
        assert row.subject_ids == list(relation_pair_key(a.id, b.id))
        assert row.judged_by.agent_id == "critic"

    async def test_review_dereferences_them(self, storage):
        await _twin_labels(storage)
        await _judge_pair(storage, judge=CRITIC)

        result, _ = await tools.review(storage)
        row = next(
            d for d in result["decisions"]
            if d["kind"] == DecisionKind.RELATION_VERDICT.value
        )

        previews = {s["content_preview"] for s in row["subjects"]}
        assert previews == {
            "works_for (relationship)", "employed_by (relationship)"
        }
        assert {s["subject_kind"] for s in row["subjects"]} == {"relation_label"}

    async def test_a_label_subject_is_not_declared_as_a_retrieved_node(self, storage):
        """`retrieved` drives focus in the viewer, and a label is not a node.
        Declaring one would ask the viewer to focus on nothing."""
        await _twin_labels(storage)
        await _judge_pair(storage, judge=CRITIC)

        _, meta = await tools.review(storage)

        a = await storage.get_relation_label("works_for", "relationship")
        assert a.id not in (meta.retrieved or [])

    async def test_it_is_its_own_kind(self, storage):
        """Review *selects* on kind, and a reviewer auditing judgments about
        claims does not want judgments about vocabulary mixed in."""
        await _twin_labels(storage)

        await _judge_pair(storage)

        assert await storage.query_decisions(kinds=[DecisionKind.SIMILARITY]) == []
        assert len(
            await storage.query_decisions(kinds=[DecisionKind.RELATION_VERDICT])
        ) == 1

    async def test_the_row_names_no_node(self, storage):
        """The subjects are label records, so nothing surfaces against a node
        nobody judged. That is the rejected #69 option — the endpoint ids of the
        edges carrying the label — which satisfied *ids only* by filing the row
        under topics the decision was not about."""
        await _twin_labels(storage)
        await _judge_pair(storage)

        row = (await storage.query_decisions(
            kinds=[DecisionKind.RELATION_VERDICT]
        ))[0]
        every_node = {node.id for node in await storage.query_nodes()}
        assert set(row.subject_ids) & every_node == set()
        assert await storage.get_nodes(row.subject_ids) == {}


class TestVerdictsArePerGraph:
    async def test_another_graph_is_unaffected(self, storage):
        await _twin_labels(storage)
        await _judge_pair(storage)
        assert await _nominated(storage) == set()

        here = storage.current_database
        await storage.switch_database("elsewhere")
        try:
            await _twin_labels(storage)
            assert frozenset(("works_for", "employed_by")) in await _nominated(storage)
        finally:
            await storage.switch_database(here)

        assert await _nominated(storage) == set()


class TestAVerdictIsReadableWhereTheVocabularyIs:
    """Requiring `because` was justified by the next agent — who otherwise
    skips the pair without knowing whether it was examined or waved through —
    and until `list_relations` carried the verdicts, that next agent could not
    learn either, from anywhere: the table's other reads serve the sweep and
    the write path, and the journal row names the pair but never which way or
    why."""

    async def test_list_relations_carries_the_standing_verdict(self, storage):
        await _twin_labels(storage)
        await _judge_pair(storage, "distinct", judge=CRITIC)

        result, _ = await tools.list_relations(storage)
        by_label = {r["label"]: r for r in result["relations"]}

        [v] = by_label["works_for"]["verdicts"]
        assert v["with"] == "employed_by"
        assert v["verdict"] == "distinct"
        assert v["because"] == "Different relationships."
        assert v["judged_by"] == "critic"
        assert v["decided_at"]

    async def test_the_verdict_reads_from_both_sides_of_the_pair(self, storage):
        """One row, two labels: the reader arrives from either one."""
        await _twin_labels(storage)
        await _judge_pair(storage)

        result, _ = await tools.list_relations(storage)
        by_label = {r["label"]: r for r in result["relations"]}

        [v] = by_label["employed_by"]["verdicts"]
        assert v["with"] == "works_for"
        assert by_label["funded_by"]["verdicts"] == []

    async def test_an_unjudged_vocabulary_carries_none(self, storage):
        await _twin_labels(storage)

        result, _ = await tools.list_relations(storage)

        assert all(r["verdicts"] == [] for r in result["relations"])

    async def test_a_disagreement_shows_both_rows(self, storage):
        """Append-only survives into the read: a disagreement is made visible
        rather than resolved, so both verdicts are shown with their reasons."""
        await _twin_labels(storage)
        await _judge_pair(storage, "distinct", judge=CRITIC)
        await _judge_pair(
            storage, "synonymous", because="One relationship.", judge=EDITOR
        )

        result, _ = await tools.list_relations(storage)
        by_label = {r["label"]: r for r in result["relations"]}

        assert {
            v["verdict"] for v in by_label["works_for"]["verdicts"]
        } == {"distinct", "synonymous"}

    async def test_an_unattributed_verdict_names_no_judge(self, storage):
        await _twin_labels(storage)
        await _judge_pair(storage, judge=None)

        result, _ = await tools.list_relations(storage)
        by_label = {r["label"]: r for r in result["relations"]}

        [v] = by_label["works_for"]["verdicts"]
        assert v["judged_by"] is None


class TestReflectSaysWhatItSuppressed:
    async def test_the_count_tells_settled_from_unexamined(self, storage):
        """The sweep drops judged pairs silently, so without the count an
        empty `similar_relations` on a well-judged graph reads exactly like a
        graph with nothing similar in it."""
        await _twin_labels(storage)

        before, _ = await tools.reflect(storage, TWINS)
        assert before["relation_pairs_suppressed"] == 0
        assert before["similar_relations"] != []

        await _judge_pair(storage)

        after, _ = await tools.reflect(storage, TWINS)
        assert after["similar_relations"] == []
        assert after["relation_pairs_suppressed"] == 1


class TestKindIsCopiedNotDefaulted:
    """A default would state 'relationship' on behalf of an agent who stated
    nothing, and the stale-kind refusal would then blame them for it — an
    attribution pair refused for a claim the agent never made. Missing reads
    like missing `because`: refused, naming the field."""

    async def test_an_entry_omitting_kind_is_refused(self, storage):
        await _edge(storage, "cited_in", kind="attribution")
        await _edge(storage, "quoted_in", kind="attribution")

        result, _ = await tools.apply_reflection(
            storage,
            TWINS,
            relation_verdicts=[{
                "pair": ["cited_in", "quoted_in"],
                "verdict": "distinct",
                "because": "Citation is not quotation.",
            }],
            judge=CRITIC,
        )

        assert result["relation_verdicts_recorded"] == 0
        assert "`kind` is required" in (
            result["relation_verdicts_refused"][0]["reason"]
        )

    async def test_a_kind_less_entry_does_not_suppress(self, storage):
        await _twin_labels(storage)

        await tools.apply_reflection(
            storage,
            TWINS,
            relation_verdicts=[{
                "pair": ["works_for", "employed_by"],
                "verdict": "distinct",
                "because": "Different relationships.",
            }],
            judge=CRITIC,
        )

        assert frozenset(("works_for", "employed_by")) in await _nominated(storage)


class TestSuppressionIsPermanentByDesign:
    """Inherited from the fact-pair layer deliberately rather than by accident,
    so a wrong `distinct` silences a pair for good. Stated here rather than left
    for a later reader to decide it is a bug — the dual of the futile-cycle rule,
    and `ISSUES.md` #80 is where a retraction would be argued."""

    async def test_describing_a_label_does_not_reopen_the_pair(self, storage):
        await _twin_labels(storage)
        await _judge_pair(storage)

        await tools.describe_relation(
            "works_for", storage, description="Employment, not retainer."
        )

        assert await _nominated(storage) == set()

    async def test_re_using_a_label_does_not_reopen_the_pair(self, storage):
        """Suppression is keyed on the record's id, and re-coining does not mint
        a new one — which is exactly what `recorded_relation_label` preserves."""
        await _twin_labels(storage)
        await _judge_pair(storage)

        await _edge(storage, "works_for", judge=EDITOR)

        assert await _nominated(storage) == set()

    async def test_nothing_in_the_tool_surface_withdraws_a_verdict(self, storage):
        await _twin_labels(storage)
        await _judge_pair(storage)

        with pytest.raises(TypeError):
            await tools.apply_reflection(
                storage, TWINS, relation_verdict_retractions=[["works_for", "employed_by"]]
            )
