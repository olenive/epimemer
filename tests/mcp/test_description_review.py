"""A description is reviewed when the material under it moves, and not before.

Reflect used to decide whether to ask for a description by a length ratio, which
answers *is this topic thin* and answers it identically on every run: a good
one-line description over forty facts came back on every reflect, and the judge
declined it on every reflect, and nothing recorded the decline. The rule here
asks the other question, *has anything happened under this topic since somebody
last stood behind what it says about itself*, and the nomination carries the
delta rather than a sample.

The two answers are `enrichments`, a new sentence, and `descriptions_confirmed`,
*I read it against the change and it still fits*. Both clear the nomination;
neither is optional, because an unanswered one comes back for ever.
`dev-docs/DESCRIPTION_REVIEW.md` carries the case.
"""

import pytest

from epimemer.core.types import (
    BASE_METACONTEXT_ID,
    DecisionKind,
    JudgeRef,
    NodeStatus,
    NodeType,
    Topic,
)
from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.mcp import tools
from epimemer.mcp.config import ServerConfig
from epimemer.pipelines.metacontexts import TAG_EXTRACTION_METHOD, created_from_tag

CRITIC = JudgeRef(agent_id="critic", digest="d1")
EDITOR = JudgeRef(agent_id="editor", digest="d2")

ISSUE_53 = "Validity intervals: when a claim was true, per source."


@pytest.fixture
def embedder():
    return MockEmbeddingProvider(model_id="mock-embed", dimension=8)


@pytest.fixture
def config():
    return ServerConfig(storage_backend="memory", embedding_provider="mock")


async def _ingest(storage, embedder, config, text, *, tags=(), facts=("a claim",), describe=True):
    """One document, its facts carrying every tag named.

    Tags rather than a segment's own topics, because a tag's material hangs off
    `tagged_with_topic` and the tag node is reused across calls: ingesting a
    second document under the same tag is how something changes under a topic
    that has already been described.

    `describe=False` for a call whose tags already exist, which is the only way
    a tag reaches the graph undescribed now.
    """
    seg, _ = await tools.segment_text(text, storage, embedder, config)
    stored, _ = await tools.store_decomposition(
        document_id=seg["document_id"],
        segments=[{"segment_id": seg["segments"][0]["segment_id"], "facts": list(facts)}],
        storage=storage,
        embedding_provider=embedder,
        metacontext_id=BASE_METACONTEXT_ID,
        tags=list(tags),
        tag_descriptions=(
            {name: f"Everything filed under {name}." for name in tags} if describe else None
        ),
        judge=CRITIC,
    )
    return stored


async def _legacy_tag(storage, embedder, config, name="issue-53", *, facts) -> Topic:
    """A tag with no description and no review time, carrying material.

    What every tag on a graph written before the ingest rule looks like, and
    what the backfill has to reach: `store_decomposition` refuses to mint one
    now, so the node is put there directly and the material hung off it by an
    ordinary ingest, which finds the name already resolved and asks for no
    description.
    """
    tag = Topic(content=name, source_id=None, extraction_method=TAG_EXTRACTION_METHOD)
    await storage.store_node(tag)
    await _ingest(
        storage, embedder, config, "A document.", tags=[name], facts=facts, describe=False
    )
    return tag


async def _resend_description(storage, embedder, config, description, *, name="issue-53") -> dict:
    """A second document under an existing tag, carrying a description for it."""
    seg, _ = await tools.segment_text("Second.", storage, embedder, config)
    stored, _ = await tools.store_decomposition(
        document_id=seg["document_id"],
        segments=[{"segment_id": seg["segments"][0]["segment_id"], "facts": ["another"]}],
        storage=storage,
        embedding_provider=embedder,
        metacontext_id=BASE_METACONTEXT_ID,
        tags=[name],
        tag_descriptions={name: description},
        judge=CRITIC,
    )
    return stored


async def _named(storage, content: str, *, node_type=NodeType.TOPIC):
    found = await storage.get_node_by_content(content, node_type=node_type)
    assert found is not None
    return found


async def _enrich(storage, embedder, topic_id: str, description: str, *, judge=EDITOR):
    result, _ = await tools.apply_reflection(
        storage,
        embedder,
        enrichments=[{"topic_id": topic_id, "description": description}],
        judge=judge,
    )
    return result


