"""Corroboration derived at read time — how many *independent* sources say this.

`ISSUES.md` #51. The `ValueSignal` docs promised two things and #46 gave the
first one a home: *"how well-supported by evidence"* became the caller-supplied
`confidence` prior. This is the second, *"multiple independent sources increase
confidence"* — and it is a fact about the **graph** rather than about the
material, so it changes whenever the graph changes.

**Never stored, always derived.** A stored count is the trap that removed
`novelty`: an answer frozen at the moment it was taken, against a baseline
nothing records. So these tests exercise a read-time walk and nothing here
asserts on a persisted field.

**In-degree is the wrong proxy**, which is what the first test pins. Ten
inferences drawn from one document raise `knowledge_in_degree_for` tenfold while
adding no support at all, and archival already consumes that number as
structural importance. Distinct *documents* — better, distinct *publishers* — is
the thing the claim is about.

**Computed over a similarity neighbourhood, not over node identity.** Facts are
never deduplicated (#52), so the same claim is many nodes. Including
``{node} ∪ {SIMILARITY neighbours}`` is not a workaround for that: a wrong
similarity edge overstates a number whose workings are returned and can be
checked, where a wrong merge destroys a node and cannot be undone.

The known inaccuracy ships with it, per the entry: "the city is called
Leningrad" and "the city is called Saint Petersburg" are similar, so under this
scheme they corroborate each other. #53 is what removes that, and until then the
defect is a wrong number rather than a fabricated node.
"""

from epimemer.core.types import (
    EdgeType,
    Fact,
    Inference,
    NodeEdge,
    NodeStatus,
    NodeType,
    RawDocument,
    Topic,
    ValueSignal,
)
from epimemer.pipelines.query.corroboration import corroboration_for


async def _publisher_entity(storage, name: str) -> Topic:
    """Resolve-or-create by exact name, as `_upsert_entity_topic` does.

    Load-bearing rather than incidental. Minting a fresh `Topic` per call would
    make two "BBC" documents two entities, and the "same publisher scores 1"
    test would then be asserting against a graph real ingest cannot produce —
    green or red for reasons that have nothing to do with the walk.
    """
    existing = await storage.get_node_by_content(name, node_type=NodeType.TOPIC)
    if isinstance(existing, Topic):
        return existing
    entity = Topic(content=name, source_id=None, extraction_method="agent:source")
    await storage.store_node(entity)
    return entity


async def _document(storage, source: str, publisher: str | None = None) -> RawDocument:
    """A document, optionally attributed to a publisher entity.

    The attribution edge is written exactly as `segment` writes it — `RELATED`
    with label `published_by` and kind `attribution` — because the walk reads
    the same edge, and a test that invented its own shape would pass against an
    implementation that never sees a real one.
    """
    doc = RawDocument(content=f"the text of {source}", source=source)
    await storage.store_document(doc)
    if publisher is not None:
        entity = await _publisher_entity(storage, publisher)
        await storage.store_edge(NodeEdge(
            src_id=doc.id, dst_id=entity.id, type=EdgeType.RELATED,
            label="published_by", kind="attribution",
        ))
    return doc


async def _fact(storage, content: str, doc: RawDocument | None = None) -> Fact:
    fact = Fact(content=content, source_id="seg-1", value=ValueSignal())
    await storage.store_node(fact)
    if doc is not None:
        await storage.store_edge(NodeEdge(
            src_id=fact.id, dst_id=doc.id, type=EdgeType.SOURCED_FROM
        ))
    return fact


async def _inference(storage, content: str, doc: RawDocument | None = None) -> Inference:
    node = Inference(content=content, source_id="seg-1", value=ValueSignal())
    await storage.store_node(node)
    if doc is not None:
        await storage.store_edge(NodeEdge(
            src_id=node.id, dst_id=doc.id, type=EdgeType.SOURCED_FROM
        ))
    return node


async def _join(storage, src, dst, edge_type: EdgeType) -> None:
    await storage.store_edge(
        NodeEdge(src_id=src.id, dst_id=dst.id, type=edge_type)
    )


async def _count(storage, node) -> int:
    """The count for one node, which is what a caller actually reads."""
    result = await corroboration_for([node.id], storage)
    return result[node.id].count


