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

Text is broken into non-overlapping, variable-length segments aligned to semantic boundaries (not naive fixed-size chunks). Strategies:
- **Paragraph split** — implemented, the default.
- **Semantic similarity drop** (TextTiling-style) — implemented. Embed each sentence, cut where cosine similarity between adjacent sentences drops sharply. Cheap, no LLM needed.
- **LLM-guided** and **hybrid** (embedding boundaries, LLM refinement) — designed, not built. Both need an LLM, which the server does not call (see *Epimemer makes no LLM calls*), so either the split is delegated to the calling agent or a provider is re-introduced.

### Topic assignment

The segment-to-topic relationship is **many-to-many**: a segment can be `about` multiple topics, and a topic can span multiple segments. Topic overlap is represented structurally in the graph via edges, not by duplicating text.

**At ingestion** (write fast): the calling agent extracts one or more paragraph-level topic descriptions per segment and passes them to `store_decomposition`. Each becomes a new topic node. No deduplication at this stage — if a topic is described slightly differently across segments, both versions are kept (lazy approach avoids premature commitment).

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

### Sources, tags, and relations are nodes & edges
Where knowledge came from and what it's about are modelled as **graph structure**,
not denormalized strings — so a source or tag can carry its own facts, relate to
siblings, and sit in a frame:
- **Source** — every node gets a `sourced_from` edge to its originating
  `RawDocument`; a named publisher/author (`published_by`) is an entity **Topic**.
  "Which nodes came from X" is a traversal (see `find_nodes`).
- **Tags are Topics** — a tag name resolves (by exact name) to a Topic linked by a
  `tagged_with` edge, so tag consolidation *is* topic-merge.
- **Relations are open vocabulary** — engine edges are a typed enum; user relations
  use one `RELATED` sentinel with a free `label` and a `kind`
  (`relationship` followed in retrieval / `attribution` not). Behaviour is finite
  and hardcoded; the vocabulary is open. Synonymous labels consolidate via
  `reflect` → `apply_reflection relation_merges`.

These are *separate from metacontexts*: metacontexts are epistemic frames that
change retrieval scope; sources/tags/relations are structure that (for sources and
attribution) is deliberately not expanded in default retrieval.

### Epimemer makes no LLM calls
Ingest is the two-step `segment` → `store_decomposition` flow: the server splits
text and stores what it is given, and the **calling agent** does the
topic/fact/inference extraction. An earlier design ran decomposition inside the
server behind an LLM abstraction (Pydantic AI + mock), with a hybrid
vector-first / LLM-fallback topic assignment; that path was removed. The server
therefore has no API keys, no model choice, and no per-ingest LLM latency of its
own, and anything requiring a judgement call is the agent's to make.

### Test-driven development with analysis and benchmarking
The memory system's correctness is hard to assess during normal use, so development follows a test-driven approach combined with frequent analysis and benchmarking:
- **Unit tests** for each module, with a mock embedding provider so no model is downloaded
- **Marimo notebooks** for interactive step-through and visualization of each Petri net sub-module in action
- **Benchmarking hooks** built into each module from the start — not necessarily measured upfront, but with placeholders and instrumentation so benchmarks can be added incrementally

## Node Value Signals

Every node carries a `ValueSignal`. One member is a score, one is a judgment, and two are clocks — and the split is deliberate: **a score can be computed, a judgment cannot, and use is an event rather than either.**

