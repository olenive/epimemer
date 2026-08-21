"""Tests for Phase 5: Reflection.

Covers:
- Topic consolidation: finds similar pairs above threshold
- Topic consolidation: dissimilar topics not paired
- Topic consolidation: merge creates new topic, marks originals as merged
- Contradiction detection: finds similar fact pairs
- Contradiction detection: excludes already-linked pairs
- Archival: finds old superseded nodes
- Archival: exports nodes and edges correctly
- Archival: active nodes never included
"""

from datetime import datetime, timedelta, timezone

import pytest

from epimemer.core.types import (
    BASE_METACONTEXT_ID,
    EdgeType,
    EmbeddingRecord,
    Fact,
    Inference,
    Metacontext,
    NodeEdge,
    NodeStatus,
    Topic,
    ValueSignal,
)
from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.pipelines.reflection.archival import (
    archive_nodes,
    find_archival_candidates,
    never_retrieved,
    nominate_archival_candidates,
)
from epimemer.pipelines.reflection.contradiction_detection import detect_contradictions
from epimemer.pipelines.reflection.topic_consolidation import (
    find_similar_topic_pairs,
    merge_similar_topics,
)
from epimemer.mcp.tools import judge_importance
from epimemer.storage.memory import InMemoryStorage


# --- Fixtures ---


@pytest.fixture
def embedding_provider() -> MockEmbeddingProvider:
    return MockEmbeddingProvider(model_id="mock-embed", dimension=8)


@pytest.fixture
async def storage_with_similar_topics(embedding_provider: MockEmbeddingProvider):
    """Storage with topic pairs that have identical embeddings (similarity=1.0).

    topic_a and topic_b share the exact same content (and thus same embedding).
    topic_c is completely different.
    """
    storage = InMemoryStorage()

    topic_a = Topic(
        id="topic-a",
        content="Machine learning algorithms",
        source_id="seg-1",
        value=ValueSignal(confidence=0.8),
    )
    topic_b = Topic(
        id="topic-b",
        content="Machine learning algorithms",  # identical content = identical embedding
        source_id="seg-2",
        value=ValueSignal(confidence=0.6),
    )
    topic_c = Topic(
        id="topic-c",
        content="Ocean currents and marine biology deep sea exploration",
        source_id="seg-3",
        value=ValueSignal(confidence=0.9),
    )

    for topic in [topic_a, topic_b, topic_c]:
        await storage.store_node(topic)
        vectors = await embedding_provider.embed([topic.content])
        emb = EmbeddingRecord(
            item_id=topic.id,
            model_id=embedding_provider.model_id,
            vector=vectors[0],
        )
        await storage.store_embedding(emb)

    return storage


@pytest.fixture
async def storage_with_similar_facts(embedding_provider: MockEmbeddingProvider):
    """Storage with fact pairs — some similar, some with existing edges.

    fact_a and fact_b have identical content (will be similar).
    fact_c is different.
    fact_d and fact_e have identical content but are already linked.
    """
    storage = InMemoryStorage()

    fact_a = Fact(
        id="fact-a",
        content="The Earth orbits the Sun",
        source_id="seg-1",
    )
    fact_b = Fact(
        id="fact-b",
        content="The Earth orbits the Sun",  # identical = high similarity
        source_id="seg-2",
    )
    fact_c = Fact(
        id="fact-c",
        content="Quantum entanglement enables secure communication protocols",
        source_id="seg-3",
    )
    fact_d = Fact(
        id="fact-d",
        content="Water boils at 100 degrees Celsius",
        source_id="seg-4",
    )
    fact_e = Fact(
        id="fact-e",
        content="Water boils at 100 degrees Celsius",  # identical but already linked
        source_id="seg-5",
    )

    for fact in [fact_a, fact_b, fact_c, fact_d, fact_e]:
        await storage.store_node(fact)
        vectors = await embedding_provider.embed([fact.content])
        emb = EmbeddingRecord(
            item_id=fact.id,
            model_id=embedding_provider.model_id,
            vector=vectors[0],
        )
        await storage.store_embedding(emb)

    # Link fact_d and fact_e with a SIMILARITY edge
    edge = NodeEdge(
        id="edge-sim-1",
        src_id="fact-d",
        dst_id="fact-e",
        type=EdgeType.SIMILARITY,
    )
    await storage.store_edge(edge)

    return storage


