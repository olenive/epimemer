"""A relation label gets a meaning (#74, stage 2).

Stage 1 gave a label identity and nothing read it. This is the half that pays,
and the reason is that it moves the intervention from **repair to prevention**:
an agent picking from a described vocabulary never coins the fourth synonym, so
no merge is needed to clean up after it. Three surfaces carry the prose —
`list_relations` for discovery, `link` at the moment of reuse, and
`describe_relation` to write it.

What is pinned here is mostly what the description is *not*. It is advisory
prose, so nothing enforces it and nothing steers a coinage. It belongs to the
shared label rather than to an edge. And it is a judgment about the graph's
**words**, not about the graph's **claims**, which is why it journals its own
kind: a reviewer auditing what the graph asserts must not have to read past
vocabulary notes to do it.

Both backends via the `storage` fixture — a description the two stores disagree
about is the divergence `tests/conftest.py` exists for.
"""

from epimemer.core.types import (
    DecisionKind,
    EdgeType,
    JudgeRef,
    NodeEdge,
    RelationLabel,
    Topic,
)
from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.mcp import tools


_MOCK_EMBEDDER = MockEmbeddingProvider(model_id="mock-embed", dimension=8)

CRITIC = JudgeRef(agent_id="critic", digest="d1")
EDITOR = JudgeRef(agent_id="editor", digest="d2")


async def _topic(storage, content):
    """A stored node, with no embedding — `link` only checks that it exists."""
    node = Topic(content=content, source_id="seg1")
    await storage.store_node(node)
    return node


async def _pair(storage, a="Vienna", b="Austria"):
    return await _topic(storage, a), await _topic(storage, b)


async def _coin(storage, label, *, kind="relationship", judge=CRITIC):
    """Coin `label` by using it, which is the only act that records a coiner."""
    a, b = await _pair(storage, f"{label}-src", f"{label}-dst")
    await tools.link(a.id, b.id, storage, relation=label, kind=kind, judge=judge)
    return a, b


async def _relations(storage):
    result, _ = await tools.list_relations(storage)
    return {(r["label"], r["kind"]): r for r in result["relations"]}


class TestListRelationsCarriesTheDescription:
    async def test_a_described_label_comes_back_with_its_prose(self, storage):
        await _coin(storage, "advised")
        await tools.describe_relation(
            "advised", storage, description="Retained counsel, not employment."
        )

        rows = await _relations(storage)

        assert rows[("advised", "relationship")]["description"] == (
            "Retained counsel, not employment."
        )

    async def test_an_undescribed_label_comes_back_with_an_empty_one(self, storage):
        """Undescribed is a true state and reads as one. It is not an error and
        it is not absence of the field — the next agent has to be able to tell
        *nobody has said* from *this key is missing*."""
        await _coin(storage, "advised")

        rows = await _relations(storage)

        assert rows[("advised", "relationship")]["description"] == ""

    async def test_a_graph_with_no_records_at_all_still_answers(self, storage):
        """Every graph that predates stage 1 is this graph. The join is
        left-outer for exactly that reason: a missing record is the ordinary
        answer, and the read degrades to what it did before rather than
        refusing."""
        a, b = await _pair(storage)
        await storage.store_edge(
            NodeEdge(
                src_id=a.id, dst_id=b.id, type=EdgeType.RELATED,
                label="capital_of", kind="relationship",
            )
        )
        assert await storage.query_relation_labels() == []

        rows = await _relations(storage)

        assert rows[("capital_of", "relationship")]["count"] == 1
        assert rows[("capital_of", "relationship")]["description"] == ""

    async def test_the_count_stays_derived_from_the_edges(self, storage):
        """Not stored on the record, because counts are scoped to active nodes
        (#14 step 2) and a stored one would drift the moment a node retired."""
        await _coin(storage, "advised")
        c, d = await _pair(storage, "Rome", "Italy")
        await tools.link(c.id, d.id, storage, relation="advised", judge=EDITOR)
        await tools.describe_relation("advised", storage, description="prose")

        rows = await _relations(storage)

        assert rows[("advised", "relationship")]["count"] == 2


