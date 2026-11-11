# Known issues

A reference for maintainers: the open defects, each with the trigger or
decision it waits on.

---

## Working an issue

1. **Write the failing test first.** Each entry names the test module, suggested
   test names, and the assertions. The test must fail against current `main`
   for the reason described, then pass after the fix.
2. Fix the bug, scoped to the entry.
3. Run the whole suite: `uv run python -m pytest tests/ -q`, and
   `make test-integration` and `make test-postgres` when the change touches
   storage or concurrency (add `SURREAL_PORT=8123` or `PG_PORT=5434` if a
   target reports its port in use). The opt-in suites skip themselves unless
   they are pointed at a server, and pytest calls that a pass, so read the
   counts rather than the exit code.
4. **Delete the entry** once the fix is merged, moving anything durable into
   the document that states the design first.
5. One commit per entry, or per tightly coupled group, so each is reviewable.

Every entry is written to be picked up cold, by someone who has read none of
the conversation that produced it. An actionable entry carries all six:

| | |
|---|---|
| **What breaks** | The behaviour, not the code smell. Ideally reproduced: the call, and what came back. |
| **Why it matters** | What a caller gets wrong today. An entry that cannot say this belongs in `PROPOSED_FEATURES.md`. |
| **Files** | Module and function names, so nobody re-derives the search. Name the rule to reuse, not just the site to change. |
| **The decision, if any** | Stated as an open question with its options: never prejudged, never omitted. |
| **Guarding tests** | Module, names, assertions, and why they fail on `main`. Step 1 is unrunnable without this. |
| **Verify** | The commands, including `make test-integration` where storage is touched. |

Deleting an entry has one precondition beyond *merged*: nothing else may name
it as the primary record. A document saying "the full statement lives here"
means the entry is load-bearing and must be moved before it is dropped.

Two agents on this file at once: name the entry you are working in your commit
message, and check the **Files** rows before starting. Entries whose file sets
are disjoint are safe in parallel; entries sharing one are not. The `dev-docs/`
design documents are shared state.

---

## Open issues

### The judge picker cannot reach the user on a 2026-07-28 connection

⏸ **Deferred**, trigger stated below.

**What breaks.** The 2026-07-28 protocol forbids server-to-client requests.
The mcp SDK (2.3.0) installs `NotifyOnlyOutbound` (`mcp/server/connection.py`)
on that path, which refuses them by construction, and fastmcp's
`Context.elicit` (4.1.3) raises a `ToolError` on such a connection before
anything reaches the wire. `claim_agent` logs "no elicitation channel to the
user" and binds an approved judge unconfirmed (`description_confirmed: false`),
even when the seat changed, which is the case the picker exists for. Seen with
Claude Code 2.1.287 through 2.1.319.

**Why it matters.** The user's approval on a changed seat (a new model, client
or conversation) degrades silently to the agent's own report of who it is.

**Files.** `epimemer/mcp/server.py`: `_elicit_judge_name`, `_elicit_agent_id`,
and the `except Exception` fallbacks around their `ctx.elicit` calls, which
return `_NO_CHANNEL`; `tools.claim_agent` decides what `_NO_CHANNEL` means.

**The decision.** Keep binding unconfirmed (today's behaviour, designed for
clients without elicitation, and the only way writes work on Claude Code
2.1.287 through 2.1.319), or refuse to bind when the seat changed and no question can be put,
which blocks every write on that client? The era's own route for a question is
a multi-round-trip result: the tool returns an `InputRequiredResult` and reads
the answer from `ctx.input_responses` when the client calls again, which
fastmcp 4.1.3 supports and which works only where the client answers it.

**Guarding tests.** In `tests/mcp/test_judge_seat.py`, a test such as
`test_a_changed_seat_with_no_channel_to_the_user` using `_seated_ctx`, whose
`elicit` raises: claim, record a different model, claim again, and assert
whichever behaviour is chosen, either `status == "claimed"` with
`description_confirmed is False`, or a refusal naming the missing channel.
`tests/mcp/test_claim_agent.py::test_no_channel_still_binds_an_approved_id`
pins today's behaviour for an unchanged seat and must keep passing either way.

**Verify.** `uv run python -m pytest tests/ -q`.

**Trigger:** Claude Code answering `input_required` results on a 2026-07-28
connection, or a reader deciding the refusal is worth the outage.

---

### A decomposition can land after a re-cut deleted the passages it cites