@pytest.fixture
async def storage_with_archival_candidates(embedding_provider: MockEmbeddingProvider):
    """Storage with nodes of various statuses and ages.

    - old_superseded: SUPERSEDED, 120 days old (should be archival candidate)
    - recent_superseded: SUPERSEDED, 30 days old (too recent)
    - old_merged: MERGED, 100 days old (should be archival candidate)
    - active_topic: ACTIVE (never archived)
    """
    storage = InMemoryStorage()
    now = datetime.now(timezone.utc)

    old_superseded = Fact(
        id="fact-old-superseded",
        content="Old superseded fact",
        source_id="seg-1",
        status=NodeStatus.SUPERSEDED,
        superseded_at=now - timedelta(days=120),
        created_at=now - timedelta(days=200),
    )
    recent_superseded = Fact(
        id="fact-recent-superseded",
        content="Recently superseded fact",
        source_id="seg-2",
        status=NodeStatus.SUPERSEDED,
        superseded_at=now - timedelta(days=30),
        created_at=now - timedelta(days=60),
    )
    old_merged = Topic(
        id="topic-old-merged",
        content="Old merged topic",
        source_id="seg-3",
        status=NodeStatus.MERGED,
        superseded_at=now - timedelta(days=100),
        created_at=now - timedelta(days=150),
    )
    active_topic = Topic(
        id="topic-active",
        content="Active topic that should not be archived",
        source_id="seg-4",
    )

    # The replacement nodes
    replacement_fact = Fact(
        id="fact-replacement",
        content="Replacement fact",
        source_id="seg-1",
    )
    merged_topic = Topic(
        id="topic-merged-result",
        content="Merged topic result",
        source_id="seg-3",
    )

    for node in [old_superseded, recent_superseded, old_merged, active_topic,
                 replacement_fact, merged_topic]:
        await storage.store_node(node)

    # History edges
    edge1 = NodeEdge(
        id="edge-superseded-1",
        src_id="fact-old-superseded",
        dst_id="fact-replacement",
        type=EdgeType.SUPERSEDED_BY,
    )
    edge2 = NodeEdge(
        id="edge-merged-1",
        src_id="topic-old-merged",
        dst_id="topic-merged-result",
        type=EdgeType.MERGED_INTO,
    )
    await storage.store_edge(edge1)
    await storage.store_edge(edge2)

    return storage


# --- Topic consolidation tests ---


async def test_find_similar_topic_pairs_above_threshold(
    storage_with_similar_topics, embedding_provider
):
    """Topics with identical content are found as similar pairs."""
    storage = storage_with_similar_topics

    pairs = await find_similar_topic_pairs(
        storage, embedding_provider, similarity_threshold=0.85
    )

    assert len(pairs) >= 1
    # topic-a and topic-b have identical embeddings (similarity = 1.0)
    ids_in_pairs = {(p[0].id, p[1].id) for p in pairs}
    assert ("topic-a", "topic-b") in ids_in_pairs or ("topic-b", "topic-a") in ids_in_pairs

    # The score for the identical pair should be 1.0
    for a, b, score in pairs:
        if {a.id, b.id} == {"topic-a", "topic-b"}:
            assert score == pytest.approx(1.0)


async def test_dissimilar_topics_not_paired(
    storage_with_similar_topics, embedding_provider
):
    """Topics with very different content are not paired at the default threshold."""
    storage = storage_with_similar_topics

    pairs = await find_similar_topic_pairs(
        storage, embedding_provider, similarity_threshold=0.99
    )

    # At threshold 0.99, only truly identical embeddings should pair
    for a, b, score in pairs:
        pair_ids = {a.id, b.id}
        # topic-c should not be paired with topic-a or topic-b
        assert "topic-c" not in pair_ids


async def test_merge_carries_the_higher_importance(embedding_provider):
    """A judgment on either source survives the merge.

    Merge builds a fresh node, so anything the merged signal forgets to name is
    silently reset to its default — and losing an importance judgment because
    two topics were collapsed is exactly the erosion the field exists to stop.
    """
    storage = InMemoryStorage()
    for node_id, importance in (("topic-i", 0.9), ("topic-j", 0.4)):
        await storage.store_node(Topic(
            id=node_id,
            content="Machine learning algorithms",
            source_id="seg-1",
            value=ValueSignal(importance=importance),
        ))

    merged = await merge_similar_topics(
        await storage.get_node("topic-i"),
        await storage.get_node("topic-j"),
        storage,
        embedding_provider,
    )
    assert merged.value.importance == pytest.approx(0.9)


