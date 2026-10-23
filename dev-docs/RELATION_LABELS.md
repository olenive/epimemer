# Relation labels: a vocabulary with a record

Built except label deprecation (§5). The code is
`epimemer/pipelines/reflection/relation_verdicts.py`,
`relation_consolidation.py` and the label tools in `epimemer/mcp/tools.py`;
`docs/REFLECTION.md` describes `relation_verdicts` for a caller.

Each user-tier relationship label (`works_for`, `published_by`) has a record:
something to describe, something a decision can name, and something a decline
can be recorded against. Labels are never rewritten; a vocabulary converges
through descriptions and verdicts.

---

## 1. The problem

### 1.1 A label needs a record

`list_relations` derives the vocabulary from edges, grouped by
`(label, kind)`. Without a record a label has no meaning to describe, no id a
decision can name, and nothing to change but its edges.

### 1.2 What labels affect, and what they do not

**Labels do not affect retrieval.** Expansion reads an edge's type and kind,
never its label (`traversal_excluded`), and outside `list_relations`, `link`
and the engine-tier `published_by` label that corroboration reads, nothing in
a query pipeline reads a user-tier label. So consolidating labels changes a
printed string and nothing about which nodes come back, a different class of
operation from a fact merge, and the two share no machinery.

**Near-identical strings can mean opposite things**: a servant *works for* a
master in a culture with no employment, while a corporation *employs* a
consultant who does little work. The nominator sees only strings, so the
judgment is the agent's.

### 1.3 The decline treadmill

Without a record of declines the relation sweep, recomputed on every
`reflect`, would re-nominate a declined pair for ever. The `assessed` edge
cannot serve, because labels are not nodes; §4 is the equivalent.

---

## 2. The record

### 2.1 The type

A `RelationLabel` sits beside `Metacontext`: one table per graph on every
backend, unique on `(name, kind)`. It is not a node, because a label is
vocabulary, not knowledge (§9). The field is `name`, not `content`, because it
is a token edges carry verbatim, and not `label`, which would stutter against
`NodeEdge.label`.

### 2.2 Where it lives

Every backend writes through one pure merge, `recorded_relation_label`: `id`,
`created_at` and `judged_by` come from the existing record and a blank
description never overwrites prose, so *the coiner, never the describer* is
structural. `name` cannot move, because edges join to it by string (§2.4), and
`kind` cannot, because the kind in force is the one on the edges. The
SurrealDB write upserts on `(name, kind)` rather than on `uid`, so a
create-or-fetch race writes the same row.

### 2.3 Creation, and the graphs that already exist

**Every write path that names a label creates or fetches its record**: `link`
when a label is coined, `describe_relation` when one is described, and
`relation_verdicts` when a pair is judged. A test asserts every record is
reachable without the CLI, which catches a fourth path. **Only `link` records
a judge**, because only `link` coins.

**`epimemer relations backfill` is a convenience, not a precondition.** It
writes a judge-less record per distinct `(label, kind)`. It must never be the
only route, because the CLI refuses embedded backends and an agent cannot run
it (`DEVELOPER_GUIDE.md`, *The CLI is not a remedy an agent can reach*).

**Every read tolerates a missing record** and falls back to the recordless
behaviour; the worst case is that suppression does not apply to a pair nobody
has judged.

### 2.4 Identity is the id; the name does not move

`NodeEdge.label` keeps the string and joins by name, so a rename would mean
rewriting every edge. Renaming is not supported. If it is built, its history
belongs on the record, one entry per rename, because it must survive a rename
that touched no edges.

---

## 3. Descriptions

### 3.1 Guidance, not a schema

