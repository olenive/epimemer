# Temporal validity: the design

The graph records **when** a claim was true, per source, so that a claim that
was right of its period is never filed as an error and an inference cannot
silently combine claims that were never simultaneously true. `docs/VALIDITY.md`
describes the behaviour a caller sees; this document holds the model and the
reasons. Three decisions are lettered T1, T2 and T3 throughout the codebase:

- **T1, what a validity interval is and where it lives.** Validity is carried
  on the **`sourced_from` edge**, per source, measured against a named
  **timeline**, with endpoints that distinguish *unknown* from *unbounded*,
  and it is read back per source with no default collapse.
- **T2, which mechanism owns a world-change.** Status and intervals answer
  different questions, so there is no forced choice; the split is in the
  **edge**. A correction writes `superseded_by` and is terminal; a
  world-change writes `temporally_followed_by` and is reversible, because
  recurrence falsifies *replaced* but not *came after*.
- **T3, the retrieval surface and the naming.** History is returned by
  default with lineage collapse; corrections are reachable but off by
  default; valid-time queries return **buckets** rather than a filter,
  because a filter turns missing metadata into a silent false negative; and
  transaction-time lookup is named `graph_as_of`, and the bucketed valid-time
  query is `search(valid_as_of=…)`.

---

## 1. The problem: the Saint Petersburg case

The city was Saint Petersburg, then Petrograd (1914), then Leningrad (1924),
then Saint Petersburg again (1991). Every one of those was true. Ingest *"the
city is called Leningrad"*, later ingest *"the city is called Saint
Petersburg"*, and a graph without validity has exactly two things it can do
with the pair, both wrong: treat them as a contradiction (neither is false),
or supersede one by the other (which marks a historical truth as though it
had been an error).

Content time does not help. `propose_timepoints` builds `TIMELINK` edges from
dates *named in the text*, and mention time differs from validity routinely:
*"Napoleon was born in 1769"* mentions 1769 and is valid always; *"the city is
called Leningrad"* mentions nothing and is valid 1924 to 1991. The case that
needs a date is exactly the case that gets none. And `graph_as_of` is
transaction time, *what did the graph hold then*, never *what was true then*.

Without validity, supersession conflates *we were wrong* with *the world
moved*, so the graph forgets history by filing it as error; contradiction
detection is unsound in both directions; an inference can combine claims
that were never simultaneously true and nothing detects it; search returns
period-bound claims as current; corroboration inflates across periods; and
fact dedup cannot be made safe.

**Validity is a set of intervals, not an interval.** *"The Labour Party is in
government"* holds over 1945–51, 1964–70, 1974–79, 1997–2010 and 2024–, and
the gaps are as real as the spans. That constraint decides the design:
supersession cannot model change, because supersede "Labour is in
government" in 1951 and the same claim becomes true again in 1964 with
nowhere to say so; a lineage chain cannot either, because two alternating
claims would need edges in both directions between the same pair; and dedup
becomes easier, because "Labour is in government" ingested in 1997 and again
in 2024 is one claim with two intervals.

---

## 2. T1: the validity model

Vocabulary first, because it is most of the fix. This is **valid time**, when
a claim was true. `created_at`, `superseded_at` and `graph_as_of` are
**transaction time**, what the graph held and when. The two axes blur
silently once code exists, so the words are fixed before the fields. The
prior art (Snodgrass; XTDB, Datomic; SQL:2011) is worth reading before
inventing.

### 2.1 What carries validity

Facts and inferences. Topics carry none, for a conceptual reason rather than
a structural one: validity is a property of a *claim*, and a topic is a
subject, not an assertion. "The BBC" is timeless; *"the BBC was founded in
1922"* is the fact that holds the date. (A structural rule, *no source, no
validity*, would have been wrong: topics extracted from text do carry a
`source_id`.)

Inferences carry validity twice: asserted from their own source, and derived
from their premises. The mismatch is what the soundness check reads (§2.11).