class TestMergeConfidencePairing:
    """Why `max` confidence is right here, which nobody had written down (#46).

    For a caller-supplied prior `max` looks wrong on its own — the more
    credulous assessment wins and the disagreement vanishes. It survives
    because of what it pairs with: the higher-confidence description becomes
    the *primary* content, so the merged node's confidence describes the
    content it actually leads with. Break either half and the pair lies.
    """

    async def _pair(self, storage, confidence_i, confidence_j):
        for node_id, content, confidence in (
            ("topic-i", "gradient descent converges", confidence_i),
            ("topic-j", "gradient descent diverges", confidence_j),
        ):
            await storage.store_node(Topic(
                id=node_id, content=content, source_id="seg-1",
                value=ValueSignal(confidence=confidence),
            ))
        return await storage.get_node("topic-i"), await storage.get_node("topic-j")

    async def test_the_winning_confidence_belongs_to_the_primary_content(
        self, embedding_provider
    ):
        storage = InMemoryStorage()
        weak, strong = await self._pair(storage, 0.3, 0.9)

        merged = await merge_similar_topics(weak, strong, storage, embedding_provider)

        assert merged.value.confidence == pytest.approx(0.9)
        assert merged.content.startswith(strong.content)

    async def test_an_unrated_pair_still_resolves_to_the_first_argument(
        self, embedding_provider
    ):
        """The honest remainder of #46's fix, not a regression.

        Two nodes nobody rated tie at the default and the `>=` takes the first
        — which is what the comparison did for *every* pair before priors
        existed. What changed is that it is now the unrated case rather than
        the only case.
        """
        storage = InMemoryStorage()
        first, second = await self._pair(storage, None, None)

        merged = await merge_similar_topics(first, second, storage, embedding_provider)

        assert merged.value.confidence is None
        assert merged.content.startswith(first.content)


class TestMergeCarriesTheValueClocks:
    """A merge must carry *when* each signal was set, not only its value (#45).

    Merge builds a fresh node, and a field-by-field rebuild silently resets
    every field it forgets to name. Forgetting the two clocks is worse than
    losing a timestamp: the merged node keeps `importance = max(sources)` while
    claiming it was never judged, and `judgment_is_stale` reads the *pair*. An
    unjudged node is never stale, so a high-importance merged node fell through
    every nomination class permanently — the one safety net that stops
    importance protecting a node forever was unreachable for anything a merge
    produced.
    """

    async def _pair(self, storage, judged_days_ago=None, retrieved_days_ago=None):
        """One touched topic and one untouched one, ready to merge."""
        def ago(days):
            return None if days is None else datetime.now(timezone.utc) - timedelta(days=days)

        await storage.store_node(Topic(
            id="topic-touched",
            content="Machine learning algorithms",
            source_id="seg-1",
            value=ValueSignal(
                importance=0.9,
                importance_judged_at=ago(judged_days_ago),
                retrieved_at=ago(retrieved_days_ago),
            ),
        ))
        await storage.store_node(Topic(
            id="topic-untouched",
            content="Machine learning algorithms",
            source_id="seg-1",
            value=ValueSignal(),
        ))
        return (
            await storage.get_node("topic-touched"),
            await storage.get_node("topic-untouched"),
        )

    async def test_merge_carries_the_judgment_clock_across(self, embedding_provider):
        """The judgment and its date travel together or the pair lies."""
        storage = InMemoryStorage()
        touched, untouched = await self._pair(storage, judged_days_ago=200)

        merged = await merge_similar_topics(
            touched, untouched, storage, embedding_provider
        )

        assert merged.value.importance == pytest.approx(0.9)
        assert merged.value.importance_judged_at == touched.value.importance_judged_at

    async def test_merge_carries_the_retrieval_clock_across(self, embedding_provider):
        """Knowledge that has been retrieved does not become unretrieved."""
        storage = InMemoryStorage()
        touched, untouched = await self._pair(storage, retrieved_days_ago=3)

        merged = await merge_similar_topics(
            touched, untouched, storage, embedding_provider
        )

        assert merged.value.retrieved_at == touched.value.retrieved_at
        assert not never_retrieved(merged)

    async def test_a_merged_node_with_a_stale_judgment_is_still_nominated(
        self, embedding_provider
    ):
        """The consequence, end to end: the safety net has to reach merges."""
        storage = InMemoryStorage()
        touched, untouched = await self._pair(storage, judged_days_ago=400)
        merged = await merge_similar_topics(
            touched, untouched, storage, embedding_provider
        )

        candidates = await nominate_archival_candidates(
            storage, judgment_max_age_days=180
        )

        assert [(c.node_id, c.reason) for c in candidates] == [
            (merged.id, "stale_judgment")
        ]

    async def test_a_merge_of_untouched_topics_leaves_both_clocks_unset(
        self, embedding_provider
    ):
        """`None` means never, and a merge invents no history that did not happen."""
        storage = InMemoryStorage()
        touched, untouched = await self._pair(storage)

        merged = await merge_similar_topics(
            touched, untouched, storage, embedding_provider
        )

        assert merged.value.importance_judged_at is None
        assert merged.value.retrieved_at is None


