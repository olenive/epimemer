# Benchmarks

Measurements from `scripts/bench.py` (`make bench`). This file, not the script,
is what makes ISSUES.md **#14**'s deferral condition checkable: #14 says the
full-scan / N+1 patterns get fixed when "latency is felt", and these are the
numbers that say when.

Re-run and append a new section rather than editing an old one — a baseline is
only useful next to the one before it.

## How to read these

- **Embeddings are mocked at 384 dimensions**, the real model's width. Model
  inference is a constant per text that would dominate and hide the graph costs
  this exists to expose; the vector width is kept because scan cost scales with
  it. Every number here is therefore a **floor** — real ingest is slower by the
  embedding time, real search by roughly one query embedding.
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

**`list_sources` is quadratic**, and it is the finding that matters: 4.6 ms →
208 ms → **18 seconds**. Each 10× of data costs ~45× then ~87×. It iterates
every active node and fetches that node's edges (`mcp/tools.py`), so both the
outer loop and the work per iteration grow together.

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

### Not yet measured

- **SurrealDB over `ws://`.** The interesting case, since it multiplies exactly
  the per-node queries that dominate above. Run with
  `EPIMEMER_BENCH_URL=ws://localhost:8000/rpc make bench`.
- **`reflect` at 10k, to completion.** Abandoned at 19 minutes here. Worth one
  patient run to get the real figure, though the shape is already clear enough
  to act on.
- **Real embeddings.** `--real-embeddings` adds the constant this deliberately
  omits, for an end-to-end figure.
