# Epimemer Implementation Plan

## Principles

- **Test-driven**: each module gets unit tests with mocked LLM/DB calls before implementation is considered complete
- **Observable**: each module gets a Marimo notebook for interactive step-through and visualization of its Petri net
- **Benchmarkable**: instrumentation and placeholders from the start, even where benchmarks aren't yet defined
- **Modular**: every external dependency (LLM, embedding model, database) is behind an abstraction layer
- **Incremental**: each phase produces something testable and demonstrable before the next begins

## Dependencies & Tooling

- **Python 3.14+** (required by Petritype)
- **uv** for package management
- **Petritype** (`../petritype`, local install)
- **SurrealDB** for storage (behind abstraction layer)
- **Pydantic AI** for LLM abstraction — structured outputs validated against the same Pydantic models used as Petri net tokens. Supports Claude, OpenAI, Gemini, local models.
- **Sentence Transformers** for initial embedding provider
- **Marimo** for interactive analysis notebooks
- **pytest** + **pytest-asyncio** for testing

---

## Phase 0: Foundation

### Scope
Project structure, shared types, storage abstraction, SurrealDB adapter, LLM abstraction, embedding abstraction.

### Modules

#### 0.1 Project setup
- `pyproject.toml` with all dependencies
- Directory structure:
  ```
  epimemer/
    core/           # shared Pydantic models (node types, edge types, etc.)
    storage/        # storage abstraction + SurrealDB adapter
    llm/            # LLM abstraction (via Pydantic AI)
    embeddings/     # embedding abstraction + sentence-transformers adapter
    pipelines/      # Petri net sub-modules (one per algorithm)
    orchestration/  # top-level orchestration net
    mcp/            # MCP server
    logging/        # structured logging + feedback
  notebooks/        # Marimo notebooks (one per module)
  tests/
  ```

#### 0.2 Core types (`epimemer/core/`)
Pydantic models that serve as both storage schema and Petri net token types:
- `RawDocument`, `Segment`
- `Topic`, `Fact`, `Inference`
- `NodeEdge` (typed edges with metadata)
- `EmbeddingRecord` (item_id, model_id, vector)
- `ValueSignal` (novelty, confidence, relevance, last_reinforced)

#### 0.3 Storage abstraction (`epimemer/storage/`)
- Abstract interface: `StorageBackend` protocol class
  - `store_node()`, `get_node()`, `query_nodes()`
  - `store_edge()`, `get_edges()`, `traverse()`
  - `store_embedding()`, `vector_search()`
  - `archive()`, `restore()`
- SurrealDB adapter implementing the protocol
- In-memory adapter for tests

#### 0.4 LLM abstraction (`epimemer/llm/`)
- Pydantic AI agents configured to return the core Pydantic models directly
- `DecompositionAgent` — takes text, returns `list[Topic | Fact | Inference]`
- `SegmentationAgent` — takes text, returns `list[Segment]` (for LLM-guided strategy)
- Mock implementations for tests that return deterministic outputs

#### 0.5 Embedding abstraction (`epimemer/embeddings/`)
- `EmbeddingProvider` protocol: `embed(texts: list[str], model_id: str) -> list[vector]`
- Sentence Transformers adapter
- Mock adapter for tests

### Tests
- [ ] Core types: validation, serialization round-trip, type checking as Petri net tokens
- [ ] Storage: CRUD operations via in-memory adapter
- [ ] Storage: same tests against SurrealDB adapter (integration, can be skipped in CI)
- [ ] LLM: mock agent returns valid Pydantic models
- [ ] Embeddings: mock provider returns correct-dimension vectors
- [ ] Embeddings: sentence-transformers adapter produces real vectors (integration)

### Marimo notebook: `notebooks/00_foundation.py`
- Verify SurrealDB connection
- Store and retrieve sample nodes/edges/embeddings
- Run a vector similarity search
- Visualize a simple type relationship diagram

