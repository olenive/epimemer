# Relation labels: a vocabulary with a record

A design, built except label deprecation (§5): the code is `epimemer/pipelines/reflection/relation_verdicts.py`, `relation_consolidation.py` and the label tools in `epimemer/mcp/tools.py`.

Each user-tier relationship label (`works_for`, `published_by`) has a record:
something to describe, something a decision can name, and something a decline
can be recorded against. Labels are never rewritten; a vocabulary converges
through descriptions and verdicts, not merges. `docs/REFLECTION.md` describes
`relation_verdicts` for a caller.

---

## 1. The problem

### 1.1 A label needs a record

`list_relations` derives the vocabulary by scanning the edges of active nodes
and grouping by `(label, kind)`. Without a record there is nothing to
describe, so an agent choosing a label sees words and counts and no way to
learn what *this graph* means by each; nothing to name in a decision, so a
journal row about a pair of labels has no subject; and nothing to change but
the edges, so any rename would be an irreversible bulk rewrite.

### 1.2 What labels affect, and what they do not

**Labels do not affect retrieval.** Expansion reads an edge's type and kind,
never its label (`traversal_excluded`), and outside `list_relations`, `link`
and the engine-tier `published_by` label corroboration reads, no query
pipeline reads a user-tier label. So consolidating two labels changes a string
that gets printed and nothing about which nodes come back. That is a different
class of operation from a fact merge, which destroys a node and moves
corroboration counts, and the two should not share machinery.

