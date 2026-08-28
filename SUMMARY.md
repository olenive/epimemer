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
[ Semantic Segmentation ]    — topic-aware, non-overlapping segments
        ↓
[ Decomposition ]            — extract typed nodes (topics, facts, inferences)
        ↓
[ Representation ]           — embed via pluggable embedding providers
        ↓
[ Graph Construction ]       — link nodes by typed relationships
        ↓
[ Storage Layer ]            — unified or polyglot persistence
        ↓
[ Query Layer ]              — semantic + lexical + structural, rank-fused
        ↓
[ Reflection ]               — async consolidation (cluster, merge, prune)
```

## Node Types

Every ingested text is decomposed into three types of nodes:

### Topics
Paragraph-length semantic summaries — not keywords or short labels. Topics act as "soft ontological nodes" that embed well, support clustering, and can evolve over time. They describe the underlying theme of a segment in enough detail to preserve nuance.

### Facts
Atomic, verifiable, grounded statements tied to source material. Minimal ambiguity. Each fact tracks provenance (source, extraction method) and may carry a confidence prior — the ingesting agent's reading of how well the record backs the claim, supplied once and never measured. A fact also carries a **claim kind** — `state` (a condition holding over a period) or `event` (an occurrence) — judged at ingest and read by deduplication, which merges states and never events. Nullable, and an unjudged fact simply never merges.

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
- Graph edges carry a `weight` and a free-form `metadata` dict — the dict *can* hold a source model or a derivation method, but nothing writes a confidence there today. Per-source support levels on the `sourced_from` edge remain unbuilt, and the node's own confidence prior is a different number answering a different question
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
  and hardcoded; the vocabulary is open. A label also has a **record** — an id, a
  description, and the thing a decision about it can name — so `reflect`
  nominates likely synonyms and `apply_reflection relation_verdicts` records
  what was decided about a pair, which is what stops it being offered on every
  pass. **Nothing rewrites a label.** Consolidation by bulk relabel was removed
  on 2026-08-28: edges are not versioned, so it was the one irreversible
  operation in the system, spent on the one thing that affects no retrieval.

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

That includes **when a claim was true**: a node may carry validity intervals, and
ingest is the only place they can come from, since tense and the dates written in
the text are visible there and nowhere afterwards. They are supplied per node,
land on its `sourced_from` edge, and are marked `stated` or `inferred` — a date
the agent knows from world knowledge and the document does not give is neither,
and must not be supplied at all.

### Test-driven development with analysis and benchmarking
The memory system's correctness is hard to assess during normal use, so development follows a test-driven approach combined with frequent analysis and benchmarking:
- **Unit tests** for each module, with a mock embedding provider so no model is downloaded
- **Marimo notebooks** for interactive step-through and visualization of each Petri net sub-module in action
- **Benchmarking hooks** built into each module from the start — not necessarily measured upfront, but with placeholders and instrumentation so benchmarks can be added incrementally

## Node Value Signals

Every node carries a `ValueSignal`. One member is a score, one is a judgment, and two are clocks — and the split is deliberate: **a score can be computed, a judgment cannot, and use is an event rather than either.**

- **Confidence** (0.0–1.0, nullable) — how well the record would back a claim up if it were challenged. A **caller-supplied prior**, never computed: only the ingesting agent has read the material, so it is supplied at `store_decomposition` on a four-value ladder (0.3 hedged or partisan / 0.5 default, omit it / 0.7 established / 0.9 primary or authoritative), with an optional one-line `confidence_basis` in node metadata saying why a non-default value was chosen. **Omitting it stores absence, not 0.5**, so "nobody assessed this" and "assessed, and ordinary" are different states — the same reason both clocks below are nullable. Code that ranks or compares reads absence as 0.5 via `rated_confidence`; code that displays or relays passes it through — the merge rule (where an unrated signal loses to a rated one), the dict a caller reads, and the visualisation, whose tooltip prints a dash rather than a number nobody supplied. The corroboration half of its old documented promise moved out to a read-time derivation. Per-source levels on the provenance edge remain unbuilt.
- **Importance** (0.0–1.0) — *does this matter?* Moved only by the `judge_importance` tool, in either direction, asymptotically toward its bound, and every move records a reason. Nothing automatic touches it: a decayed judgment would be a number nobody stands behind.
- **`retrieved_at`** — null until a search returns the node, then the time it last did. *Is this being used?*
- **`importance_judged_at`** — null until someone judges it. What ages is not the judgment but confidence in its *currency*, which is what the `stale_judgment` archival class reads.

Both clocks are nullable because "never" and "long ago" are different states, and only a nullable timestamp can tell them apart.

A merge collapses several nodes into a fresh one, so its signal is built by `merged_value_signal` — max importance and confidence, and **the later of each clock**, with null losing to any real value. Max confidence looks wrong for a supplied prior until you see what it pairs with: in a topic merge the higher-confidence description becomes the merged node's *primary* content, so the number describes the text the node leads with, and breaking either half makes the pair lie. **A fact merge pairs it differently:** the agent writes fresh content for the survivor, so there is no winning description to point at — instead the `confidence_basis` of whichever source supplied the kept confidence travels with it into the survivor's metadata, since a prior separated from its reason is the state that guidance exists to prevent. Carrying the number without its date would be worse than losing both: the merged node would claim a judgment nobody made, and since `stale_judgment` reads the *pair*, an unjudged node is never stale and the merged node stayed exempt from every archival class forever. One shared function, because a merge rebuilds the signal field by field and silently resets whatever it forgets to name.

`reflect` reads these to nominate candidates — it never writes them:

- Never retrieved + not judged important + nothing depending on it → archival candidate
- Judged important, but judged long ago and never revisited → hand back to review

That is the whole of it today. The other `reflect` phases — splitting,
enrichment, contradiction detection, recurrence detection, the soundness check,
Boundary proposals, relation consolidation — key off embeddings, edge shape, text
length and validity intervals rather than value signals. Topic consolidation is
the exception: it picks the primary description by
confidence, a comparison that was a permanent tie while every node sat at the
constant 0.5.

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

A Timeline also carries an optional **`reference_time`** — that clock's own *now*,
set via `set_reference_time`. It is what makes "current" answerable on a timeline
that is not wall-clock: a fictional claim is current when its interval contains
*that timeline's* reference time. Any code asking whether a claim holds now must
ask against the relevant clock rather than reaching for `datetime.now()`.

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
- **Inheritance**: a document is ingested *with* a metacontext, and every node extracted from it inherits that metacontext. There is no frame-inherits-frame machinery — a reader that wants two frames names two.
- **Multiple metacontexts per node**: a node can carry multiple metacontexts (e.g., something can be "propaganda" and also "true as far as we know" — these are different axes).
- **No predefined axes**: rather than pre-defining categories (source reliability, fictionality, domain), metacontexts are created, split, and merged dynamically — the same way Topics are managed.
- **Absence names no frame:** a node with no `has_metacontext` edge is a node nobody said anything about — what absence means everywhere else here (an omitted `confidence` is unrated, an absent `judged_by` is unknown, an omitted `claim_kind` is unjudged). Nothing is inferred from silence, so an agent that ingested fiction and said nothing leaves claims nobody has framed rather than a graph asserting fiction as fact.
- **The consequence is deliberate**: a frameless node shares a frame with **nothing** — never compared, never merged, and returned by no scoped search. It is reachable only on a graph written before the requirement, `graph_stats.nodes_without_frame` counts them, and `epimemer frames declare` is how a graph stops holding any.
- **`the-real` is a convention, not a mechanism**: the id every graph should use for the frame holding real-world claims, so two graphs do not end up with one frame under two strings. Nothing reads it specially, and it must exist like any other frame — `create_metacontext` takes a chosen id for exactly this.
- **The frame is required at ingest:** `store_decomposition` takes `metacontext_id` as a required argument. It does not prevent a wrong frame — a reflexive answer is as wrong as silence was — but it makes the error *recoverable*: the frame is an edge carrying the judge who wrote it and a journal row naming it, so `review` finds it and `reframe` fixes it. One frame per call, so a mixed document is two calls; a per-node override belongs beside the per-node `importance` and `confidence`, and is deliberately not foreclosed.
- **Search names the frames it wants, as a list**: results are nodes standing in **any** of them. No frame inherits another — a question about a novel's world read against real history names both. Omitting the list searches every frame, which is a coherent question rather than an unstated assumption, and is why the read side is optional where ingest is not.
- **Nothing invents a frame on a node's behalf**: splits inherit what the parent states, a synthesised parent inherits the one set its children all stand in and is refused when they differ, and a merge **re-states** the survivor's frame under the merging agent's judge rather than migrating an edge somebody else wrote — the survivor's content is synthesised, so no source's framing was made about that wording. Union is never the answer: one node asserted in two worlds is the worst outcome available.
- **A stated metacontext must exist in the graph you are in:** ids are per graph, and `store_decomposition` and `search` both refuse one that resolves nowhere — every id in a search's list, not just the first. Unchecked, the framing edge points at nothing and the node shares a frame with *no other node*: never compared, never merged, and absent from every frame-scoped search including the one that was meant.

### Impact on Retrieval

- **Always return metacontexts**: every search result should include associated metacontexts to avoid confusion between fiction and fact.
- **Context-aware search**: when the conversation context makes the metacontext obvious (e.g., discussing a specific novel), the retriever should prefer that metacontext. When ambiguous, return multiple results with clear metacontext labels.
- **No silent mixing**: the system should never mix fictional and factual results without surfacing the distinction.

### Why This Matters

The "Fall of Carthage" means different things in a historical metacontext vs. The World of Darkness fictional universe. AI safety capabilities described in a sci-fi novel are different from real-world AI safety research. Political events described by opposing parties carry different framing. Without metacontext, the memory system risks conflating these — silently corrupting retrieval quality.

## Retrieval

`search` is one tool with several arms behind it. Full detail:
[docs/RETRIEVAL.md](docs/RETRIEVAL.md).

### Two arms, because they fail in opposite directions

Embedding similarity has **no notion of term rarity**. The default model splits an
identifier like `JIRA-4417` into word pieces and mean-pools them with the rest of
the sentence, so the query embeds to roughly "short alphanumeric string" — close
to *every other ticket id in the graph*. The failure is not that the right node
ranks low; it is that the wrong ones rank about equally high.

So a keyword arm (BM25) runs alongside the vector arm, over **two corpora**: node
content, and the raw **segments** text was extracted from. The second matters
because the calling agent writes the fact content — if it paraphrases the
identifier away, no search of any kind recovers it from nodes. The corpora answer
different questions: nodes are *what do I believe?*, segments are *where did I
read that?*

Callers declare the exact strings that matter as `terms`. A term is matched whole
and terms are ORed; **each declared term's best hit survives to the final result**
even where fusion would have cut it.

### Fusion is by rank, never by score

Cosine similarity and BM25 are on incomparable scales, so any weighted sum is a
magic number that gets re-tuned forever. Reciprocal Rank Fusion uses only ranks
(`1/(60 + rank)`), which is also the only quantity that may legitimately cross a
corpus boundary — BM25's IDF is computed per index.

### Every result says how it was reached

Each node carries `provenance`: `lexical` (a term matched its content), `segment`
(a term matched its source passage), `vector` (similarity), `expanded` (reached by
an edge from one of those), or `direct` (returned unranked, by tools that do not
rank). A node found by more than one route gets the **most specific** label.

Flattening this to a boolean would throw away the most useful thing hybrid
retrieval produces: *this matched at 0.82; that one was dragged in by an edge from
it; this third came back on an exact token match* is the question actually being
asked when a search disappoints.

### History returns by default, folded

Knowledge that is not current is still knowledge, so `historical` claims come back
by default; `corrected` ones are reachable but off. Default-on requires **lineage
collapse** — a retired claim is near-identical text to its replacement, so without
folding, one claim with four predecessors fills half a top-10. The replacement
takes the slot and the earlier versions attach to it.

### Valid time answers in groups, never as a filter

`valid_as_of` returns two buckets — `valid` and `unknown` — and excludes nothing.
A filter would turn missing metadata into a silent false negative. See *Valid
Time* below.

### Corroboration is asked for, not assumed

`include_corroboration=True` adds how many **independent publishers** back each
result. It answers a question `confidence` cannot: `confidence` is a prior about
the material supplied at ingest, corroboration is a fact about the graph, so it
is derived at read time and never stored — a stored count is an answer frozen
against a baseline nothing records.

It counts *independence*, not strength: three hedged reports from three outlets
score 3, exactly as three confident ones would. **A claim about another period is
not a second witness:** where source dates put a similar node provably
outside the subject's periods, it stops counting and is returned separately as
`adjacent_periods` — nothing leaves the graph, both claims stay true of their
own stretch, and the caller is told what was set aside rather than left with a
number that quietly shrank. It is off by default because it
is the most expensive annotation on the retrieval path, and because its cost
rises with the density of `similarity` edges — it would grow fastest on the
graphs where it says most.

**Until 2026-08-22 it said less than that implies**, and the reason is worth
knowing before you read any count taken earlier: nothing *wrote* a `similarity`
edge, so the neighbourhood was the node itself and
the number was a count of publishers behind one node — correct and cheap rather
than wrong, but not the cross-restatement reading described here.
`apply_reflection(similarities=[…])` now writes one, and only on an agent's
explicit `one_claim` verdict. Its companion `assessed` edge, written for **both**
verdicts, is deliberately not read here: it records that a pair was judged, which
is not a claim that the judgment agreed. A verdict can be **withdrawn** — record
`distinct` on the pair and the count comes back down, via a
`retracted_similarity` edge that disqualifies the standing one rather than
deleting it. Once, and one way: nothing re-asserts a withdrawn verdict. See
[docs/RETRIEVAL.md](docs/RETRIEVAL.md).

## Data Model (Minimal)

Fields are either *content* (immutable — corrections create new nodes) or
*metadata* (mutated in place; marked below). See **Node History**.

```
nodes (
  id, type, content, source_id, embedding_id, metadata,   -- content (immutable)
  extraction_method, created_at,                           -- content (immutable)
  claim_kind,      -- facts only: "state" | "event" | null (content, immutable)
                   -- judged at ingest; null is unjudged, and never merges
  status,          -- "active" | "corrected" | "historical" |
                   -- "merged" | "archived"                 (mutated in place)
                   -- ("superseded" is the legacy value: retired by
                   --  supersession, reason unrecorded. Nothing writes it now.)
  superseded_at,   -- timestamp, nullable                  (mutated in place)
  lifecycle,       -- append-only list of episodes: retired_at, because,
                   -- counterpart, restored_at. A node can leave the active set
                   -- more than once (recurrence), and the (status,
                   -- superseded_at) pair is a single slot that cannot say
                   -- "retired, then came back". query_changes reads this
  confidence,      -- 0.0–1.0, nullable; supplied at ingest,   (mutated in place)
                   -- absent when unrated, read as 0.5
  importance,      -- 0.0–1.0, moved only by judgment      (mutated in place)
  retrieved_at          -- timestamp, null until first retrieval
  importance_judged_at  -- timestamp, null until an agent judges it
  -- source_id is the Segment for text-derived nodes; entity/tag Topics have none.
  -- Sources and tags are NOT fields — they are Topics/RawDocuments reached by edges.
)

