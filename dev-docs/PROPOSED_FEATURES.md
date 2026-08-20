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
superseding node, and neither did `query_changes`. Filed as `ISSUES.md` #57 and
**resolved 2026-08-17**: counterpart ids on both surfaces, with the append-only
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
`SemanticPalette` in `theme.ts` (ISSUES.md #56) — not as picker work, but
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
`ISSUES.md` #53 T2): recurrence-rule facts — "Christmas is Dec 24–26,
annually" — are `CyclicalTimeline`'s case and **never route through
supersession or restore**; they never stop being true, so they have no
lifecycle, and their occurrences are separate event facts.

---

### The three missing notebooks

**What.** `notebooks/00_foundation.py` (storage + vector search + type
diagrams), `07_timelines_metacontext.py`, and `08_orchestration.py`.

**Why.** The existing notebooks are how the system is explained to someone
approaching it. The gaps are conspicuous: `00_` is the entry point, and the two
missing later ones cover the parts hardest to understand from the code alone.

**Cost.** Small each, and independent. Marimo, so remember the constraints in
CLAUDE.md — cells are functions, they return values, and no variable is
redefined across cells.

**Blockers.** None.

---

### Benchmark coverage

**What.** The gaps are listed in `dev-docs/BENCHMARKS.md` → *Not yet measured*
and are not repeated here: a remote (non-loopback) SurrealDB, a diverse corpus,
real embeddings end to end, and embedding throughput separated from ingest.

**Why.** Every current number is a floor measured against mocked embeddings on
loopback. The diverse-corpus gap is the one that most affects conclusions, and
**it runs the opposite way from what this entry originally claimed**: the mock's
similarity distribution degenerates as vector width grows, so at the width the
bench actually runs only ~0.05% of pairs clear the threshold against ~19% for
real embeddings. Anything scaling with surviving candidate pairs is therefore
**understated by about three orders of magnitude** — which is exactly why the
quadratic memory growth in `ISSUES.md` #60 was invisible here. The measured table
by width is in `BENCHMARKS.md`.

**Cost.** Small per item. Embedding throughput is a `scripts/bench.py` flag; a
diverse corpus is a fixture; a remote SurrealDB is a deployment, not code.

**Blockers.** None.

---

## Needs a decision before it needs code

### Merge for Facts and Inferences

**The state.** `merge_nodes` is already type-agnostic — it embeds, migrates and
dedupes edges, and retires sources as MERGED with `merged_into` lineage. But
`apply_reflection(merges=...)` accepts **Topics only**, so the capability exists
and is unreachable for two of the three node types.

**The question, which is not a coding one.** Should near-duplicate Facts merge?
Probably, and it is the obvious next step. Should Inferences? Inferences are
deliberately designed to let competing derivations coexist, so merging them
risks collapsing a disagreement the graph is supposed to preserve.

**What would settle it.** A rule for when two derivations are the same claim
rather than two claims that agree. Until that exists, extending the wired path
would be guessing.

**Cost once decided.** Small — the machinery is built. This is a gate on
judgment, not on work.

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

- ~~**Batched node and embedding reads**~~ — `ISSUES.md` #14, step 4. **Done.**
  This row read "ahead of everything in this file" while `reflect` on SurrealDB
  was the one operation failing at a size real use reaches (~2,000 nodes). #14
  and #47 took that crossing to ~26,000 on SurrealDB and ~320,000 in-memory, and
  what binds it now is the bytes moved to compare vectors. **The successor
  concern is memory, not time**: `reflect`'s candidate pair lists are quadratic
  and uncapped (`ISSUES.md` #60), and can exhaust memory *below* the timeout
  crossing.
- **A dedicated read connection for viz snapshots** — `ISSUES.md` #16, deferred
  until the server gains concurrent clients.
- **Native HNSW vector indexes** — `surrealdb_adapter.py:1105`. Waiting on
  SurrealDB, not on us.
- **Valid-time rendering on the timeline panel** — designed:
  `TIMELINE_VISUALISATION.md` §13, with a checked-in visual reference at
  `dev-docs/mockups/valid-time-grammar.html`. **Unblocked 2026-08-19** — #53 is
  built, so the intervals it renders now exist. One leg the entry did not
  anticipate has to come first: the viz snapshot carries no validity at all, so
  the grammar currently has nothing to draw. Two parts, then — per-source
  intervals into `snapshot.py`, then the SVG grammar. The grammar was designed
  early because it pins two decisions — gaps are never styled as false, bars fade
  through the now-line — that would otherwise be made by accident in the first
  renderer.
  **Its colour set shipped ahead of it** (2026-08-12, ISSUES.md #56): the
  palette was promoted to serve both panels and now lives in
  `VISUALISATION.md` C.6, built as `SemanticPalette` in `theme.ts`. A shared
  palette never depended on the interval data, and the two panels were already
  disagreeing about what colour a fact is.
