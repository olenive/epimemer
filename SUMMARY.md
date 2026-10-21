# Epimemer: Layered Epistemic Memory System

The architecture: what the pieces are and why they have the shape they do.
The behaviour a caller sees is specified in `docs/`; measurements are in
`dev-docs/BENCHMARKS.md`; work not yet built is in
`dev-docs/PROPOSED_FEATURES.md`.

## Core Concept

A semantic memory system: a typed graph of topics, facts and inferences, each
carrying its provenance, with an embedding index over it for retrieval. The
graph is reorganised over time, merged, split and re-judged, under agent
review.

## Node Types

Every ingested text is decomposed into three types of node.

### Topics
An extracted topic is a paragraph-length summary of a theme, not a keyword.
Written that way it embeds well, clusters well, and can be refined later
without losing nuance. The other kind is a **topic node created from a tag**: a
Topic created from a name the caller passed in `tags=`, which gathers nodes for
retrieval (see *Source nodes, topic nodes, and relations* below). Every topic
has a `content`, the name the graph joins on, and a `description`, prose saying
what it covers. Enrichment writes the description and never the name, and a
tag cannot be created without one, since a bare name embeds on its own
characters and two dated tags then look like duplicates.

### Facts
Atomic, verifiable, grounded statements tied to source material. Each fact
tracks its provenance and may carry a confidence prior: the ingesting agent's
reading of how well the record backs the claim, supplied by a judge and never
computed. A fact also carries a **claim kind**, `state` (a condition holding
over a period) or `event` (an occurrence), judged at ingest and read by
deduplication, which merges states and never events. It is nullable: an
unjudged fact never merges.

### Inferences
Higher-level interpretations reasoned from facts and context. Explicitly
provisional and revisable. Several competing inferences from the same
evidence may coexist. They are kept distinct from facts so that a reader
always knows which is which.

## Embeddings and the graph

The graph is the record: nodes, their provenance and every judgment made
about them. Embeddings are an index over it, used to retrieve and to nominate
candidates for review.

- Each vector is a record of its own, tagged with the `model_id` that made it,
  and search reads only the configured model's. A topic node whose name or
  description changes is re-embedded in place, so a stale vector never ranks
  beside the new one.
- A bundle leaves the embeddings out, and import recomputes them with the
  importing server's model, which is how a graph moves to a new model.
- Edges are typed (`about`, `contains`, `supports`, `similarity`,
  `contradiction` and so on) and carry a `weight` and a free-form `metadata`
  dict. Ingest writes the structural ones from each passage; `similarity` and
  `contradiction` edges are written by an agent's verdict.

## Segmentation and Topic Assignment

The server cuts text into non-overlapping passages, by paragraph (the default)
or where similarity between neighbouring sentences drops, reports `doubts`
about its own cut, and accepts the calling agent's own cut stated as anchors
([docs/INGEST.md](docs/INGEST.md)).

The segment-to-topic relationship is **many-to-many**: a segment can be
`about` several topics and a topic can span several segments, represented by
edges rather than duplicated text. At ingestion each topic description the
agent extracts becomes a new topic node, with no deduplication. At reflect,
`reflect` nominates similar topics, and the merges an agent approves through
`apply_reflection` collapse them, linked to the survivor by `merged_into`
edges.

## Key Design Principles

### "Write fast, organize slow"
Ingestion is append-only with minimal processing. Expensive restructuring
(clustering, merging, pruning) happens later: `reflect` nominates it and the
agent's verdicts apply it. This avoids latency spikes and premature structural
commitment.

### Source nodes, topic nodes, and relations are nodes and edges
Where knowledge came from, and what it is about, are modelled as graph
structure rather than string fields, so a source node or a topic node can carry
its own facts, relate to siblings, and sit in a metacontext:

- **Source**: every node gets a `sourced_from` edge to its originating
  `RawDocument`; a named publisher or author (`published_by`) is an entity
  **Topic**. "Which nodes came from X" is a traversal (see `find_nodes`).
