# Epimemer — Known Issues

Live issues only. **A resolved entry is deleted**, not archived: what it taught
belongs in the document that states the design, and how it got fixed belongs in
`git log`. This file should shrink as the system improves.

**Entries are named, not numbered.** A reference like *"#63"* costs every reader
a lookup and goes stale the moment the entry is deleted, so nothing outside this
file should carry one. Name the thing instead — *"the nomination bar that was
two numbers"* — and the reference survives the entry.

Where a fix produced a rule that outlives it, the rule lives in the design
document it belongs to: `dev-docs/VALIDITY_DESIGN.md` for temporal validity,
`dev-docs/RELATION_LABELS.md` for the vocabulary, `dev-docs/REVIEW_MODE.md` for
the decision journal and review, `dev-docs/REVIEW_EPISTEMIC.md` for the
epistemic model, `dev-docs/BENCHMARKS.md` for what has been measured, and
`docs/` for behaviour a caller sees.

---

## Working an issue

1. **Write the failing test first.** Each entry names the test module, suggested
   test names, and the assertions. The test must fail against current `main` for
   the reason described, then pass after the fix.
2. Fix the bug, scoped to the entry.
3. Run the whole suite: `uv run python -m pytest tests/ -q` (and
   `make test-integration` when the change touches storage or concurrency — add
   `SURREAL_PORT=8123` if the target reports 8000 in use). Both opt-in suites
   skip themselves when they cannot reach a server, and pytest calls that a
   pass, so read the counts rather than the exit code.
4. **Delete the entry** once the fix is merged, moving anything durable into the
   document that states the design first.
5. One commit per entry, or per tightly-coupled group, so each is reviewable.

**Every entry is written to be picked up cold**, by someone who has read none of
the conversation that produced it. An actionable entry carries all six:

| | |
|---|---|
| **What breaks** | The behaviour, not the code smell. Ideally reproduced: the call, and what came back. |
| **Why it matters** | What a caller gets wrong today. An entry that cannot say this belongs in `PROPOSED_FEATURES.md`. |
| **Files** | Module and function names, so nobody re-derives the search. Name the rule to reuse, not just the site to change. |
| **The decision, if any** | Stated as an open question with its options — never prejudged, never omitted. |
| **Guarding tests** | Module, names, assertions, and *why they fail on `main`* — step 1 is unrunnable without this. |
| **Verify** | The commands, including `make test-integration` where storage is touched. |

**Deleting an entry has one precondition beyond *merged*:** nothing else may
name it as the primary record. A document saying *"the full statement lives
here"* means the entry is load-bearing and must be moved before it is dropped.
Temporal validity was the standing example, and moving it to
`dev-docs/VALIDITY_DESIGN.md` is what let its entry go.

**Two agents on this file at once:** name the entry you are working in your
commit message, and check the **Files** rows before starting. Entries whose file
sets are disjoint are safe in parallel; entries sharing one are not, however
unrelated they read. The `dev-docs/` design documents are shared state.

**Backend parity is structural.** `tests/conftest.py` parameterises a `storage`
fixture over `InMemoryStorage` and `SurrealDBStorage(url="mem://")`, so every
test taking it runs against both. Storage-behaviour bugs must be tested that
way. Backend-specific internals belong in `test_memory_storage.py` /
`test_surrealdb_storage.py`, which construct their own store. Concurrency is
only exercised by the opt-in Docker suite; the default suite is sequential.

---

## Method

What this file has cost to learn, kept because each rule outlived the defect
that produced it. Stated as rules rather than as history — the instance is here
to make the rule concrete, not to be looked up.

**Repair the class, not today's instances.** A status→opacity map in the
frontend fell through to *fully visible* for any status it did not list, drawing
retired nodes as live. The fix was the fallthrough default reading *active or
retired*, not two more keys, so a status added later cannot draw as live.

**A rule stated for one branch of a conditional is not a rule the code
applies.** A world-change branch carried the argument; the correction branch
carried the same risk and no argument, four lines apart.

**A transaction taking a domain object must decide whether the argument is a
request or a snapshot, and say which.** A list of nodes to retire was a request
whose `lifecycle` field was being read as a snapshot.

