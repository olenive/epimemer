# Epimemer: Layered Epistemic Memory System

The architecture: what the pieces are and why they have the shape they do.
Behaviour a caller sees is specified in `docs/`; measurements live in
`dev-docs/BENCHMARKS.md`; work not yet built lives in
`dev-docs/PROPOSED_FEATURES.md`.

## Core Concept

A semantic memory system with a dual-space design: vector embeddings are the
primary representation, and a typed graph is derived on top. Rather than
holding static triples, the system reorganises itself over time, merging,
splitting and re-judging its own content under agent review.

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
[ Storage Layer ]            — in-memory, or SurrealDB (embedded or served)
        ↓
[ Query Layer ]              — semantic + lexical + structural, rank-fused
        ↓
[ Reflection ]               — deliberate consolidation (cluster, merge, prune)
```

## Node Types

Every ingested text is decomposed into three types of nodes:

### Topics
Paragraph-length semantic summaries, not keywords or short labels. Topics
embed well, support clustering, and can evolve over time. They describe the
underlying theme of a segment in enough detail to preserve nuance.

### Facts
Atomic, verifiable, grounded statements tied to source material. Each fact
tracks provenance and may carry a confidence prior: the ingesting agent's
reading of how well the record backs the claim, supplied once and never
computed. A fact also carries a **claim kind**, `state` (a condition holding
over a period) or `event` (an occurrence), judged at ingest and read by
deduplication, which merges states and never events. Nullable: an unjudged
fact simply never merges.

### Inferences
Higher-level interpretations reasoned from facts and context. Explicitly
provisional and revisable. Multiple competing inferences from the same
evidence are permitted to coexist. Distinguished from facts to maintain
epistemic clarity.

## Dual-Space Design

### Vector space (semantic)
- Embeddings are the primary representation, not the graph.
- Multiple embedding models supported per item, partitioned by `model_id`;
  embeddings are appended, never overwritten, so a new model can be
  re-indexed in the background with no downtime.
- Embeddings are treated as indexed views over the data, not the data itself.

### Graph space (structural)
- Derived from, but not dependent on, a specific embedding model.
- Relationships are typed: `about`, `contains`, `implies`, `supports`,
  `derived_from`, `similarity`, `contradiction`, and so on.
- Edges carry a `weight` and a free-form `metadata` dict.
- Structure is contextual and interpretive, not ground truth.

## Segmentation and Topic Assignment

Text is broken into non-overlapping, variable-length segments aligned to
semantic boundaries. Two strategies are built: **paragraph split** (the
default) and **semantic similarity drop** (TextTiling-style: embed each
sentence, cut where similarity between neighbours drops sharply; cheap, no
LLM needed). LLM-guided splitting is a backlog item with an architectural
decision attached (`dev-docs/PROPOSED_FEATURES.md`).

The segment-to-topic relationship is **many-to-many**: a segment can be
`about` several topics and a topic can span several segments, represented by
edges rather than duplicated text.

**At ingestion** (write fast): the calling agent extracts one or more topic
descriptions per segment and passes them to `store_decomposition`. Each
becomes a new topic node, with no deduplication at this stage.

**At reflect** (organize slow): topic descriptions are embedded and
clustered, and similar topics are merged into unified nodes, with the
originals preserved via `merged_into` history edges.

## Key Design Principles

### "Write fast, organize slow"
Ingestion is append-only with minimal processing. Expensive restructuring
(clustering, merging, pruning) happens later, through the deliberate
`reflect` operation. This avoids latency spikes and premature structural
commitment.

### Sources, tags, and relations are nodes and edges
Where knowledge came from, and what it is about, are modelled as graph
structure rather than string fields, so a source or tag can carry its own
facts, relate to siblings, and sit in a frame:

- **Source**: every node gets a `sourced_from` edge to its originating
  `RawDocument`; a named publisher or author (`published_by`) is an entity
  **Topic**. "Which nodes came from X" is a traversal (see `find_nodes`).
- **Tags are Topics**: a tag name resolves, by exact name, to a Topic linked
  by a `tagged_with` edge, so tag consolidation *is* topic merge.
- **Relations are open vocabulary**: engine edges are a typed enum; user
  relations use one `RELATED` sentinel with a free `label` and a `kind`
  (`relationship`, followed in retrieval, or `attribution`, not followed).
  Behaviour is finite and hardcoded; the vocabulary is open. A label also has
  a **record**: an id, a description, and the thing a decision about it can
  name, so `reflect` can nominate likely synonyms and
  `apply_reflection(relation_verdicts=…)` can record what was decided about a
  pair. **Nothing rewrites a label**: edges are not versioned, so a bulk
  relabel would be the one irreversible operation in the system.

These are separate from metacontexts. Metacontexts are epistemic frames that
change retrieval scope; sources, tags and relations are structure, and
provenance and attribution edges are deliberately not expanded in default
retrieval.

### Epimemer makes no LLM calls
Ingest is the two-step `segment` → `store_decomposition` flow: the server
splits text and stores what it is given, and the **calling agent** does the
topic/fact/inference extraction. The server has no API keys, no model choice,
and no per-ingest LLM latency of its own; anything requiring a judgment call
is the agent's to make.

That includes **when a claim was true**: validity intervals can only come
from ingest, because the tense and the dates written in the text are visible
there and nowhere afterwards. They are supplied per node, land on the
`sourced_from` edge, and are marked `stated` or `inferred`. A date the agent
knows from world knowledge, which the document does not give, is neither, and
must not be supplied at all.

### Test-driven development, with analysis and benchmarking
The memory system's correctness is hard to assess during normal use, so
development is test-first, with a mock embedding provider so no model is
downloaded, marimo notebooks for stepping through each pipeline, and
benchmarks that measure rather than estimate (`dev-docs/BENCHMARKS.md`).

## Node Value Signals

Every node carries a `ValueSignal`. One member is a score, one is a judgment,
and two are clocks. The split is deliberate: a score can be computed, a
judgment cannot, and use is an event rather than either.

- **Confidence** (0.0–1.0, nullable): how well the record would back the
  claim if challenged. A **caller-supplied prior**, never computed, given at
  `store_decomposition` on a four-value ladder (0.3 hedged or partisan / 0.5
  ordinary, omit it / 0.7 established / 0.9 primary or authoritative), with
  an optional one-line `confidence_basis` saying why. **Omitting it stores
  absence, not 0.5**: "nobody assessed this" and "assessed, and ordinary" are
  different states. Ranking code reads absence as 0.5 via `rated_confidence`;
  display code passes the absence through. The scale is the same in every
  frame, measured against that frame's own record: a fictional fact can
  honestly score 0.9 if the fiction's material backs it, because confidence
  answers "does the frame's record support this claim", not "is this real".
- **Importance** (0.0–1.0): does this matter? Moved only by the
  `judge_importance` tool, in either direction, asymptotically toward its
  bound, and every move records a reason. Nothing automatic touches it: a
  decayed judgment would be a number nobody stands behind.
- **`retrieved_at`**: null until a search returns the node, then the time it
  last did. Is this being used?
- **`importance_judged_at`**: null until someone judges it. What ages is not
  the judgment but confidence in its currency, which is what the
  `stale_judgment` archival class reads.

Both clocks are nullable because "never" and "long ago" are different states,
and only a nullable timestamp can tell them apart.

A merge collapses several nodes into a fresh one, so its signal is rebuilt by
one shared function, `merged_value_signal`: max importance and confidence,
the later of each clock, and null losing to any real value. One shared
function, because a field-by-field rebuild silently resets whatever it
forgets to name. The `confidence_basis` of whichever source supplied the kept
confidence travels into the survivor's metadata, since a prior separated from
its reason is the state the ladder exists to prevent.

`reflect` reads these signals to nominate candidates and never writes them:
never retrieved, not judged important, nothing depending on it → archival
candidate; judged important long ago and never revisited → hand back to
review.

## Timelines

Timelines represent when things happened in the world, as opposed to
`created_at` / `superseded_at`, which track when the *system* learned
something.

A `Timeline` is a node type acting as an ordered container of embedded
`Timepoint`s. Each timepoint has:

- **A stable UUID**, immune to reordering, insertion, or value refinement.
- **A temporal value**: a concrete datetime or interval (optional
  `start`/`end`), a free-text label ("during the Renaissance"), or both.
- **A position**, managed by the timeline's ordering, not by the timepoint.

Other nodes link to specific timepoints via `TIMELINK` edges; nodes connect
to their timelines via `ASSOCIATED_TIMELINE` edges, and a node can have
several.

A timeline also carries an optional **`reference_time`**: that clock's own
*now*, set via `set_reference_time`. It is what makes "current" answerable on
a timeline that is not wall-clock: a fictional claim is current when its
interval contains *that timeline's* reference time, so any code asking
whether a claim holds now must ask the relevant clock rather than
`datetime.now()`.

Properties worth knowing:

- **Shared timepoints**: two events at the same moment link to the same
  timepoint; different granularity ("May 5th" vs "3pm on May 5th") makes
  separate ones.
- **Stability**: adding, reordering or refining timepoints never disturbs
  existing links, because links reference the UUID; removing one orphans its
  links, which is detected and flagged.

Specialised timeline types (precise, vague, cyclical) are a backlog item:
`dev-docs/PROPOSED_FEATURES.md` → *Specialized timelines*.

## Metacontext

Metacontext is the epistemic frame that disambiguates different takes,
sources, or interpretations of the same information. It answers: *in what
context is this true?*

### Structure

A `Metacontext` is a node in the graph, like a high-level Topic but for
disambiguation rather than categorisation. Examples: "Real historical
events", a fictional universe, a party line, "Reporting by the BBC". Because
metacontexts are nodes, they can relate to each other via ordinary edges and
participate in search like other nodes.

### Association

- Nodes link to their metacontexts via `HAS_METACONTEXT` edges.
- **Inheritance**: a document is ingested *with* a metacontext, and every
  node extracted from it inherits that metacontext. There is no
  frame-inherits-frame machinery; a reader who wants two frames names two.
- **Multiple metacontexts per node**: something can be "propaganda" and also
  "true as far as we know"; those are different axes.
- **No predefined axes**: metacontexts are created, split, and merged
  dynamically, the same way Topics are managed.
- **Absence names no frame**: a node with no `has_metacontext` edge is a node
  nobody said anything about, which is what absence means everywhere here (an
  omitted `confidence` is unrated, an absent `judged_by` is unknown). Nothing
  is inferred from silence. The consequence is deliberate: a frameless node
  is never compared, never merged, and returned by no scoped search. Only a
  graph written before frames were required holds any;
  `graph_stats.nodes_without_frame` counts them and `epimemer frames declare`
  ends the state.
- **`the-real` is a convention, not a mechanism**: the id every graph should
  use for the frame holding real-world claims, so two graphs do not end up
  with one frame under two strings. Nothing reads it specially, and it must
  exist like any other frame; `create_metacontext` takes a chosen id for
  exactly this.
- **The frame is required at ingest**: `store_decomposition` takes
  `metacontext_id` as a required argument. It cannot prevent a wrong frame,
  but it makes the error recoverable: the frame is an edge carrying the judge
  who wrote it and a journal row naming it, so `review` finds it and
  `reframe` fixes it. One frame per call, so a mixed document is two calls.
- **Search names the frames it wants, as a list**: results are nodes standing
  in **any** of them. Omitting the list searches every frame, which is a
  coherent question; that is why the read side is optional where ingest is
  not.
- **Nothing invents a frame on a node's behalf**: splits inherit what the
  parent states, a synthesised parent inherits the one set its children all
  stand in and is refused when they differ, and a merge re-states the
  survivor's frame under the merging agent's judge, because the survivor's
  content is synthesised and no source's framing was made about that wording.
  Union is never the answer: one node asserted in two worlds is the worst
  outcome available.
- **A stated metacontext must exist in the graph you are in**: ids are per
  graph, and `store_decomposition` and `search` both refuse one that resolves
  nowhere, every id in a search's list included.

### Why this matters

The "Fall of Carthage" means different things in a historical frame and in a
fictional universe. AI capabilities described in a novel are not real-world
research. Political events described by opposing parties carry different
framing. Without metacontext the memory would conflate these, silently
corrupting retrieval, so search results always carry their metacontext
labels and fiction is never mixed with fact without the distinction showing.

## Retrieval

`search` is one tool with several arms behind it. Full detail:
[docs/RETRIEVAL.md](docs/RETRIEVAL.md).

- **Two arms, because they fail in opposite directions.** Embedding
  similarity has no notion of term rarity: an identifier like `JIRA-4417`
  embeds to roughly "short alphanumeric string", close to every other ticket
  id. So a keyword arm (BM25) runs alongside the vector arm, over two
  corpora: node content, and the raw **segments** text was extracted from.
  Nodes answer *what do I believe?*; segments answer *where did I read
  that?*, which matters when the agent paraphrased an identifier away.
  Callers declare the exact strings that matter as `terms`; each declared
  term's best hit survives to the final result.
- **Fusion is by rank, never by score.** Cosine and BM25 are on incomparable
  scales, so results merge by Reciprocal Rank Fusion, and ranks are the only
  quantity that crosses a corpus boundary.
- **Every result says how it was reached**: `provenance` is `lexical`,
  `segment`, `vector`, `expanded` (reached by an edge), or `direct`.
- **History returns by default, folded.** Claims retired because the world
  moved on (`historical`) come back and say so; claims retired as wrong
  (`corrected`) are off by default. A claim's earlier versions attach to its
  replacement rather than each taking a result slot.
- **Valid time answers in groups, never as a filter.** `valid_as_of` returns
  `valid` and `unknown` buckets and excludes nothing, because a filter would
  turn missing metadata into a silent false negative.
- **Corroboration is asked for, not assumed.** `include_corroboration=True`
  adds how many independent publishers back each result, derived at read
  time from `similarity` edges recorded by agent verdicts, never stored. It
  counts independence, not strength, and a claim whose dates put it in a
  different period is reported separately under `adjacent_periods` rather
  than counted. It is off by default because it is the most expensive
  annotation on the retrieval path.

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

Epimemer is append-only for **knowledge content**: a node's `content`, its
`source_id`, `created_at`, and provenance are never changed. A correction or
consolidation creates a new node linked to its predecessor via typed edges:

- **Update**: `node_v1 --superseded_by--> node_v2` for a correction,
  `node_v1 --temporally_followed_by--> node_v2` for a world-change, and
  `node_v1.status` records the same *why*: `corrected` (it was wrong) or
  `historical` (it was right, and remains right of its period). The caller
  must say which; there is no default, because filing a change in the world
  as an error is how a graph forgets its own history.
  - The status also decides **which edges follow the replacement**. A
    correction hands over everything but history and review edges; a
    world-change hands over the frame and the tags only, because the
    historical node is still true of its period and its own sources are what
    say so. Judgment edges (`similarity`, `contradiction`, `variant_of`)
    stay on the node they were made about under every retirement: the claim
    may survive a correction, but the wording the judgment was made against
    does not. `migration_disposition(edge_type, status)` is the whole rule.
  - **The lineage edge splits the same way.** A correction writes
    `superseded_by` and is terminal; a world-change writes
    `temporally_followed_by`, which states order rather than replacement and
    so survives a claim becoming true again. `lineage_edge_type_for(status)`
    pairs with `superseded_status_for(because)` so the node and the edge
    cannot disagree. The edge never claims adjacency — Saint Petersburg →
    Petrograd → Leningrad → Saint Petersburg is three separately observed
    transitions — so cycles and parallel same-direction edges are legal, and
    nothing may dedup them by `(src, dst, type)`.
  - **Recurrence**: `historical` is restorable and `corrected` is not, and
    similarity nomination sees historical candidates, which is what makes
    the `recurs` verdict reachable. `check_conflicts` returns each
    candidate's status, `reflect` reports mixed pairs under `recurrences`,
    and `restore` reactivates a named node and writes the new source's
    `sourced_from` edge in one transaction, refusing without one: a claim
    back to active with no edge saying who asserts it is one the graph
    states and cannot attribute.
- **Merge**: `node_a --merged_into--> node_c`,
  `node_b --merged_into--> node_c`.

History is part of the graph itself rather than a separate versioning system;
traversing it is following edges backwards.

### What is immutable vs. mutated in place

A node also carries lifecycle and label metadata that *is* mutated in place,
because it is not the knowledge claim and editing it rewrites no history:

| Mutated in place | Set by | Why it is not a version |
|---|---|---|
| `status`, `superseded_at` | supersede / merge | this is precisely how a node is retired, and how "what the graph held at time T" is reconstructed — transaction time, never validity |
| `value.confidence` | the ingesting agent's prior at `store_decomposition`, or absent; merges combine it via `merged_value_signal` | supplied once at creation and never re-set; a correction mints a new node rather than rewriting this one |
| `importance`, `importance_judged_at` | `judge_importance` | a recorded assessment of the same claim, with its own reason trail |
| `retrieved_at` | `search` | a record that the node was read, not a change to what it says |

So "a node is never mutated" is shorthand for "a node's *content* is never
mutated".

- **Current state** = all nodes with `status = "active"`.
- **State at time T** = all nodes where `created_at <= T` and
  (`superseded_at IS NULL` or `superseded_at > T`).

### Archival

Retired and merged nodes accumulate, and archival is an **export**: eligible
non-active nodes older than a cutoff, with their history edges, are exported
to cold storage and flipped to `archived`, leaving every active query
unaffected. **`historical` nodes are excluded**: they were retired because
the world changed, not because they were wrong, so age alone is not grounds
to discard them. Nothing is deleted, and `restore` reverses it. Embeddings
are archived only when no active node's edges were derived using them.

## Valid Time — when a claim was true

Node History above is **transaction time**: when the graph learned something.
Valid time is the other axis, and conflating them was the largest correctness
gap the system has had. Full detail: [docs/VALIDITY.md](docs/VALIDITY.md).

**The Saint Petersburg problem.** Saint Petersburg was Petrograd was
Leningrad was Saint Petersburg, and every one of those was true. A model that
can record such a pair only as a contradiction or a correction files
historical truth as error and lets an inference combine claims that were
never simultaneously true, with nothing to detect it.

The model, briefly:

- **Validity lives on the `sourced_from` edge, per source**, never on the
  node and never collapsed at read time. A claim with two sources has two
  periods; union and intersection both lie undetectably.
- **An interval carries endpoints, a timeline, a witness point, and a
  `basis`.** Endpoints distinguish `precise`, `named`, `unknown` and
  `unbounded`: "we don't know when it started" and "it had no start" are
  different claims. Intervals are measured against a named timeline, and
  periods on different clocks are simply not comparable. `basis` is `stated`
  or `inferred`; a date from the agent's own world knowledge is neither and
  must not be supplied.
- **Comparison answers four values**: `before`, `after`, `overlap`,
  `unknown` — and every consumer must treat `unknown` as *we cannot tell*
  rather than folding it into false.
- **The soundness check** flags an active inference whose premises no source
  puts in the same period, reporting the offending pairs with their dates.
  It is silent whenever a pair cannot be placed: a check on evidence, never
  on ignorance.
- **Boundary proposals** are the other half of "ingest extracts, reflect
  proposes": a document cannot know its claim will ever stop being true, so
  only something seeing the *next* document can close the first period.
  Publication dates are never used, because they bound when a claim was
  asserted, not when it held.

## Reflection

`reflect` **reads and never writes**. It scans the graph, nominates
candidates, and hands them back; every change goes through
`apply_reflection`, and the judgment in between belongs to the agent, or, for
the consequential calls, to a human. Full detail:
[docs/REFLECTION.md](docs/REFLECTION.md).

The principle underneath is one line: **embeddings are a good candidate
generator and a poor judge.** Similarity nominates *these two facts are about
the same thing*; only an agent can answer *do they contradict, supersede, or
coexist?* So `reflect` returns pairs with their scores rather than verdicts.

One phase per worklist, and `REFLECT_PHASES` in `mcp/tools.py` names them in
execution order. Two separations in that list are load-bearing: recurrences
are reported apart from contradictions, because a claim standing beside its
own successor is not in conflict with it; and cross-frame pairs are dropped
rather than reported, because high similarity across disjoint frames is
coexistence.

`apply_reflection` writes every kind of reflect decision. Its `merges` are
**Topics only**: facts collapse through `merge_facts` and inferences through
`merge_inferences`, both resolution actions on the review-loop path, because
`redundant` is judged when a document arrives and not when the graph is next
swept. A topic merge applies only when every pair of sources clears a fixed
bar; the bar is not a parameter of the call, because a caller must not choose
the bar its own merge is checked against.

**A merge is reversible, and it is the one operation in the system that
destroys anything.** The information a reversal needs — which source held
which edge — exists only while the merge is being made, so `merge_nodes`
captures it on the survivor at merge time. `reverse_merge` restores the
sources, replays their edges and deletes the survivor, refusing whenever
anything has accrued to it since. Repeated merge/reverse cycles on one fact
are refused by `merge_cycle_limit`.

## Agent Interface (MCP)

Memory is exposed as tools, not as a raw database. Claude Code auto-prefixes
these as `mcp__epimemer__<name>`.

Ingestion is a two-step process: `segment` breaks text into chunks, then the
agent extracts topics/facts/inferences and passes them to
`store_decomposition`. Epimemer does not decompose text itself.

The tools group into: **core memory** (`segment`, `store_decomposition`,
`search`, `link`, `update`, `supersede_by`, `judge_importance`); **discovery
& stats** (`query_graph`, `topic_tree`, `find_nodes`, `list_sources`,
`list_relations`, `describe_relation`, `graph_stats`); **conflict handling**
(`check_conflicts`, `record_contradiction`, `record_variant`, `merge_facts`,
`merge_inferences`, `reverse_merge`, `configure_merge`,
`configure_warnings`); **reflection** (`reflect`, `configure_reflection`,
`apply_reflection`); **temporal access** (`graph_as_of`, `query_changes`);
**archival** (`archive`, `restore`); **timelines** (`create_timeline`,
`set_reference_time`, `add_timepoint`, `query_timeline`, `create_timelink`);
**metacontexts** (`create_metacontext`, `get_metacontexts`, `reframe`);
**graph management** (`list_graphs`, `use_graph`, `delete_graph`);
**agents** (`claim_agent`); **review** (`review`, `apply_review`, `rejudge`,
`correct_interval`); and **visualization** (`viz_status`).

See [INTEGRATION.md](INTEGRATION.md#available-tools) for the canonical table
with one-line descriptions and the authoritative tool count — this document
intentionally does not restate the count so it can only drift in one place.

### Who is judging

An agent can be given an identity: `claim_agent` proposes a **name** and a
self-description, and the **user** picks which judge it is. A judge nobody
approved is refused, because an agent that could admit its own identity could
not then establish that a *different* agent reviewed anything.

Three layers with different rules: the **key** is opaque, frozen into every
decision and shown to nobody; the **name** is the handle, freely renamable by
the user and resolved at read time, so a rename carries every old decision
with it; **descriptions** append and are never edited, pinned per decision by
digest, because a decision made last week was made by whatever the agent
claimed to be last week. Renaming onto a name another judge holds asks
whether they are the same judge, and yes consolidates them. Approval is per
graph, so `use_graph` can unbind a judge.

Every decision names its judge — ingest included, which is where the
judgments nothing re-makes are supplied — and is also appended to a
**journal**, an append-only table with no update path, so *what did this
agent judge* is one query. A blank judge means unknown and nothing more; a
graph can be set to require one. `review()` reads the journal back shakiest
first, `apply_review` records that somebody checked a decision, and `rejudge`
revises a judgment made at ingest without touching the claim. See
[docs/ATTRIBUTION.md](docs/ATTRIBUTION.md).

Historical graph state is read with `graph_as_of` (a lifecycle snapshot at a
past instant) and `query_changes` (births and retirements across a window),
not via an `at_time` parameter on `search`. That is *transaction* time; when
a claim was **true** is `search(valid_as_of=…)`, and the names are marked on
both sides so neither inherits the wrong default reading.

## Storage

Two backends implement one `StorageBackend` protocol, in full, with no
capability flags: **in-memory** (fast, ephemeral, used by most tests) and
**SurrealDB** (persistent; served over `ws://`, or embedded via `mem://` /
`file://` / `surrealkv://`). Callers invoke the protocol unconditionally, and
guard tests compare signatures.

