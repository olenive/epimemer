# Epimemer: Layered Epistemic Memory System

## Core Concept

A continuously self-organizing semantic memory system that goes beyond traditional knowledge graphs. Rather than storing static triples, the system maintains an evolving dual-space architecture where embeddings provide the semantic foundation and graph structure is derived on top.

The name reflects the system's nature: memory that evolves, restructures, and reinterprets over time.

## Architecture Overview

```
[ Incoming Data ]
        ↓
[ Ingestion Layer ]          — append-only, minimal logic
        ↓
[ Semantic Segmentation ]    — topic-aware, overlapping segments
        ↓
[ Decomposition ]            — extract typed nodes (topics, facts, inferences)
        ↓
[ Representation ]           — embed via pluggable embedding providers
        ↓
[ Graph Construction ]       — link nodes by typed relationships
        ↓
[ Storage Layer ]            — unified or polyglot persistence
        ↓
[ Query Layer ]              — semantic + structural + hybrid retrieval
        ↓
[ Reflection ]               — async consolidation (cluster, merge, prune)
```

## Node Types

Every ingested text is decomposed into three types of nodes:

### Topics
Paragraph-length semantic summaries — not keywords or short labels. Topics act as "soft ontological nodes" that embed well, support clustering, and can evolve over time. They describe the underlying theme of a segment in enough detail to preserve nuance.

### Facts
Atomic, verifiable, grounded statements tied to source material. Minimal ambiguity. Each fact tracks provenance (source, confidence, extraction method).

### Inferences
Higher-level interpretive derivations reasoned from facts and context. Explicitly provisional and revisable. Multiple competing inferences from the same evidence are permitted to coexist. Distinguished from facts to maintain epistemic clarity.

## Dual-Space Design

### Vector Space (semantic)
- Embeddings are the primary representation, not the graph
- Multiple embedding models supported per item, partitioned by `model_id`
- Embeddings are treated as indexed views over data, not the data itself
- Supports A/B testing, migration, and task-specific embeddings without rebuild

### Graph Space (structural)
- Derived from but not dependent on a specific embedding
- Relationships are typed: `about`, `contains`, `implies`, `supports`, `derived_from`, `similarity`, `contradiction`, etc.
- Graph edges store metadata including source model, confidence, and derivation method
- Structure is contextual and interpretive, not "ground truth"

## Segmentation and Topic Assignment

### Segmentation

Text is broken into non-overlapping, variable-length segments aligned to semantic boundaries (not naive fixed-size chunks). Candidate strategies include:
- **Semantic similarity drop** (TextTiling-style) — embed each sentence, cut where cosine similarity between adjacent sentences drops sharply. Cheap, no LLM needed.
- **LLM-guided** — ask an LLM to identify topic boundaries. Most accurate, most expensive.
- **Hybrid** — use embedding similarity to find candidate boundaries, then LLM to refine.

### Topic assignment

The segment-to-topic relationship is **many-to-many**: a segment can be `about` multiple topics, and a topic can span multiple segments. Topic overlap is represented structurally in the graph via edges, not by duplicating text.

**At ingestion** (write fast): per-segment LLM extraction produces one or more paragraph-level topic descriptions per segment. Each becomes a new topic node. No deduplication at this stage — if a topic is described slightly differently across segments, both versions are kept (lazy approach avoids premature commitment).

**At reflect** (organize slow): topic descriptions are embedded and clustered. Similar topics across segments are merged into unified topic nodes, with originals preserved via `merged_into` history edges. Value signals help identify merge candidates — topics with high mutual similarity and many shared segments surface naturally.

## Key Design Principles

### "Write fast, organize slow"
- Ingestion is append-only with minimal processing
- Expensive restructuring (clustering, merging, pruning, centroid updates) happens asynchronously via a `reflect` operation
- Avoids latency spikes and premature structural commitment