### 2.2 Where it lives: on the `sourced_from` edge

A node-level set has to union what its sources assert, and union takes one
careful source and one sloppy one and produces a period neither claims. The
error mode of the combination rule is exactly the quantity being recorded,
the same failure as a false dedup manufacturing corroboration. Provenance is
where per-source confidence lives for the same reason: a value describing
what *this source says* must not outlive the source.

Two things follow. Intervals survive merges for free, since merging migrates
edges and per-source validity rides along with no combination rule to
invent. And one edge per (node, document) carries a **list**, because a
single source can assert several disjoint periods.

Only a `sourced_from` edge may carry an interval. On a `similarity` or
`tagged_with_topic` edge it would be a period attributed to nobody, the
node-level set reintroduced through a side door.

### 2.3 Reading it back: per source, no default collapse

A query answers with `(source, interval)` pairs. Fact *"Labour was in
government"*, two sources: **A** (2011 almanac) 1997–2010, **B** (sloppy
blog) 1995–2010. Ask about 1996. Union says yes, wrong, and the bad source
widened the answer where the good one cannot narrow it. Intersection says
no, right here; now let A say 1997–2010 and B say 2024–present, both correct,
different episodes, and intersection yields *never in government*. Union
breaks when sources disagree about the same episode; intersection breaks
when they describe different episodes; nothing in the data says which. A
caller wanting one answer supplies its own rule.

Comparison is O(sources_A × sources_B), fine at one to three sources each,
and every consumer handles a set rather than an answer. Taken anyway, because
a collapse is easy to add and near-impossible to remove once callers depend
on it.

### 2.4 The interval type: three endpoint states

An endpoint is **point** (a concrete instant, `precise`, or a free-text
label, `named`), **unknown** (there is a boundary; we do not know where), or
**unbounded** (there is no boundary). `core/temporal.py` defines them as a
discriminated union on `instant_kind`.

`unknown` and `unbounded` being different values is the load-bearing part.
*"The city is named Placeberg"* has an unknown start; assuming either
"always" or "recently" is a fabrication. *"Water is H₂O"* is unbounded.
Collapsing them reproduces the empty-set ambiguity one level down.

This is why `Timepoint` is not reused: its `start: datetime | None` means
both, and changing it would alter what existing timeline data means.
`Timepoint` stays mention time. One fact routinely has both: *"the 1991
renaming"* mentions 1991 while *"the city is called Leningrad"* is true until
1991.

**Labels are stored and never silently resolved.** A label is what the source
said; a bound is what can be computed. "During the Renaissance" stays text
and contributes *unknown* to any comparison unless someone resolves it
explicitly.

**No consumer branches on the endpoint kind.** Everything downstream asks the
comparison question and consumes the answer, so adding a kind touches one
module. `instant_kind` is read in exactly two files, `core/temporal.py` and
the ingest guidance that has to name the shapes an agent writes; a
structural test fails the moment a third appears. The imprecise-instant type
is named generally rather than for validity, because `published_at` uses it
too (§2.7).

**A self-contradictory interval is refused at construction**, on definite
violations only: a start at or after its end, or a witness its own endpoints
exclude. No document says *"as of 1990, Labour governed 1997–2010"*; that is
a construction error, and left standing it would let the comparison derive an
overlap from a premise that cannot hold. Unknown endpoints never trip it.
Naive datetimes are read as UTC once, at construction, because a hand-typed
historical date is naive far more often than not.

### 2.5 The clock: keyed by timeline, not by metacontext

Each interval names the **timeline** it is measured against. The value is
carried on the edge; only the clock is a reference, so a missing timeline
leaves the interval fully readable and merely incomparable across clocks.
Timeline rather than metacontext because the two axes cross in both
directions: a change to the history of a fictional universe has a place on
the real timeline (when the revision was published) and on the in-universe
timeline (when the events happen), and competing accounts of real history
both run on CE dates and should compare directly. Real-world facts use the
default wall-clock timeline (`reference_time = None`), so the common case
needs no decision from any caller.

