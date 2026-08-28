"""A relation label gets a record (the label record, stage 1).

A user-tier relationship label used to exist **nowhere**: `list_relations`
derived the vocabulary by scanning edges and grouping by `(label, kind)`, so
there was no row, no id and no description — a string repeated on every edge
that carried it. Nothing could describe it, nothing could name it in a decision,
and "renaming" one meant rewriting every edge in place.

Stage 1 is deliberately additive and changes no behaviour: the record comes into
existence at the moment a label is coined, every read tolerates its absence, and
nothing yet reads the description. What is pinned here is the part later stages
build on — that identity exists, that it is per graph, and that coining is the
only act that claims to have introduced a word.

Both backends via the `storage` fixture, because a record the two stores disagree
about is the divergence `tests/conftest.py` exists for.
"""

from epimemer.core.types import (
    EdgeType,
    JudgeRef,
    NodeEdge,
    RelationLabel,
    Topic,
)
from epimemer.cli import _backfill_relations
from epimemer.mcp import tools


CRITIC = JudgeRef(agent_id="critic", digest="d1")
EDITOR = JudgeRef(agent_id="editor", digest="d2")


async def _topic(storage, content):
    """A stored node, with no embedding — `link` only checks that it exists."""
    node = Topic(content=content, source_id="seg1")
    await storage.store_node(node)
    return node


async def _pair(storage, a="Vienna", b="Austria"):
    return await _topic(storage, a), await _topic(storage, b)


class TestCoiningALabelRecordsIt:
    async def test_one_record_carrying_the_coining_judge(self, storage):
        a, b = await _pair(storage)

        await tools.link(a.id, b.id, storage, relation="capital_of", judge=CRITIC)

        [label] = await storage.query_relation_labels()
        assert label.name == "capital_of"
        assert label.kind == "relationship"
        assert label.judged_by == CRITIC
        assert label.description == "", "undescribed is a true state, not a gap"

    async def test_reusing_a_label_records_no_second_one_and_keeps_the_coiner(self, storage):
        # A second agent using an existing word is not claiming to have
        # introduced it. Same rule as a re-recorded edge.
        a, b = await _pair(storage)
        c, d = await _pair(storage, "Paris", "France")
        await tools.link(a.id, b.id, storage, relation="capital_of", judge=CRITIC)

        await tools.link(c.id, d.id, storage, relation="capital_of", judge=EDITOR)

        [label] = await storage.query_relation_labels()
        assert label.judged_by == CRITIC

    async def test_an_engine_edge_records_no_label(self, storage):
        a, b = await _pair(storage)

        await tools.link(a.id, b.id, storage, edge_type="about", judge=CRITIC)

        assert await storage.query_relation_labels() == []

    async def test_a_coin_with_no_judge_records_no_judge(self, storage):
        a, b = await _pair(storage)

        await tools.link(a.id, b.id, storage, relation="capital_of")

        [label] = await storage.query_relation_labels()
        assert label.judged_by is None