- **A tag becomes a topic node**: a *tag* is the name passed in `tags=`. It
  resolves, by exact name, to a Topic, the topic node created from that tag,
  and a `tagged_with_topic` edge links the node to it. That topic node joins
  the ingest's metacontext, so a name stands in every metacontext it is used
  from. Consolidating tags *is* topic merge. That edge is a retrieval index and
  carries no evidential weight: `supports` is the edge corroboration reads, and
  nothing weighs this one.
- **Relations are open vocabulary**: engine edges are a typed enum; user
  relations use one `RELATED` sentinel with a free `label` and a `kind`
  (`relationship`, followed in retrieval, or `attribution`, not followed).
  Behaviour is finite and hardcoded; the vocabulary is open. A label also has
  a **record**, so `reflect` can nominate likely synonyms and a verdict about
  a pair can be recorded. **Nothing rewrites a label**: edges are not
  versioned, so a bulk relabel would be the one irreversible operation in the
  system.

These are separate from metacontexts. A metacontext changes retrieval scope;
source nodes, topic nodes and relations are structure, and
provenance and attribution edges are deliberately not expanded in default
retrieval.

### Epimemer makes no LLM calls
Ingest is the two-step `segment` then `store_decomposition` flow: the server
splits text and stores what it is given, and the **calling agent** extracts
the topics, facts and inferences. The server has no API keys, no model
choice, and no per-ingest LLM latency of its own. Anything requiring a
judgment call is the agent's to make.

That includes **when a claim was true**: validity intervals can only come
from ingest, because the tense and the dates written in the text are visible
there and nowhere afterwards. They are supplied per node, land on the
`sourced_from` edge, and are marked `stated` or `inferred`. A date the agent
knows from world knowledge, which the document does not give, is neither, and
must not be supplied at all.

## Node Value Signals

Every node carries a `ValueSignal`. One member is a score, one is a judgment,
and two are clocks. The split is deliberate: a score can be computed, a
judgment cannot, and use is an event rather than either.

- **Confidence** (0.0–1.0, nullable): how well the record would back the
  claim if challenged. A **caller-supplied prior**, never computed, given at
  `store_decomposition` with an optional one-line `confidence_basis` saying
  why. **Omitting it stores absence, not 0.5**: "nobody assessed this" and
  "assessed, and ordinary" are different states. Ranking reads absence as 0.5;
  display passes the absence through. The scale is the same in every
  metacontext, measured against that metacontext's own record: a fictional
  fact can honestly score 0.9 if the fiction's material backs it, because
  confidence answers "does the metacontext's record support this claim", not
  "is this real".
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

A merge rebuilds the survivor's signal from its sources: the highest
importance and confidence, the later of each clock, and null losing to any real
value. The `confidence_basis` behind the kept confidence travels with it.

`reflect` reads these signals to nominate candidates and never writes them.
Never retrieved, not judged important, nothing depending on it: an archival
candidate. Judged important long ago and never revisited: handed back for
review.

## Timelines

Timelines represent when things happened in the world, as opposed to
`created_at` and `superseded_at`, which track when the *system* learned
something. Full detail: [docs/TIMELINES.md](docs/TIMELINES.md).

- **One record per timeline** holds its timepoints, the order sources have
  stated between them, the contradictions in that order, and its recurrence
  rules. A timepoint has a stable id, so adding, reordering or refining points
  never disturbs the `TIMELINK` edges that name it.
- **A timeline has its own *now***, `reference_time`, so "current" is
  answerable on a clock that is not the wall clock: code asking whether a claim
  holds now asks the relevant timeline rather than `datetime.now()`.
- **Stated order places undated points.** A source can say one point came
  before another, and a point nobody dated gets `earliest` and `latest`
  bounds, derived on read and never written.
- **Order that cannot all hold is recorded rather than refused**, as a
  `TemporalContradiction` that `reflect` nominates until a judge retires a
  constraint, splits the point, or holds it. Every verdict is append-only.
- **A recurrence rule is stored once and its occurrences computed per query**,
  because a stored expansion is a second thing that can disagree with the rule.
  An occurrence is named by the start the rule gives it, and becomes a
  timepoint only when a fact needs one to attach to.

