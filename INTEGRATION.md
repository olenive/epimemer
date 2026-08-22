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
| `EPIMEMER_SURREALDB_DATABASE` | `memory` | SurrealDB database name |
| `EPIMEMER_GRAPH` | (empty) | Override database name for multi-graph |
| `EPIMEMER_EMBEDDING_PROVIDER` | `sentence-transformers` | `sentence-transformers`, `mock` |
| `EPIMEMER_EMBEDDING_MODEL_ID` | `all-MiniLM-L6-v2` | Any sentence-transformers model |
| `EPIMEMER_SEGMENTATION_STRATEGY` | `paragraph` | `paragraph`, `semantic` |
| `EPIMEMER_RECORD_RETRIEVAL` | `true` | Stamp `retrieved_at` on search results; `false` disables |
| `EPIMEMER_IMPORTANCE_STEP` | `0.25` | Fraction of the gap to the bound closed by one `judge_importance` call, in either direction |
| `EPIMEMER_TOOL_TIMEOUT_SECONDS` | `30.0` | Timeout per tool operation |
| `EPIMEMER_APPROVED_AGENTS` | (empty) | Comma-separated agent ids the user admits to every graph this server opens. The approval channel for clients that cannot elicit, and the only one that reaches an embedded store |
| `EPIMEMER_VIZ_ENABLED` | `true` | `true`, `false` |
| `EPIMEMER_VIZ_HOST` | `127.0.0.1` | Any bind address |
| `EPIMEMER_VIZ_PORT` | `8765` | Any port |
| `EPIMEMER_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `EPIMEMER_LOG_FILE` | (stderr) | File path |

### Verify Connection

In Claude Code, run `/mcp` to check the server status. You should see `epimemer` listed with 38 tools.

## Available Tools

Tools are auto-prefixed as `mcp__epimemer__<name>` by Claude Code. This table is
the canonical list of the 38 tools — other docs should link here rather than
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

Both storage backends support multiple named graphs. The starting graph is
`"default"` in-memory and `EPIMEMER_SURREALDB_DATABASE` (default `"memory"`)
under SurrealDB.

| Tool | Purpose |
|------|---------|
| `list_graphs` | List available knowledge graphs and show the active one |
| `use_graph` | Switch to or create a knowledge graph |
| `delete_graph` | Delete a knowledge graph permanently |

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
