"""Embedding provider protocol.

Defines the interface that all embedding backends must implement.
Providers are pluggable — swap between sentence-transformers, OpenAI, etc.
"""

from typing import Protocol


class EmbeddingProvider(Protocol):
    """Protocol for embedding providers."""

    @property
    def model_id(self) -> str:
        """Unique identifier for this embedding model."""
        ...

    @property
    def dimension(self) -> int:
        """Dimensionality of the embedding vectors."""
        ...

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts. Returns one vector per text."""
        ...