**A correctness defect does not wait for a performance visit**, and a cost you
have not measured is a guess — including when it sounds structural.

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
a precondition the model did not have — and the missing precondition mattered
far beyond dedup, having quietly broken three other things.

**A measured quantity is not yet a measured consequence.** Segment text was
measured crossing the embedding window before anyone checked whether segments
are ever embedded. They are not.

**An id nobody dereferences is not an identity.** Label ids were resolved with a
node lookup, so every decision row about a label read as *node not in this
graph* — for two stages, silently.

**A guard whose reach is an accident of where the code sat is one that fails
open.** Guards that must not miss a site parse the source and compare what they
find against a declared list, in both directions.

**A requirement that oscillates with unrelated state is worse than a strict
one.** A rule switched on only when a second graph existed would start refusing
writes because of state the agent never touched.

**Granularity is the logical operation.** A guard taken per query is not a guard
on the operation; the turn is taken at the tool boundary, where the operation
is.

**Ask what reads this** of any field. Three defects came from that one question,
each a value nothing consumed.

**Read the query plan, not the call count.** The two largest costs in one
optimisation pass were single calls that scanned a whole table, which a
round-trip counter rates as cheap.

**Fit an exponent over three sizes, not two.** A two-point fit read fixed setup
cost as curvature and put a crossing at a fifth of its real value.

**A measurement that says no is a real outcome.** More than one design here was
settled by a number that killed it, and the cheapest version of that is
measuring before building rather than after.

**A test suite that always builds a well-formed graph cannot see a defect whose
precondition is a graph nobody set up.** Running a migration against real data
catches a class the suite structurally cannot.

---

## Open issues

### A suppression has no retraction, so every wrong decline is permanent

🟡 **Open**, waiting on a real case. The dual of the rule that made declines
recordable in the first place; `RELATION_LABELS.md` §4.2 states the label half.

**Filed because the reasoning already exists in three places and has no number
of its own**, which is the state that loses it. `RELATION_LABELS.md` §4.2 states
it, `RELATION_LABELS.md` states it, and `similarity_decisions.py` states the
fact-layer half in as many words — but those entries are pruned when their
stages finish, and the argument would go with it.

**The rule and its dual.** The lesson that produced verdicts is *a sweep recomputed from current
state that records no declines is a futile cycle by construction*: it re-offers
what was already refused and cannot know it. The fix is a suppression index —
The `assessed` edge for fact pairs, `RelationVerdict` for label pairs (built
2026-08-27).
**The dual is what the fix creates: a suppression with no retraction makes every
wrong decline permanent by construction.** A pair judged `distinct` in error
never returns, on either layer, however much later evidence says it should.

**This is not the affirmative half, and the difference is the whole reason for
a separate entry.** That one was *nothing retracts a `one_claim` verdict* — the affirmative
half — and it was fixed 2026-08-23 with a deliberately **one-directional**
retraction: `distinct` withdraws a standing `one_claim`, and nothing re-asserts a
withdrawn one, because wrongly withholding a corroboration count is cheaper than
wrongly inventing agreement. That fix left suppression
untouched on purpose. So the affirmative half is retractable and the suppressive
half is not, on both layers.

**And the fix may legitimately differ per layer**, which is the finding most at
risk of being lost. The affirmative half's asymmetry is entirely a property of **corroboration**.
Nothing corroborates on a relation label: a wrong `synonymous` invents no
support and a wrong `distinct` costs no count, so **neither failure mode exists
there**, and a *symmetric* retraction is a live option for labels where it would
be wrong for facts. Porting that shape across unexamined would import a
constraint with no justification at this layer.