### Benchmarking notes
- Instrument storage operations with timing decorators from the start
- Placeholder: SurrealDB write throughput (nodes/sec), vector search latency (ms at N vectors)
- Placeholder: embedding throughput (texts/sec for sentence-transformers)

---

## Phase 1: Segmentation

### Scope
Break raw text into non-overlapping, variable-length segments aligned to semantic boundaries. Multiple strategies behind a shared interface.

### Modules

#### 1.1 Segmentation interface
- Input type: `RawDocument`
- Output type: `list[Segment]`
- Each strategy is an `ExecutableGraph` factory decorated with `@petri_net`

#### 1.2 Strategy: semantic similarity (TextTiling-style)
Petri net:
```
[RawDocument] → split_sentences → [Sentences]
[Sentences] → compute_similarities → [SentenceSimilarities]
[SentenceSimilarities] → detect_boundaries → [BoundaryIndices]
[BoundaryIndices] + [Sentences] → form_segments → [Segments]
```
No LLM calls — pure embedding + computation.

#### 1.3 Strategy: LLM-guided
Petri net:
```
[RawDocument] → llm_segment → [Segments]
```
Uses `SegmentationAgent` from Phase 0.

#### 1.4 Strategy: hybrid
Petri net:
```
[RawDocument] → split_sentences → [Sentences]
[Sentences] → compute_similarities → [CandidateBoundaries]
[CandidateBoundaries] + [RawDocument] → llm_refine_boundaries → [Segments]
```

### Tests
- [ ] Semantic strategy: known text with clear topic shift produces boundary at expected location
- [ ] Semantic strategy: single-topic text produces one segment
- [ ] LLM strategy: mock agent returns expected segments (deterministic)
- [ ] Hybrid strategy: candidate boundaries are passed to LLM for refinement
- [ ] All strategies: output type is `list[Segment]` with valid spans
- [ ] All strategies: segments are non-overlapping and cover the full text
- [ ] Petri net: tokens flow through all places correctly (inspect place states after execution)

