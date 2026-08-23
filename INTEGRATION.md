# Epimemer: Claude Code Integration

## MCP Server Setup

### Add to Claude Code

```bash
claude mcp add epimemer -- uv run --directory /path/to/epimemer python -m epimemer.mcp.server
```

Or add directly to `~/.claude.json`:

```json
{
  "mcpServers": {
    "epimemer": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/epimemer", "python", "-m", "epimemer.mcp.server"],
      "env": {
        "EPIMEMER_VIZ_ENABLED": "true"
      }
    }
  }
}
```

This uses the defaults: sentence-transformers for embeddings and in-memory
storage. The sentence-transformers model (`all-MiniLM-L6-v2`, ~80MB) downloads
on first run.

Epimemer performs no decomposition of its own — extracting topics, facts, and
inferences from text is the calling agent's job, via the `segment` →
`store_decomposition` two-step ingest (see *Available Tools*).

With visualization enabled, open http://127.0.0.1:8765 in your browser to see
the knowledge graph and pipeline execution in real time.

### Configuration via Environment Variables

| Variable | Default | Options |
|----------|---------|---------|
| `EPIMEMER_STORAGE_BACKEND` | `memory` | `memory`, `surrealdb` |
| `EPIMEMER_SURREALDB_URL` | `ws://localhost:8000/rpc` | Any SurrealDB URL |
| `EPIMEMER_SURREALDB_USER` | `root` | SurrealDB username |
| `EPIMEMER_SURREALDB_PASS` | `root` | SurrealDB password |
| `EPIMEMER_SURREALDB_NAMESPACE` | `epimemer` | SurrealDB namespace |
| `EPIMEMER_SURREALDB_DATABASE` | `default` | SurrealDB database name — one database per graph |
| `EPIMEMER_GRAPH` | (empty) | The graph this server opens. Overrides the above. **Set it per server** — see below |
| `EPIMEMER_EMBEDDING_PROVIDER` | `sentence-transformers` | `sentence-transformers`, `mock` |
| `EPIMEMER_EMBEDDING_MODEL_ID` | `all-MiniLM-L6-v2` | Any sentence-transformers model |
| `EPIMEMER_SEGMENTATION_STRATEGY` | `paragraph` | `paragraph`, `semantic` |
| `EPIMEMER_RECORD_RETRIEVAL` | `true` | Stamp `retrieved_at` on search results; `false` disables |
| `EPIMEMER_IMPORTANCE_STEP` | `0.25` | Fraction of the gap to the bound closed by one `judge_importance` call, in either direction |
| `EPIMEMER_TOOL_TIMEOUT_SECONDS` | `30.0` | Timeout per tool operation |
| `EPIMEMER_APPROVED_AGENTS` | (empty) | Comma-separated agent ids the user admits to every graph this server opens. The approval channel for clients that cannot elicit, and the only one that reaches an embedded store |
| `EPIMEMER_REQUIRE_JUDGE` | `false` | Refuse any write that names no judge, on every graph this server opens. Off by default: a blank judge means *unknown*, and many graphs have no reason to care. Overridable per graph with `epimemer agents require`, and deliberately not settable by any MCP tool |
| `EPIMEMER_VIZ_ENABLED` | `true` | `true`, `false` |
| `EPIMEMER_VIZ_HOST` | `127.0.0.1` | Any bind address |
| `EPIMEMER_VIZ_PORT` | `8765` | Any port |
| `EPIMEMER_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `EPIMEMER_LOG_FILE` | (stderr) | File path |

### Verify Connection

In Claude Code, run `/mcp` to check the server status. You should see `epimemer` listed with 41 tools.

## Available Tools

Tools are auto-prefixed as `mcp__epimemer__<name>` by Claude Code. This table is
the canonical list of the 41 tools — other docs should link here rather than
restate the count.

### Core Memory Operations

| Tool | Purpose |
|------|---------|
| `segment` | Split text into chunks (step 1 of ingest) |
| `store_decomposition` | Store agent-extracted topics/facts/inferences (step 2 of ingest) |
| `search` | Hybrid retrieval — embedding similarity **and** keyword matching, fused, then graph expansion. Pass exact identifiers as `terms`; `include_corroboration=True` adds how many independent publishers back each result. See [docs/RETRIEVAL.md](docs/RETRIEVAL.md) |
| `link` | Create typed edges between nodes |
| `update` | Create a new node version (immutable history). `because` is required — `"it_was_wrong"` or `"the_world_changed"` |
| `supersede_by` | Retire a node in favour of an already-existing one. `because` as above; if you cannot tell which happened, `record_contradiction` instead of guessing |
| `judge_importance` | Raise or lower a node's importance and record why (importance protects it from archival) |

### Discovery & Stats

| Tool | Purpose |
|------|---------|
| `query_graph` | Traverse the graph from a starting node |
| `topic_tree` | Drill into a topic hierarchy — ancestors and subtopics, previews only |
| `find_nodes` | Return nodes linked to a source or topic hub (traversal, not similarity) |
| `list_sources` | List the distinct source/origin nodes, with reference counts |
| `list_relations` | List the distinct user-defined relationship labels, with usage counts |
| `graph_stats` | Node/edge counts, type breakdown, and reflection pressure for the active graph |

### Conflict Handling

| Tool | Purpose |
|------|---------|
| `check_conflicts` | Find active facts that may conflict with the given facts (you judge each) |
| `record_contradiction` | Record a same-frame contradiction between two facts (both stay active) |
| `record_variant` | Record two facts as cross-frame variants of one proposition |
| `merge_facts` | Collapse facts that restate one claim into a single node, keeping every source. Refuses events, cross-frame pairs, and facts ingested without a `claim_kind` |
| `reverse_merge` | Undo a merge: restore the sources with their own edges and destroy the survivor. The only tool that deletes a node. Refuses when anything has been added to the survivor since |
| `configure_merge` | Read or set this graph's `merge_undo_depth` (how far back a merge stays reversible) and `merge_cycle_limit` (how many merge/un-merge rounds before a merge refuses) |

### Reflection

| Tool | Purpose |
|------|---------|
| `reflect` | Analyse the graph for consolidation/cleanup candidates (reads only) |
| `configure_reflection` | Set (or clear) this graph's store threshold for suggesting a reflect |
| `apply_reflection` | Apply agent decisions from a reflection (including user-approved archivals) |

### Review

Reading the decision journal back, and recording that somebody checked it.
Every judgment the graph records — who decided, about what, and when — see
[docs/ATTRIBUTION.md](docs/ATTRIBUTION.md).

| Tool | Purpose |
|------|---------|
| `review` | This graph's decisions, shakiest first: a declared low `certainty` before anything unrated, then by derived difficulty (thin sources, wide merges, open contradictions, ground that moved since). Modes `all` / `by_agent` / `since` / `unreviewed`, narrowed by `agent_id`, `since`/`until` and `certainty_ceiling`. Read-only, capped, and one graph wide — `graph` names which |
| `apply_review` | Record that you checked decisions and what you concluded — `confirmations` and `dissents`, each with a required `because`. Neither changes the graph: a dissent records the finding, and the undo is `reverse_merge` / `restore` / `apply_reflection` / `rejudge` |
| `rejudge` | Revise a judgment made at ingest — `claim_kind`, `confidence`, `confidence_basis` — without touching the claim. Not a supersession: nothing is retired, no edge moves, and the value replaced is kept on the node |

### Temporal Access

| Tool | Purpose |
|------|---------|
| `graph_as_of` | Snapshot what the graph *held* at a past instant (transaction time; for what was *true* then, `search(valid_as_of=…)`) |
| `query_changes` | Node births + retirements across one or more time windows |

### Archival

| Tool | Purpose |
|------|---------|
| `archive` | Export old superseded nodes for cold storage |
| `restore` | Reimport archived nodes, and return archived ones to active |

### Timeline Operations

| Tool | Purpose |
|------|---------|
| `create_timeline` | Create a named timeline (optionally anchored to its own "now") |
| `set_reference_time` | Set or clear a timeline's "now" — what past and future are measured against |
| `add_timepoint` | Add a timepoint (concrete or vague) to a timeline |
| `query_timeline` | Find nearest timepoints or query a time range |
| `create_timelink` | Link a node to a specific timepoint on a timeline |

### Metacontext Operations

| Tool | Purpose |
|------|---------|
| `create_metacontext` | Create an epistemic frame (e.g., "Real world", "Fiction") |
| `get_metacontexts` | Get metacontexts associated with a node |

### Graph Management (knowledge graphs)

Both storage backends support multiple named graphs.

| Tool | Purpose |
|------|---------|
| `list_graphs` | List available knowledge graphs and show the active one |
| `use_graph` | Switch to or create a knowledge graph |
| `delete_graph` | Delete a knowledge graph permanently |

#### Which graph a server opens

One rule, applied when the process starts:

```
EPIMEMER_GRAPH  →  else EPIMEMER_SURREALDB_DATABASE  →  else "default"
```

**The active graph is process state.** `use_graph` switches it and nothing
persists the switch, so a client reconnect — `/mcp`, a restart, a crash — starts
a fresh server that lands back on whatever that rule resolves to. A session that
spent an hour in one graph reopens somewhere else, and the switch it made is
gone. That is the intended behaviour; the point is that it is silent.

**So give every server an explicit `EPIMEMER_GRAPH`.** One MCP server entry per
project, each naming its graph, is the configuration this is built for. A server
without one is not broken — it opens `default` — but it will not stay where
`use_graph` put it.

**Every tool requires an `expected_graph`** — reads as well as writes — and
refuses rather than run when it is missing, or when the server is somewhere
else. That is the check a machine
makes; reporting the graph is only a hint an agent may read, and the incident was
silent precisely because every response said success.

Four tools are exempt, each because it is *about* graphs rather than in one:
`list_graphs` asks which exist, `use_graph` and `delete_graph` take the graph as
their argument, and `viz_status` is server-level.

**Reads are the half worth stating explicitly.** This began as three write tools,
on the argument that everything else dereferences a node id and so already fails
on the wrong graph. That argument missed two things. A wrong-graph `search`
returns a *plausible answer* the agent then reasons from and reports, leaving no
artifact anywhere — where a misfiled write at least leaves the material and its
journal row sitting together in the graph that received them. And an id that
fails to resolve is a worse failure than a refusal, not a substitute for one:
`merge_facts` raises *node not found*, which does not say *wrong graph*, so the
agent's next move is a workaround; `apply_reflection` does not even raise, it
skips; and where two graphs share ids — a restored archive, a copied database —
the ids resolve and the call lands.

**It is mandatory, unconditional, and there is no setting** — not per graph, not
per server. Two settings were considered and both rejected for one reason: *a
guard must not be configured by the state it is guarding against.* A per-graph
flag would be read from whichever graph the call is **actually** in, so landing
in the wrong one would switch the guard off in exactly the case it exists for. A
gate that turned itself on once a server could see a second graph would read a
live database list, so creating a graph would start refusing calls that worked
yesterday and deleting it would stop — a requirement that oscillates with
unrelated state is not a policy.

This is where it differs from `require_judge`, which *is* per graph and rightly
so: that is a policy about rigour, and rigour legitimately varies by use case.
There is no use case for not minding which graph a call lands in.

**Do not read the graph name out of a refusal and paste it back.** The check is
worth something only because the agent's expectation and the server's state are
worked out independently; echoing one into the other makes them agree by
construction. The refusal names the active graph so you can recover, not so you
can copy it.

**Tools also report `active_graph`** whether or not you passed an expectation —
`list_graphs`, `graph_stats` and `viz_status` report it too, but only if
something thinks to ask.

**Nothing moves the graph out from under a call in progress.** Tool calls run
concurrently — a client that batches independent calls into one block is doing
what agent harnesses ask for — and two things move the active graph: a
`use_graph`, and a dashboard snapshot of a graph this session is not on, which
borrows the connection to read it. Both now wait for the calls in flight, and
calls arriving during one wait for it. So a `use_graph` batched alongside an
ingest cannot split that ingest across two graphs, and a snapshot cannot
redirect a write. This is the one failure `expected_graph` could never catch:
the agent's expectation and the server's active graph agree while the database
underneath has moved.

The default is `default` and is deliberately a name nobody would give a real
graph. It used to be `memory`, which collided with a real graph of that name —
so a server configured without `EPIMEMER_GRAPH` wrote a project's material into
an unrelated graph and reported success. A default that lands somewhere empty is
wrong in a way you notice.

### Agents

Who is judging, so a later review can tell one agent's decisions from another's.
The id is **the user's to assign** — an agent proposes, the user approves, and an
id nobody approved is refused. Approval is per graph, so switching graphs can
unbind a judge.

| Tool | Purpose |
|------|---------|
| `claim_agent` | Propose an id and a self-description; binds this session to the judge the user approves |

Approval reaches the user through the client's elicitation prompt, through
`EPIMEMER_APPROVED_AGENTS`, or through `epimemer agents confirm <id>` — which
works only against a served SurrealDB, since an embedded store lives inside the
server process. No MCP tool can approve an id: a tool the agent calls cannot
establish that the *user* called it.

Every write records the claimed identity — ingest included — and every decision
is also appended to an append-only journal, so *what did this agent judge* is one
query and *has anyone checked it* is derived from a row pointing back. A graph
can be set to **refuse** writes that name no judge (`epimemer agents require on`,
or `EPIMEMER_REQUIRE_JUDGE` for the whole server); it is off by default, and no
MCP tool can change it. See [docs/ATTRIBUTION.md](docs/ATTRIBUTION.md).

### Visualization

| Tool | Purpose |
|------|---------|
| `viz_status` | Report this session's visualization hub URL, reachability, and the session id to select in the viewer |

## System Prompt Guidance

To help the agent use Epimemer effectively, add the contents of `epimemer_prompts/DEFAULT.md` to your project's CLAUDE.md or system instructions.

Customized prompts for different use cases can be added as additional files in the `epimemer_prompts/` directory.

## Response Format

All tool responses follow this structure:

```json
{
  "result": { ... },
  "_meta": {
    "nodes_searched": 142,
    "nodes_returned": 5,
    "graph_hops": 2,
    "latency_ms": 87,
    "source_types": {"topic": 2, "fact": 2, "inference": 1}
  }
}
```
