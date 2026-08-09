# Benchmarks

Measurements from `scripts/bench.py` (`make bench`). This file describes **where
the system stands now**, not how it got here — superseded runs are deleted rather
than kept, and `git log` holds them if a comparison is ever wanted.

It is what makes ISSUES.md **#14**'s deferral condition checkable: #14 says the
full-scan / N+1 patterns get fixed when latency is felt, and these are the
numbers that say when.

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

| Nodes | Backend | ingest (docs/min) | search p50 | `list_sources` | `reflect` |
|---|---|---|---|---|---|
| 1,000 | memory | 19,158 | 23.0 ms | 29 ms | 851 ms |
| 3,000 | memory | 18,758 | 66.9 ms | 87 ms | 6,665 ms |
| 1,000 | SurrealDB | 3,951 | 214 ms | 1,080 ms | 10,284 ms |
| 2,000 | SurrealDB | 3,829 | 283 ms | 2,013 ms | 29,946 ms |

### The 30 s crossings

`EPIMEMER_TOOL_TIMEOUT_SECONDS` defaults to **30 s**, so "crossing" means *the
tool call fails*, not *feels slow*.

| Operation | in-memory | SurrealDB (loopback) | shape |
|---|---|---|---|
| `search` | ~1.6M | not reachable | linear in-memory, flat on SurrealDB |
| `list_sources` | ~1M | ~28,000 | linear on both |
| **`reflect`** | **~6,700** | **~2,000** | quadratic (exp. 1.87 / 1.54) |
| ingest | not reachable | not reachable | flat |

**`reflect` is the limiting operation on both backends**, and SurrealDB is the
limiting backend by 3.3×. Nothing else is within two orders of magnitude of
failing.

---

## What each operation's cost is made of

**Ingest — not a problem.** Flat across every size measured on both backends. The
write path has never been the ceiling.

**`search` — the cheapest of the three, on both backends.** In-memory it is
linear and always was. On SurrealDB it is **flat**: ranking happens before the
status filter, because SurrealDB re-runs such a subquery per embedding row and
that cost two orders of magnitude when it was inside the ranking query. What
remains is a **~120 ms floor of per-result enrichment round-trips** — one
`get_node` and per-result annotation per hit. That floor is #14's N+1 pattern and
is what a batched edge fetch would attack.

**`list_sources` / `list_relations` — linear, and not urgent.** `mcp/tools.py`
iterates every active node and fetches that node's edges: O(N) queries per call.
In-memory that is now a dict lookup per node rather than a full scan, because
`InMemoryStorage` keeps endpoint indexes (`by_src` / `by_dst`) beside `edges`.
The N+1 *call pattern* remains and is #14's, but in-memory it is no longer worth
attacking on its own.

**`reflect` — the one that fails.** Two different stories per backend, and the
difference is the whole diagnosis:

- **In-memory it is arithmetic-bound and quadratic.** The contradiction phase
  compares every surviving candidate fact pair. That work is genuine, not
  redundant, so it was made fast rather than removed: `similar_pairs`
  (`pipelines/reflection/contradiction_detection.py`) stacks the vectors and
  takes a matrix product per 512-row block instead of one Python call per pair.
  That bought 4.1–4.6× and moved the crossing to ~6,700, with the exponent
  essentially unchanged — a constant factor moves a quadratic crossing by roughly
  its square root.
- **On SurrealDB it is round-trip bound**, which is why the same change bought
  only 1.27–1.36× there. `detect_contradictions` issues one
  `get_embeddings_for_item` per fact, then two edge queries per fact to build the
  already-linked set, all sequential. **That is #14, and it is now what fails
  first on that backend**, at ~2,000 nodes. The next real gain on the networked
  path is batching, not arithmetic.

### What retrieval reinforcement costs

`search` writes every returned node back with a bumped `relevance` and a fresh
timestamp — k extra writes per call, on by default
(`EPIMEMER_REINFORCEMENT_BOOST=0.2`). Measured with `--skip-reflect` against the
same container: **+5% in-memory, +8–12% on SurrealDB** (+14–17 ms).

Cheap, and **flat in graph size**: k does not grow with the graph, so the cost is
a constant — visible on SurrealDB where each write is a round-trip, near-invisible
in-memory. It changes no crossing. It lands on top of the ~120 ms enrichment
floor and is the same shape of cost, so a batched write and a batched edge fetch
would be one piece of work.

---

## Before optimizing anything here

**Profile first. Every performance fix in this project so far has overturned the
cause its issue predicted** — the candidate explanations were wrong four times
running, and in each case a profile redirected the work to something the issue
had not mentioned. The recipe: seed via `bench._seed`, wrap one `await
reflect(...)` in `cProfile`, sort by cumulative time.

Two implementation notes that outlived the measurements that produced them:

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
  `NodeEdge` objects being indexed.

---

## Not yet measured

- **A remote (non-loopback) SurrealDB.** Every network number here is localhost.
- **A diverse corpus.** The 17-word synthetic vocabulary inflates anything
  scaling with surviving candidate pairs.
- **Real embeddings.** `--real-embeddings` adds the constant this deliberately
  omits, for an end-to-end figure.
- **Embedding throughput on its own**, separated from ingest.

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
