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

The marimo notebooks under `notebooks/` need an extra, and rendering the graph
diagrams also needs the Graphviz system binaries, which no Python package can
supply:

```bash
uv sync --extra notebooks
brew install graphviz          # or your platform's equivalent
uv run marimo edit notebooks/01_segmentation.py
```

### Run tests

```bash
uv run python -m pytest tests/ -q
```

The notebook dependency check skips without that extra; everything else runs on
a plain `uv sync`.

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
./scripts/start_local_surrealdb.sh
```

That script is how this project starts SurrealDB, and it is the only copy of
those flags worth keeping. It starts Colima if it is not running, creates the
container on-disk if it does not exist, waits for the health endpoint, and
registers the MCP server with Claude Code. Safe to re-run.

**The storage path is the whole difference between persistent and not.**
`surreal start` takes an optional `[PATH]`, and its default is `memory` — so a
container started without one keeps the entire graph in RAM and loses it on
restart, with no error and no warning. Everything here that starts SurrealDB
for real passes an explicit `rocksdb:` path.

If you already have a container from an older version of these instructions,
check it:

```bash
docker inspect surrealdb --format '{{json .Config.Cmd}}'
```

The output must contain `rocksdb:`. If it reads only `["start","--user",
"root","--pass","root"]` the container is in-memory, and anything stored in it
is already gone on the next restart. To migrate:

```bash
docker rm -f surrealdb && ./scripts/start_local_surrealdb.sh
```

### Register Epimemer with SurrealDB

The script does this. To do it by hand:

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

# Restart later. The data lives in the `surreal-data` volume rather than in
# the container, so it survives both of these.
colima start --memory 1
docker start surrealdb
```

### SurrealDB without Colima

If you have Docker Desktop or a native SurrealDB install, the script does not
apply and you start it yourself. Note the explicit storage path in both — it is
what the script exists to get right:

```bash
# Native install — on disk, relative to the working directory
surreal start --user root --pass root rocksdb:epimemer.db

# Or Docker Desktop — on disk, in a named volume that outlives the container.
# -u 0:0 because the image's non-root user cannot write the volume mount.
docker run -d --name surrealdb -p 8000:8000 \
  --restart unless-stopped -u 0:0 \
  -v surreal-data:/data \
  surrealdb/surrealdb:latest \
  start --user root --pass root rocksdb:/data/epimemer.db
```

Then register the MCP server as above.

## Configuration

