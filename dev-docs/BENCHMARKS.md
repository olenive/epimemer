# Benchmarks

Measurements from `scripts/bench.py` (`make bench`) and
`scripts/corpus_measure.py`. This file describes where the system stands now.
Superseded runs are deleted rather than kept, and `git log` holds them if a
comparison is ever wanted.

**When a change moves one of these numbers, measure before and after on the
same machine in the same session, then replace the numbers here with the new
ones.** Machine state matters more than it looks: a run taken against a
previously recorded baseline on a busier box produced a 40% discrepancy on a
metric the change could not touch. A same-session pair with unchanged controls
is trustworthy; a cross-run comparison is not.

Dates on measurements are deliberate. A measurement is evidence about the code
on that date, and the date is what says how old the evidence is.

## How to read these

- **Embeddings are mocked** (`mock-384`, genuinely 384-wide) unless a row says
  otherwise. Model inference is a constant per text that would dominate and
  hide the graph costs this exists to expose. Every ingest number here is
  therefore a floor: ingest is **7.8× slower** under `all-MiniLM-L6-v2`, and
  nothing after write time moves.
- **Survival rates depend on the corpus and the text length, and every row
  says which.** The mock builds vectors from hash bytes, so under it every
  pair sits near 0.75 cosine and almost nothing clears the 0.80 bar at 384
  dimensions (0.05%). Under the real model, pair similarity is dominated by
  text length: over a fixed 17-word vocabulary it climbs from 0.62% of pairs
  clearing 0.80 at 4 words, through 1.11% at 8 (fact length, what `reflect`
  scores), to 74.9% at a paragraph. A survival rate is meaningless without
  the text length it was measured at.
- **Node counts are exact**: 4 nodes per segment, 5 segments per document.
- **All network numbers are loopback.** A remote SurrealDB is worse by the RTT
  difference times the round-trip count.
- **Absolute SurrealDB figures are soft.** They move with whatever else the
  machine is doing; the shapes and the ratios are the durable part.

**Machine:** Apple M4 Max, macOS 26.4.1, arm64, Python 3.14.0.
**SurrealDB:** `surrealdb/surrealdb:latest start … memory`, loopback
container.

---

## Where things stand

Measured at 2,000, 4,000 and 8,000 nodes, templated corpus, mock embeddings:

| Nodes | Backend | ingest (docs/min) | search p50 | `list_sources` | `reflect` |
|---|---|---|---|---|---|
| 2,000 | memory | 18,487 | 45.1 ms | 31 ms | 197 ms |
| 4,000 | memory | 18,805 | 88.5 ms | 64 ms | 365 ms |
| 8,000 | memory | 18,171 | 177.0 ms | 149 ms | 780 ms |
| 2,000 | SurrealDB | 3,878 | 230.0 ms | 258 ms | 819 ms |
| 4,000 | SurrealDB | 3,750 | 355.3 ms | 514 ms | 2,051 ms |
| 8,000 | SurrealDB | 3,730 | 581.8 ms | 1,034 ms | 5,696 ms |

### The 30 s crossings

`EPIMEMER_TOOL_TIMEOUT_SECONDS` defaults to 30 s, so "crossing" means *the
tool call fails*, not *feels slow*. Fitted over three sizes, which is enough
to rank the operations and not enough to trust a third digit.

| Operation | in-memory | SurrealDB (loopback) | exponent (mem / surreal) |
|---|---|---|---|
| `search` | ~1.5M | ~2.9M | 0.99 / 0.67 |
| `list_sources` | ~870,000 | ~230,000 | 1.13 / 1.00 |
| **`reflect`** | **~320,000** | **~26,000** | 0.99 / 1.40 |
| ingest | not reachable | not reachable | flat |

Nothing here fails at a size anyone is running. `reflect` is the first to go
and SurrealDB is the limiting backend.

**Fit over at least three sizes spanning 4×, or report the measurements
without a crossing.** A two-point fit reads fixed setup cost as curvature,
inflates the exponent and understates the crossing badly: two points once put
`reflect` at an exponent of 1.8 and a crossing near 7,000 for code that three
points measured at 0.99.

