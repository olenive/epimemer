# Epimemer

A layered epistemic memory system for AI agents. Epimemer maintains an evolving dual-space architecture (vector + graph) where embeddings provide the semantic foundation and typed graph structure is derived on top.

For the full architectural design, see [SUMMARY.md](SUMMARY.md).

## Quickstart

### Prerequisites

- Python 3.14+
- [uv](https://docs.astral.sh/uv/)
- [Petritype](../petritype) cloned as a sibling directory

### Install

```bash
git clone <repo-url> epimemer
cd epimemer
uv sync
```

### Run tests

```bash
uv run python -m pytest tests/ -q
```

### Start the MCP server (in-memory, mock providers)

```bash
EPIMEMER_STORAGE_BACKEND=memory \
EPIMEMER_EMBEDDING_PROVIDER=mock \
EPIMEMER_DECOMPOSITION_PROVIDER=mock \
uv run python -m epimemer.mcp.server
```

This starts on stdio — designed to be called by Claude Code or another MCP client.

### Connect to Claude Code

```bash
claude mcp add epimemer -- uv run --directory /path/to/epimemer python -m epimemer.mcp.server
```

Then verify with `/mcp` in Claude Code. See [INTEGRATION.md](INTEGRATION.md) for full configuration options and system prompt guidance.

## Production Setup (SurrealDB)

### With Docker Compose

```bash
docker compose up -d
```

This starts:
- **SurrealDB** on port 8000
- **Epimemer MCP server** connected to SurrealDB, using sentence-transformers for embeddings and Pydantic AI for LLM decomposition

### Without Docker

1. Install and start [SurrealDB](https://surrealdb.com/install):
   ```bash
   surreal start --user root --pass root memory
   ```

2. Start the MCP server:
   ```bash
   EPIMEMER_STORAGE_BACKEND=surrealdb \
   EPIMEMER_SURREALDB_URL=ws://localhost:8000/rpc \
   EPIMEMER_EMBEDDING_PROVIDER=sentence-transformers \
   EPIMEMER_DECOMPOSITION_PROVIDER=pydantic-ai \
   EPIMEMER_DECOMPOSITION_MODEL=claude-sonnet-4-20250514 \
   uv run python -m epimemer.mcp.server
   ```

## Configuration

All configuration is via `EPIMEMER_` environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `EPIMEMER_STORAGE_BACKEND` | `memory` | `memory` or `surrealdb` |
| `EPIMEMER_SURREALDB_URL` | `ws://localhost:8000/rpc` | SurrealDB connection URL |
| `EPIMEMER_EMBEDDING_PROVIDER` | `sentence-transformers` | `sentence-transformers` or `mock` |
| `EPIMEMER_EMBEDDING_MODEL_ID` | `all-MiniLM-L6-v2` | Embedding model name |
| `EPIMEMER_DECOMPOSITION_PROVIDER` | `pydantic-ai` | `pydantic-ai` or `mock` |
| `EPIMEMER_DECOMPOSITION_MODEL` | `claude-sonnet-4-20250514` | LLM model for decomposition |
| `EPIMEMER_SEGMENTATION_STRATEGY` | `paragraph` | `paragraph` or `semantic` |
| `EPIMEMER_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `EPIMEMER_LOG_FILE` | (stdout) | Path to log file |

## MCP Tools

14 tools exposed via the Model Context Protocol:

| Tool | Purpose |
|------|---------|
| `memory.ingest` | Ingest text — segment, decompose, build graph, embed |
| `memory.search` | Hybrid retrieval (vector + graph), metacontext-aware |
| `memory.link` | Create typed edges between nodes |
| `memory.update` | Create new node version (immutable history) |
| `memory.reflect` | Consolidate topics, decay stale nodes, detect contradictions |
| `memory.query_graph` | Traverse the graph from a node |
| `memory.archive` | Export old superseded nodes to cold storage |
| `memory.restore` | Reimport archived nodes |
| `memory.create_timeline` | Create a named timeline |
| `memory.add_timepoint` | Add a concrete or vague timepoint to a timeline |
| `memory.query_timeline` | Find nearest timepoints or query a time range |
| `memory.create_timelink` | Link a node to a timepoint on a timeline |
| `memory.create_metacontext` | Create an epistemic frame for disambiguation |
| `memory.get_metacontexts` | Get metacontexts for a node |

## Architecture

See [SUMMARY.md](SUMMARY.md) for the full design. Key concepts:

- **Dual-space**: vector embeddings as primary representation, typed graph derived on top
- **Three node types**: Topics (themes), Facts (atomic statements), Inferences (provisional derivations)
- **Timelines**: ordered containers of timepoints for temporal relationships
- **Metacontexts**: epistemic frames that disambiguate fiction from fact, sources, perspectives
- **Petri nets**: all pipelines are executable, typed, visualizable Petri nets via [Petritype](../petritype)
- **Immutable history**: nodes are never mutated — updates create new versions with history edges

## Project Structure

```
epimemer/
  core/           — Pydantic models (node types, edges, timelines, metacontexts)
  storage/        — Storage protocol + InMemory + SurrealDB adapters
  embeddings/     — Embedding protocol + sentence-transformers + mock
  llm/            — LLM protocol + Pydantic AI + mock
  pipelines/
    segmentation/     — Paragraph split, semantic similarity
    decomposition/    — LLM extraction, topic assignment
    graph_construction/ — Edge creation, value updates, versioning, persistence
    query/            — Vector search, graph expansion, hybrid retrieval
    reflection/       — Topic consolidation, value decay, contradiction detection, archival
    timeline/         — Pure functional timeline operations
    orchestration/    — Top-level request routing Petri net
  mcp/            — FastMCP server, tool implementations, config
  logging/        — Structured JSON logging
tests/            — 283 tests (unit, pipeline, MCP, integration)
```

## Documentation

- [SUMMARY.md](SUMMARY.md) — Architectural design
- [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) — Phased implementation plan
- [INTEGRATION.md](INTEGRATION.md) — Claude Code integration guide
- [DEVELOPER_GUIDE.md](DEVELOPER_GUIDE.md) — Development and debugging guide
