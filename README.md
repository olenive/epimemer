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
Epimemer is a memory server for AI agents, speaking the
[Model Context Protocol](https://modelcontextprotocol.io). The agent hands
over what it has read, and Epimemer stores it as a typed graph of **topics**,
**facts** and **inferences**. For each claim the graph records where it came
from, which world it is about, when it held, what contradicts it, and who
decided what. It then points the agent at the graph's own weak spots for
review.

Epimemer does no extraction of its own. Reading a document and deciding what
it claims is the agent's job. Epimemer's job is to store those claims with
their evidence and to keep asking whether they still stand.

- **Provenance as structure.** Every fact carries `sourced_from` edges to the
  documents that assert it, and a merge keeps one edge per contributing
  source.
- **Metacontexts.** A claim states which world it is about: the real one, a novel, a
  named source, a perspective. Fiction never corroborates fact, and two
  perspectives can disagree without either being wrong.
- **Validity in time.** Each source records when it says a claim held, so a
  fact read in 1997 and one read in 2024 can be one condition with two
  periods, or two events that must never merge.
- **Timelines.** Points on a timeline carry the dates their sources gave them.
  A source can also state an order between two points nobody dated, which
  gives an undated point a position between the dated ones. When the stated
  orders cannot all hold, the graph records the disagreement and asks a judge
  rather than refusing the write. A rule such as "the second Tuesday of every
  month" is stored once and its occurrences are computed on demand. See
  [docs/TIMELINES.md](https://github.com/olenive/epimemer/blob/main/docs/TIMELINES.md).
- **A review loop.** `reflect` nominates near-duplicates, contradictions,
  stale evidence and never-retrieved nodes. `apply_reflection` records the
  agent's verdict on each, and a recorded verdict settles the question until
  `reopen` puts it back.
- **A decision journal.** Every judgment names the judge that made it.
  `review` reads the journal back, least certain first, so a different agent
  or a person can check what an earlier one decided.

Python 3.14+. MIT licensed.

## Install

```bash
uv tool install "epimemer[sentence-transformers]"
# or: pip install "epimemer[sentence-transformers]"
```

| Extra | Adds | When you need it |
|-------|------|------------------|
| `sentence-transformers` | Local embeddings via [sentence-transformers](https://www.sbert.net) (pulls in PyTorch) | Always, for real use: it is the only embedding provider apart from `mock`, which exists for tests. Without it the server refuses to start and names the extra to install |
| `postgres` | The Postgres driver, `psycopg` with its compiled libpq | With `EPIMEMER_STORAGE_BACKEND=postgres`. Without it the server refuses to start and names the extra |
| `gcs` | `gcsfs` | Backups to a `gs://` URL |
| `s3` | `s3fs` | Backups to an `s3://` URL |

Extras combine in one install, for example
`uv tool install "epimemer[sentence-transformers,postgres]"`.

The embedding model (`all-MiniLM-L6-v2`, about 80 MB) downloads on first run.

## Connect to Claude Code

```bash
claude mcp add epimemer -- epimemer serve
```

Then run `/mcp` inside Claude Code to confirm the server is listed. This uses
the defaults: local embeddings and **in-memory storage, lost when the server
exits**. See [Persistence](#persistence) for the setup that keeps a graph.

Any other MCP client works the same way: the server speaks stdio, and
`epimemer serve` is the command. The
[integration guide](https://github.com/olenive/epimemer/blob/main/INTEGRATION.md)
has the full configuration, the canonical tool table, and how the server hands
the agent its guidance, so nothing is pasted into an agent's instructions.

## Persistence

Persistent storage is a [SurrealDB](https://surrealdb.com) server, a
SQLite file per graph with nothing to run, or a Postgres database for a graph
several people share (both below). **The
storage path is the whole difference between persistent and not.** `surreal
start` takes an optional `[PATH]` whose default is `memory`, so a server
started without one keeps the entire graph in RAM and loses it on restart,
with no error and no warning. Every command here passes an explicit
`rocksdb:` path.

```bash
# Docker: on disk, in a named volume that outlives the container.
# -u 0:0 because the image's non-root user cannot write the volume mount.
docker run -d --name surrealdb -p 8000:8000 \
  --restart unless-stopped -u 0:0 \
  -v surreal-data:/data \
  surrealdb/surrealdb:latest \
  start --user root --pass root rocksdb:/data/epimemer.db

# Or a native install: on disk, relative to the working directory
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
`EPIMEMER_SURREALDB_USER` and `EPIMEMER_SURREALDB_PASS` for anything else.

### SQLite: one file per graph, no server

The same graph can live in a SQLite file instead, with nothing to run:

```bash
claude mcp add epimemer \
  -e EPIMEMER_STORAGE_BACKEND=sqlite \
  -e EPIMEMER_GRAPH=default \
  -- epimemer serve
```

Each graph is `~/.epimemer/graphs/<graph>.sqlite`, created on first use; set
`EPIMEMER_SQLITE_DIR` to keep them elsewhere. The driver is Python's own
`sqlite3` module, so there is nothing extra to install, and the file can be
read and written from a second process while the server runs, so the
`epimemer agents` commands work against it. Do not delete a graph that another
server still has open: the other server keeps writing to the unlinked file and
loses everything when it closes. An existing graph moves over with
`epimemer graphs export` and `epimemer graphs import`.

### Postgres: a graph shared by a team

A graph can also live in a Postgres database you already run:

```bash
uv tool install "epimemer[sentence-transformers,postgres]"

claude mcp add epimemer \
  -e EPIMEMER_STORAGE_BACKEND=postgres \
  -e EPIMEMER_POSTGRES_DSN=postgresql://epimemer:secret@db.example.org:5432/epimemer \
  -e EPIMEMER_GRAPH=team \
  -- epimemer serve
```

Shared means several Epimemer servers, on one machine or many, reading and
writing one graph at once. Each server keeps its own active graph and its own
judge claims; everything else lives in Postgres, so one server sees another's
writes as soon as they are made, and the `epimemer agents` commands work
against it too.
Each graph is a schema of its own name in the database the DSN names, created
on first use, so the database must exist and the role must be allowed to create
schemas in it. The DSN is a URL or `key=value` pairs, so `sslmode` and a
password file work as they do for any Postgres client. Without the extra or
without a DSN, the server refuses to start and says which is missing.

Sharing today means everyone writes: `search` records when a claim was
retrieved, so a role granted only `SELECT` cannot search yet. Give each person
a role that can write to the graph's schema.

`epimemer graphs list` prints the backend, where its graphs live and the
graphs on it, for any backend.

## Configuration

All configuration is via `EPIMEMER_` environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `EPIMEMER_STORAGE_BACKEND` | `memory` | `memory`, `surrealdb`, `sqlite` or `postgres` |
| `EPIMEMER_SURREALDB_URL` | `ws://localhost:8000/rpc` | SurrealDB connection URL |
| `EPIMEMER_SURREALDB_USER` | `root` | SurrealDB username |
| `EPIMEMER_SURREALDB_PASS` | `root` | SurrealDB password |
| `EPIMEMER_SURREALDB_NAMESPACE` | `epimemer` | SurrealDB namespace |
| `EPIMEMER_SURREALDB_DATABASE` | `default` | The graph a SurrealDB server opens when `EPIMEMER_GRAPH` is unset; SurrealDB keeps one database per graph |
| `EPIMEMER_SQLITE_DIR` | `~/.epimemer/graphs` | For the `sqlite` backend: the directory holding one `<graph>.sqlite` file per graph, created on first use |
| `EPIMEMER_POSTGRES_DSN` | (empty) | For the `postgres` backend: the database holding one schema per graph, as a URL or `key=value` pairs. Required with that backend: the server refuses to start without it |
| `EPIMEMER_GRAPH` | (empty) | The graph this server opens, on any backend. Unset, the server opens `default`, or `EPIMEMER_SURREALDB_DATABASE` on SurrealDB. **Set it per server**: see [which graph a server opens](https://github.com/olenive/epimemer/blob/main/INTEGRATION.md#which-graph-a-server-opens) |
| `EPIMEMER_EMBEDDING_PROVIDER` | `sentence-transformers` | `sentence-transformers` or `mock` |
| `EPIMEMER_EMBEDDING_MODEL_ID` | `all-MiniLM-L6-v2` | Embedding model name |
| `EPIMEMER_EMBEDDING_DIMENSION` | `384` | Vector dimension for the `mock` provider. `sentence-transformers` takes its dimension from the model |
| `EPIMEMER_SEGMENTATION_STRATEGY` | `paragraph` | How `segment` cuts a document when the call names no strategy: `paragraph` or `semantic` |
| `EPIMEMER_REFLECT_THRESHOLD` | `10` | Stores into a graph before the server suggests a reflect. Counted per graph in storage and reported by `graph_stats`; a graph can override it with `configure_reflection` |
| `EPIMEMER_BACKUP_THRESHOLD` | `50` | Stores into a graph before the server suggests a backup. Counted per graph, reported by `graph_stats`, and overridable per graph with `configure_backup`. A separate count from the reflect one: reflecting clears that one, a successful backup clears this |
| `EPIMEMER_BACKUP_DESTINATION` | (empty) | Where `backup_graph` writes: a local path, a `gs://` URL or an `s3://` URL. The tool takes no path of its own, so this is the only thing that says where a graph goes. Empty means the tool refuses and names this variable rather than inventing somewhere. Credentials come from each provider's own chain |
| `EPIMEMER_BACKUP_KEEP` | (empty) | How many bundles of a graph to leave at the destination after a backup writes. Empty keeps every one, so deleting a backup has to be asked for. Set it to a count and the older bundles of that graph go, oldest first, ordered by the date in the filename; a `--plain` directory is never touched. A process setting like the destination it governs, with no per-graph override and no tool that can change it |
| `EPIMEMER_RECORD_RETRIEVAL` | `true` | Whether `search` stamps `retrieved_at` on what it returns. `false` disables it, which blinds the `never_retrieved` nomination; ranking is unaffected either way |
| `EPIMEMER_IMPORTANCE_STEP` | `0.25` | How much of the gap to its bound one `judge_importance` call closes, up or down. Nothing moves importance automatically |
| `EPIMEMER_TOOL_TIMEOUT_SECONDS` | `30.0` | Timeout per tool operation |
| `EPIMEMER_APPROVED_AGENTS` | (empty) | Comma-separated agent ids the user admits as judges in every graph this server opens. The approval channel for clients without an approval prompt of their own, and the only one that reaches an embedded store. See [ATTRIBUTION.md](https://github.com/olenive/epimemer/blob/main/docs/ATTRIBUTION.md) |
| `EPIMEMER_CLIENT_SESSION_ID` | `CLAUDE_CODE_SESSION_ID`, else unset | The client conversation this server serves, part of the seat a judge is confirmed in. Claude Code sets `CLAUDE_CODE_SESSION_ID` for every stdio server and keeps it across a reconnect, so there it needs no setting. See [INTEGRATION.md](https://github.com/olenive/epimemer/blob/main/INTEGRATION.md) |
| `EPIMEMER_CLIENT_STATE_DIR` | `~/.epimemer/client-state` | Where the `epimemer client-state record` hook records which model is behind each conversation. Give the hook the same directory with `--dir` if you change it |
| `EPIMEMER_REQUIRE_JUDGE` | `false` | Refuse any write that names no judge, on every graph this server opens. Off by default: a blank judge means *unknown*, and many graphs have no reason to care. A graph can override it with `epimemer agents require`; no MCP tool can set it |
| `EPIMEMER_VIZ_ENABLED` | `true` | Publish visualization events to the hub |
| `EPIMEMER_VIZ_HOST` | `127.0.0.1` | Visualization hub host |
| `EPIMEMER_VIZ_PORT` | `8765` | Visualization hub port |
| `EPIMEMER_VIZ_AUTOSPAWN` | `true` | Spawn a hub automatically if none is running |
| `EPIMEMER_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `EPIMEMER_LOG_FILE` | (stderr) | Path to log file (see below) |

**Set `EPIMEMER_LOG_FILE`.** When a client reports nothing beyond a connection
that failed, this file is where the reason is: a server that dies during
startup has nowhere else to put it, since everything on stderr goes with the
process. `scripts/check_server_starts.py`, in the repository, asks the same
question from a shell: run it with the interpreter the server uses, and it
prints what the server wrote. A server that starts logs its version, storage
backend and embedding provider.

## MCP Tools

The [integration guide](https://github.com/olenive/epimemer/blob/main/INTEGRATION.md#available-tools)
lists every tool, grouped by purpose, with a one-line description of each.
Claude Code prefixes each as `mcp__epimemer__<name>`.

## Visualization

A browser dashboard showing the knowledge graph, pipeline execution and
timelines, live. It is a **standalone hub** that many MCP sessions publish
to, rather than a server embedded in each MCP process, so several agents can
be watched from one page.

- **The hub owns the port** (`EPIMEMER_VIZ_HOST:EPIMEMER_VIZ_PORT`, default
  `127.0.0.1:8765`). Each MCP process dials out to it and registers as a
  *session*; the browser picks a session from the header selector.
- **Auto-spawn**: the first MCP process with `EPIMEMER_VIZ_ENABLED=true`
  spawns a detached hub if none is running (disable with
  `EPIMEMER_VIZ_AUTOSPAWN=false`).
- **CLI**: `epimemer-viz [--status|--stop]` for explicit control.
- **`viz_status` tool**: ask through the session you are driving. It returns
  the hub URL, whether the hub can see this session, and the `session_id` to
  pick in the selector. This answers "I opened the visualizer but can't find
  my graph".
- **Activity log**: one entry per transaction, filterable by verb, node,
  text and time; click an entry to highlight the nodes it acted on.
- **Warnings in the log**: every warning a tool computed is a `warned` row
  beside its act, muted ones included, dimmed and labelled *not shown to the agent*.
- **Warning settings**: the **warnings** button shows what this graph does
  about each kind; `configure_warnings` is the tool that changes one.
- **Retrieval focus**: pick a recent tool call and everything it did *not*
  return is dimmed; the drawer's **Response** tab shows what Epimemer returned.
- **Timeline**: one timeline at a time, in *record time* or *content time*,
  with undated points in a tray and recurrence occurrences computed for the window.

> **`EPIMEMER_VIZ_HOST` is a privacy setting as well as a network one.** On
> the default loopback bind the hub keeps whole retrieval records, so they
> survive the MCP process exiting. Point it at a non-loopback address and
> sessions mirror **structural metadata only**, with no query text and no
> response payloads; the payloads stay in the MCP process, reachable only
> while it is running.

## Administration

`epimemer agents list` shows a graph's approved judges, whether it requires
one, and what each has said about itself. `epimemer agents confirm <name>`
admits one, `epimemer agents rename <handle> <name>` renames one (add
`--same-judge` to fold two that are really one into one), and `epimemer
agents require on|off|default` decides whether writes to that graph must
name one. No MCP tool can do any of this: a tool the agent calls cannot prove
the user asked for it, so these acts are reserved for a person at the CLI.

`epimemer agents retire <handle>` takes a judge out of use: its name, history
and decisions stay, and `claim_agent` refuses it. `epimemer agents reinstate
<handle>` brings it back. `epimemer agents delete <handle>` removes a judge that
has never judged anything, and refuses with the counts if anything names it.

`epimemer relations backfill` gives every relationship label in use a record;
it is idempotent.

`epimemer metacontexts declare` gives every node that names no metacontext
one, `the-real` unless `--metacontext` names another, after asking.

`epimemer tags repair` finds topic nodes whose name was replaced by a
description and puts the name back, asking about each node, since only a person
can say which sentence was the name.

All of these work against a served SurrealDB, a SQLite graph or Postgres, each
of which a second process reaches as the server does. An embedded store (the
in-memory backend, or a `mem://`, `file://` or `surrealkv://` SurrealDB URL)
lives inside the server process, so a CLI writing to it would write to a
separate copy the running server never reads. For the two settings, use
`EPIMEMER_APPROVED_AGENTS` and `EPIMEMER_REQUIRE_JUDGE` instead; the command
refuses and names the right variable rather than appearing to succeed.

### Backup and restore

Four commands, writing the same bundles as the `backup_graph` tool:

```bash
epimemer graphs export <graph> --to <path>     # a bundle of the whole graph
epimemer graphs import <bundle> --graph <name> # rebuild it as a new graph
epimemer graphs verify <bundle>                # check one, then drop the copy
epimemer graphs convert <graph> --to postgres  # rebuild it on another backend, in one step
```

A bundle is one JSON Lines file per section of the graph, plus a manifest,
compressed into `<graph>-<YYYY-MM-DD>.epimemer.tar.gz`. `--plain` writes the
directory uncompressed for reading or diffing, and import accepts either.

`--to` takes a local path, which covers an external drive, a synced folder or a
repository you commit yourself, or a cloud URL. `gs://` needs
`epimemer[gcs]` installed and `s3://` needs `epimemer[s3]`; a URL whose
filesystem is missing is refused with the extra named. Credentials come from
each provider's standard chain, and Epimemer reads none of its own.

**Embeddings are not in the bundle.** Import re-embeds every node with the
provider the importing server is configured with, which is why *export, change
`EPIMEMER_EMBEDDING_MODEL_ID`, import* is how a graph moves to a new embedding
model. The manifest records what it was embedded with, so a restore onto a
different model says so.

**Import creates a new graph.** It refuses a name that already exists, with no
override: replacing a graph is `delete_graph` and then import, two acts you
already have. A failed import drops the partial graph and says why.

The `graphs` commands run against whatever store the configuration names, an
embedded one included, and warn there: opened from outside the server it is
empty, so an export writes an empty bundle and an import is lost on exit.

Nothing runs on a schedule: when a response says `backup_suggested`, the agent
asks you, and on a yes calls `backup_graph`, which writes to
`EPIMEMER_BACKUP_DESTINATION`. `EPIMEMER_BACKUP_THRESHOLD` sets how often it asks.

## Documentation

- [SUMMARY.md](https://github.com/olenive/epimemer/blob/main/SUMMARY.md): the architecture, its concepts and the reasons for them
- [INTEGRATION.md](https://github.com/olenive/epimemer/blob/main/INTEGRATION.md): Claude Code integration, agent guidance and the canonical tool table
- [docs/](https://github.com/olenive/epimemer/blob/main/docs/README.md): how each part behaves for a caller, one page each: ingest, retrieval, valid time, timelines, reflection and attribution

## Contributing

Development setup, the test suites, the frontend build and where the design
notes live are in
[CONTRIBUTING.md](https://github.com/olenive/epimemer/blob/main/CONTRIBUTING.md).
Bugs and proposals go to the
[issue tracker](https://github.com/olenive/epimemer/issues).

## License

[MIT](https://github.com/olenive/epimemer/blob/main/LICENSE).
