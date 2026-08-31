# Epimemer: Claude Code Integration

## MCP Server Setup

### Add to Claude Code

Install the package, then register the server:

```bash
uv tool install "epimemer[sentence-transformers]"
claude mcp add epimemer -- epimemer serve
```

Or add directly to `~/.claude.json`:

```json
{
  "mcpServers": {
    "epimemer": {
      "command": "epimemer",
      "args": ["serve"],
      "env": {
        "EPIMEMER_VIZ_ENABLED": "true"
      }
    }
  }
}
```

From a checkout, `uv run --directory /path/to/epimemer epimemer serve` is the
equivalent command.

This uses the defaults: sentence-transformers for embeddings and in-memory
storage. The embedding model (`all-MiniLM-L6-v2`, ~80MB) downloads on first
run. The provider itself is the `sentence-transformers` extra; selecting it
without that extra installed refuses at startup and names it.

Epimemer performs no decomposition of its own — extracting topics, facts, and
inferences from text is the calling agent's job, via the `segment` →
`store_decomposition` two-step ingest (see *Available Tools*).

With visualization enabled, open http://127.0.0.1:8765 in your browser to see
the knowledge graph and pipeline execution in real time.

### Configuration

All configuration is environment variables, documented in one place: the
[README's configuration table](README.md#configuration). For a persistent
setup you will usually set `EPIMEMER_STORAGE_BACKEND`, `EPIMEMER_SURREALDB_URL`
and `EPIMEMER_GRAPH` (see *Which graph a server opens* below).

### Verify Connection

In Claude Code, run `/mcp` to check the server status. You should see `epimemer` listed with 46 tools.

## Available Tools

Tools are auto-prefixed as `mcp__epimemer__<name>` by Claude Code. This table is
the canonical list of the 46 tools — other docs should link here rather than
restate the count.

### Core Memory Operations

| Tool | Purpose |
|------|---------|
| `segment` | Split text into chunks (step 1 of ingest) |
| `store_decomposition` | Store agent-extracted topics/facts/inferences (step 2 of ingest). `metacontext_id` is required — `the-real` for base reality |
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
| `list_relations` | List the distinct user-defined relationship labels, with usage counts and descriptions |
| `describe_relation` | Say what one of this graph's relationship labels means here — advisory prose the next agent reads before coining |
| `graph_stats` | Node/edge counts, type breakdown, and reflection pressure for the active graph |

### Conflict Handling

| Tool | Purpose |
|------|---------|
| `check_conflicts` | Find active facts that may conflict with the given facts (you judge each) |
| `record_contradiction` | Record a same-frame contradiction between two facts (both stay active) |
| `record_variant` | Record two facts as cross-frame variants of one proposition |
| `merge_facts` | Collapse facts that restate one claim into a single node, keeping every source. Refuses events, cross-frame pairs, and facts ingested without a `claim_kind` |
| `reverse_merge` | Undo a merge: restore the sources with their own edges and destroy the survivor. The only tool that deletes a node. Refuses when anything has been added to the survivor since |
| `merge_inferences` | Collapse inferences that state one conclusion into a single node. The survivor rests on the union of the sources' premises; where those premises are dated and fall clear of each other the response says so in `warnings` rather than refusing |
| `configure_merge` | Read or set this graph's `merge_undo_depth` (how far back a merge stays reversible) and `merge_cycle_limit` (how many merge/un-merge rounds before a merge refuses) |
| `configure_warnings` | Read or set what this graph does about advisories: per-kind `proceed` / `flag`, and `surface` — the global mute, which governs whether you are *shown* them and never whether they are recorded. A kind explicitly set to `flag` outranks the mute; one following the default does not |

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
| `review` | This graph's decisions, shakiest first: a declared low `certainty` before anything unrated, then by derived difficulty (thin sources, wide merges, open contradictions, ground that moved since). Modes `all` / `by_agent` / `since` / `unreviewed` / `advisory` (operations that completed against an objecting advisory — an advisory that merely escalates a correct call writes no row), narrowed by `agent_id` (a handle: a judge's name, its key, or a key it used to be recorded under), `since`/`until` and `certainty_ceiling`. Read-only, capped, and one graph wide — `graph` names which, and `elsewhere` counts the journal in every other graph so a reviewer is told where else to look |
| `apply_review` | Record that you checked decisions and what you concluded — `confirmations` and `dissents`, each with a required `because`. Neither changes the graph: a dissent records the finding, and the undo is `reverse_merge` / `restore` / `apply_reflection` / `rejudge` |
| `reframe` | Withdraw a frame from a node, or move it to another in one call — a metacontext assignment was one-way |
| `correct_interval` | Replace what one source is recorded as asserting about when a claim held |
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

**The active graph is process state.** `use_graph` switches it for the life of
the process only: a client reconnect (`/mcp`, a restart, a crash) starts a
fresh server that lands back on whatever that rule resolves to, silently. So
give every server an explicit `EPIMEMER_GRAPH`. One MCP server entry per
project, each naming its graph, is the configuration this is built for.

**Every tool requires an `expected_graph`**, reads as well as writes, and
refuses to run when it is missing or names a graph the server is not on. A
wrong-graph read is the dangerous case: it returns a plausible answer from the
wrong data and leaves no trace. Four tools are exempt because they are about
graphs rather than in one: `list_graphs` asks which exist, `use_graph` and
`delete_graph` take the graph as their argument, and `viz_status` is
server-level.

State the graph you meant, worked out from the user's request or from your own
`use_graph`. Do not copy the name out of a refusal: the check works only while
your expectation and the server's state are arrived at independently, and the
refusal names the active graph so you can recover, not so you can echo it.

The requirement is unconditional, with no setting to relax it. Concurrent tool
calls cannot move the graph out from under each other: a `use_graph` batched
alongside other calls waits for them to finish, so a batched ingest cannot be
split across two graphs.

### Agents

Who is judging, so a later review can tell one agent's decisions from
another's. The id is **the user's to assign**: an agent proposes, the user
approves, and an unapproved id is refused. Approval is per graph, so switching
graphs can unbind a judge.

| Tool | Purpose |
|------|---------|
| `claim_agent` | Propose a name and a self-description; binds this session to the judge the user picks. Returns both `name` (say this to a person) and `agent_id` (an opaque key, for `review`) |

Approval reaches the user through the client's approval prompt, through
`EPIMEMER_APPROVED_AGENTS`, or through `epimemer agents confirm <id>` (served
SurrealDB only, since an embedded store lives inside the server process). No
MCP tool can approve an id: a tool the agent calls cannot prove that the user
called it.

Every write records the claimed identity, and every decision is also appended
to a journal, so *what did this agent judge* is one query. A graph can be set
to refuse writes that name no judge (`epimemer agents require on`, or
`EPIMEMER_REQUIRE_JUDGE` for the whole server); that is off by default, and no
MCP tool can change it. See [docs/ATTRIBUTION.md](docs/ATTRIBUTION.md).

### Visualization

| Tool | Purpose |
|------|---------|
| `viz_status` | Report this session's visualization hub URL, reachability, and the session id to select in the viewer |

## Agent Guidance

`epimemer_prompts/DEFAULT.md` is the full guide to using these tools well:
when to ingest, search and reflect, and how to record verdicts. Add its
contents to your agent's instructions (for Claude Code, the project's
CLAUDE.md), or point the agent at the file. Serving it over MCP itself, so
that nothing needs copying, is on the backlog in
`dev-docs/PROPOSED_FEATURES.md`.

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
