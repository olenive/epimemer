# Proposed features

Work that does not exist yet. **This is a backlog, not a set of designs** —
each entry carries what it is, why it is worth doing, roughly what it costs, and
what has to be true before it can start. A feature gets a real design when it is
picked up, in its own document, as `VISUALISATION.md`, `TIMELINE_VISUALISATION.md`
and `REVIEW_EPISTEMIC.md` all did. Designs written far ahead of building are
usually wrong by the time anyone reads them.

Three files divide the work and the rule between them is simple:

| File | Holds |
|---|---|
| `ISSUES.md` | Things that are **wrong** — bugs, and fixes deferred with a stated trigger |
| **This file** | Things that **do not exist** and might be worth building |
| Per-feature design docs | **How** a specific thing gets built, written when it is picked up |

Nothing is duplicated across them. An item here that turns out to be a defect
moves to `ISSUES.md`; an item that gets picked up grows a design document and is
reduced here to a pointer.

**Entries are written to be picked up cold** (2026-08-21). The six things an
actionable entry carries are listed in `ISSUES.md` → *Working an issue*, and they apply
here too, with **Blockers** standing in for that list's *decision* row. The
difference in kind: an issue must say what breaks, and an entry here must say
what does not exist — an entry that can only be justified as "this is currently
wrong" belongs in `ISSUES.md` instead.

**Running two of these in parallel.** The ready-to-build set below touches
mostly disjoint trees, which is what makes it parallelisable — *Colour
customisation* is `epimemer/visualization/frontend/`, *Benchmark coverage* is
`scripts/bench.py` plus fixtures, and *Specialized timelines* is
`epimemer/core/` and both storage backends. The last one is the only member that
collides with ordinary backend work, and it is also the only one asked to settle
a modelling question first.
Claim an entry by name in your commit message.

---

## Built and merged

Kept here, reduced to a pointer, until the next backlog review — the design
documents they name are the record.

### Retrieval provenance — built

**Built 2026-08-18**, merged to `main` the same day.
`RETRIEVAL_PROVENANCE.md` §10 holds the construction notes — the five further
tools the coverage oracle found, the producer-side payload guard, the
desaturation rule shared by both panels; what follows is the original rationale.

**What.** A focus mode that desaturates everything the last retrieval did *not*
return — in both panels, with the dimmed nodes still clickable — plus the
response text the agent received, reachable from a list of recent retrievals.

**Fully designed already** — `RETRIEVAL_PROVENANCE.md`. Read that rather than
this paragraph.

**Why.** The dashboard shows what is in the graph. It cannot show what the agent
was given, which is the question you have when a search disappoints. The
non-returned nodes stay on screen because half the value is seeing what was
*missed*.

**Cost.** One recording site (`_run_with_timeout` covers every tool, which
turned out to be **fourteen** carrying node ids rather than six), a bounded ring
plus an RPC, and two panels learning one appearance rule. Six commits; the first
three change nothing a user sees.

**Blockers.** None, but two ordering constraints: `LEXICAL_SEARCH.md` should
land first (it turns the provenance enum from two values into four), and the
bounded-ring module is shared with the event log, which is the simpler consumer
and validates it.

---

### Event log — built

**Built 2026-08-18**, merged to `main` the same day. `EVENT_LOG.md` §11
holds the construction notes — the seventh verb the no-`superseded` rule forced,
the rail-not-a-column layout ruling, the ring sizing; what follows is the
original rationale.

**What.** A filterable log panel of what the agent changed, whose entries
highlight their nodes in the graph on click.

**Fully designed already** — `EVENT_LOG.md`. Read that rather than this
paragraph.

**Why.** Supersession is a destructive-looking act performed invisibly: a belief
the graph held becomes historical, the agent decided it, and nothing shows. This
is the queued feature with the clearest epistemic justification.

**It started with a defect, now fixed.** `NodeStatusChanged` did not name the
superseding node, and neither did `query_changes`. Filed as counterpart ids and
**resolved 2026-08-17**: Counterpart ids on both surfaces, with the append-only
lifecycle-episode list from `EVENT_LOG.md` §6. The panel itself
(`EVENT_LOG.md` §9 steps 2–6) followed on 2026-08-18.