**Relation nominations are rare on real graphs.** The largest real graph held
one user-tier label for its first months. A nomination needs two same-`kind`
labels at 0.9 cosine or above (`reflect`'s `relation_similarity_threshold`).
That is either evidence the problem does not occur, or evidence that nothing
encourages `link` with a user relation so the vocabulary never grows enough to
need consolidating; the two readings point opposite ways and have not been
distinguished.

**Near-identical strings can mean opposite things.** The user's example: a
servant *works for* a master in a culture with no employment relation, while
elsewhere in the same universe a corporation formally *employs* an on-call
consultant who does very little work. The nominator sees only strings.

### 1.3 The decline treadmill

The relation sweep re-derives from scratch on every `reflect`. Without a
record of declines, a pair an agent considered and declined would be
re-nominated next session, to a different agent, for ever. The `assessed` edge
closes exactly this for fact pairs. It cannot serve here because it runs
between two nodes, and `works_for` and `employed_by` are not nodes. §4 is the
equivalent for labels.

---

## 2. The record

### 2.1 The type

A `RelationLabel` sits beside `Metacontext`: one table per graph on every
backend, unique on `(name, kind)`. It is not a node (§9): a label is
vocabulary, not knowledge, and `Metacontext` is the precedent for a named,
described thing that lives beside the graph rather than in it.

**`name`, not `content`.** `Metacontext.content` is named for prose. A
relation label's name is a token that edges carry verbatim, and calling it
`content` would invite someone to write a sentence in it. `label` would
stutter against `NodeEdge.label`, which it joins to.

### 2.2 Where it lives

Every backend writes through one pure merge, `recorded_relation_label`: `id`,
`created_at` and `judged_by` come from the record already there, and a blank
description never overwrites prose, so the coiner-never-the-describer rule is
structural rather than a convention every caller has to remember. Only the
description and metadata may move: `name` cannot, because edges join to it by
string (§2.4), and `kind` cannot, because the kind in force is the one on the
edges. The SurrealDB write upserts on the natural pair rather than on `uid`,
so a create-or-fetch caller that lost a race writes over the same row instead
of hitting the unique index.

### 2.3 Creation, and the graphs that already exist

**Every write path that names a label creates-or-fetches its record.** Three
of them: `link` when a label is coined, `describe_relation` when one is
described, and `relation_verdicts` when a pair is judged. Each already writes
and already carries a judge, so the record comes into existence at exactly
the moments an agent touches the vocabulary, and no path can be blocked
waiting for one. An enumeration of write paths is a claim that ages; a test
asserting every record is reachable without the CLI is what catches a fourth.

**Only `link` records a judge**, because only `link` coins.

**The CLI backfill is a convenience, not a precondition.** `epimemer
relations backfill` reads the same scan `list_relations` uses and writes a
judge-less record per distinct `(label, kind)`, so a long-lived graph can get
its vocabulary in one go. It must not be the only way a pre-existing label
gets a record: the CLI refuses embedded backends, which is the default
development configuration, and an agent cannot run it at all
(`DEVELOPER_GUIDE.md`, *The CLI is not a remedy an agent can reach*). Its
refusal message says plainly that nothing is lost.

**Every read tolerates a missing record.** A label without one is a graph
nobody has touched since records existed, and every reader falls back to the
recordless behaviour. The worst outcome is that suppression does not apply to
a pair nobody has judged.

### 2.4 Identity is the id; the name does not move

`NodeEdge.label` keeps the string and joins by name, so renaming a label
would break the join unless every edge were rewritten, the bulk relabel this
design exists to stop needing. Renaming is not supported. If it is ever
built, the history belongs on the record, one entry per rename rather than
one per edge, because it must survive a rename that touched zero edges.

---

## 3. Descriptions

### 3.1 Guidance, not a schema

One description per label per graph, because the same words mean different
things in different graphs. It will not partition the servant case from the
consultant case, and it does not need to: it is prose an agent reads, free to
say *"in the Court context this means X; for corporate contracts use Y."*
Making it enforceable would make it a schema; describing individual edges
rather than the shared label would make this a hypergraph. Neither is done
(§8).

### 3.2 Where an agent meets it

- `list_relations` returns `description` beside `label`, `kind` and `count`,
  always carrying the field (empty when absent), because the row exists
  either way. Its counts stay derived from edges, scoped to active nodes, for
  a reason a stored count would drift from.
- `link` returns the description of the label it reused, as
  `relation_description`, so an agent coining against an existing name is
  told what that name already means at the moment it matters. It omits the
  key rather than sending an empty one: `""` reads as *this graph means
  nothing by the word*, absence as *nobody has said*.
- `describe_relation(name, kind, description)` writes one, journalled. It
  resolves the kind from the edges, as `link` does for a reused label, and
  that read covers every edge while `list_relations` is scoped to active
  nodes, so a label whose only remaining edges hang off retired nodes is
  describable but not listed. Right way round: the vocabulary outlives the
  claims that used it, and the alternative would make a word undescribable
  exactly when the graph had begun to forget what it meant. The response
  reports what was stored, not what was asked for, since a blank description
  leaves existing prose alone and echoing the argument would tell an agent it
  had cleared a description it had not.

This is the half that pays. It moves the intervention from repair to
prevention: an agent picking from a described vocabulary never coins the
fourth synonym. Descriptions solve comprehension, not convergence: nothing
pulls ten words for one idea together except `list_relations` sorting by
usage count, which is a weak force. That is the strongest argument for
building deprecation (§5).

---

## 4. Suppressing a decline

### 4.1 The verdict

`relation_verdicts` mirrors `apply_reflection(similarities=[…])`, the same
problem one layer down, with two answers: **`distinct`**, different
relationships that look alike, and **`synonymous`**, the same relationship
written two ways. `docs/REFLECTION.md` has the shape and the refusals.

**Both suppress.** Recording *"yes, these are synonyms, and I am not merging
them"* is a real judgment, and leaving it unrecordable would be the
treadmill again for the affirmative answer. `synonymous` acts on nothing (§5).

**`because` is required on both**: a verdict with no reason marks the pair
judged, so the next agent skips it without knowing whether it was examined or
waved through.

**A missing `kind` is refused rather than defaulted**: copying it from the
nomination is the agent's job, and defaulting it would make the stale-kind
refusal blame the agent for a value the call invented. A pair already
carrying this agent's identical verdict is refused too, since a retry is not
a second opinion.

**A label with no record is not refused; it gets one**, judge-less, per §2.3.

**A different judge recording a verdict the pair already carries has
confirmed, not decided.** A confirmation row cites the oldest decision for
that pair, which is what stops a third agent doing the work a fourth time.

**A different judge disagreeing is recorded, not refused.** Both rows survive
with their judges and their reasons, and since both verdicts suppress, the
disagreement changes nothing operationally; it is made visible rather than
resolved. A row is only written when no agreeing row stands, so the table
holds at most one row per (pair, verdict).

**Two unnamed judges compare equal, so an anonymous repeat is refused as a
retry.** Where a graph does not require a judge, a replayed batch and a
genuine second reader are indistinguishable, and they want opposite
treatments. Refusing costs an unnamed agent the ability to confirm, which
the journal's first row already records; accepting would let a retried call
manufacture agreement out of nobody.

### 4.2 The suppression read

Verdicts are a small append-only table, not a field on the label record
(§9), and each row holds the sorted pair of label ids, so (a, b) and (b, a)
are one pair. Every backend derives the judged set through
`standing_relation_pairs`, a pure function over the rows, rather than each
writing the rule as a query: a suppression index two backends spell
differently is one that can disagree about what has been judged. A pair
either of whose sides has no record is suppressed by nothing.

**What was written is readable where the next agent looks.** Each
`list_relations` row carries the standing verdicts naming its label: the
other label, the verdict, the reason, the judge and the date, newest first,
both rows of a disagreement. And `reflect` reports
`relation_pairs_suppressed`, counted where the skip happens, because the
suppression is silent by design and without the count an empty nomination
list on a well-judged graph is indistinguishable from a graph with nothing
similar in it. *Settled* and *unexamined* must never read the same.

This is a denormalised suppression index, and legitimate as one for
`similarity_decisions.py`'s reason: it is immutable and append-only, so it
cannot drift from the journal row that also records the act.

**A suppression stands until it is reopened.** `reopen` appends a row whose
verdict is `reopened`, and `standing_relation_pairs` reads the newest row for
each pair: while that row is the newest, the pair is nominated again. Nothing
is withdrawn and nothing is overruled, which is what keeps the table
append-only and the rule symmetric. That symmetry is the right one here and
would be wrong on the fact layer, for the reason that layer is
one-directional: a false unification manufactures agreement while a
withdrawal merely under-counts. Neither failure exists on a label, because
nothing corroborates on one, so a wrong `synonymous` invents no support and a
wrong `distinct` costs no count. That holds only while nothing acts
irreversibly on `synonymous`; ship an irreversible deprecation (§5) and it
needs re-deriving.

Two consequences follow, and both are load-bearing:

- **A verdict recorded after a reopen is a fresh decision, never a retry.**
  The rows the newest reopen set aside are dropped before the retry and
  confirmation checks run. Without that, the second judge's answer reads as a
  repeat of the first one's, no row is written, and the pair stays nominated
  for ever while the call reports success.
- **`reopened` is not one of the answers.** A withdrawal offered beside
  `distinct` and `synonymous` would read as a third answer, so a verdict
  outside those two is refused and `reopen` is the only writer.

The nomination carries the reopening, so the next judge sees the reason and
the date rather than re-deriving why the pair is back.

### 4.3 The journal row

`DecisionKind.RELATION_VERDICT`, with the two label ids as its subjects. A
decision's subject is not always a claim, so `review()` says what each
subject is (`subject_kind`: `node`, `relation_label`, or null), and a label
is never declared in `retrieved`, which drives focus in a node viewer. Giving
the subject an identity is worth nothing until the reader dereferences it.

Its own kind rather than `SIMILARITY`: review selects on kind, and a reviewer
auditing judgments about claims does not want judgments about vocabulary.
Describing a label journals `RELATION_DESCRIPTION`, not `ENRICHMENT`, for the
same reason: `ENRICHMENT` is reflect's topic enrichment, and prose about what
the graph's words mean does not belong among changes to what it claims.

A reopen writes `DecisionKind.REOPENED` over the same two subjects, in the
same transaction as the verdict row, rather than a `RELATION_VERDICT` with a
withdrawal in its prose: a reviewer asking what has been decided about the
vocabulary should not get the rows where a decision was taken back.

---

## 5. Labels are never rewritten; deprecation is unbuilt

`relation_merges` was removed and nothing replaced it. `reflect` still
nominates likely synonyms, `relation_verdicts` records what was decided about
a pair, and `describe_relation` is what makes a vocabulary converge.
`tests/mcp/test_no_relation_merging.py` guards the absence.

**What that costs.** A `synonymous` verdict acts on nothing: an agent who
concludes that two labels are one relationship can record that and describe
both, and no mechanism folds them. That is the gap deprecation would fill,
and the decision is that an unfilled gap beats an irreversible fill.

**What it buys.** Merging was the system's one operation with no undo, spent
on the least valuable thing in the graph. Edges are not versioned, so a bulk
relabel destroyed the pre-rewrite partition at the moment it ran, with no
journal row naming what it had done. Removing it makes every retained
operation reversible or recorded, which is a property worth having whole.
Dropping is reversible and building is not, so the call went this way.

**The shape deprecation would take, if it is ever wanted.** Because labels do
not affect retrieval (§1.2), deprecation needs no rewrite: marking
`employed_by` as an alias of `works_for` sets `status` and an `alias_of` id
on the record. Existing edges keep their own wording; `list_relations` shows
the canonical set with aliases folded underneath; `link` steers new coinings
to the survivor. Nothing is destroyed, so nothing needs reversing. Undo you
never need beats undo that works.

Two constraints bind it from the first version:

- **It must record its own state changes**, and this is a deadline rather
  than a preference. Two agents can deprecate and un-deprecate the same label
  alternately (§6, FC2); nothing is rewritten, so the graph is unharmed, but
  the journal is append-only and `review`'s difficulty signals keep
  resurfacing the pair. The bound is a cycle limit in the shape of
  `merge_cycle_limit` (`REVIEW_MODE.md` §7.8), a refusal counted from state
  that already exists, and it is nearly free on exactly the condition that
  the history is recorded. Ship deprecation without that history and the
  early oscillations are unreconstructible: the only steps with a deadline
  are the ones recording something that exists once.
- **Terminality is the wrong bound.** A fact-pair retraction is one-way
  because a false unification manufactures agreement while a withdrawal only
  under-counts. Neither failure exists for a label (§4.2), so a deprecation
  nobody may reverse would make a wrong deprecation permanent and buy nothing
  for it. Count the cycles; do not forbid the second one.

---

## 6. Futile cycles

A sweep recomputed from current state that records no declines is a futile
cycle by construction: it re-offers what was already refused and cannot know
it is doing so. Four instances were identified for labels. Verdicts bound the
one that was live; the other three are constraints on features nobody has
built, and are written as such rather than as work outstanding.

| | Reachable? | Bound by |
|---|---|---|
| FC1, the nomination treadmill (§1.3) | Was live | Verdicts (§4) |
| FC2, deprecate ↔ un-deprecate | Only if deprecation exists | A cycle limit over deprecation's own history (§5) |
| FC3, nudge, comply, re-coin, nudge | Only if coin-time steering exists; nothing proposes it (§8) | A nudge must carry the description, or neither it nor the agent can tell a synonym from a distinction and the loop has no exit |
| FC4, rename ping-pong | Only if renaming exists; §2.4 does not build it | History on the record, since per-edge capture costs O(edges × cycles) against O(cycles) and must survive a rename that touched zero edges |

Suppression does not bound FC2: it stops the *nomination*, and
deprecate/un-deprecate would be direct calls that need no nomination.

---


## 8. What this deliberately does not do

- **It does not describe edges.** A description belongs to the shared label,
  not to one edge. Per-edge meaning is the step that would make this a
  hypergraph.
- **It does not enforce anything.** A vocabulary the system polices is a
  schema, and this system's whole shape is that agents judge and the graph
  records.
- **It does not re-point edges at label ids.** `NodeEdge.label` keeps its
  string.
- **It does not add renaming** (§2.4).
- **It does not nudge.** `link` reports the description of a label it reused;
  it does not steer an agent away from a name it was about to coin. Steering
  carries FC3, so whatever proposes it owes the answer there.
- **It does not merge labels** (§5).
- **It does not check metacontexts when nominating a pair.** A check would
  not stop the servant/consultant pair, whose usages sit in one fictional
  universe, and the corroboration harm behind `merge_facts`' cross-metacontext
  refusal does not transfer, because nothing corroborates on a label. The
  answer to a cross-metacontext label is its description. If a check is ever
  built, the bar is disjointness, not `fact_dedup`'s exact equality: here
  nothing inherits a union, so equality would make a label legitimately used
  in two metacontexts unpairable with anything.

---

## 9. Rejected

1. **A label as a node.** It would enter search, embeddings, reflection,
   archival and merging; the whole machine would start answering questions
   about the words the graph uses.
2. **Edges pointing at label records by id.** A migration over every
   user-tier edge for no gain: nothing traverses on the label, so the
   indirection buys nothing and costs the ability to read an edge without a
   join.
3. **A `subject_labels` field on `DecisionRecord`.** One field with two
   namespaces. Label ids as subjects make it unnecessary.
4. **The endpoint node ids as a label decision's subjects.** It satisfies
   *ids only* by making the row surface under nodes the decision was not
   about. (`link` does journal `[src_id, dst_id]`, correctly: there the
   endpoints really are what the decision was about.)
5. **Suppression stored on the label record.** Mutable state held twice,
   once per side, free to disagree.
6. **A `ValueSignal` on the record.** Nothing ranks or retrieves a label.
7. **`status` before anything writes it.** A status with one reachable value
   is a constant, not a state; it arrives with deprecation if that is built.
8. **Refusing a verdict on an unrecorded label and pointing at the CLI.** The
   CLI refuses embedded backends, the default development configuration, and
   an agent cannot run it in any case. A remedy the agent cannot issue, on a
   backend where it refuses, is not a remedy. Create-or-fetch at verdict time
   removes the refusal rather than improving its message.
9. **Suppressing declines keyed on the label strings, with no record.** It
   looked like the small option and is not: declining is a decision, every
   decision leaves a journal row, and `subject_ids` holds ids, so it would
   have forced rejection 3 as a side effect.
10. **Merging labels with a captured undo.** Meeting `REVIEW_MODE.md` §7.1's
    rule (any operation collapsing many into one must capture the partition
    while it exists) is most of the cost of building deprecation, on an
    operation deprecation replaces.