**`reflect` is quadratic in principle and does not look it yet.** Both pair
phases compare every pair, so the work really is O(N²); at these sizes it
happens inside two matrix products whose constant is small enough that the
linear costs (reading the vectors, walking the edges) still dominate. Expect
the exponent to climb toward 2 well beyond 8,000 nodes.

**The crossings are time only.** `reflect` allocates about 580 bytes per
*surviving* pair. The synthetic corpus produces almost none, so these figures
cannot show memory failing before the timeout. Real prose survives at about
0.01% (below), which projects to about 3 MB at 10,000 facts, and the
nomination cap truncates the lists downstream anyway.

---

## What each operation's cost is made of

**Ingest is not a problem.** Flat across every size measured on both
backends.

**The exact-content lookup on the write path needs its index named.**
`get_node_by_content` is resolved by SurrealDB's planner through
`idx_{table}_status`, an index matching every active row, with `content`
applied afterwards as a predicate, and that is linear in table size. Defining
a `content` index does nothing on its own, and a composite `(content, status)`
index plans the same way. `WITH INDEX idx_{table}_content` moves `content`
into the access path:

| topics | planner's own choice | with a `content` index defined | with the index named |
|---|---|---|---|
| 400 | 1.05 ms | 1.08 ms | |
| 1,200 | 2.63 ms | 2.04 ms | |
| 3,000 | 4.02 ms | 4.30 ms | **0.53 ms** |

The index's write cost is +4.6% on a 3,000-node seed, inside run-to-run
spread, cheap enough that ingest affords one such lookup per fact, which is
what the verbatim-recurrence check needs.

**`search` is the cheapest of the three, on both backends.** In-memory it is
linear. On SurrealDB it is close to flat: ranking happens before the status
filter, because SurrealDB re-runs such a subquery per embedding row and that
cost two orders of magnitude when it sat inside the ranking query. Per-result
enrichment is batched, one query per annotation kind for the whole result set
rather than per hit.

**`list_sources` and `list_relations` ask once for the whole active set**
(`get_edges_for`) rather than fetching each node's edges in turn. What is
left in `list_sources` is one `get_document` plus `get_node` per distinct
source, bounded by sources rather than by graph size.

**`reflect` is the limiting operation, and it is bound by bytes.**

- **In-memory it is arithmetic-bound.** Both pair phases compare every pair,
  which is genuine work rather than redundancy, so both were made fast rather
  than made smaller: `similar_pairs` (`pipelines/reflection/pair_scoring.py`)
  stacks the vectors and takes a matrix product per 512-row block instead of
  one Python call per pair. Storage batching buys nothing on this backend
  (1.03 to 1.10×, noise), as expected of one with no round-trips.
- **On SurrealDB it was round-trip bound, and is not any more.** Batching
  every read took round-trips at 1,200 nodes from 3,086 to 56. Three separate
  causes, in order of size, and the batching everyone predicted was the
  smallest:

  1. **A per-call full scan of the embedding table.** `get_embeddings_for_item`
     asked `WHERE item_id = $i AND model_id = $m`, and the planner chose
     `idx_emb_model`, which matches every row for the graph's one model, then
     filtered `item_id` afterwards: linear in table size, inside a per-node
     loop, 64% of `reflect` at 1,200 nodes. Asking on `item_id` alone uses
     `idx_emb_item` and is flat at about 0.6 ms. That one predicate was 2.3×
     on its own.
  2. **`IN` does not use an index**, so `get_edges_for` was scanning the edge
     table once per chunk and paying O(rows × ids) for the predicate. Reading
     the candidate rows and matching them in Python is 14× faster past about
     100 ids.
  3. **The batched reads themselves** (`get_nodes`, `get_embeddings_for_items`).
     Real, and the smallest of the three.

  **What binds it now is payload.** At 4,000 nodes `reflect` is 2,032 ms, of
  which 1,840 ms is inside 87 queries, 21 ms each, because those queries move
  whole tables: 894 ms for the two `get_embeddings_for_items` calls that fetch
  every fact and topic vector, 430 ms of edges, 349 ms of nodes. That is close
  to irreducible with this design: pairwise comparison needs the vectors, and
  they are 384 floats each. Reducing it means moving the comparison to the
  server or keeping vectors across calls, both larger changes than anything so
  far, and neither worth making while the crossing sits at about 26,000 nodes.