> **Checked 2026-08-27 against stage 4.** *"Nothing acts on `synonymous`"* is
> stage-dated — stage 4's deprecation would act on it. The asymmetry still does
> not reappear, because deprecation is reversible by design (`RELATION_LABELS.md`
> §5, and FC2's whole shape assumes it). **That makes §4.2's conclusion depend on
> stage 4 staying reversible**, which is now said in §4.2 rather than left to be
> rediscovered: ship an irreversible deprecation and this argument needs
> re-deriving from scratch.

**Cost of leaving it.** On the fact side it is live but quiet: a wrong `distinct`
is invisible precisely because suppression works. **The label side is now the
second instance** — label verdicts shipped 2026-08-27, so the system holds two
instances of an acknowledged defect, which is the deadline this entry was filed
to beat. It is *quiet* rather than urgent for the reason the entry already gave:
the largest real graph holds one label, so no pair can be nominated and none can
be wrongly suppressed. **That is the corpus arguing, not the design.**

Stage 3 makes one thing here concrete rather than hypothetical: its refusal for a
repeated verdict now points at this entry by number, so an agent that wants a
verdict revisited is told where the question lives instead of being told only
*no*.

**Not recommended for building yet.** What it needs first is a case: a
suppression somebody actually wants undone. Both layers can wait for one, and
the retraction's *shape* should be argued from the real instance rather than
guessed at symmetrically.

---

### FTS index backfill runs inside `connect()` with no progress reporting

⏸ **Deferred**, trigger stated below.

Filed 2026-08-18 from the lexical-search construction; the indexes are on
`main` as of that date. Defining the full-text indexes backfills
every existing row the first time `_setup_schema` runs against a graph —
inside `connect()`, before anything else can happen, with nothing visible to
the user. `IF NOT EXISTS` means it happens exactly once per graph.

Measured (`LEXICAL_SEARCH.md` §11.5 — SurrealDB 3.0.5, median of 3,
documents = nodes + segments): 2,000 → 1.0 s; 6,000 → 3.8 s; **20,000 →
19 s**. Steady-state connect stays ~30 ms. This is lifespan startup, not a
tool call, so no timeout fires — it just looks like a hang.

Deferred because nothing hurts at current graph sizes, and per this file's
policy the next performance fix should come from something real.

**Trigger:** graphs approaching ~10,000 nodes, where the first connect blocks
for >10 s unexplained. **Fix then:** build the index asynchronously after
connect (searches degrade to vector-only until it lands, which R3 makes
graceful), or surface progress through logging / the viz hub. Whoever picks
it up decides between them; the async option must not violate the §5 rule
that a schema that cannot be set up is a failed connection.

---

### The in-memory store cannot persist, so every local use needs a server

🟡 **Open, deprioritised.** Premise corrected 2026-08-26 — see below.

**`InMemoryStorage` has no save or load path.** `epimemer/storage/memory.py`
carries no `save`/`load`/`dump`/`snapshot` method and no `json`, `pickle`,
`Path` or `open(` call anywhere in its 1,201 lines. The state lives in
`_GraphStore` for exactly as long as the process does.

**So SurrealDB is the only persistent backend there is.** That is fine for a
long-running server and wrong for everything else. Anything that runs as a
command and exits — a CLI, a CI step, a developer tool — either re-ingests its
whole corpus on every invocation or stands up a database server to avoid it.
Re-ingest is not a fallback here: ingest is LLM-driven, so the cost is a model
call per document *every time*, not just the first.

**Why it is being raised now.** a client task (see
`~/Documents/notes/`) would use Epimemer's retrieval for
a developer command-line tool. Two things follow. A tool that requires a
database server to be stood up is a worse tool than one that does not, and
would be marked as such. And SurrealDB's *server* is distributed under the
Business Source License — source-available rather than OSI-approved — which is
an awkward runtime dependency to hand over inside a deliverable delivered under contract. Neither problem exists if the local store can write to a file.

**Smallest fix: serialise `_GraphStore` and reload it.** This is persistence
for an existing backend, not a new backend, so the rule about implementing the
full protocol on every backend does not apply. A new SQLite backend would be
the fallback if serialisation turns out not to be viable, and is considerably
more work.

**Three things it needs decided first.**

1. **What is saved and what is rebuilt.** Nodes, edges and lifecycle records
   have to be saved. Embeddings are expensive to recompute and should be saved
   with them. The BM25 index is cheap to rebuild from the stored text, so
   saving it may be a false economy — worth measuring rather than assuming.
2. **When it writes.** A snapshot on clean shutdown loses everything on a
   crash; writing on every mutation costs the speed that makes the in-memory
   store worth having. Somewhere between the two, and the choice should be
   stated rather than defaulted into.
3. **Format and compatibility.** A pickle is fastest and is a liability the
   first time the types change. A versioned JSON or msgpack payload survives a
   schema change, which matters because a stale snapshot silently loading into
   a newer type is the failure mode that is hardest to notice.

**Timing has a consequence beyond the schedule.** Built before any a client
contract starts, this is our own prior work and stays ours. Built during one,
it would be a contract deliverable, and
publishing it back to our own repository would need the client's consent. That is a reason to do it early rather than a
reason to hurry it.

#### The licence premise did not survive checking — 2026-08-26

**Corrected: nothing shipped is BSL, so the second reason above does not hold.**
The confusion that produced it was **BSD and BSL**, which are unrelated: BSD is
permissive and ubiquitous, while the Business Source License is
*source-available*, restricting production use until a change date.

**Inventory of 186 installed distributions**, read from package metadata
(`License-Expression`, then `License ::` classifiers, then the `License` field):

| Category | Count | Notable |
| --- | --- | --- |
| Permissive (MIT, BSD, Apache-2.0, ISC, PSF) | **178** | `surrealdb` **Apache-2.0**; `torch`, `starlette`, `uvicorn`, `websockets`, `scipy` BSD; `pydantic`, `petritype` MIT |
| Weak copyleft (MPL-2.0, file-level) | 2 | `certifi`, `tqdm` |
| Mixed, GPL-flagged | 1 | `docutils` — "Public Domain; BSD; GPL", predominantly public domain, and a docs tool |
| No metadata | 5 | `caio`, `loro`, `mistralai` (all via `pydantic-ai`), plus `epimemer` itself |
| **Source-available / restricted (BSL, SSPL, Elastic)** | **0** | **none** |

**The BSL applies to the SurrealDB server binary, which is not a Python package
and is not shipped.** Connecting over `ws://` means *running* BSL software,
which is a deployment question rather than a redistribution one. And embedded
mode avoids even that: `surrealkv://` and `file://` work today, in-process, with
no server — measured 2026-08-26 at **25 ms to reopen an existing store**, 1 ms
for a count, and 0.85 s to ingest 725 nodes with embeddings against in-memory's
0.029 s. Nothing BSL travels inside a deliverable either way.

**Two caveats, stated because this is the kind of claim that gets quoted.**
Package metadata is not a legal opinion, and the five packages with none, plus
the Rust engine bundled in the `surrealdb` wheel, would each want a real check
before anything is handed over.

**The weight is not where the attention went.** `torch`, pulled in by
`sentence-transformers`, is **385 MB**; `surrealdb` is 9.8 MB and `sqlite3` is
in the standard library. A retrieval tool has to embed its query, so it needs
the embedder. If an embeddable Epimemer meets a size or dependency constraint,
that is where it will bite, and the answer is a remote embedding endpoint or an
ONNX runtime rather than a storage backend.

**So what is left of this entry is ordinary.** Local persistence with no server
is a *convenience* for a command-line tool, worth having and not worth
prioritising over work already designed. SQLite remains attractive on its own
merits — public domain, and `sqlite3` is stdlib, so it is not a new dependency
at all — but as an addition rather than an escape.

> **Carry-forward: three exchanges of architecture were spent before the premise
> was checked**, and the check took ten minutes. *Confirm the constraint before
> designing around it* — the same failure as the frame check proposed for label
> nominations, designed for a case it turned out not to catch, and as relation
> merging, which survived until somebody counted how often it fires.

#### Measured first, 2026-08-25, because the format question has a number

**One of the three questions dissolved on inspection: there is no BM25 index.**
`memory.py`'s own docstring says lexical search *"scores the corpus on every
call (`storage/bm25.py`) rather than maintaining an index"*. Nothing to save and
nothing to rebuild. The clause came over from SurrealDB's FTS backfill,
which is a different backend's problem.

**What a snapshot costs**, synthetic corpus at the real embedding width (384,
`all-MiniLM-L6-v2`), edges at `memory`'s observed ratio of ~7.5 per node:

| nodes | encoding | size | dump | load |
| --- | --- | --- | --- | --- |
| 725 | JSON float lists | 6.9 MB | 0.07s | 0.04s |
| 725 | JSON + base64 f32 | 2.7 MB | 0.01s | 0.01s |
| 10,000 | JSON float lists | 95.1 MB | 1.03s | 0.61s |
| 10,000 | JSON + base64 f32 | 37.9 MB | 0.08s | 0.08s |
| 100,000 | JSON float lists | 951.5 MB | 10.62s | 6.94s |
| 100,000 | JSON + base64 f32 | 378.6 MB | 0.90s | 0.88s |

**Floats as JSON text are not viable** — a gigabyte and eleven seconds at
100,000 nodes, for data that is 153 MB of float32.

**And the parts want opposite treatment.** At 100,000 nodes the payload splits
into 46 MB of node records, 118 MB of edge records and 210 MB of base64
vectors, and gzip -1 takes the first two down by an order of magnitude while
taking 3.4 seconds to shave 26% off the third. Vectors are near-incompressible
and everything else is highly compressible. (The text ratios are optimistic —
the synthetic content repeats — but the *contrast* is the finding, not the
ratio.) A `.npy` block is 153.6 MB against 210.3 MB inline, which is exactly
the base64 overhead, and `numpy` is already a runtime dependency.

**So: one file, two members, compressed differently.** A zip container with the
structural records deflated and the vectors stored uncompressed. One file
cannot desync the way a sidecar pair can, `zipfile` sets compression per member,
and nothing outside the standard library and numpy is needed.

---


---

## Older carry-overs (open, low priority)

- **No retroactive repair of old graphs.** Fixes apply to new operations;
  pre-existing graphs keep stale state until rebuilt. Accepted. One concrete
  instance: every node written before 2026-08-19 carries a literal `0.5`
  confidence, so those rows read as *rated ordinary* when nobody rated them —
  336 of 624 nodes across both real graphs. Absence means something only for
  nodes written since, and the population shrinks only as graphs are rebuilt.

- **There is no path for source discredit.** When a document turns out
  fabricated, every prior derived from it overstates and nothing can sweep per
  source, because support levels live on the node rather than on the
  `sourced_from` edge. Accepted and recorded rather than built; the provenance
  edge is where it would go. Also stated in `SUMMARY.md`.

- **Merge is Topic-only on the wired path.** A scope question rather than a bug;
  it lives in README → *Not yet built*.

**Guidance produces a `confidence_basis` without being enforced.** Measured
2026-08-21 over both real graphs: 163 of 163 rated non-default nodes carry one,
and zero post-guidance nodes sit at a rated `0.5` — they are stored absent
instead, which is the ladder's *omit the field* rule followed exactly. So the
refusal-at-the-boundary fallback stays unbuilt. The trap that measurement set,
recorded because it produced a confident wrong answer first: `confidence_basis`
lives in `node.metadata`, apart from `value.confidence`, deliberately — the
basis is prose about one judgment and `ValueSignal` is the numbers every ranker
reads. Querying `value.confidence_basis`, where it reads as though it belongs,
returns 0% and looks like a finding. **A field's home is part of its
definition.** Pinned in `tests/test_corpus_measure_smoke.py`.

---

## What to pick up next

**Nothing here breaks.** Every open entry is waiting on a trigger rather than on
work, and the triggers are stated in each.

**The performance thread has run out.** `reflect` was the only operation that
failed inside a plausible graph size; two rounds of work took its crossing from
~2,200 nodes to ~26,000 on SurrealDB and ~320,000 in memory. What binds it now
is the bytes moved to compare vectors, close to irreducible without moving the
comparison server-side or caching vectors across calls — both larger changes
than the ones that got it here, and neither worth making at this crossing. **The
next performance issue should come from a profile, not from this file.**

Work that does not exist yet, as opposed to work that is wrong, lives in
`dev-docs/PROPOSED_FEATURES.md`.