## Metacontext

A metacontext separates different takes, sources, or interpretations of the same
information. It answers: *in what context is this true?*

### Structure

A `Metacontext` is a node in the graph, like a high-level Topic but for
disambiguation rather than categorisation. Examples: "Real historical
events", a fictional universe, a party line, "Reporting by the BBC". Because
metacontexts are nodes, they can relate to each other via ordinary edges, such
as `based_on` for one metacontext that builds on another.

### Association

- Nodes link to their metacontexts via `HAS_METACONTEXT` edges.
- **Inheritance**: a document is ingested *with* a metacontext, and every
  node extracted from it inherits that metacontext. One metacontext never
  inherits from another; a reader who wants two names two.
- **Several metacontexts per node**: something can be "propaganda" and also
  "true as far as we know"; those are different axes.
- **No predefined axes**: a graph creates whatever metacontexts its material
  needs, with `create_metacontext`.
- **Absence names no metacontext**: a node with no `has_metacontext` edge is
  a node nobody said anything about, which is what absence means everywhere
  here (an omitted `confidence` is unrated, an absent `judged_by` is unknown).
  Nothing is inferred from silence. So a node without a metacontext is never
  compared, never merged, and returned by no scoped search.
- **`the-real` is a convention, not a mechanism**: the id every graph should
  use for the metacontext holding real-world claims, so two graphs do not end
  up with one metacontext under two strings. Nothing reads it specially, and it
  must exist like any other metacontext; `create_metacontext` takes a chosen id
  for exactly this.
- **The metacontext is required at ingest**: `store_decomposition` takes
  `metacontext_id` as a required argument. It cannot prevent a wrong
  metacontext, but it makes the error recoverable: the metacontext is an edge
  carrying the judge who wrote it and a journal row naming it, so `review`
  finds it and `reassign_metacontext` fixes it. One metacontext per call, so a
  mixed document is two calls.
- **A read names the metacontexts it wants, as a list**: `search`,
  `find_nodes`, `query_graph` and `topic_tree` each take `metacontexts`, and
  results are nodes standing in **any** of them. Omitting the list reads every
  metacontext, which is a coherent question; that is why the read side is
  optional where ingest is not ([docs/RETRIEVAL.md](docs/RETRIEVAL.md)).
- **Nothing invents a metacontext on a node's behalf**: splits inherit what the
  parent states; a synthesised parent inherits the one set its children all
  stand in and is refused when they differ; a merge re-states the survivor's
  metacontext under the merging agent's judge, because the survivor's content
  is synthesised and no source's framing was made about that wording. Union is
  never the answer for a claim: one node asserted in two worlds is the worst
  outcome available. A name is the exception, and the only one: a topic node
  created from a tag stands in the union of the metacontexts it is used from,
  because it asserts nothing in any of them.

### Why this matters

The "Fall of Carthage" means different things in a historical metacontext
and in a fictional universe. AI capabilities described in a novel are not real-world
research. Political events described by opposing parties carry different
framing. Without metacontext the memory would conflate these and silently
corrupt retrieval, so search results always carry their metacontext labels,
and fiction is never mixed with fact without the distinction showing.

## Retrieval

`search` is one tool with several arms behind it. Full detail:
[docs/RETRIEVAL.md](docs/RETRIEVAL.md).

- **Two arms, because they fail in opposite directions.** Embedding
  similarity has no notion of term rarity, so an identifier like `JIRA-4417`
  embeds close to every other ticket id. A keyword arm (BM25) runs beside the
  vector arm, over node content and over the raw segments the text came from.
- **Fusion is by rank, never by score**, because cosine and BM25 are on
  incomparable scales.
- **Valid time answers in groups, never as a filter**, because a filter would
  turn missing metadata into a silent false negative.
- **Corroboration is asked for, not assumed**: how many independent
  publishers back a result, derived at read time from `similarity` edges
  recorded by agent verdicts and never stored.

## Node History