class TestIdentityIsNameAndKindTogether:
    async def test_the_same_name_under_a_different_kind_is_a_different_record(
        self, storage
    ):
        # The kind decides whether retrieval follows the edge, so two labels
        # spelled alike but behaving differently are two vocabulary entries.
        await storage.store_relation_label(
            RelationLabel(name="published_by", kind="relationship")
        )
        await storage.store_relation_label(
            RelationLabel(name="published_by", kind="attribution")
        )

        assert len(await storage.query_relation_labels()) == 2

    async def test_storing_the_same_pair_twice_updates_rather_than_duplicates(
        self, storage
    ):
        await storage.store_relation_label(RelationLabel(name="works_for"))

        await storage.store_relation_label(
            RelationLabel(name="works_for", description="employment, formal or not")
        )

        [label] = await storage.query_relation_labels()
        assert label.description == "employment, formal or not"

    async def test_an_update_keeps_the_id_the_record_already_had(self, storage):
        # The id is what a decision about this label will name, so a write that
        # reassigned it would leave journal rows pointing at nothing — the label record's own
        # defect, rebuilt one layer down.
        first = await storage.store_relation_label(RelationLabel(name="works_for"))

        second = await storage.store_relation_label(
            RelationLabel(name="works_for", description="employment")
        )

        assert second == first
        [label] = await storage.query_relation_labels()
        assert label.id == first

    async def test_an_update_does_not_restamp_the_coiner(self, storage):
        # The coiner, never the describer: describing a label is not a claim to
        # have introduced it, and the rule is structural rather than a
        # convention every caller has to remember.
        await storage.store_relation_label(
            RelationLabel(name="works_for", judged_by=CRITIC)
        )

        await storage.store_relation_label(
            RelationLabel(name="works_for", description="employment", judged_by=EDITOR)
        )

        [label] = await storage.query_relation_labels()
        assert label.judged_by == CRITIC

    async def test_a_blank_description_never_overwrites_prose(self, storage):
        await storage.store_relation_label(
            RelationLabel(name="works_for", description="employment, formal or not")
        )

        await storage.store_relation_label(RelationLabel(name="works_for"))

        [label] = await storage.query_relation_labels()
        assert label.description == "employment, formal or not"

    async def test_a_missing_record_reads_as_none_and_raises_nothing(self, storage):
        # The ordinary answer on any graph written before this existed. Every
        # caller degrades to today's behaviour rather than refusing.
        assert await storage.get_relation_label("never_coined", "relationship") is None
        assert await storage.query_relation_labels() == []


class TestRecordsArePerGraph:
    async def test_a_label_in_one_graph_is_invisible_from_another(self, storage):
        # The same words mean different things in different graphs, which is the
        # whole content of the servant/consultant example behind the label record.
        here = storage.current_database
        await storage.store_relation_label(RelationLabel(name="works_for"))

        await storage.switch_database("elsewhere-74")
        try:
            assert await storage.get_relation_label("works_for", "relationship") is None
            assert await storage.query_relation_labels() == []
        finally:
            await storage.switch_database(here)

        assert await storage.get_relation_label("works_for", "relationship") is not None


class TestTheBackfill:
    """A convenience for a long-lived graph, never a precondition — it refuses
    embedded backends, which is the default development configuration, and an
    agent cannot run it at all."""

    async def _edge(self, storage, a, b, label, kind="relationship"):
        await storage.store_edge(NodeEdge(
            src_id=a.id, dst_id=b.id, type=EdgeType.RELATED, label=label, kind=kind
        ))

    async def test_it_records_one_label_per_distinct_pair(self, storage):
        a, b = await _pair(storage)
        c, d = await _pair(storage, "Paris", "France")
        await self._edge(storage, a, b, "capital_of")
        await self._edge(storage, c, d, "capital_of")
        await self._edge(storage, a, c, "published_by", kind="attribution")

        message = await _backfill_relations(storage)

        recorded = {(l.name, l.kind) for l in await storage.query_relation_labels()}
        assert recorded == {
            ("capital_of", "relationship"),
            ("published_by", "attribution"),
        }
        assert "2 label(s) in use, 2 newly recorded" in message

    async def test_it_records_nothing_for_engine_edges(self, storage):
        a, b = await _pair(storage)
        await storage.store_edge(
            NodeEdge(src_id=a.id, dst_id=b.id, type=EdgeType.ABOUT)
        )

        await _backfill_relations(storage)

        assert await storage.query_relation_labels() == []

    async def test_it_writes_no_judge(self, storage):
        # Backfilling is the record catching up with edges that already exist,
        # not a claim that anybody introduced the word.
        a, b = await _pair(storage)
        await self._edge(storage, a, b, "capital_of")

        await _backfill_relations(storage)

        [label] = await storage.query_relation_labels()
        assert label.judged_by is None

    async def test_a_later_coin_does_not_adopt_a_judge_either(self, storage):
        a, b = await _pair(storage)
        c, d = await _pair(storage, "Paris", "France")
        await self._edge(storage, a, b, "capital_of")
        await _backfill_relations(storage)

        await tools.link(c.id, d.id, storage, relation="capital_of", judge=CRITIC)

        [label] = await storage.query_relation_labels()
        assert label.judged_by is None, "the record existed; nobody coined it here"

    async def test_it_is_idempotent(self, storage):
        a, b = await _pair(storage)
        await self._edge(storage, a, b, "capital_of")
        await _backfill_relations(storage)

        message = await _backfill_relations(storage)

        assert len(await storage.query_relation_labels()) == 1
        assert "0 newly recorded, 1 already had a record" in message

    async def test_a_rerun_does_not_wipe_a_description(self, storage):
        a, b = await _pair(storage)
        await self._edge(storage, a, b, "capital_of")
        await storage.store_relation_label(
            RelationLabel(name="capital_of", description="the seat of government")
        )

        await _backfill_relations(storage)

        [label] = await storage.query_relation_labels()
        assert label.description == "the seat of government"

    async def test_an_empty_vocabulary_says_so(self, storage):
        assert "nothing to record" in await _backfill_relations(storage)