- **Confidence** (0.0–1.0) — *intended as* how well-supported by evidence. **Not yet written by anything**: every node is created at 0.5, so the topic-merge comparison that reads it to pick a primary description is always a tie. Decided but not built (#46): it becomes a **caller-supplied prior** — the ingesting agent's judgment, on a four-value ladder, nullable so that "unrated" is distinguishable from "rated ordinary" — with per-source levels on the provenance edge. The corroboration half of its old documented promise moved out to a read-time derivation (#51).
- **Importance** (0.0–1.0) — *does this matter?* Moved only by the `judge_importance` tool, in either direction, asymptotically toward its bound, and every move records a reason. Nothing automatic touches it: a decayed judgment would be a number nobody stands behind.
- **`retrieved_at`** — null until a search returns the node, then the time it last did. *Is this being used?*
- **`importance_judged_at`** — null until someone judges it. What ages is not the judgment but confidence in its *currency*, which is what the `stale_judgment` archival class reads.

Both clocks are nullable because "never" and "long ago" are different states, and only a nullable timestamp can tell them apart.

A merge collapses several nodes into a fresh one, so its signal is built by `merged_value_signal` — max importance and confidence, and **the later of each clock**, with null losing to any real timestamp. Carrying the number without its date would be worse than losing both: the merged node would claim a judgment nobody made, and since `stale_judgment` reads the *pair*, an unjudged node is never stale and the merged node stayed exempt from every archival class forever (#45). One shared function, because a merge rebuilds the signal field by field and silently resets whatever it forgets to name.

`reflect` reads these to nominate candidates — it never writes them:

- Never retrieved + not judged important + nothing depending on it → archival candidate
- Judged important, but judged long ago and never revisited → hand back to review

That is the whole of it today. The other `reflect` phases — consolidation,
splitting, enrichment, contradiction detection, relation consolidation — key off
embeddings, edge shape and text length, not off value signals, and they will
keep doing so while confidence remains a constant (#46).

> **Two scores were removed rather than fixed, for the same underlying reason: a stored number was answering a question that only makes sense at the moment it is asked.**
>
> A decaying **Relevance** score fell on every `reflect`, so it measured how often an operator ran `reflect` as much as it measured the node. `retrieved_at` answers the same question without that confound.
>
> **Novelty** was meant as how unexpected a node is relative to existing graph state, and was never computed — every node was created at 1.0. Computing it at ingest would not have rescued it: the same content is unexpected arriving into an empty graph and unremarkable arriving into a mature one, so a stored answer records arrival order and then freezes. The word also quietly conflated two things — *new to the graph*, which `created_at` already gives exactly, and *unlike what is known*, which is the one anybody wanted. The latter is well-posed whenever it is asked against the graph as it stands, and the nearest-neighbour distance `vector_search` returns answers it with no field, no migration and a current baseline. **"Surprise" is the better name for the concept** and is used for it below; it says unexpectedness rather than newness, and it carries its own precondition — surprising *relative to what*. Reserved for a caller-supplied signal if one is ever wanted, since an observer-relative name fits a reported judgment (as `importance` is) and misfits a computed one.

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
- **Vague timelines** (ordered events without concrete dates) — list of labeled timepoints, ordered by the calling agent when the labels alone are ambiguous
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
  status,          -- "active" | "corrected" | "historical" |
                   -- "merged" | "archived"                 (mutated in place)
                   -- ("superseded" is the pre-#53 legacy value: retired by
                   --  supersession, reason unrecorded. Nothing writes it now.)
  superseded_at,   -- timestamp, nullable                  (mutated in place)
  confidence,      -- 0.0–1.0, nothing writes it yet (#46) (mutated in place)
  importance,      -- 0.0–1.0, moved only by judgment      (mutated in place)
  retrieved_at          -- timestamp, null until first retrieval
  importance_judged_at  -- timestamp, null until an agent judges it
  -- source_id is the Segment for text-derived nodes; entity/tag Topics have none.
  -- Sources and tags are NOT fields — they are Topics/RawDocuments reached by edges.
)

documents (
  id, content, source, source_type, metadata, created_at
)

edges (
  src_id, dst_id, type, label, kind, weight, metadata
  -- engine types: about, contains, implies, supports, abstracts, derived_from,
  --   similarity, contradiction, subtopic_of, superseded_by, merged_into,
  --   timelink, associated_timeline, has_metacontext, tagged_with, sourced_from
  -- user relations: type = related, with a free `label` and a `kind`
  --   (relationship | attribution)
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

- **Update**: `node_v1 --superseded_by--> node_v2`, and `node_v1.status` records *why* — `corrected` (it was wrong) or `historical` (it was right, and remains right of its period). The caller must say which; there is no default, because filing a change in the world as an error is how a graph forgets its own history (#53).
  - **Designed, not built (#53 T2):** the *edge* splits too. A correction keeps `superseded_by` and is terminal; a world-change gets a new `temporally_followed_by` and is **reversible**, because a claim can become true again and recurrence falsifies "replaced" while leaving "came after" true. `historical` becomes restorable, `corrected` does not, and ingest must surface historical twins instead of creating a duplicate node.
- **Merge**: `node_a --merged_into--> node_c`, `node_b --merged_into--> node_c`

This makes history part of the graph itself rather than a separate versioning system. Traversing history is just following edges backwards.

### What is immutable vs. mutated in place

History is preserved by keeping *content* immutable — but a node also carries
**lifecycle and label metadata** that *is* mutated in place, because it is not the
knowledge claim and editing it rewrites no history:

| Mutated in place | Set by | Why it's not a version |
|---|---|---|
| `status`, `superseded_at` | supersede / merge | this is precisely how a node is *retired*, and how "what the graph held at time T" is reconstructed — **transaction time, not validity**: it says when belief changed, never when the claim was true (#53) |
| `value.confidence` | creation default; topic merge combines it via `merged_value_signal`, clocks included | a changing salience score, not a changed claim |
| `importance`, `importance_judged_at` | `judge_importance` | a recorded assessment of the same claim, with its own provenance trail |
| `retrieved_at` | `search` | a record that the node was read, not a change to what it says |
| edge `label` (user relations) | reflection (relation consolidation) | edges are not versioned; relabelling a synonym is a plain update |

So "a node is never mutated" is shorthand for "a node's *content* is never mutated".
Mutating metadata uses dedicated in-place storage operations (`update_node_status`,
`relabel_edges`) and never touches the content embedding. (Sources and tags are now
Topics linked by edges, so they consolidate by topic-merge, not in-place mutation.)

- **Current state** = all nodes with `status = "active"` (no outgoing `superseded_by` or `merged_into` edges)
- **State at time T** = all nodes where `created_at <= T` and (`superseded_at IS NULL` or `superseded_at > T`)

This approach aligns with the existing design:
- Append-only, consistent with "write fast, organize slow"
- Uses the same graph structure, node types, and edge types — no separate versioning layer
- Provenance metadata already tracked on nodes naturally extends to record *why* a new version was created
- Works natively in SurrealDB — it's just more nodes and edges

### Archival

Over time, the graph accumulates retired and merged nodes that are no longer needed for active queries. Since these nodes are already marked with `status` and `superseded_at`, archival is a straightforward query — export the eligible non-active nodes older than a cutoff date, along with their history edges (`superseded_by`, `merged_into`), to cold storage (flat files, object storage, or a separate DB). Then delete them from the active database. **`historical` nodes are excluded**: they were retired because the world changed, not because they were wrong, so they stay true of their period and age alone is not grounds to discard them.

The active graph is unaffected — every `active` node's content, provenance, and relationships are self-contained. To restore historical state, reimport the archived nodes and edges; since nothing was mutated, they slot back in exactly where they were.

**Embedding cleanup rule**: archive a node's embeddings only when no active node's edges were derived using that embedding.

## Agent Interface (MCP)

Memory is exposed as tools, not as a raw database. Claude Code auto-prefixes these as `mcp__epimemer__<name>`.

Ingestion is a two-step process: `segment` breaks text into chunks, then the agent extracts topics/facts/inferences and passes them to `store_decomposition`. Epimemer does not decompose text itself — that is the calling agent's job.

The tools group into: **core memory** (`segment`, `store_decomposition`, `search`, `link`, `update`, `supersede_by`); **discovery & stats** (`query_graph`, `topic_tree`, `find_nodes`, `list_sources`, `list_relations`, `graph_stats`); **conflict handling** (`check_conflicts`, `record_contradiction`, `record_variant`); **reflection** (`reflect`, `configure_reflection`, `apply_reflection`); **temporal access** (`as_of`, `query_changes`); **archival** (`archive`, `restore`); **timelines** (`create_timeline`, `add_timepoint`, `query_timeline`, `create_timelink`); **metacontexts** (`create_metacontext`, `get_metacontexts`); and **graph management** (`list_graphs`, `use_graph`, `delete_graph`).

See [INTEGRATION.md](INTEGRATION.md#available-tools) for the canonical table with one-line descriptions and the authoritative tool count — this document intentionally does not restate the count so it can only drift in one place.

Historical graph state is read with the dedicated `as_of` (a lifecycle snapshot at a past instant) and `query_changes` (births and retirements across a window) tools — not via an `at_time` parameter on `search`/`query_graph`.

## Storage

**SurrealDB** is the primary candidate for prototyping — unified documents, vectors, and graph in one system with a single query language (SurrealQL). **Postgres + pgvector** is the pragmatic production fallback. The architecture is storage-agnostic by design.

### Multi-Graph Support

All backends support multiple named graphs. The `StorageBackend` protocol requires `list_databases`, `switch_database`, and `delete_database`. SurrealDB uses separate databases within a namespace; InMemoryStorage uses a dict-of-dicts pattern. The default graph is `"default"`. Agents manage graphs at runtime via the `list_graphs`, `use_graph`, and `delete_graph` tools.

### Scaling Limits

Several read paths are O(N) in the number of active nodes and do per-node edge fetches: `list_sources` / `list_relations`, `reflect`'s pending-review gather and split/enrichment loops, and `search`'s per-node frame/label enrichment. Over a websocket to SurrealDB each item is a round-trip.

These limits are now **measured** rather than estimated — see [dev-docs/BENCHMARKS.md](dev-docs/BENCHMARKS.md) for data and ISSUES.md #14 for the analysis. Against the 30 s default tool timeout (`EPIMEMER_TOOL_TIMEOUT_SECONDS`), the operations fail at roughly:

| Operation | in-memory | SurrealDB (loopback) |
|---|---|---|
| `search` | ~10M nodes | not reachable (flat, ~135 ms) |
| `reflect` | ~7,400 nodes | **~3,200 nodes** |
| `list_sources` | ~1M nodes | ~29,000 nodes |

So: `reflect` is the limiting operation on both backends, and everything else has been pushed past any size worth quoting. Ingest is flat and not a concern. Don't point a large persistent graph at this unwarned.

These figures depend on two optimisations worth knowing about, because the naive form of each is what a reader would otherwise expect: in-memory edge lookups go through endpoint indexes (`by_src` / `by_dst` in `storage/memory.py`) rather than scanning the edge set, and SurrealDB's `vector_search` ranks before filtering by status rather than filtering inside the ranking query — SurrealDB re-runs such a subquery per row, which cost `search` two orders of magnitude. What remains under `search` on SurrealDB is ~120 ms of per-result enrichment round-trips, the N+1 pattern ISSUES.md #14 tracks.

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
- **Transition guards** (paired with priorities) can implement the "write fast, organize slow" principle — e.g., the reflect transition only fires when enough unprocessed items accumulate
- **Async transitions** align with the inherently async operations in this system (embedding models, database writes, hub event publishing)

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
- Guards on orchestration transitions gate when processes trigger (e.g., reflect only fires when enough new items accumulate)

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

@petri_net(name="segmentation-paragraph", mode="manual",
           description="Paragraph-boundary segmentation")
def paragraph_segmentation() -> ExecutableGraph: ...
```

### Boundary Guideline
Petri nets should be used where they add clarity — algorithms with meaningful internal state, branching, or concurrency. Trivial operations (e.g., a single database write) don't need their own net.

### Data model alignment
The data model types (Topics, Facts, Inferences, Segments, Embeddings) should be defined as Pydantic models. These serve double duty: they are both the storage schema and the Petri net token types, keeping the pipeline and persistence layer in sync.

## Open Questions

- **Incremental clustering**: online HDBSCAN, centroid drift detection, split heuristics
- **Value signal computation**: decided 2026-08-12, not yet built (#46, #51). The documented promise — "how well-supported by evidence" *and* "multiple independent sources increase confidence" — was two claims wanting opposite storage. Support becomes a **caller-supplied prior** on a four-value ladder, with per-source levels on the `sourced_from` edge rather than a dict on the node, so a level cannot outlive the source it describes. Corroboration is **derived at read time** from distinct publishers over a similarity neighbourhood, and never written back. (Neither decay curves nor novelty are among these any more — both signals were removed rather than tuned, and the "relative to what baseline?" that dogged novelty is answered by asking at read time instead of storing an answer. See the removal note under *Node Value Signals*.)
- **Value-driven consolidation thresholds**: how do value signals translate into concrete merge/split decisions? Archival thresholds are settled (importance ceiling, judgment age); merge and split still key off embedding similarity alone.
- **Topic evolution**: the structural mechanisms need design. The input a split wants is *surprise* — how unlike the material a topic already holds a new member is — which is a read-time question over embeddings rather than a stored field. It is also nearly free where it would be asked: `reflect` already builds the block-wise similarity matrix over every topic and fact (`pair_scoring.similar_pairs`), and a per-row max over that same matrix is one reduction on data already in hand
- **Contradiction handling**: contradictions surface today via embedding similarity plus an LLM judgment; the resolution or coexistence strategy needs design
- **Timeline implementation details**: efficient storage and querying of precise timelines (DataFrame-backed), vague timeline ordering heuristics, cyclical timeline template-to-instance mapping
- **Metacontext inheritance scope**: how deep does inheritance go? If a metacontext is inherited from a document, do inferences derived from those facts also inherit it? Probably yes, but edge cases need thought.
- **Metacontext-aware value signals**: *answered 2026-08-12 (#46)* — the scale is the same, the record it measures against is the frame's. A fictional fact can honestly score 0.9: the question is how well that frame's material backs the claim, not whether the frame is real. Left here because the reasoning matters — without it an agent conflates "is this true?" with "does the frame assert this?", every fiction node lands at the bottom of the scale, and confidence quietly becomes a fiction detector, duplicating badly what metacontexts already carry.
- **Temporal validity — the "Saint Petersburg Problem"** (ISSUES.md #53, the largest open gap): **the graph cannot say *when* a claim was true.** Nodes carry ingest time and supersession time; neither is validity. Saint Petersburg → Petrograd → Leningrad → Saint Petersburg were all correct, and the model can only record such a pair as a contradiction or a supersession, both wrong. Validity is also a *set* of intervals rather than one — a party in government over five separate spans — which rules out a simple `valid_from`/`valid_to` pair. It propagates: supersession files historical truth as error, contradiction detection is unsound in both directions, corroboration inflates, fact dedup cannot be made safe, and **inference can combine claims that were never simultaneously true**. See `dev-docs/REVIEW_EPISTEMIC.md` §13.
  - **Design status (2026-08-12): split into T1 / T2 / T3, and T1 is decided.** The vocabulary is fixed — this is **valid time**, as against the **transaction time** `created_at`, `superseded_at` and `as_of` already record. Validity is a new type carried **on the `sourced_from` edge, per source** (beside #46's per-source confidence, for the same reason), measured against a named **timeline** rather than a metacontext, with endpoints distinguishing *unknown* from *unbounded*, read back per source with **no default collapse**. `RawDocument` gains an optional `published_at`, with no fallback to `created_at`. Nothing is built yet. **T2 is also decided**: status and intervals answer different questions and both happen, so there is no forced choice — the split is in the edge (`superseded_by` for corrections, terminal; `temporally_followed_by` for world-changes, reversible), which fills the review loop's long-missing sixth verdict. **T3 is decided too, so the design is complete and none of it is built**: `HISTORICAL` nodes are returned by a default `search` (with lineage collapse, or ranking fills with versions of one claim), `CORRECTED` nodes are reachable but off by default, valid-time queries return **buckets** (*provably valid* / *unknown*) rather than a filter — a filter would turn missing metadata into a silent false negative — and **`as_of` is renamed `graph_as_of`**, reserving `valid_as_of`, because the unmarked name inherits the wrong default reading. Full statement in ISSUES.md #53 → *T1 decided*; shape and consequences in `REVIEW_EPISTEMIC.md` §13.8.
- **Cross-metacontext retrieval**: when a query straddles metacontexts (e.g., "compare real AI with sci-fi AI"), how should retrieval compose results from multiple metacontexts?