Epimemer is append-only for **knowledge content**: a node's `content`, its
`source_id`, `created_at`, and provenance are never changed. A correction or
consolidation creates a new node linked to its predecessor via typed edges:

- **Update**: `node_v1 --superseded_by--> node_v2` for a correction,
  `node_v1 --temporally_followed_by--> node_v2` for a world-change, and
  `node_v1.status` records the same *why*: `corrected` (it was wrong) or
  `historical` (it was right, and remains right of its period). The caller
  must say which; there is no default, because filing a change in the world
  as an error is how a graph forgets its own history. The status also decides
  which edges follow the replacement, and whether the node can be restored
  when its claim becomes true again ([docs/VALIDITY.md](docs/VALIDITY.md)).
- **Merge**: `node_a --merged_into--> node_c`,
  `node_b --merged_into--> node_c`.

History is part of the graph itself rather than a separate versioning system;
traversing it is following edges backwards. An edge a judge decides should
stop counting is retired rather than deleted, so the record still says it was
once asserted.

### What is immutable and what is mutated in place

A node also carries lifecycle and judgment metadata that *is* mutated in place,
because it is not the knowledge claim and editing it rewrites no history:

| Mutated in place | Set by | Why it is not a version |
|---|---|---|
| `status`, `superseded_at` | supersede / merge | this is how a node is retired, and how "what the graph held at time T" is reconstructed: transaction time, never validity |
| `value.confidence`, `claim_kind` | the ingesting agent at `store_decomposition`, or absent; `rejudge` revises them and keeps the replaced value on the node | a judgment about the claim rather than the claim; a correction mints a new node instead |
| `importance`, `importance_judged_at` | `judge_importance` | a recorded assessment of the same claim, with its own reason trail |
| `retrieved_at` | `search` | a record that the node was read, not a change to what it says |

So "a node is never mutated" is shorthand for "a node's *content* is never
mutated".

- **Current state** = all nodes with `status = "active"`.
- **State at time T** = all nodes where `created_at <= T` and
  (`superseded_at IS NULL` or `superseded_at > T`).

### Archival

Archival is an **export**, never a delete, and **`historical` nodes are
excluded** from it: they were retired because the world changed, not because
they were wrong, so age alone is not grounds to discard them
([docs/REFLECTION.md](docs/REFLECTION.md)).

## Valid Time: when a claim was true

Node History above is **transaction time**: when the graph learned something.
Valid time is the other axis, and the two must never be conflated. Full
detail: [docs/VALIDITY.md](docs/VALIDITY.md).

**The Saint Petersburg problem.** Saint Petersburg was Petrograd was
Leningrad was Saint Petersburg, and every one of those was true. A model that
can record such a pair only as a contradiction or a correction files
historical truth as error and lets an inference combine claims that were
never simultaneously true, with nothing to detect it.

So validity lives on the `sourced_from` edge, **per source**, and is never
collapsed onto the node: a claim with two sources has two periods, and union
and intersection both lie undetectably. Comparison answers `before`, `after`,
`overlap` or `unknown`, and every consumer treats `unknown` as *we cannot
tell*. Ingest supplies the intervals, and reflect proposes where a period
ends, because only something that sees the next document can close the first.

## Reflection

`reflect` **changes nothing in the graph**: it scans it, nominates
candidates, and hands them back, and the only thing it resets is the count of
stores since the last reflect. Every change goes through
`apply_reflection`, and the judgment in between belongs to the agent, or, for
the consequential calls, to a human. Full detail:
[docs/REFLECTION.md](docs/REFLECTION.md).

The principle underneath is one line: **embeddings are a good candidate
generator and a poor judge.** Similarity nominates *these two facts are about
the same thing*; only an agent can answer *do they contradict, supersede, or
coexist?* So `reflect` returns pairs with their scores rather than verdicts.

Every nomination has a recordable answer, including the negative one, and a
recorded answer is not asked for again until something under it changes;
`reopen` puts a question back without asserting anything about the answer.
Cross-metacontext pairs are dropped rather than reported, because high
similarity across disjoint metacontexts is coexistence.

