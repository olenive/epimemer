"""Mock embedding provider for testing.

Returns deterministic vectors based on text hash, so the same text
always produces the same embedding. Useful for unit tests.
"""

import hashlib


class MockEmbeddingProvider:
    """Deterministic mock embedding provider for testing."""

    def __init__(self, model_id: str = "mock-embed", dimension: int = 8):
        self._model_id = model_id
        self._dimension = dimension

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def dimension(self) -> int:
        return self._dimension

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._deterministic_vector(text) for text in texts]

    def _deterministic_vector(self, text: str) -> list[float]:
        """Generate a deterministic unit vector from text content."""
        digest = hashlib.sha256(text.encode()).digest()
        raw = [b / 255.0 for b in digest[: self._dimension]]
        # Normalize to unit vector
        norm = sum(x * x for x in raw) ** 0.5
        if norm == 0:
            return [0.0] * self._dimension
        return [x / norm for x in raw]
