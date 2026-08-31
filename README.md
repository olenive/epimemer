# Epimemer

## Motivation
LLMs store general knowledge in their weights and specific or episodic
facts, inferences or background information in their context. However,
as specific details accumulate over time, they eventually exhaust the
available context window.
Epimemer is a tool for context engineering with the goal of providing
necessary information without flooding the context window with details
that are irrelevant to the task at hand.

## Outline
An epistemic memory server for AI agents, speaking the
[Model Context Protocol](https://modelcontextprotocol.io). The agent hands
over what it has read, and Epimemer keeps it as a typed graph of **topics**,
**facts** and **inferences** that remembers where each claim came from, which world it is
about, when it held, what contradicts it, and who decided what. It then
nominates the graph's own weak points for the agent to review.

Epimemer performs no extraction of its own. Reading a document and deciding
what it claims is the calling agent's job; Epimemer's job is to hold those
claims honestly and to keep asking whether they still stand.

- **Provenance, not strings.** Every fact carries `sourced_from` edges to the
  documents that assert it, and a merge keeps one per contributing source.
- **Frames.** A claim states which world it is about — the real one, a novel, a
  named source, a perspective — so fiction never corroborates fact and two
  perspectives can disagree without either being wrong.
- **Validity in time.** Each source records when it says a claim held, so a
  fact read in 1997 and one read in 2024 can be the same condition with two
  periods, or two events that must never merge.
- **A review loop.** `reflect` nominates near-duplicates, contradictions, stale
  evidence and never-retrieved nodes; `apply_reflection` records the agent's
  verdict on each, and a verdict once recorded is never asked again.
- **A decision journal.** Every judgment names the judge that made it. `review`
  reads them back shakiest first, so a different agent — or a person — can
  check what an earlier one decided.

Python 3.14+. MIT licensed.

## Install

```bash
uv tool install "epimemer[sentence-transformers]"
# or: pip install "epimemer[sentence-transformers]"
```

| Extra | Adds | When you need it |
|-------|------|------------------|
| `sentence-transformers` | Local embeddings via [sentence-transformers](https://www.sbert.net) (pulls in PyTorch) | The default embedding provider. Without it, set `EPIMEMER_EMBEDDING_PROVIDER` to another provider, or the server refuses to start and says which extra to install |
| `notebooks` | [marimo](https://marimo.io) and the Petri-net plotting stack | Only for the walkthrough notebooks in the repository |

The embedding model (`all-MiniLM-L6-v2`, ~80 MB) downloads on first run.

## Connect to Claude Code

```bash
claude mcp add epimemer -- epimemer serve
```

Then run `/mcp` inside Claude Code to confirm the server is listed. This uses
the defaults: local embeddings and **in-memory storage, lost when the server
exits** — see [Persistence](#persistence) for the setup that keeps a graph.

Any other MCP client works the same way: the server speaks stdio, and
`epimemer serve` is the command. The
[integration guide](https://github.com/olenive/epimemer/blob/main/INTEGRATION.md)
has the full configuration, the system-prompt guidance that tells an agent how
to use the tools well, and the canonical tool table.

## Persistence

Persistent storage is a [SurrealDB](https://surrealdb.com) server. **The
storage path is the whole difference between persistent and not**: `surreal
start` takes an optional `[PATH]` whose default is `memory`, so a server
started without one keeps the entire graph in RAM and loses it on restart,
with no error and no warning. Every command here passes an explicit
`rocksdb:` path.

```bash
# Docker — on disk, in a named volume that outlives the container.
# -u 0:0 because the image's non-root user cannot write the volume mount.
docker run -d --name surrealdb -p 8000:8000 \
  --restart unless-stopped -u 0:0 \
  -v surreal-data:/data \
  surrealdb/surrealdb:latest \
  start --user root --pass root rocksdb:/data/epimemer.db

# Or a native install — on disk, relative to the working directory
surreal start --user root --pass root rocksdb:epimemer.db
```

Then register the server with the backend named:

```bash
claude mcp add epimemer \
  -e EPIMEMER_STORAGE_BACKEND=surrealdb \
  -e EPIMEMER_SURREALDB_URL=ws://localhost:8000/rpc \
  -e EPIMEMER_GRAPH=default \
  -- epimemer serve
```

`root`/`root` are SurrealDB's local-development credentials; set
`EPIMEMER_SURREALDB_USER` and `EPIMEMER_SURREALDB_PASS` for anything else. Set
`EPIMEMER_GRAPH` per server: the active graph is process state, so a client
reconnect lands back on whatever the server opened.

## Configuration

All configuration is via `EPIMEMER_` environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `EPIMEMER_STORAGE_BACKEND` | `memory` | `memory` or `surrealdb` |
| `EPIMEMER_SURREALDB_URL` | `ws://localhost:8000/rpc` | SurrealDB connection URL |
| `EPIMEMER_SURREALDB_USER` | `root` | SurrealDB username |
| `EPIMEMER_SURREALDB_PASS` | `root` | SurrealDB password |
| `EPIMEMER_SURREALDB_NAMESPACE` | `epimemer` | SurrealDB namespace |
| `EPIMEMER_SURREALDB_DATABASE` | `default` | SurrealDB database name — one database per graph. This is a *name*, not a storage mode: whether storage is in-memory is decided by `EPIMEMER_STORAGE_BACKEND` here and by the `[PATH]` argument on the server |
| `EPIMEMER_GRAPH` | (empty) | The graph this server opens, overriding the database name above. **Set it per server.** The active graph is process state, so `use_graph` lasts only as long as the process and a client reconnect lands back here — see the [integration guide](https://github.com/olenive/epimemer/blob/main/INTEGRATION.md#which-graph-a-server-opens) |
| `EPIMEMER_EMBEDDING_PROVIDER` | `sentence-transformers` | `sentence-transformers` or `mock` |
| `EPIMEMER_EMBEDDING_MODEL_ID` | `all-MiniLM-L6-v2` | Embedding model name |
| `EPIMEMER_EMBEDDING_DIMENSION` | `384` | Embedding vector dimension |
| `EPIMEMER_SEGMENTATION_STRATEGY` | `paragraph` | `paragraph` or `semantic` |
| `EPIMEMER_SIMILARITY_THRESHOLD` | `0.75` | Similarity threshold for search |
| `EPIMEMER_REFLECT_THRESHOLD` | `10` | Server-wide default: stores in a graph before suggesting reflection (counted per graph, in storage; reported with the count by `graph_stats`, and overridable per graph via `configure_reflection`) |
| `EPIMEMER_RECORD_RETRIEVAL` | `true` | Whether `search` stamps `retrieved_at` on what it returns. `false` disables it, at the cost of making `never_retrieved` blind; ranking is never affected either way |
| `EPIMEMER_IMPORTANCE_STEP` | `0.25` | How much of the gap to its bound one `judge_importance` call closes, up or down. Nothing automatic moves it |
| `EPIMEMER_TOOL_TIMEOUT_SECONDS` | `30.0` | Timeout per tool operation |
| `EPIMEMER_APPROVED_AGENTS` | (empty) | Comma-separated agent ids the user admits as judges in every graph this server opens. Read when the backend connects and when the server lands on a graph. The approval channel for clients with no approval prompt of their own, and the only one that reaches an embedded store — see [ATTRIBUTION.md](https://github.com/olenive/epimemer/blob/main/docs/ATTRIBUTION.md) |
| `EPIMEMER_REQUIRE_JUDGE` | `false` | Refuse any write that names no judge, on every graph this server opens. Off by default: a blank judge means *unknown*, and many graphs have no reason to care. Overridable per graph with `epimemer agents require`, and deliberately not settable by any MCP tool |
| `EPIMEMER_VIZ_ENABLED` | `true` | Publish visualization events to the hub |
| `EPIMEMER_VIZ_HOST` | `127.0.0.1` | Visualization hub host |
| `EPIMEMER_VIZ_PORT` | `8765` | Visualization hub port |
| `EPIMEMER_VIZ_AUTOSPAWN` | `true` | Spawn a hub automatically if none is running |
| `EPIMEMER_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `EPIMEMER_LOG_FILE` | (stderr) | Path to log file |

## MCP Tools

Tools exposed via the Model Context Protocol (auto-prefixed as `mcp__epimemer__<name>` by Claude Code), grouped by purpose:

- **Core memory**: `segment`, `store_decomposition`, `search`, `link`, `update`, `supersede_by`, `judge_importance`
- **Discovery & stats**: `query_graph`, `topic_tree`, `find_nodes`, `list_sources`, `list_relations`, `describe_relation`, `graph_stats`
- **Conflict handling**: `check_conflicts`, `record_contradiction`, `record_variant`, `merge_facts`, `merge_inferences`, `reverse_merge`, `configure_merge`, `configure_warnings`
- **Reflection**: `reflect`, `configure_reflection`, `apply_reflection`
- **Temporal access**: `graph_as_of`, `query_changes`
- **Archival**: `archive`, `restore`
- **Timelines**: `create_timeline`, `set_reference_time`, `add_timepoint`, `query_timeline`, `create_timelink`
- **Metacontexts**: `create_metacontext`, `get_metacontexts`
- **Graph management**: `list_graphs`, `use_graph`, `delete_graph`
- **Agents**: `claim_agent` — say which judge you are; the user picks it, and
  can rename it later without disturbing a single decision
- **Review**: `review` — the decisions this graph has recorded, shakiest first;
  `apply_review` — record that you checked one, and whether you agree;
  `rejudge` — revise a judgment made at ingest without touching the claim;
  `reframe` — withdraw a metacontext from a node, or move it to another in
  one call; `correct_interval` — replace what one source is recorded as
  asserting about when a claim held
- **Visualization**: `viz_status`

The [integration guide](https://github.com/olenive/epimemer/blob/main/INTEGRATION.md#available-tools)
has the canonical table with one-line descriptions and the authoritative tool count.

## Visualization

A browser dashboard showing the knowledge graph, pipeline execution and
timelines, live. It is a **standalone hub** that many MCP sessions publish to,
rather than a server embedded in each MCP process, so several agents can be
watched from one page.

- **The hub owns the port** (`EPIMEMER_VIZ_HOST:EPIMEMER_VIZ_PORT`, default
  `127.0.0.1:8765`). Each MCP process dials out to it and registers as a
  *session*; the browser picks a session from the header selector.
- **Auto-spawn**: the first MCP process with `EPIMEMER_VIZ_ENABLED=true` spawns
  a detached hub if none is running (disable with `EPIMEMER_VIZ_AUTOSPAWN=false`).
- **CLI**: `epimemer-viz [--status|--stop]` for explicit control.
- **`viz_status` tool**: ask through the very session you are driving — it
  returns the hub URL, whether the hub can see this session, and the
  `session_id` to pick in the selector. The durable answer to "I opened the
  visualizer but can't find my graph".
- **Activity log**: one entry per transaction — what the agent stored,
  corrected, world-changed, merged, archived or restored — filterable by verb,
  node id, text and time. Click an entry to highlight the nodes it acted on;
  click a node to filter the log to it.
- **Retrieval focus**: pick a recent tool call and everything it did *not*
  return desaturates. Dimmed nodes stay clickable — the interesting click is
  on one that did not come back — and the drawer's **Response** tab shows
  exactly what Epimemer returned.
- **Timeline**: one timeline at a time on a vertical axis, in *record time*
  (when the graph learned each node) or *content time* (when the described
  events happened). Large gaps collapse to a labelled break; vague timepoints
  sit in an *undated* tray rather than being given an invented date.

> **`EPIMEMER_VIZ_HOST` is a privacy setting as well as a network one.** On the
> default loopback bind the hub keeps whole retrieval records, so they survive
> the MCP process exiting. Point it at a non-loopback address and sessions
> mirror **structural metadata only** — no query text, no response payloads —
> and the payloads stay in the MCP process, reachable only while it is running.

## Administration

`epimemer agents list` shows a graph's approved judges, whether it requires
one, and what each has said about itself. `epimemer agents confirm <name>`
admits one, `epimemer agents rename <handle> <name>` renames one (add
`--same-judge` to consolidate two that are really one), and `epimemer agents
require on|off|default` decides whether writes to that graph must name one.
No MCP tool can perform these acts: they are deliberately reserved for a
person, because a tool the agent calls cannot prove that the user asked for
it.

`epimemer relations backfill` gives every relationship label already in use a
record, in one go. It is idempotent and never touches a label that has one.

All of these work only against a **served** SurrealDB. An embedded store
lives inside the server process, so a CLI writing to it would write to a
separate copy the running server never reads. For the two settings, use
`EPIMEMER_APPROVED_AGENTS` and `EPIMEMER_REQUIRE_JUDGE` instead; the command
refuses and names the right variable rather than appearing to succeed.

## Architecture

- **Dual-space**: vector embeddings as primary representation, typed graph derived on top
- **Three node types**: Topics (themes), Facts (atomic statements), Inferences (provisional derivations)
- **Timelines**: ordered containers of timepoints for temporal relationships
- **Metacontexts**: epistemic frames that disambiguate fiction from fact, sources, perspectives
- **Petri nets**: all pipelines are executable, typed, visualizable Petri nets via [Petritype](https://github.com/olenive/petritype)
- **Immutable history**: a node's *content* is never mutated — updates create new versions with history edges (lifecycle metadata like `status` and value signals is mutated in place)
- **Sources, tags, relations**: provenance and aboutness are nodes & edges (`sourced_from`, `tagged_with`), not strings; relationships are open-vocabulary user-labelled edges

## Documentation

- [SUMMARY.md](https://github.com/olenive/epimemer/blob/main/SUMMARY.md) — Architectural design: the concepts and their rationale
- [INTEGRATION.md](https://github.com/olenive/epimemer/blob/main/INTEGRATION.md) — Claude Code integration guide, system-prompt guidance and the canonical tool table
- [docs/RETRIEVAL.md](https://github.com/olenive/epimemer/blob/main/docs/RETRIEVAL.md) — How `search` is answered: the two arms, rank fusion, result provenance, lineage collapse
- [docs/VALIDITY.md](https://github.com/olenive/epimemer/blob/main/docs/VALIDITY.md) — When a claim was true: intervals per source, correction vs world-change, recurrence, the soundness check
- [docs/REFLECTION.md](https://github.com/olenive/epimemer/blob/main/docs/REFLECTION.md) — The review loop: verdicts, what `reflect` nominates, what `apply_reflection` writes
- [docs/ATTRIBUTION.md](https://github.com/olenive/epimemer/blob/main/docs/ATTRIBUTION.md) — Who judged this: the agent registry, why the user assigns the id, how approval reaches them, the append-only journal of every decision, and reading it back with `review` / `apply_review` / `rejudge`

## Contributing

Development setup, the test suites, the frontend build and where the design
history lives are in
[CONTRIBUTING.md](https://github.com/olenive/epimemer/blob/main/CONTRIBUTING.md).
Bugs and proposals go to the
[issue tracker](https://github.com/olenive/epimemer/issues).

## License

[MIT](https://github.com/olenive/epimemer/blob/main/LICENSE).