**Cost.** A coarse per-transaction event, a bounded ring in the hub, a panel.
Six commits; the first three change nothing a user sees. No storage work, and
the filtering is client-side — deliberately not BM25, for the reason in
`EVENT_LOG.md` §5.

**Blockers.** None. Shares its bounded-ring module with retrieval provenance
(`RETRIEVAL_PROVENANCE.md`), which is the argument for building this one
first — it is the simpler consumer and it validates the ring.

---

### Lexical search — built

**Built 2026-08-18**, merged to `main` the same day. `LEXICAL_SEARCH.md`
§11 holds the construction notes — engine-dialect negotiation, the
embedded-core IDF divergence, the no-stemming ruling; what follows is the
original rationale.

**What.** BM25 keyword retrieval over nodes *and* segments, fused into `search`
alongside the existing vector path by Reciprocal Rank Fusion.

**Fully designed already** — `LEXICAL_SEARCH.md`, including the SurrealDB 3.0
syntax and BM25 scoring behaviour verified against the running engine. Read that
rather than this paragraph.

**Why.** `search` is vector-only, and sentence embeddings cannot find an
arbitrary identifier: `JIRA-4417` and `JIRA-4418` embed to nearly the same
point. There is no substring or token match anywhere in the storage protocol, so
a node reachable only by a rare token is unreachable. Indexing segments as well
as nodes covers the case where extraction paraphrased the identifier away
before it ever reached a node.

**Cost.** One new protocol method on both backends (SurrealDB uses native FTS;
the memory backend needs BM25 in Python), a second for the segment→node bridge,
a new transition and a fusion step in the query net. Six commits, each green
alone; the first four change nothing agent-visible.

**Sequencing.** Should land *before* retrieval provenance
(`RETRIEVAL_PROVENANCE.md`). That feature's record distinguishes two seed
tiers today and four once lexical exists; designing it as a boolean first
means rebuilding it after.

**Blockers.** None.
### Review mode and agent attribution — built

**Built 2026-08-22 and 2026-08-23**, every step. `REVIEW_MODE.md` is the design
and the record; §10's build order is ✅ throughout and §12.1 states that no
design question remains open.

**What.** An agent registry, a judge recorded on every decision the system
makes, a decision journal, and a `review()` loop whose modes run from *the
uncertain ones* to *all of them*. The use case is a second agent auditing a
first agent's work — and it is what makes *"a different agent reviewed this"*
something a graph can show rather than something an agent asserts.

**What shipped, in one line each:** merge reversal with its pre-merge partition
capture and cycle limit; `apply_reflection(similarities=…)` and the `assessed`
edge, which stopped the re-nomination treadmill; the agent registry with
approval reaching a person rather than the agent; the judge threaded through
every reflect-side and ingest write path, with a per-graph requirement setting
that ships default-off; the decision journal; and `review()` with its modes,
ordering, filters, `apply_review` and `rejudge`.

**Two things were scoped out here and built later** as `reframe` and
`correct_interval`: a metacontext assignment that could not be withdrawn, and a
validity interval that could not be corrected. Both were kept out of `rejudge`,
on the grounds that the split is about **addressing** rather than naming.
### Inference merge, advisories and warning settings — built

**Built 2026-08-28.** `WARNINGS_AND_SETTINGS.md` is the record, reduced to what
the code cannot say for itself.

What shipped: `merge_inferences(source_ids, content)` beside `merge_facts`;
`reflect`'s eleventh nominee list, `inference_merge_candidates`, scoped to
inferences sharing a premise; a general `Advisory` facility with a per-graph
`WarningPolicy` and the `configure_warnings` tool; and
`DecisionKind.PROCEEDED_DESPITE_ADVISORY` with `review(mode="advisory")` reading
it back — which is what turned that mode from a refusal into a selection.