**Cross-clock comparison returns `unknown`, never `disjoint`.** An inference
drawn across an in-universe fact and a real-world fact is temporally
uncheckable, which is itself worth surfacing.

### 2.6 Open world

An interval says what a source **asserts**. It asserts nothing about the
outside, so a moment outside every stated interval is **unknown**, not false.
Closed-world assertions ("Labour governed *only* during…") would need their
own marking; none exists.

This bounds the soundness check: nothing can prove two claims were *never*
simultaneously true. What the check can honestly say is *no source asserts
them true at a common moment*, which fires only when both facts have
intervals and those intervals do not intersect.

### 2.7 `published_at`, and the witness point

`RawDocument.created_at` is ingestion time. Using it as evidence about when a
claim held is transaction time wearing valid time's clothes: a 1970 memoir
read today carries `created_at = 2026`. So `RawDocument` has an optional
`published_at`, in the imprecise-instant type, so a document dated "1990" is
not stored as midnight on 1 January 1990. **There is no fallback to
`created_at`.** No `published_at` means no witness point; a fallback would
have every undated document claim its facts were witnessed on the day it
happened to be ingested, and a graph rebuilt next year would say something
else.

**Intervals carry their own optional witness point**: "this interval is
asserted to contain T". Not redundant with `published_at`: with three
endpoint states there is no way to express "contains 1990", and without it
two undated facts never provably overlap and the soundness check never
fires. The two come apart in both directions, which is why a boolean
`asserted_as_current` was rejected: an undated document saying "as of March
1990…" has a witness and no `published_at`; a diary published in 1990
recounting 1985 in the present tense has both, differing. One witness per
interval; a source asserting a claim at two separated moments writes two
intervals.

**Publication dates never close a period.** A publication date bounds when a
claim was *asserted*, never when the previous one stopped holding, so
closing Leningrad's period at a 2000 gazetteer would have the graph assert
the city was called Leningrad in 1995. Over-claiming is the one direction
this design never takes.

### 2.8 Provenance of the interval: stated, inferred, never invented

**The agent is not a source.** Models hallucinate, and a hallucinated interval
is indistinguishable from a documented one once stored. Accepted consequence:
the Leningrad case cannot get its 1991 boundary from agent world knowledge. A
1970 document yields "called Leningrad, witnessed 1970, end unknown" and the
graph never learns about 1991 unless a document says so, or unless a second
document lets reflect propose the boundary (§2.9). Coverage is bounded by
what is ingested, not by what the model knows.

But judging **tense** (whether *"the city is called X"* leaves an interval
open) is already reading rather than copying, so every interval is marked
`stated` (dates present in the text) or `inferred` (from tense, context, or
two sources jointly), and world knowledge is forbidden rather than marked. A
caller can filter to stated-only, which is what makes the discipline
auditable.

### 2.9 When it is written: ingest extracts, reflect proposes

Ingest is where the source text is present, and two things are visible only
there: tense, and dates stated in the text. Reflect has extracted facts and a
graph, not a document.

Reflect is not optional, because the motivating case is invisible at ingest.
Document 1 (1970): *"the city is called Leningrad."* Document 2 (2000): *"the
city is called Saint Petersburg."* Neither states an end date. Only reflect,
seeing both, can propose that the first interval closes before the second
opens. Such a boundary is *inferred* and surfaces as a **proposal for
review** (`apply_reflection(boundaries=…)`), never written silently. Two
undated documents yield no proposal; what reflect does buy is that a 1991
date stated in the second document lands on the first document's fact,
which no single-document ingest could ever do.

Accepting a boundary is what unblocks the soundness check: while a period is
open, nothing can be concluded about it and its successor together. The
agent judges a succession, reflect proposes the boundary it implies, and the
check can then see an inference that spans it. The soundness check and the boundary proposals each cost about 10% of a
`reflect` call, linear (`BENCHMARKS.md`).

