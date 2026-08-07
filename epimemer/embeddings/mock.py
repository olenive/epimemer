"""Mock embedding provider for testing.

Returns deterministic vectors based on text hash, so the same text
always produces the same embedding. Useful for unit tests.
"""

import hashlib


def _hash_bytes(text: str, count: int) -> bytes:
    """`count` deterministic bytes for `text`, stretched across SHA-256 blocks.

    One digest is 32 bytes, so anything wider needs more than one. Block 0 is
    the plain digest of the text, which keeps every vector of width <= 32
    byte-identical to what this provider produced before it could stretch.
    """
    blocks: list[bytes] = []
    produced = 0
    index = 0
    while produced < count:
        material = text if index == 0 else f"{text}:{index}"
        block = hashlib.sha256(material.encode()).digest()
        blocks.append(block)
        produced += len(block)
        index += 1
    return b"".join(blocks)[:count]


def deterministic_vector(text: str, dimension: int) -> list[float]:
    """A deterministic unit vector of exactly `dimension` components."""
    raw = [b / 255.0 for b in _hash_bytes(text, dimension)]
    norm = sum(x * x for x in raw) ** 0.5
    if norm == 0:
        return [0.0] * dimension
    return [x / norm for x in raw]


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
        return [deterministic_vector(text, self._dimension) for text in texts]
