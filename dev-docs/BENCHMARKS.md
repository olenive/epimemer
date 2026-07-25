# Benchmarks

Measurements from `scripts/bench.py` (`make bench`). This file, not the script,
is what makes ISSUES.md **#14**'s deferral condition checkable: #14 says the
full-scan / N+1 patterns get fixed when "latency is felt", and these are the
numbers that say when.

Re-run and append a new section rather than editing an old one — a baseline is
only useful next to the one before it.

## How to read these

- **Embeddings are mocked.** Model inference is a constant per text that would
  dominate and hide the graph costs this exists to expose. Every number here is
  therefore a **floor** — real ingest is slower by the embedding time, real
  search by roughly one query embedding.
  **Correction (2026-07-29):** earlier revisions of this file claimed the mock
  runs at 384 dimensions, the real model's width. It does not.
  `MockEmbeddingProvider` derives its vector from a SHA-256 digest and so is
  capped at **32 dimensions** regardless of the `dimension` argument, while its
  `dimension` property still reports what was asked for. Vector-scan cost is
  therefore understated relative to a real 384-dim model.
- **The synthetic corpus is unrealistically self-similar.** Documents are drawn
  from a 17-word vocabulary, so most fact pairs clear the 0.80 contradiction
  threshold. Measured: 19% of unrelated pairs under the mock, and 49% under real
  `all-MiniLM-L6-v2` on similarly templated text. Anything whose cost scales
  with *surviving candidate pairs* (the contradiction phase of `reflect`) is
  therefore overstated here relative to a diverse real corpus. Costs that scale
  with node or edge counts are not affected.
- Node counts are exact (the synthetic corpus is 4 nodes per segment, 5
  segments per document).
- `mem://` measures the algorithms without network cost. A SurrealDB run over
  `ws://` adds a round-trip to every one of the per-node queries, so the N+1
  operations degrade much faster there. Add it with `EPIMEMER_BENCH_URL`.

---

## 2026-07-29 — first baseline

**Machine:** Apple M4 Max, macOS 26.4.1, arm64, Python 3.14.0.
**Commit:** `b95fc16`. **Backend:** `InMemoryStorage`. **Embeddings:** mock-384.

| Nodes | Edges | Ingest (docs/min) | search p50 | search p95 | list_sources | reflect |
|---|---|---|---|---|---|---|
| 100 | 325 | 26,153 | 2.9 ms | 5.3 ms | 4.6 ms | 25.7 ms |
| 1,000 | 3,250 | 32,007 | 21.3 ms | 22.4 ms | 208 ms | 5,412 ms |
| 10,000 | 32,500 | 30,742 | 212 ms | 222 ms | **18,066 ms** | **>19 min** (abandoned) |

### What this says

**Ingest is flat** (~30k docs/min) and does not degrade with graph size — the
write path is not where the ceiling is.

**`search` is linear** in node count: 10× the graph, ~10× the latency
(2.9 → 21.3 → 212 ms). Linear is expected — the vector scan is exhaustive — and
at 10k nodes a fifth of a second is still usable. This is the least urgent part
of #14.

**`list_sources` is quadratic** — 4.6 ms →
208 ms → **18 seconds**. Each 10× of data costs ~45× then ~87×. It iterates
every active node and fetches that node's edges (`mcp/tools.py`), so both the
outer loop and the work per iteration grow together. (Read the later section
first: this is the biggest single number here, but `reflect` is the operation
that actually breaks, and it breaks far sooner.)

**`reflect` was already 5.4 s at 1,000 nodes**, up from 25.7 ms at 100 — ~210×
for 10× the data. At 10,000 it was **still running after 19 minutes** and was
abandoned, so that figure is a lower bound, not a measurement. Extrapolating the
observed growth puts it in the hours.

### The consequence #14 did not state

`EPIMEMER_TOOL_TIMEOUT_SECONDS` defaults to **30 s**. At 10k nodes
`list_sources` takes 18 s of that budget on the fastest available hardware, with
mocked embeddings and no network. On a SurrealDB backend over a websocket, where
each of those per-node edge fetches becomes a round-trip, both `list_sources` and
`reflect` will **exceed the timeout and fail** rather than merely feel slow.

So #14's trigger should be read as: **~10k nodes on `mem://`, and considerably
fewer on SurrealDB.** A persistent graph approaching that size is not a
"performance ceiling" — it is a broken tool call.

> **Superseded by the next section (same day).** The follow-up run across
> 1.5k–3k shows `reflect` crossing 30 s at **~1,800 nodes**, not 10k — it is
> cubic, and it is the operation that breaks first by a wide margin.

---

## 2026-07-29 (later) — the 1.5k–3k range

