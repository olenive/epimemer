"""Server configuration and provider factory functions.

ServerConfig reads from environment variables (EPIMEMER_ prefix).
Factory functions create storage and embedding providers based on the config.
"""

import os
from typing import Literal

from pydantic import BaseModel, field_validator

from epimemer.core.advisories import WarningPolicy
from epimemer.embeddings.protocol import EmbeddingProvider
from epimemer.storage.protocol import StorageBackend

# Recording retrieval is on by default. Defined here rather than inline on the
# field so `tools.search` can share the constant instead of keeping a second
# copy that can drift out of step with the config.
DEFAULT_RECORD_RETRIEVAL = True

# How much of the gap to 1.0 one `judge_importance` call closes. Shared with
# `tools.judge_importance` for the same reason as the flag above.
DEFAULT_IMPORTANCE_STEP = 0.25


class ServerConfig(BaseModel):
    """Configuration for the Epimemer MCP server."""

    storage_backend: Literal["memory", "surrealdb"] = "memory"
    surrealdb_url: str = "ws://localhost:8000/rpc"
    surrealdb_user: str = "root"
    surrealdb_pass: str = "root"
    surrealdb_namespace: str = "epimemer"
    # The graph a server lands on when nothing names one. **Deliberately a name
    # nobody would give a real graph**: it used to be `"memory"`, which collided
    # with a real graph of that name, so a server started without
    # `EPIMEMER_GRAPH` wrote into somebody's actual data and looked like it had
    # worked. A default that lands somewhere empty is wrong in a way you notice.
    surrealdb_database: str = "default"
    # `EPIMEMER_GRAPH` — the graph to open, overriding `surrealdb_database`.
    # Empty means *unset*, not a graph named "". Set it per server: the active
    # graph is process state, so `use_graph` lasts only as long as the process
    # and a client reconnect lands back on whatever this resolves to.
    graph: str = ""

    embedding_provider: Literal["sentence-transformers", "mock"] = "sentence-transformers"
    embedding_model_id: str = "all-MiniLM-L6-v2"
    embedding_dimension: int = 384

    segmentation_strategy: Literal["paragraph", "semantic"] = "paragraph"
    similarity_threshold: float = 0.75

    reflect_threshold: int = 10
    tool_timeout_seconds: float = 30.0

    # Agent ids the user admits to every graph this server opens
    # (REVIEW_MODE.md §2.3, §10.3). This is the approval channel for clients
    # that cannot elicit **and** for the embedded backend, where the
    # `epimemer agents confirm` CLI cannot help: a second `mem://` connection is
    # a separate store, so the CLI would write approvals into a
    # store this process will never read. Empty means every claim_agent is
    # refused until a user answers an elicitation — which is the right default,
    # since an agent that could admit its own id would be asserting its own
    # identity (§2.2).
    approved_agents: list[str] = []

    # The process default for *must a write name a judge?* (REVIEW_MODE.md
    # §3.3.1). Off, because blank means **unknown** and for many graphs it
    # genuinely does not matter who judged; a user who wants every write tied to
    # an agent or a person turns it on here or per graph.
    #
    # Not an MCP tool, and that is the whole reason it lives in config: a gate
    # the agent can open is decoration. Turning it on without approving an id
    # first refuses every write, which is why the refusal says so.
    require_judge: bool = False

    # Whether `search` stamps `retrieved_at` on what it returns. Costs one
    # write per returned node; turning it off makes `never_retrieved` blind, so
    # archival nomination stops being able to tell used nodes from stale ones.
    record_retrieval: bool = DEFAULT_RECORD_RETRIEVAL

    # Asymptotic step applied by the `judge_importance` tool. Nothing lowers
    # importance on a clock — a judgment ages, it does not erode.
    importance_step: float = DEFAULT_IMPORTANCE_STEP

    # The process default for what to do about advisories, which a graph
    # overrides with `configure_warnings`. **No environment variable**, and that
    # is a decision rather than an omission: `by_kind` is a map, an env var is
    # one string, and a hand-rolled parser for it would be a second syntax for a
    # setting the tool already expresses properly. A deployment that wants a
    # different default constructs `ServerConfig` with one.
    warning_policy: WarningPolicy = WarningPolicy()

    log_level: str = "INFO"
    log_file: str | None = None

    viz_enabled: bool = True
    viz_host: str = "127.0.0.1"
    viz_port: int = 8765
    viz_autospawn: bool = True

    @field_validator("approved_agents", mode="before")
    @classmethod
    def _split_ids(cls, value):
        """Accept a comma-separated string, because an env var is one string.

        Whitespace around an id is the user's formatting, not part of the id —
        an id that differs from the approved one by a space refuses every claim
        and gives no clue why.
        """
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value