⏸ **Deferred**, trigger stated below. The re-cut is `resegment`,
`SEGMENTATION.md` §5.2.

**What breaks.** `resegment` refuses while any node cites one of the
document's passages, and checks that inside its own transaction. The other
order is not guarded: `store_decomposition` validates its passage ids, then
awaits the embedding work, then writes through `write_batch_tx`. A re-cut of
the same document that commits inside that window is not refused, because
nothing cites the passages yet, and the decomposition's nodes then land
citing passages that no longer exist. It takes two agents on one document in
a shared graph.

**Smallest fix.** A check inside `write_batch_tx`, on every backend, that
every passage a node names as its source still exists, refusing the whole
batch otherwise. Facts cite passages; topics may cite a document or nothing,
so the check applies to ids that name a passage and leaves the rest alone.

**Guarding tests.** `test_write_batch_tx_refuses_a_node_citing_a_deleted_passage`
in `tests/storage/test_storage_parity.py` (`TestResegmentTx`) is written and
marked `xfail(strict=True)` against this entry, so it fails the suite the
moment the fix lands and the marker comes off with it. Still to write: a
two-instance test in `test_postgres_storage.py` with a re-cut committed
between the read and the write, the order its existing re-cut test does not
cover.

**Decision needed.** Whether the refusal names the passages or just the count.
`store_decomposition` cannot retry against the new cut, since the agent's
claims were extracted from the old passages, so it returns the refusal for the
agent to re-read and re-decompose.

**Trigger.** A shared graph where two agents ingest and re-cut the same
document, or the first node found whose source passage is missing.

### Edge collapse on a merge or correction keys only on source, target and type

🟡 **Open.** Found by the differential test
(`tests/storage/test_differential.py`), which drives the in-memory oracle and
each persistent backend through the same random history and compares the
exported bundles.

**What breaks.** When a merge or a correction re-points edges onto a survivor,
duplicates are collapsed by `(src, dst, type)` and nothing else, in every
backend (`_migrate_edges_inplace` in `memory.py` and its counterparts). Two
timelinks from one node to different timepoints of one timeline collapse to
one, so a link is lost; on seed 48 a retired timelink was kept and a live one
dropped; related edges that differ only in their label collapse the same way.
The key ignores `metadata`, the relation label, and whether the edge is
retired.

Three further differences are SurrealDB's alone, excluded from the test's menu
with a comment each: which duplicate survives a collapse (the others keep the
edge stored first, SurrealDB the first it reads; seed 9 step 261); on a
correction SurrealDB does not collapse at all (seed 37 step 55); and a
world-change that copies two edges in one step numbers them in read order, so
their ids come out swapped (seed 29 step 30). SQLite and Postgres match the
oracle on fifty seeds with those exclusions off.

**Smallest fix.** Decide the key first: at least `(src, dst, type, label)`,
with a retired edge never outranking a live one, and a timelink's timepoint
part of its identity. Then one rule in `pipelines/graph_construction/versioning.py`
that every backend's migration follows, and the SurrealDB migration brought to
the same order and the same collapse on correction. The differential test's
excluded operations go back into the menu as the proof.

**Decision needed.** The key. Whether `metadata` ever distinguishes two edges
worth keeping, or only the label and the timepoint do.

**Trigger.** A graph that merges nodes carrying several timelinks or several
labelled relations to one neighbour, which any graph built with the timeline
tools will be.

### FTS index backfill runs inside `connect()` with no progress reporting

⏸ **Deferred**, trigger stated below.

On SurrealDB, the one backend with full-text indexes, defining them backfills
every existing row the first time `_setup_schema` runs against a graph, inside
`connect()`, before anything else can happen, with nothing visible to the
user. `IF NOT EXISTS` means it happens exactly once per graph. The other
backends score the corpus with `storage/bm25.py` on every call and have no
index to build. `BENCHMARKS.md`, *What defining the full-text index costs,
once*, has the measurements. This is lifespan startup, not a tool call, so no
timeout fires; it looks like a hang.