### What the soundness check and boundary proposals add to `reflect`

In-memory, mock embeddings, median of three, each measured against the same
graph with that one phase stubbed out:

| nodes | soundness check | boundary proposals |
|---|---|---|
| 1,000 | **10.1 ms** (of 95.7) | **9.7 ms** (of 101.4) |
| 3,000 | **27.7 ms** (of 295.4) | **25.6 ms** (of 324.2) |

Both linear, and about 10% of a call each at both sizes. Nearly all of it is
batched reads neither can avoid, since the graph measured carries no
intervals at all, so the soundness check finds no dated premises and the
boundary phase finds no dated successions. That is the shape to expect: the
feature is sparse by design, and the common case is a phase paying its floor
and reporting nothing.

**Ordering the reads is worth a third of the soundness check.** Fetching
every premise fact and then discarding the undated ones cost 41.0 ms at 3,000
nodes; reading validity first and fetching only the premises that can still
form a pair costs 27.7 ms. On an undated graph no nodes are fetched at all.
Narrow before you fetch, not after.

### `reflect` reads the node set 13 times

One `reflect` over a 1,000-node graph issues 13 `query_nodes` calls and
materialises 5,250 node copies, 5.25× the graph:

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

Rows 6, 8, 12 and 13 are the same query. None is a per-node shape; every one
is a single batched call, which is exactly why they go unnoticed: the
round-trip count stays flat while the payload multiplies.

| | in-memory | SurrealDB |
|---|---|---|
| share of `reflect` inside `query_nodes` | 39–43% | 17–23% |
| just the four untyped active scans | ~30% | 13–16% |
| recoverable by memoising identical calls | **18%** | **5%** |
| recoverable by one read per status, filtered in Python | **27%** | **8%** |

The share is lower on SurrealDB, the opposite of the usual shape here, because
there the embedding reads dominate. **Not built.** 27% of the fastest backend
is real but not what binds the crossing. If it is built: the memo is only safe
because `reflect` writes nothing (`TestReflectWritesNothing`); the cache must
be lazy rather than a prefetch, so the fetch stays attributed to the phase
that needs it first in the pipeline strip; and it should arrive as the storage
handle rather than as a parameter on every phase function. Of the 13 reads,
five ask for genuinely different statuses and no cache removes them.

### What retrieval reinforcement costs

`search` writes every returned node back with a fresh `retrieved_at`, k extra
writes per call, on by default (`EPIMEMER_RECORD_RETRIEVAL=true`). Measured
with `--skip-reflect`: +5% in-memory, +8 to 12% on SurrealDB (+14 to 17 ms).
Flat in graph size, since k does not grow with the graph. It changes no
crossing, and it is the only per-node write left in the read path.

### What corroboration costs, and why it is opt-in

Median ms per call, over the node set a real `search(k=10, graph_hops=1)`
returns, localhost SurrealDB 3.0.5 and the in-memory store, mock embeddings:

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

The `search` column is the plain call, without corroboration.

**Two costs, and they are separable.** With no similarity edges at all it is
about 3× `review_labels_for` on SurrealDB, which is round-trips: twelve
batched queries against that function's four. With edges it is the fan-out:
the walk leaves the result set for each node's similarity neighbourhood, and
nothing bounds how large that is. At degree 10 it is nearly the whole call.