class TestLinkTellsTheAgentWhatTheWordAlreadyMeans:
    async def test_reusing_a_described_label_returns_its_description(self, storage):
        """At the moment it matters, rather than only to an agent that thought
        to look first."""
        await _coin(storage, "advised")
        await tools.describe_relation(
            "advised", storage, description="Retained counsel, not employment."
        )
        c, d = await _pair(storage, "Rome", "Italy")

        result, _ = await tools.link(
            c.id, d.id, storage, relation="advised", judge=EDITOR
        )

        assert result["relation_description"] == (
            "Retained counsel, not employment."
        )

    async def test_coining_a_new_label_returns_no_description(self, storage):
        """There is nothing to report and no key claiming there is. Reporting
        the empty string would read as *this graph means nothing by it*."""
        a, b = await _pair(storage)

        result, _ = await tools.link(
            a.id, b.id, storage, relation="capital_of", judge=CRITIC
        )

        assert "relation_description" not in result

    async def test_reusing_an_undescribed_label_returns_no_description(self, storage):
        await _coin(storage, "advised")
        c, d = await _pair(storage, "Rome", "Italy")

        result, _ = await tools.link(
            c.id, d.id, storage, relation="advised", judge=EDITOR
        )

        assert "relation_description" not in result

    async def test_it_reports_rather_than_steers(self, storage):
        """The edge is created with the label the agent asked for. Nothing here
        redirects a coinage: a nudge that cannot carry the distinction it is
        overruling is FC3's loop with no exit, and §8 makes steering a non-goal."""
        await _coin(storage, "advised")
        await tools.describe_relation("advised", storage, description="counsel")
        c, d = await _pair(storage, "Rome", "Italy")

        result, _ = await tools.link(
            c.id, d.id, storage, relation="employed_by", judge=EDITOR
        )

        [edge] = await storage.get_edges_from(c.id)
        assert edge.id == result["edge_id"]
        assert edge.label == "employed_by"


class TestDescribingCreatesTheRecordItNeeds:
    async def test_a_label_with_no_record_gets_one(self, storage):
        """A graph that predates stage 1 must not need the CLI to become
        describable — the CLI refuses embedded backends and an agent cannot run
        it anyway. Every write path that names a label creates-or-fetches."""
        a, b = await _pair(storage)
        await storage.store_edge(
            NodeEdge(
                src_id=a.id, dst_id=b.id, type=EdgeType.RELATED,
                label="capital_of", kind="relationship",
            )
        )

        result, _ = await tools.describe_relation(
            "capital_of", storage, description="Seat of government.", judge=EDITOR
        )

        assert result["described"] is True
        assert result["created"] is True
        [label] = await storage.query_relation_labels()
        assert label.description == "Seat of government."

    async def test_the_record_it_creates_carries_no_judge(self, storage):
        """Describing a word is not claiming to have introduced it. Only `link`
        coins, and the description is journalled in its own right."""
        a, b = await _pair(storage)
        await storage.store_edge(
            NodeEdge(
                src_id=a.id, dst_id=b.id, type=EdgeType.RELATED,
                label="capital_of", kind="relationship",
            )
        )

        await tools.describe_relation(
            "capital_of", storage, description="Seat of government.", judge=EDITOR
        )

        [label] = await storage.query_relation_labels()
        assert label.judged_by is None

    async def test_describing_a_coined_label_keeps_its_coiner(self, storage):
        await _coin(storage, "advised", judge=CRITIC)

        await tools.describe_relation(
            "advised", storage, description="counsel", judge=EDITOR
        )

        [label] = await storage.query_relation_labels()
        assert label.judged_by == CRITIC


