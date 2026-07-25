# Epimemer — Known Issues

Living issue tracker. **Last review: 2026-07-28.**

Everything found so far is resolved except **14** and **16**, both deferred by
design and described below. Resolved entries are **removed from this file** —
their resolution lives in git history and the merged code. Issue numbers are
stable IDs; the gaps (6–13, 15, 17–25) are deleted-resolved items, not missing
work. **26–30** below are planned work (features/enhancements, prioritized
2026-07-28), written so another agent can pick each up cold. New findings
continue from **31**.

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
applies to them too. Ordered by priority. Cross-references: **27** is the open
item in TODO.md ("visible counter" is **26**, "drill-down recall" is **27**);
**28** is README → *Not yet built* → *Benchmarking*.

### Issue 26 — Auto-reflect counter: make it visible and user-controllable — 🆕 PLANNED

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

1. **`graph_stats` reports it.** Add `stores_since_reflect`,
   `reflect_threshold`, `reflect_suggested` to the `graph_stats` result
   (`tools.graph_stats`; the tool wrapper at `mcp/server.py:1150-1163` already
   has `storage` and `config` in scope). This alone answers "how close am I to
   a suggested reflect?".
2. **Per-graph threshold override.** New small tool
   `configure_reflection(threshold: int | None)`: persists an override next to
   the counter (same per-graph marker record both backends already maintain —
   extend the storage protocol with get/set, implemented on **both** backends
   per the parity rule; no `hasattr` probing). `None` clears the override;
   effective threshold = override or config default. "Reflect sooner" is then
   just calling `reflect`; "delay" is raising the threshold. Resetting the
   counter without reflecting (true snooze) is deliberately **not** included —
   it would silently discard the signal.
3. **Viz badge.** Emit a small `ReflectCounterUpdated` graph event
   (`visualization/events.py`; fields: `count`, `threshold`) after bump
   (`server.py:327`) and reset (reflect path, `server.py:637-648`). Frontend:
   a header badge `reflect 7/10` for the selected session, amber once
   `count >= threshold` (`frontend/src/main.ts` + `types.ts`; rebuild and
   commit the static bundle).