**Default-off rests on the round-trip cost alone.** The without-edges column
is still several times every other annotation, so
`include_corroboration=False` and the cost is stated in the tool description.
The similarity edges in this table are synthetic, assigned at a fixed degree
by `--similarity-degree`. Real `similarity` edges are written one judgment at
a time by `apply_reflection(similarities=[…], verdict="one_claim")`, so real
degree accumulates at the rate pairs are judged, far below 10 for a long
while. The companion `assessed` edge, written for both verdicts and so
accumulating faster, costs corroboration nothing because corroboration does
not read it; if this cost ever climbs, `assessed` in the neighbourhood walk
would be a bug, not a workload. Re-measure against real degrees when a graph
has a few dozen judged pairs.

```bash
uv run python scripts/bench.py --n 400,2000 --skip-reflect \
    --publishers 4 --similarity-degree 10 --url ws://localhost:8000/rpc
```

The lever, if it is ever wanted on the default path: the six typed
neighbourhood queries collapse into one untyped `get_edges_for` per
direction, trading round-trips for bytes. Indicated by the degree-0 column
and contraindicated by the degree-10 one, which is why it was left alone.

---

## What real text looks like

Two questions turned on what real text does rather than generated text, so
these were taken from graphs of real ingested content (`epimemer/memory`, 568
nodes and 80 segments at the time; `epimemer/petritype-server`, 136 nodes and
28 segments), read without opening a storage backend, since those namespaces
must not be written to. `scripts/corpus_measure.py`.

### Node text does not reach the embedding window, and segments are never embedded

`all-MiniLM-L6-v2` truncates at 256 word-pieces. Tokenised lengths over both
graphs:

| corpus | n | median | p95 | max | over 256 |
|---|---|---|---|---|---|
| fact | 350 | 30 | 56 | 81 | **0** |
| inference | 124 | 38 | 56 | 63 | **0** |
| topic | 150 | 20 | 38 | 69 | **0** |
| **segment** | **108** | **148** | **305** | **496** | **12 (11.1%)** |

Nodes have 3× headroom at their worst, and no amount of graph growth changes
that: a fact is one sentence by construction. Segments cross the window
routinely, and it does not matter: no code path constructs an
`EmbeddingRecord` for a segment, every stored embedding points at a node,
`vector_search` resolves hits through `get_node` so a segment could not be
returned anyway, and segmentation's own sentence embeddings are transient.
Segments are searched by BM25 alone, which indexes the whole field, and that
is precisely why they answer *where did I read that?* well.

The rule this taught: a measured quantity is not yet a measured consequence.
Before a distribution becomes a cost, something has to be shown to pay it.
The precondition lives on `EmbeddingRecord.item_id`, where anyone adding
segment embedding will meet it.

### `reflect`'s surviving-pair rate on real prose is about 0.01%

Same model, the real 0.80 threshold, and the stored vectors written at ingest,
so this is the distribution `reflect` sees:

| corpus | items | pairs | survivors | rate | median pair similarity | p99.9 |
|---|---|---|---|---|---|---|
| bench fact text (control) | 400 | 79,800 | 887 | 1.11% | 0.500 | 0.883 |
| real facts, `memory` | 277 | 38,226 | 4 | **0.0105%** | **0.164** | 0.683 |
| real facts, `petritype-server` | 73 | 2,628 | 0 | **0.0%** | 0.160 | 0.720 |
| real topics, `memory` | 112 | 6,216 | 1 | 0.0161% | 0.153 | 0.707 |

Read the distribution, not the rate. Four survivors is too few to trust as a
rate, but 38,226 pairs is plenty to locate the distribution, and it sits
nowhere near the threshold: the median real fact pair scores 0.164, and 99.9%
stay under 0.683. At about 580 bytes per surviving pair that projects to
about 210 pairs and 0.1 MB at 2,000 facts, 5,200 pairs and 3 MB at 10,000.

Three things this does not establish:

- **A claim-duplicate corpus is the worst case, and it is a different thing.**
  The same news story from fifty outlets is claim-duplicate; dev notes are
  subject-similar, which is much weaker. Planting (below) makes the shape
  measurable.
