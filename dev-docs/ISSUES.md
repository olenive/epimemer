# Epimemer — Known Issues

Living issue tracker. **Last review: 2026-08-10.**

Everything found so far is resolved except **14** and **16**, both deferred by
design, and **14**, whose first two steps are now actionable — the deferral
rested on "nothing fails because of it", and #39 ended that. **39**, **42** and
**43** are resolved but not yet merged, so their entries are still here.
Resolved entries are **removed from this file** once merged — their resolution
lives in git history and the merged code. Issue numbers are stable IDs; the gaps
(6–13, 15, 17–38, 40, 41) are deleted-resolved items, not missing work, and code
comments citing a number no longer listed here are pointing at one of them. New
findings continue from **44**.

35–38 were the value model & graph hygiene plan
(`dev-docs/REVIEW_EPISTEMIC.md` §12, which records what the plan did not
anticipate) plus the mock-embedding width fix.

The performance work (issues 28, 31, 32 and 33) is resolved and its entries are
gone. **`dev-docs/BENCHMARKS.md`** carries the state those fixes left the system
in and the conclusions still worth acting on, but not the runs themselves — it
describes where things stand, not how they got there, and superseded
measurements are deleted rather than kept. The blow-by-blow is in `git log`.
#14 below depends on the current numbers.

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

### Issue 14 — Full-scan / N+1 query patterns — ▶ ACTIONABLE (steps 1–2); step 3 still blocked