### 2.10 Comparison: four values, `unknown` among them

`TemporalRelation` is `before | after | overlap | unknown`. `unknown` is its
own value and never a probability: *no information about the ordering* and
*genuinely even odds* are different claims. If distributions ever land, a
probability rides on the three known answers and never stands in for the
fourth.

This is a deliberate simplification of Allen's thirteen interval relations.
Four suffice for the soundness check, and the richer relations are derivable
from the endpoints on demand.

**Comparison concludes only what cannot be otherwise.** Unknown and named
endpoints withhold, and `unknown` is the majority answer by design. Two
refinements keep it from withholding where it actually knows: an unbounded
endpoint settles a comparison even against an unlocated one, since every
moment of a non-empty interval falls after the beginning of time and before
the end of it, so a claim asserted to have always held overlaps a period
nobody has dated; and a witness point can only ever *add* an overlap, never
an ordering, since a witness bounds an endpoint from the inside and an
inside bound cannot show that a period stops before a moment.

### 2.11 The soundness check: flags, never blocks

The graph makes no inferences; the agent does and the graph stores them. So
this is a check over stored inferences, run at reflect: an inference whose
premises' asserted intervals do not intersect is flagged as unsound. It
**flags and never blocks**, since `unknown` is the common outcome and a
blocking check on unknown is unusable, and it **never fires on unknown**,
only on non-intersecting asserted intervals (§2.6).

With per-source validity, "the premises' asserted intervals" needs a
collapse rule, and it is the one collapse §2.3 permits: the **existential
union** per premise, the set of moments *some* source asserts it true, with
the check firing only when no pair of asserted intervals across the two
premises intersects. Its error direction is safe: a sloppy, over-wide source
can only suppress a flag, never manufacture one. Intersection instead would
produce false flags, the dangerous direction, which is why the rule is
written down and pinned by a test.

---

## 3. T2: which mechanism owns a world-change

Two mechanisms could describe a world-change: node replacement (old node
`HISTORICAL`, new node, an edge between them) or an interval closing on a
node that stays `ACTIVE`. If both survived as alternatives, the agent facing
a world-change would have to choose between them at mechanism grain.

**They are not alternatives.** They answer different questions, both happen,
and the agent's only judgment stays the correction-versus-world-change call
that `because` asks for.

| | Asks | Nature |
|---|---|---|
| **Validity intervals** | *When was this true?* | A claim about the **world**. Source-attributed, sparse, open-world; the agent may not invent them (§2.8) |
| **`NodeStatus`** | *Is this the graph's current answer?* | A claim about the **graph**. Always present, closed by construction, and legitimately the agent's to set |

That split also answers an objection: marking a node `HISTORICAL` does not
violate "the agent is not a source". That rule governs assertions about the
world; a status is bookkeeping about the graph's own answer, the same tier as
`archived`. And the two can disagree in a checkable way: an interval closed
in 1991 on a node still marked current is a defect the graph can find in
itself.

### 3.1 Why status cannot be derived from intervals

The attractive alternative was pure intervals: derive *not current* as "no
interval contains now". Under T1 endpoints are commonly `unknown` and
semantics are open-world, so an interval `(unknown, unknown)` *might* contain
now, the derivation returns `unknown` for very nearly every node, and a
retrieval filter answering "unknown" about almost everything is not a filter.
The property that makes T1 correct, never asserting a boundary nobody
stated, is exactly what stops a status being read off the intervals. A
derived field is only as available as the data it derives from, and a model
built to admit ignorance propagates that ignorance into everything computed
from it.

### 3.2 The split is in the edge

`superseded_by` claims *replacement*, and recurrence falsifies replacement:
Labour returning to government does not make the 2010 retirement
retroactively wrong, it makes "replaced" the wrong word for what happened.

| Event | `because` | Edge | Status left behind | Reversible |
|---|---|---|---|---|
| We were wrong | `it_was_wrong` | `superseded_by` | `CORRECTED` | No, terminal |
| The world moved | `the_world_changed` | `temporally_followed_by` | `HISTORICAL` | Yes |

