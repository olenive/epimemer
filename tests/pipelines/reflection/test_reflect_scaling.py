"""`reflect` must not re-read the same thing once per candidate pair.

Profiling `reflect` on a 1,500-node graph put **88% of the wall clock** in
`same_frame` → `frames_of` → `get_edges_from`: 105k calls to resolve the frames
of at most 1,500 distinct nodes, each one a full scan of the edge set. Candidate
pairs grow quadratically with facts and the scan is linear in edges, so the
product is cubic — which is what the benchmarks measured.

These tests pin the shape of the work rather than its duration. A wall-clock
assertion in the suite would be a flake; the timing belongs in `make bench`.
What matters here is that frame resolution is proportional to *nodes*, not to
*pairs*, and that nothing about the answer changed.
"""

import contextlib
from datetime import datetime, timezone

import pytest

from epimemer.core.types import (
    EdgeType,
    EmbeddingRecord,
    Fact,
    Metacontext,
    NodeEdge,
    NodeStatus,
    Topic,
)
from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.mcp.tools import reflect
from epimemer.pipelines.reflection import contradiction_detection, topic_consolidation
from epimemer.pipelines.reflection.review import frames_of, same_frame


@pytest.fixture
def embedding_provider() -> MockEmbeddingProvider:
    return MockEmbeddingProvider(model_id="mock-embed", dimension=8)


@contextlib.contextmanager
def _counting(storage, requests: list):
    """Record each frame-resolution request, as the ids it asked about.

    Frames are read in bulk now (#14), so what a per-pair implementation would
    inflate is the number of *requests*, not the number of ids in them: a
    resolver that fell back to answering pairs one at a time would show up here
    as many single-id requests rather than one request naming the whole set.

    Restores on exit. Two measurements are taken from one store, and a wrapper
    left in place gets wrapped by the next one — so the first list keeps filling
    during the second run and the comparison reads backwards.
    """
    original = storage.get_edges_for

    async def counted(node_ids, *, direction, edge_type=None):
        if edge_type == EdgeType.HAS_METACONTEXT:
            requests.append(list(node_ids))
        return await original(node_ids, direction=direction, edge_type=edge_type)

    storage.get_edges_for = counted
    try:
        yield storage
    finally:
        storage.get_edges_for = original


async def _facts_that_look_alike(storage, provider, count: int):
    """Facts sharing one embedding, so every pair is a contradiction candidate.

    That is the load that exposes the defect: the pair count is quadratic while
    the number of distinct nodes to resolve frames for stays linear.
    """
    vector = (await provider.embed(["shared"]))[0]
    facts = []
    for i in range(count):
        fact = Fact(content=f"Claim number {i}", source_id="s1")
        await storage.store_node(fact)
        await storage.store_embedding(
            EmbeddingRecord(item_id=fact.id, model_id=provider.model_id, vector=vector)
        )
        facts.append(fact)
    return facts


class TestFrameResolutionScales:

    async def test_frames_are_resolved_for_the_whole_set_at_once(
        self, storage, embedding_provider
    ):
        """With 12 mutually-similar facts there are 66 candidate pairs. Resolving
        frames once per pair means 132 lookups for 12 nodes."""
        facts = await _facts_that_look_alike(storage, embedding_provider, 12)
        requests: list[list[str]] = []
        with _counting(storage, requests):
            await reflect(storage, embedding_provider)

        assert requests, "no frame lookups happened at all — test no longer exercises this"
        assert max(len(ids) for ids in requests) == len(facts), (
            "no request named the whole candidate set; frames are being resolved "
            "in smaller groups than the pass already knows about"
        )

    async def test_request_count_does_not_follow_the_pair_count(
        self, storage, embedding_provider
    ):
        """Doubling the facts quadruples the pairs. Requests must not move."""
        await _facts_that_look_alike(storage, embedding_provider, 8)
        small: list[list[str]] = []
        with _counting(storage, small):
            await reflect(storage, embedding_provider)

        await _facts_that_look_alike(storage, embedding_provider, 8)
        large: list[list[str]] = []
        with _counting(storage, large):
            await reflect(storage, embedding_provider)

        # 8 → 16 facts: pairs go 28 → 120. A per-pair implementation would grow
        # ~4×, a per-node one 2×; one request for the set does not grow at all.
        assert len(large) == len(small), (
            f"frame requests grew {len(small)} → {len(large)} when the pair "
            "count quadrupled"
        )


