# Epimemer — Known Issues

Living issue tracker. **Last review: 2026-08-10.**

Everything found so far is resolved except **14** and **16**, both deferred by
design, and **39**, **42** and **43**, which are scoped and actionable (**43**
blocks **42**). **34**, **40** and **41** are resolved but not yet merged, so
their entries are still here. Resolved entries are **removed from this file** —
their resolution lives in git history and the merged code. Issue numbers are
stable IDs; the gaps (6–13, 15, 17–33, 35–38) are deleted-resolved items, not
missing work. New findings continue from **44**.

35–38 were the value model & graph hygiene plan
(`dev-docs/REVIEW_EPISTEMIC.md` §12, which records what the plan did not
anticipate) plus the mock-embedding width fix. Merged 2026-08-07; their entries
are gone, and #39 below exists because fixing the width changed what the
benchmarks measure.

The performance work (issues 28, 31, 32 and 33) is the exception worth knowing
about: its entries are gone, but the measurements, the method and the reasoning
survive in **`dev-docs/BENCHMARKS.md`**, which is written to be read on its own.
#14 below depends on it.

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

### Issue 14 — Full-scan / N+1 query patterns — ⏸ DEFERRED, triggers now measured

**Status.** Still deferred, but no longer on a guess: `scripts/bench.py`
(`make bench`) has measured both backends, and three fixes have since removed
every part that had actually broken — `reflect`'s cubic frame lookups, the
in-memory edge scan, and SurrealDB's correlated status filter. What remains is
real but not yet reached. **Full data, method and the history of those three
fixes: `dev-docs/BENCHMARKS.md`**, which is the durable record now that their
issue entries have been deleted.

**Why it is deferred and not closed.** The fix is a storage-protocol change
(aggregate queries, batched edge fetch) plus concurrency, and one prong —
`asyncio.gather` on enrichment — is blocked by **#16**'s shared-connection
hazard. It should be driven by a graph that actually hurts, not landed
speculatively. Nothing here is near the sizes below.

---

#### The measurements

`EPIMEMER_TOOL_TIMEOUT_SECONDS` defaults to **30 s**, so "crossing" below means
*the tool call fails*, not *feels slow*.

**The two tables below are 32-wide** (see the caveats at the end of this issue);
the 384-wide replacements are in `dev-docs/BENCHMARKS.md` § *2026-08-07*. They
are kept because the *relative* story they tell — which operation dominates,
and which fix moved what — is unchanged by the width. The crossings table after
them is the corrected one.

In-memory (`mem://`), current code, mocked embeddings, Apple M4 Max. Bracketed
figures are from before edge lookups were indexed — they are what made the
in-memory backend look like the problem:

| Nodes | search p50 | `list_sources` | `reflect` |
|---|---|---|---|
| 1,000 | 3.6 ms *(21)* | 27 ms *(203)* | 559 ms *(1,204)* |
| 3,000 | 8.9 ms *(64)* | 85 ms *(1,740)* | 4,402 ms *(10,554)* |
| 10,000 | 27.9 ms *(214)* | 278 ms *(18,757)* | 54,579 ms *(125,180)* |

SurrealDB over `ws://` (**loopback** — a remote server is worse). Bracketed
`search` figures are from before ranking was separated from the status filter:

| Nodes | search p50 | `list_sources` | `reflect` |
|---|---|---|---|
| 1,000 | 118 ms *(1,515)* | 857 ms | 6,060 ms |
| 2,000 | 131 ms *(5,875)* | 1,818 ms | 15,679 ms |
| 4,000 | 136 ms | 3,743 ms | not run |

30 s crossings, at a real 384 dimensions (2026-08-07 re-baseline):

| Operation | in-memory | SurrealDB (loopback) |
|---|---|---|
| `search` | ~1.3M (linear) | not reachable (flat) |
| `reflect` | ~2,900 | **~1,400** |
| `list_sources` | ~1M (linear) | ~30,000 |

**`reflect` is now the limiting operation on both backends** — ~2,900 nodes
in-memory, ~1,400 on SurrealDB — and it is the one whose residual cost is
genuine O(F²) work rather than a fixable access pattern. That part is **#39**,
not this issue. Everything else has been pushed past any size worth quoting.

`list_sources` and `reflect` in the SurrealDB table predate the `search` fix
but are unaffected by it — `list_sources` was re-measured alongside `search` as
a control and moved by less than run-to-run noise. The in-memory and SurrealDB
columns never confound each other: the backends share call sites at the tool
layer and no implementation, so a fix to one cannot move the other.

---

#### Per-operation analysis

**`search` — was the urgent one, now the cheapest.** In-memory it was always
linear and healthy (28 ms at 10k). Over a websocket it was **two orders of
magnitude slower and superlinear** (exponent 1.96 per doubling), crossing 30 s
at ~5,100 nodes on the most frequently called tool in the system.