### Embeddings are decoupled and pluggable
- Schema supports N embeddings per item
- Never overwrite — always append with `model_id`
- Graph edges are not dependent on a specific embedding model
- Background re-indexing when introducing new models, no downtime

### Provenance & tags on everything
Every node records **where it came from** and optional **labels for filtering**:
- `provenance` — a list of `{source, source_type, source_id, ingested_at}` records,
  system-stamped at ingest from the document's `source`/`source_type` (e.g.
  `"ISSUES.md"` / `document`, `"stripe-api"` / `api`). Makes "which nodes came from
  X" queryable (see `find_nodes`). A merged node carries the union of its sources'.
- `tags` — a list of `{key?, value}` free-text labels (no controlled vocabulary),
  attached by the agent/user at ingest and consolidated later by `reflect`.
- `source_id` — the segment a node was extracted from; `extraction_method` — e.g.
  `agent`, `agent:merge`; `confidence` — 0.0–1.0.

Provenance and tags are *separate from metacontexts*: metacontexts are epistemic
frames that change retrieval scope, whereas provenance and tags are filterable
metadata that do not.

### Test-driven development with analysis and benchmarking
The memory system's correctness is hard to assess during normal use, so development follows a test-driven approach combined with frequent analysis and benchmarking:
- **Unit tests** with mocked LLM calls for each module
- **Marimo notebooks** for interactive step-through and visualization of each Petri net sub-module in action
- **Benchmarking hooks** built into each module from the start — not necessarily measured upfront, but with placeholders and instrumentation so benchmarks can be added incrementally
- See [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) for the phased plan

## Node Value Signals

Every node carries four value signals that are updated during ingestion and used by `reflect` to drive consolidation:

- **Novelty** (0.0–1.0) — how unexpected relative to existing graph state. Contradictions and new topic clusters score high.
- **Confidence** (0.0–1.0) — how well-supported by evidence. Multiple independent sources increase confidence.
- **Relevance** (0.0–1.0) — how connected to frequently-queried topics. Nodes that are regularly retrieved or linked score higher.
- **Recency** (`last_reinforced` timestamp) — when last reinforced by new evidence. Nodes that haven't been touched decay.

