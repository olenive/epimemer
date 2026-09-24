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
| `notebooks` | [marimo](https://marimo.io) and the Petri-net plotting stack | Only for the walkthrough notebooks in the repository |

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
has the full configuration, the canonical tool table, and how the agent
guidance reaches the agent: the server sends the per-call rules on connect and
serves the full guide as the MCP prompt `guide`, so nothing is pasted into an
agent's instructions.

## Persistence

Persistent storage is a [SurrealDB](https://surrealdb.com) server. **The
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
`EPIMEMER_SURREALDB_USER` and `EPIMEMER_SURREALDB_PASS` for anything else. Set
`EPIMEMER_GRAPH` per server: the active graph is process state, so a client
that reconnects lands back on whatever the server opened.

## Configuration

All configuration is via `EPIMEMER_` environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `EPIMEMER_STORAGE_BACKEND` | `memory` | `memory` or `surrealdb` |
| `EPIMEMER_SURREALDB_URL` | `ws://localhost:8000/rpc` | SurrealDB connection URL |
| `EPIMEMER_SURREALDB_USER` | `root` | SurrealDB username |
| `EPIMEMER_SURREALDB_PASS` | `root` | SurrealDB password |
| `EPIMEMER_SURREALDB_NAMESPACE` | `epimemer` | SurrealDB namespace |
| `EPIMEMER_SURREALDB_DATABASE` | `default` | SurrealDB database name, one database per graph. A name only: whether storage is in memory is decided by `EPIMEMER_STORAGE_BACKEND` here and by the `[PATH]` argument on the server |
| `EPIMEMER_GRAPH` | (empty) | The graph this server opens, overriding the database name above. **Set it per server.** The active graph is process state, so `use_graph` lasts only as long as the process, and a client reconnect lands back here. See [which graph a server opens](https://github.com/olenive/epimemer/blob/main/INTEGRATION.md#which-graph-a-server-opens) |
| `EPIMEMER_EMBEDDING_PROVIDER` | `sentence-transformers` | `sentence-transformers` or `mock` |
| `EPIMEMER_EMBEDDING_MODEL_ID` | `all-MiniLM-L6-v2` | Embedding model name |
| `EPIMEMER_EMBEDDING_DIMENSION` | `384` | Embedding vector dimension |
| `EPIMEMER_SEGMENTATION_STRATEGY` | `paragraph` | `paragraph` or `semantic` |
| `EPIMEMER_SIMILARITY_THRESHOLD` | `0.75` | Similarity threshold for search |
| `EPIMEMER_REFLECT_THRESHOLD` | `10` | Stores into a graph before the server suggests a reflect. Counted per graph in storage and reported by `graph_stats`; a graph can override it with `configure_reflection` |
| `EPIMEMER_BACKUP_THRESHOLD` | `50` | Stores into a graph before the server suggests a backup. Counted per graph, reported by `graph_stats`, and overridable per graph with `configure_backup`. A separate count from the reflect one: reflecting clears that one, a successful backup clears this |
| `EPIMEMER_BACKUP_DESTINATION` | (empty) | Where `backup_graph` writes: a local path, a `gs://` URL or an `s3://` URL. The tool takes no path of its own, so this is the only thing that says where a graph goes. Empty means the tool refuses and names this variable rather than inventing somewhere. Credentials come from each provider's own chain |
| `EPIMEMER_BACKUP_KEEP` | (empty) | How many bundles of a graph to leave at the destination after a backup writes. Empty keeps every one, so deleting a backup has to be asked for. Set it to a count and the older bundles of that graph go, oldest first, ordered by the date in the filename; a `--plain` directory is never touched. A process setting like the destination it governs, with no per-graph override and no tool that can change it |
| `EPIMEMER_RECORD_RETRIEVAL` | `true` | Whether `search` stamps `retrieved_at` on what it returns. `false` disables it, which blinds the `never_retrieved` nomination; ranking is unaffected either way |
| `EPIMEMER_IMPORTANCE_STEP` | `0.25` | How much of the gap to its bound one `judge_importance` call closes, up or down. Nothing moves importance automatically |
| `EPIMEMER_TOOL_TIMEOUT_SECONDS` | `30.0` | Timeout per tool operation |
| `EPIMEMER_APPROVED_AGENTS` | (empty) | Comma-separated agent ids the user admits as judges in every graph this server opens. Read when the backend connects and when the server lands on a graph. The approval channel for clients without an approval prompt of their own, and the only one that reaches an embedded store. See [ATTRIBUTION.md](https://github.com/olenive/epimemer/blob/main/docs/ATTRIBUTION.md) |
| `EPIMEMER_REQUIRE_JUDGE` | `false` | Refuse any write that names no judge, on every graph this server opens. Off by default: a blank judge means *unknown*, and many graphs have no reason to care. A graph can override it with `epimemer agents require`; no MCP tool can set it |
| `EPIMEMER_VIZ_ENABLED` | `true` | Publish visualization events to the hub |
| `EPIMEMER_VIZ_HOST` | `127.0.0.1` | Visualization hub host |
| `EPIMEMER_VIZ_PORT` | `8765` | Visualization hub port |
| `EPIMEMER_VIZ_AUTOSPAWN` | `true` | Spawn a hub automatically if none is running |
| `EPIMEMER_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `EPIMEMER_LOG_FILE` | (stderr) | Path to log file. A server that cannot start writes the reason here before it exits, and a server that starts logs its version, storage backend and embedding provider |

**Set `EPIMEMER_LOG_FILE`.** When a client reports nothing beyond a connection
that failed, this file is where the reason is: a server that dies during
startup has nowhere else to put it, since everything on stderr goes with the
process. `python scripts/check_server_starts.py` asks the same question from a
shell, and prints what the server wrote.

## MCP Tools

Tools exposed via the Model Context Protocol (Claude Code prefixes each as
`mcp__epimemer__<name>`), grouped by purpose:

- **Core memory**: `segment`, `store_decomposition`, `search`, `link`, `update`, `rename_topic`, `supersede_by`, `judge_importance`
- **Discovery & stats**: `query_graph`, `topic_tree`, `find_nodes`, `list_sources`, `list_relations`, `describe_relation`, `graph_stats`
- **Conflict handling**: `check_conflicts`, `record_contradiction`, `record_variant`, `merge_facts`, `merge_inferences`, `reverse_merge`, `configure_merge`, `configure_warnings`
- **Reflection**: `reflect`, `configure_reflection`, `apply_reflection`, `reopen`
- **Temporal access**: `graph_as_of`, `query_changes`
- **Archival**: `archive`, `restore`
- **Timelines**: `create_timeline`, `set_reference_time`, `add_timepoint`, `order_timepoints`, `resolve_temporal_contradiction`, `merge_timepoints`, `add_recurrence`, `end_recurrence`, `record_recurrence_exception`, `query_timeline`, `create_timelink`
- **Metacontexts**: `create_metacontext`, `get_metacontexts`
- **Graph management**: `list_graphs`, `use_graph`, `delete_graph`, `backup_graph`, `configure_backup`
- **Agents**: `claim_agent` says which judge you are. The user picks the
  judge, and can rename it later without disturbing any decision. The claim
  returns a `judge_token` to pass on every write, so two agents sharing one
  connection each keep their own judge
- **Review**: `review` lists the decisions this graph has recorded, least
  certain first; `apply_review` records that you checked one, and whether you
  agree; `rejudge` revises a judgment made at ingest without touching the
  claim; `reassign_metacontext` withdraws a metacontext from a node, or moves it to
  another; `correct_interval` replaces what one source is recorded as
  asserting about when a claim held
- **Visualization**: `viz_status`

The [integration guide](https://github.com/olenive/epimemer/blob/main/INTEGRATION.md#available-tools)
has the canonical table with one-line descriptions and the authoritative tool count.

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
- **Activity log**: one entry per transaction, saying what the agent stored,
  corrected, world-changed, merged, archived or restored, filterable by verb,
  node id, text and time. A timeline decision and a `reopen` each carry their
  own verb, the one the decision journal files them under. Click an entry to
  highlight the nodes it acted on; click a node to filter the log to it.
- **Warnings in the log**: every warning a tool computed appears as a `warned`
  row beside the act it accompanied, saying which tool raised it, what it said
  and whether the agent was asked to raise it with you. A warning this graph
  has muted is shown too, dimmed and labelled *not shown to the agent*, because
  the dashboard is where you find out what the agent was not told. Filter to
  `warned` to read them alone.
- **Warning settings**: the **warnings** button beside the reflect badge shows
  what this graph does about each kind, and whether that answer is the process
  default or something set on this graph. It is a view, not a control: use the
  `configure_warnings` tool to change one, so the change is recorded against a
  session and a judge the way every other change is.
- **Retrieval focus**: pick a recent tool call and everything it did *not*
  return is dimmed. Dimmed nodes stay clickable, since the interesting click
  is on one that did not come back, and the drawer's **Response** tab shows
  exactly what Epimemer returned.
- **Timeline**: one timeline at a time on a vertical axis, in *record time*
  (when the graph learned each node) or *content time* (when the described
  events happened). Large gaps collapse to a labelled break; vague timepoints
  sit in an *undated* tray rather than being given an invented date. A point
  only the stated order places is drawn as a hatched band across the bounds it
  has, a point whose order is disputed carries a mark beside it, and a
  recurrence rule's occurrences are beads on a dotted spine in a lane of their
  own, computed for the window on screen and never stored.

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

`epimemer agents retire <handle>` takes a judge out of use. It keeps its name,
its history and every decision, and review still answers for it; what changes
is that `claim_agent` refuses it and the picker stops offering it beside the
live ones. `epimemer agents reinstate <handle>` brings one back, as does the
picker's *A retired judge…* entry. `epimemer agents delete <handle>` removes a
judge that has never judged anything: it scans the graph, shows what it found,
and refuses with the counts if anything names the judge, since a deleted record
would leave rows carrying a key nothing resolves to a name.

`epimemer relations backfill` gives every relationship label already in use a
record, in one go. It is idempotent and never touches a label that has one.

`epimemer tags repair` puts back the names an old enrichment overwrote. It is
for graphs written before enrichment learned to describe a topic instead of
renaming it, and it asks about each node before touching it, since only a person
can say which of two sentences was the tag's name. Idempotent: on a graph that
never held the damage it finds nothing.

All of these work only against a **served** SurrealDB. An embedded store lives
inside the server process, so a CLI writing to it would write to a separate
copy the running server never reads. For the two settings, use
`EPIMEMER_APPROVED_AGENTS` and `EPIMEMER_REQUIRE_JUDGE` instead; the command
refuses and names the right variable rather than appearing to succeed.

### Backup and restore

Three commands, and they are the same code the `backup_graph` tool runs:

```bash
epimemer graphs export <graph> --to <path>     # a bundle of the whole graph
epimemer graphs import <bundle> --graph <name> # rebuild it as a new graph
epimemer graphs verify <bundle>                # check one, then drop the copy
```

A bundle is one JSON Lines file per section (metacontexts, judges, nodes,
edges, the decision journal, the relation vocabulary, timelines, documents and
segments, and the graph's own settings), plus a manifest, compressed into
`<graph>-<YYYY-MM-DD>.epimemer.tar.gz`. `--plain` writes the directory
uncompressed for reading or diffing, and import accepts either.

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

The `graphs` commands run against whatever store the configuration names, so
unlike the commands above they are not refused on an embedded one. They do warn:
an embedded store opened from outside the server is an empty one, so exporting
it writes an empty bundle and importing into it throws the graph away when the
command exits.

Nothing runs on a schedule. Instead, `store_decomposition` and
`apply_reflection` report `stores_since_backup` against `backup_threshold`, and
when `backup_suggested` is true the agent raises it with you. Say yes and it
calls `backup_graph`, which writes to `EPIMEMER_BACKUP_DESTINATION` and takes no
path of its own, so an agent can act on the prompt without choosing where your
graph goes. `EPIMEMER_BACKUP_THRESHOLD` sets how often it asks.

## Architecture

- **Dual-space**: vector embeddings as the primary representation, with a typed graph derived on top
- **Three node types**: Topics (themes), Facts (atomic statements), Inferences (provisional derivations)
- **Timelines**: one record holding timepoints, the ordering constraints sources have asserted between them, the temporal contradictions those constraints opened, and recurrence rules whose occurrences are computed per query
- **Metacontexts**: they separate fiction from fact, and one source or perspective from another
- **Petri nets**: all pipelines are executable, typed, visualizable Petri nets via [Petritype](https://github.com/olenive/petritype)
- **Immutable history**: a node's *content* is never mutated; an update creates a new version joined to the old one by a history edge. Lifecycle metadata such as `status` and value signals is mutated in place
- **Sources and topics are nodes**: a fact reaches its source document by a `sourced_from` edge and a tag by a `tagged_with_topic` edge, not by a string field. A *tag* is a name passed in at ingest; it resolves to a Topic node, the topic node created from that tag. That edge is a retrieval index and carries no evidential weight; `supports` is the evidential edge
- **Relations**: relationships between nodes are open-vocabulary, user-labelled edges

## Documentation

- [SUMMARY.md](https://github.com/olenive/epimemer/blob/main/SUMMARY.md): the architecture, its concepts and the reasons for them
- [INTEGRATION.md](https://github.com/olenive/epimemer/blob/main/INTEGRATION.md): Claude Code integration, agent guidance and the canonical tool table
- [docs/RETRIEVAL.md](https://github.com/olenive/epimemer/blob/main/docs/RETRIEVAL.md): how `search` is answered: the two arms, rank fusion, result provenance, lineage collapse
- [docs/VALIDITY.md](https://github.com/olenive/epimemer/blob/main/docs/VALIDITY.md): when a claim was true: intervals per source, correction versus world-change, recurrence, the soundness check
- [docs/TIMELINES.md](https://github.com/olenive/epimemer/blob/main/docs/TIMELINES.md): when something happened and in what order: dated points, the order a source states between undated ones, temporal contradictions and their verdicts, recurrence rules
- [docs/REFLECTION.md](https://github.com/olenive/epimemer/blob/main/docs/REFLECTION.md): the review loop: verdicts, what `reflect` nominates, what `apply_reflection` writes
- [docs/ATTRIBUTION.md](https://github.com/olenive/epimemer/blob/main/docs/ATTRIBUTION.md): who judged this: the agent registry, why the user assigns the id, how approval reaches an agent, the append-only journal of every decision, and reading it back with `review`, `apply_review` and `rejudge`

## Contributing

Development setup, the test suites, the frontend build and where the design
notes live are in
[CONTRIBUTING.md](https://github.com/olenive/epimemer/blob/main/CONTRIBUTING.md).
Bugs and proposals go to the
[issue tracker](https://github.com/olenive/epimemer/issues).

## License

[MIT](https://github.com/olenive/epimemer/blob/main/LICENSE).