class TestTheCliIsNeverTheOnlyWayIn:
    """The backfill refuses embedded backends and an agent cannot run it, so a
    design whose only remedy was the CLI would have no remedy at all."""

    async def test_coining_records_a_label_with_no_cli_involved(self, storage):
        a, b = await _pair(storage)

        await tools.link(a.id, b.id, storage, relation="works_for", judge=CRITIC)

        assert await storage.get_relation_label("works_for", "relationship") is not None

    async def test_describing_records_one_for_a_graph_that_predates_this(
        self, storage
    ):
        """The second of the three write paths, arriving with stage 2. A graph
        written before any of this has edges and no records, and this is the
        in-memory store — exactly the backend where the CLI refuses."""
        a, b = await _pair(storage)
        await storage.store_edge(
            NodeEdge(
                src_id=a.id, dst_id=b.id, type=EdgeType.RELATED,
                label="works_for", kind="relationship",
            )
        )
        assert await storage.get_relation_label("works_for", "relationship") is None

        await tools.describe_relation(
            "works_for", storage, description="Employment, not retainer."
        )

        record = await storage.get_relation_label("works_for", "relationship")
        assert record is not None and record.description == "Employment, not retainer."

    async def test_a_verdict_records_both_labels_it_judges(self, storage):
        """The fourth path, arriving with stage 3, and the one that completes
        the claim. An earlier draft **refused** here and pointed at the CLI,
        which would have left FC1 unfixable on exactly this backend — the
        default development configuration. Both records are created judge-less:
        judging two words against each other is not a claim to have introduced
        either."""
        from epimemer.embeddings.mock import MockEmbeddingProvider

        a, b = await _pair(storage)
        c, d = await _pair(storage, "c", "d")
        for src, dst, label in ((a, b, "works_for"), (c, d, "employed_by")):
            await storage.store_edge(
                NodeEdge(
                    src_id=src.id, dst_id=dst.id, type=EdgeType.RELATED,
                    label=label, kind="relationship",
                )
            )
        assert await storage.get_relation_label("works_for", "relationship") is None

        await tools.apply_reflection(
            storage,
            MockEmbeddingProvider(model_id="mock-embed", dimension=8),
            relation_verdicts=[{
                "pair": ["works_for", "employed_by"],
                "kind": "relationship",
                "verdict": "distinct",
                "because": "A servant, not an employee.",
            }],
            judge=CRITIC,
        )

        for label in ("works_for", "employed_by"):
            record = await storage.get_relation_label(label, "relationship")
            assert record is not None and record.judged_by is None
