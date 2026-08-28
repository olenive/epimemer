# Valid time — when a claim was true

Saint Petersburg was Petrograd was Leningrad was Saint Petersburg. Every one of
those was true. A memory system that can only record such a pair as a
*contradiction* or a *correction* is wrong in both directions, and the damage is
not cosmetic: it files historical truth as error, it removes it from the active
set, and — the reason this outranked everything else in the backlog — **it lets
an inference combine claims that were never simultaneously true, with nothing to
detect it.**

This page is the model that fixes it. Design history and the arguments that were
rejected: `dev-docs/VALIDITY_DESIGN.md` and `dev-docs/REVIEW_EPISTEMIC.md` §13.

---

## 1. Two clocks, named apart

| Axis | Question | Where it lives |
|---|---|---|
| **Transaction time** | *When did the graph learn this?* | `created_at`, `superseded_at`, `lifecycle`, read by `graph_as_of` and `query_changes` |
| **Valid time** | *When was this true?* | validity intervals on `sourced_from` edges, read by `search(valid_as_of=…)` |

Both names are marked on both sides. A bare `as_of` would inherit the default
reading, and in a knowledge graph *"as of 1980"* reads as *what was true then* —
the wrong axis. This is the design's one migration cost, and it was paid while
only one axis existed.

---

## 2. Validity lives on the provenance edge, per source

Not on the node. A claim with two sources has **two periods**, and they are kept
apart:

```
fact ──sourced_from[validity: [interval, …]]──> document A
     └─sourced_from[validity: [interval, …]]──> document B
```

Nothing is ever unioned onto the node, and nothing collapses at read time either.
The reason is that both collapses lie in a way nobody could later detect:

- **Union** takes one careful source and one sloppy one and yields a period
  *neither* claims.
- **Intersection** takes two separate episodes and yields "never".

So `search` returns `validity` as a list of `(source, intervals)` pairs and lets
the caller decide. Only a `sourced_from` edge may carry validity at all.

Alongside it, `RawDocument` carries an optional **`published_at`** — when the
document was published, as against `created_at`, which is when it was ingested.
It never falls back to `created_at`, because a fabricated publication date is
worse than a missing one.

---

## 3. What an interval is

A `ValidityInterval` carries:

| Field | Meaning |
|---|---|
| `start`, `end` | imprecise instants — see below |
| `timeline_id` | which clock this is measured against |
| `witnessed_at` | a moment the source actually attests the claim held |
| `basis` | `stated` or `inferred` |

Intervals are **half-open**, and a self-contradictory one (starting at or after
it ends) is refused at construction rather than stored.

### Endpoints distinguish *unknown* from *unbounded*

Four kinds, and the first two are routinely confused in systems that only have a
nullable datetime:

| Kind | Meaning |
|---|---|
| `precise` | a date the source gives |
| `named` | a label the source gives — "during the Renaissance" |
| `unknown` | the source does not say when this began or ended |
| `unbounded` | the source says it has *always* held, or holds still |

*"We don't know when it started"* and *"it had no start"* are different claims,
and a single `NULL` cannot tell them apart.

### Timelines, not metacontexts

Validity is measured against a named **timeline**. A fictional date and a real one
are not comparable, and there is no conversion between them, so `compare_intervals`
simply refuses to place periods on different clocks against each other. A timeline
also carries its own `reference_time` — that clock's *now* — which is why
*"is this claim current?"* must be asked against the relevant timeline rather than
against `datetime.now()`.

### `basis` — stated or inferred

`stated` means the document gave the date. `inferred` means it was worked out.

**A date the agent knows from world knowledge and the document does not give is
neither, and must not be supplied at all.** That is the whole content of the rule:
provenance means the graph can say who asserted what, and a date smuggled in from
the agent's own knowledge is attributed to a source that never said it.

`basis` is per **interval**, not per endpoint. That is a real, accepted cost —
accepting an inferred boundary on one end makes a stated start unreportable as
stated (§7). Per-endpoint basis is the recorded future option.

### Comparison

`compare_intervals` answers four values, never two:

`before` · `after` · `overlap` · **`unknown`**

The fourth is the point. Two periods that cannot be placed relative to each other
— because an endpoint is unknown, or because they are on different clocks — give
`unknown`, and every consumer must handle it as *we cannot tell* rather than
folding it into a false.

---

## 4. Only ingest can supply intervals

Epimemer makes no LLM calls, so the calling agent supplies validity at
`store_decomposition`, per node. It has to be there: tense and the dates written
in the text are visible at ingest and nowhere afterwards.

