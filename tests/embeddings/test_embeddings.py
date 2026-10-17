"""Tests for embedding providers."""

import asyncio
import importlib
import math
import sys
import threading
import time
import types

import numpy

from epimemer.embeddings.mock import MockEmbeddingProvider

PROVIDER_MODULE = "epimemer.embeddings.sentence_transformers"


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


async def test_sentence_transformers_encodes_off_the_loop_one_at_a_time(monkeypatch, request):
    """A stand-in model, so no weights load. Each encode waits for the loop to
    run a coroutine, which an encode holding the loop would wait for in vain,
    and counts the encodes running beside it."""
    loop_turned = threading.Event()
    running: list[str] = []
    counts: list[int] = []
    turned: list[bool] = []

    def encode(texts: list[str], convert_to_numpy: bool) -> numpy.ndarray:
        running.append(texts[0])
        counts.append(len(running))
        turned.append(loop_turned.wait(timeout=2))
        time.sleep(0.01)
        running.remove(texts[0])
        return numpy.ones((len(texts), 2))

    stand_in = types.ModuleType("sentence_transformers")
    stand_in.SentenceTransformer = lambda name: types.SimpleNamespace(
        get_sentence_embedding_dimension=lambda: 2, encode=encode
    )
    monkeypatch.setitem(sys.modules, "sentence_transformers", stand_in)
    monkeypatch.delitem(sys.modules, PROVIDER_MODULE, raising=False)
    # Runs before monkeypatch's undo, so a later import gets the real module.
    request.addfinalizer(lambda: sys.modules.pop(PROVIDER_MODULE, None))
    provider = importlib.import_module(PROVIDER_MODULE).SentenceTransformersProvider()

    async def turn_the_loop() -> None:
        loop_turned.set()

    first, second, _ = await asyncio.gather(
        provider.embed(["a"]), provider.embed(["b"]), turn_the_loop()
    )

    assert turned == [True, True]
    assert counts == [1, 1]
    assert first == second == [[1.0, 1.0]]