async def test_merge_creates_new_topic_marks_originals(
    storage_with_similar_topics, embedding_provider
):
    """Merging two topics creates a new one and marks originals as merged."""
    storage = storage_with_similar_topics

    topic_a = await storage.get_node("topic-a")
    topic_b = await storage.get_node("topic-b")

    merged = await merge_similar_topics(topic_a, topic_b, storage, embedding_provider)

    # Merged topic is stored
    stored_merged = await storage.get_node(merged.id)
    assert stored_merged is not None
    assert stored_merged.content == merged.content
    assert merged.extraction_method == "merge"

    # Content combines both
    assert "Machine learning algorithms" in merged.content

    # Value signals combined correctly
    assert merged.value.confidence == pytest.approx(0.8)  # max

    # Originals marked as merged
    original_a = await storage.get_node("topic-a")
    original_b = await storage.get_node("topic-b")
    assert original_a.status == NodeStatus.MERGED
    assert original_b.status == NodeStatus.MERGED

    # merged_into edges exist
    edges_from_a = await storage.get_edges_from("topic-a")
    merged_edges_a = [e for e in edges_from_a if e.type == EdgeType.MERGED_INTO]
    assert len(merged_edges_a) == 1
    assert merged_edges_a[0].dst_id == merged.id

    edges_from_b = await storage.get_edges_from("topic-b")
    merged_edges_b = [e for e in edges_from_b if e.type == EdgeType.MERGED_INTO]
    assert len(merged_edges_b) == 1
    assert merged_edges_b[0].dst_id == merged.id


# --- Contradiction detection tests ---


async def test_detect_contradictions_finds_similar_pairs(
    storage_with_similar_facts, embedding_provider
):
    """Facts with identical content are detected as potential contradiction candidates."""
    storage = storage_with_similar_facts

    pairs = await detect_contradictions(
        storage, embedding_provider, similarity_threshold=0.80
    )

    # fact_a and fact_b have identical embeddings and are not already linked
    pair_id_sets = [{a.id, b.id} for a, b, _s in pairs]
    assert {"fact-a", "fact-b"} in pair_id_sets


async def test_detect_contradictions_excludes_already_linked(
    storage_with_similar_facts, embedding_provider
):
    """Fact pairs already linked by SIMILARITY edges are excluded."""
    storage = storage_with_similar_facts

    pairs = await detect_contradictions(
        storage, embedding_provider, similarity_threshold=0.80
    )

    # fact_d and fact_e have identical embeddings but are already linked
    pair_id_sets = [{a.id, b.id} for a, b, _s in pairs]
    assert {"fact-d", "fact-e"} not in pair_id_sets


# --- Archival tests ---


async def test_archival_finds_old_superseded_nodes(storage_with_archival_candidates):
    """Old superseded and merged nodes are found as archival candidates."""
    storage = storage_with_archival_candidates

    candidates = await find_archival_candidates(storage, max_age_days=90)

    candidate_ids = {n.id for n in candidates}
    assert "fact-old-superseded" in candidate_ids  # 120 days old
    assert "topic-old-merged" in candidate_ids  # 100 days old


async def test_archival_excludes_recent_superseded(storage_with_archival_candidates):
    """Recently superseded nodes are not archival candidates."""
    storage = storage_with_archival_candidates

    candidates = await find_archival_candidates(storage, max_age_days=90)

    candidate_ids = {n.id for n in candidates}
    assert "fact-recent-superseded" not in candidate_ids  # only 30 days old


async def test_archival_active_nodes_never_included(storage_with_archival_candidates):
    """Active nodes are never included in archival candidates."""
    storage = storage_with_archival_candidates

    candidates = await find_archival_candidates(storage, max_age_days=0)

    candidate_ids = {n.id for n in candidates}
    assert "topic-active" not in candidate_ids


async def test_archive_nodes_exports_correctly(storage_with_archival_candidates):
    """archive_nodes exports nodes and edges to a serializable dict."""
    storage = storage_with_archival_candidates

    candidates = await find_archival_candidates(storage, max_age_days=90)
    result = await archive_nodes(candidates, storage)

    assert "nodes" in result
    assert "edges" in result
    assert len(result["nodes"]) == len(candidates)

    # Edges should include the history edges for archived nodes
    edge_ids = {e["id"] for e in result["edges"]}
    assert "edge-superseded-1" in edge_ids
    assert "edge-merged-1" in edge_ids

    # Node data should be serializable dicts
    for node_dict in result["nodes"]:
        assert "id" in node_dict
        assert "content" in node_dict
        assert "status" in node_dict



# --- Frame helpers (metacontext-relative judgment) ---


async def test_frames_of_untagged_is_base():
    """A node with no metacontext edges is implicitly in base reality."""
    from epimemer.pipelines.reflection.review import frames_of

    storage = InMemoryStorage()
    f = Fact(content="untagged", source_id="s1")
    await storage.store_node(f)
    assert await frames_of(f.id, storage) == {BASE_METACONTEXT_ID}