- **The rate's behaviour with size is unmeasured.** Subsets at n = 50, 100
  and 200 produced 0, 0 and 1 survivors, too few to fit a trend. If mutual
  similarity rises as a graph fills in one domain, the projection is a floor.
- **The nomination cap is a response bound, not a memory bound.** The pair
  lists are truncated to `max_nominations` for readability; nothing caps the
  pairs computed before that.

---

## The corpus is an input

`bench.py` generates its corpus, and the corpus decides every figure that
scales with surviving pairs. Every emitted record names its corpus and its
provider, because a cost figure is only comparable with a later one if the
corpus it was taken over is recorded beside it.

### Widening the vocabulary does not produce prose

The obvious way to make a random corpus more realistic is a bigger word bag.
It steps straight over real prose. 400 sentences, the real model, the fact
threshold:

| corpus | survivors / 79,800 | rate | median pair |
|---|---|---|---|
| 17-word bag, 8 words per sentence | 903 | 1.13% | 0.495 |
| 17-word bag, 12 words per sentence | 2,761 | 3.46% | 0.603 |
| 200-word bag | 0 | **0.0%** | 0.316 |
| 2,000-word bag | 0 | 0.0% | 0.306 |
| 20,000-word bag | 0 | 0.0% | 0.303 |
| this repository's own `dev-docs` prose | 1 / 46,056 | 0.0022% | 0.130 |

There is nothing between 1.13% and zero, and real prose sits at about 0.01%.
What survives a pair scorer is shared phrasing, and a random generator never
restates anything: a 17-word bag survives because every sentence is a
near-copy of every other, and a wide bag survives at nothing because no
sentence is a copy of any. Real prose survives for a third reason, mostly
unrelated claims plus a few genuine restatements, and a vocabulary dial cannot
produce that. The same defect appears one level up: a corpus drawn from six
domains of subject, verb and qualifier phrases still survived at 0.48%,
because two sentences sharing a subject and a verb differ only by a qualifier.

### The diverse corpus, and planting

`--corpus diverse` (the default) frames sentences over slots wide enough that
two draws rarely share phrasing, and `--duplicate-groups` /
`--duplicate-size` plant restatement clusters, one claim as several sources
would put it. 1,200 facts, real embeddings:

| corpus | surviving pairs / 719,400 | rate | planted |
|---|---|---|---|
| templated | 8,413 | 1.17% | |
| diverse | 9 | **0.0013%** | 0 |
| diverse, 60 clusters of 10 | 2,712 | 0.377% | 2,700 |

Real facts in the `memory` graph sit at 0.0105%, so the diverse base is on the
right side of real prose. The planting is exact: 2,700 planted gives 2,712
survivors, 80 gives 81, one clique of 50 (1,225 planted) gives 1,226. The
generator contributes a residue in the single digits and everything else is
an input, so a survivor count can be chosen before the run rather than
discovered afterwards.

`--corpus diverse` means nothing without `--real-embeddings`, and says so on
stderr when a planted run does not get them: the mock hashes text rather
than reading it, so a restatement is simply a different string. Measured at
1,200 facts, templated and diverse both survive at about 0.05% under the
mock, the same number twice and both a fact about the hash.

Facts draw from their own random stream, guarded by a test: planting consumes
randomness, and on one shared stream every topic, inference and document body
after the first cluster would differ too, making two corpora rather than one
corpus with and without duplicates.

### What a near-duplicate corpus costs

1,200 facts, in-memory, real embeddings, median of three seeds:

| corpus | surviving pairs | `reflect` |
|---|---|---|
| diverse | 3–11 | 333 ms |
| diverse, 2,700 planted | 2,706–2,796 | 349 ms |

**A thousandfold more surviving pairs costs 5%.** The extra lands in
`contradiction_detection` (57 to 68 ms); `boundary_proposals` and
`inference_merge_nomination` get cheaper, because planting replaces facts
rather than adding them and a planted corpus holds fewer distinct claims at
the same node count. The nomination cap is why the alarm does not fire:
survivors are truncated to `max_nominations` before anything downstream sees
them.

