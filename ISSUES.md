# Epimemer — Known Issues

Living issue tracker. **Last review: 2026-07-28.**

Everything found so far is resolved except **14** and **16**, both deferred by
design and described below. Resolved entries are **removed from this file** —
their resolution lives in git history and the merged code. Issue numbers are
stable IDs; the gaps (6–13, 15, 17–25, 27, 30) are deleted-resolved items, not
missing work. **26**, **28** and **29** below are planned work (features/
enhancements, prioritized 2026-07-28), written so another agent can pick each up
cold. New findings continue from **31**.

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

### Issue 14 — Full-scan / N+1 query patterns (known scaling ceiling) — ⏸ DEFERRED (by design)

> **⏸ Deferred 2026-07-22.** Not a bug — a performance ceiling. Fine at current
> scale (in-memory, and small SurrealDB graphs). The fix is a real protocol
> change (aggregate/grouped query methods on `StorageBackend`, batched edge
> fetch, `asyncio.gather` on `search` enrichment) that should be driven by a
> measured need, not landed speculatively. The trigger to pick this up: a
> persistent SurrealDB graph large enough that `list_sources` / `reflect` /
> `search` latency is felt. The ceiling is now documented for users (SUMMARY.md
> *Scaling Limits*).
>
> **Update 2026-07-29 — the trigger now has a number (from #28).** First
> baselines in `dev-docs/BENCHMARKS.md`, on `mem://` with mocked embeddings (so
> a floor, on fast hardware, with no network):
>
> | Nodes | search p50 | `list_sources` | `reflect` |
> |---|---|---|---|
> | 100 | 2.9 ms | 4.6 ms | 25.7 ms |
> | 1,000 | 21.3 ms | 208 ms | 5,412 ms |
> | 10,000 | 212 ms | **18,066 ms** | **>19 min**, abandoned unfinished |
>
> `search` is linear and fine. `list_sources` is **quadratic** — ~45× then ~87×
> per 10× of data — and `reflect` is worse.
>
> **This changes the severity.** `EPIMEMER_TOOL_TIMEOUT_SECONDS` defaults to
> 30 s, and `list_sources` already burns 18 s of it at 10k nodes under the most
> favourable conditions measurable. On SurrealDB over a websocket — where every
> per-node edge fetch becomes a round-trip — `list_sources` and `reflect` will
> **exceed the timeout and fail**, not merely feel slow. So this stops being a
> "performance ceiling" and becomes a broken tool call somewhere below 10k
> nodes.
>
> Still deferred, because no real graph here is near that size yet, but the
> trigger is now concrete: **~10k nodes on `mem://`, materially fewer on
> SurrealDB.** The SurrealDB run is the measurement still missing
> (`EPIMEMER_BENCH_URL=ws://... make bench`) and would set the real number.

**Severity: performance (not a bug).** Fine in-memory; over websocket to
SurrealDB each item is a round-trip:

- `list_sources` / `list_relations` (`mcp/tools.py:571-617`): iterate **all**
  active nodes and fetch edges per node — O(N) queries per listing call.
- `gather_pending_review` (`review.py:114-130`): `review_labels` per active
  node — several edge queries each — on every `reflect`.
- `reflect` split/enrichment loops (`tools.py:952-977`): re-embed every
  topic's associated material on every reflect call.
- `search` enrichment: `frames_of` + `review_labels` per returned node.

**Fix direction (when it bites):** aggregate queries in the protocol
(`count edges grouped by dst for sourced_from`, `distinct labels+kind for
RELATED`), batched edge fetch for a set of node ids, and concurrency
(`asyncio.gather`) for the per-node enrichment in `search`. Until then this is
a documented ceiling (SUMMARY.md *Scaling Limits*) so nobody points a large
graph at it unwarned.

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

## Planned work (features & enhancements, prioritized 2026-07-28)

Not bugs — the next tranche of product work, tracked here at the user's request
so the workflow above (failing test first, scoped commits, delete on merge)
applies to them too. Ordered by priority. Cross-references: **26** is the
"visible counter" item in TODO.md; **28** is README → *Not yet built* →
*Benchmarking*.

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

### Issue 29 — `reflect` is invisible in the pipeline strip — 🆕 PLANNED

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

| Order | Issue | Why |
|---|---|---|
| 1 | 29 | Reflect in the pipeline strip — closes the observability gap the strip redesign created |
| deferred | 16 | Multi-graph concurrency — trigger: the server gains concurrent clients (viz-read leg closed by the hub; fix now scoped to `hub_client.py`) |
| deferred | 14 | Full-scan / N+1 — trigger measured (#28): ~10k nodes on `mem://`, fewer on SurrealDB, where it fails the tool timeout rather than just feeling slow |