**Fixed 2026-07-29, and the profile redirected it** — both candidate
explanations this issue named (per-item embedding fetches, per-node enrichment
round-trips) were **wrong**. A component breakdown put 99% of the call in the
single `vector_search` SurrealQL query, whose status filter SurrealDB re-ran
per embedding row. Ranking before filtering made it flat: 118/131/136 ms at
1k/2k/4k nodes, exponent 0.10. The ~120 ms that remains is the per-result
enrichment — the N+1 pattern that *is* this issue's, and the floor any further
work here would have to attack.

**`reflect` — fixed twice, now the limiting operation.** Caching frame
lookups per pass made it quadratic (it was cubic), moving the in-memory
crossing from ~1,800 to ~5,000 nodes; indexing edge lookups bought a further
2.3× for ~7,400 — both figures at the old 32-wide vectors. At a real 384 it
crosses at **~2,900** in-memory and **~1,400** on SurrealDB. What remains is
dominated by `_cosine_similarity` in `detect_contradictions` — 280k
pure-Python pairwise comparisons at 1,500 nodes, and now over 384-component
vectors rather than 32. That is **genuine O(F²) work, not redundancy**;
vectorizing it (numpy) would buy a large constant factor but not change the
exponent. **Now filed as #39** — the width correction is what made it worth
filing, by halving the graph size at which it fails.

**`list_sources` / `list_relations` — least urgent, and the in-memory half was
a different bug.** `mcp/tools.py` iterates every active node and fetches that
node's edges: O(N) queries per call. On SurrealDB that is linear with a
round-trip constant (~29k crossing). In-memory it measured *quadratic* — worse
than the networked backend past ~10k — because `InMemoryStorage.get_edges_from`
/ `get_edges_to` scanned the whole edge set per call. Endpoint indexes
(`by_src` / `by_dst`) fixed that: 67× at 10k and linear, crossing pushed to
~1M nodes. The N+1 *call pattern* is still here and still this issue's, but it
now costs a dict lookup per node rather than a full scan, so in-memory it is no
longer worth attacking on its own.

**Ingest — not a problem.** Flat at ~30k docs/min in-memory across every size
measured, ~2k docs/min on SurrealDB. The write path is fine.

---

#### What to fix, in order

Two earlier steps are already done and are not repeated here: indexing
in-memory edge lookups, and separating ranking from the status filter in
SurrealDB's `vector_search`. Both are written up in `dev-docs/BENCHMARKS.md`,
and both are worth reading first — in each case the fix the issue predicted was
not the fix the profile found.

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

#### How to reproduce

```bash
make bench BENCH_N=1000,3000                      # in-memory
docker run -d --rm --name bench-surreal -p 8001:8000 \
  surrealdb/surrealdb:latest start --user root --pass root memory
EPIMEMER_BENCH_URL=ws://localhost:8001/rpc make bench BENCH_N=1000,2000
```

The profiling recipe that found `reflect`'s real cause: seed via
`bench._seed`, wrap one `await reflect(...)` in `cProfile`, sort by cumulative
time. Do this before optimizing anything here — every performance fix in this
project so far has overturned the cause its issue predicted.

#### Caveats on every number above

- **The synthetic corpus is unrealistically self-similar** — a 17-word
  vocabulary, so most fact pairs clear the 0.80 contradiction threshold (19%
  under the mock, 49% under the real model on templated text). Anything scaling
  with *surviving candidate pairs* is overstated. Node- and edge-scaled costs
  are not.