class TestWhatDescribingRefuses:
    async def test_a_label_no_edge_carries(self, storage):
        """A record naming a word this graph has never used would show up in
        `list_relations` as a meaning with no usage."""
        result, _ = await tools.describe_relation(
            "never_used", storage, description="prose", judge=EDITOR
        )

        assert result["described"] is False
        assert "never_used" in result["refused"]
        assert await storage.query_relation_labels() == []

    async def test_a_refusal_writes_no_journal_row(self, storage):
        await tools.describe_relation("never_used", storage, description="prose")

        assert await storage.query_decisions(
            kinds=[DecisionKind.RELATION_DESCRIPTION]
        ) == []

    async def test_a_kind_the_edges_do_not_carry(self, storage):
        """The kind decides whether retrieval follows the edge, so it is in
        force on the edges and the record only mirrors it. Changing it here
        would leave the record disagreeing with everything it describes."""
        await _coin(storage, "cited_by", kind="attribution")

        result, _ = await tools.describe_relation(
            "cited_by", storage, description="prose", kind="relationship"
        )

        assert result["described"] is False
        assert result["kind"] == "attribution", "the refusal names the kind in force"

    async def test_a_refused_kind_change_leaves_the_record_alone(self, storage):
        await _coin(storage, "cited_by", kind="attribution")
        await tools.describe_relation(
            "cited_by", storage, description="first", kind="attribution"
        )

        await tools.describe_relation(
            "cited_by", storage, description="second", kind="relationship"
        )

        [label] = await storage.query_relation_labels()
        assert label.kind == "attribution"
        assert label.description == "first"


class TestRedescribing:
    async def test_the_text_is_replaced_and_the_record_is_one(self, storage):
        await _coin(storage, "advised")
        await tools.describe_relation("advised", storage, description="first")

        await tools.describe_relation("advised", storage, description="second")

        [label] = await storage.query_relation_labels()
        assert label.description == "second"

    async def test_it_journals_a_second_row_and_edits_no_first(self, storage):
        """The journal is append-only, which is what makes *who said this, and
        when* answerable at all."""
        await _coin(storage, "advised")
        await tools.describe_relation(
            "advised", storage, description="first", judge=CRITIC
        )
        await tools.describe_relation(
            "advised", storage, description="second", judge=EDITOR
        )

        rows = await storage.query_decisions(
            kinds=[DecisionKind.RELATION_DESCRIPTION]
        )

        assert len(rows) == 2
        assert {r.judged_by.agent_id for r in rows} == {"critic", "editor"}

    async def test_the_identity_survives_a_redescription(self, storage):
        """The whole point of the record: a journal row naming this label must
        keep resolving. A fresh `RelationLabel` minting a new id over the old is
        #74's own defect one layer down."""
        await _coin(storage, "advised")
        first, _ = await tools.describe_relation(
            "advised", storage, description="first"
        )

        second, _ = await tools.describe_relation(
            "advised", storage, description="second"
        )

        assert second["relation_label_id"] == first["relation_label_id"]
        assert second["created"] is False

    async def test_a_blank_description_leaves_prose_alone_and_says_so(self, storage):
        """`describe_relation` is not how a description is cleared, and the
        response has to report what was **stored** rather than what was asked
        for — otherwise it tells the agent it wiped a description it did not."""
        await _coin(storage, "advised")
        await tools.describe_relation("advised", storage, description="counsel")

        result, _ = await tools.describe_relation("advised", storage, description="")

        assert result["description"] == "counsel"
        [label] = await storage.query_relation_labels()
        assert label.description == "counsel"

    async def test_descriptions_are_per_graph(self, storage):
        """The same words mean different things in different graphs, which is
        the entire content of the servant/consultant example."""
        await _coin(storage, "advised")
        await tools.describe_relation("advised", storage, description="counsel")
        here = storage.current_database

        await storage.switch_database("elsewhere-74-stage2")
        try:
            assert await storage.query_relation_labels() == []
        finally:
            await storage.switch_database(here)