One description per label per graph, because the same words mean different
things in different graphs. It is prose an agent reads (*"in the Court context
this means X; for corporate contracts use Y"*). Enforcing it would make it a
schema, and describing individual edges would make this a hypergraph (§8).

### 3.2 Where an agent meets it

- **`list_relations`** returns `description` beside `label`, `kind` and
  `count`, empty when absent. Counts stay derived from edges on active nodes,
  since a stored count would drift.
- **`link`** returns the reused label's description as `relation_description`,
  so an agent coining against an existing name learns its meaning then. It
  omits the key rather than sending `""`, because empty reads as *means
  nothing* and absence as *nobody has said*.
- **`describe_relation(name, description, kind)`** writes one, journalled. It
  refuses a label no edge carries and a `kind` the edges do not carry. Its
  check covers every edge while `list_relations` covers active nodes, so a word
  stays describable after its claims retire. The response reports what was
  stored, since a blank description leaves existing prose alone.

Descriptions move the intervention from repair to prevention, but they solve
comprehension, not convergence: only `list_relations`' sort by count pulls
synonyms together, which is the strongest argument for deprecation (§5).

---

## 4. Suppressing a decline

### 4.1 The verdict

`relation_verdicts` mirrors `apply_reflection(similarities=[…])` one layer
down, with two answers, `distinct` and `synonymous`. `docs/REFLECTION.md` has
the shape and the refusals.

- **Both suppress**, because *these are synonyms and I am not merging them* is
  a real judgment. `synonymous` acts on nothing (§5).
- **`because` is required**, because a reasonless verdict lets the next agent
  skip a pair without knowing whether it was examined.
- **A missing `kind` is refused rather than defaulted**, so the stale-kind
  refusal never blames the agent for a value the call invented.
- **A label with no record gets one**, judge-less (§2.3), rather than a refusal
  pointing at a CLI the agent cannot run.
- **Same judge, same verdict: refused as a retry.** Two unnamed judges compare
  equal, because accepting would let a replayed call manufacture agreement out
  of nobody.
- **Different judge, same verdict: a confirmation** citing the oldest decision
  for the pair, so the table holds at most one row per (pair, verdict).
- **Different judge, other verdict: recorded**, both rows standing. Both
  verdicts suppress, so the disagreement is made visible rather than resolved.

### 4.2 The suppression read

Verdicts are a small append-only table holding the sorted pair of label ids.
Every backend derives the judged set through `standing_relation_pairs`, a pure
function over the rows, because a suppression index two backends spell
differently can disagree. A pair with an unrecorded side is suppressed by
nothing. Like `assessed`, it is a denormalised index allowed because it is
immutable and cannot drift from the journal.

**Settled and unexamined must never read the same.** Each `list_relations`
row carries the standing verdicts naming its label (other label, verdict,
reason, judge, date, newest first), and `reflect` reports
`relation_pairs_suppressed`, counted where the skip happens.

**A suppression stands until `reopen`**, which appends a `reopened` row;
`standing_relation_pairs` reads the newest row per pair, so the pair is
nominated again while that row is newest. Reopening is symmetric here, unlike
the fact layer, because nothing corroborates on a label: a wrong `synonymous`
invents no support and a wrong `distinct` costs no count. That holds only while
nothing acts irreversibly on `synonymous` (§5).

- **A verdict after a reopen is a fresh decision**: the rows the newest reopen
  set aside are dropped before the retry and confirmation checks, or the second
  judge's answer would be swallowed as a repeat and the pair nominated for
  ever.
- **`reopened` is not an answer**: a verdict outside `distinct` and
  `synonymous` is refused, and `reopen` is the only writer.

The nomination carries the reopening's reason and date.

### 4.3 The journal row

`DecisionKind.RELATION_VERDICT`, with the two label ids as subjects. `review`
says what each subject is (`subject_kind`: `node`, `relation_label` or null),
and a label is never declared in `retrieved`, which drives focus in a node
viewer. It is its own kind rather than `SIMILARITY`, and a description is
`RELATION_DESCRIPTION` rather than `ENRICHMENT`, because review selects on kind
and judgments about vocabulary are not judgments about claims. A reopen writes
`DecisionKind.REOPENED` over the same subjects in the same transaction as its
verdict row, so a reviewer asking what was decided does not get the rows where a
decision was taken back.

---

## 5. Labels are never rewritten; deprecation is unbuilt

There is no label merge; `tests/mcp/test_no_relation_merging.py` guards the
absence. `reflect` nominates likely synonyms, `relation_verdicts` records the
decision, and `describe_relation` makes the vocabulary converge. A merge would
be an operation with no undo, because edges are not versioned and a bulk
relabel destroys the pre-rewrite partition, spent on the least valuable thing
in the graph. The cost is that a `synonymous` verdict folds nothing.

**Deprecation, if it is built**, needs no rewrite, because labels do not affect
retrieval (§1.2): an alias sets `status` and an `alias_of` id on the record,
edges keep their wording, `list_relations` folds aliases under the canonical
label, and `link` steers new coinings to it. Two constraints bind it from the
first version:

- **It must record its own state changes**, because a cycle limit in the shape
  of `merge_cycle_limit` (`REVIEW_MODE.md` §7.8) needs that history, and
  oscillations before it is recorded are unreconstructible.
- **It must be reversible**, with cycles counted rather than a second
  reversal forbidden, because the fact layer's reason for one-way retraction
  does not exist for labels (§4.2).

---

## 6. Futile cycles

A sweep recomputed from current state that records no declines re-offers what
was refused. Verdicts bound the one that exists; the rest are constraints on
features not built.

| | Reachable? | Bound by |
|---|---|---|
| FC1, the nomination treadmill (§1.3) | yes | verdicts (§4) |
| FC2, deprecate and un-deprecate | only with deprecation | a cycle limit over deprecation's own history (§5); suppression does not bound it, since these would be direct calls |
| FC3, nudge, comply, re-coin, nudge | only with coin-time steering | the nudge must carry the description, or nobody can tell a synonym from a distinction |
| FC4, rename ping-pong | only with renaming | history on the record (§2.4), O(cycles) rather than O(edges × cycles) |

---

## 8. What this deliberately does not do

- **Describe edges**: per-edge meaning would make this a hypergraph.
- **Enforce anything**: a policed vocabulary is a schema.
- **Re-point edges at label ids, rename, or merge** (§2.4, §5).
- **Nudge**: `link` reports a reused label's description and steers nothing,
  and whatever adds steering owes FC3's answer.
- **Check metacontexts when nominating a pair**: nothing corroborates on a
  label, so `merge_facts`' reason does not transfer, and the answer to a label
  used across metacontexts is its description. A check, if built, would test
  disjointness, not `fact_dedup`'s exact equality, so a label legitimately used
  in two metacontexts stays pairable.

---

## 9. Rejected

1. **A label as a node**: search, embeddings, reflection, archival and merging
   would all start answering questions about the graph's words.
2. **Edges pointing at label records by id**: a migration over every user-tier
   edge for nothing, since nothing traverses on the label.
3. **Label strings or endpoint node ids as a decision's subjects, or a
   `subject_labels` field**: `subject_ids` holds ids, and endpoints would
   surface the row under nodes it was not about. (`link` does journal
   `[src_id, dst_id]`, because there the endpoints are the subject.)
4. **Suppression stored on the label record**: mutable state held twice, once
   per side.
5. **A `ValueSignal` or a `status` on the record now**: nothing ranks a label,
   and a status with one reachable value is a constant.
6. **Merging labels with a captured undo**: meeting `REVIEW_MODE.md` §7.1's
   capture rule is most of the cost of deprecation, on an operation
   deprecation replaces.