async def _nominations(storage, embedder) -> dict[str, dict]:
    result, _ = await tools.reflect(storage, embedder)
    return {c["topic_id"]: c for c in result["enrichment_candidates"]}


async def _described_tag(storage, embedder, config, *, facts=("the older claim",)) -> Topic:
    """A tag somebody has described, with its material already in place."""
    await _ingest(storage, embedder, config, "First document.", tags=["issue-53"], facts=facts)
    tag = await _named(storage, "issue-53")
    await _enrich(storage, embedder, tag.id, ISSUE_53)
    return await storage.get_node(tag.id)


class TestATagIsReachableAtLast:
    """`gather_associated_material_for` followed `extracted_under_topic` and
    `abstracts`, and a tag holds neither. So the enrichment scan could not reach
    the one kind of topic a description is worth most to, and the only path to a
    described tag was an agent calling `apply_reflection` by hand.

    This is the backfill: the tags already on a real graph carry no description
    and no review time, and reflect now asks for one."""

    async def test_an_undescribed_tag_with_material_is_nominated(self, storage, embedder, config):
        tag = await _legacy_tag(
            storage, embedder, config, facts=["a claim about when this was true, at some length"]
        )

        assert tag.id in await _nominations(storage, embedder)

    async def test_the_nomination_carries_the_tagged_material(self, storage, embedder, config):
        tag = await _legacy_tag(
            storage, embedder, config, facts=["a claim about when this was true, at some length"]
        )

        nominated = (await _nominations(storage, embedder))[tag.id]

        assert nominated["sample"] == ["a claim about when this was true, at some length"]
        assert nominated["material_count"] == 1


class TestAnUnreviewedTopicKeepsTheRatio:
    """There is no reference time to measure change from, so the first-time
    question is the only one available: is this topic thin beside its material."""

    async def test_it_says_so_with_a_null_since_and_a_sample(self, storage, embedder, config):
        tag = await _legacy_tag(
            storage, embedder, config, facts=["a claim about when this was true, at some length"]
        )

        nominated = (await _nominations(storage, embedder))[tag.id]

        assert nominated["since"] is None
        assert "sample" in nominated
        assert "changed_material" not in nominated

    async def test_a_topic_whose_material_is_thin_is_left_alone(self, storage, embedder, config):
        """The ratio still applies where nobody has reviewed: one short claim
        under a long name is not a topic anybody needs to describe yet."""
        tag = await _legacy_tag(
            storage,
            embedder,
            config,
            "a rather long tag name indeed, longer than its material",
            facts=["short"],
        )

        assert tag.id not in await _nominations(storage, embedder)

    async def test_a_tag_minted_today_is_described_and_reviewed_from_birth(
        self, storage, embedder, config
    ):
        """Which is what leaves the ratio with only old topics to answer for:
        the creator writes the description, and writing it is reading it."""
        await _ingest(storage, embedder, config, "A document.", tags=["issue-61"])
        tag = await _named(storage, "issue-61")

        assert tag.description_reviewed_at == tag.created_at
        assert tag.id not in await _nominations(storage, embedder)


class TestOnceReviewedTheRatioIsNeverConsultedAgain:
    async def test_a_described_topic_with_nothing_new_is_not_nominated(
        self, storage, embedder, config
    ):
        tag = await _described_tag(storage, embedder, config)

        assert tag.id not in await _nominations(storage, embedder)

    async def test_material_that_dwarfs_the_description_is_still_not_nominated(
        self, storage, embedder, config
    ):
        """The ratio would nominate this on every run, for ever. Nothing has
        happened since somebody read it, so the answer is no."""
        tag = await _described_tag(
            storage, embedder, config, facts=["a claim about when this was true " * 20]
        )

        assert tag.id not in await _nominations(storage, embedder)

    async def test_the_review_time_is_stamped_by_the_enrichment(self, storage, embedder, config):
        tag = await _described_tag(storage, embedder, config)

        assert tag.description_reviewed_at is not None