async def test_frames_of_returns_tagged_frames():
    """A tagged node reports the metacontexts it is tagged with."""
    from epimemer.pipelines.reflection.review import frames_of

    storage = InMemoryStorage()
    mc = Metacontext(content="Fiction")
    await storage.store_metacontext(mc)
    f = Fact(content="tagged", source_id="s1")
    await storage.store_node(f)
    await storage.store_edge(
        NodeEdge(src_id=f.id, dst_id=mc.id, type=EdgeType.HAS_METACONTEXT)
    )
    assert await frames_of(f.id, storage) == {mc.id}


async def test_same_frame_two_untagged_share_base():
    """Two untagged nodes are both in base reality → same frame (genuine)."""
    from epimemer.pipelines.reflection.review import same_frame

    storage = InMemoryStorage()
    a = Fact(content="a", source_id="s1")
    b = Fact(content="b", source_id="s1")
    await storage.store_node(a)
    await storage.store_node(b)
    assert await same_frame(a.id, b.id, storage) is True


async def test_same_frame_disjoint_frames_not_same():
    """A base-reality node and a fiction-tagged node do not share a frame."""
    from epimemer.pipelines.reflection.review import same_frame

    storage = InMemoryStorage()
    mc = Metacontext(content="Fiction")
    await storage.store_metacontext(mc)
    real = Fact(content="real", source_id="s1")
    fic = Fact(content="fictional", source_id="s1")
    await storage.store_node(real)
    await storage.store_node(fic)
    await storage.store_edge(
        NodeEdge(src_id=fic.id, dst_id=mc.id, type=EdgeType.HAS_METACONTEXT)
    )
    assert await same_frame(real.id, fic.id, storage) is False


# --- Review labels (computed retrieval visibility, REVIEW_EPISTEMIC.md §4.1) ---


async def test_review_labels_superseded_candidate():
    """An incoming supersession_candidate edge labels the older node."""
    from epimemer.pipelines.reflection.review import review_labels

    storage = InMemoryStorage()
    old = Fact(content="old", source_id="s1")
    newer = Fact(content="new", source_id="s1")
    await storage.store_node(old)
    await storage.store_node(newer)
    await storage.store_edge(
        NodeEdge(src_id=newer.id, dst_id=old.id, type=EdgeType.SUPERSESSION_CANDIDATE)
    )

    labels = await review_labels(old, storage)
    assert labels["superseded_candidate"] == [newer.id]
    # The newer fact itself is not flagged.
    assert await review_labels(newer, storage) == {}


async def test_review_labels_evidence_stale_via_flag():
    """An explicit evidence_superseded flag marks the inference stale."""
    from epimemer.pipelines.reflection.review import review_labels

    storage = InMemoryStorage()
    fact = Fact(content="evidence", source_id="s1")
    inf = Inference(content="conclusion", source_id="s1")
    await storage.store_node(fact)
    await storage.store_node(inf)
    await storage.store_edge(
        NodeEdge(src_id=fact.id, dst_id=inf.id, type=EdgeType.EVIDENCE_SUPERSEDED)
    )

    labels = await review_labels(inf, storage)
    assert labels["evidence_stale"] == [fact.id]


async def test_review_labels_evidence_merged_is_not_evidence_stale():
    """A merged premise gets its own label (#61).

    The two events are not the same one: a correction says the claim under the
    inference was wrong, a merge says two phrasings of it collapsed into one.
    Sharing a label would report the first when the second happened — and would
    put the inference in front of archival, which nominates on `evidence_stale`.
    """
    from epimemer.pipelines.reflection.review import review_labels

    storage = InMemoryStorage()
    fact = Fact(content="evidence", source_id="s1", status=NodeStatus.MERGED)
    inf = Inference(content="conclusion", source_id="s1")
    await storage.store_node(fact)
    await storage.store_node(inf)
    await storage.store_edge(
        NodeEdge(src_id=fact.id, dst_id=inf.id, type=EdgeType.EVIDENCE_MERGED)
    )

    labels = await review_labels(inf, storage)
    assert labels["evidence_merged"] == [fact.id]
    assert "evidence_stale" not in labels


async def test_review_labels_evidence_stale_via_superseded_evidence():
    """An inference derived_from a now-superseded fact is stale even without a flag."""
    from epimemer.pipelines.reflection.review import review_labels

    storage = InMemoryStorage()
    fact = Fact(content="evidence", source_id="s1", status=NodeStatus.SUPERSEDED)
    inf = Inference(content="conclusion", source_id="s1")
    await storage.store_node(fact)
    await storage.store_node(inf)
    await storage.store_edge(
        NodeEdge(src_id=inf.id, dst_id=fact.id, type=EdgeType.DERIVED_FROM)
    )

    labels = await review_labels(inf, storage)
    assert labels["evidence_stale"] == [fact.id]


