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
| `EPIMEMER_REFLECT_THRESHOLD` | `10` | Stores in a graph before suggesting reflection (counted per graph, in storage; the count and the threshold are both reported by `graph_stats`) |
| `EPIMEMER_TOOL_TIMEOUT_SECONDS` | `30.0` | Timeout per tool operation |
| `EPIMEMER_VIZ_ENABLED` | `true` | Publish visualization events to the hub |
| `EPIMEMER_VIZ_HOST` | `127.0.0.1` | Visualization hub host |
| `EPIMEMER_VIZ_PORT` | `8765` | Visualization hub port |
| `EPIMEMER_VIZ_AUTOSPAWN` | `true` | Spawn a hub automatically if none is running |
| `EPIMEMER_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `EPIMEMER_LOG_FILE` | (stderr) | Path to log file |

## MCP Tools

Tools exposed via the Model Context Protocol (auto-prefixed as `mcp__epimemer__<name>` by Claude Code), grouped by purpose:

- **Core memory**: `segment`, `store_decomposition`, `search`, `link`, `update`, `supersede_by`
- **Discovery & stats**: `query_graph`, `topic_tree`, `find_nodes`, `list_sources`, `list_relations`, `graph_stats`
- **Conflict handling**: `check_conflicts`, `record_contradiction`, `record_variant`
- **Reflection**: `reflect`, `apply_reflection`
- **Temporal access**: `as_of`, `query_changes`
- **Archival**: `archive`, `restore`
- **Timelines**: `create_timeline`, `add_timepoint`, `query_timeline`, `create_timelink`
- **Metacontexts**: `create_metacontext`, `get_metacontexts`
- **Graph management**: `list_graphs`, `use_graph`, `delete_graph`

See [INTEGRATION.md](INTEGRATION.md#available-tools) for the canonical table with one-line descriptions and the authoritative tool count.

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
    graph_construction/ — Edge creation, node versioning
    query/            — Vector search, graph expansion, hybrid retrieval
    reflection/       — Topic consolidation, value decay, contradiction detection, archival
    timeline/         — Pure functional timeline operations
    orchestration/    — Top-level request routing Petri net
  mcp/            — FastMCP server, tool implementations, config
  logging/        — Structured JSON logging
  visualization/ — Standalone viz hub, session client, and frontend
tests/            — unit, pipeline, MCP, integration
```

## Visualization

The visualizer is a **standalone hub** that many MCP sessions publish to, rather
than an HTTP server embedded in each MCP process. This resolves the port-contention
failure where a stale MCP orphan would hold the port and serve the wrong (empty)
graph.

- **The hub owns the port** (`EPIMEMER_VIZ_HOST:EPIMEMER_VIZ_PORT`, default
  `127.0.0.1:8765`). Each MCP process dials out to it and registers as a *session*;
  the browser picks a session from the header selector.
- **Auto-spawn**: the first MCP process with `EPIMEMER_VIZ_ENABLED=true` spawns a
  detached hub if none is running (disable with `EPIMEMER_VIZ_AUTOSPAWN=false`).
- **CLI**: `uv run epimemer-viz [--status|--stop]` for explicit control.
- **`viz_status` tool**: ask through the very session you are driving — it returns
  the hub URL, whether the hub can see this session, and the `session_id` to pick
  in the selector. The durable answer to "I opened the visualizer but can't find my
  graph".

**Migrating from the embedded server:** old MCP processes running pre-hub code may
still hold `:8765` with the old embedded server. Run `pkill -f epimemer.mcp.server`
once (or `uv run epimemer-viz --status` to see what holds the port), then reconnect.

## Testing

```bash
# Default suite — embedded, no external services
make test          # or: uv run python -m pytest tests/ -q
```

Most storage and MCP tests run against **both** backends (a `conftest.py` fixture
parameterizes over `InMemoryStorage` and `SurrealDBStorage("mem://")`).

The embedded `mem://` backend cannot model two real connections, so there is a
separate **opt-in** suite (`tests/storage/test_surrealdb_integration.py`) for
real ws:// connection/auth and cross-connection transaction atomicity. It
**skips itself** when `EPIMEMER_SURREAL_WS_URL` is unset — so a bare `pytest`
never runs it and never signals that it exists. Run it via Docker:

```bash
make test-integration   # spins up SurrealDB, waits for it, runs the suite, tears it down
```

The visualization frontend has its own suite (vitest, no browser or DOM
required) covering the event-reduction logic — pipeline run state, the
WebSocket event router, and the hub API client. Rendering modules are covered by
`tsc` rather than unit tests. `make test` stays Python-only, so Node is not a
prerequisite for backend work:

```bash
make test-frontend      # npm run typecheck && npm test, in the frontend directory
```

## Not yet built

Designed but unimplemented, in rough priority order. Known bugs and deferred
fixes live in [ISSUES.md](ISSUES.md) instead.

- **Specialized timelines.** Only the base `Timeline`/`Timepoint` exists.
  `PreciseTimeline` (datetime interval index for range and proximity queries),
  `VagueTimeline` (labelled points with relative before/after ordering), and
  `CyclicalTimeline` (templates like "every Monday", mapped to concrete
  instances on link) are described in SUMMARY.md → *Timelines* → *Multiple
  Implementations*. Each needs add/remove/reorder with stable UUIDs, proximity
  search, overlap detection, and storage round-trip.
- **Benchmarking.** No benchmark module. Structured logging gives per-tool
  latency, but there is nothing measuring write throughput, vector-search
  latency at N vectors, embedding throughput, reflect cost by graph size, or
  end-to-end ingest/query/reflect.
- **Notebooks.** `notebooks/00_foundation.py` (storage + vector search + type
  diagrams), `07_timelines_metacontext.py`, and `08_orchestration.py` are
  missing.
- **LLM-guided and hybrid segmentation.** Both need an LLM; the server makes no
  LLM calls of its own (SUMMARY.md → *Epimemer makes no LLM calls*), so this
  means either delegating the split to the calling agent or re-introducing a
  provider abstraction. Paragraph and semantic-similarity segmentation cover
  the current use cases.
- **Merge is Topic-only on the wired path.** `merge_nodes` is type-agnostic but
  `apply_reflection merges` accepts Topics only; extending to Facts and
  Inferences is undecided (Inferences are meant to let competing derivations
  coexist).

## Documentation

- [SUMMARY.md](SUMMARY.md) — Architectural design
- [ISSUES.md](ISSUES.md) — Known issues and deferred fixes
- [INTEGRATION.md](INTEGRATION.md) — Claude Code integration guide
- [DEVELOPER_GUIDE.md](DEVELOPER_GUIDE.md) — Development and debugging guide