class TestConsolidatingLabelsKeepsTheVocabularyHonest:
    """`apply_reflection(relation_merges=…)` is the **fourth** write path that
    names a label, and §2.3 enumerated three — it was written as if stage 4 had
    already replaced merging, which #74 has not decided it will.

    Before this, consolidating into a label nobody had coined left the edges
    pointing at a word with no record: the description being consolidated
    *toward* absent, while the one consolidated *away* sat in the store
    unreachable through any agent surface.
    """

    async def test_the_survivor_gets_a_record_it_did_not_have(self, storage):
        await _coin(storage, "cites")

        await tools.apply_reflection(
            storage, _MOCK_EMBEDDER,
            relation_merges=[{"labels": ["cites"], "into": "references"}],
        )

        record = await storage.get_relation_label("references", "relationship")
        assert record is not None

    async def test_the_survivors_record_carries_no_judge(self, storage):
        """Merging is not coining. The agent consolidating two words is not
        claiming to have introduced the one that survived."""
        await _coin(storage, "cites", judge=CRITIC)

        await tools.apply_reflection(
            storage, _MOCK_EMBEDDER,
            relation_merges=[{"labels": ["cites"], "into": "references"}],
        )

        record = await storage.get_relation_label("references", "relationship")
        assert record.judged_by is None

    async def test_an_existing_survivor_keeps_its_record_untouched(self, storage):
        await _coin(storage, "cites")
        await _coin(storage, "references", judge=EDITOR)
        await tools.describe_relation(
            "references", storage, description="What the survivor means."
        )
        before = await storage.get_relation_label("references", "relationship")

        await tools.apply_reflection(
            storage, _MOCK_EMBEDDER,
            relation_merges=[{"labels": ["cites"], "into": "references"}],
        )

        after = await storage.get_relation_label("references", "relationship")
        assert after.id == before.id
        assert after.description == "What the survivor means."
        assert after.judged_by == EDITOR

    async def test_the_losers_description_comes_back_rather_than_vanishing(
        self, storage
    ):
        """Handed to the agent, not folded in. Concatenating two definitions is
        the system making an editorial judgment it is not entitled to, and this
        design's shape is that agents judge and the graph records."""
        await _coin(storage, "cites")
        await tools.describe_relation(
            "cites", storage, description="A footnote, not an endorsement."
        )

        result, _ = await tools.apply_reflection(
            storage, _MOCK_EMBEDDER,
            relation_merges=[{"labels": ["cites"], "into": "references"}],
        )

        assert result["relation_descriptions_orphaned"] == [{
            "label": "cites",
            "kind": "relationship",
            "description": "A footnote, not an endorsement.",
            "merged_into": "references",
        }]

    async def test_nothing_is_reported_for_an_undescribed_loser(self, storage):
        """There is no prose to strand, so there is nothing to settle."""
        await _coin(storage, "cites")

        result, _ = await tools.apply_reflection(
            storage, _MOCK_EMBEDDER,
            relation_merges=[{"labels": ["cites"], "into": "references"}],
        )

        assert result["relation_descriptions_orphaned"] == []

    async def test_the_survivor_is_describable_immediately_afterwards(self, storage):
        """The point of handing the prose back: the agent can act on it in the
        same breath. Before the record existed this call would have created one
        anyway, but the merge left `list_relations` and the store disagreeing
        about whether the word had a record at all."""
        await _coin(storage, "cites")
        await tools.describe_relation("cites", storage, description="A footnote.")
        result, _ = await tools.apply_reflection(
            storage, _MOCK_EMBEDDER,
            relation_merges=[{"labels": ["cites"], "into": "references"}],
        )
        [stranded] = result["relation_descriptions_orphaned"]

        settled, _ = await tools.describe_relation(
            stranded["merged_into"], storage, description=stranded["description"]
        )

        assert settled["described"] is True
        rows = await _relations(storage)
        assert rows[("references", "relationship")]["description"] == "A footnote."

    async def test_merging_a_label_into_itself_records_nothing(self, storage):
        await _coin(storage, "cites")

        result, _ = await tools.apply_reflection(
            storage, _MOCK_EMBEDDER,
            relation_merges=[{"labels": ["cites"], "into": "cites"}],
        )

        assert result["relations_consolidated"] == 0
        assert result["relation_descriptions_orphaned"] == []