async def test_review_labels_contested_same_frame():
    """A contradiction to an active same-frame node marks both contested."""
    from epimemer.pipelines.reflection.review import review_labels

    storage = InMemoryStorage()
    a = Fact(content="X true", source_id="s1")
    b = Fact(content="X false", source_id="s1")
    await storage.store_node(a)
    await storage.store_node(b)
    await storage.store_edge(
        NodeEdge(src_id=a.id, dst_id=b.id, type=EdgeType.CONTRADICTION)
    )

    assert (await review_labels(a, storage))["contested"] == [b.id]
    # Symmetric: the partner is contested too (edge followed either direction).
    assert (await review_labels(b, storage))["contested"] == [a.id]


async def test_review_labels_contested_cleared_when_partner_retired():
    """Once the contradicting partner is superseded, the node is no longer contested."""
    from epimemer.pipelines.reflection.review import review_labels

    storage = InMemoryStorage()
    a = Fact(content="a", source_id="s1")
    b = Fact(content="b", source_id="s1", status=NodeStatus.SUPERSEDED)
    await storage.store_node(a)
    await storage.store_node(b)
    await storage.store_edge(
        NodeEdge(src_id=a.id, dst_id=b.id, type=EdgeType.CONTRADICTION)
    )

    assert "contested" not in await review_labels(a, storage)


async def test_review_labels_contested_excludes_cross_frame():
    """A cross-frame contradiction is coexistence, not a contest — no label."""
    from epimemer.pipelines.reflection.review import review_labels

    storage = InMemoryStorage()
    mc = Metacontext(content="Fiction")
    await storage.store_metacontext(mc)
    a = Fact(content="a", source_id="s1")
    b = Fact(content="b", source_id="s1")
    await storage.store_node(a)
    await storage.store_node(b)
    await storage.store_edge(
        NodeEdge(src_id=b.id, dst_id=mc.id, type=EdgeType.HAS_METACONTEXT)
    )
    await storage.store_edge(
        NodeEdge(src_id=a.id, dst_id=b.id, type=EdgeType.CONTRADICTION)
    )

    assert "contested" not in await review_labels(a, storage)


async def test_review_labels_clean_node_empty():
    """A node with no review-edges has no labels."""
    from epimemer.pipelines.reflection.review import review_labels

    storage = InMemoryStorage()
    t = Topic(content="fine", source_id="s1")
    await storage.store_node(t)
    assert await review_labels(t, storage) == {}


async def test_gather_pending_review_collects_flagged_only():
    """gather_pending_review returns active nodes with review labels, not clean ones."""
    from epimemer.pipelines.reflection.review import gather_pending_review

    storage = InMemoryStorage()
    old = Fact(content="old", source_id="s1")
    newer = Fact(content="new", source_id="s1")
    clean = Topic(content="unrelated", source_id="s1")
    for node in (old, newer, clean):
        await storage.store_node(node)
    await storage.store_edge(
        NodeEdge(src_id=newer.id, dst_id=old.id, type=EdgeType.SUPERSESSION_CANDIDATE)
    )

    flagged = {n.id: labels for n, labels in await gather_pending_review(storage)}
    assert old.id in flagged
    assert flagged[old.id]["superseded_candidate"] == [newer.id]
    # The newer fact and the unrelated topic are not flagged.
    assert newer.id not in flagged
    assert clean.id not in flagged


# --- Archival nomination (the hygiene arm of the review loop) ---


@pytest.fixture
async def storage_for_nomination():
    """One node of each nomination class, plus three that must be spared.

    Nominate:
      - fact-retired: SUPERSEDED long ago, low importance
      - inference-stale: evidence superseded out from under it
      - fact-trivial: active, never reinforced, low importance, unsupported
    Spare:
      - fact-reinforced: used since it was created
      - fact-supported: another node depends on it (structural importance)
      - fact-important: judged important
    """
    storage = InMemoryStorage()
    now = datetime.now(timezone.utc)
    born = now - timedelta(days=200)

    def untouched(importance: float) -> ValueSignal:
        # `retrieved_at` defaults to None, which *is* the "never used" signal —
        # no longer something the fixture has to construct out of timestamps.
        return ValueSignal(importance=importance)

    retired = Fact(
        id="fact-retired", content="retired and unimportant", source_id="seg-1",
        status=NodeStatus.SUPERSEDED, superseded_at=now - timedelta(days=120),
        created_at=born, value=untouched(0.2),
    )
    superseded_evidence = Fact(
        id="fact-evidence", content="evidence that was superseded", source_id="seg-1",
        status=NodeStatus.SUPERSEDED, superseded_at=now - timedelta(days=5),
        created_at=born, value=untouched(0.5),
    )
    stale = Inference(
        id="inference-stale", content="rests on superseded evidence", source_id="seg-1",
        created_at=born, value=untouched(0.3),
    )
    trivial = Fact(
        id="fact-trivial", content="never used, never judged", source_id="seg-1",
        created_at=born, value=untouched(0.3),
    )
    reinforced = Fact(
        id="fact-reinforced", content="used since creation", source_id="seg-1",
        created_at=born,
        value=ValueSignal(importance=0.3, retrieved_at=now - timedelta(days=1)),
    )
    supported = Fact(
        id="fact-supported", content="something depends on this", source_id="seg-1",
        created_at=born, value=untouched(0.3),
    )
    important = Fact(
        id="fact-important", content="judged to matter", source_id="seg-1",
        created_at=born, value=untouched(0.9),
    )
    dependent = Inference(
        id="inference-dependent", content="derived from the supported fact",
        source_id="seg-1", created_at=born,
    )

    for node in (retired, superseded_evidence, stale, trivial, reinforced,
                 supported, important, dependent):
        await storage.store_node(node)

    await storage.store_edge(NodeEdge(
        src_id=stale.id, dst_id=superseded_evidence.id, type=EdgeType.DERIVED_FROM,
    ))
    await storage.store_edge(NodeEdge(
        src_id=dependent.id, dst_id=supported.id, type=EdgeType.DERIVED_FROM,
    ))
    # Every extracted node carries a segment anchor; it must not read as support.
    for node in (trivial, reinforced, supported, important):
        await storage.store_edge(NodeEdge(
            src_id="seg-1", dst_id=node.id, type=EdgeType.CONTAINS,
        ))

    return storage


