# Valid time: when a claim was true

Saint Petersburg was Petrograd, then Leningrad, then Saint Petersburg again,
and each name was true in its period. Epimemer records when each claim held,
so a succession like that is filed neither as a contradiction nor as a
correction, and an inference that combines claims never true at the same time
is caught.

Design notes: `dev-docs/VALIDITY_DESIGN.md` and `dev-docs/REVIEW_EPISTEMIC.md` §13.

---

## 1. Two clocks, named apart

| Axis | Question | Where it lives |
|---|---|---|
| **Transaction time** | *When did the graph learn this?* | `created_at`, `superseded_at`, `lifecycle`, read by `graph_as_of` and `query_changes` |
| **Valid time** | *When was this true?* | validity intervals on `sourced_from` edges, read by `search(valid_as_of=…)` |

Each tool names its clock in its argument, so *"as of 1980"* is never read on
the wrong axis.

---

## 2. Validity lives on the provenance edge, per source

A claim with two sources has **two periods**, and they are kept apart:

```
fact ──sourced_from[validity: [interval, …]]──> document A
     └─sourced_from[validity: [interval, …]]──> document B
```

Nothing unions them onto the node or collapses them at read time: a union of
a careful source and a sloppy one yields a period neither claims, and an
intersection of two episodes yields "never". So `search` returns `validity` as
a list of `(source, intervals)` pairs and the caller decides. Only a
`sourced_from` edge may carry validity.

`RawDocument` also carries an optional **`published_at`**, when the document
was published, as against `created_at`, when it was ingested. It never falls
back to `created_at`.

---

## 3. What an interval is

A `ValidityInterval` carries:

| Field | Meaning |
|---|---|
| `start`, `end` | imprecise instants, see below |
| `timeline_id` | which clock this is measured against |
| `witnessed_at` | a moment the source actually attests the claim held |
| `basis` | `stated` or `inferred` |

Intervals are **half-open**, and a self-contradictory one (starting at or
after it ends, or witnessed outside its own endpoints) is refused rather than
stored.

### Endpoints distinguish *unknown* from *unbounded*

| Kind | Meaning |
|---|---|
| `precise` | a date the source gives |
| `named` | a label the source gives: "during the Renaissance" |
| `unknown` | the source does not say when this began or ended |
| `unbounded` | the source says it has *always* held, or holds still |

*"We don't know when it started"* and *"it had no start"* are different claims.

### Timelines, not metacontexts

Validity is measured against a named **timeline**. A fictional date and a real
one are not comparable, and there is no conversion between them. A timeline
carries its own `reference_time`, that clock's *now*, so *"is this claim
current?"* is asked against the relevant timeline rather than the wall clock.

### `basis`: stated or inferred

`stated` means the document gave the date. `inferred` means it was worked
out, from tense or context or from two documents read together.

**A date the agent knows from world knowledge and the document does not give
is neither, and must not be supplied at all**: it would be attributed to a
source that never said it.

### Comparison

`compare_intervals` answers four values, never two:

`before` · `after` · `overlap` · **`unknown`**

Two periods that cannot be placed relative to each other, because an endpoint
is unknown or because they are on different clocks, give `unknown`, and every
consumer treats it as *we cannot tell* rather than as false.

---

## 4. Only ingest can supply intervals

Epimemer makes no LLM calls, so the calling agent supplies validity at
`store_decomposition`, per node: tense and the dates written in the text are
visible at ingest and nowhere afterwards.

Everything downstream **reads**, with two exceptions that write an endpoint
without reading the text: `apply_reflection(boundaries=[…])` fills one that
is still **open**, from a succession two documents imply together (§7); and
`correct_interval` replaces one that is **present and wrong** (§7.1).

---

## 5. The world changing is not the same as being wrong

The lifecycle splits on which of the two happened:

| | **Correction** | **World-change** |
|---|---|---|
| The old claim was | wrong | right, and remains right of its period |
| Node status | `corrected` | `historical` |
| Lineage edge | `superseded_by` | `temporally_followed_by` |
| Reversible? | no, terminal | **yes** |
| Returned by default `search`? | no | yes |
| Eligible for archival by age? | yes | no |
| Keeps its own provenance? | no, moved to the replacement | yes |

The caller must say which: `because` is required on `update` and
`supersede_by`, with no default, because filing a change in the world as an
error is how a graph forgets its own history.

**The succession edge never claims adjacency.** Saint Petersburg to Petrograd
to Leningrad to Saint Petersburg is three separately observed transitions, so
cycles and parallel same-direction edges are legal and are never deduplicated.

### Recurrence

This section is about **lifecycle recurrence**: a retired claim becomes true
again, and `restore` puts it back with the `recurs` verdict. A **recurrence
rule**, "the second Tuesday of every month", is a pattern on a timeline; see
[TIMELINES.md](TIMELINES.md).

`historical` is restorable and `corrected` is not.

- Similarity nomination sees historical candidates, which is what makes the
  **`recurs`** verdict reachable.
- `check_conflicts` returns each candidate's status, because telling
  `redundant` from `recurs` *is* that distinction.
- `reflect` reports mixed active/historical pairs under `recurrences`, apart
  from `contradictions`: a claim beside its own successor is not in conflict
  with it.
- `restore(node_ids=…, sourced_from=…)` reactivates the node **and writes the
  new source's `sourced_from` edge in one transaction**. Without
  `sourced_from` it refuses, since the graph would state a claim it cannot
  attribute.