class TestCountingDistinctSources:
    """The four cases #51 specified before any of this existed.

    Three supporters drawn from one document score 1, not 3; two documents with
    distinct publishers score 2; two documents sharing a publisher score 1; and
    a neighbour the subject *contradicts* does not count. The written-up result
    is `docs/RETRIEVAL.md` §8.
    """

    async def test_three_supporters_from_one_document_score_one(
        self, storage
    ):
        """The motivating case, and the whole argument against in-degree.

        Three nodes point at the claim, so its in-degree is three and its
        corroboration is one: one document said this, three times over.
        """
        doc = await _document(storage, "one-report")
        claim = await _fact(storage, "the deploy failed", doc)
        for i in range(3):
            supporter = await _inference(storage, f"a reading {i}", doc)
            await _join(storage, supporter, claim, EdgeType.DERIVED_FROM)

        assert await _count(storage, claim) == 1

    async def test_two_documents_with_distinct_publishers_score_two(
        self, storage
    ):
        """Independence is the content of the claim, so this is the real 2."""
        bbc = await _document(storage, "bbc-report", publisher="BBC")
        reuters = await _document(storage, "reuters-report", publisher="Reuters")
        claim = await _fact(storage, "the deploy failed", bbc)
        elsewhere = await _fact(storage, "the deploy failed", reuters)
        await _join(storage, claim, elsewhere, EdgeType.SIMILARITY)

        assert await _count(storage, claim) == 2

    async def test_two_documents_sharing_a_publisher_score_one(self, storage):
        """Two BBC articles are one source. Counting documents would say two."""
        first = await _document(storage, "bbc-morning", publisher="BBC")
        second = await _document(storage, "bbc-evening", publisher="BBC")
        claim = await _fact(storage, "the deploy failed", first)
        restated = await _fact(storage, "the deploy failed", second)
        await _join(storage, claim, restated, EdgeType.SIMILARITY)

        assert await _count(storage, claim) == 1

    async def test_a_contradicted_neighbour_does_not_corroborate(self, storage):
        """Review 2026-08-12, amendment 1 — the sharpest failure of the lot.

        Contradicting pairs are *near-maximally similar* ("the deploy failed" /
        "the deploy succeeded"), and a `SIMILARITY` edge written before the
        contradiction verdict stays in the graph. Without this exclusion a
        document that **contradicts** the claim counts as support for it.
        """
        ours = await _document(storage, "our-report", publisher="BBC")
        theirs = await _document(storage, "their-report", publisher="Reuters")
        claim = await _fact(storage, "the deploy failed", ours)
        denial = await _fact(storage, "the deploy succeeded", theirs)
        await _join(storage, claim, denial, EdgeType.SIMILARITY)
        await _join(storage, claim, denial, EdgeType.CONTRADICTION)

        assert await _count(storage, claim) == 1

    async def test_the_contradiction_excludes_in_either_direction(self, storage):
        """Which way `record_contradiction` happened to write it is not a fact
        about the epistemics. Reading one direction only is how the exclusion
        would work in tests and half the time in production."""
        ours = await _document(storage, "our-report", publisher="BBC")
        theirs = await _document(storage, "their-report", publisher="Reuters")
        claim = await _fact(storage, "the deploy failed", ours)
        denial = await _fact(storage, "the deploy succeeded", theirs)
        await _join(storage, denial, claim, EdgeType.SIMILARITY)
        await _join(storage, denial, claim, EdgeType.CONTRADICTION)

        assert await _count(storage, claim) == 1

    async def test_a_cross_frame_variant_does_not_corroborate(self, storage):
        """Amendment 1's other half.

        A `variant_of` partner is *that frame's* resolution of the proposition,
        not evidence for this one. Counting it would let a fiction frame
        corroborate base reality, which is the one thing CLAUDE.md forbids
        outright.
        """
        ours = await _document(storage, "our-report", publisher="BBC")
        other_frame = await _document(storage, "the-novel", publisher="Gollancz")
        claim = await _fact(storage, "the city is Saint Petersburg", ours)
        variant = await _fact(storage, "the city is Novigrad", other_frame)
        await _join(storage, claim, variant, EdgeType.SIMILARITY)
        await _join(storage, claim, variant, EdgeType.VARIANT_OF)

        assert await _count(storage, claim) == 1


