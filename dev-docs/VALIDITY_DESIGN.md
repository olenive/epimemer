# Temporal validity: the design

The model is `epimemer/core/temporal.py`, read back by
`epimemer/pipelines/query/validity.py` and checked at reflect by
`epimemer/pipelines/reflection/soundness.py` and `boundaries.py`.
`docs/VALIDITY.md` describes the behaviour a caller sees; this page holds the
decisions a change has to keep. Code cites three decisions as T1, T2 and T3,
and cites T1's parts as `T1 §N`, which is §2.N here:

- **T1, what a validity interval is and where it lives**: on the
  `sourced_from` edge, per source, measured against a named timeline, with
  endpoints that distinguish *unknown* from *unbounded*, read back per source
  with no default collapse.
- **T2, which mechanism owns a world change**: the split is in the edge. A
  correction writes `superseded_by` and is terminal; a world change writes
  `temporally_followed_by` and is reversible, because recurrence falsifies
  *replaced* but not *came after*.
- **T3, the retrieval surface**: history returned by default with lineage
  collapse, corrections reachable but off by default, valid-time queries
  answered in buckets rather than filtered, and the two clocks named
  `graph_as_of` and `search(valid_as_of=…)`.

---

## 1. The problem

Leningrad and Saint Petersburg were each the city's true name in their period.
Without validity, a graph can only call the pair a contradiction or file one as
an error, and an inference can combine claims never true at the same time.
Mention time (`TIMELINK` timepoints from dates in the text) does not help,
because the claim that needs a period usually names no date, and
`graph_as_of` is transaction time.

**Validity is a set of intervals, not an interval.** *"Labour is in
government"* holds over five separate spans, and the gaps are as real as the
spans. That rules out modelling change by supersession or a lineage chain
alone, and makes dedup easier: the claim ingested in 1997 and in 2024 is one
claim with two intervals.

---

## 2. T1: the validity model

This is **valid time**, when a claim was true. `created_at`, `superseded_at`
and `graph_as_of` are **transaction time**. The words are fixed before the
fields, because the two axes blur silently once code exists.

### 2.1 What carries validity

Facts and inferences. Topics carry none, because a topic is a subject, not an
assertion; a rule of *no source, no validity* would be wrong, since extracted
topics carry a `source_id`. Inferences carry validity twice, asserted by their
own source and derived from their premises, and the soundness check reads the
mismatch (§2.11).

### 2.2 Where it lives: on the `sourced_from` edge

A node-level set would have to union its sources, and the union of a careful
source and a sloppy one is a period neither claims. On the edge, intervals
survive merges with no combination rule, and one edge per (node, document)
carries a list, because one source can assert several disjoint periods. Only a
`sourced_from` edge may carry an interval; on any other edge it would be a
period attributed to nobody.

### 2.3 Reading it back: per source, no default collapse

A query answers with `(source, interval)` pairs. Union breaks when sources
disagree about one episode; intersection breaks when they describe different
episodes; nothing in the data says which, so a caller wanting one answer
supplies its own rule. Every consumer handles a set, because a collapse is easy
to add and near-impossible to remove once callers depend on it.

### 2.4 The interval type: three endpoint states

An endpoint is a **point** (`precise` instant or `named` label),
**unknown**, or **unbounded**, a discriminated union on `instant_kind`.

- **`unknown` and `unbounded` differ**, because assuming *always* or
  *recently* for an unknown start is a fabrication. `Timepoint` is not reused,
  because its `start: datetime | None` means both; it stays mention time.
- **Labels are stored and never silently resolved**: *"during the
  Renaissance"* contributes *unknown* to any comparison.
- **No consumer branches on the endpoint kind**, so adding a kind touches one
  module. A test in `tests/core/test_temporal.py` holds the Python readers of
  `instant_kind` to `core/temporal.py` and the ingest guidance in
  `mcp/server.py`; the dashboard also reads it, to give each kind its mark.
  The imprecise-instant type is named generally because `published_at` uses
  it too (§2.7).
- **A self-contradictory interval is refused at construction**, on definite
  violations only (a start at or after its end, a witness its endpoints
  exclude), because it would let comparison derive an overlap from an
  impossible premise. Naive datetimes are read as UTC once, at construction.

### 2.5 The clock: keyed by timeline, not by metacontext

Each interval names the timeline it is measured against; a missing timeline
leaves the interval readable and merely incomparable across clocks. Timeline
rather than metacontext, because the axes cross: a revision to a fictional
history has a real publication date and an in-universe date, and competing
accounts of real history share CE dates. Real-world facts use the default
wall-clock timeline, so the common case needs no decision. **Cross-clock
comparison returns `unknown`, never `disjoint`.**