class TestContradictionScoringIsBatched:
    """Scoring must cost one matrix product, not one Python call per pair (#39).

    This is the phase that crosses the 30 s tool timeout — at ~1,400 nodes on
    SurrealDB and ~2,900 in-memory, which is around 70 documents. Unlike every
    earlier `reflect` fix there is no redundancy left to remove here: the
    comparisons are genuine work, and what these tests pin is that the work
    happens in bulk. The exponent is unchanged either way, so neither test
    asserts a duration — that measurement belongs in `make bench`, as the
    module docstring above says.
    """

    async def test_no_python_cosine_call_per_pair(
        self, storage, embedding_provider, monkeypatch
    ):
        """12 mutually-similar facts are 66 pairs, and 66 Python calls.

        In production each of those calls walks a 384-component vector twice to
        re-derive norms that do not change between pairs.
        """
        await _facts_that_look_alike(storage, embedding_provider, 12)

        calls = 0

        def counted(*_vectors):
            nonlocal calls
            calls += 1
            return 0.0  # below any threshold, so nothing downstream shifts

        monkeypatch.setattr(
            contradiction_detection, "_cosine_similarity", counted, raising=False
        )

        await reflect(storage, embedding_provider)

        assert calls == 0, (
            f"pairwise scoring made {calls} per-pair Python calls; it must score "
            "the set in one batched operation"
        )

    async def test_the_whole_set_is_scored_in_one_call(
        self, storage, embedding_provider, monkeypatch
    ):
        """One batched call per `reflect`, whatever the fact count.

        Doubling the facts quadruples the pairs. If the call count moves at all
        with size, scoring has been split back up per pair or per row.
        """
        sizes: list[int] = []
        original = contradiction_detection.similar_pairs

        def counted(vectors, threshold, **kwargs):
            sizes.append(len(vectors))
            return original(vectors, threshold, **kwargs)

        monkeypatch.setattr(contradiction_detection, "similar_pairs", counted)

        await _facts_that_look_alike(storage, embedding_provider, 12)
        await reflect(storage, embedding_provider)
        assert sizes == [12]

        await _facts_that_look_alike(storage, embedding_provider, 12)
        sizes.clear()
        await reflect(storage, embedding_provider)
        assert sizes == [24], "the pair count quadrupled; the call count must not move"


async def _topics_that_look_alike(storage, provider, count: int):
    """Topics sharing one embedding, so every pair clears any threshold."""
    vector = (await provider.embed(["shared"]))[0]
    topics = []
    for i in range(count):
        topic = Topic(content=f"Subject number {i}", source_id="s1")
        await storage.store_node(topic)
        await storage.store_embedding(
            EmbeddingRecord(item_id=topic.id, model_id=provider.model_id, vector=vector)
        )
        topics.append(topic)
    return topics