These signals are updated at ingestion time (e.g., a new supporting fact increases an inference's confidence and recency). The `reflect` operation uses them to decide what to merge, decay, or flag:

- Low relevance + stale recency → candidate for decay or archival
- High confidence + contradicted by new evidence → surface the conflict
- High novelty + low confidence → flag for attention

## Timelines

Timelines represent temporal structure — when things happened in the world (as opposed to `created_at`/`superseded_at` which track when the *system* learned something).

### Structure

A `Timeline` is a node type that acts as an ordered container of embedded `Timepoint`s. Each Timepoint has:
- **Stable UUID** — immune to reordering, insertion, or value refinement
- **Temporal value** — flexible: concrete datetime/interval (optional `start`/`end`) and/or free-text label (e.g., "during the Renaissance")
- **Position** — managed by the Timeline's ordering logic, not by the Timepoint itself

Other nodes link to specific Timepoints via `TIMELINK` edges. The edge points to the Timeline node and carries a `timepoint_id` in metadata referencing the specific Timepoint within the Timeline.

Topics (and other nodes) connect to their Timelines via `ASSOCIATED_TIMELINE` edges. A node can have multiple associated timelines.

### Multiple Implementations

Different contexts need different backing structures:
- **Precise timelines** (hundreds of dated events) — DataFrame-backed (Polars/Pandas) with a time interval index for efficient range queries and ordering
- **Vague timelines** (ordered events without concrete dates) — list of labeled timepoints, ordering assisted by LLM when needed
- **Cyclical timelines** (recurring events) — represent templates like "weekly standup" or "annual review" separately from concrete instance timelines

All implementations share the same typed interface (Timepoint with stable UUIDs, same edge patterns).

### Properties

- **Shared timepoints**: if two events happen at the same timepoint, they link to the same Timepoint (e.g., Alice and Bob's birthday on May 5th). Different granularity creates separate timepoints ("May 5th" vs "3pm on May 5th").
- **Timeline references**: timelines can reference each other for overlapping periods.
- **Creation**: timelines can be created intentionally or emerge dynamically when enough temporal data accumulates on a topic.
- **Temporal proximity in retrieval**: even if separate timepoints exist for similar times, their proximity on a timeline indicates potential relationships. Retrieval processes should leverage this.

### Stability Guarantees

- **Add timepoint**: existing links unaffected
- **Remove timepoint**: links referencing it become orphaned (detected and flagged)
- **Reorder**: links unaffected (they reference UUID, not position)
- **Refine value**: links unaffected (UUID is stable)

## Metacontext

Metacontext is the epistemic frame that disambiguates different takes, sources, or interpretations of the same information. It answers the question: *in what context is this true?*

### Structure

A `Metacontext` is a node in the graph — similar to a high-level Topic but for disambiguation rather than categorization. Examples:
- "Real historical events" — factual baseline
- "World of Darkness fictional universe" — fictional setting where vampires exist
- "Labour Party — party line" — political perspective
- "Reporting by the BBC" — source framing
- "Propaganda from company XYZ" — source reliability flag

Because metacontexts are nodes, they can relate to each other via the same edge types as other nodes (e.g., "Culture universe" → "science fiction" → "fiction"). They participate in search and retrieval like other nodes.

### Association

- Nodes link to their metacontexts via `HAS_METACONTEXT` edges.
- **Inheritance**: when a document is ingested with a metacontext, all nodes extracted from it inherit that metacontext.
- **Multiple metacontexts per node**: a node can carry multiple metacontexts (e.g., something can be "propaganda" and also "true as far as we know" — these are different axes).
- **No predefined axes**: rather than pre-defining categories (source reliability, fictionality, domain), metacontexts are created, split, and merged dynamically — the same way Topics are managed.

### Impact on Retrieval

- **Always return metacontexts**: every search result should include associated metacontexts to avoid confusion between fiction and fact.
- **Context-aware search**: when the conversation context makes the metacontext obvious (e.g., discussing a specific novel), the retriever should prefer that metacontext. When ambiguous, return multiple results with clear metacontext labels.
- **No silent mixing**: the system should never mix fictional and factual results without surfacing the distinction.

### Why This Matters

The "Fall of Carthage" means different things in a historical metacontext vs. the World of Darkness fictional universe. AI safety capabilities described in a sci-fi novel are different from real-world AI safety research. Political events described by opposing parties carry different framing. Without metacontext, the memory system risks conflating these — silently corrupting retrieval quality.

## Data Model (Minimal)

Fields are either *content* (immutable — corrections create new nodes) or
*metadata* (mutated in place; marked below). See **Node History**.

```
nodes (
  id, type, content, source_id, embedding_id, metadata,   -- content (immutable)
  extraction_method, created_at,                           -- content (immutable)
  provenance,      -- list of Provenance (where this came from); immutable
  status,          -- "active" | "superseded" | "merged"   (mutated in place)
  superseded_at,   -- timestamp, nullable                  (mutated in place)
  novelty,         -- 0.0–1.0, updated continuously        (mutated in place)
  confidence,      -- 0.0–1.0, updated continuously        (mutated in place)
  relevance,       -- 0.0–1.0, updated continuously        (mutated in place)
  last_reinforced, -- timestamp
  tags             -- list of Tag; mutated in place by consolidation
)

provenance entry (
  source,          -- e.g. "ISSUES.md", "stripe-api", "chat#4012"
  source_type,     -- free string; e.g. document | api | chat
  source_id,       -- RawDocument id (or external id), nullable
  ingested_at, metadata
)

tag (
  key,             -- optional dimension (nullable) — enables "filter by key"
  value            -- free text (no controlled vocabulary)
)

documents (
  id, content, source, source_type, metadata, created_at
)

edges (
  src_id, dst_id, type, weight, metadata
  -- type includes: about, contains, implies, supports, abstracts,
  --   derived_from, similarity, contradiction, superseded_by,
  --   merged_into, timelink, associated_timeline, has_metacontext
)

segments (
  id, source_id, text, span_start, span_end
)

embeddings (
  id, item_id, model_id, vector, created_at
)

timelines (
  id, name, description, implementation_type,
  timepoints: [
    { id, start, end, label, metadata }  -- start/end optional (vague timepoints)
  ]
)

metacontexts (
  id, content, description, metadata
  -- a node type; linked to other nodes via has_metacontext edges
)
```

## Node History

Epimemer is append-only for **knowledge content**: a node's `content` (the claim it
encodes), its `source_id`, `created_at`, and `provenance` are never changed. A
correction or consolidation does not modify or delete the existing node — it creates
a new node linked to its predecessor via typed edges:

- **Update**: `node_v1 --superseded_by--> node_v2`
- **Merge**: `node_a --merged_into--> node_c`, `node_b --merged_into--> node_c`

This makes history part of the graph itself rather than a separate versioning system. Traversing history is just following edges backwards.

### What is immutable vs. mutated in place

History is preserved by keeping *content* immutable — but a node also carries
**lifecycle and label metadata** that *is* mutated in place, because it is not the
knowledge claim and editing it rewrites no history:

| Mutated in place | Set by | Why it's not a version |
|---|---|---|
| `status`, `superseded_at` | supersede / merge | this is precisely how a node is *retired* and how "state at time T" is reconstructed |
| `value` signals (novelty / confidence / relevance) | reflection (decay, reinforcement) | a changing salience score, not a changed claim |
| `tags` | reflection (tag consolidation) | free-text labels for filtering, not content |

So "a node is never mutated" is shorthand for "a node's *content* is never mutated".
Mutating metadata uses dedicated in-place storage operations (`update_node_status`,
`set_node_tags`) and never touches the content embedding.

- **Current state** = all nodes with `status = "active"` (no outgoing `superseded_by` or `merged_into` edges)
- **State at time T** = all nodes where `created_at <= T` and (`superseded_at IS NULL` or `superseded_at > T`)

This approach aligns with the existing design:
- Append-only, consistent with "write fast, organize slow"
- Uses the same graph structure, node types, and edge types — no separate versioning layer
- Provenance metadata already tracked on nodes naturally extends to record *why* a new version was created
- Works natively in SurrealDB — it's just more nodes and edges

### Archival

Over time, the graph accumulates superseded and merged nodes that are no longer needed for active queries. Since these nodes are already marked with `status` and `superseded_at`, archival is a straightforward query — export all non-active nodes older than a cutoff date, along with their history edges (`superseded_by`, `merged_into`), to cold storage (flat files, object storage, or a separate DB). Then delete them from the active database.

The active graph is unaffected — every `active` node's content, provenance, and relationships are self-contained. To restore historical state, reimport the archived nodes and edges; since nothing was mutated, they slot back in exactly where they were.

**Embedding cleanup rule**: archive a node's embeddings only when no active node's edges were derived using that embedding.

## Agent Interface (MCP)

Memory is exposed as tools, not as a raw database. Claude Code auto-prefixes these as `mcp__epimemer__<name>`.

Ingestion is a two-step process: `segment` breaks text into chunks, then the agent extracts topics/facts/inferences and passes them to `store_decomposition`.

| Tool                  | Purpose                                        |
|-----------------------|------------------------------------------------|
| `segment`             | Segment text into chunks (step 1 of ingest)    |
| `store_decomposition` | Store extracted nodes and edges (step 2)       |
| `search`              | Semantic + graph retrieval, metacontext-aware   |
| `link`                | Create explicit edges between nodes             |
| `update`              | Create new node version, supersede the old one  |
| `reflect`             | Analyse graph for consolidation opportunities   |
| `apply_reflection`    | Apply agent decisions from reflection analysis  |
| `query_graph`         | Structured traversal from a starting node       |
| `archive`             | Move superseded nodes older than cutoff to cold storage |
| `restore`             | Reimport archived nodes for a time range        |
| `create_timeline`     | Create a named timeline                         |
| `add_timepoint`       | Add a concrete or vague timepoint to a timeline |
| `query_timeline`      | Find nearest timepoints or query a time range   |
| `create_timelink`     | Link a node to a timepoint on a timeline        |
| `create_metacontext`  | Create an epistemic frame for disambiguation    |
| `get_metacontexts`    | Get metacontexts for a node                     |
| `list_graphs`         | List available knowledge graphs                 |
| `use_graph`           | Switch to or create a knowledge graph           |
| `delete_graph`        | Delete a knowledge graph permanently            |

Both `search` and `query_graph` accept an optional `at_time` parameter to query historical graph state.

## Storage

**SurrealDB** is the primary candidate for prototyping — unified documents, vectors, and graph in one system with a single query language (SurrealQL). **Postgres + pgvector** is the pragmatic production fallback. The architecture is storage-agnostic by design.

### Multi-Graph Support

All backends support multiple named graphs. The `StorageBackend` protocol requires `list_databases`, `switch_database`, and `delete_database`. SurrealDB uses separate databases within a namespace; InMemoryStorage uses a dict-of-dicts pattern. The default graph is `"default"`. Agents manage graphs at runtime via the `list_graphs`, `use_graph`, and `delete_graph` tools.

## Update Behaviours

When new data arrives:
1. Generate new segments, topics, facts, inferences
2. Match topics against existing graph via embedding similarity
3. Deduplicate facts via semantic similarity
4. Detect contradictions where possible
5. Allow competing inferences to coexist
6. Threshold-based decisions: merge, link, or create new nodes

## Implementation Approach: Petri Nets via Petritype

### Motivation
The system must not be a black box. A newcomer should be able to look at any part of the pipeline and understand what is happening, what state data is in, and how it flows through processing steps.

### Approach
All key data processing steps are implemented as executable Petri nets using Petritype (`../petritype`, installed locally via uv). Petritype is a Python 3.14+ library that makes Petri nets executable and typed:
- **Places** are typed containers — each place declares a Python type and only holds tokens matching that type (enforced at runtime via typeguard)
- **Transitions** are real Python functions — async supported, with typed inputs/outputs
- **Tokens** are actual data (Pydantic models, primitives, etc.) flowing through the net
- **Execution** is a loop: find enabled transitions → select one (pluggable selectors) → fire it (extract tokens, call function, distribute results)
- **Visualization** is built in via Graphviz — the running system *is* the diagram

Petri nets are a natural fit because:
- The system is fundamentally about data items flowing through states via processing steps
- Concurrency is pervasive (parallel embeddings, async reflection, simultaneous ingestion)
- The type system on places creates natural interfaces between processing stages
- **Type-based output routing** means a decomposition transition that returns a Topic, Fact, or Inference will automatically route each to the correct typed place — branching logic is expressed by the graph structure, not hidden in conditionals
- **Activation functions** can implement the "write fast, organize slow" principle — e.g., the reflect transition only fires when enough unprocessed items accumulate
- **Async transitions** align with the inherently async operations in this system (LLM calls, embedding APIs, database writes)

### Development Strategy
1. Decompose the system into discrete algorithms (segmentation, decomposition, embedding, graph construction, reflection, querying, etc.)
2. Implement each algorithm as its own Petri net
3. Build a **top-level orchestration Petri net** whose transitions invoke the algorithm-level nets

### Composition Model: Nested Petri Nets

The system is a Petri net of Petri nets. Each algorithm is a self-contained `ExecutableGraph` with a clear **interface contract** — typed input and output types that serve as its signature. The orchestration net's transitions call sub-nets via `ExecutableGraphOperations.execute_graph()`.

**The orchestration net** operates on coarse-grained types representing the outputs of whole processes (e.g., `RawDocument` → `SegmentedDocument` → `DecomposedGraph`). It governs what triggers what and what data flows between processes.

**The algorithm nets** operate on fine-grained types internal to each process. They are independently developed, tested, and visualized.

This separation means:
- **Debugging**: zoom into the relevant sub-net to see internal state and data flow
- **Testing**: each algorithm net is testable in isolation with mock tokens
- **Visualization**: the orchestration net shows the big picture; each sub-net shows its own detail
- **Evolution**: swap out or modify an algorithm net without affecting the orchestration layer, as long as the interface types are preserved

Petritype features that support this:
- Async transitions allow the orchestration net to invoke sub-nets without blocking
- Type routing at the orchestration level handles branching between processes
- Activation functions on orchestration transitions gate when processes trigger (e.g., reflect only fires when enough new items accumulate)

### Swappable Strategies via Typed Interfaces

Any algorithm sub-net can have multiple strategy implementations behind the same interface types. The pattern:

1. Define the interface contract — input and output Pydantic models shared by all strategies
2. Implement each strategy as a separate `ExecutableGraph` factory function
3. The orchestration transition selects which factory to invoke (via configuration or runtime decision)

This applies across the system — segmentation, topic extraction, decomposition, embedding, reflection, and querying can all have alternative strategies. Swapping a strategy changes the internal Petri net without affecting the orchestration layer, as long as the typed interface is preserved.

The `@petri_net` decorator supports this by tagging each strategy with metadata (name, description, mode), enabling discovery tooling to enumerate available strategies:

```python
@petri_net(name="segmentation-semantic", mode="manual",
           description="TextTiling-style semantic similarity segmentation")
def semantic_segmentation() -> ExecutableGraph: ...

@petri_net(name="segmentation-llm", mode="manual",
           description="LLM-guided topic boundary detection")
def llm_segmentation() -> ExecutableGraph: ...
```

### Boundary Guideline
Petri nets should be used where they add clarity — algorithms with meaningful internal state, branching, or concurrency. Trivial operations (e.g., a single database write) don't need their own net.

### Data model alignment
The data model types (Topics, Facts, Inferences, Segments, Embeddings) should be defined as Pydantic models. These serve double duty: they are both the storage schema and the Petri net token types, keeping the pipeline and persistence layer in sync.

## Open Questions

- **Incremental clustering**: online HDBSCAN, centroid drift detection, split heuristics
- **Value signal computation**: precise algorithms for computing novelty (relative to what baseline?), relevance decay curves, confidence aggregation from multiple sources
- **Value-driven consolidation thresholds**: how do value signals translate into concrete merge/split/decay decisions?
- **Topic evolution**: value signals provide the inputs (declining relevance = decay, rising novelty = splitting), but the structural mechanisms need design
- **Contradiction handling**: high-novelty contradictions surface automatically via value signals, but the resolution or coexistence strategy needs design
- **Timeline implementation details**: efficient storage and querying of precise timelines (DataFrame-backed), vague timeline ordering heuristics, cyclical timeline template-to-instance mapping
- **Metacontext inheritance scope**: how deep does inheritance go? If a metacontext is inherited from a document, do inferences derived from those facts also inherit it? Probably yes, but edge cases need thought.
- **Metacontext-aware value signals**: does "confidence" mean the same thing in a fictional metacontext (canonicity) vs. factual (likelihood of truth)? May need metacontext-specific interpretation of value signals.
- **Cross-metacontext retrieval**: when a query straddles metacontexts (e.g., "compare real AI with sci-fi AI"), how should retrieval compose results from multiple metacontexts?
