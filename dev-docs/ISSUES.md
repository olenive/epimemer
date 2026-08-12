# Epimemer — Known Issues

Living issue tracker. **Last review: 2026-08-12.**

Open: **16**, **46**, **48**, **51**, **52**, **53**. Resolved and awaiting
deletion once merged: **54**, **55**, **56**. New findings continue from
**57**.

**#53 is the most important thing in this file.** *Facts have no validity
interval, so the graph cannot say when a claim was true.* Saint Petersburg was
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

**#53's design is complete and none of it is built.** All six review findings
are answered — 2, 4 and 6 by T1; 1 by T2; 3 across both; 5 by T3. What remains
is construction. T2 unblocked **#54** and added a second caller to **#48**.

The rest: **46** is decided and ready, **51** follows it, **52** is deferred
behind **53** (its safety precondition is what uncovered the problem), **48**
is a defect that does not hurt yet, and **16** stays deferred by design.
**54**, **55** and **56** are done — and all three were the same shape: a rule
stated in one place and re-derived, differently, somewhere else.
**Nothing open *fails* at a size anyone is running** — #53 is a correctness
ceiling rather than a crash, which is precisely why it is easy to keep not
noticing.

**A design review (2026-08-12) of the open set added amendments** — blockquotes
marked *Review 2026-08-12* inside #46, #51, #52 and #53 — and filed **#54**.
Nothing already decided was overturned; each amendment is either a problem the
decided design must answer before implementation (#46, #51), a condition the
re-open trigger must carry (#52), or a place where the recommendation is not
yet decidable as written (#53). Three of #53's six are closed by T1 (items 2, 4
and 6); item 1 *is* T2. #46's amendments still need developer sign-off, since
they change the decided field shape.

**One finding is unfiled and belongs to whoever picks up #55.** Commit `666904f`
widened `NodeStatus` without updating the frontend's status→opacity map
(`graph-panel.ts:52`), whose own comment warns that "an unlisted status falling
through to 1.0 would draw a retired node as a live one". `corrected` and
`historical` are unlisted, and all four call sites use `?? 1.0`. The fix is the
fallthrough default, not two more keys — that repairs the class rather than
today's instances.

**46 was decided on 2026-08-12, and the decision split it again.** The
documentation promised two things in one sentence — "how well-supported by
evidence" and "multiple independent sources increase confidence" — and they want
opposite implementations. Support is a judgment about material only the
ingesting agent has read, so `confidence` becomes a caller-supplied prior with
a four-value ladder and written guidance. Corroboration is a fact about the
graph that changes as the graph does, so it is derived at read time under its
own name (**#51**) and never writes the field. The general lesson, the third in
this file to arrive by the same route: **when one field is documented with an
"and", check whether the two halves want the same storage.** They did not for
`relevance`, they did not for `novelty`, and they do not here.

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
(1–15, 17–45, 47, 49, 50) are deleted-resolved items, not missing work, and code
comments citing a number no longer listed here are pointing at one of them.

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

Listed by issue number, not by priority — for priority see *Recommended order*
at the end. **The one to read first is #53**, whose T1 section is the design of
record for validity. The one to *do* first is **#55**, at the bottom, which is
four lines and needs no decision from anyone.

### Issue 48 — `get_node_by_content` scans the node table on every ingest — ▶ ACTIONABLE

Found by the same query-plan audit as #14 step 4, on the *write* path rather
than the read path.

`SELECT * FROM {table} WHERE content = $content AND status = $status LIMIT 1`
has no index on `content`, so the planner takes `idx_{table}_status` — which
matches every active row — and filters afterwards.

> **#53 T2 (2026-08-12) added a second caller to this path.** Recurrence — a
> claim retired as `HISTORICAL` becoming true again — requires the lookup to
> surface historical twins as well as active ones, or ingest silently creates a
> duplicate node. That widens the status filter this query already carries, so
> the index decision and the recurrence lookup should be made in one visit
> rather than two. Measured per call: **1.3 ms
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

### Issue 46 — `confidence` is a constant that documentation describes as a measurement — ▶ ACTIONABLE

Found by the audit that resolved #44: having established that `relevance` was
written but never read, the obvious next question was whether its siblings were
any better off. They are worse in a different direction — **read, but never
written.**

> **Narrowed 2026-08-11.** This entry originally covered `novelty` as well.
> **`novelty` was removed** rather than decided; the two fields shared a symptom
> and nothing else, and bundling them was hiding that they had different answers.
> See `REVIEW_EPISTEMIC.md` §12.1 for the reasoning and the naming conclusion
> ("surprise", reserved for a caller-supplied signal).
>
> **Decided 2026-08-12: option (2), and the sentence about independent sources
> becomes #51.** The documented promise is really two claims bolted together —
> *"how well-supported by evidence"* and *"multiple independent sources increase
> confidence"* — and they want opposite implementations. The first is a judgment
> about the material, which only the ingesting agent has read; it stays a stored
> field and the caller supplies it. The second is a fact about the graph, which
> changes as the graph does; it is derived at read time under its own name and
> never writes `confidence`. One number cannot carry both without becoming the
> thing that killed `relevance`.

> **Review 2026-08-12 — two amendments to the decided design. Both change the
> field shape, so they need sign-off before implementation starts:**
>
> 1. **Store the unrated case as absent, not as 0.5.** The ladder below says
>    "0.5 = default, omit the field" — so a *deliberate* middling rating and an
>    *unconsidered* one land as the same stored number. That is the trap this
>    file has now caught three times (`retrieved_at`, `importance_judged_at`,
>    and the `now`-default lesson in `ValueSignal`'s own docstring): a default
>    that cannot express "never happened". The merge-rule section below even
>    names the 0.5 tie "honestly the *unrated* case" — it should be
>    distinguishable in storage, not only in prose. Shape:
>    `confidence: float | None = None`, with `None` *read as* 0.5 wherever the
>    number is consumed. Cost: the two readers
>    (`topic_consolidation.py:164`'s comparison and `_node_to_dict`'s dump)
>    handle `None`, and `merged_value_signal` needs a rule for it — `None`
>    loses to any real value, by the same argument its clocks already use.
> 2. **Record why alongside the prior.** `judge_importance` refuses a raw
>    setter because "an unattributable judgment cannot be reviewed later"
>    (`server.py:570`). A caller-written 0.9 with no reason recorded is exactly
>    that — the same argument, unapplied to the field it was learned on. An
>    optional one-line basis per entry (e.g. `confidence_basis`, stored in node
>    metadata the way the `reinforcements` trail is) makes a high prior
>    auditable without burdening the omit-it default: guidance should ask for
>    it whenever the supplied value is not 0.5.
>
> And a known gap, accepted rather than solved — record it in the docs, do not
> build it: **there is no path for source discredit.** The guidance below is
> right that contradiction and age must not lower the prior, but when a
> *document* turns out fabricated, every prior derived from it overstates, and
> nothing can sweep per-source. When provenance-edge-level support lands (see
> *Recommended order*), that is the natural place for it; until then it is a
> stated limitation.

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

#### Why not the other two

**(1) Compute it — rejected on its premise, not its cost.** The proposal was to
derive confidence from `knowledge_in_degree_for`. That function's own docstring
calls what it returns *"their **structural importance**"* (`archival.py:188`),
and archival consumes it as exactly that. Deriving `confidence` from it too
would make `confidence` and `importance` the same number computed twice under
two names — a fresh instance of the bug family #44, #45 and the `novelty` half
of this entry were clearing, arrived at by trying to close it. **In-degree is
connectedness, not corroboration.** Ten inferences drawn from one document raise
in-degree tenfold and add no support whatever.

**(3) Remove it — rejected because the claim it makes is worth making.** Unlike
`relevance` and `novelty`, the question "how well-supported is this?" is one
callers actually want answered, it has a live reader, and the field is already
returned to the caller (`_node_to_dict`, `tools.py:2038`, dumps the whole model,
so `value.confidence` rides along in every `search` result). The defect is that
nothing writes it, not that nobody wants it.

#### What (2) requires

The field is the easy part. **A prior nobody supplies is worse than the constant
it replaces** — same number, more API surface, and now documentation claiming a
knob that turns nothing. So the tool guidance is the deliverable, not a
trimming, and it needs to survive the ways an agent will get it wrong.

**One definition sentence, chosen to exclude the three things it keeps getting
confused with:**

> Confidence is how well the record would back this claim up if it were
> challenged — a property of the evidence, not of how far you agree with the
> conclusion, and not of how much the claim matters.

**A four-value ladder rather than a continuum.** Agents calibrate poorly across
a float and well across labelled buckets, and a continuum makes every node a
near-tie for a comparison (`topic_consolidation.py:164`) that reads the number
ordinally anyway:

| Value | When |
|---|---|
| **0.3** | The source hedges — "reportedly", "may have", "one account says" — or the claim is your reading of the text rather than something it states, or the source is partisan on this particular point |
| **0.5** | Default. Stated plainly, no specific reason to doubt or specially trust it. **Omit the field** |
| **0.7** | Stated as established by a source in a position to know |
| **0.9** | A primary or authoritative source *for this claim*: the person about their own preference, the spec about its own behaviour, the original announcement |

**0.0 and 1.0 are reserved.** 1.0 asserts a claim that cannot be revised, which
no ingested statement earns; 0.0 asserts one certainly false, which is a reason
not to store it — or to store it and `record_contradiction`.

#### Edge cases the guidance has to answer

Worked through before writing it, because each is a way the field silently
becomes something else:

1. **Agreement is not evidence.** A confidently-worded source for a claim the
   agent knows to be contradicted still scores low — the record does not back
   it. The counterfactual phrasing gets this right where "how the source states
   it" does not; what is excluded is the agent's *preference*, not its
   knowledge.
2. **Inside a metacontext, the frame is the record.** A fictional fact can
   honestly be 0.9. Without this the agent conflates "is this true?" with "does
   the frame assert this?", every fiction node lands at 0.3, and confidence
   quietly becomes a fiction detector — duplicating, badly, what metacontexts
   already carry. This also answers the standing open question in `SUMMARY.md`
   (*"does confidence mean the same thing in a fictional metacontext?"*): the
   scale is the same, the record it measures against is the frame's.
3. **The rule survives one level down.** A legend *within* the fiction, or an
   unreliable narrator, is hedged by the frame's own text and scores 0.3 in-frame
   — which is the right answer and evidence the rule generalises rather than
   being a special case bolted on for fiction.
4. **Same source, different claims.** "I prefer a functional style" from the
   user is 0.9; "I think the deploy failed because of DNS" from the same user in
   the same message is 0.3. Confidence is per-node, never per-document — which
   matters here more than anywhere, since conversation with the user is this
   system's most common ingest.
5. **Do not lower it for contradiction.** `record_contradiction` and the
   computed `contested` review label already carry that, and they update as the
   graph does. Encoding it in the prior double-counts and goes stale.
6. **Do not lower it for age.** A 2019 document was well-supported in 2019 and
   still is; what has aged is currency, not support. Exactly the argument that
   gives `importance` no decay (`archival.py:166-169`), and `created_at` plus
   supersession already expresses it.
7. **Inferences do not start low for being inferences.** Confidence measures the
   strength of the derivation, not the fact of being derived. Guidance, not a
   constraint: an inference should not generally exceed its weakest support, but
   enforcing that means computing it, which is #51's business.

#### The merge rule turns out to be right, for a reason nobody had written down

`merged_value_signal` takes `max` confidence, currently justified only as "both
sites already did". For a caller-supplied prior, `max` looks wrong — the more
credulous assessment wins and the disagreement disappears. It survives because
of what it pairs with: `merge_similar_topics` selects the **higher-confidence
description as primary**, so the merged node's confidence describes its primary
content, which is by construction the one that held the max. The two rules are
consistent, and the docstring should say that instead.

This is also the sense in which (2) repairs `topic_consolidation.py:164` rather
than leaving it: once agents supply real values the ordinal comparison starts
meaning what it claims. Nodes neither agent rated still tie at 0.5 and still
resolve to "whichever was passed first" — but that is now honestly the
*unrated* case rather than every case.

#### Work

1. `store_decomposition` accepts `confidence` per entry alongside `importance`
   (`_decomposition_entry`, `tools.py:175`; `ValueSignal` bounds already reject
   out-of-range without a clamp).
2. Tool guidance in `server.py` and `tools.py` — definition sentence, ladder,
   omit-by-default, plus the short forms of edge cases 2, 4, 5 and 6. The
   reasoning stays here; the docstring stays short, because a long one costs
   ingest quality on every other field.
3. `merged_value_signal`'s `confidence` docstring bullet gets the justification
   above, replacing "as both sites already did".
4. Docs: `SUMMARY.md:232` (`creation-time only so far`), `:295` (mutation
   table), `:143`, and the promise at `:39`/`:55` — a prior, not a measurement.
   Resolve the open question at `:450` per edge case 2.
5. `CLAUDE.md`'s memory-system section says nothing about value priors; the
   ladder belongs there too, since that is what an agent reads before ingesting.

**Failing test first**:
`tests/mcp/test_tools.py::TestStoreDecompositionValuePriors` — an entry carrying
`confidence` stores it; one omitting it keeps the documented default; an
out-of-range value is refused by the `ValueSignal` bounds rather than silently
clamped. Plus `tests/core/test_types.py` for the merge pairing: the signal whose
confidence wins the `max` is the one belonging to the description chosen as
primary.

---

### Issue 51 — corroboration is documented, wanted, and computed nowhere — ▷ READY (after 46)

Split out of #46 on 2026-08-12. The `ValueSignal` documentation promises two
things and the split gave each its own home: *"how well-supported by evidence"*
became the caller-supplied prior, and *"multiple independent sources increase
confidence"* is this entry. It is a fact about the graph rather than about the
material, so it changes as the graph changes — which is precisely why it must
be **derived at read time and never stored**. A stored corroboration count is
the trap that removed `novelty`: an answer frozen at the moment it was taken,
against a baseline nothing records.

> **Review 2026-08-12 — three amendments and a planning note:**
>
> 1. **Exclude contradictors from the neighbourhood.** Contradicting pairs are
>    near-maximally similar ("the deploy failed" / "the deploy succeeded"), and
>    a `SIMILARITY` edge recorded before the contradiction verdict stays in the
>    walk — so a document that *contradicts* the claim counts as **support**
>    for it. Exclude nodes joined to the subject by `CONTRADICTION`, and by
>    `VARIANT_OF` (a cross-frame variant is that frame's resolution, not
>    corroboration of this one). Cheap: both are edge-type filters on a walk
>    already being made.
> 2. **State the non-interaction with `confidence`.** Three hedged 0.3 reports
>    from three publishers score corroboration 3 — the same as three 0.9s.
>    Defensible, since independence is the thing being counted, but callers
>    will read the count as support, so the response and the docs must say the
>    two signals do not interact rather than leaving it to be discovered.
> 3. **Publisher identity is name-brittle.** `published_by` entities are
>    deduplicated by exact content match (`get_node_by_content`), so "BBC" and
>    "BBC News" are two publishers and the distinct-publisher count inherits
>    the over-split. Fine at current scale; say so in the response rather than
>    silently, alongside the no-`published_by` fallback caveat below.
>
> **Planning note: the semantics migrate twice.** This ships computed over
> duplicates (the similarity neighbourhood); when #52 lands it moves to
> identity (merged nodes, unions of provenance). Callers will have learned to
> read the number by then. Plan the second migration here — what changes, what
> stays comparable — rather than discovering it when #52 re-opens.

**In-degree is the wrong proxy and should not be reached for.** See #46 for the
full argument; the short form is that `knowledge_in_degree_for` is already
consumed by archival as structural importance, and ten inferences drawn from a
single document raise it tenfold while adding no support at all.

**The right shape follows the provenance edges.** For a node, walk incoming
`SUPPORTS` / `DERIVED_FROM` edges to its supporting nodes, take each one's
`SOURCED_FROM` document, and count the **distinct** documents. Better still,
count distinct `published_by` entities: two BBC articles are one source, not
two, and independence is the whole content of the claim. That dimension is
already modelled — `segment` resolves `published_by` into an entity topic joined
to the document by an attribution edge (`tools.py:117-121`) — so this needs no
new schema.

**Compute it over a similarity neighbourhood, not over node identity
(revised 2026-08-12).** The obvious reading of the above assumes the same claim
is one node. It is not — facts are never deduplicated (#52), and that issue is
now deferred behind #53, so waiting for identity means waiting on the two
hardest things open. Include `{this node} ∪ {nodes joined to it by SIMILARITY}`
in the walk instead. This is not a workaround; it is better in three ways:

- **Nothing is destroyed.** A wrong similarity edge overstates a reported
  number. A wrong merge destroys a node and cannot be undone.
- **The error is auditable.** Return the contributing nodes alongside the count,
  and an inflated figure is visible and checkable. A merged node hides its own
  mistake.
- **It works today.** `SIMILARITY` is already a `fact ↔ fact` edge type and
  `pair_scoring.similar_pairs` already builds the matrix, made fast by #47.

**Honest caveat:** the Saint Petersburg case still bites in softer form. "The
city is called Leningrad" and "the city is called Saint Petersburg" are similar,
so under this scheme they corroborate each other. The damage is a wrong number
whose workings can be inspected rather than a fabricated node — a difference in
kind, not degree — but it is a real defect and #53 is what removes it.

Decisions the implementation has to make, none of them obvious:

- **Documents with no `published_by`.** Most of them, today. Falling back to the
  document as its own source is the honest default, but it means an ingest habit
  (whether the caller bothers to attribute) shows up as a corroboration
  difference — the `relevance` confound in miniature, and worth stating in the
  response rather than hiding.
- **Whether the node's own source counts.** It should, as 1: a fact from one
  document is corroborated once, not zero times, and 0 would make the common
  case look like an error.
- **Depth.** One hop is defensible and cheap. Transitive support through
  inferences is more faithful and risks counting the same document repeatedly
  along different paths, so it needs the distinct-set semantics anyway.
- **Where it surfaces.** Alongside the computed `review` labels on `search`
  results, which is the existing precedent for a derived-at-read-time annotation.

**Cost, which decides whether it goes on the default path.** It is a second
edge hop per result set on top of `review_labels_for`. `get_edges_for` is
batched since #14, so it is round-trip-cheap, but it has not been measured and
`search` is the hottest path in the system. Measure before making it
unconditional; an opt-in flag is the fallback.

**Failing test first**: `tests/pipelines/test_corroboration.py` — a fact
supported by three nodes drawn from **one** document scores 1, not 3; the same
fact supported from two documents with distinct publishers scores 2; two
documents sharing a publisher score 1; and (review 2026-08-12) a fact whose
similarity neighbourhood includes a node it **contradicts** does not count that
node's document.

---

### Issue 52 — facts are never deduplicated across documents — ⏸ DEFERRED, blocked on #53

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

---

### Issue 53 — facts have no validity interval, so the graph cannot say *when* a claim was true — ◆ HIGH PRIORITY, DESIGN COMPLETE / NOT BUILT

Filed 2026-08-12. Surfaced while asking whether fact deduplication (#52) could
be made safe by requiring temporal agreement. It cannot, because the temporal
information it would require is not in the model — and following that back
showed the gap is not dedup's, it is the graph's.

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

##### What T2 unblocks

**#54's shape is settled and it is no longer blocked.** A world-change goes
through `temporally_followed_by`; the historical node keeps its own
`sourced_from` edges and therefore its validity intervals, and the replacement
gets **none of them**. Both blanket answers were withdrawn — copying everything
fabricates attribution, migrating nothing drops `has_metacontext` and moves a
fiction-frame replacement into base reality. Migration becomes **per edge
type**; #54 holds the table.

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

### Issue 54 — world-change supersession strips the historical node's provenance and validity — ✅ RESOLVED (2026-08-12)

Filed 2026-08-12, out of the design review of #53 step 1. The defect was known
at build time and deliberately deferred ("edge ownership waits for the validity
model"); the review's finding is that the deferral is wrong for the interim
floor, because the cost is in **data** rather than code.

`supersede_node` migrates the old node's edges onto the replacement
(`versioning.py`, via `supersede_node_tx` on both backends), and migration is a
**move**: `_migrate_edges_inplace` (`storage/memory.py:474`) re-points each
edge's endpoints in place, and the SurrealDB adapter does the equivalent. For a
`CORRECTED` node that is right — the audit-trail husk does not need sources.
For a `HISTORICAL` node it contradicts the status's own purpose: the node kept
*because it is still true of its period* can no longer answer "which document
said the city was called Leningrad?" — its `sourced_from`, `tagged_with` and
user-tier edges now belong to the claim that replaced it.

> **T1 (2026-08-12) makes this worse and changes how the fix is written.**
> Validity intervals live **on the `sourced_from` edge**, per source. Moving
> that edge therefore strips the historical node not merely of its provenance
> but of **its validity intervals** — the only thing that makes it true *of a
> period*. The case is no longer "it cannot say who asserted it" but "it cannot
> say what period", which is the whole justification for keeping the node.
> Ordering is unchanged; the implementation must be written knowing intervals
> ride on these edges, and its test should assert the historical node retains
> them.

**Why this cannot wait for #53:** every world-change supersession performed
between now and the interval model damages historical provenance; moved edges
have no undo; and there is no retroactive repair of old graphs by standing
policy (see *Older carry-overs*). The full ownership question — which edges
belong to which period — genuinely does wait for #53. This issue is only the
interim floor that stops the bleeding.

**Fix (decided 2026-08-12, third pass — both earlier versions are withdrawn):**
when the supersession status is `HISTORICAL`, migration becomes **per edge
type**. Neither of the two earlier drafts survives, and the reason each failed
is worth keeping, because both were reached by generalising from one edge type.

*Draft 1, "copy everything", fabricates attribution.* A `sourced_from` edge
copied onto the replacement records the old claim's document asserting the
**new** claim — soon with the old claim's intervals attached — which is exactly
the class T1 §8 forbids the agent itself.

*Draft 2, "migrate nothing", breaks frames.* Migration has no per-type
granularity today: `migration_excluded` (`core/types.py:187`) excludes only
`NON_KNOWLEDGE_EDGE_TYPES` — history plus review — so **everything else** is one
undifferentiated bucket. "Nothing" therefore drops `has_metacontext` along with
provenance, and a fiction-frame fact's replacement lands in base reality. A
routine supersession would silently break CLAUDE.md's one hard rule: never mix
fictional and factual information. It would also drop `tagged_with`, leaving the
replacement unreachable by topic traversal.

The policy, by group:

| Edges | On `HISTORICAL` | Why |
|---|---|---|
| `sourced_from` | **neither move nor copy** | Attribution. That document did not assert the new claim, and the edge carries the intervals that make the old node true *of its period* |
| `has_metacontext` | **copy** | A frame is not a claim about the world — it says *which* world. Losing it changes the replacement's frame without anyone deciding to |
| `tagged_with` | **copy** | Topics are timeless (T1 §7). The replacement is about the same subjects, and without the tags it is unreachable by topic traversal |
| Knowledge edges — `contradiction`, `variant_of`, `related`, `supports`, `derived_from` | **stay** on the historical node | Each is a claim *about the old claim*. Re-pointing one asserts it of a claim nobody assessed |
| History + review | unchanged — version-anchored | Was already correct, and stays so: `migration_disposition` answers `keep` for both |

The replacement's provenance is its own: a `succeeds` verdict arrives with a new
document, and `store_decomposition` gives the new fact its own `sourced_from` at
creation. When the status is `CORRECTED`, behaviour is unchanged — move
everything, because the audit-trail husk does not need sources and the
replacement is the *same claim*, corrected.

**This needs a real policy function, not a wider boolean.** `migration_excluded`
answers yes/no and cannot express *copy*. Replace it at the two call sites with
one pure function returning the disposition — `move | copy | keep` — from
`(edge, status)`, so the table above lives in one testable place rather than
being reproduced in two backends. `supersede_by_existing` already migrates
nothing and is unaffected; the change is scoped to `supersede_node_tx` on both
backends, switched by the `status` argument the transaction already receives.

**This exposes the `update` world-change path as the odd one out.** A
replacement written via `update(node_id, new_content,
because="the_world_changed")` has agent-authored content and, under
migrate-nothing, **no source at all** — colliding with T1 §8 ("the agent is
not a source"). Guidance for the docstrings on `update` and for
`epimemer_prompts/DEFAULT.md`: **world-changes should arrive as documents**
and resolve via `supersede_by` against the newly-ingested fact; `update` with
`the_world_changed` is for content the caller can genuinely attribute, and an
unattributable replacement is a smell worth naming in the docstring.

**Also in scope, same commit or its neighbour:** the `because` guidance has no
honest can't-tell. An agent that cannot determine which of two opposite things
happened — two undated claims and no world knowledge — is currently forced to
guess, and a guessed status reads as a judgment (the #53 review, item 3, shows
the guess will often be *backwards*). The docstrings on `update` /
`supersede_by` (`server.py:471`, `:512`) and `apply_reflection`'s supersession
spec should route can't-tell to `record_contradiction` / leave-contested
(resolution option 4 in `REVIEW_EPISTEMIC.md` §6) rather than inviting a
fabricated `because`. Documentation only; no signature change.

**Failing test first**: extend `tests/pipelines/test_supersession_kind.py`
(runs on both backends via the parity fixture) —

- supersede a fact carrying a `sourced_from` edge with
  `because="the_world_changed"`: the historical node **still has** its
  `sourced_from` edge, and the replacement **does not gain it** — its
  provenance is its own or absent;
- the same supersession on a fact tagged into a **fiction metacontext**: the
  replacement is in **the same frame**, not base reality. This is the assertion
  that fails under "migrate nothing" and it is the reason the policy is
  per-type;
- `tagged_with`: both nodes carry the tag afterwards — the historical node keeps
  it *and* the replacement has it;
- a `contradiction` edge on the old node stays on the old node and does **not**
  appear on the replacement;
- supersede with `because="it_was_wrong"`: the corrected node does **not** keep
  any of them (unchanged move behaviour, all groups);
- history/review edges stay version-anchored in both cases
  (`migration_excluded`'s successor leaves them alone).

- **T1 addition:** the historical node retains the **validity intervals** on
  its `sourced_from` edge; nothing about the old claim's validity appears on
  the replacement.

**Resolved 2026-08-12.** `migration_excluded` is gone. `migration_disposition(
edge_type, status)` returns `move | copy | keep` and is the only place the
policy exists; `moved_edge_types(status)` derives the type list the SurrealDB
adapter needs, so the backend cannot answer differently from the rest of the
system. Guarded by `tests/core/test_types.py` →
`TestWorldChangeMigrationPolicy` (six cases, including that a legacy
`SUPERSEDED` row still behaves as it always did) and
`tests/pipelines/test_supersession_kind.py` →
`TestWorldChangeKeepsTheHistoricalNodesEdges` (six cases on **both** backends
via the parity fixture). Before the fix, six of those twelve failed with the
edge simply absent from the historical node.

Two things worth knowing for whoever builds #53 on top:

- **The frame test passes both before and after**, and that is the point of it.
  Today's behaviour moves the frame, so the assertion only fails against
  "migrate nothing" — it is a guard against the wrong fix rather than a
  demonstration of the defect, and it is the reason the policy is per-type.
- **SurrealDB plans its copies in Python**, pre-transaction, exactly as
  `merge_nodes_tx` already plans its re-pointing — the adapter is
  single-connection and documented as unsafe for concurrent callers, so nothing
  interleaves. Copies are rebuilt rather than cloned so `uid` and `created_at`
  belong to the new edge.

**The documentation half shipped with it**, and it found one live defect:
`apply_reflection`'s docstring specified `supersessions: [{old_id, by_id}]`
while the code has required `because` since `666904f` — a caller following the
docstring got a `KeyError`. Also fixed: `epimemer_prompts/DEFAULT.md` called
`supersede_by(old_id, existing_id)` without the required argument. Both now
carry the can't-tell routing — if you cannot tell a correction from a
world-change, `record_contradiction` and leave the pair contested, because a
guessed `because` is indistinguishable afterwards from a judged one — and the
`update` docstring names the unattributable-replacement smell.

---

### Issue 55 — the graph view draws corrected and historical nodes as live — ✅ RESOLVED (2026-08-12)

Filed 2026-08-12. A regression introduced by `666904f`, the commit that split
`SUPERSEDED`. Found while triaging the design review, which could not have seen
it — the review read documents, and this defect lives in the gap between a
Python enum and a TypeScript lookup table.

`STATUS_OPACITY` (`visualization/frontend/src/graph-panel.ts:52`) fades
everything that has left the active set, and its own comment states the hazard
exactly:

> Everything that has left the active set fades the same way. […] an unlisted
> status falling through to 1.0 would draw a retired node as a live one.

The map lists `active`, `superseded`, `merged`, `archived`. It does not list
`corrected` or `historical`, which are the values new writes now produce, and
all four call sites use `STATUS_OPACITY[...] ?? 1.0` (lines 213, 226, 237, 312).
So every node retired since `666904f` renders at full opacity —
indistinguishable from live knowledge in the one view built for looking at the
graph.

**Why it was missed, which is the reusable part.** The commit's verification
included "no frontend files touched", offered as evidence of safety. It was the
cause: the wire protocol (`visualization/events.py:50`) passes `status` as a
bare string, so widening the enum on the Python side is invisible to every
compiler and test on the TypeScript side. **A string-typed boundary between two
languages has no build-time guard, so widening an enum on one side is a silent
change on the other** — worth remembering wherever `NodeView` fields are
stringly typed.

**Fix:** change the fallthrough, not the table. `?? 1.0` becomes a retired
default so the *class* of bug is closed rather than today's two instances;
`active` stays explicit at 1.0. Adding `corrected: 0.3` and `historical: 0.3`
would leave the next status to repeat this.

**The same boundary gets crossed again by #53:** `temporally_followed_by` will
reach the frontend as a bare string too. Whoever builds T2 checks the
frontend's edge-type tables in the same commit — this entry is the precedent.

**Failing test first**: `graph-panel.test.ts` — a node with a status the map has
never heard of renders at retired opacity, not 1.0; `active` still renders at
1.0.

**Resolved 2026-08-12.** `STATUS_OPACITY` is gone; `statusOpacity(status)` names
`active` and fades everything else, so the retired list can no longer fall
behind the Python enum. Guarded by `src/graph-panel.test.ts` →
`describe("statusOpacity")`, three cases: `active` at 1.0, all five retired
statuses below 1.0, and an unknown status below 1.0 — the last is the one that
failed before the fix (*expected 1 to be less than 1*).

---

### Issue 56 — the two panels disagree about what colour a fact is — ✅ RESOLVED (2026-08-12)

Filed 2026-08-12, out of the valid-time grammar review. `VISUALISATION.md` C.6
asserted that the semantic hues are *"how the graph says what kind of thing you
are looking at, and the panels agree on them"*. The second half was false:

| Node type | `graph-panel.ts:29` | `timeline-panel.ts:89` |
|---|---|---|
| fact | `#22c55e` green | `#3b82f6` blue |
| inference | `#f59e0b` amber | `#a78bfa` violet |

Both panels are visible at once — they are the two halves of the split pane — so
a user watching a fact appear in one and move in the other sees it change
kind. This is the same class of defect as #55 (a lookup table drifting from the
meaning it encodes), one level up: not a missing key, but two tables that never
agreed.

**Decision (2026-08-12):** one shared semantic palette, taken from the
valid-time grammar's validated set. `VISUALISATION.md` **C.6 holds the table and
the reasoning** — fact blue, inference violet, topic to the grammar's green,
contradiction keeps red, and the now-line becomes a neutral dashed rule rather
than competing for it. `TIMELINE_VISUALISATION.md` §13.3 defers to C.6.

**Consequences that are mechanical, not further decisions.** Each is a hue that
now collides with a *different* meaning than it had:

- `subtopic_of` (`#818cf8`) was derived from topic indigo — it follows topic;
- `derived_from` (`#a78bfa`) is now the inference **node** colour;
- `supports` (`#4ade80`) is now adjacent to the topic **node** colour;
- `REFERENCE_STROKE` / `REFERENCE_LABEL` (amber) become the neutral now-rule —
  and amber is wanted for *pending*.

**One structural note.** `timeline-panel.ts` holds these as module constants
with a single value each, so it has no dark-theme variant to change; the palette
is per-theme. The recolour should route through `theme.ts`'s palette rather than
re-adding constants, which is the direction Part C is going anyway. That makes
this issue a small down-payment on C.1 rather than work C.1 has to redo.

**Failing test first**: a shared-palette test asserting that `graph-panel` and
`timeline-panel` resolve the *same* colour for `fact` and for `inference` in
both themes — the assertion that is false today, and the one that stops the two
tables drifting apart again. Plus: no two distinct semantic meanings resolve to
the same hue in either theme.

**Resolved 2026-08-12.** `SemanticPalette` in `theme.ts` is the one table, per
theme; `semanticPaletteFor(theme)` reads it. Both panels now name a *meaning*
rather than a hex value — `nodeColor` / `edgeColor` in `graph-panel.ts`,
`markColor` / `selectedMarkColor` in `timeline-panel.ts` — so a re-pick is a
one-line change in one file and neither panel can drift again.

Guarded by `src/palette.test.ts`: cross-panel equality for `fact` and
`inference` in both themes (failed before the fix with *expected '#22c55e' to be
'#3b82f6'*), distinctness across the nine meanings, and that the load-bearing
hues actually vary by theme.

Three things the fix had to deal with that the entry did not anticipate:

- **Hues are baked into each cytoscape element's `color` data at add time**, so
  `applyTheme` had to start re-writing them. Restyling alone left half the
  canvas in the previous theme — invisible until the palette gained a theme
  axis, because until then the hues were the same in both.
- **The edge table's collisions resolve by rule, not by re-picking.** An edge
  tied to a node kind takes that kind's hue: `supports` → fact, `derived_from` →
  inference, `subtopic_of` → topic. That is why they collided in the first place
  — each was a hand-picked near-miss of the kind it belongs to.
- **`theme.ts` claimed the hues were theme-independent** *"so that fact green
  means the same thing in both themes"*. That reasoning is what let the drift
  happen: with no theme axis, nothing ever forced the two tables to be
  reconciled. The docstring now says so.

**Left alone deliberately:** `pipeline-detail.ts` keeps its own
active/completed/failed colours, which C.6 lists as a separate group. Worth
knowing that this fix *removed* a collision there — pipeline-completed green was
exactly the old fact green, and pipeline-active amber exactly the old inference
amber. Pipeline-completed `#22c55e` is now merely adjacent to topic `#1baf7a`,
in a different panel.

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

**#55 goes first regardless**, being genuinely decision-free: a status map in
the frontend that `666904f` left behind, drawing corrected and historical nodes
at full opacity against its own comment's warning.

**#48 is a defect that does not hurt yet** — an O(N) scan per ingested node,
invisible at today's sizes. Worth doing before the graph sizes that make it
visible, and worth measuring rather than patching: the obvious fix (index the
content) may cost more than it saves.

**#46 is decided and #51 follows it.** Neither fails; both exist because the
docs describe measurements the code does not take, which is the same trap #44
was. Do them in order — #51's whole justification is that `confidence` is not
the place to put a corroboration count, which only reads as a decision once
`confidence` is something else. Between them the deliverable is written
guidance rather than code: the field and the derivation are both small, and a
prior no agent knows how to set is worth less than the constant it replaces.

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
| 1 | **53 (validity intervals)** | **Design complete (T1/T2/T3), nothing built.** Everything else on this list is a defect inside a sound model; this one says the model cannot express something true. The floor (splitting `SUPERSEDED`) is done; the rest is construction against the entry, and `graph_as_of` is the only piece carrying a migration cost |
| 2 | 46 (`confidence` becomes a supplied prior) | Decided 2026-08-12, but **two review amendments change the field shape and need sign-off first** (store the unrated case as absent; record a basis alongside a non-default prior). The work remains the tool guidance more than the field. Independent of 53 |
| 3 | 51 (corroboration derived at read time) | After 46, which is what makes it a separate signal rather than a rewrite of one. Apply the review's neighbourhood exclusions (contradictors, variants). Measure the extra hop before putting it on the default `search` path. Ships with a known 53-shaped inaccuracy, stated in the entry |
| 4 | 48 (`get_node_by_content` scans per ingest) | **Ready now** but not urgent, and the fix needs measuring before it is chosen. **T2 added a second caller** — the verbatim-twin floor; the load-bearing recurrence detector is nomination including `HISTORICAL` (#53 T2 second pass) — so do both in one visit to this path |
| deferred | 52 (fact deduplication) | Re-open after 53 — and the re-open must carry the review's event/state distinction: interval union dedupes states, never events. Not before |
| deferred | 16 | The server gains concurrent clients (the viz-read leg is closed by the hub; the fix is now scoped to `hub_client.py`) |