class TestTopicScoringIsBatched:
    """The same obligation as contradiction scoring, on the topic phase (#47).

    Once storage stopped being the cost (#14 step 4), this loop became **71% of
    `reflect` on SurrealDB and 88% in-memory** — 800 ms of Python at 1,200
    nodes against 9 ms of storage. It is the same quadratic and the same fix:
    the comparisons are genuine work, so they move into one matrix product
    rather than being reduced.

    As with #39, neither test asserts a duration. The exponent does not change,
    so what is pinned is the shape; `make bench` measures the constant.
    """

    async def test_no_python_cosine_call_per_pair(
        self, storage, embedding_provider, monkeypatch
    ):
        """12 mutually-similar topics are 66 pairs, and 66 Python calls.

        Each one re-derives both norms from scratch, so the same topic's vector
        is walked once per partner rather than once per pass.
        """
        await _topics_that_look_alike(storage, embedding_provider, 12)

        calls = 0

        def counted(*_vectors):
            nonlocal calls
            calls += 1
            return 0.0  # below any threshold, so nothing downstream shifts

        monkeypatch.setattr(
            topic_consolidation, "_cosine_similarity", counted, raising=False
        )

        await reflect(storage, embedding_provider)

        assert calls == 0, (
            f"topic scoring made {calls} per-pair Python calls; it must score "
            "the set in one batched operation"
        )

    async def test_the_whole_set_is_scored_in_one_call(
        self, storage, embedding_provider, monkeypatch
    ):
        """Doubling the topics quadruples the pairs; the call count must not move."""
        sizes: list[int] = []
        original = topic_consolidation.similar_pairs

        def counted(vectors, threshold, **kwargs):
            sizes.append(len(vectors))
            return original(vectors, threshold, **kwargs)

        monkeypatch.setattr(topic_consolidation, "similar_pairs", counted)

        await _topics_that_look_alike(storage, embedding_provider, 12)
        await reflect(storage, embedding_provider)
        assert sizes == [12]

        await _topics_that_look_alike(storage, embedding_provider, 12)
        sizes.clear()
        await reflect(storage, embedding_provider)
        assert sizes == [24], "the pair count quadrupled; the call count must not move"


class TestEdgeFetchesDoNotScaleWithTheGraph:
    """Every phase asks for its edges in bulk (#14 step 1).

    Profiling `reflect` on SurrealDB after #39 put it at **4,323 storage calls
    for 400 nodes**, and the dominant sites were not the ones #14 predicted:
    `gather_pending_review` alone issued three per node (35%), relation
    consolidation two more (18%), while the contradiction phase #14 named was
    14%. All of them share one shape — iterate nodes, fetch that node's edges —
    so all of them are fixed by asking once for many.

    Round-trips are what these tests pin, because on a networked backend that is
    what the wall clock is made of. As everywhere in this module the duration
    itself belongs in `make bench`.
    """

    async def _single_node_edge_calls(self, storage, provider, count: int) -> int:
        """Per-node edge lookups issued by one `reflect` over `count` facts."""
        await _facts_that_look_alike(storage, provider, count)

        calls = 0
        originals = {
            name: getattr(storage, name)
            for name in ("get_edges_from", "get_edges_to")
        }

        def counting(original):
            async def counted(node_id, *, edge_type=None):
                nonlocal calls
                calls += 1
                return await original(node_id, edge_type=edge_type)

            return counted

        for name, original in originals.items():
            setattr(storage, name, counting(original))
        try:
            await reflect(storage, provider)
        finally:
            for name, original in originals.items():
                setattr(storage, name, original)
        return calls

    async def test_doubling_the_graph_does_not_add_lookups(
        self, storage, embedding_provider
    ):
        """8 → 16 facts. A per-node fetch doubles; a batched one does not move.

        Deliberately a growth assertion rather than a budget: what is wrong is
        that the count tracks the graph, and a fixed number would have to be
        rewritten every time a phase is added.
        """
        # Each call *adds* facts to the same graph, so the second reflect runs
        # over 16 of them — the same accumulate-and-compare shape as
        # `test_lookup_count_grows_with_nodes_not_pairs` above.
        small = await self._single_node_edge_calls(storage, embedding_provider, 8)
        large = await self._single_node_edge_calls(storage, embedding_provider, 8)

        assert large <= small, (
            f"per-node edge lookups grew {small} → {large} when the graph "
            "doubled; every phase must fetch edges for its whole node set at once"
        )

    async def test_reflect_asks_for_edges_in_bulk(self, storage, embedding_provider):
        """The positive form: the batched call is the one actually being used.

        Without this, deleting the phases entirely would pass the test above.
        """
        await _facts_that_look_alike(storage, embedding_provider, 6)
        topic = Topic(content="Weather", source_id="s1")
        await storage.store_node(topic)

        batched = 0
        original = storage.get_edges_for

        async def counted(node_ids, *, direction, edge_type=None):
            nonlocal batched
            batched += 1
            return await original(node_ids, direction=direction, edge_type=edge_type)

        storage.get_edges_for = counted
        await reflect(storage, embedding_provider)

        assert batched > 0, "no phase used the batched edge fetch"