**Status.** Actionable for the first time: `scripts/bench.py`
(`make bench`) has measured both backends, and four fixes have since removed
every part that had actually broken — `reflect`'s cubic frame lookups, the
in-memory edge scan, SurrealDB's correlated status filter, and the pairwise
Python in the contradiction phase (#39). **The current numbers and the method
are in `dev-docs/BENCHMARKS.md`**, which carries where those fixes left the
system rather than the runs that produced them; the runs are in `git log`.

**Update 2026-08-10 (#39 promoted this).** With the pairwise arithmetic
vectorized, `reflect` on SurrealDB barely moved — 1.36× against in-memory's 4.6×
— because that backend's time goes on sequential round-trips: one
`get_embeddings_for_item` per fact, then two edge queries per fact to build the
already-linked set. **That is this issue, and it is now what fails first on
SurrealDB**, at ~2,000 nodes against in-memory's ~6,700. The deferral stands —
the fix is still a protocol change on both backends and the `asyncio.gather`
prong is still blocked by #16 — but the *grounds* have changed. This entry no
longer rests on "nothing fails because of it", and the first candidate is the
batched edge fetch in step 1 below, now with a concrete graph size attached.
See `dev-docs/BENCHMARKS.md`.

**Promoted 2026-08-10.** This entry was deferred for months on one sentence —
"nothing fails because of it" — and that sentence stopped being true when #39
landed. `reflect` on SurrealDB is now round-trip bound and crosses the 30 s tool
timeout at **~2,000 nodes**, roughly 100 documents of five segments. It is the
only thing in the system that fails at a size real use reaches.

The deferral was also broader than the blocker justified. Only **step 3**
(`asyncio.gather` on enrichment) is blocked by #16's shared-connection hazard.
**Steps 1 and 2 are unblocked**, and step 1 is the one that helps every N+1 site
at once — including `_hierarchy_annotations` (`mcp/tools.py`), which was
deliberately left waiting for it.

So: steps 1–2 are the next work in this file. Step 3 keeps #16's trigger.

---

#### Where this issue stands

`EPIMEMER_TOOL_TIMEOUT_SECONDS` defaults to **30 s**, so "crossing" means *the
tool call fails*, not *feels slow*. **The numbers live in
`dev-docs/BENCHMARKS.md`** and are not duplicated here — a second copy is a
second thing to keep true, and this entry has already outlived four rounds of
them.

What matters for this issue: **`reflect` is the limiting operation on both
backends, and on SurrealDB the limit is now this issue rather than arithmetic.**
Three live N+1 sites, worst first:

1. **`detect_contradictions` fetches per fact** — one
   `get_embeddings_for_item`, then `get_edges_from` and `get_edges_to` to build
   the already-linked set, all sequential. On SurrealDB this is what puts
   `reflect`'s crossing at ~2,000 nodes against in-memory's ~6,700. It is the
   first thing that fails on a networked backend.
2. **Per-result enrichment under `search`** — the ~120 ms floor that remains
   after ranking was separated from the status filter. Nothing fails because of
   it; it is simply the floor any further `search` work would have to attack.
3. **`list_sources` / `list_relations`** iterate every active node and fetch that
   node's edges: O(N) queries per call. Linear on both backends and far from any
   crossing. The call pattern is this issue's, but in-memory it now costs a dict
   lookup per node rather than a full scan (`by_src` / `by_dst` endpoint
   indexes), so it is not worth attacking on its own.

Ingest is flat on both backends at every size measured; the write path has never
been the ceiling.

---

#### What to fix, in order

Two earlier steps are already done and are not repeated here: indexing
in-memory edge lookups, and separating ranking from the status filter in
SurrealDB's `vector_search`. `dev-docs/BENCHMARKS.md` records what
they left behind, and its standing warning applies to anything attempted here:
in each case the fix the issue predicted was not the fix the profile found.

1. **Batched edge fetch in the protocol.** `get_edges_for(node_ids, edge_type)`
   returning a map, implemented on **both** backends per the parity rule. This
   is the one change that helps every N+1 site at once, and it is what
   `_hierarchy_annotations` (`mcp/tools.py`) was deliberately left waiting for.
2. **Aggregate queries** for the listing tools: count edges grouped by `dst`
   for `sourced_from`; distinct label+kind for `RELATED`.
3. **`asyncio.gather` on per-node enrichment** — **blocked by #16**. Do not
   introduce concurrent storage access while the SurrealDB connection is
   shared and `use()`-switched.

---

#### Before touching any of it

`dev-docs/BENCHMARKS.md` carries the reproduction commands, the caveats that
qualify every number (mocked embeddings, a self-similar synthetic corpus,
loopback-only networking), and the profiling recipe. Its standing warning is the
one that matters here: **profile first** — every performance fix in this project
so far has overturned the cause its issue predicted, including twice on this
issue's own candidate explanations.

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

### Issue 39 — `reflect` is the nearest real failure: O(F²) cosine similarity in pure Python

**Status.** ✅ RESOLVED 2026-08-10 by vectorizing, which was step 1 of the two
the issue proposed. Step 2 (cutting the candidate set) was **not needed and not
done**: the measurement said so, which is what it was there for.

**What it bought.** `reflect` went 4.1× faster at 1,000 nodes in-memory and 4.6×
at 3,000, moving the 30 s crossing from ~2,970 to **~6,700** nodes. The exponent
is essentially unchanged (1.99 → 1.87), exactly as this issue predicted — a
constant factor moves a quadratic crossing by roughly its square root, √4.6 =
2.14 against 2.26 observed. Full data, controls and method:
`dev-docs/BENCHMARKS.md`.

**What it did not buy, and this is the finding.** SurrealDB moved only 1.27–1.36×,
to a crossing of ~2,000. The pairwise arithmetic was never that backend's
bottleneck: `reflect` there is dominated by sequential round-trips — one
`get_embeddings_for_item` per fact, then two edge queries per fact to build the
already-linked set — which is **#14's N+1 pattern**. So SurrealDB is now the
limiting backend by 3.3× rather than 1.8×, and the thing that fails first on it
is #14 rather than this issue. #14 stays deferred (its fix is a protocol change
on both backends, and one prong is blocked by #16), but the grounds for
deferring it — "nothing fails because of it" — are weaker than they were. Noted
in #14 and in the benchmark section.

**Guarding tests.**
- `tests/pipelines/reflection/test_similarity_scoring.py` — 13 tests over the new
  pure `similar_pairs`, most of them checked against the loop it replaced, which
  is kept in the test file as the oracle. The two failure modes a batched
  implementation has and a loop does not get their own classes: **blocking** (a
  pair straddling a block seam, the diagonal appearing in every block, every
  block size agreeing) and **normalization** (a zero vector taking a whole row to
  NaN, which would compare false against the threshold and silently switch
  detection off).
- `tests/pipelines/reflection/test_reflect_scaling.py::TestContradictionScoringIsBatched`
  — the regression guard, in the shape that module already uses: no per-pair
  Python call survives, and the whole set is scored in exactly one call whether
  there are 12 facts or 24. Before the fix the first of these reported *66
  per-pair Python calls* for 12 facts, on both backends.
- `TestAnswersAreUnchanged` in the same module still passes untouched, which was
  the issue's condition: this is a speed fix, and the pairs found must be
  identical. Ordering is part of that — `similar_pairs` returns pairs in index
  order so that the caller's stable sort leaves tied pairs in the sequence the
  loop produced.

**Three things the fix had to decide that the issue did not anticipate.**
- **Blocking is not optional.** The naive vectorization allocates an F×F matrix:
  800 MB of float64 at 10,000 facts, which trades a timeout for an allocation
  failure. Scoring proceeds in blocks of 512 rows, so peak memory is block ×
  facts.
- **float64, not float32.** Half the memory and roughly twice the speed, but the
  issue's own condition is that the pairs found be identical, and float32 puts
  ~1e-7 of slack on a comparison against a 0.80 threshold. Not worth it.
- **Ragged vectors had to be given a defined behaviour.** A matrix cannot be
  built from mixed widths, so facts whose stored vector differs from the first
  one's width are dropped, exactly as facts with no embedding already were. The
  loop did not fail on this case — it `zip`ped the vectors together, silently
  truncating to the shorter and scoring the pair on a prefix. Dropping is the
  quieter of two quiet behaviours, and the honest one.

`numpy` was already installed as a transitive dependency of
`sentence-transformers` and is now declared directly in `pyproject.toml`, since
the code imports it.

---

*Original report:*

**Status.** Open, and newly worth filing. It sat as a "watch" row for weeks on
the strength of a crossing at ~7,400 nodes. The 2026-08-07 re-baseline at a real
384 dimensions (#38, resolved) moved that to **~2,900 in-memory and ~1,400 on
SurrealDB** — around 70 documents of five segments. That is a graph size real
use reaches, so the trigger the watch row was waiting for has fired.

**Symptom.** `detect_contradictions`
(`epimemer/pipelines/reflection/contradiction_detection.py`) compares every
surviving candidate fact pair with `_cosine_similarity`, a pure-Python dot
product. At 1,500 nodes that is ~280k comparisons, and each now walks
384-component vectors rather than 32. `reflect` exceeds the 30 s default
`EPIMEMER_TOOL_TIMEOUT_SECONDS` at the sizes above — the tool call *fails*, it
does not merely feel slow.

**What is different about this one.** Every earlier performance fix removed
*redundancy* — repeated frame lookups, a full scan per edge lookup, a
re-executed status filter. There is none left here: the pairwise comparison is
genuine work. Vectorizing with numpy buys a large constant factor (plausibly
10–50×, which moves the crossing by roughly the square root of that) and does
**not** change the exponent. A real reduction needs fewer pairs, not faster
pairs — blocking, an ANN index, or a cheaper pre-filter before the exact score.

**Fix, in order of what to try.**
1. **Vectorize the scoring.** Stack the candidate vectors into one array and
   take a matrix product instead of a Python loop. Contained, no behaviour
   change, and the measurement will say whether it is enough.
2. **Cut the candidate set** if it is not. The 0.80 threshold currently admits
   19% of unrelated pairs under the mock corpus — see the self-similarity caveat
   in `dev-docs/BENCHMARKS.md`, which also means any measurement here overstates
   the phase relative to a diverse real corpus. Measure on realistic text before
   choosing a blocking strategy.

**Failing test first.** This is a performance issue, so the guard is a
measurement, not an assertion about a wrong answer:
- `tests/pipelines/reflection/test_reflect_scaling.py` — extend the existing
  scaling coverage with a contradiction-phase case that fails at a node count
  the current implementation cannot finish inside the tool timeout.
- Whatever changes, `TestAnswersAreUnchanged` in that module must still pass:
  the pairs found must be identical, since this is a speed fix and not a
  detection change.

**Re-bench after.** `make bench BENCH_N=1000,3000` and the SurrealDB command in
#14, appending a section to `dev-docs/BENCHMARKS.md`. Compare only against the
2026-08-07 baseline — anything older is 32-wide.

---

### Issue 42 — `importance` only moves up, and a stale judgment protects a node forever

**Status.** ✅ RESOLVED 2026-08-10, in the two commits the issue proposed.
`reinforce` is now `judge_importance(node_id, direction, reason)` with the
mirrored down step, and nomination reads the `(importance, importance_judged_at)`
pair so an assessment nobody has revisited stops protecting a node.

**Guarding tests.**
- `tests/pipelines/test_reflection.py::TestJudgmentStaleness` — the assertions
  this issue exists for: a judgment nobody has revisited in `judgment_max_age_days`
  comes back as a `stale_judgment` nominee; a recent one is left alone; and three
  downward judgments return a node to the cheap tier under its own steam.
- `tests/mcp/test_tools.py::TestJudgeImportanceDownward` — the down step lowers
  by the mirrored fraction; 50 judgments approach 0 without reaching it;
  up-then-down lands at 0.46875 rather than home; **40 alternating judgments
  settle on the two-cycle {3/7, 4/7}**; both directions share one ordered
  provenance trail; the judgment clock moves and the retrieval clock does not;
  an unknown direction is refused before anything is written.
- `tests/mcp/test_tools.py::TestApplyReflectionJudgments` — a downward judgment
  applies through `apply_reflection`; **judging back up also clears the
  staleness**, which is what stops a re-confirmed nominee returning forever;
  unknown ids are skipped as supersessions are.
- `TestJudgeImportanceUpward` (the former `TestReinforce`) still passes with
  `direction="up"` — the upward behaviour is unchanged, which was the condition.

**Two decisions taken while building it.**
- **`judgment_max_age_days` defaults to 180**, not the 90 of `max_age_days`. A
  judgment should outlive the retirement window: re-reviewing every judged node
  quarterly is noise, and the target is assessments that have quietly expired,
  not recent ones.
- **`stale_judgment` sorts last** in `_REASON_ORDER` and is documented as *not*
  an archival claim. It asks the agent to re-confirm or lower an assessment, and
  judging the node back up is a correct answer that needs no human approval —
  it changes a degree, not a status.

**The provenance key stayed `reinforcements`.** Renaming it is a data migration
for a cosmetic gain. Entries now carry `direction`; **an entry without one
predates this tool and means "up"**, which is documented on `judge_importance`.

---

*Original report:*

**Status.** Open, scoped. **Blocked on #43**, whose judgment timestamp half of
this reads. Rewritten 2026-08-10 after the design discussion recorded below; the
first draft proposed a `deprioritize` tool alongside `reinforce`, and considered
decaying `importance` on a clock. Both were wrong, for reasons kept here because
they are the reasons the shape below is right.

**Symptom.** Two halves of one hole.

1. `ValueSignal.importance` has exactly one deliberate mutator — `reinforce`
   (`epimemer/mcp/tools.py:1100`) — and it only raises. Nothing lowers it
   anywhere: `apply_decay` touches `relevance` only and says why
   (`pipelines/reflection/value_decay.py:44-48`), `update` carries the signal
   forward verbatim (`tools.py:1069`), topic merge takes `max()` of its sources
   (`tools.py:1710`), and `config.py:52` states the rule outright — "nothing
   lowers importance on a clock." Each is correct in isolation. Together they
   mean *judgment* has no downward path, on the axis that exists to carry
   judgment.
2. `nominate_archival_candidates` skips anything with
   `importance > importance_ceiling` (0.5) (`archival.py:234,243`). So one
   `reinforce` call removes a node from the cheap nomination tier
   **permanently**. Cleanup does not get it wrong later — cleanup never looks
   again.

`REVIEW_EPISTEMIC.md` §12.3 gives the example itself: "error message X matters
until the bug is fixed, then doesn't." The upward half of that sentence is
implemented; the downward half is not.

**Why archival does not already cover it.** Three reasons, in increasing order of
what they cost:

1. **Different verdict.** Archival is an all-or-nothing status flip that per §7
   wants human approval; a change of degree is something the agent may conclude
   alone. Forcing the first to express the second overstates what was concluded.
2. **The middle case has no expression.** Still worth keeping, no longer worth
   the earlier assessment, is neither important nor archivable. Today the only
   way to record it is to leave the stale number in place.
3. **Re-nomination**, above: the mechanical tier goes blind, so the agent would
   have to carry the re-assessment out of band — precisely the job this system
   exists to do.

---

#### Fix, part 1 — judgment moves both ways

`reinforce` becomes `judge_importance(node_id, direction, reason, related_id=None)`.

**The rename is not cosmetic.** "Reinforcement" is the right word for the
*retrieval* mechanism: it is the memory-science term, and that mechanism
genuinely only goes up, with decay as its counterweight on a clock. It is the
wrong word for a judgment that can go either way, and keeping it would have
forced the incoherent `reinforce(direction="down")`. Naming the tool for the
**act** rather than the outcome makes the argument coherent — and `direction` is
not ceremony wrapped around the judgment, it *is* the judgment: "this matters
more than the graph currently thinks", or less.

**Steps rather than a setter**, which §12.4 already decided and the discussion
sharpened:

- an agent setting `0.7` has not seen any other node's value and is guessing at a
  scale it cannot see; "more than the graph thinks" is a judgment it can make
  well;
- a setter is last-writer-wins — three judgments that took a node to 0.85 are
  erased by one agent typing 0.6 six months later on one conversation's context.
  Steps compose; setters overwrite.

The one moment a setter is safe already exists: `store_decomposition`'s ingest
prior, applied at creation before there is anything to overwrite. Set at birth,
nudge thereafter.

The down step mirrors the up step in form:

```
up:    importance += step × (1 − importance)     # asymptotic to 1.0
down:  importance -= step × importance           # asymptotic to 0.0
```

Both close the gap to their bound by the same fraction. Neither reaches its
bound, so arithmetic can never judge a node out of existence and neither needs a
clamp.

**They are mirrors, not inverses — say so in the docstring.** Up-then-down does
not return home: from 0.5 at the default step of 0.25, up gives 0.625 and down
gives 0.469. Repeated alternation settles into a **2-cycle**, `{0.4286, 0.5714}`,
straddling 0.5 almost exactly — up from 0.4286 gives 0.5714, down from 0.5714
gives 0.4286, and neither side wins. Two agents in sustained disagreement
therefore park the node at the un-judged default, oscillating across the 0.5
nomination ceiling, so whether it is nominatable depends on which judgment came
last. That is the right terminal state: the most recent assessment governs, and
neither side can lock it permanently.

**Why not an exactly invertible form.** Two exist and both were rejected.
`(i − step)/(1 − step)` returns home but goes negative below the step size and
needs a clamp — so it is invertible in the mid-range, where nothing depends on
it, and lossy near the floor, where the nomination ceiling sits and the
consequences land. Log-odds (`sigmoid(logit(i) ± k)`) is genuinely both
invertible and asymptotic, and is the stronger of the two, but it costs the
settable knob (`EPIMEMER_IMPORTANCE_STEP = 0.25` means "close a quarter of the
remaining gap"; `k` is in log-odds units nobody sets by intuition), needs input
clamping anyway because `logit(0)` and `logit(1)` are infinite, and trades a
one-line formula for one that has to be explained.

All of that buys **invertibility, which nothing here consumes.** A later downward
judgment is not an undo — it is a new judgment on new information, and the
provenance trail keeps both entries deliberately. If undo were ever wanted, the
honest implementation restores the recorded prior value from that trail rather
than hoping the arithmetic reverses.

The asymptote, by contrast, is load-bearing. Reaching 1.0 would mean "maximally
important, and no future judgment can raise this further"; reaching 0 would mean
"worthless, full stop". Arithmetic must not manufacture certainty on the agent's
behalf — the same commitment as archive-never-delete, expressed on the number
line.

**Deferred: a `strength` modifier.** `strength="slight"|"normal"|"strong"` as a
multiplier on the step is about ten lines and a constant table. Deferred on three
grounds: an *optional* parameter with a default is a non-breaking addition
whenever it arrives, so waiting locks nothing in; there is no evidence yet for
what the multipliers should be, and inventing three magic numbers is how a knob
acquires a wrong default nobody revisits; and each extra knob needs a decision
rule in `DEFAULT.md` or agents pick the strongest option by default and the
gradations mean nothing. Trigger to revisit: one step proving too coarse in
practice — measurable, unlike taste.

---

#### Fix, part 2 — a stale judgment stops protecting

Given #43's `importance_judged_at`, nomination reads the **pair** rather than the
number: "rated important, judged six months ago, never since" is a re-review
candidate. `nominate_archival_candidates` gains a `judgment_max_age_days` in the
shape of its existing `max_age_days=90`.

**No decay on `importance`, deliberately.** The first draft of this issue
considered it and it is wrong. A decayed importance is a *fabricated* assessment
— nobody judged that node 0.6, the clock invented it — and the provenance trail
would read "judged 0.85, reason X" beside a field saying 0.6, with nothing
accounting for the gap. That is exactly the unattributable number §12.4's
no-raw-setter rule exists to prevent, arriving through the back door. Staleness
gets the same effect honestly: the recorded judgment stays exactly as recorded,
and what ages is confidence in its *currency* — which is what a timestamp
expresses and a number cannot.

---

#### Two constraints carried over

**One provenance trail, not two.** Keep appending to
`metadata["reinforcements"]` with a `"direction"` field; **absent means up**, so
records written before this change keep their meaning. A reviewer wants a bump
and its later reversal in order, with both reasons — two lists split a story
whose only value is that it is chronological. The key name outlives its accuracy
here; renaming it is a data migration for a cosmetic gain, and #43 already spends
the migration budget.

**Merge still takes `max()`.** A node judged down, then merged with an un-judged
duplicate, comes back up. Correct — merge must not discard the *other* source's
judgment — but it means a downward judgment is not durable across a merge. Noted,
not fixed: changing it needs an argument about whose judgment wins.

---

**Failing test first.** The mirror tests are the cheap half; the one that
justifies the issue is re-nomination, and it fails today for want of any
mechanism at all.

- `tests/pipelines/test_reflection.py` — a fact judged above the ceiling drops
  out of `nominate_archival_candidates`; after a downward judgment it is
  nominated again. **This is the assertion the issue is about.** Second case: a
  fact still above the ceiling, but whose `importance_judged_at` is older than
  `judgment_max_age_days`, is nominated for re-review.
- `tests/mcp/test_tools.py::TestJudgeImportance` — mirroring the existing
  `TestReinforce` (`test_tools.py:635`): the down step lowers asymptotically and
  repeated calls approach but never reach 0.0; `relevance` and `retrieved_at` are
  untouched while `importance_judged_at` moves; entries append rather than
  replace, and an up interleaved with a down keeps both in order with their
  directions intact; unknown `node_id` and unknown `related_id` are rejected.
- The 2-cycle earns its own named test rather than a comment: up-then-down lands
  below the start, and repeated alternation settles on `{0.4286, 0.5714}` at the
  default step rather than drifting to a bound. A future change to the step form
  that breaks this should have to argue with a test, not discover it in a graph.

These take the `storage` fixture, so they run against both backends per the
parity rule; the write goes through `store_node` and needs no protocol change.

**Two commits.**

1. The tool — rename, direction, down step — with its tests and the doc updates:
   `INTEGRATION.md:63`'s tool count (34, enforced by
   `test_tool_count_matches_integration_doc`; a *rename* leaves the count alone,
   which is worth confirming rather than assuming), `README.md:132`'s tool list
   and its `EPIMEMER_IMPORTANCE_STEP` row, `epimemer_prompts/DEFAULT.md`
   §"Recording that something matters" — the agent needs telling *when* to judge
   downward, not merely that it can — and `REVIEW_EPISTEMIC.md` §12.2/§12.4,
   where the upward paths are enumerated and "there is no raw setter" is decided.
   That decision survives intact; it just stops being the whole story.
2. Nomination reading judgment staleness, plus
   **`apply_reflection(judgments=[...])`**. §12.3 lets the agent `reinforce` a
   nominee instead of letting it go, but has no way to say "keep it, and stop
   treating it as important" — the verdict most likely to be right about a node
   reinforced once and never revisited.

---

### Issue 43 — `last_reinforced` names the wrong mechanism, and a judgment leaves no timestamp

**Status.** ✅ RESOLVED 2026-08-10. `retrieved_at` and `importance_judged_at`
replace `last_reinforced`, both nullable and both defaulting to `None`;
`never_reinforced()` is `never_retrieved()` and reads the null directly;
`_NEVER_REINFORCED_TOLERANCE` is gone. **#42 is unblocked.**

**Guarding tests.**
- `tests/core/test_types.py::TestValueSignal` — both clocks start unset, and a
  `ValueSignal` parsed from a dict carrying neither key reads as unset rather
  than as freshly touched. That second one is the migration.
- `tests/mcp/test_tools.py::TestReinforce::test_reinforce_stamps_the_judgment_clock_and_only_that_one`
  — the two clocks are independent, which is the entire point of having two.
  `TestSearchReinforcement` covers the other direction: retrieval stamps
  `retrieved_at` and leaves the judgment clock alone.
- `tests/pipelines/test_reflection.py` — a node no search has returned is
  nominated; a retrieved one is spared *however recently it was created*. The
  second case is what the old tolerance window got wrong: it asked "were these
  two timestamps written close together" rather than "did anything happen".
- `frontend/src/timeline-model.test.ts` — a node with `retrieved_at: null`
  renders as a point, and the detail says "never" rather than printing null.

**One decision beyond what the issue specified.** The `ArchivalReason` literal
`"never_reinforced"` was renamed to `"never_retrieved"` as well. It is
agent-facing — it appears in `reflect`'s output and in
`epimemer_prompts/DEFAULT.md` — so leaving it would have preserved the exact
naming lie this issue exists to remove, in the one place an agent actually reads.
Nothing persists it (candidates are computed per call), so there was no migration
cost.

**What it cost.** Both timestamps are nullable, so the fixtures that used to
construct "never used" out of two pinned timestamps now just omit the field —
`ValueSignal(importance=0.3)` says it. The frontend needed a type change and
nothing else: `parseTime` already accepted `null`, and the record-time mark's
`end` was already conditional, so the null case was correct by construction
before it was reachable.

---

*Original report:*

**Status.** Open, ready, mechanical. **#42 depends on it.** Found 2026-08-10
while writing #42: the question "should `reinforce` set `last_reinforced`?" has no
good answer, because the field's name spans two mechanisms that are deliberately
separated everywhere else in the design.

**Symptom.** Three things move a node's value, on two clocks, and only one writes
a timestamp:

| Mechanism | Trigger | Moves | Timestamp |
|---|---|---|---|
| Retrieval reinforcement (`reinforced_signal`, `tools.py:624`) | automatic, every `search` hit | `relevance` ↑ | sets `last_reinforced` |
| Decay (`apply_decay`) | `reflect` | `relevance` ↓ | — |
| `reinforce` tool (`tools.py:1100`) | agent judgment | `importance` ↑ | **nothing** |

`last_reinforced` is written in exactly one place (retrieval, `tools.py:636`) and
read for logic in exactly one (`never_reinforced`, `archival.py:159`). It records
the *passive* mechanism under a name that reads like the *deliberate* one — so
`never_reinforced`'s docstring, "nothing has touched this node since it was
created", is false: an agent can deliberately judge a node important and it still
reads as untouched.

§12.2 separates "is this being used?" from "does this matter?" everywhere except
in the vocabulary, which is where a reader meets it first.

**Why this is not just tidiness.** Nomination can ask *was this ever used*. It
cannot ask *when was this last judged*, because nothing records it. So a judgment
can never go stale — which is half of #42, and the half no tool can fix, because
the information was never written down.

**The change.**

| Now | After | Why |
|---|---|---|
| `last_reinforced: datetime`, default `_now` | `retrieved_at: datetime \| None`, default `None` | Names the mechanism that actually writes it |
| — | `importance_judged_at: datetime \| None`, default `None` | The judgment clock, which does not exist today |
| `never_reinforced()` | `never_retrieved()` | What it already computes |
| `_NEVER_REINFORCED_TOLERANCE` | deleted | It exists only because `default_factory=_now` makes "never" and "just now" indistinguishable (`archival.py:121-124`). `None` says it directly |

Nullability is the substantive half, not a style choice: "never retrieved" and
"never judged" are real states that a `_now` default fabricates.

**The ingest prior stays un-stamped.** `store_decomposition`'s optional
`importance` is "a prior, not a verdict" (§12.4) — importance is properly judged
at reflect time. A prior that stamped `importance_judged_at` would masquerade as
a judgment and buy itself the full staleness window before anyone looked. It
leaves the field `None`, which is both true and immediately eligible for review.

**Migration.** `ValueSignal` persists inside the node via
`model_dump(mode="json")` and returns through `model_validate`, so an old record
simply lacks the new keys and takes the defaults. With `None` that means every
pre-existing node reads as never retrieved and never judged, so class-3
nomination *proposes* them. That is the right direction — nomination is a
proposal the agent judges with graph context, never a verdict — and it is the
opposite of what a `_now` default would do, which is to mark every old node as
freshly retrieved and silently exempt whole graphs from cleanup. Worth saying in
the commit message: the first `reflect` after this lands will have more to say
than usual on an existing graph.

**Frontend.** `NodeView` carries the field to the browser (`events.py:51`,
`frontend/src/types.ts:65`), where record-time marks draw `created_at →
last_reinforced` as the span over which a node stayed relevant
(`timeline-model.ts:259-275`). The null case already behaves correctly — `end` is
already conditional, and the docstring already says "a node never reinforced
since creation is a plain point" — so this is a type change plus `parseTime`
tolerating `null`, and it makes an existing intent literal rather than
incidental.

**Failing test first.**

- `tests/pipelines/test_reflection.py` — a node whose `retrieved_at` is `None` is
  treated as never retrieved, with no tolerance window in play. Today the
  equivalent case passes only because two clock reads land a millisecond apart.
- `tests/mcp/test_tools.py` — a judgment sets `importance_judged_at` and leaves
  `retrieved_at` alone; `search` reinforcement sets `retrieved_at` and leaves
  `importance_judged_at` alone. The two clocks being independent is the whole
  point of the split, and nothing asserts it today.
- `tests/core/test_types.py` — a fresh `ValueSignal` has both timestamps `None`,
  and one parsed from a dict missing both keys does too. That second case is the
  migration.
- `frontend/src/timeline-model.test.ts` — a node with `retrieved_at: null`
  renders as a point, not a zero-length span and not a crash.

**Also touches.** `SUMMARY.md:129,221,281`, `README.md:219`,
`REVIEW_EPISTEMIC.md` §12.1–12.2 where the signal is enumerated,
`epimemer_prompts/DEFAULT.md`, and the five Python test modules plus three
frontend ones that name the field.

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

**#14 steps 1–2 are what is left**, and they are no longer speculative: with the
pairwise arithmetic vectorized, `reflect` on SurrealDB is round-trip bound and
fails at ~2,000 nodes. #39, #42 and #43 are done and unmerged — the value model
now moves in both directions, and a judgment nobody revisits stops protecting a
node.

Work that does not exist yet, as opposed to work that is wrong, lives in
`dev-docs/PROPOSED_FEATURES.md`.

**#14 and #16 stay deferred**, with stated triggers — but #14's grounds weakened
when #39 landed. Vectorizing the contradiction phase left `reflect` on SurrealDB
round-trip bound, so "nothing fails because of it" no longer holds there.
`dev-docs/BENCHMARKS.md` has the numbers; #14 above has the analysis.

What to pick up, and what has to be true first:

| Order | Work | Trigger |
|---|---|---|
| ✅ | 39 (reflect's O(F²)) | Done, unmerged. Vectorized; the in-memory crossing moved ~2,970 → ~6,700. Step 2 (cutting the candidate set) proved unnecessary and was not done |
| ✅ | 43 (value vocabulary & judgment timestamp) | Done, unmerged. `retrieved_at` + `importance_judged_at`, both nullable; the tolerance window is gone |
| ✅ | 42 (importance moves one way only) | Done, unmerged. `judge_importance` carries a direction, and nomination reads the judgment clock so an unrevisited assessment stops protecting a node |
| 1 | 14 steps 1–2 (batched edge fetch, aggregate queries) | **Ready now.** `reflect` on SurrealDB crosses 30 s at ~2,000 nodes and the cause is this issue's N+1 pattern. A protocol change on both backends per the parity rule; step 1 helps every N+1 site at once |
| deferred | 16 | The server gains concurrent clients (the viz-read leg is closed by the hub; the fix is now scoped to `hub_client.py`) |
| deferred | 14 step 3 | `asyncio.gather` on per-node enrichment — blocked by #16's shared-connection hazard, and the only part of #14 that is |