Everything downstream **reads**, with two exceptions that write an endpoint
without reading the text: `apply_reflection(boundaries=[…])` fills one that is
still **open**, from a succession two documents imply together (§7); and
`correct_interval` replaces one that is **present and wrong** (§7.1). Nothing
infers a date from the text later, because the text is gone by then.

---

## 5. The world changing is not the same as being wrong

The lifecycle splits on which of the two happened, and the split runs through the
status, the edge, and the edge migration together:

| | **Correction** | **World-change** |
|---|---|---|
| The old claim was | wrong | right, and remains right of its period |
| Node status | `corrected` | `historical` |
| Lineage edge | `superseded_by` | `temporally_followed_by` |
| Reversible? | no — terminal | **yes** |
| Returned by default `search`? | no | yes |
| Eligible for archival by age? | yes | no |
| Keeps its own provenance? | no — moved to the replacement | yes |

The caller must say which; there is no default, because filing a change in the
world as an error is how a graph forgets its own history. `because` is required
on `supersede_by`, and `lineage_edge_type_for(status)` pairs with
`superseded_status_for(because)` so the node and the edge cannot disagree.

**The succession edge never claims adjacency.** Saint Petersburg → Petrograd →
Leningrad → Saint Petersburg is three separately observed transitions, so cycles
and parallel same-direction edges are legal — and nothing may deduplicate them by
`(src, dst, type)`. Every walker over this edge type must therefore be cycle-safe.

### Recurrence

Because a world-change is reversible, a claim can become true again. `historical`
is restorable and `corrected` is not.

- Similarity nomination sees historical candidates, which is what makes the
  **`recurs`** verdict reachable at all — the guard saying retired nodes must
  never resurface was also what hid the twin.
- `check_conflicts` returns each candidate's status, because telling `redundant`
  from `recurs` *is* that distinction.
- `reflect` reports mixed active/historical pairs under `recurrences`, apart from
  `contradictions`: a claim beside its own successor is not in conflict with it.
- `restore` reactivates the named node **and writes the new source's
  `sourced_from` edge in one transaction**. Without naming that source it
  refuses — a claim back to active with no edge saying who asserts it is one the
  graph states and cannot attribute.

### Lifecycle episodes

`(status, superseded_at)` is a single slot, and a node can now leave the active
set more than once. The pair cannot express *retired, then came back*: clear
`superseded_at` on the return and the retirement vanishes from every time window;
keep it and the retirement reports the node's current status, which by then is
`active`.

So the history is an **append-only list** of episodes — `retired_at`, `because`,
`counterpart`, `restored_at` — with `(status, superseded_at)` kept on the node as
the current-state snapshot the fast paths read. `query_changes` reads the
episodes, which is why a node that retired, returned and retired again reports
three events rather than one.

---

## 6. Reading it back

Retrieval is covered in [RETRIEVAL.md](RETRIEVAL.md#7-valid-time-answers-in-groups-never-as-a-filter).
The short form:

- Results carry `validity`, per source, uncollapsed.
- `valid_as_of` answers in **two buckets** — `valid` and `unknown` — and excludes
  nothing.

There is deliberately no third bucket. T3's design named one (*provably not
valid*), and the open-world rule leaves it **unreachable**: an interval asserts
what a source says and nothing about the outside, so no moment is provably *not*
valid without a closed-world marking nobody has proposed. A value nothing can
produce would earn a dead branch in every caller.

---

## 7. What reflect does with validity

Three phases read intervals. All **propose and never write**; see
[REFLECTION.md](REFLECTION.md).

### The soundness check

An inference is flagged when **no source puts its premises in the same period** —
`compare_intervals` returns `before` or `after` for every pair of their stated
periods. Reported with the offending pairs and their dates, not as a verdict.

Two properties make it a check on evidence rather than on ignorance:

- It is **silent whenever a pair cannot be placed.** `unknown` never flags. An
  undated graph produces no findings at all, which is correct — nothing was
  learned about it.
- Where a premise has several sources, their periods are **unioned per premise**
  before comparison. That is the one collapse permitted anywhere in this design,
  and its error direction is the safe one: an over-wide source suppresses a flag,
  never manufactures one.

It flags; it never blocks. Ingest cannot do this job — the motivating case spans
two documents, neither of which is in front of the agent while the other is being
stored.

### The same check, asked about a merge that has not happened

`merge_inferences` collapses two derivations into one node resting on the
**union** of their premises. So the same question — do these premises fall clear
of each other? — can be asked of a survivor that does not exist yet, and it is:
`inference_merge_candidates` carries the answer as an advisory, and so does the
merge's own response.

The two are one computation with one implementation, and the reason for going to
the trouble is that the finding is only recoverable beforehand. Once the merge
lands, the `derived_from` edges have migrated and nothing distinguishes *these
premises arrived from two inferences* from *this one was drawn on both*.