class TestWhatCountsAsASource:
    async def test_the_nodes_own_source_counts_as_one(self, storage):
        """Decided in the entry: a fact from one document is corroborated
        *once*, not zero times. 0 would make the common case look like an
        error, and every caller would add 1 back."""
        doc = await _document(storage, "only-report", publisher="BBC")
        claim = await _fact(storage, "the deploy failed", doc)

        assert await _count(storage, claim) == 1

    async def test_an_unattributed_document_is_its_own_source(self, storage):
        """The honest default for the common case — most documents carry no
        `published_by` today."""
        first = await _document(storage, "a-note")
        second = await _document(storage, "another-note")
        claim = await _fact(storage, "the deploy failed", first)
        restated = await _fact(storage, "the deploy failed", second)
        await _join(storage, claim, restated, EdgeType.SIMILARITY)

        assert await _count(storage, claim) == 2

    async def test_a_node_with_no_source_at_all_is_absent(self, storage):
        """Absent rather than mapped to 0, matching `validity_for` and
        `review_labels_for`: a caller filters by membership, and a response
        carries the field only where it says something."""
        orphan = await _fact(storage, "no provenance", None)

        assert await corroboration_for([orphan.id], storage) == {}

    async def test_a_supporter_from_a_second_document_corroborates(self, storage):
        """The positive case for the support walk.

        Without it, every exclusion test above would pass against an
        implementation whose neighbourhood was empty — the failure mode where a
        guard looks green because it guards nothing.
        """
        ours = await _document(storage, "our-report", publisher="BBC")
        theirs = await _document(storage, "their-report", publisher="Reuters")
        claim = await _fact(storage, "the deploy failed", ours)
        corroborating = await _inference(storage, "the rollout was aborted", theirs)
        await _join(storage, corroborating, claim, EdgeType.DERIVED_FROM)

        assert await _count(storage, claim) == 2

    async def test_a_supporter_reached_by_supports_counts_too(self, storage):
        """Both edges record the same relation, as the soundness check already
        reads them (`test_soundness.py`)."""
        ours = await _document(storage, "our-report", publisher="BBC")
        theirs = await _document(storage, "their-report", publisher="Reuters")
        claim = await _fact(storage, "the deploy failed", ours)
        supporter = await _fact(storage, "the rollout was aborted", theirs)
        await _join(storage, supporter, claim, EdgeType.SUPPORTS)

        assert await _count(storage, claim) == 2


class TestTheAnswerIsAuditable:
    """The property that makes the neighbourhood approach defensible at all.

    An inflated figure has to be *visible and checkable*, because the design
    knowingly ships a similarity neighbourhood that will sometimes be wrong. A
    bare integer would hide its own mistake exactly as a merged node does.
    """

    async def test_the_contributing_nodes_come_with_the_count(self, storage):
        bbc = await _document(storage, "bbc-report", publisher="BBC")
        reuters = await _document(storage, "reuters-report", publisher="Reuters")
        claim = await _fact(storage, "the deploy failed", bbc)
        elsewhere = await _fact(storage, "the deploy failed", reuters)
        await _join(storage, claim, elsewhere, EdgeType.SIMILARITY)

        [result] = (await corroboration_for([claim.id], storage)).values()

        assert result.count == len(result.sources) == 2
        contributed = {
            node_id for source in result.sources for node_id in source.node_ids
        }
        assert contributed == {claim.id, elsewhere.id}

    async def test_a_source_names_its_publisher_and_its_documents(self, storage):
        """Amendment 3: publisher identity is name-brittle — `published_by`
        entities are deduplicated by exact content match, so "BBC" and "BBC
        News" are two publishers. The response has to show its working for that
        to be checkable rather than merely true."""
        morning = await _document(storage, "bbc-morning", publisher="BBC")
        evening = await _document(storage, "bbc-evening", publisher="BBC")
        claim = await _fact(storage, "the deploy failed", morning)
        restated = await _fact(storage, "the deploy failed", evening)
        await _join(storage, claim, restated, EdgeType.SIMILARITY)

        [result] = (await corroboration_for([claim.id], storage)).values()

        [source] = result.sources
        assert source.publisher == "BBC"
        assert set(source.document_ids) == {morning.id, evening.id}

    async def test_the_unattributed_fallback_is_reported_not_hidden(
        self, storage
    ):
        """Whether the caller bothered to attribute is an *ingest habit*, and
        it shows up here as a corroboration difference — the `relevance`
        confound in miniature. The entry's verdict is to state it in the
        response rather than let it be discovered."""
        attributed = await _document(storage, "bbc-report", publisher="BBC")
        anonymous = await _document(storage, "a-note")
        claim = await _fact(storage, "the deploy failed", attributed)
        restated = await _fact(storage, "the deploy failed", anonymous)
        await _join(storage, claim, restated, EdgeType.SIMILARITY)

        [result] = (await corroboration_for([claim.id], storage)).values()

        assert result.count == 2
        assert result.unattributed_documents == 1
        assert {source.publisher for source in result.sources} == {"BBC", None}


