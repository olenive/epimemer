# Epimemer — Known Issues

Living issue tracker. **Last review: 2026-08-07.**

Everything found so far is resolved except **14** and **16**, both deferred by
design, and **34–38**, which are scoped and actionable (35–37 are the value
model & graph hygiene plan, designed in `dev-docs/REVIEW_EPISTEMIC.md` §12;
38 is a small standalone fix). Resolved entries are **removed from this file**
— their resolution lives in git history and the merged code. Issue numbers are
stable IDs; the gaps (6–13, 15, 17–33) are deleted-resolved items, not missing
work. New findings continue from **39**.

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
2.3× for ~7,400. On SurrealDB it crosses at ~3,200. What remains is dominated
by `_cosine_similarity` in `detect_contradictions` — 280k pure-Python pairwise
comparisons at 1,500 nodes, ~3 s of 5.8 s. That is **genuine O(F²) work, not
redundancy**; vectorizing it (numpy) would buy a large constant factor but not
change the exponent. Nobody has raised that as an issue — do so if it bites.

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
- **`MockEmbeddingProvider` is capped at 32 dimensions** by its SHA-256 source
  regardless of the `dimension` argument, while its `dimension` property reports
  what was asked for. Vector-scan cost is understated relative to a real 384-dim
  model. (Now filed as **#38**; fixing it invalidates these baselines — see the
  re-bench note there.)
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

**Status.** Open. Scoped, not started. This is the only unbuilt part of
`dev-docs/TIMELINE_VISUALISATION.md` (§7 there has the design).

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

### Issue 35 — Value signal is write-only: decay has no upward counterpart and nothing reads the result

**Status.** Open. Design: `dev-docs/REVIEW_EPISTEMIC.md` §12 (path 1 of §12.2).
Independent of #36/#37; smallest of the three — do it first.

**Symptom.** `ValueSignal` (`epimemer/core/types.py:147`) has exactly three
writers: creation defaults, `apply_decay`
(`pipelines/reflection/value_decay.py`, down only, uniform), and topic-merge.
`last_reinforced` is never updated after creation and nothing reinforces
`relevance`, so relevance is a monotone function of age — it cannot distinguish
"old and load-bearing" from "old and dead", which is exactly the distinction
the #37 archival sweep needs.

**Fix.** In `search` (`epimemer/mcp/tools.py`), after the result set is chosen:
for each returned node set `last_reinforced = now` and
`relevance += boost × (1 − relevance)`, then write back with `store_node`.
Asymptotic form so repeated hits saturate instead of pinning at 1.0. `boost`
comes from `ServerConfig` (suggest `reinforcement_boost: float = 0.2`; `0.0`
disables). Tool-layer change, so both backends get it through the ordinary
write path — no protocol change.

**Scope guard.** Reinforcement must **not** feed back into search ranking —
results stay ordered by similarity only. The feedback loop (retrieved → higher
relevance → retrieved) is benign at archival granularity but compounds if
wired into ranking. See `dev-docs/REVIEW_EPISTEMIC.md` §12.4.

**Cost note.** Adds k writes per search on top of the ~120 ms SurrealDB
enrichment floor documented in #14. After landing, re-run
`EPIMEMER_BENCH_URL=… make bench BENCH_N=1000,2000` and update
`dev-docs/BENCHMARKS.md` if search p50 moves materially.

**Failing test first.** In `tests/mcp/test_tools.py`, against the parametrized
`storage` fixture (both backends):
- `test_search_reinforces_returned_nodes` — ingest, capture a returned node's
  `relevance`/`last_reinforced`, search again; both must have increased, and
  non-returned nodes must be untouched.
- `test_search_reinforcement_disabled_at_zero_boost` — `boost=0.0` leaves
  signals byte-identical.

---

### Issue 36 — No `importance` dimension and no agent-facing upward path for it

**Status.** Open. Design: `dev-docs/REVIEW_EPISTEMIC.md` §12 (§12.1–12.2).
Independent of #35; **prerequisite for #37**.

**Symptom.** An agent that learns something making an existing node more
important has no way to record that. The only candidate field, `relevance`, is
owned by the decay clock — a judgment written there silently decays back out.

**Fix.** Three parts, one commit each is fine:

1. **Field.** `importance: float = Field(default=0.5, ge=0.0, le=1.0)` on
   `ValueSignal`. Excluded from `apply_decay` — importance never moves on the
   clock. `update`'s copy-the-signal behaviour (`mcp/tools.py:921`) already
   carries it forward. Pydantic's default covers rows written before the field
   existed (consistent with the "no retroactive repair" carry-over below).
2. **Tool.** `reinforce(node_id, reason, related_id=None)` in `mcp/tools.py` +
   MCP registration: bumps `importance += step × (1 − importance)` (config
   `importance_step`, suggest 0.25) and appends
   `{at, reason, related_id}` to a `reinforcements` list in the node's
   `metadata` — every bump leaves an auditable trace; there is **no raw
   setter**. Error on unknown `node_id` or unknown `related_id`.
3. **Ingest prior.** `store_decomposition` accepts an optional per-fact
   `importance` (default 0.5) as a prior; real judgment happens at reflect time
   (#37).

**Failing test first.**
- `tests/storage/test_storage_parity.py::test_value_signal_importance_round_trips`
  — a node stored with non-default `importance` reads back with it, both
  backends. (Serialization is the likely SurrealDB failure mode — this is the
  test that must fail first.)
- `tests/pipelines/…::test_decay_leaves_importance_untouched` — `apply_decay`
  moves `relevance`, never `importance`.
- `tests/mcp/test_tools.py::test_reinforce_bumps_and_records_provenance` —
  bump applied asymptotically; `metadata["reinforcements"]` records reason and
  `related_id`; unknown ids error.

---

### Issue 37 — No hygiene path for trivial facts: active nodes accumulate forever

**Status.** Open. Design: `dev-docs/REVIEW_EPISTEMIC.md` §12.3. **Depends on
#36** (importance field); #35 makes nomination meaningfully better but is not
a hard blocker. Build this as **one more arm of the existing review loop, not
a new subsystem**: nomination = candidate generation, resolution through
`reflect` → `apply_reflection`, human approval in-conversation — the
`pending_review` pattern with a different verdict. If the implementation
grows a parallel workflow, it has gone wrong.

**Symptom.** Principle 2 ("nothing is destroyed") has no counterweight:
trivial facts — small decisions, stale error records — stay active and
retrievable forever, diluting search. `archive` today only ever sees
SUPERSEDED/MERGED nodes >90 days old (`pipelines/reflection/archival.py`) and
is **export-only**: there is no status an active node can move to, so even a
node judged worthless cannot leave the active set.

**Fix.** Four parts; the status is the foundation and goes first.

1. **`NodeStatus.ARCHIVED`.** New enum member. Approved archival = export via
   the existing `archive_nodes` path **+** atomic status flip (one transaction,
   both backends — same shape as `supersede_by_existing_tx`). Existing
   `status = 'active'` filters (`query_nodes`, `vector_search`) then exclude
   archived nodes with no further query changes — verify, don't assume.
   `restore` flips back to ACTIVE.
2. **Nomination.** Pure function in `pipelines/reflection/archival.py`
   (mechanical, no LLM), priority-ordered per §12.3: superseded/merged with low
   `importance` first; then `evidence_stale` inferences; then active facts with
   `last_reinforced == created_at`, low importance, and zero knowledge-edge
   in-degree (exclude `NON_KNOWLEDGE_EDGE_TYPES`).
3. **Worklist + resolution.** `reflect` surfaces `archival_candidates`
   (pattern: `pending_review`); `apply_reflection(archivals=[...])` applies the
   human-approved set via part 1. Missing/already-archived ids skipped, same
   as `supersessions`.
4. **Inference follow-on.** After archiving facts, walk `derived_from`: an
   inference whose entire evidence set is archived/superseded joins the *next*
   `archival_candidates` list — flagged, never auto-archived.

**Failing test first.**
- `tests/storage/test_storage_parity.py::test_archived_nodes_excluded_from_queries`
  — a node flipped to ARCHIVED disappears from `query_nodes(status=ACTIVE)` and
  from `vector_search` results, both backends; restore brings it back.
- Parity: the export+flip transaction is atomic — a failure mid-batch leaves
  every node ACTIVE.
- `tests/pipelines/…::test_archival_nomination_ordering` — fixture graph with
  one node of each class; assert the priority order and that a reinforced or
  high-in-degree node is *not* nominated.
- `tests/mcp/test_tools.py` — `reflect` surfaces the worklist;
  `apply_reflection(archivals=…)` archives exactly the approved ids; an
  inference with fully-archived evidence appears in the next worklist.

---

### Issue 38 — `MockEmbeddingProvider` silently caps vectors at 32 dimensions while reporting the requested width

**Status.** Open. Small and self-contained. First flagged as a caveat under
#14's benchmark numbers ("arguably its own small bug") — promoting it to an
issue because it quietly distorts every mock-embedding measurement and test.

**Symptom.** `_deterministic_vector` (`epimemer/embeddings/mock.py:28`) takes
`digest[: self._dimension]` of a SHA-256 digest — 32 bytes — so any
`dimension > 32` yields 32-wide vectors while the `dimension` property reports
what was asked for. The provider is not even internally consistent: the
zero-norm branch returns `[0.0] * self._dimension` at the *requested* width.
Consequences: `scripts/bench.py` runs "mock-384" embeddings that are actually
32-wide, so every vector-scan cost in `dev-docs/BENCHMARKS.md` and #14 is
understated relative to a real 384-dim model; any test that trusts
`provider.dimension` to match the stored vectors is silently wrong for
widths > 32.

**Fix.** Stretch the hash instead of truncating it: concatenate
`sha256(f"{text}:{i}")` blocks for `i = 0, 1, 2, …` until `dimension` bytes are
available, then normalize. Determinism is preserved; vectors for
`dimension ≤ 32` change only if the block scheme replaces the plain digest —
keep `i = 0` as the unsuffixed digest if avoiding that churn matters (check
what breaks; snapshot-style tests may pin exact vectors).

**Consequence to plan for.** Fixing this changes what `make bench` measures —
vector width goes from 32 to a real 384. The recorded baselines in
`dev-docs/BENCHMARKS.md` were taken under the bug, so after the fix re-run
both backends and annotate the table (one line: numbers before this date were
32-wide). This is not optional; otherwise the next benchmark comparison
"regresses" mysteriously.

**Failing test first.**
`tests/embeddings/…::test_mock_embedding_width_matches_reported_dimension` —
for `dimension` in {8, 32, 384}: every vector from `embed` has length
`provider.dimension`, is unit-norm, and is deterministic across calls.
Fails today at 384 (length 32).

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

**#34–37 are the actionable ones.** #14/#16 are deferred by design with stated
triggers, and the performance work that was actionable is done: `reflect` went
cubic → quadratic, in-memory edge lookups are indexed, and SurrealDB's `search`
went quadratic → flat. `dev-docs/BENCHMARKS.md` has the data; #14 above has the
analysis.

What to pick up, and what has to be true first:

| Order | Work | Trigger |
|---|---|---|
| 1 | 34 (timepoint extraction) | Ready now. Settle the `write_batch_tx` atomicity question first, in its own commit |
| 2 | 35 (retrieval reinforcement) | Ready now; smallest, independent. Re-bench search after |
| 3 | 36 (importance + `reinforce`) | Ready now; independent of 35. The parity round-trip test is the one to write first |
| 4 | 37 (archival arm) | After 36 (needs `importance`). `NodeStatus.ARCHIVED` + atomic flip is the foundation commit |
| anytime | 38 (mock embedding width) | Independent of everything; pairs naturally with the re-bench steps in 35/14 since it changes what `make bench` measures |
| watch | reflect's O(F²) | **Not an issue yet — raise one when a real graph gets close.** It is the limiting operation on both backends (~7,400 nodes in-memory, ~3,200 on SurrealDB) and the only remaining cost that is genuine pairwise work rather than a fixable access pattern. Vectorizing `_cosine_similarity` buys a large constant factor, not an exponent |
| watch | 14 (enrichment N+1) | The ~120 ms floor under every SurrealDB `search` is now per-result enrichment round-trips. Nothing fails because of it, so it stays deferred — but it is what a batched edge fetch would attack |
| deferred | 16 | The server gains concurrent clients (the viz-read leg is closed by the hub; the fix is now scoped to `hub_client.py`) |
| deferred | 14 (rest) | Batched edge fetch + aggregate queries: a protocol change on both backends, and the `asyncio.gather` prong is blocked by #16 |
