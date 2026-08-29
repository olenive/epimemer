# Benchmarks

Measurements from `scripts/bench.py` (`make bench`). This file describes **where
the system stands now**, not how it got here — superseded runs are deleted rather
than kept, and `git log` holds them if a comparison is ever wanted.

It is what keeps the batching work honest — the full-scan / N+1 problem, and
these numbers are what decides which of its remaining N+1 sites is worth
attacking — and, twice now, what proved the site it had nominated was not the
one costing the time.

**When a change moves one of these numbers, measure before *and* after on the
same machine in the same session, then replace the numbers here with the new
ones.** Machine state matters more than it looks — a run taken against a
previously recorded baseline on a busier box produced a 40% discrepancy on a
metric the change could not touch. A same-session pair with unchanged controls is
trustworthy; a cross-run comparison is not.

## How to read these

- **Embeddings are mocked** (`mock-384`, genuinely 384-wide). Model inference is
  a constant per text that would dominate and hide the graph costs this exists to
  expose. Every number here is therefore a **floor** — real ingest is slower by
  the embedding time, real search by roughly one query embedding. That constant
  is now measured rather than gestured at: **ingest is 7.8× slower** under
  `all-MiniLM-L6-v2`, and nothing after write time moves.
- **The synthetic corpus produces almost no candidate pairs, which is the
  opposite of what this note used to say.** It read: *unrealistically
  self-similar … 19% of unrelated pairs clear 0.80 under the mock … anything
  scaling with surviving candidate pairs is **overstated** here.* Re-measured
  2026-08-20 over 400 bench sentences, by the mock's vector width:

  | width | mean pair similarity | share clearing 0.80 |
  |---|---|---|
  | 8 | 0.762 | 40.9% |
  | 64 | 0.752 | 10.8% |
  | **384 — what the bench runs** | 0.749 | **0.05%** |

  The mock builds vectors from hash bytes scaled to `[0, 1]`, so every pair sits
  near 0.75 and the spread narrows as the width grows; at 384 almost nothing
  crosses the threshold. The 19% figure was taken at some narrower width and no
  longer describes this configuration. **Every `reflect` figure below is
  therefore measured on a corpus that generates essentially no surviving pairs**,
  and understates anything scaling with them. Node- and edge-scaled costs are
  unaffected and remain trustworthy. What this hid is filed as **the nomination cap**.
- **The 49% did not stand, and why it did not is the more useful finding**
  (measured 2026-08-20, `scripts/corpus_measure.py`). It was carried here as
  "49% for real `all-MiniLM-L6-v2` on similarly templated text" and used to
  project `reflect`'s memory. Re-measured through the real model at the real
  0.80 threshold, over the bench's own generator:

  | text scored | words | median pair similarity | share clearing 0.80 |
  |---|---|---|---|
  | bench paragraph / segment | ~36 | 0.842 | **74.9%** |
  | bench fact — **what `reflect` scores** | 8 | 0.500 | **1.11%** |
  | bench topic | 4 | 0.328 | 0.62% |

  **Pair similarity is dominated by text length**, and over a fixed 17-word
  vocabulary it climbs steeply with it: 0.62% at 4 words, 1.11% at 8, 3.70% at
  12, 21.8% at 20, 74.9% at a paragraph. So 49% is a real number for *some*
  templated text and the wrong number for the pairs `reflect` actually forms,
  which are fact-length. **A survival rate is meaningless without the text
  length it was measured at** — that is the correction worth carrying forward,
  and it is why the row above names the words as well as the rate.
- **Node counts are exact**: 4 nodes per segment, 5 segments per document.
- **All network numbers are loopback.** A remote SurrealDB is worse by the RTT
  difference times the round-trip count.
- **Absolute SurrealDB figures are soft.** They move with whatever else the
  machine is doing; the shapes and the ratios are the durable part.

**Machine:** Apple M4 Max, macOS 26.4.1, arm64, Python 3.14.0.
**SurrealDB:** `surrealdb/surrealdb:latest start … memory`, loopback container.

---

## Where things stand

**Measured at 2,000/4,000/8,000 rather than the 1,000/2,000 this table used to
carry.** `reflect` got fast enough that the small sizes no longer separate the
fixed costs from the scaling ones, and a two-point exponent turned out to be
worth little — see the warning below the crossings.

| Nodes | Backend | ingest (docs/min) | search p50 | `list_sources` | `reflect` |
|---|---|---|---|---|---|
| 2,000 | memory | 18,487 | 45.1 ms | 31 ms | 197 ms |
| 4,000 | memory | 18,805 | 88.5 ms | 64 ms | 365 ms |
| 8,000 | memory | 18,171 | 177.0 ms | 149 ms | 780 ms |
| 2,000 | SurrealDB | 3,878 | 230.0 ms | 258 ms | 819 ms |
| 4,000 | SurrealDB | 3,750 | 355.3 ms | 514 ms | 2,051 ms |
| 8,000 | SurrealDB | 3,730 | 581.8 ms | 1,034 ms | 5,696 ms |

### The 30 s crossings

`EPIMEMER_TOOL_TIMEOUT_SECONDS` defaults to **30 s**, so "crossing" means *the
tool call fails*, not *feels slow*. Fitted over three sizes, which is enough to
rank the operations and not enough to trust a third digit.

| Operation | in-memory | SurrealDB (loopback) | exponent (mem / surreal) |
|---|---|---|---|
| `search` | ~1.5M | ~2.9M | 0.99 / 0.67 |
| `list_sources` | ~870,000 | ~230,000 | 1.13 / 1.00 |
| **`reflect`** | **~320,000** | **~26,000** | 0.99 / 1.40 |
| ingest | not reachable | not reachable | flat |

**Nothing here fails at a size anyone is running.** `reflect` is still the first
to go, and SurrealDB is still the limiting backend, but the nearest crossing is
~26,000 nodes rather than the ~2,200 it was two changes ago.

> **Two points are not a shape.** The previous version of this table put
> `reflect` at a quadratic exponent of 1.75–1.87 and crossings near 7,000,
> extrapolated from 1,000 and 2,000 nodes. Three points put the same code's
> successor at 0.99 in-memory — the earlier fit was reading fixed setup cost as
> curvature, which inflates the exponent and understates the crossing badly.
> Fit over at least three sizes spanning 4×, or report the measurements without
> a crossing.