**Trigger:** graphs approaching 10,000 nodes, where the first connect blocks
for more than 10 s unexplained. **Fix then:** build the index asynchronously
after connect (searches degrade to vector-only until it lands, which the
lexical arm's fallback makes graceful), or surface progress through logging or
the viz hub. Whoever picks it up decides between them; the async option must
not violate the rule that a schema that cannot be set up is a failed
connection.

---

### A past snapshot loses a merge survivor once the merge is reversed

⏸ **Deferred**, decision stated below.

`reverse_merge` deletes the survivor, the one deletion of a single node there
is. `graph_as_of` reads the nodes that exist now, so a snapshot taken at an
instant between the merge and its reversal shows neither the sources, which
were retired as merged for that spell, nor the survivor, which is gone. The
graph held the claim then, and the snapshot says it held nothing.

**Why it matters:** *what did we believe on date X* gives a wrong answer for
any claim merged before X and un-merged after it.

**Files:** `reverse_merge_tx` in each `epimemer/storage/` adapter;
`was_active_at` in `epimemer/core/types.py`; `graph_as_of` in
`epimemer/mcp/tools.py`.

**The decision:** keep the survivor as a retired node with a lifecycle episode
in place of deleting it, which changes the documented rule that a reversal
destroys it, or accept the gap and say so in the `graph_as_of` description.

**Guarding tests:** `tests/storage/test_storage_parity.py`, beside
`TestTheActiveSetAtAnInstant`: merge two facts, reverse the merge, and assert
that `query_nodes(at_time=<between the two>)` returns the survivor. It fails
on `main` because the survivor's row no longer exists.

**Verify:** `uv run python -m pytest tests/storage tests/mcp -q`, then
`make test-integration` and `make test-postgres`.

---

### A node reimported from an archive shows as active through its archived spell

⏸ **Deferred**, trigger stated below.

`apply_reflection(archivals=...)` exports a node and only then flips it to
archived, so the export holds the node as it was while active, with no
lifecycle episode for the archival. `restore(archive_data=...)` writes that
copy back when the node is missing from the graph. The node returns with no
record of the spell it spent archived, so `graph_as_of` and `query_changes`
report it as active throughout.

**Why it matters:** a transaction-time read is wrong for the archived spell.
It needs a node that is absent from the graph, which archival alone never
causes, so today it takes a rebuilt graph or a bundle restored elsewhere.

**Trigger:** the first time a cold-storage reimport is used on a graph whose
history is read back. **Fix then:** have the reimport append an episode from
the archival's journal row, or export the node after the flip.

**Files:** `restore` and the archivals block of `apply_reflection` in
`epimemer/mcp/tools.py`; `archive_nodes` in
`epimemer/pipelines/reflection/archival.py`.

**Guarding tests:** `tests/mcp/test_tools.py`, beside the restore tests:
archive a node, remove it from storage, restore it from `archive_data`, and
assert `graph_as_of` at an instant inside the archived spell leaves it out.

**Verify:** `uv run python -m pytest tests/mcp tests/pipelines -q`.

---

### `reflect` reads its counter before the call takes its turn

`memory_reflect` in `epimemer/mcp/server.py` calls `get_reflect_counter()`
before it enters `_run_with_timeout`, so the read happens outside the call's
turn over the active graph and before the wrong-graph check.

**Why it matters:** a `reflect` that names the wrong graph still reads the
active graph's counter before it is refused.

**Files:** `memory_reflect` in `epimemer/mcp/server.py`; move the read inside
the function handed to `_run_with_timeout`.

**Guarding tests:** `tests/mcp/test_tools.py`: call `reflect` with an
`expected_graph` that is not the active one, on a storage that records each
counter read, and assert none was made.

**Verify:** `uv run python -m pytest tests/mcp -q`.

---

### A write refused for its judge is missing from the tool log

The four refusals `_judge_for_write` builds in `epimemer/mcp/server.py` (wrong
graph, unknown token, changed seat, judge required) return before
`_run_with_timeout`, so they skip the tool log and the retrieval record.

**Why it matters:** the log is how a session that went wrong is read back, and
these refusals are among the calls it most needs to show.

**Files:** `_judge_for_write` and the 27 write tools that return its refusal,
in `epimemer/mcp/server.py`; log the refusal where it is built, or move the
gate inside the wrapper.

**Guarding tests:** `tests/mcp/test_require_judge.py`: refuse a write and
assert the tool log names the tool and the refusal.

**Verify:** `uv run python -m pytest tests/mcp -q`.

---

## Older carry-overs (open, low priority)

- **No retroactive repair of old graphs.** Fixes apply to new operations;
  pre-existing graphs keep stale state until rebuilt, except where a mechanical
  schema migration runs on open. One concrete instance: nodes written before
  confidence became optional carry a literal `0.5`, so those rows read as
  *rated ordinary* when nobody rated them. Absence means something only for
  nodes written since.
