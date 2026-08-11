# Epimemer — Known Issues

Living issue tracker. **Last review: 2026-08-11.**

Open: **16**, **46**, **48**. **16** stays deferred by design, **46** needs a
decision, and **48** is a defect that does not hurt yet. **Nothing open fails at
a size anyone is running.**

**46 was split and halved.** It covered `novelty` and `confidence` together
because both were uncomputed constants documented as measurements; that shared
symptom was the only thing they shared. **`novelty` is removed** — the entry is
gone, `REVIEW_EPISTEMIC.md` §12.1 has the reasoning, and the guard is
`test_a_node_stored_with_novelty_still_loads` in `tests/core/test_types.py`.
Worth carrying forward: **a field whose meaning depends on when it is measured
cannot be stored honestly.** `relevance` (#44) failed that test on operator
habit, `novelty` on arrival order. Ask it of a new score before adding one, and
prefer the read-time derivation — it needs no migration and cannot go stale.

**14 and 47 are both resolved**, and between them they took `reflect` on
SurrealDB from failing at ~2,200 nodes to a 30 s crossing around **26,000**, and
in-memory to ~320,000. #14 was the full-scan / N+1 entry that ran for months;
#47 was the Python pair loop it uncovered, worth a further **2.9–4.4× on
SurrealDB and 7.3–16.5× in-memory**. `BENCHMARKS.md` carries the numbers, the
three causes behind #14 — of which the batched reads it nominated were the
smallest — and what binds `reflect` now, which is payload rather than
round-trips or arithmetic. **44** (`relevance` was write-only) and **45** (a
merge reset both value clocks) are also resolved; see `REVIEW_EPISTEMIC.md`
§12.1.

**Three methods earned their keep** and are worth reusing:

- Asking **"what reads this?"** of one field found #44, #45 and #46.
- Reading the **query plan**, not just the call count, found the two largest
  costs in #14 step 4 — both single calls that scanned a whole table, which a
  round-trip counter rates as cheap.
- **Fitting an exponent over three sizes rather than two.** The two-point fit
  put `reflect` at 1.75–1.87 and its crossing near 7,000; three points put its
  successor at 0.99 in-memory. The short fit was reading fixed setup cost as
  curvature.

Resolved entries are **removed from this file** once merged — their resolution
lives in git history and the merged code. Issue numbers are stable IDs; the gaps
(1–15, 17–45, 47) are deleted-resolved items, not missing work, and code comments
citing a number no longer listed here are pointing at one of them. New findings
continue from **49**.

35–38 were the value model & graph hygiene plan
(`dev-docs/REVIEW_EPISTEMIC.md` §12, which records what the plan did not
anticipate) plus the mock-embedding width fix.

The performance work (issues 28, 31, 32, 33 and 39) is resolved and its entries
are gone. **`dev-docs/BENCHMARKS.md`** carries the state those fixes left the system
in and the conclusions still worth acting on, but not the runs themselves — it
describes where things stand, not how they got there, and superseded
measurements are deleted rather than kept. The blow-by-blow is in `git log`.
#48 below depends on the current numbers.

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

### Issue 48 — `get_node_by_content` scans the node table on every ingest — ▶ ACTIONABLE

Found by the same query-plan audit as #14 step 4, on the *write* path rather
than the read path.

`SELECT * FROM {table} WHERE content = $content AND status = $status LIMIT 1`
has no index on `content`, so the planner takes `idx_{table}_status` — which
matches every active row — and filters afterwards. Measured per call: **1.3 ms
at 400 facts, 2.1 at 1,200, 4.3 at 3,000**, linear in table size.

It is called during ingest to make a repeated source/tag name reuse one node, so
ingest is O(N) per node and O(N²) overall. **It does not hurt yet**: ingest is
flat to 2,000 nodes in `BENCHMARKS.md` because 4 ms against the rest of a
`store_decomposition` is nothing. It is filed because it is the same defect as
the embedding one, on a path whose cost currently hides it.

**The fix is not obvious and that is why this is an issue rather than a patch.**
An index on `content` means indexing full node text, which is heavy and may cost
more on the write side than it saves. Options worth measuring: index a hash of
the content instead; scope the lookup to the node types that actually use exact
-name upsert (source/tag topics) rather than all three tables; or accept it with
the measurement recorded.

**Failing test first** — a plan assertion is the honest guard here, in
`tests/storage/test_surrealdb_storage.py`: `EXPLAIN` the lookup and assert it
does not resolve through a status index. Behaviour is already covered by the
exact-name upsert tests, which must keep passing unchanged.

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

### Issue 46 — `confidence` is a constant that documentation describes as a measurement — ◆ NEEDS A DECISION

Found by the audit that resolved #44: having established that `relevance` was
written but never read, the obvious next question was whether its siblings were
any better off. They are worse in a different direction — **read, but never
written.**

> **Narrowed 2026-08-11.** This entry originally covered `novelty` as well.
> **`novelty` was removed** rather than decided; the two fields shared a symptom
> and nothing else, and bundling them was hiding that they had different answers.
> See `REVIEW_EPISTEMIC.md` §12.1 for the reasoning and the naming conclusion
> ("surprise", reserved for a caller-supplied signal). The short version: a
> stored novelty answers "unexpected relative to what the graph held *then*",
> which is arrival order frozen forever, while the useful question is against the
> graph as it stands — well-posed at read time, already answerable from
> `vector_search`, and needing no field. **`confidence` is not in that position**,
> which is why it is still here: corroboration accumulates rather than being
> relative to a moment, so its stored form is defensible.

Every node created by ingest gets `ValueSignal()` (`mcp/tools.py:313`), so
`confidence` is **always 0.5**, against documentation promising "how
well-supported by evidence; multiple independent sources increase confidence".

Nothing computes it. The only writer after creation is topic merge
(`merged_value_signal`, `core/types.py`), which takes the `max` over inputs that
are themselves the constant, so the result is the same constant again.
`store_decomposition` accepts an importance prior but nothing equivalent.

**One behavioural consequence.** `merge_similar_topics` reads `confidence` to
choose which description becomes primary (`topic_consolidation.py:164`). Since
every node ties at 0.5, the `>=` always takes `topic_a`, so the
"higher-confidence description wins" rule is really "whichever was passed first
wins". The merged content is a concatenation, so nothing is lost — but the
ordering is arbitrary while reading as if it were principled.

Not a failure. What makes it worth an entry is the same thing that made #44 one:
**the documentation describes a mechanism that does not exist**, so the next
person to reach for a "how well-supported is this?" signal finds one that is
documented, populated, rendered, and meaningless.

#### The decision (no code before it)

1. **Compute it.** "How well-supported" means counting corroborating edges,
   which is structural in-degree, which `knowledge_in_degree_for` already
   computes and archival already uses. Ask whether a second, *stored* copy of a
   value already derived on demand earns its keep — and note the trap that sank
   `novelty` does not apply here, since in-degree at read time and in-degree at
   write time differ by accumulation rather than by baseline.
2. **Accept it as a prior and say so.** Keep the field, let
   `store_decomposition` take it alongside `importance`, and rewrite the docs to
   describe a caller-supplied hint rather than a computed measurement.
3. **Remove it**, as #44 removed `relevance` and as `novelty` has now gone.
   Cheapest, but unlike either of those it has a live reader, so the merge
   primary-selection rule needs a replacement — content length, or `created_at`,
   either of which is at least honest about being arbitrary.

**Recommendation: (2).** It is the smallest change that makes the documentation
true, it keeps the door open for (1) later without another migration, and it
matches how `importance` already works — a judgment the calling agent supplies,
because the agent is the only party in the system that has read the material.

**Test once a direction is chosen.** For (2):
`tests/mcp/test_tools.py::TestStoreDecompositionValuePriors` — a decomposition
entry carrying `confidence` stores it; one omitting it keeps the documented
default; an out-of-range value is refused by the `ValueSignal` bounds rather
than silently clamped.

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

**Nothing here breaks, and the performance thread has run out.** `reflect` was
the only operation that failed inside a plausible graph size; #14 and #47 took
its crossing from ~2,200 nodes to ~26,000 on SurrealDB and ~320,000 in-memory.
What binds it now is the bytes moved to compare vectors — close to irreducible
without moving the comparison server-side or caching vectors across calls, both
of which are larger changes than either issue was, and neither worth making at
this crossing. **The next performance issue should come from a profile, not
from this list.**

**#48 is a defect that does not hurt yet** — an O(N) scan per ingested node,
invisible at today's sizes. Worth doing before the graph sizes that make it
visible, and worth measuring rather than patching: the obvious fix (index the
content) may cost more than it saves.

**#46 is blocked on a decision, not on effort**: nothing fails, but the docs
describe a measurement the code does not take, which is the same trap #44 was.
Its `novelty` half needed no decision in the end — the question "measured
against what, and does that survive being stored?" answered it.

**#16 stays deferred**, with its trigger stated. #14's step 3 was dropped rather
than deferred — batching beat gathering, so the prong #16 blocked is one nobody
wants.

Work that does not exist yet, as opposed to work that is wrong, lives in
`dev-docs/PROPOSED_FEATURES.md`.

What to pick up, and what has to be true first:

| Order | Work | Trigger |
|---|---|---|
| 1 | 48 (`get_node_by_content` scans per ingest) | **Ready now** but not urgent, and the fix needs measuring before it is chosen. Do it before graph sizes make it visible, not after |
| blocked | 46 (`confidence` is a constant) | Needs a decision between computing it, accepting it as a caller-supplied prior (recommended), or removing it — which now costs a replacement rule for merge primary-selection. Not a failure — the docs describe a mechanism that does not exist |
| deferred | 16 | The server gains concurrent clients (the viz-read leg is closed by the hub; the fix is now scoped to `hub_client.py`) |
