# Epimemer — Known Issues

Living issue tracker. **Last review: 2026-07-23.**

The 2026-07-20 code-review sweep (issues 6–23) is complete: 16 resolved, 2
deferred by design (**14** and **16**, below). The resolved entries have been
**removed from this file** — their resolution lives in git history and the
merged code. Issue numbers are stable IDs; the gaps (6–13, 15, 17–23) are
deleted-resolved items, not missing work. New findings continue from **24**.

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

### Issue 24 — Visualizer silently serves a stale/empty graph after a reconnect — 🆕 OPEN

**Severity: medium (observability correctness — it actively misleads).** Found
2026-07-23 while viewing the live visualizer.

**Symptom.** The browser at `http://127.0.0.1:8765` shows an empty knowledge
graph and a graph dropdown listing only `default`, with the header reading
`MCP: default`, even though the MCP server the user is actually driving is on the
SurrealDB `memory` graph with data in it. Nothing in the UI or in tool responses
signals a problem — the graph just looks empty.

**Root cause — a stale process holds the viz port.**

1. Every `/mcp` reconnect (and every manual `python -m epimemer.mcp.server`)
   spawns a new server process; old ones are **not reliably reaped** (one
   session accumulated ~10). Confirm with `ps ax | grep '[e]pimemer.mcp.server'`.
2. The viz server binds a **fixed** port (`config.viz_port`, default 8765;
   `server.py:105`). Only the first process to bind wins.
3. A stray process launched **without `EPIMEMER_*` env** defaults to the
   in-memory backend and the `default` database — an empty store. If it grabbed
   `:8765` first, the browser is talking to *it*.