`because` is required, with no default, spelled as sentences because the
caller is a language model and the sentences *are* the judgment.
`NodeStatus.SUPERSEDED` survives as a legacy value for rows written before
the split, since they genuinely do not record which kind they were and
inventing an answer would be a lie; every reader uses `SUPERSEDED_STATUSES`
rather than an equality against one member.

`temporally_followed_by` states temporal order rather than replacement, so
it survives recurrence: the Saint Petersburg claim temporally followed the
Leningrad one in 1991, and the Leningrad claim becoming current again would
not contradict that.

**The edge does not claim adjacency.** Saint Petersburg → Petrograd →
Leningrad → Saint Petersburg: if the edge meant *next*, discovering the
Petrograd step later would make an existing edge wrong and force a rewire.
So the edge records one observed transition and says nothing about what may
lie between. The chain is walkable but not guaranteed gapless, and ordering
comes from validity intervals where they exist rather than from edge
adjacency.

**Cycles and repeated transitions are legal, and every reader must know.**
Saint Petersburg's chain returns to its own node, and *"Labour is in
government"* → *"the Conservatives are in government"* recurs in the same
direction in 1951, 1970, 1979 and 2010. Every `temporally_followed_by` walk
must be cycle-safe; "follow to the terminal successor" does not terminate on
this graph. Parallel edges between one pair are legal, one per observed
transition, and nothing may dedup them by `(src, dst, type)`.

The edge is in `HISTORY_EDGE_TYPES`: it is lineage rather than knowledge, and
if it migrated on a later supersession it would detach from the transition
it records.

**On the name.** `succeeded_by` was rejected because `SUCCEEDED_BY` and
`SUPERSEDED_BY` are near-homographs that mean opposite things: three letters
apart, mid-word, in the same enum, and a misread in review produces no
signal. `followed_by` alone was rejected because this graph also carries
`based_on` and `implies`, so a bare "A followed by B" invites a causal
reading.

### 3.3 Recurrence

A claim retired `HISTORICAL` and asserted again by a later document is
**one** node, reactivated, not a second node with identical content.

- **`HISTORICAL` is restorable; `CORRECTED` is not.** `restore` accepts
  `HISTORICAL` and refuses `CORRECTED`, which is the literal meaning of its
  rule that restoring must not resurrect a node that was superseded for
  being wrong.
- **Recurrence is seen through similarity nomination, not content lookup.**
  Two documents almost never phrase a claim identically, so a verbatim check
  catches only verbatim recurrence (`store_decomposition` still reports those
  as `historical_twins`, the cheap floor). The candidate pass
  (`check_conflicts` at ingest, the `reflect` sweep as safety net) includes
  `HISTORICAL` nodes, and candidates carry their `status`, because an agent
  cannot otherwise tell an active twin from a historical one, which is the
  entire basis for choosing between `redundant` and `recurs`.
- **`vector_search` takes `statuses: frozenset[NodeStatus]`**, defaulting to
  active only, on the protocol and both backends. Both seed routes take the
  set, not a singular status; while `text_search` took one, the lexical half
  of a hybrid search was the half that could not see historical nodes. A
  second retired-only lookup method was rejected because it would duplicate
  the SurrealDB over-fetch machinery, the only difficult part of that adapter.
- **`recurs` is a verdict of its own.** `redundant` assumes an active twin
  and `succeeds` assumes a different claim following; `recurs` means *the same
  claim, previously retired `HISTORICAL`, asserted true again*. Its action is
  reactivation: `restore` to `ACTIVE` plus a new `sourced_from` edge carrying
  the new document's interval, in one transaction, because a node back to
  `ACTIVE` with no edge recording why is a claim the graph asserts and cannot
  attribute. The prior intervals and the `temporally_followed_by` record are
  untouched, so the node ends holding several disjoint intervals.
  Reactivation stays an explicit act; flipping a node live behind the
  caller's back on a string match is too brittle to do silently.