### Multi-graph support

Both backends support multiple named graphs: SurrealDB as separate databases
within a namespace, in-memory as a dict of graphs. Agents manage them with
`list_graphs`, `use_graph`, and `delete_graph`.

**The active graph is process state, so every other tool must name the graph
it means.** A `use_graph` lasts only as long as the process, and a client
reconnect silently lands back on whatever the configuration resolves to.
Every tool therefore requires an `expected_graph`, reads as much as writes,
and refuses rather than run when it is missing or names a graph the server
is not on. It is unconditional, with no setting: a per-graph flag would be
read from whichever graph the call is *actually* in, which would disable the
guard in exactly the case it exists for. A wrong-graph **read** is the worse
half: it returns a plausible answer the agent then reasons from, leaving no
artifact, where a misfiled write at least sits beside its own journal row.

### Scaling limits

The limits are **measured**, not estimated; `dev-docs/BENCHMARKS.md` has the
data. Against the 30 s default tool timeout, `search` fails at roughly 1.5M
nodes in-memory and 2.9M on SurrealDB; `reflect` at ~320,000 and ~26,000.
`reflect` is the limiting operation on both backends, its candidate pair
lists are the one quadratic cost, and its response is capped
(`max_nominations` per list, cut lists named in `truncated`). Ingest is flat.
Do not point a large persistent graph at this unwarned.

