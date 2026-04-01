"""Sentence Transformers embedding provider.

Uses the sentence-transformers library for local embedding generation.
"""

from sentence_transformers import SentenceTransformer


class SentenceTransformersProvider:
    """Embedding provider backed by sentence-transformers."""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        self._model_name = model_name
        self._model = SentenceTransformer(model_name)
        self._dimension = self._model.get_sentence_embedding_dimension()

    @property
    def model_id(self) -> str:
        return self._model_name

    @property
    def dimension(self) -> int:
        return self._dimension

    async def embed(self, texts: list[str]) -> list[list[float]]:
        # sentence-transformers is synchronous but fast for small batches
        embeddings = self._model.encode(texts, convert_to_numpy=True)
        return [vec.tolist() for vec in embeddings]
