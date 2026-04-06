"""Server configuration and provider factory functions.

ServerConfig reads from environment variables (EPIMEMER_ prefix).
Factory functions create storage and embedding providers based on the config.
"""

import os
from typing import Literal

from pydantic import BaseModel

from epimemer.embeddings.protocol import EmbeddingProvider
from epimemer.storage.protocol import StorageBackend


class ServerConfig(BaseModel):
    """Configuration for the Epimemer MCP server."""

    storage_backend: Literal["memory", "surrealdb"] = "memory"
    surrealdb_url: str = "ws://localhost:8000/rpc"
    surrealdb_user: str = "root"
    surrealdb_pass: str = "root"
    surrealdb_namespace: str = "epimemer"
    surrealdb_database: str = "memory"

    embedding_provider: Literal["sentence-transformers", "mock"] = "sentence-transformers"
    embedding_model_id: str = "all-MiniLM-L6-v2"
    embedding_dimension: int = 384

    segmentation_strategy: Literal["paragraph", "semantic"] = "paragraph"
    similarity_threshold: float = 0.75

    reflect_threshold: int = 10
    tool_timeout_seconds: float = 30.0

    log_level: str = "INFO"
    log_file: str | None = None

    viz_enabled: bool = True
    viz_host: str = "127.0.0.1"
    viz_port: int = 8765


def load_config() -> ServerConfig:
    """Load server config from EPIMEMER_ environment variables."""
    env_map = {
        "storage_backend": "EPIMEMER_STORAGE_BACKEND",
        "surrealdb_url": "EPIMEMER_SURREALDB_URL",
        "surrealdb_user": "EPIMEMER_SURREALDB_USER",
        "surrealdb_pass": "EPIMEMER_SURREALDB_PASS",
        "surrealdb_namespace": "EPIMEMER_SURREALDB_NAMESPACE",
        "surrealdb_database": "EPIMEMER_SURREALDB_DATABASE",
        "embedding_provider": "EPIMEMER_EMBEDDING_PROVIDER",
        "embedding_model_id": "EPIMEMER_EMBEDDING_MODEL_ID",
        "embedding_dimension": "EPIMEMER_EMBEDDING_DIMENSION",
        "segmentation_strategy": "EPIMEMER_SEGMENTATION_STRATEGY",
        "similarity_threshold": "EPIMEMER_SIMILARITY_THRESHOLD",
        "reflect_threshold": "EPIMEMER_REFLECT_THRESHOLD",
        "tool_timeout_seconds": "EPIMEMER_TOOL_TIMEOUT_SECONDS",
        "log_level": "EPIMEMER_LOG_LEVEL",
        "log_file": "EPIMEMER_LOG_FILE",
        "viz_enabled": "EPIMEMER_VIZ_ENABLED",
        "viz_host": "EPIMEMER_VIZ_HOST",
        "viz_port": "EPIMEMER_VIZ_PORT",
    }

    overrides = {}
    for field_name, env_var in env_map.items():
        value = os.environ.get(env_var)
        if value is not None:
            overrides[field_name] = value

    return ServerConfig(**overrides)


def create_storage(config: ServerConfig) -> StorageBackend:
    """Create a storage backend from config."""
    if config.storage_backend == "surrealdb":
        from epimemer.storage.surrealdb_adapter import SurrealDBStorage
        return SurrealDBStorage(
            url=config.surrealdb_url,
            user=config.surrealdb_user,
            password=config.surrealdb_pass,
            namespace=config.surrealdb_namespace,
            database=config.surrealdb_database,
        )
    else:
        from epimemer.storage.memory import InMemoryStorage
        return InMemoryStorage()


def create_embedding_provider(config: ServerConfig) -> EmbeddingProvider:
    """Create an embedding provider from config."""
    if config.embedding_provider == "sentence-transformers":
        from epimemer.embeddings.sentence_transformers import SentenceTransformersProvider
        return SentenceTransformersProvider(model_name=config.embedding_model_id)
    else:
        from epimemer.embeddings.mock import MockEmbeddingProvider
        return MockEmbeddingProvider(
            model_id=config.embedding_model_id,
            dimension=config.embedding_dimension,
        )