class TestAChangeUnderADescribedTopicNominatesIt:
    async def test_a_fact_added_since_nominates(self, storage, embedder, config):
        tag = await _described_tag(storage, embedder, config)

        await _ingest(
            storage,
            embedder,
            config,
            "Second document.",
            tags=["issue-53"],
            facts=["the newer claim"],
        )

        assert tag.id in await _nominations(storage, embedder)

    async def test_the_nomination_carries_the_new_fact_and_not_the_older_one(
        self, storage, embedder, config
    ):
        """The delta is the sample. What came before `since` was covered when
        the description was written, which is what makes a random sample of the
        whole the wrong answer after the first time."""
        tag = await _described_tag(storage, embedder, config)
        await _ingest(
            storage,
            embedder,
            config,
            "Second document.",
            tags=["issue-53"],
            facts=["the newer claim"],
        )

        nominated = (await _nominations(storage, embedder))[tag.id]

        assert [m["content"] for m in nominated["changed_material"]] == ["the newer claim"]
        assert [m["change"] for m in nominated["changed_material"]] == ["created"]
        assert nominated["changed_count"] == 1
        assert nominated["material_count"] == 2

    async def test_the_nomination_says_what_it_measured_from(self, storage, embedder, config):
        tag = await _described_tag(storage, embedder, config)
        await _ingest(
            storage, embedder, config, "Second.", tags=["issue-53"], facts=["the newer claim"]
        )

        nominated = (await _nominations(storage, embedder))[tag.id]

        assert nominated["since"] == tag.description_reviewed_at.isoformat()
        assert nominated["current_description"] == ISSUE_53
        assert nominated["current_content"] == "issue-53"

    async def test_a_fact_archived_since_nominates(self, storage, embedder, config):
        """A description standing over material that has since been retired is
        as stale as one written before half of it arrived."""
        tag = await _described_tag(storage, embedder, config)
        fact = await _named(storage, "the older claim", node_type=NodeType.FACT)

        await tools.apply_reflection(storage, embedder, archivals=[fact.id], judge=EDITOR)

        nominated = (await _nominations(storage, embedder))[tag.id]
        assert [(m["content"], m["change"]) for m in nominated["changed_material"]] == [
            ("the older claim", "archived")
        ]

    async def test_a_fact_superseded_since_nominates(self, storage, embedder, config):
        tag = await _described_tag(
            storage, embedder, config, facts=["the older claim", "the replacement"]
        )
        old = await _named(storage, "the older claim", node_type=NodeType.FACT)
        new = await _named(storage, "the replacement", node_type=NodeType.FACT)

        await tools.supersede_by(old.id, new.id, storage, because="it_was_wrong", judge=EDITOR)

        nominated = (await _nominations(storage, embedder))[tag.id]
        assert [(m["content"], m["change"]) for m in nominated["changed_material"]] == [
            ("the older claim", "superseded")
        ]

    async def test_the_cap_holds_and_the_count_reports_the_rest(self, storage, embedder, config):
        """Twenty fits a response holding several nominations; nothing is
        hidden by it, because `changed_count` says what it left out."""
        tag = await _described_tag(storage, embedder, config)

        await _ingest(
            storage,
            embedder,
            config,
            "Second document.",
            tags=["issue-53"],
            facts=[f"claim number {n}" for n in range(25)],
        )

        nominated = (await _nominations(storage, embedder))[tag.id]
        assert len(nominated["changed_material"]) == 20
        assert nominated["changed_count"] == 25
        assert nominated["material_count"] == 26


