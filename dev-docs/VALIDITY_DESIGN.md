# Temporal validity — the design

*Facts have no validity interval, so the graph cannot say **when** a claim was
true.* That was the largest gap this system ever had, and this document is the
design that closed it, built 2026-08-19.

It lives here rather than in the issue tracker because it is a statement of
what the model **is**, not a task: `docs/VALIDITY.md` describes the behaviour a
caller sees, `dev-docs/REVIEW_EPISTEMIC.md` §13 records the review that shaped
it, and both send a reader here for the arguments and the rejected
alternatives. The three decisions are lettered T1, T2 and T3 throughout the
codebase.

- **T1 — what a validity interval is and where it lives.** Validity is carried
  on the **`sourced_from` edge**, per source, measured against a named
  **timeline**, with endpoints that distinguish *unknown* from *unbounded*, and
  it is read back per source with no default collapse.
- **T2 — which mechanism owns a world-change.** Status and intervals answer
  different questions, so there is no forced choice; the split is in the
  **edge**. A correction writes `superseded_by` and is terminal; a world-change
  writes `temporally_followed_by` and is reversible, because recurrence
  falsifies *replaced* but not *came after*.
- **T3 — the retrieval surface and the naming.** History is returned by default
  with lineage collapse; corrections are reachable but off by default;
  valid-time queries return **buckets** rather than a filter, because a filter
  turns missing metadata into a silent false negative; and `as_of` became
  `graph_as_of`, reserving `valid_as_of`.

---

## What construction settled

Seven rules that the T1–T3 sections below imply but do not state, each fixed
while the model was being built and each load-bearing since.

**Comparison concludes only what cannot be otherwise.** Unknown and named
endpoints withhold, and `unknown` is the majority answer by design. Two
refinements keep it from withholding where it actually knows: an unbounded
endpoint settles a comparison even against an unlocated one — every moment of a
non-empty interval falls after the beginning of time and before the end of it,
so a claim asserted to have always held overlaps a period nobody has dated — and
a witness point can only ever *add* an overlap, never an ordering, since a
witness bounds an endpoint from the inside and an inside bound cannot show that
a period stops before a moment.

**A self-contradictory interval is refused at construction**, on *definite*
violations only: a start at or after its end, or a witness its own endpoints
exclude. That is a construction error rather than a source disagreeing with
itself — no document says *"as of 1990, Labour governed 1997–2010"* — and left
standing it would let the comparison derive an overlap from a premise that
cannot hold. Unknown endpoints never trip it, which keeps it from becoming a
check on unknown. Naive datetimes are read as UTC once, at construction, because
a hand-typed historical date is naive far more often than not, and mixing it
with an aware one raises from inside a comparison rather than answering.

**Only a `sourced_from` edge may carry an interval.** On a `similarity` or
`tagged_with` edge it would be a period attributed to nobody, which is the
node-level set this design rejected, reintroduced through a side door.

**`instant_kind` is read in exactly two files** — `core/temporal.py`, which
defines it, and the ingest guidance, which has to name the shapes an agent
writes. A structural test fails the moment a third appears, which is what keeps
*"adding a kind is a known, small change"* true rather than aspirational.

**Both seed routes take a `statuses` set**, not a singular status. While
`text_search` took one and `vector_search` took a set, the lexical half of a
hybrid search was the half that could not see historical nodes.

**Publication dates never close a period.** A publication date bounds when a
claim was *asserted*, never when the previous one stopped holding, so closing
Leningrad's period at a 2000 gazetteer would have the graph assert the city was
called Leningrad in 1995. Over-claiming is the one direction this design never
takes — which is why the worked example for boundary proposals yields no
proposal from two undated documents. What reflect reading two documents does buy
is real: a 1991 date stated in the second lands on the first document's fact,
which no single-document ingest could ever do.

**Accepting a boundary is what unblocks the soundness check.** While a period is
open, nothing can be concluded about it and its successor together, so the check
is blocked on exactly the pairs it most wants to see. Closing the period is what
lets disjointness fire. The two halves are one loop: the agent judges a
succession, reflect proposes the boundary it implies, and the check can then see
an inference that spans it. It costs about 10% of a `reflect` call, linear —
measured in `BENCHMARKS.md`.

---

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
6. **Corroboration inflates** — two claims about different periods read as
   agreeing about one.
7. **Fact dedup cannot be made safe**, which is where this started.

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
  or create a second node identical to the first, which is the duplication fact dedup
  exists to prevent.
- **It also breaks a lineage chain (c).** Two claims that alternate would need
  `succeeded_by` edges in both directions between the same pair — a cycle, and
  meaningless as lineage.
