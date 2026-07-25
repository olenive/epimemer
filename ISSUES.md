# Epimemer — Known Issues

Living issue tracker. **Last review: 2026-07-29.**

Everything found so far is resolved except **14** and **16**, both deferred by
design and described below. Resolved entries are **removed from this file** —
their resolution lives in git history and the merged code. Issue numbers are
stable IDs; the gaps (6–13, 15, 17–25, 27, 30) are deleted-resolved items, not
missing work. **26**, **28**, **29**, **31**, **32** and **33** are done and
awaiting deletion on merge. Open: **14** and **16** only, both deferred with
measured triggers. New findings continue from **34**.

**Workflow (required for every fix):**

1. **Write the failing test first.** Each issue names the test module, suggested
   test name(s), and the assertions. The test must fail against current `main`
   for the reason described, then pass after the fix.
2. Fix the bug. Keep the fix scoped to the issue.
3. Run the whole suite: `uv run python -m pytest tests/ -q` (and
   `make test-integration` when the change touches storage/concurrency).
4. Update this file: mark the issue ✅ RESOLVED with the guarding test name(s);
   **once it is merged to `main`, delete the entry** — git history and the code
   are the durable record.
5. One commit per issue (or per tightly-coupled group), so each is reviewable.

**Backend parity is structural.** `tests/conftest.py` parameterizes a `storage`
fixture over `InMemoryStorage` and `SurrealDBStorage(url="mem://")`, so every
test taking it runs against both backends. Storage-behaviour bugs must be tested
this way. Backend-specific internals belong in `test_memory_storage.py` /
`test_surrealdb_storage.py`, which construct their own store. Concurrency is only
exercised by the opt-in Docker suite (`make test-integration`); the default suite
is entirely sequential.

---

## Open issues

### Issue 14 — Full-scan / N+1 query patterns — ⏸ DEFERRED, triggers now measured