- **Restoration must not overwrite lifecycle history.** A scalar
  `(superseded_at, status)` cannot represent *retired, then came back*, so
  each node carries an append-only lifecycle episode list and `restore`
  appends an episode rather than mutating one (`EVENT_LOG.md` §6).

**Cyclical facts never route through this machinery.** "The Christmas
holiday period" is a recurrence *rule*: it never stops being true, so it
never retires, never restores, and enumerating its occurrences as validity
intervals is the wrong representation even though the lists could hold them.
That is the `CyclicalTimeline` case (`PROPOSED_FEATURES.md`). Individual
occurrences ("Christmas 2025 in Berlin") are event facts, which are never
interval-unioned into the rule.

### 3.4 Edge migration on retirement

A world-change goes through `temporally_followed_by`; the historical node
keeps its own `sourced_from` edges and therefore its validity intervals, and
the replacement gets none of them. Both blanket answers were rejected:
copying everything fabricates attribution, migrating nothing drops
`has_metacontext` and moves a fiction-metacontext replacement into base reality.
Migration is per edge type, and the table is the code:
`migration_disposition(edge_type, status)` in `core/types.py`. Archival
excludes `HISTORICAL`: a node retired because the world changed is still true
of its period, so ageing it out would be the same defect one level down.

---

## 4. T3: the retrieval surface and the naming

### 4.1 Buckets, not a filter

The obvious surface is a valid-time filter, `search(..., valid_at="1980")`.
T1 makes it dishonest by the mechanism that killed derived status in T2:
validity is sparse and open-world, so *unknown* has to go somewhere and both
destinations lie. Exclude it and claims that may well have held in 1980
vanish; include it and the parameter is not filtering. Since most nodes will
never carry intervals, exclusion is the default failure, and the caller
reasonably concludes *the graph does not know* when it holds the Leningrad
fact and merely lacks a date. **A filter converts missing metadata into a
false negative, silently.**

The rule, stated once: **wherever open-world data meets a boolean question,
the answer has three values, and squeezing it into two is where the lie
enters.** Derived status (T2), interval comparison (§2.10) and retrieval are
three instances.

So `search(valid_as_of=…)` answers with groups: **provably valid at `t`**
(intervals cover it), **unknown** (the claim is held; its validity cannot be
determined), and excluded (provably not valid). Nothing is dropped silently
and the shape of the ignorance is visible. This is `before | after | overlap
| unknown` applied at retrieval rather than between two intervals.

### 4.2 Reachability: two parameters, the asymmetry in the defaults

| Parameter | Default | Why |
|---|---|---|
| `include_historical` | **on** | Knowledge that is not current is still knowledge, the reason `HISTORICAL` exists |
| `include_corrected` | **off** | Kept for the audit trail rather than for reading; re-offering a claim concluded false should be deliberate |

Making `CORRECTED` unreachable entirely was rejected. It contradicts the
principle applied everywhere else here (per-source confidence, no default
collapse, buckets over filters: report and let the caller decide), and
*"what did we believe about X that turned out wrong?"* is a legitimate
question for an epistemic memory, unanswerable if you must already know the
node id and walk `superseded_by`. Retrieval is the third consumer of the
`CORRECTED`/`HISTORICAL` split, after archival exclusion and `restore`, which
keeps the distinction from being one only its writer cares about. Both seed
routes take their status semantics from this surface, never from their own
defaults.

### 4.3 Default-on requires lineage collapse