**`reflect` is quadratic in principle and does not look it yet.** Both phases
compare every pair, so the work really is O(N²); at these sizes it happens
inside two matrix products whose constant is small enough that the linear costs
— reading the vectors, walking the edges — still dominate. Expect the exponent
to climb toward 2 well beyond 8,000 nodes. In-memory measuring 0.99 is a
statement about where the crossover between those two costs currently sits, not
a claim that the pair comparison went away.

**And the crossings above are time only.** `reflect` also allocates ~580 bytes
per *surviving* pair, which nothing caps — on a corpus where pairs actually
survive, memory can fail before the timeout does, at a size below the ~26,000
crossing. The corpus here produces almost none, so these figures cannot show it.
Measurements and projection: **the nomination cap**.

---

## What each operation's cost is made of

**Ingest — not a problem.** Flat across every size measured on both backends. The
write path has never been the ceiling.

**The exact-content lookup, though, was linear in table size (the content-lookup index, fixed
2026-08-19).** `get_node_by_content` runs on the write path, and left to itself
SurrealDB resolved it through `idx_{table}_status` — an index matching every
active row — with `content` applied afterwards as a predicate. Measured per call
on a real server:

| topics | planner's own choice | with a `content` index defined | with the index named |
|---|---|---|---|
| 400 | 1.05 ms | 1.08 ms | — |
| 1,200 | 2.63 ms | 2.04 ms | — |
| 3,000 | 4.02 ms | 4.30 ms | **0.53 ms** |

**Defining the index does nothing; naming it is the whole fix.** A composite
`(content, status)` index plans the same way. `WITH INDEX idx_{table}_content`
moves `content` into the access path and leaves `status` as the post-filter.

The write cost that made this worth measuring rather than patching is not
there: three seeds of 3,000 nodes each way came out at 1,944 ms median
unindexed against 2,033 ms indexed — **+4.6%**, inside a run-to-run spread of
1,728–2,721 ms. Cheap enough that ingest now affords one such lookup per fact,
which is what the verbatim-recurrence check needs.

**`search` — the cheapest of the three, on both backends.** In-memory it is
linear and always was. On SurrealDB it is close to flat: ranking happens before
the status filter, because SurrealDB re-runs such a subquery per embedding row
and that cost two orders of magnitude when it was inside the ranking query. The
per-result enrichment round-trips that used to floor it at ~120 ms are now
batched — one query per annotation kind for the whole result set rather than per
hit — which is most of the 1.2–1.4× it gained.

**`list_sources` / `list_relations` — no longer an N+1.** Both used to iterate
every active node and fetch that node's edges: O(N) queries per call. Both now
ask once for the whole active set (`get_edges_for`), which took `list_relations`
from 803 queries to 7 at 400 nodes and `list_sources` from 883 to 87. What is
left in `list_sources` is one `get_document` + `get_node` per *distinct source*,
which is bounded by sources rather than by graph size. It gained a further
2.2–3.3× when the edge fetch stopped using `IN` for large id sets (below), which
is also what flattened its exponent from 1.56 to ~1.

**`reflect` — still the limiting operation, and now bound by bytes.**

- **In-memory it is arithmetic-bound.** Both pair phases compare every pair,
  which is genuine work rather than redundancy, so both were made fast rather
  than made smaller: `similar_pairs` (`pipelines/reflection/pair_scoring.py`)
  stacks the vectors and takes a matrix product per 512-row block instead of one
  Python call per pair. Facts went first (the profiling pass, 4.1–4.6×) and topics followed
  once storage stopped hiding them (the pair-loop fix, **7.3× at 1,000 nodes and 16.5× at
  2,000** in-memory — the ratio grows with size because what was removed was
  quadratic and what remains is not). Storage batching buys nothing on this
  backend, as expected of one with no round-trips: **1.03–1.10×** for batching step 4,
  which is noise.
- **On SurrealDB it was round-trip bound, and is not any more.** Step 4 bought
  **6.19× at 1,000 nodes and 7.09× at 2,000** (6,464 → 1,044 ms; 24,946 →
  3,519 ms), measured same-session, and took round-trips at 1,200 nodes from
  3,086 to **56**. Vectorizing the topic phase on top of that bought a
  further **2.9× and 4.4×** at the same two sizes.

  Three separate causes, and the batching everyone predicted was the smallest:

  1. **A per-call full scan of the embedding table** — 64% of `reflect` at
     1,200 nodes, and quadratic. `get_embeddings_for_item` asked
     `WHERE item_id = $i AND model_id = $m`, and the planner chose
     `idx_emb_model`, which matches *every* row for the graph's one model, then
     filtered `item_id` afterwards. Per call: 2.4 ms at 400 embeddings, 6.2 at
     1,200, 15.6 at 3,000 — dead linear in table size, inside a per-node loop.
     Asking on `item_id` alone uses `idx_emb_item` and is flat at ~0.6 ms.
     Fixing that one predicate, with no batching at all, took `reflect` at 1,200
     nodes from 8,890 ms to 3,868 ms — **2.3× from a one-line change**.
  2. **`IN` does not use an index**, so `get_edges_for` was scanning the edge
     table once per chunk *and* paying O(rows × ids) for the predicate. Reading
     the candidate rows and matching them in Python is 14× faster past ~100 ids.
  3. **The batched reads themselves** (`get_nodes`, `get_embeddings_for_items`),
     which is what batching step 4 nominated. Worth 3,868 → 1,465 ms once the two
     above were fixed. Real, and the smallest of the three.

  The write side had already gone: value decay was one `UPSERT` per active node
  per pass, and removing `relevance` removed it, making `reflect` a pure
  read.

  **What binds it now is payload, not round-trips or arithmetic.** At 4,000
  nodes `reflect` is 2,032 ms, of which 1,840 ms is inside **87** queries — 21 ms
  each, because those queries move whole tables: 894 ms for the two
  `get_embeddings_for_items` calls that fetch every fact and topic vector, 430 ms
  of edges, 349 ms of nodes. That is close to irreducible with this design —
  pairwise comparison needs the vectors, and they are 384 floats each. Reducing
  it means moving the comparison to the server or keeping vectors across calls,
  both of which are larger changes than anything in batching or the pair-loop fix, and neither is
  worth making while the crossing sits at ~26,000 nodes.

### What the two validity phases add to `reflect` (boundary proposals and §11, 2026-08-19)

