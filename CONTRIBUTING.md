# Contributing

## Setup

```bash
git clone https://github.com/olenive/epimemer.git
cd epimemer
uv sync --extra dev
```

Python 3.14+ and [uv](https://docs.astral.sh/uv/). The default suite needs
no external services and no embedding model: it runs against the in-memory
store, SurrealDB's embedded `mem://` engine and SQLite files in a temporary
directory, with a mock embedding provider.

## Tests

```bash
make test          # or: uv run python -m pytest tests/ -q
```

Most storage and MCP tests run against **every** backend: a `conftest.py`
fixture parameterises over `InMemoryStorage`, `SurrealDBStorage("mem://")`,
`SQLiteStorage` and `PostgresStorage`. The Postgres parameter skips itself
unless `EPIMEMER_TEST_POSTGRES_DSN` names a database. Point it only at a
database you can throw away: every test drops every graph schema in it.

Two things `mem://` cannot model, two real connections and surviving a
restart, have their own **opt-in** suites, which skip themselves under a bare
`pytest`:

- `tests/storage/test_surrealdb_integration.py`: real `ws://` connection and
  cross-connection transaction atomicity, against an already-running server.
  Runs when `EPIMEMER_SURREAL_WS_URL` is set.
- `tests/storage/test_surrealdb_persistence.py`: rocksdb-backed data
  surviving a full server restart. Controls its own throwaway container; runs
  when `EPIMEMER_SURREAL_PERSIST_TEST=1`.

One target runs both, spinning up SurrealDB in Docker, waiting for it, and
tearing it down. If port 8000 is taken it stops before starting anything and
names the process holding it; re-run on another port:

```bash
make test-integration
make test-integration SURREAL_PORT=8123
```

The Postgres parameter has a target of its own, which starts a throwaway
`postgres:17-alpine` in Docker, runs the suite selected to Postgres and tears
the server down, with the same port check:

```bash
make test-postgres
make test-postgres PG_PORT=5434
```

A skipped parameter is reported as a pass, so check that the run says
`passed` with nothing skipped.

### Frontend

The visualization frontend under `epimemer/visualization/frontend/` is
TypeScript, built with Vite and tested with vitest. `make test` stays
Python-only so Node is not a prerequisite for backend work.

```bash
make test-frontend      # npm run typecheck && npm test
make build-frontend     # npm ci && npm run build
```

The build writes `epimemer/visualization/static/`, which is **not committed**:
the release workflow builds it into the wheel, and a checkout serves the hub's
API without the page until you build it. Rebuild after any change under
`frontend/src`.

### Before asking for a review

```bash
uv run python scripts/prose_drift.py
```

It finds prose that states how many of something the code enumerates (how
many reflect phases there are, how many lists are capped). It is a lint rather
than a test, because pinning the numbers in the suite would lock in the
duplication that makes them go stale.

### Before changing a dependency

```bash
uv run python scripts/audit_dependencies.py
```

It asks OSV whether anything installed here has a published advisory. A weekly
CI job runs the same command and files what it finds as an issue. It is the
only thing watching this project's Python dependencies: Dependabot's `pip`
ecosystem and GitHub's dependency graph do not read `uv.lock`. A finding names
a package and a severity, not whether this code reaches the affected path.
Updating is `uv lock --upgrade`, run the suite, one commit.

## Conventions

[AGENTS.md](AGENTS.md) holds the coding rules, checked in so that they are
reviewed beside the code. Read it before a first change.

The MCP boundary has a guard: every parameter an implementation in
`epimemer/mcp/tools.py` accepts is either exposed on the matching tool in
`epimemer/mcp/server.py` or classified in
`tests/mcp/test_boundary_exposes_the_implementation.py` with the reason it is
the server's to supply. A new parameter fails that test until you do one or
the other.

## Where the design lives

**How the system works** is in the user documentation linked from the
[README](README.md#documentation). **How each part is designed, and why**, is
under `dev-docs/`:

- [DEVELOPER_GUIDE.md](dev-docs/DEVELOPER_GUIDE.md): debugging each layer in
  isolation, timestamp comparison rules, adding a pipeline or a backend
- [ISSUES.md](dev-docs/ISSUES.md): open issues only; a resolved one is removed
- [PROPOSED_FEATURES.md](dev-docs/PROPOSED_FEATURES.md): work not yet built:
  what, why, rough cost, and what has to be true before it can start
- [BENCHMARKS.md](dev-docs/BENCHMARKS.md): measured scaling limits and where
  they come from
- [VIZ_CONTRACT.md](dev-docs/VIZ_CONTRACT.md): what a client of the
  visualisation hub may rely on, and when `VIZ_SCHEMA_VERSION` must change
- The remaining files are the design of one subsystem each: its decisions
  and the reasons that still bind

These documents describe the current design and are rewritten when it
changes. They are not a changelog: a resolved issue or a superseded plan is
deleted rather than kept for the record, and the git history is where the
record lives.

## Reporting

Bugs and proposals go to the
[issue tracker](https://github.com/olenive/epimemer/issues). For a security
concern, follow [SECURITY.md](SECURITY.md) rather than opening a public issue.
