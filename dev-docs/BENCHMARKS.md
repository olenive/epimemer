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

### Not yet measured

- **SurrealDB over `ws://`.** The interesting case, since it multiplies exactly
  the per-node queries that dominate above. Run with
  `EPIMEMER_BENCH_URL=ws://localhost:8000/rpc make bench`.
- **`reflect` at 10k, to completion.** Abandoned at 19 minutes here. Worth one
  patient run to get the real figure, though the shape is already clear enough
  to act on.
- **Real embeddings.** `--real-embeddings` adds the constant this deliberately
  omits, for an end-to-end figure.