### 2.6 Open world

An interval says what a source asserts and nothing about the outside, so a
moment outside every interval is unknown, not false. No closed-world marking
exists. So the soundness check can only say *no source asserts them true at a
common moment*, and fires only when both claims have intervals that do not
intersect.

### 2.7 `published_at`, and the witness point

`RawDocument.created_at` is ingestion time, so `published_at` is a separate,
optional imprecise instant with **no fallback to `created_at`**, because a
fallback would date every undated document's claims to its ingest day.

Each interval carries an optional witness point (*this interval contains T*),
because with three endpoint states nothing else can say *contains 1990*, and
without it two undated claims never provably overlap. It is separate from
`published_at` because the two differ in both directions (an undated text
saying "as of March 1990"; a 1990 diary recounting 1985). One witness per
interval.

**Publication dates never close a period**: a date bounds when a claim was
asserted, never when the previous one stopped holding. Over-claiming is the
direction this design never takes.

### 2.8 Provenance of the interval: stated, inferred, never invented

**The agent is not a source**: a hallucinated interval is indistinguishable
from a documented one once stored. So coverage is bounded by what is ingested,
and a boundary the documents do not give arrives only through reflect (§2.9).
Judging tense is reading, so every interval is marked `stated` or `inferred`,
and world knowledge is forbidden rather than marked; the basis is what makes
the discipline auditable.

### 2.9 When it is written: ingest extracts, reflect proposes

Tense and stated dates are visible only at ingest. Reflect is still needed,
because a succession across two documents is invisible to either: only reflect
can propose that the first interval closes before the second opens. Such a
boundary is `inferred` and is a proposal (`apply_reflection(boundaries=…)`),
never written silently; accepting it is what lets the soundness check see an
inference that spans the change.

A declined proposal (`boundaries_declined`) is a journal row keyed on the
question (claim and source as subjects; endpoint, date and clock in `covers`),
never an `assessed` edge, because the two claims are often also nominated as a
similar or contradictory pair and the edge would silence those too.

### 2.10 Comparison: four values, `unknown` among them

`TemporalRelation` is `before | after | overlap | unknown`. `unknown` is never
a probability, because *no information* and *even odds* are different claims;
four values suffice for the soundness check, and Allen's richer relations are
derivable on demand.

Comparison concludes only what cannot be otherwise, and `unknown` is the
majority answer. An unbounded endpoint settles a comparison even against an
unlocated one, and a witness point can add an overlap but never an ordering,
since it bounds an endpoint from the inside.

### 2.11 The soundness check: flags, never blocks

An inference whose premises' asserted intervals do not intersect is flagged at
reflect, never blocked (since `unknown` is common) and never on `unknown`. Per
premise, the check uses the **existential union** of its sources, the one
collapse §2.3 permits, because an over-wide source can then only suppress a
flag, never manufacture one; intersection would produce false flags. A test
pins the rule.

---

## 3. T2: which mechanism owns a world change

Status and intervals are not alternatives; they answer different questions,
and the agent's only judgment is the correction-or-world-change call `because`
asks for.

| | Asks | Nature |
|---|---|---|
| **Validity intervals** | *When was this true?* | about the world: source-attributed, sparse, open-world; the agent may not invent them (§2.8) |
| **`NodeStatus`** | *Is this the graph's current answer?* | about the graph: always present, and the agent's to set |

Marking a node `HISTORICAL` is therefore not the agent acting as a source.

### 3.1 Why status cannot be derived from intervals

With mostly `unknown` endpoints and open-world semantics, *no interval
contains now* answers `unknown` for nearly every node, which is not a status.

### 3.2 The split is in the edge

| Event | `because` | Edge | Status left behind | Reversible |
|---|---|---|---|---|
| We were wrong | `it_was_wrong` | `superseded_by` | `CORRECTED` | No |
| The world moved | `the_world_changed` | `temporally_followed_by` | `HISTORICAL` | Yes |

`because` is required with no default, spelled as sentences because the
sentences are the judgment. `NodeStatus.SUPERSEDED` survives for rows written
before the split, which do not record which kind they were, so every reader
uses `SUPERSEDED_STATUSES` rather than equality against one member.

- **The edge does not claim adjacency**: it records one observed transition,
  so learning of an intermediate step later never makes an edge wrong.
  Ordering comes from intervals.