A topic merge applies only when every pair of sources clears a fixed bar. The
bar is not a parameter of the call, because a caller must not choose the bar
its own merge is checked against.

**A merge is reversible.** The information a reversal needs, which source held
which edge, exists only while the merge is being made, so it is captured on the
survivor then. `reverse_merge` restores the sources, replays their edges and
deletes the survivor, refusing whenever anything has accrued to it since.

## Agent Interface (MCP)

Memory is exposed as tools, not as a raw database. See
[INTEGRATION.md](INTEGRATION.md#available-tools) for every tool, grouped by
purpose, with a one-line description of each.

### Who is judging

The **user** picks which judge an agent is: `claim_agent` proposes a name and
a self-description, and a judge nobody approved is refused, because an agent
that could admit its own identity could not then establish that a
*different* agent reviewed anything. Every decision names its judge, ingest
included, and is appended to a **journal** with no update path, so *what did
this agent judge* is one query; `review` reads it back least certain first.
See [docs/ATTRIBUTION.md](docs/ATTRIBUTION.md).

Historical graph state is read with `graph_as_of` (a lifecycle snapshot at a
past instant) and `query_changes` (births and retirements across a window),
not via an `at_time` parameter on `search`. That is *transaction* time; when
a claim was **true** is `search(valid_as_of=…)`, and the names are marked on
both sides so neither inherits the wrong default reading.

## Storage

Four backends implement one `StorageBackend` protocol, in full, with no
capability flags: **in-memory** (fast, ephemeral, used by most tests),
**SQLite** (persistent, a file per graph and nothing to run), **SurrealDB**
(persistent; served over `ws://`, or embedded via `mem://`, `file://` or
`surrealkv://`) and **Postgres** (persistent, for a graph several servers
share). Callers invoke the protocol unconditionally, and guard tests compare
signatures.

### Multi-graph support

Every backend holds multiple named graphs, and agents manage them with
`list_graphs`, `use_graph`, and `delete_graph`.

**The active graph is process state, so every other tool must name the graph
it means**: each requires an `expected_graph`, reads as much as writes, and
refuses when it is missing or wrong
([INTEGRATION.md](INTEGRATION.md#which-graph-a-server-opens)). There is no
setting to relax it: a per-graph flag would be read from whichever graph the
call is *actually* in, which would disable the guard in exactly the case it
exists for.

### Scaling limits

The limits are **measured**, not estimated; `dev-docs/BENCHMARKS.md` has the
data. `reflect` is the limiting operation, its candidate pair lists are the one
quadratic cost, and its response is capped (`max_nominations` per list, cut
lists named in `truncated`). Ingest is flat. SQLite and Postgres have not been
measured. Do not point a large persistent graph at this unwarned.

## Implementation Approach: Petri Nets via Petritype

The system must not be a black box: a newcomer should be able to look at a
processing stage and see what is happening, what state data is in, and how it
flows. So the stages that move data through steps are executable, typed Petri
nets, via [Petritype](https://github.com/olenive/petritype), and the
dashboard's pipeline strip draws each net and lights it as it fires.

Four nets exist: the two segmentation strategies, edge creation at ingest, and
hybrid retrieval, whose two arms fork from one request and join at fusion, the
one shape where the net says something a function body would hide. Each is
declared acyclic at construction, so termination is checked when the net is
built rather than hoped for at run time.

Everything else, reflection, review, timelines, metacontexts and transfer, is
plain functions over the storage protocol. A net is used where a stage has
steps worth watching; a reduction over a similarity matrix or a transaction
over the journal does not get one. The Pydantic data-model types serve double
duty as storage schema and token types.

## Observability

The black-box principle has a running answer: a live dashboard, described in
[README.md](README.md#visualization). Two of its choices are architectural:

- **A standalone hub, not a server per MCP process.** Sessions dial out and
  register; the browser picks one. So several agents can be watched from one
  page, and no MCP process has to own a port.
- **The panel showing what a call returned is labelled "Response", not
  "Context"**: what lands in the model's context is the client's rendering of
  what was returned, and a panel captioned "what the agent saw" would claim
  something the system cannot verify.
