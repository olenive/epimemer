# Benchmarks

Measurements from `scripts/bench.py` (`make bench`). This file describes **where
the system stands now**, not how it got here — superseded runs are deleted rather
than kept, and `git log` holds them if a comparison is ever wanted.

It is what keeps ISSUES.md **#14** honest. #14 is the full-scan / N+1 entry, and
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
  the embedding time, real search by roughly one query embedding.
- **The synthetic corpus is unrealistically self-similar.** Documents are drawn
  from a 17-word vocabulary, so most fact pairs clear the 0.80 contradiction
  threshold. Measured: 19% of unrelated pairs under the mock, 49% under real
  `all-MiniLM-L6-v2` on similarly templated text. Anything scaling with
  *surviving candidate pairs* — the contradiction phase of `reflect` — is
  overstated here relative to a diverse corpus. Node- and edge-scaled costs are
  not affected.
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

---

## What each operation's cost is made of

**Ingest — not a problem.** Flat across every size measured on both backends. The
write path has never been the ceiling.

**The exact-content lookup, though, was linear in table size (#48, fixed
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
which is what the verbatim-recurrence check needs (#53 T2).

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
  Python call per pair. Facts went first (#39, 4.1–4.6×) and topics followed
  once storage stopped hiding them (#47, **7.3× at 1,000 nodes and 16.5× at
  2,000** in-memory — the ratio grows with size because what was removed was
  quadratic and what remains is not). Storage batching buys nothing on this
  backend, as expected of one with no round-trips: **1.03–1.10×** for #14 step 4,
  which is noise.
- **On SurrealDB it was round-trip bound, and is not any more.** Step 4 bought
  **6.19× at 1,000 nodes and 7.09× at 2,000** (6,464 → 1,044 ms; 24,946 →
  3,519 ms), measured same-session, and took round-trips at 1,200 nodes from
  3,086 to **56**. Vectorizing the topic phase on top of that (#47) bought a
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
     which is what #14 step 4 nominated. Worth 3,868 → 1,465 ms once the two
     above were fixed. Real, and the smallest of the three.

  The write side had already gone: value decay was one `UPSERT` per active node
  per pass, and removing `relevance` (#44) removed it, making `reflect` a pure
  read.

  **What binds it now is payload, not round-trips or arithmetic.** At 4,000
  nodes `reflect` is 2,032 ms, of which 1,840 ms is inside **87** queries — 21 ms
  each, because those queries move whole tables: 894 ms for the two
  `get_embeddings_for_items` calls that fetch every fact and topic vector, 430 ms
  of edges, 349 ms of nodes. That is close to irreducible with this design —
  pairwise comparison needs the vectors, and they are 384 floats each. Reducing
  it means moving the comparison to the server or keeping vectors across calls,
  both of which are larger changes than anything in #14 or #47, and neither is
  worth making while the crossing sits at ~26,000 nodes.

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

---

## Before optimizing anything here

**Profile first. Every performance fix in this project so far has overturned the
cause its issue predicted** — six times running, and in each case a profile
redirected the work to something the issue had not mentioned. #14 first named
the contradiction phase's edge queries as what held `reflect` at the timeout;
they were 14% of its storage calls, and removing all of them left the crossing
where it was. Step 4 then named batched node and embedding reads; those turned
out to be the *smallest* of the three causes, behind a query that never used the
right index. The recipe: seed via `bench._seed`, wrap one `await reflect(...)` in
`cProfile`, sort by cumulative time.

**#47 is the one exception, and worth knowing why.** It was predicted correctly
and it under-promised: the issue expected ~4× by analogy with #39 and measured
7.3–16.5× in-memory. The difference is that #39 removed a quadratic from a
`reflect` that had other quadratics left, while #47 removed the last one — so
the ratio kept growing with graph size instead of settling at a constant. A
profile is what made the prediction reliable: #47 was written *from* the
measurement that closed #14, not from a guess about where time goes.

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
- **A diverse corpus.** The 17-word synthetic vocabulary inflates anything
  scaling with surviving candidate pairs.
- **Real embeddings.** `--real-embeddings` adds the constant this deliberately
  omits, for an end-to-end figure.
- **Embedding throughput on its own**, separated from ingest.
- **`query_changes` after #57 (2026-08-17).** The window predicate now adds two
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

`--skip-reflect` drops the slowest step when only `search` and `list_sources`
are of interest. `BENCH_N=10000` is about two minutes, dominated by `reflect`.

To measure a change against its own baseline, stash the changed file rather than
checking out an older commit — the test suite may reference symbols the benchmark
does not, so `git stash push <file>` gives a clean baseline with everything else
identical.