class TestAnsweringANomination:
    async def test_a_confirmation_stamps_the_review_time(self, storage, embedder, config):
        tag = await _described_tag(storage, embedder, config)
        await _ingest(
            storage, embedder, config, "Second.", tags=["issue-53"], facts=["the newer claim"]
        )

        await tools.apply_reflection(
            storage, embedder, descriptions_confirmed=[tag.id], judge=EDITOR
        )

        after = await storage.get_node(tag.id)
        assert after.description_reviewed_at > tag.description_reviewed_at

    async def test_a_confirmation_writes_no_text_and_no_history(self, storage, embedder, config):
        """Nothing was replaced, so there is no replaced wording to keep, and
        the trail the earlier writes left is untouched."""
        tag = await _described_tag(storage, embedder, config)

        await tools.apply_reflection(
            storage, embedder, descriptions_confirmed=[tag.id], judge=EDITOR
        )

        after = await storage.get_node(tag.id)
        assert after.description == ISSUE_53
        assert after.metadata == tag.metadata

    async def test_a_confirmation_journals_its_own_kind(self, storage, embedder, config):
        """Not `ENRICHMENT` with unchanged text: the journal should show that
        somebody looked and chose not to change it, and a reviewer selecting
        `ENRICHMENT` should get rows where a description moved."""
        tag = await _described_tag(storage, embedder, config)

        await tools.apply_reflection(
            storage, embedder, descriptions_confirmed=[tag.id], judge=EDITOR
        )

        rows = await storage.query_decisions(kinds=[DecisionKind.DESCRIPTION_REVIEW])
        assert [row.subject_ids for row in rows] == [[tag.id]]
        assert rows[0].judged_by.agent_id == "editor"

    async def test_several_confirmations_journal_one_row(self, storage, embedder, config):
        """One act of reading applied to whatever it covered, which is the
        granularity an archival sweep already uses."""
        first = await _described_tag(storage, embedder, config)
        await _ingest(storage, embedder, config, "Other.", tags=["issue-61"], facts=["another"])
        second = await _named(storage, "issue-61")
        await _enrich(storage, embedder, second.id, "A second described tag.")

        await tools.apply_reflection(
            storage, embedder, descriptions_confirmed=[first.id, second.id], judge=EDITOR
        )

        rows = await storage.query_decisions(kinds=[DecisionKind.DESCRIPTION_REVIEW])
        assert len(rows) == 1
        assert set(rows[0].subject_ids) == {first.id, second.id}

    async def test_a_confirmation_clears_the_nomination(self, storage, embedder, config):
        tag = await _described_tag(storage, embedder, config)
        await _ingest(
            storage, embedder, config, "Second.", tags=["issue-53"], facts=["the newer claim"]
        )
        assert tag.id in await _nominations(storage, embedder)

        await tools.apply_reflection(
            storage, embedder, descriptions_confirmed=[tag.id], judge=EDITOR
        )

        assert tag.id not in await _nominations(storage, embedder)

    async def test_an_enrichment_clears_it_too(self, storage, embedder, config):
        tag = await _described_tag(storage, embedder, config)
        await _ingest(
            storage, embedder, config, "Second.", tags=["issue-53"], facts=["the newer claim"]
        )

        await _enrich(storage, embedder, tag.id, "A sharper sentence.")

        assert tag.id not in await _nominations(storage, embedder)

    async def test_an_unanswered_nomination_comes_back(self, storage, embedder, config):
        """The rule an unjudged pair already follows, and the right one here:
        the description is unreviewed until somebody reviews it."""
        tag = await _described_tag(storage, embedder, config)
        await _ingest(
            storage, embedder, config, "Second.", tags=["issue-53"], facts=["the newer claim"]
        )
        assert tag.id in await _nominations(storage, embedder)

        assert tag.id in await _nominations(storage, embedder)

    async def test_the_count_comes_back_in_the_applied_summary(self, storage, embedder, config):
        tag = await _described_tag(storage, embedder, config)

        result, meta = await tools.apply_reflection(
            storage, embedder, descriptions_confirmed=[tag.id], judge=EDITOR
        )

        assert result["descriptions_confirmed"] == 1
        assert meta.nodes_returned == 1

    async def test_an_id_that_is_not_a_topic_is_skipped(self, storage, embedder, config):
        """Skipped rather than raised, as supersessions and archivals are, so a
        batch partially applies cleanly."""
        tag = await _described_tag(storage, embedder, config)
        fact = await _named(storage, "the older claim", node_type=NodeType.FACT)

        result, _ = await tools.apply_reflection(
            storage,
            embedder,
            descriptions_confirmed=[fact.id, "no-such-node", tag.id],
            judge=EDITOR,
        )

        assert result["descriptions_confirmed"] == 1

    async def test_an_entry_that_is_not_an_id_refuses_the_batch(self, storage, embedder):
        """The same shape check `archivals` gets: a list of ids whose entries
        are not ids cannot be applied at all."""
        with pytest.raises(ValueError) as caught:
            await tools.apply_reflection(
                storage, embedder, descriptions_confirmed=[{"topic_id": "x"}], judge=EDITOR
            )

        assert "descriptions_confirmed[0]" in str(caught.value)