class TestBatching:
    """One round-trip for the whole result set, like every other read-time
    annotation. Asking per node is what made `gather_pending_review` the largest
    single source of round-trips in `reflect` (#14)."""

    async def test_many_nodes_in_one_call(self, storage):
        bbc = await _document(storage, "bbc-report", publisher="BBC")
        reuters = await _document(storage, "reuters-report", publisher="Reuters")
        one = await _fact(storage, "the deploy failed", bbc)
        two = await _fact(storage, "the deploy failed", reuters)
        three = await _fact(storage, "unrelated", reuters)
        await _join(storage, one, two, EdgeType.SIMILARITY)

        result = await corroboration_for([one.id, two.id, three.id], storage)

        assert result[one.id].count == 2
        assert result[two.id].count == 2
        assert result[three.id].count == 1

    async def test_an_empty_request_touches_nothing(self, storage):
        assert await corroboration_for([], storage) == {}


class TestStatusDecisionsTakenHere:
    """**Not inherited from the entry — decided while writing this file.**

    #51 says nothing about node status, which means it would otherwise be
    settled by whichever query the implementation happened to reach for. Both
    rules below follow from #53's split rather than from anything new, but they
    are flagged because they are a decision and deserve to be argued with.
    """

    async def test_a_corrected_neighbour_does_not_corroborate(self, storage):
        """`corrected` means *we were wrong* — the claim should never have been
        believed and the node is kept for the audit trail rather than for its
        content. A claim known to be false is not evidence for anything."""
        ours = await _document(storage, "our-report", publisher="BBC")
        theirs = await _document(storage, "their-report", publisher="Reuters")
        claim = await _fact(storage, "the deploy failed", ours)
        withdrawn = await _fact(storage, "the deploy failed", theirs)
        withdrawn.status = NodeStatus.CORRECTED
        await storage.store_node(withdrawn)
        await _join(storage, claim, withdrawn, EdgeType.SIMILARITY)

        assert await _count(storage, claim) == 1

    async def test_a_historical_neighbour_still_corroborates(self, storage):
        """`historical` means *the world moved on* — the claim was right and is
        **still right of its period**. Dropping it here would be the same
        forgetting #53 exists to prevent, one layer along."""
        ours = await _document(storage, "our-report", publisher="BBC")
        theirs = await _document(storage, "their-report", publisher="Reuters")
        claim = await _fact(storage, "the city is Leningrad", ours)
        retired = await _fact(storage, "the city is Leningrad", theirs)
        retired.status = NodeStatus.HISTORICAL
        await storage.store_node(retired)
        await _join(storage, claim, retired, EdgeType.SIMILARITY)

        assert await _count(storage, claim) == 2


class TestTheNonInteractionWithConfidence:
    """Review 2026-08-12, amendment 2, stated as a test because prose in a
    docstring is not a guarantee.

    Three hedged 0.3 reports from three publishers score 3, the same as three
    0.9s. That is defensible — independence is the thing being counted, not
    strength — but callers will read the count as support, so the two signals
    must be shown not to interact.
    """

    async def test_hedged_sources_count_the_same_as_confident_ones(
        self, storage
    ):
        async def claim_from(publisher: str, confidence: float) -> Fact:
            doc = await _document(storage, f"{publisher}-report", publisher=publisher)
            fact = Fact(
                content="the deploy failed",
                source_id="seg-1",
                value=ValueSignal(confidence=confidence),
            )
            await storage.store_node(fact)
            await storage.store_edge(NodeEdge(
                src_id=fact.id, dst_id=doc.id, type=EdgeType.SOURCED_FROM
            ))
            return fact

        hedged = await claim_from("Alpha", 0.3)
        also_hedged = await claim_from("Beta", 0.3)
        third = await claim_from("Gamma", 0.3)
        for other in (also_hedged, third):
            await _join(storage, hedged, other, EdgeType.SIMILARITY)

        assert await _count(storage, hedged) == 3