async def test_archival_nomination_ordering(storage_for_nomination):
    """Nominees come back worst-first, and the three spared classes are absent."""
    candidates = await nominate_archival_candidates(
        storage_for_nomination, max_age_days=90
    )

    assert [c.node_id for c in candidates] == [
        "fact-retired", "inference-stale", "fact-trivial",
    ]
    assert [c.reason for c in candidates] == [
        "retired", "evidence_stale", "never_retrieved",
    ]


async def test_archival_nomination_spares_used_and_supported_nodes(
    storage_for_nomination,
):
    nominated = {
        c.node_id
        for c in await nominate_archival_candidates(
            storage_for_nomination, max_age_days=90
        )
    }
    assert "fact-reinforced" not in nominated
    assert "fact-supported" not in nominated
    assert "fact-important" not in nominated


async def test_archival_nomination_respects_the_limit(storage_for_nomination):
    """The cheap pass is bounded: cost tracks the junk, not the graph."""
    candidates = await nominate_archival_candidates(
        storage_for_nomination, max_age_days=90, limit=2
    )
    assert [c.node_id for c in candidates] == ["fact-retired", "inference-stale"]


async def test_a_node_no_search_has_returned_is_nominated():
    """"Never retrieved" is now a state, not an inference from two timestamps.

    This used to need a one-second tolerance window: `retrieved_at` defaulted to
    creation time, so "never touched" had to be read as "these two clock reads
    are close together". A null says it outright, and the window is gone.
    """
    storage = InMemoryStorage()
    fresh = Fact(content="just created", source_id="seg-1")
    await storage.store_node(fresh)
    assert fresh.value.retrieved_at is None

    candidates = await nominate_archival_candidates(storage, max_age_days=90)

    assert [c.node_id for c in candidates] == [fresh.id]


class TestJudgmentStaleness:
    """A judgment protects a node, but not forever (#42).

    Importance is what keeps a node out of the cheap nomination tier. Before
    this, one upward judgment removed it permanently — cleanup did not get the
    node wrong later, it never looked again — so an assessment that had since
    expired went on protecting it with nobody able to notice.

    Staleness rather than decay, deliberately: a decayed importance would be a
    number nobody judged, sitting beside a provenance trail that says otherwise.
    Here the recorded judgment stays exactly as recorded, and what ages is
    confidence in its currency.
    """

    async def _judged(self, importance: float, judged_days_ago: int | None):
        storage = InMemoryStorage()
        at = (
            None if judged_days_ago is None
            else datetime.now(timezone.utc) - timedelta(days=judged_days_ago)
        )
        node = Fact(
            id="fact-judged", content="judged important once", source_id="seg-1",
            value=ValueSignal(importance=importance, importance_judged_at=at),
        )
        await storage.store_node(node)
        return storage

    async def test_a_recently_judged_node_is_left_alone(self):
        storage = await self._judged(0.9, judged_days_ago=10)
        assert await nominate_archival_candidates(storage, judgment_max_age_days=180) == []

    async def test_a_judgment_nobody_has_revisited_comes_back_for_review(self):
        """The assertion this issue is about."""
        storage = await self._judged(0.9, judged_days_ago=400)

        candidates = await nominate_archival_candidates(
            storage, judgment_max_age_days=180
        )

        assert [(c.node_id, c.reason) for c in candidates] == [
            ("fact-judged", "stale_judgment")
        ]

    async def test_a_downward_judgment_returns_a_node_to_the_cheap_tier(self):
        """The other route back, and the one an agent drives directly."""
        storage = await self._judged(0.9, judged_days_ago=1)
        node = await storage.get_node("fact-judged")
        assert await nominate_archival_candidates(storage) == []

        for _ in range(3):
            await judge_importance(
                node.id, direction="down", reason="the bug was fixed",
                storage=storage, importance_step=0.25,
            )

        candidates = await nominate_archival_candidates(storage)
        assert [c.node_id for c in candidates] == ["fact-judged"]
        assert candidates[0].reason == "never_retrieved"


