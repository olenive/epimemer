"""Tests for embedding providers."""

import math

from epimemer.embeddings.mock import MockEmbeddingProvider


class TestMockEmbeddingProvider:

    async def test_returns_correct_dimension(self):
        provider = MockEmbeddingProvider(dimension=8)
        vectors = await provider.embed(["hello"])
        assert len(vectors) == 1
        assert len(vectors[0]) == 8

    async def test_deterministic(self):
        provider = MockEmbeddingProvider()
        v1 = await provider.embed(["hello"])
        v2 = await provider.embed(["hello"])
        assert v1 == v2

    async def test_different_texts_different_vectors(self):
        provider = MockEmbeddingProvider()
        vectors = await provider.embed(["hello", "world"])
        assert vectors[0] != vectors[1]

    async def test_unit_vectors(self):
        provider = MockEmbeddingProvider(dimension=8)
        vectors = await provider.embed(["test"])
        norm = math.sqrt(sum(x * x for x in vectors[0]))
        assert abs(norm - 1.0) < 1e-6

    async def test_batch(self):
        provider = MockEmbeddingProvider()
        vectors = await provider.embed(["a", "b", "c"])
        assert len(vectors) == 3

    async def test_model_id(self):
        provider = MockEmbeddingProvider(model_id="my-model")
        assert provider.model_id == "my-model"

    async def test_mock_embedding_width_matches_reported_dimension(self):
        """Widths above one SHA-256 digest (32 bytes) must not be truncated.

        A provider whose `dimension` disagrees with what `embed` returns makes
        every mock-embedding measurement understate vector-scan cost.
        """
        for dimension in (8, 32, 384):
            provider = MockEmbeddingProvider(dimension=dimension)
            vectors = await provider.embed(["hello", "world"])
            again = await provider.embed(["hello", "world"])

            assert all(len(v) == provider.dimension for v in vectors)
            assert vectors == again
            for vector in vectors:
                norm = math.sqrt(sum(x * x for x in vector))
                assert abs(norm - 1.0) < 1e-6