### Lifecycle episodes

A node can leave the active set more than once, so its history is an
**append-only list** of episodes. `query_changes` reads them, which is why a
node that retired, returned and retired again reports three events.

---

## 6. Reading it back

Retrieval is covered in
[RETRIEVAL.md](RETRIEVAL.md#7-valid-time-answers-in-groups-never-as-a-filter).
In short:

- Results carry `validity`, per source, uncollapsed.
- `valid_as_of` answers in **two buckets**, `valid` and `unknown`, and
  excludes nothing.
- The dashboard's timeline panel draws each source's periods in a lane of its
  own; the marks are `dev-docs/TIMELINE_VISUALISATION.md` §13, with a visual
  reference at `dev-docs/mockups/valid-time-grammar.html`.

---

## 7. What reflect does with validity

The phases that read intervals **propose and never write**; see
[REFLECTION.md](REFLECTION.md).

### The soundness check

An inference is listed under `unsound_inferences` when **no source puts its
premises in the same period**: `compare_intervals` returns `before` or `after`
for every pair of their stated periods. It reports the offending pairs and
their dates, not a verdict, and it never blocks.

- It is **silent whenever a pair cannot be placed.** `unknown` never flags,
  so an undated graph produces no findings.
- Where a premise has several sources, their periods are **unioned per
  premise** before comparison, the one collapse permitted anywhere here. An
  over-wide source can suppress a flag, never manufacture one.

It runs at reflect because the two premises usually come from different
documents, and only reflect sees both.

### The same check, asked about a merge that has not happened

`merge_inferences` makes one node resting on the **union** of the sources'
premises, so the same check is asked before the merge: reflect's
`inference_merge_candidates` and the merge's own response carry it as a
warning. After the merge it cannot be recovered, because the `derived_from`
edges have moved. The usual answer is to narrow the merged claim's wording or
period, which is why it warns rather than refuses.

### Boundary proposals

Reflect's `boundary_proposals` cover what **two documents say together**:

> Document 1: *"the city is called Leningrad."*
> Document 2: *"the city has been called Saint Petersburg since 1991."*

The first document leaves its period open. Only something seeing both can
close it. Three rules:

1. **The succession edge is the licence.** A proposal is drawn only from a
   `temporally_followed_by` edge, the agent's recorded verdict that the world
   moved on. Reflect never judges succession itself, and `superseded_by`
   licenses nothing: a claim that was never true has no period to close.
2. **Only a date some document actually gives is proposed**: the successor's
   own located start moved across the edge, or the predecessor's own located
   end. Where a side holds several periods, the boundary nearest the handover
   is used.
3. **Publication dates are never used.** A document published in 2000 bounds
   when its claim was *asserted*, never when the previous one stopped holding.

So two undated documents yield **no proposal**.

Every proposal is `inferred`, and `apply_reflection(boundaries=[…])` is the
only thing that writes one. It re-derives which interval is meant from the
graph as it stands and **refuses** when the request no longer names exactly one
open period; refusals come back in `boundaries_refused` with a reason.

A proposal you disagree with is declined through
`apply_reflection(boundaries_declined=[…])`, with the proposal's identity and
a required `reason`. The period stays open, and one `boundary_declined`
journal row records the claim, the source, the endpoint, the date and the
clock. That stops exactly that proposal being offered again, by `reflect` and
on the timeline panel; a proposal for another date or the other endpoint is a
different question and is still offered. The decline is a journal row rather
than an edge, so the two claims can still be nominated as a similar or
contradictory pair. `reopen(boundary={…})` withdraws a decline, and the
proposal returns carrying the reopening's reason.

### 7.1 Correcting a period that is present and wrong

`boundary_proposals` fills an endpoint that is **open**. A recorded date that
was *misread* (a republication date taken for the original, a tense read the
wrong way) is corrected with `correct_interval(node_id, source_id, intervals,
because)`.

**It is not a supersession**: the claim is unchanged, nothing is retired and
no lineage moves. It is a tool of its own rather than a field on `rejudge`
because an interval belongs to a (node, source) pair, not to a node
(`dev-docs/REVIEW_MODE.md` §6.5).

**The whole list for that (node, source) pair is replaced**, since an interval
has no id of its own. An empty list is allowed, and is how a period that was
invented outright comes off.

**`basis` stays yours to state per interval**, unlike an accepted boundary,
which is forced to `inferred`: a correction often restores what the document
actually said.

Refused for a blank `because`, a missing node, a source not named by exactly
one `sourced_from` edge from that node, or a replacement identical to what is
there. The prior list is kept in the edge's `interval_corrections` trail.
Corroboration reads intervals, so that trail and the journal row's timestamp
bound which answers a wrong interval affected.

---

## 8. Known limits

- **`basis` is per interval.** Accepting a proposed boundary marks the whole
  interval `inferred`, so a start the document *stated* stops being
  reportable as stated. Leaving it `stated` would have a source appear to
  assert a date no document gave.
- **Deduplication merges states and never events.** Identical claims over
  disjoint periods are one node with several intervals, and the intervals
  survive a merge because they live on the `sourced_from` edges. But *"Labour
  is in government"* in 1997 and 2024 is one state, while *"Labour won the
  election"* in 1997 and 2024 is two events, and the sentences alone cannot
  tell them apart. The judgment is made at ingest and stored as
  `Fact.claim_kind`; a fact ingested without one never merges.