async def test_a_retrieved_node_is_spared_however_recently_it_was_created():
    """The other half: one search hit takes a node out of the cheap tier.

    Under the old tolerance rule a node retrieved within a second of being
    created was still 'never used', because the check was a duration rather
    than a fact about what happened.
    """
    storage = InMemoryStorage()
    used = Fact(
        content="returned by a search",
        source_id="seg-1",
        value=ValueSignal(retrieved_at=datetime.now(timezone.utc)),
    )
    await storage.store_node(used)

    candidates = await nominate_archival_candidates(storage, max_age_days=90)

    assert candidates == []


async def test_archived_evidence_strands_its_inference():
    """The follow-on: archive the evidence, and what rests on it comes back.

    Flagged, never swept along — an inference is the layer that is expensive to
    recreate, so it goes through review on its own.
    """
    storage = InMemoryStorage()
    now = datetime.now(timezone.utc)

    evidence = Fact(id="fact-swept", content="trivial detail", source_id="seg-1")
    inference = Inference(
        id="inference-stranded", content="rests on the swept fact", source_id="seg-1",
        value=ValueSignal(importance=0.9),  # high, so only the follow-on can nominate it
    )
    await storage.store_node(evidence)
    await storage.store_node(inference)
    await storage.store_edge(NodeEdge(
        src_id=inference.id, dst_id=evidence.id, type=EdgeType.DERIVED_FROM,
    ))

    before = {c.node_id for c in await nominate_archival_candidates(storage)}
    assert "inference-stranded" not in before

    await storage.set_node_status_tx(
        [evidence], status=NodeStatus.ARCHIVED, at=now
    )

    after = await nominate_archival_candidates(storage)
    stranded = [c for c in after if c.node_id == "inference-stranded"]
    assert len(stranded) == 1
    assert stranded[0].reason == "evidence_stale"
    # ...and the archived evidence is gone from the active set, not re-nominated.
    assert "fact-swept" not in {c.node_id for c in after}


async def test_a_merged_premise_does_not_nominate_its_inference_for_archival():
    """`evidence_merged` is a re-read request, not an archival claim (#61).

    The basis did not change — the same claim now has one node and every
    document that asserted it. Nominating here would propose discarding an
    inference because its premise got *better* provenance, and it would fire
    on every dependent of every merge.
    """
    storage = InMemoryStorage()
    absorbed = Fact(
        id="fact-absorbed", content="the deploy failed", source_id="seg-1",
        status=NodeStatus.MERGED,
    )
    survivor = Fact(
        id="fact-survivor", content="deployments have been failing", source_id="seg-1",
    )
    inference = Inference(
        id="inference-dependent", content="the pipeline is unreliable", source_id="seg-1",
        value=ValueSignal(importance=0.9),
    )
    for node in (absorbed, survivor, inference):
        await storage.store_node(node)
    await storage.store_edge(NodeEdge(
        src_id=inference.id, dst_id=survivor.id, type=EdgeType.DERIVED_FROM,
    ))
    await storage.store_edge(NodeEdge(
        src_id=absorbed.id, dst_id=inference.id, type=EdgeType.EVIDENCE_MERGED,
    ))

    candidates = await nominate_archival_candidates(storage)

    assert "inference-dependent" not in {c.node_id for c in candidates}


async def test_partly_archived_evidence_does_not_strand_an_inference():
    """One surviving support is still a basis."""
    storage = InMemoryStorage()
    kept = Fact(id="fact-kept", content="still good", source_id="seg-1")
    swept = Fact(id="fact-gone", content="trivial", source_id="seg-1")
    inference = Inference(
        id="inference-ok", content="two supports", source_id="seg-1",
        value=ValueSignal(importance=0.9),
    )
    for node in (kept, swept, inference):
        await storage.store_node(node)
    for dst in (kept.id, swept.id):
        await storage.store_edge(NodeEdge(
            src_id=inference.id, dst_id=dst, type=EdgeType.DERIVED_FROM,
        ))

    await storage.set_node_status_tx(
        [swept], status=NodeStatus.ARCHIVED, at=datetime.now(timezone.utc)
    )

    nominated = {c.node_id for c in await nominate_archival_candidates(storage)}
    assert "inference-ok" not in nominated