A historical claim and its replacement are near-identical text, so both
score near the top, and a claim with four historical predecessors would fill
half a top-10 with versions of one thing. So when a historical node and its
successor both match, the successor takes the slot and the historical node
attaches to it; the `temporally_followed_by` chain is what makes this
computable, and the walk is cycle-safe (§3.2). When only the historical node
matches (asked in its period's vocabulary, "Leningrad"), it holds its own
slot.

### 4.4 `graph_as_of` and `valid_as_of`

SQL:2011 writes `FOR SYSTEM_TIME AS OF` and `FOR APPLICATION_TIME AS OF`,
prefixing the phrase in both cases because "as of" alone does not say which
clock. The unmarked name inherits the default reading, and in a knowledge
graph the default reading of "as of 1980" is *what was true in 1980*, the
wrong axis. So transaction-time lookup is `graph_as_of`, and the bucketed query is
`search(valid_as_of=…)`. The standard-matching alternative
(`system_time_as_of` / `valid_time_as_of`) is more canonical and more to type.

### 4.5 "Current" is timeline-relative

T1 keys intervals by timeline, and `Timeline.reference_time` is that
timeline's *now*. So *is this claim current?* must be asked against the
relevant clock: a claim in a fictional metacontext is current when its interval
contains that timeline's reference time, not wall-clock now. The first
implementation of any "is it current" reader will reach for
`datetime.now()`, which is why this is written down.

---

## 5. Rejected

- **Two nullable datetimes on the node** (`valid_from` / `valid_to`). A single
  pair cannot express a disjoint set, and it needed `None` to mean both
  "unknown" and "always".
- **Validity `Timepoint`s attached by a new edge type.** Close to the design
  that was built, but `Timepoint.start: datetime | None` collapses unknown
  and unbounded, and `TIMELINK` is a weak reference that resolves to an empty
  row rather than an error, too weak for something inference soundness
  depends on.
- **A `succeeded_by` lineage relation as the whole answer.** Recurrence makes
  cycles, and a lineage chain cannot hold them; its cheap half survived as
  the status split.
- **Deriving status from intervals** (§3.1).
- **A node-level validity set** (§2.2).
- **A default collapse rule** (§2.3).
- **A boolean `asserted_as_current`** (§2.7).
- **A fallback from `created_at` to `published_at`** (§2.7).
- **A valid-time filter** (§4.1).
- **`CORRECTED` unreachable from search** (§4.2).
- **A second retired-only lookup method** (§3.3).

---

## 6. Tests

`tests/pipelines/test_validity.py`, `test_supersession_kind.py` and the
storage parity suite pin, among others:

- a fact superseded because the world changed stays retrievable as
  true-of-its-period, and is distinguishable from one superseded because it
  was wrong;
- a claim that holds over two disjoint periods is one node carrying both;
- an inference whose premises' asserted intervals do not intersect is
  flagged, and one whose premises are merely `unknown` is not; adding one
  wider source interval that bridges them clears the flag;
- a claim with no intervals is distinguishable from one asserted always-true;
- overlap against a label-only interval reports `unknown`, not disjoint;
- `unknown` and `unbounded` endpoints are distinguishable through storage and
  back;
- two sources asserting different periods for one fact are readable
  separately;
- intervals measured on different timelines compare as `unknown`;
- a document with no `published_at` yields no witness point;
- `inferred` and `stated` are distinguishable, and a caller can filter to
  stated-only;
- validity edges survive a node merge with their attribution intact;
- a `Topic` has nowhere to put an interval;
- a world-change writes `temporally_followed_by`, a correction writes
  `superseded_by`, and neither writes the other;
- a `HISTORICAL` node can be restored; a `CORRECTED` node cannot;
- similarity nomination surfaces a `HISTORICAL` twin as a candidate for a
  paraphrased re-assertion;
- a `recurs` resolution reactivates the twin and attaches the new source's
  edge and interval, leaving the prior intervals and the
  `temporally_followed_by` record untouched;
- `temporally_followed_by` does not migrate on a later supersession; a cycle
  terminates every walker; two same-direction transitions between one pair
  coexist as two edges;
- a `HISTORICAL` node is returned by a default `search`, a `CORRECTED` node is
  not, and both are reachable when their parameter is set;
- when a historical node and its successor both match, the result holds one
  slot with the historical node attached;
- `graph_as_of` answers about transaction time, and nothing named `as_of`
  remains to be misread.