### Marimo notebook: `notebooks/01_segmentation.py`
- Load sample texts (short, medium, long; single-topic, multi-topic)
- Run each strategy side by side
- Visualize the Petri net execution step by step (using Petritype's Graphviz animation)
- Display segments with boundaries highlighted in the original text
- Compare segment boundaries across strategies

### Benchmarking notes
- Placeholder: segments/sec for each strategy
- Placeholder: boundary quality metric (requires labeled data — defer to later)
- Instrument: number of LLM calls per document for hybrid strategy (should decrease over time? No — this is per-document. Note for Phase 2 topic assignment instead.)

---

## Phase 2: Decomposition & Topic Assignment

### Scope
Extract Topics, Facts, and Inferences from segments. Assign topics using a hybrid vector-first / LLM-fallback approach.

### Modules

#### 2.1 Decomposition sub-net
Petri net:
```
[Segment] → extract_nodes → [Topic | Fact | Inference]
```
The `extract_nodes` transition uses the `DecompositionAgent` (Pydantic AI). Type-based output routing sends each result to the correct typed place.

#### 2.2 Topic assignment: hybrid strategy
This is the key optimization — avoid calling the LLM for every segment once the topic graph has content.

```
[Segment] → embed_segment → [EmbeddedSegment]
[EmbeddedSegment] → match_existing_topics → [MatchedSegment | UnmatchedSegment]
[MatchedSegment] → link_to_existing → [StoredLinks]
[UnmatchedSegment] → llm_extract_topic → [NewTopic]
[NewTopic] → store_topic → [StoredTopic]
```

**Vector-first**: embed the segment, compare to existing topic embeddings. If similarity > threshold → assign to existing topic.
**LLM-fallback**: if no match → ask LLM to generate a new paragraph-level topic description.

Over time, as the topic graph grows, most segments match existing topics and LLM calls decrease.

#### 2.3 Fact and inference extraction
Always uses LLM (these are harder to do with pure vector approaches):
```
[Segment] → extract_facts → [list[Fact]]
[Segment] → extract_inferences → [list[Inference]]
```

### Tests
- [ ] Decomposition: mock LLM returns valid Topic, Fact, Inference models
- [ ] Decomposition: type routing sends each to correct place
- [ ] Topic matching: segment similar to existing topic links to it (vector cosine > threshold)
- [ ] Topic matching: segment dissimilar to all existing topics triggers LLM fallback
- [ ] Topic matching: threshold edge cases (just above, just below)
- [ ] Fact extraction: mock returns atomic, grounded statements
- [ ] Inference extraction: mock returns interpretive statements distinguished from facts
- [ ] Provenance: all nodes carry source_id, confidence, extraction_method
- [ ] Value signals: novelty set high for new topics, lower for matched existing topics

### Marimo notebook: `notebooks/02_decomposition.py`
- Feed sample segments through decomposition
- Visualize the Petri net step by step
- Show extracted Topics / Facts / Inferences with provenance
- Demonstrate topic matching: show which segments matched existing topics vs. triggered LLM
- Track LLM call count as the topic graph grows (feed multiple documents sequentially)

### Benchmarking notes
- **LLM call reduction**: track ratio of vector-matched vs LLM-fallback topic assignments over time. This is a key efficiency metric.
- Placeholder: decomposition latency per segment (LLM-bound)
- Placeholder: topic matching accuracy (requires labeled data — defer)
- Instrument: similarity scores for matched topics (distribution analysis in notebook)

---

## Phase 3: Graph Construction & Value Marking

### Scope
Create typed edges between nodes. Initialize and update value signals. Store everything via the storage layer.

### Modules

#### 3.1 Edge creation sub-net
```
[Topic] + [Segment] → create_about_edge → [NodeEdge]
[Fact] + [Segment] → create_contains_edge → [NodeEdge]
[Inference] + [Segment] → create_implies_edge → [NodeEdge]
[Fact] + [Topic] → create_supports_edge → [NodeEdge]
[Inference] + [Topic] → create_abstracts_edge → [NodeEdge]
[Fact] + [Inference] → create_derived_from_edge → [NodeEdge]
```

Which edges to create depends on co-occurrence and semantic similarity between the extracted nodes.

#### 3.2 Value signal initialization
When nodes are created:
- **Novelty**: high if no similar existing node, low if close match exists
- **Confidence**: based on extraction method and LLM confidence (if available)
- **Relevance**: initialized to baseline, updated on query
- **Recency** (`last_reinforced`): set to `created_at`

#### 3.3 Value signal update at ingestion
When new data arrives that relates to existing nodes:
- Supporting evidence → increase confidence, update recency
- Contradicting evidence → increase novelty on both the new and existing nodes
- Re-encountered topic → increase relevance, update recency

#### 3.4 Storage persistence
Store all nodes, edges, embeddings, and value signals via the storage abstraction.

### Tests
- [ ] Edge creation: correct edge types for each node pair combination
- [ ] Edge creation: edges carry metadata (source, confidence)
- [ ] Value signals: novelty is high for novel content, low for redundant content
- [ ] Value signals: confidence increases when supporting evidence arrives
- [ ] Value signals: contradictions increase novelty on both sides
- [ ] Storage round-trip: store and retrieve complete graph (nodes + edges + embeddings + values)
- [ ] Node history: update creates new version with `superseded_by` edge, original preserved

### Marimo notebook: `notebooks/03_graph_construction.py`
- Ingest a sequence of related documents
- Visualize the growing graph (nodes, edges, types)
- Show value signals as node colors/sizes
- Step through the Petri net for edge creation
- Demonstrate node versioning: show history chain after updates

### Benchmarking notes
- Placeholder: graph construction time per document
- Placeholder: edge count growth rate (should be sub-quadratic)
- Instrument: value signal distributions (histogram of novelty/confidence/relevance across all active nodes)

---

## Phase 4: Query Layer

### Scope
Semantic search, graph traversal, and hybrid retrieval.

### Modules

#### 4.1 Vector search
```
[QueryText] → embed_query → [QueryEmbedding]
[QueryEmbedding] → vector_search → [CandidateNodes]
```

#### 4.2 Graph expansion
```
[CandidateNodes] → expand_via_graph → [ExpandedContext]
```
k-hop traversal from candidate nodes, weighted by edge type and value signals.

#### 4.3 Hybrid retrieval
```
[QueryText] → embed_query → [QueryEmbedding]
[QueryEmbedding] → vector_search → [CandidateNodes]
[CandidateNodes] → expand_via_graph → [ExpandedContext]
[ExpandedContext] → rerank → [RankedResults]
```
Optional cross-encoder reranking.

#### 4.4 Temporal queries
Filter by `at_time` parameter to retrieve historical graph state.

### Tests
- [ ] Vector search: returns nodes semantically similar to query
- [ ] Vector search: respects `node_types` filter (only Topics, only Facts, etc.)
- [ ] Graph expansion: traverses edges from candidate nodes, returns connected subgraph
- [ ] Graph expansion: respects depth limit
- [ ] Hybrid: combines vector + graph results
- [ ] Temporal: `at_time` excludes nodes created after that time, includes superseded nodes active at that time
- [ ] Value-weighted: higher-relevance nodes rank higher in results

### Marimo notebook: `notebooks/04_query.py`
- Load a populated graph (from Phase 3 notebook or fixture)
- Interactive query box: type a question, see results
- Visualize: which nodes were found by vector search, which were added by graph expansion
- Show value signals of returned nodes
- Demonstrate temporal query: same query at different timestamps

### Benchmarking notes
- Placeholder: query latency at various graph sizes (100, 1K, 10K, 100K nodes)
- Placeholder: retrieval quality (precision/recall — requires labeled query-answer pairs, defer)
- Instrument: number of nodes visited in graph expansion, vector search time vs graph expansion time

---

## Phase 5: Reflection

### Scope
Async consolidation: topic merging, cluster refinement, value signal decay, archival.

### Modules

#### 5.1 Topic consolidation
```
[ActiveTopics] → cluster_topics → [TopicClusters]
[TopicClusters] → merge_similar → [MergedTopics]
```
Embed all active topic descriptions, cluster (HDBSCAN or simpler), merge clusters where appropriate. Original topics preserved via `merged_into` edges.

#### 5.2 Value signal decay
```
[ActiveNodes] → apply_decay → [UpdatedNodes]
```
Nodes not reinforced recently have relevance decreased. Decay curve TBD (linear, exponential, or step function).

#### 5.3 Contradiction detection
```
[HighNoveltyFacts] → find_contradictions → [ContradictionPairs]
```
Surface pairs of facts with high semantic similarity but opposing content. Uses embedding similarity + LLM verification.

#### 5.4 Archival
```
[SupersededNodes] → filter_by_age → [ArchiveCandidates]
[ArchiveCandidates] → check_embedding_deps → [SafeToArchive]
[SafeToArchive] → export_to_cold_storage → [ArchivedNodes]
```

### Tests
- [ ] Topic merging: two similar topics merge into one, originals get `merged_into` edges
- [ ] Topic merging: dissimilar topics are not merged
- [ ] Value decay: unreinforced nodes have reduced relevance after decay pass
- [ ] Value decay: recently reinforced nodes are unaffected
- [ ] Contradiction detection: opposing facts surface as pairs
- [ ] Archival: superseded nodes older than cutoff are exported
- [ ] Archival: active nodes are never archived
- [ ] Archival: embeddings referenced by active edges are not archived

### Marimo notebook: `notebooks/05_reflection.py`
- Load a graph with redundant topics and stale nodes
- Run reflection step by step
- Visualize: before/after graph with merged topics highlighted
- Show value signal changes (decay, reinforcement)
- Demonstrate archival: which nodes were exported, graph size before/after

### Benchmarking notes
- Placeholder: reflection time at various graph sizes
- Placeholder: topic merge quality (requires manual review — defer)
- Instrument: number of merges, number of nodes decayed, number of nodes archived per reflection pass

---

## Phase 6: MCP Server & Logging

### Scope
Standalone MCP server exposing the memory tools. Structured logging. Feedback metadata in tool responses.

### Modules

#### 6.1 MCP server (`epimemer/mcp/`)
Implements the tool interface from the summary:

| Tool | Triggers |
|------|----------|
| `memory.ingest` | Segmentation → Decomposition → Graph Construction (phases 1-3) |
| `memory.search` | Query Layer (phase 4) |
| `memory.link` | Direct edge creation |
| `memory.update` | Node versioning with `superseded_by` |
| `memory.reflect` | Reflection pipeline (phase 5) |
| `memory.query_graph` | Graph traversal |
| `memory.archive` | Archival pipeline |
| `memory.restore` | Cold storage reimport |

#### 6.2 Structured logging (`epimemer/logging/`)
- Emit structured JSON logs for every MCP tool invocation
- Log: tool name, timestamp, input summary, output summary, latency, nodes touched, LLM calls made
- Configurable log level and destination (file, stdout)

#### 6.3 Response metadata
Every MCP tool response includes a `_meta` field:
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
The agent can surface this in conversation (e.g., "Found 5 relevant nodes — 2 topics, 2 facts, 1 inference").

### Tests
- [ ] MCP server: each tool callable via MCP protocol
- [ ] MCP server: invalid inputs return structured errors
- [ ] Logging: every tool call produces a structured log entry
- [ ] Logging: log contains timing, node counts, LLM call counts
- [ ] Response metadata: `_meta` field present and accurate on all tool responses
- [ ] End-to-end: ingest → search → verify ingested content is retrievable

### Marimo notebook: `notebooks/06_mcp.py`
- Start MCP server
- Call each tool programmatically
- Display structured logs
- Show response metadata
- Demonstrate the full ingest → search cycle

### Benchmarking notes
- Placeholder: end-to-end latency for each tool
- Placeholder: throughput (ingestions/sec, queries/sec)
- Instrument: all timing already captured by logging layer

---

## Phase 8: Orchestration & Integration

### Scope
Top-level orchestration Petri net. End-to-end integration with Claude Code.

### Modules

#### 8.1 Orchestration net
The parent Petri net that composes all sub-nets:
```
[IncomingRequest] → route_request → [IngestRequest | SearchRequest | ReflectRequest | ...]
[IngestRequest] → run_ingestion_pipeline → [IngestResult]
[SearchRequest] → run_query_pipeline → [SearchResult]
[ReflectRequest] → run_reflection_pipeline → [ReflectResult]
...
```
Activation functions gate when reflection triggers (e.g., enough new items since last reflect).

#### 8.2 Claude Code integration
- MCP server configuration for Claude Code (`~/.claude/settings.json`)
- System prompt guidance for when/how the agent should use memory tools
- Test with real conversations

### Tests
- [ ] Orchestration: ingestion request flows through segmentation → decomposition → graph construction
- [ ] Orchestration: search request flows through query pipeline
- [ ] Orchestration: reflect fires only when activation condition is met
- [ ] Integration: Claude Code can call memory tools via MCP
- [ ] Integration: agent receives response metadata and can report what it found

### Marimo notebook: `notebooks/08_orchestration.py`
- Visualize the orchestration Petri net
- Feed a sequence of requests (ingest, ingest, search, reflect, search)
- Show how sub-nets are invoked at each step
- Display the full graph state after each operation
- Show logging output alongside

### Benchmarking notes
- End-to-end benchmarks: ingest N documents, query M times, reflect, measure total time and quality
- Memory growth: graph size over time with realistic usage patterns
- LLM cost tracking: total LLM calls across all operations

---

## Phase 7: Timelines & Metacontext

### Scope
Two new structural capabilities: temporal scaffolding (Timelines) and epistemic framing (Metacontext). Both are node types with specialized edge types and retrieval behaviors.

### Modules

#### 7.1 Timeline types and storage (`epimemer/core/`)

New core types:
- `Timepoint(BaseModel)`: `id` (UUID), `start` (datetime | None), `end` (datetime | None), `label` (str | None), `metadata` (dict)
- `Timeline(BaseModel)`: `id`, `name`, `description`, `timepoints` (ordered list of Timepoint), `implementation_type` (Literal["precise", "vague", "cyclical"])
- New `EdgeType` values: `TIMELINK`, `ASSOCIATED_TIMELINE`, `HAS_METACONTEXT`
- `Metacontext(BaseModel)`: `id`, `content`, `description`, `metadata`, value signal, status (same lifecycle as other nodes)

Storage protocol additions:
- `store_timeline()`, `get_timeline()`, `update_timeline()` (add/remove/reorder timepoints)
- `query_timelines()` — by associated topic, by time range
- Timelink edges use standard edge storage with `timepoint_id` in metadata

#### 7.2 Timeline implementations (`epimemer/pipelines/timeline/`)

Multiple backing implementations behind a shared protocol:
- **PreciseTimeline**: backed by sorted list with datetime interval index. Supports efficient range queries, insertion, and temporal proximity search. (Consider Polars/Pandas for large timelines.)
- **VagueTimeline**: ordered list of labeled timepoints. Ordering assisted by LLM when needed. Supports relative ordering queries ("before/after").
- **CyclicalTimeline**: template-based (e.g., "every Monday", "annually in spring"). Maps to concrete instances when linked to specific events.

Each implementation must support:
- Add/remove/reorder timepoints (with stable UUIDs)
- Find timepoints near a given time (temporal proximity search)
- Detect overlapping intervals
- Serialize/deserialize for storage

#### 7.3 Metacontext types and propagation (`epimemer/core/`, `epimemer/pipelines/`)

- `Metacontext` as a node type with the same lifecycle as Topics (active/superseded/merged, value signals)
- `HAS_METACONTEXT` edges from any EpistemicNode to a Metacontext
- **Inheritance at ingestion**: when a document is ingested with a metacontext, all extracted nodes automatically get `HAS_METACONTEXT` edges
- **Split/merge**: metacontexts can be consolidated during reflection, same as topics
- **Retrieval**: search results always include associated metacontexts; retriever can filter or prefer specific metacontexts

#### 7.4 MCP tool updates

- `memory.ingest` gains optional `metacontext` parameter (name or ID) — all extracted nodes inherit it
- `memory.search` gains optional `metacontext` filter — restrict results to a specific epistemic frame
- New tool: `memory.timeline` — create/query timelines, add timelinks
- Search results include metacontext labels in response

#### 7.5 Timeline-aware retrieval

- Temporal proximity search: "find facts near March 2023 on the AI History timeline"
- Timeline traversal: walk a timeline forward/backward from a timepoint, collecting linked nodes
- Cross-timeline correlation: find events on different timelines that overlap temporally

### Tests

- [ ] Timepoint: stable UUIDs survive add/remove/reorder
- [ ] PreciseTimeline: range queries, insertion ordering, proximity search
- [ ] VagueTimeline: LLM-assisted ordering, label-based lookup
- [ ] CyclicalTimeline: template mapping, instance creation
- [ ] Metacontext: node creation, inheritance at ingestion, multiple metacontexts per node
- [ ] Metacontext: split/merge during reflection
- [ ] Retrieval: metacontext always included in search results
- [ ] Retrieval: metacontext filter excludes non-matching results
- [ ] Timelink: edge creation, timepoint_id in metadata, orphan detection on timepoint removal
- [ ] MCP: memory.ingest with metacontext parameter
- [ ] MCP: memory.search with metacontext filter
- [ ] MCP: memory.timeline tool
- [ ] End-to-end: ingest with metacontext → search filtered by metacontext → verify isolation

### Marimo notebook: `notebooks/07_timelines_metacontext.py`
- Create a Timeline, add timepoints, link facts to timepoints
- Visualize a timeline with its linked nodes
- Demonstrate metacontext inheritance from ingestion
- Show metacontext-filtered search vs. unfiltered
- Cross-metacontext retrieval example (real vs. fictional)

### Benchmarking notes
- Timeline query performance: range queries on precise timelines with 100, 1000, 10000 timepoints
- Metacontext filtering overhead on search latency
- Memory overhead of metacontext edges per node

---

## Topic Assignment Deep Dive

A key optimization is reducing LLM dependency for topic assignment. Three approaches on a spectrum:

### 1. All LLM
Every segment gets an LLM call to generate topic descriptions. Most accurate, most expensive. Appropriate for early development and as the baseline to benchmark against.

### 2. Vector-first, LLM-fallback (recommended default)
1. Embed the new segment
2. Compare to all existing topic embeddings (vector search)
3. If `max_similarity > threshold` → assign segment to existing topic(s) — no LLM call
4. If no match → LLM generates new topic description

**Properties:**
- LLM calls decrease over time as the topic graph grows
- Most segments eventually match existing topics
- Only genuinely novel content triggers LLM
- The threshold is a tunable parameter — too low = over-merging, too high = redundant topics
- Can assign to multiple existing topics if several are above threshold

### 3. All vector (BERTopic-style)
Cluster segment embeddings, generate topic labels from cluster statistics (TF-IDF of cluster members, or take the centroid's nearest document). No LLM calls at all. Topic descriptions are less rich but the approach is fast and deterministic.

### Recommendation
Start with approach 1 (all LLM) for correctness, implement approach 2 as the primary strategy, keep approach 3 as an option for high-throughput scenarios. All three are swappable strategies behind the same typed interface.

The Marimo notebook for Phase 2 should track LLM call count across all three approaches on the same dataset to quantify the cost reduction.

---

## LLM Abstraction via Pydantic AI

Pydantic AI provides model-agnostic structured outputs. The decomposition step benefits directly:

```python
from pydantic_ai import Agent

decomposition_agent = Agent(
    'claude-sonnet-4-20250514',
    output_type=list[Topic | Fact | Inference],  # validates against our core models
    system_prompt="Extract topics, facts, and inferences from the given text segment..."
)

result = await decomposition_agent.run(segment.text)
nodes = result.output  # list of validated Pydantic models, directly usable as Petri net tokens
```

**Benefits:**
- Swapping models is one line: `'claude-sonnet-4-20250514'` → `'openai:gpt-4o'` → `'gemini-2.0-flash'`
- Output validation against our Pydantic models — invalid LLM output raises immediately
- Built-in retry logic for malformed outputs
- The returned models are the same types used as Petri net tokens and storage schema — no translation layer

**For tests:** Pydantic AI supports `TestModel` which returns deterministic outputs without making real API calls. This plugs directly into the mock LLM strategy.

---

## How It Comes Together

The end state is:

1. **SurrealDB** running locally, storing the epistemic graph
2. **Epimemer MCP server** running as a process, exposing memory tools
3. **Claude Code** (or another agent harness) configured to use the MCP server
4. The agent can:
   - `memory.ingest` new information (triggers the full pipeline via orchestration net), optionally with a metacontext
   - `memory.search` to find relevant context (vector + graph hybrid retrieval), optionally filtered by metacontext
   - `memory.query_graph` for structured traversal
   - `memory.reflect` to trigger consolidation (or this fires automatically via activation functions)
   - `memory.timeline` to create/query timelines and temporal relationships
5. Every tool call produces:
   - Structured JSON logs (for the user to monitor)
   - Response metadata (for the agent to report what it found/did)
   - Associated metacontexts on all returned nodes (for epistemic clarity)
6. The user can:
   - Open any Marimo notebook to inspect the current graph state
   - Step through any sub-net to understand what happened
   - View logs to see when and how memory was used
   - Query historical graph state via `at_time` parameters