class TestMaterialIsGatheredOnce:

    async def test_a_topic_material_is_read_once_per_reflect(
        self, storage, embedding_provider
    ):
        """Split detection and the enrichment scan both need every topic's
        material. Gathering it twice doubles the read for nothing.

        Now that the read is batched (#14) the count to watch is per edge type
        across the whole topic set, not per topic: one `SUPPORTS` query and one
        `ABSTRACTS` query serve both phases.
        """
        topic = Topic(content="Weather", source_id="s1")
        await storage.store_node(topic)
        for i in range(3):
            fact = Fact(content=f"Observation {i}", source_id="s1")
            await storage.store_node(fact)
            await storage.store_edge(
                NodeEdge(src_id=fact.id, dst_id=topic.id, type=EdgeType.SUPPORTS)
            )

        counts: dict[EdgeType, int] = {}
        original = storage.get_edges_for

        async def counted(node_ids, *, direction, edge_type=None):
            if edge_type in (EdgeType.SUPPORTS, EdgeType.ABSTRACTS):
                counts[edge_type] = counts.get(edge_type, 0) + 1
            return await original(node_ids, direction=direction, edge_type=edge_type)

        storage.get_edges_for = counted
        await reflect(storage, embedding_provider)

        assert counts, "no material lookups happened — test no longer exercises this"
        assert max(counts.values()) == 1, (
            f"topic material was gathered {max(counts.values())} times in one "
            "reflect"
        )


class TestAnswersAreUnchanged:

    async def test_disjoint_frames_still_suppress_a_contradiction(
        self, storage, embedding_provider
    ):
        """The caching must not flatten the frame check it is caching."""
        fiction = Metacontext(content="Fiction")
        await storage.store_metacontext(fiction)
        vector = (await embedding_provider.embed(["shared"]))[0]

        a = Fact(content="The sky is green", source_id="s1")
        b = Fact(content="The sky is blue", source_id="s2")
        for fact in (a, b):
            await storage.store_node(fact)
            await storage.store_embedding(
                EmbeddingRecord(
                    item_id=fact.id,
                    model_id=embedding_provider.model_id,
                    vector=vector,
                )
            )
        # `a` lives in the fiction frame; `b` is untagged, so base reality.
        await storage.store_edge(
            __import__("epimemer.core.types", fromlist=["NodeEdge"]).NodeEdge(
                src_id=a.id, dst_id=fiction.id, type=EdgeType.HAS_METACONTEXT
            )
        )

        assert await frames_of(a.id, storage) == {fiction.id}
        assert await same_frame(a.id, b.id, storage) is False

        result, _ = await reflect(storage, embedding_provider)
        assert result["contradictions"] == []

    async def test_same_frame_facts_still_surface(self, storage, embedding_provider):
        await _facts_that_look_alike(storage, embedding_provider, 3)

        result, _ = await reflect(storage, embedding_provider)

        assert result["contradictions"], "same-frame candidates must still be reported"

    async def test_topics_and_facts_are_unaffected(self, storage, embedding_provider):
        """A smoke check that the other phases still return what they did."""
        topic = Topic(content="Weather", source_id="s1")
        await storage.store_node(topic)
        await _facts_that_look_alike(storage, embedding_provider, 3)

        result, meta = await reflect(storage, embedding_provider)

        assert set(result) == {
            "similar_pairs",
            "split_candidates",
            "enrichment_candidates",
            "contradictions",
            "recurrences",
            "pending_review",
            "archival_candidates",
            "similar_relations",
        }
        assert meta.nodes_returned >= len(result["contradictions"])