class TestTheReviewTimeSurvivesTheGraph:
    async def test_a_confirmed_topic_stays_active_and_keeps_its_edges(
        self, storage, embedder, config
    ):
        """A confirmation records that somebody looked. It is not a graph
        change, so nothing about the node moves but the moment."""
        tag = await _described_tag(storage, embedder, config)
        before = await storage.get_edges_to(tag.id)

        await tools.apply_reflection(
            storage, embedder, descriptions_confirmed=[tag.id], judge=EDITOR
        )

        after = await storage.get_node(tag.id)
        assert after.status is NodeStatus.ACTIVE
        assert after.content == "issue-53"
        assert {e.id for e in await storage.get_edges_to(tag.id)} == {e.id for e in before}


class TestANewTagNeedsADescription:
    """Refused rather than warned, because the caller is the only party that
    knows what the tag means at the moment it is minted, and a warning is read
    by the next agent, not this one."""

    async def test_a_new_tag_without_an_entry_is_refused(self, storage, embedder, config):
        with pytest.raises(ValueError) as caught:
            await _ingest(
                storage, embedder, config, "A document.", tags=["issue-53"], describe=False
            )

        assert "issue-53" in str(caught.value)
        assert "tag_descriptions" in str(caught.value)

    async def test_the_refused_call_writes_nothing(self, storage, embedder, config):
        with pytest.raises(ValueError):
            await _ingest(
                storage, embedder, config, "A document.", tags=["issue-53"], describe=False
            )

        assert await storage.query_nodes(node_type=NodeType.TOPIC) == []
        assert await storage.query_nodes(node_type=NodeType.FACT) == []

    async def test_every_name_that_needs_one_is_listed_at_once(self, storage, embedder, config):
        """An agent fixing one name and resending into the next refusal is the
        treadmill in miniature."""
        with pytest.raises(ValueError) as caught:
            await _ingest(
                storage,
                embedder,
                config,
                "A document.",
                tags=["issue-53", "issue-61", "validity"],
                describe=False,
            )

        for name in ("issue-53", "issue-61", "validity"):
            assert name in str(caught.value)

    async def test_a_per_entry_tag_is_covered_by_the_call_level_dict(
        self, storage, embedder, config
    ):
        """One dict for both places a tag can appear, which is why the argument
        is not a change to the shape of `tags`."""
        seg, _ = await tools.segment_text("A document.", storage, embedder, config)

        await tools.store_decomposition(
            document_id=seg["document_id"],
            segments=[
                {
                    "segment_id": seg["segments"][0]["segment_id"],
                    "facts": [{"content": "a claim", "tags": ["validity"]}],
                }
            ],
            storage=storage,
            embedding_provider=embedder,
            metacontext_id=BASE_METACONTEXT_ID,
            tag_descriptions={"validity": "When a claim was true, per source."},
            judge=CRITIC,
        )

        assert (await _named(storage, "validity")).description == (
            "When a claim was true, per source."
        )

    async def test_a_per_entry_tag_without_an_entry_is_refused(self, storage, embedder, config):
        seg, _ = await tools.segment_text("A document.", storage, embedder, config)

        with pytest.raises(ValueError) as caught:
            await tools.store_decomposition(
                document_id=seg["document_id"],
                segments=[
                    {
                        "segment_id": seg["segments"][0]["segment_id"],
                        "facts": [{"content": "a claim", "tags": ["validity"]}],
                    }
                ],
                storage=storage,
                embedding_provider=embedder,
                metacontext_id=BASE_METACONTEXT_ID,
                judge=CRITIC,
            )

        assert "validity" in str(caught.value)

    async def test_a_call_that_creates_no_tags_needs_no_dict(self, storage, embedder, config):
        seg, _ = await tools.segment_text("A document.", storage, embedder, config)

        stored, _ = await tools.store_decomposition(
            document_id=seg["document_id"],
            segments=[{"segment_id": seg["segments"][0]["segment_id"], "facts": ["a claim"]}],
            storage=storage,
            embedding_provider=embedder,
            metacontext_id=BASE_METACONTEXT_ID,
            judge=CRITIC,
        )

        assert stored["nodes_created"]["facts"] == 1
        assert stored["tags_described"] == 0

    async def test_a_second_document_under_an_existing_tag_needs_no_dict(
        self, storage, embedder, config
    ):
        await _ingest(storage, embedder, config, "First.", tags=["issue-53"])

        stored = await _ingest(
            storage, embedder, config, "Second.", tags=["issue-53"], describe=False
        )

        assert stored["nodes_created"]["facts"] == 1

    async def test_a_blank_line_is_no_description(self, storage, embedder, config):
        """A key whose value says nothing would let a caller satisfy the
        requirement without meeting it."""
        seg, _ = await tools.segment_text("A document.", storage, embedder, config)

        with pytest.raises(ValueError) as caught:
            await tools.store_decomposition(
                document_id=seg["document_id"],
                segments=[{"segment_id": seg["segments"][0]["segment_id"], "facts": ["a claim"]}],
                storage=storage,
                embedding_provider=embedder,
                metacontext_id=BASE_METACONTEXT_ID,
                tags=["issue-53"],
                tag_descriptions={"issue-53": "   "},
                judge=CRITIC,
            )

        assert "issue-53" in str(caught.value)

    async def test_a_description_matches_up_to_spelling(self, storage, embedder, config):
        """Names are matched by `tag_key`, the same normalisation tags resolve
        by, so a dict keyed `claim_kind` answers for a tag written
        `claim-kind`."""
        seg, _ = await tools.segment_text("A document.", storage, embedder, config)

        await tools.store_decomposition(
            document_id=seg["document_id"],
            segments=[{"segment_id": seg["segments"][0]["segment_id"], "facts": ["a claim"]}],
            storage=storage,
            embedding_provider=embedder,
            metacontext_id=BASE_METACONTEXT_ID,
            tags=["claim-kind"],
            tag_descriptions={"claim_kind": "Condition or occurrence, judged at ingest."},
            judge=CRITIC,
        )

        assert (await _named(storage, "claim-kind")).description == (
            "Condition or occurrence, judged at ingest."
        )