Run to test an extrapolation from the baseline above, which put `reflect`'s
30 s crossing at ~2,100 nodes. Same machine and commit lineage; `reflect`
measured on every point.

| Nodes | search p50 | list_sources | reflect |
|---|---|---|---|
| 1,000 | 21.3 ms | 208 ms | 5,412 ms |
| 1,500 | 31.8 ms | 446 ms | 16,397 ms |
| 2,000 | 43.4 ms | 796 ms | **40,203 ms** |
| 2,500 | 54.2 ms | 1,264 ms | 81,443 ms |
| 3,000 | 69.2 ms | 1,898 ms | 138,449 ms |

### `reflect` is cubic, not quadratic

Fitted exponents between adjacent points: **2.73, 3.12, 3.16, 2.91** — call it
N³. The earlier two-point estimate of N^2.3 across one decade was optimistic;
five points over the range that matters say otherwise.

**`reflect` exceeds the 30 s default tool timeout at ~1,800 nodes**, and at
2,000 it takes 40 s. That is not a future ceiling — a graph of 2,000 nodes is
about 100 documents of five segments, and on such a graph `reflect` **fails
today** with `EPIMEMER_TOOL_TIMEOUT_SECONDS` at its default. With real
embeddings it is worse: the split and enrichment phases embed every topic's
material on every call.

By contrast `list_sources` fits **1.88, 2.01, 2.07, 2.23** — quadratic — and
does not cross 30 s until ~11k nodes. It is the more dramatic single number at
10k but the less urgent problem, and the baseline section above overstates its
importance relative to `reflect`.

`search` remains linear (21 → 69 ms across 3×) and is not a concern.

---

## 2026-07-29 (after the #31 fix) — `reflect` is quadratic

Two changes: frame resolution is cached per pass (`frame_resolver`), and each
topic's material is gathered once instead of once per phase. Same machine and
corpus as the runs above.

| Nodes | reflect before | reflect after | speedup |
|---|---|---|---|
| 1,500 | 16,397 ms | **2,620 ms** | 6.3× |
| 2,000 | 40,203 ms | 4,979 ms | 8.1× |
| 3,000 | 138,449 ms | **10,584 ms** | 13.1× |
| 5,000 | (not run) | 30,066 ms | — |

**The cubic term is gone.** The fitted exponent between 1,500 and 3,000 is
**2.03** — quadratic. The 30 s crossing moves from ~1,800 nodes to **~5,000**,
predicted at 4,880 from the fit and measured at 30,066 ms at 5,000.

### What the profile said, and what it cost

Profiling at 1,500 nodes put **88% of the wall clock** (17.0 s of 19.4 s) in
`same_frame` → `frames_of` → `get_edges_from`: 105,056 calls to resolve the
frames of at most 1,500 distinct nodes, each one a full scan of the edge set.
Candidate pairs are quadratic in facts and the scan is linear in edges, so that
product was the cubic term. Caching frame lookups per pass removed it.

Gathering each topic's material once rather than twice was worth a further
4–5% — real, but small next to the first change. Note that neither fix is what
Issue #31 originally proposed as most promising; the profile redirected it.

After both, the remaining cost is dominated by `_cosine_similarity` in
`detect_contradictions` — 280,875 pairwise comparisons in pure Python, ~3 s of
the remaining 5.8 s at 1,500 nodes. That is **genuine O(F²) work, not
redundancy**. Vectorizing it would buy a large constant factor but not change
the exponent, and it is not currently on the issue list.

---

## 2026-07-29 — SurrealDB over `ws://`

The measurement every earlier section listed as missing. Throwaway container
(`surrealdb/surrealdb:latest start --user root --pass root memory`) on
localhost, so this is a **loopback** round-trip — a remote server would be
worse. Post-#31 code.

| Nodes | ingest (s) | search p50 | list_sources | reflect |
|---|---|---|---|---|
| 100 | 0.067 | 126 ms | 91 ms | 463 ms |
| 500 | 0.243 | 447 ms | 465 ms | 2,720 ms |
| 1,000 | 0.504 | 1,443 ms | 911 ms | 6,060 ms |
| 2,000 | 0.999 | **5,284 ms** | 1,870 ms | 15,679 ms |

Ratio to in-memory at 1,000 nodes: **search 68×**, `list_sources` 4.4×,
`reflect` 1.1×.

### `search` is the urgent one, and that was not the expectation

Everything before this treated `search` as the healthy operation — linear
in-memory, 212 ms at 10k nodes. Over a websocket it is **68× slower and
superlinear** (exponent 1.87 between 1,000 and 2,000), crossing the 30 s tool
timeout at **~5,100 nodes**. At 1,000 nodes it is already 1.4 s per call.

That matters more than the raw number suggests: `search` is the most frequently
called tool in the system, where `list_sources` and `reflect` are occasional.
A 1.4 s search on a 1,000-node graph is a bad interactive experience well
before anything fails.

