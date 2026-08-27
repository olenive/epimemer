# Epimemer — Known Issues

Living issue tracker. **Last review: 2026-08-22.**

Open: **66** (2026-08-22, not blocking), **67** (2026-08-22, latent) and
**68** (2026-08-22, not blocking), plus **16** and **58**, both deferred with
their triggers stated. **64** was built on 2026-08-22 — its entry is kept for
the measurement it hands forward, and the larger design it grew into continues
in `dev-docs/REVIEW_MODE.md`, steps 2–7. **65** was found and built on
2026-08-22, ahead of #64's first step, which is what would have made it
reachable; its entry is kept until the next prune for the two carry-forwards it
earned. **68** was found by building #64's step 1 and is the third instance of
#64's own shape: a judgment with no way to unmake it.
**61**, **62** and **63** were built on 2026-08-21. **46**, **48** and **51** were built
and merged (2026-08-19, -19, -20); their entries are deleted per the workflow
below, and what they taught is kept here and in the docs they name —
`SUMMARY.md` and `AGENTS.md` for #46's ladder, `BENCHMARKS.md` for #48's
measurement, `docs/RETRIEVAL.md` §8 for #51. **54**, **55**, **56** and **57**
went the same way on 2026-08-18. **59** and **60** closed on 2026-08-21 — #60
built, #59 closed with no code once its premise failed — and both entries are
kept until the next prune, since each records a lesson not yet rehoused. New
findings continue from **61**.

**Filed 2026-08-27, both out of #74 stage 2**: **80** — a suppression has no
retraction, so every wrong decline is permanent by construction; it is the dual
of #64's rule, and distinct from **68**, which was the affirmative half and is
fixed. And **81** — a relation merge stranded the label record it merged away,
found by trying to finish #74 stage 1's deferred test 9, fixed the same day but
for a residue stage 4 owns. The paragraph above is the 2026-08-22 review's
snapshot and is not maintained past it; **the board at the end of this file is
what is current.**

**#53's entry stays, and the reason is specific rather than sentimental.**
`REVIEW_EPISTEMIC.md` §13.8 says in as many words that the full statement of T1
— "eleven numbered sections, with the arguments and the rejected alternatives" —
lives here, and `docs/VALIDITY.md` sends design history here too. Two documents
name this entry as the primary, so it is one, and deleting it would leave both
pointing at a hole. **The condition for pruning it is that the design history
moves somewhere of its own first** — not that the work is finished, which it
already is.

**#53 was the most important thing in this file, and it is now built
(2026-08-19).** The statement of the problem is kept below because it is the
argument the design rests on, and because nothing about *why* it mattered stops
being true once it is fixed. *Facts have no validity interval, so the graph
cannot say when a claim was true.* Saint Petersburg was
Petrograd was Leningrad was Saint Petersburg; every one of those was true, and
the model can only record a pair like that as a contradiction or a supersession,
both of which are wrong. The consequences are not cosmetic: supersession files
historical truth as error and removes it from the active set, contradiction
detection is unsound in both directions, and — the reason this outranks
everything else here — **inference can combine claims that were never
simultaneously true, with nothing to detect it.** That is a soundness defect in
the layer the system exists to provide.

**#53 is split into T1 / T2 / T3, all three decided (2026-08-12).** They are
written up inside the entry and are the design of record, together with the
binding second- and third-pass amendments (same date) marked inside each — read
those too. Several withdraw earlier wording: #54's fix went *copy everything* →
*migrate nothing* → **per edge type**, and T2's recurrence mechanism was
restated twice before it named the code it changes.

- **T1 — what a validity interval is and where it lives.** Replaces the entry's
  original recommendation (b): validity is a new type carried on the
  **`sourced_from` edge**, per source, measured against a named **timeline**,
  with endpoints distinguishing *unknown* from *unbounded*, read back per-source
  with no default collapse.
- **T2 — which mechanism owns a world-change.** Status and intervals answer
  different questions and both happen, so there is no forced choice. The split
  is in the **edge**: a correction writes `superseded_by` and is terminal; a
  world-change writes the new **`temporally_followed_by`** and is reversible,
  because recurrence falsifies "replaced" but not "came after". This is §13.2's
  missing sixth verdict.
- **T3 — the retrieval surface and the naming.** `HISTORICAL` is returned by
  default (with lineage collapse, or ranking fills with versions of one claim);
  `CORRECTED` is reachable but off by default; valid-time queries return
  **buckets** (*provably valid* / *unknown*) rather than a filter, because a
  filter turns missing metadata into a silent false negative; and `as_of` is
  renamed **`graph_as_of`**, reserving `valid_as_of`, since the unmarked name
  inherits the wrong default reading.

**#53 is built.** All six review findings are answered — 2, 4 and 6 by T1; 1 by
T2; 3 across both; 5 by T3 — and everything decided landed on 2026-08-19: T2's
`temporally_followed_by` edge; the interval type and its comparison
(`core/temporal.py`); per-source intervals stored on `sourced_from`, with
`published_at` on the document; recurrence — a retired claim can be nominated,
judged and reactivated; T3's retrieval surface, so validity is finally **read**
(history by default with a claim's earlier versions folded into it, per-source
periods on results, `valid_as_of` answering in groups, `as_of` → `graph_as_of`);
§11's soundness check, which flags an inference whose premises no source puts in
the same period; and §9's boundary proposals, where a succession the agent has
judged lets reflect propose that one claim's period closes where the next one's
begins. T2 unblocked **#54** and closed **#48**, which was fixed alongside step 4
as both entries asked.

Two decided details moved on contact with the rest of the design, both recorded
in the entry: T3 named **three** valid-time buckets and T1 §6's open-world rule
leaves only **two**, since nothing can prove a claim was *not* true at a moment;
and §9's own worked example — two documents, neither carrying a date — yields no
proposal, because a publication date bounds when a claim was *asserted* and using
it as a period's end would have the graph assert something false. What §9 buys is
still real: a date from the second document lands on the first document's fact,
which no single-document ingest could do.

The rest: **52** is unblocked now that **53** has landed (its safety
precondition is what uncovered the problem in the first place), **59** and
**60** were both measured on 2026-08-20 and both shrank — #59 to segments only,
#60 from a memory failure to an unbounded response — and **16** stays deferred
by design.

**The measurement sitting (2026-08-20) is worth reading as a method, not just
two outcomes.** Both entries had guessed, both guesses were wrong in ways that
would have produced real work: #59 named inferences as a risk when they are the
safest corpus measured, and #60 projected gigabytes from a survival rate
measured on text several times longer than the text it was applied to. Neither
error was visible from inside the entry. **The reusable finding is that a rate
measured on one corpus must carry the shape of that corpus with it** — pair
similarity over a fixed vocabulary runs from 0.62% at four words to 74.9% at a
paragraph, so a survival rate quoted without its text length is not a number. **46**, **48**, **51** and **53**–**57** are done — and #54, #55 and
#56 were the same shape: a rule stated in one place and re-derived,
differently, somewhere else. **Nothing open *fails* at a size anyone is
running** except possibly **#60**, whose projection rests on a survival rate
borrowed from a corpus the same entry calls degenerate — which is why its first
option is to measure rather than to fix. #53 never failed either: it was a
correctness ceiling rather than a crash, which is precisely why it was easy to
keep not noticing.