All configuration is via `EPIMEMER_` environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `EPIMEMER_STORAGE_BACKEND` | `memory` | `memory` or `surrealdb` |
| `EPIMEMER_SURREALDB_URL` | `ws://localhost:8000/rpc` | SurrealDB connection URL |
| `EPIMEMER_SURREALDB_USER` | `root` | SurrealDB username |
| `EPIMEMER_SURREALDB_PASS` | `root` | SurrealDB password |
| `EPIMEMER_SURREALDB_NAMESPACE` | `epimemer` | SurrealDB namespace |
| `EPIMEMER_SURREALDB_DATABASE` | `memory` | SurrealDB database name — one database per graph. This is a *name*, not a storage mode: the default is spelled `memory` but says nothing about where data lives. Whether storage is in-memory is decided by `EPIMEMER_STORAGE_BACKEND` here and by the `[PATH]` argument on the server |
| `EPIMEMER_GRAPH` | (empty) | Override database name for multi-graph |
| `EPIMEMER_EMBEDDING_PROVIDER` | `sentence-transformers` | `sentence-transformers` or `mock` |
| `EPIMEMER_EMBEDDING_MODEL_ID` | `all-MiniLM-L6-v2` | Embedding model name |
| `EPIMEMER_EMBEDDING_DIMENSION` | `384` | Embedding vector dimension |
| `EPIMEMER_SEGMENTATION_STRATEGY` | `paragraph` | `paragraph` or `semantic` |
| `EPIMEMER_SIMILARITY_THRESHOLD` | `0.75` | Similarity threshold for search |
| `EPIMEMER_REFLECT_THRESHOLD` | `10` | Server-wide default: stores in a graph before suggesting reflection (counted per graph, in storage; reported with the count by `graph_stats`, and overridable per graph via `configure_reflection`) |
| `EPIMEMER_RECORD_RETRIEVAL` | `true` | Whether `search` stamps `retrieved_at` on what it returns. `false` disables it, at the cost of making `never_retrieved` blind; ranking is never affected either way |
| `EPIMEMER_IMPORTANCE_STEP` | `0.25` | How much of the gap to its bound one `judge_importance` call closes, up or down. Nothing automatic moves it |
| `EPIMEMER_TOOL_TIMEOUT_SECONDS` | `30.0` | Timeout per tool operation |
| `EPIMEMER_APPROVED_AGENTS` | (empty) | Comma-separated agent ids the user admits as judges in every graph this server opens. Read when the backend connects and when the server lands on a graph. The approval channel for clients that cannot elicit, and the only one that reaches an embedded store — see [docs/ATTRIBUTION.md](docs/ATTRIBUTION.md) |
| `EPIMEMER_VIZ_ENABLED` | `true` | Publish visualization events to the hub |
| `EPIMEMER_VIZ_HOST` | `127.0.0.1` | Visualization hub host |
| `EPIMEMER_VIZ_PORT` | `8765` | Visualization hub port |
| `EPIMEMER_VIZ_AUTOSPAWN` | `true` | Spawn a hub automatically if none is running |
| `EPIMEMER_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `EPIMEMER_LOG_FILE` | (stderr) | Path to log file |

## MCP Tools

Tools exposed via the Model Context Protocol (auto-prefixed as `mcp__epimemer__<name>` by Claude Code), grouped by purpose:

- **Core memory**: `segment`, `store_decomposition`, `search`, `link`, `update`, `supersede_by`, `judge_importance`
- **Discovery & stats**: `query_graph`, `topic_tree`, `find_nodes`, `list_sources`, `list_relations`, `graph_stats`
- **Conflict handling**: `check_conflicts`, `record_contradiction`, `record_variant`, `merge_facts`, `reverse_merge`, `configure_merge`
- **Reflection**: `reflect`, `configure_reflection`, `apply_reflection`
- **Temporal access**: `graph_as_of`, `query_changes`
- **Archival**: `archive`, `restore`
- **Timelines**: `create_timeline`, `set_reference_time`, `add_timepoint`, `query_timeline`, `create_timelink`
- **Metacontexts**: `create_metacontext`, `get_metacontexts`
- **Graph management**: `list_graphs`, `use_graph`, `delete_graph`
- **Agents**: `claim_agent` — say which judge you are; the user assigns the id
- **Visualization**: `viz_status`

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
    reflection/       — Topic consolidation, contradiction detection, review, archival
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
- **Activity log** (the header's *Log* button): one entry per transaction — what
  the agent stored, corrected, world-changed, merged, archived or restored —
  filterable by verb, node id, text and time. Click an entry to highlight the
  nodes it acted on; click a node to filter the log to it.
- **Retrieval focus** (the header's *Retrieval* selector): pick a recent tool
  call and everything it did *not* return desaturates, in both panels. Dimmed
  nodes stay clickable — the interesting click is on one that did not come back
  — and the drawer's **Response** tab shows exactly what epimemer returned.

> **`EPIMEMER_VIZ_HOST` is a privacy setting as well as a network one.** On the
> default loopback bind the hub keeps whole retrieval records, so they survive
> the MCP process exiting. Point it at a non-loopback address and sessions
> mirror **structural metadata only** — no query text, no response payloads —
> and the payloads stay in the MCP process, reachable only while it is running.

### Light and dark mode

A toggle sits at the top right of the header. The choice persists in
`localStorage`; with nothing stored the page follows the OS and keeps following
it live, so a system change mid-session is picked up. An inline script in the
document head applies the theme before first paint, so a dark-mode user never
sees a white flash while the bundle loads.

Page chrome uses Tailwind's `dark:` variants against a `dark` class on `<html>`.
The three *drawn* surfaces — the cytoscape canvas, the timeline SVG and the
graphviz Petri nets — cannot be reached by CSS variants, so they read a palette
from `theme.ts` at render time and are repainted on toggle. `theme.ts` holds two
palettes: the neutrals, and a **shared semantic palette** — the hues that say
what kind of thing something is. Both vary by theme, and both panels read the
same one, so a fact is the same colour wherever it is drawn.

### Panels

- **Knowledge graph** — nodes and edges, force or hierarchy layout.
- **Pipelines** — Petri net execution, live.
- **Timeline** — one timeline at a time on a vertical axis, past at the top,
  read like a chat log. Facts and topics sit to the left of the line,
  inferences to the right; selecting a mark expands its text in place. Two
  modes:
  - *record time*, the default: when the graph learned each node, drawn from
    `created_at` out to `retrieved_at`, or a point when that is null.
  - *content time*: `Timeline`/`Timepoint` data — when the described events
    happened. Ingestion proposes timepoints from dates stated in the text, so
    this is populated by default; `create_timeline`, `add_timepoint` and
    `create_timelink` are how an agent curates one deliberately.

  Where the data has a gap far larger than its local spacing — a graph idle
  for days between bursts, or a timeline jumping centuries — the axis **breaks**
  and collapses the gap to a labelled marker, so dense clusters stay legible.
  Zooming into a cluster dissolves the break. Vague timepoints ("during the
  Renaissance") have no coordinate and sit in an *undated* tray beside the axis
  rather than being given an invented date.

  Filter by linked node type, status, epistemic frame, date range, or free text.
  Text supports `field:value` (`source:BBC`, `mc:fiction`, `type:fact`) and
  quoted phrases; an unrecognised prefix is treated as literal text, so `12:30`
  searches for a time rather than a field called `12`.

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

Two things `mem://` cannot model — two real connections, and surviving a
restart — have their own **opt-in** suites:

- `tests/storage/test_surrealdb_integration.py` — real ws:// connection/auth and
  cross-connection transaction atomicity, against an already-running server.
  Skips itself when `EPIMEMER_SURREAL_WS_URL` is unset.
- `tests/storage/test_surrealdb_persistence.py` — rocksdb-backed data surviving a
  full server restart. Controls its own throwaway container, so it skips unless
  `EPIMEMER_SURREAL_PERSIST_TEST=1`.

Neither runs — nor signals that it exists — under a bare `pytest`. One target
runs both, spinning up SurrealDB, waiting for it, and tearing it down:

```bash
make test-integration
```

**If port 8000 is taken**, the target stops before starting anything and names
the process holding it. Re-run on another port:

```bash
make test-integration SURREAL_PORT=8123
```

That check is worth its keep. A process that *accepts* connections on the port
without answering — another Colima/Docker profile forwarding it, or a wedged
container — leaves Docker's publish silently unreachable, and the symptom is a
target that hangs before the first test rather than one that fails.

The visualization frontend has its own suite (vitest) covering the
event-reduction logic — pipeline run state, the WebSocket event router, the hub
API client — and the timeline panel's scale, break heuristic, filters and mark
construction, which are pure and DOM-free. The timeline panel's own rendering
and interaction are covered under jsdom; the cytoscape graph panel is covered by
`tsc` rather than unit tests. `make test` stays Python-only, so Node is not a
prerequisite for backend work:

```bash
make test-frontend      # npm run typecheck && npm test, in the frontend directory
```

## Administration

`uv run epimemer agents list` shows a graph's approved judge ids and what each
agent has said about itself. `uv run epimemer agents confirm <id>` admits an id
— the act no MCP tool may perform, since a tool the agent calls cannot establish
that the *user* called it. It works only against a **served** SurrealDB: an
embedded store lives inside the server process, so approving there would write
into a store the running server never reads. Use `EPIMEMER_APPROVED_AGENTS`
instead in that case; the command says so rather than appearing to succeed.

## Not yet built

Proposed work — what it is, why, roughly what it costs, and what has to be true
before it can start — lives in
[dev-docs/PROPOSED_FEATURES.md](dev-docs/PROPOSED_FEATURES.md). Known bugs and
deferred fixes are separate, in [dev-docs/ISSUES.md](dev-docs/ISSUES.md).

## Documentation

**How the system works** — read these to understand the behaviour:

- [SUMMARY.md](SUMMARY.md) — Architectural design: the concepts and their rationale
- [docs/RETRIEVAL.md](docs/RETRIEVAL.md) — How `search` is answered: the two arms, rank fusion, result provenance, lineage collapse
- [docs/VALIDITY.md](docs/VALIDITY.md) — When a claim was true: intervals per source, correction vs world-change, recurrence, the soundness check
- [docs/REFLECTION.md](docs/REFLECTION.md) — The review loop: verdicts, what `reflect` nominates, what `apply_reflection` writes
- [docs/ATTRIBUTION.md](docs/ATTRIBUTION.md) — Who judged this: the agent registry, why the user assigns the id, how approval reaches them
- [INTEGRATION.md](INTEGRATION.md) — Claude Code integration guide and the canonical tool table

**How it got that way** — design history, appended to rather than rewritten:

- [dev-docs/ISSUES.md](dev-docs/ISSUES.md) — Known issues and deferred fixes
- [dev-docs/PROPOSED_FEATURES.md](dev-docs/PROPOSED_FEATURES.md) — Backlog of work not yet built
- [dev-docs/DEVELOPER_GUIDE.md](dev-docs/DEVELOPER_GUIDE.md) — Development and debugging guide
- [dev-docs/BENCHMARKS.md](dev-docs/BENCHMARKS.md) — Measured scaling limits and where they come from
- [dev-docs/REVIEW_EPISTEMIC.md](dev-docs/REVIEW_EPISTEMIC.md) — The review loop's design, including the validity model (§13)
- [dev-docs/LEXICAL_SEARCH.md](dev-docs/LEXICAL_SEARCH.md), [dev-docs/RETRIEVAL_PROVENANCE.md](dev-docs/RETRIEVAL_PROVENANCE.md), [dev-docs/EVENT_LOG.md](dev-docs/EVENT_LOG.md) — Feature designs
- [dev-docs/WARNINGS_AND_SETTINGS.md](dev-docs/WARNINGS_AND_SETTINGS.md) — Advisories, per-graph warning policy, node notes, inference merge (designed, not built)
- [dev-docs/VISUALISATION.md](dev-docs/VISUALISATION.md), [dev-docs/TIMELINE_VISUALISATION.md](dev-docs/TIMELINE_VISUALISATION.md) — Dashboard design
