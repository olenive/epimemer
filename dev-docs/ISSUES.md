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

### A suppression has no retraction, so every wrong decline is permanent

🟡 **Open**, waiting on a real case.

**What breaks.** A sweep recomputed from current state that records no
declines re-offers what was already refused and cannot know it, so every
nominator here has a suppression index: the `assessed` edge for fact pairs,
`RelationVerdict` for label pairs (`RELATION_LABELS.md` §4.2), and the
`retention` journal row for single nodes. None of the three can be withdrawn. A
pair judged `distinct` in error never returns, however much later evidence says
it should.

**This is not the affirmative half.** A `one_claim` verdict is retractable, and
deliberately one way: `distinct` withdraws a standing `one_claim`, and nothing
re-asserts a withdrawn one, because wrongly withholding a corroboration count
is cheaper than wrongly inventing agreement. Suppression was left untouched by
that design on purpose. So the affirmative half is retractable and the
suppressive half is not, on every layer.

**The fix may legitimately differ per layer.** The affirmative half's asymmetry
is entirely a property of corroboration. Nothing corroborates on a relation
label: a wrong `synonymous` invents no support and a wrong `distinct` costs no
count, so neither failure mode exists there, and a symmetric retraction is a
live option for labels where it would be wrong for facts. Porting the fact
shape across unexamined would import a constraint with no justification. That
conclusion depends on label deprecation staying reversible (`RELATION_LABELS.md`
§5): ship an irreversible deprecation and the argument needs re-deriving.

**The single-node layer is the mildest.** A node kept for its own sake (the
`never_retrieved` nomination, whose verdict covers nothing) can never have a
later reason fail to be covered, so a wrong keep there is permanent exactly as
a wrong `distinct` is. It costs one idle node lingering in storage, permanence
in the safe direction. There is a residual case on the anchored branch too:
the same facts archived, restored and re-archived still match their old
anchors. A retraction primitive would answer all three layers at once, which is
the argument for building it in one place rather than three.

**Why it is quiet.** A wrong `distinct` is invisible precisely because
suppression works, and the largest real graph holds too few labels for a label
pair to be nominated. That is the corpus arguing, not the design. The refusal
for a repeated verdict points at this entry by name, so an agent that wants a
verdict revisited is told where the question lives instead of only *no*.

**Not recommended for building yet.** What it needs first is a case: a
suppression somebody actually wants undone. The retraction's shape should be
argued from the real instance rather than guessed at symmetrically.

---

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

### Parent synthesis gates an all-tag child set the way topic merge used to

🟡 **Open.** Parent synthesis calls `shared_metacontext_set` over its children, so a
stamped topic node created from a tag and a bare one as children land in
`parents_refused`.
Same shape as the merge fix: test whether every child `created_from_tag`, and let
an all-tag parent inherit the empty metacontext set. Left undone because no synthesis
over topic nodes created from tags has been wanted yet, and the gate refusing is
the safe
direction to be wrong in.

### Metacontext stamps on tag-named topic nodes survive a merge

🟡 **Open**, and the reason it matters is a sweep nobody has run yet. Topic nodes
created from tags
written before tags were exempt carry a `the-real` stamp from
`epimemer metacontexts declare`, which stamped every node it found holding
none; tag
topics written since carry none. `merge_nodes` migrates a stamped source's
`has_metacontext` edge onto the survivor, so a stamp outlives the node it was
on. Harmless while the merge gate exempts them, but it means stripping
the stamped originals leaves stamps behind on anything already merged. Either
drop metacontext edges from migration when every source was created from a tag,
or scope any
cleanup to survivors as well as originals.

### A tag name resolves only while its node is active, so a merge splits the tag

🔴 **Open, and the same defect has arrived twice.** `_tag_topic` and
`_resolve_node_reference` both resolve a tag through `get_node_by_content`, which
filters to ACTIVE. A tag whose node has been retired therefore resolves to
nothing, whatever retired it, and the next document carrying that name mints a
second topic node while `find_nodes(tagged_with_topic=...)` returns an empty list for
the old one.

Enrichment has done this (it supersedes the tag's topic node with an enriched
copy).
Topic merge can do it too: a merge retires its sources as `MERGED` and names
the survivor whatever the agent chose, so merging `design decisions` into
`design-decisions` strands the first name. Exempting tag-named topic nodes from
the merge
metacontext gate made such a merge easier to perform.

The fix is on the read side, and it closes both doors plus any rename written
later: resolve a `MERGED` or `CORRECTED` hit forward through `merged_into` or
`superseded_by` to the live successor. `TOPIC_DESCRIPTIONS.md` §2.2 carries the
reasoning and §6 has it as Stage 0.

### Tag names embed so alike that reflect can nominate two unrelated tags

🔴 **Open, and live on real graphs.** A topic node created from a tag is embedded
on its name
alone, so tags built from one template score as near-duplicates however
different their subjects. On one real graph, every pair above 0.75 cosine was
also above the 0.80 nomination bar (`pipelines/reflection/review.py`), and most
of them were `dev-session-<date>` pairs at 0.97 to 0.99: different days of
work, and a merge would fuse their topic nodes. The rest were one tag spelled two ways
and genuinely should merge. The bare names score the pairs that must not merge
higher than the pairs that should, so a reviewer working from the nomination
list has no signal to tell them apart.

The nominations are proposals rather than merges, so nothing is lost until a
run accepts one without checking the dates. `TOPIC_DESCRIPTIONS.md` proposes
the fix, embedding a description alongside the name, and its §1.2 carries the
measurements. Until that ships, treat any nominated pair of `dev-session` tags
as a false positive.

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
