# Epimemer: Claude Code Integration

## MCP Server Setup

### Add to Claude Code

Install the package, then register the server:

```bash
uv tool install "epimemer[sentence-transformers]"
claude mcp add epimemer -- epimemer serve
```

Or add it directly to `~/.claude.json`:

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
storage. The embedding model (`all-MiniLM-L6-v2`, about 80 MB) downloads on
first run. The provider comes from the `sentence-transformers` extra; if it is
selected without that extra installed, the server refuses to start and names
the extra.

Epimemer does no decomposition of its own. Extracting topics, facts and
inferences from text is the agent's job, done through the two-step ingest
`segment` then `store_decomposition` (see *Available Tools*).

With visualization enabled, open http://127.0.0.1:8765 in your browser to see
the knowledge graph and pipeline execution as they happen, alongside a timeline
of what the graph knows about when, and a log of every decision and every
warning the agent was given.

### Configuration

All configuration is environment variables, documented in one place: the
[README's configuration table](README.md#configuration). For a persistent
setup you will usually set `EPIMEMER_STORAGE_BACKEND`, `EPIMEMER_SURREALDB_URL`
and `EPIMEMER_GRAPH` (see *Which graph a server opens* below).

### Verify Connection

In Claude Code, run `/mcp` to check the server status. You should see `epimemer` listed with 55 tools.

## Available Tools

Claude Code prefixes each tool as `mcp__epimemer__<name>`. This table is
the canonical list of the 55 tools; other docs link here rather than
restate the count.

### Core Memory Operations

| Tool | Purpose |
|------|---------|
| `segment` | Split text into chunks (step 1 of ingest) |
| `store_decomposition` | Store the topics, facts and inferences you extracted (step 2 of ingest). `metacontext_id` is required: `the-real` for base reality |
| `search` | Hybrid retrieval: embedding similarity and keyword matching run separately, their rankings are fused, then graph expansion adds what the winners connect to. Pass exact identifiers as `terms`. `include_corroboration=True` adds how many independent publishers back each result. `metacontexts` scopes the whole response, edges and matched passages included. A result dated to a timepoint whose order is in dispute carries `date_contested`, which is doubt about the date rather than about the claim. See [docs/RETRIEVAL.md](docs/RETRIEVAL.md) |
| `link` | Create a typed edge between two nodes |
| `update` | Create a new version of a node; the old one is kept as history. `because` is required: `"it_was_wrong"` or `"the_world_changed"` |
| `supersede_by` | Retire a node in favour of one that already exists. `because` as above. If you cannot tell which happened, use `record_contradiction` instead of guessing |
| `judge_importance` | Raise or lower a node's importance and record why. Importance protects a node from archival |

### Discovery & Stats

| Tool | Purpose |
|------|---------|
| `query_graph` | Traverse the graph from a starting node. `metacontexts` scopes the neighbours, as on `search`; the starting node comes back either way |
| `topic_tree` | Walk a topic hierarchy: ancestors and subtopics, previews only. `metacontexts` scopes the tree |
| `find_nodes` | Return the nodes linked to a source document or a topic node, by following edges rather than by similarity. `metacontexts` scopes the listing |
| `list_sources` | List the distinct source nodes, with reference counts |
| `list_relations` | List the distinct user-defined relationship labels, with usage counts and descriptions |
| `describe_relation` | Say what one of this graph's relationship labels means here: guidance the next agent reads before coining a label |
| `graph_stats` | Node and edge counts, type breakdown, and reflection pressure for the active graph |

### Conflict Handling

| Tool | Purpose |
|------|---------|
| `check_conflicts` | Find active facts that may conflict with the given facts. You judge each pair |
| `record_contradiction` | Record a same-metacontext contradiction between two facts. Both stay active |
| `record_variant` | Record two facts as cross-metacontext variants of one proposition |
| `merge_facts` | Collapse facts that restate one claim into a single node, keeping every source. Refuses events, cross-metacontext pairs, and facts ingested without a `claim_kind` |
| `reverse_merge` | Undo a merge: restore the sources with their own edges and destroy the survivor. The only tool that deletes a node. Refuses when anything has been added to the survivor since the merge |
| `merge_inferences` | Collapse inferences that state one conclusion into a single node. The survivor rests on the union of the sources' premises. Where those premises are dated and fall clear of each other, the response says so in `warnings` rather than refusing |
| `configure_merge` | Read or set this graph's `merge_undo_depth` (how far back a merge stays reversible) and `merge_cycle_limit` (how many merge and un-merge rounds before a merge refuses) |
| `configure_warnings` | Read or set what this graph does about warnings: per-kind `proceed` or `flag`, and `surface`, the global mute. The mute governs whether you are *shown* warnings, never whether they are recorded. A kind explicitly set to `flag` outranks the mute; one following the default does not |

### Reflection

| Tool | Purpose |
|------|---------|
| `reflect` | Analyse the graph for consolidation and cleanup candidates, including open disputes about the order of timepoints. Reads only |
| `configure_reflection` | Set or clear this graph's store threshold for suggesting a reflect |
| `apply_reflection` | Apply your decisions from a reflection, including user-approved archivals |
| `reopen` | Put a question a verdict suppressed back on the worklist: two node ids for a pair judged `distinct`, two relation label names for a label pair, one node id for a node a `retained` verdict kept. Asserts nothing about the answer, and a `reason` is required |

### Review

Reading the decision journal back, and recording that somebody checked it.
The journal holds every judgment the graph records: who decided, about what,
and when. See [docs/ATTRIBUTION.md](docs/ATTRIBUTION.md).

| Tool | Purpose |
|------|---------|
| `review` | This graph's decisions, least certain first: a declared low `certainty` before anything unrated, then by derived difficulty (thin sources, wide merges, open contradictions, ground that moved since). Modes: `all`, `by_agent`, `since`, `unreviewed`, `warning` (operations that completed against an objecting warning; a warning that only escalated a correct call writes no row). Narrow by `agent_id` (a judge's name, its key, or a key it used to be recorded under), by `since` and `until`, and by `certainty_ceiling`. Read-only, capped, and one graph wide: `graph` names which, and `elsewhere` counts the journal in every other graph so a reviewer knows where else to look |
| `apply_review` | Record that you checked decisions and what you concluded: `confirmations` and `dissents`, each with a required `because`. Neither changes the graph. A dissent records the finding; the undo is `reverse_merge`, `restore`, `apply_reflection` or `rejudge` |
| `reassign_metacontext` | Withdraw a metacontext from a node, or move it to another metacontext in one call |
| `correct_interval` | Replace what one source is recorded as asserting about when a claim held |
| `rejudge` | Revise a judgment made at ingest (`claim_kind`, `confidence`, `confidence_basis`) without touching the claim. Not a supersession: nothing is retired, no edge moves, and the value replaced is kept on the node |