def load_config() -> ServerConfig:
    """Load server config from EPIMEMER_ environment variables."""
    env_map = {
        "storage_backend": "EPIMEMER_STORAGE_BACKEND",
        "surrealdb_url": "EPIMEMER_SURREALDB_URL",
        "surrealdb_user": "EPIMEMER_SURREALDB_USER",
        "surrealdb_pass": "EPIMEMER_SURREALDB_PASS",
        "surrealdb_namespace": "EPIMEMER_SURREALDB_NAMESPACE",
        "surrealdb_database": "EPIMEMER_SURREALDB_DATABASE",
        "graph": "EPIMEMER_GRAPH",
        "embedding_provider": "EPIMEMER_EMBEDDING_PROVIDER",
        "embedding_model_id": "EPIMEMER_EMBEDDING_MODEL_ID",
        "embedding_dimension": "EPIMEMER_EMBEDDING_DIMENSION",
        "segmentation_strategy": "EPIMEMER_SEGMENTATION_STRATEGY",
        "similarity_threshold": "EPIMEMER_SIMILARITY_THRESHOLD",
        "reflect_threshold": "EPIMEMER_REFLECT_THRESHOLD",
        "approved_agents": "EPIMEMER_APPROVED_AGENTS",
        "require_judge": "EPIMEMER_REQUIRE_JUDGE",
        "record_retrieval": "EPIMEMER_RECORD_RETRIEVAL",
        "importance_step": "EPIMEMER_IMPORTANCE_STEP",
        "tool_timeout_seconds": "EPIMEMER_TOOL_TIMEOUT_SECONDS",
        "log_level": "EPIMEMER_LOG_LEVEL",
        "log_file": "EPIMEMER_LOG_FILE",
        "viz_enabled": "EPIMEMER_VIZ_ENABLED",
        "viz_host": "EPIMEMER_VIZ_HOST",
        "viz_port": "EPIMEMER_VIZ_PORT",
        "viz_autospawn": "EPIMEMER_VIZ_AUTOSPAWN",
    }

    overrides = {}
    for field_name, env_var in env_map.items():
        value = os.environ.get(env_var)
        if value is not None:
            overrides[field_name] = value

    return ServerConfig(**overrides)


def create_storage(config: ServerConfig) -> StorageBackend:
    """Create a storage backend from config, landing on the configured graph.

    **This is the only thing that decides which graph a server opens.** The
    active graph is process state — `use_graph` switches it and nothing persists
    the switch — so a client reconnect starts a fresh process and comes back
    here. A session that spent an hour in one graph reopens in whatever this
    resolves to, which is intended and is why the tools report `active_graph`.
    """
    if config.storage_backend == "surrealdb":
        from epimemer.storage.surrealdb_adapter import SurrealDBStorage

        # EPIMEMER_GRAPH first, then EPIMEMER_SURREALDB_DATABASE. Empty means
        # unset rather than a graph named "".
        database = config.graph if config.graph != "" else config.surrealdb_database
        return SurrealDBStorage(
            url=config.surrealdb_url,
            user=config.surrealdb_user,
            password=config.surrealdb_pass,
            namespace=config.surrealdb_namespace,
            database=database,
        )
    else:
        from epimemer.storage.memory import InMemoryStorage

        return InMemoryStorage()


def create_embedding_provider(config: ServerConfig) -> EmbeddingProvider:
    """Create an embedding provider from config."""
    if config.embedding_provider == "sentence-transformers":
        # An optional extra, because it brings PyTorch with it. Refusing here
        # names the extra; the alternative is an ImportError from three
        # modules down that names a package the user never asked for.
        try:
            from epimemer.embeddings.sentence_transformers import (
                SentenceTransformersProvider,
            )
        except ImportError as missing:
            raise RuntimeError(
                "EPIMEMER_EMBEDDING_PROVIDER is 'sentence-transformers' but that "
                "package is not installed. It is an optional extra: install "
                "'epimemer[sentence-transformers]', or set "
                "EPIMEMER_EMBEDDING_PROVIDER to another provider."
            ) from missing
        return SentenceTransformersProvider(model_name=config.embedding_model_id)
    else:
        from epimemer.embeddings.mock import MockEmbeddingProvider

        return MockEmbeddingProvider(
            model_id=config.embedding_model_id,
            dimension=config.embedding_dimension,
        )
