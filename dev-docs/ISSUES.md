# Known issues

Live issues only. A resolved entry is deleted, not archived: what it taught
belongs in the document that states the design, and how it got fixed belongs in
`git log`. This file should shrink as the system improves.

Entries are named, not numbered. A reference like "#63" costs every reader a
lookup and goes stale the moment the entry is deleted, so nothing outside this
file should carry one. Name the thing instead, "the nomination bar that was two
numbers", and the reference survives the entry.

Where a fix produced a rule that outlives it, the rule lives in the design
document it belongs to: `VALIDITY_DESIGN.md` for temporal validity,
`RELATION_LABELS.md` for the vocabulary, `REVIEW_MODE.md` for the decision
journal and review, `REVIEW_EPISTEMIC.md` for the epistemic model,
`BENCHMARKS.md` for what has been measured, and `docs/` for behaviour a caller
sees.

---

## Working an issue

1. **Write the failing test first.** Each entry names the test module, suggested
   test names, and the assertions. The test must fail against current `main`
   for the reason described, then pass after the fix.
2. Fix the bug, scoped to the entry.
3. Run the whole suite: `uv run python -m pytest tests/ -q`, and
   `make test-integration` when the change touches storage or concurrency (add
   `SURREAL_PORT=8123` if the target reports 8000 in use). Both opt-in suites
   skip themselves when they cannot reach a server, and pytest calls that a
   pass, so read the counts rather than the exit code.
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
are disjoint are safe in parallel; entries sharing one are not, however
unrelated they read. The `dev-docs/` design documents are shared state.

Backend parity is structural. `tests/conftest.py` parameterises a `storage`
fixture over `InMemoryStorage` and `SurrealDBStorage(url="mem://")`, so every
test taking it runs against both. Storage-behaviour bugs must be tested that
way. Backend-specific internals belong in `test_memory_storage.py` and
`test_surrealdb_storage.py`, which construct their own store. Concurrency is
only exercised by the opt-in Docker suite; the default suite is sequential.

---

## Rules this file has taught

Each rule outlived the defect that produced it. The instance is there to make
the rule concrete, not to be looked up.

**Repair the class, not today's instances.** A status-to-opacity map in the
frontend fell through to *fully visible* for any status it did not list,
drawing retired nodes as live. The fix was a fallthrough default reading
*active or retired*, not two more keys, so a status added later cannot draw as
live.

**A rule stated for one branch of a conditional is not a rule the code
applies.** A world-change branch carried the argument; the correction branch
carried the same risk and no argument, four lines apart.

**A transaction taking a domain object must decide whether the argument is a
request or a snapshot, and say which.** A list of nodes to retire was a request
whose `lifecycle` field was being read as a snapshot.

**A correctness defect does not wait for a performance visit**, and a cost you
have not measured is a guess, including when it sounds structural.

**Confirm the constraint before designing around it.** Three exchanges of
architecture went into working around a licence that turned out to govern a
server binary this project never ships. The check took ten minutes.

**An enumeration of write paths in a design is a claim that ages.** A list of
three was correct when written and wrong two stages later, because a path it
assumed would be gone was not. The guard is a test that exercises every path.

**An issue is only independent of a decision if the decision cannot delete its
code path.** A review called one issue independent of every open question by
checking the mechanism outward without checking the consequence running back.

**When an issue is blocked on a precondition, check whether the precondition's
absence is the larger defect.** Asking what would make fact dedup safe produced
a precondition the model did not have, and the missing precondition mattered
far beyond dedup, having quietly broken three other things.

**A measured quantity is not yet a measured consequence.** Segment text was
measured crossing the embedding window before anyone checked whether segments
are ever embedded. They are not.

**An id nobody dereferences is not an identity.** Label ids were resolved with
a node lookup, so every decision row about a label read as *node not in this
graph*, silently, for two stages.

**A guard whose reach is an accident of where the code sat is one that fails
open.** Guards that must not miss a site parse the source and compare what they
find against a declared list, in both directions.

**A requirement that oscillates with unrelated state is worse than a strict
one.** A rule switched on only when a second graph existed would start
refusing writes because of state the agent never touched.

**Granularity is the logical operation.** A guard taken per query is not a
guard on the operation; the turn is taken at the tool boundary, where the
operation is.

**Ask what reads this** of any field. Three defects came from that one
question, each a value nothing consumed.

**Read the query plan, not the call count.** The two largest costs in one
optimisation pass were single calls that scanned a whole table, which a
round-trip counter rates as cheap.

**Fit an exponent over three sizes, not two.** A two-point fit read fixed setup
cost as curvature and put a crossing at a fifth of its real value.

**A measurement that says no is a real outcome.** More than one design here
was settled by a number that killed it, and the cheapest version of that is
measuring before building rather than after.

**A test suite that always builds a well-formed graph cannot see a defect whose
precondition is a graph nobody set up.** Running a migration against real data
catches a class the suite structurally cannot.

