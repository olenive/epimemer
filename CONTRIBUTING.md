# Contributing

## Setup

```bash
git clone https://github.com/olenive/epimemer.git
cd epimemer
uv sync --extra dev
```

Python 3.14+ and [uv](https://docs.astral.sh/uv/). The default suite needs
no external services and no embedding model: it runs against the in-memory
store and SurrealDB's embedded `mem://` engine, with a mock embedding provider.

The marimo notebooks under `notebooks/` need an extra, and rendering their
Petri-net diagrams also needs the Graphviz system binaries, which no Python
package can supply:

```bash
uv sync --extra dev --extra notebooks
brew install graphviz          # or your platform's equivalent
uv run marimo edit notebooks/01_segmentation.py
```

## Tests

```bash
make test          # or: uv run python -m pytest tests/ -q
```

Most storage and MCP tests run against **both** backends: a `conftest.py`
fixture parameterises over `InMemoryStorage` and `SurrealDBStorage("mem://")`.

Two things `mem://` cannot model — two real connections, and surviving a
restart — have their own **opt-in** suites, which skip themselves under a bare
`pytest`:

- `tests/storage/test_surrealdb_integration.py` — real `ws://` connection and
  cross-connection transaction atomicity, against an already-running server.
  Runs when `EPIMEMER_SURREAL_WS_URL` is set.
- `tests/storage/test_surrealdb_persistence.py` — rocksdb-backed data
  surviving a full server restart. Controls its own throwaway container; runs
  when `EPIMEMER_SURREAL_PERSIST_TEST=1`.

One target runs both, spinning up SurrealDB in Docker, waiting for it, and
tearing it down. If port 8000 is taken it stops before starting anything and
names the process holding it; re-run on another port:

```bash
make test-integration
make test-integration SURREAL_PORT=8123
```

### Frontend

The visualization frontend under `epimemer/visualization/frontend/` is
TypeScript, built with Vite and tested with vitest. `make test` stays
Python-only so Node is not a prerequisite for backend work.

```bash
make test-frontend      # npm run typecheck && npm test
cd epimemer/visualization/frontend && npm ci && npm run build
```

The build writes `epimemer/visualization/static/`, which is **not committed**:
the release workflow builds it into the wheel, and a checkout serves the hub's
API without the page until you build it. Rebuild after any change under
`frontend/src`.

### Before asking for a review

```bash
uv run python scripts/prose_drift.py
```

It finds prose that states how many of something the code enumerates — how
many reflect phases there are, how many lists are capped — which is the one
kind of documentation drift that has recurred here. Its own docstring says why it is a
lint you run and not a test: pinning the numbers would institutionalise the
duplication that causes them to go stale.

## Conventions

[AGENTS.md](AGENTS.md) holds the coding rules and is checked in so that they
are reviewed beside the code — functional style, Pydantic for data, no
singletons, every backend implementing the full storage protocol, and the
rule against citing issue numbers in code or prose. Read it before a first
change.

The MCP boundary has a guard: every parameter an implementation in
`epimemer/mcp/tools.py` accepts is either exposed on the matching tool in
`epimemer/mcp/server.py` or classified in
`tests/mcp/test_boundary_exposes_the_implementation.py` with the reason it is
the server's to supply. A new parameter fails that test until you do one or
the other.

## Layout

```
epimemer/
  core/           — Pydantic models (node types, edges, timelines, metacontexts)
  storage/        — Storage protocol + InMemory + SurrealDB adapters
  embeddings/     — Embedding protocol + sentence-transformers + mock
  pipelines/
    segmentation/     — Paragraph split, semantic similarity
    graph_construction/ — Edge creation, node versioning
    query/            — Vector search, graph expansion, hybrid retrieval
    reflection/       — Topic consolidation, contradiction detection, review, archival
    timeline/         — Pure functional timeline operations
    orchestration/    — Top-level request routing Petri net
  mcp/            — FastMCP server, tool implementations, config
  logging/        — Structured JSON logging
  visualization/  — Standalone viz hub, session client, and frontend
tests/            — unit, pipeline, MCP, integration
scripts/          — benchmarks, corpus measurement, prose lint, local SurrealDB
notebooks/        — marimo walkthroughs of each pipeline stage
```

## Where the design lives

**How the system works** is in the user documentation linked from the
[README](README.md#documentation). **How it got that way** — what was decided,
measured and deferred — is under `dev-docs/`:

- [DEVELOPER_GUIDE.md](dev-docs/DEVELOPER_GUIDE.md) — debugging each layer in
  isolation, timestamp comparison rules, adding a pipeline or a backend
- [ISSUES.md](dev-docs/ISSUES.md) — live issues only; resolved ones are removed
- [PROPOSED_FEATURES.md](dev-docs/PROPOSED_FEATURES.md) — work not yet built:
  what, why, rough cost, and what has to be true before it can start
- [BENCHMARKS.md](dev-docs/BENCHMARKS.md) — measured scaling limits and where
  they come from
- The remaining files are feature designs and the reasoning behind them

These documents shrink over time except where they describe current
architecture — they are not a changelog.

## Reporting

Bugs and proposals go to the
[issue tracker](https://github.com/olenive/epimemer/issues). For a security
concern, follow [SECURITY.md](SECURITY.md) rather than opening a public issue.