The only phases added to `reflect` since it became the limiting operation, so
their cost is worth stating rather than assuming. In-memory, mock embeddings,
median of three, each measured against the same graph with that one phase
stubbed out — a difference of one phase rather than of two whole benchmarks:

| nodes | soundness check | boundary proposals |
|---|---|---|
| 1,000 | **10.1 ms** (of 95.7) | **9.7 ms** (of 101.4) |
| 3,000 | **27.7 ms** (of 295.4) | **25.6 ms** (of 324.2) |

Both linear, and ~10% of a call each at both sizes. Nearly all of it is batched
reads neither can avoid, since the graph measured carries **no intervals at
all** — so the soundness check finds no dated premises and the boundary phase
finds no dated successions. That is the shape to expect: this feature is sparse
by design, and the common case is a phase paying its floor and reporting
nothing.

**Ordering the reads is worth a third of the soundness check.** Fetching every
premise fact and then discarding the undated ones cost 41.0 ms at 3,000 nodes;
reading validity first and fetching only the premises that can still form a pair
costs 27.7 ms. On an undated graph no nodes are fetched at all. The general form
is the one this file keeps finding: narrow before you fetch, not after.

### `reflect` reads the node set 13 times (2026-08-20)

Counted rather than estimated, because a first pass at this note guessed
"three full scans" and guessed low. One `reflect` over a 1,000-node graph issues
**13 `query_nodes` calls** and materialises **5,250 node copies** — 5.25× the
graph — split like this:

| # | Call | Phase | Rows at n=1,000 |
|---|---|---|---|
| 1 | `topic` | topic consolidation | 250 |
| 2 | `topic` | split detection (cached on for enrichment) | 250 |
| 3–4 | `fact` × active, historical | contradiction + recurrence | 500, 0 |
| 5 | `inference` | soundness check | 250 |
| 6–7 | *all* × active, historical | boundary proposals | **1,000**, 0 |
| 8 | *all* × active | `gather_pending_review` | **1,000** |
| 9–11 | *all* × superseded, corrected, merged | archival candidates | 0, 0, 0 |
| 12 | *all* × active | archival nomination | **1,000** |
| 13 | *all* × active | relation consolidation | **1,000** |

**Four of them are the same query.** Rows 6, 8, 12 and 13 each read the whole
active set, and row 13 uses nothing but the ids. None is the per-node shape batching
removed — every one is a single batched call — which is exactly why they went
unnoticed: the round-trip count stayed flat while the payload multiplied.

What that costs, and what a fix could recover (median of three, same graph,
same session):

| | in-memory | SurrealDB |
|---|---|---|
| share of `reflect` inside `query_nodes` | 39–43% | 17–23% |
| …just the four untyped active scans | ~30% | 13–16% |
| recovered by memoising identical calls | **18%** | **5%** |
| recovered by one read per status, filtered in Python | **27%** | **8%** |

The gap between the last two rows is the typed reads (rows 1–5), which a shared
active set could serve by filtering. **The share is lower on SurrealDB, not
higher**, which is the opposite of the usual shape here and worth knowing before
anyone optimises for the wrong backend: there the embedding reads dominate, so
the node scans are a smaller slice of a bigger number.

**Not yet built, and the memo is only safe because `reflect` writes nothing** —
pinned by `TestReflectWritesNothing`, so two reads of one status inside a single
pass cannot disagree. 27% of the fastest backend is real but it is not what
binds the crossing, which is on SurrealDB at ~26,000 nodes; measure again there
before spending anything on it.

**If it is built, the shape is constrained by two things already decided.** The
cache must be **lazy, not a prefetch**: `reflect` already caches its topic set
and the comment says *"lazy rather than hoisted so the fetch stays attributed to
the phase that needs it first"* — a prelude that read everything up front would
show all the cost landing on phase one, in the pipeline strip built to show
where the time goes. And it should arrive as the storage handle rather than as a
parameter on eight phase functions, which keeps each of them answerable from
`storage` alone.

**Only part of the separation is accretion.** Of the 13 reads, five ask for
genuinely different statuses and no cache removes them; four ask for one *type*
of the active set, which is strictly cheaper than a shared read in isolation and
redundant only because three other phases read the whole set anyway; four are
the same query. The "each phase is independently reusable" argument does not
hold up — of the eight phase functions, only `find_archival_candidates` has a
caller outside `reflect` today.

### What retrieval reinforcement costs

`search` writes every returned node back with a fresh `retrieved_at` — k extra
writes per call, on by default (`EPIMEMER_RECORD_RETRIEVAL=true`). Measured with
`--skip-reflect` against the same container: **+5% in-memory, +8–12% on
SurrealDB** (+14–17 ms). `search` is otherwise unchanged by step 4 (0.95–0.96×,
i.e. noise): it fetches one embedding per query, not one per node.

Cheap, and **flat in graph size**: k does not grow with the graph, so the cost is
a constant — visible on SurrealDB where each write is a round-trip, near-invisible
in-memory. It changes no crossing. Since value decay was removed this is the only
per-node write left in the read path, so it is what a batched write would serve.

### What corroboration costs, and why it is opt-in (read-time corroboration, 2026-08-20)

Read-time corroboration asked for this number before deciding whether corroboration
could go on the default `search` path. It could not. Median ms per call, over
the node set a real `search(k=10, graph_hops=1)` returns, against a localhost
SurrealDB 3.0.5 and the in-memory store, mock embeddings at 384 dimensions:

| backend | nodes | sim/fact | sim edges | result set | `review_labels_for` | `validity_for` | `corroboration_for` | `search` p50 |
|---|---|---|---|---|---|---|---|---|
| memory | 400 | 0 | 0 | 37 | 0.11 | 0.29 | **1.46** | 18.2 |
| memory | 400 | 3 | 595 | 55 | 0.16 | 0.39 | **5.36** | 19.8 |
| memory | 400 | 10 | 1,991 | 100 | 0.42 | 0.76 | **17.3** | 23.1 |
| memory | 2000 | 0 | 0 | 37 | 0.09 | 0.23 | **1.28** | 83.1 |
| memory | 2000 | 3 | 3,000 | 46 | 0.13 | 0.32 | **5.37** | 86.1 |
| memory | 2000 | 10 | 9,993 | 107 | 0.51 | 0.84 | **34.0** | 89.8 |
| surrealdb | 400 | 0 | 0 | 36 | 9.48 | 3.17 | **30.1** | 139 |
| surrealdb | 400 | 3 | 595 | 56 | 14.4 | 4.78 | **52.1** | 182 |
| surrealdb | 400 | 10 | 1,991 | 93 | 29.7 | 10.0 | **109** | 267 |
| surrealdb | 2000 | 0 | 0 | 37 | 33.1 | 10.8 | **108** | 346 |
| surrealdb | 2000 | 3 | 3,000 | 51 | 63.5 | 19.6 | **235** | 420 |
| surrealdb | 2000 | 10 | 9,993 | 111 | 41.6 | 37.4 | **540** | 545 |