**A rule stated only in a comment is a rule the code does not have.** Every
engine edge type carried its shape in prose beside the enum, and `link` wrote
whichever pairing it was asked for, so readers each re-checked what the edge
type had already told them. The comments became `EDGE_SHAPES`, a total map with
a test over it, and the ones that only restated the table went.

**A field's home is part of its definition.** `confidence_basis` lives in
`node.metadata`, apart from `value.confidence`, on purpose: the basis is prose
about one judgment and `ValueSignal` is the numbers every ranker reads.
Querying `value.confidence_basis`, where it reads as though it belongs, returns
nothing and looks like a finding. Pinned in `tests/test_corpus_measure_smoke.py`.

---

## Open issues

### The judge picker cannot reach the user on a 2026-07-28 connection

⏸ **Deferred**, trigger stated below.

**What breaks.** On the 2026-07-28 protocol era the mcp SDK's stdio path
carries no server-to-client requests (`NotifyOnlyOutbound` in
`mcp/server/connection.py`), so `ctx.elicit` raises. `claim_agent` logs "no
elicitation channel to the user" and binds an approved judge unconfirmed
(`description_confirmed: false`), even when the seat changed, which is the case
the picker exists for. Seen with Claude Code 2.1.287 on 2 October 2026.

**Why it matters.** The user's approval on a changed seat (a new model, client
or conversation) degrades silently to the agent's own report of who it is.

**Files.** `epimemer/mcp/server.py`: `_elicit_judge_name`, `_elicit_agent_id`,
and the `except Exception` fallbacks around their `ctx.elicit` calls, which
return `_NO_CHANNEL`; `tools.claim_agent` decides what `_NO_CHANNEL` means.

**The decision.** Keep binding unconfirmed (today's behaviour, designed for
clients without elicitation, and the only way writes work on Claude Code
2.1.287), or refuse to bind when the seat changed and no question can be put,
which blocks every write on that client until the SDK carries server requests
on the modern path?

**Guarding tests.** In `tests/mcp/test_judge_seat.py`, a test such as
`test_a_changed_seat_with_no_channel_to_the_user` using `_seated_ctx`, whose
`elicit` raises: claim, record a different model, claim again, and assert
whichever behaviour is chosen, either `status == "claimed"` with
`description_confirmed is False`, or a refusal naming the missing channel.
`tests/mcp/test_claim_agent.py::test_no_channel_still_binds_an_approved_id`
pins today's behaviour for an unchanged seat and must keep passing either way.

**Verify.** `uv run python -m pytest tests/ -q`.

**Trigger:** the mcp SDK or Claude Code offering server-to-client requests on a
2026-07-28 stdio connection, or a reader deciding the refusal is worth the
outage.

---

### A decomposition can land after a re-cut deleted the passages it cites

⏸ **Deferred**, trigger stated below. Found on 2026-10-08 while building the
segmentation stages (`SEGMENTATION.md` §5.2).

**What breaks.** `resegment` refuses while any node cites one of the
document's passages, and checks that inside its own transaction. The other
order is not guarded: `store_decomposition` validates its passage ids, then
awaits the embedding work, then writes through `write_batch_tx`. A re-cut of
the same document that commits inside that window is not refused, because
nothing cites the passages yet, and the decomposition's nodes then land
citing passages that no longer exist. One agent cannot do this to itself; it
takes two agents on one document in a shared graph.

**Smallest fix.** A check inside `write_batch_tx`, on every backend, that
every passage a node names as its source still exists, refusing the whole
batch otherwise. Facts cite passages; topics may cite a document or nothing,
so the check applies to ids that name a passage and leaves the rest alone.
`tests/storage/test_storage_parity.py`: `test_write_batch_tx_refuses_a_node_citing_a_deleted_passage`,
and a two-instance test in `test_postgres_storage.py` with a re-cut committed
between the read and the write.