**Two things the entry got wrong before it was built, both recorded because they
changed the design.** The first reading treated a merge's premise union as a
*fabrication* and concluded inferences must never merge; it is not, because the
agent writes fresh content over the combined premises, so disjoint premises make
the result genuinely unsound rather than falsely flagged — which is what made a
pre-decision warning the right shape instead of a refusal. And the measured
*"zero pairs at the nomination bar"* was a fact about a corpus with no merges in
it: the population appeared the same day facts started merging.

**Two pieces are deliberately still unbuilt** and are below: similar-inference
edges, and getting advisories onto the dashboard.

---

### The missing notebooks — built

**Built 2026-08-28.** `00_foundation.py` (the three node types, the store,
vector search, and a type diagram) and `07_timelines_metacontext.py` (frames
against periods — which world a claim is about, versus when it held).

**`08_orchestration.py` was not written, and should not be.** `06_orchestration`
already covers the orchestration net; a second notebook on the same subject
would duplicate it. What it needed was fixing, not doubling — it offered an
`ingest` action the net has never had and looked for an `IngestInput` place that
does not exist, so it raised on load. Fixed in the same visit.

**That defect is why `test_every_notebook_runs` exists.** `test_notebooks.py`
had named this class as out of reach — *"a notebook whose imports are fine but
whose body reads something gone still passes"* — on the grounds that catching it
needs execution, and execution needs providers, storage and a runtime. It turns
out to need none of those: marimo compiles the dataflow into each cell's
signature and final `return`, so running the cells in file order with a stub for
the UI reproduces what marimo does, and the notebooks build their own in-memory
store. The check caught two bugs in `00_foundation.py` as it was written.

**The remaining gaps are 02 and 05** — decomposition and reflection — deleted
when they broke rather than repaired. Reflection is the larger loss: it is the
subsystem hardest to understand from the code, and it now has no notebook at
all. Not filed as work here until somebody wants it.

---


---

## Ready to build

### Colour customisation — designed, not built

**What.** A dropdown of colour pickers for the parts of the dashboard the user
actually looks at: timeline text, detail text, and every background, with the
choices persisted per theme in `localStorage`.

**Fully designed already** — `VISUALISATION.md` Part C, phased C1→C4, including
the token model, the contrast-ratio guard, and what is deliberately *not*
customisable. This entry exists only so the backlog is complete; read Part C
rather than this paragraph.

**One piece is already built.** C.6's semantic palette shipped 2026-08-12 as
`SemanticPalette` in `theme.ts` — not as picker work, but
because the graph and timeline panels disagreed about what colour a fact is.
The hues now have a single per-theme home, which is a small down-payment on C1's
"one source of truth for colour". Everything else in Part C is unbuilt.

**Cost.** C1 (the Tailwind token migration) is the bulk of it: ~230 grey class
occurrences collapse to nine CSS-variable-backed semantic tokens. Large but
mechanical, and it ends with a structural test that stops the migration rotting
back. C2 and C3 (store, picker) are a few hundred lines on top.

**Why it is worth doing even without the picker.** C1 gives `theme.ts` and the
markup a single source of truth for colour. They currently duplicate those
values and have already drifted once — the light-mode darkening pass had to fix
the timeline axis separately from the chrome.

**Blockers.** None.

---

### Specialized timelines

**What.** Only the base `Timeline` / `Timepoint` model exists. SUMMARY.md →
*Timelines* → *Multiple Implementations* describes three that do not:

- **`PreciseTimeline`** — a datetime interval index supporting range and
  proximity queries.
- **`VagueTimeline`** — labelled points ordered by relative before/after
  constraints rather than coordinates.
- **`CyclicalTimeline`** — templates such as "every Monday", mapped to concrete
  instances when something links to them.

**Why.** The timeline panel currently plots what curation and extraction happen
to produce. These are what would let a caller *ask questions* of a timeline —
what happened near this, what overlaps this, what recurs — rather than only
render it.

**Cost.** The largest item in this file. Each needs add/remove/reorder with
stable UUIDs, its own query surface (proximity, overlap detection), and a
storage round-trip on both backends per the parity rule. `VagueTimeline` is the
awkward one: relative ordering is a constraint graph, and it has to stay
coherent when constraints conflict.