The `search` column is the plain call, without corroboration — the annotation is
opt-in, so it is not in that number. Compare the two columns to see what asking
for it costs.

**Two costs, and they are separable.** With no similarity edges at all it is
~3× `review_labels_for` on SurrealDB, which is round-trips: twelve batched
queries against that function's four. With edges it is the fan-out — the walk
leaves the result set for each node's similarity neighbourhood, and nothing
bounds how large that is. At degree 10 it is nearly the whole call.

**The direction is what settles it.** Similarity edges are written by
`apply_reflection`, so this gets more expensive the more a graph has been
reflected over — and a reflected-over graph is exactly where corroboration has
something to say. Default-on would have got slower the more useful it became.
So it is `include_corroboration=False`, and the cost is stated in the tool
description rather than discovered.

Two caveats on these numbers. The similarity edges are **synthetic** — assigned
at a fixed degree by `--similarity-degree`, not produced by `reflect` over a real
corpus — so that column is a dial rather than an observation, and what a real
graph sits at is unmeasured. And the corpus is the standard synthetic one, so
this inherits its vocabulary limits like everything else here.

> **Amended 2026-08-21 — what a real graph sits at is now measured, and it is
> zero.** Both statements above need correcting, and the second caveat was
> pointing straight at the first without either being followed up.
>
> **"Similarity edges are written by `apply_reflection`" is false.** It writes
> nine kinds of decision and none of them is a `similarity` edge; nothing in the
> codebase writes one at all. The census: **0 of 4,386** edges
> on `memory`, **0 of 1,028** on `petritype-server`.
>
> **What survives.** The *shape* of the finding is untouched — the fan-out is
> unbounded, degree 10 really is nearly the whole call, and the with-edges and
> without-edges costs really are separable. The measurement was taken against a
> dial and reported as one, which is why the numbers stand.
>
> **What does not.** The argument that settled default-off. "This gets more
> expensive the more a graph has been reflected over" is not true of any graph
> that exists: reflecting writes no similarity edges, so no amount of reflection
> moves this cost. The right reading is the *without-edges* column — ~3×
> `review_labels_for`, entirely round-trips — and that is still several times
> every other annotation, so **default-off survives on the round-trip cost
> alone**. The conclusion was right and one of its two reasons was not.
>
> **The order this leaves things in.** Whatever the `assessed` edge builds will be the first
> thing that puts real similarity edges in a graph, and the degree column
> becomes an observation the first time it does. Re-measure then, against a
> graph whose edges came from judgments rather than from `--similarity-degree`.
>
> > **2026-08-22 — the writer exists now.**
> > `apply_reflection(similarities=[{pair, verdict: "one_claim", …}])` writes a
> > `similarity` edge, one agent judgment at a time. So the degree
> > column stops being a dial the moment a graph accumulates a few dozen, and
> > **the re-measurement above is now takeable rather than hypothetical** —
> > against real degrees, which the census can read directly.
> >
> > Two things to hold on to when it is taken. Real degree will be **far below
> > 10** for a long while: the edge requires an agent to have had the pair in
> > front of it and said the two are one claim, so it accumulates at the rate
> > pairs are judged, not at the rate facts are stored. And the companion
> > `assessed` edge — written for *both* verdicts, so it accumulates several
> > times faster — costs corroboration nothing, because corroboration does not
> > read it. If a future measurement shows this cost climbing, that is the first
> > thing to check: `assessed` in the neighbourhood walk would be a bug, not a
> > workload.


Reproduce with, one run per degree:

```bash
uv run python scripts/bench.py --n 400,2000 --skip-reflect \
    --publishers 4 --similarity-degree 10 --url ws://localhost:8000/rpc
```

The `annotations` record carries every column above. Both new flags default to
**off**, so every figure recorded elsewhere in this file was taken over the same
plain corpus it always was.

The lever, if it is ever wanted on the default path: the six typed neighbourhood
queries collapse into one untyped `get_edges_for` per direction, trading
round-trips for bytes. Indicated by the degree-0 column and contraindicated by
the degree-10 one, which is why it was left alone rather than guessed at.

---

## What real text actually looks like (the embedding-window measurement and the nomination cap, 2026-08-20)

Everything above is measured on generated text. Two open issues turned on what
*real* text does instead, so this takes it directly from graphs of real ingested
content — `epimemer/memory` (Epimemer's own dev history, 568 nodes / 80
segments) and `epimemer/petritype-server` (136 nodes / 28 segments) — read
**without opening a storage backend**, since those namespaces must not be
written to. `scripts/corpus_measure.py`.

### Node text does not reach the embedding window; segment text does

`all-MiniLM-L6-v2` truncates at 256 word-pieces, counted with `[CLS]`/`[SEP]`
inside the budget. Tokenized lengths over both graphs:

| corpus | n | median | p95 | max | over 256 |
|---|---|---|---|---|---|
| fact | 350 | 30 | 56 | 81 | **0** |
| inference | 124 | 38 | 56 | 63 | **0** |
| topic | 150 | 20 | 38 | 69 | **0** |
| **segment** | **108** | **148** | **305** | **496** | **12 (11.1%)** |

**Nodes have 3× headroom at their worst** — the longest of 624 real nodes is 81
word-pieces against a 256 window — so decomposed claims are simply not at risk,
and no amount of graph growth changes that: a fact is one sentence by
construction.

**Segments cross it routinely.** 10.0% of `memory`'s and 14.3% of
`petritype-server`'s exceed the window, and the worst loses **48% of its text**
to truncation. Segments are a *searchable corpus* in their own right
(`docs/RETRIEVAL.md` §3), so this is the silent under-return the embedding-window measurement describes,
confined entirely to the half nobody suspected — the entry guessed at "`Segment`
text and unusually long inference content", and inferences turn out to top out
at 63 word-pieces.