class TestWhatANewTagIsWrittenWith:
    async def test_the_description_lands_on_the_node(self, storage, embedder, config):
        await _ingest(storage, embedder, config, "A document.", tags=["issue-53"])

        assert (await _named(storage, "issue-53")).description == "Everything filed under issue-53."

    async def test_it_is_reviewed_as_of_its_creation(self, storage, embedder, config):
        """The writer has just looked at it, so there is nothing for reflect to
        ask about until the material moves."""
        await _ingest(storage, embedder, config, "A document.", tags=["issue-53"])

        tag = await _named(storage, "issue-53")
        assert tag.description_reviewed_at == tag.created_at

    async def test_it_is_embedded_on_the_name_and_the_description(self, storage, embedder, config):
        """`embedding_text` joins the two only for a node `created_from_tag`
        recognises, so the extraction method has to be set before the embed."""
        await _ingest(storage, embedder, config, "A document.", tags=["issue-53"])
        tag = await _named(storage, "issue-53")

        expected = (await embedder.embed(["issue-53. Everything filed under issue-53."]))[0]
        stored = await storage.get_embeddings_for_item(tag.id, model_id=embedder.model_id)
        assert [record.vector for record in stored] == [expected]
        assert created_from_tag(tag)

    async def test_it_still_carries_the_judge_that_minted_it(self, storage, embedder, config):
        await _ingest(storage, embedder, config, "A document.", tags=["issue-53"])

        assert (await _named(storage, "issue-53")).judged_by == CRITIC


