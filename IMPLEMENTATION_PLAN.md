# Epimemer Implementation Plan

## Principles

- **Test-driven**: each module gets unit tests with mocked LLM/DB calls before implementation is considered complete
- **Observable**: each module gets a Marimo notebook for interactive step-through and visualization of its Petri net
- **Benchmarkable**: instrumentation and placeholders from the start, even where benchmarks aren't yet defined
- **Modular**: every external dependency (LLM, embedding model, database) is behind an abstraction layer
- **Incremental**: each phase produces something testable and demonstrable before the next begins

## Implementation Status

Phases 0-6 and 8 are complete. Phase 7 is partially complete. The visualization system (not in the original plan) has been implemented as a bonus.

### What's done

- **Phase 0 (Foundation)**: Core types, storage protocol (memory + SurrealDB), embedding abstraction (sentence-transformers + mock). The original LLM abstraction (Pydantic AI + mock) was later removed — see *Pivot* below.
- **Phase 1 (Segmentation)**: Paragraph split and semantic similarity strategies with Petri nets
- **Phase 2 (Decomposition)**: superseded — decomposition is now agent-driven. The in-server LLM decomposition net and hybrid topic-assignment path were removed; see *Pivot* below.
- **Phase 3 (Graph Construction)**: Edge creation net, value signal init/update, persistence, node versioning
- **Phase 4 (Query Layer)**: Vector search, graph expansion, hybrid retrieval net, temporal queries
- **Phase 5 (Reflection)**: Topic consolidation (flat + hierarchical), value decay, contradiction detection, topic splitting, topic enrichment, topic hierarchy (DAG with SUBTOPIC_OF), archival, full reflection Petri net
- **Phase 6 (MCP Server)**: 29 tools (see [INTEGRATION.md](INTEGRATION.md#available-tools) for the canonical list), structured logging, response metadata
- **Phase 7 (Timelines & Metacontext)**: Base Timeline/Timepoint types, Metacontext with propagation, MCP tools for both
- **Phase 8 (Orchestration)**: Orchestration net with auto-suggested reflection
- **Visualization**: Event bus, instrumented storage/executor, WebSocket server, Cytoscape.js graph panel, Graphviz SVG pipeline panel, graph browser with snapshot loading
- **Multi-graph for InMemoryStorage**: All backends support multiple named graphs, `supports_multi_graph` flag removed from protocol
- **Visualization Data Model**: NodeView/EdgeView shared models for events and snapshots, HTTP endpoints for graph listing and snapshot retrieval, WebSocket sequence numbers and subscription filtering

### Pivot: decomposition is agent-driven

The original plan (Phases 0 and 2) ran decomposition *inside* the server, via an
LLM abstraction (Pydantic AI + mock) and a hybrid vector-first / LLM-fallback
topic-assignment net. That path was removed: **Epimemer makes no LLM calls of its
own.** Ingest is now the two-step `segment` → `store_decomposition` flow, where
the *calling* agent extracts topics/facts/inferences. Anything below that
describes an in-server LLM decomposition, a `SegmentationProvider` /
`DecompositionProvider` LLM protocol, or an "LLM-fallback" topic strategy is
retained as design history, not current behaviour.

---

## Remaining Work

### Phase 1: Segmentation — missing strategies

Two segmentation strategies from the original plan have not been implemented. The paragraph split and semantic similarity strategies cover the primary use cases; these are lower priority.

#### LLM-guided segmentation
```
[RawDocument] → llm_segment → [Segments]
```
Would use a `SegmentationProvider` abstraction. Note the original LLM protocol was removed (see *Pivot*), so this strategy would require re-introducing a provider or delegating the split to the calling agent.

#### Hybrid segmentation
```
[RawDocument] → split_sentences → [Sentences]
[Sentences] → compute_similarities → [CandidateBoundaries]
[CandidateBoundaries] + [RawDocument] → llm_refine_boundaries → [Segments]
```
Combines embedding-based boundary detection with LLM refinement.

---

### Phase 7: Timelines — specialized implementations

The base Timeline class is implemented. Three specialized implementations are missing. These add efficient temporal queries, LLM-assisted ordering for vague dates, and cyclical pattern matching.

#### PreciseTimeline
Backed by sorted list with datetime interval index. Supports efficient range queries, insertion, and temporal proximity search.

#### VagueTimeline
Ordered list of labeled timepoints. Ordering assisted by LLM when needed. Supports relative ordering queries ("before/after").

#### CyclicalTimeline
Template-based (e.g., "every Monday", "annually in spring"). Maps to concrete instances when linked to specific events.

Each implementation must support:
- Add/remove/reorder timepoints (with stable UUIDs)
- Find timepoints near a given time (temporal proximity search)
- Detect overlapping intervals
- Serialize/deserialize for storage

---

### Multi-graph for InMemoryStorage (done)

All backends now support multiple named graphs. InMemoryStorage uses a dict-of-dicts pattern (`_GraphStore` dataclass per graph). The `supports_multi_graph` flag has been removed from the protocol — all backends implement `list_databases`, `switch_database`, and `delete_database`. Default graph name is `"default"` (was `"ephemeral"`).

---

### Visualization — Data Model & Graph Browser (done)

- **NodeView/EdgeView shared models**: Events and HTTP snapshots use the same Pydantic models (`events.py`). `NodeStored` wraps `NodeView`, `EdgeStored` wraps `EdgeView`. Value signals (`novelty`, `confidence`, `relevance`), `source_id`, `extraction_method`, and `created_at` are now included.
- **HTTP endpoints**: `GET /api/graphs` (list + active), `GET /api/snapshot?graph=X` (full node/edge dump). Storage methods `viz_list_nodes`/`viz_list_edges` read cross-graph without switching the active connection. These must never be MCP tools (`grep -r "viz_list_" epimemer/mcp/` enforced).
- **WebSocket protocol**: Per-connection sequence numbers, `GraphSwitched` event, subscription-based graph filtering (`{"subscribe": ["graph-a"]}`).
- **Frontend graph selector**: Dropdown to pick viewed graph, "MCP Graph" label (read-only), "Live"/"Snapshot" mode badge, Refresh button (amber ring on sequence gap). Both panels clear on graph switch. Auto-snapshot on WebSocket reconnect.
- **View-only**: The frontend never modifies MCP state or stored data.

---

### Future — Multi-agent Visualization

Not yet implemented. When multiple MCP servers need to share events:

- Replace `InProcessEventBus` with Redis Pub/Sub or NATS
- Each MCP server publishes events with `graph` tag to shared channel
- Centralized viz server subscribes and routes to frontend connections by subscription
- The WebSocket subscription mechanism already provides the frontend protocol

---

### Missing notebooks

| Notebook | Purpose |
|----------|---------|
| `notebooks/00_foundation.py` | SurrealDB connection, store/retrieve nodes, vector search, type diagrams |
| `notebooks/07_timelines_metacontext.py` | Timeline creation, timepoint linking, metacontext inheritance, filtered search |
| `notebooks/08_orchestration.py` | Full orchestration net visualization, request routing, sub-net invocation |

---

### Benchmarking

No dedicated benchmarking module exists. Structured logging captures latency per tool call, but the plan called for:

- Storage: SurrealDB write throughput (nodes/sec), vector search latency (ms at N vectors)
- Embeddings: throughput (texts/sec for sentence-transformers)
- Segmentation: segments/sec per strategy
- Decomposition: n/a — decomposition is agent-driven, so there are no in-server LLM calls to measure
- Graph construction: time per document, edge count growth rate
- Query: latency at various graph sizes (100, 1K, 10K, 100K nodes)
- Reflection: time at various graph sizes, merge/decay/archive counts
- End-to-end: ingest N documents, query M times, reflect, total time

---

## Topic Assignment Deep Dive

**Retired** — this in-server topic-extraction machinery was removed when ingest
went agent-driven (see *Pivot*). Kept as design history; the calling agent now
performs topic/fact/inference extraction and passes the result to
`store_decomposition`.

| # | Strategy | Status | Notes |
|---|----------|--------|-------|
| 1 | All LLM | Removed | Every segment got LLM topic extraction |
| 2 | Vector-first, LLM-fallback | Removed (was default) | LLM calls decreased as the topic graph grew |
| 3 | All vector (BERTopic-style) | Not implemented | Cluster embeddings, no LLM. Fast but less rich |

---

## How It Comes Together

The end state is:

1. **SurrealDB** running locally, storing the epistemic graph
2. **Epimemer MCP server** running as a process, exposing memory tools
3. **Claude Code** (or another agent harness) configured to use the MCP server
4. **Visualization server** (optional) showing the knowledge graph and pipeline execution in real time
5. The agent can:
   - `segment` + `store_decomposition` to ingest new information (two-step: segment text, then store extracted nodes), optionally with a metacontext
   - `search` to find relevant context (vector + graph hybrid retrieval), optionally filtered by metacontext
   - `query_graph` for structured traversal
   - `reflect` to trigger consolidation (the orchestration net can auto-suggest it once enough new items accumulate)
   - `create_timeline` / `add_timepoint` / `query_timeline` / `create_timelink` for temporal relationships
   - `list_graphs` / `use_graph` / `delete_graph` to manage multiple knowledge graphs (all backends)
6. Every tool call produces:
   - Structured JSON logs (for the user to monitor)
   - Response metadata (for the agent to report what it found/did)
   - Associated metacontexts on all returned nodes (for epistemic clarity)
   - Real-time visualization events (when viz is enabled)
7. The user can:
   - Open any Marimo notebook to inspect the current graph state
   - Open the visualization dashboard (http://127.0.0.1:8765) to browse knowledge graphs, watch live events, and view pipeline execution
   - View logs to see when and how memory was used
   - Query historical graph state via the `as_of` and `query_changes` tools