**A design review (2026-08-12) of the open set added amendments** — blockquotes
marked *Review 2026-08-12* inside #46, #51, #52 and #53 — and filed **#54**.
Nothing already decided was overturned; each amendment was either a problem the
decided design had to answer before implementation (#46, #51), a condition the
re-open trigger must carry (#52, still live), or a place where the
recommendation was not yet decidable as written (#53). Every one is now
answered: #46's two were signed off and built on 2026-08-19, #51's three on
2026-08-20, and #53's six by T1, T2 and T3. **The method is the carry-forward:
review the open set before building it, and record the amendments against the
entries rather than in a separate document**, so the thing being built and the
objection to it stay in one place.

**That review's one unfiled finding was closed by #55.** Commit `666904f` had
widened `NodeStatus` without updating the frontend's status→opacity map, whose
own comment warned that "an unlisted status falling through to 1.0 would draw a
retired node as a live one". The fix taken was the fallthrough default rather
than two more keys — `statusOpacity` in `graph-panel.ts` now reads *active or
retired*, so a status added later cannot draw as live. **Repair the class, not
today's instances**, is the carry-forward.

**46 was decided on 2026-08-12, and the decision split it again.** The
documentation promised two things in one sentence — "how well-supported by
evidence" and "multiple independent sources increase confidence" — and they
wanted opposite implementations. Support is a judgment about material only the
ingesting agent has read, so `confidence` became a caller-supplied prior with a
four-value ladder and written guidance. Corroboration is a fact about the graph
that changes as the graph does, so it is derived at read time under its own name
(**#51**) and never writes the field. The general lesson, the third in this file
to arrive by the same route: **when one field is documented with an "and", check
whether the two halves want the same storage.** They did not for `relevance`,
they did not for `novelty`, and they did not here.

Two lessons from building it outlived the entries and are kept because nothing
else records them:

- **Substitute a default only to rank or compare, never to display or relay.**
  `confidence` is nullable so that *unrated* and *rated ordinary* are different
  states; `rated_confidence` reads absence as 0.5 for ordering, and every
  display path passes the absence through — the tooltip prints a dash rather
  than asserting an assessment nobody made. The first build of `NodeView` got
  this wrong in the frontend's favour and was corrected the same day.
- **An index changes nothing until the query names it.** #48's `content` lookup
  was 4.0 ms unindexed and 4.3 ms with the index merely *defined*, because the
  planner still resolved through the status index; `WITH INDEX` took it to
  0.53 ms. Query plans, not schema, are where an index decision is verified —
  and the guard has to be a plan assertion, since behaviour cannot see it.
  `BENCHMARKS.md` holds the numbers.

**49 and 50 are resolved, and the thread is worth the space.** Two notebooks had
been broken for four months because nothing imported them; the guard is
`tests/test_notebooks.py`. Three things it taught:

- **The obvious version of that test would have caught neither notebook.**
  Marimo keeps every import inside an `@app.cell` function body, so importing a
  broken notebook succeeds and the dead import only raises when the cell runs.
  The test parses and resolves instead.
- **A manual sweep had reported four surviving notebooks as clean when three
  were.** The static check immediately found `06_orchestration.py` importing a
  module deleted months ago, plus a call whose signature had lost an argument.
  Reading files is not checking them.
- **Writing the check found the packaging defect behind it (#50).** The
  notebooks imported `rustworkx`, `matplotlib` and `graphviz`, none declared;
  they now live in a `notebooks` extra depending on `petritype[examples]` rather
  than naming petritype's own requirements. `marimo` moved *into* that extra in
  the same pass — nothing outside `notebooks/` imported it, so it had been a
  demo dependency shipped to every install. **Ask both directions of a
  dependency list**: what is imported but undeclared, and what is declared but
  unimported.

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
(1–15, 17–46, 47, 48, 49, 50, 51, 54–57) are deleted-resolved items, not missing
work, and code comments citing a number no longer listed here are pointing at one
of them.

**Deleting an entry has one precondition beyond "merged":** nothing else may
name it as the primary record. A pointer *to* an issue number is fine and
expected; a document saying "the full statement lives in ISSUES.md #N" means
that entry is load-bearing and must be moved before it is dropped. #53 is the
standing example.

35–38 were the value model & graph hygiene plan
(`dev-docs/REVIEW_EPISTEMIC.md` §12, which records what the plan did not
anticipate) plus the mock-embedding width fix.

The performance work (issues 28, 31, 32, 33 and 39) is resolved and its entries
are gone. **`dev-docs/BENCHMARKS.md`** carries the state those fixes left the system
in and the conclusions still worth acting on, but not the runs themselves — it
describes where things stand, not how they got there, and superseded
measurements are deleted rather than kept. The blow-by-blow is in `git log`.

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

**Every entry is written to be picked up cold, by someone — or something — that
has read none of this conversation.** Added 2026-08-21, after a review round
produced findings that were true, actionable, and impossible to start from. An
actionable entry carries all six:

| | |
|---|---|
| **What breaks** | The behaviour, not the code smell. Ideally reproduced: the call, and what came back. |
| **Why it matters** | What a caller gets wrong today. An entry that cannot say this belongs in `PROPOSED_FEATURES.md`, not here. |
| **Files** | Module and function names, so nobody re-derives the search. Name the rule to reuse, not just the site to change. |
| **The decision, if any** | Stated as an open question with its options — never prejudged, never omitted. `#61` and `#62` are the pattern. |
| **Guarding tests** | Module, names, assertions, and *why they fail on `main`* — step 1 above is unrunnable without this. |
| **Verify** | The commands, including `make test-integration` where storage is touched. |

**Two agents on this file at once:** claim an entry by its number in your commit
message, and check the **Files** rows before starting — entries whose file sets
are disjoint are safe to run in parallel, and entries that share one are not,
however unrelated they read. The four `dev-docs/` design documents are shared
state: amend them in place with a dated note (the blockquote convention used
throughout) rather than rewriting a section someone else may be editing.

**Backend parity is structural.** `tests/conftest.py` parameterizes a `storage`
fixture over `InMemoryStorage` and `SurrealDBStorage(url="mem://")`, so every
test taking it runs against both backends. Storage-behaviour bugs must be tested
this way. Backend-specific internals belong in `test_memory_storage.py` /
`test_surrealdb_storage.py`, which construct their own store. Concurrency is only
exercised by the opt-in Docker suite (`make test-integration`); the default suite
is entirely sequential.

---

## Open issues

Listed by issue number, not by priority — for priority see *Recommended order*
at the end. **Nothing here is outstanding work.** **#16** and **#58** are
deferred with their triggers stated; the designed-but-unbuilt queue lives in
`WARNINGS_AND_SETTINGS.md` and `PROPOSED_FEATURES.md`, which is where an agent
looking for the next thing should go.

**#61, #62 and #63 were built on 2026-08-21** and are kept until the next prune,
each for a decision the code alone does not explain: `evidence_merged` is its own
edge type rather than a qualified supersession flag (#61); a look-alike about
another period stops counting *and* comes back named, because the two were never
alternatives (#62); and the nomination bar is one constant at 0.80 rather than
two numbers (#63). **#52**, **#59** and
**#60** are closed and kept as records: #52 built on 2026-08-21 with one
migration deliberately left for a corpus that has merges in it, #59 closed
without code because its premise failed, #60 built at the scope the measurement
left it.

**#53 is built and its entry is documentation, not a task.** It is here because
two other documents name it as the design of record for validity — read it for
that, not as something outstanding.

### Issue 16 — the active graph moves under a call in flight — ✅ FIXED (2026-08-23), same day it was reopened

> **Fixed 2026-08-23.** `storage/active_graph.py`: one guard per backend, with
> two sides. A tool call takes `using()`; the two things that move the active
> graph — `switch_database` and the `viz_list_*` borrow — take `moving()`.
> Users do not exclude each other, so the common case costs an uncontended lock
> and an integer; movers are preferred, so a busy session cannot starve a
> snapshot.
>
> **Granularity is the logical operation**, which is the whole reason the turn
> is taken at the MCP tool boundary (`_run_with_timeout`) and at the snapshot
> RPC (`hub_client.py`) rather than inside the storage calls. A guard taken per
> query would leave the hole it exists to close: a move only has to land between
> two of the several calls one tool makes.
>
> **The title was wrong, and that is the finding.** This was filed as *"viz
> reads re-point the shared connection"* — a SurrealDB problem with a SurrealDB
> fix (a second connection). But the active graph is **process state on every
> backend**: `InMemoryStorage` resolves `self._graphs[self._database]` per call,
> so a `use_graph` landing mid-ingest splits that ingest across two graphs there
> too, with no connection involved. The second-connection fix would have closed
> one backend's half of it. Carry-forward: **a fix aimed at the mechanism you
> noticed can miss the property that made it a bug** — the property here is
> *shared mutable state read per call*, and the connection was one instance.
>
> The asymmetry that survives is the opposite of the one the entry assumed:
> in-memory viz reads take **no** turn, because a dict lookup reaches another
> graph without going near the active one. Only SurrealDB has to borrow, because
> SurrealQL has no cross-database query and a second embedded connection is a
> second store.
>
> **Reproduced against a served SurrealDB before and after**
> (`test_a_snapshot_borrow_waits_for_a_write_in_flight`, and
> `EPIMEMER_SURREAL_WS_URL` pointed at a real server). With the guard disabled
> the write issued during a snapshot borrow **is not in the graph it was issued
> against** — it went to the graph being snapshotted, over the wire, silently.
> That is the wrong-graph incident with no agent involved.
>
> **Two carry-forwards, both about how this stayed open for a month.**
>
> - **A deferral rests on a premise, and the trigger has to be something you can
>   check rather than something you expect to be told.** This one said *"the
>   server is single-client stdio, so nothing issues concurrent tool calls"* and
>   named its trigger as an event — *the server gains concurrent clients*. The
>   premise went false without the event: the same single client started batching
>   parallel calls, which is what every agent harness now asks for. Nothing
>   re-checked it because nothing was written as checkable.
> - **A concurrency test whose subject cannot occur reports green for the wrong
>   reason.** The first end-to-end version passed with the guard removed. With
>   in-memory storage and a hash-based embedder **every await completes without
>   suspending**, so `asyncio.gather` ran the ingest to completion before the
>   switch started and there was no race to lose. It needed a provider that
>   actually yields (`_suspending`) before it could fail.
>
> One residue, stated rather than hidden: **a call that waits on a person takes
> no turn.** `claim_agent` blocks on an elicitation, and holding a user's turn
> across it would stall every snapshot behind a prompt nobody has read — *the
> dashboard is seconds stale* becoming *the dashboard is down*. A borrow landing
> in that window can still redirect that one call's write, to a graph's `agent`
> table. The trade is deliberate and tested as such.
>
> **#73 is unblocked** by this: a cross-graph read that cannot move the active
> graph out from under anything is exactly what the journal locator needed.


> **Reopened 2026-08-23, while settling #72. The trigger this was waiting for
> has already fired, and nobody noticed because it fired in a different shape.**
>
> The deferral below rests on one sentence: *"the server is single-client stdio,
> so nothing issues concurrent tool calls against the shared connection today."*
> **That is false.** A single client's *batched parallel tool calls* are
> concurrent tool calls, and batching independent calls into one block is
> ordinary agent behaviour — Claude Code's own instructions ask for it.
>
> Measured, rather than argued — `scripts/concurrency_probe.py` (`fastmcp`
> 3.1.1, one in-process `Client`, two tools held 400 ms, `asyncio.gather`):
>
> ```
>     7.7 ms  enter graph_stats
>     7.8 ms  enter list_graphs      ← 0.1 ms later, not 400 ms
>   409.2 ms  exit  graph_stats
>   412.4 ms  exit  list_graphs
> ```
>
> Two calls overlap for the whole 400 ms. Nothing in `epimemer/mcp/` serializes
> them — there is no lock over the tool boundary, only `hub_client.py`'s lock
> over viz reads *against each other*.
>
> **So the live hazard is a viz snapshot racing a tool call**, on SurrealDB, and
> it needs no second client. `/api/snapshot?graph=X` takes its graph **from the
> browser**, so snapshotting a graph other than the active one is the ordinary
> case — the viewer clicks a graph in the list. `viz_list_nodes` then points the
> shared connection at X and restores it in a `finally`, and any tool call in
> flight during that window **writes into X**.
>
> That is the wrong-graph incident's exact mechanism, manufactured inside the
> server: a silent write to a graph nobody named, with a success response. The
> difference is that `expected_graph` cannot catch this one — the agent's
> expectation and the server's `current_database` **agree**; it is `_selected`,
> the database actually on the wire, that has moved underneath both.
>
> Unaffected: the in-memory backend, whose viz reads index `self._graphs`
> directly and switch nothing. `EPIMEMER_VIZ_ENABLED` defaults to **true**, so a
> SurrealDB deployment with the dashboard open is exposed by default.
>
> **The fix shape is unchanged and still scoped** (see the 2026-07-28 update): a
> dedicated read connection for SurrealDB, opened in the hub client's RPC
> handler. What changed is that it is no longer waiting on anything.
>
> It also **blocks #73**, the cross-graph journal locator #72 left behind, for
> the same reason: any read of another graph needs one that does not move the
> active database.


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

### Issue 52 — facts are never deduplicated across documents — ✅ BUILT (2026-08-21), migration declined the same day

> **First merges taken on a real corpus, 2026-08-21.** Three revisions of
> today's own documentation — `docs/RETRIEVAL.md` §8, `SUMMARY.md`'s
> corroboration section, and `docs/REFLECTION.md` §2 and §4 — were ingested into
> the `memory` graph with `claim_kind` set on all **44 facts, every one a
> `state`**. `reflect` nominated **18 pairs** above the bar with `truncated`
> empty; **five were merged and thirteen declined**, and that ratio is the design
> working rather than a disappointment. Similarity paired *"most nodes carry no
> validity intervals"* with *"nothing leaves the graph under `adjacent_periods`"*
> at 0.89, and *"Issue 55 and 56 were fixed"* with *"Issue 54 was fixed"* at
> 0.87 — the second pair events besides. Judgment is what the nomination bar was
> always deferring to, and it declined most of what it was handed.
>
> **Three things the corpus showed that the tests could not.**
>
> 1. **The comparability claim holds, and the count did not move.** This entry
>    and `docs/RETRIEVAL.md` both assert that *"the `sources` list on every
>    result is what makes a count taken before and after comparable"*. Measured:
>    each survivor carries **two `document_ids` under one publisher**, and
>    `corroboration.count` stayed at **1** across the merge. All three documents
>    are honestly `published_by: epimemer`, and three files from one project are
>    not three witnesses — so the number that did *not* move is the correct
>    reading, and the plural `sources` list is the only place the merge shows. A
>    merge that raised the count would have been the bug.
> 2. **The pre-`claim_kind` island is now concrete rather than projected.** A
>    cross-generation merge was attempted and refused verbatim: *"1 of these
>    facts were stored without a claim_kind, so nothing knows whether they
>    describe conditions or occurrences — and the two answers merge in opposite
>    directions."* Five facts from document `279f9f42` now sit un-mergeable
>    beside near-identical twins ingested the same day. **The island does not
>    shrink by waiting**: the only exit is re-ingest, and its cost scales with
>    the corpus rather than with any fix. *(Written when this entry still listed
>    an outstanding migration, and pointing at the wrong one — the migration in
>    the heading was corroboration's, now declined below. Re-ingesting the island
>    remains the only way out of it, and nobody has scheduled it.)*
> 3. **The merge collected inferences onto one survivor**, which is the
>    precondition `WARNINGS_AND_SETTINGS.md` §6 was waiting on and had measured
>    at zero the same morning. The survivor *"Corroboration is off by default…"*
>    now `supports` three inferences, two of which — *"Corroboration grows
>    fastest on the graphs where it has the most to say"* and *"…grows most
>    expensive on exactly the graphs where it has the most to say"* — state one
>    claim and previously hung off different premises. See #61 and that
>    document's §6.1.

> **Built 2026-08-21, and the decision this waited on was made rather than
> deferred: the event/state judgment is recorded at ingest.** `Fact.claim_kind`
> is `"state" | "event" | None`, supplied on a `store_decomposition` entry, and
> `merge_facts(source_ids, content)` is the action `redundant` never had. Sources
> are retired as `MERGED` and the survivor keeps **one `sourced_from` edge per
> contributing document**, each carrying that document's own periods — the
> plurality this issue was wanted for, and it needed no new code: edge migration
> already collapses two edges to the *same* document while preserving both sets
> of intervals (#53 T1 §2), which is exactly why validity was put on the edge.
>
> **Why ingest, and what it costs.** The judgment wants the document — the tense,
> the sentences either side, whether *"the election"* is a particular one — and a
> merge is offered two stripped sentences with none of that. The cost is paid
> immediately and in full, and it is measured rather than estimated: the two real
> graphs hold **350 facts (277 + 73) and 0 of them carry a `claim_kind`**, so the
> entire existing corpus is unmergeable and no later pass can repair it, because
> the repair needs the document. (Counted 2026-08-21 by raw HTTP `SELECT`, which
> is how the real namespace is read without `connect()` defining tables — #58.)
> That is the safe direction — a missed merge undercounts, a false one
> manufactures corroboration — and it was the price of the decision rather than
> an oversight in it.
>
> **Seven decisions made during construction rather than inherited from above:**
>
> 1. **Frame *equality*, not `same_frame`.** `same_frame` asks whether two nodes
>    share at least one frame, which is right for a contradiction — an overlap
>    makes the conflict real — and wrong here, because the survivor inherits the
>    union. A base-reality claim merged with one also framed as fiction would
>    assert in a world only one source ever stood in. The precondition above says
>    "different metacontexts"; a partial overlap is the case it does not name, and
>    it refuses.
> 2. **The similarity floor *is* the nomination bar**, not a second, higher one.
>    `SIMILARITY_NOMINATION_THRESHOLD` moved to `review.py` and both readers take
>    it from there, so the rule can be stated as *a merge may only collapse facts
>    that could have been offered to each other as candidates* rather than as two
>    numbers that happen to agree. It is not a second opinion on the agent's
>    judgment — the entry is right that this is an LLM judgment about
>    propositions — it catches a pairing named by mistake.
> 3. **Refusals are returned with prose; only unresolvable ids raise.** A refusal
>    is a judgment the graph made and the agent has a real alternative to take
>    (record `SIMILARITY` and keep both); an id naming nothing, or naming a
>    topic, is a request that was never well-formed. Follows `BoundaryRefused`.
> 4. **Refusals are ordered permanent-first.** A cross-frame pair will never
>    merge however the graph changes; an unjudged one merges as soon as somebody
>    judges it. Reporting the fixable obstacle while a permanent one also stands
>    sends an agent to do work that changes nothing.
> 5. **No new `reflect` nominee list.** The entry proposed a new consumer of
>    `pair_scoring.similar_pairs`. It turned out not to need one: reflect's
>    `contradictions` already nominates same-frame fact pairs above 0.80, which
>    is the same population — a merge candidate *is* a contradiction nominee the
>    agent judges as "same claim". Adding a fifth quadratic list the day after
>    #60 capped the four that exist would have been perverse.
>
> **Amended 2026-08-21 (#63): decision 5 was false as written, and decision 2
> was true only of the two readers it named.** The sweeps nominated at 0.80
> while the merge gate refused below 0.83, so "the same population" was a
> superset, and a pair in [0.80, 0.83) was offered by reflect and then refused
> by `merge_facts`. The conclusion survives — no fifth list is needed — but it
> needed the numbers to agree, which they now do at 0.80. The reasoning to keep
> is that **"already nominated by X" is a claim about X's threshold**, and it is
> worth reading the constant rather than the sentence next to it.
> 6. **Every `EVENT` is refused, including unique ones.** *"Napoleon was born in
>    1769"* can only happen once and could be merged safely. Separating "can
>    happen twice" from "happened once" is a third judgment to get wrong and its
>    error direction is the unsafe one, so the vocabulary stays two-valued and
>    the over-refusal is taken knowingly.
> 7. **The survivor is stamped `STATE` and carries the basis of the confidence it
>    keeps.** Every source cleared the gate as a state, so a survivor left
>    unjudged could never merge again for want of a judgment its own parts
>    carried. And `merged_value_signal` takes the highest confidence while the
>    prose explaining it lives in `metadata` (#46) — rebuilding one without the
>    other leaves a prior nobody can review, which is the state #46's guidance
>    exists to prevent, reached by a path nobody chose.
>
> **What is still outstanding is the corroboration migration below** —
> neighbourhood to identity. It is not broken by this: the neighbourhood walk
> over a merged node is still correct and merely stops being necessary, and the
> `sources` list on every result is what makes a count taken before and after
> comparable. Revisiting it wants a corpus with merges in it, and there is none
> yet.
>
> Also settled here: the recommended order's *"keep the nullable scalar
> `confidence` until dedup lands, then revisit"*. Dedup has landed and the
> scalar stays — merging is rare and gated, so nearly every fact still has one
> source, and a read-time derivation would be a hop paid to combine one number.

> **The trigger has fired.** #53 landed on 2026-08-19, so the precondition this
> was deferred for now exists: facts carry per-source validity, and
> `compare_intervals` answers `before | after | overlap | unknown` rather than
> forcing a two-valued guess. Nothing else about the entry changes — in
> particular the re-open still has to carry the event/state distinction below,
> which is the thing #53 makes *expressible* rather than the thing it decides.

> **Deferred 2026-08-12, the day it was filed** — not for want of value but
> because the precondition that makes it safe turned out to be missing from the
> model, and chasing it produced **#53**, which matters more.
>
> The tuning question was: can dedup be made safe by requiring agreement on
> subject matter *and* timeline? **No — the temporal half is not available.**
> Facts carry no validity interval, and the `TIMELINK` edges they may carry are
> *mention* time drawn from dates named in the content, so the claims that most
> need a date ("the city is called Leningrad") have none. Requiring temporal
> agreement would therefore be vacuous for the majority of pairs, and vacuous
> in exactly the direction that permits false merges.
>
> **What #53 changes when it lands.** Under a set-of-intervals model, identical
> claims recurring over disjoint periods are one node with several validity
> intervals — the merge is correct and the intervals union. That converts
> dedup's worst case into its cleanest one, so this should be re-opened *after*
> #53 rather than attempted before it.
>
> **The trigger to pick it up:** #53 lands, or redundant facts are measured to
> degrade `reflect` or search quality — whichever comes first. Node count alone
> is not the trigger; see the threshold tension below.
>
> **Corroboration does not have to wait.** #51 was rewritten to compute over a
> similarity neighbourhood rather than assuming identity, which delivers the
> benefit this issue was wanted for without taking its risk.

> **Review 2026-08-12 — the re-open trigger must carry a distinction the
> taxonomy lacks.** "#53 converts dedup's worst case into its cleanest" is true
> only for **stative** claims. *"Labour is in government"* ingested for 1997
> and for 2024 is one state whose intervals union — clean. *"Labour won the
> election"* ingested for 1997 and for 2024 is near-identical text describing
> **two events**; an interval-union merge fabricates one fact spanning two
> victories. The set model dedupes *states* and must never dedupe *events*, and
> nothing in the verdict taxonomy or the planned classifier distinguishes them.
> When this re-opens, the classifier needs an explicit event/state judgment
> ahead of the merge decision, and the failing test needs the pair above.
>
> **Until then, `redundant` is a dead branch** — REVIEW_EPISTEMIC.md §3's
> action column says "dedup or ignore", but no fact-merge action exists (merge
> is Topic-only on the wired path), so the verdict either no-ops or tempts the
> agent into a supersession whose required `because` has no honest answer:
> "same claim" is neither *it was wrong* nor *the world changed*. Interim
> action, now stated in §3: **record `SIMILARITY` and keep both**, which is
> exactly what #51 consumes.

The analysis below stands and is why the deferral is a judgment rather than a
delay.

Filed 2026-08-12, out of the #46 discussion. `get_node_by_content` is called in
exactly three places (`tools.py:163`, `:282`, `:880`) — source/publisher
entities and tag topics. **No decomposition path deduplicates facts or
inferences.** The same claim arriving in two documents produces two nodes, so a
graph fed the same widely-repeated fact fifty times holds fifty nodes for it.

**This is wanted** — node count aside, it is what makes a fact's provenance
plural, and every downstream question about corroboration (#51) assumes it.
What follows is why it is not plumbing.

**Exact match will not fire.** Two documents essentially never phrase a claim
identically, so the mechanism that works for tag names catches nothing here.
The operation that would actually fire is semantic, and semantic similarity is
not claim identity in either direction: *"Napoleon was born in 1769"* and
*"Napoleon died in 1821"* sit close in embedding space and are different
claims; *"the deploy failed"* and *"the deployment did not succeed"* are one
claim and may not clear a conservative threshold. Getting this right is an LLM
judgment about propositions, not a cosine threshold.

**The failure mode is the worst one available, and it is worth being precise
about why.** A false merge does not just lose information — it **manufactures
corroboration**. Two distinct claims wrongly unified become one node with two
independent sources, which reads as *better supported* than either was. The
error mode of dedup is exactly the quantity #51 measures, so a sloppy
implementation does not degrade corroboration, it inverts it. A missed merge,
by contrast, only undercounts. **Tune to under-merge.**

That has a consequence worth stating plainly, because it collides with the
motivation: **reducing node count and measuring corroboration pull the
threshold in opposite directions.** Whichever is chosen should be chosen
knowingly, and corroboration should win — a graph with redundant nodes is
untidy, a graph with fabricated support is wrong.

**It is less a missing feature than the fourth branch of a classification the
system already half-performs.** Two similar facts can be four things, and three
already have edge types and machinery:

| Relation | Modelled today |
|---|---|
| Same claim, different frames | `VARIANT_OF`, `record_variant` |
| One updates the other | `SUPERSEDED_BY`, `SUPERSESSION_CANDIDATE` |
| They conflict | `CONTRADICTION`, `record_contradiction` |
| **The same claim** | **nothing — this issue** |

So the work is a new consumer of `pair_scoring.similar_pairs` (which already
builds the matrix, and which #47 made fast) plus a classifier that can return
"same claim" as distinct from the other three, not a new subsystem.

**Preconditions that are correctness, not polish:**

- **Frame-scoped.** Two identical sentences in different metacontexts must
  become `variant_of`, never one node. Merging fiction into base reality is the
  single worst outcome the system can produce, and the naive similarity pass
  does exactly that.
- **Active nodes only.** Merging a superseded fact with its superseder destroys
  the history that supersession exists to record.
- **Signal merge via `merged_value_signal`.** Fact merge would be the second
  caller of the shared rebuild, which is the function's whole reason for
  existing (#45): a field-by-field rebuild silently resets what it forgets.
- **Provenance is additive.** The survivor keeps a `SOURCED_FROM` edge per
  contributing document — that plurality is the point, and it is what makes
  per-source confidence (#46) and corroboration (#51) mean anything.

**Failing test first**: `tests/pipelines/test_fact_dedup.py` — two documents
asserting the same claim in different words yield one fact with two
`sourced_from` edges; two documents asserting *different* claims about the same
entity stay two facts; the same sentence in two metacontexts stays two facts
joined by `variant_of`; and (review 2026-08-12) two near-identical **event**
claims from different periods — "Labour won the election", 1997 and 2024 —
stay two facts even under the #53 interval model.

> **Inherited from #51 when its entry was deleted (2026-08-20): corroboration's
> second migration is this issue's to make.** Corroboration ships computed over
> a **similarity neighbourhood** because facts are not deduplicated; when they
> are, it moves to identity — merged nodes with unions of provenance — and
> callers will have learned to read the number by then. Planned here rather
> than discovered later:
>
> - **What changes.** The neighbourhood walk collapses into the node itself, so
>   the count stops being able to over-report through a wrong `similarity`
>   edge, and `docs/RETRIEVAL.md` §8's Saint Petersburg caveat goes with it.
> - **What stays comparable.** The unit is unchanged — distinct publishers,
>   with the node's own source counting as 1 — so a count taken before and
>   after means the same thing and can only get *more* accurate. The `sources`
>   list on every result is what makes that checkable across the change.
> - **What this issue must not break.** Publishers are keyed by entity **id**,
>   deferring to `_upsert_entity_topic`'s exact-content match rather than
>   re-deriving identity. A graph holding duplicate entity nodes over-counts
>   until `reflect` merges them, and that is the right place for the repair —
>   fact dedup should not grow a second, different notion of entity identity.
>
> **Declined 2026-08-21, with no code, because its premise is inverted.** The
> migration assumed corroboration counts over a *populated* neighbourhood that
> merging would shrink toward identity. It does not. **No similarity edge exists
> on any real graph** — 0 of 4,386 on `memory`, 0 of 1,028 on
> `petritype-server` — because nothing in the codebase writes one, which is now
> **#64**. So the neighbourhood is already `{node}`, corroboration is already the
> identity reading, and collapsing the walk would change no count anywhere.
>
> Both stated payoffs are gone on inspection, and by different routes:
>
> - **"The Saint Petersburg caveat goes with it"** — already collected, by #62,
>   on 2026-08-21, through a different mechanism. `assertions_are_disjoint` and
>   `adjacent_periods` settle the Leningrad pair *inside* the neighbourhood
>   reading. §8's caveat was rewritten then and no longer says what this bullet
>   was written against.
> - **"Stops over-reporting through a wrong `similarity` edge"** — an
>   over-report needs an edge, and there are none. What the bullet describes is
>   the risk `corroboration.py` explicitly accepts in exchange for auditability:
>   a wrong edge inflates a number **whose workings come back with it**, in
>   `sources[].node_ids`. That trade was argued, not overlooked, and it does not
>   become a defect by being restated as a migration.
>
> **And it would now be the wrong direction.** Collapsing the walk removes the
> only consumer of a judgment #64 exists to start recording. The two changes are
> not independent: **the neighbourhood is the mechanism by which "keep both,
> joined by `similarity`" means anything at all**, and this migration would
> delete it on the grounds that a *different* mechanism had superseded it —
> which merging has not, and structurally cannot.
>
> **Merging structurally cannot replace it, and this is the load-bearing
> point.** `merge_refusal` refuses an **event** outright, refuses an unjudged
> fact, and refuses a cross-frame pair. So identity can never count the
> paradigm case corroboration exists for: *two publishers reporting the same
> occurrence*. Under an identity-only reading, BBC and Reuters on one election
> score 1 for ever, because the gate that would unify them refuses every event
> by design. The live corpus says the same thing about the other refusal —
> **305 of 356 active facts (86%) carry no `claim_kind`** and never will, so
> identity is permanently unreachable for them too.
>
> Kept in place of the migration: **the `sources` list is still what makes
> counts comparable**, and that claim survives intact — it was verified across
> the five merges of 2026-08-21, where each survivor gained a second
> `document_id` under one publisher and the count correctly did not move.

---

### Issue 53 — facts have no validity interval, so the graph cannot say *when* a claim was true — ✅ BUILT (2026-08-19)

Filed 2026-08-12. Surfaced while asking whether fact deduplication (#52) could
be made safe by requiring temporal agreement. It cannot, because the temporal
information it would require is not in the model — and following that back
showed the gap is not dedup's, it is the graph's.

> **Construction note (2026-08-19) — #53 is built. The six-step order, plus
> §9's boundary proposals, which the order had omitted.**
>
> The build order is not written down anywhere above, so it is recorded here as
> it is worked: (1) the lineage edge, done; (2) the interval type and its
> comparison, pure functions with no storage, done; (3) intervals onto the
> `sourced_from` edge, per source, done; (4) recurrence, bundled with #48 since
> both visit `get_node_by_content`, done; (5) T3's retrieval surface, done;
> (6) §11's soundness check, done. **The order itself omitted §9's
> reflect-proposed boundaries**, built afterwards as (7) — see the end of this
> note, which records the three decisions it needed.
>
> **(1) is a gap-closing job rather than a new one.** #54 shipped the *status*
> split on 2026-08-12 — `superseded_status_for(because)` returning `CORRECTED`
> or `HISTORICAL`, with `migration_disposition` branching on it — and the
> *edge* split stayed on paper. For the week between, every world-change wrote
> `superseded_by`: a node whose status said *still true of its period*, reached
> by an edge that said *replaced*. One of the two was wrong and it was always
> the edge, because the status is what the caller judged and the edge was a
> constant.
>
> The rule is `lineage_edge_type_for(status)`, deliberately shaped as the pair
> to `SUPERSESSION_REASONS`: one decides what the node becomes, the other what
> the edge says, and reading both from `status` is what stops them drifting
> apart again. It refuses any status that is not a supersession, on
> `superseded_status_for`'s grounds — a merge writes `merged_into` through its
> own path, and answering for it would hand back a plausible edge for an event
> that did not happen.
>
> **What it buys immediately is small and worth naming honestly**: nothing
> reads the new edge yet. The value is that the graph stops making two
> contradictory statements about the same event, and that the recurrence work
> in (4) has an edge it can attach to. `HISTORY_EDGE_TYPES` membership carries
> the three behaviours T2 asked for — not traversed, not migrated, anchored to
> its version — without a line of new policy.
>
> **Two fixture drifts fixed on the way.** Four tests paired `HISTORICAL` with
> a hand-built `superseded_by` edge, a combination production can no longer
> produce; they now derive the type from the status. And the frontend's
> `EDGE_MEANINGS` gained the new type, which is #55 exactly — a Python enum
> growing a member a TypeScript lookup table never heard of. Same hue as
> `superseded_by` on purpose: *which* retirement happened is the node's status
> colour to say, and saying it twice lets the two readings disagree.
>
> **Left alone, and noted rather than fixed:** `link(A, B, "superseded_by")`
> still writes a lineage edge without flipping A's status, and now accepts
> `temporally_followed_by` on the same terms. That hole is older than this
> issue and `REVIEW_EPISTEMIC.md` §6.1 already records the decision — the
> answer was to add `supersede`, not to forbid `link` — so this slice widens
> the enum by one and changes nothing about it.
>
> **(2) is built (2026-08-19): `epimemer/core/temporal.py`.** `ImpreciseInstant`
> (T1 §4), `ValidityInterval` (§4, §5, §7, §8), `TemporalRelation` and
> `compare_intervals` (§10). Pure — it reads no clock, touches no storage and
> makes no network call — and deliberately offers **no collapse over sets**
> (§3), because the union/intersection trap is the thing §3 exists to refuse and
> a default is near-impossible to remove once callers depend on it. `published_at`
> (§7) is not here: it changes a stored model and belongs with (3).
>
> **Two things T1 specified and construction had to make exact.**
>
> *Four endpoint classes for §4's three states.* `precise`, `named`, `unknown`,
> `unbounded`. §4 groups the first two as one "point (concrete instant or
> free-text label)" state, but the implementation of that grouping is
> `at: datetime | None` with a label beside it — the exact `None`-means-two-things
> shape §4 rejects `Timepoint` for, one level further down. So a named endpoint
> is its own class: it compares *identically* to `unknown`, and differs only in
> carrying the words the source used, which are the evidence for any later
> resolution. Resolving one produces a `PreciseInstant` that keeps the label. The
> three-state argument is untouched — `unknown` still is not `unbounded` — and
> the extensibility rule holds either way.
>
> *Intervals are half-open, `[start, end)`.* T1 does not decide this and it
> cannot be left undecided in code. Closed intervals make the exact instant of
> the 1991 renaming a moment the city is provably called both names, and every
> adjacent pair of periods overlap by a point — which would fire §11's check on
> ordinary succession. So an instant belongs to the period that starts on it.
> A claim true at a single moment is a witness point, not a zero-width interval,
> and a zero-width interval is refused as empty.
>
> **Comparison concludes only what cannot be otherwise.** Unknown and named
> endpoints withhold, and `unknown` is the majority answer by design. Two
> refinements were needed to keep it from withholding where it actually knows:
> an unbounded endpoint settles a comparison even against an unlocated one
> (every moment of a non-empty interval falls after the beginning of time and
> before the end of it, so a claim asserted to have always held overlaps a period
> nobody has dated); and witness points can only ever *add* an overlap, never an
> ordering, since a witness bounds an endpoint from the inside and an inside
> bound cannot show a period stops before a moment. Both are pinned by tests,
> along with a matrix asserting `before` one way is `after` the other for every
> pair — the failure that guards against is a rule added to one branch and not
> its mirror, which no single example catches.
>
> **A self-contradictory interval is refused at construction**, on *definite*
> violations only: start at or after end, or a witness its own endpoints exclude.
> That is a construction error rather than a source disagreeing with itself — no
> document says *"as of 1990, Labour governed 1997–2010"* — and left standing it
> would let the comparison derive an overlap from a premise that cannot hold.
> Unknown endpoints never trip it, which keeps it from becoming a check on
> unknown. Naive datetimes are read as UTC once, at construction, because a
> hand-typed historical date is naive far more often than not and mixing it with
> an aware one raises from inside a comparison rather than answering.
>
> **§4's structural test exists**: `instant_kind` is read in exactly two files —
> `core/temporal.py`, which defines it, and the ingest guidance, which has to
> name the shapes an agent writes. It fails the moment a third appears, which is
> what keeps "adding a kind is a known, small change" true rather than
> aspirational.
>
> **(3) is built (2026-08-19): intervals are stored, per source.**
> `NodeEdge.validity` is a list of `ValidityInterval`, and **only a
> `sourced_from` edge may carry one** — on a `similarity` or `tagged_with` edge
> it would be a period attributed to nobody, which is §2's node-level set
> reached by accident. `RawDocument.published_at` is an `ImpreciseInstant`, and
> the no-fallback rule is in the field comment because a fallback to
> `created_at` is the natural thing for someone to add later.
>
> An ingesting agent supplies both: `validity` per decomposition entry (it lands
> on that node's provenance edge), `published_at` on `segment`. The guidance is
> the deliverable here, as #46 found — it names the four endpoint shapes, says
> that omitting the field entirely is the common and correct case, and states
> the one prohibition that has no representation in the type: **a date the agent
> knows and the document does not give is neither `stated` nor `inferred`, and
> must not be supplied at all**.
>
> **One defect found while building it, which the field would otherwise have
> shipped with.** Edge migration collapses duplicates by `(src, dst, type)`, so
> merging two nodes extracted from the same document dropped one provenance edge
> — and with it everything that edge asserted. "Intervals survive a merge for
> free" (§2) is the property that put validity on the edge, and the dedup is
> exactly where it would quietly stop being true. Both backends now hand the
> loser's intervals to the survivor through `merged_validity`. That is **not**
> the union §3 forbids: that union is across *sources*, where a sloppy source
> widens a careful one's period; both lists here came from the same document
> about what is now the same claim, so the list is doing what it was always for.
> Exact duplicates are dropped, since one source asserting a period twice is one
> assertion. The SurrealDB planner also iterated a *set* of source ids, which
> made which duplicate survived depend on hash order; it now follows the
> caller's order.
>
> **What still does not exist, stated so it is not assumed:** nothing *reads*
> validity. There is no `(source, interval)` retrieval surface yet — the whole
> edge is visible through `query_graph`, and the purpose-built read belongs with
> T3 (step 5) rather than being invented ahead of its naming decisions. Reflect
> does not yet propose the boundaries §9 describes, and §11's check is step 6.
>
> *Step 5 built that read (`validity_for`), so only the last two sentences of
> this paragraph still stand.*
>
> **(4) is built (2026-08-19): a retired claim can be seen, judged and brought
> back.** T2's reversibility, in four pieces.
>
> *Nomination sees it.* `vector_search` takes `statuses`, defaulting to
> `{ACTIVE}` so nothing resurfaces by accident, and `check_conflicts` asks for
> `{ACTIVE, HISTORICAL}`. This was the whole blocker: the guard that said
> *superseded and merged nodes must never resurface here* was also what made
> `recurs` unreachable, so the verdict existed on paper and could never fire.
> Every candidate now carries its `status`, because telling `redundant` from
> `recurs` *is* the active-or-retired distinction and a list that hides it
> invites the misclassification the verdict was added to prevent. The two
> policy sets live in `core/types.py` — `NOMINATED_STATUSES` and
> `RESTORABLE_STATUSES` — so "what can come back" is answered once.
>
> *Reflect catches what ingest missed.* `check_conflicts` is opt-in, so a graph
> whose agent never called it would never be asked. The sweep nominates the same
> set and reports mixed pairs under **`recurrences`**, apart from
> `contradictions` — a claim beside its own successor is not a contradiction,
> and filing it under that word is the same misreading from the other side. The
> wider set is scored **once** and partitioned after, because this is the phase
> that crosses the tool timeout as a graph grows (#39); a test pins the single
> batched call.
>
> *A verbatim floor under both.* `store_decomposition` reports
> `historical_twins`: facts just stored that are word-for-word a retired claim.
> It reports and never acts — flipping a node live on a string match is too
> brittle to do silently — and it is affordable only because #48 was fixed in
> the same visit, one indexed lookup per fact rather than a table scan. That
> bundling was the point of doing the two together.
>
> *Reactivation is one transaction.* `restore` gained `node_ids`,
> `sourced_from` and `validity`. A `HISTORICAL` node comes back **only** when
> the caller names the document asserting it again, and the flip and that edge
> land together — `set_node_status_tx` grew an `edges` parameter for exactly
> this, since a node back to ACTIVE with no edge recording why is an assertion
> the graph makes and cannot attribute. `CORRECTED` is refused, which is what
> this tool's docstring always literally said and could not enforce before the
> status split. Refusals are checked before anything is written, so a batch
> naming one corrected node changes nothing rather than half-applying.
>
> The prior intervals and the `temporally_followed_by` record are untouched, so
> a reactivated node holds several disjoint periods — the shape a list of
> intervals was for. The retirement stays in the lifecycle history, which is
> what makes a *second* cycle describable; the `EVENT_LOG.md` §6 constraint is
> already satisfied by the append-only episodes.
>
> **One asymmetry left deliberately.** `vector_search` takes `statuses`;
> `text_search` still takes a singular `status`. Recurrence asks only the vector
> route, so no caller can make the two seed routes disagree today — but this
> must close at step 5, or the lexical half of a hybrid search will be the one
> that cannot see historical nodes. Noted in `text_search`'s own contract.
>
> *Closed at step 5, as required.* Both routes now take one `statuses` set.
>
> **(5) is built (2026-08-19): retrieval reads all of it.** T3's surface, and
> the first thing in #53 that a caller sees without asking for it.
>
> *Reachability, with the asymmetry in the defaults.* `reachable_statuses` turns
> T3's two switches into the set both arms are asked with — `include_historical`
> on, `include_corrected` off. Legacy `SUPERSEDED` rides with the cautious one:
> those rows do not record which event they were, `LINEAGE_EDGE_TYPES` already
> reads them as corrections, and putting them anywhere else would let two
> readings of one unrecorded retirement disagree. `MERGED` and `ARCHIVED` have no
> switch — a merged node's content lives on the survivor, and an archived one was
> deliberately set aside.
>
> *Lineage collapse, which is the condition rather than a refinement.* A retired
> claim and its replacement are near-identical text, so default-on history
> without this is a ranking regression. `fold_lineage` walks
> `superseded_by`/`temporally_followed_by` forward and attaches each retired
> match to the matched claim that replaced it. **It reads the status, not the
> edge**: two ACTIVE nodes joined by a lineage edge are two current claims —
> `restore` leaves exactly that shape, and `link` writes it by hand — and folding
> one would hide a live answer. The walk is cycle-safe because
> `temporally_followed_by` *permits* cycles by design, so a recurrence closes
> one on ordinary data rather than on corrupt data; in a cycle there is no last
> version, so the best-ranked member hosts the rest. The first draft let two
> nodes in a cycle fold into each other and both leave the result — it
> terminated and still lost the answer, which is the worse of the two failures.
>
> *The cut moved.* Folding after a top-k cut rearranges a result that has already
> lost what it was meant to save, so fusion over-fetches and `collapse_lineage`
> cuts back to `k` — a transition of its own, before expansion, so no hop is
> spent walking out of a node about to be folded away. R2's protection moves with
> the cut, and a declared term's hit is exempt from folding as well: *present in
> the response* was never the promise, *comes back as a result* was.
>
> *Validity is readable, per source.* `validity_for` answers T1 §3's
> `(source, interval)` pairs on one batched edge query, and `search` puts them on
> each node that has any. Nothing is collapsed on the way out.
>
> *Buckets, and the third one that cannot exist.* `valid_as_of` labels every
> result `valid` or `unknown` and excludes nothing. **T3 named three buckets and
> T1 §6 settles it at two**: an interval asserts nothing about the outside, so a
> moment outside every stated period is unknown, not false. Nothing can prove a
> claim was *not* true at a moment without a closed-world marking, and §6
> proposes none — so the excluded bucket is unreachable rather than empty, and a
> verdict nothing can produce would only get a dead branch written for it.
> `ValidityVerdict` has two members and says why. This *sharpens* T3's argument:
> a valid-time filter was rejected there for turning missing metadata into a
> silent false negative, and under §6 it is not merely dishonest but
> unimplementable, because there is no negative to filter on. **If closed-world
> assertions are ever wanted, this is the entry they attach to.**
>
> *A moment asked about protects the claim that answers it.* Without that, a
> search for what the city was called in 1980 returns the 1991 answer with the
> 1980 one folded underneath — the asked-for answer hidden beneath the wrong
> one, which is this step's own defect arriving from the temporal side.
>
> *Witness points had to be read here, not just stored.* `validity_at` reaches
> from a witness back to the start and forward to itself, because the commonest
> shape of a current claim — located start, **unknown** end, since `unbounded`
> would say it can never stop — concludes nothing from its endpoints alone. That
> is what T1 §7 said witness points were for; step 5 is where it becomes true.
> `_definitely_contains`, which the overlap rule uses, is untouched.
>
> *The rename.* `as_of` → **`graph_as_of`**, with `valid_as_of` now taken by the
> parameter above. `QueryRequest.at_time` became `graph_at_time` on the same
> grounds — it is unread, but it is the type both axes now land in, and an
> unmarked name beside a marked one is the confusion the rename exists to
> prevent. No alias was left: a lingering `as_of` in the tool list is exactly the
> misreadable name the decision was against.
>
> **One defect found on the way, in a place behaviour could not reach.** The
> visualisation's `InstrumentedStorage` re-declares every protocol parameter by
> hand, so the widened `text_search` left it accepting `status` and raising
> `TypeError` from inside any visualised search. The existing guard checked
> method *presence* only; it now compares signatures, and that guard was
> mutation-checked against the drift it just missed.
>
> **What is still not built:** §11's soundness check over stored inferences
> (step 6), and reflect proposing the boundaries §9 describes. Graph expansion
> still traverses only ACTIVE neighbours — history edges are excluded from
> traversal anyway, so a retired node arrives as a seed or not at all.
>
> **(6) is built (2026-08-19): the soundness check, and with it the whole build
> order.** `find_unsound_inferences` reports, as a reflect phase, an active
> inference whose premises no source puts in the same period. This is the
> strongest form of what #53 exists for — not a display defect but a soundness
> defect in the layer the system provides — and it could not be written before
> now because there was nothing to compare.
>
> *The rule is named once, in `assertions_are_disjoint`.* Per premise, the
> collapse is the **existential union** — the moments *some* source asserts the
> claim held — and the flag fires only when every cross pair provably falls
> clear. §11's second pass demanded that be written down because the error
> direction is the whole argument: a sloppy, over-wide source can *suppress* a
> flag and never manufacture one, while an implementer reaching for the
> intersection instead gets false flags. A test pins the suppressing case, and
> stripping the definiteness requirement fails seven.
>
> **A pair that cannot be placed blocks the finding rather than counting as
> disjoint.** Every cross pair must compare `before` or `after`; `unknown` is
> not a weak yes. That is §11's *never fires on unknown* taken literally, and it
> is the same open-world rule that emptied T3's third retrieval bucket at step 5
> — an interval asserts nothing about the outside, so what the flag says is *no
> source asserts these were ever both true*, never *they never were*. Both
> readings were available in the prose; only one of them is a check on evidence
> rather than on ignorance.
>
> *It runs at reflect and not at ingest*, which §11 permits either way and the
> motivating case decides: an inference joining a 1970 document's fact to a 2000
> document's is invisible while either is being stored, because the other is not
> in front of the agent. Reflect is the only vantage point where both premises
> are present. Four batched queries whatever the graph's size, and the phase
> sits beside the two other *is this graph consistent* arms.
>
> *What it reports is evidence, not a verdict.* Each flag names the inference and
> the offending premise pairs **with their periods**, because the agent's move —
> re-derive, narrow, or retire — is a judgment, and a verdict with its dates
> hidden cannot be argued with. Pairs are reported once rather than in both
> orders, and one entry per inference rather than per pair, since the decision is
> about the inference.
>
> **With this the six-step order is complete. One decided piece of #53 was never
> in it**: §9's *reflect proposes a boundary*. Two documents, 1970 and 2000,
> neither stating an end date — only reflect, seeing both, can propose that the
> first interval closes before the second opens, as an `inferred` boundary
> surfaced for review and never written silently. That is decided design and
> explicitly *"not optional garnish"*, and it is unbuilt. It needs its own
> decisions before code — which pairs qualify, what the proposal looks like, and
> how `apply_reflection` accepts one — so it is named here rather than
> improvised at the end of a construction note.
>
> **(7) §9's boundary proposals are built (2026-08-19), and the three decisions
> it needed are recorded here.** `propose_boundaries` is a reflect phase;
> `apply_reflection(boundaries=[...])` is the only thing that writes one.
>
> *Which pairs qualify: a `temporally_followed_by` edge, and nothing else.* That
> edge is the agent's recorded verdict that the world moved from one claim to the
> next, so the interval consequence is bookkeeping rather than a second judgment.
> Without it, deciding that two similar facts are successive **is** the judgment
> §3 reserves for the agent, and a sweep making it would be guessing the
> succession as well as the date. `superseded_by` licenses nothing: a correction
> says the claim was never true, and a claim that was never true has no period to
> close. It composes cleanly with what was already there — reflect nominates the
> pair, the agent judges it `succeeds`, and the *next* reflect proposes the
> boundary that judgment implies.
>
> *Which date: one a document actually gives.* The successor's own located start
> moved onto the predecessor, or the predecessor's own located end moved onto the
> successor — §9's *"the first interval closes before the second opens"* read as
> a **relation** rather than as a date. Half-open intervals make it exact: the
> instant belongs to the period that starts on it, so there is no overlap and no
> gap to argue about.
>
> **Publication dates are deliberately not used, and that means §9's own worked
> example yields nothing.** Two undated documents, 1970 and 2000: a publication
> date bounds when a claim was *asserted*, never when the previous one stopped
> holding, so closing Leningrad's period at the 2000 gazetteer would have the
> graph assert the city was called Leningrad in 1995. Over-claiming is the one
> direction this design never takes. What "reflect seeing two documents" buys is
> real all the same, and it is what §13.8 item 5 promised: the 1991 date comes
> from the *second* document and lands on the *first* document's fact, which no
> single-document ingest could ever do. The worked example is the case where
> nobody wrote a date down anywhere, and no amount of reading can recover one.
>
> *What the proposal looks like:* the interval as it stands and as it would
> become, side by side, plus the claim and source that license it. Both are shown
> because the change is easy to miss — **the revised interval's basis is
> `inferred`**, so an interval whose start a document *stated* stops being
> reportable as stated once the other end is worked out.
>
> **That is a real cost and it is `basis` being per interval (§8) rather than per
> endpoint.** The alternatives are worse: leaving it `stated` has a source appear
> to assert a date no document gave, which is the one thing §8 exists to prevent;
> putting the closure on a second edge leaves the *open* interval in place, and
> the existential union then keeps the claim open anyway, so nothing is closed
> and §9's purpose is missed. Losing "the start was stated" under-claims, which
> is this design's safe direction throughout. **If per-endpoint basis is ever
> wanted, this paragraph is the reason**; it was not taken here because it
> changes a decided type, every stored interval and both backends, for a
> refinement to something that is already honest.
>
> *How it is accepted:* `apply_reflection(boundaries=[{node_id, source_id,
> endpoint, at, timeline_id?}])`, re-derived from the graph as it stands rather
> than trusting an echo of a proposal that may be stale. It **requires exactly
> one candidate** — that source's period, on that clock, still open at that
> endpoint — and refuses otherwise: several means ambiguous, none means already
> answered. Refusals come back in `boundaries_refused` with a reason rather than
> being skipped silently, because the thing being overwritten is what a source is
> recorded as asserting. Construction is the consistency check, so a boundary the
> evidence contradicts — a predecessor still being witnessed after the successor
> began — is never proposed and never written.
>
> *Refusals worth naming*: a `named` endpoint is never proposed over, because
> resolving a source's words into a date is an explicit act (§4) and not
> something a sweep does quietly; an `unbounded` one is never proposed over,
> because a source saying there is no boundary is not a gap to close; and periods
> on different clocks never meet, exactly as `compare_intervals` refuses to place
> them.
>
> **§4's structural test earned its place here.** The first draft read
> `instant_kind` inside the boundary module — twice, for *is this located* and
> *is this open* — and the test failed on the third file exactly as designed.
> Both questions moved into `core/temporal.py` as `located` and
> `is_open_boundary`, which is where a fifth endpoint kind will decide how they
> answer. That is the rule working rather than the rule being annoying.
>
> **What accepting a boundary is *for*, stated because it is not obvious:** while
> a period is open, nothing can be concluded about it and its successor together,
> so §11's soundness check is blocked on exactly the pairs it most wants to see.
> Closing it is what lets `assertions_are_disjoint` fire. The two halves of §9
> and §11 are one loop: the agent judges a succession, reflect proposes the
> boundary it implies, and the check can then see an inference that spans it.
>
> Cost: ~10% of a `reflect` call, linear, the same shape as the soundness phase —
> measured in `BENCHMARKS.md`, along with the repeated active-node scan that
> `reflect` now does several times over and that a pass-scoped cache would remove.

#### The shape of it — the "Saint Petersburg Problem"

The city was Saint Petersburg, then Petrograd (1914), then Leningrad (1924),
then Saint Petersburg again (1991). **Every one of those was true.** Ingest
*"the city is called Leningrad"*, later ingest *"the city is called Saint
Petersburg"*, and the graph has no way to record that both are correct over
different periods. It has exactly two things it can do with the pair, and both
are wrong: treat them as a contradiction (neither is false), or supersede one by
the other (which marks a historical truth as though it had been an error).

#### Evidence — what the model actually holds

- **`Fact` has no validity fields** (`core/types.py:291`): `created_at` (ingest
  time), `superseded_at`, `status`. Same for `Topic` and `Inference`.
- **Content time is *mention* time, not validity time.** `propose_timepoints`
  (`pipelines/timeline/functions.py:168`) builds `TIMELINK` edges from
  `detect_temporal_expressions(content)` — dates *named in the text*. The two
  differ routinely: *"Napoleon was born in 1769"* mentions 1769 and is valid
  always; *"the city is called Leningrad"* mentions nothing and is valid
  1924–1991. **The case that needs a date is exactly the case that gets none.**
- **`as_of` is transaction time** — "nodes that existed and were still active at
  `at`" (`server.py:891`). It answers *what did the graph hold then*, never
  *what was true then*. The valid-time counterpart does not exist.

#### Why this is graph-wide and not a dedup precondition

1. **Supersession conflates two opposite things.** `supersede_by`'s own
   docstring (`tools.py:1242`) says it resolves "an outdated fact **or** a
   same-frame contradiction". Those are epistemically opposite: *we were wrong*
   (a correction — the claim should never have been believed) versus *the world
   moved* (an evolution — the claim was right and remains right **of its
   period**). Both produce `NodeStatus.SUPERSEDED`.
2. **So the graph forgets history by filing it as error.** Once "called
   Leningrad" is superseded it leaves the active set. Note that `NodeStatus`
   already draws this kind of distinction where someone noticed it mattered —
   `ARCHIVED` carries the comment "retired for triviality rather than for being
   wrong" (`core/types.py:38`). `SUPERSEDED` needs the same care and does not
   have it.
3. **Contradiction detection is unsound in both directions.** Claims true of
   different periods read as contradictory; genuine contradictions about the
   same instant are indistinguishable from ordinary change.
4. **Inference is unsound.** The system's central operation derives inferences
   from sets of facts. With no validity, an inference can combine claims that
   were never simultaneously true, and nothing detects it. This is the strongest
   form of the problem: not a display defect but a soundness defect in the layer
   the system exists to provide.
5. **Search returns period-bound claims as current**, with nothing marking them.
6. **Corroboration (#51) inflates** — two claims about different periods read as
   agreeing about one.
7. **Dedup (#52) cannot be made safe**, which is where this started.

#### What already exists to build on

`Timepoint` **already models intervals** (`start`/`end`, `core/types.py:377`)
and already tolerates vagueness (`label` alone, e.g. "during the Renaissance").
The shape is present; it is simply attached to timelines and mention-time rather
than to claim validity. `Timeline` carrying its own `now` is the precedent for
"a frame has its own present". And metacontexts already handle *true within a
frame* — validity is the temporal analogue, and the two should compose: a
fictional claim valid over a fictional period.

#### Validity is a *set* of intervals, not an interval

The constraint that decides the design. A claim can be true, stop being true,
and become true again — repeatedly. *"The Labour Party is in government"* holds
over 1945–51, 1964–70, 1974–79, 1997–2010 and 2024–, and the gaps are as real
as the spans. Saint Petersburg is the same shape: the city carried that name,
lost it, and regained it in 1991.

Three consequences, each of which eliminates something:

- **Recurrence breaks supersession as a model of change.** Supersede "Labour is
  in government" in 1951 and the same claim becomes true again in 1964. Nothing
  in the graph can express that: you would have to resurrect a superseded node,
  or create a second node identical to the first, which is the duplication #52
  exists to prevent.
- **It also breaks a lineage chain (c).** Two claims that alternate would need
  `succeeded_by` edges in both directions between the same pair — a cycle, and
  meaningless as lineage.
- **It makes dedup easier rather than harder, which reverses #52's outlook.**
  "Labour is in government" ingested in 1997 and again in 2024 is *one* claim
  with two validity intervals. Under a set model the merge is correct and the
  intervals union. Dedup's danger was always claims that differ; identical
  claims recurring are the case a set model handles cleanly.

#### The decision (no code before it)

Broadly, valid time alongside the transaction time the graph already has.

- **(a) Two nullable datetimes on the node** (`valid_from` / `valid_to`).
  **Eliminated by the above** — a single pair cannot express a disjoint set. It
  also needed `None` to mean both "unknown" and "always", which are different
  claims, the same defect this file has caught three times in other fields.
- **(b) Validity `Timepoint`s attached by a new edge type — many per node.**
  Reuses the interval shape, inherits vagueness for free, gives the set
  naturally (a node simply has several), and composes with timelines and
  metacontexts. Heavier, and needs interval-overlap logic that nothing in the
  codebase has yet.
- **(c) Model state-change as an explicit relation** — a `succeeded_by` distinct
  from `superseded_by`. **Eliminated as a complete answer** by recurrence, though
  its cheap half survives as the first step below.

**Recommendation: (b)**, now on two grounds rather than one. Vagueness is the
normal case — "during the war", "under the USSR", "before the merger" — and
`Timepoint` is the only shape in the model that already handles it without
guessing a date, a principle `propose_timepoints` is careful about today. And
multiplicity falls out for free: several validity edges per node, which is
exactly the set the problem requires.

**Open sub-question for whoever takes it:** whether the gaps need to be
explicit. A set of true-intervals leaves everything outside them ambiguous
between "known false" and "unknown", which is the `None`-means-two-things trap
in a new place. Closed-world within a stated span is probably the answer —
"between 1945 and now, these are the intervals" — but it is not obvious and it
should not be decided by accident.

**But the smallest useful step is independent of that choice, and went first:
split `SUPERSEDED` into "corrected" and "no longer current".** It needs no
interval model, it stops the graph filing historical truth as error, and it is
what makes the honest version of search filtering and inference-validity
checking possible at all.

> **✅ Step 1 done 2026-08-12** — guarded by
> `tests/pipelines/test_supersession_kind.py` (16 tests, both backends).
> `NodeStatus` gained `CORRECTED` and `HISTORICAL`; `SUPERSEDED` survives as the
> legacy value, since pre-existing rows genuinely do not record which kind they
> were and inventing an answer would be a lie. `update`, `supersede_by` and
> `apply_reflection`'s supersession specs take a required `because` —
> `"it_was_wrong"` or `"the_world_changed"` — spelled as sentences because the
> caller is a language model and the sentences *are* the judgment. No default:
> that is the finding, not an oversight.
>
> Two things came with it. Every reader that said `== NodeStatus.SUPERSEDED`
> now uses `SUPERSEDED_STATUSES`, because the equality would still have run and
> silently stopped matching two cases in three. And **archival now excludes
> `HISTORICAL`**: a node retired because the world changed is still true of its
> period, so ageing it out would be the same defect one level down. That is the
> reader which keeps this from being a field nobody consumes — the trap #44 was.
>
> **Still open below**: this does not solve recurrence. A claim that becomes
> true *again* has nowhere to say so, which is what the interval-set model is
> for. One further limitation found while building it and deliberately left
> alone: `supersede_node_tx` migrated the old node's edges onto the replacement,
> which is right for a correction and wrong for a world-change — the historical
> node should keep its own provenance. It was thought to need the validity model
> first; it did not. Filed as **#54** and **fixed 2026-08-12**, before this
> entry is built.
> *(Review 2026-08-12: the waiting judgment is reversed for the interim floor —
> filed as **#54**. Migration is a move, not a copy, so the cost of waiting
> accrues in data. The full ownership question still waits for the decision.)*

> **Review 2026-08-12 — six problems the recommendation has to answer before
> code. None overturns (b); each is a place where (b) as written is not yet
> decidable. They are the agenda for the design decision, not reasons to delay
> it.**
>
> 1. **Step 1 and (b) disagree about what a world-change *is*.** Step 1 models
>    it as node replacement: old → `HISTORICAL`, new node, `superseded_by`
>    edge. Under (b), a recurring claim is **one `ACTIVE` node whose intervals
>    open and close** — the 1951 election closes an interval; it does not
>    retire a node. These are two mechanisms for the same event, and if both
>    survive, the agent facing a world-change must choose between them — the
>    forced-wrong-choice pattern of the verdict table, one level up, at
>    mechanism grain, and harder to guide than a two-value `because`. The
>    decision must say which mechanism owns the event once (b) lands. The
>    likely shape: intervals own world-change for a *recurring or dated* claim,
>    supersession shrinks to corrections, and `HISTORICAL` survives for claims
>    whose successor is a *different* claim rather than a dated recurrence —
>    but that is a decision to make explicitly, not a default to inherit.
> 2. **The empty validity set is the model's real centre.** Validity is
>    precisely what is *not in the text* — "the city is called Leningrad" names
>    no date — so intervals come from agent world-knowledge at ingest or review
>    time, supplied unevenly, and most facts will never get any. "No validity
>    edges" will then mean *always true* or *unknown* or *nobody bothered*:
>    three claims in one absence, the trap this file has caught four times, now
>    at the centre of the model. The open sub-question above covers gaps
>    *between* intervals; the empty set is the bigger case. It needs an
>    explicit representation — e.g. an `always` marker distinct from absence —
>    before any reader is allowed to treat absence as either.
> 3. **Ingest order is not validity order.** §5.1's nomination judges each pair
>    from the newer *document*, so a 1970s memoir ingested today nominates
>    "called Leningrad" against the current fact — and a recency-driven
>    `supersedes`/`succeeds` verdict points backwards in time. Succession
>    verdicts must be validity-directed, not arrival-directed, and for undated
>    pairs that requires world knowledge the judging agent may not have. Which
>    argues for `succeeds` verdicts carrying their proposed intervals, so a
>    human reviewing the worklist can check the direction rather than trust it.
> 4. **Vague timepoints make the soundness check three-valued.** The motivating
>    check — flag an inference combining claims never simultaneously true —
>    needs interval overlap, and label-only timepoints ("under the USSR") do
>    not compute overlap. So the check returns overlap / disjoint / **unknown**,
>    and unknown is the *common* outcome, since vagueness is the normal case by
>    this entry's own argument. What unknown does — pass silently, pass with a
>    flag, or block — is a decision; pass-with-flag is the honest default, but
>    it should be chosen rather than inherited from whichever branch the code
>    happens to take.
> 5. **Retrieval has no reader yet, and `as_of` will be misread.** Today
>    `HISTORICAL` is invisible at search — `vector_search`/`query_nodes`
>    default `status=ACTIVE`, and the split's only consumer is archival
>    exclusion — so from a searching agent's view `HISTORICAL` and `CORRECTED`
>    are identical: gone. The promised "retrievable as true-of-its-period"
>    needs a designed surface (a valid-time parameter on `search`, or
>    included-with-a-label), and without one an agent asked about the past will
>    re-ingest the historical claim as a fresh node — manufacturing exactly the
>    duplicate #52 will later be invited to merge with the current claim.
>    Naming discipline belongs in the same decision: `as_of` is *transaction*
>    time ("what did the graph hold then"), and once valid time exists callers
>    will read it as "what was true then". Adopt the bitemporal vocabulary
>    (valid time vs transaction time) before the axes blur — this territory is
>    well-trodden (bitemporal databases; Snodgrass; XTDB/Datomic) and the traps
>    ahead (open intervals, unknown-vs-forever, current-flag queries, interval
>    indexes) all have prior art worth reading before inventing.
> 6. **Validity timepoints need a concrete home.** `Timepoint`s are embedded in
>    a `Timeline`, not graph nodes, and `TIMELINK` carries `timepoint_id` in
>    edge *metadata* — a weak reference the read path resolves to an empty row
>    rather than an error (`propose_timepoints` docstring says so). "Several
>    validity timepoints per node" must say which timeline hosts them (a
>    reserved validity timeline? per-frame ones, given the claim above that
>    validity composes with metacontexts?) and whether the weak-reference shape
>    is strong enough for something inference-soundness now depends on.
>
> Separately filed as actionable now: **#54** — world-change supersession
> strips the historical node's provenance. That is data damage accruing with
> use, and it does not wait for this decision.

---

#### ✅ T1 decided (2026-08-12) — the validity model

The decision was split into three topics in dependency order: **T1** what a
validity interval is and where it lives, **T2** which mechanism owns a
world-change, **T3** retrieval and naming. T1 is settled and recorded here. T2
and T3 remain open below.

**T1 replaces recommendation (b) rather than confirming it.** Validity is not
`Timepoint`s hung off a new edge type. It is a new type, carried on the
*provenance* edge, measured against a named clock. Close enough to (b) to have
grown out of it, different enough that (b) as written should not be built.

**Vocabulary, settled first because it is most of the fix.** This is **valid
time** — when a claim was true. `created_at`, `superseded_at` and `as_of` are
**transaction time** — what the graph held, and when. The two axes blur silently
once code exists and are near-impossible to separate afterwards, so the words
are fixed before the fields. Prior art is deep (Snodgrass; XTDB, Datomic) and
worth reading before inventing.

##### 1. What carries validity

Facts and inferences. **Topics carry none**, and the reason is conceptual rather
than structural: validity is a property of a *claim*, and a topic is a subject,
not an assertion. There is nothing there to be true, so nothing to be true
*during*. "The BBC" is timeless; *"the BBC was founded in 1922"* is the fact
that holds the date.

The structural rule first proposed — *no source, no validity* — was wrong and is
recorded so nobody re-derives it: `Topic.source_id` is `str | None` and topics
extracted from text **do** carry one. Only entity and tag topics do not. The
conceptual rule excludes all topics; the structural one would have admitted some
by accident.

Inferences carry validity **twice**: asserted from their own source, and derived
from their premises. The mismatch is the point — see §11.

##### 2. Where it lives — on the `sourced_from` edge, not on the node

A node-level set has to union what its sources assert, and union takes one
careful source and one sloppy one and produces a period **neither claims**. That
is the same failure as a false dedup manufacturing corroboration: the error mode
of the combination rule is exactly the quantity being recorded.

Provenance is where #46's per-source `confidence` is already going, for the same
reason: a value describing what *this source says* must not outlive the source.

Two things then follow rather than being chosen:

- **Intervals survive merges for free.** Merging migrates edges, so per-source
  validity rides along with no combination rule to invent — the property that
  made per-source support the right call in #46.
- **One edge per (node, document) carries a *list*.** A single source can assert
  several disjoint periods, which is the recurrence case the entry above is
  built around.

##### 3. Reading it back — per source, no default collapse

A query answers with `(source, interval)` pairs. **No built-in collapse ships in
the first cut.**

The worked reason, because the temptation to add one is strong. Fact *"Labour
was in government"*, two sources: **A** (2011 almanac) 1997–2010, **B** (sloppy
blog) 1995–2010. Ask about 1996.

- **Union** → yes. Wrong, and note the shape: the bad source widened the answer
  and the good one cannot narrow it.
- **Intersection** → no. Right here. Now let **A** say 1997–2010 and **B** say
  2024–present, both correct, different episodes: intersection yields *empty* —
  "never in government".

Union breaks when sources disagree about the **same** episode; intersection
breaks when they describe **different** episodes; nothing in the data says
which. That is #52's state-versus-event distinction arriving by another road,
and it is why no collapse is safe by default. A caller wanting one answer
supplies its own rule.

Costs, stated rather than discovered: comparison is O(sources_A × sources_B),
fine at one to three sources each and worth watching; and every consumer handles
a set rather than an answer. Taken anyway, because a collapse is easy to add and
near-impossible to remove once callers depend on it — and because a default
collapse is the "one number condensing too much" that #46 already rejected.

##### 4. The interval type — three endpoint states

Endpoints are **point** (concrete instant or free-text label), **unknown**
(there is a boundary; we do not know where), or **unbounded** (there is no
boundary).

`unknown` and `unbounded` being different values is the load-bearing part.
*"The city is named Placeberg"* has an unknown start — it may or may not always
have been so, and assuming either is a fabrication. *"Water is H₂O"* is
unbounded. Collapsing them was the first draft of this design and it reproduced
the empty-set ambiguity one level down.

This is why `Timepoint` is **not** reused: its `start: datetime | None` means
both, and changing it would alter what existing timeline data means. `Timepoint`
stays what it is — *mention* time, dates named in content — which is a different
thing from *true during*. One fact routinely has both, and *"the 1991
renaming"* mentions 1991 while *"the city is called Leningrad"* is true until
1991.

**Labels are stored and never silently resolved.** A label is what the source
said; a bound is what can be computed. "During the Renaissance" stays text and
contributes *unknown* to any comparison unless someone resolves it explicitly.
Same split as content versus embedding.

**Endpoints are a discriminated union** (Pydantic `Literal` discriminator — a
union of `BaseModel`s, not a class hierarchy, per CLAUDE.md). The extensibility
rule is discipline rather than schema: **no consumer branches on the endpoint
kind.** Everything downstream asks the comparison question and consumes the
answer, so adding a kind — a probability distribution, if that day comes —
touches one module. Worth a structural test asserting the kind set is referenced
in exactly one place.

Name the type for an imprecise instant generally rather than for validity
specifically: `published_at` uses it too (§7), and a document should not have to
depend on something called validity-anything.

##### 5. The clock — keyed by timeline, not by metacontext

Each interval names the **timeline** it is measured against. The value is
carried on the edge; only the clock is a reference — so a missing timeline
leaves the interval fully readable and merely incomparable across clocks. That
is a real difference from the weak reference the review flagged in `TIMELINK`,
where a missing `timepoint_id` resolves to an empty row that reads as data.

Timeline rather than metacontext, because the two axes cross in both directions:

- **One frame, two clocks.** A change to the history of a fictional universe has
  a place on the real timeline (when the revision was published) *and* on the
  in-universe timeline (when the events happen). Keying by metacontext cannot
  split them.
- **Two frames, one clock.** Competing accounts of real history both run on CE
  dates and should compare directly.

`Timeline` is already the right object: `reference_time` exists, and its comment
already argues that a fictional timeline's present is a fact about that world.
Real-world facts use a default wall-clock timeline (`reference_time = None`
already means *follow the wall clock*), so the common case needs no decision
from any caller.

**Cross-clock comparison returns `unknown`, never `disjoint`.** Useful
side-effect: an inference drawn across an in-universe fact and a real-world fact
is temporally uncheckable, which is itself worth surfacing — the temporal
sibling of `cross-frame`.

##### 6. Interval semantics outside the stated span — open world

*Recorded from consequence rather than from discussion; flagged for
confirmation.*

An interval says what a source **asserts**. It asserts nothing about the
outside. So a moment outside every stated interval is **unknown**, not false —
consistent with the rest of the model never inventing information. Closed-world
assertions ("Labour governed *only* during…") are rare and would need their own
marking; none is proposed.

This weakens §11's check and the entry above overstates it. With open-world
semantics nothing can prove two claims were **never** simultaneously true. What
the check can honestly say is *no source asserts them true at a common moment*,
which fires only when both facts have intervals and those intervals do not
intersect. Narrower than "provably never simultaneous", still useful, and the
wording of the guarantee matters more than its strength.

##### 7. `published_at`, and the witness point

`RawDocument.created_at` is **ingestion** time. Using it as evidence about when
a claim held is transaction time wearing valid time's clothes — a 1970 memoir
read today carries `created_at = 2026`. There is nowhere else to put a real
date: `source` is free text like `"ISSUES.md"`, `metadata` is untyped.

- **`RawDocument` gains `published_at`, optional**, using the imprecise-instant
  type from §4 so a document dated "1990" is not stored as midnight on 1 January
  1990. It earns its place independently of validity — it bounds what a source
  could have known, and it is what anyone would sort or weigh sources by.
- **There is no fallback to `created_at`.** No `published_at` means no witness
  point. A fallback is the original bug with an extra step: every undated
  document would claim its facts were witnessed on the day it happened to be
  ingested, and a graph rebuilt next year would say something else. Same lesson
  as `retrieved_at` staying nullable, and worth stating in the field comment
  because a fallback is the natural thing for someone to add later.
- **Intervals carry their own optional witness point** — "this interval is
  asserted to contain T". It is *not* redundant with `published_at`: with three
  endpoint states there is no way to express "contains 1990", because that is a
  bound and bounds are what we chose not to model. Without it, two undated facts
  never provably overlap and §11 never fires.

The two come apart in both directions, which is why a boolean
`asserted_as_current` was rejected: an undated document saying "as of March
1990…" has a witness and no `published_at`; a diary published in 1990 recounting
1985 in the present tense has both, differing. One witness point per interval —
a source asserting a claim at two separated moments writes two intervals.

##### 8. Provenance of the interval itself — stated, inferred, never invented

**The agent is not a source.** Models hallucinate, and a hallucinated interval
is indistinguishable from a documented one once stored. This also keeps the
architecture intact: the server makes no LLM calls, and the agent is a conduit.

Accepted consequence, stated because it is the motivating example: **the
Leningrad case cannot get its 1991 boundary from agent world knowledge.** A 1970
document yields "called Leningrad, witnessed 1970, end unknown" and the graph
never learns about 1991 unless a document says so, or unless a second document
lets reflect propose the boundary (§9). Coverage is bounded by what is ingested,
not by what the model knows.

But the line between copying and inventing is not clean, and pretending it is
would leave "stick to the source" as a prompt instruction with nothing checking
it. Judging **tense** — whether *"the city is called X"* leaves an interval open
— is already reading rather than copying. So every interval is marked:

- **stated** — dates present in the text;
- **inferred** — from tense, context, or two sources jointly (§9);
- **world knowledge is forbidden**, not marked.

A caller can then filter to stated-only, which is what makes the discipline
auditable rather than aspirational.

##### 9. When it is written — ingest extracts, reflect proposes

**Ingest** is where the source text is actually present, and two things are
visible only there: **tense**, and **dates stated in the text**. Reflect has
extracted facts and a graph, not a document; asking it to do this work means
re-reading segments to do ingest's job late with less context.

**Reflect** is not optional garnish, because the motivating case is structurally
invisible at ingest. Document 1 (1970): *"the city is called Leningrad."*
Document 2 (2000): *"the city is called Saint Petersburg."* **Neither states an
end date.** Only reflect, seeing both, can propose that the first interval
closes before the second opens. Such a boundary is *inferred* per §8 and
surfaces as a **proposal for review**, never written silently.

##### 10. Comparison — four values, `unknown` among them

`before | after | overlap | unknown`. `unknown` is its own value and never a
probability: *no information about the ordering* and *genuinely even odds* are
different claims, and collapsing them is the defect this file has now caught on
`relevance`, `novelty`, `confidence`, and the empty validity set. If
distributions ever land, a probability rides on the three **known** answers and
never stands in for the fourth.

This is a deliberate simplification of **Allen's interval algebra** (thirteen
relations: before, meets, overlaps, starts, during, finishes, equals, and
inverses). Four suffice for the soundness check, and the richer relations are
derivable from the endpoints on demand, so nothing is lost by not storing them.
Ordering is worth surfacing even when both endpoints are precise.

##### 11. The soundness check — flags, never blocks

The graph makes no inferences; the agent does and the graph stores them. So this
is a check over **stored** inferences, run at reflect or on demand: an inference
whose premises' asserted intervals do not intersect is flagged as unsound.

Two properties are required, not optional. It **flags and never blocks** —
`unknown` is the common outcome and a blocking check on unknown is unusable. And
it **never fires on unknown**, only on non-intersecting asserted intervals, per
§6. Rare, and high value when it fires: sparse-but-critical is the shape of this
whole feature.

*Second pass (2026-08-12):* with per-source validity, "the premises' asserted
intervals" needs a collapse rule after all, and it must be named here or
someone will invent the wrong one. The rule is the **existential union** per
premise — the set of moments *some* source asserts it true — and the check
fires only when no pair of asserted intervals across the two premises
intersects. This is the one collapse §3's no-default-collapse rule permits,
because its error direction is safe: a sloppy, over-wide source can only
*suppress* a flag, never manufacture one. An implementer reaching for the
intersection instead gets false flags — the dangerous direction — which is why
the rule is written down and pinned by a test below.

##### What T1 closes from the review's six

| Review item | Status after T1 |
|---|---|
| 2 — empty set means three things | **Closed.** `unknown` ≠ `unbounded` on endpoints, so the empty set means exactly *no information* (§4) |
| 4 — vague timepoints make the check three-valued | **Closed.** Four values with `unknown` explicit; flags never block; fires only on non-intersecting assertions (§10, §11) |
| 6 — validity timepoints need a home | **Closed.** On the `sourced_from` edge, a new type, clock by timeline reference (§2, §4, §5) |
| 3 — ingest order is not validity order | **Partly.** Direction now comes from `published_at` and witness points rather than arrival (§7); succession *verdicts* remain T2 |
| 5 — retrieval reader, `as_of` naming | **Vocabulary fixed** (valid vs transaction time); the retrieval surface is T3 |
| 1 — which mechanism owns a world-change | **Open — this is T2**, and it decides #54's shape |

##### What T1 changes elsewhere

**#54 gets a stronger argument and a different fix.** Validity lives on
`sourced_from`, and `supersede_node_tx` **moves** those edges to the replacement
— so a world-change supersession strips the historical node not merely of its
provenance but of **its validity intervals**, which are the only thing making it
"true of its period". The case is no longer "it cannot say who asserted it" but
"it cannot say *what period*". Ordering is unchanged; the fix must be written
with intervals in mind.

**#46 gains a consistency check.** Per-source confidence and per-source validity
now sit on the same edge for the same reason. Whatever shape one takes, the
other should match.

---

#### ✅ T2 decided (2026-08-12) — which mechanism owns a world-change

The review's item 1: step 1 models a world-change as node replacement, while the
interval model would have it close an interval on a node that stays `ACTIVE`.
Two mechanisms for one event, and if both survive the agent must choose between
them at mechanism grain.

**The answer is that they are not alternatives.** They answer different
questions, both happen, and the agent's only judgment stays the
correction-versus-world-change call that `because` already asks for.

##### The option T1 eliminated

The attractive answer before T1 was **pure intervals**: delete `HISTORICAL` as a
stored status and derive it — a node is no longer current when no interval
contains now. T1 makes that unimplementable, and it is T1's own honesty that
does it.

Deriving *not current* requires **no interval containing now**. Under T1
endpoints are commonly `unknown` and semantics are open-world (§6), so an
interval `(unknown, unknown)` **might** contain now. The derivation returns
`unknown` for very nearly every node, and a retrieval filter answering "unknown"
about almost everything is not a filter.

So the property that makes T1 correct — never asserting a boundary nobody
stated — is exactly what stops a status being read off the intervals. Worth
keeping as a warning: *a derived field is only as available as the data it
derives from, and a model built to admit ignorance will propagate that
ignorance into everything computed from it.*

##### Two questions, not one mechanism

| | Asks | Nature |
|---|---|---|
| **Validity intervals** | *When was this true?* | A claim about the **world**. Source-attributed, sparse, open-world; the agent may not invent them (T1 §8) |
| **`NodeStatus`** | *Is this the graph's current answer?* | A claim about the **graph**. Always present, closed by construction, and legitimately the agent's to set |

That split also answers an objection T1 raises against itself: marking a node
`HISTORICAL` does **not** violate "the agent is not a source". That rule governs
assertions about the world. A status is bookkeeping about the graph's own
answer — the same tier as `archived`, which nobody thinks is a claim about
reality.

Bonus, and worth building once both exist: the two can **disagree**, and the
disagreement is checkable without asking anyone. An interval closed in 1991 on a
node still marked current is a defect the graph can find in itself.

##### The real problem was the edge, not the status

Recurrence, made concrete. *"Labour is in government"* is retired `HISTORICAL`
in 2010. A 2025 document asserts it again. Ingest looks the content up, and
`get_node_by_content` **filters to `ACTIVE`** (`storage/memory.py:356`), so it
finds nothing and **creates a second node with identical content** — precisely
the duplication #52 exists to prevent, manufactured by our own supersession.

> **Correction (2026-08-12, second pass).** The mechanism above misstates the
> code: `get_node_by_content` is called in exactly three places, all
> `node_type=TOPIC` (`tools.py:164`, `:283`, `:884`) — **no fact path looks
> content up at all**, so the duplicate is manufactured by #52's gap whether or
> not supersession happened. And exact match would barely help if it were
> wired: two documents almost never phrase a claim identically (#52's own
> analysis), so a verbatim twin check catches only verbatim recurrence. The
> conclusions below survive; the load-bearing detector changes — see *Second
> pass* below.

`restore` cannot undo it either: it is `ARCHIVED`-only, and its docstring gives
the right reason — *"restoring an archive must not resurrect a node that was
superseded for being wrong"* (`mcp/tools.py:2024`). True of `CORRECTED`. It now
also blocks `HISTORICAL`, which is the one case that must come back.

The deeper fault is the **edge**. `superseded_by` claims *replacement*, and
recurrence falsifies replacement: Labour returning to government does not make
the 2010 retirement retroactively wrong — it makes "replaced" the wrong word for
what happened.

##### The decision — split the edge the way the status was split

| Event | Edge | Status left behind | Reversible |
|---|---|---|---|
| We were wrong | `superseded_by` | `CORRECTED` | **No** — terminal |
| The world moved | **`temporally_followed_by`** (new) | `HISTORICAL` | **Yes** |

`temporally_followed_by` states temporal order rather than replacement, so it
survives recurrence: the Saint Petersburg claim temporally followed the
Leningrad one in 1991, and the Leningrad claim becoming current again would not
contradict that. `superseded_by` keeps meaning what it says and stays terminal.

**This is §13.2's missing sixth verdict**, arriving as an edge rather than as a
verdict label. The review predicted it would be needed; this is where it lands.

Two consequences make recurrence work:

- **`HISTORICAL` is restorable; `CORRECTED` is not.** `restore`'s existing rule
  is right and needs only to name the two statuses separately instead of
  treating supersession as one thing.
- **Ingest must see `HISTORICAL` twins.** `get_node_by_content` filtering to
  `ACTIVE` is what turns a recurrence into a duplicate. It should surface the
  historical twin for reactivation rather than silently creating a second node.
  Reactivation stays an **explicit act** — flipping a node live behind the
  caller's back, on an exact-string match, is too brittle to do silently.

##### On the name

`succeeded_by` was the incumbent — option (c) above proposed it — and it is
rejected. Not mainly for confusion with success, which context mostly defeats,
but because **`SUCCEEDED_BY` and `SUPERSEDED_BY` are near-homographs that mean
opposite things**: three letters apart, mid-word, in the same enum, valid in the
same position, and denoting exactly the two halves of the distinction this issue
exists to draw. A misread in review produces no signal at all. `followed_by`
alone was rejected in turn because this graph also carries `based_on` and
`implies`, so a bare "A followed by B" invites a **causal** reading — a more
plausible misreading, and therefore a worse one.

##### The edge does not claim adjacency

Deliberate, and it rules out names like `follows_next_in_time`. Saint Petersburg
→ Petrograd (1914) → Leningrad (1924) → Saint Petersburg (1991): if the edge
meant *next*, discovering the Petrograd step later would make an existing edge
**wrong** and force a rewire. Edges here are the durable source of truth, and
silently rewiring them on new evidence is the mutation this design avoids
everywhere else.

So the edge records **one observed transition** and says nothing about what may
lie between. Three renames create three edges as each is observed, giving the
chain by construction without anyone asserting completeness — the same
open-world stance T1 took. The chain is walkable but not guaranteed gapless, and
ordering comes from validity intervals where they exist rather than from edge
adjacency.

##### Housekeeping this pulls in

`HISTORY_EDGE_TYPES` is `{SUPERSEDED_BY, MERGED_INTO}` (`core/types.py:137`)
and feeds both `migration_disposition` and the default-traversal exclusion. The
new edge **joins it**: it is lineage rather than knowledge, and if it migrated
on a later supersession it would detach from the transition it records.

##### Second pass (2026-08-12) — the detector, the verdict, and two edge constraints

Three binding amendments from the post-decision review.

**1. The recurrence detector is similarity nomination, not content lookup.**
For recurrence to be *seen*, the candidate-generation pass (`check_conflicts`
at ingest; the `reflect` sweep as safety net) must include **`HISTORICAL`**
nodes among its candidates — today both scan active facts only, so the
historical twin is never nominated and no judgment is ever invited. The
exact-content lookup is still worth wiring into ingest as the cheap
verbatim-match floor (#48's second caller), but it is the minor half.

**2. Recurrence is a verdict of its own: `recurs`.** The taxonomy otherwise
forces a wrong one — `redundant` assumes an active twin, `succeeds` assumes a
*different* claim following. `recurs` means *the same claim, previously
retired `HISTORICAL`, asserted true again*. Its action: surface the historical
twin and propose **reactivation** — `restore` to `ACTIVE` plus a new
`sourced_from` edge carrying the new document's interval, the prior intervals
and the `temporally_followed_by` record untouched. Reactivation stays an
explicit act (worklist → `apply_reflection`), per the bullet above.
`REVIEW_EPISTEMIC.md` §3 carries the verdict row; §5.1 carries the recall
requirement.

**3. Cycles and repeated transitions are legal, and every reader must know.**
This entry eliminated option (c) *because* alternation makes cycles — then
correctly reintroduced cyclic edges without saying so. Said now: Saint
Petersburg's chain returns to its own node, and *"Labour is in government"* →
*"the Conservatives are in government"* recurs in the **same direction** in
1951, 1970, 1979 and 2010. Two consequences:

- **Every `temporally_followed_by` walk must be cycle-safe.** T3's lineage
  collapse is the first such walker; "follow to the terminal successor" does
  not terminate on this graph.
- **Parallel edges between one pair are legal — one per observed transition —
  and nothing may dedup them by `(src, dst, type)` signature.**
  `_migrate_edges_inplace` already skips the type — `migration_disposition`
  answers `keep` for everything in `HISTORY_EDGE_TYPES`, which this joins — so
  today's one signature-dedup site is safe by construction; the constraint is
  recorded so the next dedup site does not collapse four transitions into one.

##### Third pass (2026-08-12) — the two mechanisms the second pass left unnamed

The amendments above say *what* must happen without saying *where*, and both
land on code that today refuses them by design. Decided now, because the entry
otherwise reads as though the recall already exists.

**1. Recall is one `statuses` parameter on `vector_search`, not a caller fix.**
`check_conflicts` does no status filtering of its own — it inherits it from
`vector_search`, which is `ACTIVE`-only *by construction on both backends* and
says why: *"Superseded and merged nodes must never resurface here"*
(`surrealdb_adapter.py:1223`; the same guard at `memory.py:687`). That guard is
what makes `recurs` unreachable, and **the same guard blocks T3's
`include_historical=True` default**. One change, two customers.

So `vector_search` gains `statuses: frozenset[NodeStatus] =
frozenset({NodeStatus.ACTIVE})` on the protocol and both backends. The default
preserves today's behaviour exactly, so no existing path resurfaces anything by
accident. The alternative — a second retired-only lookup method — was rejected
because it duplicates the SurrealDB over-fetch machinery (`_OVERFETCH_FACTORS`,
`_ranked_active_items`), which is the only difficult part of that adapter;
`_active_ids` generalises to a status-parameterised form instead.

**Ships in the same commit: `check_conflicts` candidates carry their `status`.**
Once retired nodes can appear, an agent cannot tell an active twin from a
historical one — and that distinction is the entire basis for choosing between
`redundant` and `recurs`. A candidate list that hides it invites exactly the
misclassification the verdict was added to prevent.

**2. `recurs` resolves through a widened `restore`, not a new tool.** `restore`
is `ARCHIVED`-only (`mcp/tools.py:1856`), but its docstring's reason is narrower
than its check — *"restoring an archive must not resurrect a node that was
superseded for being wrong"* forbids `CORRECTED` and says nothing about
`HISTORICAL`, which T2 already called restorable. Widen it to accept
`HISTORICAL`; keep `CORRECTED` refused, which is now the docstring's literal
meaning rather than an accident of the pre-split enum.

The reactivation and the new `sourced_from` edge (with the new document's
interval) must land in **one transaction**: a node back to `ACTIVE` with no
edge recording why is a claim the graph asserts and cannot attribute. The prior
intervals and the `temporally_followed_by` record are untouched, so the node
ends holding several disjoint intervals — the shape
`TIMELINE_VISUALISATION.md` §13.1 draws as beads on a spine.

**Added constraint (2026-08-17, from the `EVENT_LOG.md` review) — restoration
must not overwrite lifecycle history.** `query_changes` derives its events
from `(superseded_at, status)`, and that pair cannot represent *retired, then
came back* — clearing the timestamp erases the retirement; keeping it makes
the derived kind read `"active"` at retirement time. Since cycles are legal
here, the fix is an **append-only lifecycle episode list** on the node
(`{retired_at, because, restored_at | None}` per episode, current-state
fields kept as a snapshot), and the widened `restore` appends an episode
rather than mutating one. Details and the named test: `EVENT_LOG.md` §6. The
#57 counterpart id rides on the episode.

**And a category guard: cyclical facts never route through this machinery at
all.** "The Christmas holiday period" is a recurrence *rule* — it never stops
being true, so it never retires, never restores, and enumerating its
occurrences as validity intervals is the wrong representation even though T1's
lists could hold them. That is the `CyclicalTimeline` case
(`PROPOSED_FEATURES.md` → *Specialized timelines*). An agent marking such a
fact `HISTORICAL` in January is making a category error; individual
occurrences ("Christmas 2025 in Berlin") are *event* facts, which per #52's
amendment are never interval-unioned into the rule.

##### What T2 unblocks

**#54's shape is settled and it is no longer blocked** — and it was then built
(2026-08-12). A world-change goes through `temporally_followed_by`; the
historical node keeps its own `sourced_from` edges and therefore its validity
intervals, and the replacement gets **none of them**. Both blanket answers were
withdrawn — copying everything fabricates attribution, migrating nothing drops
`has_metacontext` and moves a fiction-frame replacement into base reality.
Migration is **per edge type**, and the table that was #54's is now the code:
`migration_disposition(edge_type, status)` in `epimemer/core/types.py`.

**#48 gains a second caller.** The `get_node_by_content` path must now consider
`HISTORICAL` twins as well as `ACTIVE` ones, so the scan #48 wants to fix for
performance is about to grow a second reason to be touched. Do them together
rather than visiting that path twice.

**Review item 1 is closed.** Item 5's retrieval half and the `as_of` question
remain, and are T3.

---

#### ✅ T3 decided (2026-08-12) — the retrieval surface and the naming

The review's item 5, in two halves: `HISTORICAL` has no reader at retrieval, and
`as_of` will be misread once valid time exists. **This closes #53's design.**

##### The trap, which is the same one for the third time

The obvious surface is a valid-time **filter**: `search(..., valid_at="1980")`
returning claims true in 1980. T1 makes it dishonest by exactly the mechanism
that killed derived status in T2. Validity is sparse and open-world, so
*unknown* has to go somewhere and both destinations lie: exclude it and claims
that may well have held in 1980 vanish; include it and the parameter is not
filtering.

Since most nodes will never carry intervals, exclusion is the default failure —
the caller gets a near-empty list and reasonably concludes *the graph does not
know*, when in fact the graph holds the Leningrad fact and merely lacks a date
for it. **A filter converts missing metadata into a false negative, silently.**

Three times now the same shape has appeared: derived status (T2), interval
comparison (T1 §10), and retrieval. Worth stating once as a rule: **wherever
open-world data meets a boolean question, the answer has three values, and
squeezing it into two is where the lie enters.**

##### Valid-time retrieval returns buckets, not a filter

The same query answers with groups:

- **provably valid at `t`** — intervals cover it;
- **unknown** — the claim is held; its validity cannot be determined;
- *excluded:* provably not valid at `t`.

The caller sees "no dated claims, two undated candidates" and can act on it.
Nothing is dropped silently and the *shape* of the ignorance is visible. This is
`before | after | overlap | unknown` (T1 §10) applied at retrieval rather than
between two intervals — the consistency is structural, not coincidental.

**Staged**: the shape is decided now; the code waits for validity to exist.

##### Reachability — two parameters, and the asymmetry lives in the defaults

| Parameter | Default | Why |
|---|---|---|
| `include_historical` | **on** | Knowledge that is not current is still knowledge — the reason `HISTORICAL` exists at all |
| `include_corrected` | **off** | Kept for the audit trail rather than for reading; re-offering a claim concluded false should be deliberate |

An earlier draft made `CORRECTED` unreachable from search entirely, on the
grounds that it was wrong and should not be re-offered. **Rejected**, for two
reasons worth keeping:

- It contradicts the principle applied everywhere else here — per-source
  confidence, no default collapse on validity, buckets over filters: *report and
  let the caller decide.* An exception needs a justification, and "it was wrong"
  is not one when the node is labelled as such.
- *"What did we believe about X that turned out wrong?"* is a legitimate
  question for an epistemic memory. Under the unreachable version it can be
  answered only by already knowing the node id and walking `superseded_by` — you
  must know what you are looking for before you can look for it. That is not an
  audit trail, it is a filing cabinet with no index.

Retrieval is also the **third** consumer of the `CORRECTED`/`HISTORICAL` split,
after archival exclusion and `restore`, which is what keeps the distinction from
being one only its writer cares about.

##### Default-on requires lineage collapse, or it is a regression

Not optional, and it is the condition under which `include_historical=on` is
safe.

Search ranks by similarity, and a historical claim and its replacement are
near-identical text — *"the city is called Leningrad"* against *"the city is
called Saint Petersburg"*. Both score near the top. A claim with four historical
predecessors fills half a top-10 with versions of one thing and displaces
unrelated knowledge the caller actually needed.

**Fix, using the edge T2 created:** when a historical node and its successor
both match, the successor takes the slot and the historical node **attaches to
it** rather than competing for one. The `temporally_followed_by` chain is
exactly the structure that makes this computable, and the result reads the way
a caller wants — one current answer with its history hanging off it.

Labels ride the existing machinery: `review_labels_for`
(`pipelines/reflection/review.py:32`) already maps each label to the related
node ids a caller can hop to, which is the shape this needs.

##### `as_of` → `graph_as_of`

`as_of` (`mcp/server.py:905`) is **transaction** time — "the nodes that existed
and were still active at `at`". Once valid time exists the bare name is
ambiguous, and SQL:2011 settles the question by precedent: it writes `FOR
SYSTEM_TIME AS OF` and `FOR APPLICATION_TIME AS OF`, prefixing the phrase in
**both** cases because "as of" alone does not say which clock.

The decisive argument is *which* name gets marked. **The unmarked name inherits
the default reading**, and in a knowledge graph the default reading of "as of
1980" is *what was true in 1980* — the wrong axis. Leaving `as_of` bare and
adding `valid_as_of` later marks the safe name and leaves the misreadable one
unmarked, which is backwards.

So: **`graph_as_of`** now, reserving **`valid_as_of`**. Symmetric, short,
self-documenting — what the graph held, versus what was valid. The
standard-matching alternative (`system_time_as_of` / `valid_time_as_of`) is more
canonical and more to type.

This is the only piece of #53 with a migration cost: a public MCP tool name plus
`epimemer_prompts/DEFAULT.md`. It is cheapest now, while one axis exists and
there is no second tool to confuse it with.

##### "Current" is timeline-relative — a constraint, not a decision

Surfaced by T3 and easy to hard-code wrongly. T1 keys intervals by timeline, and
`Timeline.reference_time` is already documented as that timeline's *now* — "a
fictional timeline's present is a fact about that world, not a viewer
preference" (`core/types.py`).

Together those mean *is this claim current?* must be asked **against the
relevant clock**. A claim in a fictional frame is current when its interval
contains that timeline's reference time, not when it contains wall-clock now.
The first implementation of any "is it current" reader will reach for
`datetime.now()`, and unpicking that afterwards is painful — so it is written
down here before anyone writes the reader.

##### What T3 closes

**Review item 5, and with it #53's design in full.** All six review findings are
now answered: 2, 4 and 6 by T1; 1 by T2; 3 across T1 and T2; 5 here. What
remains in #53 is construction, not decision.

---

**Failing test first**: `tests/pipelines/test_validity.py` —

- a fact superseded because the world changed stays retrievable as
  true-of-its-period, and is distinguishable from one superseded because it was
  wrong;
- a claim that holds over two disjoint periods is **one** node carrying both,
  not two nodes and not a resurrection;
- an inference whose premises' asserted intervals do not intersect is flagged,
  and one whose premises are merely `unknown` is **not**;
- a claim with **no** intervals is distinguishable from one asserted always-true
  — absence must not mean two things;
- overlap against a label-only (vague) interval reports **unknown**, not
  disjoint;

and, added by the T1 decision —

- an endpoint that is `unknown` is distinguishable from one that is `unbounded`,
  through storage and back;
- two sources asserting different periods for one fact are readable
  **separately** — no union, no intersection, no collapsed answer;
- intervals measured on different timelines compare as `unknown`, never
  `disjoint`;
- a document with no `published_at` yields **no** witness point — nothing falls
  back to `created_at`;
- an interval marked `inferred` is distinguishable from one marked `stated`, and
  a caller can filter to stated-only;
- validity edges survive a node merge, carrying their source attribution intact;
- a `Topic` has nowhere to put an interval at all;

and, added by the T2 decision —

- a world-change writes `temporally_followed_by`, a correction writes
  `superseded_by`, and neither writes the other;
- a `HISTORICAL` node can be restored to `ACTIVE`; a `CORRECTED` node
  **cannot**;
- ingesting content identical to a `HISTORICAL` node surfaces that node rather
  than creating a second one — the recurrence case, and the one that currently
  manufactures a duplicate;
- a restored node carries **both** validity intervals, and the
  `temporally_followed_by` edge recording its earlier retirement is still there
  and still true;
- `temporally_followed_by` does not migrate on a later supersession.

and, added by the T3 decision —

- a `HISTORICAL` node is returned by a default `search`; a `CORRECTED` node is
  **not**, and both are reachable when their parameter is set;
- when a historical node and its successor both match, the result holds **one**
  slot, the successor's, with the historical node attached — not two;
- a valid-time query returns *provably valid* and *unknown* as separate groups,
  and a node with no intervals lands in **unknown**, never in *excluded*;
- `graph_as_of` answers about transaction time, and nothing named `as_of`
  remains to be misread.

and, added by the second-pass review —

- similarity nomination surfaces a `HISTORICAL` twin as a candidate — a
  **paraphrased** re-assertion, not just a verbatim one;
- a `recurs` resolution reactivates the twin and attaches the new source's
  edge and interval, leaving the prior intervals and the
  `temporally_followed_by` record untouched;
- the soundness check collapses per-premise sources by **existential union**:
  two premises whose interval sets share no intersecting pair are flagged, and
  adding one wider source interval that bridges them **clears** the flag;
- when only the historical node matches a query (asked in its period's
  vocabulary — "Leningrad"), it holds its own result slot; lineage collapse
  merges only when both match;
- a `temporally_followed_by` cycle terminates every walker, and two
  same-direction transitions between one pair coexist as two edges.

---

### Issue 58 — FTS index backfill runs inside `connect()` with no progress reporting — ⏸ DEFERRED (trigger stated)

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

### Issue 59 — embeddings are truncated at 256 word-pieces with no guard anywhere — ✅ CLOSED (2026-08-21), no defect reachable

> **Closed 2026-08-21 without code, because the remaining half had no target.**
> The 2026-08-20 measurement below halved the scope to segments and prescribed
> option 3 — a truncation flag on `EmbeddingRecord`. Building it turned up the
> premise underneath: **segments are never embedded.**
>
> Four independent checks, and they agree:
>
> - **No path writes one.** All seven `EmbeddingRecord(...)` construction sites
>   take a node id (`tools.py:247, 415, 471, 2241, 2266`;
>   `graph_construction/versioning.py:72, 168`).
> - **No stored record points at one.** 624 embeddings across the two real
>   graphs against 624 nodes — 488/488 in `memory`, 136/136 in
>   `petritype-server` — and zero whose `item_id` is a segment.
> - **Nothing could return one.** `vector_search` resolves every hit through
>   `storage.get_node(item_id)`, so a segment record would be fetched and
>   dropped even if some path wrote one.
> - **Segmentation's own embeddings are transient.** `semantic_similarity.py`
>   embeds sentences to find boundaries and stores nothing.
>
> **So the 256 word-piece window never touches segment text.** Segments reach
> retrieval through BM25 alone (`lexical_search.py`, `corpus="segments"`), which
> indexes the whole field — and that is not incidental, it is why they answer
> §3's question well: *a rare identifier* is exactly what lexical search finds
> and vectors lose.
>
> **What the entry got wrong is worth naming, because it is this file's own
> recurring shape.** "Segments cross the window and they are a search corpus"
> is two true claims with a false join. The measurement was sound — 11.1% of
> real segment text does cross 256 word-pieces, and the worst does lose 48% —
> but a cost was inferred from it without checking that anything pays it. **A
> measured quantity is not yet a measured consequence.** The instrument read
> segment *text* out of the `segment` table, which is exactly where the
> tokenizer question lives, and told us nothing about whether that text is ever
> handed to the tokenizer. It is not.
>
> **The failing test the entry owed is withdrawn with the fix.** A text over the
> window and a prefix of it still embed to the same vector — that is
> unchanged — but for nodes, whose longest real instance reaches 81 word-pieces
> against a 256 window and which are one sentence by construction. Asserting on
> a path with 3× headroom is the guard on an unreachable path that the entry's
> own option 4 declined.
>
> **What is kept instead is the precondition, stated where it would be
> violated.** `EmbeddingRecord.item_id` said "node or segment id" and now says
> what is true, plus the consequence: embedding segments would make this
> truncation real on the day it was added, so the guard belongs with that work
> if it is ever done. Recorded there rather than here because the comment is
> what someone adding it will actually read.
>
> **Not a reason to embed segments.** Whether they should be is a feature
> question and belongs in `PROPOSED_FEATURES.md`, not in a defect entry — and
> `docs/RETRIEVAL.md` §3's argument runs the other way.

> **Measured 2026-08-20 — the suspicion was half right, and the wrong half is
> the one that matters.** Token lengths over 624 real nodes and 108 real
> segments from two graphs of genuinely ingested content
> (`scripts/corpus_measure.py`; full tables in `BENCHMARKS.md`):
>
> | corpus | n | median | p95 | max | over 256 |
> |---|---|---|---|---|---|
> | fact | 350 | 30 | 56 | 81 | **0** |
> | inference | 124 | 38 | 56 | 63 | **0** |
> | topic | 150 | 20 | 38 | 69 | **0** |
> | **segment** | **108** | **148** | **305** | **496** | **12 (11.1%)** |
>
> **Nodes are not at risk and structurally cannot become so.** The longest of
> 624 real nodes reaches 81 word-pieces against a 256 window — 3× headroom — and
> a decomposed claim is one sentence by construction, so this does not drift
> with graph size. The entry guessed the exceptions would be "`Segment` text and
> unusually long inference content"; **inferences top out at 63**, making them
> the *safest* corpus measured rather than a risk.
>
> **Segments cross the window routinely, and they are a search corpus.** 11.1%
> exceed it and the worst loses **48% of its text**. `docs/RETRIEVAL.md` §3 has
> segments answering a different question from nodes and being searched in their
> own right, so a truncated segment is exactly the silent under-return this
> entry describes — just confined to one of the two corpora.
>
> **This resolves the four options, differently per corpus, which is why the
> measurement was worth taking before choosing:**
>
> - **Nodes: accept it, with the number recorded** (option 4). Refusing or
>   chunking would add a guard to a path nothing reaches.
> - **Segments: store the truncation as a fact about the record** (option 3) —
>   the measured token count or a flag on `EmbeddingRecord`, converting a silent
>   gap into a visible one. Refusing (option 1) would fail ingest on 11% of real
>   segments, which is not a defensible answer to "this document has a long
>   paragraph"; chunk-and-pool (option 2) changes what a segment vector *means*
>   and wants its own justification rather than arriving as a truncation fix.
>
> **The failing test the entry asks for is unchanged and still owed**: a text
> over the window and a prefix of it must stop embedding to the same vector
> indistinguishably. Under option 3 they still embed alike — what changes is
> that the record says one was cut.

Filed 2026-08-18. **Called for in `LEXICAL_SEARCH.md` §9 on 2026-08-18 and not
filed at the time** — the same omission that let #57 sit unfiled for a month
after `EVENT_LOG.md` asked for it. Filed now on that precedent, before the
detail is lost.

`all-MiniLM-L6-v2` truncates its input at **256 word-pieces**, and there is no
content-length guard anywhere on the path to it:
`SentenceTransformersProvider.embed` passes `texts` straight to
`model.encode` (`embeddings/sentence_transformers.py`), and nothing upstream
measures, splits or warns. A long fact's tail is simply absent from its
embedding, and nothing says so — not the caller, not the log, not the stored
`EmbeddingRecord`.

**The failure is silent in the direction that matters.** A node whose content
runs past the window is stored, indexed and returned by `graph_stats` like any
other; it is only *unfindable by the part of itself that was cut off*. Vector
search cannot report a miss it does not know it had, so the symptom is a search
that quietly under-returns — the same shape as the defect `LEXICAL_SEARCH.md`
was built to fix, one layer down.

**Lexical search mitigates it incidentally and must not be mistaken for a fix.**
BM25 indexes the whole field on both backends, so a truncated tail is still
reachable *by an exact token in it* — which is real relief for identifiers and
none at all for paraphrase. The underlying gap is separate and untouched.

**Scale is unmeasured, and that is the first piece of work.** 256 word-pieces is
roughly 150–200 English words; a decomposed fact is usually one sentence, so the
suspicion is that almost nothing is affected today and that the exceptions are
`Segment` text and unusually long inference content. **Measure before deciding:**
the distribution of tokenized lengths over a real graph's nodes and segments,
and how many cross the window. A fix chosen ahead of that number is the trap
this file has recorded three times.

**Options, none obvious, all needing that measurement first:**

- **Refuse to embed what will be truncated**, and say so. Honest and loud; makes
  ingest fail on content the caller cannot easily shorten.
- **Chunk and pool.** Embed each window and mean-pool, which is what the model
  does *within* the window anyway. Changes the meaning of a vector, so it wants
  its own justification rather than being slipped in.
- **Store the truncation as a fact about the record** — a flag or the measured
  token count on `EmbeddingRecord` — and let readers decide. Cheapest, and it
  converts a silent gap into a visible one, which is this file's usual verdict.
- **Accept it with the number recorded**, if the measurement says the window is
  never reached in practice.

**Failing test first**, per the workflow: `tests/embeddings/` — a text known to
exceed the window and a prefix of it embed to the *same* vector today, which is
the defect stated as an assertion. Whatever option is chosen, that pair must
stop being indistinguishable — by raising, by differing, or by the record
saying which one was cut.

**Not a lexical-search defect and not fixed by it.** It is filed here rather
than in `LEXICAL_SEARCH.md` §9 for that reason; §9 keeps the pointer.

---

### Issue 60 — `reflect` holds every candidate pair in memory, with no cap — ✅ BUILT (2026-08-21)

> **Built 2026-08-21, as option 2 and at the demoted priority the measurement
> left it at.** `reflect(max_nominations=200)` caps each of the four quadratic
> lists — `similar_pairs`, `contradictions`, `recurrences`,
> `similar_relations`, named in `CAPPED_KEYS` — to its highest-scoring entries,
> and the response carries `truncated: [<list names>]`, empty on an ordinary
> graph.
>
> **The scope is the response, and saying so is part of the fix.** The scored
> tuples inside `similar_pairs` are still one per surviving pair, so peak
> allocation is *not* bounded by this. That is the honest reading of what the
> measurement changed: at 0.0105% real survival the memory argument became ~3
> MB and the unbounded response became the reason to act, so a cap advertised as
> a memory bound would be claiming something it does not deliver. Bounding
> allocation means capping inside the scorer, which is a larger change against a
> problem nobody has.
>
> **Three decisions made during construction rather than inherited:**
>
> - **The count of what was dropped is not reported**, as the entry proposed.
>   A caller told "there were 40,000 more" has no better move than the one it
>   already has, and the tool guidance now says that outright: act on what came
>   back and reflect again, rather than reaching for a bigger number.
> - **Each list is capped after the contradiction/recurrence partition, never
>   before.** One scored set feeds both, so a cap on the set would let the
>   larger half starve the other — and recurrence is the safety net under an
>   opt-in detector (#53 T2). Pinned by a test.
> - **The cap is applied at response assembly, in one place over `CAPPED_KEYS`,
>   not inside each phase.** The phase events keep reporting what the pass
>   actually found, which is what makes a truncated response visible in the
>   viz strip instead of indistinguishable from a quiet graph. The server log
>   line says so too.
>
> **200 is chosen against the distribution, not picked.** Real corpora yield 4
> surviving fact pairs out of 38,226, and the measured rate projects ~5,200 at
> 10,000 facts — so the default sits ~3 orders of magnitude above what a real
> graph returns and still well under a response no agent can read. This is not
> the "invented threshold" trap options 3 and 4 were withdrawn for: that
> objection is about a bar derived from the corpus at run time and therefore
> irreproducible, where this is a fixed, documented, overridable constant.
>
> Tests: `tests/pipelines/reflection/test_nomination_cap.py` (23 across both
> backends). The failing test the entry asked for is
> `TestThePairListsAreBounded`, which returned all 435 pairs before the fix.

> **Measured 2026-08-20, and the projection below does not survive it.** The
> entry's own first option was "measure it honestly first", on the grounds that
> everything else was guesswork until the real survival rate was known. It was
> guesswork, and by four orders of magnitude.
>
> Real stored vectors, real 0.80 threshold, real `all-MiniLM-L6-v2`
> (`scripts/corpus_measure.py`; full tables in `BENCHMARKS.md`):
>
> | corpus | pairs | survivors | rate | median pair similarity | p99.9 |
> |---|---|---|---|---|---|
> | bench fact text (control) | 79,800 | 887 | 1.11% | 0.500 | 0.883 |
> | real facts, `memory` | 38,226 | 4 | **0.0105%** | **0.164** | 0.683 |
> | real facts, `petritype-server` | 2,628 | 0 | **0.0%** | 0.160 | 0.720 |
>
> At the real rate, 10,000 facts project to **~5,200 surviving pairs and ~3 MB**
> — against the ~14 GB predicted below. **Read the distribution rather than the
> rate**: 4 survivors is too few to trust as a rate, but 38,226 pairs locate the
> distribution firmly, and the median real fact pair scores 0.164 with 99.9% of
> pairs under 0.683. The threshold is 0.80. For this to bite, the whole
> distribution has to move, not its tail.
>
> **Where the 49% came from, which is the more reusable finding.** It was
> measured on "similarly templated text" and applied to a fact count — but pair
> similarity is dominated by **text length**, and over the bench's 17-word
> vocabulary it climbs steeply: 0.62% at 4 words, 1.11% at 8 (what `reflect`
> actually scores), 3.70% at 12, 21.8% at 20, **74.9% at a paragraph**. So 49%
> is a real number for some templated text and the wrong number for the pairs
> this issue counts. **A survival rate without the text length it was measured
> at is not a number** — `BENCHMARKS.md` now names the length in every row.
>
> **What this does not establish**, since the temptation is to read it as an
> all-clear: a **claim-duplicate** corpus (the same story from fifty outlets)
> remains untested and was this entry's actual worst case — dev notes are
> subject-similar, which is much weaker; the rate's behaviour *with size* is
> unmeasured, since subsets at n = 50/100/200 gave 0, 0 and 1 survivors, too few
> to fit a trend, so if mutual similarity rises as a graph fills in one domain
> the figures above are a floor; and **nothing here caps anything** — the bound
> is still absent.
>
> **Verdict: option 2 (cap the nominations and say so), demoted from urgent to
> cheap insurance.** It bounds the response as well as the memory, it costs
> little, and it is right independent of the rate — but it is no longer racing a
> failure. The unbounded response, not the memory, is now the better argument
> for it. **Options 3 and 4 are withdrawn**: streaming the phase is a large
> change against a 3 MB problem, and an adaptive threshold was already the
> "invented threshold" trap the lexical work refused twice.

Filed 2026-08-20, from a question about whether `reflect` pulls the whole graph
into memory. It does not — the node reads are linear and modest — but the
**candidate pair lists are quadratic and unbounded**, and the benchmark cannot
see it because of the corpus it uses.

**Measured, in-memory, peak allocation on top of the store:**

| nodes | facts | peak | contradiction candidates |
|---|---|---|---|
| 500 | 250 | 3.9 MB | 16 |
| 1,000 | 500 | 8.5 MB | 58 |
| 2,000 | 1,000 | 20.0 MB | 275 |
| 4,000 | 2,000 | 40.2 MB | 923 |
| 8,000 | 4,000 | 80.4 MB | 3,921 |

Peak is **linear at ~10 KB per node** — that is the node copies and the
embedding matrix, and it is fine. The pair count is **quadratic**, roughly 4×
per doubling, and here it stays small only because almost nothing clears the
threshold.

**The cost of a pair, measured directly.** Same 2,000-node graph, topic
threshold dropped to 0.0 so every pair survives: 124,750 pairs, peak 89.6 MB
against 20.0 MB — **~580 bytes per surviving pair**, across the scored tuples,
the nominated list and the response dicts.

**Nothing bounds the survivor count** — no limit parameter, no top-k, no size
check anywhere on the path, and every survivor goes into the response. The only
thing that shrinks the set is `already_linked`, which excludes pairs an agent
has already joined by a `similarity` or `contradiction` edge: real mitigation on
a worked-over graph, none at all on a fresh one.

**What that projects to.** At the 49% survival rate `BENCHMARKS.md` measured for
real embeddings on similarly-templated text — **superseded 2026-08-20; the
measured rate on real prose is 0.0105% and the note at the top of this entry
carries the corrected table. Kept because the arithmetic is right and only its
input was wrong, which is the failure worth remembering:**

| facts | surviving pairs | pair memory |
|---|---|---|
| 2,000 | ~1.0 M | ~0.6 GB |
| 5,000 | ~6.1 M | ~3.5 GB |
| 10,000 | ~24.5 M | ~14 GB |

So on a corpus of genuinely similar documents, `reflect` can want multiple
gigabytes at ~10,000 facts — **below the ~26,000-node timeout crossing the
benchmarks quote**, which means memory can fail before time does. The response
would be hundreds of megabytes of JSON before that, so in practice the transport
or the timeout goes first; none of the three is a good failure.

**Why the benchmark was blind to it.** Its corpus caveat claimed to
*overstate* anything scaling with surviving pairs; at the vector width the bench
actually runs, 0.05% of pairs clear the threshold rather than the 19% recorded,
so it understates by three orders of magnitude. The measurement by width, and
the corrected caveat, are in `BENCHMARKS.md` — not repeated here.

**Options, in the order they are worth considering:**

- **Measure it honestly first.** Re-run `reflect` scaling with
  `--real-embeddings`, or with a mock whose similarity distribution is not
  degenerate. Everything below is guesswork until the real survival rate on a
  real corpus is known, and this file's own policy is to act on a profile.
- **Cap the nominations, and say so in the response.** A top-k by score with an
  explicit `truncated: true` and the count omitted. Cheap, bounds both memory
  and the response, and fits the house style — report the limit rather than
  silently drop. The judgement call is what a caller does with "there were
  40,000 more": the honest answer is probably that the graph needs a different
  operation, not a longer list.
- **Stream or page the phase**, so the pairs never all exist at once. Correct
  and much larger; it changes the tool's shape from one call returning
  everything to something resumable.
- **Raise the floor adaptively** — pick the threshold from the score
  distribution rather than a constant. Attractive and dangerous: it makes the
  answer depend on the corpus in a way nobody can reproduce, which is the
  "invented threshold" trap the lexical work already refused twice.

**Failing test first**, per the workflow: `tests/pipelines/reflection/` — build a
graph whose facts all clear the threshold, and assert `reflect`'s nomination
lists are bounded rather than quadratic in the fact count. It must fail on
current `main` by returning every pair, and the assertion should be on the count
the response carries, not on wall-clock or bytes.

**Not the node scans.** The 13 `query_nodes` calls per `reflect` are a separate
finding, recorded in `BENCHMARKS.md`; they are linear and cost time rather than
headroom. Fixing them does nothing for this.

---

### Issue 61 — a fact merge does not flag its dependent inferences — ✅ RESOLVED (2026-08-21)

> **Exercised outside the test suite, 2026-08-21 — the same day.** The first
> five `merge_facts` calls on a real corpus (#52) wrote **14 `evidence_merged`
> edges** and put the label on **seven inferences**, each naming the retired
> phrasings rather than the survivor, as designed.
>
> **The invariant that could only be checked here held.** `docs/REFLECTION.md`
> §4 asserts that `evidence_merged` is not a weaker `evidence_stale` — that
> staleness is an archival class and a merged premise is not, because otherwise
> every merge would nominate its own dependents for discard. Re-running
> `reflect` after the merges: all seven appear in `pending_review`, and **none
> of them appears in `archival_candidates`**, whose `evidence_stale` class held
> six unrelated nodes. Before this corpus there were no merged premises
> anywhere, so the claim had nothing to fail against.

Found the day `merge_facts` shipped (#52), while designing inference merge.
Small, real, and a regression in the sense that the path it breaks was
previously exhaustive.

**Every other event that changes a premise flags what rests on it.**
`supersede_node` and `supersede_by_existing` both call
`plan_evidence_stale_edges`, writing `evidence_superseded` edges to dependent
inferences; `review_labels_for` additionally derives `evidence_stale` from any
`derived_from` edge into a retired fact; `evidence_gone_for` covers the case
where the whole evidence set is archived. A **merge does none of them**, because
`merge_nodes` never calls the planner and the merged sources leave the active
set as `MERGED`, which is not in `SUPERSEDED_STATUSES`.

**What that leaves.** The `derived_from` edge migrates onto the survivor, the
survivor is `ACTIVE`, and nothing fires — so a dependent inference now rests on
**agent-written text it was never drawn from**. Verified by construction:
merging *"the deploy failed"* and *"the deployment did not succeed"* into
*"deployments have been failing"* leaves the dependent inference's review labels
`{}`.

The justification for flagging is the one correction already uses: the wording
under the inference changed. A merge asserts the claim is the same, but that is
the *agent's* judgment about two source phrasings, made without reference to
what was derived from either.

**Smallest correct fix**: `merge_nodes` plans `plan_evidence_stale_edges` for
each source and carries the edges into `merge_nodes_tx`, exactly as the two
supersession paths do.

**The decision it needed, made 2026-08-21: a sibling edge type.**
`EdgeType.EVIDENCE_MERGED` (absorbed fact → dependent inference), in
`REVIEW_EDGE_TYPES` so it is neither migrated nor traversed, deriving its own
review label `evidence_merged`. The alternative — `evidence_superseded` with a
reason in metadata — was rejected on where the distinction would live: all three
consumers (labels, archival, migration) route on edge *type*, so a reader that
had not been taught to check the metadata would keep reporting an overturning
that did not happen. **The argument that settled it was a consequence rather
than a principle**: `nominate_archival_candidates` nominates on `evidence_stale`,
so sharing the label would propose discarding an inference because its premise
gained provenance — on every merge, for every dependent.

**Built the same day.** `plan_evidence_merged_edges` beside
`plan_evidence_stale_edges` (both now over a shared `dependent_inference_ids`,
so *what depends on this fact* is answered once); `merge_nodes` plans one flag
per source, so an inference resting on two of them is told about both, and each
flag names the wording that went away rather than the survivor; `merge_nodes_tx`
grows `evidence_edges` across the protocol, both backends and the instrumented
wrapper, written after migration so a flag is never re-pointed onto the
survivor. The wrapper publishes them and counts them, as both supersession paths
already did — otherwise the dashboard's live graph is missing an edge the store
has.

**What this cost that a live check would not have.** `evidence_stale` has two
halves — an explicit flag *and* a scan for `derived_from` into a retired fact —
and the second is what makes it self-healing. A merge gets no such half: the
`derived_from` edge is migrated onto the survivor by the same transaction, so
after it commits nothing distinguishes a dependent of an absorbed fact from an
inference drawn on the survivor directly. **The flag is the only record the
event will ever leave**, which is why it is planned at the merge rather than
derived at read time, and why a merge that fails to write it cannot be repaired
afterwards. Recorded here because it is the general shape: *a derived label can
only be derived while its evidence is still in the graph.*

Guarding tests: `TestADependentInferenceIsToldItsPremiseChanged` (four, both
backends) in `tests/pipelines/test_fact_dedup.py`;
`test_review_labels_evidence_merged_is_not_evidence_stale` and
`test_a_merged_premise_does_not_nominate_its_inference_for_archival` in
`tests/pipelines/test_reflection.py`;
`test_merge_publishes_the_flags_it_wrote_on_dependents` in
`tests/visualization/test_instrumented_storage.py`.

Designed alongside the inference-merge work in
`dev-docs/WARNINGS_AND_SETTINGS.md` §1 and §7.

### Issue 62 — corroboration counts a claim's own successor as independent support — ✅ RESOLVED (2026-08-21)

**The defect.** `corroboration_for` counts distinct publishers over
``{node} ∪ {SIMILARITY neighbours}``, excluding only `contradiction` /
`variant_of` partners and `CORRECTED` nodes. Nothing asks whether two
neighbours were ever asserted to hold *at the same time*. So "the city is
called Leningrad" and "the city is called Saint Petersburg" corroborate each
other, and the count rises exactly where the graph already knows better —
those two are what `temporally_followed_by` links, and a claim and its own
successor are near-maximally similar.

**Stated in three places already, filed in none.** The module docstring
(`epimemer/pipelines/query/corroboration.py`, "#53 was expected to remove that
and did not"), `docs/RETRIEVAL.md` §8's Saint Petersburg caveat, and #52's
inherited blockquote below. This entry exists because those record the
*inaccuracy* while the remedy the docstring names has never been tracked as
work — the filing precedent #59 set: file it before the detail is lost.

**Not the same fix as #52's inherited corroboration migration**, and closing
one must not be read as closing the other. That migration moves the count from
a *neighbourhood* reading to an *identity* reading once facts deduplicate, and
waits on a corpus with merges in it. This is a filter inside the neighbourhood
walk and works today, on a corpus with no merges at all.

**Files.** `epimemer/pipelines/query/corroboration.py` — `_documents_by_node`
(currently discards everything but `dst_id`) and `corroboration_for`'s
neighbourhood step. The rule is `assertions_are_disjoint` in
`epimemer/core/temporal.py`, which the temporal soundness check
(`pipelines/reflection/soundness.py`) already applies to premise pairs; reuse
it rather than restating the comparison.

**It costs no round trips.** The walk already reads the `sourced_from` edges of
the subject *and* every neighbour, and those edges carry the per-source
`validity` intervals (#53 T1). The change is to stop throwing them away.

**It is conservative by construction.** `assertions_are_disjoint` answers False
for an undated side, for intervals on different timelines, and for any cross
pair that compares `unknown` (`temporal.py` §6's open-world rule) — so it can
only ever drop a neighbour whose periods *provably* fall clear. On today's
corpora, where most nodes carry no intervals, it will fire rarely; that is the
correct amount, not a reason to widen it.

**One decision it needs first, and only one: disqualify or annotate?**
Dropping the neighbour changes a number callers read — the same objection
`WARNINGS_AND_SETTINGS.md` §7 raises against inference-to-inference similarity
edges. The alternative is to keep counting it and mark the source, letting the
reader discount it, which is what `unattributed_documents` already does for a
different weakness. Decide before writing code; the entry does not prejudge it.

**Guarding tests** (write first, must fail on `main` for the stated reason):
`tests/pipelines/test_corroboration.py` — a `temporally_followed_by` pair
with disjoint stated periods must not corroborate; an undated pair must still
corroborate (the open-world rule); a pair whose periods overlap must still
corroborate. Both backends via the `storage` fixture.

**Verify.** `uv run python -m pytest tests/ -q`, and re-read
`docs/RETRIEVAL.md` §8 — the Saint Petersburg caveat is part of this fix, not a
separate chore.

> **✅ Resolved 2026-08-21.** Guarded by
> `tests/pipelines/test_corroboration.py::TestAClaimAboutAnotherPeriodIsNotASecondWitness`
> — thirteen tests on both backends. Three fail on `main` for the stated reason
> (the Leningrad pair scores 2), the rest pin the boundary: undated,
> half-dated, overlapping, touching and differently-clocked pairs all go on
> corroborating, and a source asserting one overlapping period among several
> still witnesses the claim.
>
> **The decision was not disqualify *or* annotate — it was both, and the
> question was mis-framed.** Framing it as a choice assumed the fix removes
> something, and it removes nothing: both claims stay in the graph, true of
> their own periods, with the succession between them recorded three times over
> (the dates on each provenance edge, `temporally_followed_by`, the
> predecessor's status). The only thing that narrows is an integer computed on
> the way out and never stored. Once that is clear the two halves stop
> competing — the count becomes honest *and* the look-alike comes back named,
> in `adjacent_periods`, with its publisher, documents and periods.
>
> **Reporting is not a consolation prize for the exclusion; it is the half that
> carries new information.** Where a search returns the subject but not the
> neighbour, this block is the only place the adjacent claim appears at all — so
> a silent filter would cost the caller a fact the graph holds, on top of
> leaving a shrunken number with its working hidden. That is the reverse of the
> usual argument for silence, and it is why the module's three existing
> exclusions stay silent while this one does not: a contradicted or corrected
> neighbour is *not knowledge the caller wants*, and an adjacent period is.
>
> **One thing the entry did not anticipate: where in the walk it lands.** It has
> to run *before* the supporter hop. A look-alike left in until the neighbourhood
> is assembled walks its own supporters in behind it, and their documents with
> them — so a comparison made even one stage later lets the same publisher back
> through the side door by a different path. `adjacent` is therefore subtracted
> in both places `excluded` already was, since a supporter can reach it by a
> second route.
>
> **And one thing the entry got wrong: "it costs no round trips."** Nearly. The
> periods do ride on edges the walk already read — but it read them at stage 4,
> and they are needed at stage 1.5, so the provenance read is now split in two:
> subjects and look-alikes first, then a top-up for whatever the supporter hop
> added, skipped entirely when it added nothing. Thirteen calls, or twelve; still
> constant in the size of the result set. `_documents_by_node` is gone,
> replaced by `_source_edges_by_node` plus two pure readers, because taking
> `dst_id` and discarding the rest of the edge on one line *was* the whole
> defect.
>
> **Carry-forward — an invisible exclusion is indistinguishable from a graph
> that never held the claim.** Whenever a filter's output is a number someone
> reads, the thing filtered out has to be reachable from the same response, or
> the reader cannot tell a corrected count from a smaller world. `#51` already
> knew this about the count (`sources` exists so an inflated figure stays
> checkable) and the same reasoning simply had not been applied to what the walk
> leaves behind.

---

### Issue 63 — the nomination bar was two numbers, and the lower one nominated what the higher one refused — ✅ RESOLVED (2026-08-21)

Found by review the same day #61 shipped. Not a crash and not a data defect: a
false statement made to an agent, produced by a constant that existed in two
places.

**What it was.** `SIMILARITY_NOMINATION_THRESHOLD` was 0.83 and read by
`check_conflicts` and `merge_facts`; reflect's contradiction and recurrence
sweeps passed a literal `0.80` (`tools.py`, `contradiction_detection.py`), and
`server.py` re-declared `0.83` as its own literal at the MCP boundary. A pair
scoring 0.81 was therefore nominated by reflect, judged `redundant` by the
agent, and refused by `merge_facts` — with a message saying these were "not
facts the graph would have offered each other as candidates", which the graph
had just done.

**Fixed by unifying downwards, to 0.80**, with every path — both
`check_conflicts` declarations, `merge_facts`, `detect_contradictions` — reading
the one constant. Raising the sweeps to 0.83 instead would have bought the
invariant by narrowing contradiction *and* recurrence nomination, which is a
worse trade: the merge floor is explicitly not a second opinion on the agent's
judgment (`fact_dedup.merge_refusal`), so 0.83-vs-0.80 was never what separated
a duplicate from a pairing named by mistake.

**The invariant, now pinned rather than implied: merge floor ≤ every nomination
bar.** `test_every_nomination_path_is_gated_at_the_merge_floor`
(`tests/pipelines/test_fact_dedup.py`) checks it by *signature*, across the MCP
boundary too, because a drifted default is invisible to any call that passes the
argument explicitly — and the MCP copy was the one nothing would have caught.
`test_a_pair_reflect_nominates_can_be_merged` checks the band from outside, at
a measured 0.8099.

**Two carry-forwards, both instances of a class this file keeps meeting:**

- **A constant with a stated invariant needs a test that reads every
  declaration of it**, not one. #52's decision 2 said "both readers take it from
  there" and was true of the two readers it named; the sweep and the MCP
  boundary were a third and fourth nobody counted.
- **A refusal message must not assert what the system would have done.** The
  threshold is an argument, so the string could be false for a caller passing
  its own — and it was. It now names the bar the call applied and leaves the
  claim about the graph to the constant.


---

### Issue 64 — `EdgeType.SIMILARITY` has three readers and no writer — ✅ BUILT (2026-08-22), the rest of the design continues in `REVIEW_MODE.md`

Found while taking #52's outstanding corroboration migration, which is what
sent anyone looking at the neighbourhood in the first place. The migration is
declined on the strength of this (see #52); the defect it uncovered is real and
separate.

**Nothing in the codebase ever writes a `similarity` edge.** `grep` for
`EdgeType.SIMILARITY` returns three sites and all three read:

| site | reads it for | what an empty set does |
|---|---|---|
| `pipelines/query/corroboration.py` | forms the neighbourhood the count is taken over | every count becomes the identity reading |
| `pipelines/reflection/contradiction_detection.py` | `already_linked`, to skip pairs already assessed | nothing is ever skipped |
| — | — | there is no third; the rest of the grep is `types.py` |

`record_contradiction` and `record_variant` each write their edge type from a
tool of their own. `similarity` has no such tool. The action the design names
over and over — *"record `SIMILARITY` and keep both"*, in `fact_dedup`'s refusal
prose, in `REVIEW_EPISTEMIC.md` §3, in the `redundant` row of
`docs/REFLECTION.md` §2 — is reachable only through the generic
`link(edge_type="similarity")`, and so is never taken.

**Measured on every real graph, 2026-08-21.** Across 5,414 edges on the two
graphs that hold real ingested text:

| graph | edges | `similarity` | `contradiction` | `variant_of` |
|---|---|---|---|---|
| `memory` | 4,386 | **0** | 0 | 0 |
| `petritype-server` | 1,028 | **0** | 0 | 0 |

The zeroes in the last two columns mean only that nobody has called those tools.
The `similarity` zero is different in kind: **no call exists that would make it
non-zero**, short of hand-writing edges through `link`.

**Two live consequences, both verified rather than inferred.**

1. **Corroboration's documented behaviour does not happen.** `docs/RETRIEVAL.md`
   §8 and this repo's summary both say the count is *"computed over a similarity
   neighbourhood"*. On every real graph the neighbourhood is `{node}` and the
   count is already the identity reading — which is why merging five pairs on
   2026-08-21 moved no count. That was read at the time as the publisher rule
   working correctly, and it was, but it was **also** this: there was no
   neighbourhood on either side of the merge to move away from.
2. **Declined pairs are re-nominated for ever.** `already_linked` is built from
   `SIMILARITY ∪ CONTRADICTION` and is therefore always empty, so a pair the
   agent looked at and deliberately kept apart comes back on the next sweep with
   nothing recording that it was judged. Of the 18 pairs `reflect` nominated on
   2026-08-21, five merged and **thirteen were declined and left no trace**;
   they are still nominated today. The `redundant` verdict's fallback is not a
   no-op by design, it is a no-op by omission.

**What this is not.** It is not an argument for writing `similarity` edges
automatically at nomination time. The edge is supposed to record a *judgment* —
these two are one claim's neighbourhood and were kept apart on purpose — and a
sweep that wrote them for every pair over the bar would fill the graph with
assertions nobody made, and would suppress its own future nominations while
doing it. The nomination bar defers to judgment (#63); so must this.

**Wants a decision before it wants code**, and the shapes differ in what they
record:

- **`apply_reflection(similarities=[…])`** — a tenth kind of decision beside the
  nine, so *declining* a nomination is an outcome the agent can apply in the
  same call as everything else it decided. Matches the existing surface, and the
  edge then carries the same provenance as every other applied verdict.
- **`merge_facts` writes the edge when it refuses** — appealing, because the
  refusal already holds the pair and has just argued for this exact fallback.
  Wrong for at least the cross-frame refusal, where `record_variant` is the
  right action and a `similarity` edge would assert the wrong relation; and it
  turns a call the agent expects to change nothing on refusal into one that
  writes.
- **A `record_similarity` tool**, symmetric with `record_contradiction` and
  `record_variant`. Smallest and most obvious, and the least connected to the
  loop that produces the judgments.

**Decided 2026-08-22: the first**, and the decision grew in the taking. The
missing writer turned out to be one part of a larger gap — *no decision in this
system records who made it* — so `similarities=[…]` is now step 1 of a design
that also adds an agent registry, attribution on every write path, and a review
loop for a second agent to audit a first. Written up in
**`dev-docs/REVIEW_MODE.md`** (designed 2026-08-22, not built); this entry stays
open until that document's step 1 lands.

> **Built 2026-08-22.** `EdgeType.ASSESSED`,
> `pipelines/reflection/similarity_decisions.py`,
> `apply_reflection(similarities=[…])`, and `ALREADY_JUDGED_EDGE_TYPES` in
> `contradiction_detection` — which is now
> `SIMILARITY ∪ CONTRADICTION ∪ VARIANT_OF ∪ ASSESSED`, so `variant_of` starts
> suppressing too, having always been a judgment about a pair and simply never
> read as one. 57 tests over both backends. **This entry closes; the design it
> grew into does not** — steps 2–7 of `REVIEW_MODE.md` (registry, attribution,
> journal, `review()`) are still ahead, and that document is where they live.
>
> **The measurement that motivated it is now the thing to re-take.** Every
> corroboration cost figure in `BENCHMARKS.md` was taken against synthetic edges
> at a fixed degree, because real ones did not exist. They exist now, one
> `one_claim` verdict at a time, so the fan-out is finally an observation rather
> than an assumption — and worth re-measuring once a graph has accumulated a
> few dozen.
>
> **What building it changed in the design** is written up in `REVIEW_MODE.md`
> §10.2's amendment; the load-bearing one is that the *retired id is skipped*
> rule was too broad by exactly half the problem. The recurrence sweep nominates
> active/historical pairs, so refusing a historical side would have left the
> treadmill running on the population where the graph is offering a claim beside
> its own predecessor. The rule is `NOMINATED_STATUSES` on both sides — not a
> widening, but the same rule stated properly: an `assessed` edge earns its
> place by suppressing a nomination, so it belongs exactly where a nomination
> could have happened.
>
> **And it left one thing it could not do** — nothing retracts a `one_claim`
> verdict once the `similarity` edge is written. Filed as **#68** rather than
> worked around.

**Whichever is taken, #52's migration stays declined** — collapsing the walk
would remove the reading that this issue exists to make reachable.

**Fixing this changes what corroboration costs**, which is the third thing to
carry: every measurement of that cost was taken against synthetic edges at a
fixed degree (`BENCHMARKS.md`), because real ones did not exist to measure. A
graph that starts recording judgments is the first one where the fan-out is an
observation.

---

### Issue 65 — a correction re-points judgment edges onto wording nobody judged — ✅ BUILT (2026-08-22, found the same day)

Found by review of `dev-docs/REVIEW_MODE.md`, which proposed to start writing
`similarity` edges (#64) and so would have made this reachable. **Latent today
and only today**: both real graphs carry zero `similarity`, `contradiction` and
`variant_of` edges, so nothing has ever migrated.

**What it is.** `migration_disposition(edge_type, status)`
(`core/types.py:351`) returns `"move"` for every knowledge edge on a `CORRECTED`
retirement, and `SIMILARITY`, `CONTRADICTION` and `VARIANT_OF` are deliberately
**not** in `NON_KNOWLEDGE_EDGE_TYPES` — the set's comment says so in as many
words, because they are real edges to follow. So a correction drags them onto
the replacement:

> `A` carries a `similarity` edge to `B`, meaning *one claim, restated*. `A` is
> corrected to `A′`. The edge re-points, asserting **`A′` and `B` are one
> claim** — and `corroboration.py` walks `SIMILARITY` to build its
> neighbourhood, so `B`'s publisher is counted as backing a wording nobody
> compared it against.

That is **manufactured corroboration**, which `fact_dedup.py`'s header calls the
worst failure available: a false unification does not lose information, it
inverts the quantity corroboration measures. And it arrives by a route neither
the merge gate nor #64's two-population split can see, because no merge happened.

`CONTRADICTION` has the same fault with an extra sting: **a correction may be
exactly what resolved the contradiction**, so re-pointing it asserts a conflict
that the correction settled.

**The principle is already written down, one status too narrowly.**
`migration_disposition`'s own docstring, on the world-change case: *"a
contradiction or a variant is a judgment made **about the old claim**, and
re-pointing one asserts it of a claim nobody assessed."* Correct — and applied
only to `HISTORICAL`. A correction is not different in this respect: the claim
is the same, the **wording** changed, and the judgment was about the wording.

**Fix, and why not the obvious one.** Adding the three types to
`NON_KNOWLEDGE_EDGE_TYPES` would also drop them from default graph traversal —
a second behaviour change nobody asked for, against a set whose comment exists
to say they *are* traversable. Instead a set of its own, consulted first, so
that a judgment is anchored on **every** retirement:

```python
JUDGMENT_EDGE_TYPES: frozenset[EdgeType] = frozenset(
    {EdgeType.SIMILARITY, EdgeType.CONTRADICTION,
     EdgeType.VARIANT_OF, EdgeType.ASSESSED}   # ASSESSED arrives with #64
)
```

`A′` then starts with no judgments and is re-nominated, which is right: `A′`
against `B` is a pair nobody has judged.

**Blocks #64's step 1.** That step starts writing `similarity` edges, which is
what ends the latency. Fixing after would mean shipping the defect knowingly.

**Carry-forward.** *A rule stated for one branch of a conditional is not a rule
the code applies.* The world-change branch carried the argument; the correction
branch carried the same risk and no argument, and the two sat four lines apart.

> **Built 2026-08-22, before step 1 as required.** `JUDGMENT_EDGE_TYPES` sits in
> `core/types.py` and `migration_disposition` consults it before the status
> branch, so it answers `keep` for `similarity`, `contradiction` and
> `variant_of` on **every** retirement. Both backends inherit it without
> changing: `memory.py` calls `migration_disposition` per edge and
> `surrealdb_adapter.py` derives its type filter from `moved_edge_types`, so
> neither holds a policy of its own — which is why this was a four-line change
> to one function. Seventeen tests, run against both backends: the policy
> directly, a correction and a merge leaving each of the three types behind,
> provenance still moving on a correction (the half that did **not** change,
> asserted beside the half that did), and traversal unaffected.
>
> **Two corrections came out of building it, neither changing the verdict.**
>
> **1. The set covers a merge too, and `REVIEW_MODE.md` §10.2 said otherwise.**
> That section had `SIMILARITY` migrating onto a merge survivor, arguing `S`
> contains `A`'s claim so a one-claim judgment survives. §10.2.1, four pages
> later, said judgment edges are anchored on any retirement. Two subsections,
> opposite answers — this issue's own carry-forward pattern, in a new place.
> Anchoring wins on asymmetry: it costs one re-nomination of `S` against `B`,
> which is *correct* since nobody has judged that pair, while migrating can
> manufacture corroboration in silence. And the merge case is not really the
> weaker one — `merge_facts` **synthesises** the survivor's content, so `S` is
> nobody's judged wording.
>
> **2. The reasoning above does not carry on its own terms.** This entry argues
> that a correction changes the wording and not the claim. But
> `migration_disposition`'s docstring holds that a correction preserves the
> claim — that is precisely why the sources follow it — so a *one-claim*
> judgment would survive that reading intact. What actually carries it is the
> **substantive** correction: "the population is 500,000" → "5,000,000" is the
> same claim, and its sources rightly follow, but a counterpart judged one claim
> against the old figure was judged against a number that is no longer there,
> and `corroboration.py` would count that counterpart's publisher as backing the
> new figure. Same verdict, different load-bearing reason — and the reason
> matters, because *"the wording is gone"* is what shows a merge belongs in the
> same rule while *"the claim is gone"* would not.
>
> **`ASSESSED` is not in the set**, contrary to the snippet above. When #64 step
> 1 adds it, `REVIEW_EDGE_TYPES` is its home: it needs the same anchoring *and*
> exclusion from traversal, being a suppression index rather than knowledge, and
> `NON_KNOWLEDGE_EDGE_TYPES` is consulted first — so listing it in both would be
> redundant.
>
> **A second carry-forward.** *When a fix is derived from an argument, check the
> argument against the code before trusting the fix.* The verdict here was right
> and the stated reason was not, which is survivable only because someone
> re-derived it. A fix that is right for the wrong reason generalises wrongly —
> in this case it would have left the merge case out.

---

### Issue 67 — SurrealDB's supersede paths trust the caller's lifecycle — ✅ FIXED (2026-08-22, found the same day)

> **✅ Fixed 2026-08-22.** One private helper, `_stored_lifecycles`, and
> **five** call sites rather than the three below: `supersede_node_tx`,
> `supersede_by_existing_tx` and both branches of `set_node_status_tx` are
> the fix, and `merge_nodes_tx` and `reverse_merge_tx` were moved onto the
> same helper so the rule is stated once instead of being re-argued in a
> comment per builder. Four parity tests, each failing on SurrealDB and
> passing in memory before the change, which is the divergence itself.
>
> **The restore branch fails the other way, and the entry did not say so.**
> `with_return` is a no-op on a history with nothing open, so a caller's
> pre-archival copy does not append a wrong episode — it writes an **empty
> list** over the real one, losing the retirement as well as its return.
> Same cause, opposite symptom: the retirement paths write too much
> history and the return path writes none, so a fix that only looked for
> a short list would have missed it.
>
> **Left alone on purpose:** a missing row still fails where each path
> already fails it — `set_node_status_tx`'s THROW, or an UPDATE matching
> nothing — because the helper falls back to the argument. That the two
> backends disagree about a *missing* node in the supersede paths
> (`KeyError` in memory, silent partial application on SurrealDB) is a
> second divergence in the same functions, and it is not this issue.

Found while building merge reversal (#64 step 0c), which made a *second*
retirement of the same node reachable for the first time and so exposed the
class of bug.

**What it is.** `SurrealDBStorage` builds a node's new `lifecycle` from the node
object the caller passed, not from the row in the database:

```python
"lifecycle": _episode_rows(with_retirement(
    old_node.lifecycle, at=superseded_at, because=status, ...
)),
```

A caller holding a node it loaded *before* an earlier retirement passes a stale
list, and `with_retirement` appends to that — so the UPDATE writes a lifecycle
missing every episode since. `InMemoryStorage` reads the stored node instead, so
the two backends give different histories for the same call, which is precisely
what `test_storage_parity.py` exists to prevent.

**The merge instance is fixed** (`merge_nodes_tx`, and `reverse_merge_tx` was
written the same way from the start): both now read the stored lifecycle before
the transaction. It was not optional there — a second merge/reverse cycle came
back looking like the first, which would have made `merge_cycle_limit` blind to
exactly the oscillation it exists to catch.

**Three instances remain**, all in `surrealdb_adapter.py`: `supersede_node_tx`,
`supersede_by_existing_tx`, and the restore path that builds `with_retirement`
from `node.lifecycle`. Left rather than swept in with the merge fix, so that one
commit does not quietly rewrite three transaction builders — and they are not
reachable today, because every production caller (`tools.update`,
`tools.restore`) loads the node immediately before retiring it.

**Why it is 🟠 rather than 🔴.** Latent, with no path to it in shipped code. But
it is latent the way #64 was latent: the day a caller caches a node across two
supersessions, the graph loses history silently and no test outside parity would
notice.

**Fix.** The same three lines each: read the stored node in the same
pre-transaction batch the method already performs, and build the episode list
from that. A parity test per path is what stops it coming back.

**Carry-forward.** *A transaction that takes a domain object as an argument has
to decide whether the argument is a request or a snapshot, and say which.* Here
`source_nodes` is a request (which nodes to retire) whose `lifecycle` field was
silently being read as a snapshot.

---

### Issue 66 — two ingest-time judgments have no way to be revised — ✅ FIXED 2026-08-27 (found 2026-08-22)

> **Built 2026-08-27 as two tools, `reframe` and `correct_interval`.** The entry
> below argued for keeping them out of `rejudge` on the ground that answering
> either inside a tool named for ingest priors "would bury a load-bearing
> decision in a utility". That conclusion was right and **the reason given was
> not the strongest one available**: the split is about **addressing**. `rejudge`
> names a `node_id` and promises no status, edge or lineage moves; a frame
> revision moves an edge and changes what retrieval does, and an interval belongs
> to a **(node, source) pair**, so folding it in would grow a `source_id` that is
> read for one field out of five. That is this file's own tell — *a parameter
> that needs "only applies when" to describe when it is read* — and it decides
> the question where "it would look untidy" decides nothing.
>
> **The last-frame refusal was a trap, caught in review before it was built.**
> The first proposal answered *can you withdraw a node's last frame?* with a flat
> refusal — which would have left the tool unable to fix **the paradigm case it
> exists for**, since the motivating example is a real fact mis-filed under a
> novel's frame whose correct home is base reality. What that question is
> actually probing is that withdrawal-to-untagged is a **promotion**: base-reality
> knowledge is inherited by every frame, so the claim goes from asserted in one
> world to asserted in all of them. So the answer is an acknowledgment, not a
> guard — `to_base_reality=True` is required there, on `expected_graph`'s
> reasoning that the check is worth something only because the intent is stated
> independently of the state. It is refused where it does not apply, because a
> flag that lies about what it authorised is worse than no flag.
>
> **`assign` makes the A→B move atomic**, and that is the second thing review
> added. Withdraw-then-link passes through untagged — asserted in every frame —
> and strands the node there if the second call never lands; link-then-withdraw
> passes through `{A, B}`, no worse than the starting error. Doing it in one call
> avoids both, and it composes with the flag: a move never reaches the last-frame
> question, so `to_base_reality` stays honest about what it gates.
>
> **The withdrawal deletes the edge**, on #68's carry-forward: *check whether the
> read that would honour an undo-without-delete is already there.* It is not —
> frames are derived by scanning `has_metacontext` edges, so a `withdrawn` marker
> would need every site to subtract it and any site missed would fail **open**,
> with the frame still applying. Deleting fails closed. The withdrawn frame
> survives in the node's `reframings` trail and in the journal row, and that
> retention matters more than a rejudgment's: every search and corroboration
> answer given while the frame was wrong was wrong, and the trail plus the row's
> timestamp is the only thing that bounds which answers those were.
>
> **On the interval side**, an empty replacement list is allowed and is the
> correction for a period that was invented outright — refusing it would leave a
> fabricated interval unremovable, which is this entry's own shape a second time.
> `basis` stays the caller's to state rather than being forced to `inferred` as
> `apply_boundary` forces it, because a correction is often restoring what the
> document actually said. `boundary_proposals` is untouched: filling an open
> endpoint and overwriting a present-but-wrong one are different acts on
> different evidence, and only the first can ever be automated.
>
> **The discoverability fix shipped too**, and it was the only real argument for
> a single tool: `rejudge`'s docstring and its "nothing to revise" refusal both
> name all three siblings, so an agent that looks in the obvious place is pointed
> on rather than falling through to `supersede_by` and filing a true claim as an
> error.
>
> **Ordering, as review recommended**: the frame tool is the more urgent of the
> two — it corrupts three read paths at once and its blast radius grows with
> exactly the frame usage `AGENTS.md` encourages, where an interval error
> corrupts corroboration and `graph_as_of` for one (node, source) pair. Both
> shipped together in the end.
>
> One correction to the entry below, from review: `rejudge` covers **five**
> fields, not three — `certainty` and `certainty_basis` arrived with step 7.


> **Still open 2026-08-23, and now precisely bounded.** `rejudge` shipped with
> step 7 and covers `claim_kind`, `confidence` and `confidence_basis` — the
> three the survey assigned it. These two were left out on purpose then and are
> left out now, for the reason below rather than for lack of a writer: both are
> questions about their own subsystem, and answering either inside a tool named
> for ingest priors would bury a load-bearing epistemic move in a metadata
> utility. What has changed is that the shape of the answer is no longer a
> guess — `rejudge` is what a revision-without-supersession looks like, trail
> and all, and either of these can be built to match it.

Surfaced by surveying what `rejudge` should cover (`dev-docs/REVIEW_MODE.md`
§6.5.1). Both are the #64 shape — *a judgment the system lets you make and never
lets you unmake* — and both are left out of that document on purpose, because
each is a question about its own subsystem rather than about ingest priors.

**1. A metacontext assignment cannot be withdrawn.** `link` writes a
`HAS_METACONTEXT` edge; nothing removes one. So a fact wrongly framed as fiction
stays framed, for ever.

This is not cosmetic. Frames are load-bearing in two places that both fail
*silently*: `merge_refusal` refuses a cross-frame pair, so a mis-framed fact
becomes permanently unmergeable with its own twin; and corroboration
disqualifies `variant_of` partners, so the count moves. A frame is also the one
thing the system will not let a merge inherit — *"asserting in one world what
was only ever claimed in another"* is called the single worst outcome available
— which makes the inability to correct one conspicuous.

**2. A per-source validity interval cannot be corrected.** Intervals are
supplied on the `sourced_from` edge at ingest (#53 T1 §2). `boundary_proposals`
fills an **open** endpoint where a succession implies one; nothing revises an
endpoint that is present and wrong. Since #62, corroboration reads those
intervals to decide whether a look-alike is a witness or an adjacent period —
so a wrong interval now silently moves a count as well as a date.

**Neither is a supersession**, which is why neither has quietly been solved by
`update` already: the claim is unchanged and the world has not moved, so
`because` has no honest value. They are the same category as `claim_kind` —
*the judgment about the claim was wrong* — which REVIEW_MODE §6.5 answers with
`rejudge` for the fields it covers.

**Why they are filed separately rather than folded in.** Withdrawing a frame is
an epistemic move with consequences across merge, corroboration and retrieval;
correcting an interval belongs beside the boundary machinery that already
reasons about endpoints. Putting either behind a tool named for tidying ingest
metadata would hide a load-bearing decision in a utility.

**Not blocking anything.** Recorded so the survey that found them is not lost.

---

### Issue 68 — nothing retracts a `one_claim` verdict — ✅ FIXED (2026-08-23)

> **Fixed 2026-08-23.** `distinct` over a pair that already carries a
> `similarity` edge is now a **withdrawal** instead of a refusal: it writes a
> `retracted_similarity` edge, and `DISQUALIFYING_EDGE_TYPES` in
> `corroboration.py` reads it. The count comes back to what it would have been.
>
> **The entry weighed two shapes and the answer was a third, already in the
> codebase.** It proposed a `retracted` marker on the edge (rejected: mutable
> state denormalised, which §3.4 forbids) or a third verdict with a lineage edge
> onto the `similarity` (rejected here: more machinery than the problem earns).
> What it missed is that **the read side already existed**. `contradiction` has
> disqualified a standing `similarity` since before this design, on a comment
> that describes exactly this situation — *"the `similarity` edge written before
> the verdict stays in the graph"*. So the retraction is one more member of a
> list, and the fix is a new edge type with one writer, one reader, and no new
> mechanism. Carry-forward: **before designing a mechanism for "undo without
> delete", check whether the read that would honour it is already there** — this
> system disqualifies in several places and deletes in one.
>
> **The refusal did not disappear; it changed direction.** Nothing re-asserts
> `one_claim` over a withdrawal, and the asymmetry is the design rather than the
> same defect pointed the other way. The two failures are not comparable:
> withdrawing wrongly **withholds** a count, while re-asserting wrongly
> **invents** agreement — and invented agreement does not lose information, it
> inverts the quantity corroboration measures. Under-counting is the direction
> #52 already chose when it left the pre-`claim_kind` corpus unmergeable. Where
> a pair really is one claim, `merge_facts` is the call that says so, and the
> refusal names it.
>
> **Suppression is untouched**, which is what keeps this narrow. The `assessed`
> edge stays and the pair stays out of every nomination: it has now been judged
> twice, and re-offering it would restart the treadmill #64 closed. A retraction
> changes what corroboration counts and nothing else.
>
> The journal gets **`DecisionKind.RETRACTION`**, its own kind rather than
> `REVERSAL`. Both undo an earlier decision, but a merge reversal **deletes** the
> survivor — the system's only hard delete — and a reviewer selecting `REVERSAL`
> to audit that must not get rows where nothing was destroyed. The row `reviews`
> **and** `supersedes` the original `SIMILARITY` row, the shape §4 gives a
> reversal. A repeated `distinct` writes nothing and journals nothing: a retried
> batch must not read as two agents disowning the pair on separate occasions.
>
> **Found while adding the frontend row: `variant_of` has been drawing as
> unknown-kind grey since it was introduced** — #55's failure, live, and caught
> only because #68 put a row beside it. Fixed, and the new guard is scoped to
> the family where it matters rather than to every edge type: grey is the right
> default for `sourced_from` and eleven other structural edges, and wrong for a
> judgment about a pair, since those all draw on top of each other and mean
> different things.


Found on building #64's step 1, and it is #64's own shape arriving one tier
down: *a judgment the system lets you make and never lets you unmake.* #66
records two more of these; this is the third, and the first that touches the
number retrieval reports.

`apply_reflection(similarities=[{pair, verdict: "one_claim", …}])` writes a
`similarity` edge. **Corroboration counts it as a second independent source.**
Nothing removes one — not `apply_reflection`, not `link`, not `update`. So an
agent that judged two facts one claim and later concludes it was wrong has no
call to make.

**What the code does about it today.** A later `distinct` on that pair is
**refused**, with the standing edge's id in the reason. The alternative was
worse: writing `assessed` beside a `similarity` that goes on corroborating a
pair the agent has just disowned, and reporting success while doing it. The
refusal costs no suppression — the standing edge already suppresses the pair —
so what it buys is that the gap is visible at the moment somebody hits it,
instead of being a count nobody can explain later.

**Why it is not simply a delete.** The system's rule is that nothing is
destroyed, and step 0c spent a whole design (`REVIEW_MODE.md` §7) arriving at a
*reversal* for merges rather than a delete — capture the prior state, restore
it, keep the history of both events. The same answer probably fits here and is
much cheaper, because a similarity edge destroys nothing on the way in: the
retraction wants a **writer**, not a delete. Two shapes worth weighing when it
is taken:

- **A `retracted` marker on the edge**, with corroboration skipping marked
  edges. Append-only, keeps both judgments, and needs no new edge type — but it
  makes an edge mutable, which §3.4 permits only for immutable denormalisations
  and this would not be one.
- **A third verdict**, `retracted`, writing a second `assessed` edge that
  supersedes the first and a lineage edge onto the `similarity`. More faithful
  to how the rest of the system records reversals, and more machinery than the
  problem has yet earned.

**Not blocking, and bounded by construction**: every `similarity` edge in the
graph was written by an agent that had the pair in front of it and said the two
were one claim, so the population that could ever need retracting is small and
deliberate. Filed because the honest refusal is not a fix, and because a count
that cannot be walked back is exactly the failure `fact_dedup`'s header calls
the worst this system can produce — reached this time through a door that is
supposed to be the *safe* alternative to merging.

---

### Issue 69 — merging relation labels leaves no record of who did it — 🟡 OPEN (found 2026-08-23), superseded by #74

> **Superseded 2026-08-24 by #74, which dissolves the question rather than
> answering it.** All four shapes below assume the merge happens and argue about
> where its record goes. Three things measured on 2026-08-24 undercut the
> premise: relation merges fire approximately never (one label in the largest
> real graph), labels do not affect retrieval at all (`traversal_excluded` reads
> `type` and `kind`, never `label`), and the feature is a port of tag
> consolidation whose premise did not survive — a tag *was* a retrieval handle
> and a label is not. Give the label a record and deprecation replaces merging:
> nothing is rewritten, and the subject finally has an id. Do not build any of
> the four options below without settling #74 first.


The last decision in `apply_reflection` with neither an inline judge nor a
journal row. Its twin, accepting a boundary proposal, was closed by step 5;
this one was not, and the obstacle is the **field** rather than the work.

`relation_merges=[{labels: [...], into: ...}]` relabels every user-tier edge
carrying a synonym, in place — edges are not versioned, so there is nothing to
stamp, which is why step 3 left it alone. The journal is the right home for a
judgment about somebody else's row. But `DecisionRecord.subject_ids` holds
**node ids**, and this judgment's subjects are **labels**: putting them there
gives one field two namespaces, which is the tell `REVIEW_MODE.md` §11 records
twice — *a field that needs the word "or" to describe what it holds*.

Three shapes, none free:

- **`relabel_edges` returns the ids it changed**, and they become the subjects.
  Faithful — §3.5 already expects edge ids in this field for decisions *about*
  edges — but it changes a protocol method on both backends and writes subject
  lists in the hundreds for a broad label.
- **A `labels` field on the record.** One more column for one writer, and the
  first thing on this row that is not an id.
- **The node ids at the endpoints of the relabelled edges.** Raised in review,
  and it does satisfy *ids only* — but it buys that by making the row surface
  under nodes the decision was not about: §9's derived `node.notes` view would
  show *"somebody merged two relation labels"* against a topic nobody judged.
  Cheaper than the first option and less faithful than the second.
- **Leave it, and let the reviewer read `edges_relabeled`.** What ships today:
  the count is in the response and nowhere else, so nobody can ask *who merged
  these labels* a month later.

**There is no `relation_merge` kind in `DecisionKind` meanwhile.** It shipped
with the journal and was removed the same day on review: a kind nothing writes
is worse here than a dead enum member usually is, because review *selects* on
it — an unwritten kind is a filter that returns nothing and looks like a clean
graph. That is `WARNINGS_AND_SETTINGS.md` §8.1's rule for `AdvisoryAction`
(*"a value nothing can produce is worse than no value at all"*) applying
unchanged. The absence is recorded in `DecisionKind`'s docstring, which is where
a not-yet belongs; the enum is where a selectable vocabulary belongs.

**Small, and worth taking with step 7**, when `apply_review` gives the journal a
second writer and the shape of a record is open anyway.

---

### Issue 70 — SurrealDB compares timestamps as strings, and Pydantic does not always write the same string — ✅ FIXED 2026-08-23 (found the same day)

A backend divergence, found while building the journal and reproduced:

```python
node = Fact(content="x", source_id="s",
            created_at=datetime(2026, 8, 23, 12, 0, 41, tzinfo=timezone.utc))
await store.store_node(node)
await store.query_nodes(at_time=datetime(2026, 8, 23, 12, 0, 41, 500000, ...))
# memory:   1 node     ← correct, the node existed by then
# surrealdb: 0 nodes
```

Timestamps are stored as ISO strings and compared as strings, which is
chronologically correct **only while every rendering has the same shape**.
Pydantic omits the fractional part when it is exactly zero, so the row holds
`…:41Z` while the bound is `…:41.500000+00:00` — and `"Z" > "."`, so the earlier
row sorts past the later bound and drops out. It reaches `graph_as_of`'s
`created_at <= $at_time` and `superseded_at > $at_time`, and the lifecycle
window `query_changes` reads.

**Rare, and not benign.** One timestamp in a million lands on a whole second,
and `graph_as_of` is the tool whose whole promise is *the graph as it stood* —
a node silently missing from a point-in-time answer is the kind of wrong nobody
notices. The `+00:00` versus `Z` suffix mismatch on its own is harmless, which
is why this survived: at equal instants it errs in the direction each comparison
already wants.

**Fixed by comparing instants rather than spellings.** `instant()` in
`surrealdb_adapter.py` wraps both sides in `type::datetime`, at the three sites
that compare a timestamp: `query_nodes(at_time=…)`, `query_changes`' window, and
`_EPISODE_IN_WINDOW`. No migration — it is correct for rows already written,
whatever shape they are in, which is what makes it the cheap answer.
`TestTimestampsAtAWholeSecond` covers both directions and the lifecycle window,
on both backends.

**The measurement said take it, and corrected this entry's own reasoning while
doing so.** The first draft said the fix cost "the index", and there is no
index: `created_at`, `superseded_at` and the lifecycle timestamps are unindexed,
so both forms already plan as `Iterate Table`. *Check the plan before believing
a cost argument.* What the conversion actually costs is ~2.3 µs per row
scanned — 1.19× on a realistic `query_nodes` window over 10,000 rows, under 2 ms
on the real graphs.

**Where a timestamp is indexed the picture inverts, which is why the rule has
two halves.** A range over the journal's `decided_at` went **6.2 ms → 281 ms at
50,000 rows, 45×**, because wrapping the field turns `Iterate Index` into
`Iterate Table`. So the journal keeps a plain comparison and pays on the write
side instead: `_decision_row` renders microseconds unconditionally. Reader
converts where there is no index; writer pads where there is. Both halves are in
`DEVELOPER_GUIDE.md` under *Comparing timestamps*, and `AGENTS.md` carries the
one-line rule.

**The first draft also gated a correctness bug on a performance trigger** —
"wants whoever next touches `graph_as_of`'s performance" — which inverts this
repo's own language: #53 called silent point-in-time wrongness "the kind of
wrong nobody notices". Caught in review. The carry-forward is the pair:
*a correctness defect does not wait for a performance visit*, and
*a cost you have not measured is a guess, including when it sounds structural*.

**Why it survived**: `datetime.now()` essentially never lands on a whole second,
so every timestamp in the parity suite was constructed safely by accident. The
fixture guarantees parity over the values tests happen to build, which is not
the same as parity.

---

### Issue 71 — every call names the graph it means — ✅ FIXED (2026-08-23)

> **Decided 2026-08-23 by the user, and it is stricter and simpler than this
> entry proposed.** Naming the graph is **mandatory, unconditional, and there is
> no setting**. Two proposals below were rejected on the way, both by the user:
>
> **The count-based gate is out.** This entry recommended requiring it only
> where the server can see more than one graph. That gate reads
> `list_databases()`, which is *live* — so creating a second graph switches the
> requirement on and deleting it switches it back off. Writes that worked
> yesterday start refusing because of state the agent never touched. **A
> requirement that oscillates with unrelated state is not a policy.**
>
> **A per-graph setting is out, and the reason generalises.** `require_judge` is
> stored inside each graph, which is right for it — rigour legitimately varies
> by use case. It is *self-defeating* here: the server would look the
> requirement up in whichever graph is **active**, and the whole premise is that
> the active graph may not be the one the agent thinks. Land in a graph with the
> flag off and the gate waves the call through — it turns itself off in exactly
> the case it exists for. **A guard must not be configured by the state it is
> guarding against.**
>
> **So there is no setting at all**, per-graph or per-server, which deletes the
> question this entry was filed to answer. The only argument for one was
> backwards compatibility, and the failure is loud and self-explaining — a
> refusal naming the parameter and the graph to switch to.
>
> **And it covers reads, which this entry never considered.** The scope was
> three write tools, on the argument that everything else dereferences a node id
> and so already fails. Wrong twice. A wrong-graph **read** returns a plausible
> answer the agent reasons from and reports, and leaves **no artifact anywhere**
> — where a misfiled write at least leaves the material and its journal row
> together in the graph that received them. And a failing id is a *worse*
> failure than a refusal, not a substitute: `merge_facts` raises *node not
> found*, which does not say *wrong graph*, so the next move is a workaround;
> `apply_reflection` skips silently; and where two graphs share ids — a restored
> archive, a copied database, which is #54/#55/#56 again — the ids resolve and
> the call lands.
>
> **Built 2026-08-23 (change one of two).** `expected_graph` on all 37 content
> tools; four exempt because each is *about* graphs (`list_graphs`, `use_graph`,
> `delete_graph`, `viz_status`). One gate, at `_run_with_timeout`, **inside** the
> turn that holds the graph still — outside it, a `use_graph` landing between the
> check and the call would leave a call that passed the gate running elsewhere.
> Absence still proceeds; making it a refusal is change two, and the tests that
> prove the gate works run against it first.
>
> **Building it found a defect that predates the change and had never fired in a
> test.** The gate returns a refusal dict, and `_log` then called the *tool's
> own success summariser* on it — which reads keys a refusal does not carry,
> raised `KeyError`, and turned the response into `{"error": "'segments'"}`.
> The sentence telling the agent to call `use_graph` was being swallowed on the
> way out, and had been since `expected_graph` shipped. It passed review because
> every test called `tools.segment_text` **directly**, one layer below the
> boundary, where no summariser runs. Carry-forward: **a test at the layer below
> the boundary cannot see what the boundary does to the answer.** The gate's
> refusal now brings its own summariser.
>
> **The ordering gap was closed too, on the user's call, and it was worse than
> first described.** `_judge_for_write` runs in the tool body and so finished
> before the boundary gate ever ran — and *everything it reads is graph state*:
> the approved-agent list a bound judge is checked against, and the
> `require_judge` setting. On a wrong-graph call that is twice misleading. The
> agent is refused with *claim an agent* rather than *wrong graph*, which sends
> it to `claim_agent` — itself gated; and the **operator** gets a warning that a
> judge *"is not approved in graph Y"*, a revocation that never happened, about
> a graph nobody meant to be in. Never a wrong write either way, since the
> boundary gate refuses afterwards.
>
> The cost was also misjudged: not 37 call sites but **14**, every one already
> holding `expected_graph`. `tools.wrong_graph` stays the single declaration —
> a second call site of one function, not a second policy — and the boundary
> keeps the backstop for the tools that never come through here. The principle
> worth banking: **anything that reads graph state before establishing which
> graph it is in is reading state it has not earned the right to read**, and
> *"the consequence is only a bad message"* is the same reasoning that left this
> guard covering three tools in the first place.
>
> **Change two, same day: absence is a refusal.** `NAMES_ITS_OWN_GRAPH` in
> `server.py` is the only exemption and the test suite reads it rather than
> restating it — a second copy would be free to disagree with the one the gate
> consults, and the disagreement would look like a passing test.
>
> **The blast radius was the evidence.** Turning it on failed **82 tests across
> nine files** — every one a call that went through the MCP boundary without
> saying which graph it meant, which is exactly the population the gate exists
> for. Most took a graph name; four did not, and those four were the
> interesting ones: each *switches graphs first* and then ingests or claims, so
> a blanket `expected_graph="default"` made them fail. Two test helpers now
> thread the graph as a parameter rather than defaulting it once at the top,
> which is the same discipline the tool now asks of an agent.
>
> **The refusal for a missing parameter says more than the one for a mismatch.**
> It names the active graph, because the agent cannot see the reconnect that put
> it there and has to be able to recover — and it says in as many words not to
> paste that name back, because the check is worth something only while the two
> sides are worked out independently. That cannot be enforced, so it is
> written down.

### Issue 71 — should a server be able to *require* that a write names its graph? — ✅ DECIDED AND BUILT 2026-08-23 (raised the same day)

> **Decided by the user 2026-08-23 and built the same day; heading corrected
> 2026-08-27, which is the only thing that was wrong here.** The entry below
> still reads as an open question and its "recommended variant" was **rejected**
> — kept as written, because the rejected option is the argument.
>
> **The answer went stricter than this entry proposed: mandatory, unconditional,
> no setting, and covering reads as well as writes.** Both settable shapes were
> refused for one reason — *a guard must not be configured by the state it is
> guarding against.* The count-based gate below reads a live `list_databases()`,
> so creating a second graph would start refusing calls that worked yesterday
> and deleting it would stop; a per-graph flag would be read from whichever
> graph the call is wrongly in, disabling itself in exactly the case it exists
> for. Unlike `require_judge`, which is about rigour and legitimately varies by
> use case, this is a correctness check — and there is no use case for not
> minding which graph a call lands in.
>
> **Re-decided identically 2026-08-27**, when the question was put again from a
> position of not knowing it had been settled: the user chose mandatory over the
> count-based variant, on the ground that a rule keyed to how many graphs exist
> is one an agent gets thwarted by without ever having been told it applies —
> which is the same objection in different words. Two independent arrivals at
> the same answer, which is worth more than the entry that recorded it once.
>
> Live check, 2026-08-27: a `graph_stats` call with no `expected_graph` refuses
> and names the active graph, warning against copying it back.


`expected_graph` is optional, and an agent that never passes it gets none of its
protection. That is the residue of the wrong-graph incident: the parameter
closes the hole for a caller who opts in, and the caller who caused the incident
would not have.

The shape that would close it properly is `require_judge`'s, one field over —
an opt-in setting, per graph and per server, reachable only through an
environment variable and the CLI, never an MCP tool, refusing any write that
does not name the graph it means.

**Recommended variant, if it is taken: require it only where the server can see
more than one graph.** A server with a single graph cannot misfile, and asking
its agent to confirm the only possible answer is ceremony that teaches the agent
to pass the parameter without reading it — the failure mode of every mandatory
field. `list_databases()` already answers the question, and the gate would turn
itself on at exactly the moment ambiguity appears, which is when a second graph
is created.

**The argument against taking it at all**, and it is not weak: unlike a judge,
the graph is something the server already knows, so this asks the agent to
restate a fact rather than supply one. The value is entirely in the two sides
being derived independently — the agent's *intent* against the server's *state*
— and that value is real only if the agent's side genuinely comes from its
intent rather than from a previous response it copied without thinking.

Not urgent. `expected_graph` plus the documentation is a real improvement on
silence, and this decides whether the remaining gap is worth a mandatory field.

---

### Issue 72 — a misdirected write journals in the graph it went to — ✅ DECIDED (2026-08-23), residue is #73

> **Decided 2026-08-23, before step 6, as this entry asked. Per graph stays;
> `review()` gains no `graphs=`; the response names its graph.**
>
> **The ids settle where a row lives.** `subject_ids` holds node ids, and a node
> id resolves only in the graph that holds it — so a row filed anywhere else
> carries ids that dereference nowhere, and a central journal would have to
> store the graph name on every row while every reader switched graphs before it
> could act on one. The row belongs beside its subjects, which is also what the
> forensic half of this entry describes: the misplaced material and the row that
> records it are **together** in `memory`. Nothing was orphaned. What was lost
> was knowing which graph to open, and `expected_graph` is the refusal for that.
>
> **The fan-out is the unsafe option, which is the opposite of how it looked.**
> `review(graphs=[…])` would have to borrow the active database and give it
> back — the `viz_list_*` pattern, unsafe under concurrent tool calls by its own
> docstring and by #16. The manual sequence is *safer*: `list_graphs` →
> `use_graph` → `review()`, once per graph, where each switch is the active
> state rather than one borrowed mid-call. **A convenience less safe than the
> sequence it replaces is not a convenience** — worth carrying forward, because
> the fan-out reads as the obvious improvement right up to the point you ask
> what it does to the connection.
>
> **The cheap half is where nearly all the value is.** Every `review()` response
> names the graph it answered from. That converts silent scoping into stated
> scoping, which is the whole content of the `WARNINGS_AND_SETTINGS.md` §5.2
> complaint this entry invoked: a reviewer who can see the answer is one graph
> wide can widen it.
>
> **The expensive half is a locator, not a reader**, and it is now **#73** —
> counts per graph, no rows — blocked on #16, since it needs a cross-graph read
> that does not move the active database.
>
> **One rule banked for #73: the graph is not a field on the row.** A merged
> listing tags each row with the graph it was *read from*. Stored, it would be
> free to disagree with where the row actually lives (a restored archive, a
> copied database), which is #54, #55 and #56 for the fourth time.
>
> Written up in `REVIEW_MODE.md` §6.6, with the signature note in §10.6.


The decision journal is per graph, like everything else. So an ingest that lands
in the wrong graph writes its `ingest` record **there** — the forensic trail
that would answer *who put this here and when* is filed beside the misplaced
material, in the graph nobody is looking at.

The wrong-graph incident is the worked example: it was found by an agent
re-reading its own conversation, and the journal row that describes it sits in
`memory` rather than in `field-notes`, where somebody investigating would look.

This generalises past the incident. **`review()` (steps 6–7) is per graph too**,
so *"show me everything this agent decided"* means *"…in this graph"*, and an
agent working across several graphs in one session cannot be reviewed in one
question. That is the same *"the reviewing agent ends up unable to ask one
question"* that `WARNINGS_AND_SETTINGS.md` §5.2 warns about, arriving through a
door the design did not consider.

**Not a bug in the journal** — per-graph is right, and a cross-graph journal
would need somewhere to live that is not a graph. What is missing is a *read*:
something that can ask several graphs the same question and say which answered.
`viz_list_nodes(database=…)` is the precedent for a cross-graph read that does
not switch the active database.

Worth settling **before step 6**, since `review()`'s signature is where the
answer would go.

---

### Issue 73 — a reviewer is not told which other graphs hold this agent's decisions — ✅ FIXED (2026-08-23)

> **Built 2026-08-23, as `review()`'s `elsewhere` — counts per graph, no rows,
> no new tool.** Every response now carries `elsewhere.graphs` (one entry per
> other graph, zeros included), `elsewhere.total`, `elsewhere.counted_with` and
> `elsewhere.unreadable`.
>
> **The count was the easy half; the *turn* was the design problem.** Reading
> another graph on SurrealDB means borrowing the connection, borrowing means
> taking the guard's mover turn, and `moving()` inside `using()` **raises** by
> design (#16) — you cannot exclude the calls using the graph while being one of
> them. So `review` had to declare itself in `MOVES_THE_GRAPH`, at the boundary,
> for the whole call. Two consequences, both accepted rather than hidden: a
> review now excludes other tool calls and viz snapshots for its duration, and
> it reads a **single instant** as a result — which is what a journal read
> wanted anyway, since a review racing a write was reading a moving target.
> **A read can be a mover.** The set was named as *the tools that move the
> graph* and it now contains one that only borrows it, which is the same
> distinction `viz_list_*` has always had.
>
> **#16's carry-forward repeated itself, and the fixture is what caught it.**
> The in-memory sweep is a dict lookup, borrows nothing, and passes whether or
> not the declaration exists — so an end-to-end test on one backend would have
> been green for the wrong reason, exactly as #16's first concurrency test was.
> The end-to-end fixture runs **both backends** for that reason, and removing
> the declaration was checked to fail it.
>
> **One rule banked, and it decided the scope: a locator may overcount and must
> never undercount.** Only the filters `query_decisions` already implements are
> mirrored — `agent_id`, `since`, `until`. `certainty_ceiling` and
> `mode="unreviewed"` are **not**, so a graph counted at 12 can list fewer than
> 12 once you switch to it. Every filter reimplemented for a second read is
> somewhere two implementations can disagree, and a locator that disagrees with
> the reader it points at is worse than one that is plainly wider: too high
> costs a wasted look, too low costs the look entirely. `counted_with` says
> which filters ran so the difference reads as scope rather than as a defect.
>
> **Both backends now build the journal filter once** — `_decision_matches`
> in-memory, `_decision_clauses` on SurrealDB — shared by the reader and the
> locator. That makes the agreement structural rather than remembered, and it
> matters most on the text-timestamp boundary #70 was about: the test that
> counts the row sitting exactly on `since` and then reads it there is what
> fails if the two ever stop sharing.
>
> **Naming a graph must not create one.** `USE` on an unknown SurrealDB database
> is not an error and `self._graphs[db]` on the in-memory backend creates, so a
> locator counting blind would have manufactured a database for every name it
> was asked about — one review turning into a namespace of empty graphs.
> Checked against `list_databases` first; an unknown graph is **omitted**, never
> counted zero, because *nothing there* is an answer and *not checked* is not.
>
> The manual sequence stays exactly where #72 left it: `list_graphs` →
> `use_graph` → `review()` is what you do once the locator has told you where.
> It was never the workaround, and the locator does not replace it.


#72 settled that the journal stays per graph and that `review()` takes no
`graphs=` list, and left one thing genuinely missing: **a reviewer has no way to
find out that there is more elsewhere.** `review()` names the graph it answered
from, so the scoping is stated rather than silent — but *"agent-1 also has 12
decisions in `field-notes`"* is the sentence that turns *go and look somewhere else*
from something the reviewer has to think of into something it is told.

This matters most for the reviewer the registry exists for: a **later, different**
agent (§2.2). The agent that made the decisions knows which graphs it worked in,
because it switched them itself. The one checking its work does not.

**A locator, not a reader.** Counts per graph and nothing else — no rows, no
`subject_ids`, nothing that would have to be dereferenced in a graph the caller
is not in. That keeps the payload honest (a merged row list is readable but not
actionable, since every write path is single-graph) and keeps the read small
enough to be worth doing across every graph in the namespace.

**Was blocked on #16, which was fixed the same day.** The locator needs a
cross-graph read that cannot move the active graph out from under a call in
flight, and that is now what `graph_guard.moving()` provides: a counting read
takes a mover's turn exactly as a snapshot does. What is left is deciding the
surface — a field on `review()`'s response is the obvious home, and #72's §6.6
says the reader tags each count with the graph it read, never the row.

**Two rules banked by #72, for whoever builds this:**

- **The graph is not a field on the row.** A merged listing tags each row with
  the graph it was *read from*. Stored, it could disagree with where the row
  actually lives — a restored archive, a copied database.
- **The manual sequence stays the fallback and is not a workaround.**
  `list_graphs` → `use_graph` → `review()` is safe precisely because each switch
  is the active state. The locator makes it *findable*, not obsolete.

Not urgent. Review works, and says what it covers.

---

### Issue 74 — a relation label is a string with no record, so nothing can describe it and nothing can decide about it — 🟡 STAGES 1–2 BUILT (2026-08-26, 2026-08-27), raised 2026-08-24, supersedes #69

> **Designed 2026-08-24 in `dev-docs/RELATION_LABELS.md`**, at the user's
> direction and before any code. Four stages: the record and a backfill;
> descriptions; FC1's verdict and suppression, which is where #69 resolves; and
> deprecation, left undecided because this entry has not settled whether
> relation merging should survive at all. Stage 2 precedes stage 3 for FC3's
> reason — the description is what lets an agent tell a synonym from a
> distinction. That document has the types, protocol methods, call sites and
> tests; this entry is the argument for why.
>
> **The standalone FC1 fix was considered and rejected**, and the reasoning is
> worth keeping here: keying suppression on the label strings looked like the
> small option, and it fails safe (a stale string suppresses nothing, which is
> today's behaviour). But declining is a **decision**, every decision leaves a
> journal row, and `subject_ids` holds node ids — so it would have forced the
> `subject_labels` field this entry argues against, as a side effect. *The
> cheap fix that answers an open question by accident is not the cheap fix.*

> **Stage 1 built 2026-08-26: the label has an identity.** `RelationLabel` beside
> `Metacontext`, three protocol methods on both backends, `link` creating the
> record when a label is coined, and `epimemer relations backfill` for a graph
> written before this. Additive, and no behaviour changes: nothing yet reads the
> description, and every read tolerates a missing record, which is the ordinary
> answer on any graph that predates it.
>
> **One defect found in the fix, and it was this issue's own defect one layer
> down.** `store_relation_label` took whatever record it was handed, so passing
> a freshly constructed `RelationLabel` for a label that already had one minted
> a **new id over the old** — and a journal row naming the label would then
> point at an id nothing resolves, which is exactly the *nothing to name in a
> decision* problem #74 exists to remove. It reached a passing test, which
> checked the description and not the id. Now `recorded_relation_label` is a
> pure merge both backends write through: `id`, `created_at` and `judged_by`
> come from the record already there, and only `description` and `metadata`
> move. The design **stated** that rule and left it to the callers; stating a
> constraint in a docstring is not enforcing it.
>
> That also makes **the coiner-never-the-describer rule structural**. Only `link`
> records a judge, because only `link` coins; a describer, a verdict or the
> backfill carries none. Preserving `judged_by` on update is what stops the next
> caller having to remember it.
>
> **The backfill's refusal says that nothing is lost.** It inherits the CLI's
> embedded-backend refusal — the default development configuration — so the
> message has to name the reason that is harmless: every write path creates the
> record, so the vocabulary fills in as it is used, and the command only exists
> to do it in one go. A refusal reading as *this graph cannot be fixed* would be
> worse than no command.
>
> 38 tests over both backends. Suite green at 2909. **FC1 is still live**: it is
> stage 3, and needs stage 2 first so a verdict is made against a described
> vocabulary.


**A user-tier relationship label exists nowhere.** `list_relations` *derives* the
vocabulary by scanning the edges of active nodes and grouping by `(label, kind)`.
There is no row, no id, no description — the label is a string repeated on every
edge that carries it. Three consequences, and they are the three open questions
about relations:

- **Nothing to describe.** An agent choosing a label sees a list of words with
  counts, and no way to learn what this graph means by each.
- **Nothing to name in a decision.** #69 asked where a label merge's
  `subject_ids` go, and the answer was *nowhere clean*, because the subject has
  no id.
- **Nothing to change but the edges.** "Renaming" a label means rewriting every
  edge carrying it, in place, irreversibly.

#### What was measured first, because the answer changes the question

**It fires approximately never.** `memory`, the largest real graph, holds
**one** user-tier relationship label — `published_by`, on 4 edges. A merge
nomination needs two labels of the same `kind` at ≥0.9 cosine. Zero are
possible. Not a load-bearing feature by usage.

**Labels do not affect retrieval.** `traversal_excluded` (`core/types.py`) is the
single function deciding whether a search expands through an edge, and it reads
`edge.type` and `edge.kind` — **never** `edge.label`. Outside `list_relations`
(counting), `link` (writing), and the engine-tier `published_by` constant in
`corroboration.py`, no query pipeline reads a user-tier label at all. So merging
two labels changes a string that gets printed and nothing about which nodes come
back. **That is a different class of operation from a fact merge**, which
destroys a node and moves corroboration counts — and the two have been sharing
machinery and vocabulary as though they were the same thing.

**It is a port, not a design.** `relation_consolidation.py` arrived in `4d3526b`
(2026-07-23), the same commit that **deleted** `tag_consolidation.py`, with the
same cosine function and the same 0.9 threshold. The tag premise did not survive
the port: a tag *was* the retrieval handle, so `billing` and `billings` really
were two buckets and a search for one missed the other. A relation label is not
a handle for anything.

**And it has no frame check** — now **#75**. `merge_facts` refuses cross-frame
pairs; label consolidation groups by `kind` alone, so two fictional universes in
one graph pool their vocabularies and are judged on string similarity. The worked
example, from the user: a servant *works for* a master where the culture has no
employment relation at all, while elsewhere in the same universe a corporation
formally *employs* an on-call consultant who does very little work. Near-identical
strings, opposite meanings, and the nominator sees only the strings.

> **Corrected 2026-08-24, on writing #75.** An earlier version of this paragraph
> implied a frame check would catch that example. **It would not** — both usages
> are in the *same* universe, so their derived frames are identical and nothing
> fires. The example argues for **descriptions**, which is what this entry
> actually proposes; the frame check is a separate and smaller thing. *A missing
> guard and the case that motivated the entry are not automatically the same
> problem.*

#### The proposal: give a label a record

A small stored entity, **not** a node — `Metacontext` is the precedent and the
shape: a name, a `description` ("longer explanation"), a status. Per graph,
because the same words mean different things in different graphs, which is the
whole content of the example above.

**Deprecation then replaces merging, and rewrites nothing.** Because labels do
not affect retrieval, marking `employed_by` as an alias of `works_for` needs no
edge to change: existing edges keep their own wording, and `list_relations`
shows the canonical set with aliases folded underneath. A lossy irreversible
bulk rewrite becomes a reversible annotation — and **#69 evaporates rather than
being answered**, because a decision about a label finally has an id to put in
`subject_ids`.

**The description is the half that pays.** It moves the intervention from repair
to prevention: an agent picking a label from a glossary never coins the fourth
synonym. `link` already calls `get_relation_kind` to reuse an existing label's
kind — the same lookup can hand back the label and what it means.

**Advisory prose, not a schema, and that is what keeps it out of hypergraph
territory.** One description per label per graph. It will not partition the
servant case from the consultant case, and it does not need to: it is prose an
agent reads, free to say *"in the Court context this means X; for corporate
contracts use Y."* Making it enforceable would make it a schema, and describing
individual **edges** rather than the shared label is the step that would make
this a hypergraph. Neither is proposed.

#### Explicitly not covered: relabelling one edge

Raised while settling this, and it is a **different operation** — filed here so
it is not mistaken for part of the above.

*"New information arrives and this particular relationship turns out to be formal
employment after all"* is a **correction to one claim**, not vocabulary
bookkeeping. There is no path for it today: `link` only creates, `relabel_edges`
is bulk-only, and `store_edge`/`delete_edge` exist on the protocol with no MCP
tool reaching them. An agent that learns this can only add a second edge and
leave the first standing, so the graph asserts both.

It also inherits a question nodes already answered. `NodeEdge.judged_by` records
**who asserted this edge**; changing the label in place silently reassigns that
assertion to a claim the original judge never made — which is exactly what the
design refuses to do for nodes, where a correction supersedes rather than
overwrites. Edges are not versioned, so there is nowhere for the superseded
assertion to go. Two honest shapes: retire the old edge and assert a new one
(needs an edge-retirement concept edges do not have), or change in place and
capture the prior label in `NodeEdge.metadata` — which is capture-or-lose, and
worthless unless written at the moment of the change (§7.1's rule again).

**Per-edge rename history is not proposed here.** If a label gains a record, its
history belongs on that record: one entry per rename rather than one per edge,
and it survives a rename that touched zero edges, which a per-edge log cannot.
The edge-level log is only worth building once something actually asks *what did
this edge originally say*.

#### Reversal parity, and where it stops

Raised by the user: *if a node merge can be reversed, a relabel should be too,
with the same caveats.* Agreed on the principle, and the principle is not about
nodes — **any operation collapsing many into one destroys the partition, and the
partition exists only at the moment of the operation.** That is §7.1's *capture
or lose*, stated about merges and true of collapse in general. So **whichever
collapses survive this issue must capture at the time they run**, not when
somebody wants the undo.

The qualification that decides the design: **for nodes, capture was the only
option; for labels, it is not.** A node merge must destroy — two nodes becoming
one is the point of it — and §7.1 chose capture because there was no
alternative. A label merge destroys for no gain, since deprecation delivers the
same tidier vocabulary while rewriting nothing. **Undo you never need beats undo
that works.**

`reverse_merge`'s three caveats transfer unevenly, which is worth having written
down before anyone reasons from the parallel too far:

- **Capture-or-lose transfers exactly.** Which edges carried `employed_by` exists
  only while the rewrite is happening. `merge_undo_depth`'s lesson applies too —
  past the retention window it is permanent.
- **"Refuse when the world moved on" transfers weakly.** Node reversal is refused
  when anything has accreted onto the survivor, because reversal *deletes* the
  node those edges point at and the refusal is all that stands between a
  contested claim and the silent loss of its contest record. A label reversal
  deletes nothing; nothing points at a label. The only accretion found is
  `get_relation_kind`, where a later edge inherited the surviving label's `kind`
  — real, and a footnote beside a vanishing contradiction.
- **The hard-delete caveat does not transfer at all.** Reversing a relabel writes
  a string back, so none of the narrow justification `reverse_merge` needs
  applies.

Easier for labels than for nodes, in other words — which supports the parallel
and is also exactly why not destroying is better still. **The place the
principle earns its keep is the single-edge relabel above**, where the loss is
real and unavoidable: the claim genuinely changed, the prior label is gone
unless captured, and the capture has to ship before the first relabel does.

#### Futile cycles to design against

Flagged by the user, and looking for them found one that is **live today**.

**FC1 — the nomination treadmill, running now.** `find_similar_relation_pairs`
re-derives from scratch on every `reflect`: scan edges, embed the label strings,
cosine, threshold. It records nothing about declines. **#64 closed exactly this
for fact pairs** — the `assessed` edge is a suppression index so a pair an agent
has judged never comes back — and relation labels got no equivalent. So a pair
correctly rejected (the servant/consultant case is the worked example) is
re-offered on every pass, for ever, and the cost is agent attention, which is
the scarcest thing here. A suppression edge cannot be written today because the
subject is a **label pair**, not a node pair — #69's field problem in a second
place, and a third thing a label record would fix.

**FC2 — deprecate ↔ un-deprecate.** Deprecation should be reversible, so two
agents disagreeing can alternate. Cheap in the graph, since nothing is
rewritten — but **the journal is append-only, so a futile cycle permanently
inflates the record**, and `review`'s difficulty signals keep resurfacing the
same pair. **FC1's suppression does not bound this** and an earlier draft said
it did: suppression stops the *nomination*, and deprecate/un-deprecate are
direct calls needing no nomination. The bound is a **cycle limit** in
`merge_cycle_limit`'s shape, counted from the label record's own state-change
history — which deprecation has to keep anyway, and which therefore has a
deadline: ship deprecation without it and the early oscillations cannot be
reconstructed. `RELATION_LABELS.md` §5. **Terminality is the wrong bound**,
though #68 is right next door: its retraction is one-way because a false
unification manufactures agreement, and neither that failure nor its opposite
exists for a label.

**FC3 — nudge, comply, re-coin, nudge.** A coin-time nudge (*"`works_for`
already exists"*) plus a later agent that genuinely needs the distinction gives
a loop with no exit. **Not reachable, because nothing proposes a nudge** —
`RELATION_LABELS.md` §3.2 has `link` *report* the description of a label it
reused, which is information rather than a redirect, and §8 now names steering
as a non-goal. The constraint stands for whatever proposes one: **a nudge must
carry the description**, or neither it nor the agent being nudged can tell a
synonym from a distinction.

**Three of these four are not reachable**, which is the useful thing to say
about them: FC2 needs deprecation, FC3 needs steering, FC4 needs renaming, and
none of the three is built. They are preconditions on features that would
create them, not work outstanding — and each is recorded against the thing that
would make it possible rather than in a shared list that reads like a defect
register. **FC1 is the only one describing something the system does now.**

**FC4 — rename ping-pong, and a second argument about where history goes.**
Bulk relabel A→B then B→A. With per-edge capture each pass appends to every
affected edge, so the cycle costs O(edges × cycles); with history on the label
record it is O(cycles). An independent reason for the placement argued above.
Not reachable either: renaming is not built, because edges join to the label by
string.

**The general rule, worth checking against any new nominator: a sweep that is
recomputed from current state and records no declines is a futile cycle by
construction.** It re-offers what was already refused, and it cannot know it is
doing so. That is #64's lesson stated once rather than rediscovered per feature.

**And its dual, which the fix creates: a suppression with no retraction makes
every wrong decline permanent by construction.** The fact-pair layer chose that
deliberately — `similarity_decisions.py` says the pair *"stays out of every
future nomination"*, and #68's retraction left suppression untouched on purpose
— so it is inherited knowingly here rather than by accident. Both halves are
stated in `RELATION_LABELS.md` §4.2, along with the reason #68's
**one-directional** retraction must not be copied across unexamined: it is
terminal because a false unification manufactures agreement while a withdrawal
only under-counts, and **neither failure exists for labels**, since nothing
corroborates on one.

#### Cost, stated plainly

A new stored entity is a table on both backends, protocol methods, at least one
read tool and one write tool, and a viz row. Not large, and larger than #69 was.
The description and the deprecation are separable: the description pays on its
own and does not depend on aliases existing.

---

### Issue 75 — relation-label nominations ignore metacontext, and the obvious reason to care is the wrong one — 🟡 OPEN (raised 2026-08-24, from #74)

`merge_facts` refuses a pair whose facts do not stand in **exactly the same set
of frames** (`fact_dedup.py`), and `apply_reflection(similarities=…)` refuses a
cross-frame `one_claim`. `find_similar_relation_pairs` groups by `kind` alone
and reads no frames at all. Two fictional universes in one graph therefore pool
their vocabularies, and the whole test is cosine similarity over the two label
strings.

**The asymmetry is real and is filed so nobody reads it as an oversight.** What
follows is why the fact-side justification does *not* transfer, and what a check
here would actually be worth — which is less than it first appears, and not what
this entry was expected to say.

#### The corroboration harm does not exist here

`merge_facts` refuses cross-frame pairs because a merged node inherits the
**union** of its sources' frames, so collapsing a base-reality claim into one
also framed as fiction leaves a node asserting both — *"the single worst outcome
available"*, in that module's own words, and the same manufactured-agreement
failure `fact_dedup` exists to prevent.

**Nothing here inherits anything.** Labels do not affect retrieval (#74), so
nothing corroborates on one: merging a fiction label into a fact label invents
no support, moves no count, and changes no answer to any query. Under #74's
deprecation model it rewrites nothing at all. The harm is that the **vocabulary**
loses a distinction the frames were carrying — a description problem, not an
epistemic one.

#### It does not catch the case that motivated it, and #74 says it does

The worked example is a servant who *works for* a master in a culture with no
employment relation, beside a corporation that formally *employs* an on-call
consultant — **both inside the same fictional universe**. If that universe is
one metacontext, or both usages are untagged, their frame sets are identical and
no frame check fires. `RELATION_LABELS.md` §8 and #74's own text claim it "would
stop the servant/consultant pair being proposed at all"; **that is wrong**, and
both are corrected alongside this entry.

What a check would catch is a different case: *two universes*, or fiction beside
base reality, where one label is used only in each. Real, and not the case
anybody was worried about.

#### If it is built, the bar is disjointness, not equality

A label has no frame of its own. Its frames are derived — the union of
`frames_for` over the endpoint nodes of every edge carrying it, via
`frame_resolver`, which exists for exactly this many-pairs shape.

**Do not copy `fact_dedup`'s *exactly the same set* rule.** That bar is right
there because a merge inherits a union; here nothing inherits, so a label
legitimately used in two frames would become unmergeable with anything. The
right question is `same_frame`'s — **share at least one** — and the check is its
negation: do not nominate a pair whose derived frame sets are **disjoint**. Two
questions, already distinguished in `review.py`, and this is the second one.

#### Worth what, then

- **A nomination-quality improvement**, not a safety check: a pair used in wholly
  disjoint frames is unlikely to be a genuine synonym, and nominating it spends
  the scarcest thing in the loop.
- **It matters more once deprecation exists** (`RELATION_LABELS.md` stage 4),
  where `list_relations` would fold a fiction label under a fact one.
- **The better answer to the cross-frame case is the description** (stage 2),
  which can say *"in the Court frame this means X"* — the distinction stated
  rather than the pair refused.

**Not urgent, and less urgent than it looked.** Zero nominations are possible on
the largest real graph today (one label), and the failure this would prevent
costs a wasted look rather than a wrong belief. Independent of every stage of
`RELATION_LABELS.md`; buildable at any point after stage 1 gives labels records
to hang derived frames off, and arguably not worth building before stage 4.

---

### Issue 76 — the base metacontext has no row, and absence is silently promoted to an assertion — 🟡 DECIDED 2026-08-27, BUILD PENDING (raised 2026-08-24, two of three built 2026-08-25)

> **Decided 2026-08-27 by the user, after two review rounds: require the frame
> at ingest, and drop the declared default outright. The remaining build is
> seven steps and they ship together.**
>
> **The declared default is dropped, not deferred, and the wording it replaces
> was itself the hazard.** *"Deferred until a graph holds mixed content"*
> invites someone to build it on the day it is most dangerous. Two arguments
> killed it, neither of which is in the section below:
>
> - **It fails at the thing it is for.** Under a declared default, whether a
>   claim is fiction or real history depends on **which graph the write landed
>   in** — the same ambient process state `expected_graph` exists because it
>   cannot be trusted (#71). It fails in the other direction too: a genuinely
>   real fact recorded while worldbuilding is filed as fiction, silently. By this
>   entry's own standard — *the assumption is invisible and undeclared, not that
>   it exists* — a graph-level declaration leaves the assumption invisible at the
>   **write**, which is where the confusion happens.
> - **It is retroactive by construction.** `frames_for` resolves absence at
>   **read** time (`or {BASE_METACONTEXT_ID}`, `review.py:226`), so the override
>   would replace that constant and flipping it would reclassify every untagged
>   node in the graph at once — no per-node record, no journal row, no `reframe`
>   trail. A bulk epistemic move dressed as a config edit, and the `AGENTS.md`
>   counter-case exactly: a guard configured by the state it guards against. Had
>   it been built it would have needed to be write-once, user-only, CLI/env and
>   never an MCP tool. **And once the field is required it has no upside left** —
>   it could only ever act on the legacy population, making it a pure retroactive
>   reclassifier: the dangerous half with none of the benefit.
>
> **The habituation objection is dead, and not for the reason first offered.**
> The implementer argued it got cheaper because `reframe` shipped the same
> morning (#66) — true, convenient, and not load-bearing. The real answer is
> that habituation assumes absence is a readable third state, and it is not:
> `frames_for` promotes absence to The Real before any consumer sees it, so
> contradiction detection, `merge_facts` and `search` all already act on it as a
> deliberate base-reality claim. **There is no signal for a reflexive `the-real`
> to degrade.** A stated one is the same assertion carrying a judge and a journal
> row, which makes it findable by `review(by_agent)` and fixable by `reframe`.
> The precedent nobody cited until review: **`expected_graph` is required
> everywhere and answered near-reflexively**, and this repo already decided a
> cheap mostly-reflexive declaration is worth requiring when the failure it
> guards is silent.
>
> **What the requirement honestly does not do is prevent the error.** A templated
> `the-real` on a fiction ingest is exactly as wrong as silence was. The pitch is
> **detectability and recoverability**, never prevention.
>
> **The finding that changes the shape of the build: `untagged` does not stop
> being producible.** `apply_reflection` mints untagged nodes today — parent
> synthesis (`tools.py:3237`) and splits (`tools.py:3269`) both create a `Topic`
> with no frame edge, and `plan_subtopic_edges` (`versioning.py:586`) returns
> `SUBTOPIC_OF` edges only. So **reflect converts framed knowledge into unframed
> assertions**, which is this entry's promotion arriving through a side door.
> A softer second instance: topic merge migrates edges from every source and has
> no frame gate — `merge_refusal`'s frame-set-equality check lives in
> `fact_dedup` and covers facts alone — so a cross-frame topic merge leaves the
> survivor asserted in both worlds. Enrichment is clean: it goes through
> `supersede_node(status=CORRECTED)` and migration carries the frame edge, per
> `WORLD_CHANGE_COPIED_EDGE_TYPES` (`types.py:400`), whose comment names this
> exact hazard. Both leaks are moot today — zero frames exist anywhere — and both
> go live the day the requirement makes frames real.
>
> **The build, and the ordering is binding:**
>
> 1. `metacontext_id` becomes **required** on `store_decomposition`. A breaking
>    MCP change, deliberately.
> 2. Splits **inherit** the parent's frame set — same content, refined.
> 3. Synthesis inherits when all children share one frame set and **refuses when
>    they do not** — `merge_refusal`'s precedent one level up. Not a union: one
>    topic asserted in two worlds is the outcome this system calls the worst
>    available.
> 4. Topic merge gets the frame-set-equality gate facts already have.
> 5. `search` stays **optional**. Required, it would make cross-frame search
>    impossible, and the frame-plus-base inheritance model means an omitted
>    filter is a coherent question rather than an unstated assumption — the read
>    side has no absence problem.
> 6. **No backfill of the 684 legacy nodes.** Writing `the-real` onto them would
>    manufacture 684 judge-less, deliberate-looking assertions — the exact
>    ambiguity the requirement exists to end. Record the date the rule started
>    somewhere queryable (graph metadata or `docs/`, not only here); `created_at`
>    bounds the population for any reviewer. The `0.5`-confidence legacy
>    population in #46 is the precedent. The promotion is correct for
>    approximately all 684, since every existing graph is genuinely base-reality
>    content — so this is a documentation obligation, not a migration.
> 7. The docstring tells agents to split a mixed document into two calls.
>
> **Steps 1 and 2–4 must ship in the same change.** The date boundary in step 6
> is only honest if reflect has stopped minting untagged nodes; ship them apart
> and the recorded date is false the day it is written.
>
> **The granularity limit, recorded so it is not rediscovered.**
> `metacontext_id` is one value applied to every node in the decomposition
> (`tools.py:869–872`), and the motivating Le Guin case is a **mixed batch** — a
> real-author fact inside a fiction-frame discussion. The requirement forces an
> answer per call; it cannot make the answer right per node. The eventual shape
> is already anticipated by this repo's own guidance: facts and topics accept
> per-node objects for `importance`/`confidence` because *the same message can
> carry a 0.9 preference and a 0.3 guess*. A per-node frame override belongs in
> that object, for the same reason. Not now — but the design must not foreclose
> it.

> **Two of the three are built (2026-08-25): the phantom, and the unvalidated
> id. The declared default stays open.**
>
> **The row.** `store_decomposition` now calls `ensure_base_metacontext` before
> it writes anything, so the first ingest into a graph gives The Real a record.
> Put there rather than at graph creation because *that* is where the claim is
> actually made — an empty graph asserts nothing about any world, and a graph
> created before this change would otherwise never get the row at all. It is
> one keyed read per ingest, which after the first document is all it is.
>
> **A stated frame must resolve here**, on `store_decomposition` and on
> `search` alike, raising the way `_extraction_timeline` already refuses a
> timeline that does not exist — the precedent was sitting one function above
> the defect. `require_metacontext` is the single home, and the refusal **lists
> the frames that do exist**, because no MCP tool enumerates metacontexts: for
> an agent holding a stale id that message is the only place the right one
> appears.
>
> **Three judgments worth keeping:**
>
> - **`the-real` is accepted with no row.** It is reserved, and it is what
>   `frames_for` answers for an untagged node, so it names a real frame in
>   every graph — including one nothing has been written to yet. Checking it
>   against storage would refuse the one id that cannot be wrong.
> - **`search` is checked too, and it is the half that mattered more.** A
>   dangling id there does not fail; it narrows to base reality alone and
>   answers as though that were the frame — the wrong-graph read failure one
>   layer in, leaving no artifact anywhere afterwards.
> - **`cross_frame=True` does not excuse a bad id.** The flag makes the id
>   inert for filtering, which is exactly why a wrong one would go unnoticed
>   there. One rule beats a rule with an exception.
>
> The check runs **before** the document is built, so a bad id costs nothing
> and leaves no partial decomposition behind. Twenty tests across both
> backends; a residue was also cleared — a dangling first line of a comment
> left behind when #71 moved the wrong-graph gate to the MCP boundary.
>
> **Still open: the declared per-graph default.** Nothing below it changed. It
> is worth building when a graph actually holds mixed content, and none does.

Two halves of one gap, filed together because the second explains why the first
went unnoticed.

#### The phantom

`BASE_METACONTEXT_ID = "the-real"` is a reserved id, and `frames_for` hands it
back for every node with no `has_metacontext` edges. `ensure_base_metacontext`
creates the row — and **nothing calls it.** The only caller in the repository is
a test.

**Measured 2026-08-24 on `memory`: `metacontexts: 0` across 684 nodes.** So every
node in the largest real graph stands in a frame that has no record: absent from
`get_metacontexts`, absent from the dashboard, and impossible for an agent to
look up or read a description of. Nothing breaks, because every consumer does id
comparisons rather than lookups — which is exactly why it survived.

The fix is one call and no behaviour change: create the row where a graph is
first written to, so *"The Real — base reality, the default frame for untagged
knowledge"* is a thing an agent can see it has been writing into.

**Built 2026-08-25** in `store_decomposition`. A graph written to before that
date still has no row until its next ingest, which is the right shape: the row
appears the first time the graph asserts anything into the frame.

#### Absence as assertion

The larger half, and the reason the phantom is easy to miss. **This is the one
place in the system where absence is promoted to a positive claim.** Elsewhere
the rule is stated and enforced:

- `confidence` — *"omitting stores 'unrated', which is deliberately different
  from a rated 0.5"* (#46)
- `judged_by` — *"Absent means **unknown**, and nothing more"* (§3.3)
- `claim_kind` — *"Omit it when you genuinely cannot tell"*

An untagged node does not record *nobody said which world this is about*. It
asserts *this is true of the real world*. So an agent that ingests fiction and
forgets to tag it yields a graph asserting fiction as fact, and nothing can
distinguish that from a deliberate base-reality claim. On 684 nodes, no agent
has ever said it once.

**But the defaulting is forced, and that is the part this entry does not
propose changing.** Frame is not like confidence: a ranker can skip an unrated
confidence, and absence costs nothing. A contradiction detector **cannot** skip
an unknown frame — it has to decide whether the conflict is genuine, and so does
`merge_facts`, which compares frame *sets*. There is no useful behaviour for
*unknown frame*, so something must be assumed. **What is wrong is that the
assumption is invisible and undeclared, not that it exists.**

#### The shape, if it is built

**A per-graph declared default**, in the `reflect_threshold` pattern `AGENTS.md`
blesses: a process default, a persisted per-graph override, and one pure
`resolve_*(override, default)`. That converts an implicit default into a
declared one — #71's move, one layer in.

**Requiring `metacontext_id` on every ingest was considered and is worse**, and
the reason generalises. The surface is small — **two** tools take the field
today, `store_decomposition` and `search` — so the cost is not the parameter
count. Two arguments against:

- ~~**The requirement cannot validate itself.**~~ **Retracted 2026-08-24, by the
  user, and it was the argument this section rested on.** Metacontext ids are
  **per graph**, so a stated id is checkable against the active graph exactly as
  `expected_graph` is: it resolves here or it does not. The case it catches is
  the wrong-graph incident one layer in — an agent that believes it is in the
  fiction graph, names the fiction frame, and is actually in the default graph.
  Today that call writes untagged content which silently becomes a base-reality
  assertion. **A value is checkable whenever it names something in a namespace
  the server owns**, and *only the agent knows it* was the wrong test.
  One limit: naming **The Real** catches nothing, since that id is valid in
  every graph — so the check bites for exactly the graph-specific frames where
  a mistake is dangerous. And a second value survives independently of any
  check: a stated frame keeps the error **recoverable**, because fiction that
  lands in the wrong graph carrying its frame is misfiled, while fiction that
  lands untagged has become fact.
- **Habituation degrades the signal it was added to create.** Every graph today
  is single-frame, so a required field would be answered identically on every
  ingest for ever, which trains the answer to be reflexive — and then reads as
  deliberate on the day a mixed graph appears.

A declared default is strictly better on both counts: single-frame graphs say it
once, and an explicit `metacontext_id` at ingest then **means** something,
because it is a departure from something stated rather than the only way to say
anything at all.

#### And nothing validates the id today

Found while settling the above. `store_decomposition` writes the framing edge to
whatever it is handed:

```python
if metacontext_id:
    for node in seg_nodes:
        batch_edges.append(NodeEdge(
            src_id=node.id, dst_id=metacontext_id, type=EdgeType.HAS_METACONTEXT,
        ))
```

No existence check, on either backend. So an id from another graph, or a typo,
produces an edge pointing at nothing — and the consequence is worse than being
untagged, which is at least coherent.

**A node in a frame that does not exist becomes epistemically isolated,
silently.** `frames_for` returns the dangling id rather than falling back to
base reality, so the node shares a frame with **nothing**: it is never nominated
as contradicting anything, never merges with anything, and drops out of every
frame-scoped search — including a search for the frame the agent meant. It is
present in the graph and unreachable by every mechanism that would have
questioned it.

**The fix is one existence check and is worth doing whether or not the field
becomes required**: refuse a `metacontext_id` that does not resolve in the
active graph. It is also the mechanism that makes requiring the field viable at
all, per the retraction above.

**Built 2026-08-25** as `require_metacontext`, called by both tools that take
the field. See the amendment at the top of this entry for what the check
deliberately lets through (`the-real`) and what it deliberately does not
(`cross_frame=True`).

#### The overloading, and the worked example that shows it

`the-real` does two jobs: **the default for untagged nodes**, and **a positive
frame meaning base reality as opposed to fiction**. The user's example is what
separates them, and it is not a fiction case at all — *"what Milanese people
knew by 1860"* and *"what Londoners knew by 1860"* are two frames over the same
real past. `Metacontext`'s own docstring anticipates this (*"Reporting by the
BBC"*), so the model supports it; nothing guides it.

**The choice has teeth.** `same_frame` asks whether two nodes share *at least
one* frame, and contradiction nomination skips any pair that does not:

- `{the-real, milan-1860}` vs `{the-real, london-1860}` — share `the-real`, so
  the pair is **nominated as a contradiction**.
- `{milan-1860}` vs `{london-1860}` — disjoint, so it is **never nominated**.

`merge_facts` is safe either way, comparing frame sets for equality. Contradiction
detection is where the modelling choice silently becomes a behaviour.

**The rule is already in the code and was never written down for agents.**
`_in_frame_nodes`: *"Knowledge in the base frame applies everywhere; sibling
frames are excluded."* So a claim in the base frame is asserted in **every**
frame, which is the opposite of what a perspective frame is for — and the
guidance follows directly:

- **Do not add `the-real` to a perspective frame.** Tag the perspective alone.
- **The test is whether the claim holds in every other frame in this graph.**
  *"Milan is in Lombardy"* is shared background and belongs to base reality;
  *"Milanese merchants believed the pass was closed"* does not.
- **The inheritance is one-way, and that is the design**: a Milan-scoped search
  returns Milan nodes *plus* base-frame nodes, so the shared world flows into
  the frame while the perspective does not flow out.
- **Two perspectives disagreeing about one world are not nominated**, which is
  usually right — they coexist, neither claiming the other is wrong. Where the
  disagreement is the finding, `record_contradiction` still asserts it and
  returns `same_frame: false`, marking it as cross-frame rather than as a
  same-world conflict. Assertion is available; only automatic detection is gated.

Drafted 2026-08-24 into `AGENTS.md` and `epimemer_prompts/DEFAULT.md` ahead of
any build, because the choice is being made by agents already and the graph
cannot tell them it was made wrongly.

#### Order

The phantom first — one call, no behaviour change, and it makes the default
frame visible, which is most of what the second half is complaining about. The
guidance above costs nothing and shipped immediately. The declared default
becomes worth building when a graph actually holds mixed content; today none
does.

> **2026-08-25:** the phantom and the existence check are built. What remains
> is the declared default, and the trigger is unchanged — **a graph that holds
> more than one frame's worth of content.** Until then a per-graph default
> would be stated once and never read, which is the habituation argument
> against requiring the field, pointed at the remedy instead.

---

### Issue 77 — a judge id can never be changed, and the prompt that assigns one hides what already exists — 🟡 OPEN (raised 2026-08-25), superseded by #78

> **Superseded 2026-08-25 by #78, the same day, after the user pushed back
> on the rejection below and was right.** Everything recorded here still
> holds as a description of the defects; what changed is the remedy. The
> "rejected" section is kept rather than deleted, because #78 exists
> because of how it was wrong.

Two halves that caused each other. The second is how the first happened, on
this repository's own graph, on the day it was filed.

#### The split, and that nothing joins it

`JudgeRef.agent_id` is frozen into every node's `judged_by`, every edge, every
value signal and every journal row at write time. `review(mode="by_agent")`
matches it as an exact string. **There is no rename and no alias**: `rejudge`
revises `claim_kind`, `confidence` and `confidence_basis` — a judgment about a
claim — and touches nothing about who made it. So changing the id does not
re-attribute a judge's history. It splits it, permanently and silently, and
nothing in the graph records that the two halves were ever one agent.

**Measured 2026-08-25 on `memory`:** two decision rows, one under
`Opus 5 Judge` (2026-08-23) and one under `Opus 5` (2026-08-25) — the same
model, the same repository, the same work, asked for by `by_agent` under either
id and returning half the answer each time. The cost today is one row against
one row. It only grows.

#### How the second id came to exist

`_elicit_agent_id` asks the user to accept or edit a proposed id. **It does not
tell them which ids this graph has already approved**, and it takes free text
(`response_type=str`). `_unapproved_reason` — the *refusal* path, taken only
where no elicitation channel exists — does list them. So the one place a human
actually chooses an id is the one place the existing ids are not shown.

The sequence on 2026-08-25 was exactly that: an agent proposed `claude-opus-5`;
the prompt named that id and the self-description and nothing else; the user
typed `Opus 5`; `Opus 5 Judge` already existed, with the only decision in the
graph. A prompt that had listed one line — *this graph already knows
`Opus 5 Judge`* — would have made the collision visible at the moment it was
being created.

**The same shape refuses a typo.** Free text means every reuse of an existing id
is retyped exactly, and a keystroke mints a new judge with no warning and no way
back. That is the first half, reachable by accident.

**And the response does not say a judge is new.** `claim_agent` returns
`description_versions: 1` and `new_description: true`, from which *this judge
has no history* can be inferred and was not. Nothing says `new_agent: true`.

#### The shape, if it is built

**Aliasing.** An `Agent` gains the ability to point at the id that supersedes
it, and `review(mode="by_agent")` follows the chain. Nothing is rewritten:
journal rows keep the id they recorded, exactly as `rejudge` keeps the value it
replaces and a dissent records a finding rather than performing an undo. The
append-only-list-with-dates shape is already in `Agent.descriptions` and in
`LifecycleEpisode`, so this is one more member of a pattern rather than new
machinery. Aliasing must be user-assigned through the same channels as approval
— an agent that could declare itself a continuation of another judge could
launder self-review into independent review, which is the property §2.2 exists
to protect.

**The prompt.** List the ids this graph has approved, and which of them already
carry decisions. Offer them as choices rather than as prose to be retyped. Say
plainly when the answer will create a **new** judge rather than reuse one, and
carry the same in the response.

#### Rejected: a stable UUID plus a mutable display name

Raised by the user 2026-08-25 — give each judge an opaque id and a separate
human-readable name, so a rename never touches the graph. It is the standard
fix for an identifier doubling as a label, and it is the wrong one here.

- **The readable id is load-bearing.** `Agent`'s docstring: *"The id is assigned
  by the user, which is what makes review provable — an agent that mints its own
  id cannot establish that it is a different agent from the one that decided
  yesterday."* The approval channels are `EPIMEMER_APPROVED_AGENTS`, the
  `epimemer agents confirm` CLI, and `ctx.elicit`. Approving a UUID is approving
  something the approver cannot identify, which turns a decision into a rubber
  stamp.
- **The hard part only moves.** An agent cannot propose an opaque id, so it
  would propose a name and the server would resolve it. Same name → same UUID
  makes the name the identity again and the UUID decoration. Same name → new
  UUID mints two judges that display identically, which is **worse** than the
  present defect: today the two ids are visibly different, so the split is at
  least legible.
- **A name resolved at read time renames the past.** The digest on `JudgeRef`
  pins the description version current at the decision, deliberately, *"which is
  what makes a decision readable years later without an as-of query"*. A display
  name joined at read time reintroduces the thing that design rejected — right
  for a typo, wrong when a judge's role changes and old decisions start showing
  a name that judge did not hold.
- **It does not fix the split that exists.** UUIDs make *future* renames free
  and do nothing for `Opus 5 Judge` versus `Opus 5`. That needs an alias — and
  with the alias, the UUID buys nothing.
- **The separation it asks for is already there.** `Agent.descriptions` is
  append-only, versioned, human-readable and freely revisable; `id` is the
  stable key. A display name would be a third layer over a seam that exists.

**Against two decision rows**, a migration touching every `judged_by`, the
approved-id list, the env var, the CLI and the docs is a great deal of machinery
for a problem that has fired once — the test that settled #74.

#### Order

**The prompt first.** It is the cheaper half, it needs no schema change, and it
is preventive: every day it is not fixed is another chance to mint a judge
nobody meant to create. Aliasing is the remedy for splits that already exist,
and there is one, worth two rows. Neither is urgent; both get more expensive
exactly as the journal fills, which is the argument for doing the preventive
half now and the repair when a real query is answered wrongly by it.

---

### Issue 78 — judge identity conflates the key, the name and the claim, and the prompt that assigns all three shows nothing — 🟢 FIXED 2026-08-26 (raised 2026-08-25, supersedes #77)

> **Stage 1 built 2026-08-25: the picker, the cadence, and the new-judge flag.**
> Defects 1, 2, 3, 6, 7, 8, 9 and 10 are closed; 5 is softened, since a name
> chosen badly can at least now be *seen* and re-picked rather than retyped.
> **4 and the three-layer split are untouched** and are the remaining stages.
>
> `judge_roster` builds the options — the union of the agent records and the
> approved ids, ordered most-recently-used first — and `_elicit_agent_id` offers
> them through `ctx.elicit`'s titled-choice form. The ordering is load-bearing
> rather than cosmetic: the cadence asks on **every** bind, which is affordable
> only while the answer the user wants is the first line offered.
>
> **A defect in the fix, found by an existing test.** Asking on every bind meant
> a client with no elicitation channel could no longer claim *at all* — because
> "the user declined" and "there is nobody to ask" were the **same value**,
> `None`, and the conflation had been harmless only while an approved id skipped
> the question entirely. `ApprovalOutcome` now separates them, and they go
> opposite ways: **declined refuses even a pre-approved id**, because a person
> saying no is not overruled by a list they added to last week; **unavailable
> falls back to the approved list**, because `EPIMEMER_APPROVED_AGENTS` and
> `epimemer agents confirm` are §2.3's user involvement happening earlier rather
> than none. The test that caught it was `_silent`, whose docstring said *"a
> client with no channel to the user"* while its code said *declined* — the
> comment was right and the model was wrong, and nothing had needed to tell them
> apart until now.
>
> **Two smaller judgments.** Choice keys are prefixed (`use:`), because agent
> ids are user-assigned free text validated for emptiness and nothing else, so a
> bare sentinel for *a new judge* is a string somebody could legitimately be
> called. And a picker that cannot render **degrades to the free-text prompt**
> rather than to a refusal, since rendering a choice schema is the client's
> business — what must not degrade is *asking*.
>
> Decision counts were considered for the roster line and dropped: one journal
> query per agent, on the path of every claim, to disambiguate what
> `last used <date>` already disambiguates.
>
> **Exercised live 2026-08-25 against `memory`**, which is the only test that
> could settle it: rendering a choice schema is the client's business and no
> unit test reaches it. It rendered as a selectable table, the lines were
> readable, and both `Opus 5 Judge` and `Opus 5` were offered — so the split
> that defect 4 describes is now something the user *sees* every time they
> bind, which is as far as stage 1 goes toward fixing it. The claim also took
> 65 seconds, which is the point: an already-approved id waited on a person,
> where the old code would have bound in milliseconds without one.
>
> **One friction, and it is not ours to fix.** The table opens only after a
> right-arrow keypress; collapsed, it shows the message alone. That is the
> client's elicitation UI, and the temptation is to write *press → to see the
> judges* into the prompt — which would be wrong in every other client and
> stale the moment this one changes. What the server can do it already does:
> the message names the question and the graph, so a collapsed prompt still
> says what is being asked. Recorded because a future reader will meet the same
> friction and should not go looking for it in this layer.
>
> One defect shipped and was fixed the same day: the message read *"Judging as
> 'Opus 5' The user confirmed this description."* — three optional clauses
> concatenated, and the not-a-new-judge branch contributed no sentence break.
> Now pinned by a test over both branches.

> **Stage 2 built 2026-08-26: the three-layer split, and the migration that
> absorbs aliasing.** Defect 4 and defect 5 close, which is the whole of what
> stage 1 left. `Agent` now carries `id` (opaque, `new_agent_id()`, shown to
> nobody), `name` (freely renamable, unique per graph, resolved at read time)
> and `former_ids`; `descriptions` is untouched.
>
> **No migration writes anything.** The plan had existing string ids becoming
> former ids of a fresh UUID, and that turned out to be unnecessary work: an
> opaque key is opaque whatever it looks like, so a legacy id simply *is* the
> key, and `agent_name` reads it as the name — which is what it was. Only
> **new** judges get a UUID. A record written before the split is named on its
> next claim, and nothing else changes. The one real migration is the
> user-facing one the design predicted: two records that should be one.
>
> **Consolidation arrives through renaming rather than as a menu entry**, and
> that is the better shape. Renaming to a name another judge holds returns
> `same_judge_needed` instead of refusing, and the caller asks. So the repair
> appears exactly where the duplication is visible, needs no second concept, and
> the CLI answers the same question with `--same-judge`. Nothing is deleted:
> the absorbed record is kept, `live_agents` derives that it is no longer a
> judge, and both description histories merge — dropping the absorbed one would
> leave its own old decisions unreadable through the record that now answers for
> them, since a decision records `(key, digest)`.
>
> **`query_decisions(agent_id=…)` became `agent_ids=…`** on the protocol and
> both backends, because after a consolidation a judge *is* a set of keys.
> `judge_aliases` is the single place a handle resolves, and it lives beside the
> protocol rather than at the MCP boundary because `apply_review`'s duplicate
> check needs it too — a check that saw only the currently bound key would let
> one judge confirm the same decision twice.
>
> **Two smaller judgments.** The approved list holds **keys**, not names, or a
> rename would silently withdraw an approval; `seed_approved_judges` resolves at
> the seeding boundary so `EPIMEMER_APPROVED_AGENTS` and `epimemer agents
> confirm` still take the names a person types. And a picker answer is treated
> as a **handle**, so typing an existing name at the *new judge* prompt joins
> that judge rather than minting a second record with the same name — which is
> literally how `Opus 5 Judge` and `Opus 5` came to exist.
>
> **A defect found while writing the tests, in the fix.** Choosing a bare
> approved id from the picker — one the user seeded and nothing has claimed —
> minted a UUID beside it, orphaning the approval the user had actually given.
> A seeded string *is* its key, because nothing else could be; the claim path
> now adopts it and only mints where the handle is genuinely new.
>
> Rename reaches the user through the picker **and** the CLI, not one or the
> other: the CLI cannot reach an embedded or in-memory store at all, and the
> picker is where a user sees the wrong name.
>
> 79 tests added — 25 pure (`tests/core/test_agent_identity.py`), 42
> behavioural over both backends (`tests/mcp/test_judge_identity.py`), plus the
> journal's set semantics and the CLI command. Suite green at 2871.

One entry for ten defects, because they cause each other and fixing any one
alone makes another worse. #77 filed the first pass and proposed the wrong
remedy; this replaces it.

#### The ten

**The gate is weaker than it reads.** It guards *minting* an id, never
*assuming* one.

1. **An already-approved id binds silently.** `claim_agent` elicits only where
   `agent_id not in approved`. Once an id is approved in a graph, any session,
   any agent, binds to it with no user involvement at all.
2. **The refusal hands out the valid ids.** `_unapproved_reason` returns
   `approved_agent_ids` in the payload — correct in isolation (§2.2: *the
   refusal is the prompt*), and combined with (1) it makes a refusal a directory
   lookup. Propose anything, read the list, claim one silently.
3. **An unchanged description prompts nothing either.** The second gate fires
   only `if is_new_text`, so re-using a previous self-description verbatim
   passes both gates in silence.

**Identity is permanent and overloaded.**

4. **No rename and no alias.** `agent_id` is frozen into every `judged_by`,
   every journal row and every value signal at write time, and
   `review(mode="by_agent")` matches it exactly. Changing it splits a judge's
   history permanently, and nothing records that the halves were ever one agent.
   **Measured on `memory`: one row under `Opus 5 Judge`, one under `Opus 5`.**
5. **The id is asked for at the worst possible moment.** The user must name a
   judge perfectly on first contact — before knowing what it will be used for —
   and that name is load-bearing for ever. Names legitimately evolve; so does
   what a judge is *for*. This is how the split above came to exist.

**The prompt shows nothing.**

6. **`_elicit_agent_id` does not list the ids this graph already knows.** It
   names the proposed id and the self-description. The one place a human
   chooses an identity is the one place the existing identities are invisible.
   The refusal path lists them; the prompt path does not.
7. **Free text (`response_type=str`).** Every reuse is retyped exactly, and a
   keystroke mints a permanent new judge with no warning.
8. **Truncation.** One long prose paragraph with the question buried mid-string,
   which the terminal client cuts.
9. **The response never says a judge is new.** `description_versions: 1` and
   `new_description: true` imply it; nothing states it.
10. **`epimemer agents list` exists and is unreachable at the moment of need** —
    a separate terminal, refused on embedded backends, and the user has to think
    to run it while a prompt is waiting.

#### Three layers, because one field is doing three jobs

`agent_id` is simultaneously the join key, the human handle and — via the
description it is paired with — the claim about what this judge is. Those have
different rules, and collapsing them is what makes every defect above
unfixable in isolation.

| Layer | Mutable | Job |
| --- | --- | --- |
| `id` (UUID) | never | The join key. Frozen into every row, never displayed. |
| `name` | freely, by the user | The handle: picker, `review` column, frontend label. Resolved **at read time**. |
| `descriptions[]` | append-only, versioned | What the judge claimed to be **then**. Pinned per decision by digest. Unchanged. |

`JudgeRef` keeps its shape — `agent_id` plus `digest` — with the id now opaque.

**Read-time resolution is right for the name and wrong for the description, and
that is not a contradiction.** A description answers *what did this judge claim
to be when it decided this*, which must be pinned or the decision stops being
readable years later — the reason the digest exists. A name answers *which judge
is this*, and for that the user wants the name they know it by **now**: rename a
judge and old rows should follow, or the rename has achieved nothing. #77
rejected UUIDs partly by running these together.

**Names must be unique per graph, enforced when set.** Otherwise `by_agent` by
name is ambiguous after a rename and the picker shows two identical rows.

#### The picker, which is what makes the rest safe

`ctx.elicit` in fastmcp 3.1.1 takes more than free text. `response_type` as
`dict[str, dict[str, str]]` renders a **single-select titled choice** and
returns the selected key; `list[str]` does the untitled form. Only `title` is
read per option, so the title is the whole line — which suits a terminal, and
almost certainly renders better than the paragraph causing (8).

The flow:

- **Every bind puts the picker up** (subject to cadence, below). Choices are
  built from `list_agents()`, which exists on the protocol and has no consumer
  outside the CLI today. Each title carries name, decision count and last seen:
  *"Opus 5 Judge — 1 decision, last 2026-08-23"*.
- **Choosing an existing judge is the confirmation.** One keystroke, which is
  what makes asking every time affordable rather than punitive.
- **`New judge…` opens a second, free-text prompt.** The only path that mints an
  id, and the rare one, so it pays the extra step.
- **`Rename this judge…` lives here too.** Same user channel as approval, no new
  tool, and nothing an agent can reach — which is what §2.2 requires.
- **The message stays short.** The question first; context lives in the titles.
- **The response says `new_agent: true|false`.**

**This is what overturned #77's rejection of an opaque id.** Two of that entry's
objections — *approving a UUID is approving something you cannot identify* and
*the name-to-identity resolution problem only moves* — were both premised on a
free-text prompt. With a picker the user never sees an id, and a **human**
resolves name to identity on every bind rather than a machine guessing. The
picker is the precondition for the UUID, not an alternative to it.

#### Migration absorbs the aliasing feature

`Agent` carries the ids it was formerly known by. That one list is
simultaneously:

- the migration path (existing string ids become former ids of a new UUID),
- the repair for the `Opus 5 Judge` / `Opus 5` split, which the user resolves
  once by naming both as the same judge,
- and the resolver for every row written before the change.

**Nothing is rewritten.** Old rows keep the id they recorded and lookup resolves
through the list — the same shape as `rejudge` keeping the value it replaces and
a dissent recording a finding rather than performing an undo. So aliasing stops
being a standing feature and becomes a one-time consolidation: #77's remedy,
folded into the migration that makes it unnecessary in future.

#### Cadence — decided 2026-08-25 by the user: (b)

How often the picker appears. **Not** whether it appears — the condition
replacing today's `if agent_id not in approved`, which is defect (1).

- **(b) Once per session, per graph, per identity. Chosen.** The memo sits
  beside the judge in `ctx.set_state`, which already holds the binding, so it is
  session-scoped and costs no new machinery and no singleton. A second call
  naming the **same** judge in the **same** graph binds silently; a different
  id, a different graph or a different session asks. Closes (1) fully — no
  identity is ever bound that this session has not had confirmed — while making
  a repeat call idempotent rather than another prompt.
- **(a) Every `claim_agent` call.** No memo and no state, and a cleaner rule to
  state, which is the whole of its case. Rejected because the two differ only
  where an agent re-claims within a session, and that is not a rogue path: a
  `use_graph` unbinds the judge, so re-claiming is the documented recovery.
  Charging a prompt for it trains the user to dismiss prompts.
- **(c) Once per process.** Rejected: the `fallback_judge` lifespan slot is
  shared, so a second client would inherit the first's confirmation.
- **(d) Only unapproved ids.** Today, and defect (1).

**The memo is keyed on the identity, not on the session.** *Confirmed at all*
would let an agent claim judge A, be approved, then claim judge B silently —
which is defect (1) rebuilt inside the fix.

**Cadence does not fix the reconnect complaint.** Session state dies with the
connection, so (a) and (b) both re-ask after an MCP restart. That is inherent —
what makes it tolerable is the picker, not the frequency.

#### Costs, stated plainly

- **`EPIMEMER_APPROVED_AGENTS` and the CLI take text.** Seeding approvals by
  UUID is unusable for a human. Likely answer: the env var names judges, and a
  name matching nothing mints one on first connect. Needs deciding; not hard.
- **The migration itself**, across every graph holding agents. Bounded today:
  `memory` has two, the other graphs none.

Both are smaller than what they buy, given that renaming is otherwise permanent
and the naming decision is otherwise demanded before the user can know the
answer.

#### Order

**The picker first**, because every other part depends on it: it is what makes
an opaque id safe, what makes asking every time affordable, and what turns
(6), (7), (8) and (10) into one change. Then cadence, which is a condition.
Then the three-layer split and its migration, which is the schema work and the
only part that touches stored rows. Aliasing needs no separate stage — it
arrives as the migration's user-facing step.

---

### Issue 79 — the in-memory store cannot persist, so every local use needs a server — 🟡 OPEN, DEPRIORITISED (raised 2026-08-25, premise corrected 2026-08-26)

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
> designing around it* — the same failure as #75, where a guard was designed for
> a case it turned out not to catch, and #74, where a feature survived until
> somebody counted how often it fires.

#### Measured first, 2026-08-25, because the format question has a number

**One of the three questions dissolved on inspection: there is no BM25 index.**
`memory.py`'s own docstring says lexical search *"scores the corpus on every
call (`storage/bm25.py`) rather than maintaining an index"*. Nothing to save and
nothing to rebuild. The clause came over from SurrealDB's FTS backfill (#58),
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

### Issue 80 — a suppression has no retraction, so every wrong decline is permanent — 🟡 OPEN (raised 2026-08-27, from #64/#68 and `RELATION_LABELS.md` §4.2)

**Filed because the reasoning already exists in three places and has no number
of its own**, which is the state that loses it. `RELATION_LABELS.md` §4.2 states
it, #74's FC section states it, and `similarity_decisions.py` states the fact-
layer half in as many words — but #74's entry is scheduled to be pruned when its
stages finish, and the argument would go with it.

**The rule and its dual.** #64's lesson is *a sweep recomputed from current
state that records no declines is a futile cycle by construction*: it re-offers
what was already refused and cannot know it. The fix is a suppression index —
the `assessed` edge for fact pairs, `RelationVerdict` for label pairs in stage 3.
**The dual is what the fix creates: a suppression with no retraction makes every
wrong decline permanent by construction.** A pair judged `distinct` in error
never returns, on either layer, however much later evidence says it should.

**This is not #68, and the difference is the whole reason for a separate
number.** #68 was *nothing retracts a `one_claim` verdict* — the affirmative
half — and it was fixed 2026-08-23 with a deliberately **one-directional**
retraction: `distinct` withdraws a standing `one_claim`, and nothing re-asserts a
withdrawn one, because wrongly withholding a corroboration count is cheaper than
wrongly inventing agreement (#52's direction). #68's own fix left suppression
untouched on purpose. So the affirmative half is retractable and the suppressive
half is not, on both layers.

**And the fix may legitimately differ per layer**, which is the finding most at
risk of being lost. #68's asymmetry is entirely a property of **corroboration**.
Nothing corroborates on a relation label (#74): a wrong `synonymous` invents no
support and a wrong `distinct` costs no count, so **neither failure mode exists
there**, and a *symmetric* retraction is a live option for labels where it would
be wrong for facts. Porting #68's shape across unexamined would import a
constraint with no justification at this layer.

> **Checked 2026-08-27 against stage 4.** *"Nothing acts on `synonymous`"* is
> stage-dated — stage 4's deprecation would act on it. The asymmetry still does
> not reappear, because deprecation is reversible by design (`RELATION_LABELS.md`
> §5, and FC2's whole shape assumes it). **That makes §4.2's conclusion depend on
> stage 4 staying reversible**, which is now said in §4.2 rather than left to be
> rediscovered: ship an irreversible deprecation and this argument needs
> re-deriving from scratch.

**Cost of leaving it.** Zero today on the label side, because stage 3 is not
built and one label in the largest real graph means no pair can be nominated. On
the fact side it is live but quiet: a wrong `distinct` is invisible precisely
because suppression works. **When stage 3 ships, the system holds two instances
of an acknowledged, unfiled defect** — which is the deadline this entry exists to
beat.

**Not recommended for building yet.** What it needs first is a case: a
suppression somebody actually wants undone. Both layers can wait for one, and
the retraction's *shape* should be argued from the real instance rather than
guessed at symmetrically.

---

### Issue 81 — a relation merge strands the label record it merged away — 🟡 PARTLY FIXED 2026-08-27 (found the same day, from #74 stage 2)

**Found by trying to finish #74 stage 1's deferred test 9**, which claims *every
record a label can acquire is reachable without the CLI*. `RELATION_LABELS.md`
§2.3 enumerates three write paths that name a label — `link`, `describe_relation`
and stage 3's `relation_verdicts`. **There are four.**
`apply_reflection(relation_merges=…)` exists today, agents reach it from
`reflect`'s `similar_relations`, and it touched no records at all. The design
missed it because §7.3 has stage 4 *replacing* `relation_merges` — it was written
as if the path were already gone, and #74 has not decided that merging survives
at all.

Measured on the in-memory store before the fix:

```
before:               [('advised', 'ADVISED: employment.'), ('advises', 'ADVISES: retainer.')]
apply_reflection(relation_merges=[{"labels": ["advises"], "into": "advised"}])
after list_relations: [('advised', 2, 'ADVISED: employment.')]
after records:        [('advised', 'ADVISED: employment.'), ('advises', 'ADVISES: retainer.')]
```

Three consequences, all created by stages 1–2 rather than pre-existing — before
the record existed there was nothing to strand:

1. **The merged-away record survives its last edge.** `advises` holds a
   description for a word this graph no longer uses. That is *exactly* the state
   `describe_relation` refuses to create ("a label no edge carries"), so the
   system creates by merge what it refuses by hand — the tell that one of the
   two is wrong.
2. **`list_relations` and `query_relation_labels` disagree.** The first is
   derived from edges and drops it; the second, and `viz_list_relation_labels`
   with it, keeps it. The dashboard shows vocabulary the graph has abandoned.
3. **Merging into an uncoined label left the survivor with no record**, so the
   description being consolidated *toward* was absent while the one consolidated
   *away* sat in the store unreachable through any agent surface.

**Fixed 2026-08-27, in the two halves that could be fixed without deciding
stage 4.** The survivor gets a record if it had none, judge-less — merging is
not coining, so nobody is claiming to have introduced the surviving word. And
the loser's prose comes back in `relation_descriptions_orphaned`
(`{label, kind, description, merged_into}`) instead of vanishing. **Nothing
folds it into the survivor**: settling two definitions into one is an editorial
judgment the system is not entitled to make, and this design's whole shape is
that agents judge and the graph records — the same nominate-don't-decide split
`reflect` uses everywhere else. The agent settles it with `describe_relation`.

**What is left is consequence 1, and it cannot be fixed here.** Removing the
stranded record needs either a hard delete — which this system has exactly one
of, `reverse_merge_tx`, deliberately unreachable from any MCP tool — or a
`status` on the record, which is stage 4 and explicitly undecided
(`RELATION_LABELS.md` §5). **So this is evidence for stage 4 rather than a gap in
stage 2**: merging without a state model on the label record strands records, and
that is now a measured cost rather than a predicted one. Whatever settles §5
settles this.

**Carry-forward, and the reason this entry is worth its number: an enumeration of
write paths in a design is a claim that ages.** §2.3's list of three was correct
when written and wrong two stages later, because a path it assumed would be gone
was not. The guard that would have caught it is stage 1's test 9 — which is why
finishing that test matters beyond its own assertion.

---

## Older carry-overs (open, low priority)

From the original live-graph walkthrough (issues 1–5, otherwise resolved or kept
by design — see git history of this file, commit `22fc874` and follow-ups):

- **No retroactive repair of old graphs.** Fixes apply to new operations;
  pre-existing graphs keep stale state until rebuilt. Accepted. #46 left one
  concrete instance: every node written before 2026-08-19 carries a literal
  `0.5` confidence, so those rows read as *rated ordinary* when nobody rated
  them. Absence means something only for nodes written since.

Two live triggers, kept when their entries were deleted:

- **From #46 — does guidance actually produce a `confidence_basis`?**
  **Measured 2026-08-21: yes, 163 of 163 — 100%.** The basis is asked for by
  tool guidance rather than enforced at the boundary, and the accepted risk was
  that absence would then mean nothing: *no basis given* and *guidance not read*
  indistinguishable. The census over both real graphs
  (`corpus_measure.py --skip-survival`, `measurement: priors`):

  | population | `memory` | `petritype-server` | owes a basis |
  |---|---|---|---|
  | rated non-default (161×0.9, 2×0.7) | 163 | 0 | yes — **163 carry one** |
  | unrated (field absent) | 125 | 0 | no |
  | legacy literal `0.5` (pre-2026-08-19) | 200 | 136 | no |

  **So the fallback is not needed** — refusal at the tool boundary, the shape
  `judge_importance` uses, stays unbuilt. Two further readings, both of which
  the raw rate would hide:

  - **Zero post-#46 nodes sit at a rated `0.5`.** They are stored absent
    instead, which is the ladder's "omit the field" rule being followed
    exactly — and it is what makes absence informative rather than ambiguous,
    which was the whole point of `float | None`.
  - **The legacy population is now sized: 336 of 624 nodes.** They read as
    *rated ordinary* though nobody rated them. That is the retroactive-repair
    carry-over above, not a new defect, and it shrinks only as graphs are
    rebuilt.

  **The trap this measurement sets, recorded because it produced a confident
  wrong answer first:** `confidence_basis` lives in `node.metadata`, apart from
  `value.confidence` — deliberately, since the basis is prose about one judgment
  and `ValueSignal` is the numbers every ranker reads. A query for
  `value.confidence_basis`, where it reads as though it belongs, returns 0% and
  looks like a finding. **A field's home is part of its definition**; asking the
  store the wrong question is not a null result. Pinned in
  `tests/test_corpus_measure_smoke.py`.
- **From #46/#51 — there is still no path for source discredit.** When a
  document turns out fabricated, every prior derived from it overstates and
  nothing can sweep per-source, because support levels live on the node rather
  than on the `sourced_from` edge. Accepted and recorded rather than built; the
  provenance edge is where it would go. Also stated in `SUMMARY.md`.

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

**#54 was ordered first, was wrong to be, and is unblocked anyway.** The review
filed it as "independent of every open decision", which checked the mechanism
outward without checking the consequence running back: **T2 decided whether a
world-change goes through supersession at all.** It does — so
`supersede_node_tx` does see `HISTORICAL`, and the copy-not-move fix has
callers. Had T2 gone the other way it would have been careful work on a dead
path. The lesson is the ordering method, not the outcome: *an issue is only
independent of a decision if the decision cannot delete its code path.*

The urgency was also overstated in the other direction: `because` landed on
2026-08-12, so no `HISTORICAL` node has ever existed outside tests. The damage
is prospective, not a backlog.

**#55 went first**, being genuinely decision-free: a status map in the frontend
that `666904f` left behind, drawing corrected and historical nodes at full
opacity against its own comment's warning.

**#48 was a defect that did not hurt yet** — an O(N) scan per ingested node,
invisible at today's sizes. Fixed 2026-08-19, and the "measure rather than
patch" instinct earned its keep: the obvious fix, indexing the content, changed
nothing at all until the query named the index, and the write cost it was
feared for turned out to be under 5%.

**#46 and #51 are both done, in that order, and the order mattered.** Neither
failed; both existed because the docs described measurements the code does not
take, which is the same trap #44 was. #51's whole justification is that
`confidence` is not the place to put a corroboration count — which only reads as
a decision once `confidence` is something else, and #46 is what made it
something else. For #46 the deliverable was written guidance rather than code,
as predicted: the field and its consumer sweep are a handful of lines and the
ladder is the rest, because a prior no agent knows how to set is worth less than
the constant it replaces. For #51 the deliverable was a **measurement that said
no** — it is the most expensive annotation on the retrieval path, and its cost
rises with similarity-edge density, so a default-on version would have got
slower exactly as it got more useful.

**On building 46 and 51 "assuming 52 lands" — split which part waits.** Putting
per-source support on the provenance edge is right whether or not dedup ever
arrives: the edge is where provenance already lives, so the value cannot
outlive the source it describes, and when dedup *does* merge two facts the
levels ride along on the edges with no combination rule to invent. Build that
now. What genuinely depends on #52 is only the question of whether the node
keeps a scalar `confidence` at all — with one source per fact a read-time
derivation is a hop paid to combine one number, and with several it is the
honest answer. **Keep the nullable scalar until dedup lands, then revisit.**
Making the whole design wait on the hardest open issue would strand the part
that is ready and correct either way.

**#53 outranks all of it, and was found by pulling on exactly this thread.**
Asking what would make dedup safe produced a precondition the model does not
have, and the missing precondition turned out to matter far beyond dedup. Worth
recording as a method alongside the three above: **when an issue is blocked on a
precondition, check whether the precondition's absence is itself the larger
defect.** It was here, and #52 was the fourth thing it had quietly broken.

**#16 stays deferred**, with its trigger stated. #14's step 3 was dropped rather
than deferred — batching beat gathering, so the prong #16 blocked is one nobody
wants.

Work that does not exist yet, as opposed to work that is wrong, lives in
`dev-docs/PROPOSED_FEATURES.md`.

What to pick up, and what has to be true first:

| Order | Work | Trigger |
|---|---|---|
| ✅ | ~~55, 56~~ | **Done 2026-08-12** — the two frontend lookup tables that had drifted from what they encode |
| ✅ | ~~54 (historical provenance and validity)~~ | **Done 2026-08-12.** `migration_disposition(edge_type, status)` is the policy; a world-change keeps provenance and judgments on the historical node and copies only the frame and tags |
| ✅ | ~~57 (supersession events named no counterpart)~~ | **Done 2026-08-17** — counterpart ids on the live events and on `query_changes`, carried by the append-only lifecycle episodes (`EVENT_LOG.md` §6), which is what made the log panel readable |
| ✅ | ~~46 (`confidence` becomes a supplied prior)~~ | **Done 2026-08-19.** Decided 2026-08-12, both review amendments signed off the day it was built: `float \| None` with unrated stored as absent, an optional `confidence_basis` asked for by guidance, and a consumer sweep saying what each reader does about absence. The deliverable was the tool guidance, as the entry predicted |
| ✅ | ~~**53 (validity intervals)**~~ | **Built 2026-08-19.** Everything else on this list is a defect inside a sound model; this one says the model cannot express something true. Done (all 2026-08-19): the status split; T2's lineage edge, which closed a week in which a world-change wrote an edge contradicting its own node's status; the interval type with its four-value comparison; per-source intervals stored on `sourced_from`, supplied at ingest; recurrence — a retired claim can be nominated, judged and reactivated, which also closed #48; and T3's retrieval surface, which is where validity is finally read — history returned by default with a claim's earlier versions folded into it, per-source periods on results, `valid_as_of` answering in groups, and the `as_of` → `graph_as_of` rename, this design's one migration cost, now paid; §11's soundness check, which flags an inference whose premises no source puts in the same period; and §9's boundary proposals, which close a period where the succession the agent judged says the next one opens. **Two decided details moved on contact with the rest of the design**, both recorded in the entry: T3's third valid-time bucket is unreachable under T1 §6's open-world rule, and §9's own worked example yields no proposal because publication dates cannot end a period. **The entry is kept** — `REVIEW_EPISTEMIC.md` §13.8 and `docs/VALIDITY.md` both name it as the full statement of the design |
| ✅ | ~~51 (corroboration derived at read time)~~ | **Done 2026-08-20.** Both review exclusions applied, and the extra hop measured before it went anywhere: it is the most expensive annotation on the retrieval path and its cost rises with similarity-edge density, so it ships as `search(include_corroboration=True)` rather than by default. The row asked for the measurement and the measurement said no, which is the outcome this column exists to produce. Two status rules were decided during construction rather than inherited (`corrected` does not corroborate, `historical` does); `archived` left unpinned. Still carries the known 53-shaped inaccuracy and 46's accepted gap — per-source levels on the provenance edge, and with them any path for source discredit |
| ✅ | ~~48 (`get_node_by_content` scans per ingest)~~ | **Done 2026-08-19**, in the same visit as #53 step 4 as this row predicted. The measurement decided it: an index on `content` changes nothing until the query names it, and then the lookup goes 4.0 ms → 0.53 ms at 3,000 nodes for under 5% on writes. Guarded by a plan assertion, since behaviour cannot see the defect |
| ✅ | ~~**59 + 60, as one measurement sitting**~~ | **Measured 2026-08-20, together, as this row asked** — one read of two real graphs answered both. Both shrank. #59 is **segments only**: 624 real nodes top out at 81 word-pieces against a 256 window, while 11.1% of segments cross it and the worst loses 48% of its text, so nodes get option 4 (accept, recorded) and segments get option 3 (say the record was cut). #60 loses its headline: the real fact-pair survival rate is **0.0105%**, not 49%, projecting ~3 MB at 10,000 facts rather than ~14 GB — because the 49% was measured on longer templated text (it sits between the 20-word and paragraph points) and applied to fact-length pairs. Both entries keep their fixes, at much lower priority; `scripts/corpus_measure.py` is the instrument and `BENCHMARKS.md` holds the tables |
| ✅ | ~~59's segment flag, 60's nomination cap~~ | **Done 2026-08-21**, and the two halves ended differently. **#60** shipped as option 2: each of the four quadratic lists capped to its highest-scoring 200 with `truncated: [...]` in the response, bounding the *response* rather than the peak allocation — the honest scope once the measurement moved the argument off memory. **#59 closed with no code**, because the segment flag it prescribed had nothing to attach to: segments are never embedded, on four independent checks, so the 256 word-piece window never reaches them and BM25 (which indexes the whole field) is how they are searched. The entry had joined two true claims — segment text crosses the window, segments are a search corpus — into a false one. **A measured quantity is not yet a measured consequence**; the instrument read the text without checking that anything hands it to the tokenizer. The precondition is now recorded on `EmbeddingRecord.item_id`, where whoever embeds segments will read it |
| ✅ | ~~52 (fact deduplication)~~ | **Built 2026-08-21.** The decision the row asked for was made rather than deferred: **the event/state judgment is recorded at ingest**, on `Fact.claim_kind`, because it wants the document and a merge sees two stripped sentences. `merge_facts` gives `redundant` the action it never had, keeping one `sourced_from` edge per contributing document — which needed no new code, since edge migration already preserves both sets of intervals when two edges to one document collapse. The cost is paid in full and up front: **the corpus written before today is unjudged and so unmergeable**, and no later pass can repair it, which is the under-merge direction the entry chose. The proposed new `reflect` nominee list was not built — reflect's `contradictions` already nominates the same population, and adding a fifth quadratic list the day after #60 capped four would be perverse. **#51's second migration stays open on purpose**: it is not broken by this, and revisiting it wants a corpus that has merges in it |
| ✅ | ~~61 (a fact merge does not flag its dependents)~~ | **Done 2026-08-21**, decision and build in one sitting. The decision the row asked for went to a **sibling edge type**, `evidence_merged`, rather than a qualified `evidence_superseded` — and what settled it was a consequence rather than a principle: archival nominates on `evidence_stale`, so one shared label would have every merge propose discarding its own dependents. The build is the shape the row predicted (`merge_nodes` calls a planner beside the two supersession paths, both now over one `dependent_inference_ids`), plus the seam the row did not: `merge_nodes_tx` grows `evidence_edges` across the protocol, both backends and the instrumented wrapper. **One thing learned that is worth more than the fix**: unlike `evidence_stale`, this label has no live-check half and never can, because the `derived_from` edge is migrated onto the survivor by the same transaction — *a derived label can only be derived while its evidence is still in the graph* |
| ✅ | ~~63 (the nomination bar was two numbers)~~ | **Done 2026-08-21.** Found by review, fixed the same day: the sweeps nominated at 0.80 while the merge gate refused below 0.83, so reflect offered pairs `merge_facts` then rejected — telling the agent the graph would never have paired them, right after it had. One constant at 0.80 now, read by both `check_conflicts` declarations, `merge_facts` and `detect_contradictions`, with the invariant **merge floor ≤ every nomination bar** pinned by signature across the MCP boundary. The carry-forward is the shape of the miss: **a constant with a stated invariant needs a test that reads every declaration of it** — #52's "both readers take it from there" was true of the two it named, and there were four |
| ✅ | ~~62 (corroboration does not read validity)~~ | **Done 2026-08-21.** The one decision the row asked for turned out to be **mis-framed**: dropping and marking are not alternatives, because nothing is dropped from the *graph* — both claims stay, true of their own periods, and only a read-time integer narrows. So the count became honest and the uncounted look-alike comes back named in `adjacent_periods`, which is the half that carries new information: where a search returns one of the pair, that block is the only place the other appears at all. Two things the row had wrong. **Placement** it did not mention and which decides correctness: the comparison must run before the supporter hop, or the look-alike walks its own supporters — and their publishers — in behind it. **Cost** it stated as "no round trips": nearly, but the periods were being read at stage 4 and are needed at stage 1.5, so the provenance read splits in two, thirteen calls or twelve, still constant in result-set size. #52's inherited corroboration migration stays open and separate, as the row said |
| ✅ | ~~65 (a correction re-points judgment edges)~~ | **Found and built 2026-08-22**, in that order and on the same day, because it is a defect in shipped code that #64's step 1 would have made reachable — fixing it after would have meant shipping it knowingly. `JUDGMENT_EDGE_TYPES` in `core/types.py`, consulted by `migration_disposition` before the status branch, so `similarity`, `contradiction` and `variant_of` are anchored on **every** retirement. Four lines, because both backends derive their answers from that one function and hold no policy of their own. **Two things the design had wrong surfaced only on building it**: `REVIEW_MODE.md` §10.2 and §10.2.1 gave opposite answers for `similarity` on a merge, and the issue's stated reasoning (*a correction changes the wording, not the claim*) contradicts `migration_disposition`'s own account of a correction. The verdict survived both; the reason did not, and the real one — the **substantive** correction, "500,000" → "5,000,000", leaving a counterpart judged against a number that is gone — is what shows a merge belongs in the same rule. Carry-forward: **when a fix is derived from an argument, check the argument against the code before trusting the fix** — right for the wrong reason generalises wrongly |
| ✅ | ~~64's steps 0a–0c (merge reversal)~~ | **Built 2026-08-22**, in one sitting, because the three depend on each other in a way that made any subset useless: 0a captures the pre-merge edge partition (destroyed by migration, so **capture or lose**), 0b refuses an oscillating merge, and both are **dormant** until 0c writes the `restored_at` and reads the payload. `reverse_merge` restores the sources and **deletes** the survivor — the only hard delete in the system, and it lives *inside* `reverse_merge_tx` rather than behind a `delete_node` method, because the safest way never to expose a hard delete is not to have one. New tools: `reverse_merge`, `configure_merge`. Deferred by design: the reversal `DecisionRecord` (step 5) and the `judge` argument (steps 2–4). Building it found **#67**, a backend divergence that made a second merge/reverse cycle look like the first — which would have blinded `merge_cycle_limit` to the exact oscillation it exists to catch |
| ✅ | ~~64 (`similarity` has no writer)~~ | **Found 2026-08-21**, while taking #52's outstanding corroboration migration — which is declined on the strength of it, with no code. The migration assumed a populated neighbourhood that merging would shrink toward identity; the neighbourhood is **empty on every real graph** (0 of 4,386 edges on `memory`, 0 of 1,028 on `petritype-server`), so collapsing the walk would change no count and would delete the only consumer of a judgment nothing yet records. Both of the migration's stated payoffs were already gone: the Saint Petersburg caveat was collected by #62 through `adjacent_periods`, and "stops over-reporting through a wrong edge" describes a trade `corroboration.py` argued for rather than a defect. **Merging structurally cannot replace the walk** — `merge_refusal` refuses every event, so identity can never count two publishers on one occurrence, the paradigm case the whole annotation exists for. The decision this row wants is *which surface records the judgment*: a tenth `apply_reflection` argument (recommended), a write on `merge_facts`' refusal, or a `record_similarity` tool. Carry-forward: **an edge type with readers and no writer is not a feature with low adoption, it is a feature that has never run** — three documents described its cost curve and one named the wrong writer. **Built 2026-08-22** as `REVIEW_MODE.md` step 1, taking the recommended surface: `EdgeType.ASSESSED`, `apply_reflection(similarities=[…])`, and a nomination sweep that now reads `SIMILARITY ∪ CONTRADICTION ∪ VARIANT_OF ∪ ASSESSED`. The split — both verdicts suppress, only `one_claim` corroborates — is the design, and building it corrected one rule: *retired ids are skipped* was too broad by exactly half the problem, since the recurrence sweep nominates active/historical pairs, so the gate is `NOMINATED_STATUSES` on both sides. Left behind: **#68**, nothing retracts a `one_claim` verdict |
| ✅ | ~~67 (SurrealDB's retirement paths trusted the caller's lifecycle)~~ | **Fixed 2026-08-22**, the day after it was found and before step 3 threads `judge` through the same transaction builders — #65's argument reused: a defect in shipped code that the next step would build on top of. `_stored_lifecycles` reads the row instead of the argument, at five call sites, with a parity test per path. The entry named three and there were four: **both** branches of `set_node_status_tx` were wrong, and the return branch fails by writing an empty history rather than a long one. Carry-forward stands as written — *a transaction that takes a domain object has to decide whether the argument is a request or a snapshot, and say which* — with one addition: **the two failure directions of one cause do not look alike**, so a symptom-shaped search finds half of them |
| ✅ | ~~`REVIEW_MODE.md` step 2 (the agent registry)~~ | **Built 2026-08-22.** `claim_agent`, the `agent` table on both backends, `EPIMEMER_APPROVED_AGENTS`, the `epimemer` CLI, and `use_graph` re-validation. A registry with nothing pointing at it yet, which is the shape the build order intends — steps 3–4 are what make a decision carry a judge. Two things the design had not settled, both found by building it: **the id gate and the description gate are different strengths** (an unapproved id is refused; a new description is recorded unconfirmed, because *self-described, unconfirmed* is the object §2.4 exists to keep distinct), and **a tool that waits on a person cannot share the tool timeout** — 30s would turn *the user was still reading* into *the client cannot elicit*, which refuses the claim. Config seeding was also widened from connect-time to every graph the server lands on: approval is per graph, so the narrow rule left the embedded backend unapprovable one `use_graph` later, which is the failure it was written to prevent |
| ✅ | ~~`REVIEW_MODE.md` step 3 (the judge on reflect-side writes)~~ | **Built 2026-08-23.** `JudgeRef` through ten tools, five storage transactions on both backends, and four carriers — lifecycle episode, edge, value signal, node. Two writers the design's list had missed are in: **`update`**, which is `supersede_by`'s twin and would otherwise leave *who retired this* answerable or not depending on which tool the agent reached for, and **`link`**. Two rules were decided by building rather than inherited, and both are about not overwriting a name: re-recording an existing pair leaves its judge alone, because a second agent calling the same tool has *confirmed* rather than decided; and importance keeps the latest judge on the value signal while each entry in the reinforcement trail names its own, since three agents' judgments compose into one number. Two gaps named rather than left to be found: boundary acceptance and relation relabelling edit existing records in place and want a journal row, not an inline stamp |
| ✅ | ~~`REVIEW_MODE.md` step 4 (ingest, and the require-a-judge setting)~~ | **Built 2026-08-23.** Ingest attributed on both steps, `require_judge` per graph on both backends behind `EPIMEMER_REQUIRE_JUDGE` and `epimemer agents require`, and one gate at the boundary over twelve write tools. **The escape hatch changed shape**: the design wanted an explicit `agent_id` on every write, and it is a lifespan-held fallback binding used only where session state does not exist — ten schemas narrower, claimed once rather than repeated per call, and no weaker, since approving the id is the gate and the binding was only ergonomics. The document and its segments carry no judge (*who pasted this* is not *who judged what it says*), and reusing an entity or tag topic does not restamp it, which is step 3's re-recorded-edge rule again |
| ✅ | ~~`REVIEW_MODE.md` step 5 (the decision journal)~~ | **Built 2026-08-23.** The `decision` table on both backends with six indexes, five reads, and a row at fifteen writers; `WARNINGS_AND_SETTINGS.md` §9's node notes folded in, so `node.notes` is a subject query and there is one review machine rather than two. **`kind` carries `because`** — a correction and a world-change are opposite claims (#53) and a reviewer asking for one does not want the other. Granularity is **per act, not per call**: ingest, an archival sweep and a reactivation are one row each; reflect's other lists get a row apiece, because those are independent verdicts batched into one request. Re-recording a pair verdict now writes a **confirmation** pointing at the oldest record for that pair, which is what §3.4's rule was waiting for. Two things left open on purpose: `certainty` has no tool that supplies one (step 7's `apply_review` and `rejudge` are the first, where the ladder can be stated once instead of on twelve schemas), and relation merges still have no row — **#69**, because their subjects are labels and `subject_ids` holds node ids. Building it found **#70**, a timestamp comparison that makes `graph_as_of` answer differently on the two backends |
| ✅ | ~~the default graph was a real graph~~ | **Fixed 2026-08-23**, the day it bit. A server started without `EPIMEMER_GRAPH` fell back to `EPIMEMER_SURREALDB_DATABASE`, whose default was the literal string **`memory`** — which is also the name of this repo's dev-history graph. An agent working on unrelated material reconnected mid-session, landed there without knowing, and ingested 61 nodes of one project's procurement documents into another project's graph; every response said success. Three parts to the fix: the default is now `default`, a name nobody would give a real graph; `segment` and `store_decomposition` report `active_graph`, since ingest is otherwise indistinguishable between the right graph and the wrong one; and `INTEGRATION.md` states the resolution rule and that **the active graph is process state**, so `use_graph` does not survive a reconnect. The 61 nodes were archived out (reversible; nothing is deleted), and that archival is the journal's first production row. Carry-forward: **a default that collides with a real name fails silently, and a default that lands somewhere empty fails loudly** — the wrong one had been there since the initial commit, unexercised because every configured server named its graph |
| ✅ | ~~review pass on the journal + the graph fix~~ | **2026-08-23**, an independent agent over both commits. Three findings, all taken. `journal()` **never raises** — it landed after the decision and outside its transaction, which is the safe direction, but raising still failed the tool call *after* the graph write, and every retry was worse than the missing row (a retried merge refuses, a retried contradiction writes a row reading as an original, a retried ingest stores the document twice). `DecisionKind` **carries no member without a writer**: `relation_merge` and `proceeded_despite_advisory` both shipped unwritten and both came out, which is `WARNINGS_AND_SETTINGS.md` §8.1's rule for `AdvisoryAction` binding harder here because review *selects* on the kind. And the reporting fix was upgraded to a **refusal**: `expected_graph` on `segment`, `store_decomposition` and `restore`, since answering *"every response said success"* with a better success response leaves the failure attention-dependent. **One correction to the review's own reasoning**, which moved where the guard goes: the incident's two ingest steps were *internally consistent* in the wrong graph, so the existing `Segment not found` guard never fires and a check of step two against step one would not have caught it — the comparison has to be the agent's intent against the server's state, at the entry point. Left open: **71** (should naming the graph be mandatory) and **72** (a misdirected write journals in the wrong graph) |
| ✅ | ~~70 (timestamps compared as strings)~~ | **Fixed 2026-08-23**, same day, and the fix is one function: `instant()` wraps both sides in `type::datetime`, so the comparison is about instants rather than spelling. No migration — correct for rows already written. **The measurement overturned the entry's own cost argument**: it claimed the fix cost an index, and there is no index on `created_at`, `superseded_at` or the lifecycle timestamps — both forms already plan as `Iterate Table`, so the conversion costs ~2.3 µs/row and nothing else. Where a timestamp *is* indexed it inverts, hard: the journal's `decided_at` range went 6.2 ms → 281 ms at 50,000 rows, 45×, so that one keeps a plain comparison and pays on the write side. Reader converts without an index, writer pads with one; both halves documented in `DEVELOPER_GUIDE.md` and the rule is in `AGENTS.md`. Two carry-forwards: **a correctness defect does not wait for a performance visit** (the entry had gated it on one), and **`datetime.now()` never lands on a whole second**, so a parity suite that builds its own timestamps guarantees parity over the safe values it happens to pick |
| ✅ | ~~72 (a misdirected write journals in the graph it went to)~~ | **Decided 2026-08-23**, before step 6 as the entry asked, and with **no code**: the journal stays per graph, `review()` takes no `graphs=`, and every response names the graph it answered from. **The ids decide where a row lives** — `subject_ids` resolves only in the graph holding those nodes, so a central journal would carry ids that dereference nowhere. The forensic complaint was overtaken: the misplaced material and the row recording it sit **together**, and `expected_graph` closes the hole that made *which graph* the unknown. **The fan-out turned out to be the unsafe option** — `review(graphs=[…])` has to borrow the active database and give it back, while `list_graphs` → `use_graph` → `review()` switches for real; *a convenience less safe than the sequence it replaces is not a convenience*. Left behind: **73**, the locator that would say where else to look, blocked on **16** — which settling this reopened |
| ✅ | ~~16 (the active graph moves under a call in flight)~~ | **Fixed 2026-08-23**, the day #72 reopened it, a month after it was deferred as latent. One guard per backend with two sides — a tool call takes `using()`, a `switch_database` or a `viz_list_*` borrow takes `moving()` — at the **logical-operation** boundary, since a move only has to land between two of the several storage calls one tool makes. **The title was the finding**: filed as a SurrealDB connection problem with a second-connection fix, it is shared mutable state read per call, and `InMemoryStorage` has the same defect through `use_graph` with no connection in sight — the proposed fix would have closed one backend's half. Reproduced against a **served** SurrealDB: with the guard off, a write issued during a snapshot borrow lands in the graph being snapshotted, silently, which is the wrong-graph incident with no agent involved. Two carry-forwards about the month: **a deferral's trigger has to be checkable rather than an event you expect to be told about** (the premise went false without the event), and **a concurrency test whose subject cannot occur reports green for the wrong reason** — in-memory storage and a hash embedder never suspend, so the first end-to-end test passed with the guard removed. Unblocks **73** |
| ✅ | ~~68 (nothing retracts a `one_claim` verdict)~~ | **Fixed 2026-08-23.** `distinct` over a standing `one_claim` now **withdraws** it, writing a `retracted_similarity` edge that `DISQUALIFYING_EDGE_TYPES` reads — so the corroboration count comes back down. **The entry weighed two shapes and the answer was a third already in the codebase**: `contradiction` has disqualified a standing `similarity` since before this design, on a comment describing this exact situation, so the retraction is one more member of a list rather than new machinery. The refusal moved rather than vanished — nothing re-asserts a withdrawn verdict, because withdrawing wrongly *withholds* a count while re-asserting wrongly *invents* agreement, and #52 already chose that direction. Suppression untouched; `DecisionKind.RETRACTION` in the journal, its own kind because `REVERSAL` deletes a node and this destroys nothing. Adding the frontend row found **`variant_of` drawing as unknown-kind grey** since it was introduced — #55 live, fixed, with a guard scoped to pair judgments |
| ✅ | ~~`REVIEW_MODE.md` step 6 (`review`)~~ | **Built 2026-08-23.** The journal read back shakiest-first, capped, read-only, one graph wide and saying which (#72). **Tier-1 ordering came with it** rather than waiting for step 7: `certainty` is already a field, so a sort that ignored it would have gone silently wrong the moment `apply_review` wrote one — and the rule it encodes, *an unrated decision never outranks a flagged one*, is the half worth pinning early. Only the parameters step 6 owns shipped; the rest would each have been an argument that did nothing. Three things the design did not say: **`confidence` lives on `ValueSignal`, not the node** (so reading it off one raises on a `Topic`), **`meta.retrieved` is not the use signal** (only `search` stamps `retrieved_at`, so declining to declare would only have greyed the viewer), and **`merge_facts` journals `[survivor, *sources]`** — so *three or more sources* read off the subject count calls every two-source merge wide. The first two were caught by the retrieval-declaration parity suite, not by anything written for this step. **Honest scope, measured**: the row says it works on the existing corpus, which is true of the signals (they read nodes) and false of the journal — `memory` holds **one** row, `field-notes` and `petritype-server` **none**, so every decision made before 2026-08-23 is invisible to review permanently, the same island #52 left in the other direction |
| ✅ | ~~`REVIEW_MODE.md` step 7 (the review modes and their writers)~~ | **Built 2026-08-23**, and the design's build order is now complete. `review` gains `by_agent` / `since` / `unreviewed` and `certainty_ceiling`; `apply_review` and `rejudge` are the writers, and the first two tools that supply a `certainty`. **The second list changed what it does, and that renamed it**: `reversals` became `dissents`, because it reverses nothing — every undo already has a tool with its own refusals and its own row that legitimately sets `supersedes`, and a dispatcher over four of them is #72's fan-out. A dissent sets `reviews` and never `supersedes`, so the journal never claims to have overturned a decision whose effect still stands; its real use is where the undo was **refused**, which the design had not considered. **`advisory` is refused by name rather than shipped** — it selects on a `DecisionKind` nothing writes, which would return an empty list reading as *nothing is contested*. **Not one transaction**, against §10.7: it performs nothing, so each entry is an independent judgment batched with unrelated ones, refused per item like `apply_reflection`. Three things found on the way: a **retry must not read as a second opinion** (an identical judgment by the same judge is refused; two blank judges cannot be told apart, which is one more thing `require_judge` buys), **`rejudge` has to keep the value it replaces** or it would be the one call that destroys a judgment rather than superseding it, and **`DecisionRecord.certainty` was unbounded** — harmless while nothing supplied one, and the ordering sorts on it. The `DecisionKind` drift guard **caught the new kinds and was itself wrong**: it scanned `mcp/tools.py`, true only because every writer had happened to live there. Carry-forward: **a guard whose reach is an accident of where the code sat is one that fails open** |
| ✅ | ~~71 (should a server be able to require that a write names its graph?)~~ | **Decided and half-built 2026-08-23.** The answer is stricter than the entry proposed: **mandatory, unconditional, no setting**, and covering **reads** as well as writes. Two shapes rejected by the user, both for the same underlying reason — *a guard must not be configured by the state it is guarding against*. The count-based gate reads a live `list_databases()`, so a second graph switches the requirement on and deleting it switches it off; a per-graph setting gets read from whichever graph you are wrongly in, so it disables itself precisely when it matters. **Reads were the omission**: a wrong-graph `search` returns a plausible answer the agent reasons from and leaves no artifact, where a misfiled write at least sits beside its own journal row. `expected_graph` is on all 37 content tools with one gate at `_run_with_timeout`, inside the turn, and a missing one refuses. Turning it on failed **82 tests across nine files** — every call that had been going through the boundary without saying which graph it meant, which is the population the gate is for; four of them switch graphs first, so two helpers now thread the graph rather than defaulting it. **It surfaced a defect older than itself** — the refusal's recovery message had been swallowed by a `KeyError` in `_log` since `expected_graph` shipped, because the tool's success summariser ran over a refusal dict, and every test called `tools.*` one layer *below* the boundary where no summariser runs |
| ✅ | ~~73 (a reviewer is not told which other graphs hold this agent's decisions)~~ | **Built 2026-08-23**, as `review()`'s `elsewhere` — counts per graph, zeros included, no rows and no new tool. **The count was the easy half and the turn was the design problem**: borrowing the connection means taking the guard's mover turn, and `moving()` inside `using()` raises by design, so `review` had to join `MOVES_THE_GRAPH` and a **read** is now a mover. It excludes other calls for its duration and reads a single instant in exchange, which is what a journal read wanted anyway. **#16's carry-forward repeated itself**: the in-memory sweep borrows nothing and passes whether or not the declaration exists, so the end-to-end fixture runs both backends and removing the declaration was checked to fail it. One rule banked, and it set the scope: **a locator may overcount and must never undercount** — only the filters `query_decisions` already implements are mirrored, `certainty_ceiling` and `unreviewed` are not, and `counted_with` says so, because every mirrored filter is somewhere two implementations can disagree. Both backends now build the journal filter once, shared by reader and locator, which is what makes the `since` boundary row count and read the same (#70's trap). And **naming a graph must not create one** — `USE` on an unknown database is not an error, so a blind count would have manufactured a namespace of empty graphs |
| ✅ | ~~76, two of three (the base metacontext has no row; nothing validates `metacontext_id`)~~ | **Built 2026-08-25.** `store_decomposition` creates The Real before it writes, so the first ingest into a graph gives the default frame a record — at ingest rather than at graph creation, because an empty graph asserts nothing about any world and a graph created earlier would otherwise never get one. And `require_metacontext` refuses an id that resolves nowhere here, on `store_decomposition` **and `search`**. **The read was the half that mattered**: a write with a dangling id is at least visible, while a search on one silently narrows to base reality and answers as though that were the frame. The refusal **lists the frames that do exist**, because no MCP tool enumerates metacontexts — the refusal is the only listing there is. Three judgments: `the-real` passes with no row (reserved, and what an untagged node resolves to, so refusing it would refuse the one id that cannot be wrong); `cross_frame=True` does **not** excuse a bad id, since the flag makes it inert for filtering and that is exactly why a wrong one would go unnoticed; and the check runs before the document is built, so a bad id leaves no partial decomposition behind. **The precedent was one function above the defect** — `_extraction_timeline` has refused an unknown timeline since it was written, for the same reason. Cleared a residue on the way: a dangling first line of a comment left by #71's move of the wrong-graph gate to the MCP boundary |
| ✅ | ~~78 (judge identity conflates the key, the name and the claim)~~ | **Fixed 2026-08-26 in two stages.** Stage 1 (2026-08-25) replaced the free-text prompt with a **picker** over the judges this graph already knows and moved the gate from *minting* an id to **assuming** one; stage 2 split the field into three — an opaque key, a freely renamable name resolved at read time, and the existing per-decision pinned description. **The picker was the precondition, not an alternative**: it is what makes an opaque key safe, because a human resolves name to identity on every bind rather than a machine guessing, and it is why #77's rejection of an opaque id did not survive. **The migration the design planned turned out to be unnecessary** — an opaque key is opaque whatever it looks like, so a legacy string id simply *is* the key and reads as its own name; only new judges get a UUID and nothing is rewritten. What was left was the one migration that needed a person: two records that should be one, which now arrives through **renaming** rather than as its own concept — renaming to a taken name asks *are these the same judge*, so the repair appears exactly where the duplication is visible. Consolidating deletes nothing: the absorbed record is kept, `live_agents` derives that it is no longer a judge, and both description histories merge, because a decision records `(key, digest)` and dropping the absorbed history would leave its own old rows unreadable. `query_decisions` takes `agent_ids` now — after a consolidation a judge **is** a set of keys — and `judge_aliases` sits beside the protocol rather than at the MCP boundary because `apply_review`'s duplicate check needs it too. Each stage turned up a defect **in itself**: stage 1's *ask on every bind* broke elicitation-less clients, because *declined* and *nobody to ask* were one value; stage 2's picker minted a UUID beside a bare approved id the user had seeded, orphaning the only approval they had given |
| ✅ | ~~66 (two ingest-time judgments have no way to be revised)~~ | **Built 2026-08-27** as `reframe` and `correct_interval`, five days after it was filed. The entry's conclusion — keep them out of `rejudge` — was right; its reason was not the strongest available. **The ground is addressing**: `rejudge` names a node and promises no edge moves, while a frame revision moves an edge and changes what retrieval does, and an interval belongs to a (node, source) pair — so folding it in grows a `source_id` read for one field out of five, which is this file's own *a parameter that needs "only applies when"* tell. **Review caught a trap before it was built**: the proposed flat refusal on withdrawing a node's last frame would have left the tool unable to fix the paradigm case it exists for, since a real fact mis-filed under a novel's frame belongs in base reality. Withdrawal-to-untagged is a **promotion** — base-reality knowledge is inherited by every frame — so it takes an acknowledgment (`to_base_reality=True`) rather than a guard, refused where it does not apply. **`assign` makes the A→B move atomic**, so the repair never passes through untagged and never strands a node asserted in every frame. The withdrawal **deletes** the edge on #68's carry-forward — the honouring read does not exist, so a marker would fail open where deleting fails closed — and the withdrawn frame survives in the node's trail and the journal row, which is what bounds which past search and corroboration answers were wrong. Carry-forward: **a refusal that blocks the motivating example is a design error, not a safety feature** |
| **next, with 74** | 76 | **Decided 2026-08-27 after two review rounds, build pending — seven steps that ship together.** The frame becomes **required** on `store_decomposition`; the declared per-graph default is **dropped, not deferred**, because *deferred until mixed content exists* invites building it on the day it is most dangerous. Two arguments killed the default: it makes fiction-or-fact depend on **which graph the write landed in**, which is the ambient state **71** exists because it cannot be trusted; and it is **retroactive by construction**, since `frames_for` resolves absence at read time, so flipping the override would reclassify every untagged node at once with no per-node record — a bulk epistemic move dressed as a config edit, and the `AGENTS.md` counter-case exactly. **The habituation objection is dead, and not for the reason first offered**: the implementer credited `reframe` shipping the same morning, which was convenient and not load-bearing; the real answer is that absence is already promoted to The Real before any consumer sees it, so there is no signal for a reflexive `the-real` to degrade — and `expected_graph` is the precedent, required everywhere and answered reflexively. **Review found the claim that made the case for it false as stated**: *untagged stops being producible* is not true, because `apply_reflection` mints untagged nodes at parent synthesis and at splits, so reflect converts framed knowledge into unframed assertions; topic merge is a softer second instance, with no frame gate where facts have one. That fix must ship **in the same change**, or the legacy date boundary is false the day it is written. **No backfill** of the 684 legacy nodes: writing `the-real` onto them manufactures 684 judge-less deliberate-looking assertions, which is the ambiguity the rule exists to end. Carry-forward: **the requirement buys detectability and recoverability, never prevention** — a templated `the-real` on a fiction ingest is exactly as wrong as silence was |
| **next** | 74 | Now also carries a **live defect**: relation-label nominations have no suppression, so a rejected pair is re-offered on every reflect for ever — the treadmill **64** closed for fact pairs with the `assessed` edge, never built for labels, and unbuildable today because the subject is a label pair rather than a node pair. **74** (a relation label is a string with no record) **supersedes 69** — raised 2026-08-24 after measuring the feature 69 was about: relation merges fire approximately never, labels do not affect retrieval, and the consolidation is a port of tag consolidation whose premise did not survive. Give a label a record and a description, and deprecation replaces merging without rewriting an edge. **69** stays open but blocked on it. **FC2–FC4 are settled 2026-08-24**: none is reachable — they need deprecation, steering and renaming respectively, and none exists — so each is recorded as a precondition on the feature that would create it rather than as work outstanding. **Stage 1 built 2026-08-26** — the record, three protocol methods on both backends, `link` create-or-fetch and a CLI backfill; identity now exists, which is what every later stage needed first. **Stage 2 built 2026-08-27** — `description` on `list_relations`, on `link`'s response when it reuses a label, a `describe_relation` tool journalling its own `RELATION_DESCRIPTION` kind, and `viz_list_relation_labels`. This is the half that pays, because it moves the intervention from repair to prevention: an agent picking from a described vocabulary never coins the fourth synonym. Two calls the design had not made: the kind is resolved by `get_relation_kind`, which reads **every** edge while `list_relations` reads only edges on active nodes — so a label whose users have all retired stays describable, which is the right way round; and a blank description leaves prose alone, so the response reports what was **stored** rather than echoing an argument that cleared nothing. **FC1 is still live**: it is stage 3 — and stage 3 now has an ordering constraint on it. **Whether merging survives at all was put to the user 2026-08-27 and is with a reviewer**: the recommendation is to remove `relation_merges` (a lossy irreversible bulk rewrite in an otherwise append-only system) and defer stage 4; the user's instinct is that removal is right and deprecation is still worth having. Undecided. **Either way stage 3 must land before or with any removal**, because `reflect`'s `similar_relations` nominations exist to feed `relation_merges` and would otherwise feed nothing. `RELATION_LABELS.md` §5 has the arguments. **76** is **decided 2026-08-27 and pending a build** — the row and the existence check shipped 2026-08-25; the declared default is **dropped outright** and the frame becomes required at ingest instead. See its own row. **75** (relation-label nominations ignore metacontext) was filed 2026-08-24 and came out **smaller than expected**: the corroboration harm behind `merge_facts`' cross-frame refusal does not transfer, since nothing corroborates on a label, and the check does not catch the example that motivated it. **66** is **built 2026-08-27** as `reframe` and `correct_interval` — see its own row. **78 supersedes 77** the same day it was filed. **77** described the defects and proposed the wrong remedy: it rejected an opaque judge id on two objections that were both premised on a free-text prompt, and the user pushed back. **78** is the whole area in one entry — ten defects, of which three are a gate that guards *minting* an id and never *assuming* one, so an approved id binds with no user involvement and the refusal helpfully lists the ids that will. The remedy is a **picker**, which `ctx.elicit` already supports and which is the precondition for everything else: it is what lets a judge id be opaque, since the user picks a rendered identity rather than typing a string, and a human resolves name to identity on every bind rather than a machine guessing. Then three layers — an immutable UUID, a freely renamable name resolved at read time, and the existing per-decision pinned description — because one field is doing three jobs with three different rules. **Migration absorbs the aliasing feature 77 asked for**: former ids on the agent record are the migration path, the repair for the `Opus 5 Judge` / `Opus 5` split, and the resolver for pre-change rows, with nothing rewritten. **Stage 1 built 2026-08-25** — picker, cadence and the new-judge flag, closing eight of the ten; the identity split and the three-layer model remain. Building it turned up a defect in itself: asking on every bind broke elicitation-less clients entirely, because *declined* and *nobody to ask* were one value, and an existing test's docstring had described the distinction years before anything needed it. Cadence decided by the user the same day: the picker appears once per session, per graph, **per identity** — keyed on the identity rather than the session, since *confirmed at all* would let an agent be approved as one judge and then bind silently as another. **Stage 2 built 2026-08-26 and 78 is closed** — see its own row |
| ✅ | ~~81 (a relation merge strands the label record it merged away)~~ | **Found and fixed 2026-08-27**, in that order and the same day, by trying to finish #74 stage 1's deferred test 9 — *every record a label can acquire is reachable without the CLI*. `RELATION_LABELS.md` §2.3 enumerates three write paths that name a label; **there are four**, because `apply_reflection(relation_merges=…)` exists today and the design was written as if stage 4 had already replaced it. Before the fix, consolidating into an uncoined label left the edges pointing at a word with no record, the survivor undescribed, and the loser's prose sitting in the store unreachable through any agent surface — while `list_relations` and `query_relation_labels` quietly disagreed about whether the abandoned word existed. The survivor now gets a judge-less record (merging is not coining) and the loser's prose comes back in `relation_descriptions_orphaned` rather than being folded in: settling two definitions into one is the agent's judgment, not the system's, which is `reflect`'s nominate-don't-decide split applied one layer over. **The residue is deliberate**: removing the stranded record needs a hard delete this system deliberately has only one of, or the `status` that is stage 4 and undecided — so this is now *measured* evidence for §5 rather than a predicted cost. Carry-forward: **an enumeration of write paths in a design is a claim that ages**, and the guard that catches it is exactly the deferred test |
| when a case turns up | 80 | **A suppression has no retraction, so every wrong decline is permanent by construction** — the dual of #64's rule, and the thing #64's own fix creates. Filed 2026-08-27 because the reasoning already existed in three places and had no number: `RELATION_LABELS.md` §4.2, #74's FC section, and `similarity_decisions.py` — and #74's entry is scheduled to be pruned when its stages finish, which would take the argument with it. **Not #68**, which was the affirmative half and was fixed 2026-08-23 with a deliberately one-way retraction; that fix left suppression untouched on purpose. **And the two layers may legitimately need different fixes**, which is the part most at risk of being lost: #68's asymmetry is entirely a property of corroboration, nothing corroborates on a label, so a symmetric retraction is a live option there and would be wrong for facts. Zero cost today — stage 3 is unbuilt and one label in the largest real graph means no pair can be nominated — but **when stage 3 ships the system holds two instances of an acknowledged defect**, which is the deadline. Build it from a real case: a suppression somebody actually wants undone, argued from the instance rather than guessed at |
| designed | inference merge, advisories, node notes | Not on this board — `dev-docs/WARNINGS_AND_SETTINGS.md`, designed 2026-08-21 and deliberately unbuilt. The duplication it addresses does not exist yet: 123 active inferences across both real graphs yield 5,053 pairs and **zero** at the nomination bar. It becomes real once fact merges start collecting inferences onto one survivor |
| deprioritised | 79 | **Premise corrected 2026-08-26.** Filed on two reasons — a local store that cannot persist, and SurrealDB's BSL — and the second did not survive checking. **BSD and BSL are unrelated**, and an inventory of 186 installed distributions found **zero** source-available packages: 178 permissive, `surrealdb` itself Apache-2.0. The BSL governs the **server binary**, which is not a Python package and is never shipped; embedded `surrealkv://` needs no server at all and reopens a store in **25 ms**. What is left is a convenience for a command-line tool, worth having and not worth displacing designed work. Two findings outlast the entry: floats as JSON are unviable (951 MB and 10.6 s at 100k nodes, against 379 MB and 0.9 s as base64 float32), and **`torch` is 385 MB against `surrealdb`'s 9.8 MB**, so dependency weight for an embeddable Epimemer is an embedding problem, not a storage one. Carry-forward: *confirm the constraint before designing around it* |
| deferred | 58 | A graph large enough that the FTS backfill inside `connect()` is worth reporting on. **16 left this row on 2026-08-23** — its trigger had already fired |
