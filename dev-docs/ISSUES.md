# Epimemer — Known Issues

Living issue tracker. **Last review: 2026-08-10.**

Everything found so far is resolved except **14** and **16**. **14**'s first two
steps are done; what remains is step 4, which the work on steps 1–2 discovered
and which is where `reflect`'s cost actually sits. **16** stays deferred by
design.
Resolved entries are **removed from this file** once merged — their resolution
lives in git history and the merged code. Issue numbers are stable IDs; the gaps
(6–13, 15, 17–43) are deleted-resolved items, not missing work, and code
comments citing a number no longer listed here are pointing at one of them. New
findings continue from **44**.

35–38 were the value model & graph hygiene plan
(`dev-docs/REVIEW_EPISTEMIC.md` §12, which records what the plan did not
anticipate) plus the mock-embedding width fix.

The performance work (issues 28, 31, 32, 33 and 39) is resolved and its entries
are gone. **`dev-docs/BENCHMARKS.md`** carries the state those fixes left the system
in and the conclusions still worth acting on, but not the runs themselves — it
describes where things stand, not how they got there, and superseded
measurements are deleted rather than kept. The blow-by-blow is in `git log`.
#14 below depends on the current numbers.

**Workflow (required for every fix):**

1. **Write the failing test first.** Each issue names the test module, suggested
   test name(s), and the assertions. The test must fail against current `main`
   for the reason described, then pass after the fix.
2. Fix the bug. Keep the fix scoped to the issue.
3. Run the whole suite: `uv run python -m pytest tests/ -q` (and
   `make test-integration` when the change touches storage/concurrency — add
   `SURREAL_PORT=8123` if the target reports 8000 in use). Both opt-in suites
   skip themselves when they cannot reach a server, and pytest calls that a
   pass, so read the counts rather than the exit code.
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

### Issue 14 — Full-scan / N+1 query patterns — ▶ ACTIONABLE (steps 1–2 done; step 4 is the new front)

**Status.** Steps 1 and 2 are **done and merged**: `get_edges_for` exists on both
backends and every N+1 *edge* site in the system now uses it. Step 3 stays
blocked by #16 — and is now moot, see below. Step 4 is new, and it is what
actually holds `reflect` at the timeout.

**Guarded by** `tests/storage/test_storage_parity.py::TestBatchedEdgeFetch` and
`tests/pipelines/reflection/test_reflect_scaling.py::TestEdgeFetchesDoNotScaleWithTheGraph`.

#### What steps 1–2 bought

Measured before and after on the same machine in the same session, with
`store_decomposition` flat to three significant figures as the control:

| backend | operation | 1,000 nodes | 2,000 nodes |
|---|---|---|---|
| surrealdb | `list_sources` | 1,133 → **275** ms (4.12×) | 2,343 → **862** ms (2.72×) |
| surrealdb | `search` | 230 → **164** ms (1.40×) | 296 → **246** ms (1.20×) |
| surrealdb | `reflect` | 11,165 → **8,107** ms (1.38×) | 34,695 → **29,056** ms (1.19×) |
| memory | `list_sources` | 28 → **16** ms (1.79×) | 58 → **31** ms (1.85×) |
| memory | `reflect` | 856 → **787** ms (1.09×) | 3,038 → **2,913** ms (1.04×) |

Round-trips at 400 nodes, which is the number the fix actually targets:
`reflect` 5,144 → **1,448**, `search` 319 → **141**, `list_sources` 883 → **87**,
`list_relations` 803 → **7**.

#### What it did not buy, and why

**`reflect` on SurrealDB is still at the 30 s timeout at ~2,000 nodes.** The
prediction in the previous version of this entry — that the batched edge fetch
was what stood between `reflect` and the crossing — was wrong, which is the
fifth time in a row this project's predicted cause has not survived a profile.

Attribution after the fix, at 400 nodes: of the storage reads `reflect` still
issues per node, **none are edge fetches**. What is left is

- `get_node` per neighbour (300 of 600 reads, all in `topic_enrichment`, which
  fetches each fact/inference body just to read `.content`),
- `get_embeddings_for_item` per fact and per topic (300, split between
  contradiction detection and topic consolidation),