### `list_sources` is *linear* here, and quadratic in-memory

The reversal is the useful clue. `InMemoryStorage.get_edges_from` /
`get_edges_to` scanned the entire edge set on every call (`storage/memory.py`);
SurrealDB has an index. So the in-memory backend paid O(E) per edge lookup where
SurrealDB pays one round-trip, and the in-memory quadratic curve was an artefact
of a missing index rather than of the N+1 call pattern.

At 10k nodes the two crossed over: in-memory `list_sources` (18 s) was already
worse than SurrealDB's projection (~9 s), despite having no network at all.

That diagnosis was acted on as #32 — see the last section, where the in-memory
curve becomes linear and this reversal disappears.

### 30 s crossings, both backends

| Operation | in-memory | SurrealDB (loopback) |
|---|---|---|
| `search` | ~140k (linear, 212 ms at 10k) | **~5,100** |
| `reflect` | ~5,000 | ~3,200 |
| `list_sources` | ~11,000 | ~29,000 |

### Not yet measured

- ~~**SurrealDB over `ws://`.**~~ Measured — see below.
- ~~**`reflect` at 10k, to completion.**~~ Measured in the #32 section: 125 s
  before that change, 55 s after. Both past the tool timeout.
- **The in-memory crossings in the table above are superseded** by the #32
  section — `list_sources` ~11,000 and `search` ~140k were measured before edge
  lookups were indexed.
- **A remote (non-loopback) SurrealDB.** Every network number here is over
  localhost, so real deployments are worse by the RTT difference multiplied by
  the round-trip count.
- **A diverse corpus.** See the caveat above: the 17-word synthetic vocabulary
  inflates anything that scales with surviving candidate pairs.
- **Real embeddings.** `--real-embeddings` adds the constant this deliberately
  omits, for an end-to-end figure.

---

## 2026-07-29 (after the #32 fix) — in-memory edge lookups are indexed

`InMemoryStorage` now keeps two endpoint indexes beside `edges` (`by_src`,
`by_dst`: node id → edge ids) instead of filtering the whole edge set on every
`get_edges_from` / `get_edges_to`.

Before and after were measured back-to-back on the same machine in the same
session, `main` code vs. the change, mock embeddings, same corpus and seed.

| Nodes | `list_sources` before → after | `search` p50 | `reflect` |
|---|---|---|---|
| 1,000 | 203 ms → **27 ms** (7.4×) | 21.0 → 3.6 ms | 1,204 → 559 ms |
| 3,000 | 1,740 ms → **85 ms** (20.5×) | 63.7 → 8.9 ms | 10,554 → 4,402 ms |
| 10,000 | 18,757 ms → **278 ms** (67.5×) | 214.4 → 27.9 ms | 125,180 → 54,579 ms |

**`list_sources` is linear now.** Its fitted exponent over 1k → 10k falls from
**1.97 to 1.01**; the 3× steps cost 3.09× and 3.28×. The 30 s crossing moves
from ~11,000 nodes to **~1,000,000**, which is another way of saying this
operation is no longer a scaling concern in-memory.

### Two operations improved that were not the target

- **`search` — 7.7× faster** and still linear. It was never the *shape* of the
  problem in-memory (212 ms at 10k), but its per-result enrichment
  (`frames_of`, `review_labels`, `_hierarchy_annotations`) does several edge
  lookups per hit, each of which was a full scan. Its crossing goes from ~140k
  to ~10M nodes.
- **`reflect` — 2.3× faster**, exponent unchanged at 2.09. #31 removed the
  *redundant* frame lookups; the ones that remain are now cheap individually.
  The 30 s crossing moves from ~5,000 nodes to **~7,400**.

This is the same pattern as #31: the fix was aimed at one operation and paid out
across every caller that walks nodes and asks for their edges, because that
access pattern is everywhere in this codebase.

### `reflect` at 10,000 nodes, to completion

Listed as missing in every earlier section, now run twice: **125,180 ms** before
this change, **54,579 ms** after. Both are far past the 30 s tool timeout —
`reflect` remains the operation that fails first in-memory, and its residual
cost is the genuine O(F²) `_cosine_similarity` work described in the #31
section, not redundancy.

### What the index costs

~102 bytes per edge (3.2 MiB at 32,500 edges), measured with
`sys.getsizeof` over both index dicts and their sets. That is roughly what the
`edges` dict's own table and keys cost, and small next to the `NodeEdge` objects
being indexed. Nothing here trades meaningful memory for the speed.

### Reproduction

```bash
uv run python scripts/bench.py --n 1000,3000   # ~1 min
uv run python scripts/bench.py --n 10000       # ~2 min, dominated by reflect
```