### Real embeddings end to end, and embedding on its own

2,400 nodes, in-memory:

| provider | ingest | vs mock |
|---|---|---|
| `mock-384` | 0.52 s | |
| `all-MiniLM-L6-v2` | 4.06 s | **7.8×** |

Every ingest figure above is a floor by roughly a factor of eight, and every
`reflect` and `search` figure is unaffected: the model is paid at write time.

Embedding on its own, texts per second, 12-word sentences:

| provider | batch 1 | batch 32 | batch 256 |
|---|---|---|---|
| `mock-384` | 37,400 | 38,500 | 38,200 |
| `all-MiniLM-L6-v2` | **174** | 2,300–5,800 | 2,300–5,800 |

Batching is worth 14× to 34×, and it is all won by 32. The mock is flat, as a
hash should be, which is what makes it the right baseline.

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
the fact vectors 12.8× (26 ms to 334 ms). What grows over a network is
`contradiction_detection`; what shrinks is every CPU-bound phase. The number
to take from a phase table is which phases move, not what any one of them
costs.

**A phase share is evidence about the shape of a run, not about a function.**
A single run once charged 58 ms to `soundness_check` on a near-duplicate
corpus against 25 ms undated, reproducible across three seeds; timed on its
own, `find_unsound_inferences` costs 22.8 ms on the plain corpus and 23.0 ms
on the duplicate one. The difference was the garbage collector, and where it
lands depends on what the process has already allocated. Where a share is
surprising, time the function on its own before believing it.

### A corpus that carries intervals, and what the soundness check really costs

`--dated-share` gives documents a period and their facts the same one, on the
`sourced_from` edge where a period lives. `find_unsound_inferences` on its
own, 1,200 facts and 600 inferences:

| corpus | soundness |
|---|---|
| undated | 22.5 ms |
| dated, 1,200 dated premises, two per inference | **46.4 ms** |

The early-out is worth half the phase: an undated graph fetches no premise
nodes at all, and most of every graph is undated and always will be.

The quadratic part, holding the dated fact count at 1,200 and varying how
many of them one inference rests on:

| dated premises per inference | comparisons | overhead over undated |
|---|---|---|
| 2 | 600 | 24.7 ms |
| 5 | 2,400 | 25.8 ms |
| 10 | 5,400 | 26.9 ms |
| 20 | 11,400 | 32.2 ms |
| 40 | 23,400 | 42.8 ms |

39× the comparisons buys 1.7× the overhead. A premise pair costs about 0.8 µs
against a 20 µs per-premise fetch, so the fetch dominates until an inference
carries a couple of hundred dated premises. The constant is the thing to
watch, not the exponent.

---

## What defining the full-text index costs, once

`DEFINE INDEX ... FULLTEXT` backfills every existing row, and `_setup_schema`
runs inside `connect()`, which has no progress reporting. Measured against
SurrealDB 3.0.5 over ws://localhost, indexes dropped and redefined three
times per size, median reported. Each node carries a 14-word sentence and each
segment a 40-word one; both corpora are indexed.

| Nodes | Segments | Documents indexed | First connect | Steady connect |
|---|---|---|---|---|
| 1,000 | 1,000 | 2,000 | **1.0 s** | 31 ms |
| 3,000 | 3,000 | 6,000 | **3.8 s** | 30 ms |
| 10,000 | 10,000 | 20,000 | **19 s** (13–24 s) | 59 ms |

Roughly 0.5 to 1 ms per indexed document, with a spread at 10,000 wider than
the gap between the first two sizes, so treat the shape as "seconds, growing
with the graph" rather than a clean linear law. `IF NOT EXISTS` means it
happens once per graph. A graph in the tens of thousands makes `connect()`
block for tens of seconds with nothing on screen; `ISSUES.md` carries the
trigger, and the fix is progress reporting or an out-of-band build, never
skipping the index, which would leave `text_search` silently returning
nothing.

---