### Temporal Access

| Tool | Purpose |
|------|---------|
| `graph_as_of` | Snapshot what the graph *held* at a past instant (transaction time). For what was *true* then, use `search(valid_as_of=…)` |
| `query_changes` | Node births and retirements across one or more time windows |

### Archival

| Tool | Purpose |
|------|---------|
| `archive` | Export old superseded and merged nodes for cold storage. Deletes nothing |
| `restore` | Reimport archived nodes, or return a retired claim to active when a new source says it is true again |

### Timeline Operations

| Tool | Purpose |
|------|---------|
| `create_timeline` | Create a named timeline, optionally anchored to its own "now" |
| `set_reference_time` | Set or clear a timeline's "now", which past and future are measured against |
| `add_timepoint` | Add a timepoint to a timeline; the response says which kind the dates made it, `instant`, `interval` or `vague`, and names any contradiction a new date opened. `recurrence_id` with `occurrence_start` in place of dates materialises one occurrence of a rule, idempotently |
| `order_timepoints` | Record that a source says these points came in this order, so a point nobody dated still gets a place in time |
| `resolve_temporal_contradiction` | Answer a dispute about the order: retire the constraint, split the point into two events, or hold it for want of evidence |
| `merge_timepoints` | Two points are one moment: fold one into the other, moving every constraint and every fact dated to it |
| `query_timeline` | Find the nearest timepoints, query a time range, or ask what the order puts before, after or between named points; each point comes back with its kind, its derived bounds and whether its order is contested, and the facts attached to it in `linked`. Occurrences of recurrence rules are computed in, marked with the rule, the `occurrence_start` that names them and the point that materialised them, with the rule's own facts in `linked_via_rule` on each of them; `next_after` moves the moment "next" is measured from |
| `add_recurrence` | Record a rule for something that happens over and over, by arithmetic (`anchor` and `period`) or by the calendar (an RFC 5545 `rrule`); the response previews the first few occurrences |
| `end_recurrence` | Say when a rule stopped applying. The rule is not retired and nothing is overwritten: each change is appended, and the response gives the whole history |
| `record_recurrence_exception` | One occurrence cancelled, or moved to another date under the identity the rule gave it |
| `create_timelink` | Link a node to one timepoint (`timepoint_id`), reporting that point's kind, bounds and whether it is contested, or to a recurrence rule (`recurrence_id`), where the fact holds at every occurrence and the response gives the rule's label and the bounds it is in force between. Exactly one of the two |

