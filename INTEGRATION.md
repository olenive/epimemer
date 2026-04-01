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

This uses the defaults: sentence-transformers for embeddings, pydantic-ai for
decomposition, and in-memory storage. The sentence-transformers model
(`all-MiniLM-L6-v2`, ~80MB) downloads on first run.

With visualization enabled, open http://127.0.0.1:8765 in your browser to see
the knowledge graph and pipeline execution in real time.

### Configuration via Environment Variables

| Variable | Default | Options |
|----------|---------|---------|
| `EPIMEMER_STORAGE_BACKEND` | `memory` | `memory`, `surrealdb` |
| `EPIMEMER_SURREALDB_URL` | `ws://localhost:8000/rpc` | Any SurrealDB URL |
| `EPIMEMER_EMBEDDING_PROVIDER` | `sentence-transformers` | `sentence-transformers`, `mock` |
| `EPIMEMER_EMBEDDING_MODEL_ID` | `all-MiniLM-L6-v2` | Any sentence-transformers model |
| `EPIMEMER_DECOMPOSITION_PROVIDER` | `pydantic-ai` | `pydantic-ai`, `mock` |
| `EPIMEMER_DECOMPOSITION_MODEL` | `claude-sonnet-4-20250514` | Any Pydantic AI model |
| `EPIMEMER_SEGMENTATION_STRATEGY` | `paragraph` | `paragraph`, `semantic` |
| `EPIMEMER_VIZ_ENABLED` | `false` | `true`, `false` |
| `EPIMEMER_VIZ_HOST` | `127.0.0.1` | Any bind address |
| `EPIMEMER_VIZ_PORT` | `8765` | Any port |
| `EPIMEMER_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `EPIMEMER_LOG_FILE` | (stdout) | File path |

### Verify Connection

In Claude Code, run `/mcp` to check the server status. You should see `epimemer` listed with 14 tools.

## Available Tools

### Core Memory Operations

| Tool | Purpose |
|------|---------|
| `memory.ingest` | Ingest text — segments, extracts nodes, constructs graph, embeds |
| `memory.search` | Hybrid retrieval — vector similarity + graph expansion |
| `memory.link` | Create typed edges between nodes |
| `memory.update` | Create new node version (immutable history) |
| `memory.reflect` | Consolidate — merge similar topics, decay stale nodes, detect contradictions |
| `memory.query_graph` | Traverse the graph from a starting node |
| `memory.archive` | Export old superseded nodes for cold storage |
| `memory.restore` | Reimport archived nodes |

### Timeline Operations

| Tool | Purpose |
|------|---------|
| `memory.create_timeline` | Create a named timeline |
| `memory.add_timepoint` | Add a timepoint (concrete or vague) to a timeline |
| `memory.query_timeline` | Find nearest timepoints or query a time range |
| `memory.create_timelink` | Link a node to a specific timepoint on a timeline |

### Metacontext Operations

| Tool | Purpose |
|------|---------|
| `memory.create_metacontext` | Create an epistemic frame (e.g., "Real world", "Fiction") |
| `memory.get_metacontexts` | Get metacontexts associated with a node |

## System Prompt Guidance

Add this to your project's CLAUDE.md or system instructions to help the agent use memory effectively:

```
## Memory System (Epimemer)

You have access to an epistemic memory system via MCP tools. Use it to:

### When to ingest (memory.ingest)
- After learning new information from the user or external sources
- When the user shares documents, articles, or knowledge you should remember
- Include a metacontext_id when the information has a specific framing (fiction, source, perspective)

### When to search (memory.search)
- Before answering questions that might benefit from prior context
- When the user asks "do you remember..." or references past conversations
- Use metacontext_id to filter results when the context is clear (e.g., discussing a specific fictional universe)

### When to reflect (memory.reflect)
- After ingesting several documents (the system auto-reflects after 10 ingestions)
- When explicitly asked to consolidate or organize knowledge
- Periodically during long sessions

### Interpreting _meta
Every tool response includes a _meta field with:
- nodes_returned: how many nodes were found/affected
- llm_calls: number of LLM calls made (for cost awareness)
- latency_ms: how long the operation took
- source_types: breakdown by node type (topic, fact, inference)

Surface this information naturally: "Found 5 relevant nodes (2 topics, 2 facts, 1 inference)."

### Metacontext awareness
- Always check the metacontexts field on returned nodes
- Never mix fictional and factual information without explicitly noting the distinction
- When creating new metacontexts, use clear, descriptive names
```

## Response Format

All tool responses follow this structure:

```json
{
  "result": { ... },
  "_meta": {
    "nodes_searched": 142,
    "nodes_returned": 5,
    "graph_hops": 2,
    "llm_calls": 0,
    "latency_ms": 87,
    "source_types": {"topic": 2, "fact": 2, "inference": 1}
  }
}
```
