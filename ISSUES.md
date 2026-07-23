# Epimemer — Known Issues

Living issue tracker. Last full review: **2026-07-20** (code review + empirical
verification against embedded SurrealDB `mem://`; full test suite green at the
time — 452 passed, 4 skipped — which is exactly why the verified bugs below are
notable: the suite does not catch them).

**Workflow (required for every fix):**

1. **Write the failing test first.** Each issue below names the test module,
   suggested test name(s), and the assertions. The test must fail against
   current `main` for the reason described, then pass after the fix.
2. Fix the bug. Keep the fix scoped to the issue.
3. Run the whole suite: `uv run python -m pytest tests/ -q`.
4. Update this file: mark the issue ✅ RESOLVED with a one-line resolution note
   and the test name(s) that guard it.
5. One commit per issue (or per tightly-coupled group), so each is reviewable.

Storage-behaviour issues must be tested for **backend parity**: the same test
against `InMemoryStorage` and `SurrealDBStorage(url="mem://")` (see
`tests/storage/` for the existing pattern). Several bugs below exist precisely
because the backends diverge and only the in-memory one is exercised.

> **Backend parity is now structural (2026-07-20).** `tests/conftest.py` defines
> a `storage` fixture parameterized over both backends, so *every* test taking
> it runs twice. This replaced the per-file `InMemoryStorage()` fixtures in
> `test_tools.py`, `test_issues_reproduction.py`, `test_graph_construction.py`
> and `test_full_pipeline.py`, taking the suite from 521 to 657 tests (+1.6s
> total runtime).
>
> Verified that this is not decorative: reverting the Issue 6 fix now breaks
> **12 tests** across `test_tools`, `test_graph_construction` and
> `test_full_pipeline` that previously passed against the broken backend. The
> class of bug this file was written about can no longer hide.
>
> Backend-specific internals still belong in `test_memory_storage.py` /
> `test_surrealdb_storage.py`, which construct their own store.

---

## Resolved history (2026-06-25 → 2026-06-28, condensed)

Issues 1–5 from the original live-graph walkthrough are resolved or kept by
design; full analysis is in git history of this file (commit `22fc874` and
follow-ups).

- **1 — `update` created an unembedded (unsearchable) replacement** ✅ fixed:
  `supersede_node`/`merge_nodes` embed the replacement by construction.
- **2 — `vector_search` returned superseded nodes** ✅ fixed: both backends
  restrict vector search to ACTIVE nodes. (But see **Issue 7** — the *graph
  expansion* path still lacks this filter.)
- **3 — supersession orphaned the node's edges** ✅ fixed: non-history edges
  migrate onto the replacement in the same transaction.
- **4 — `superseded_by` lineage not traversable via `query_graph`** — kept by
  design (history edges are metadata, excluded from default traversal).
- **5 — `link` cannot target source-document nodes** — kept by design (`link`
  resolves epistemic nodes only; provenance edges are ingest-owned).

Regression coverage: `tests/mcp/test_issues_reproduction.py` plus
storage/versioning parity tests.

Still-open carry-overs from the old "Open items" list:

- **Merge is Topic-only (wired path).** `merge_nodes` is type-agnostic but
  `apply_reflection merges` accepts Topics only. Extension to Facts/Inferences
  under discussion (Inferences are meant to let competing derivations coexist).
- **No retroactive repair of old graphs.** Fixes apply to new operations;
  pre-existing graphs keep stale state until rebuilt. Accepted.

---

## Open issues (2026-07-20 review)

Ordered by recommended fix order. 6 → 11 are bugs (6, 7, 9 first — data loss,
retrieval correctness, security). 12 is a decided refactor (we ARE keeping
Petritype — see note there). 13–23 are correctness/robustness/doc items;
22–23 come from the post-merge review of Issue 12.

---

### Issue 6 — SurrealDB silently drops re-stores of existing records (data loss) — ✅ RESOLVED

**Severity: critical.** On the persistent backend, every code path that uses
`store_*` as "store or update" silently writes nothing. No error is raised.

> **✅ Resolved 2026-07-20.** All seven `store_*` methods in
> `surrealdb_adapter.py` now go through the `_upsert(table)` helper —
> `UPSERT {table} CONTENT $data WHERE uid = $uid`. Chosen over
> `UPSERT type::thing($tb, $uid)` (both verified working against `mem://`)
> because it **preserves the existing generated record id**: re-keying by `uid`
> would leave pre-existing graphs with two rows per node, and `get_node`'s
> `WHERE uid = $uid LIMIT 1` could then return the stale one — turning "no
> retroactive repair" (accepted) into active corruption (not). `store_*` is now
> documented as upsert-by-id on the `StorageBackend` protocol, with
> `write_batch_tx` documented as the deliberate insert-only exception.
> Verified: `apply_decay` against `mem://` now moves relevance 0.5 → 0.25;
> previously a silent no-op.
>
> Guarded by the new `tests/storage/test_storage_parity.py` (16 tests, each
> parameterized over both backends — 8 failed against pre-fix `main`).
> `test_add_timepoint_persists_on_storage` lives there rather than in
> `test_tools.py` as originally suggested, so it covers both backends for free.
> Note that tool-reported `timepoints_count` is computed from the in-memory
> object and read "2" even while the write was being dropped — the assertion
> that catches the bug re-reads through `query_timeline`.

**Verified repro** (embedded `mem://`, 2026-07-20):

```python
s = SurrealDBStorage(url="mem://"); await s.connect()
t = Topic(content="hello", source_id="s1")
await s.store_node(t)
t.value.relevance = 0.123
await s.store_node(t)                    # no error
(await s.get_node(t.id)).value.relevance  # -> 0.5 (unchanged!)

tl = Timeline(name="tl"); await s.store_timeline(tl)
tl2, tp = add_timepoint(tl, start=...)
await s.store_timeline(tl2)              # no error
len((await s.get_timeline(tl.id)).timepoints)  # -> 0 (timepoint lost!)
```

**Root cause.** `SurrealDBStorage.store_node` / `store_timeline` (and the other
`store_*`) use `INSERT INTO {table} $data` with a UNIQUE index on `uid`
(`surrealdb_adapter.py` `_setup_schema`). Re-inserting an existing `uid` is
**silently ignored** — not an error, not an update. `InMemoryStorage` upserts
(dict assignment), so the two backends diverge and the suite (which exercises
these paths in-memory) stays green. The underlying gap: the `StorageBackend`
protocol never specifies whether `store_*` is insert-only or upsert.

**Affected callers** (all currently no-ops on SurrealDB):

- `pipelines/reflection/value_decay.py:52` — `apply_decay` re-stores decayed
  nodes → **reflect's decay does nothing on SurrealDB**.
- `mcp/tools.py:1474` — `add_timeline_timepoint` re-stores the timeline →
  **`add_timepoint` loses every timepoint on SurrealDB** (and `create_timelink`
  then fails to find the timepoint).
- `pipelines/graph_construction/value_updates.py:29,54,74` — same pattern
  (currently dead code; see Issue 18).

**Failing tests to write first** (backend parity — parameterize over
`InMemoryStorage` and `SurrealDBStorage(url="mem://")`):

- `tests/storage/test_storage_parity.py::test_store_node_twice_updates_in_place`
  — store a Topic; mutate `value.relevance`; store again; `get_node` returns
  the new relevance AND a `count()` query / `query_nodes` shows exactly one row.