- **Cycles and repeated transitions are legal.** Every `temporally_followed_by`
  walk must be cycle-safe, and parallel edges between one pair, one per
  transition, must never be deduplicated by `(src, dst, type)`.
- **The edge is in `HISTORY_EDGE_TYPES`**, so it never migrates and detaches
  from the transition it records.
- **The name**: `succeeded_by` is three letters from `superseded_by` with the
  opposite meaning, and a bare `followed_by` invites a causal reading beside
  `based_on` and `implies`.

### 3.3 Recurrence

A `HISTORICAL` claim asserted again is one node, reactivated
(`docs/VALIDITY.md`, *Recurrence*, has the behaviour).

- **`restore` accepts `HISTORICAL` and refuses `CORRECTED`**, so a claim
  retired for being wrong never comes back.
- **Recurrence is found by similarity nomination**, which includes `HISTORICAL`
  and carries each candidate's `status`, because restatements are rarely
  verbatim. `store_decomposition`'s `historical_twins` is only the verbatim
  floor.
- **`vector_search` and `text_search` take `statuses: frozenset[NodeStatus]`**,
  defaulting to active only, so neither half of a hybrid search is blind to
  historical nodes; a separate retired-only lookup would duplicate SurrealDB's
  over-fetch machinery.
- **`recurs` restores and writes the new `sourced_from` edge in one
  transaction**, because an active node with no edge saying why is a claim the
  graph cannot attribute. Earlier intervals and the `temporally_followed_by`
  record stay. Reactivation is always explicit.
- **Restoration appends a lifecycle episode** rather than overwriting one,
  because a scalar cannot say *retired, then came back* (`EVENT_LOG.md`).

**Cyclical facts never use this machinery.** *"The Christmas holiday period"*
never stops being true, so it is a recurrence rule on a timeline
(`TIMELINES.md` §2.4), and its occurrences are event facts never unioned into
the rule.

### 3.4 Edge migration on retirement

On a world change the historical node keeps its `sourced_from` edges and
intervals, and the replacement gets none. Migration is per edge type, in
`migration_disposition(edge_type, status)` in `core/types.py`, because copying
everything fabricates attribution and migrating nothing drops
`has_metacontext`. Archival excludes `HISTORICAL`, since such a node is still
true of its period.

---

## 4. T3: the retrieval surface and the naming

### 4.1 Buckets, not a filter

A valid-time filter would have to drop or keep *unknown*, and both lie;
dropping is the silent default failure, since most nodes carry no intervals.
**Wherever open-world data meets a boolean question, the answer needs an
*unknown*.** So `search(valid_as_of=…)` answers in two groups, *valid* and
*unknown*, and drops nothing. There is no *provably not valid* group, because
nothing can fill it under §2.6; it would arrive with a closed-world marking.

### 4.2 Reachability: two parameters, the asymmetry in the defaults

| Parameter | Default | Why |
|---|---|---|
| `include_historical` | on | knowledge that is not current is still knowledge |
| `include_corrected` | off | kept for the audit trail; re-offering a claim concluded false should be deliberate |

`CORRECTED` stays reachable, because *what did we believe that turned out
wrong?* is a legitimate question. Both seed routes take their status semantics
from this surface, never from their own defaults.

### 4.3 Default-on requires lineage collapse

A historical claim and its replacement score near each other, so when both
match, the successor takes the slot and the historical node attaches to it,
through a cycle-safe walk of `temporally_followed_by` (§3.2). A historical node
matched alone keeps its own slot.

### 4.4 `graph_as_of` and `valid_as_of`

"As of" alone does not say which clock, and in a knowledge graph it reads as
valid time. So transaction-time lookup is `graph_as_of` and the bucketed query
is `search(valid_as_of=…)`.

### 4.5 "Current" is timeline-relative

`Timeline.reference_time` is a timeline's *now*. Nothing in the server answers
*is it current* on its own; a caller asks `search(valid_as_of=…,
timeline_id=…)` with that timeline's reference time. Any reader that does
answer it must resolve the timeline's clock, never `datetime.now()`.

---

## 5. Rejected

- **Two nullable datetimes on the node**: one pair cannot express a disjoint
  set, and `None` would mean both unknown and always.
- **Validity as `Timepoint`s on a new edge type**: `Timepoint` collapses
  unknown and unbounded, and `TIMELINK` resolves a dangling reference to an
  empty row rather than an error, too weak for what soundness depends on.
- **A lineage relation as the whole answer**: recurrence makes cycles a chain
  cannot hold.