> **Correction, 2026-08-21: the numbers above stand, the conclusion drawn from
> them does not.** Segment text does cross the window at the rates given — that
> was measured and is unchanged. But **segments are never embedded**, so nothing
> ever hands that text to the tokenizer: no code path constructs an
> `EmbeddingRecord` for a segment, all 624 stored embeddings across both graphs
> point at nodes (488/488 and 136/136), `vector_search` resolves hits through
> `get_node` so a segment record could not be returned anyway, and
> segmentation's own sentence embeddings are transient. Segments are searched by
> **BM25 alone**, which indexes the whole field — which is precisely why they
> answer §3's *where did I read that?* question well, since a rare identifier is
> what lexical search finds and vectors lose.
>
> **The mistake is a reusable one, and it is this instrument's characteristic
> failure.** *Segment text crosses the window* and *segments are a search
> corpus* are both true, and the join between them is not. `corpus_measure.py`
> read segment text out of the `segment` table — the right place to ask the
> tokenizer question — and had no way to see whether that text is ever
> tokenized. **A measured quantity is not yet a measured consequence:** before a
> distribution becomes a cost, something has to be shown to pay it. The embedding-window measurement closed
> on this without code; the precondition now lives on
> `EmbeddingRecord.item_id`, where anyone adding segment embedding will meet it.

### `reflect`'s surviving-pair rate on real prose is ~0.01%, not 49%

Same model, the real 0.80 threshold, and the real stored vectors — the ones
written at ingest, so this is the distribution `reflect` sees rather than one
re-derived here:

| corpus | items | pairs | survivors | rate | median pair similarity | p99.9 |
|---|---|---|---|---|---|---|
| bench fact text (control) | 400 | 79,800 | 887 | 1.11% | 0.500 | 0.883 |
| real facts, `memory` | 277 | 38,226 | 4 | **0.0105%** | **0.164** | 0.683 |
| real facts, `petritype-server` | 73 | 2,628 | 0 | **0.0%** | 0.160 | 0.720 |
| real topics, `memory` | 112 | 6,216 | 1 | 0.0161% | 0.153 | 0.707 |

**Read the distribution, not the rate.** Four survivors is too few to trust as a
rate, but 38,226 pairs is plenty to locate the distribution, and it sits nowhere
near the threshold: the median real fact pair scores **0.164**, and 99.9% of
them stay under **0.683**. For the nomination cap's projection to bite, the whole distribution
would have to move — not its tail.

What that projects to at the sizes the nomination cap names, at ~580 bytes per surviving pair:

| facts | surviving pairs | pair memory |
|---|---|---|
| 2,000 | ~210 | ~0.1 MB |
| 5,000 | ~1,300 | ~0.8 MB |
| 10,000 | ~5,200 | **~3 MB** |

against the ~14 GB the borrowed rate predicted at 10,000 — **four orders of
magnitude**, and the difference between an urgent fix and cheap insurance.

**Three things this does not establish**, because the temptation is to read it
as an all-clear:

- **A near-duplicate corpus was untested and was the nomination cap's actual
  worst case.** The same news story from fifty outlets is claim-duplicate; dev
  notes are subject-similar, which is a much weaker thing. One can now be built
  rather than waited for — see *what a near-duplicate corpus actually costs*
  below, where a thousandfold rise in surviving pairs moves `reflect` by 5%.
- **The rate's behaviour with size is unmeasured.** Subsets at n = 50/100/200
  produced 0, 0 and 1 survivors — too few to fit a trend. If mutual similarity
  rises as a graph fills in one domain, the projection above is a floor.
- **Nothing here caps anything.** The bound the nomination cap proposes is still absent; the
  measurement changes its priority, not its correctness.

---

## The corpus became an input (2026-08-29)

Everything above was taken over one corpus: sentences of words drawn at random
from a 17-word vocabulary. That corpus is wrong in the one dimension most of
these figures scale with, and the section above measures how wrong. This section
is what replaced it, and what the replacement then measured.

### Widening the vocabulary does not work, and was tried first

The obvious repair is a bigger word bag. It does not reach real prose — it steps
straight over it. 400 sentences, the real model, the fact threshold:

| corpus | survivors / 79,800 | rate | median pair |
|---|---|---|---|
| 17-word bag, 8 words per sentence | 903 | 1.13% | 0.495 |
| 17-word bag, 12 words per sentence | 2,761 | 3.46% | 0.603 |
| 200-word bag | 0 | **0.0%** | 0.316 |
| 2,000-word bag | 0 | 0.0% | 0.306 |
| 20,000-word bag | 0 | 0.0% | 0.303 |
| this repository's own `dev-docs` prose | 1 / 46,056 | 0.0022% | 0.130 |

There is nothing between 1.13% and zero, and real prose sits at ~0.01%. **What
survives a pair scorer is shared phrasing, and a random generator never restates
anything** — the 17-word bag survives because every sentence is a near-copy of
every other, and a wide bag survives at nothing because no sentence is a copy of
any. Real prose survives for a third reason: mostly unrelated claims, plus a few
genuine restatements. A vocabulary dial cannot produce that, whatever its size.

The same defect appears one level up. A first attempt at a diverse corpus drew
from six domains of subject/verb/qualifier phrases and still survived at 0.48%,
because two sentences sharing a subject and a verb differ only by a qualifier.
Any generator with a small combinatorial space produces shared phrasing.

### The diverse corpus, and planting

`--corpus diverse` frames sentences over slots wide enough that two draws rarely
share phrasing, and `--duplicate-groups` / `--duplicate-size` plant restatement
clusters — one claim as several sources would put it. 1,200 facts, real
embeddings:

| corpus | surviving pairs / 719,400 | rate | planted |
|---|---|---|---|
| templated | 8,413 | 1.17% | — |
| diverse | 9 | **0.0013%** | 0 |
| diverse, 60 clusters of 10 | 2,712 | 0.377% | 2,700 |

Real facts in the `memory` graph sit at 0.0105%, so the diverse base is now on
the right side of real prose rather than two orders wrong.

**The planting is exact, and that is its point.** 2,700 planted → 2,712
survivors; 80 planted → 81; one clique of 50 (1,225 planted) → 1,226. The
generator contributes a residue in the single digits and everything else is an
input. A corpus whose survivor count is chosen before the run can be varied
deliberately; one that emerges from a vocabulary size can only be discovered
afterwards, which is how the 49% got borrowed in the first place.