## Implementation Approach: Petri Nets via Petritype

The system must not be a black box: a newcomer should be able to look at any
part of the pipeline and see what is happening, what state data is in, and
how it flows. So all key processing steps are executable, typed Petri nets,
via [Petritype](https://github.com/olenive/petritype):

- **Places** are typed containers; each declares a Python type and only holds
  matching tokens (enforced at runtime).
- **Transitions** are real Python functions, async supported.
- **Tokens** are actual data: Pydantic models flowing through the net.
- **Type-based output routing** means a transition returning a Topic, Fact,
  or Inference routes each to the correct typed place; branching is expressed
  by graph structure, not hidden in conditionals.
- **Visualization** is built in via Graphviz: the running system *is* the
  diagram.

The system is a Petri net of Petri nets. Each algorithm (segmentation,
graph construction, query, reflection, …) is a self-contained
`ExecutableGraph` with typed inputs and outputs as its interface contract,
and a top-level **orchestration net** invokes the algorithm nets, operating
on coarse-grained types (`RawDocument` → `SegmentedDocument` →
`DecomposedGraph`). Each sub-net is independently developed, tested, and
visualized, and any of them can have alternative strategy implementations
behind the same interface types, discoverable via the `@petri_net`
decorator's metadata.

Petri nets are used where they add clarity: algorithms with meaningful
internal state, branching, or concurrency. Trivial operations do not get
their own net. The Pydantic data-model types serve double duty as storage
schema and token types, keeping the pipeline and persistence layers in sync.

## Observability

The black-box principle has a running answer: a live dashboard. Setup and
panels are in [README.md](README.md#visualization); what matters here is
what it makes visible and why:

- **A standalone hub, not a server per MCP process.** Sessions dial out and
  register; the browser picks one. The embedded form had a failure mode where
  a stale orphan held the port and served an empty graph.
- **The graph and the pipelines**, the latter being the Petri nets executing.
- **A timeline in two modes**, *record time* (when the graph learned each
  node) and *content time* (when the described events happened) — the same
  distinction the model draws between transaction and valid time. Vague
  timepoints get an undated tray rather than an invented date.
- **An activity log, one entry per transaction**: what the agent stored,
  corrected, world-changed, merged, archived or restored.
- **Retrieval focus**: pick a recent tool call and everything it did *not*
  return desaturates, with dimmed nodes still clickable, because the
  interesting click is on a node that did not come back. The response panel
  is labelled "Response", not "Context": what lands in the model's context is
  the client's rendering of what was returned, and a panel captioned "what
  the agent saw" would claim something the system cannot verify.

**`EPIMEMER_VIZ_HOST` is a privacy setting as well as a network one.** On the
default loopback bind the hub keeps whole retrieval records so they survive
the MCP process exiting; pointed at a non-loopback address, sessions mirror
structural metadata only and payloads stay in the process that produced them.