**Tests first.** `tests/mcp/test_graph_stats.py::test_reports_reflect_counter`
(parameterized `storage` fixture); `test_configure_reflection_persists_override`
— set override, rebuild server context on the same storage, assert the
effective threshold survives (mirrors the #25 guard test);
`tests/visualization/`: counter event emitted on bump and reset.

---

### Issue 27 — Hierarchy-aware recall: make splits pay off at retrieval time — ✅ RESOLVED

> **✅ Resolved 2026-07-28.** Scope 1 and 2 built; scope 3 (hierarchy-aware
> ranking) deliberately **not** built — it was conditional on results looking
> noisy after 1+2, and they don't. Reopen only with a concrete query where a
> parent and child both rank and the parent is noise.
>
> - `search` annotates returned Topics with `parents` / `subtopics` as
>   `{id, content_preview}` (`_hierarchy_annotations`, `mcp/tools.py`). Topics
>   outside a hierarchy gain no keys; non-Topic nodes are untouched.
> - New `topic_tree(topic_id, depth=2)` tool (`mcp/tools.py` +
>   `mcp/server.py`): ancestors to the roots, descendants to `depth`, previews
>   only, with `has_more` on branches cut off by the limit so a truncation is
>   never read as a leaf. `get_ancestors` added to
>   `pipelines/reflection/topic_hierarchy.py`; `get_children` / `get_parents`
>   now have production callers for the first time.
>
> **Deviations from the plan below, both deliberate:** the annotation key is
> `parents` (plural), because SUBTOPIC_OF is a DAG and a topic can hold several
> parents — a singular `parent` would silently drop one. And the lookups are
> *deduplicated* rather than batched: the protocol has no multi-node edge fetch,
> and adding one is #14's work, not this issue's. Neighbour bodies are fetched
> once each across the result set and reuse nodes the result already carries, so
> a parent and its children returning together cost no extra fetches.
>
> Guarded by `tests/mcp/test_topic_hierarchy_recall.py` (11 tests, both
> backends): `test_annotates_parents_and_subtopics`,
> `test_annotations_carry_previews_not_full_content`,
> `test_unrelated_topics_carry_no_hierarchy_keys`,
> `test_non_topic_nodes_are_not_annotated`,
> `test_returns_ancestors_and_nested_descendants`,
> `test_ancestors_run_from_nearest_parent_to_root`,
> `test_depth_limits_descent_and_flags_more`,
> `test_returns_previews_not_material`, `test_rejects_depth_below_one`,
> `test_rejects_unknown_topic`, `test_rejects_non_topic_node`.

**Why.** `apply_reflection splits` builds a `SUBTOPIC_OF` DAG
(`core/types.py:55`; helpers in `pipelines/reflection/topic_hierarchy.py` —
parents/children/roots/cycle detection all exist;
`graph_construction/versioning.py:167-191` plans the edges), but retrieval
never uses it: `search` (hybrid retrieval + `query/graph_expansion.py`) treats
`subtopic_of` as just another edge. Splitting a bloated topic currently buys
nothing at recall time — the stated point of the feature (TODO.md, marked
PARTIAL) was drill-down without loading everything into context.

**Scope:**

1. **Search results expose the hierarchy.** In `search` enrichment, when a
   returned node is a Topic with `subtopic_of` neighbours, annotate it:
   `parent: {id, content_preview}` and `subtopics: [{id, content_preview}]`
   (previews ~100 chars — ids + previews only, never full material). The
   calling agent can then decide to drill rather than receiving everything.
2. **New tool `topic_tree(topic_id, depth=2)`.** Lazy subtree fetch built on
   the existing `topic_hierarchy` functions: returns the topic, its ancestors
   to the root, and descendants to `depth`, previews only. This is the
   drill-down primitive.
3. **(Stretch, separate commit) hierarchy-aware ranking.** When both a parent
   and its child match a query, prefer the child (more specific) and mention
   the parent in its annotation instead of returning both at full weight.
   Decide based on how noisy real results look after 1+2 — don't build it
   speculatively.

**Notes for the implementer.** Enrichment in `search` is per-node storage
round-trips — the same N+1 family as #14. Keep the new lookups batched per
result set (one `get_edges_*` pass over returned topic ids), not per-node
loops, so this doesn't deepen the #14 ceiling.

**Tests first.** `tests/mcp/test_search.py::test_search_annotates_hierarchy`
and `test_topic_tree_depth_and_previews` — build a 3-level hierarchy via the
public tools (`store_decomposition` + `apply_reflection splits`), assert
annotations/subtree shape on the parameterized `storage` fixture; cycle-safety
already guarded in `topic_hierarchy`, don't re-test it here.

---

### Issue 28 — Benchmark harness (arms the #14 trigger) — 🆕 PLANNED

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

### Issue 30 — Frontend has no test runner — 🆕 PLANNED

**Why.** `epimemer/visualization/frontend/src/` is 1,706 lines of TypeScript
across 8 modules with **zero tests** — no runner is installed (`package.json`
has no `test` script; nothing in the repo references vitest, jest or
playwright). The Python side has a parity-parameterized suite and a `make test`
target; the frontend has `tsc` type-checking only, which catches shape errors
and nothing about behaviour. Two planned items (26.3's reflect badge, 29's
reflect strip rendering) add to this pile, so the runner should land first.

The event-reduction logic is the part that actually warrants tests, and it is
already written in a testable shape — pure functions plus closure factories, no
DOM:

- `pipeline-store.ts` (175 lines): `emptyRunState`, `applyTokensUpdate`,
  `applyPipelineEvent`, `markStale` are pure state transitions;
  `createPipelineStore` is a factory over them.
- `events.ts` (169 lines): `createEventRouter` — per-session subscription
  routing and system-message dispatch.
- `api.ts` (44 lines): three `fetch` wrappers, testable against a stubbed
  `fetch`.

`graph-panel.ts`, `pipeline-strip.ts`, `pipeline-detail.ts` and `main.ts` are
DOM/Cytoscape rendering — **out of scope**; testing them needs jsdom and a
Cytoscape harness for little return. Draw the line at the logic modules.

**Scope.**

1. Add `vitest` as a devDependency (it reuses the existing `vite.config.ts`, so
   no second build config) and a `"test": "vitest run"` script. Node
   environment, not jsdom — the in-scope modules never touch the DOM.
2. `src/*.test.ts` beside each module under test, matching the codebase's
   functional style.
3. Wire it into `make test-frontend`, and note in the Makefile header that the
   default `make test` stays Python-only so the frontend toolchain is not a
   prerequisite for running the backend suite.

**Tests.** This issue *is* the tests; the test-first rule does not apply.
Minimum coverage to call it done:

- `applyPipelineEvent` over a full started → transition fired/completed →
  completed sequence, and the failure path (`pipeline_failed`), asserting
  status and per-transition state at each step.
- `applyPipelineEvent` on an out-of-order or unknown-transition event — assert
  it does not throw and leaves state coherent, since the hub gives no ordering
  guarantee across reconnects.
- `applyTokensUpdate` and `markStale` as isolated transitions.
- `createEventRouter`: an event for session A reaches only A's handler; system
  messages reach the system handler; unsubscribing stops delivery.

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
| 1 | 26.1 | `graph_stats` reports the counter — ~30 min, and it makes reflection pressure visible while working on 27 |
| ✅ | 27 | Hierarchy-aware recall — done (scope 1+2); ranking stretch deliberately skipped |
| 2 | 30 | Frontend test runner — before 26.3 and 29 add more untested TypeScript |
| 4 | 26.2, 26.3 | Threshold override (storage-protocol change, both backends) then the viz badge — the non-small half of 26 |
| 5 | 28 | Benchmark harness — turns #14's trigger from a complaint into a number |
| 6 | 29 | Reflect in the pipeline strip — closes the observability gap the strip redesign created |
| deferred | 16 | Multi-graph concurrency — trigger: the server gains concurrent clients (viz-read leg closed by the hub; fix now scoped to `hub_client.py`) |
| deferred | 14 | Full-scan / N+1 — trigger: a large persistent graph makes latency felt (measure with #28) |