`--corpus diverse` means nothing without `--real-embeddings`, and says so on
stderr when it does not get them: the mock hashes text rather than reading it,
so a restatement is simply a different string.

### What a near-duplicate corpus actually costs

The section above named this as untested and as the nomination cap's actual
worst case — *the same news story from fifty outlets*. Planting is what makes it
testable. 1,200 facts, in-memory, real embeddings, median of three seeds:

| corpus | surviving pairs | `reflect` |
|---|---|---|
| diverse | 3–11 | 333 ms |
| diverse, 2,700 planted | 2,706–2,796 | 349 ms |

**A thousandfold more surviving pairs costs 5%.** The extra lands in
`contradiction_detection` (57 → 68 ms); `boundary_proposals` and
`inference_merge_nomination` get *cheaper*, because planting replaces facts
rather than adding them and a planted corpus therefore holds fewer distinct
claims at the same node count. The alarm does not fire on the corpus it was
raised about, and the nomination cap is why: 8,413 survivors are truncated to
`max_nominations` before anything downstream sees them.

**This comparison was measured wrongly first.** Planting consumes randomness, so
on one shared stream every topic, inference and document body after the first
cluster differed too, and the two runs were two corpora rather than one corpus
with and without duplicates. Facts now draw from their own stream, guarded by a
test.

### Real embeddings end to end, and embedding on its own

2,400 nodes, in-memory:

| provider | ingest | vs mock |
|---|---|---|
| `mock-384` | 0.52 s | — |
| `all-MiniLM-L6-v2` | 4.06 s | **7.8×** |

So every ingest figure above is a floor by roughly a factor of eight, and every
`reflect` and `search` figure is unaffected — the model is paid at write time.

Embedding on its own, which is what says whether a slow ingest is a slow model
or a slow graph — texts per second, 12-word sentences:

| provider | batch 1 | batch 32 | batch 256 |
|---|---|---|---|
| `mock-384` | 37,400 | 38,500 | 38,200 |
| `all-MiniLM-L6-v2` | **174** | 2,300–5,800 | 2,300–5,800 |

**Batching is worth 14× to 34×, and it is all won by 32.** Batch 32 and batch
256 are not separable at this noise level; the cliff is entirely between one
text and a full batch. The mock is flat, as a hash should be, which is what
makes it the right baseline: the gap between the two rows is the model, and
nothing else.

### Where `reflect`'s time goes, and what a networked backend changes

`--reflect-phases` runs a second, watched `reflect` and reports each phase's
share. 2,400 nodes, templated, mock embeddings:

| phase | in-memory | SurrealDB |
|---|---|---|
| topic_consolidation | 7.7% | 11.8% |
| split_detection | 14.8% | 15.6% |
| contradiction_detection | 19.4% | **30.7%** |
| soundness_check | 8.3% | 6.6% |
| inference_merge_nomination | 4.9% | 4.0% |
| boundary_proposals | 7.8% | 3.8% |
| pending_review | 9.6% | 6.5% |
| archival_nomination | 14.8% | 16.7% |
| relation_consolidation | 11.4% | 4.1% |
| **total** | 308 ms | 1,466 ms |

Whole-operation ratios at this size: ingest 6.0×, `reflect` 4.8×, and reading
the fact vectors 12.8× (26 ms → 334 ms).

**The soundness share transfers, against the expectation recorded for it.** The
prediction was that its three batched reads become three round-trips and the
~10% share would not survive the move; measured, it goes 8.3% → 6.6% — it falls,
because everything around it pays round-trips too. What actually grows over a
network is `contradiction_detection`, and what shrinks is every CPU-bound phase.
The number to take from a phase table is which phases *move*, not what any one
of them costs.

### Reading a phase share: what a single watched run can charge to the wrong phase

A single run charged **58 ms** to `soundness_check` on a near-duplicate corpus,
against 25 ms on the same corpus undated — reproducible across three seeds, and
it looked exactly like a phase reacting to duplicates. Timed on its own,
`find_unsound_inferences` costs **22.8 ms on the plain corpus and 23.0 ms on the
duplicate one**: no corpus sensitivity at all. Disabling the collector takes
~18% off `reflect`'s total, and where that lands depends on what the process has
already allocated — the bench runs `reflect` twice, and the artefact appears only
in the second.

**A phase share is evidence about the shape of a run, not about a function.**
Where one is surprising, time the function on its own before believing it. That
is what separated this reading from the real one below.

### A corpus that carries intervals, and what the soundness check really costs

`--dated-share` gives documents a period and their facts the same one, on the
`sourced_from` edge where a period lives. Every `reflect` figure recorded before
this existed was over an undated corpus, where the check returns at its first
test: with nothing dated there is nothing to compare, and its whole measured
cost was the reads that discover that.

`find_unsound_inferences` on its own, 1,200 facts and 600 inferences:

| corpus | soundness |
|---|---|
| undated | 22.5 ms |
| dated — 1,200 dated premises, two per inference | **46.4 ms** |

**The early-out is worth half the phase**, which is what it was written for: an
undated graph fetches no premise nodes at all, and most of every graph is
undated and always will be.

The quadratic part is the one the note called *small in every case anyone has
described*. Holding the dated fact count at 1,200 and varying only how many of
them one inference rests on:

| dated premises per inference | comparisons | overhead over undated |
|---|---|---|
| 2 | 600 | 24.7 ms |
| 5 | 2,400 | 25.8 ms |
| 10 | 5,400 | 26.9 ms |
| 20 | 11,400 | 32.2 ms |
| 40 | 23,400 | 42.8 ms |

**39× the comparisons buys 1.7× the overhead.** A premise pair costs ~0.8 µs
against a ~20 µs per-premise fetch, so the fetch dominates until an inference
carries a couple of hundred dated premises. "Small in every case described" is
now measured rather than assumed, and the constant is the thing to watch, not
the exponent.

---

## Before optimizing anything here

**Profile first. Every performance fix in this project so far has overturned the
cause its issue predicted** — six times running, and in each case a profile
redirected the work to something the issue had not mentioned. Batching first named
the contradiction phase's edge queries as what held `reflect` at the timeout;
they were 14% of its storage calls, and removing all of them left the crossing
where it was. Step 4 then named batched node and embedding reads; those turned
out to be the *smallest* of the three causes, behind a query that never used the
right index. The recipe: seed via `bench._seed`, wrap one `await reflect(...)` in
`cProfile`, sort by cumulative time.

