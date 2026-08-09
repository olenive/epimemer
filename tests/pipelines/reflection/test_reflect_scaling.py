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

import pytest

from epimemer.core.types import (
    EdgeType,
    EmbeddingRecord,
    Fact,
    Metacontext,
    NodeEdge,
    Topic,
)
from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.mcp.tools import reflect
from epimemer.pipelines.reflection import contradiction_detection
from epimemer.pipelines.reflection.review import frames_of, same_frame


@pytest.fixture
def embedding_provider() -> MockEmbeddingProvider:
    return MockEmbeddingProvider(model_id="mock-embed", dimension=8)


def _counting(storage, counts: dict):
    """Wrap get_edges_from so the test can count frame lookups by node."""
    original = storage.get_edges_from

    async def counted(node_id, *, edge_type=None):
        if edge_type == EdgeType.HAS_METACONTEXT:
            counts[node_id] = counts.get(node_id, 0) + 1
        return await original(node_id, edge_type=edge_type)

    storage.get_edges_from = counted
    return storage


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

    async def test_frames_are_resolved_per_node_not_per_pair(
        self, storage, embedding_provider
    ):
        """With 12 mutually-similar facts there are 66 candidate pairs. Resolving
        frames once per pair means 132 lookups for 12 nodes."""
        await _facts_that_look_alike(storage, embedding_provider, 12)
        counts: dict[str, int] = {}
        _counting(storage, counts)

        await reflect(storage, embedding_provider)

        assert counts, "no frame lookups happened at all — test no longer exercises this"
        assert max(counts.values()) == 1, (
            f"a node's frames were resolved {max(counts.values())} times in one "
            "reflect; frame lookups must not scale with candidate pairs"
        )

    async def test_lookup_count_grows_with_nodes_not_pairs(
        self, storage, embedding_provider
    ):
        """Doubling the facts quadruples the pairs. Lookups must merely double."""
        await _facts_that_look_alike(storage, embedding_provider, 8)
        small: dict[str, int] = {}
        _counting(storage, small)
        await reflect(storage, embedding_provider)
        small_total = sum(small.values())

        await _facts_that_look_alike(storage, embedding_provider, 8)
        large: dict[str, int] = {}
        _counting(storage, large)
        await reflect(storage, embedding_provider)
        large_total = sum(large.values())

        # 8 → 16 facts: pairs go 28 → 120, so a per-pair implementation would
        # grow ~4×. Per-node growth is bounded well below that.
        assert large_total < small_total * 3


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


class TestMaterialIsGatheredOnce:

    async def test_a_topic_material_is_read_once_per_reflect(
        self, storage, embedding_provider
    ):
        """Split detection and the enrichment scan both need each topic's
        material. Reading it twice doubles a full edge scan per topic for
        nothing."""
        topic = Topic(content="Weather", source_id="s1")
        await storage.store_node(topic)
        for i in range(3):
            fact = Fact(content=f"Observation {i}", source_id="s1")
            await storage.store_node(fact)
            await storage.store_edge(
                NodeEdge(src_id=fact.id, dst_id=topic.id, type=EdgeType.SUPPORTS)
            )

        counts: dict[tuple[str, EdgeType], int] = {}
        original = storage.get_edges_to

        async def counted(node_id, *, edge_type=None):
            if edge_type in (EdgeType.SUPPORTS, EdgeType.ABSTRACTS):
                key = (node_id, edge_type)
                counts[key] = counts.get(key, 0) + 1
            return await original(node_id, edge_type=edge_type)

        storage.get_edges_to = counted
        await reflect(storage, embedding_provider)

        assert counts, "no material lookups happened — test no longer exercises this"
        assert max(counts.values()) == 1, (
            f"a topic's material was gathered {max(counts.values())} times in one "
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
            "nodes_decayed",
            "similar_pairs",
            "split_candidates",
            "enrichment_candidates",
            "contradictions",
            "pending_review",
            "archival_candidates",
            "similar_relations",
        }
        assert meta.nodes_returned >= len(result["contradictions"])