documents (
  id, content, source, source_type, metadata, created_at,
  published_at   -- imprecise instant, nullable. When the document was
                 -- published, as against created_at, which is when it was
                 -- ingested. Never falls back to created_at
)

edges (
  src_id, dst_id, type, label, kind, weight, metadata,
  validity       -- list of intervals, `sourced_from` edges only. When *this
                 -- source* asserts the claim held: per source, never unioned
                 -- onto the node, so one careful source and one sloppy one
                 -- cannot produce a period neither claims
  -- engine types: about, contains, implies, supports, abstracts, derived_from,
  --   similarity, contradiction, subtopic_of, superseded_by,
  --   temporally_followed_by, merged_into,
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

- **Update**: `node_v1 --superseded_by--> node_v2` for a correction, `node_v1 --temporally_followed_by--> node_v2` for a world-change, and `node_v1.status` records the same *why* — `corrected` (it was wrong) or `historical` (it was right, and remains right of its period). The caller must say which; there is no default, because filing a change in the world as an error is how a graph forgets its own history.
  - The status also decides **which edges follow the replacement**. A correction hands over everything but history and review — the retired node is an audit husk and the replacement is the same claim, corrected. A world-change hands over the frame and the tags only: the historical node keeps its own provenance, because it is still true of its period and its sources are what say so. Judgments — `similarity`, `contradiction`, `variant_of` — stay on the node they were made about under **every** retirement, correction and merge included: the claim may survive a correction, but the wording the judgment was made against does not, and a merged survivor's content is synthesised. `migration_disposition(edge_type, status)` is the whole rule.
  - **The edge splits the same way.** A correction keeps `superseded_by` and is terminal; a world-change writes `temporally_followed_by`, which states order rather than replacement and so survives a claim becoming true again. `lineage_edge_type_for(status)` is the rule, paired with `superseded_status_for(because)` so the node and the edge cannot disagree. The edge never claims adjacency — Saint Petersburg → Petrograd → Leningrad → Saint Petersburg is three separately observed transitions — so cycles and parallel same-direction edges are legal, and nothing may dedup them by `(src, dst, type)`.
  - **Recurrence is built (2026-08-19)** — the reversibility the split exists to enable. `historical` is restorable and `corrected` is not (`RESTORABLE_STATUSES`), and similarity nomination now sees historical candidates (`vector_search(statuses=…)`, `NOMINATED_STATUSES`), which is what makes the **`recurs`** verdict reachable at all: the guard saying retired nodes must never resurface was also what hid the twin. `check_conflicts` returns each candidate's status — telling `redundant` from `recurs` *is* that distinction — and `reflect` reports mixed pairs under `recurrences`, apart from `contradictions`, since a claim beside its own successor is not in conflict with it. `restore` reactivates a named node and writes the new source's `sourced_from` edge in one transaction; without naming that source it refuses, because a claim back to active with no edge saying who asserts it is one the graph states and cannot attribute. `store_decomposition` reports `historical_twins` as a cheap verbatim floor.
- **Merge**: `node_a --merged_into--> node_c`, `node_b --merged_into--> node_c`

This makes history part of the graph itself rather than a separate versioning system. Traversing history is just following edges backwards.

### What is immutable vs. mutated in place

History is preserved by keeping *content* immutable — but a node also carries
**lifecycle and label metadata** that *is* mutated in place, because it is not the
knowledge claim and editing it rewrites no history:

| Mutated in place | Set by | Why it's not a version |
|---|---|---|
| `status`, `superseded_at` | supersede / merge | this is precisely how a node is *retired*, and how "what the graph held at time T" is reconstructed — **transaction time, not validity**: it says when belief changed, never when the claim was true |
| `value.confidence` | the ingesting agent's prior at `store_decomposition`, or absent; a topic or fact merge combines it via `merged_value_signal`, clocks included | supplied once at creation and never re-set — a correction mints a new node rather than rewriting this one, which is why the basis beside it is a single line and not a trail |
| `importance`, `importance_judged_at` | `judge_importance` | a recorded assessment of the same claim, with its own provenance trail |
| `retrieved_at` | `search` | a record that the node was read, not a change to what it says |

So "a node is never mutated" is shorthand for "a node's *content* is never mutated".
Mutating metadata uses a dedicated in-place storage operation
(`set_node_status_tx`) and never touches the content embedding. (Sources and tags are now
Topics linked by edges, so they consolidate by topic-merge, not in-place mutation.)

- **Current state** = all nodes with `status = "active"` (no outgoing `superseded_by`, `temporally_followed_by` or `merged_into` edges)
- **State at time T** = all nodes where `created_at <= T` and (`superseded_at IS NULL` or `superseded_at > T`)

This approach aligns with the existing design:
- Append-only, consistent with "write fast, organize slow"
- Uses the same graph structure, node types, and edge types — no separate versioning layer
- Provenance metadata already tracked on nodes naturally extends to record *why* a new version was created
- Works natively in SurrealDB — it's just more nodes and edges

### Archival

Over time, the graph accumulates retired and merged nodes that are no longer needed for active queries. Since these nodes are already marked with `status` and `superseded_at`, archival is a straightforward query — export the eligible non-active nodes older than a cutoff date, along with their history edges (`superseded_by`, `temporally_followed_by`, `merged_into`), to cold storage (flat files, object storage, or a separate DB). Then delete them from the active database. **`historical` nodes are excluded**: they were retired because the world changed, not because they were wrong, so they stay true of their period and age alone is not grounds to discard them.

The active graph is unaffected — every `active` node's content, provenance, and relationships are self-contained. To restore historical state, reimport the archived nodes and edges; since nothing was mutated, they slot back in exactly where they were.

**Embedding cleanup rule**: archive a node's embeddings only when no active node's edges were derived using that embedding.

## Valid Time — when a claim was true

The section above is **transaction time**: when the graph learned something. This
is the other axis, and conflating them was the largest correctness gap the system
has had. Full detail: [docs/VALIDITY.md](docs/VALIDITY.md).

**The Saint Petersburg problem.** Saint Petersburg was Petrograd was Leningrad was
Saint Petersburg. Every one of those was true. A model that can record such a pair
only as a *contradiction* or a *correction* is wrong in both directions — it files
historical truth as error, removes it from the active set, and lets an inference
combine claims that were **never simultaneously true**, with nothing to detect it.

### Where validity lives

On the **`sourced_from` edge, per source** — never on the node, and never
collapsed at read time. A claim with two sources has two periods, and both
collapses lie undetectably: union takes one careful source and one sloppy one and
yields a period *neither* claims; intersection turns two separate episodes into
"never".

An interval carries endpoints, a timeline, a witness point, and a `basis`:

- **Endpoints distinguish four kinds** — `precise`, `named` ("during the
  Renaissance"), **`unknown`**, and **`unbounded`**. *"We don't know when it
  started"* and *"it had no start"* are different claims, and one nullable
  datetime cannot tell them apart.
- **Measured against a named timeline**, not a metacontext. There is no conversion
  between an in-universe date and a real one, so periods on different clocks are
  simply not comparable — and a timeline's own `reference_time` is what "current"
  means on that clock.
- **`basis` is `stated` or `inferred`.** A date the agent knows from world
  knowledge and the document does not give is *neither*, and must not be supplied
  at all.
- **Comparison answers four values** — `before`, `after`, `overlap`, **`unknown`**
  — and the fourth is the point: every consumer must treat it as *we cannot tell*
  rather than folding it into a false.

Ingest is the only place intervals can come from: tense and the dates written in
the text are visible there and nowhere afterwards. Everything downstream reads.

### Correction and world-change are different events

The lifecycle splits on which happened, and the split runs through the status, the
lineage edge, and edge migration together — `corrected` / `superseded_by`,
terminal; `historical` / `temporally_followed_by`, **reversible**. The caller must
say which, because filing a change in the world as an error is how a graph forgets
its own history.

The succession edge never claims adjacency, so cycles and parallel same-direction
edges are legal and nothing may deduplicate them by `(src, dst, type)` — which
makes cycle-safety a requirement of every walker over it.

**Recurrence** is the reversibility this exists to enable: a `historical` claim can
become true again. `restore` reactivates it and writes the new source's edge in one
transaction, refusing without it — a claim back to active with no edge saying who
asserts it is one the graph states and cannot attribute.

### Reading it back, and checking it

- `search` returns each node's `validity` per source, and `valid_as_of` answers in
  buckets rather than filtering. There is deliberately **no third bucket**: an
  interval asserts nothing about the world outside itself, so no moment is
  provably *not* valid, which makes a valid-time filter unimplementable rather
  than merely misleading.
- **The soundness check** flags an active inference whose premises no source puts
  in the same period, reporting the offending pairs with their dates rather than a
  verdict. It is silent whenever a pair cannot be placed — a check on evidence,
  never on ignorance.
- **Boundary proposals** are the other half of "ingest extracts, reflect
  proposes". A document cannot know its claim will ever stop being true, so only
  something seeing the *next* document can close the first period. Publication
  dates are deliberately never used: they bound when a claim was *asserted*, so
  closing Leningrad's period at a 2000 publication would have the graph assert the
  city was called Leningrad in 1995.

## Reflection

`reflect` **reads and never writes.** It scans the graph, nominates candidates,
and hands them back; every change goes through `apply_reflection`, and the
judgment in between belongs to the agent — or, for the consequential calls, a
human. Full detail: [docs/REFLECTION.md](docs/REFLECTION.md).

The principle underneath is one line: **embeddings are a good candidate generator
and a poor judge.** Similarity nominates *these two facts are about the same
thing*; only an agent can answer *do they contradict, supersede, or coexist?* So
`reflect` returns pairs with their scores rather than verdicts.

Ten phases, each a worklist: topic consolidation, split detection, enrichment,
contradiction detection, recurrence detection, the temporal soundness check,
Boundary proposals, the pending-review worklist, archival nomination, and relation
consolidation.

Two separations in that list are load-bearing. **Recurrences are reported apart
from contradictions** — a claim standing beside its own successor is not in
conflict with it. And **cross-frame pairs are dropped rather than reported**: high
similarity across disjoint metacontexts is coexistence, and calling it a
contradiction is the misreading metacontexts exist to prevent.

`apply_reflection` writes nine kinds of decision. `merges` is the only
*consolidation* among them that retires nodes from the active graph, so its bar
is deliberately high — every pair of sources must clear the threshold or the
merge is rejected and reported. It is **Topics only**: facts collapse through
`merge_facts`, which is a resolution action on the review-loop path rather than a
reflection decision, because `redundant` is judged when a document arrives and
not when the graph is next swept.

**A fact merge is reversible, and it is the one operation in the system that
destroys anything** (built 2026-08-22, `dev-docs/REVIEW_MODE.md` §7). The
information a reversal needs — which source held which edge — exists only while
the merge is being made, since migration re-points edges onto the survivor and
collapses duplicates, so `merge_nodes` captures it on the survivor at merge
time; `merge_undo_depth` bounds how far back along a lineage those payloads are
kept. `reverse_merge` restores the sources, replays their edges (splitting one
that collapsed when two sources cited a single document) and **deletes the
survivor** rather than retiring it, so that reversing back and forth any number
of times leaves the same active graph as doing it once. It refuses whenever
anything has accrued to the survivor since the merge, because the delete would
take those edges with it. Repeated merge/reverse cycles on one fact are refused
by `merge_cycle_limit`, which reads the append-only lifecycle rather than any new
state.

## Agent Interface (MCP)

Memory is exposed as tools, not as a raw database. Claude Code auto-prefixes these as `mcp__epimemer__<name>`.

Ingestion is a two-step process: `segment` breaks text into chunks, then the agent extracts topics/facts/inferences and passes them to `store_decomposition`. Epimemer does not decompose text itself — that is the calling agent's job.

The tools group into: **core memory** (`segment`, `store_decomposition`, `search`, `link`, `update`, `supersede_by`, `judge_importance`); **discovery & stats** (`query_graph`, `topic_tree`, `find_nodes`, `list_sources`, `list_relations`, `describe_relation`, `graph_stats`); **conflict handling** (`check_conflicts`, `record_contradiction`, `record_variant`, `merge_facts`, `merge_inferences`, `reverse_merge`, `configure_merge`, `configure_warnings`); **reflection** (`reflect`, `configure_reflection`, `apply_reflection`); **temporal access** (`graph_as_of`, `query_changes`); **archival** (`archive`, `restore`); **timelines** (`create_timeline`, `set_reference_time`, `add_timepoint`, `query_timeline`, `create_timelink`); **metacontexts** (`create_metacontext`, `get_metacontexts`, `reframe`); **graph management** (`list_graphs`, `use_graph`, `delete_graph`); **agents** (`claim_agent`); **review** (`review`, `apply_review`, `rejudge`, `correct_interval`); and **visualization** (`viz_status`).

See [INTEGRATION.md](INTEGRATION.md#available-tools) for the canonical table with one-line descriptions and the authoritative tool count — this document intentionally does not restate the count so it can only drift in one place.

### Who is judging

An agent can be given an identity — `claim_agent` proposes a **name** and a
self-description, and the **user** picks which judge it is from the judges this
graph already knows, or names a new one. A judge nobody approved is refused,
because an agent that could admit its own identity could not then establish that
a *different* agent reviewed anything.

**Three layers, with different rules** (2026-08-26). The **key** is opaque,
frozen into every decision and shown to nobody. The **name** is the handle —
freely renamable by the user, resolved at read time, so a rename carries every
old decision with it and nobody has to name a judge correctly before knowing
what it will be used for. **Descriptions** append and are never edited, pinned
per decision by digest, because a decision made last week was made by whatever
the agent claimed to be last week. A description is a self-reported claim, not a
credential, and only `confirmed_at` carries human weight.

Renaming to a name another judge holds asks whether they are the **same judge**,
and yes consolidates them: the survivor answers for both sets of keys, both
description histories are kept, and no journal row is rewritten. That is the
repair for one judge accidentally recorded twice, and it reaches the user
through the same two channels approval does. Approval is per graph, so
`use_graph` can unbind a judge.

Decisions made during review carry that identity: retiring a node records who
retired it and returning it records who brought it back, a judgment edge records
who asserted it, and content written during reflect records who wrote it. A blank
means **unknown** and nothing more — no date is implied, and a graph is free
never to name anyone. Ingest is attributed too, which is where the judgments nothing re-makes are
supplied — the event/state call, the confidence prior. A graph can be set to
**require** a judge and refuse writes without one (`epimemer agents require on`),
though it never does by default. Every decision is also appended to a **journal**
— an append-only table with no update path — so *what did this agent judge* is
one query rather than five scans, and *has anyone checked this* is derived from
a row pointing back rather than a flag anyone edits. `review()` reads that
journal back **shakiest first** — a declared low `certainty` before anything
unrated, then by derived difficulty — with modes for one judge, one time window,
or what nobody has looked at. `apply_review` records that somebody checked a
decision and whether they agree, because a confirmation nobody records means the
next agent repeats the work; and `rejudge` revises a judgment made at ingest
without touching the claim, which is what stops review being able to find every
ingest-time mistake and fix none of them. See
[docs/ATTRIBUTION.md](docs/ATTRIBUTION.md) for the built behaviour and
`dev-docs/REVIEW_MODE.md` for how it was reached.

Historical graph state is read with the dedicated `graph_as_of` (a lifecycle snapshot at a past instant) and `query_changes` (births and retirements across a window) tools — not via an `at_time` parameter on `search`/`query_graph`. That is *transaction* time; the other axis, when a claim was **true**, is `search(valid_as_of=…)`, and the names are marked on both sides so neither inherits the wrong default reading.

## Storage

**SurrealDB** is the primary candidate for prototyping — unified documents, vectors, and graph in one system with a single query language (SurrealQL). **Postgres + pgvector** is the pragmatic production fallback. The architecture is storage-agnostic by design.

### Multi-Graph Support

All backends support multiple named graphs. The `StorageBackend` protocol requires `list_databases`, `switch_database`, and `delete_database`. SurrealDB uses separate databases within a namespace; InMemoryStorage uses a dict-of-dicts pattern. The default graph is `"default"`. Agents manage graphs at runtime via the `list_graphs`, `use_graph`, and `delete_graph` tools.

**The active graph is process state, so every other tool must name the graph it means.** `use_graph` lasts as long as the process: a client reconnect lands back on whatever configuration resolves to, and a session that switched an hour ago comes back somewhere else with nothing to say so. Every tool therefore requires an `expected_graph`, reads as much as writes, and refuses rather than run when it is missing or names a graph the server is not on. It is unconditional and there is no setting: a per-graph flag would be read from whichever graph the call is *actually* in, which would disable the guard in exactly the case it exists for. A wrong-graph **read** is the worse half — it returns a plausible answer the agent then reasons from, leaving no artifact, where a misfiled write at least sits beside its own journal row.

### Scaling Limits

The read paths that were O(N) with a round-trip per node — `list_sources` / `list_relations`, `reflect`'s pending-review gather, `search`'s per-result enrichment — now ask once for the whole set instead. What remains linear is the *payload*, not the round-trips.

These limits are **measured** rather than estimated — see [dev-docs/BENCHMARKS.md](dev-docs/BENCHMARKS.md) for the data and the analysis. Against the 30 s default tool timeout (`EPIMEMER_TOOL_TIMEOUT_SECONDS`), the operations fail at roughly:

| Operation | in-memory | SurrealDB (loopback) |
|---|---|---|
| `search` | ~1.5M nodes | ~2.9M nodes |
| `reflect` | ~320,000 nodes | **~26,000 nodes** |
| `list_sources` | ~870,000 nodes | ~230,000 nodes |

So: `reflect` is the limiting operation on both backends, and everything else has been pushed past any size worth quoting. Ingest is flat and not a concern. Don't point a large persistent graph at this unwarned.

**Those are time limits only.** `reflect` also allocates ~580 bytes per *surviving* candidate pair, and the pair lists are quadratic in the node set. **Measured on real prose 2026-08-20: 0.0105% of fact pairs clear the 0.80 threshold, which projects to ~3 MB at 10,000 facts** — not the gigabytes an earlier estimate predicted from a rate measured on templated text. That moved the argument from memory to the *response*, and **the response is now capped (2026-08-21)**: each of the four pair lists returns its highest-scoring `max_nominations` (200 by default) and any list that was cut is named in the response's `truncated` key. The peak allocation is deliberately not bounded by that — it would mean capping inside the scorer, which is a large change against a 3 MB problem. What the measurement does not cover is a corpus of genuine near-duplicates, which nothing here has ingested. Measurements and the corrected projection: `dev-docs/BENCHMARKS.md`.

These figures depend on two optimisations worth knowing about, because the naive form of each is what a reader would otherwise expect: in-memory edge lookups go through endpoint indexes (`by_src` / `by_dst` in `storage/memory.py`) rather than scanning the edge set, and SurrealDB's `vector_search` ranks before filtering by status rather than filtering inside the ranking query — SurrealDB re-runs such a subquery per row, which cost `search` two orders of magnitude. What remains under `search` on SurrealDB is ~120 ms of per-result enrichment round-trips, the N+1 pattern that batching removed elsewhere.

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

## Observability

The system must not be a black box, and that principle has a running answer: a
live dashboard. Setup, panels and configuration are in
[README.md](README.md#visualization); the design is in `dev-docs/VISUALISATION.md`,
`dev-docs/TIMELINE_VISUALISATION.md`, `dev-docs/EVENT_LOG.md` and
`dev-docs/RETRIEVAL_PROVENANCE.md`. What matters at this level is *what* it makes
visible and why each part exists:

- **A standalone hub, not a server per MCP process.** Sessions dial out and
  register; the browser picks one. The embedded form had a failure mode where a
  stale orphan held the port and served an empty graph.
- **The graph and the pipelines**, the latter being the Petri nets executing —
  the running system *is* the diagram.
- **A timeline in two modes** — *record time* (when the graph learned each node)
  and *content time* (when the described events happened). Keeping them apart on
  screen is the same distinction the model draws between transaction and valid
  time. Vague timepoints get an *undated tray* rather than an invented date.
- **An activity log, one entry per transaction** rather than per write — what the
  agent stored, corrected, world-changed, merged, archived or restored. The
  readable unit is the thing the agent did, not the rows it touched.
- **Retrieval focus**: pick a recent tool call and everything it did *not* return
  desaturates. The interesting click is on a node that did **not** come back —
  *why didn't this match?* — so dimmed nodes stay live rather than being filtered
  away. The response panel is labelled **"Response"**, not "Context": what lands
  in the model's context is the client's rendering of what we returned, and a
  panel captioned "what the agent saw" would claim something the system cannot
  verify.

**`EPIMEMER_VIZ_HOST` is a privacy setting as well as a network one.** On the
default loopback bind the hub keeps whole retrieval records so they survive the
MCP process exiting; pointed at a non-loopback address, sessions mirror
*structural metadata only* and payloads stay in the process that produced them.

## Open Questions

- **Incremental clustering**: online HDBSCAN, centroid drift detection, split heuristics
- **Value signal computation**: decided 2026-08-12; **the node half is built, the read-time half is not**. The documented promise — "how well-supported by evidence" *and* "multiple independent sources increase confidence" — was two claims wanting opposite storage. Support is now a **caller-supplied prior** on a four-value ladder, nullable so an unrated node is distinguishable from an ordinary one, with an optional one-line basis beside it. Still open: per-source levels on the `sourced_from` edge rather than a dict on the node, so a level cannot outlive the source it describes; and corroboration **derived at read time** from distinct publishers over a similarity neighbourhood, never written back. A known gap accepted rather than solved: **there is no path for source discredit** — when a document turns out fabricated, every prior derived from it overstates and nothing can sweep per-source until the provenance-edge levels land. (Neither decay curves nor novelty are among these any more — both signals were removed rather than tuned, and the "relative to what baseline?" that dogged novelty is answered by asking at read time instead of storing an answer. See the removal note under *Node Value Signals*.)
- **Value-driven consolidation thresholds**: how do value signals translate into concrete merge/split decisions? Archival thresholds are settled (importance ceiling, judgment age); merge and split still key off embedding similarity alone.
- **Topic evolution**: the structural mechanisms need design. The input a split wants is *surprise* — how unlike the material a topic already holds a new member is — which is a read-time question over embeddings rather than a stored field. It is also nearly free where it would be asked: `reflect` already builds the block-wise similarity matrix over every topic and fact (`pair_scoring.similar_pairs`), and a per-row max over that same matrix is one reduction on data already in hand
- **Contradiction handling**: contradictions surface today via embedding similarity plus an LLM judgment; the resolution or coexistence strategy needs design
- **Timeline implementation details**: efficient storage and querying of precise timelines (DataFrame-backed), vague timeline ordering heuristics, cyclical timeline template-to-instance mapping
- **Metacontext inheritance scope**: how deep does inheritance go? If a metacontext is inherited from a document, do inferences derived from those facts also inherit it? Probably yes, but edge cases need thought.
- **Metacontext-aware value signals**: *answered 2026-08-12, and now stated in the `store_decomposition` guidance an agent reads* — the scale is the same, the record it measures against is the frame's. A fictional fact can honestly score 0.9: the question is how well that frame's material backs the claim, not whether the frame is real. Left here because the reasoning matters — without it an agent conflates "is this true?" with "does the frame assert this?", every fiction node lands at the bottom of the scale, and confidence quietly becomes a fiction detector, duplicating badly what metacontexts already carry.
- **Temporal validity — the "Saint Petersburg Problem"** — *answered, and built in full; the design is `dev-docs/VALIDITY_DESIGN.md`.* It was the largest gap this document ever recorded: **the graph could not say when a claim was true**, so historical truth was filed as error, contradiction detection was unsound in both directions, corroboration inflated, fact dedup could not be made safe, and inference could combine claims that were never simultaneously true. The model, its retrieval surface, recurrence, the soundness check and boundary proposals all now exist — see **Valid Time** above and [docs/VALIDITY.md](docs/VALIDITY.md), with the design history in `dev-docs/VALIDITY_DESIGN.md` and
`dev-docs/REVIEW_EPISTEMIC.md` §13.
  - **What it left open**, all recorded rather than forgotten: `basis` is per interval rather than per endpoint, so accepting a proposed boundary makes a *stated* start unreportable as stated (under-claiming, the safe direction); fact deduplication was unblocked by it and is now **built** — it dedupes **states** and never **events**, on a `claim_kind` judgment recorded at ingest because two documents years apart yield near-identical sentences and nothing computed from them separates the cases; and the timeline panel does not yet draw intervals, though the grammar is designed (`dev-docs/TIMELINE_VISUALISATION.md` §13).
- **Cross-metacontext retrieval**: when a query straddles metacontexts (e.g., "compare real AI with sci-fi AI"), how should retrieval compose results from multiple metacontexts?