**The pair-loop fix is the one exception, and worth knowing why.** It was predicted correctly
and it under-promised: the issue expected ~4× by analogy with the profiling pass and measured
7.3–16.5× in-memory. The difference is that the profiling pass removed a quadratic from a
`reflect` that had other quadratics left, while the pair-loop fix removed the last one — so
the ratio kept growing with graph size instead of settling at a constant. A
profile is what made the prediction reliable: The pair-loop fix was written *from* the
measurement that closed batching, not from a guess about where time goes.

On a networked backend, **count round-trips before timing anything** — wrap
`SurrealDBStorage._query`, or the individual storage methods, in a counter and
attribute each call to its caller. That is what identifies which N+1 site
matters, and it is cheap enough to run at 400 nodes. Attribute to the *reflect
phase* as well by subscribing to the pipeline event bus: the phase boundaries
are exact because `reflect` is sequential, and it is what showed that the
enrichment material gather is billed to `split_detection`, whichever phase asks
for it first.

**Then check the query plan, not just the call count.** A call count says which
site is hot; `EXPLAIN` says whether each call is O(1) or O(table). The two
biggest wins in step 4 were both plan problems that no amount of round-trip
counting would have found — one call in the right place can still scan
everything. `EXPLAIN` output is a nested `children` tree; walk it for
`IndexScan` and the `index` attribute, and treat *no* index attribute as a full
scan.

Three implementation notes that outlived the measurements that produced them:

- **A single `LET $active = (…); SELECT …` call does not work through the
  SurrealDB driver.** `db.query` returns the *first* statement's result, so the
  select's rows are discarded and `LET`'s `None` comes back in their place —
  silently. `search`'s exact fallback is therefore two calls. Verified with
  `query_raw`, which does return every statement's result.
- **`vector_search`'s over-fetch is not merely an optimisation.** Ranking `k`
  rows and filtering afterwards returns fewer than `k` results on any graph with
  history at the top of the ranking — retrieval would quietly go blind on the
  oldest graphs. That is why the escalation and the exact fallback both exist,
  and why the tests pin *which path* answered rather than only what it returned.
- **In-memory endpoint indexes cost ~102 bytes per edge** (3.2 MiB at 32,500
  edges, measured with `sys.getsizeof` over both index dicts and their sets).
  Roughly what the `edges` dict's own table and keys cost, and small next to the
  `NodeEdge` objects being indexed. The embedding item index added for step 4 is
  the same trade on a smaller table.
- **A second predicate can cost you the index.** Adding `AND model_id = $m` to
  an `item_id` lookup made the planner take the *other* index, the unselective
  one, and filter afterwards. A composite `(item_id, model_id)` index did not
  help — measured, the planner still preferred the unselective one, and adding
  it made the plan a bare scan. `WITH INDEX idx_emb_item` did work (22× at
  3,000 rows), but so did simply dropping the predicate from the query and
  filtering in Python, which is what shipped: same speed, no version-specific
  syntax, and it degrades to correct rather than to a parse error. Do not drop
  `idx_emb_model` itself — `_ranked_items` narrows by model and needs it.
- **`IN $ids` does not use an index here at all.** Verified with `EXPLAIN` on
  `src_id IN $ids` against `idx_edge_src`, with and without a `WITH INDEX` hint:
  both plan a full scan, and the list is then tested per row, so a batched fetch
  costs O(rows × ids). Two consequences. Chunking an `IN` buys almost nothing —
  3,000 nodes over 9,000 edges took 692 ms at 200 ids per chunk against 511 ms
  as a single query — so a chunk size is a memory bound, not a speed knob. And
  past a crossover it is cheaper to read the candidate rows and match them in
  Python: measured at **100–200 ids for edges** (stable across 400/1,200/3,000
  nodes, because both sides grow linearly in table size), **past 400 for nodes**
  and **past 1,000 for embeddings** — the heavier the row, the longer `IN` stays
  worth it, since the alternative reads rows nobody asked for.

### What defining the full-text index costs, once

`DEFINE INDEX ... FULLTEXT` backfills every existing row, and `_setup_schema`
runs inside `connect()`, which has no progress reporting. So the first connect
after lexical search ships is slower than every connect before it, exactly once,
somewhere the user cannot see it. `LEXICAL_SEARCH.md` §5 called for this to be
measured before the index shipped rather than after.

Measured against SurrealDB 3.0.5 over ws://localhost, indexes dropped and
redefined three times per size, median reported. Each node carries a ~14-word
sentence and each segment a ~40-word one; both corpora are indexed, so the
document count is nodes **plus** segments.

| Nodes | Segments | Documents indexed | First connect | Steady connect |
|---|---|---|---|---|
| 1,000 | 1,000 | 2,000 | **1.0 s** | 31 ms |
| 3,000 | 3,000 | 6,000 | **3.8 s** | 30 ms |
| 10,000 | 10,000 | 20,000 | **19 s** (13–24 s) | 59 ms |

Roughly 0.5–1 ms per indexed document, and the spread at 10,000 is wider than
the gap between the first two sizes — so treat the shape as "seconds, growing
with the graph" rather than as a clean linear law.

Two things follow. `IF NOT EXISTS` means this happens once per graph, and every
subsequent connect is back to ~30 ms, so it is a one-off and not a tax. But a
graph in the tens of thousands makes `connect()` block for tens of seconds with
nothing on screen, which will read as a hang. If that becomes a complaint, the
fix is progress reporting or an out-of-band index build — not skipping the
index, which would leave `text_search` silently returning nothing.

---

## Not yet measured

- **A remote (non-loopback) SurrealDB.** Every network number here is localhost.
  The only remaining gap that is a deployment rather than a flag, and the one
  the loopback figures are least able to stand in for: a round-trip over a real
  link is not a round-trip over a socket, and `contradiction_detection`'s share
  is what would move.
- **A claim-duplicate corpus that was *ingested* rather than planted.** Planting
  makes the shape measurable, and it remains synthetic — the clusters are as
  similar as the substitution table makes them, which is a choice made here and
  observed nowhere. What real duplicate reporting scores at is still unknown.