4. The process the user actually drives (surrealdb/`memory`) tries to bind
   `:8765`, fails, and `_run_viz` (`server.py:112-121`) catches the uvicorn
   `SystemExit` and logs only a **warning to `EPIMEMER_LOG_FILE`** ("port in
   use? Continuing without visualization"), then serves MCP without viz. The
   user never sees that log, and the stale server keeps answering on `:8765`.

**Verified 2026-07-23.** `lsof -nP -iTCP:8765 -sTCP:LISTEN` pointed at a python
PID whose `ps eww` showed no `EPIMEMER_*` env; `curl -s
http://127.0.0.1:8765/api/graphs` returned `{"graphs":["default"],
"active_graph":"default"}`. Cleared by `pkill -f 'epimemer.mcp.server'` then a
clean `/mcp` reconnect, after which the correct process bound `:8765` and served
`memory`.

**Why the suite misses it.** It is a multi-process / lifecycle + UX failure, not
a single-process behaviour. Adjacent to Issue 16 (both stem from viz/multi-graph
process-global assumptions) but distinct: this is about which *process* owns the
port and how silently the loser fails.

**Fix direction — independently pickable:**

1. **Make the mismatch self-evident in the UI (cheapest, highest value).**
   `api_graphs` (`ws_server.py:137-142`) returns only db names + active db; add
   the **backend kind** (in-memory vs surrealdb) and render it in the header, so
   `MCP: default (in-memory)` reads instantly as "wrong/empty server". Needs a
   backend label on the storage protocol (e.g. a `backend_name` property) or
   `type(storage).__name__`. Touches `ws_server.py`, `frontend/src/api.ts`, and
   the header render in `frontend/src/main.ts`; rebuild the bundle
   (`cd frontend && npm run build`) and commit `visualization/static/`.
2. **Make the bind conflict loud where the user looks.** In `_run_viz`
   (`server.py:112-121`) escalate to `logger.error` **and write to stderr**
   (Claude Code surfaces MCP-server stderr, unlike the log file) with an explicit
   message: "port 8765 already held by another epimemer server — your browser is
   NOT talking to this process; stop stray servers." Optionally add an
   `EPIMEMER_VIZ_STRICT` flag that fails startup instead of continuing.
3. **Startup pre-flight.** Before binding, probe `:viz_port`; if something already
   answers `/api/graphs`, log the conflict explicitly, naming the other server's
   active graph/backend. `server.py`.
4. **(Follow-up, heavier) single-instance hygiene.** A pidfile + "new server
   signals the old to release the port", or the harness reaping the old child on
   reconnect. Orphan accumulation is partly a Claude-Code reconnect behaviour
   epimemer can't fully control, so treat this as mitigation, not the core fix.

**Operational aid (do first — it's tiny):** a `make viz-doctor` /
`scripts/viz_doctor.sh` that prints running `epimemer.mcp.server` PIDs (with
their `EPIMEMER_*` env) and the PID owning `:8765`. Turns this 20-minute
diagnosis into one command.

**Tests.** The race itself is not unit-testable, but the pieces are:
`tests/visualization/test_ws_server.py::test_api_graphs_reports_backend` (the
backend label is present in the payload), and a `server.py` test that
monkeypatches `uvicorn.Server.serve` to raise `SystemExit` and asserts the
bind-failure path logs at ERROR / writes the explicit stderr message.

---

### Issue 25 — `stores_since_reflect` is process-local and resets on reconnect — 🆕 OPEN

**Severity: low (feature reliability).** Found 2026-07-23: two documents stored
across a `/mcp` reconnect both reported `stores_since_reflect: 1`.

**Symptom.** The auto-suggest-reflection signal (`reflect_suggested`, meant to
fire once `reflect_threshold` ingests accumulate — default 10, `config.py:34`)
effectively never triggers for a user who reconnects between ingests, even though
the persistent graph keeps growing.

**Root cause.** The counter lives in the server's in-memory lifespan context, not
in storage. `server.py:136` seeds `"stores_since_reflect": 0` per process;
`server.py:318` increments it on each `store_decomposition`; `server.py:627,638`
resets it to 0 on `reflect`. A new process (every reconnect / restart) starts
again at 0. So with a **persistent** graph the data survives but the "stores
since reflect" signal does not — the two are inconsistent.

**Decide the intended semantics first.** The docs frame it per-graph-lifetime
("auto-suggests after N ingestions"); the implementation is per-session. Pick
one:

- **Per-graph (matches the docs):** persist the counter — e.g. a small
  `reflect_state` marker in the active graph, updated on store and cleared on
  reflect, read back into the context on `connect`. Survives reconnects. Cost: a
  storage read on startup + a write per store.
- **Per-session (matches the code):** keep it, but document it and rename the
  surfaced semantics so it doesn't read as a lifetime count — then the docs
  ("auto-suggests after N ingestions") need correcting.

A cheaper alternative: base the suggestion on a persistent node-count delta since
the last reflect, avoiding a dedicated counter entirely.

**Test.** `tests/mcp/...::test_reflect_counter_survives_reconnect` — drive a
store through the tool, tear down and rebuild the server context against the
**same** storage, and assert the counter reflects the prior store (fails today:
resets to 0). Only meaningful once the semantics above are decided.

---

## Older carry-overs (open, low priority)

From the original live-graph walkthrough (issues 1–5, otherwise resolved or kept
by design — see git history of this file, commit `22fc874` and follow-ups):

- **Merge is Topic-only (wired path).** `merge_nodes` is type-agnostic but
  `apply_reflection merges` accepts Topics only. Extension to Facts/Inferences is
  under discussion (Inferences are meant to let competing derivations coexist).
- **No retroactive repair of old graphs.** Fixes apply to new operations;
  pre-existing graphs keep stale state until rebuilt. Accepted.

---

## Recommended order

| Order | Issue | Why |
|---|---|---|
| 1 | 24 | Silently shows the wrong/empty graph in the visualizer — actively misleading |
| 2 | 25 | Auto-reflect suggestion never fires across reconnects |
| deferred | 16 | Multi-graph concurrency — trigger: the server gains concurrent clients |
| deferred | 14 | Full-scan / N+1 — trigger: a large persistent graph makes latency felt |