- and, outside the read count, one `UPSERT` per node from value decay.

Edge batching removed 72% of the round-trips and moved the wall clock 19%,
because the traffic that remains is the expensive part.

#### What to fix, in order

Two earlier steps predate this list and are not repeated: indexing in-memory
edge lookups, and separating ranking from the status filter in SurrealDB's
`vector_search`.

1. ~~**Batched edge fetch in the protocol.**~~ **Done.**
   `get_edges_for(node_ids, *, direction, edge_type)` returning a map, on both
   backends. Every N+1 edge site uses it, including `_hierarchy_annotations`,
   which had been left waiting for exactly this.
2. ~~**Aggregate queries for the listing tools.**~~ **Done, by step 1 rather
   than by aggregation.** A `GROUP BY` over `node_edge` would have counted
   labels belonging to archived and superseded endpoints, since edges outlive
   their nodes — it would have been one query and the wrong answer. Scoping to
   active nodes needs their ids anyway, so the batched fetch gets the same
   result honestly: `list_relations` went 803 queries → 7.
3. **`asyncio.gather` on per-node enrichment** — **blocked by #16, and no longer
   wanted.** The one place this pattern had already been used (`_in_frame_nodes`)
   is now a single batched query: sequential *and* faster, so the concurrency
   that #16 makes unsafe buys nothing. Prefer batching over gathering anywhere
   this comes up again.
4. **Batched node and embedding reads — the new front.** `get_nodes(ids)` and
   `get_embeddings_for_items(ids, model_id)`, same shape as `get_edges_for` and
   the same parity obligation, plus a batched write for value decay. This is
   what `reflect`'s 30 s crossing is made of now, on the evidence above rather
   than on prediction. Profile again before assuming the mix stays as measured.

---

#### Where this issue stands

`EPIMEMER_TOOL_TIMEOUT_SECONDS` defaults to **30 s**, so "crossing" means *the
tool call fails*, not *feels slow*. `reflect` on SurrealDB is the only operation
in the system that fails at a size real use reaches — ~2,000 nodes, roughly 100
documents of five segments. `search` and the listing tools are now far from any
crossing on both backends.

Ingest is flat on both backends at every size measured; the write path has never
been the ceiling.

`dev-docs/BENCHMARKS.md` carries the reproduction commands, the caveats that
qualify every number (mocked embeddings, a self-similar synthetic corpus,
loopback-only networking), and the profiling recipe. Its standing warning is the
one that matters here, and step 1 above is the latest evidence for it:
**profile first.**

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

## Older carry-overs (open, low priority)

From the original live-graph walkthrough (issues 1–5, otherwise resolved or kept
by design — see git history of this file, commit `22fc874` and follow-ups):

- **No retroactive repair of old graphs.** Fixes apply to new operations;
  pre-existing graphs keep stale state until rebuilt. Accepted.

Merge being Topic-only on the wired path is a scope question rather than a bug —
it lives in README → *Not yet built*.

---

## Recommended order

**#14 step 4 is what is left**, and it is the only thing in the system that
breaks at a size real use reaches: `reflect` on SurrealDB still crosses 30 s at
~2,000 nodes. Steps 1–2 took 72% of its round-trips out and moved the wall clock
19%, which is what established that the remaining cost is per-node *node and
embedding* reads rather than edges. #14 above has the measurements.

**#16 stays deferred**, with its trigger stated. **#14 step 3** is dropped
rather than deferred — batching turned out to beat gathering, so the prong #16
blocked is one nobody wants.

Work that does not exist yet, as opposed to work that is wrong, lives in
`dev-docs/PROPOSED_FEATURES.md`.

What to pick up, and what has to be true first:

| Order | Work | Trigger |
|---|---|---|
| 1 | 14 step 4 (batched node + embedding reads, batched decay write) | **Ready now**, and measured rather than predicted: after steps 1–2 every per-node read left in `reflect` is a `get_node` or a `get_embeddings_for_item`. A protocol change on both backends per the parity rule, same shape as `get_edges_for` |
| deferred | 16 | The server gains concurrent clients (the viz-read leg is closed by the hub; the fix is now scoped to `hub_client.py`) |