### Metacontext Operations

| Tool | Purpose |
|------|---------|
| `create_metacontext` | Create a metacontext (for example "Real world" or "Fiction") |
| `get_metacontexts` | Get the metacontexts a node is in |

### Graph Management (knowledge graphs)

Both storage backends support multiple named graphs.

| Tool | Purpose |
|------|---------|
| `list_graphs` | List the available knowledge graphs and show the active one |
| `use_graph` | Switch to a knowledge graph, creating it if needed |
| `delete_graph` | Delete a knowledge graph permanently |
| `backup_graph` | Write the active graph out as a bundle, to the destination the server was configured with (`EPIMEMER_BACKUP_DESTINATION`). Takes no path: where a graph goes is the user's decision. Refuses plainly when nothing is configured |
| `configure_backup` | Set or clear this graph's store threshold for suggesting a backup |

#### Which graph a server opens

One rule, applied when the process starts:

```
EPIMEMER_GRAPH  →  else EPIMEMER_SURREALDB_DATABASE  →  else "default"
```

**The active graph is process state.** `use_graph` switches it for the life of
the process only. A client reconnect (`/mcp`, a restart, a crash) starts a
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

State the graph you meant, worked out from the user's request or from your
own `use_graph`. Do not copy the name out of a refusal: the check works only
while your expectation and the server's state are arrived at independently.
The refusal names the active graph so you can recover, not so you can echo it.

There is no setting to relax the requirement. Concurrent tool calls cannot
move the graph out from under each other: a `use_graph` batched alongside
other calls waits for them to finish, so a batched ingest cannot be split
across two graphs.

### Agents

Who is judging, so a later review can tell one agent's decisions from
another's. The id is **the user's to assign**: an agent proposes, the user
approves, and an unapproved id is refused. Approval is per graph, so switching
graphs can unbind a judge.

| Tool | Purpose |
|------|---------|
| `claim_agent` | Propose a name and a self-description; binds this session to the judge the user picks. Returns `name` (say this to a person), `agent_id` (an opaque key, for `review`) and `judge_token` (pass it as `judge_token` on every write) |

Approval reaches the user through the client's approval prompt, through
`EPIMEMER_APPROVED_AGENTS`, or through `epimemer agents confirm <id>` (served
SurrealDB only, since an embedded store lives inside the server process). No
MCP tool can approve an id: a tool the agent calls cannot prove that the user
called it.

Several agents can share one connection, a subagent beside the agent that
spawned it. Each claims its own judge and passes the `judge_token` its claim
returned on every write, which is what credits the write to that agent's judge
rather than to whichever of them claimed last. A write carrying no token is
credited to the most recent claim; a token the connection never issued is
refused.

Every write records the claimed identity, and every decision is also appended
to a journal, so *what did this agent judge* is one query. A graph can be set
to refuse writes that name no judge (`epimemer agents require on`, or
`EPIMEMER_REQUIRE_JUDGE` for the whole server). That is off by default, and no
MCP tool can change it. See [docs/ATTRIBUTION.md](docs/ATTRIBUTION.md).

### Visualization

| Tool | Purpose |
|------|---------|
| `viz_status` | Report this session's visualization hub URL, whether the hub can see the session, and the session id to select in the viewer |

## Agent Guidance

The server carries its own guidance, so nothing needs copying into an agent's
instructions. It comes in two sizes:

- **The rules**, `epimemer_prompts/RULES.md`, are the server's MCP
  `instructions` string: about 2 KB, sent to every client on connect, so they
  sit in every context. They are what must hold on every call: pass
  `expected_graph`, claim a judge, name the metacontext, record every verdict,
  read `warnings` first.
- **The guide**, `epimemer_prompts/DEFAULT.md`, is the MCP prompt `guide`:
  about 60 KB, when to ingest, search, reflect and review, and what each
  warning means. Claude Code lists it as the slash command
  `/mcp__epimemer__guide`; an agent should pull it before nontrivial memory
  work rather than hold it in every context.

Both files ship with the package. A project's own instructions can still add
to them (which graph its work goes in, what to tag it with), and the rules
are short enough to restate there when a client does not surface MCP
`instructions`.

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