- **Whether the surviving-pair rate moves with graph size.** Still open, and
  narrowed rather than answered: the diverse corpus's accidental survivors grow
  sub-linearly in pairs (2 → 9 as pairs went 79,800 → 719,400), but a generator
  with no topical fill-in cannot speak to a real graph filling in one domain.
- **`query_changes` after counterpart ids (2026-08-17).** The window predicate now adds two
  array-filter scans per row (lifecycle episodes) on top of the existing full
  scan, on SurrealDB. Correct, unindexed, unmeasured — flagged at resolution
  time; probably irrelevant at current graph sizes. Per house policy, act on a
  profile, not on this note.

---

## Reproduction

```bash
make bench BENCH_N=1000,3000                      # in-memory

docker run -d --rm --name bench-surreal -p 8001:8000 \
  surrealdb/surrealdb:latest start --user root --pass root memory
EPIMEMER_BENCH_URL=ws://localhost:8001/rpc make bench BENCH_N=1000,2000
docker rm -f bench-surreal
```

Against a throwaway container the namespace is irrelevant, but `bench.py`
creates and drops databases and defaults to the `epimemer_bench` namespace
rather than `epimemer` — pointing a run at a server that holds real graphs
should not put scratch ones beside them. `--namespace` overrides it.

`--skip-reflect` drops the slowest step when only `search` and `list_sources`
are of interest. `BENCH_N=10000` is about two minutes, dominated by `reflect`.

### The corpus dials

Every one defaults to what the historical figures were taken at, so a plain run
stays comparable with everything above.

```bash
# a corpus that survives the pair scorer like prose rather than like a bag of
# 17 words, with a known population of near-duplicates planted in it
uv run python scripts/bench.py --n 2400 --corpus diverse --real-embeddings     --duplicate-groups 60 --duplicate-size 10

# where reflect's time goes, on whichever backend is configured
uv run python scripts/bench.py --n 2400 --reflect-phases

# the soundness check with something to compare, and its quadratic part
uv run python scripts/bench.py --n 2400 --dated-share 1.0
uv run python scripts/bench.py --n 2400 --dated-share 1.0 --facts-per-segment 20
```

**`diverse` is the default from 2026-08-29**, and every figure above it in this
file was taken over `templated` — pass `--corpus templated` to reproduce one.
The default held at `templated` for a day, on the grounds that the recorded
figures should stay comparable; that is the wrong side of the trade. A benchmark
should default to measuring the thing it exists to measure, and comparability
belongs in a labelled row rather than in a default nobody re-reads. Every emitted
record now names its corpus and its provider, which is what makes that true.

**The flip changes nothing under the mock, and that bounds what it is for.** The
corpus reaches a score only through the provider, and the mock hashes text rather
than reading it, so its vectors sit in a band the corpus cannot move. Measured at
1,200 facts, 2026-08-29:

| corpus | provider | survivors / 719,400 | rate |
|---|---|---|---|
| templated | `mock-384` | 412 | 0.0573% |
| diverse | `mock-384` | 340 | 0.0473% |
| templated | `all-MiniLM-L6-v2` | 8,413 | **1.17%** |
| diverse | `all-MiniLM-L6-v2` | 9 | **0.0013%** |

The same number twice under the mock, and both a fact about the hash. So the
corpus choice is inert without `--real-embeddings`, and the warning fires on
*planting* without them rather than on the corpus: a note on every default run is
one nobody reads, while a planted run that cannot plant is a caller asking for
something specific that will not happen. `--reflect-phases` costs a second `reflect`, and its output
is evidence about a run rather than about a function — see the caveat above
before attributing a surprising share to the phase it is charged to.

Every run now emits a `corpus` record: the survival rate at the fact threshold,
the planted pair count, the dated fact count, and what the soundness check
finds. **A cost figure is only comparable with a later one if the corpus it was
taken over is recorded beside it**, and that is what went wrong with the
17-word corpus for as long as it did.

Guarded by `tests/test_bench_smoke.py`.

To measure a change against its own baseline, stash the changed file rather than
checking out an older commit — the test suite may reference symbols the benchmark
does not, so `git stash push <file>` gives a clean baseline with everything else
identical.

### The real-corpus figures

```bash
uv run python scripts/corpus_measure.py \
  --database memory,petritype-server --synthetic-control 400
```

Reads whatever graphs are named, **without opening a storage backend** —
`connect()` defines tables and runs the FTS backfill, and these are the real
namespaces. It reads the stored vectors rather than re-embedding, so the pair
scores are the ones `reflect` sees, and it reads both thresholds out of
`detect_contradictions` and `find_similar_topic_pairs` rather than restating
them. `--synthetic-control` scores the same number of `bench.py`-generated
sentences through the real model, which is what located the 49% discrepancy.

**The numbers above are specific to these two graphs and do not travel.** Anyone
reproducing this on a different corpus should expect different rates — that is
the point of the measurement, and the reason the tables name the corpus and its
size in every row. Guarded by `tests/test_corpus_measure_smoke.py`.

### Do supplied priors carry a reason? (the confidence prior's trigger, 2026-08-21)

The same read answers a question the confidence prior left open and nothing measured: whether tool
guidance actually produces a `confidence_basis`, given it is asked for rather
than enforced.

| population | `memory` | `petritype-server` | carries a basis |
|---|---|---|---|
| rated non-default (161×0.9, 2×0.7) | 163 | 0 | **163 — 100%** |
| unrated (field absent) | 125 | 0 | n/a, owes none |
| legacy literal `0.5` (pre-2026-08-19) | 200 | 136 | n/a, owes none |

Emitted as `measurement: priors`, so `--skip-survival` is enough:

```bash
uv run python scripts/corpus_measure.py \
  --database memory,petritype-server --skip-survival
```

**Guidance is producing them, so the enforcement fallback stays unbuilt.** The
second reading is the one a bare rate would hide: **no post-the confidence prior node sits at a
rated `0.5`** — they are stored absent instead, which is the ladder's own
instruction and what makes absence informative rather than ambiguous.

**One measurement trap, worth the line because it produced a confident wrong
answer first.** `confidence_basis` is stored in `node.metadata`, not beside the
number it explains in `value` — deliberately, since the basis is prose about one
judgment while `ValueSignal` holds the numbers every ranker reads. Querying
`value.confidence_basis` returns a clean 0% that looks like a finding. **Asking
the store the wrong question is not a null result.**