## Before optimising anything here

**Profile first.** Every performance fix in this project so far has
overturned the cause its issue predicted, and in each case a profile
redirected the work to something the issue had not mentioned: the
contradiction phase's edge queries were 14% of `reflect`'s storage calls and
removing all of them left the crossing where it was; the batched node and
embedding reads turned out to be the smallest of three causes, behind a query
that never used the right index. The one prediction that held was made *from*
a profile. The recipe: seed via `bench._seed`, wrap one `await reflect(...)`
in `cProfile`, sort by cumulative time.

On a networked backend, **count round-trips before timing anything**: wrap
`SurrealDBStorage._query` in a counter and attribute each call to its caller.
It is cheap enough to run at 400 nodes. Attribute to the reflect phase as well
by subscribing to the pipeline event bus; the phase boundaries are exact
because `reflect` is sequential, and it is what showed that the enrichment
material gather is billed to `split_detection`, whichever phase asks for it
first.

**Then check the query plan, not just the call count.** A call count says
which site is hot; `EXPLAIN` says whether each call is O(1) or O(table). The
two biggest wins so far were both plan problems no round-trip count would have
found. `EXPLAIN` output is a nested `children` tree; walk it for `IndexScan`
and the `index` attribute, and treat no index attribute as a full scan.

Implementation notes that outlived the measurements that produced them:

- **A single `LET $active = (…); SELECT …` call does not work through the
  SurrealDB driver.** `db.query` returns the first statement's result, so the
  select's rows are discarded and `LET`'s `None` comes back in their place,
  silently. `search`'s exact fallback is therefore two calls. `query_raw`
  returns every statement's result.
- **`vector_search`'s over-fetch is not merely an optimisation.** Ranking `k`
  rows and filtering afterwards returns fewer than `k` results on any graph
  with history at the top of the ranking, so retrieval would quietly go blind
  on the oldest graphs. That is why the escalation and the exact fallback
  both exist, and why the tests pin which path answered rather than only what
  it returned.
- **In-memory endpoint indexes cost about 102 bytes per edge** (3.2 MiB at
  32,500 edges), roughly what the `edges` dict's own table and keys cost, and
  small next to the `NodeEdge` objects being indexed.
- **A second predicate can cost you the index.** Adding `AND model_id = $m`
  to an `item_id` lookup made the planner take the other, unselective index
  and filter afterwards. A composite `(item_id, model_id)` index did not help;
  the planner still preferred the unselective one, and adding it made the
  plan a bare scan. `WITH INDEX idx_emb_item` worked (22× at 3,000 rows), but
  so did dropping the predicate and filtering in Python, which is what
  shipped: same speed, no version-specific syntax, and it degrades to correct
  rather than to a parse error. Do not drop `idx_emb_model` itself:
  `_ranked_items` narrows by model and needs it.
- **`IN $ids` does not use an index here at all.** Verified with `EXPLAIN` on
  `src_id IN $ids` against `idx_edge_src`, with and without a `WITH INDEX`
  hint: both plan a full scan, and the list is then tested per row, so a
  batched fetch costs O(rows × ids). Chunking an `IN` buys almost nothing
  (3,000 nodes over 9,000 edges: 692 ms at 200 ids per chunk against 511 ms
  as one query), so a chunk size is a memory bound, not a speed knob. Past a
  crossover it is cheaper to read the candidate rows and match them in
  Python: 100 to 200 ids for edges (stable across sizes, since both sides
  grow linearly), past 400 for nodes, past 1,000 for embeddings. The heavier
  the row, the longer `IN` stays worth it, since the alternative reads rows
  nobody asked for.

---

## Not yet measured

- **A remote (non-loopback) SurrealDB.** Every network number here is
  localhost. A round-trip over a real link is not a round-trip over a socket,
  and `contradiction_detection`'s share is what would move.
- **A claim-duplicate corpus that was ingested rather than planted.** Planted
  clusters are as similar as the substitution table makes them, which is a
  choice made here and observed nowhere.