class TestADescriptionForATagTheGraphAlreadyHas:
    async def test_a_described_tag_keeps_its_own_wording(self, storage, embedder, config):
        """A live description is judged prose with a history trail, and
        replacing it is enrichment, where the wording it replaces is kept."""
        await _ingest(storage, embedder, config, "First.", tags=["issue-53"])

        await _resend_description(storage, embedder, config, "Something else entirely.")

        assert (await _named(storage, "issue-53")).description == "Everything filed under issue-53."

    async def test_the_call_says_so_in_warnings(self, storage, embedder, config):
        await _ingest(storage, embedder, config, "First.", tags=["issue-53"])
        tag = await _named(storage, "issue-53")

        stored = await _resend_description(storage, embedder, config, "Something else entirely.")

        assert [w["kind"] for w in stored["warnings"]] == ["description_not_written"]
        assert stored["warnings"][0]["subjects"] == [tag.id]

    async def test_nothing_is_journalled_against_it(self, storage, embedder, config):
        """The ingest was right, so there was nothing to proceed against, and a
        `proceeded_despite_advisory` row for every re-sent description would
        swamp exactly the review that kind exists for."""
        await _ingest(storage, embedder, config, "First.", tags=["issue-53"])

        stored = await _resend_description(storage, embedder, config, "Something else entirely.")

        assert stored["notify_user"] is False
        assert await storage.query_decisions(kinds=[DecisionKind.PROCEEDED_DESPITE_ADVISORY]) == []

    async def test_an_ordinary_ingest_raises_nothing(self, storage, embedder, config):
        stored = await _ingest(storage, embedder, config, "A document.", tags=["issue-53"])

        assert "warnings" not in stored
        assert stored["notify_user"] is False


class TestAnExistingTagWithNoDescriptionTakesTheSuppliedOne:
    """So a graph backfills as it is used, with no separate pass. This is the
    one place prose supplied at ingest reaches a name somebody else minted."""

    async def test_it_is_written(self, storage, embedder, config):
        await _legacy_tag(storage, embedder, config, facts=["a claim"])

        stored = await _ingest(storage, embedder, config, "Second.", tags=["issue-53"])

        assert (await _named(storage, "issue-53")).description == "Everything filed under issue-53."
        assert stored["tags_described"] == 1

    async def test_it_is_stamped_as_reviewed(self, storage, embedder, config):
        await _legacy_tag(storage, embedder, config, facts=["a claim"])

        await _ingest(storage, embedder, config, "Second.", tags=["issue-53"])

        assert (await _named(storage, "issue-53")).description_reviewed_at is not None

    async def test_it_is_re_embedded_on_both_fields(self, storage, embedder, config):
        """The vector was written on the bare name, and a stale one left beside
        it would rank the tag on wording it no longer carries."""
        tag = await _legacy_tag(storage, embedder, config, facts=["a claim"])

        await _ingest(storage, embedder, config, "Second.", tags=["issue-53"])

        expected = (await embedder.embed(["issue-53. Everything filed under issue-53."]))[0]
        stored = await storage.get_embeddings_for_item(tag.id, model_id=embedder.model_id)
        assert [record.vector for record in stored] == [expected]

    async def test_it_records_no_replaced_wording(self, storage, embedder, config):
        """There was none: an empty description is undescribed, and a trail
        entry saying so would be a revision nobody made."""
        tag = await _legacy_tag(storage, embedder, config, facts=["a claim"])

        await _ingest(storage, embedder, config, "Second.", tags=["issue-53"])

        assert "description_history" not in (await storage.get_node(tag.id)).metadata

    async def test_it_raises_no_warning(self, storage, embedder, config):
        await _legacy_tag(storage, embedder, config, facts=["a claim"])

        stored = await _ingest(storage, embedder, config, "Second.", tags=["issue-53"])

        assert "warnings" not in stored

    async def test_it_leaves_the_ratio_path(self, storage, embedder, config):
        """Which is the point of the backfill: the tag was nominated on the
        ratio with nothing to measure change from, and it now has a description
        somebody stood behind over everything then under it."""
        tag = await _legacy_tag(
            storage, embedder, config, facts=["a claim about when this was true, at some length"]
        )
        assert (await _nominations(storage, embedder))[tag.id]["since"] is None

        await _ingest(storage, embedder, config, "Second.", tags=["issue-53"], facts=["later"])

        assert tag.id not in await _nominations(storage, embedder)

    async def test_a_later_document_nominates_it_on_the_change(self, storage, embedder, config):
        """And the question it comes back with is the other one."""
        tag = await _legacy_tag(
            storage, embedder, config, facts=["a claim about when this was true, at some length"]
        )
        await _ingest(storage, embedder, config, "Second.", tags=["issue-53"], facts=["later"])

        await _ingest(storage, embedder, config, "Third.", tags=["issue-53"], facts=["later still"])

        nominated = (await _nominations(storage, embedder))[tag.id]
        assert nominated["since"] is not None
        assert [m["content"] for m in nominated["changed_material"]] == ["later still"]