- `...::test_store_timeline_twice_updates_timepoints` — store timeline; add a
  timepoint via `pipelines.timeline.functions.add_timepoint`; store again;
  `get_timeline` returns 1 timepoint; still exactly one timeline row.
- MCP-level guard: `tests/mcp/test_tools.py::test_add_timepoint_persists_on_storage`
  already-style test but run against the SurrealDB `mem://` backend: call
  `add_timeline_timepoint` twice, assert `timepoints_count == 2` and
  `query_timeline` returns both.

**Fix.**

1. Document `store_*` as **upsert by id** in `storage/protocol.py` docstrings
   (this is what all callers assume).
2. Implement upsert in `SurrealDBStorage` for every `store_*` method. Options:
   `UPSERT` statement keyed on `uid`, or `UPDATE {table} CONTENT $data WHERE
   uid = $uid` followed by `INSERT` when zero rows matched. Verify the chosen
   SurrealQL against `mem://` in the tests — the silent-ignore behaviour above
   shows assumptions about SurrealDB statements must be empirically checked.
3. `write_batch_tx` explicitly documents "ids are assumed new" — that contract
   can stay insert-only; say so in its docstring for contrast.

---

### Issue 7 — Graph expansion resurfaces superseded/merged nodes — ✅ RESOLVED

**Severity: high.** Issue 2's fix covered the vector path only. The graph-hop
path has no status filter, so retired content comes back one hop away.

> **✅ Resolved 2026-07-20.** `expand_via_graph` now resolves the neighbour
> *before* keeping the edge and traverses only `NodeStatus.ACTIVE` nodes,
> dropping the edge along with the hidden node. The reorder was necessary: the
> edge used to be appended before `get_node` was called, so filtering the node
> alone would have left the edge dangling. This also fixes a latent case — an
> edge to a **missing** node (`get_node` → `None`) was previously still
> returned, and is now dropped on the same path. Seed nodes remain the caller's
> responsibility, as documented.
>
> Guarded by `tests/pipelines/test_query.py::test_graph_expansion_excludes_non_active_neighbors`,
> which asserts at both the pipeline layer and through `tools.query_graph`.

**Mechanism.** `expand_via_graph`
(`epimemer/pipelines/query/graph_expansion.py:58-64`) adds any neighbour that
`get_node` returns, regardless of `status`. Normally supersession migrates
edges off the old node, but `supersede_by_existing` **deliberately does not
migrate the loser's edges** (the winner carries its own evidence — see
`versioning.py::supersede_by_existing` docstring). Result: the retired node
keeps knowledge edges (`supports`, `derived_from`, …) to active nodes, and any
`search`/`query_graph` that reaches an active neighbour pulls the SUPERSEDED
node into results.

**Failing test first** —
`tests/pipelines/test_query.py::test_graph_expansion_excludes_non_active_neighbors`:

1. Create fact A with a `supports` edge A → inference I; facts A and B active.
2. `supersede_by_existing(A, B.id, storage)` (A keeps its `supports` edge by
   design).
3. `expand_via_graph(seed_nodes=[I], storage, hops=1)` — assert A is **not** in
   the returned nodes and the A→I edge is not in the returned edges.
4. Same assertion through the tool layer: `tools.query_graph(I.id, ...)` and
   `tools.search(...)` seeded to hit I.

**Fix.** In `expand_via_graph`, skip neighbours whose `status !=
NodeStatus.ACTIVE` (skip the edge too — an edge to a hidden node is dangling
noise for the caller). Keep it unconditional for now; if a "show retired"
mode is ever wanted it should be an explicit parameter.

---

### Issue 9 — SurrealQL injection via graph name in `delete_database` — ✅ RESOLVED

**Severity: high (security).** `surrealdb_adapter.py:139-141`:

```python
await self.db.query(f"REMOVE DATABASE IF EXISTS `{database}`;")
```