It is an advisory rather than a refusal because the honest response is usually to
narrow the merged claim's wording or period — which the agent does by writing
content. Refusing would block a merge it could have fixed.

### Boundary proposals

Ingest extracts what one document says. Reflect proposes what **two documents say
together**, and the motivating case is structurally invisible at ingest:

> Document 1: *"the city is called Leningrad."*
> Document 2: *"the city has been called Saint Petersburg since 1991."*

The first document cannot know its claim will ever stop being true, so the first
period is left open. Only something seeing both can close it.

Three rules keep this honest:

1. **The succession edge is the licence.** A proposal is drawn from a
   `temporally_followed_by` edge — the agent's recorded verdict that the world
   moved from one claim to the next. Reflect never judges succession itself.
   `superseded_by` licenses nothing here: a claim that was never true has no
   period to close.
2. **Only a date some document actually gives is ever proposed** — the
   successor's own located start moved across the edge, or the predecessor's own
   located end. Where a side holds several periods, the boundary nearest the
   handover is used.
3. **Publication dates are never used.** A document published in 2000 bounds when
   its claim was *asserted*, never when the previous one stopped holding, and
   closing Leningrad's period at 2000 would have the graph assert the city was
   called Leningrad in 1995.

A consequence worth stating plainly: §9's own worked example — two documents,
neither carrying a date — yields **no proposal**, and that is the honest outcome.
What the feature buys is real anyway: a date from the second document lands on
the first document's fact, which no single-document ingest could do.

Every proposal is `inferred`, nothing is written by the proposing pass, and
`apply_reflection(boundaries=[…])` is the only thing that writes. It re-derives
which interval is meant from the graph as it stands and **refuses** rather than
guesses when the request no longer names exactly one open period — several means
ambiguous, none means already answered. Refusals come back with a reason, because
a boundary silently not applied is worse than one rejected out loud.

### 7.1 Correcting a period that is present and wrong

`boundary_proposals` fills an endpoint that is **open**. Nothing derives that a
date already recorded was *misread* — a republication date taken for the
original, a tense read the wrong way — so correcting one is a separate act on
separate evidence, and it is `correct_interval(node_id, source_id, intervals,
because)`.

**It is not a supersession.** The claim is unchanged and the world has not moved,
so `because` in the `update` sense has no honest value; this is the same category
as a mislabelled `claim_kind`, and `dev-docs/VALIDITY_DESIGN.md` is where the split
between it and `rejudge` is argued. Nothing is retired and no lineage moves.

**The whole list for that (node, source) pair is replaced**, because an interval
is a position in a list on one `sourced_from` edge and has no id of its own.
Supplying an empty list is allowed, and is how a period that was invented outright
comes off — refusing that would leave a fabricated interval unremovable.

**`basis` stays yours to state per interval**, unlike an accepted boundary, which
is forced to `inferred`. A correction is often restoring what the document
actually said, and calling that inferred would understate it.

Refused where the graph cannot back the change: a blank `because`, no node, a
source no `sourced_from` edge names, or a replacement identical to what is already
there — a restatement is not a revision. The prior list is kept in the edge's
`interval_corrections` trail, which matters because corroboration reads intervals
to decide whether a look-alike witnesses the same period or is the neighbouring
truth: a wrong interval has been moving counts for as long as it stood, and the
trail plus the journal row's timestamp is what bounds which answers were affected.

---

## 8. Known limits

- **`basis` is per interval.** Accepting a proposed boundary marks the whole
  interval `inferred`, so a start the document *stated* stops being reportable as
  stated. The alternative — leaving it `stated` — would have a source appear to
  assert a date no document gave, which is the one thing the rule exists to
  prevent. Under-claiming is the safe direction.
- **Fact deduplication is built**, and
  this model is what made it safe: identical claims recurring over disjoint
  periods are one node with several intervals, and because validity lives on the
  `sourced_from` edge the intervals survive a merge with no combination rule to
  invent. But it dedupes **states** and never **events** — *"Labour is in
  government"* in 1997 and 2024 is one state whose intervals union; *"Labour won
  the election"* in 1997 and 2024 is two events, and merging them would fabricate
  one victory spanning both. The two sentences are near-identical, so nothing
  computed from them separates the cases: the judgment is made at ingest, where
  the document is still readable, and stored as `Fact.claim_kind`. A fact
  ingested without one never merges, which is the whole corpus written before
  that date.
- **Valid-time rendering is designed and not built.** The timeline panel does not
  yet draw intervals; the grammar is `dev-docs/TIMELINE_VISUALISATION.md` §13,
  with a checked-in visual reference at `dev-docs/mockups/valid-time-grammar.html`.