class _TwoSubjects(MockEmbeddingProvider):
    """An embedder that puts every text beginning `alpha` on one axis and
    everything else on another, so a topic holding two of each bisects cleanly
    and `should_split` nominates it whatever the hash says."""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [
            [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
            if text.startswith("alpha")
            else [0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
            for text in texts
        ]


TWO_SUBJECTS = ("alpha one", "alpha two", "beta one", "beta two")


async def _split_nominations(storage, embedder) -> set[str]:
    result, _ = await tools.reflect(storage, embedder)
    return {c["topic_id"] for c in result["split_candidates"]}


class TestDecliningASplit:
    """A split nomination had no answer but splitting, so a topic read and kept
    whole came back on every reflect: 97 of them on one real graph. Declining
    stamps the same review time a description confirmation does, and the scan
    skips a topic whose material has not moved since."""

    @pytest.fixture
    def embedder(self):
        return _TwoSubjects(model_id="mock-embed", dimension=8)

    async def test_a_topic_never_reviewed_is_nominated(self, storage, embedder, config):
        await _ingest(storage, embedder, config, "Doc.", tags=["mixed"], facts=TWO_SUBJECTS)
        tag = await _named(storage, "mixed")
        # The ingest described the tag and stamped it; undo the stamp to stand
        # for a topic nobody has stood behind.
        await storage.store_node(tag.model_copy(update={"description_reviewed_at": None}))

        assert tag.id in await _split_nominations(storage, embedder)

    async def test_a_declined_topic_is_not_nominated_again(self, storage, embedder, config):
        await _ingest(storage, embedder, config, "Doc.", tags=["mixed"], facts=TWO_SUBJECTS)
        tag = await _named(storage, "mixed")
        await storage.store_node(tag.model_copy(update={"description_reviewed_at": None}))
        assert tag.id in await _split_nominations(storage, embedder)

        result, _ = await tools.apply_reflection(
            storage, embedder, splits_declined=[tag.id], judge=EDITOR
        )

        assert result["splits_declined"] == 1
        assert tag.id not in await _split_nominations(storage, embedder)

    async def test_material_moving_after_the_decline_nominates_again(
        self, storage, embedder, config
    ):
        await _ingest(storage, embedder, config, "Doc.", tags=["mixed"], facts=TWO_SUBJECTS)
        tag = await _named(storage, "mixed")
        await tools.apply_reflection(storage, embedder, splits_declined=[tag.id], judge=EDITOR)
        assert tag.id not in await _split_nominations(storage, embedder)

        await _ingest(
            storage,
            embedder,
            config,
            "Later.",
            tags=["mixed"],
            facts=["beta three"],
            describe=False,
        )

        assert tag.id in await _split_nominations(storage, embedder)

    async def test_a_decline_journals_its_own_kind_and_changes_nothing_else(
        self, storage, embedder, config
    ):
        await _ingest(storage, embedder, config, "Doc.", tags=["mixed"], facts=TWO_SUBJECTS)
        tag = await _named(storage, "mixed")

        await tools.apply_reflection(storage, embedder, splits_declined=[tag.id], judge=EDITOR)

        after = await storage.get_node(tag.id)
        assert after.description_reviewed_at > tag.description_reviewed_at
        assert after.description == tag.description
        assert after.metadata == tag.metadata
        rows = await storage.query_decisions(kinds=[DecisionKind.SPLIT_DECLINED])
        assert [row.subject_ids for row in rows] == [[tag.id]]
        assert rows[0].judged_by.agent_id == "editor"
        assert await storage.query_decisions(kinds=[DecisionKind.DESCRIPTION_REVIEW]) == []

    async def test_a_description_confirmation_also_settles_the_split(
        self, storage, embedder, config
    ):
        """One moment for *last stood behind what this topic says and covers*:
        confirming the description is standing behind the same material."""
        await _ingest(storage, embedder, config, "Doc.", tags=["mixed"], facts=TWO_SUBJECTS)
        tag = await _named(storage, "mixed")
        await storage.store_node(tag.model_copy(update={"description_reviewed_at": None}))
        assert tag.id in await _split_nominations(storage, embedder)

        await tools.apply_reflection(
            storage, embedder, descriptions_confirmed=[tag.id], judge=EDITOR
        )

        assert tag.id not in await _split_nominations(storage, embedder)