The name is interpolated into the query. `delete_graph` / `use_graph` are
agent-facing MCP tools taking arbitrary strings, so a name containing a
backtick breaks out of the quoting and executes attacker-chosen SurrealQL
(e.g. `` foo` ; REMOVE NAMESPACE epimemer; -- ``).

> **✅ Resolved 2026-07-20.** Shared `validate_graph_name`
> (`storage/protocol.py`, `^[A-Za-z0-9_-]{1,64}$`) applied in all three layers:
> `tools.use_graph`/`delete_graph` return `{"status": "invalid_name"}` so the
> agent gets a readable error, and both backends raise `ValueError` as defence
> in depth. `delete_database` keeps interpolation with a comment explaining why
> (SurrealQL takes the database name as an identifier, not a bindable value).
>
> **Correction to the analysis above: the exploit needs two steps, and is
> worse than described.** `delete_graph` returns `not_found` for any name it
> cannot see, so a hostile name never reaches the query directly. The working
> chain was `use_graph(hostile, confirm=True)` — SurrealDB creates a database
> on `use`, and the client library accepted the name — followed by
> `delete_graph(hostile, confirm=True)`, which then passed the existence check
> and interpolated. Verified against `mem://`: a bystander database `victim`
> was destroyed, and the hostile-named database *survived*, because the
> injected statement changed which database `REMOVE` targeted. Post-fix the
> same probe is rejected at step one and `victim` survives.
>
> Guarded by `tests/storage/test_surrealdb_storage.py::TestGraphNameInjection`
> (including a bystander-survives test), `TestGraphNameValidation` in the parity
> file (both backends agree on legality), and `TestGraphNameValidationTools` in
> `tests/mcp/test_tools.py`.

**Failing tests first** —
`tests/storage/test_surrealdb_storage.py::test_hostile_graph_names_rejected`:

- `delete_database('x`; REMOVE DATABASE `other')` and
  `switch_database("a;b")` raise `ValueError` (no query executed).
- After the attempted deletion with a hostile name, a pre-created database
  `other` still exists (`list_databases`).
- Tool level, `tests/mcp/test_tools.py`: `use_graph` / `delete_graph` with a
  hostile name return an error, and `list_graphs` is unchanged.

**Fix.** Validate names against `^[A-Za-z0-9_-]{1,64}$` in **both** layers:
the MCP tools (`tools.use_graph`, `tools.delete_graph`) for a friendly error,
and `SurrealDBStorage.switch_database` / `delete_database` as defence in depth
(raise `ValueError`). Apply the same check in `InMemoryStorage` so backends
agree on what a legal graph name is.

---

### Issue 8 — `sys.stdout` swap in `_run_net` is racy and (now) unnecessary — ✅ RESOLVED

**Severity: medium.** `mcp/tools.py:39-63` swaps `sys.stdout = sys.stderr`
around net execution to keep Petritype debug prints off MCP's stdio transport.
Two problems:

1. **Race:** with two overlapping tool calls, B saves A's redirected stdout as
   its "original"; when both finish, `sys.stdout` is left pointing at stderr
   permanently. It also mutates process-global state across `await` points.
2. **Obsolete:** Petritype's engine overhaul made `verbose=False` the default
   of `ExecutableGraphOperations.execute_graph`, so the engine no longer
   prints.

> **✅ Resolved 2026-07-20.** Redirect deleted; `_run_net` is now a plain
> two-branch dispatch. The docstring records *why* nothing suppresses stdout,
> so the hack does not get reintroduced.
>
> **Grep result (the precondition ISSUES.md asked for).** Petritype's engine
> does still contain `print(` calls — `executable_graph_components.py:1157,1178`
> — but both sit behind `if verbose:` and `execute_graph` defaults
> `verbose=False`, so they are unreachable on our path. The two *ungated*
> prints (`rustworkx_graph.py:86,92`) are reached only via
> `plotting/rustworkx_to_graphviz.py`, never from execution. No upstream
> Petritype bug to file. `epimemer/` itself contains no `print(` at all.
>
> Guarded by `tests/mcp/test_tools.py::TestRunNetStdout::test_run_net_does_not_touch_stdout`,
> which runs two `_run_net` calls concurrently through `asyncio.gather` (the
> transition awaits `asyncio.sleep(0)` so they interleave) and asserts both that
> `sys.stdout` is unchanged and that nothing was written to it. Pre-fix it fails
> on the identity assertion — the swap leaves `sys.stdout` pointing at stderr,
> exactly the leak described.

**Failing test first** —
`tests/mcp/test_tools.py::test_run_net_does_not_touch_stdout`: run two `_run_net`
calls concurrently via `asyncio.gather` (use a net with a transition that
`await asyncio.sleep(0)`s so they interleave), then assert `sys.stdout is` the
original object; additionally capture stdout (capsys) and assert net execution
wrote nothing to it.

**Fix.** Delete the redirect from `_run_net` (grep Petritype's execution path
for stray `print(` first to confirm; if any remain, that's a Petritype bug to
fix upstream, not something to paper over here). This is also subsumed by
Issue 12, but do the deletion independently — it's a two-line fix.

---

### Issue 10 — Timeline tools mis-parse timezone-aware datetimes — ✅ RESOLVED

**Severity: medium.** `mcp/server.py:1000-1001` (`add_timepoint`) and
`1039-1041` (`query_timeline`) parse with
`datetime.fromisoformat(x).replace(tzinfo=timezone.utc)`. `.replace` on an
offset-aware input **discards** the offset instead of converting —
`"2024-01-01T12:00:00+02:00"` becomes 12:00 UTC, not 10:00 UTC. A correct
helper already exists in the same file: `_parse_utc` (`server.py:27-37`, used
by `as_of`/`query_changes`), and the storage layer's temporal comparisons
explicitly rely on uniform UTC.

> **✅ Resolved 2026-07-20.** `memory_add_timepoint` and `memory_query_timeline`
> now use `_parse_utc` for `start`/`end` and `target`/`range_start`/`range_end`;
> the local `from datetime import ...` shims are gone. Swept the package
> afterwards: `fromisoformat` now appears exactly once, inside `_parse_utc`, so
> every ISO string entering the server funnels through one converter.
>
> **Testing note worth keeping.** The first version of the range test passed
> *before* the fix and was therefore worthless: it stored the timepoint with an
> offset and queried with offset bounds, so both values shifted by the same two
> hours and the errors cancelled. The committed test stores at an unambiguous
> `+00:00` and queries with offset-bearing bounds, isolating the bound parsing.
> A test for a timezone bug must vary only one side of the comparison.
>
> Guarded by `tests/mcp/test_e2e.py::test_add_timepoint_converts_offset_to_utc`
> and `::test_query_timeline_range_converts_offset_to_utc` (both fail pre-fix).

**Failing test first** — `tests/mcp/test_e2e.py` (parsing lives in the server
layer, so test through FastMCP `call_tool`):
`test_add_timepoint_converts_offset_to_utc` — create a timeline, add a
timepoint with `start="2024-01-01T12:00:00+02:00"`, `query_timeline`, assert
the stored start equals `2024-01-01T10:00:00+00:00`.

**Fix.** Use `_parse_utc` for `start`/`end` in `memory_add_timepoint` and
`target`/`range_start`/`range_end` in `memory_query_timeline`; delete the local
`from datetime import ...` shims in those two functions.

---

### Issue 11 — `InMemoryStorage` stores and returns live references — ✅ RESOLVED

**Severity: medium (test-fidelity hazard — it is *why* Issue 6 stayed
invisible).** `store_*` keeps the caller's object; `get_*`/`query_*` return the
stored objects. Any caller mutating a returned node silently mutates the store
(`value_decay.py` even relies on this: "For InMemoryStorage the node is already
mutated in place"). SurrealDB round-trips through serialization, so the same
code behaves differently per backend.

> **✅ Resolved 2026-07-20.** `_copy`/`_copy_all` (`model_copy(deep=True)`)
> applied at every storage boundary in `InMemoryStorage`, in both directions,
> for nodes, edges, documents, segments, embeddings, timelines and
> metacontexts. Internal mutators (`update_node_status`, `relabel_edges`,
> `_migrate_edges_inplace`) still operate on the stored objects via `self._g`
> and remain correct, as predicted.
>
> **The predicted test breakage did not happen — and the first pass was
> nonetheless incomplete.** After copying the simple boundary the suite stayed
> green, which was suspicious rather than reassuring. It was: the *compound*
> operations — `supersede_node_tx`, `supersede_by_existing_tx`,
> `merge_nodes_tx`, `write_batch_tx` — write to `self._g` directly and were
> still storing caller objects by reference. Those are now copied too, and
> `test_write_batch_tx_does_not_alias_caller_objects` /
> `test_supersede_tx_does_not_alias_caller_objects` were verified to fail
> without that second change. Anyone re-doing this work should note that
> `store_*` is not the only write path.
>
> Audited for the dangerous silent-breakage class — code mutating a fetched
> object *without* writing it back, which now no-ops — and found none outside
> the storage layer. `value_decay.py` was the one caller documented as relying
> on aliasing; it always did call `store_node`, so it was already correct, and
> its now-false comment ("the node is already mutated in place") is updated.
>
> Guarded by `tests/storage/test_memory_storage.py::TestStoreIsolation`
> (10 tests: both directions, every record type, plus the two compound paths).

**Failing tests first** — `tests/storage/test_memory_storage.py`:

- `test_mutating_returned_node_does_not_change_store` — store a node, `get_node`,
  mutate `content` and `value.relevance` on the returned object, `get_node`
  again → unchanged.
- `test_mutating_caller_object_after_store_does_not_change_store` — store, then
  mutate the original object → `get_node` unchanged.

**Fix.** `model_copy(deep=True)` on the way **in** (all `store_*`) and on the
way **out** (all getters/queries), for nodes, edges, documents, segments,
embeddings, timelines, metacontexts. Then audit internal code that relied on
shared identity: `update_node_status`, `relabel_edges`,
`_migrate_edges_inplace` operate on the *stored* objects via `self._g` so they
remain correct; `apply_decay` works because it calls `store_node` (which after
Issue 6 is defined as upsert). Expect a handful of tests to fail during this
change — those failures are the point: they mark code depending on aliasing.

---

### Issue 12 — Adopt the Petritype `Runner` (steps 1–2) — ✅ RESOLVED

> **✅ Resolved 2026-07-20** (steps 1 and 2; step 3 remains a separate design task).
>
> `execute_with_events` no longer runs its own firing loop. It publishes
> `PipelineStarted`, hands `Runner.run_to_completion` a single observer, and
> publishes `PipelineCompleted` with `graph.step_count` deltas. The observer
> (`pipeline_observer`) is the only code that knows the wire format; it detects
> a firing by `step_count` increasing and names it from `graph.last_fired`.
> `_run_net` now takes no `max_transitions` and drives the Runner on both
> paths — the event bus adds an observer and nothing else.
>
> **Input/output place names now come from the topology**, not
> `input_place_history` / `output_place_history`. The history is capped for
> memory and its retention is configurable, so it is not a dependable source;
> the arcs wired to a transition are. The two agree for any transition whose
> input arcs all carry a token when it fires, which is every net here.
>
> **The removed caps were not doing anything.** All four nets are acyclic and
> shorter than their cap: retrieval is exactly 3 transitions (so `max_transitions=3`
> *happened* to equal quiescence — one more transition and it would have silently
> truncated), semantic segmentation 3, paragraph split 1, edge creation 1. The
> caps were latent bugs, not safety: too low truncates the pipeline and returns
> a partial result with no error; high enough to be safe and it never fires.
>
> Guarding tests, all in `tests/pipelines/test_net_execution.py`:
> `TestRunsToQuiescence` (12-transition chain — above the old default of 10 —
> both with and without a bus, plus `test_both_paths_agree_on_the_result`
> proving observation is a tap not a valve), `TestEventsCoverEveryTransition`,
> and `TestEveryNetReachesQuiescence` (the watch item: one smoke test per net,
> run through `_run_net` with mock providers). The pre-existing
> `tests/visualization/test_instrumented_executor.py` assertions are unchanged
> and still pass — the ws event schema did not move.
>
> Verified load-bearing: dropping the observer from the `RunContext` fails
> `test_emits_pipeline_lifecycle_events`, `test_token_counts_updated_after_each_step`
> and `test_every_transition_is_reported`. Full suite 709 passed, 4 skipped.
>
> **Still carrying caps:** `orchestration_net.py:335` and `:349`
> (`execute_with_auto_reflect`, `max_transitions=10`). Left alone deliberately —
> that function is unused by `server.py` and is exactly what step 3 rewrites.

> **Review addendum (2026-07-20, post-merge review of `aeb65d8`).** The cap
> removal and both-paths-one-runner design are endorsed; the analysis that a
> truncating cap is either a latent bug or dead config is correct. The review
> makes the **new safety story** explicit, since it is now spread across three
> places and none of them said so:
>
> 1. **Termination** rests on every net being acyclic — true of all five.
>    **Now enforced (2026-07-21):** Petritype gained a construction-time
>    acyclicity check, and all five factories pass `expect_acyclic=True` to
>    `construct_graph`, so an accidental cycle fails at build time with its path
>    named instead of looping at run time
>    (`tests/pipelines/test_net_execution.py::TestNetsAreAcyclicByConstruction`).
>    If a deliberately cyclic net ever lands (reflect-until-stable, retry loops),
>    drop the flag on that net and bound it at run time with a **raising** fuse —
>    Petritype's `RunContext.error_after_n_firings` (raises `TooManyFiringsError`),
>    never a truncating cap. A budget is only a safety device if exceeding it is
>    an error.
> 2. **Runaway protection** is `_run_with_timeout` (`server.py:193`):
>    `asyncio.wait_for` at `tool_timeout_seconds` around **every** registered
>    tool (verified: all 29 route through it). Wall-clock strictly dominates a
>    transition count as a guard. Caveat: cancellation lands at await points —
>    a long synchronous transition body delays it.
> 3. **Cancellation safety** holds because the nets `_run_net` drives do not
>    write storage mid-net — writes happen afterwards via `write_batch_tx` — so
>    a timeout cannot leave partial writes. This invariant is load-bearing and
>    was previously undocumented; a future net that embeds a storage write in a
>    transition breaks it silently. Recorded here so it is at least findable.
>
> Two defects found in the new code are filed as **Issue 22** (the observer is
> silently sequential-only) and **Issue 23** (no failure path in
> `execute_with_events`). Engine-level items were recorded upstream in
> `../petritype/SUGGESTIONS.md`; **several have since landed in Petritype and
> Epimemer has adopted them (2026-07-21):** `last_fired` in the concurrent runner
> (which reframed Issue 22 — see its note), a construction-time acyclicity check
> (`expect_acyclic`, now on all nets), and a raising firing fuse
> (`error_after_n_firings`) replacing the old truncating `max_transitions`, which
> Petritype has deprecated. Still upstream-only: the per-step setup cost in the
> sequential loop.

**Severity: refactor / enabler.** **Decision: we are keeping Petritype** — the
goal is to later visualise exactly what Epimemer is doing, live. Petritype
recently gained exactly the machinery Epimemer hand-rolled:

- `petritype/runtime.py` (commit `2bc295c`, 2026-07-17): `Runner` +
  `RunContext` — sequential/concurrent execution to quiescence or indefinitely,
  **observers handed the live graph after every state change**, a typed command
  inbox (`Extend`/`SetTokens`/`SetParam`/`Enable`/`Disable`), thread offload.
- Engine overhaul (`c3c6149`): failure semantics
  (`restore_tokens_on_failure`, `TransitionFailedError`), `verbose=False`
  default.
- `guard` / `priority` fields (`f5a2b56`) replacing the deprecated
  `activation_function` — engine-enforced enabling conditions.
- First-class counters: `graph.last_fired`, `graph.fired_counts`,
  `graph.step_count`.

(Epimemer uses petritype as an **editable path dependency**, so all of this is
already the code we run against.)

**Scope, in order** (1 and 2 done; 3 outstanding):

1. ✅ **Replace `visualization/instrumented_executor.py::execute_with_events`**
   with `Runner.run_to_completion(RunContext(graph=..., observers=(publish,)))`.
   The observer publishes the same event stream (`PipelineStarted`,
   `TransitionFired`/`Completed`, `TokensUpdated`, `PipelineCompleted`) built
   from `graph.last_fired` / `fired_counts` / place token counts, instead of
   peeking `transition_history`. Test-first: the existing
   `tests/visualization/test_instrumented_executor.py` assertions on the event
   sequence are the contract — port them to the observer implementation before
   deleting the old loop, and keep them passing unchanged (the event schema is
   the interface the ws frontend consumes; it must not change).
2. ✅ **Replace `_run_net` in `mcp/tools.py`** with the Runner: runs to
   quiescence, so the per-net `max_transitions` magic numbers (3 for
   retrieval, 10 default) go away, along with the stdout hack (Issue 8). When
   an event bus is present, this is the same code path as (1) — one runner,
   observers optional — deleting the current two-branch dispatch.
3. **Later (design task, not this pass):** use `guard` on an orchestration-net
   transition to gate auto-reflect ("fire when `stores_since_reflect >=
   threshold`"), and run MCP requests *through* the orchestration net so the
   live visualization shows the whole system, not just sub-nets. Today
   `orchestration_net.py` is current but unused by `server.py`, and its
   `execute_with_auto_reflect` duplicates the server's `stores_since_reflect`
   counter — when wiring it up, keep exactly one counter.

**Watch item:** ✅ done — running against Petritype HEAD means engine changes
land silently, so `TestEveryNetReachesQuiescence` in
`tests/pipelines/test_net_execution.py` executes each net (paragraph split,
semantic similarity, edge_creation, retrieval, orchestration) end-to-end with
mock providers through `_run_net`. That class is the integration contract with
Petritype; keep a case in it for every new net.

---

### Issue 13 — Frame-scoped `search` filters *after* top-k (can miss or empty out) — ✅ RESOLVED

**Severity: medium.** `tools.search` (`mcp/tools.py:359-365`) runs vector
top-k first, then drops out-of-frame nodes. A frame-scoped query where the
frame's nodes rank below k returns fewer than k results — possibly zero — even
though relevant in-frame nodes exist.

> **✅ Resolved 2026-07-20.** Frame-scoped retrieval now goes through
> `_retrieve_frame_scoped`, which over-fetches: it runs the net with an inflated
> `k` (`k * 4`, capped at 200) and grows the fetch — doubling — until at least
> `k` in-frame nodes survive the filter, or the vector store returns fewer hits
> than asked for (exhausted), or the cap is reached. The frame check is batched:
> `_in_frame_nodes` gathers the per-node `frames_of` lookups with
> `asyncio.gather` instead of awaiting them one at a time (Issue 14's N+1 on this
> path). The non-scoped and `cross_frame` paths are unchanged — one net run.
>
> **Not truncated to `k`.** The result can exceed `k`, exactly as it already
> could via graph expansion; `k` is a seed/relevance budget, not an output cap.
> The stopping condition bounds the over-fetch instead. A storage-level frame
> filter is the eventual answer (Issue 14); this bounds the work until then.
>
> **Test-design note worth keeping.** The distractors that must outrank the
> in-frame node have to be in a *sibling* frame, not untagged: untagged nodes are
> base reality (`BASE_METACONTEXT_ID`) and are *always* in scope, so a test built
> from untagged distractors goes green after over-fetch for the wrong reason —
> it never exercises exclusion. The committed tests tag distractors with a second
> metacontext, so the top-k is genuinely all-excluded and the pre-fix result is
> empty.
>
> Guarded by `tests/mcp/test_tools.py::TestSearchFrameScopingBeyondTopK`
> (parameterized over both backends): `test_frame_scoped_search_reaches_beyond_top_k`
> (k sibling-frame distractors fill the top-k; the lower-ranked in-frame node
> still returns) and `test_frame_scoped_search_iterates_past_initial_overfetch`
> (15 distractors, past the initial k*4=12, so the fetch has to grow). Both fail
> pre-fix on both backends (verified: neutralizing the over-fetch turns all four
> red). The existing `TestSearchFrameScoping` base-inclusion/sibling-exclusion
> tests still pass unchanged.

**Failing test first** —
`tests/mcp/test_tools.py::test_frame_scoped_search_reaches_beyond_top_k`: with
the mock embedding provider, construct ≥ k off-frame nodes that outrank one
in-frame node for the query (mock vectors let you control cosine ranking);
`search(query, k=k, metacontext_id=frame)` must return the in-frame node.

**Fix.** Over-fetch and iterate: query vector search with an inflated k
(e.g. `k * 4`, capped), filter by frame, repeat with a larger k only if fewer
than k in-frame results and more candidates exist. Keep the frame check
batched (Issue 14's `frames_of` is one storage call per node — gather them
concurrently or batch-fetch the `has_metacontext` edges). A storage-level
frame filter is the eventual answer; don't block this fix on it.

---

### Issue 14 — Full-scan / N+1 query patterns (known scaling ceiling) — ⏸ DEFERRED (by design)

> **⏸ Deferred 2026-07-22.** Not a bug — a performance ceiling. Fine at current
> scale (in-memory, and small SurrealDB graphs). The fix is a real protocol
> change (aggregate/grouped query methods on `StorageBackend`, batched edge
> fetch, `asyncio.gather` on `search` enrichment) that should be driven by a
> measured need, not landed speculatively. The trigger to pick this up: a
> persistent SurrealDB graph large enough that `list_sources` / `reflect` /
> `search` latency is felt. The ceiling is now documented for users (SUMMARY.md
> *Scaling Limits*, added under Issue 17).

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
a documented ceiling — add a "scaling limits" note to SUMMARY.md (part of
Issue 17) so nobody points a large graph at it unwarned.

---

### Issue 15 — `hasattr` duck-typing instead of protocol methods — ✅ RESOLVED

> **✅ Resolved 2026-07-22.** Added `async def connect` / `async def close` to the
> `StorageBackend` protocol (a *Lifecycle* section), implemented them as no-ops on
> `InMemoryStorage`, and delegated them through `InstrumentedStorage` (the viz
> wrapper has explicit pass-throughs, no `__getattr__`, so the new methods had to
> be added there too — the server calls them unconditionally at viz startup).
> `server.py` now calls `await storage.connect()` / `await storage.close()`
> without a `hasattr` guard. Dropped the `hasattr(children[0], "source_id")` at
> the parent-synthesis site in `tools.py` — every node type carries `source_id`
> (Topic's is nullable, which the direct access preserves). Guarded by
> `test_inmemory_connect_close_are_noops` and
> `TestLifecyclePassThrough::test_connect_close_delegate_to_inner` (a spy proves
> the wrapper forwards rather than shadowing — load-bearing for SurrealDB, whose
> `connect` actually opens a socket). Left the one remaining `hasattr` in
> `logging/structured.py` (`hasattr(record, "structured_data")`): that is the
> idiomatic optional-attribute check on a stdlib `LogRecord`, not storage
> capability detection. Suite green (709 passed).

**Severity: low (style/robustness; explicit protocol flags are the house
preference).**

- `mcp/server.py:82-83` — `if hasattr(storage, "connect")`; `:142-143` —
  `if hasattr(storage, "close")`. Fix: add `async def connect(self) -> None`
  and `async def close(self) -> None` to `StorageBackend` protocol; implement
  as no-ops in `InMemoryStorage`; call unconditionally.
- `mcp/tools.py:1105` — `children[0].source_id if hasattr(children[0],
  "source_id")`: every `EpistemicNode` has `source_id`; drop the `hasattr`.

Test: the protocol conformance is exercised by existing storage tests once the
methods exist; add `test_inmemory_connect_close_are_noops`.

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
> clients (e.g. the multi-agent viz path in IMPLEMENTATION_PLAN, or an HTTP/SSE
> transport). Keeping open as the reminder, as the analysis below intends.

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

### Issue 17 — Documentation drift (docs describe a previous iteration) — ✅ RESOLVED

> **✅ Resolved 2026-07-22.** Swept every drifted doc against the code:
> - **INTEGRATION.md** made canonical: dropped the "pydantic-ai for decomposition"
>   claim (decomposition is agent-driven), `19 → 29` tools, added the 10 missing
>   tools to grouped tables, and de-qualified "Graph Management (SurrealDB
>   backends)" since both backends do multi-graph.
> - **SUMMARY.md**: replaced the 19-row table with a grouped listing that links to
>   INTEGRATION.md and deliberately states no count (so it can only drift in one
>   place); corrected the false `at_time` claim to `as_of`/`query_changes`;
>   "activation functions" → guard/priority (×2); added a Scaling Limits note
>   (Issue 14).
> - **README.md**: same table→group+link treatment; fixed the `graph_construction`
>   description (dropped deleted `value_updates`).
> - **DEVELOPER_GUIDE.md**: every debug example swept off the deleted
>   `execute_graph(max_transitions=N)` idiom to `Runner.step` /
>   `run_to_completion`; removed the Decomposition example (deleted modules) and
>   rewrote it as the agent-driven reality; rewrote the Reflection example around
>   the `reflect` tool (no `reflection_net`); fixed the orchestration example
>   (`decomp` arg gone, `segment` action, `ServerConfig` fields); `segment_text`
>   now shows the required `embedding_provider`; test-structure and
>   "Adding a Storage Backend" sections de-`supports_multi_graph`-ed. Ran the
>   rewritten segmentation + orchestration examples end-to-end to confirm.
> - **IMPLEMENTATION_PLAN.md**: `14 → 29` (link to INTEGRATION.md), added an
>   explicit *Pivot: decomposition is agent-driven* note, marked the Topic
>   Assignment table retired, fixed `at_time` and the "protocol already exists"
>   line.
> - **AGENTS.md** (= `CLAUDE.md`, a symlink): "auto-reflects after 10 ingestions"
>   → auto-*suggests* (the server sets `reflect_suggested`, it does not reflect on
>   its own); `llm_calls` line removed under Issue 20.
>
> Guarded by `tests/mcp/test_tools.py::test_tool_count_matches_integration_doc`,
> which counts `@mcp.tool` registrations and fails if INTEGRATION.md's stated
> count drifts. Suite green (707 passed).

**Severity: medium — the docs are the interface for agents.** The newer docs
(`REVIEW_EPISTEMIC.md`, `epimemer_prompts/DEFAULT.md`, `TODO.md`, this file)
are accurate. The older ones have drifted:

- **SUMMARY.md**
  - Tool table lists 19 tools; the server registers **29**. Missing:
    `supersede_by`, `check_conflicts`, `record_contradiction`,
    `record_variant`, `as_of`, `query_changes`, `find_nodes`, `list_sources`,
    `list_relations`, `graph_stats`.
  - "Both `search` and `query_graph` accept an optional `at_time` parameter"
    — **false**; temporal access is `as_of` / `query_changes`.
  - "Activation functions" passages → Petritype deprecated
    `activation_function`; the mechanism is now `guard` / `priority`.
  - Add the scaling-limits note (Issue 14).
- **INTEGRATION.md** — "19 tools" and its tool tables: same 10 missing.
- **DEVELOPER_GUIDE.md**
  - Examples import **deleted modules**: `epimemer.llm.mock`
    (`MockDecompositionProvider`), `epimemer.pipelines.decomposition
    .llm_decomposition`, `epimemer.pipelines.reflection.reflection_net`
    (only stale `.pyc` files remain).
  - Every execution example teaches
    `ExecutableGraphOperations.execute_graph(graph, max_transitions=N)` — the
    silently-truncating idiom Issue 12 removed from production. Sweep them to
    the Runner (`Runner.run_to_completion(RunContext(graph=...))`) or, for
    step-through debugging, `Runner.step` — which is the legitimate use of a
    cap-of-1.
  - Orchestration example passes a `decomp` argument `orchestration_net` no
    longer takes; `segment_text` example omits the required
    `embedding_provider`.
  - "Adding a New Storage Backend" documents a `supports_multi_graph` property
    that was removed (IMPLEMENTATION_PLAN itself records the removal).
  - Test-structure section lists `tests/llm/` which contains only
    `__init__.py`.
- **IMPLEMENTATION_PLAN.md** — "14 tools"; Phases 0/2 still describe the
  in-server LLM-decomposition path that was deleted when ingest went
  agent-driven. Record the pivot explicitly.
- **CLAUDE.md / AGENTS.md** (Memory System section) — "the system auto-reflects
  after 10 ingestions": it only *suggests* (`reflect_suggested` flag).
  `epimemer_prompts/DEFAULT.md` has the correct wording ("auto-suggests") —
  copy it.

**Recommendation:** keep **one** canonical tool table (INTEGRATION.md) and
make SUMMARY.md link to it, so the count can only drift in one place.
No failing test for docs; instead add a tiny doc-consistency test:
`tests/mcp/test_tools.py::test_tool_count_matches_integration_doc` that counts
`@mcp.tool` registrations and greps INTEGRATION.md for the stated count —
cheap and catches the next drift.

---

### Issue 18 — Dead code and deleted-module remnants — ✅ RESOLVED

**Severity: low (hygiene).**

> **✅ Resolved 2026-07-21.** Deleted `value_updates.py` and, with it, the
> `TestValueUpdates` class (7 methods → 14 parameterized tests; suite 717 → 703,
> a conscious removal). Verified first that the three functions were unused by
> `epimemer/` and referenced only inside that test class. Deleted the empty
> packages `epimemer/llm/`, `epimemer/pipelines/decomposition/` and the
> `tests/llm/` stub (all `__init__.py` were 0 bytes and none were imported
> anywhere), and removed the untracked `TRASH/`. Cleared the stale orphan
> `.pyc` for the deleted `reflection_net` and `value_updates` modules (`.pyc`
> are not git-tracked, so this is a local-artifact cleanup only). Full suite
> green afterwards. The DEVELOPER_GUIDE examples that import these now-gone
> modules are Issue 17's to fix.

- `epimemer/pipelines/graph_construction/value_updates.py` — all three
  functions unused (REVIEW_EPISTEMIC already flags
  `update_value_on_contradiction` as dead). They also carry the Issue-6
  `store_node`-as-update pattern. Decided: **delete**.
  Correction to the above: they are unused by `epimemer/`, but they are *not*
  untested — `TestValueUpdates` in `tests/pipelines/test_graph_construction.py`
  is 7 tests exercising this module, which surfaced only when reverting the
  Issue 6 fix made all 7 fail on the SurrealDB parameterization (they had been
  passing in-memory only). Deleting the module means deleting those 7 tests
  with it; that is the intent, but it should be a conscious removal rather than
  a surprise.
- `epimemer/llm/` and `epimemer/pipelines/decomposition/` are empty packages
  (only `__init__.py` + stale `__pycache__` from deleted modules). Delete the
  packages (and their `tests/llm/` stub) or leave a README stating planned
  use; today they just mislead (see DEVELOPER_GUIDE drift).
- `TRASH/` directory in the repo root — empty except `tmp`; remove or
  gitignore.
- Stale `__pycache__` entries (`reflection_net.cpython-314.pyc`, etc.) —
  cleaned automatically once the empty packages are resolved; consider
  `find . -name __pycache__ -prune -exec rm -rf {} +` in a clean step.

### Issue 19 — `None`-valued dict keys behaved differently per backend — ✅ RESOLVED

> **✅ Resolved 2026-07-20.** Both backends now normalize on write via
> `normalize_for_storage` (`storage/protocol.py`), so a None-valued key is
> dropped everywhere and absence is the single representation of "no
> information". `InMemoryStorage` routes all 19 write sites through `_store`
> (normalize, then deep-copy) while reads keep using `_copy`; `_serialize`
> applies `drop_none_values` explicitly, which is a no-op against SurrealDB but
> makes the contract visible at the boundary instead of an accident of the
> driver's encoding.
>
> **Correction to the analysis below: SurrealDB does have `NULL`.** It has two
> concepts where Python has one — `NULL` (key exists, value is nothing) and
> `NONE` (key does not exist) — and they are genuinely distinct: `NULL = NONE`
> is False, `WHERE v IS NULL` matches only an explicitly-nulled row. The defect
> was never a missing feature, it was the *mapping*: the Python driver encodes
> every `None` as `NONE`, and offers no way to express `NULL` for a
> parameterized value (it ships a `TAG_NONE` CBOR tag and no NULL counterpart;
> `NULL` is reachable only as a SurrealQL literal, which does not compose with
> the `CONTENT $data` form used throughout the adapter). Normalizing to absence
> was chosen over contorting the adapter to fake `NULL`, because in a free-form
> bag `{"note": None}` and `{}` carry the same meaning.
>
> Measured rules, which the normalizer mirrors exactly: a `None` **dict value**
> is dropped at any depth, including inside dicts nested in lists; a `None`
> **list element** is preserved, because arrays keep their positions and
> dropping one would shift every later index; `{}` and falsy values (`0`, `""`,
> `False`) are preserved.
>
> Guarded by `tests/storage/test_normalization.py` (the rules, in isolation) and
> `TestPayloadFidelity` in the parity suite (each backend obeys them). Verified
> load-bearing: reverting the in-memory half fails exactly the two
> `[memory]`-parameterized agreement tests. The `StorageBackend` docstring now
> states the round-trip contract and tells a new backend author to apply the
> normalization and to add themselves to the parity fixtures.

**Severity: low-medium (backend divergence).**

Storing a node with `metadata={"note": None}` and reading it back gives
`metadata == {}` on SurrealDB and `{"note": None}` in memory. The key is not
set-to-null, it is *absent*: `"note" in got.metadata` is False.

**Root cause is general, not specific to `metadata`.** SurrealDB does not store
a key whose value is `NONE`, at any level. Inspecting a stored row directly
confirms it — a `Topic` written with `source_id=None` has no `source_id` key at
all:

    stored keys: ['content', 'created_at', 'extraction_method', 'id',
                  'metadata', 'status', 'uid', 'value']

Declared model fields survive this **only because Pydantic refills the default
on read**: every nullable field in `core/types.py` is declared `= None`, so
absent-key → default-None → indistinguishable from a stored null. That is
load-bearing luck, not design. The day someone writes a field as
`x: str | None = "unknown"`, storing `None` will read back `"unknown"` on
SurrealDB and `None` in memory — silent corruption rather than a harmless
round trip. A field-default audit belongs with any fix here.

Free-form dicts have no schema to refill from, which is why `metadata` is where
the divergence becomes visible.

Nothing in `epimemer/` reads metadata keys back today (no `metadata[...]`, no
`in metadata`, no `.get` — the package only ever writes them and passes them to
the viz layer), so there is no live bug. But `metadata` is caller-supplied
through `tools.py:95` and `tools.py:665`, so an MCP client can put a `None` in
and observe backend-dependent results.

Queries already account for absence where it matters: `query_changes` filters
with `superseded_at != NONE` rather than a null comparison.

Found by probing payloads across both backends.

**Still open, deliberately not fixed here:** the field-default audit. Nullable
model fields round-trip only because every one of them is declared `= None` and
Pydantic refills that default. A field declared `x: str | None = "unknown"`
would read back `"unknown"` on SurrealDB and `None` in memory —
normalization does not help, since the key is absent from the row either way.
Nothing in `core/types.py` violates this today (checked: all six nullable fields
default to None). A validator or a test asserting the convention would make it
enforced rather than observed.

### Issue 20 — `_meta.llm_calls` is always zero — ✅ RESOLVED

**Severity: low (dead field / doc drift).**

> **✅ Resolved 2026-07-22.** Chose **removal** over a caller-reported count:
> `epimemer` makes no LLM calls of its own (decomposition/segmentation are
> agent-driven), so the field could only ever read 0 — a structurally-false
> "cost awareness" signal is worse than no signal, and nothing consumes it
> (`server.py` produced it, the structured log mirrored it, no frontend/MCP
> client reads it). Dropped `llm_calls` from `ResponseMeta` (`mcp/types.py`) and
> `ToolInvocationLog` (`logging/structured.py`), removed the pass-through in
> `server.py`, and pruned every doc mention (INTEGRATION.md `_meta` example,
> AGENTS.md/CLAUDE.md and `epimemer_prompts/DEFAULT.md` `_meta` lists). Guarded
> by `tests/mcp/test_response_meta.py` (field absent from the model and from the
> serialized `_meta`) and `test_logging.py::test_no_llm_calls_field`. If a real
> per-call cost signal is ever wanted it should be caller-reported and named for
> what it measures, not resurrected as this always-zero counter.

`llm_calls` is declared in `epimemer/mcp/types.py:16` and
`epimemer/logging/structured.py:23`, and read in `epimemer/mcp/server.py:179` —
but **never incremented anywhere in the package**. Every tool response reports
`llm_calls: 0`.

This matters because `CLAUDE.md` instructs the agent to surface it to the user
("Found 5 relevant nodes... N llm calls"), so the documented cost-awareness
signal is structurally false. Note that `epimemer` itself makes no LLM calls —
decomposition and segmentation are performed by the *calling* agent — so either
the field should be removed along with its `CLAUDE.md`/`INTEGRATION.md`
mentions, or the MCP tools should accept a caller-reported count. Fold into
Issue 17's documentation sweep.

### Issue 21 — The integration suite is opt-in and nothing signals it exists — ✅ RESOLVED

> **✅ Resolved 2026-07-22.** Added a `Makefile` with `make test` (default suite)
> and `make test-integration`, plus a *Testing* section in README.md that names
> the opt-in suite and explains why a bare `pytest` never runs it. The
> `test-integration` target does the whole dance: starts a throwaway
> `surrealdb/surrealdb:latest` container, **polls `/health` until it accepts
> connections** (so the suite can't silently skip on a slow start — the exact
> failure mode this issue is about), runs the four tests, and tears the container
> down via an `EXIT` trap even on failure. Ran it end-to-end: **4 passed**, and
> the container was removed afterward. No new tests (this is process/discoverability).

**Severity: low (process, not code).**

`tests/storage/test_surrealdb_integration.py` covers what `mem://` structurally
cannot — real ws:// connection and auth, and transaction atomicity under
genuine cross-connection concurrency. It is well-built and deliberately gated:
`_server_reachable()` attempts no connection when `EPIMEMER_SURREAL_WS_URL` is
unset, so the default path skips cleanly.

Verified working 2026-07-20 against `surrealdb/surrealdb:latest` in Docker —
**4 passed in 0.48s**:

    docker run -d --rm --name epimemer-surreal-it -p 8000:8000 \
      surrealdb/surrealdb:latest start --user root --pass root memory
    EPIMEMER_SURREAL_WS_URL=ws://localhost:8000/rpc uv run pytest \
      tests/storage/test_surrealdb_integration.py

The gap is discoverability, not coverage: the suite skips silently, so it could
break and stay broken indefinitely without anyone learning. Wanted: a documented
one-liner (Makefile target or README section) and, if CI ever gains a service
container, a scheduled run. No new tests needed.

> **Note on the parity/integration split.** These four tests are the *only*
> concurrency coverage in the project — the default suite is entirely
> sequential. That is a reasonable design (embedded `mem://` cannot model two
> connections), but it means "657 tests pass" says nothing about concurrent
> behaviour unless the integration suite is also run.

---

### Issue 22 — `pipeline_observer` is silently sequential-only — ✅ RESOLVED

**Severity: low today, a trap the day viz meets concurrency.** Found in the
post-merge review of Issue 12 (`aeb65d8`).

> **✅ Resolved 2026-07-21.** `pipeline_observer` now derives what fired by
> diffing `graph.fired_counts` against the previous snapshot held in its closure,
> and emits one Enabled/Fired/Completed triple per `+1` in the diff. It no longer
> reads `step_count`/`last_fired` at all. `fired_counts` is monotonic, never
> trimmed, and maintained by *both* execution modes, so the observer is now
> mode-proof: a concurrent batch that completes several transitions under one
> notification is fully reported, where the old code emitted only the single
> `last_fired`. Sequential behaviour is unchanged — one firing per notification
> means the diff is `+1` on one transition, so the ordered event stream stays
> identical (the existing `test_every_transition_is_reported` passes untouched).
> Within a batch, triples are ordered by net definition; concurrent completion
> order is only partial anyway.
>
> **Correction to the analysis below: the current engine *does* set `last_fired`
> in the concurrent path** (`runtime.py:_run_concurrent`, per completion). The
> real defect is the *batching*, not a missing `last_fired`: `asyncio.wait`
> returns several done tasks, the loop bumps `step_count`/`fired_counts` for each,
> then `_notify` runs **once** with `last_fired` naming only the last. Verified
> against a 4-wide parallel fan: `step_count` reaches 4 in one batch, but the old
> observer emitted a single `transition_fired`. So the consequence was not "events
> vanish entirely" but "all-but-one per batch are dropped" — no guard needed, the
> diff is the whole fix.
>
> Guarded by `tests/pipelines/test_net_execution.py::
> TestEventsCoverEveryTransition::test_events_cover_every_transition_in_concurrent_mode`,
> which drives a 4-wide independent fan in `CONCURRENT` mode and asserts all four
> `transition_fired` events appear (plus a `step_count == 4` sanity check that the
> fan really batched). Red against the old observer (emitted one).

`pipeline_observer` (`visualization/instrumented_executor.py:100-147`) detects
a firing by `step_count` increasing and names it from `graph.last_fired`. Both
assumptions hold **only** in `ExecutionMode.SEQUENTIAL`:

- Petritype's concurrent deposit loop (`runtime.py` `_run_concurrent`
  ~412–418) increments `step_count` and updates `fired_counts` but **never
  sets `last_fired`** — only `execute_graph` does
  (`executable_graph_components.py:1231`), and the concurrent path bypasses it.
- One notification can cover several completions (`asyncio.wait` batch), so
  `step_count` can jump by more than 1 while the observer emits at most one
  set of transition events.

Consequence: switch the `RunContext` to `CONCURRENT` — which the design docs'
"concurrency is pervasive" makes likely eventually — and transition events
vanish entirely (the `last_fired is not None` guard suppresses them), leaving
only `TokensUpdated`. Nothing errors; no current test fails, because every
test runs sequential.

**Failing test first** — `tests/pipelines/test_net_execution.py::
test_events_cover_every_transition_in_concurrent_mode`: subscribe to a bus,
drive the 12-transition chain via
`Runner.run_to_completion(RunContext(graph=..., mode=ExecutionMode.CONCURRENT,
observers=(pipeline_observer(bus, "chain"),)))`, assert the
`transition_fired` events name all twelve transitions. Fails today with zero
events.

**Fix.** Make the observer mode-proof by diffing `graph.fired_counts` (keep
the previous snapshot in the closure; emit one Enabled/Fired/Completed triple
per +1 in the diff). `fired_counts` is monotonic, never trimmed, and
maintained by both modes — the engine documents it as exactly this API. Batch
ordering is lost, but concurrent completion order is partial anyway.
Until then, an `assert ctx.mode is ExecutionMode.SEQUENTIAL`-style guard in
`execute_with_events` would at least make the coupling fail loudly. The
engine-side half (set `last_fired` in the concurrent loop) is
`../petritype/SUGGESTIONS.md` item 1.

---

### Issue 23 — `execute_with_events` has no failure path — ✅ RESOLVED

**Severity: medium (viz correctness on the unhappy path).** Found in the
post-merge review of Issue 12.

> **✅ Resolved 2026-07-21.** Added a `PipelineFailed` event (`pipeline_name`,
> `error`, `transitions_fired`, `duration_ms`) to `visualization/events.py` and
> the `PipelineEvent` union. `execute_with_events` now wraps the runner in
> try/except: on any exception it publishes `PipelineFailed` and re-raises, so
> the stream always ends on a terminal event and the caller
> (`_run_with_timeout`) still sees the error. `PipelineCompleted` stays
> success-only — the two are distinct so a consumer can tell a finished pipeline
> from a failed one.
>
> `transitions_fired` is exact: the runner mutates the graph in place, so
> `step_count` survives the raise, and `execute_graph` bumps `step_count` only
> *after* a successful firing (the raise at `executable_graph_components.py:1220`
> precedes the increment at `:1238`). So the raising transition is not counted —
> the two-transition test reports `1`.
>
> **Frontend consumer updated at source** (`frontend/src/types.ts` union +
> `pipeline-panel.ts` terminal handler that clears the running state and shows
> the error). `tsc --noEmit` is clean. The built bundle under
> `visualization/static/assets/` is *not* regenerated here — that needs a
> `npm run build` in `frontend/` and is left as a deliberate, separate ship step
> rather than committing a regenerated minified blob into a backend fix.
>
> Guarded by `tests/pipelines/test_net_execution.py::TestFailureTerminatesTheStream`:
> `test_failed_transition_still_terminates_the_event_stream` (bus path — exactly
> one `pipeline_failed`, no `pipeline_completed`, carries name/error, count `1`;
> verified red when the publish is removed) and
> `test_no_bus_path_propagates_without_swallowing` (the no-bus branch has no
> event to emit but must still raise).

`Runner.run_to_completion` is not wrapped
(`visualization/instrumented_executor.py:175`): a raising transition
(`TransitionFailedError`) exits after `PipelineStarted` was published and
`PipelineCompleted` never is. The frontend shows an eternally-running
pipeline. There is no `PipelineFailed` event class at all, and the failure
path has zero test coverage on either `_run_net` branch. (The no-bus branch is
fine as-is: the exception propagates to `_run_with_timeout`, which logs and
returns a clean error response.)

**Failing test first** — `tests/pipelines/test_net_execution.py::
test_failed_transition_still_terminates_the_event_stream`: a two-transition
chain whose second transition raises; run through `execute_with_events`;
assert (a) the exception propagates to the caller, and (b) the bus received a
terminal event (`pipeline_failed`) carrying the pipeline name and the error.
Fails today — the stream ends on a `TokensUpdated`.

**Fix.** Add a `PipelineFailed` event (`pipeline_name`, `error`,
`transitions_fired`, `duration_ms`) to `visualization/events.py`; wrap the
runner call in try/except, publish, re-raise. Frontend treats it as terminal
(clears the "running" state, shows the error). Keep `PipelineCompleted` for
success only — a shared "ended" event would lose the distinction the frontend
needs.

---

## Recommended order

| Order | Issue | Why first |
|---|---|---|
| 1 | 6 | Silent data loss on the persistent backend |
| 2 | 7 | Retired knowledge leaks into retrieval |
| 3 | 9 | Injection in an agent-facing tool |
| 4 | 10 | Wrong timestamps corrupt temporal queries |
| 5 | 8 | Two-line deletion; unblocks concurrency |
| 6 | 11 | Restores backend parity so tests mean something |
| 7 | 12 (steps 1–2) ✅ | Deletes bespoke executor code; enables live viz |
| 8 | 13 ✅ | Frame-scoped recall correctness |
| 9 | 23 ✅, 22 ✅ | Issue-12 review follow-ups; small, natural to fold into step 3 |
| 10 | 17 ✅, 18 ✅, 20 ✅, 21 ✅ | Docs + hygiene + process sweep in one pass |
| 11 | 15 ✅, 14 ⏸, 16 ⏸ | 15 done; 14 & 16 deferred by design (see their notes for the trigger) |