**Decision needed.** Whether the refusal names the passages or just the count,
and whether `store_decomposition` retries against the new cut (it cannot: the
agent's claims were extracted from the old passages) or returns the refusal
for the agent to re-read and re-decompose. The second is the only honest one.

**Trigger.** A shared graph where two agents ingest and re-cut the same
document, or the first node found whose source passage is missing.

### Edge collapse on a merge or correction keys only on source, target and type

🟡 **Open.** Found on 2026-10-08 by the differential test
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
tools will be; or the first release after this one, whichever comes first.

### FTS index backfill runs inside `connect()` with no progress reporting

⏸ **Deferred**, trigger stated below.

Defining the full-text indexes backfills every existing row the first time
`_setup_schema` runs against a graph, inside `connect()`, before anything else
can happen, with nothing visible to the user. `IF NOT EXISTS` means it happens
exactly once per graph.

Measured (`LEXICAL_SEARCH.md` §5, SurrealDB 3.0.5, median of 3, documents =
nodes + segments): 2,000 in 1.0 s; 6,000 in 3.8 s; 20,000 in 19 s.
Steady-state connect stays around 30 ms. This is lifespan startup, not a tool
call, so no timeout fires; it just looks like a hang.

Deferred because nothing hurts at current graph sizes, and the next
performance fix should come from something real.

**Trigger:** graphs approaching 10,000 nodes, where the first connect blocks
for more than 10 s unexplained. **Fix then:** build the index asynchronously
after connect (searches degrade to vector-only until it lands, which the
lexical arm's fallback makes graceful), or surface progress through logging or
the viz hub. Whoever picks it up decides between them; the async option must
not violate the rule that a schema that cannot be set up is a failed
connection.

---

### The in-memory store cannot persist, so every local use needs a server

🟡 **Open, deprioritised.**

**Update, 2026-10-08.** The addition this entry left open is being built: a
SQLite backend, one file per graph, designed in `SQLITE_BACKEND.md`. The
in-memory store itself stays as it is, for the reason below.

**What breaks.** `InMemoryStorage` has no save or load path; state lives in
`_GraphStore` for exactly as long as the process does. So SurrealDB is the only
persistent backend. That is fine for a long-running server and awkward for
anything that runs as a command and exits (a CLI, a CI step, a developer tool):
it either re-ingests its corpus on every invocation, at one model call per
document every time, or stands up a database to avoid it.

**Why it is deprioritised.** Embedded SurrealDB (`surrealkv://`, `file://`)
already runs in-process with no server: about 25 ms to reopen an existing
store, 1 ms for a count, and 0.85 s to ingest 725 nodes with embeddings
against in-memory's 0.029 s. A licence argument for avoiding SurrealDB was
checked and rejected: the Business Source License covers the SurrealDB server
binary, which is not a Python package and is not shipped; the `surrealdb`
Python package is Apache-2.0 and nothing in the installed dependency set is
source-available. (Package metadata is not a legal opinion; the packages with
no metadata and the Rust engine bundled in the wheel would each want a real
check before anything is handed over.) The dependency that actually weighs is
`torch`, at 385 MB via `sentence-transformers`: if an embeddable Epimemer ever
meets a size constraint, the answer is a remote embedding endpoint or an ONNX
runtime, not a storage backend.

What is left is a convenience for command-line tools, worth having and not
worth prioritising over designed work. SQLite remains attractive on its own
merits (`sqlite3` is stdlib, so no new dependency) as an addition rather than
an escape.

**Smallest fix: serialise `_GraphStore` and reload it.** Persistence for an
existing backend, not a new backend, so the full-protocol rule does not apply.

**Decisions first.**

1. **What is saved.** Nodes, edges and lifecycle records; embeddings with
   them, since they are expensive to recompute. There is no BM25 index to
   save: `memory.py` scores the corpus on every call.
2. **When it writes.** A snapshot on clean shutdown loses everything on a
   crash; writing on every mutation costs the speed that makes the in-memory
   store worth having. The choice should be stated rather than defaulted into.
3. **Format.** Measured on a synthetic corpus at the real embedding width
   (384): floats as JSON text are not viable (a gigabyte and eleven seconds at
   100,000 nodes, for 153 MB of float32), and the parts want opposite
   treatment, since node and edge records compress by an order of magnitude
   while vectors are near-incompressible. So: one zip container, structural
   records deflated, vectors stored uncompressed as a `.npy` member. One file
   cannot desync the way a sidecar pair can, `zipfile` sets compression per
   member, and nothing beyond the standard library and numpy is needed. A
   version field in the container, because a stale snapshot silently loading
   into a newer type is the failure hardest to notice.

---

## Older carry-overs (open, low priority)

- **No retroactive repair of old graphs.** Fixes apply to new operations;
  pre-existing graphs keep stale state until rebuilt, except where a mechanical
  schema migration runs on open. One concrete instance: nodes written before
  confidence became optional carry a literal `0.5`, so those rows read as
  *rated ordinary* when nobody rated them. Absence means something only for
  nodes written since.

- **There is no path for source discredit.** When a document turns out
  fabricated, every prior derived from it overstates and nothing can sweep per
  source, because support levels live on the node rather than on the
  `sourced_from` edge. Accepted and recorded rather than built; the provenance
  edge is where it would go (`PROPOSED_FEATURES.md`, *Per-source support
  levels*).

- **`confidence_basis` is guidance, not enforced.** Measured over both real
  graphs, every rated node carried one and no post-guidance node sat at a rated
  `0.5`, so the refusal-at-the-boundary fallback stays unbuilt.

---

## What to pick up next

Nothing here breaks. Every open entry is waiting on a trigger rather than on
work, and the triggers are stated in each.

The performance thread has run out. `reflect` was the only operation that
failed inside a plausible graph size; two rounds of work took its crossing from
about 2,200 nodes to about 26,000 on SurrealDB and 320,000 in memory. What
binds it now is the bytes moved to compare vectors, close to irreducible
without moving the comparison server-side or caching vectors across calls, both
larger changes than the ones that got it here. The next performance issue
should come from a profile, not from this file.

Work that does not exist yet, as opposed to work that is wrong, lives in
`PROPOSED_FEATURES.md`.
