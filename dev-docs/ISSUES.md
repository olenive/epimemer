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

### Issue 66 — two ingest-time judgments have no way to be revised — 🔴 OPEN (found 2026-08-22)

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

### Issue 68 — nothing retracts a `one_claim` verdict — 🟠 OPEN (found 2026-08-22)

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
| **next** | 68 (no retraction), or `REVIEW_MODE.md` step 3 | 68 is small and bounded — every `similarity` edge was written deliberately, so the population that could need retracting is tiny — and it is the third instance of #64's shape after #66's two. Step 3 threads `judge` through the reflect-side writers, which is what gives step 5's journal something to read; it needs step 2, which is now in. Neither blocks the other |
| designed | inference merge, advisories, node notes | Not on this board — `dev-docs/WARNINGS_AND_SETTINGS.md`, designed 2026-08-21 and deliberately unbuilt. The duplication it addresses does not exist yet: 123 active inferences across both real graphs yield 5,053 pairs and **zero** at the nomination bar. It becomes real once fact merges start collecting inferences onto one survivor |
| deferred | 16, 58 | 16: the server gains concurrent clients (the viz-read leg is closed by the hub; the fix is now scoped to `hub_client.py`). 58: a graph large enough that the FTS backfill inside `connect()` is worth reporting on |