**Blockers.** None technical, but worth a decision first: whether these are
three types or one type with a mode. The current `Timepoint` already spans
concrete, interval, and label-only, so the case for three separate models is not
obvious and should be argued before any of it is built.

One constraint already decided ahead of it (2026-08-17, recorded in
`VALIDITY_DESIGN.md` T2): recurrence-rule facts — "Christmas is Dec 24–26,
annually" — are `CyclicalTimeline`'s case and **never route through
supersession or restore**; they never stop being true, so they have no
lifecycle, and their occurrences are separate event facts.

---

### Benchmark coverage

**What.** The gaps are listed in `dev-docs/BENCHMARKS.md` → *Not yet measured*
and are not repeated here: a remote (non-loopback) SurrealDB, a diverse corpus,
real embeddings end to end, and embedding throughput separated from ingest.

**Why.** Every current number is a floor measured against mocked embeddings on
loopback. The diverse-corpus gap is the one that most affects conclusions, and
**it has now been measured twice, each time smaller than the last**:

| corpus scored | pairs clearing 0.80 |
|---|---|
| bench text, mock at 384 (what the bench runs) | 0.05% |
| bench text, real `all-MiniLM-L6-v2`, fact length | 1.11% |
| real facts, `memory` graph | **0.0105%** |

So the mock understates the *bench's own* corpus by about 20×, and **overstates
the real one by about 5×**. Both readings are in `BENCHMARKS.md`; the tables
there are the record.

> **Corrected 2026-08-21.** This entry read "~19% for real embeddings …
> understated by about three orders of magnitude … which is exactly why the
> quadratic memory growth in the nomination cap was invisible here". Every clause
> of that is now withdrawn elsewhere in the repo and was left standing here:
> `BENCHMARKS.md` records that the 19% was taken at a narrower mock width and
> "no longer describes this configuration", and this file's own *Tracked
> elsewhere* bullet says the nomination cap's memory projection was measured and withdrawn with
> "no successor concern". **A retraction has to be carried to every entry that
> was resting on the retracted number** — a backlog entry keeping a motivation
> the rest of the repo has dropped will send somebody to do work for a reason
> that no longer exists.

**The case survives the correction, and is narrower.** A diverse corpus is still
worth having, because every pair-scaled figure here is measured on text whose
survival rate matches nothing real in either direction — but it buys accuracy,
not a hidden failure.

**Cost.** Small per item. Embedding throughput is a `scripts/bench.py` flag; a
diverse corpus is a fixture; a remote SurrealDB is a deployment, not code.

**Blockers.** None.

---

## Needs a decision before it needs code

### Advisories on the dashboard

**The state.** Advisories exist, are recorded, and reach the agent. Nothing
shows them to a person watching the graph.

**The obstacle is one decision, not the work.** The event bus emits at the five
`_tx` boundaries (`EVENT_LOG.md`), and an advisory is **not** a transaction — it
is computed before one and may accompany a call that writes nothing. So this
needs either a new event kind or a deliberate choice to carry the advisory on
the act that triggered it. The second is cheaper and couples the two; the first
is honest about what an advisory is.

**Also worth deciding at the same time**: the settings panel for
`configure_warnings`. It is per graph and has to say so on its face — the
dashboard follows a `use_graph` switch, and a panel that looks global while
writing per-graph state is a trap. *Inherited* is a fourth visual state beside
the two actions, because a kind following the process default is not the same as
one explicitly set to the same value: only the first tracks a changed default,
and without showing which, clearing an override is impossible through the UI.
Reuse `SemanticPalette` in `theme.ts` rather than minting colours.

**Cost.** Small once the event decision is made.

---

### Similar-inference edges

**The state.** `reflect` nominates near-identical inferences that **share a
premise**. Ones that do not are nominated by nothing, and the proposal is to
offer `similarity` edges between them. Nothing enforces node types on
`SIMILARITY` — `link` checks only that both nodes exist — and retrieval already
traverses it, so the expansion benefit arrives for free.

