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

## Ready to build

### Colour customisation — designed, not built

**What.** A dropdown of colour pickers for the parts of the dashboard the user
actually looks at: timeline text, detail text, and every background, with the
choices persisted per theme in `localStorage`.

**Fully designed already** — `VISUALISATION.md` Part C, phased C1→C4, including
the token model, the contrast-ratio guard, and what is deliberately *not*
customisable. This entry exists only so the backlog is complete; read Part C
rather than this paragraph.

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
loopback. The diverse-corpus gap is the one that most affects conclusions — the
17-word synthetic vocabulary inflates anything scaling with surviving candidate
pairs, which is exactly the phase that fails first.

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

- **Batched node and embedding reads** — `ISSUES.md` #14, step 4. Not a
  proposal: `reflect` on SurrealDB is the one thing in the system that currently
  *fails* at a size real use reaches (~2,000 nodes), and after the batched edge
  fetch landed this is what its remaining round-trips are. Ahead of everything
  in this file.
- **A dedicated read connection for viz snapshots** — `ISSUES.md` #16, deferred
  until the server gains concurrent clients.
- **Native HNSW vector indexes** — `surrealdb_adapter.py:1105`. Waiting on
  SurrealDB, not on us.
- **Valid-time rendering on the timeline panel** — designed:
  `TIMELINE_VISUALISATION.md` §13, with a checked-in visual reference at
  `dev-docs/mockups/valid-time-grammar.html`. Blocked on #53 construction (the
  data it renders does not exist yet); the grammar was designed early because it
  pins two decisions — gaps are never styled as false, bars fade through the
  now-line — that would otherwise be made by accident in the first renderer.
  **Its colour set shipped ahead of it**: the palette was promoted to serve both
  panels and now lives in `VISUALISATION.md` C.6, with the recolour tracked as
  ISSUES.md **#56**. That part is unblocked, because a shared palette does not
  depend on the data.
