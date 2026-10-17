"""Sentence Transformers embedding provider.

Uses the sentence-transformers library for local embedding generation.
"""

import asyncio
import threading

from sentence_transformers import SentenceTransformer


class SentenceTransformersProvider:
    """Embedding provider backed by sentence-transformers."""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        self._model_name = model_name
        self._model = SentenceTransformer(model_name)
        self._dimension = self._model.get_sentence_embedding_dimension()
        self._encode_lock = threading.Lock()

    @property
    def model_id(self) -> str:
        return self._model_name

    @property
    def dimension(self) -> int:
        return self._dimension

    def _encode(self, texts: list[str]):
        # One encode at a time, held in the worker itself: a lock taken on the
        # event loop would be released if the caller were cancelled while the
        # worker ran on, and the next encode could overlap it.
        with self._encode_lock:
            return self._model.encode(texts, convert_to_numpy=True)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        # A long encode must not hold the event loop: in a worker thread it
        # leaves the server responsive, and events already queued, such as the
        # dashboard's launch event for the transition calling this, get out
        # while it runs.
        embeddings = await asyncio.to_thread(self._encode, texts)
        return [vec.tolist() for vec in embeddings]