**One consequence has to be decided first, and it is a live change to a number
callers read.** `corroboration` walks `SIMILARITY` neighbours, so
inference-to-inference edges would make agreeing inferences corroborate each
other. Defensible as independent support, and not a retrieval nicety.

**The decision is narrower than it looks.** The corroboration walk now treats a
`SIMILARITY` neighbour three ways — *counted*, *excluded* (contradiction,
variant, corrected) or *reported without counting* (`adjacent_periods`) — so
this is a choice among three existing treatments rather than a yes/no. Agreeing
inferences most likely want the third.

*Files*: a nominee list in `pipelines/reflection/` beside the existing ones,
capped as the pair-built lists are, and `pipelines/query/corroboration.py` for
the consequence.

---

### LLM-guided and hybrid segmentation

**The state.** Paragraph and semantic-similarity segmentation are built and
cover the current use cases. LLM-guided splitting is designed nowhere and
buildable only after an architectural decision.

**The question.** The server **makes no LLM calls of its own** (SUMMARY.md →
*Epimemer makes no LLM calls*), and that is load-bearing: it is why the system
has no API keys, no provider configuration, no per-call cost, and no opinion
about which model you use. Two ways out, and they are not close:

1. **Delegate the split to the calling agent** — the agent segments and passes
   the result to `store_decomposition`, exactly as it already does for
   decomposition. Preserves the property entirely. Costs a round trip and makes
   segmentation quality the agent's problem.
2. **Re-introduce a provider abstraction** — the server calls a model itself.
   More capable and self-contained; gives up the property and everything that
   follows from it.

**Recommendation, for whoever picks this up.** (1), unless something concrete
turns out to be impossible that way. The no-LLM-calls property is worth more
than the convenience, and it is far easier to give up later than to win back.

**Blockers.** The decision above. No code should be written before it.

---

## Tracked elsewhere, listed so the backlog is complete

- ~~**Batched node and embedding reads**~~ — batching, step 4. **Done.**
  This row read "ahead of everything in this file" while `reflect` on SurrealDB
  was the one operation failing at a size real use reaches (~2,000 nodes). Batching
  and the pair-loop fix took that crossing to ~26,000 on SurrealDB and ~320,000 in-memory, and
  what binds it now is the bytes moved to compare vectors. ~~**The successor
  concern is memory, not time**: `reflect`'s candidate pair lists are quadratic
  and uncapped, and can exhaust memory *below* the timeout
  crossing.~~ **Measured 2026-08-20 and withdrawn.** Real fact pairs clear the
  0.80 threshold at **0.0105%**, projecting ~5,200 surviving pairs and ~3 MB at
  10,000 facts — not the ~14 GB the estimate implied, which had been taken from
  longer templated text and applied to fact-length pairs. The pair-built
  lists are capped anyway as of 2026-08-21, but as a **response** bound
  for readability, not as a memory fix. There is no successor concern; the next
  performance issue should come from a profile.
- **A dedicated read connection for viz snapshots** — deferred
  until the server gains concurrent clients.
- **Native HNSW vector indexes** — `surrealdb_adapter.py:1105`. Waiting on
  SurrealDB, not on us.
- **Valid-time rendering on the timeline panel** — designed:
  `TIMELINE_VISUALISATION.md` §13, with a checked-in visual reference at
  `dev-docs/mockups/valid-time-grammar.html`. **Unblocked 2026-08-19** — the validity model is
  built, so the intervals it renders now exist. One leg the entry did not
  anticipate has to come first: the viz snapshot carries no validity at all, so
  the grammar currently has nothing to draw. Two parts, then — per-source
  intervals into `snapshot.py`, then the SVG grammar. The grammar was designed
  early because it pins two decisions — gaps are never styled as false, bars fade
  through the now-line — that would otherwise be made by accident in the first
  renderer.
  **Its colour set shipped ahead of it** (2026-08-12, the drifted lookup tables): the
  palette was promoted to serve both panels and now lives in
  `VISUALISATION.md` C.6, built as `SemanticPalette` in `theme.ts`. A shared
  palette never depended on the interval data, and the two panels were already
  disagreeing about what colour a fact is.