class TestVocabularyIsJudgedApartFromClaims:
    async def test_a_description_journals_its_own_kind(self, storage):
        await _coin(storage, "advised")

        result, _ = await tools.describe_relation(
            "advised", storage, description="counsel", judge=EDITOR
        )

        [row] = await storage.query_decisions(
            kinds=[DecisionKind.RELATION_DESCRIPTION]
        )
        assert row.subject_ids == [result["relation_label_id"]]
        assert row.judged_by == EDITOR

    async def test_it_does_not_arrive_under_enrichment(self, storage):
        """`ENRICHMENT` is reflect's enrichment of a **topic**. A reviewer
        auditing changes to what the graph claims must not get prose about what
        the graph's words mean mixed in — §4.3's argument, one section earlier.
        The first draft wrote `ENRICHMENT` because enriching is what it is:
        the right verb, the wrong side of the line."""
        topic = await _topic(storage, "Vienna")
        await tools.journal(
            storage, DecisionKind.ENRICHMENT, [topic.id], judge=CRITIC
        )
        await _coin(storage, "advised")
        await tools.describe_relation(
            "advised", storage, description="counsel", judge=EDITOR
        )

        enrichments = await storage.query_decisions(kinds=[DecisionKind.ENRICHMENT])

        assert [r.subject_ids for r in enrichments] == [[topic.id]]

    async def test_review_reads_both_back_and_keeps_them_apart(self, storage):
        topic = await _topic(storage, "Vienna")
        await tools.journal(
            storage, DecisionKind.ENRICHMENT, [topic.id], judge=CRITIC
        )
        await _coin(storage, "advised")
        await tools.describe_relation(
            "advised", storage, description="counsel", judge=EDITOR
        )

        result, _ = await tools.review(storage, mode="all")

        kinds = {row["kind"] for row in result["decisions"]}
        assert DecisionKind.RELATION_DESCRIPTION.value in kinds
        assert DecisionKind.ENRICHMENT.value in kinds


class TestTheVizReadIsNeverAnAgentSurface:
    async def test_it_lists_the_vocabulary_for_a_named_graph(self, storage):
        await _coin(storage, "advised")
        await tools.describe_relation("advised", storage, description="counsel")

        listed = await storage.viz_list_relation_labels(storage.current_database)

        assert [(rl.name, rl.description) for rl in listed] == [
            ("advised", "counsel")
        ]

    async def test_a_graph_that_does_not_exist_is_empty_rather_than_an_error(
        self, storage
    ):
        assert list(await storage.viz_list_relation_labels("no-such-graph")) == []

    async def test_it_is_not_registered_as_a_tool(self):
        """Viz reads name their own graph and leave the active connection where
        they found it, which is exactly what an agent must never be handed: a
        read that crosses graphs without the `expected_graph` gate seeing it."""
        from epimemer.mcp.server import mcp as epimemer_mcp

        names = {tool.name for tool in await epimemer_mcp.list_tools()}

        assert not any(name.startswith("viz_list") for name in names)

    def test_it_is_not_imported_anywhere_under_mcp(self):
        """A guard on the import rather than the registration, because the
        registration is the second mistake and this is the first."""
        import pathlib

        import epimemer.mcp

        source = "\n".join(
            path.read_text()
            for path in pathlib.Path(epimemer.mcp.__file__).parent.rglob("*.py")
        )

        assert "viz_list_relation_labels" not in source