- **Whether the surviving-pair rate moves with graph size.** The diverse
  corpus's accidental survivors grow sub-linearly in pairs (2 to 9 as pairs
  went 79,800 to 719,400), but a generator with no topical fill-in cannot
  speak to a real graph filling in one domain.
- **`query_changes` with lifecycle episodes.** The window predicate adds two
  array-filter scans per row on top of the existing full scan, on SurrealDB.
  Correct, unindexed, unmeasured, and probably irrelevant at current sizes.
  Act on a profile, not on this note.

---

## Reproduction

```bash
make bench BENCH_N=1000,3000                      # in-memory

docker run -d --rm --name bench-surreal -p 8001:8000 \
  surrealdb/surrealdb:latest start --user root --pass root memory
EPIMEMER_BENCH_URL=ws://localhost:8001/rpc make bench BENCH_N=1000,2000
docker rm -f bench-surreal
```

`bench.py` creates and drops databases and defaults to the `epimemer_bench`
namespace rather than `epimemer`, so pointing a run at a server that holds
real graphs does not put scratch ones beside them. `--namespace` overrides
it. `--skip-reflect` drops the slowest step when only `search` and
`list_sources` are of interest. `BENCH_N=10000` is about two minutes,
dominated by `reflect`.

### The corpus dials

```bash
# a corpus that survives the pair scorer like prose, with a known population
# of near-duplicates planted in it
uv run python scripts/bench.py --n 2400 --corpus diverse --real-embeddings \
    --duplicate-groups 60 --duplicate-size 10

# where reflect's time goes, on whichever backend is configured
uv run python scripts/bench.py --n 2400 --reflect-phases

# the soundness check with something to compare, and its quadratic part
uv run python scripts/bench.py --n 2400 --dated-share 1.0
uv run python scripts/bench.py --n 2400 --dated-share 1.0 --facts-per-segment 20
```

`diverse` is the default corpus; the figures in *Where things stand* and the
phase table were taken over `templated`, and `--corpus templated` reproduces
them. A benchmark should default to measuring the thing it exists to measure,
and comparability belongs in a labelled row rather than in a default nobody
re-reads. Every run emits a `corpus` record: the survival rate at the fact
threshold, the planted pair count, the dated fact count, and what the
soundness check finds. Guarded by `tests/test_bench_smoke.py`.

To measure a change against its own baseline, stash the changed file rather
than checking out an older commit: the test suite may reference symbols the
benchmark does not, so `git stash push <file>` gives a clean baseline with
everything else identical.

### The real-corpus figures

```bash
uv run python scripts/corpus_measure.py \
  --database memory,petritype-server --synthetic-control 400
```

Reads whatever graphs are named without opening a storage backend, since
`connect()` defines tables and runs the FTS backfill and these are real
namespaces. It reads the stored vectors rather than re-embedding, so the pair
scores are the ones `reflect` sees, and it reads both thresholds out of
`detect_contradictions` and `find_similar_topic_pairs` rather than restating
them. `--synthetic-control` scores the same number of `bench.py` sentences
through the real model.

The numbers are specific to the graphs measured and do not travel. Anyone
reproducing this on a different corpus should expect different rates; that is
the point of the measurement, and the reason the tables name the corpus and
its size in every row. Guarded by `tests/test_corpus_measure_smoke.py`.

### Do supplied priors carry a reason?

Whether tool guidance actually produces a `confidence_basis`, given it is
asked for rather than enforced. Over both real graphs, every rated
non-default node carried one (163 of 163), and no node written since
confidence became optional sits at a rated `0.5`: they are stored absent
instead, which is the ladder's own instruction and what makes absence
informative. So the enforcement fallback stays unbuilt.

```bash
uv run python scripts/corpus_measure.py \
  --database memory,petritype-server --skip-survival
```

`confidence_basis` is stored in `node.metadata`, not beside the number in
`value`; querying `value.confidence_basis` returns a clean 0% that looks like
a finding. Asking the store the wrong question is not a null result.