**Status.** Still deferred, but no longer on a guess: `scripts/bench.py` (#28)
has measured both backends, and #31 removed the one part that had already
broken. What remains is real but not yet reached. Full data and method:
`dev-docs/BENCHMARKS.md`.

**Why it is deferred and not closed.** The fix is a storage-protocol change
(aggregate queries, batched edge fetch) plus concurrency, and one prong —
`asyncio.gather` on enrichment — is blocked by **#16**'s shared-connection
hazard. It should be driven by a graph that actually hurts, not landed
speculatively. Nothing here is near the sizes below.

---

#### The measurements

`EPIMEMER_TOOL_TIMEOUT_SECONDS` defaults to **30 s**, so "crossing" below means
*the tool call fails*, not *feels slow*.

In-memory (`mem://`), post-#31 **and post-#32**, mocked embeddings, Apple M4 Max
(pre-#32 figures in brackets — they were what made the in-memory backend look
like the problem):

| Nodes | search p50 | `list_sources` | `reflect` |
|---|---|---|---|
| 1,000 | 3.6 ms *(21)* | 27 ms *(203)* | 559 ms *(1,204)* |
| 3,000 | 8.9 ms *(64)* | 85 ms *(1,740)* | 4,402 ms *(10,554)* |
| 10,000 | 27.9 ms *(214)* | 278 ms *(18,757)* | 54,579 ms *(125,180)* |

SurrealDB over `ws://` (**loopback** — a remote server is worse), post-#33:

| Nodes | search p50 | `list_sources` | `reflect` |
|---|---|---|---|
| 1,000 | 118 ms *(1,515)* | 857 ms | 6,060 ms |
| 2,000 | 131 ms *(5,875)* | 1,818 ms | 15,679 ms |
| 4,000 | 136 ms | 3,743 ms | not run |

30 s crossings:

| Operation | in-memory | SurrealDB (loopback) |
|---|---|---|
| `search` | ~10M (linear) | not reachable (flat) |
| `reflect` | ~7,400 | **~3,200** |
| `list_sources` | ~1M (linear) | ~29,000 |

**`reflect` is now the limiting operation on both backends** — ~7,400 nodes
in-memory, ~3,200 on SurrealDB — and it is the one whose residual cost is
genuine O(F²) work rather than a fixable access pattern. Everything else has
been pushed past any size worth quoting.

`list_sources` and `reflect` in the SurrealDB table above predate #33 but are
unaffected by it (`list_sources` was re-measured alongside `search` as a control
and moved by less than run-to-run noise). The in-memory and SurrealDB columns
never confound each other: the backends share call sites at the tool layer and
no implementation, so a fix to one cannot move the other.

---

#### Per-operation analysis

**`search` — was the urgent one, now the cheapest.** In-memory it was always
linear and healthy (28 ms at 10k post-#32). Over a websocket it was **two orders
of magnitude slower and superlinear** (exponent 1.96 per doubling), crossing 30 s
at ~5,100 nodes on the most frequently called tool in the system.

**Profiled and split out as #33, now fixed** — the same move as #31, and the
same lesson: both candidate explanations named here (per-item embedding
fetches, per-node enrichment round-trips) were **wrong**. A component breakdown
put 99% of the call in the single `vector_search` SurrealQL query, whose status
filter SurrealDB re-ran per embedding row. Ranking before filtering made it
flat: 118/131/136 ms at 1k/2k/4k nodes, exponent 0.10. The ~120 ms that remains
is the per-result enrichment — the N+1 pattern that *is* this issue's, and the
floor any further work here would have to attack.

**`reflect` — fixed twice, still the second concern.** #31 made it quadratic
(was cubic), moving the in-memory crossing from ~1,800 to ~5,000 nodes, and #32
bought a further 2.3× constant factor for ~7,400; on SurrealDB it crosses at
~3,200. What remains is dominated by
`_cosine_similarity` in `detect_contradictions` — 280k pure-Python pairwise
comparisons at 1,500 nodes, ~3 s of 5.8 s. That is **genuine O(F²) work, not
redundancy**; vectorizing it (numpy) would buy a large constant factor but not
change the exponent. Nobody has raised that as an issue — do so if it bites.

**`list_sources` / `list_relations` — least urgent, and the in-memory half was
somebody else's bug.** `mcp/tools.py` iterates every active node and fetches
that node's edges: O(N) queries per call. On SurrealDB that is linear with a
round-trip constant (~29k crossing). In-memory it measured *quadratic* — worse
than the networked backend past ~10k — because `InMemoryStorage.get_edges_from`
/ `get_edges_to` scanned the whole edge set per call. **#32 fixed that**: 67×
at 10k and linear, crossing pushed to ~1M nodes. The N+1 *call pattern* is still
here and still this issue's, but it now costs a dict lookup per node rather than
a full scan, so in-memory it is no longer worth attacking on its own.

**Ingest — not a problem.** Flat at ~30k docs/min in-memory across every size
measured, ~2k docs/min on SurrealDB. The write path is fine.

---

#### What to fix, in order

1. ~~**#32 — index edges in `InMemoryStorage`.**~~ **Done 2026-07-29.** Removed
   the in-memory quadratic curve wholesale, and took `search` and `reflect`
   with it — every caller that walks nodes and asks for their edges got faster.
2. ~~**#33 — fix `vector_search`'s correlated `IN`-subquery.**~~ **Done
   2026-07-29.** Quadratic → flat, 45× at 2,000 nodes. (The profile-first rule
   paid out again: both superlinear-term candidates predicted here were wrong.)
3. **Batched edge fetch in the protocol.** `get_edges_for(node_ids, edge_type)`
   returning a map, implemented on **both** backends per the parity rule. This
   is the one change that helps every N+1 site at once, and it is what
   `_hierarchy_annotations` (`mcp/tools.py`) was deliberately left waiting for.
4. **Aggregate queries** for the listing tools: count edges grouped by `dst`
   for `sourced_from`; distinct label+kind for `RELATED`.
5. **`asyncio.gather` on per-node enrichment** — **blocked by #16**. Do not
   introduce concurrent storage access while the SurrealDB connection is
   shared and `use()`-switched.

---

#### How to reproduce

```bash
make bench BENCH_N=1000,3000                      # in-memory
docker run -d --rm --name bench-surreal -p 8001:8000 \
  surrealdb/surrealdb:latest start --user root --pass root memory
EPIMEMER_BENCH_URL=ws://localhost:8001/rpc make bench BENCH_N=1000,2000
```

Profiling recipe that found #31's real cause: seed via `bench._seed`, wrap one
`await reflect(...)` in `cProfile`, sort by cumulative time. Do this before
optimizing anything here.

#### Caveats on every number above

- **The synthetic corpus is unrealistically self-similar** — a 17-word
  vocabulary, so most fact pairs clear the 0.80 contradiction threshold (19%
  under the mock, 49% under the real model on templated text). Anything scaling
  with *surviving candidate pairs* is overstated. Node- and edge-scaled costs
  are not.
- **`MockEmbeddingProvider` is capped at 32 dimensions** by its SHA-256 source
  regardless of the `dimension` argument, while its `dimension` property reports
  what was asked for. Vector-scan cost is understated relative to a real 384-dim
  model. (That inconsistency is arguably its own small bug.)
- **All network numbers are loopback.** A remote SurrealDB is worse by the RTT
  difference times the round-trip count.

---

### Issue 32 — `InMemoryStorage` edge lookups scan the whole edge set — ✅ RESOLVED

> **✅ Resolved 2026-07-29.** `_GraphStore` carries two endpoint indexes beside
> `edges` — `by_src` / `by_dst`, node id → edge ids — and `get_edges_from` /
> `get_edges_to` read from them. Measured before and after back-to-back on the
> same machine (`dev-docs/BENCHMARKS.md`):
>
> | Nodes | `list_sources` | `search` p50 | `reflect` |
> |---|---|---|---|
> | 1,000 | 203 → **27 ms** | 21.0 → 3.6 ms | 1,204 → 559 ms |
> | 3,000 | 1,740 → **85 ms** | 63.7 → 8.9 ms | 10,554 → 4,402 ms |
> | 10,000 | 18,757 → **278 ms** | 214.4 → 27.9 ms | 125,180 → 54,579 ms |
>
> `list_sources` is **linear** now — fitted exponent 1.97 → 1.01, 67× at 10k,
> and its 30 s crossing moves from ~11,000 nodes to ~1,000,000. `search` (7.7×,
> crossing ~140k → ~10M) and `reflect` (2.3×, crossing ~5,000 → ~7,400) were not
> the target but improved anyway: their per-node enrichment does edge lookups
> too. `reflect` keeps its 2.09 exponent — the remaining cost is the genuine
> O(F²) `_cosine_similarity` work from #31, so it is still the in-memory
> operation that fails first.
>
> **Drift is prevented structurally, not by care.** `_put_edge` / `_drop_edge`
> are the only writers of `edges`, and every path goes through them — including
> `_migrate_edges_inplace`, which un-indexes an edge *before* re-pointing it
> (afterwards the old endpoints are unrecoverable) and drops self-loops and
> collapsed duplicates through `_drop_edge`. `_put_edge` un-indexes any previous
> version first, because writes are upserts by id. Empty keys are deleted rather
> than left as empty sets, so the index cannot grow unboundedly on a long-lived
> graph. The snapshot rollbacks needed no change: they deep-copy the whole
> `_GraphStore`, indexes included.
>
> The index costs ~102 bytes per edge (3.2 MiB at 32,500 edges) — about what the
> `edges` dict's own table and keys cost.
>
> Guarded by `tests/storage/test_memory_edge_index.py`: lookups agree with a
> brute-force scan; the index matches the edges it indexes after every write
> path, including both supersede transactions, `merge_nodes_tx` (migration,
> dedup and self-loop drops), a rolled-back `write_batch_tx` and a rolled-back
> supersede; indexes stay confined to their graph. `TestLookupsDoNotScan`
> substitutes an edge dict that raises when enumerated, which is the only test
> here that fails if the index is maintained but never consulted — all five
> mutants tried, including reverting the lookup to a scan, are caught.

**Original report follows.**


**Why.** `get_edges_from` / `get_edges_to` (`epimemer/storage/memory.py`) filter
`self._g.edges.values()` on every call — O(E) per lookup, where SurrealDB
answers the same question from an index. Every operation that walks nodes and
asks for their edges therefore pays O(N·E) in-memory, which is why `list_sources`
measures **quadratic on `mem://` and linear on SurrealDB**, and why at 10k nodes
the in-memory backend (18 s) is *slower than the networked one* (~9 s projected)
despite having no network at all (`dev-docs/BENCHMARKS.md`).

This is the default backend — what a user gets with no configuration — and the
one the whole test suite runs on.

**Scope.** Maintain two indexes beside `edges` in the per-graph store:
`by_src: dict[str, set[str]]` and `by_dst: dict[str, set[str]]` (edge ids).
Update them in `store_edge` and `delete_edge`; read them in `get_edges_from` /
`get_edges_to`, filtering by `edge_type` after the index lookup. Everything
stays in one file behind the existing protocol — no protocol change, no
SurrealDB change, nothing for #16 to interact with.

Watch the compound write paths (`write_batch_tx`, `supersede_node_tx`,
`merge_nodes`) — anything that mutates `edges` directly rather than through
`store_edge` must keep the indexes in step. An index that silently drifts is
worse than no index, which is what the tests below are for.

**Tests first.** `tests/storage/test_memory_edge_index.py`:

- Edges are found after `store_edge`, and *not* found after `delete_edge`
  (the index must not outlive the edge).
- The compound transactional writes leave the indexes consistent: after
  `supersede_node_tx` and `write_batch_tx`, `get_edges_from`/`get_edges_to`
  agree with a brute-force scan of `edges.values()`.
- A rolled-back / failed transaction leaves no orphan index entries.
- Parity: the existing `tests/storage/test_storage_parity.py` already covers
  behaviour across both backends — this issue must not change any of it.

**Verify with numbers, not vibes.** `make bench BENCH_N=1000,3000,10000` before
and after; `list_sources` should go from quadratic to linear. Record the result
as a new section in `dev-docs/BENCHMARKS.md`.

---

### Issue 33 — SurrealDB `vector_search`: correlated `IN`-subquery makes `search` quadratic — ✅ RESOLVED

> **✅ Resolved 2026-07-29.** `vector_search` ranks `k × 3` rows unfiltered,
> checks that handful of ids against the node tables, escalates to `k × 10` if
> too few survive, and only then falls back to the exact query. Measured against
> a throwaway Docker SurrealDB, before-run taken fresh on `main` in the same
> session:
>
> | Nodes | `search` p50 | `list_sources` (control) |
> |---|---|---|
> | 1,000 | 1,515 → **118 ms** (12.8×) | 878 → 857 ms |
> | 2,000 | 5,875 → **131 ms** (44.9×) | 1,712 → 1,818 ms |
> | 4,000 | — → **136 ms** | 3,743 ms |
>
> **Quadratic → flat.** Growth was 3.88× per doubling (exponent 1.96); it is now
> 1.15× across a *4×* increase in nodes — exponent 0.10. At 4,000 nodes `search`
> costs less than it did at 100 before the fix. What remains is the ~120 ms
> enrichment floor the profile predicted, plus roughly 6 µs per node of
> unfiltered scan. `search` is no longer among the operations worth quoting a
> timeout crossing for; on SurrealDB the nearest failure is now `reflect`
> (~3,200 nodes), then `list_sources` (~29,000).
>
> **Two things found while building it, neither of which was in the plan:**
>
> 1. **Variant B cannot be one call.** `LET $active = (…); SELECT …` through this
>    driver returns the *first* statement's result — the select's rows are
>    discarded and `LET`'s `None` is returned in their place, silently. The
>    fallback is two calls: fetch the active ids, bind them as a parameter.
>    Confirmed against `query_raw`, which does return every statement's result.
> 2. **The typed path over-fetches harder than expected.** The unfiltered scan
>    cannot exclude other node types — `embedding` has no type column — so a
>    typed search reaches past every embedding of the wrong type. Fine when the
>    requested type is common (`check_conflicts` searches facts in a loop), and
>    handled by the escalation when it is not.
>
> **Over-fetching is not merely an optimisation**, which is what makes the test
> shape matter: ranking `k` and filtering afterwards returns *fewer than `k`*
> results on any graph whose top hits are retired — retrieval would quietly go
> blind on the longest-lived graphs. The parity file now carries that invariant
> for both backends (`TestVectorSearchReturnsOnlyActiveNodes`, folding in the two
> near-duplicate single-backend tests), including the case where inactive nodes
> would otherwise consume the `k` budget.
>
> Backend-specific behaviour is in `test_surrealdb_storage.py`
> (`TestVectorSearchOverFetch`): starvation, escalation, the exact fallback,
> all-retired and fewer-than-`k` graphs, top-k equivalence against a brute-force
> reference, and the typed path. Three of those tests assert *which path
> answered* by making the exact query raise — without them, a mutant that ranks
> only `k` rows still passes everything, because the fallback rescues its
> correctness while reintroducing the cost. Nine mutants tried, all caught.

**Severity: the nearest failure in the system.** Split out of #14's search leg
on 2026-07-29 after profiling. `search` is the most-called tool; over a
websocket it costs 1.4 s at 1,000 nodes, 5.4 s at 2,000, and crosses the 30 s
tool timeout at ~5,100 (BENCHMARKS.md). In-memory search is unaffected.

**Root cause — profiled, not guessed.** Component timings of one `search` call
against Docker SurrealDB (loopback, mock embeddings, M4 Max):

| Component | 1,000 nodes | 2,000 nodes |
|---|---|---|
| `storage.vector_search` (as shipped) | **1,319 ms** | **5,326 ms** |
| identical query *without* the active-`IN` filter | 3.8 ms | 6.9 ms |
| the active-uid subquery run alone | 2.5 ms | 4.5 ms |
| everything else (`get_node`×k, expansion, all enrichment) | ~120 ms | ~120 ms (flat) |

99% of the call is the one SurrealQL query in
`surrealdb_adapter.py` `vector_search` (~line 802), and the quadratic term is
its filter:

```sql
AND item_id IN (SELECT VALUE uid FROM topic, fact, inference WHERE status = 'active')
```

SurrealDB evaluates the correlated subquery **per embedding row**, so the cost
is embeddings × active nodes. The two halves cost 7 ms and 4 ms run
separately; composed, 5,326 ms. Growth 1k→2k is 4.04× — exactly quadratic.
(The #14 candidates — per-item embedding fetches, per-node enrichment — were
both wrong; enrichment is flat.)

**Fix — variant C below, benchmarked on the same 2,000-node graph:**

| Variant | Time at 2k | vs shipped |
|---|---|---|
| A. shipped (correlated `IN`) | 5,149 ms | — |
| B. `LET $active = (…)` then `IN $active` | 142 ms | 36× |
| C. **over-fetch top `k×3` unfiltered; second query filters those ids by active status** | **10.6 ms** | **485×, identical top-k** |

Take **C with B as the starvation fallback**: two cheap queries replace one
quadratic one. Fetch top `k×3` by similarity with no status filter; one
`uid IN $ids` membership check on those ~30 ids; keep active ones; if fewer
than `k` survive (rare — inactive nodes are a small minority), retry with a
larger multiplier, and past that fall back to **variant B** — *never* to the
shipped query, which is the quadratic path this issue exists to remove. A
graph with many superseded nodes would otherwise pay ~5 s on exactly the calls
over-fetching failed to serve; B caps the worst case at its measured 142 ms
while staying exact. (B alone is not the primary because it is still
O(rows × array) in-engine and keeps growing.) With C, `search` at 2k drops to
~130 ms (enrichment-dominated, flat), and the residual unfiltered scan is
linear and tiny (6.9 ms at 2k → ~35 ms projected at 10k). The adapter's
existing TODO (native HNSW index) remains the eventual ending; no pressure at
these sizes once the quadratic filter is gone.

**Scope.** `surrealdb_adapter.py` `vector_search` only — both the typed
(`node_type` given, single-table subquery) and untyped (three-table) paths.
No protocol change, no `InMemoryStorage` change, nothing for #16 to interact
with.

**Tests first.** Split by what the assertion is about, per the parity rule:

- **Parity** (`tests/storage/test_storage_parity.py`, parameterized `storage`
  fixture): `test_vector_search_excludes_inactive_nodes` — the invariant the
  filter exists for: superseded/merged nodes never resurface, on both the
  typed and untyped paths. This is **protocol-level** and currently has no
  parity coverage at all — it lives only as near-duplicate backend tests
  (`test_memory_storage.py:243`, `test_surrealdb_storage.py:214`). Add it to
  the parity file (and fold the two duplicates into it); it must pass before
  and after the fix.
- **Backend-specific** (`tests/storage/test_surrealdb_storage.py`):
  `test_vector_search_starved_overfetch_retries` — with > 2/3 of the top hits
  inactive, still returns `k` active results (exercises the retry, then the
  variant-B fallback); result-equivalence — same (id, score) top-k as a
  brute-force reference on a mixed active/inactive corpus.

**Verify with numbers**: `EPIMEMER_BENCH_URL=… make bench BENCH_N=1000,2000`
— take a **fresh before** on current `main` rather than reusing the
BENCHMARKS.md table. Not because that table is stale: #32 changed only
`InMemoryStorage`, the two backends share no implementation, and the recorded
SurrealDB column is still current. It is so that before and after come from one
session on one machine, which is what made #31's and #32's numbers trustworthy.
Then after; `search` p50 should fall ~100× and go flat. Record both runs in
`dev-docs/BENCHMARKS.md` as the #31 fix was.

---

### Issue 16 — Multi-graph state is process-global; viz reads re-point the shared connection — ⏸ DEFERRED (by design)

> **⏸ Deferred 2026-07-22.** Latent, not active: the server is single-client
> stdio, so nothing issues concurrent tool calls against the shared connection
> today. Confirmed the two obvious fix shapes are both non-trivial here: a
> dedicated second viz connection is **incompatible with the embedded `mem://`
> backend** (a second `mem://` connection is a *separate* store, so viz snapshots
> would read an empty graph), and the `asyncio.Lock` alternative has to serialize
> *every* adapter operation to hold the "active DB stays put for the whole logical
> operation" invariant — a broad change whose only real test is the Docker
> integration suite. The trigger to pick this up: the server gains concurrent
> clients (e.g. an HTTP/SSE transport). Keeping open as the reminder, as the
> analysis below intends.
>
> **Update 2026-07-24 (viz hub):** viz snapshot reads now execute **in the owning
> MCP process** (the hub RPCs to each session), serialized there by an
> `asyncio.Lock` in `hub_client.py`, so the old cross-connection `use()`-switch
> race in `viz_list_*` is gone. The remaining hazard — a viz read racing a *tool
> call* on the same shared SurrealDB connection — is unchanged and still deferred;
> its eventual fix (a dedicated read connection for SurrealDB) lands in the hub
> client's RPC handler.
>
> **Update 2026-07-28 (fix shape shrank).** The old blocker on the
> dedicated-second-connection fix — "a second `mem://` connection is a separate
> store, so viz would read an empty graph" — **no longer applies**: snapshot
> reads run inside the owning process now, so only the SurrealDB path needs the
> second connection (opened in `hub_client.py`'s RPC handler against the same
> external DB; `mem://` keeps the existing lock path). What was an adapter-wide
> locking change is now a scoped one. Still deferred — the trigger (concurrent
> tool-call clients) hasn't fired — but whoever picks this up should start from
> the hub client, not the adapter.

**Severity: low now, high the moment there are concurrent clients.**

- `switch_database` mutates the single shared connection: a `use_graph` while
  another tool call is in flight redirects that call's writes to the new graph.
- `viz_list_nodes` / `viz_list_edges` (`surrealdb_adapter.py:145-183`)
  temporarily `use()` another database on the **same** connection and switch
  back — already documented "not safe for concurrent MCP calls".

**Fix direction:** a dedicated second connection for viz snapshot reads, and an
`asyncio.Lock` around switch + query in the adapter (or per-call
`USE ns db` scoping if the client library supports it). Acceptable to defer
while the server is single-client stdio; keep this issue open as the reminder.

---

### Issue 31 — `reflect` was cubic and failed the tool timeout at ~1,800 nodes — ✅ RESOLVED

> **✅ Resolved 2026-07-29.** `reflect` is now **quadratic** (fitted exponent
> 2.03) and the 30 s crossing moved from ~1,800 nodes to **~5,000** — measured,
> not extrapolated. 6.3× faster at 1,500 nodes, 13.1× at 3,000.
>
> **The profile redirected the fix.** None of the three directions below was the
> problem. Profiling at 1,500 nodes put **88% of the wall clock** in
> `same_frame` → `frames_of` → `get_edges_from`: 105k lookups to resolve the
> frames of at most 1,500 distinct nodes, each a full edge scan. Candidate pairs
> are quadratic in facts, the scan is linear in edges — that product was the
> cubic term. `frame_resolver(storage)` in `pipelines/reflection/review.py`
> caches per pass; `same_frame` and `review_labels` take it as an optional
> argument, so the cache is created by the caller and cannot outlive the
> operation. Applied in the contradiction loop and in `gather_pending_review`.
>
> Direction 1 below (gather material once) was real but worth only 4–5%.
> Direction 2 (cache the material embedding) is **not built**: there is no
> repeated embedding *within* a call, so it would be a cross-call cache with
> invalidation to get wrong, and the profile does not justify it. Direction 3
> stays #14's.
>
> What remains is `_cosine_similarity` in `detect_contradictions` — 280k pure-
> Python pairwise comparisons, ~3 s of the remaining 5.8 s at 1,500 nodes. That
> is genuine O(F²) work, not redundancy; vectorizing it would buy a constant
> factor, not an exponent. Not raised as an issue — raise one if it bites.
>
> **Caveat on the trigger numbers, found while doing this** (recorded in
> BENCHMARKS.md): the synthetic corpus draws from a 17-word vocabulary, so most
> fact pairs clear the 0.80 contradiction threshold — 19% of unrelated pairs
> under the mock, 49% under the real model on similarly templated text. Costs
> that scale with *surviving candidate pairs* are therefore overstated against a
> diverse real corpus. And `MockEmbeddingProvider` is capped at **32
> dimensions** by its SHA-256 source regardless of the `dimension` requested,
> while reporting the requested value — so earlier claims in BENCHMARKS.md of
> "mocked at 384, the real model's width" were wrong and are corrected there.
>
> Guarded by `tests/pipelines/reflection/test_reflect_scaling.py` (12 tests,
> both backends): frames resolved once per node not per pair, lookup growth
> bounded when facts double, material gathered once per topic, and — the ones
> that matter — disjoint frames still suppress a contradiction, same-frame
> candidates still surface, and the result shape is unchanged. Timing lives in
> `make bench`, never in the suite.

**Severity: live defect, not a ceiling.** Split out of #14 on 2026-07-29 after
the benchmark harness (#28) measured it. #14 stays deferred for `list_sources`
and `search`; this is the part that is already broken.

**Measured** (`dev-docs/BENCHMARKS.md`, `mem://`, mock embeddings, M4 Max — so a
floor):

| Nodes | 1,000 | 1,500 | 2,000 | 2,500 | 3,000 |
|---|---|---|---|---|---|
| `reflect` | 5.4 s | 16.4 s | **40.2 s** | 81.4 s | 138.4 s |

Fitted exponents between adjacent points: 2.73, 3.12, 3.16, 2.91 — cubic.
`EPIMEMER_TOOL_TIMEOUT_SECONDS` defaults to 30 s, so `reflect` **fails at
~1,800 nodes** — about 100 documents of five segments. Raising the timeout
turns a failure into a two-minute wait, which is not a fix.

**Where the cost is** (structure identified; the exact cubic term wants a
profile before anyone optimizes on faith):

- `gather_associated_material(topic, storage)` is called **twice per topic** —
  once in split detection, once in the enrichment scan (`mcp/tools.py`, phases
  `split_detection` and `enrichment_scan`). Each call is two `get_edges_to`
  lookups, and `InMemoryStorage.get_edges_to` **scans every edge in the graph**
  (`storage/memory.py`). So the pair of loops is O(T·E) twice over.
- Split detection also calls `embedding_provider.embed(material)` per topic, on
  every reflect, re-embedding material that has not changed.
- `find_similar_topic_pairs` is O(T²) vector comparisons and
  `detect_contradictions` O(F²); `gather_pending_review` is per-node edge
  queries over all active nodes.

**Fix direction, cheapest first.** These are independent; the first two need no
storage-protocol change and so are not blocked by #14 or #16:

1. **Gather once.** Compute each topic's material a single time and hand it to
   both phases. Halves the dominant term for free.
2. **Cache the material embedding.** Split detection re-embeds every topic's
   material on every call. Key by material content so an unchanged topic costs
   nothing on the second reflect.
3. **Batch the edge fetch** (this *is* #14's protocol work): one grouped query
   for the SUPPORTS/ABSTRACTS edges of all topics, instead of two full scans per
   topic.

**Tests first.** `tests/pipelines/reflection/test_reflect_scaling.py` — assert
`gather_associated_material` is called once per topic per reflect (a counting
spy on the storage backend, both backends via the parameterized fixture), and
that results are unchanged before and after. The timing itself belongs in
`make bench`, not the suite: a wall-clock assertion in pytest is a flake
waiting to happen.

**Do not** net-ify `reflect` as part of this — that is a separate decision
(#29's note), and the phase events added there already make the slow phase
visible in the strip while this is unfixed.

---

## Planned work — all resolved 2026-07-28/29, delete on merge

Not bugs — the next tranche of product work, tracked here at the user's request
so the workflow above (failing test first, scoped commits, delete on merge)
applies to them too. **All of 26–30 are now done**; their entries are deleted as
each merges to `main`. #28's harness then measured #14, which produced **31**
above.

### Issue 26 — Auto-reflect counter: make it visible and user-controllable — ✅ RESOLVED

> **✅ Scope 1 done 2026-07-28.** `graph_stats` now reports
> `stores_since_reflect`, `reflect_threshold` and `reflect_suggested`, so
> reflection pressure is readable without storing something first. The default
> threshold is process config, so `tools.graph_stats` takes it as a **required**
> keyword argument (`default_reflect_threshold`) and the MCP wrapper passes
> `config.reflect_threshold` — no default inside the tool, which would have made
> two places disagree about the effective threshold once scope 2 added an
> override.
>
> All three keys are always present, unlike `store_decomposition` which omits
> `reflect_suggested` when false: an absent key is indistinguishable from
> `false` to a caller, and this readout exists to be checked. The `>=` boundary
> matches `store_decomposition` so the two never disagree about whether a
> reflect is due.
>
> Guarded by `tests/mcp/test_graph_stats.py` (both backends):
> `test_reports_reflect_counter`, `test_fresh_graph_reports_zero`,
> `test_suggests_reflect_from_the_threshold_onwards`,
> `test_suggestion_clears_after_a_reset`,
> `test_reflect_keys_are_always_present`, and
> `test_tool_reports_the_configured_threshold` for the wrapper's config
> pass-through.
>
> **✅ Scope 2 done 2026-07-29.** New `configure_reflection(threshold)` tool
> and a per-graph override on the storage protocol
> (`get_reflect_threshold_override` / `set_reflect_threshold_override`),
> implemented on **both** backends and on the viz instrumentation wrapper.
> In-memory it is a field on the per-graph store; under SurrealDB it shares the
> existing `graph_state:reflect` record with the counter, clearing via `NONE` so
> the key is removed rather than storing a 0 that would read back as a threshold
> of zero.
>
> Resolution (`override if set, else the configured default`) is one shared
> function, `resolve_reflect_threshold` — `graph_stats` reads the override
> itself because it reports whether one is set, and `store_decomposition` goes
> through `effective_reflect_threshold`. The rule exists once so the number an
> agent is *shown* is the number it is *judged against*. `graph_stats` gained
> `reflect_threshold_overridden` so a surprising threshold is attributable.
>
> Clearing stores "no override" rather than the default's current value, so a
> later change to `EPIMEMER_REFLECT_THRESHOLD` still reaches a graph that was
> once overridden. Thresholds below 1 are rejected, not clamped. The counter is
> never touched: raising the threshold defers the signal instead of discarding
> it, which is why the "true snooze" the scope below rules out stays ruled out.
>
> Guarded by `tests/mcp/test_configure_reflection.py` (37 tests, both backends):
> storage-level set/read/overwrite/clear, per-graph isolation across a
> `switch_database`, the counter left undisturbed, resolution and its fallback,
> the tool's report/validation/no-reset behaviour, both `graph_stats` readouts,
> and — through a real MCP session — persistence across a reconnect plus
> `store_decomposition` honouring the override.
>
> **✅ Scope 3 done 2026-07-29.** A `reflect n/m` badge sits in the viewer's
> header next to the MCP graph label, amber once a reflect is due.
>
> The event (`ReflectCounterUpdated`, fields `count` / `threshold` /
> `suggested`) is emitted from the **instrumentation wrapper**, not from the two
> call sites the plan named. The wrapper is where every other graph event comes
> from, the counter mutations are storage mutations like any other, and one
> emission helper there covers bump, reset *and* a threshold change — which the
> two named sites would have missed, leaving the badge showing the right count
> against a stale denominator after `configure_reflection`. The wrapper takes
> the process default (`instrument_storage(..., default_threshold=...)`) since a
> per-graph override can replace it but not supply it.
>
> `suggested` rides on the event rather than being recomputed in the browser, so
> the inclusive boundary rule stays in one place and the badge cannot disagree
> with what the agent is told at ingest.
>
> **Beyond the written scope:** `/api/graphs` now carries a `reflect` block for
> the active graph, and the badge seeds from it on session select and on
> `graph_switched`. Events alone describe only what happened *since the browser
> connected* — a viewer opening onto a graph already at 7 of 10 would have shown
> nothing until the next store, which is the exact "see pressure at a glance"
> this issue exists for. Threading it through cost one parameter on
> `start_hub_client`.
>
> The resolution rule moved to `storage/protocol.py` as
> `resolve_reflect_threshold`, since both `mcp/tools.py` and the visualization
> wrapper now need it and visualization importing `mcp.tools` would invert the
> layering.
>
> Guarded by `tests/visualization/test_reflect_counter_events.py` (emission on
> each mutation, override precedence, active graph, and pass-through: values
> returned unchanged and reads emitting nothing),
> `tests/visualization/test_viz_endpoints.py` (the seeded `reflect` block,
> including an override), and `src/reflect-badge.test.ts` (10 tests: unknown vs
> zero, seeding, threshold changes under a steady count, and the amber
> transition).

**Why.** Post-#25 the counter is persistent per-graph (storage protocol:
`get_reflect_counter` / `bump_reflect_counter` / `reset_reflect_counter`;
`storage/memory.py:561-569`, `surrealdb_adapter.py` `_REFLECT_FIELD`), but it
is only surfaced inside `store_decomposition` responses
(`mcp/server.py:327-332`). The user cannot *see* reflection pressure at a
glance, and the threshold is a static process config
(`reflect_threshold: int = 10`, `mcp/config.py:34`, env
`EPIMEMER_REFLECT_THRESHOLD`) with no runtime control — no "reflect sooner",
no "snooze".

**Scope (three independent pieces, in order of value):**

1. ~~**`graph_stats` reports it.**~~ ✅ done — see the note above.
2. ~~**Per-graph threshold override.**~~ ✅ done — see the note above.
3. ~~**Viz badge.**~~ ✅ done — see the note above. Original plan: emit a small `ReflectCounterUpdated` graph event
   (`visualization/events.py`; fields: `count`, `threshold`) after bump
   (`server.py:327`) and reset (reflect path, `server.py:637-648`). Frontend:
   a header badge `reflect 7/10` for the selected session, amber once
   `count >= threshold` (`frontend/src/main.ts` + `types.ts`; rebuild and
   commit the static bundle).

**Tests first.** For the remaining scopes:
`test_configure_reflection_persists_override` — set override, rebuild server
context on the same storage, assert the effective threshold survives (mirrors
the #25 guard test); `tests/visualization/`: counter event emitted on bump and
reset. Scope 3's frontend half now has a runner — put the badge's
count/threshold reduction in a pure function and test it under
`make test-frontend`, rather than only in the DOM code.

---

### Issue 28 — Benchmark harness (arms the #14 trigger) — ✅ RESOLVED

> **✅ Resolved 2026-07-29.** `scripts/bench.py` + `make bench`, first baselines
> in `dev-docs/BENCHMARKS.md`, and #14 updated above with what they show. It
> did its job on the first run: `list_sources` is quadratic and already takes
> 18 s at 10k nodes, which reframes #14 from "slow" to "fails the 30 s tool
> timeout".
>
> **Embeddings are mocked at 384 dimensions** rather than real. Model inference
> is a large constant per text that would dominate every measurement and hide
> the graph costs the harness exists to expose; the vector *width* is kept
> because scan cost scales with it. Every number is therefore a floor, which is
> stated at the top of BENCHMARKS.md — `--real-embeddings` gives the end-to-end
> figure when that is the question.
>
> The script reuses the public tool functions rather than storage directly, so
> the timings include the same enrichment a real call pays for. Progress goes to
> stderr and records to stdout, so `bench.py > run.jsonl` is a clean record.
> SurrealDB databases it creates are prefix-guarded (`bench_*`) and dropped at
> the end unless `--keep`.
>
> Guarded by `tests/test_bench_smoke.py` (4 tests): a `--quick --n 10` run exits
> clean and emits one parseable record per operation, the records carry the
> measurements they promise, `reflect` is timed unless skipped, and progress
> output stays off stdout.
>
> **Not measured yet, and it is the interesting one:** SurrealDB over `ws://`
> (`EPIMEMER_BENCH_URL=... make bench`), which multiplies exactly the per-node
> queries that dominate. Also `reflect` at 10k, which needs a longer budget than
> the first run allowed.

**Why.** #14's deferral condition is "a graph large enough that latency is
felt" — but nothing measures it, so the trigger can only fire as a user
complaint. A small harness turns it into a number.

**Scope.** `scripts/bench.py` (standalone, not pytest) + `make bench`:

- Seed a synthetic graph of parameterized size (N documents × M segments,
  reusing the public ingest path so numbers reflect reality).
- Measure, per backend (`mem://` always; Docker SurrealDB when
  `EPIMEMER_BENCH_URL` is set): `store_decomposition` throughput
  (docs/min), `search` p50/p95 latency at N nodes, `reflect` wall time vs
  graph size, `list_sources` latency (the known worst N+1 offender,
  `mcp/tools.py:571-617`).
- Output one JSON line per (operation, backend, N) to stdout; a run at, say,
  N ∈ {100, 1k, 10k} nodes.
- Record the first real baselines in `dev-docs/BENCHMARKS.md` (date, machine,
  commit) — that file, not the script, is what makes #14's trigger checkable
  later.

Keep it ~200 lines; it's an instrument, not a framework. No CI wiring — run on
demand.

**Tests.** Exempt from the test-first rule (it *is* a measuring tool): one
smoke test that `bench.py --n 10 --quick` runs green on `mem://` in a few
seconds, so it doesn't rot.

---

### Issue 29 — `reflect` is invisible in the pipeline strip — ✅ RESOLVED

> **✅ Resolved 2026-07-29.** `reflect` now appears in the strip alongside the
> real nets. Frontend unchanged, as predicted.
>
> The emitter is `visualization/phase_events.py` — `phase_pipeline(bus, name,
> phases)`, an async context manager yielding `phase(name, work, tokens=...)`.
> It builds the linear topology, fires each phase, and publishes
> `PipelineCompleted` or `PipelineFailed` on the way out. Generic rather than
> reflect-specific: any sequence of awaits can now declare itself to the strip,
> and the phase list lives in one constant (`tools.REFLECT_PHASES`) that the
> topology and the calls both read.
>
> With no bus, `phase` is a bare `await` — the `_run_net` guarantee that
> watching cannot change what is computed, kept for the synthetic path too.
>
> **`tokens` carries the finding, not just the fact of running:** `len` over
> each phase's candidate list, `int` over the decay count, so the strip's token
> badges show what reflect actually turned up. Counts accumulate across phases,
> matching the net observer, so one event read in isolation still shows the run
> so far.
>
> **Watch for:** restructuring the phases into local coroutines briefly made
> `query_nodes(TOPIC)` run twice (split detection and the enrichment scan share
> the set). Caught and fixed with a lazy cache — a second full scan would have
> added to exactly the #14 cost that makes reflect the slowest operation here.
> `make bench --n 1000` confirms no regression: 5,307 ms against the 5,412 ms
> baseline recorded the same day.
>
> Guarded by `tests/visualization/test_reflect_events.py` (16 tests, both
> backends): topology shape, every phase firing and completing in order, the
> terminal `completed`, real durations, token accumulation, `pipeline_failed`
> plus re-raise when a phase throws, and — the pair that matter most — no events
> without a bus and an identical result either way.

**Why.** The pipeline strip (dev-docs/VISUALISATION.md Part B) lights up for
the four `_run_net` pipelines, but `reflect` — the most interesting process in
the system — runs as plain function phases (`mcp/tools.py:999-1057`:
contradiction detection, relation/topic consolidation, enrichment gathering,
split detection, decay, pending review) and never appears. The strip's
observability story has a hole exactly where users most want to watch.

**Scope — cheap synthetic-pipeline option (recommended).** Do **not** net-ify
`reflect` for this; that's a real refactor with its own risks (the
orchestration net in `pipelines/orchestration/orchestration_net.py` already
models auto-reflect state and would be the vehicle if net-ification is ever
wanted — separate decision). Instead, emit the existing pipeline events with a
hand-written topology from inside the `reflect` tool when `event_bus` is
present:

- On entry: `PipelineStarted(pipeline_name="reflect", ...)` with a linear
  places/transitions chain naming the phases above (the topology is synthetic;
  `events.py` doesn't care).
- Around each phase: `TransitionFired` / `TransitionCompleted` (with real
  `duration_ms`), and `TokensUpdated` with meaningful counts (e.g. pending
  candidates found per phase).
- On exit: `PipelineCompleted` / `PipelineFailed`.

The frontend needs **zero changes** — the strip renders whatever
`PipelineStarted` describes. Keep the emission helper as a small function in
`mcp/tools.py` (or `visualization/`) so the phase list lives in one place.

**Tests first.**
`tests/visualization/test_reflect_events.py::test_reflect_emits_pipeline_events`
— run `reflect` with a recording bus, assert the started → per-phase →
completed sequence and that `pipeline_failed` fires when a phase raises. Also
assert **no events and no behaviour change** when `event_bus` is `None`
(mirrors the `_run_net` guarantee that watching cannot change what is
computed, `tools.py:54-55`).

---

## Older carry-overs (open, low priority)

From the original live-graph walkthrough (issues 1–5, otherwise resolved or kept
by design — see git history of this file, commit `22fc874` and follow-ups):

- **No retroactive repair of old graphs.** Fixes apply to new operations;
  pre-existing graphs keep stale state until rebuilt. Accepted.

Merge being Topic-only on the wired path is a scope question rather than a bug —
it lives in README → *Not yet built*.

---

## Recommended order

Performance work is now measured rather than guessed — `dev-docs/BENCHMARKS.md`
has the data and #14 the analysis. The surprise from the SurrealDB run was that
**`search`** is the operation nearest to failing; profiling (#33) then reduced
it to a single correlated subquery with a verified 485× fix — implementation is
now the cheapest large win in the file. With #32 done, every in-memory crossing
is far past any plausible graph size except `reflect`'s (~7,400 nodes), and both
remaining SurrealDB crossings are below that.

| Order | Issue | Why |
|---|---|---|
| 1 | reflect's O(F²) | **Not yet an issue — raise one when a real graph gets close.** With #31, #32 and #33 done it is the limiting operation on both backends (~7,400 nodes in-memory, ~3,200 on SurrealDB) and the only remaining cost that is genuine pairwise work rather than a fixable access pattern. Vectorizing `_cosine_similarity` buys a large constant factor, not an exponent |
| 2 | 14 (enrichment N+1) | The ~120 ms floor under every SurrealDB `search` is now the per-result enrichment round-trips. Nothing is failing because of it, so it stays deferred — but it is what a batched edge fetch would attack |
| deferred | 16 | Multi-graph concurrency — trigger: the server gains concurrent clients (viz-read leg closed by the hub; fix now scoped to `hub_client.py`) |
| deferred | 14 (rest) | Batched edge fetch + aggregate queries: a protocol change on both backends, and the `asyncio.gather` prong is blocked by #16 |
