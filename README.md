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

### Start the MCP server (in-memory, no persistence)

```bash
uv run python -m epimemer.mcp.server
```

This starts on stdio — designed to be called by Claude Code or another MCP client. Data is stored in memory and lost when the server exits.

### Connect to Claude Code

```bash
claude mcp add epimemer -- uv run --directory /path/to/epimemer python -m epimemer.mcp.server
```

Then verify with `/mcp` in Claude Code. See [INTEGRATION.md](INTEGRATION.md) for full configuration options and system prompt guidance.

## Persistent Setup (SurrealDB via Colima)

For persistent storage across sessions, run SurrealDB in a container via [Colima](https://github.com/abiosoft/colima).

### Prerequisites

- [Colima](https://github.com/abiosoft/colima): `brew install colima`
- [Docker CLI](https://docs.docker.com/engine/install/): `brew install docker`

### Start SurrealDB

```bash
# Start the container runtime (1GB is plenty for SurrealDB)
colima start --memory 1

# Run SurrealDB
docker run -d --name surrealdb -p 8000:8000 \
  surrealdb/surrealdb:latest start --user root --pass root
```

### Register Epimemer with SurrealDB

```bash
claude mcp add epimemer \
  -e EPIMEMER_STORAGE_BACKEND=surrealdb \
  -e EPIMEMER_SURREALDB_URL=ws://localhost:8000/rpc \
  -e EPIMEMER_LOG_FILE=/tmp/epimemer.log \
  -- uv run --directory /path/to/epimemer python -m epimemer.mcp.server
```

### Managing the container runtime

```bash
# Stop colima when not needed (frees RAM)
colima stop

# Restart later — the SurrealDB container and its data persist
colima start --memory 1
docker start surrealdb
```

### SurrealDB without Colima

If you have Docker Desktop or a native SurrealDB install:

```bash
# Native install
surreal start --user root --pass root file:epimemer.db

# Or Docker Desktop
docker run -d --name surrealdb -p 8000:8000 \
  surrealdb/surrealdb:latest start --user root --pass root
```

## Configuration

All configuration is via `EPIMEMER_` environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `EPIMEMER_STORAGE_BACKEND` | `memory` | `memory` or `surrealdb` |
| `EPIMEMER_SURREALDB_URL` | `ws://localhost:8000/rpc` | SurrealDB connection URL |
| `EPIMEMER_SURREALDB_USER` | `root` | SurrealDB username |
| `EPIMEMER_SURREALDB_PASS` | `root` | SurrealDB password |
| `EPIMEMER_SURREALDB_NAMESPACE` | `epimemer` | SurrealDB namespace |
| `EPIMEMER_SURREALDB_DATABASE` | `memory` | SurrealDB database name |
| `EPIMEMER_GRAPH` | (empty) | Override database name for multi-graph |
| `EPIMEMER_EMBEDDING_PROVIDER` | `sentence-transformers` | `sentence-transformers` or `mock` |
| `EPIMEMER_EMBEDDING_MODEL_ID` | `all-MiniLM-L6-v2` | Embedding model name |
| `EPIMEMER_EMBEDDING_DIMENSION` | `384` | Embedding vector dimension |
| `EPIMEMER_SEGMENTATION_STRATEGY` | `paragraph` | `paragraph` or `semantic` |
| `EPIMEMER_SIMILARITY_THRESHOLD` | `0.75` | Similarity threshold for search |
| `EPIMEMER_REFLECT_THRESHOLD` | `10` | Stores before suggesting reflection |
| `EPIMEMER_TOOL_TIMEOUT_SECONDS` | `30.0` | Timeout per tool operation |
| `EPIMEMER_VIZ_ENABLED` | `true` | Enable visualization server |
| `EPIMEMER_VIZ_HOST` | `127.0.0.1` | Visualization server host |
| `EPIMEMER_VIZ_PORT` | `8765` | Visualization server port |
| `EPIMEMER_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `EPIMEMER_LOG_FILE` | (stderr) | Path to log file |

## MCP Tools

Tools exposed via the Model Context Protocol (auto-prefixed as `mcp__epimemer__<name>` by Claude Code):

| Tool | Purpose |
|------|---------|
| `segment` | Segment text into chunks (step 1 of ingest) |
| `store_decomposition` | Store agent-extracted topics/facts/inferences (step 2 of ingest) |
| `search` | Hybrid retrieval (vector + graph), metacontext-aware |
| `link` | Create typed edges between nodes |
| `update` | Create new node version (immutable history) |
| `reflect` | Analyse graph for consolidation opportunities (embedding-based) |
| `apply_reflection` | Apply agent decisions from reflection analysis |
| `query_graph` | Traverse the graph from a node |
| `archive` | Export old superseded nodes to cold storage |
| `restore` | Reimport archived nodes |
| `create_timeline` | Create a named timeline |
| `add_timepoint` | Add a concrete or vague timepoint to a timeline |
| `query_timeline` | Find nearest timepoints or query a time range |
| `create_timelink` | Link a node to a timepoint on a timeline |
| `create_metacontext` | Create an epistemic frame for disambiguation |
| `get_metacontexts` | Get metacontexts for a node |
| `list_graphs` | List available knowledge graphs |
| `use_graph` | Switch to or create a knowledge graph |
| `delete_graph` | Delete a knowledge graph permanently |

## Architecture

See [SUMMARY.md](SUMMARY.md) for the full design. Key concepts:

- **Dual-space**: vector embeddings as primary representation, typed graph derived on top
- **Three node types**: Topics (themes), Facts (atomic statements), Inferences (provisional derivations)
- **Timelines**: ordered containers of timepoints for temporal relationships
- **Metacontexts**: epistemic frames that disambiguate fiction from fact, sources, perspectives
- **Petri nets**: all pipelines are executable, typed, visualizable Petri nets via [Petritype](../petritype)
- **Immutable history**: a node's *content* is never mutated — updates create new versions with history edges (lifecycle metadata like `status` and value signals is mutated in place; see SUMMARY.md → Node History)
- **Sources, tags, relations**: provenance and aboutness are nodes & edges (`sourced_from`, `tagged_with`), not strings; relationships are open-vocabulary user-labelled edges

## Project Structure

```
epimemer/
  core/           — Pydantic models (node types, edges, timelines, metacontexts)
  storage/        — Storage protocol + InMemory + SurrealDB adapters
  embeddings/     — Embedding protocol + sentence-transformers + mock
  pipelines/
    segmentation/     — Paragraph split, semantic similarity
    graph_construction/ — Edge creation, value updates, versioning, persistence
    query/            — Vector search, graph expansion, hybrid retrieval
    reflection/       — Topic consolidation, value decay, contradiction detection, archival
    timeline/         — Pure functional timeline operations
    orchestration/    — Top-level request routing Petri net
  mcp/            — FastMCP server, tool implementations, config
  logging/        — Structured JSON logging
  visualization/ — Real-time WebSocket visualization server and frontend
tests/            — 283 tests (unit, pipeline, MCP, integration)
```

## Documentation

- [SUMMARY.md](SUMMARY.md) — Architectural design
- [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) — Phased implementation plan
- [INTEGRATION.md](INTEGRATION.md) — Claude Code integration guide
- [DEVELOPER_GUIDE.md](DEVELOPER_GUIDE.md) — Development and debugging guide