- **Every number above was measured at 32 vector dimensions, not 384** — the
  mock provider truncated its SHA-256 source (**#38**, fixed 2026-08-07). The
  384-wide re-baseline is in `dev-docs/BENCHMARKS.md`; the crossings it
  supersedes are corrected in the table above.
- **All network numbers are loopback.** A remote SurrealDB is worse by the RTT
  difference times the round-trip count.

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

### Issue 34 — Extraction never proposes timepoints, so content-time is empty by default

**Status.** ✅ RESOLVED. `dev-docs/TIMELINE_VISUALISATION.md` §7 records what was
built, including two departures from the design sketched there.

**Guarding tests.**
- `tests/storage/test_storage_parity.py::TestWriteBatchTxTimelines` — a timeline
  and its `TIMELINK` commit together; a failed batch leaves neither, and leaves
  an *existing* timeline as it was.
- `tests/storage/test_surrealdb_storage.py::TestAtomicOperations::test_write_batch_tx_rolls_back_a_timeline_upsert`
  — the parity test cannot reach this: SurrealDB builds every statement before
  running any, so a Python-injected failure aborts before the transaction opens.
  This one collides inside it, after the upsert in statement order.
- `tests/pipelines/test_temporal.py` — 50 tests over the detector, half of them
  `TestWhatItRefusesToMatch`: the expensive error here is an invented date, not
  a missed one.
- `tests/mcp/test_tools.py::TestTimepointProposal` — concrete dates become dated
  timepoints, vague ones stay undated, text with no temporal expression creates
  no timeline, repeats collapse to one timepoint, a second document appends to
  the same timeline.

**Resolution.** `write_batch_tx` now takes `timelines`, upserting them (a
timeline is one record holding a list of timepoints, so appending is a
replacement — there is no insert-shaped way to say it). Ingestion detects
temporal expressions in **node content** rather than segment text, because a
mark needs a node to hang on, and proposes onto **one shared timeline per
graph** rather than one per document, because the panel shows one timeline at a
time. Both departures are argued in §7.2.

---

*Original report:*

**Symptom.** The timeline panel's *content time* mode plots `Timeline` /
`Timepoint` data, but nothing in ingestion creates a timeline: `TIMELINK` is
written in exactly one place, the `create_timelink` tool
(`epimemer/mcp/tools.py`). So the mode is empty on any graph where an agent has
not deliberately curated a timeline, and the panel says so rather than showing
anything. Record-time mode is unaffected — it needs no curation.

**Fix.** During decomposition, detect temporal expressions in segment text and
propose `Timepoint`s: a resolved `start`/`end` where the expression is concrete,
`label` only where it is not. "During the Renaissance" must stay vague rather
than being guessed into 1500-01-01 — the panel has an undated lane precisely so
that an unresolvable date is never invented.

**Blocker to settle first.** `write_batch_tx`
(`epimemer/storage/protocol.py`) takes `nodes`, `edges` and `embeddings` only.
Ingestion is atomic *because* everything goes through it. If extraction writes
timelines, either they join that batch or a mid-document failure can leave
`TIMELINK` edges pointing at a timeline that was never stored. Extending the
batch is the right fix and touches both backends and their rollback paths — so
that is the first commit, not an afterthought.

**Failing test first.**
- `tests/storage/test_storage_parity.py::TestWriteBatchTxTimelines` — a batch
  containing a timeline plus a `TIMELINK` edge commits both; a batch that raises
  part-way leaves neither. Must run against both backends.
- Then extraction: given a segment with a concrete date, `store_decomposition`
  produces a timepoint with a `start`; given a vague expression, one with a
  `label` and no `start`.

---

### Issue 39 — `reflect` is the nearest real failure: O(F²) cosine similarity in pure Python

**Status.** Open, and newly worth filing. It sat as a "watch" row for weeks on
the strength of a crossing at ~7,400 nodes. The 2026-08-07 re-baseline at a real
384 dimensions (#38, resolved) moved that to **~2,900 in-memory and ~1,400 on
SurrealDB** — around 70 documents of five segments. That is a graph size real
use reaches, so the trigger the watch row was waiting for has fired.

**Symptom.** `detect_contradictions`
(`epimemer/pipelines/reflection/contradiction_detection.py`) compares every
surviving candidate fact pair with `_cosine_similarity`, a pure-Python dot
product. At 1,500 nodes that is ~280k comparisons, and each now walks
384-component vectors rather than 32. `reflect` exceeds the 30 s default
`EPIMEMER_TOOL_TIMEOUT_SECONDS` at the sizes above — the tool call *fails*, it
does not merely feel slow.

**What is different about this one.** Every earlier performance fix removed
*redundancy* — repeated frame lookups, a full scan per edge lookup, a
re-executed status filter. There is none left here: the pairwise comparison is
genuine work. Vectorizing with numpy buys a large constant factor (plausibly
10–50×, which moves the crossing by roughly the square root of that) and does
**not** change the exponent. A real reduction needs fewer pairs, not faster
pairs — blocking, an ANN index, or a cheaper pre-filter before the exact score.

**Fix, in order of what to try.**
1. **Vectorize the scoring.** Stack the candidate vectors into one array and
   take a matrix product instead of a Python loop. Contained, no behaviour
   change, and the measurement will say whether it is enough.
2. **Cut the candidate set** if it is not. The 0.80 threshold currently admits
   19% of unrelated pairs under the mock corpus — see the self-similarity caveat
   in `dev-docs/BENCHMARKS.md`, which also means any measurement here overstates
   the phase relative to a diverse real corpus. Measure on realistic text before
   choosing a blocking strategy.

**Failing test first.** This is a performance issue, so the guard is a
measurement, not an assertion about a wrong answer:
- `tests/pipelines/reflection/test_reflect_scaling.py` — extend the existing
  scaling coverage with a contradiction-phase case that fails at a node count
  the current implementation cannot finish inside the tool timeout.
- Whatever changes, `TestAnswersAreUnchanged` in that module must still pass:
  the pairs found must be identical, since this is a speed fix and not a
  detection change.

**Re-bench after.** `make bench BENCH_N=1000,3000` and the SurrealDB command in
#14, appending a section to `dev-docs/BENCHMARKS.md`. Compare only against the
2026-08-07 baseline — anything older is 32-wide.

---

### Issue 40 — A dropped SurrealDB connection is never re-established, so the process wedges permanently

**Status.** ✅ RESOLVED. Found live on 2026-08-10: restarting the SurrealDB
container left every MCP tool call in an already-running server returning

```
sent 1011 (internal error) keepalive ping timeout; no close frame received
```

and it never recovered. The visualization UI showed this as a permanent
"Loading…" (see #41 for that half).

**Symptom.** `SurrealDBStorage.connect()`
(`epimemer/storage/surrealdb_adapter.py`) assigns `self._db` once and nothing
ever rebuilds it. The `db` property only guards `None`, which happens on a
*never-connected* store, not a *disconnected* one. So after the websocket
underneath drops, all 60-odd `self.db.query(...)` call sites raise for the rest
of the process's life.

**Why the SDK does not save us.** `AsyncWsSurrealConnection.connect()` returns
early when `self.socket` is truthy, and `self.socket` is never cleared on a drop
— `_recv_task` swallows `ConnectionClosed`, cancels the pending futures and
exits, leaving the object looking connected. The next `_send` calls
`await self.connect()` (a no-op) and then `socket.send(...)`, which raises
`ConnectionClosed`. The SDK's own docstring says the connection is "to be used
once and discarded", so reconnection is the caller's job.

**Causes seen or expected.** A server restart (the observed one), a container
`docker restart`, a network blip, and laptop sleep — the last is the nastiest,
because the keepalive deadline expires while both ends are frozen and the
connection is torn down on wake, at the exact moment a user resumes work.

**Fix.** Retry once on a connection-level error: rebuild the connection
(reconnect, re-signin, re-select the database that was selected, re-run the
idempotent schema setup) and re-run the operation.

Three constraints the fix has to respect:
- **Never reconnect an embedded engine.** `mem://`, `memory`, `file://` and
  `surrealkv://` map to `AsyncEmbeddedSurrealConnection`, which *holds the data
  in the connection object*. Reconnecting one would silently hand back an empty
  graph instead of restoring anything. Such a connection also cannot raise
  `ConnectionClosed`, so the guard is belt-and-braces — but the failure it
  prevents is silent data loss, which earns it.
- **Re-select the database that was actually selected**, not the home one. The
  viz reads (`viz_list_nodes` and friends) temporarily `use()` another database
  and restore it in a `finally`; a reconnect during that window must come back
  pointed where the caller thinks it is.
- **One reconnect for concurrent callers.** Several in-flight operations will
  fail together. Serialize on a lock and let the losers notice the connection
  was already rebuilt, rather than each building their own and leaking all but
  the last.

Retrying the operation is safe here, and that is a property of this schema
rather than a general truth: every write is either an `UPSERT ... WHERE uid` or
an `INSERT INTO` guarded by a `UNIQUE` index on `uid` — which SurrealDB silently
ignores on collision — and the multi-statement writes are transactional, so a
connection lost mid-flight aborts them server-side. A retry is therefore a
no-op or a repeat of an idempotent write.

Out of scope: retrying the operation that was *in flight* when the socket died.
The SDK cancels its pending futures, so that caller sees `CancelledError`;
distinguishing "cancelled by us" from "cancelled by a dead socket" is not worth
the ambiguity. That one call fails, and the next one reconnects.

**Guarding tests.**
- `tests/storage/test_surrealdb_storage.py::TestReconnection` — four tests
  against a stand-in connection, because a real drop is not something the
  default suite can stage: a dropped connection is rebuilt and the query
  retried; an *embedded* connection is never rebuilt (the error propagates
  instead, since reconnecting would return an empty engine); the reconnect
  restores the database that was actually selected, not the home one;
  concurrent callers share one reconnect rather than building five.
- `tests/storage/test_surrealdb_persistence.py::test_storage_recovers_after_server_restart`
  — the real-world proof, opt-in via `make test-integration`. It keeps *the
  same* `SurrealDBStorage` across a `docker restart` and asserts the next call
  succeeds. Verified to fail without the fix, with the same
  `ConnectionClosedError` seen live.

**What the shape of the fix cost.** All 60-odd call sites now go through
`_query`/`_use` rather than `self.db.query`/`self.db.use`, and the three
module-level ranking helpers take that wrapper instead of the connection.
`_setup_schema` is the one deliberate exception — it runs inside `connect()`, so
retrying there would re-enter `connect()` while `_reconnect` holds its lock.

---

### Issue 41 — A failed session RPC leaves the viz UI on "Loading…" with a green Connected badge

**Status.** ✅ RESOLVED. Uncovered by #40, and cosmetic next to it — but it is
the reason the failure read as "the frontend is broken" for an hour.

**Symptom.** `selectSession` (`epimemer/visualization/frontend/src/main.ts`)
awaits `fetchGraphs`. When the hub answers 502 because the session's storage is
unreachable, the `catch` only does `console.error`, so:
- the graph selector keeps the `<option>Loading...</option>` placeholder from
  `index.html` — forever, with no timeout and no retry;
- both panels stay empty, with no indication why;
- the `Connected` badge stays green, because it reports the *browser↔hub*
  socket, which is genuinely fine.

Nothing on screen distinguishes "still loading" from "this session's backend is
dead", and the one place the reason exists is the browser console.

**What was done.** A new pure module, `src/session-select.ts`, holds the three
decisions; `main.ts` keeps only the DOM:
- the graph selector is now a state (`loading` / `ready` / `unavailable`) rather
  than a placeholder that happens to get overwritten. `unavailable` carries the
  hub's own words in the `title`;
- `api.ts` reads the error body, so that reason survives the fetch. `502` was a
  shrug; `sent 1011 (internal error) keepalive ping timeout` was the diagnosis,
  and it was being discarded one line after it arrived;
- session ranking is *answers > listed but not answering > gone*, most recent
  first within a rank. Recency alone picked the session you used last, which is
  the one you most recently broke. `selectFirstWorkingSession` walks that order
  until one answers, so a wedged backend no longer blanks a UI that had a
  healthy session two rows down;
- "unreachable" is a third session state, distinct from disconnected, shown in
  the session selector. Only asking reveals it — from the hub's side the session
  *is* connected — so the browser accumulates it, and clears it when a session
  re-registers or answers again;
- the status badge now reads "Hub connected", since that is all it ever meant.

**Guarding tests.**
- `src/session-select.test.ts` — 13 tests: a reachable session is preferred to a
  more recent unreachable one; the old recency behaviour survives when nothing
  is known to be unreachable; an unreachable session still beats a disconnected
  one and is still selectable (that is how its reason reaches the screen); the
  selector says "unavailable" rather than "loading" and puts the reason in the
  title.
- `src/api.test.ts` — the hub's reason survives the fetch, with the status code
  as the fallback when the body carries none.

`main.ts` stays untested by choice, as the other DOM modules do; `tsc` covers it.

---

### Issue 42 — `importance` only moves up, and a stale judgment protects a node forever

**Status.** Open, scoped. **Blocked on #43**, whose judgment timestamp half of
this reads. Rewritten 2026-08-10 after the design discussion recorded below; the
first draft proposed a `deprioritize` tool alongside `reinforce`, and considered
decaying `importance` on a clock. Both were wrong, for reasons kept here because
they are the reasons the shape below is right.

**Symptom.** Two halves of one hole.

1. `ValueSignal.importance` has exactly one deliberate mutator — `reinforce`
   (`epimemer/mcp/tools.py:1100`) — and it only raises. Nothing lowers it
   anywhere: `apply_decay` touches `relevance` only and says why
   (`pipelines/reflection/value_decay.py:44-48`), `update` carries the signal
   forward verbatim (`tools.py:1069`), topic merge takes `max()` of its sources
   (`tools.py:1710`), and `config.py:52` states the rule outright — "nothing
   lowers importance on a clock." Each is correct in isolation. Together they
   mean *judgment* has no downward path, on the axis that exists to carry
   judgment.
2. `nominate_archival_candidates` skips anything with
   `importance > importance_ceiling` (0.5) (`archival.py:234,243`). So one
   `reinforce` call removes a node from the cheap nomination tier
   **permanently**. Cleanup does not get it wrong later — cleanup never looks
   again.

`REVIEW_EPISTEMIC.md` §12.3 gives the example itself: "error message X matters
until the bug is fixed, then doesn't." The upward half of that sentence is
implemented; the downward half is not.

**Why archival does not already cover it.** Three reasons, in increasing order of
what they cost:

1. **Different verdict.** Archival is an all-or-nothing status flip that per §7
   wants human approval; a change of degree is something the agent may conclude
   alone. Forcing the first to express the second overstates what was concluded.
2. **The middle case has no expression.** Still worth keeping, no longer worth
   the earlier assessment, is neither important nor archivable. Today the only
   way to record it is to leave the stale number in place.
3. **Re-nomination**, above: the mechanical tier goes blind, so the agent would
   have to carry the re-assessment out of band — precisely the job this system
   exists to do.

---

#### Fix, part 1 — judgment moves both ways

`reinforce` becomes `judge_importance(node_id, direction, reason, related_id=None)`.

**The rename is not cosmetic.** "Reinforcement" is the right word for the
*retrieval* mechanism: it is the memory-science term, and that mechanism
genuinely only goes up, with decay as its counterweight on a clock. It is the
wrong word for a judgment that can go either way, and keeping it would have
forced the incoherent `reinforce(direction="down")`. Naming the tool for the
**act** rather than the outcome makes the argument coherent — and `direction` is
not ceremony wrapped around the judgment, it *is* the judgment: "this matters
more than the graph currently thinks", or less.

**Steps rather than a setter**, which §12.4 already decided and the discussion
sharpened:

- an agent setting `0.7` has not seen any other node's value and is guessing at a
  scale it cannot see; "more than the graph thinks" is a judgment it can make
  well;
- a setter is last-writer-wins — three judgments that took a node to 0.85 are
  erased by one agent typing 0.6 six months later on one conversation's context.
  Steps compose; setters overwrite.

The one moment a setter is safe already exists: `store_decomposition`'s ingest
prior, applied at creation before there is anything to overwrite. Set at birth,
nudge thereafter.

The down step mirrors the up step in form:

```
up:    importance += step × (1 − importance)     # asymptotic to 1.0
down:  importance -= step × importance           # asymptotic to 0.0
```

Both close the gap to their bound by the same fraction. Neither reaches its
bound, so arithmetic can never judge a node out of existence and neither needs a
clamp.

**They are mirrors, not inverses — say so in the docstring.** Up-then-down does
not return home: from 0.5 at the default step of 0.25, up gives 0.625 and down
gives 0.469. Repeated alternation settles into a **2-cycle**, `{0.4286, 0.5714}`,
straddling 0.5 almost exactly — up from 0.4286 gives 0.5714, down from 0.5714
gives 0.4286, and neither side wins. Two agents in sustained disagreement
therefore park the node at the un-judged default, oscillating across the 0.5
nomination ceiling, so whether it is nominatable depends on which judgment came
last. That is the right terminal state: the most recent assessment governs, and
neither side can lock it permanently.

**Why not an exactly invertible form.** Two exist and both were rejected.
`(i − step)/(1 − step)` returns home but goes negative below the step size and
needs a clamp — so it is invertible in the mid-range, where nothing depends on
it, and lossy near the floor, where the nomination ceiling sits and the
consequences land. Log-odds (`sigmoid(logit(i) ± k)`) is genuinely both
invertible and asymptotic, and is the stronger of the two, but it costs the
settable knob (`EPIMEMER_IMPORTANCE_STEP = 0.25` means "close a quarter of the
remaining gap"; `k` is in log-odds units nobody sets by intuition), needs input
clamping anyway because `logit(0)` and `logit(1)` are infinite, and trades a
one-line formula for one that has to be explained.

All of that buys **invertibility, which nothing here consumes.** A later downward
judgment is not an undo — it is a new judgment on new information, and the
provenance trail keeps both entries deliberately. If undo were ever wanted, the
honest implementation restores the recorded prior value from that trail rather
than hoping the arithmetic reverses.

The asymptote, by contrast, is load-bearing. Reaching 1.0 would mean "maximally
important, and no future judgment can raise this further"; reaching 0 would mean
"worthless, full stop". Arithmetic must not manufacture certainty on the agent's
behalf — the same commitment as archive-never-delete, expressed on the number
line.

**Deferred: a `strength` modifier.** `strength="slight"|"normal"|"strong"` as a
multiplier on the step is about ten lines and a constant table. Deferred on three
grounds: an *optional* parameter with a default is a non-breaking addition
whenever it arrives, so waiting locks nothing in; there is no evidence yet for
what the multipliers should be, and inventing three magic numbers is how a knob
acquires a wrong default nobody revisits; and each extra knob needs a decision
rule in `DEFAULT.md` or agents pick the strongest option by default and the
gradations mean nothing. Trigger to revisit: one step proving too coarse in
practice — measurable, unlike taste.

---

#### Fix, part 2 — a stale judgment stops protecting

Given #43's `importance_judged_at`, nomination reads the **pair** rather than the
number: "rated important, judged six months ago, never since" is a re-review
candidate. `nominate_archival_candidates` gains a `judgment_max_age_days` in the
shape of its existing `max_age_days=90`.

**No decay on `importance`, deliberately.** The first draft of this issue
considered it and it is wrong. A decayed importance is a *fabricated* assessment
— nobody judged that node 0.6, the clock invented it — and the provenance trail
would read "judged 0.85, reason X" beside a field saying 0.6, with nothing
accounting for the gap. That is exactly the unattributable number §12.4's
no-raw-setter rule exists to prevent, arriving through the back door. Staleness
gets the same effect honestly: the recorded judgment stays exactly as recorded,
and what ages is confidence in its *currency* — which is what a timestamp
expresses and a number cannot.

---

#### Two constraints carried over

**One provenance trail, not two.** Keep appending to
`metadata["reinforcements"]` with a `"direction"` field; **absent means up**, so
records written before this change keep their meaning. A reviewer wants a bump
and its later reversal in order, with both reasons — two lists split a story
whose only value is that it is chronological. The key name outlives its accuracy
here; renaming it is a data migration for a cosmetic gain, and #43 already spends
the migration budget.

**Merge still takes `max()`.** A node judged down, then merged with an un-judged
duplicate, comes back up. Correct — merge must not discard the *other* source's
judgment — but it means a downward judgment is not durable across a merge. Noted,
not fixed: changing it needs an argument about whose judgment wins.

---

**Failing test first.** The mirror tests are the cheap half; the one that
justifies the issue is re-nomination, and it fails today for want of any
mechanism at all.

- `tests/pipelines/test_reflection.py` — a fact judged above the ceiling drops
  out of `nominate_archival_candidates`; after a downward judgment it is
  nominated again. **This is the assertion the issue is about.** Second case: a
  fact still above the ceiling, but whose `importance_judged_at` is older than
  `judgment_max_age_days`, is nominated for re-review.
- `tests/mcp/test_tools.py::TestJudgeImportance` — mirroring the existing
  `TestReinforce` (`test_tools.py:635`): the down step lowers asymptotically and
  repeated calls approach but never reach 0.0; `relevance` and `retrieved_at` are
  untouched while `importance_judged_at` moves; entries append rather than
  replace, and an up interleaved with a down keeps both in order with their
  directions intact; unknown `node_id` and unknown `related_id` are rejected.
- The 2-cycle earns its own named test rather than a comment: up-then-down lands
  below the start, and repeated alternation settles on `{0.4286, 0.5714}` at the
  default step rather than drifting to a bound. A future change to the step form
  that breaks this should have to argue with a test, not discover it in a graph.

These take the `storage` fixture, so they run against both backends per the
parity rule; the write goes through `store_node` and needs no protocol change.

**Two commits.**

1. The tool — rename, direction, down step — with its tests and the doc updates:
   `INTEGRATION.md:63`'s tool count (34, enforced by
   `test_tool_count_matches_integration_doc`; a *rename* leaves the count alone,
   which is worth confirming rather than assuming), `README.md:132`'s tool list
   and its `EPIMEMER_IMPORTANCE_STEP` row, `epimemer_prompts/DEFAULT.md`
   §"Recording that something matters" — the agent needs telling *when* to judge
   downward, not merely that it can — and `REVIEW_EPISTEMIC.md` §12.2/§12.4,
   where the upward paths are enumerated and "there is no raw setter" is decided.
   That decision survives intact; it just stops being the whole story.
2. Nomination reading judgment staleness, plus
   **`apply_reflection(judgments=[...])`**. §12.3 lets the agent `reinforce` a
   nominee instead of letting it go, but has no way to say "keep it, and stop
   treating it as important" — the verdict most likely to be right about a node
   reinforced once and never revisited.

---

### Issue 43 — `last_reinforced` names the wrong mechanism, and a judgment leaves no timestamp

**Status.** Open, ready, mechanical. **#42 depends on it.** Found 2026-08-10
while writing #42: the question "should `reinforce` set `last_reinforced`?" has no
good answer, because the field's name spans two mechanisms that are deliberately
separated everywhere else in the design.

**Symptom.** Three things move a node's value, on two clocks, and only one writes
a timestamp:

| Mechanism | Trigger | Moves | Timestamp |
|---|---|---|---|
| Retrieval reinforcement (`reinforced_signal`, `tools.py:624`) | automatic, every `search` hit | `relevance` ↑ | sets `last_reinforced` |
| Decay (`apply_decay`) | `reflect` | `relevance` ↓ | — |
| `reinforce` tool (`tools.py:1100`) | agent judgment | `importance` ↑ | **nothing** |

`last_reinforced` is written in exactly one place (retrieval, `tools.py:636`) and
read for logic in exactly one (`never_reinforced`, `archival.py:159`). It records
the *passive* mechanism under a name that reads like the *deliberate* one — so
`never_reinforced`'s docstring, "nothing has touched this node since it was
created", is false: an agent can deliberately judge a node important and it still
reads as untouched.

§12.2 separates "is this being used?" from "does this matter?" everywhere except
in the vocabulary, which is where a reader meets it first.

**Why this is not just tidiness.** Nomination can ask *was this ever used*. It
cannot ask *when was this last judged*, because nothing records it. So a judgment
can never go stale — which is half of #42, and the half no tool can fix, because
the information was never written down.

**The change.**

| Now | After | Why |
|---|---|---|
| `last_reinforced: datetime`, default `_now` | `retrieved_at: datetime \| None`, default `None` | Names the mechanism that actually writes it |
| — | `importance_judged_at: datetime \| None`, default `None` | The judgment clock, which does not exist today |
| `never_reinforced()` | `never_retrieved()` | What it already computes |
| `_NEVER_REINFORCED_TOLERANCE` | deleted | It exists only because `default_factory=_now` makes "never" and "just now" indistinguishable (`archival.py:121-124`). `None` says it directly |

Nullability is the substantive half, not a style choice: "never retrieved" and
"never judged" are real states that a `_now` default fabricates.

**The ingest prior stays un-stamped.** `store_decomposition`'s optional
`importance` is "a prior, not a verdict" (§12.4) — importance is properly judged
at reflect time. A prior that stamped `importance_judged_at` would masquerade as
a judgment and buy itself the full staleness window before anyone looked. It
leaves the field `None`, which is both true and immediately eligible for review.

**Migration.** `ValueSignal` persists inside the node via
`model_dump(mode="json")` and returns through `model_validate`, so an old record
simply lacks the new keys and takes the defaults. With `None` that means every
pre-existing node reads as never retrieved and never judged, so class-3
nomination *proposes* them. That is the right direction — nomination is a
proposal the agent judges with graph context, never a verdict — and it is the
opposite of what a `_now` default would do, which is to mark every old node as
freshly retrieved and silently exempt whole graphs from cleanup. Worth saying in
the commit message: the first `reflect` after this lands will have more to say
than usual on an existing graph.

**Frontend.** `NodeView` carries the field to the browser (`events.py:51`,
`frontend/src/types.ts:65`), where record-time marks draw `created_at →
last_reinforced` as the span over which a node stayed relevant
(`timeline-model.ts:259-275`). The null case already behaves correctly — `end` is
already conditional, and the docstring already says "a node never reinforced
since creation is a plain point" — so this is a type change plus `parseTime`
tolerating `null`, and it makes an existing intent literal rather than
incidental.

**Failing test first.**

- `tests/pipelines/test_reflection.py` — a node whose `retrieved_at` is `None` is
  treated as never retrieved, with no tolerance window in play. Today the
  equivalent case passes only because two clock reads land a millisecond apart.
- `tests/mcp/test_tools.py` — a judgment sets `importance_judged_at` and leaves
  `retrieved_at` alone; `search` reinforcement sets `retrieved_at` and leaves
  `importance_judged_at` alone. The two clocks being independent is the whole
  point of the split, and nothing asserts it today.
- `tests/core/test_types.py` — a fresh `ValueSignal` has both timestamps `None`,
  and one parsed from a dict missing both keys does too. That second case is the
  migration.
- `frontend/src/timeline-model.test.ts` — a node with `retrieved_at: null`
  renders as a point, not a zero-length span and not a crash.

**Also touches.** `SUMMARY.md:129,221,281`, `README.md:219`,
`REVIEW_EPISTEMIC.md` §12.1–12.2 where the signal is enumerated,
`epimemer_prompts/DEFAULT.md`, and the five Python test modules plus three
frontend ones that name the field.

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

**#40 and #41 are done** — the pair from 2026-08-10, the only entries here that
had actually broken a running system. #40 did it in a way nothing in the default
suite could have caught, because that suite never holds a connection long enough
to lose one. **#39, #43 and #42 are what is left** — #39 because it fails now,
and #43 → #42 because the value model is one-way: every `reinforce` call ever
made has permanently removed a node from the cheap archival tier, so the cost
accrues whether or not anyone is looking.
#14/#16 are deferred by
design with stated triggers, and the earlier performance work is done: `reflect` went
cubic → quadratic, in-memory edge lookups are indexed, and SurrealDB's `search`
went quadratic → flat. `dev-docs/BENCHMARKS.md` has the data; #14 above has the
analysis.

What to pick up, and what has to be true first:

| Order | Work | Trigger |
|---|---|---|
| ✅ | 34 (timepoint extraction) | Done. `write_batch_tx` carries timelines, as its own commit |
| ✅ | 40 (SurrealDB reconnect) | Done. A container restart on 2026-08-10 wedged two live MCP servers until they were killed; the adapter now rebuilds a dropped connection |
| ✅ | 41 (viz surfaces a dead session) | Done. #40 makes a wedged session rare, not impossible, and this is what made an hour's debugging necessary |
| 1 | 39 (reflect's O(F²)) | Ready now, and the nearest thing to a live failure: `reflect` crosses the 30 s tool timeout at ~1,400 nodes on SurrealDB. Try vectorizing before anything cleverer |
| 2 | 43 (value vocabulary & judgment timestamp) | Ready now, mechanical, and #42 cannot be done without it. Also the cheapest moment to do it: the rename gets more expensive with every graph written |
| 3 | 42 (importance moves one way only) | After 43. Nothing fails today, but every `reinforce` call permanently removes a node from the cheap archival tier, so the cost accumulates unobserved |
| watch | 14 (enrichment N+1) | The ~120 ms floor under every SurrealDB `search` is now per-result enrichment round-trips. Nothing fails because of it, so it stays deferred — but it is what a batched edge fetch would attack |
| deferred | 16 | The server gains concurrent clients (the viz-read leg is closed by the hub; the fix is now scoped to `hub_client.py`) |
| deferred | 14 (rest) | Batched edge fetch + aggregate queries: a protocol change on both backends, and the `asyncio.gather` prong is blocked by #16 |