class TestReflectWritesNothing:
    """`reflect` proposes; it does not act (#44).

    Value decay was the one exception — a write per active node per pass, to a
    field nothing read. With it gone the invariant is clean and worth pinning:
    every phase is a read, the agent decides, and `apply_reflection` is the only
    thing that changes the graph. It is also what makes `reflect` safe to run
    speculatively, and it removes the largest write from the path #14 is
    still trying to make fit inside the tool timeout.
    """

    async def test_no_node_is_written_during_a_reflect(
        self, storage, embedding_provider
    ):
        await _facts_that_look_alike(storage, embedding_provider, 6)
        topic = Topic(content="Weather", source_id="s1")
        await storage.store_node(topic)

        written: list[str] = []
        original = storage.store_node

        async def counted(node):
            written.append(node.id)
            return await original(node)

        storage.store_node = counted
        try:
            await reflect(storage, embedding_provider)
        finally:
            storage.store_node = original

        assert written == [], (
            f"reflect wrote {len(written)} nodes; it must only propose"
        )


class TestReflectCatchesRecurrenceItsOwnWay:
    """The safety net under the ingest-time detector (#53 T2).

    `check_conflicts` is opt-in, so a graph whose agent never ran it can hold a
    live claim beside the retired one it repeats and nobody would ever be asked.
    Reflect nominates historical facts too — and reports them **apart from the
    contradictions**, because a claim beside its own successor is not a
    contradiction and filing it under that word is the misreading `recurs`
    exists to prevent.
    """

    async def _retire(self, storage, node, status: NodeStatus) -> None:
        await storage.set_node_status_tx(
            [node], status=status, at=datetime.now(timezone.utc)
        )

    async def test_a_live_claim_repeating_a_retired_one_is_reported(
        self, storage, embedding_provider
    ):
        facts = await _facts_that_look_alike(storage, embedding_provider, 2)
        await self._retire(storage, facts[0], NodeStatus.HISTORICAL)

        result, _ = await reflect(storage, embedding_provider)

        pairs = result["recurrences"]
        assert len(pairs) == 1
        statuses = {pairs[0]["fact_a"]["status"], pairs[0]["fact_b"]["status"]}
        assert statuses == {"active", "historical"}

    async def test_it_is_not_filed_as_a_contradiction(
        self, storage, embedding_provider
    ):
        facts = await _facts_that_look_alike(storage, embedding_provider, 2)
        await self._retire(storage, facts[0], NodeStatus.HISTORICAL)

        result, _ = await reflect(storage, embedding_provider)

        assert result["contradictions"] == []

    async def test_two_live_claims_stay_contradictions(
        self, storage, embedding_provider
    ):
        """Two active facts saying the same thing is redundancy or conflict —
        the older question, and still the one `contradictions` answers."""
        await _facts_that_look_alike(storage, embedding_provider, 2)

        result, _ = await reflect(storage, embedding_provider)

        assert result["contradictions"]
        assert result["recurrences"] == []

    async def test_a_corrected_claim_is_never_nominated(
        self, storage, embedding_provider
    ):
        facts = await _facts_that_look_alike(storage, embedding_provider, 2)
        await self._retire(storage, facts[0], NodeStatus.CORRECTED)

        result, _ = await reflect(storage, embedding_provider)

        assert result["recurrences"] == []
        assert result["contradictions"] == []

    async def test_the_wider_sweep_still_scores_in_one_call(
        self, storage, embedding_provider, monkeypatch
    ):
        """Widening what is compared must not mean scoring the matrix twice.

        This phase is the one that crosses the tool timeout as a graph grows
        (#39). The pairs are quadratic; splitting the scored set into
        contradictions and recurrences is free, and computing it twice is not.
        """
        sizes: list[int] = []
        original = contradiction_detection.similar_pairs

        def counted(vectors, threshold, **kwargs):
            sizes.append(len(vectors))
            return original(vectors, threshold, **kwargs)

        monkeypatch.setattr(contradiction_detection, "similar_pairs", counted)

        facts = await _facts_that_look_alike(storage, embedding_provider, 6)
        await self._retire(storage, facts[0], NodeStatus.HISTORICAL)
        await reflect(storage, embedding_provider)

        assert sizes == [6], "one batched call over active and historical alike"
