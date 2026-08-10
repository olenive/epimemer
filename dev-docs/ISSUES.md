# Epimemer — Known Issues

Living issue tracker. **Last review: 2026-08-10.**

Everything found so far is resolved except **14** and **16**, both deferred by
design — though **14**'s first two steps are now actionable, because the
deferral rested on "nothing fails because of it" and #39 ended that.
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

### Issue 14 — Full-scan / N+1 query patterns — ▶ ACTIONABLE (steps 1–2); step 3 still blocked

**Status.** Actionable for the first time: `scripts/bench.py`
(`make bench`) has measured both backends, and four fixes have since removed
every part that had actually broken — `reflect`'s cubic frame lookups, the
in-memory edge scan, SurrealDB's correlated status filter, and the pairwise
Python in the contradiction phase (#39). **The current numbers and the method
are in `dev-docs/BENCHMARKS.md`**, which carries where those fixes left the
system rather than the runs that produced them; the runs are in `git log`.

**Update 2026-08-10 (#39 promoted this).** With the pairwise arithmetic
vectorized, `reflect` on SurrealDB barely moved — 1.36× against in-memory's 4.6×
— because that backend's time goes on sequential round-trips: one
`get_embeddings_for_item` per fact, then two edge queries per fact to build the
already-linked set. **That is this issue, and it is now what fails first on
SurrealDB**, at ~2,000 nodes against in-memory's ~6,700. The deferral stands —
the fix is still a protocol change on both backends and the `asyncio.gather`
prong is still blocked by #16 — but the *grounds* have changed. This entry no
longer rests on "nothing fails because of it", and the first candidate is the
batched edge fetch in step 1 below, now with a concrete graph size attached.
See `dev-docs/BENCHMARKS.md`.

**Promoted 2026-08-10.** This entry was deferred for months on one sentence —
"nothing fails because of it" — and that sentence stopped being true when #39
landed. `reflect` on SurrealDB is now round-trip bound and crosses the 30 s tool
timeout at **~2,000 nodes**, roughly 100 documents of five segments. It is the
only thing in the system that fails at a size real use reaches.

The deferral was also broader than the blocker justified. Only **step 3**
(`asyncio.gather` on enrichment) is blocked by #16's shared-connection hazard.
**Steps 1 and 2 are unblocked**, and step 1 is the one that helps every N+1 site
at once — including `_hierarchy_annotations` (`mcp/tools.py`), which was
deliberately left waiting for it.

So: steps 1–2 are the next work in this file. Step 3 keeps #16's trigger.

---

#### Where this issue stands

`EPIMEMER_TOOL_TIMEOUT_SECONDS` defaults to **30 s**, so "crossing" means *the
tool call fails*, not *feels slow*. **The numbers live in
`dev-docs/BENCHMARKS.md`** and are not duplicated here — a second copy is a
second thing to keep true, and this entry has already outlived four rounds of
them.

What matters for this issue: **`reflect` is the limiting operation on both
backends, and on SurrealDB the limit is now this issue rather than arithmetic.**
Three live N+1 sites, worst first:

1. **`detect_contradictions` fetches per fact** — one
   `get_embeddings_for_item`, then `get_edges_from` and `get_edges_to` to build
   the already-linked set, all sequential. On SurrealDB this is what puts
   `reflect`'s crossing at ~2,000 nodes against in-memory's ~6,700. It is the
   first thing that fails on a networked backend.
2. **Per-result enrichment under `search`** — the ~120 ms floor that remains
   after ranking was separated from the status filter. Nothing fails because of
   it; it is simply the floor any further `search` work would have to attack.
3. **`list_sources` / `list_relations`** iterate every active node and fetch that
   node's edges: O(N) queries per call. Linear on both backends and far from any
   crossing. The call pattern is this issue's, but in-memory it now costs a dict
   lookup per node rather than a full scan (`by_src` / `by_dst` endpoint
   indexes), so it is not worth attacking on its own.

Ingest is flat on both backends at every size measured; the write path has never
been the ceiling.

---

#### What to fix, in order

Two earlier steps are already done and are not repeated here: indexing
in-memory edge lookups, and separating ranking from the status filter in
SurrealDB's `vector_search`. `dev-docs/BENCHMARKS.md` records what
they left behind, and its standing warning applies to anything attempted here:
in each case the fix the issue predicted was not the fix the profile found.

1. **Batched edge fetch in the protocol.** `get_edges_for(node_ids, edge_type)`
   returning a map, implemented on **both** backends per the parity rule. This
   is the one change that helps every N+1 site at once, and it is what
   `_hierarchy_annotations` (`mcp/tools.py`) was deliberately left waiting for.
2. **Aggregate queries** for the listing tools: count edges grouped by `dst`
   for `sourced_from`; distinct label+kind for `RELATED`.
3. **`asyncio.gather` on per-node enrichment** — **blocked by #16**. Do not
   introduce concurrent storage access while the SurrealDB connection is
   shared and `use()`-switched.

---

#### Before touching any of it

`dev-docs/BENCHMARKS.md` carries the reproduction commands, the caveats that
qualify every number (mocked embeddings, a self-similar synthetic corpus,
loopback-only networking), and the profiling recipe. Its standing warning is the
one that matters here: **profile first** — every performance fix in this project
so far has overturned the cause its issue predicted, including twice on this
issue's own candidate explanations.

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

**#14 steps 1–2 are what is left**, and they are no longer speculative: with the
contradiction phase vectorized, `reflect` on SurrealDB is round-trip bound and
fails at ~2,000 nodes. That is the only thing in the system that breaks at a size
real use reaches. `dev-docs/BENCHMARKS.md` has the numbers; #14 above has the
analysis.

**#16 stays deferred**, with its trigger stated. So does **#14 step 3**, which is
the one prong #16 actually blocks.

Work that does not exist yet, as opposed to work that is wrong, lives in
`dev-docs/PROPOSED_FEATURES.md`.

What to pick up, and what has to be true first:

| Order | Work | Trigger |
|---|---|---|
| 1 | 14 steps 1–2 (batched edge fetch, aggregate queries) | **Ready now.** `reflect` on SurrealDB crosses 30 s at ~2,000 nodes and the cause is this issue's N+1 pattern. A protocol change on both backends per the parity rule; step 1 helps every N+1 site at once |
| deferred | 16 | The server gains concurrent clients (the viz-read leg is closed by the hub; the fix is now scoped to `hub_client.py`) |
| deferred | 14 step 3 | `asyncio.gather` on per-node enrichment — blocked by #16's shared-connection hazard, and the only part of #14 that is |