- **It makes dedup easier rather than harder, reversing the earlier outlook.**
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
> reader which keeps this from being a field nobody consumes — the trap a field with no reader always is.
>
> **Still open below**: this does not solve recurrence. A claim that becomes
> true *again* has nowhere to say so, which is what the interval-set model is
> for. One further limitation found while building it and deliberately left
> alone: `supersede_node_tx` migrated the old node's edges onto the replacement,
> which is right for a correction and wrong for a world-change — the historical
> node should keep its own provenance. It was thought to need the validity model
> first; it did not. Fixed as **edge migration on retirement**, before this
> entry is built.
> *(Review 2026-08-12: the waiting judgment is reversed for the interim floor —
> filed as **edge migration on retirement**. Migration is a move, not a copy, so the cost of waiting
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
>    duplicate that dedup will later be invited to merge with the current claim.
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
> Separately actionable: **edge migration on retirement** — world-change supersession
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

Provenance is where per-source `confidence` is already going, for the same
reason: a value describing what *this source says* must not outlive the source.

Two things then follow rather than being chosen:

- **Intervals survive merges for free.** Merging migrates edges, so per-source
  validity rides along with no combination rule to invent — the property that
  made per-source support the right call.
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
which. That is dedup's state-versus-event distinction arriving by another road,
and it is why no collapse is safe by default. A caller wanting one answer
supplies its own rule.

Costs, stated rather than discovered: comparison is O(sources_A × sources_B),
fine at one to three sources each and worth watching; and every consumer handles
a set rather than an answer. Taken anyway, because a collapse is easy to add and
near-impossible to remove once callers depend on it — and because a default
collapse is the "one number condensing too much" that the confidence prior already rejected.

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
| 1 — which mechanism owns a world-change | **Open — this is T2**, and it decides how edges migrate on retirement |

##### What T1 changes elsewhere

**Edge migration gets a stronger argument and a different fix.** Validity lives on
`sourced_from`, and `supersede_node_tx` **moves** those edges to the replacement
— so a world-change supersession strips the historical node not merely of its
provenance but of **its validity intervals**, which are the only thing making it
"true of its period". The case is no longer "it cannot say who asserted it" but
"it cannot say *what period*". Ordering is unchanged; the fix must be written
with intervals in mind.

**The confidence prior gains a consistency check.** Per-source confidence and per-source validity
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
the duplication dedup exists to prevent, manufactured by our own supersession.

> **Correction (2026-08-12, second pass).** The mechanism above misstates the
> code: `get_node_by_content` is called in exactly three places, all
> `node_type=TOPIC` (`tools.py:164`, `:283`, `:884`) — **no fact path looks
> content up at all**, so the duplicate is manufactured by the dedup gap whether or
> not supersession happened. And exact match would barely help if it were
> wired: two documents almost never phrase a claim identically (dedup's own
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
verbatim-match floor, but it is the minor half.

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
The counterpart id rides on the episode.

**And a category guard: cyclical facts never route through this machinery at
all.** "The Christmas holiday period" is a recurrence *rule* — it never stops
being true, so it never retires, never restores, and enumerating its
occurrences as validity intervals is the wrong representation even though T1's
lists could hold them. That is the `CyclicalTimeline` case
(`PROPOSED_FEATURES.md` → *Specialized timelines*). An agent marking such a
fact `HISTORICAL` in January is making a category error; individual
occurrences ("Christmas 2025 in Berlin") are *event* facts, which per dedup's
amendment are never interval-unioned into the rule.

##### What T2 unblocks

**Edge migration's shape is settled and it is no longer blocked** — and it was then built
(2026-08-12). A world-change goes through `temporally_followed_by`; the
historical node keeps its own `sourced_from` edges and therefore its validity
intervals, and the replacement gets **none of them**. Both blanket answers were
withdrawn — copying everything fabricates attribution, migrating nothing drops
`has_metacontext` and moves a fiction-frame replacement into base reality.
Migration is **per edge type**, and the table is now the code:
`migration_disposition(edge_type, status)` in `epimemer/core/types.py`.

**The content-lookup scan gains a second caller.** The `get_node_by_content` path must now consider
`HISTORICAL` twins as well as `ACTIVE` ones, so the scan that indexing fixed for
performance is about to grow a second reason to be touched. Do them together
rather than visiting that path twice.

**Review item 1 is closed.** Item 5's retrieval half and the `as_of` question
remain, and are T3.

---

#### ✅ T3 decided (2026-08-12) — the retrieval surface and the naming

The review's item 5, in two halves: `HISTORICAL` has no reader at retrieval, and
`as_of` will be misread once valid time exists. **This closes the design.**

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

This is the only piece of this design with a migration cost: a public MCP tool name plus
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

**Review item 5, and with it the design in full.** All six review findings are
now answered: 2, 4 and 6 by T1; 1 by T2; 3 across T1 and T2; 5 here. What
remains is construction, not decision.

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
- The soundness check collapses per-premise sources by **existential union**:
  two premises whose interval sets share no intersecting pair are flagged, and
  adding one wider source interval that bridges them **clears** the flag;
- when only the historical node matches a query (asked in its period's
  vocabulary — "Leningrad"), it holds its own result slot; lineage collapse
  merges only when both match;
- a `temporally_followed_by` cycle terminates every walker, and two
  same-direction transitions between one pair coexist as two edges.

---

